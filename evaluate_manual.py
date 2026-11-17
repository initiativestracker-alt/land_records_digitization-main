import pathlib,json,csv,io,zipfile,re,collections,decimal,os
import openpyxl
from folio_capture.manual import parcel_records
P=pathlib.Path(__file__).resolve().parent/'data/results'
B=P/'fa50da2ccf1e481c83031f9567d7e919'
OUT=pathlib.Path(os.environ.get('EVALUATION_OUTPUT','output/manual-evaluation'));OUT.mkdir(parents=True,exist_ok=True)
evidence_dir=pathlib.Path(os.environ.get('CANDIDATE_EVIDENCE_DIR',str(B)))
docs={f.stem.replace('-evidence',''):json.loads(f.read_text()) for f in evidence_dir.glob('*-evidence.json')}
def typ(s):return {'Easements':'Easement','Covenants':'Covenant'}.get(s,s)
def norm(v):
    if v is None:return ''
    s=re.sub(r'\s+',' ',str(v)).strip().casefold()
    if s=='blank':return ''
    if re.fullmatch(r'[+-]?[\d,]+(?:\.\d+)?',s):s=str(decimal.Decimal(s.replace(',','')).normalize())
    return s
def vals(v,preserve_duplicates=False):
    if not isinstance(v,list):v=str(v).split('|') if v is not None else []
    normalized=[norm(x) for x in v if norm(x)]
    return normalized if preserve_duplicates else list(dict.fromkeys(normalized))
manual=[]
for f in sorted(P.glob('*.xlsx')):
    for s in openpyxl.load_workbook(f,data_only=True):
        for c in range(4,s.max_column+1):
            if s.cell(4,c).value is None:continue
            fields=[]
            for r in range(4,s.max_row+1):
                table=s.cell(r,2).value;field=s.cell(r,3).value
                if not table or not field:continue
                fields.append((table.split('|')[-1].strip(),field.strip(),s.cell(r,c).value,s.cell(r,c).coordinate))
            manual.append(dict(book=f.name,type=s.title,sid=str(s.cell(4,c).value),page=s.cell(1,c).value,number=s.cell(2,c).value,col=c,fields=fields))
special={
 ('23548','Transfer of Part',4,1):'p4_top_1',
 ('23548','Easement',3,5):'p3_lease_1_ease',('23548','Easement',4,2):'p4_easement_1',
 ('23548','Easement',4,5):'p4_easement_2',('23548','Easement',5,3):'p5_appurt_1',
 ('23548','Easement',5,4):'p5_easement_1',('23548','Easement',6,2):'p6_easement_1',
 ('612898','Leasehold Particulars',1,2):'p1_e2',
}
links=[]
for m in manual:
    candidates=docs.get(m['sid'],{}).get('entries',[]);key=(m['sid'],m['type'],m['page'],m['number'])
    if key in special:
        cs=[e for e in candidates if e['key']==special[key]]
        if cs and typ(cs[0]['entry_type'])!=m['type']:
            from apply_inventory import overlap
            replacements=[e for e in candidates if typ(e['entry_type'])==m['type'] and overlap(cs[0],e)>=.25]
            if replacements:cs=[max(replacements,key=lambda e:overlap(cs[0],e))]
    else:
        cs=[e for e in candidates if typ(e['entry_type'])==m['type'] and m['page'] in [r['page'] for r in e['regions']]]
        if m['type']=='Transfer of Part' and len(cs)>1:
            target=next(v for t,f,v,c in m['fields'] if t=='FT_TOP' and f=='NEW_FOLIO_ID_Number')
            cs=[e for e in cs if any(norm(r.get('NEW_FOLIO_ID_Number'))==norm(target) for r in e['attributes'].get('FT_TOP',[]))]
    assert len(cs)<=1,(key,[e['key'] for e in cs])
    e=cs[0] if cs else None;m['entry']=e
    m['child_index']=m['number']-1 if m['type']=='Description of Land' and m['sid'] in ['23949','25001','783758'] else None
    links.append({k:m[k] for k in ['book','type','sid','page','number','col']}|dict(system_key=e['key'] if e else '',entry_id=e['id'] if e else '',system_type=e['entry_type'] if e else '',child_index=m['child_index'],status='linked' if e else ('unprocessed_document' if m['sid'] not in docs else 'no_page_entry_match')))
allrows=[];summary={}
for version in ['extracted','current']:
    z=zipfile.ZipFile(os.environ.get('CANDIDATE_ZIP',str(B/'current.zip')) if version=='current' else B/'extracted.zip');rows={}
    for n in z.namelist():
        if n.endswith('.csv'):
            for r in csv.DictReader(io.StringIO(z.read(n).decode('utf-8-sig'))):rows[(n[:-4],r['entry_id'])]=r
    comparisons=[]
    for m in manual:
        e=m['entry'];r=rows.get((m['sid'],e['id'])) if e else None
        for t,f,v,cell in m['fields']:
            if f in ['FOLIO_IMAGE_FILENAME','FT_ENTRY_TEXT_TYPE_NAME']:continue
            pred=[]
            if r and r.get(t):
                attrs={table:json.loads(value) for table,value in r.items() if table.startswith(('FT_','FOLIO_')) and '.' not in table and value}
                records=parcel_records(attrs,t,m['child_index'])
                pred=[rec.get(f,'') for rec in records]
            preserve=t=='FOLIO_PARTY' and f not in {'IS_PERSON','FOLIO_PARTY_ID'}
            expected=vals(v,preserve);actual=vals(pred,preserve)
            # An absent entry never earns blank-cell credit.
            match=bool(r) and expected==actual
            comparisons.append(dict(version=version,book=m['book'],type=m['type'],sid=m['sid'],page=m['page'],entry_number=m['number'],cell=cell,system_key=e['key'] if e else '',table=t,field=f,manual_value='' if v is None else str(v),system_value=' | '.join(str(x) for x in pred),manual_nonblank=bool(expected),system_nonblank=bool(actual),match=match,linked=bool(r),processed=m['sid'] in docs))
    def stats(rs):
        nb=[r for r in rs if r['manual_nonblank']];pop=[r for r in nb if r['system_nonblank']]
        return dict(cells=len(rs),cell_matches=sum(r['match'] for r in rs),expected_nonblank=len(nb),populated_expected=len(pop),correct_nonblank=sum(r['match'] for r in nb),correct_among_populated=sum(r['match'] for r in pop),extra_values_on_blank=sum(not r['manual_nonblank'] and r['system_nonblank'] for r in rs))
    summary[version]={'processed':stats([r for r in comparisons if r['processed']]),'all':stats(comparisons),'by_type':{t:stats([r for r in comparisons if r['processed'] and r['type']==t]) for t in dict.fromkeys(m['type'] for m in manual)}}
    allrows+=comparisons
def writecsv(name,rows):
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
writecsv('entry_links.csv',links);writecsv('field_comparison.csv',allrows)
headers=[]
for m in manual:
    e=m['entry']
    number=e.get('page_entry_number') if e else None
    if isinstance(number,int) and m['child_index'] is not None:
        number+=m['child_index']
    headers.append(dict(book=m['book'],type=m['type'],sid=m['sid'],page=m['page'],
                        manual_entry_number=m['number'],system_entry_number=number,
                        system_key=e['key'] if e else '',match=bool(e) and number==m['number'],
                        processed=m['sid'] in docs))
writecsv('header_comparison.csv',headers)
writecsv('remaining_discrepancies.csv',[r for r in allrows if r['version']=='current' and r['processed'] and not r['match']])
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
print('ENTRY NUMBERS',sum(r['match'] for r in headers),'/',sum(r['processed'] for r in headers))
print('ENTRIES',len(manual),'processed',sum(m['sid'] in docs for m in manual),'linked',sum(bool(m['entry']) for m in manual),'correct_type',sum(bool(m['entry']) and typ(m['entry']['entry_type'])==m['type'] for m in manual))
print('DOCS',len({m['sid'] for m in manual}),len({m['sid'] for m in manual if m['sid'] in docs}))
print(json.dumps(summary,indent=2))
print('UNLINKED',[r for r in links if r['status']=='no_page_entry_match'])
