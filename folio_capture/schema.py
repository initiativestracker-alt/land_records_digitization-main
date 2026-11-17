"""Read the supplied workbook, including its capture instructions."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re

from openpyxl import load_workbook

TYPES = {
    "Transfer of Part": "FT_TOP",
    "Description of Land": "FT_DESCRIPTION_OF_LAND",
    "Covenants": "FT_CONVENANT",
    "Easements": "FT_EASEMENT",
    "Leasehold Yearly Rent": "FT_LHOLD_YRLY_RENT",
    "Leasehold Particulars": "FT_LHOLD_PARTICULARS",
}
SHEET_TYPES = {"Convenant": "Covenants", "Easements": "Easements", **{t: t for t in TYPES}}
SUPPRESSED = {"Y", "C", "U", "B", "F"}
STATUSES = ["", "Y", "C", "U", "B", "F"]


def schema_path():
    if os.getenv("SCHEMA_PATH"):
        return Path(os.environ["SCHEMA_PATH"])
    files = list(Path(__file__).resolve().parent.parent.glob("*.xlsx"))
    if len(files) != 1:
        raise ValueError("Set SCHEMA_PATH to the supplied attribute workbook")
    return files[0]


@lru_cache(maxsize=1)
def capture_schema():
    path = schema_path()
    book = load_workbook(path, read_only=True, data_only=True)
    inventory = {str(c).strip().split(" | ")[-1] for row in book.worksheets[0].values
                 for c in row if isinstance(c, str)}
    tables, groups, warnings, type_rules = {}, {}, [], {}
    for sheet in book.worksheets[1:]:
        entry_type = SHEET_TYPES.get(sheet.title)
        if not entry_type:
            continue
        groups[entry_type] = []
        type_rules[entry_type] = {}
        for row_number, row in enumerate(sheet.values, 1):
            if row_number == 1 or len(row) < 4 or not row[0] or not row[2]:
                continue
            table = str(row[0]).strip().split(" | ")[-1].strip()
            field = str(row[2]).strip()
            rule = str(row[3] or "").strip()
            if field not in inventory:
                warnings.append(f"{sheet.title}!C{row_number}: {field} missing from ALL FIELDS")
            definition = {"rule": rule, "source": f"{sheet.title}!C{row_number}:D{row_number}"}
            type_rules[entry_type].setdefault(table, {})[field] = definition
            if table in tables and field in tables[table] and tables[table][field]["rule"] != rule:
                # Generic instructions differ in examples between entry types; keep every source.
                tables[table][field].setdefault("variants", []).append(definition)
            elif field not in tables.get(table, {}):
                tables.setdefault(table, {})[field] = definition
            if table not in groups[entry_type]:
                groups[entry_type].append(table)
    book.close()
    return {"hash": hashlib.sha256(path.read_bytes()).hexdigest(), "tables": tables,
            "groups": groups, "type_rules": type_rules, "types": list(TYPES), "warnings": sorted(set(warnings))}


def is_lookup(table, field, profile="database"):
    if profile == "manual-v2":
        # Manual capture stores printed references and local party sequence numbers.
        # These are not represented as resolved database primary keys.
        return False
    if field == "FOLIO_ID" or field == "FOLIO_PARTY_ID":
        return True
    if field == "FOLIO_ENTRY_ID":
        return table not in {"FT_TOP_PART_OR_ENTRY", "FT_DESCRIPTION_OF_LAND_ENTRY",
                             "FT_CONVENANT_MAPPED_ENTRY"}
    return False


def default_value(rule):
    if 'default option' in rule.lower() and '“0”' in rule:
        return "0"
    match = re.search(r"(?i)default\s*(?:is\s*)?[=:'\"‘’“”\s]*([NYL0])\b", rule)
    return match.group(1).upper() if match else None
