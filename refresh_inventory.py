"""Re-inventory saved source evidence without loading manual answer workbooks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import hashlib
from pathlib import Path

from folio_capture.engine import inventory_folio
from folio_capture.export import atomic_write
from folio_capture.vision import render_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path('data/results/fa50da2ccf1e481c83031f9567d7e919'))
    parser.add_argument('--output', type=Path, default=Path('output/manual-v2-inventory'))
    parser.add_argument('--sources', default='')
    args = parser.parse_args()
    for line in Path('.env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))
    os.environ['GEMINI_MODEL'] = 'gemini-3.8-flash'
    config_hash = hashlib.sha256(Path('folio_capture/engine.py').read_bytes()).hexdigest()

    def run(path):
        folio = json.loads(path.read_text())
        sid = folio['source_id']
        if args.sources and sid not in args.sources.split(','): return
        dest = args.output / f'{sid}-inventory.json'
        fingerprint = hashlib.sha256((config_hash + path.read_text()).encode()).hexdigest()
        if dest.exists() and json.loads(dest.read_text()).get('fingerprint') == fingerprint:
            print('CACHED', sid, flush=True)
            return
        directory = args.output / 'pages' / sid
        for page in folio['pages']:
            number = page['page']
            png = directory / f'page-{number:04d}.png'
            if not png.exists():
                render_page(Path('data/folios') / sid / f'page-{number:04d}.pdf', png)
        inventory, usage = inventory_folio(folio['pages'], directory, folio['source_filename'])
        atomic_write(dest, json.dumps({'inventory':inventory, 'usage':usage, 'fingerprint':fingerprint}, ensure_ascii=False).encode())
        print('INVENTORIED', sid, len(inventory['entries']), flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run, sorted(args.baseline.glob('*-evidence.json'))))


if __name__ == '__main__':
    main()
