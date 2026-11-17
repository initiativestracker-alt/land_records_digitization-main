import {useEffect, useRef, useState} from 'react'

export type PageInfo = {page:number,source_page_number?:number,original_filename?:string}
export type Annotation = {id:string,name:string,value:string,entry_id:string|null,entry_type:string,table?:string,record?:number,pages:number[],basis:string,rule:string,sources:{page:number,box:number[],quote:string,locator:'ocr'|'model'}[]}
type Folio = {source_id:string,source_filename:string,folio_name?:string,revision:number,pages:PageInfo[],entries:{id:string,entry_type:string,status:string,regions:{page:number}[]}[],annotations?:Annotation[]}
const palette = ['#147d73','#a5499c','#275eb0','#b56b13','#8a49b1','#b43e54','#397944','#865134']
const title = (value:string) => value.replaceAll('_',' ').replace(/^FT /,'')
function color(id:string) {let hash=0;for(const c of id)hash=(hash*31+c.charCodeAt(0))|0;return palette[(hash>>>0)%palette.length]}

export default function AnnotatedView({folio,batchId}:{folio:Folio,batchId:string}) {
  const [page,setPage] = useState(1), [zoom,setZoom] = useState(100), [selected,setSelected] = useState('')
  const canvas = useRef<HTMLDivElement>(null), rows = useRef(new Map<string,HTMLButtonElement>())
  const attributes = folio.annotations || []
  const shown = attributes.filter(a=>!a.entry_id || a.pages.includes(page) || a.sources.some(s=>s.page===page))
  const info = folio.pages[page-1]
  useEffect(()=>{setSelected('')},[folio.revision])
  function select(attribute:Annotation, destination=page, focusRow=false) {
    setPage(destination);setSelected(attribute.id)
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      const source=attribute.sources.find(s=>s.page===destination)
      if(source&&canvas.current) {
        const scroller=canvas.current.parentElement!
        scroller.scrollTo({top:canvas.current.offsetTop+source.box[1]/1000*canvas.current.clientHeight-70,
          left:Math.max(0,source.box[0]/1000*canvas.current.clientWidth-40),behavior:'smooth'})
      }
      if(focusRow){rows.current.get(attribute.id)?.scrollIntoView({block:'nearest',behavior:'smooth'});rows.current.get(attribute.id)?.focus({preventScroll:true})}
    }))
  }
  const status = (a:Annotation) => a.sources.length ? (a.sources.some(s=>s.locator==='model')?'Source location · OCR unconfirmed':'Source located by OCR') :
    a.basis==='input'?'Provided by input':a.basis==='default'?'Schema default · no page source':a.basis==='human'?'Human correction · no page source':'No located source'
  return <div className="annotated-grid">
    <section className="document-panel annotated-document"><div className="document-toolbar"><div>
      <button aria-label="Previous annotated page" disabled={page===1} onClick={()=>{setPage(page-1);setSelected('')}}>‹</button>
      <label>Page <select aria-label="Annotated page" value={page} onChange={e=>{setPage(+e.target.value);setSelected('')}}>{folio.pages.map((p,i)=><option key={i} value={i+1}>{p.source_page_number??i+1}</option>)}</select></label>
      <span>of {folio.pages.length}</span><button aria-label="Next annotated page" disabled={page===folio.pages.length} onClick={()=>{setPage(page+1);setSelected('')}}>›</button>
    </div><label>Zoom <select aria-label="Annotation zoom" value={zoom} onChange={e=>setZoom(+e.target.value)}>{[75,100,125,150,200].map(v=><option key={v} value={v}>{v===100?'Fit to width':`${v}%`}</option>)}</select></label></div>
      <div className="source-page-caption"><strong>{folio.folio_name||folio.source_filename}</strong><span>{info?.original_filename||`Page ${page}`} · source page {info?.source_page_number??page}</span></div>
      <div className="document-scroll"><div ref={canvas} className="page-canvas" style={{width:`${zoom}%`}}>
        <img alt={`Folio ${folio.folio_name||folio.source_filename}, source page ${info?.source_page_number??page}`} src={`/api/batches/${batchId}/folios/${folio.source_id}/pages/${page}`} onLoad={()=>{const a=attributes.find(a=>a.id===selected);if(a)select(a,page)}}/>
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="Attribute source locations">
          {attributes.flatMap((a,index)=>a.sources.filter(s=>s.page===page).map((s,n)=><g key={`${a.id}-${n}`} role="button" tabIndex={0} aria-label={`Source ${index+1}: ${title(a.name)}: ${a.value}`} onClick={()=>select(a,page,true)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(a,page,true)}}} className={`source-mark ${selected&&selected!==a.id?'dimmed':''} ${selected===a.id?'active':''}`}>
            <title>{`${index+1}. ${title(a.name)}: ${a.value} · ${status(a)}`}</title>
            <rect x={s.box[0]} y={s.box[1]} width={s.box[2]-s.box[0]} height={s.box[3]-s.box[1]} fill={color(a.id)} fillOpacity={selected===a.id?.24:.1} stroke={color(a.id)} strokeWidth={selected===a.id?3:1.5} strokeDasharray={s.locator==='model'?'5 3':undefined}/>
            <text x={s.box[0]} y={Math.max(12,s.box[1]-3)} fill={color(a.id)}>{index+1}</text>
          </g>))}
        </svg>
      </div></div><p className="document-caption">Colour and number link each mark to its attribute. Dashed marks have no confirmed OCR location.</p>
    </section>
    <section className="annotated-attributes"><div className="section-heading"><div><div className="eyebrow">SOURCE PAGE {info?.source_page_number??page}</div><h2>Extracted attributes</h2><p>Select an attribute or a coloured source mark.</p></div><span className="status-pill">Read only</span></div>
      <div className="annotated-list">
        {folio.entries.filter(e=>e.status&&e.regions.some(r=>r.page===page)).map(e=><p className="suppression-note" key={e.id}>{e.entry_type} · {({Y:'Struck through',C:'Cancelled',U:'Uncertain',B:'Blank page',F:'Cover sheet'} as Record<string,string>)[e.status]||e.status} · body attributes suppressed</p>)}
        {shown.map(a=>{const index=attributes.findIndex(x=>x.id===a.id);return <div key={a.id} className={`annotated-attribute ${selected===a.id?'selected':''}`}>
          <button ref={el=>{if(el)rows.current.set(a.id,el);else rows.current.delete(a.id)}} className="annotated-field" onClick={()=>select(a,a.sources.some(s=>s.page===page)?page:a.sources[0]?.page||page)}>
            <span className="source-badge" style={{background:color(a.id)}}>{index+1}</span><span><small>{a.entry_type}{a.table&&` · ${title(a.table)} · record ${a.record}`}</small><strong>{title(a.name)}</strong><span className="annotated-value">{a.value}</span><small className="source-status">{status(a)}</small></span>
          </button>
          {a.sources.length>0&&<div className="annotation-evidence">{[...new Set(a.sources.map(s=>s.page))].map(p=><button className="text-button" key={p} onClick={()=>select(a,p)}>Page {folio.pages[p-1]?.source_page_number??p} ↗</button>)}{selected===a.id&&<p>“{a.sources.find(s=>s.page===page)?.quote||a.sources[0].quote}”</p>}</div>}
          {selected===a.id&&a.rule&&<p className="annotation-rule">{a.rule}</p>}
        </div>})}
        {shown.length===0&&<p className="empty">No extracted attributes on this page.</p>}
      </div>
    </section>
  </div>
}
