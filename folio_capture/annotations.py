"""Read-only, field-level source locations. OCR location is not value correctness."""

import re

from .vision import inside, rectangle


def tokens(text):
    return [clean for word in str(text).split() if (clean := re.sub(r"\W", "", word).upper())]


def locate(source, value, page):
    """Prefer a unique literal OCR match inside the original evidence box."""
    box = rectangle(source["box"])
    candidates = [tokens(value), tokens(source.get("quote", ""))[:12]]
    for engine in ("tesseract", "paddle"):
        words = [w for w in page.get(engine, {}).get("words", []) if inside(w["box"], box)]
        sequence = [(token, index) for index, word in enumerate(words) for token in tokens(word["text"])]
        text = [token for token, _ in sequence]
        for target in candidates:
            if not target:
                continue
            matches = [i for i in range(len(text)-len(target)+1) if text[i:i+len(target)] == target]
            if len(matches) != 1:
                continue
            start = matches[0]
            selected = [words[i]["box"] for i in dict.fromkeys(index for _, index in sequence[start:start+len(target)])]
            lines = []
            for word in selected:
                if lines and abs((lines[-1][1]+lines[-1][3]-word[1]-word[3])/2) < max(2, (word[3]-word[1])*.65):
                    prior = lines[-1]
                    lines[-1] = [min(prior[0],word[0]), min(prior[1],word[1]), max(prior[2],word[2]), max(prior[3],word[3])]
                else:
                    lines.append(list(word))
            return [{"box": [max(box[0],b[0]), max(box[1],b[1]), min(box[2],b[2]), min(box[3],b[3])],
                     "locator": "ocr"} for b in lines]
    return [{"box": box, "locator": "model"}]


def annotated_attributes(folio):
    pages = folio["pages"]
    result = []

    def add(key, name, value, evidence, entry=None):
        marks = []
        for source in evidence.get("sources", []):
            page = source.get("page", 0)
            if not isinstance(page, int) or not 1 <= page <= len(pages):
                continue
            try:
                locations = locate(source, value if evidence.get('basis')=='observed' else '', pages[page-1])
            except (ValueError, KeyError, TypeError):
                continue
            for location in locations:
                if evidence.get('ocr_only') and location['locator'] != 'ocr':
                    continue
                mark = {"page": page, "quote": source.get("quote", ""), **location}
                if mark not in marks:
                    marks.append(mark)
        result.append({"id": key, "name": name, "value": str(value),
                       "entry_id": entry.get("id") if entry else None,
                       "entry_type": entry.get("entry_type") if entry else "Folio metadata",
                       "pages": sorted({r["page"] for r in entry["regions"]}) if entry else sorted({m["page"] for m in marks}),
                       "basis": evidence.get("basis", "unlocated"),
                       "rule": evidence.get("rule_source", evidence.get("rule", "")), "sources": marks})

    add("input.folio_name", "Folio Name", folio.get("folio_name", folio["source_id"]), {"basis": "input"})
    for field, value in folio.get("metadata", {}).items():
        if value in (None, ""):
            continue
        if "metadata_evidence" in folio:
            sources = folio["metadata_evidence"].get(field, [])
        else:
            sources = [{**r, "quote": str(value)} for r in folio.get("metadata_sources", [])]
        basis = "human" if field in folio.get("metadata_corrections", {}) else "observed" if sources else "unlocated"
        add("metadata."+field, field, value, {"basis": basis, "sources": sources, "ocr_only": 'metadata_evidence' not in folio})
    for entry in folio["entries"]:
        if entry.get("status"):
            continue
        for table, records in entry.get("attributes", {}).items():
            for index, record in enumerate(records):
                for field, value in record.items():
                    if field.startswith("_") or value in (None, ""):
                        continue
                    key = f"{table}[{index}].{field}"
                    evidence = entry.get("field_evidence", {}).get(key, {})
                    if field == "FOLIO_IMAGE_FILENAME":
                        evidence = {"basis": "input"}
                    add(entry["id"]+":"+key, field, value, evidence, entry)
                    result[-1].update(table=table, record=index+1)
    return result
