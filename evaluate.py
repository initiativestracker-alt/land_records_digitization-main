"""Compare human-labelled field references to evidence JSON; never treat OCR as truth.
Usage: .venv/bin/python evaluate.py evidence.json labels.json
Labels: [{"entry_id":"...", "field":"TABLE[0].FIELD", "expected":"..."}]
"""
import argparse, json, math
from pathlib import Path


def evaluate(folio, labels):
    entries={e['id']:e for e in folio['entries']}
    matches=0; failures=[]
    for label in labels:
        table, tail=label['field'].split('[',1); index, field=tail.split('].',1)
        entry=entries.get(label['entry_id'],{})
        records=entry.get('attributes',{}).get(table,[])
        actual=records[int(index)].get(field,'') if len(records)>int(index) else ''
        if str(actual or '')==str(label['expected'] or ''): matches+=1
        else: failures.append({**label,'actual':actual})
    n=len(labels); p=matches/n if n else None
    # One-sided 95% Wilson lower bound, supporting conservative acceptance decisions.
    z=1.6448536269514722
    lower=((p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)) if n else None
    return {'labelled_fields':n,'matched_fields':matches,'exact_accuracy':p,'lower_bound_95':lower,
            'target':.995,'target_supported':bool(n and lower>=.995), 'failures':failures}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('evidence');parser.add_argument('labels');args=parser.parse_args()
    print(json.dumps(evaluate(json.loads(Path(args.evidence).read_text()),json.loads(Path(args.labels).read_text())),indent=2))
