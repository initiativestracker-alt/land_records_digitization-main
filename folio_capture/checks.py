"""Deterministic validation and conservative review routing; no fake probability."""

from datetime import date
import re

from .schema import capture_schema, is_lookup, STATUSES, TYPES
from .vision import inside, rectangle


def validate_entry(entry, pages):
    schema = capture_schema()
    reasons = set(entry.get("reasons", []))
    if entry.get("status", "") not in STATUSES:
        raise ValueError("Unknown cancellation code")
    for region in entry["regions"]:
        rectangle(region["box"])
        if not 1 <= region["page"] <= len(pages):
            raise ValueError("Source page is outside this folio")
    if entry.get("status"):
        entry["attributes"] = {}
        entry["field_evidence"] = {}
        entry["relationships"] = []
        reasons.add("SUPPRESSED_BY_CAPTURE_RULE")
    else:
        if entry["entry_type"] not in TYPES:
            reasons.add("OUTSIDE_DETAILED_SCHEMA")
        for table, records in entry.get("attributes", {}).items():
            if table not in schema["tables"]:
                raise ValueError(f"Unknown table: {table}")
            for index, record in enumerate(records):
                for field, value in list(record.items()):
                    if field.startswith("_"):
                        continue
                    if field not in schema["tables"][table]:
                        raise ValueError(f"Unknown field: {table}.{field}")
                    if value is not None and not isinstance(value, (str, int, float)):
                        raise ValueError("Attribute values must be scalar")
                    if is_lookup(table, field, entry.get("capture_profile", "database")) and value not in ("", None):
                        entry.setdefault("visible_references", {})[f"{table}[{index}].{field}"] = str(value)
                        record[field] = ""
                        reasons.add("LPS_LOOKUP_REQUIRED")
                    value = record[field]
                    if value in ("", None):
                        continue
                    value = str(value)
                    evidence = entry.get("field_evidence", {}).get(f"{table}[{index}].{field}", {})
                    basis = evidence.get("basis")
                    if basis not in {"observed", "default", "derived", "human", "input"}:
                        reasons.add("MISSING_FIELD_EVIDENCE")
                    if basis == "input":
                        expected = ";".join(pages[p-1].get('original_filename','') for p in sorted({r['page'] for r in entry['regions']}))
                        if table != 'FT_FOLIO_TEXT' or field != 'FOLIO_IMAGE_FILENAME' or not expected or value != expected:
                            reasons.add("MISSING_FIELD_EVIDENCE")
                    elif basis == "observed":
                        fragments = evidence.get("sources", [])
                        valid = [f for f in fragments if f.get("quote") and 1 <= f.get("page", 0) <= len(pages)
                                 and any(r["page"] == f["page"] and inside(f["box"], r["box"]) for r in entry["regions"])]
                        if not valid:
                            reasons.add("UNANCHORED_VALUE")
                        supported = False
                        for source in valid:
                            page = pages[source["page"] - 1]
                            for engine in ["tesseract", "paddle"]:
                                nearby = " ".join(w["text"] for w in page.get(engine, {}).get("words", []) if inside(w["box"], source["box"]))
                                clean = lambda s: re.sub(r"\W", "", s).upper()
                                if clean(value) and clean(value) in clean(nearby):
                                    supported = True
                        evidence["ocr_value_supported"] = supported
                        if not supported:
                            reasons.add("VALUE_NOT_CONFIRMED_BY_OCR")
                    elif basis in {"default", "derived"}:
                        if not evidence.get("rule"):
                            reasons.add("MISSING_RULE_PROVENANCE")
                        if basis == "derived" and not evidence.get("sources"):
                            reasons.add("MISSING_DERIVATION_EVIDENCE")
                    if field.endswith("_MM") and (not value.isdigit() or not 1 <= int(value) <= 12):
                        reasons.add("INVALID_DATE")
                    if field.endswith("_DD") and (not value.isdigit() or not 1 <= int(value) <= 31):
                        reasons.add("INVALID_DATE")
                    if field.endswith("_YYYY"):
                        if re.fullmatch(r"@@\d{2}", value):
                            reasons.add("PARTIAL_YEAR")
                        elif not re.fullmatch(r"\d{4}", value):
                            reasons.add("INVALID_DATE")
                    if field.endswith("_County") and value not in {"Antrim", "Armagh", "Down", "Fermanagh", "Londonderry", "Tyrone", ""}:
                        reasons.add("UNKNOWN_COUNTY")
                    if field == "POSTCODE" and not re.fullmatch(r"BT\d{1,2}\s?\d[A-Z]{2}", value.upper()):
                        reasons.add("INVALID_POSTCODE")
                for field in record:
                    if field.endswith("_YYYY"):
                        prefix = field[:-5]
                        parts = [record.get(prefix + suffix, "") for suffix in ["_YYYY", "_MM", "_DD"]]
                        if all(str(p).isdigit() for p in parts):
                            try:
                                date(*map(int, parts))
                            except ValueError:
                                reasons.add("INVALID_DATE")
                if table == "FT_EASEMENT" and record.get("DOM_TEN_REG_AFFECTED") == "N" and record.get("DOM_TEN_UNREG_AFFECTED") == "N":
                    reasons.add("EASEMENT_TENEMENT_RULE")
        generic = (entry.get("attributes", {}).get("FT_FOLIO_TEXT") or [{}])[0]
        if entry["entry_type"] in {"Transfer of Part", "Covenants", "Easements", "Leasehold Yearly Rent"}:
            if generic.get("REG_ON_FIRST_REG") != "Y" and not generic.get("REGISTERED_DATE_YYYY"):
                reasons.add("REGISTRATION_DATE_MISSING")
        if generic.get("FOLIO_TEXT_LEGIBLITY") in {"2", "9"}:
            reasons.add("UNREADABLE_OR_DAMAGED")
    signals = entry.get("signals", {})
    if signals.get("ocr_score_p10") is None or signals["ocr_score_p10"] < .8:
        reasons.add("WEAK_OCR")
    if signals.get("ocr_agreement") is None or signals["ocr_agreement"] < .8:
        reasons.add("OCR_DISAGREEMENT")
    if signals.get("deletion_strokes") or entry.get("status") == "U":
        reasons.add("CHECK_CANCELLATION_SCOPE")
    entry["reasons"] = sorted(reasons)
    entry["risk_band"] = "structural" if reasons.intersection({"CHECK_CANCELLATION_SCOPE", "UNANCHORED_VALUE"}) else "review"
    # A score threshold is not a calibrated correctness probability. No auto-accept yet.
    entry.setdefault("review_status", "needs_review")
    return entry
