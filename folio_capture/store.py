"""Durable state. In Modal this is owned exclusively by the private State class."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from .export import csv_bytes
from .schema import STATUSES, SUPPRESSED, capture_schema
from .vision import rectangle


def now():
    return datetime.now(timezone.utc).isoformat()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS folios(batch_id TEXT, source_id TEXT, payload TEXT NOT NULL,
                    extracted TEXT NOT NULL, PRIMARY KEY(batch_id,source_id));
                CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, created TEXT, batch_id TEXT,
                    source_id TEXT, payload TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def call(self, operation, **args):
        with self.lock:
            with self.connection() as db:
                result = self._call(db, operation, args)
            return result

    def _call(self, db, operation, args):
        def load(table, key, value):
            row = db.execute(f"SELECT payload FROM {table} WHERE {key}=?", (value,)).fetchone()
            if row is None:
                raise KeyError(value)
            return json.loads(row[0])

        def save_batch(batch):
            db.execute("UPDATE batches SET payload=? WHERE id=?", (json.dumps(batch), batch["id"]))

        if operation == "sources":
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM sources ORDER BY id")]
        if operation == "source":
            return load("sources", "id", args["id"])
        if operation == "put_source":
            source = {**args["source"], "uploaded_at": now()}
            db.execute("INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (source["id"], json.dumps(source)))
            return source
        if operation == "batches":
            return sorted([json.loads(r[0]) for r in db.execute("SELECT payload FROM batches")],
                          key=lambda b: b["created"], reverse=True)
        if operation == "create_batch":
            existing = [json.loads(r[0]) for r in db.execute("SELECT payload FROM batches")]
            if any(b["status"] in {"queued", "running"} for b in existing):
                raise Conflict("A batch is already active; resume or finish it first")
            ids = list(dict.fromkeys(args["source_ids"]))
            if not ids:
                raise ValueError("Select at least one folio")
            sources = {source_id: load("sources", "id", source_id) for source_id in ids}
            batch = {"id": uuid.uuid4().hex, "created": now(), "updated": now(), "status": "queued",
                     "source_ids": ids, "sources": sources, "completed": [], "errors": {}, "progress": {},
                     "schema_hash": capture_schema()["hash"], "downloads_ready": False,
                     "automatic_acceptance": False, "target_accuracy": .995, "attempt": 1}
            db.execute("INSERT INTO batches VALUES (?,?)", (batch["id"], json.dumps(batch)))
            return batch
        if operation == "batch":
            return load("batches", "id", args["id"])
        if operation == "update_batch":
            batch = load("batches", "id", args["id"])
            if args.get("attempt", batch["attempt"]) != batch["attempt"]:
                raise Conflict("Stale worker attempt")
            if "source_id" in args:
                batch["progress"][args["source_id"]] = args.get("stage", "working")
            batch.update(args.get("changes", {}))
            batch["updated"] = now()
            save_batch(batch)
            return batch
        if operation == "resume_batch":
            batch = load("batches", "id", args["id"])
            for row in db.execute("SELECT payload FROM batches"):
                other = json.loads(row[0])
                if other["id"] != batch["id"] and other["status"] in {"queued", "running"}:
                    raise Conflict("Another batch is active")
            if batch["status"] in {"queued", "running"}:
                raise Conflict("Batch still running; recover only after its worker has stopped")
            batch.update(status="queued", downloads_ready=False, errors={}, attempt=batch["attempt"] + 1,
                         updated=now())
            save_batch(batch)
            return batch
        if operation == "recover_batch":
            batch = load("batches", "id", args["id"])
            batch.update(status="interrupted", updated=now())
            save_batch(batch)
            return batch
        if operation == "put_folio":
            folio = args["folio"]
            batch = load("batches", "id", folio["batch_id"])
            if args.get("attempt", batch["attempt"]) != batch["attempt"]:
                raise Conflict("Stale worker attempt")
            current = db.execute("SELECT payload FROM folios WHERE batch_id=? AND source_id=?",
                                 (folio["batch_id"], folio["source_id"])).fetchone()
            if current:
                # Already published results (including human corrections) survive retries.
                return json.loads(current[0])
            payload = json.dumps(folio)
            db.execute("INSERT INTO folios VALUES (?,?,?,?)",
                       (folio["batch_id"], folio["source_id"], payload, payload))
            if folio["source_id"] not in batch["completed"]:
                batch["completed"].append(folio["source_id"])
            batch["errors"].pop(folio["source_id"], None)
            batch["updated"] = now()
            save_batch(batch)
            return folio
        if operation in {"folio", "folios"}:
            column = "extracted" if args.get("version") == "extracted" else "payload"
            query = f"SELECT {column} FROM folios WHERE batch_id=?"
            params = [args["batch_id"]]
            if operation == "folio":
                query += " AND source_id=?"; params.append(args["source_id"])
            values = [json.loads(r[0]) for r in db.execute(query, params)]
            if operation == "folio":
                if not values: raise KeyError(args["source_id"])
                return values[0]
            return values
        if operation == "audit":
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM audit WHERE batch_id=? AND source_id=? ORDER BY created",
                (args["batch_id"], args["source_id"]))]
        if operation == "review":
            action_id = args["action_id"]
            old = db.execute("SELECT payload,batch_id,source_id FROM audit WHERE id=?", (action_id,)).fetchone()
            if old:
                if old[1:] != (args["batch_id"], args["source_id"]) or json.loads(old[0])["action"] != args["action"]:
                    raise Conflict("Action ID was already used for a different change")
                return self._call(db, "folio", args)
            folio = self._call(db, "folio", args)
            if folio["revision"] != args["revision"]:
                raise Conflict("Another change was saved. Reload before editing.")
            action = args["action"]
            before = json.loads(json.dumps(folio))
            entries = folio["entries"]
            entry_id = action.get("entry_id")
            entry = next((e for e in entries if e["id"] == entry_id), None)
            kind = action["kind"]
            if kind == "metadata":
                allowed = {"folio_number", "county", "register_type", "first_registration_date", "edition", "opened_date", "prior_folio"}
                if set(action["values"]) - allowed:
                    raise ValueError("Unknown metadata field")
                if any(not isinstance(v, str) for v in action["values"].values()):
                    raise ValueError("Metadata values must be text")
                for name, value in action["values"].items():
                    if value != folio["metadata"].get(name):
                        if "metadata_evidence" not in folio:
                            folio["metadata_evidence"] = {k: [{**r, "quote": str(v)} for r in folio.get("metadata_sources", [])]
                                                          for k,v in folio["metadata"].items()}
                        folio["metadata_evidence"][name] = []
                        folio.setdefault("metadata_corrections", {})[name] = {"reviewer": args["reviewer"], "time": now()}
                folio["metadata"].update(action["values"])
                folio["metadata_reviewed"] = True
                for item in entries: item["review_status"] = "needs_review"
            elif kind == "acknowledge_issues":
                if not action.get("note", "").strip():
                    raise ValueError("Record why the structural issues are resolved or accepted")
                folio["issues_acknowledged"] = {"note": action["note"], "time": now()}
            elif kind == "add":
                regions = action["regions"]
                if not regions: raise ValueError("An entry needs a source region")
                for region in regions:
                    rectangle(region["box"])
                    if not 1 <= region["page"] <= len(folio["pages"]): raise ValueError("Invalid page")
                entries.append({"id": uuid.uuid4().hex, "parent_id": "", "entry_type": action.get("entry_type", "Other"),
                    "status": "", "regions": regions, "attributes": {}, "field_evidence": {}, "relationships": [],
                    "signals": {}, "review_status": "needs_review", "reasons": ["STRUCTURE_CHANGED_REEXTRACT"]})
            elif entry is None:
                raise KeyError(entry_id)
            elif kind == "edit":
                status = action.get("status", entry.get("status", ""))
                if status not in STATUSES: raise ValueError("Invalid cancellation code")
                attrs = action.get("attributes", entry.get("attributes", {}))
                original_attrs = entry.get("attributes", {})
                if not isinstance(attrs, dict): raise ValueError("Attributes must be a table map")
                schema = capture_schema()
                for table, records in attrs.items():
                    if table not in schema["tables"] or not isinstance(records, list):
                        raise ValueError("Unknown attribute table")
                    for record in records:
                        if not isinstance(record, dict): raise ValueError("Each record must be an object")
                        if set(record) - set(schema["tables"][table]) - {"_record_id", "_parent_id"}:
                            raise ValueError("Unknown attribute field")
                        if any(v is not None and not isinstance(v, (str, int, float)) for v in record.values()):
                            raise ValueError("Attribute values must be scalar")
                regions = action.get("regions", entry["regions"])
                if not regions: raise ValueError("An entry needs a source region")
                for region in regions:
                    rectangle(region["box"])
                    if not 1 <= region["page"] <= len(folio["pages"]): raise ValueError("Invalid page")
                changed_structure = regions != entry["regions"] or action.get("entry_type", entry["entry_type"]) != entry["entry_type"]
                entry.update(status=status, entry_type=action.get("entry_type", entry["entry_type"]), regions=regions,
                             attributes={} if status else attrs, review_status="needs_review",
                             review_note=action.get("note", ""))
                if status: entry["field_evidence"] = {}; entry["relationships"] = []
                entry["human_edited"] = True
                if changed_structure: entry["reasons"] = sorted(set(entry["reasons"] + ["STRUCTURE_CHANGED_REEXTRACT"]))
                for table, records in entry["attributes"].items():
                    for index, record in enumerate(records):
                        for field in record:
                            if not field.startswith("_"):
                                old_records = original_attrs.get(table, [])
                                if index < len(old_records) and old_records[index].get(field) == record[field] and old_records[index].get("_record_id") == record.get("_record_id"):
                                    continue
                                entry.setdefault("field_evidence", {})[f"{table}[{index}].{field}"] = {
                                    "basis": "human", "reviewer": args["reviewer"], "time": now()}
                from .checks import validate_entry
                validate_entry(entry, folio["pages"])
            elif kind == "approve":
                if not folio.get("metadata_reviewed"):
                    raise ValueError("Confirm folio identity before approving entries")
                if folio.get("issues") and not folio.get("issues_acknowledged"):
                    raise ValueError("Resolve or acknowledge folio structural issues first")
                if entry.get("status") == "U": raise ValueError("Resolve uncertain cancellation before approving")
                if "STRUCTURE_CHANGED_REEXTRACT" in entry.get("reasons", []):
                    raise ValueError("Re-extract this changed region before approving")
                if not entry.get("status") and not entry.get("attributes"):
                    raise ValueError("Extract or enter the attributes before approving")
                entry.update(review_status="reviewed", reviewed_by=args["reviewer"], reviewed_at=now(),
                             review_note=action.get("note", ""))
            elif kind == "split":
                new_regions = action["regions"]
                if len(new_regions) < 2: raise ValueError("A split needs two or more regions")
                for region in new_regions:
                    rectangle(region["box"])
                    if not 1 <= region["page"] <= len(folio["pages"]): raise ValueError("Invalid page")
                entries.remove(entry)
                for region in new_regions:
                    entries.append({**entry, "id": uuid.uuid4().hex, "parent_id": entry["id"],
                                    "regions": [region], "attributes": {}, "field_evidence": {}, "relationships": [],
                                    "review_status": "needs_review", "reasons": ["STRUCTURE_CHANGED_REEXTRACT"]})
            elif kind == "merge":
                ids = set(action["entry_ids"])
                merging = [e for e in entries if e["id"] in ids]
                if len(merging) != len(ids) or len(ids) < 2: raise ValueError("Select at least two existing entries")
                merged = {**entry, "id": uuid.uuid4().hex, "parent_id": entry["id"],
                          "regions": [r for e in merging for r in e["regions"]], "attributes": {},
                          "field_evidence": {}, "relationships": [], "review_status": "needs_review",
                          "reasons": ["STRUCTURE_CHANGED_REEXTRACT"]}
                entries[:] = [e for e in entries if e["id"] not in ids] + [merged]
            elif kind == "reextract_result":
                replacement = action["entry"]
                replacement.update(id=entry["id"], parent_id=entry.get("parent_id", ""), review_status="needs_review")
                entries[entries.index(entry)] = replacement
            else:
                raise ValueError("Unknown review action")
            entries.sort(key=lambda e: (e["regions"][0]["page"], e["regions"][0]["box"][1], e["id"]))
            folio["revision"] += 1
            audit = {"id": action_id, "time": now(), "reviewer": args["reviewer"],
                     "revision": folio["revision"], "action": action, "before": before, "after": folio}
            db.execute("UPDATE folios SET payload=? WHERE batch_id=? AND source_id=?",
                       (json.dumps(folio), args["batch_id"], args["source_id"]))
            db.execute("INSERT INTO audit VALUES (?,?,?,?,?)",
                       (action_id, now(), args["batch_id"], args["source_id"], json.dumps(audit)))
            return folio
        raise ValueError(f"Unknown state operation: {operation}")
