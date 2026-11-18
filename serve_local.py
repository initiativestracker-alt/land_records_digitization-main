"""Run the folio capture app locally, without Modal.

Serves the same ASGI app as the deployed service: the built UI, the API, page
images and exports, authenticated with HTTP Basic. Extraction runs in local
threads instead of Modal containers. The GPU PaddleOCR step calls the deployed
Modal ``DocumentOCR`` function remotely (hybrid mode) so this server produces
the same high-quality OCR/layout evidence as the Modal deployment; if Modal is
unreachable it falls back to a Tesseract-only stub (see gpu_ocr below).

Usage:
    python serve_local.py

Configuration (environment, or a .env file beside this script):
    APP_USERNAME / APP_PASSWORD   HTTP Basic credentials; password must be >= 16 chars
    GEMINI_API_KEY                required for extraction
    GEMINI_MODEL                  optional, defaults to gemini-3.8-flash
    FOLIO_HOST / FOLIO_PORT       optional, default 0.0.0.0:8080
    FOLIO_STATE                   SQLite path, default runtime/state/review.sqlite3
    FOLIO_ARTIFACTS               artifacts root, default runtime/artifacts
    SCHEMA_PATH                   optional; defaults to the single *.xlsx in this folder

External programs that must be on PATH:
    tesseract                     all extraction
    pdfinfo, pdfseparate, pdftoppm   PDF ingest and page rendering (Poppler)

For hybrid OCR (Modal DocumentOCR), the local environment must have a Modal
token for the workspace where folio-digitization is deployed, and the
``DocumentOCR`` function must be deployed. Set the token with
``modal token new`` or ``modal token set``; the client reads it from
~/.modal.toml or the MODAL_TOKEN_ID / MODAL_TOKEN_SECRET environment variables.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
STATE_PATH = Path(os.getenv("FOLIO_STATE", ROOT / "runtime" / "state" / "review.sqlite3"))
ARTIFACTS = Path(os.getenv("FOLIO_ARTIFACTS", ROOT / "runtime" / "artifacts"))
SOURCES = ARTIFACTS / "sources"
RESULTS = ARTIFACTS / "results"

from folio_capture.api import create_app
from folio_capture.engine import extract_entry, process_folio
from folio_capture.export import atomic_write, batch_zip
from folio_capture.sources import ingest_images
from folio_capture.store import Store
from folio_capture.vision import entry_signals

store = Store(STATE_PATH)
cancellations = {}


def gpu_ocr(images):
    """Run GPU PaddleOCR via the deployed Modal DocumentOCR function.

    Returns full word, layout and table evidence identical to the Modal
    deployment, so this server produces the same high-quality extraction.
    If Modal is unreachable (no token, network failure, or the function is not
    deployed), falls back to a Tesseract-only result and logs a warning — the pipeline
    still completes but loses the independent OCR cross-check.
    """
    try:
        import modal
        ocr = modal.Cls.from_name("folio-digitization", "DocumentOCR")()
        return ocr.run.remote(images)
    except Exception as error:
        import traceback
        print(f"WARNING: Modal DocumentOCR unavailable "
              f"({type(error).__name__}: {error}); "
              f"falling back to Tesseract-only OCR. "
              f"OCR cross-check will be unavailable.")
        traceback.print_exc()
        return [{"words": [], "layout": [], "tables": [],
                 "models": {"engine": "tesseract-only"}} for _ in images]


def progress_reporter(batch_id, attempt, source_id):
    def report(stage):
        store.call("update_batch", id=batch_id, attempt=attempt, source_id=source_id, stage=stage)
    return report


def extract_folio(source_id, batch_id, attempt):
    batch = store.call("batch", id=batch_id)
    source = batch["sources"][source_id]
    directory = RESULTS / batch_id / source_id / f"attempt-{attempt}"
    try:
        folio = process_folio(source, batch_id, str(directory), gpu_ocr,
                              progress_reporter(batch_id, attempt, source_id))
        store.call("put_folio", folio=folio, attempt=attempt)
        return {"source_id": source_id, "ok": True}
    except Exception as error:
        # Private diagnostics stay in the server log; the public error carries no credentials.
        import traceback
        traceback.print_exc()
        return {"source_id": source_id, "ok": False,
                "error": f"{type(error).__name__}: extraction did not finish; retry or inspect the server log"}


def run_batch(batch_id, attempt, cancelled):
    batch = store.call("batch", id=batch_id)
    store.call("update_batch", id=batch_id, attempt=attempt, changes={"status": "running"})
    remaining = [s for s in batch["source_ids"] if s not in batch["completed"]]
    errors = {}
    for source_id in remaining:
        if cancelled.is_set():
            return store.call("update_batch", id=batch_id, attempt=attempt, changes={"status": "interrupted"})
        result = extract_folio(source_id, batch_id, attempt)
        if not result["ok"]:
            errors[result["source_id"]] = result["error"]
    if cancelled.is_set():
        return store.call("update_batch", id=batch_id, attempt=attempt, changes={"status": "interrupted"})
    folios = store.call("folios", batch_id=batch_id, version="extracted")
    batch = store.call("batch", id=batch_id)
    manifest = {"batch_id": batch_id, "version": "extracted", "completed": batch["completed"], "errors": errors,
                "schema_hash": batch["schema_hash"], "source_ids": batch["source_ids"], "review_required": True}
    atomic_write(RESULTS / batch_id / "extracted.zip", batch_zip(folios, manifest))
    atomic_write(RESULTS / batch_id / "manifest.json", json.dumps(manifest, indent=2).encode())
    return store.call("update_batch", id=batch_id, attempt=attempt,
                      changes={"status": "complete_with_errors" if errors else "complete",
                               "errors": errors, "downloads_ready": True})


def launch_batch(batch_id, attempt):
    worker_id = uuid.uuid4().hex
    cancelled = threading.Event()
    cancellations[worker_id] = cancelled
    threading.Thread(target=run_batch, args=(batch_id, attempt, cancelled), daemon=True).start()
    return worker_id


def cancel_batch(batch):
    # Threads cannot be killed; the running folio finishes and the rest are skipped.
    cancelled = cancellations.get(batch.get("worker_id") or "")
    if cancelled:
        cancelled.set()


def read_artifact(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(RESULTS.resolve()):
        raise ValueError("Invalid artifact path")
    return resolved.read_bytes()


def ingest_pdf(filename, content, preferred_id=None):
    from pdf_to_folio import convert_pdf
    digest = hashlib.sha256(content).hexdigest()
    source_id = preferred_id or (re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:50] + "-" + digest[:12])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
        raise ValueError("Invalid source ID")
    root = SOURCES / source_id / digest
    root.mkdir(parents=True, exist_ok=True)
    pdf = root / "source.pdf"
    atomic_write(pdf, content)
    try:
        info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError) as error:
        raise ValueError("PDF could not be read; install Poppler and check for corruption or encryption") from error
    count = int(next(line for line in info.splitlines() if line.startswith("Pages:")).split()[1])
    if not 1 <= count <= 200:
        raise ValueError("PDF must contain 1-200 pages")
    target = root / "pages" / "source"
    if not target.exists():
        convert_pdf(pdf, root / "pages")
    source = {"id": source_id, "filename": Path(filename).name, "hash": digest,
              "page_count": count, "pages_path": str(target), "pdf_path": str(pdf)}
    return store.call("put_source", source=source)


def ingest_folio(folio_name, files):
    return store.call("put_source", source=ingest_images(folio_name, files, str(SOURCES)))


def _reextract(batch_id, source_id, entry_id, revision):
    folio = store.call("folio", batch_id=batch_id, source_id=source_id)
    if folio["revision"] != revision:
        return {"error": "Revision changed; retry from refreshed page"}
    entry = next(e for e in folio["entries"] if e["id"] == entry_id)
    try:
        entry["reasons"] = []
        directory = Path(folio["directory"]) / "pages"
        entry["signals"] = entry_signals(entry, folio["pages"], directory)
        result, usage = extract_entry(entry, folio, directory)
    except Exception as error:
        import traceback
        traceback.print_exc()
        result = entry
        result["reasons"] = ["ENTRY_EXTRACTION_FAILED"]
        result["extraction_error"] = type(error).__name__
        result["attributes"] = {}
        result["field_evidence"] = {}
        result["relationships"] = []
    return store.call("review", batch_id=batch_id, source_id=source_id, revision=revision,
                      action_id=uuid.uuid4().hex, reviewer="extraction-worker",
                      action={"kind": "reextract_result", "entry_id": entry_id, "entry": result})


def reextract(batch_id, source_id, entry_id, revision):
    worker_id = uuid.uuid4().hex
    cancellations[worker_id] = threading.Event()
    threading.Thread(target=_reextract, args=(batch_id, source_id, entry_id, revision), daemon=True).start()
    return worker_id


def build():
    frontend = ROOT / "frontend" / "dist"
    return create_app(
        lambda operation, **args: store.call(operation, **args),
        launch_batch,
        ingest_pdf,
        reextract,
        cancel_batch,
        read_artifact,
        frontend=str(frontend) if frontend.exists() else None,
        ingest_folio=ingest_folio,
    )


if __name__ == "__main__":
    import uvicorn
    if not (ROOT / "frontend" / "dist").exists():
        print("WARNING: frontend/dist is missing. Run 'npm run build' in frontend/ first, "
              "otherwise the API works but no UI is served.")
    if not os.getenv("GEMINI_API_KEY"):
        print("WARNING: GEMINI_API_KEY is not set. Extraction will fail when a batch is started.")
    print(f"State DB:  {STATE_PATH}")
    print(f"Artifacts: {ARTIFACTS}")
    uvicorn.run(build(), host=os.getenv("FOLIO_HOST", "0.0.0.0"), port=int(os.getenv("FOLIO_PORT", "8080")))
