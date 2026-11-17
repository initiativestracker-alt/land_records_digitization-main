"""Deploy: .venv/bin/modal deploy modal_app.py. Seed: modal run modal_app.py::seed."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid

import modal

ROOT = Path(__file__).parent
app = modal.App("folio-digitization")
artifacts = modal.Volume.from_name("folio-artifacts", create_if_missing=True)
state_volume = modal.Volume.from_name("folio-state", create_if_missing=True)
ocr_models = modal.Volume.from_name("folio-ocr-models", create_if_missing=True)
gemini_secret = modal.Secret.from_name("folio-gemini")
login_secret = modal.Secret.from_name("folio-login")
schema_file = ROOT / "LPS Folio Digitization Data Capture, Rules and Design v7.11 2024-11-24 Schema.xlsx"

cpu = (modal.Image.debian_slim(python_version="3.12")
       .apt_install("poppler-utils", "tesseract-ocr", "libglib2.0-0", "libgl1")
       .pip_install_from_requirements(str(ROOT / "requirements.txt"))
       .env({"SCHEMA_PATH": "/app/schema.xlsx", "GEMINI_MODEL": "gemini-3.8-flash"})
       .add_local_file(str(schema_file), "/app/schema.xlsx")
       .add_local_python_source("folio_capture")
       .add_local_file(str(ROOT / 'folio_capture/manual_layout.json'), '/root/folio_capture/manual_layout.json')
       .add_local_file(str(ROOT / "pdf_to_folio.py"), "/root/pdf_to_folio.py"))
web_image = cpu
if (ROOT / "frontend/dist").exists():
    web_image = cpu.add_local_dir(str(ROOT / "frontend/dist"), "/app/frontend")

gpu_image = (modal.Image.from_registry("paddlepaddle/paddle:3.2.1-gpu-cuda12.6-cudnn9.5")
             .apt_install("libgl1", "libglib2.0-0", "libgomp1")
             .pip_install("paddleocr[doc-parser]==3.3.2", "pillow", "numpy<3")
             .env({"PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True"}))


@app.cls(image=cpu, volumes={"/state": state_volume}, max_containers=1, timeout=120)
@modal.concurrent(max_inputs=1)
class State:
    @modal.enter()
    def initialize(self):
        from folio_capture.store import Store
        self.store = Store("/state/review.sqlite3")

    @modal.method()
    def call(self, operation, **args):
        # ponytail: one serialized writer; move to PostgreSQL before scaling state writers.
        result = self.store.call(operation, **args)
        transient_progress = operation == "update_batch" and "source_id" in args and not args.get("changes")
        if operation not in {"sources", "source", "batches", "batch", "folio", "folios", "audit"} and not transient_progress:
            state_volume.commit()
        return result


@app.cls(image=gpu_image, gpu="L4", volumes={"/root/.paddlex": ocr_models}, max_containers=2, timeout=1800, scaledown_window=120)
class DocumentOCR:
    @modal.enter()
    def initialize(self):
        from paddleocr import PPStructureV3
        self.pipeline = PPStructureV3(device="gpu:0", use_doc_orientation_classify=False,
            use_doc_unwarping=False, use_textline_orientation=False, use_formula_recognition=False,
            use_chart_recognition=False, text_detection_model_name="PP-OCRv5_server_det",
            text_recognition_model_name="PP-OCRv5_server_rec", layout_detection_model_name="PP-DocLayout_plus-L")
        ocr_models.commit()

    @modal.method()
    def run(self, images):
        import cv2
        import numpy as np
        output = []
        for content in images:
            image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
            height, width = image.shape[:2]
            result = next(iter(self.pipeline.predict(image)))
            raw = result.json
            if isinstance(raw, str): raw = json.loads(raw)
            raw = raw.get("res", raw)
            ocr = raw.get("overall_ocr_res", {})
            words = []
            boxes = ocr.get("rec_boxes", [])
            if not len(boxes):
                boxes = [[min(p[0] for p in poly), min(p[1] for p in poly),
                          max(p[0] for p in poly), max(p[1] for p in poly)] for poly in ocr.get("rec_polys", [])]
            for text, score, box in zip(ocr.get("rec_texts", []), ocr.get("rec_scores", []), boxes):
                words.append({"text": text, "score": float(score),
                              "box": [float(box[0]) / width * 1000, float(box[1]) / height * 1000,
                                      float(box[2]) / width * 1000, float(box[3]) / height * 1000]})
            layout = [{"label": box["label"], "score": float(box["score"]),
                       "box": [v / (width if i % 2 == 0 else height) * 1000 for i, v in enumerate(box["coordinate"])]}
                      for box in raw.get("layout_det_res", {}).get("boxes", [])]
            tables = [{"html": t.get("pred_html", ""), "cell_box_list": t.get("cell_box_list", []),
                       "table_index": t.get("table_index")} for t in raw.get("table_res_list", [])]
            output.append({"words": words, "layout": layout, "tables": tables,
                           "models": {"recognition": "PP-OCRv5_server_rec", "layout": "PP-DocLayout_plus-L"}})
        return output


@app.function(image=cpu, secrets=[gemini_secret], volumes={"/artifacts": artifacts},
              max_containers=12, timeout=3600, retries=1)
def extract_folio(source_id, batch_id, attempt):
    from folio_capture.engine import process_folio
    from folio_capture.export import atomic_write
    state = State()
    batch = state.call.remote("batch", id=batch_id)
    source = batch["sources"][source_id]
    artifacts.reload()
    directory = f"/artifacts/results/{batch_id}/{source_id}/attempt-{attempt}"
    def progress(stage):
        state.call.remote("update_batch", id=batch_id, attempt=attempt, source_id=source_id, stage=stage)
    try:
        folio = process_folio(source, batch_id, directory, DocumentOCR().run.remote, progress)
        artifacts.commit()
        state.call.remote("put_folio", folio=folio, attempt=attempt)
        progress("CSV ready")
        return {"source_id": source_id, "ok": True}
    except Exception as error:
        artifacts.commit()
        # Private diagnostics retain the traceback; public errors never contain API credentials.
        import traceback
        traceback.print_exc()
        return {"source_id": source_id, "ok": False, "error": f"{type(error).__name__}: extraction did not finish; retry or inspect the private worker logs"}


@app.function(image=cpu, volumes={"/artifacts": artifacts}, timeout=7200, max_containers=1)
def run_batch(batch_id, attempt):
    from folio_capture.export import atomic_write, batch_zip
    state = State()
    batch = state.call.remote("batch", id=batch_id)
    state.call.remote("update_batch", id=batch_id, attempt=attempt, changes={"status": "running"})
    remaining = [s for s in batch["source_ids"] if s not in batch["completed"]]
    errors = {}
    results = extract_folio.starmap([(s, batch_id, attempt) for s in remaining], return_exceptions=True)
    for source_id, result in zip(remaining, results):
        if isinstance(result, BaseException):
            errors[source_id] = f"{type(result).__name__}: worker stopped; resume this folio"
        elif not result["ok"]:
            errors[result["source_id"]] = result["error"]
    folios = state.call.remote("folios", batch_id=batch_id, version="extracted")
    batch = state.call.remote("batch", id=batch_id)
    manifest = {"batch_id": batch_id, "version": "extracted", "completed": batch["completed"], "errors": errors,
                "schema_hash": batch["schema_hash"], "source_ids": batch["source_ids"], "review_required": True}
    artifacts.reload()
    atomic_write(f"/artifacts/results/{batch_id}/extracted.zip", batch_zip(folios, manifest))
    atomic_write(f"/artifacts/results/{batch_id}/manifest.json", json.dumps(manifest, indent=2).encode())
    artifacts.commit()
    return state.call.remote("update_batch", id=batch_id, attempt=attempt,
        changes={"status": "complete_with_errors" if errors else "complete", "errors": errors, "downloads_ready": True})


@app.function(image=cpu, volumes={"/artifacts": artifacts}, timeout=600, max_containers=1)
def ingest(filename, content, preferred_id=None):
    from folio_capture.export import atomic_write
    from pdf_to_folio import convert_pdf
    digest = hashlib.sha256(content).hexdigest()
    source_id = preferred_id or (re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:50] + "-" + digest[:12])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id): raise ValueError("Invalid source ID")
    artifacts.reload()
    root = Path("/artifacts/sources") / source_id / digest
    root.mkdir(parents=True, exist_ok=True)
    pdf = root / "source.pdf"
    atomic_write(pdf, content)
    try:
        info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True, timeout=30).stdout
    except subprocess.SubprocessError as error:
        raise ValueError("PDF could not be read; check for corruption or encryption") from error
    count = int(next(line for line in info.splitlines() if line.startswith("Pages:")).split()[1])
    if not 1 <= count <= 200: raise ValueError("PDF must contain 1–200 pages")
    target = root / "pages" / "source"
    if not target.exists(): convert_pdf(pdf, root / "pages")
    source = {"id": source_id, "filename": Path(filename).name, "hash": digest,
              "page_count": count, "pages_path": str(target), "pdf_path": str(pdf)}
    artifacts.commit()
    return State().call.remote("put_source", source=source)


@app.function(image=cpu, volumes={"/artifacts": artifacts}, timeout=600, max_containers=1)
def ingest_folio(folio_name, files):
    from folio_capture.sources import ingest_images
    artifacts.reload()
    source = ingest_images(folio_name, files, "/artifacts/sources")
    artifacts.commit()
    return State().call.remote("put_source", source=source)


@app.function(image=cpu, secrets=[gemini_secret], volumes={"/artifacts": artifacts}, timeout=1800)
def extract_again(batch_id, source_id, entry_id, revision):
    from folio_capture.engine import extract_entry
    from folio_capture.vision import entry_signals
    state = State()
    folio = state.call.remote("folio", batch_id=batch_id, source_id=source_id)
    if folio["revision"] != revision: return {"error": "Revision changed; retry from refreshed page"}
    entry = next(e for e in folio["entries"] if e["id"] == entry_id)
    artifacts.reload()
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
        result["attributes"] = {}; result["field_evidence"] = {}; result["relationships"] = []
    return state.call.remote("review", batch_id=batch_id, source_id=source_id, revision=revision,
        action_id=uuid.uuid4().hex, reviewer="extraction-worker",
        action={"kind": "reextract_result", "entry_id": entry_id, "entry": result})


@app.function(image=web_image, secrets=[login_secret], volumes={"/artifacts": artifacts}, max_containers=1)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="folio-review")
def web():
    from folio_capture.api import create_app
    state = State()
    read_lock = threading.Lock()
    def read(path):
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(Path("/artifacts/results").resolve()):
            raise ValueError("Invalid artifact path")
        with read_lock:
            artifacts.reload()
            return resolved.read_bytes()
    def cancel(batch):
        if batch.get("worker_id"):
            modal.FunctionCall.from_id(batch["worker_id"]).cancel()
    return create_app(state.call.remote, lambda bid, attempt: run_batch.spawn(bid, attempt).object_id,
        ingest.remote, lambda b,s,e,r: extract_again.spawn(b,s,e,r).object_id, cancel, read,
        frontend="/app/frontend", ingest_folio=ingest_folio.remote)


@app.local_entrypoint()
def seed(start_batch: bool = False, folio_root: str = ""):
    deployed_ingest = modal.Function.from_name("folio-digitization", "ingest")
    deployed_state = modal.Cls.from_name("folio-digitization", "State")()
    deployed_batch = modal.Function.from_name("folio-digitization", "run_batch")
    ids = []
    if folio_root:
        from folio_capture.sources import image_folios, JPEG_SUFFIXES
        deployed_images = modal.Function.from_name("folio-digitization", "ingest_folio")
        for folder in image_folios(folio_root):
            files = [(p.name,p.read_bytes()) for p in folder.iterdir() if p.is_file() and p.suffix.lower() in JPEG_SUFFIXES]
            result = deployed_images.remote(folder.name, files)
            ids.append(result["id"])
            print(f"Seeded {folder.name}: {result['page_count']} JPEG pages", flush=True)
    else:
        for pdf in sorted((ROOT / "data").glob("*.pdf")):
            result = deployed_ingest.remote(pdf.name, pdf.read_bytes(), preferred_id=pdf.stem)
            ids.append(result["id"])
            print(f"Seeded {pdf.name}: {result['page_count']} pages")
    if start_batch:
        batch = deployed_state.call.remote("create_batch", source_ids=ids)
        function_call = deployed_batch.spawn(batch["id"], batch["attempt"])
        deployed_state.call.remote("update_batch", id=batch["id"], changes={"worker_id": function_call.object_id})
        print(f"Batch started: {batch['id']}")
