"""Manual export contract and provenance checks; no labelled answer fixtures."""
from copy import deepcopy
from folio_capture.manual import complete_capture, manual_sheets, manual_xlsx_bytes
from folio_capture.checks import validate_entry


def main():
    entry = {'id':'test','entry_type':'Transfer of Part','status':'','page_entry_number':2,
             'regions':[{'page':1,'box':[0,0,1000,1000]}], 'attributes':{
                 'FT_FOLIO_TEXT':[{'_record_id':'text','FOLIO_TEXT_LEGIBLITY':'0'}],
                 'FT_TOP':[{'_record_id':'top','_parent_id':'text','NEW_FOLIO_ID_Prefex':'AN','NEW_FOLIO_ID_Number':'123','NEW_FOLIO_ID_Suffix':'L'}]},
             'reasons':[]}
    complete_capture(entry,'source')
    once = deepcopy(entry)
    complete_capture(entry,'source')
    assert entry == once, 'Normalization must be idempotent'
    assert entry['attributes']['FT_TOP'][0]['NEW_FOLIO_ID'] == 'AN 123 L'
    assert entry['attributes']['FT_TOP_PART_OR_ENTRY'][0]['PART_OF_FOLIO_ENTRY'] == 'N'
    assert 'MONETARY_POUNDS' not in entry['attributes']['FT_CONSIDERATION'][0]
    folio = {'source_id':'source','entries':[entry]}
    sheets = manual_sheets([folio])
    assert list(sheets) == ['Transfer of Part','Description of Land','Covenant','Easement','Leasehold Yearly Rent','Leasehold Particulars']
    top = sheets['Transfer of Part']
    assert top[0] == [None,None,'Page No',1] and top[1][-1] == 2
    assert top[3][-1] == 'source' and top[6][-1] == 'Blank'
    from io import BytesIO
    from openpyxl import load_workbook
    before = deepcopy(folio)
    workbook = load_workbook(BytesIO(manual_xlsx_bytes([folio])), data_only=False)
    assert folio == before, 'Export must preserve stored extraction snapshots'
    assert workbook.sheetnames == list(sheets)
    assert workbook['Transfer of Part']['D2'].value == 2
    assert workbook['Transfer of Part'].freeze_panes == 'D4'
    formula = deepcopy(folio)
    formula['entries'][0]['attributes']['FT_TOP'][0]['NEW_FOLIO_ID']='=1+1'
    safe = load_workbook(BytesIO(manual_xlsx_bytes([formula])), data_only=False)
    assert safe['Transfer of Part']['D20'].value == '=1+1'
    assert safe['Transfer of Part']['D20'].data_type == 's'
    cancelled = deepcopy(entry);cancelled['status']='Y';cancelled['attributes']={}
    complete_capture(cancelled,'source');assert cancelled['attributes']=={}
    failed = deepcopy(entry);failed['attributes']={};failed['reasons']=['ENTRY_EXTRACTION_FAILED']
    complete_capture(failed,'source');assert failed['attributes']=={}
    ref = deepcopy(entry);ref['entry_type']='Easements'
    ref['attributes']={'FT_EASEMENT_DOM_REG':[{'FOLIO_ID':'AN 456'}]}
    ref['capture_profile']='manual-v2'
    validate_entry(ref,[{}]);assert ref['attributes']['FT_EASEMENT_DOM_REG'][0]['FOLIO_ID']=='AN 456'
    parcels = deepcopy(entry);parcels['entry_type']='Description of Land'
    parcels['attributes']={
        'FT_FOLIO_TEXT':[{'_record_id':'t1','REGISTERED_DATE_DD':'12'},{'_record_id':'t2','REGISTERED_DATE_DD':'15'}],
        'FT_DESCRIPTION_OF_LAND_ENTRY':[{'_record_id':'p1','_parent_id':'t1','FOLIO_ENTRY_ID':'1'},{'_record_id':'p2','_parent_id':'t2','FOLIO_ENTRY_ID':'2'}],
        'FT_DESCRIPTION_OF_LAND_ENTRY_DOC':[{'_record_id':'d1','_parent_id':'p1'},{'_record_id':'d2','_parent_id':'p2'}],
        'FT_DESCRIPTION_OF_LAND_ENTRY_ADDRESSABLE_MANUAL_ADDR':[{'_record_id':'a1','_parent_id':'d1','Line_1_StreetName':'First Road'},{'_record_id':'a2','_parent_id':'d2','Line_1_StreetName':'Second Road'}]}
    matrix=manual_sheets([{'source_id':'synthetic','entries':[parcels]}])['Description of Land']
    assert next(row[3:] for row in matrix if row[2]=='REGISTERED_DATE_DD')==['12','15'], 'Keep separate parcel dates'
    assert next(row[3:] for row in matrix if row[2]=='Line_1_StreetName')==['First Road','Second Road']
    names=deepcopy(entry);names['entry_type']='Leasehold Particulars'
    names['attributes']={'FOLIO_PARTY':[{'FOLIO_PARTY_FORENAME':'William'},{'FOLIO_PARTY_FORENAME':'William'}]}
    matrix=manual_sheets([{'source_id':'names','entries':[names]}])['Leasehold Particulars']
    assert next(row[3] for row in matrix if row[2]=='FOLIO_PARTY_FORENAME')=='William | William'
    conditional=deepcopy(entry)
    conditional['attributes']={'FT_FOLIO_TEXT':[{'_record_id':'text','HAS_ENTRY_BEEN_SPLIT':'N'}],
        'FT_TOP':[{'_record_id':'top','FOLIO_ENTRIES_SPECIFIED':'N','NEW_FOLIO_ID_County':'Down'}],
        'FT_TOP_PART_OR_ENTRY':[{'_record_id':'part','PART_OF_FOLIO_ENTRY':'Y'}]}
    conditional['field_evidence']={'FT_TOP[0].NEW_FOLIO_ID_County':{'sources':[{'quote':'Folio DN 123'}]}}
    complete_capture(conditional,'synthetic')
    assert conditional['attributes']['FT_TOP_PART_OR_ENTRY'][0]['PART_OF_FOLIO_ENTRY']=='N'
    assert conditional['attributes']['FT_TOP'][0]['NEW_FOLIO_ID_County']==''
    assert conditional['attributes']['FT_FOLIO_TEXT'][0]['HAS_ENTRY_BEEN_SPLIT']==''
    docs=deepcopy(entry)
    docs['attributes']['FT_ENTRY_DOC']=[
        {'_record_id':'margin','DOC_REF_Type':'Document','DOC_REF':'A'},
        {'_record_id':'body','DOC_REF_Type':'Instrument','DOC_REF':'B'}]
    date={'page':1,'box':[10,10,100,30],'quote':'Registered 2000'}
    margin={'page':1,'box':[10,32,100,45],'quote':'Document A'}
    body={'page':1,'box':[400,400,700,430],'quote':'Instrument B'}
    docs['field_evidence']={'FT_FOLIO_TEXT[0].REGISTERED_DATE_YYYY':{'sources':[date]},
        'FT_ENTRY_DOC[0].DOC_REF':{'sources':[margin]},
        'FT_ENTRY_DOC[1].DOC_REF':{'sources':[body]}}
    complete_capture(docs,'synthetic')
    assert docs['attributes']['FT_ENTRY_DOC'][0]['DOC_REF']=='B'
    assert docs['field_evidence']['FT_ENTRY_DOC[0].DOC_REF']['sources']==[body]
    assert docs['field_evidence']['FT_MARGINAL_NOTE_DOC[0].DOC_REF']['sources']==[margin]
    once=deepcopy(docs);complete_capture(docs,'synthetic');assert docs==once
    print('Manual capture checks passed: defaults, references, suppression, idempotence and six-sheet layout')


if __name__ == '__main__': main()
