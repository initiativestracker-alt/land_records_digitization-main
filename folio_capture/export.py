"""One CSV per source folio, plus a lossless evidence JSON and batch ZIP."""

import csv
import io
import json
from pathlib import Path
import zipfile

from .schema import capture_schema
from .sources import entry_page_numbers, entry_image_names

META_COLUMNS = ["source_id", "source_filename", "folio_name", "folio_number", "county", "register_type",
                "first_registration_date", "edition", "opened_date", "prior_folio",
                "entry_id", "parent_entry_id", "record_kind", "register_part", "entry_type",
                "source_pages", "cancellation_status", "review_status", "reason_codes",
                "revision", "scope", "folio_issues", "local_relationships"]


def csv_bytes(folio):
    schema = capture_schema()
    columns = META_COLUMNS + [f"{table}.{field}" for table, fields in schema["tables"].items()
                              for field in fields] + [table for table in schema["tables"]]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for entry in folio["entries"]:
        row = {k: folio.get("metadata", {}).get(k, "") for k in META_COLUMNS}
        row.update({"source_id": folio["source_id"], "source_filename": folio["source_filename"],
                    "folio_name": folio.get("folio_name", Path(folio["source_filename"]).stem),
                    "entry_id": entry["id"], "parent_entry_id": entry.get("parent_id", ""),
                    "record_kind": entry.get("record_kind", "entry"),
                    "register_part": entry.get("register_part", ""), "entry_type": entry["entry_type"],
                    "source_pages": json.dumps(entry_page_numbers(folio, entry)),
                    "cancellation_status": entry.get("status", ""),
                    "review_status": entry.get("review_status", "needs_review"),
                    "reason_codes": json.dumps(entry.get("reasons", [])),
                    "revision": folio["revision"], "scope": "supplied_pages",
                    "folio_issues": json.dumps(folio.get("issues", []), ensure_ascii=False),
                    "local_relationships": json.dumps(entry.get("relationships", []), ensure_ascii=False)})
        attrs = entry.get("attributes", {}) if entry.get("status", "") == "" else {}
        for table, fields in schema["tables"].items():
            records = attrs.get(table, [])
            row[table] = json.dumps(records, ensure_ascii=False) if records else ""
            for field in fields:
                values = [record.get(field, "") for record in records]
                row[f"{table}.{field}"] = (values[0] if len(values) == 1 else
                                           json.dumps(values, ensure_ascii=False) if values else "")
        # Suppression applies to body/margin attributes, not record identity and status.
        row["FT_FOLIO_TEXT.FT_ENTRY_TEXT_TYPE_NAME"] = entry["entry_type"]
        row["FT_FOLIO_TEXT.IS_ENTRY_STRIKETHROUGH"] = entry.get("status", "")
        row["FT_FOLIO_TEXT.FOLIO_IMAGE_FILENAME"] = ";".join(entry_image_names(folio, entry) or [
            f"{folio['source_id']}/page-{p:04d}.pdf" for p in sorted({r["page"] for r in entry["regions"]})])
        writer.writerow({k: "" if v is None else v for k, v in row.items()})
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def batch_zip(folios, manifest):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for folio in folios:
            archive.writestr(f"{folio['source_id']}.csv", csv_bytes(folio))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return out.getvalue()
