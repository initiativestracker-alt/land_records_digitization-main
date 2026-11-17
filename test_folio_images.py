"""Run: .venv/bin/python test_folio_images.py. No external inference calls."""

from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from openpyxl import load_workbook

from folio_capture.annotations import annotated_attributes, locate
from folio_capture.api import create_app
from folio_capture.engine import process_folio
from folio_capture.export import csv_bytes
from folio_capture.manual import manual_sheets, manual_xlsx_bytes
from folio_capture.sources import image_folios, ingest_images, jpeg_image, page_number, prepare_page
from folio_capture.store import Store


def jpeg(size=(120,200), orientation=None):
    stream = io.BytesIO()
    image = Image.new('RGB', size, 'white')
    exif = Image.Exif()
    if orientation: exif[274] = orientation
    image.save(stream, 'JPEG', exif=exif)
    return stream.getvalue()


def main():
    folders = image_folios('POC Samples')
    assert len(folders)==5 and sum(len(list(p.glob('*.jpg'))) for p in folders)==42
    assert page_number('10_r_123_456.JPG')==10 and page_number('page-0002.jpeg')==2
    with TemporaryDirectory() as root:
        files = [('10_scan.JPG',jpeg()),('1_scan.jpg',jpeg((200,120))),('2_scan.jpeg',jpeg())]
        source = ingest_images('folio name', files, root)
        assert [p['source_page_number'] for p in source['page_manifest']]==[1,2,10]
        assert source['numbering_gaps']==[[3,9]]
        assert ingest_images('folio name',list(reversed(files)),root)['id']==source['id']
        assert ingest_images('folio name',[('1_scan.jpg',jpeg((201,120)))],root)['id']!=source['id']
        assert ingest_images('other',files,root)['id']!=source['id']
        for bad_name,bad_files in [('name',[('1_a.jpg',jpeg()),('1_b.jpg',jpeg())]),
                                   ('../escape',files),('name',[('../1_a.jpg',jpeg())]),
                                   ('name',[('unknown.jpg',jpeg())]),('name',[('1_bad.jpg',b'broken')]),
                                   ('name',[('1_bad.jpg',b'\x89PNG\r\n')]),('name',[])]:
            try: ingest_images(bad_name,bad_files,root)
            except ValueError: pass
            else: raise AssertionError('Unsafe or ambiguous source accepted')
        with jpeg_image(jpeg((120,200),6)) as image: assert image.size==(200,120)
        prepare_page(source['page_manifest'][0],Path(root)/'oriented.png')
        with Image.open(Path(root)/'oriented.png') as image: assert image.size==(200,120)
        for folder in folders:
            imported=ingest_images(folder.name,[(p.name,p.read_bytes()) for p in folder.glob('*.jpg')],root)
            assert not imported['numbering_gaps']
            assert imported['folio_name']==folder.name

        inventory={'metadata':{'folio_number':'42','county':'Down'},'metadata_sources':[],
                   'metadata_evidence':{'folio_number':[{'page':1,'box':[0,0,1000,1000],'quote':'42'}]},
                   'entries':[{'key':'a','entry_type':'Transfer of Part','status':'','page_entry_number':1,
                               'regions':[{'page':1,'box':[0,0,1000,1000]},{'page':3,'box':[0,0,1000,1000]}]}],
                   'excluded_regions':[],'notes':[]}
        def extract(entry,folio,directory):
            entry['attributes']={'FT_FOLIO_TEXT':[{'FOLIO_TEXT_LEGIBLITY':'0'}]}
            return entry,[]
        with patch('folio_capture.engine.tesseract_page',return_value={'words':[],'blank':False}), \
             patch('folio_capture.engine.inventory_folio',return_value=(inventory,[])), \
             patch('folio_capture.engine.extract_entry',side_effect=extract), \
             patch('folio_capture.engine.entry_signals',return_value={}):
            folio=process_folio(source,'test',Path(root)/'attempt-1',lambda images:[{'words':[]} for _ in images])
        assert len(folio['pages'])==3 and folio['pages'][2]['source_page_number']==10
        assert folio['issues'][0]['code']=='SOURCE_PAGE_GAPS'
        exported=list(csv.DictReader(io.StringIO(csv_bytes(folio).decode('utf-8-sig'))))[0]
        assert exported['folio_name']=='folio name' and exported['source_pages']=='[1, 10]'
        assert exported['FT_FOLIO_TEXT.FOLIO_IMAGE_FILENAME']=='1_scan.jpg;10_scan.JPG'
        matrix=manual_sheets([folio])['Transfer of Part']
        assert matrix[0][-1]=='1, 10' and matrix[2]==[None,None,'Folio Name','folio name']
        assert next(row[-1] for row in matrix if row[2]=='FOLIO_IMAGE_FILENAME')=='1_scan.jpg;10_scan.JPG'
        book=load_workbook(io.BytesIO(manual_xlsx_bytes([folio])))
        assert book['Transfer of Part'].freeze_panes=='D5'
        cancelled=deepcopy(folio);cancelled['entries'][0]['status']='Y'
        assert list(csv.DictReader(io.StringIO(csv_bytes(cancelled).decode('utf-8-sig'))))[0]['FT_FOLIO_TEXT.FOLIO_IMAGE_FILENAME']=='1_scan.jpg;10_scan.JPG'

        words=[{'text':'Folio','box':[10,10,100,30]},{'text':'42','box':[110,10,140,30]},
               {'text':'Down','box':[200,10,260,30]}]
        page={'tesseract':{'words':words}}
        source_box={'page':1,'box':[0,0,1000,1000],'quote':'Folio 42'}
        assert locate(source_box,'42',page)==[{'box':[110,10,140,30],'locator':'ocr'}]
        assert locate({**source_box,'quote':'unreadable'},'missing',page)[0]['locator']=='model'
        ambiguous={'tesseract':{'words':[words[1],{**words[1],'box':[110,70,140,90]}]}}
        assert locate({**source_box,'quote':'42'},'42',ambiguous)[0]['locator']=='model'
        folio['pages'][0].update(page)
        folio['entries'][0]['attributes']['FT_TOP']=[{'NEW_FOLIO_ID':'42','PARTIES_REQD':'N'}]
        folio['entries'][0]['field_evidence']={'FT_TOP[0].NEW_FOLIO_ID':{'basis':'observed','sources':[source_box]},
            'FT_TOP[0].PARTIES_REQD':{'basis':'default','sources':[]}}
        before=deepcopy(folio)
        annotations=annotated_attributes(folio)
        assert folio==before, 'Annotated view must not change stored evidence'
        assert next(a for a in annotations if a['name']=='NEW_FOLIO_ID')['sources'][0]['box']==[110,10,140,30]
        assert next(a for a in annotations if a['name']=='PARTIES_REQD')['sources']==[]
        assert all(a['entry_id'] is None for a in annotated_attributes(cancelled))
        legacy=deepcopy(folio);legacy.pop('metadata_evidence');legacy['metadata_sources']=[source_box]
        legacy['metadata']['folio_number']='not printed'
        assert next(a for a in annotated_attributes(legacy) if a['id']=='metadata.folio_number')['sources']==[]
        os.environ.update(APP_USERNAME='test',APP_PASSWORD='test-password-long-enough')
        store=Store(Path(root)/'state.sqlite3')
        app=create_app(store.call,lambda *a:'worker',lambda *a:None,lambda *a:'task',lambda *a:None,
                       lambda p:b'original',ingest_folio=lambda name,files:store.call('put_source',source=ingest_images(name,files,root)))
        with TestClient(app) as client:
            assert client.post('/api/sources/folio').status_code==401
            client.auth=('test','test-password-long-enough')
            response=client.post('/api/sources/folio',data={'folio_name':'API source'},files=[('files',('1_scan.jpg',jpeg(),'image/jpeg'))])
            assert response.status_code==200 and response.json()['folio_name']=='API source'
            assert client.post('/api/sources/folio',data={'folio_name':'bad'},files=[('files',('1_scan.jpg',b'bad','image/jpeg'))]).status_code==422
            assert client.post('/api/sources/folio',data={'folio_name':'bad'},files=[('files',('1_scan.jpg',jpeg(),'image/jpeg'))],headers={'Origin':'https://evil.example'}).status_code==403
            batch=store.call('create_batch',source_ids=[response.json()['id']])
            folio.update(batch_id=batch['id'],source_id=response.json()['id'])
            store.call('put_folio',folio=folio,attempt=1)
            url=f"/api/batches/{batch['id']}/folios/{folio['source_id']}"
            assert client.get(url).json()['annotations']
            changed=client.post(url+'/review',json={'revision':1,'action_id':'metadata-correction','action':{'kind':'metadata','values':{'folio_number':'99'}}})
            assert changed.status_code==200
            assert next(a for a in changed.json()['annotations'] if a['id']=='metadata.folio_number')['sources']==[]
            assert store.call('folio',batch_id=batch['id'],source_id=folio['source_id'],version='extracted')['metadata']['folio_number']=='42'
    print('Image folio checks passed: 5 folios/42 pages, ordering, identity, orientation, validation, exports, grounding and API gates')


if __name__=='__main__': main()
