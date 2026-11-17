"""Run: .venv/bin/python test_capture.py. No external services needed."""
import json, os, tempfile, uuid
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from folio_capture.api import create_app
from folio_capture.store import Store
from folio_capture.export import csv_bytes
from folio_capture.checks import validate_entry
from test_pdf_to_folio import check


def main():
    check()
    os.environ.update(APP_USERNAME='test', APP_PASSWORD='test-password-long-enough')
    with tempfile.TemporaryDirectory() as root:
        store=Store(Path(root)/'state.db')
        store.call('put_source', source={'id':'parcel','filename':'parcel.pdf','hash':'abc','page_count':1})
        batch=store.call('create_batch',source_ids=['parcel'])
        bid=batch['id']
        entry={'id':'entry','entry_type':'Description of Land','status':'','regions':[{'page':1,'box':[0,0,1000,1000]}], 'attributes':{'FT_FOLIO_TEXT':[{'REG_ON_FIRST_REG':'N'}]},'field_evidence':{},'relationships':[],'reasons':[],'review_status':'needs_review'}
        folio={'batch_id':bid,'source_id':'parcel','source_filename':'parcel.pdf','revision':1,'metadata':{'folio_number':'123'},'pages':[{}],'entries':[entry],'issues':[],'directory':root}
        store.call('put_folio', folio=folio, attempt=1)
        extracted=csv_bytes(folio)
        client=TestClient(create_app(store.call,lambda *a:'worker',lambda *a:None,lambda *a:'task',lambda *a:None,lambda p:b'PK-test' if p.endswith('.zip') else extracted))
        for path in ['/', '/assets/anything.js','/api/schema',f'/api/batches/{bid}/download',f'/api/batches/{bid}/folios/parcel/pages/1']:
            assert client.get(path).status_code==401, path
        client.auth=('test','test-password-long-enough')
        assert client.get('/').status_code==200
        assert client.get(f'/api/batches/{bid}/download').status_code==409
        assert client.post('/api/batches',json={'source_ids':['parcel']},headers={'Origin':'https://evil.example'}).status_code==403
        store.call('update_batch',id=bid,changes={'downloads_ready':True,'status':'complete'})
        url=f'/api/batches/{bid}/folios/parcel'
        original=client.get(url+'/csv').content
        action={'revision':1,'action_id':uuid.uuid4().hex,'action':{'kind':'metadata','values':{'folio_number':'456'}}}
        assert client.post(url+'/review',json=action).status_code==200
        assert client.post(url+'/review',json=action).json()['revision']==2
        action['action_id']=uuid.uuid4().hex
        assert client.post(url+'/review',json=action).status_code==409
        assert client.get(url+'/csv').content==original
        assert client.get(url+'/csv?version=current').content!=original
        assert client.get(url+'/csv?version=reviewed').status_code==409
        assert client.post(url+'/review',json={'revision':2,'action_id':uuid.uuid4().hex,'action':{'kind':'add','entry_type':'Other','regions':[{'page':1,'box':[10,10,100,100]}]}}).status_code==200
        assert len(store.call('audit',batch_id=bid,source_id='parcel')[0]['before']['entries'])==1
        cancelled=json.loads(json.dumps(entry));cancelled['status']='Y';validate_entry(cancelled,[{}]);assert not cancelled['attributes']
        lookup=json.loads(json.dumps(entry));lookup['attributes']['FT_EASEMENT_DOM_REG']=[{'FOLIO_ID':'123'}];validate_entry(lookup,[{}]);assert lookup['attributes']['FT_EASEMENT_DOM_REG'][0]['FOLIO_ID']==''
        assert client.get(f'/api/batches/{bid}/download').content[:2]==b'PK'
        manual = client.get(f'/api/batches/{bid}/download?version=current&format=manual')
        assert manual.status_code == 200 and 'spreadsheetml.sheet' in manual.headers['content-type']
        assert client.get(f'/api/batches/{bid}/download?version=reviewed&format=manual').status_code == 409
        import cv2, numpy as np
        from folio_capture.vision import deletion_geometry
        image=np.full((200,400,3),255,dtype=np.uint8)
        cv2.line(image,(20,65),(380,65),(0,0,255),3)
        path=Path(root)/'deletion.png';cv2.imwrite(str(path),image)
        words=[{'box':[50,200,400,450]},{'box':[450,200,900,450]}]
        assert deletion_geometry(path,[{'page':1,'box':[0,0,1000,1000]}],words)
        assert not deletion_geometry(path,[{'page':1,'box':[0,500,1000,1000]}],words)
        from folio_capture.engine import process_folio
        page_dir=Path(root)/'source';page_dir.mkdir();(page_dir/'page-0001.pdf').write_bytes(b'test fixture')
        source={'id':'fixture','filename':'fixture.pdf','hash':'fixture','pages_path':str(page_dir)}
        inventory={'metadata':{'folio_number':'123','county':'Antrim'},'metadata_sources':[],
            'entries':[{'key':str(i),'parent_key':('self' if i==0 else 'shared' if i<3 else ''),'entry_type':'Description of Land','status':'',
                        'regions':[{'page':1,'box':[0,0,1000,1000]}]} for i in range(6)],
            'excluded_regions':[],'notes':[]}
        def extract(e,f,d):
            e['attributes']={'FT_FOLIO_TEXT':[{'REG_ON_FIRST_REG':'N'}]}
            return e,[]
        def render(pdf,destination): cv2.imwrite(str(destination),np.full((200,200,3),255,np.uint8))
        with patch('folio_capture.engine.render_page',side_effect=render), patch('folio_capture.engine.tesseract_page',return_value={'words':[],'blank':False}), patch('folio_capture.engine.model_json',return_value=(inventory,[])), patch('folio_capture.engine.extract_entry',side_effect=extract), patch('folio_capture.engine.entry_signals',return_value={'deletion_strokes':[{'page':1,'colour':'dark'}]}):
            first=process_folio(source,'test',Path(root)/'attempt-1',lambda images:[{'words':[]}])
        assert all(e['status']=='' and e['attributes'] for e in first['entries']), 'An underline must not suppress the whole entry'
        assert all('CHECK_CANCELLATION_SCOPE' in e['reasons'] for e in first['entries'])
        assert [e['parent_id'] for e in first['entries']]==['','shared','shared','','',''], 'Only actual shared source entries are split'
        def unexpected(*args): raise AssertionError('Resume repeated completed work')
        with patch('folio_capture.engine.render_page',side_effect=unexpected), patch('folio_capture.engine.model_json',side_effect=unexpected), patch('folio_capture.engine.extract_entry',side_effect=unexpected):
            resumed=process_folio(source,'test',Path(root)/'attempt-3',unexpected)
        assert [e['id'] for e in first['entries']]==[e['id'] for e in resumed['entries']]
    print('Capture checks passed: authentication, snapshots, revisions, review gates, suppression and lookups')

if __name__=='__main__': main()
