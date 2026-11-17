"""Ordered image folios and source identity shared by import, inference and exports."""

import hashlib
import io
import json
from pathlib import Path
import re
import warnings

from PIL import Image, ImageOps

JPEG_SUFFIXES = {".jpg", ".jpeg"}
MAX_BYTES = 50 * 1024 * 1024


def page_number(filename):
    match = re.match(r"^(?:page[-_ ]?)?(\d+)(?:[_\-. ]|$)", Path(filename).stem, re.I)
    if not match or int(match[1]) < 1:
        raise ValueError(f"Page filename must start with a positive page number: {filename}")
    return int(match[1])


def basename(value):
    if not value or value in {".", ".."} or len(value) > 240 or any(c in value for c in '/\\') or any(ord(c) < 32 for c in value):
        raise ValueError("Expected a plain folder or file name")
    return value


def image_folios(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Folio root is not a directory: {root}")
    images = [p for p in root.rglob("*")
                      if p.is_file() and p.suffix.lower() in JPEG_SUFFIXES
                      and not any(part.startswith('.') for part in p.relative_to(root).parts)]
    if any(p.is_symlink() for p in images):
        raise ValueError("JPEG pages must be files inside the supplied root, not symlinks")
    folders = sorted({p.parent for p in images})
    if not folders:
        raise ValueError(f"No JPEG folios found in {root}")
    return folders


def jpeg_image(content):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "JPEG":
                    raise ValueError("Expected JPEG page content")
                return ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("JPEG page is corrupt or exceeds safe image dimensions") from error


def ingest_images(folio_name, files, directory):
    """Validate the whole folio before publishing a source manifest."""
    from .export import atomic_write
    basename(folio_name)
    if not 1 <= len(files) <= 200:
        raise ValueError("An image folio must contain 1–200 JPEG pages")
    if sum(len(content) for _, content in files) > MAX_BYTES:
        raise ValueError("An image folio must be at most 50 MB")
    pages = []
    for filename, content in files:
        basename(filename)
        if Path(filename).suffix.lower() not in JPEG_SUFFIXES:
            raise ValueError(f"Expected a .jpg or .jpeg page: {filename}")
        with jpeg_image(content) as image:
            width, height = image.size
        pages.append({"source_page_number": page_number(filename), "original_filename": filename,
                      "hash": hashlib.sha256(content).hexdigest(), "width": width, "height": height})
    pages.sort(key=lambda p: p["source_page_number"])
    numbers = [p["source_page_number"] for p in pages]
    if len(numbers) != len(set(numbers)):
        raise ValueError("Duplicate page numbers in image folio")
    digest = hashlib.sha256(json.dumps({"folio_name": folio_name, "pages": pages}, sort_keys=True).encode()).hexdigest()
    source_id = re.sub(r"[^A-Za-z0-9_-]", "_", folio_name)[:50] + "-" + digest[:12]
    root = Path(directory) / source_id / digest
    content_by_name = dict(files)
    for number, page in enumerate(pages, 1):
        path = root / "originals" / page["original_filename"]
        atomic_write(path, content_by_name[page["original_filename"]])
        page.update(page=number, path=str(path))
    gaps = []
    previous = 0
    for number in numbers:
        if number > previous + 1:
            gaps.append([previous + 1, number - 1])
        previous = number
    return {"id": source_id, "filename": folio_name, "folio_name": folio_name, "kind": "image_folio",
            "hash": digest, "page_count": len(pages), "page_manifest": pages,
            "numbering_gaps": gaps}


def source_pages(source):
    if source.get("page_manifest") is not None:
        return source["page_manifest"]
    return [{"page": i, "source_page_number": i, "original_filename": path.name, "path": str(path)}
            for i, path in enumerate(sorted(Path(source["pages_path"]).glob("page-*.pdf")), 1)]


def prepare_page(page, destination):
    path = Path(page["path"])
    if path.suffix.lower() in JPEG_SUFFIXES:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with jpeg_image(path.read_bytes()) as image:
            image.save(destination, "PNG")
    else:
        from .vision import render_page
        render_page(path, destination)


def entry_page_numbers(folio, entry):
    return [folio["pages"][p-1].get("source_page_number", p)
            for p in sorted({r["page"] for r in entry["regions"]})]


def entry_image_names(folio, entry):
    if folio.get("source_kind") != "image_folio":
        return None
    return [folio["pages"][p-1]["original_filename"]
            for p in sorted({r["page"] for r in entry["regions"]})]
