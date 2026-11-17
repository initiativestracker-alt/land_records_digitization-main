"""Authenticated same-origin React UI, API, images, and revision-safe downloads."""

import asyncio
import base64
from functools import partial
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .export import batch_zip, csv_bytes
from .manual import manual_xlsx_bytes
from .schema import capture_schema
from .store import Conflict
from .annotations import annotated_attributes


class BatchRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=500)


class ReviewRequest(BaseModel):
    revision: int = Field(ge=1)
    action_id: str = Field(min_length=8, max_length=100)
    action: dict


def create_app(state_call, launch_batch, ingest_pdf, reextract, cancel_batch, read_artifact, frontend=None, ingest_folio=None):
    username, password = os.environ.get("APP_USERNAME"), os.environ.get("APP_PASSWORD")
    if not username or not password or len(password) < 16:
        raise RuntimeError("APP_USERNAME and a strong APP_PASSWORD must be configured")
    app = FastAPI(title="Folio capture", docs_url=None, redoc_url=None, openapi_url=None)

    async def call(operation, **args):
        try:
            return await asyncio.to_thread(partial(state_call, operation, **args))
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "Record not found") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.middleware("http")
    async def credentials(request: Request, next_handler):
        valid = False
        header = request.headers.get("authorization", "")
        try:
            if header.startswith("Basic "):
                name, given = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
                valid = secrets.compare_digest(name.encode(), username.encode()) & secrets.compare_digest(given.encode(), password.encode())
        except (ValueError, UnicodeError):
            pass
        if not valid:
            return Response("Authentication required", 401, headers={"WWW-Authenticate": 'Basic realm="Folio capture", charset="UTF-8"', "Cache-Control": "no-store"})
        request.state.reviewer = username
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and (urlsplit(origin).netloc != request.headers.get("host") or urlsplit(origin).scheme not in {"https", "http"}):
                return Response("Cross-origin mutation rejected", 403)
            if request.method == "OPTIONS":
                return Response("Cross-origin access disabled", 403)
        response = await next_handler(request)
        response.headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY"})
        return response

    @app.get("/api/schema")
    async def schema():
        return capture_schema()

    @app.get("/api/sources")
    async def sources():
        return [{k: source.get(k) for k in ["id", "filename", "folio_name", "kind", "hash", "page_count", "uploaded_at", "numbering_gaps"]} for source in await call("sources")]

    @app.post("/api/sources/folio")
    async def upload_folio(folio_name: str = Form(...), files: list[UploadFile] = File(...)):
        if ingest_folio is None: raise HTTPException(503, "Image folio ingestion is unavailable")
        if not 1 <= len(files) <= 200: raise HTTPException(422, "A folio must contain 1–200 JPEG pages")
        pages, total = [], 0
        for file in files:
            content = bytearray()
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 50 * 1024 * 1024: raise HTTPException(413, "An image folio must be at most 50 MB")
                content.extend(chunk)
            pages.append((file.filename or "", bytes(content)))
        try:
            source = await asyncio.to_thread(ingest_folio, folio_name, pages)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {k: source.get(k) for k in ["id", "filename", "folio_name", "kind", "hash", "page_count", "uploaded_at", "numbering_gaps"]}

    @app.post("/api/sources")
    async def upload(files: list[UploadFile] = File(...)):
        if len(files) > 50: raise HTTPException(413, "Upload at most 50 PDFs at a time")
        results = []
        for file in files:
            content = bytearray()
            while chunk := await file.read(1024 * 1024):
                content.extend(chunk)
                if len(content) > 50 * 1024 * 1024:
                    raise HTTPException(413, "Each PDF must be at most 50 MB")
            if b"%PDF-" not in content[:1024]: raise HTTPException(422, "Expected a PDF document")
            try:
                source = await asyncio.to_thread(ingest_pdf, file.filename or "upload.pdf", bytes(content))
                results.append({k: source.get(k) for k in ["id", "filename", "hash", "page_count", "uploaded_at"]})
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        return results

    @app.get("/api/batches")
    async def batches():
        return await call("batches")

    @app.post("/api/batches", status_code=202)
    async def start(body: BatchRequest):
        batch = await call("create_batch", source_ids=body.source_ids)
        try:
            worker_id = await asyncio.to_thread(launch_batch, batch["id"], batch["attempt"])
            return await call("update_batch", id=batch["id"], changes={"worker_id": worker_id})
        except Exception:
            await call("update_batch", id=batch["id"], changes={"status": "interrupted"})
            raise

    @app.get("/api/batches/{batch_id}")
    async def batch(batch_id: str):
        return await call("batch", id=batch_id)

    @app.post("/api/batches/{batch_id}/resume", status_code=202)
    async def resume(batch_id: str):
        batch = await call("resume_batch", id=batch_id)
        try:
            worker_id = await asyncio.to_thread(launch_batch, batch_id, batch["attempt"])
            return await call("update_batch", id=batch_id, changes={"worker_id": worker_id})
        except Exception:
            await call("update_batch", id=batch_id, changes={"status": "interrupted"})
            raise

    @app.post("/api/batches/{batch_id}/interrupt")
    async def interrupt(batch_id: str):
        batch = await call("batch", id=batch_id)
        await asyncio.to_thread(cancel_batch, batch)
        return await call("update_batch", id=batch_id, changes={"status": "interrupted", "attempt": batch["attempt"] + 1})

    @app.get("/api/batches/{batch_id}/folios")
    async def folios(batch_id: str):
        values = await call("folios", batch_id=batch_id)
        return [{"source_id": f["source_id"], "source_filename": f["source_filename"], "folio_name": f.get("folio_name"), "metadata": f["metadata"],
                 "revision": f["revision"], "entries": len(f["entries"]),
                 "reviewed": sum(e["review_status"] == "reviewed" for e in f["entries"]),
                 "issues": len(f["issues"]), "elapsed_seconds": f.get("elapsed_seconds")} for f in values]

    @app.get("/api/batches/{batch_id}/folios/{source_id}")
    async def folio(batch_id: str, source_id: str):
        value = await call("folio", batch_id=batch_id, source_id=source_id)
        value["annotations"] = await asyncio.to_thread(annotated_attributes, value)
        return value

    @app.post("/api/batches/{batch_id}/folios/{source_id}/review")
    async def review(batch_id: str, source_id: str, body: ReviewRequest, request: Request):
        if body.action.get("kind") == "reextract_result":
            raise HTTPException(422, "Extraction results can only be submitted by the private worker")
        value = await call("review", batch_id=batch_id, source_id=source_id, revision=body.revision,
                           action_id=body.action_id, action=body.action, reviewer=request.state.reviewer)
        value["annotations"] = await asyncio.to_thread(annotated_attributes, value)
        return value

    @app.post("/api/batches/{batch_id}/folios/{source_id}/entries/{entry_id}/reextract", status_code=202)
    async def extract_again(batch_id: str, source_id: str, entry_id: str):
        value = await call("folio", batch_id=batch_id, source_id=source_id)
        if not any(e["id"] == entry_id for e in value["entries"]): raise HTTPException(404, "Entry not found")
        task = await asyncio.to_thread(reextract, batch_id, source_id, entry_id, value["revision"])
        return {"task_id": task, "message": "Extracting the selected source region; refresh to see the proposed attributes."}

    @app.get("/api/batches/{batch_id}/folios/{source_id}/audit")
    async def audit(batch_id: str, source_id: str):
        return await call("audit", batch_id=batch_id, source_id=source_id)

    @app.get("/api/batches/{batch_id}/folios/{source_id}/pages/{page}")
    async def image(batch_id: str, source_id: str, page: int):
        value = await call("folio", batch_id=batch_id, source_id=source_id)
        if not 1 <= page <= len(value["pages"]): raise HTTPException(404, "Page not found")
        path = str(Path(value["directory"]) / "pages" / f"page-{page:04d}.png")
        content = await asyncio.to_thread(read_artifact, path)
        return Response(content, media_type="image/png")

    @app.get("/api/batches/{batch_id}/folios/{source_id}/evidence")
    async def evidence(batch_id: str, source_id: str):
        value = await call("folio", batch_id=batch_id, source_id=source_id)
        return Response(json.dumps(value, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{source_id}-evidence.json"'})

    async def downloadable(batch_id, version):
        if version not in {"extracted", "current", "reviewed"}: raise HTTPException(422, "Unknown export version")
        batch = await call("batch", id=batch_id)
        if not batch["downloads_ready"]: raise HTTPException(409, "Extraction exports are not published yet")
        return batch

    def require_reviewed(values):
        if any(e.get("review_status") != "reviewed" for f in values for e in f["entries"]):
            raise HTTPException(409, "Review is not complete")

    @app.get("/api/batches/{batch_id}/folios/{source_id}/csv")
    async def download_folio(batch_id: str, source_id: str, version: str = "extracted"):
        await downloadable(batch_id, version)
        value = await call("folio", batch_id=batch_id, source_id=source_id, version=version)
        if version == "reviewed": require_reviewed([value])
        content = (await asyncio.to_thread(read_artifact, str(Path(value["directory"]) / "extracted.csv"))
                   if version == "extracted" else csv_bytes(value))
        return Response(content, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{source_id}-{version}.csv"'})

    @app.get("/api/batches/{batch_id}/download")
    async def download_batch(batch_id: str, version: str = "extracted", format: str = "csv"):
        batch = await downloadable(batch_id, version)
        if format not in {'csv', 'manual'}:
            raise HTTPException(422, 'Unknown export format')
        if format == 'manual':
            values = await call('folios', batch_id=batch_id, version=version)
            if version == 'reviewed':
                if batch['errors'] or len(batch['completed']) != len(batch['source_ids']):
                    raise HTTPException(409, 'Finish every selected folio before downloading a reviewed batch')
                require_reviewed(values)
            content = await asyncio.to_thread(manual_xlsx_bytes, values)
            return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={'Content-Disposition':f'attachment; filename="manual-capture-{batch_id[:8]}-{version}.xlsx"'})
        if version == "extracted":
            content = await asyncio.to_thread(read_artifact, f"/artifacts/results/{batch_id}/extracted.zip")
            return Response(content, media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="folios-{batch_id[:8]}-extracted.zip"'})
        if version == "reviewed" and (batch["errors"] or len(batch["completed"]) != len(batch["source_ids"])):
            raise HTTPException(409, "Finish every selected folio before downloading a reviewed batch")
        values = await call("folios", batch_id=batch_id, version=version)
        if version == "reviewed": require_reviewed(values)
        manifest = {"batch_id": batch_id, "version": version, "source_ids": batch["source_ids"],
                    "completed": batch["completed"], "errors": batch["errors"],
                    "review_required": version != "reviewed", "schema_hash": batch["schema_hash"],
                    "revisions": {f["source_id"]: f["revision"] for f in values}}
        return Response(batch_zip(values, manifest), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="folios-{batch_id[:8]}-{version}.zip"'})

    frontend = Path(frontend or Path(__file__).resolve().parent.parent / "frontend/dist")
    if (frontend / "assets").exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/{path:path}")
    async def ui(path: str):
        if path.startswith("api/"): raise HTTPException(404, "Endpoint not found")
        if not (frontend / "index.html").exists(): return Response("Build the React frontend first", 503)
        return FileResponse(frontend / "index.html")

    return app
