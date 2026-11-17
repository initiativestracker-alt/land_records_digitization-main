"""Checkpointed local extraction experiments; never reads manual answer workbooks."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

from folio_capture.engine import extract_entry, PIPELINE_VERSION
from folio_capture.export import atomic_write, batch_zip
from folio_capture.manual import complete_capture, manual_sheets
from folio_capture.schema import TYPES, capture_schema
from folio_capture.vision import render_page


def experiment_fingerprint(model, baseline):
    digest = hashlib.sha256(model.encode())
    digest.update(Path(baseline).read_bytes())
    digest.update(capture_schema()['hash'].encode())
    for filename in ['engine.py', 'manual.py', 'schema.py', 'checks.py']:
        digest.update((Path(__file__).parent / 'folio_capture' / filename).read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path('data/results/fa50da2ccf1e481c83031f9567d7e919'))
    parser.add_argument('--output', type=Path, default=Path('output/manual-v2'))
    parser.add_argument('--sources', default='')
    parser.add_argument('--keys', default='')
    parser.add_argument('--extract', action='store_true')
    parser.add_argument('--missing-only', action='store_true', help='Extract only empty or previously failed active entries')
    parser.add_argument('--model', default='gemini-3.8-flash')
    args = parser.parse_args()
    for line in Path('.env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))
    os.environ['GEMINI_MODEL'] = args.model
    args.output.mkdir(parents=True, exist_ok=True)
    fingerprint = experiment_fingerprint(args.model, args.baseline / 'current.zip')
    cache_dir = args.output / 'entries' / fingerprint
    cached_count = 0
    succeeded = 0
    errors = []
    folios = []
    baseline = zipfile.ZipFile(args.baseline / 'current.zip')
    tables = capture_schema()['tables']
    for path in sorted(args.baseline.glob('*-evidence.json')):
        folio = json.loads(path.read_text())
        raw = list(csv.DictReader(io.StringIO(baseline.read(folio['source_id'] + '.csv').decode('utf-8-sig'))))
        rows = {r['entry_id']: r for r in raw}
        for entry in folio['entries']:
            if entry['id'] in rows:
                entry['attributes'] = {t: json.loads(rows[entry['id']][t]) for t in tables if rows[entry['id']].get(t)}
            cached = cache_dir / (folio['source_id'] + '-' + entry['id'] + '.json')
            if cached.exists():
                entry.update(json.loads(cached.read_text()))
                cached_count += 1
            complete_capture(entry, folio['source_id'])
        folio['pipeline_version'] = PIPELINE_VERSION
        folios.append(folio)
    jobs = []
    for folio in folios:
        if args.sources and folio['source_id'] not in args.sources.split(','): continue
        for entry in folio['entries']:
            if entry['entry_type'] not in TYPES or entry.get('status'): continue
            if args.keys and entry['key'] not in args.keys.split(','): continue
            if args.missing_only and entry.get('attributes') and 'ENTRY_EXTRACTION_FAILED' not in entry.get('reasons', []): continue
            cached = cache_dir / (folio['source_id'] + '-' + entry['id'] + '.json')
            if not cached.exists(): jobs.append((folio, entry, cached))
    if args.extract:
        # Render before parallel requests so shared page files are never partially read.
        for folio, entry, _ in jobs:
            directory = args.output / 'pages' / folio['source_id']
            for page in {r['page'] for r in entry['regions']}:
                png = directory / f'page-{page:04d}.png'
                if not png.exists(): render_page(Path('data/folios') / folio['source_id'] / f'page-{page:04d}.pdf', png)
        def run(job):
            folio, entry, cached = job
            proposed = deepcopy(entry)
            proposed['reasons'] = []
            result, usage = extract_entry(proposed, folio, args.output / 'pages' / folio['source_id'])
            result['experiment_model'] = args.model
            result['experiment_fingerprint'] = fingerprint
            result['experiment_usage'] = usage
            atomic_write(cached, json.dumps(result, ensure_ascii=False, indent=2).encode())
            entry.update(result)
            return folio['source_id'], entry['key'], usage
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(run, job): job for job in jobs}
            for future in as_completed(pending):
                job = pending[future]
                try:
                    print('EXTRACTED', future.result(), flush=True)
                    succeeded += 1
                except Exception as error:
                    errors.append({'source_id':job[0]['source_id'], 'entry_id':job[1]['id'],
                                   'error_type':type(error).__name__, 'output':'baseline retained'})
                    print('FAILED', job[0]['source_id'], job[1]['key'], type(error).__name__, str(error)[:120], flush=True)
    manifest = {'pipeline_version': PIPELINE_VERSION, 'requested_model': args.model, 'baseline': str(args.baseline),
                'capture_profile': 'manual-v2', 'review_required': True, 'experiment_fingerprint':fingerprint,
                'extraction_requested':args.extract, 'entries_scheduled':len(jobs) if args.extract else 0,
                'fresh_entries':succeeded, 'cached_entries':cached_count, 'refresh_errors':errors}
    atomic_write(args.output / 'manifest.json', json.dumps(manifest,indent=2).encode())
    atomic_write(args.output / 'current.zip', batch_zip(folios, manifest))
    atomic_write(args.output / 'manual_sheets.json', json.dumps(manual_sheets(folios), ensure_ascii=False).encode())
    for folio in folios:
        atomic_write(args.output / (folio['source_id'] + '-evidence.json'), json.dumps(folio, ensure_ascii=False).encode())
    print('SAVED', args.output, 'folios', len(folios), flush=True)


if __name__ == '__main__': main()
