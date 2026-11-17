"""Ensure trials cannot reuse cached results after model/source changes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from iterate_capture import experiment_fingerprint

with TemporaryDirectory() as directory:
    baseline = Path(directory) / 'baseline.zip'
    baseline.write_bytes(b'first source snapshot')
    initial = experiment_fingerprint('model-one', baseline)
    assert initial == experiment_fingerprint('model-one', baseline)
    assert initial != experiment_fingerprint('model-two', baseline)
    baseline.write_bytes(b'corrected source snapshot')
    assert initial != experiment_fingerprint('model-one', baseline)
print('Trial cache checks passed: repeatability and model/source isolation')

from apply_inventory import reconcile
from folio_capture.engine import Region, registration_outside_entry
from pydantic import ValidationError

for bad_box in ([0,0,1001,500], [500,0,200,500], [0,0,0,500]):
    try:
        Region(page=1,box=bad_box)
    except ValidationError:
        pass
    else:
        raise AssertionError('Invalid model geometry escaped response validation')
entry={'id':'old','key':'old','entry_type':'Transfer of Part','status':'U',
       'regions':[{'page':1,'box':[100,100,900,300]}], 'new_folio_hint':'AB 123',
       'attributes':{'stale':[]}, 'reasons':[]}
proposal={**entry,'status':'','page_entry_number':2,'parent_key':'','label':'Transfer'}
folio={'source_id':'example','entries':[entry]}
result=reconcile(folio,{'entries':[proposal], 'notes':[]})
assert result['entries'][0]['id']=='old'
assert result['entries'][0]['status']=='' and not result['entries'][0]['attributes']
assert entry['status']=='U', 'Reconciliation mutated baseline'
stale={**entry,'inventory_proposals':[proposal]}
unmatched=reconcile({'source_id':'example','entries':[stale]}, {'entries':[], 'notes':[]})
assert not unmatched['entries'][0].get('inventory_proposals'), 'Old proposals cannot masquerade as a new inventory match'
other={**proposal,'regions':[{'page':2,'box':[100,100,900,300]}]}
result=reconcile(folio,{'entries':[proposal,other],'notes':[]})
assert len(result['entries'])==2, 'Repeated target on another page was lost'
print('Inventory checks passed: geometry, recovery, snapshots and repeated page occurrences')

land={**entry,'entry_type':'Description of Land','status':'','new_folio_hint':'',
      'regions':[{'page':1,'box':[0,100,900,500]}]}
parcel={**land,'regions':[{'page':1,'box':[200,250,800,275]}],'page_entry_number':2}
result=reconcile({'source_id':'example','entries':[land]}, {'entries':[parcel],'notes':[]})
assert len(result['entries'])==1, 'Nested parcel duplicated its existing aggregate entry'
print('Nested parcel reconciliation passed')

# A malformed model box must trigger a targeted retry before an entry is accepted.
import json
from types import SimpleNamespace
from unittest.mock import patch
from folio_capture.engine import model_json, Extraction
payload={'records':[{'table':'FT_FOLIO_TEXT','record_id':'t','parent_id':'','fields':[
    {'name':'REGISTERED_DATE_DD','value':'1','basis':'observed','sources':[
        {'page':1,'box':[0,0,0,0],'quote':'1'}],'rule':''}]}],'notes':[]}
prompts=[]
def respond(**kwargs):
    prompts.append(kwargs['contents'][0].parts[0].text)
    if len(prompts)>1: payload['records'][0]['fields'][0]['sources'][0]['box']=[1,1,2,2]
    return SimpleNamespace(text=json.dumps(payload),usage_metadata=None)
client=SimpleNamespace(models=SimpleNamespace(generate_content=respond),close=lambda:None)
with patch('folio_capture.engine.genai.Client',return_value=client), patch.dict('os.environ',{'GEMINI_API_KEY':'test-only'}), patch('folio_capture.engine.time.sleep'):
    parsed,usage=model_json('capture',[],Extraction)
assert len(prompts)==2 and 'Correct these validation errors' in prompts[1]
assert parsed['records'][0]['fields'][0]['sources'][0]['box']==[1,1,2,2]
print('Targeted model validation retry passed (mocked; no API calls)')

registration={'fields':[{'name':'REG_ON_FIRST_REG','value':'Y','sources':[
    {'page':1,'box':[10,500,100,550],'quote':'Registered on first registration'}]}]}
assert registration_outside_entry(registration,[{'page':1,'box':[350,270,955,405]}])
assert not registration_outside_entry(registration,[{'page':1,'box':[350,490,955,600]}]), 'A beside-body margin remains eligible'
print('Registration attribution checks passed: adjacent margin versus neighbouring block')

from copy import deepcopy
from folio_capture.engine import extract_entry
initial={'records':[{'table':'FT_FOLIO_TEXT','record_id':'text','parent_id':'','fields':[
    {'name':'REG_ON_FIRST_REG','value':'Y','basis':'observed','sources':registration['fields'][0]['sources'],'rule':''},
    {'name':'FOLIO_TEXT_STAMPS','value':'STAMP','basis':'observed','sources':[
        {'page':1,'box':[400,280,500,300],'quote':'STAMP'}],'rule':''}]}],'notes':[]}
repair={'records':[{'table':'FT_FOLIO_TEXT','record_id':'text','parent_id':'','fields':[
    {'name':'REG_ON_FIRST_REG','value':'N','basis':'default','sources':[],'rule':'Description of Land!C7:D7'},
    *[{'name':f'REGISTERED_DATE_{part}','value':None,'basis':'absent','sources':[],'rule':''}
      for part in ['DD','MM','YYYY']]]}],'notes':['Registration belongs to the neighbouring entry.']}
target={'id':'scope-test','entry_type':'Description of Land','status':'','parent_id':'','reasons':[],
        'regions':[{'page':1,'box':[350,270,955,405]}]}
with patch('folio_capture.engine.image_parts',return_value=[]), patch('folio_capture.engine.model_json',side_effect=[(deepcopy(initial),[]),(repair,[])]) as model:
    fixed,_=extract_entry(target,{'source_id':'synthetic','metadata':{},'pages':[{}]},Path('.'))
assert model.call_count==2
assert fixed['attributes']['FT_FOLIO_TEXT'][0]['REG_ON_FIRST_REG']=='N'
assert fixed['attributes']['FT_FOLIO_TEXT'][0]['FOLIO_TEXT_STAMPS']=='STAMP', 'Registration review must preserve unrelated values'
print('Registration repair integration passed (mocked; no API calls)')
