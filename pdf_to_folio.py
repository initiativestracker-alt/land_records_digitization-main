"""Split PDFs into named folio folders. Requires Poppler's pdfseparate."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def convert_pdf(source: Path, output: Path) -> Path:
    source, output = Path(source), Path(output)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {source}")
    folio = output / source.stem
    if folio.exists():
        raise FileExistsError(f"Folio already exists: {folio}")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        pages = Path(temporary) / "pages"
        pages.mkdir()
        subprocess.run(
            ["pdfseparate", str(source.resolve()), str(pages / "page-%04d.pdf")],
            check=True,
        )
        pages.rename(folio)
    return folio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=Path("data"),
                        help="PDF file or folder of PDFs (default: data)")
    parser.add_argument("--output", type=Path, default=Path("data/folios"))
    args = parser.parse_args()
    if not shutil.which("pdfseparate"):
        parser.error("Install Poppler (Ubuntu: sudo apt install poppler-utils)")
    sources = sorted(p for p in args.input.iterdir()
                     if p.is_file() and p.suffix.lower() == ".pdf") if args.input.is_dir() else [args.input]
    if not sources:
        parser.error(f"No PDFs found in {args.input}")
    for source in sources:
        try:
            folio = convert_pdf(source, args.output)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            parser.exit(1, f"{source}: {error}\n")
        print(f"{source.name} -> {folio} ({len(list(folio.glob('page-*.pdf')))} pages)")


if __name__ == "__main__":
    main()
