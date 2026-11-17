"""Manual-capture contract. No labelled answers are read by this module."""

from copy import deepcopy
import json
from pathlib import Path
import re

from .schema import capture_schema, default_value
from .sources import entry_page_numbers, entry_image_names

SHEET_NAMES = {"Covenants": "Covenant", "Easements": "Easement"}


def complete_capture(entry, source_id, image_names=None):
    """Apply schema defaults and local reference formatting with provenance.

    Only complete entries with extracted attributes. Failed/suppressed entries
    must remain visibly incomplete rather than acquiring plausible defaults.
    """
    attrs = entry.get("attributes", {})
    if entry.get("status") or not attrs or "ENTRY_EXTRACTION_FAILED" in entry.get("reasons", []):
        return entry
    rules = capture_schema()["type_rules"].get(entry["entry_type"], {})
    evidence = entry.setdefault("field_evidence", {})
    relationships = entry.setdefault("relationships", [])
    generic = attrs.setdefault("FT_FOLIO_TEXT", [{}])[0]
    generic.setdefault("_record_id", "manual_text")
    root_id = generic["_record_id"]

    def record(table, parent=root_id):
        if not attrs.get(table):
            rid = "manual_" + table.lower()
            attrs[table] = [{"_record_id": rid, "_parent_id": parent}]
            if parent:
                relationships.append({"parent": parent, "child": rid, "table": table})
        return attrs[table][0]

    def assign(table, index, name, value, rule, sources=()):
        attrs[table][index][name] = value
        evidence[f"{table}[{index}].{name}"] = {
            "name": name, "value": value, "basis": "derived" if sources else "default",
            "sources": list(sources), "rule": rule, "rule_source": rule,
            "profile_generated": True,
        }

    # An asserted model default is not evidence unless the capture rule defines it.
    # Local IDs generated below have their own explicit manual-profile provenance.
    for table, records in attrs.items():
        for i, item in enumerate(records):
            for name, value in list(item.items()):
                prior = evidence.get(f'{table}[{i}].{name}', {})
                definition = rules.get(table, {}).get(name)
                if (value not in (None, '') and definition and prior.get('basis') == 'default'
                        and not prior.get('profile_generated')
                        and not (name == 'HAS_ENTRY_BEEN_SPLIT' and entry.get('parent_id'))
                        and prior.get('rule_source') == definition['source']
                        and str(value) != default_value(definition['rule'])):
                    assign(table, i, name, '', 'manual-capture: unsupported model default omitted')
                    if 'UNVERIFIED_DEFAULT' not in entry.setdefault('reasons', []):
                        entry['reasons'].append('UNVERIFIED_DEFAULT')

    # These groups carry unconditional entry-level defaults in the capture schema.
    required = {
        "Transfer of Part": ["FT_TOP", "FT_TOP_PART_OR_ENTRY", "FT_CONSIDERATION"],
        "Description of Land": ["FT_DESCRIPTION_OF_LAND", "FT_DESCRIPTION_OF_LAND_ENTRY"],
        "Covenants": ["FT_CONVENANT", "FT_CONVENANT_MAPPED"],
        "Easements": ["FT_EASEMENT"],
        "Leasehold Yearly Rent": ["FT_LHOLD_YRLY_RENT"],
        "Leasehold Particulars": ["FT_LHOLD_PARTICULARS", "FT_LHOLD_PARTICULARS_LEASE"],
    }
    legibility = str(generic.get("FOLIO_TEXT_LEGIBLITY", ""))
    if legibility not in {"2", "9"}:
        for table in required.get(entry["entry_type"], []):
            parent = {
                "FT_TOP_PART_OR_ENTRY": "FT_TOP",
                "FT_DESCRIPTION_OF_LAND_ENTRY": "FT_DESCRIPTION_OF_LAND",
                "FT_CONVENANT_MAPPED": "FT_CONVENANT",
                "FT_LHOLD_PARTICULARS_LEASE": "FT_LHOLD_PARTICULARS",
            }.get(table)
            record(table, record(parent)["_record_id"] if parent else root_id)
        for table, records in attrs.items():
            for i, item in enumerate(records):
                for name, definition in rules.get(table, {}).items():
                    value = default_value(definition["rule"])
                    if value is not None and item.get(name) in (None, ""):
                        assign(table, i, name, value, definition["source"])

    for i, item in enumerate(attrs['FT_FOLIO_TEXT']):
        item["FOLIO_IMAGE_FILENAME"] = ";".join(image_names) if image_names else source_id
        if image_names:
            evidence[f'FT_FOLIO_TEXT[{i}].FOLIO_IMAGE_FILENAME'] = {
                'basis':'input', 'sources':[], 'value':item['FOLIO_IMAGE_FILENAME'],
                'rule':'Original JPEG filenames from the ordered source manifest', 'profile_generated':True}
    generic["FT_ENTRY_TEXT_TYPE_NAME"] = SHEET_NAMES.get(entry["entry_type"], entry["entry_type"])
    if entry.get('parent_id'):
        assign('FT_FOLIO_TEXT', 0, 'HAS_ENTRY_BEEN_SPLIT', 'Y',
               'manual-capture: inventory identifies a shared source entry')
    # These schema flags say to capture Y when true; unlike default-N flags,
    # they have no negative capture value in the manual profile.
    for i, item in enumerate(attrs['FT_FOLIO_TEXT']):
        for field in ('HAS_ENTRY_BEEN_SPLIT', 'FOLIO_TEXT_CONTINUED_OVERPAGE'):
            if item.get(field) == 'N':
                assign('FT_FOLIO_TEXT', i, field, '', capture_schema()['tables']['FT_FOLIO_TEXT'][field]['source'])
    for table, records in attrs.items():
        for i, item in enumerate(records):
            for field in ('DOC_REF', 'Line_2_Barony'):
                value = item.get(field)
                if isinstance(value, str) and value.endswith('.'):
                    prior = evidence.get(f'{table}[{i}].{field}', {})
                    assign(table, i, field, value.rstrip('.'),
                           'manual-capture: omit sentence punctuation from a structured reference/name', prior.get('sources', []))
            county = item.get('NEW_FOLIO_ID_County')
            prior_county = evidence.get(f'{table}[{i}].NEW_FOLIO_ID_County', {})
            quotes = ' '.join(s.get('quote','') for s in prior_county.get('sources',[])).casefold()
            if county and quotes and str(county).casefold() not in quotes and not (
                    county == 'Londonderry' and 'derry' in quotes):
                assign(table, i, 'NEW_FOLIO_ID_County', '',
                       'manual-capture: referenced county must be printed, not inferred from a prefix',
                       prior_county.get('sources',[]))
            for name in ("DOC_REF_Type",):
                value = str(item.get(name, "")).strip().casefold().rstrip('.')
                standard = {"doc": "Document", "doc no": "Document", "document no": "Document",
                            "instrument no": "Instrument", "application no": "Application", "no": "No"}.get(value)
                if standard and item.get(name) != standard:
                    prior = evidence.get(f"{table}[{i}].{name}", {})
                    assign(table, i, name, standard, "manual-capture: expand document label abbreviation", prior.get("sources", []))
    if entry['entry_type'] == 'Transfer of Part':
        if all(r.get('FOLIO_ENTRIES_SPECIFIED') == 'N' for r in attrs.get('FT_TOP', [])):
            for i, item in enumerate(attrs.get('FT_TOP_PART_OR_ENTRY', [])):
                if not item.get('FOLIO_ENTRY_ID') and item.get('PART_OF_FOLIO_ENTRY') == 'Y':
                    assign('FT_TOP_PART_OR_ENTRY', i, 'PART_OF_FOLIO_ENTRY', 'N',
                           rules['FT_TOP_PART_OR_ENTRY']['PART_OF_FOLIO_ENTRY']['source'])
        dates = evidence.get('FT_FOLIO_TEXT[0].REGISTERED_DATE_YYYY',{}).get('sources',[])
        keep = []
        kept_indices = []
        for i, item in enumerate(attrs.get('FT_ENTRY_DOC',[])):
            ref_sources = evidence.get(f'FT_ENTRY_DOC[{i}].DOC_REF',{}).get('sources',[])
            # A labelled reference immediately below the registration date belongs
            # to the registration/marginal block, not the transfer-body document table.
            in_registration_block = item.get('DOC_REF_Type') and any(
                ref['page']==date['page'] and -3 <= ref['box'][1]-date['box'][3] <= 40
                and ref['box'][0] <= date['box'][2]+30 and ref['box'][2] >= date['box'][0]-100
                for ref in ref_sources for date in dates)
            if not in_registration_block:
                keep.append(item)
                kept_indices.append(i)
                continue
            target = len(attrs.setdefault('FT_MARGINAL_NOTE_DOC',[]))
            attrs['FT_MARGINAL_NOTE_DOC'].append(item)
            for field in ('DOC_REF_Type','DOC_REF'):
                prior = evidence.get(f'FT_ENTRY_DOC[{i}].{field}',{})
                value = str(item.get(field,'')).rstrip('.')
                assign('FT_MARGINAL_NOTE_DOC',target,field,value,
                       'manual-capture: registration reference source geometry',prior.get('sources',[]))
            for link in relationships:
                if link['child']==item.get('_record_id'):link['table']='FT_MARGINAL_NOTE_DOC'
        if keep:attrs['FT_ENTRY_DOC']=keep
        else:attrs.pop('FT_ENTRY_DOC',None)
        # Moving an earlier record changes the surviving array indexes. Keep each
        # quote/box attached to its own document rather than the removed neighbour.
        prior_evidence = {k:evidence.pop(k) for k in list(evidence) if k.startswith('FT_ENTRY_DOC[')}
        for new_index, old_index in enumerate(kept_indices):
            prefix = f'FT_ENTRY_DOC[{old_index}].'
            for key, value in prior_evidence.items():
                if key.startswith(prefix):
                    evidence[f'FT_ENTRY_DOC[{new_index}].{key[len(prefix):]}'] = value
    # The manual uses display references, not database primary keys.
    for table in ("FT_TOP", "FT_EASEMENT_DOM_REG"):
        for i, item in enumerate(attrs.get(table, [])):
            name = "NEW_FOLIO_ID" if table == "FT_TOP" else "FOLIO_ID"
            saved = entry.get("visible_references", {}).get(f"{table}[{i}].{name}", {})
            if isinstance(saved, dict) and saved.get("value") and not item.get(name):
                assign(table, i, name, saved['value'], "manual-capture: retain printed reference", saved.get('sources', []))
            if not item.get(name) and item.get("NEW_FOLIO_ID_Number"):
                value = " ".join(str(item.get("NEW_FOLIO_ID_" + suffix, "")).strip()
                                 for suffix in ("Prefex", "Number", "Suffix") if item.get("NEW_FOLIO_ID_" + suffix))
                sources = [s for suffix in ("Prefex", "Number", "Suffix")
                           for s in evidence.get(f"{table}[{i}].NEW_FOLIO_ID_{suffix}", {}).get("sources", [])]
                assign(table, i, name, value, "manual-capture: visible folio reference components", sources)

    if entry["entry_type"] == "Description of Land":
        # Re-express already captured address components in the requested manual branch.
        address_table = "FT_DESCRIPTION_OF_LAND_ENTRY_ADDRESSABLE_MANUAL_ADDR"
        doc_table = "FT_DESCRIPTION_OF_LAND_ENTRY_DOC"
        body = " ".join(str(r.get("FOLIO_TEXT_2", "")) for r in attrs.get("FT_FOLIO_TEXT", []))
        body = " ".join(body.split())
        body_sources = evidence.get("FT_FOLIO_TEXT[0].FOLIO_TEXT_2", {}).get("sources", [])
        for old_table in ("FT_DESCRIPTION_OF_LAND_ENTRY_TOWNLAND", "FT_DESCRIPTION_OF_LAND_ENTRY_NON_ADDRESSABLE"):
            retained = []
            for old in attrs.get(old_table, []):
                address = {}
                townland = old.get("TOWNLAND", "")
                description = old.get("DESCRIPTION", "")
                street = re.match(r"(.+?\b(?:Road|Street|Lane|Park|Avenue|Grove|Drive|Crescent|Place))\b", description, re.I)
                if townland: address['Line_3_Townland'] = townland
                if street: address['Line_1_StreetName'] = street.group(1)
                if not address:
                    retained.append(old)
                    continue
                county = re.search(r"\bCounty (?:of )?(Antrim|Armagh|Down|Fermanagh|Londonderry|Tyrone)\b", description or body, re.I)
                if county:
                    address['Line_2_County'] = county.group(1).title()
                    address['COUNTY_ID'] = str(['Antrim','Armagh','Down','Fermanagh','Londonderry','Tyrone'].index(address['Line_2_County'])+1)
                barony = re.search(r"\bBarony of (.+?)(?: and County|[,.;]|$)", body, re.I)
                if not barony and townland:
                    barony = re.search(re.escape(townland) + r"(?:\s*\(PART\))?\s*\|(?:[^|]*\|){3}\s*([^|.]+)", body, re.I)
                if barony: address['Line_2_Barony'] = barony.group(1).strip()
                if street:
                    locality = re.search(r"(?:,\s*|Borough of\s+)([A-Za-z][A-Za-z ]+?)(?:,?\s+(?:in the )?(?:and )?County|[,.]|$)", description, re.I)
                    if locality: address['Line_5_Other'] = locality.group(1).strip()
                address.update(_record_id=old.get('_record_id'), _parent_id=old.get('_parent_id'))
                index = len(attrs.setdefault(address_table, []))
                attrs[address_table].append(address)
                for name, value in list(address.items()):
                    if not name.startswith('_'):
                        assign(address_table,index,name,value,'manual-capture: address components from captured description',body_sources)
                for link in relationships:
                    if link['child'] == old.get('_record_id'): link['table'] = address_table
                doc = next((r for r in attrs.get(doc_table,[]) if r.get('_parent_id')==old.get('_parent_id')),None)
                if doc is None:
                    doc = {'_record_id':f'manual_doc_{index}', '_parent_id':old.get('_parent_id')}
                    attrs.setdefault(doc_table,[]).append(doc)
                    relationships.append({'parent':doc['_parent_id'],'child':doc['_record_id'],'table':doc_table})
                di = attrs[doc_table].index(doc)
                for field in ('LOCATION','ALL_OF_PROPERTY'):
                    if old.get(field) and not doc.get(field):
                        assign(doc_table,di,field,old[field],'manual-capture: description field placement',body_sources)
            if retained: attrs[old_table] = retained
            else: attrs.pop(old_table,None)
        # Partial-property flags follow the supplied rule, never an assumed amount/identity.
        if re.search(r'\bpart(?:s)?\b',body,re.I):
            for i, child in enumerate(attrs.get('FT_DESCRIPTION_OF_LAND_ENTRY',[])):
                doc = next((r for r in attrs.get(doc_table,[]) if r.get('_parent_id')==child.get('_record_id')),None)
                if doc is None:
                    doc={'_record_id':f'manual_partial_doc_{i}','_parent_id':child.get('_record_id')}
                    attrs.setdefault(doc_table,[]).append(doc)
                    relationships.append({'parent':doc['_parent_id'],'child':doc['_record_id'],'table':doc_table})
                if not doc.get('ALL_OF_PROPERTY'):
                    assign(doc_table,attrs[doc_table].index(doc),'ALL_OF_PROPERTY','N',rules[doc_table]['ALL_OF_PROPERTY']['source'],body_sources)

    if entry['entry_type'] == 'Leasehold Yearly Rent':
        for i, item in enumerate(attrs.get('FT_LHOLD_YRLY_RENT', [])):
            prior = evidence.get(f'FT_LHOLD_YRLY_RENT[{i}].CASE_TYPE', {})
            quoted = ' '.join(s.get('quote','') for s in prior.get('sources',[])).casefold()
            quoted += ' ' + str(generic.get('FOLIO_TEXT_2','')).casefold()
            trigger = {'TOP':'in conjunction with', 'FR':'first registered'}.get(item.get('CASE_TYPE'))
            if trigger and trigger not in quoted:
                assign('FT_LHOLD_YRLY_RENT', i, 'CASE_TYPE', 'L',
                       rules['FT_LHOLD_YRLY_RENT']['CASE_TYPE']['source'])

    parties = attrs.get("FOLIO_PARTY", [])
    if entry['entry_type'] == 'Leasehold Particulars' and any(
            any(p.get(f) for f in ('FOLIO_PARTY_FORENAME','FOLIO_PARTY_SURNAME','FOLIO_PARTY_ORG_NAME')) for p in parties):
        for i, lease in enumerate(attrs.get('FT_LHOLD_PARTICULARS_LEASE', [])):
            assign('FT_LHOLD_PARTICULARS_LEASE', i, 'PARTIES_REQD', 'Y',
                   rules['FT_LHOLD_PARTICULARS_LEASE']['PARTIES_REQD']['source'])
    ids = {}
    for i, party in enumerate(parties):
        if not party.get('IS_PERSON'):
            organization = party.get('FOLIO_PARTY_ORG_NAME')
            personal = party.get('FOLIO_PARTY_FORENAME') or party.get('FOLIO_PARTY_SURNAME')
            if bool(organization) != bool(personal):
                assign('FOLIO_PARTY', i, 'IS_PERSON', 'N' if organization else 'Y',
                       'manual-capture: classify captured party name as organization or person')
        party_id = str(i + 1)
        ids[party.get("_record_id")] = party_id
        assign("FOLIO_PARTY", i, "FOLIO_PARTY_ID", party_id, "manual-capture: local party sequence")
    for i, link in enumerate(attrs.get("FT_LHOLD_PARTICULARS_LEASE_PARTY_PERSON", [])):
        # Match an explicit party link; positional fallback only for equal cardinality.
        party_id = ids.get(link.get("_party_id"))
        if party_id is None and len(parties) == len(attrs["FT_LHOLD_PARTICULARS_LEASE_PARTY_PERSON"]):
            party_id = str(i + 1)
        if party_id:
            assign("FT_LHOLD_PARTICULARS_LEASE_PARTY_PERSON", i, "FOLIO_PARTY_ID", party_id,
                   "manual-capture: local party reference")
    body = ' '.join(str(r.get('FOLIO_TEXT_2','')) for r in attrs.get('FT_FOLIO_TEXT',[]))
    if body.strip():
        for i, party in enumerate(attrs.get('FT_LHOLD_PARTICULARS_LEASE_PARTY',[])):
            for field, phrase in [('PARTY_AND_ANOTHER','and another'),('PARTY_AND_OTHERS','and others')]:
                if not party.get(field) and phrase not in ' '.join(body.casefold().split()):
                    assign('FT_LHOLD_PARTICULARS_LEASE_PARTY',i,field,'N',
                           'manual-capture: qualifier absent from captured party clause',
                           evidence.get('FT_FOLIO_TEXT[0].FOLIO_TEXT_2',{}).get('sources',[]))
    entry["capture_profile"] = "manual-v2"
    return entry


def parcel_records(attrs, table, child_index=None):
    """Select one parcel's records without detaching nested address/date relationships."""
    records = attrs.get(table, [])
    children = attrs.get('FT_DESCRIPTION_OF_LAND_ENTRY', [])
    if child_index is None or not children: return records
    if child_index >= len(children): return []
    child = children[child_index]
    parents = {r.get('_record_id'):r.get('_parent_id') for rs in attrs.values() for r in rs if r.get('_record_id')}
    def below(item, ancestor):
        current = item.get('_record_id'); seen = set()
        while current and current not in seen:
            if current == ancestor: return True
            seen.add(current); current = parents.get(current)
        return False
    if table == 'FT_FOLIO_TEXT' and len(records) > 1:
        return [r for r in records if below(child,r.get('_record_id'))]
    if table == 'FT_DESCRIPTION_OF_LAND_ENTRY': return [child]
    if table.startswith('FT_DESCRIPTION_OF_LAND_ENTRY_'):
        return [r for r in records if below(r,child.get('_record_id'))]
    return records


def manual_sheets(folios):
    """Return the six manual-layout matrices, using only extraction values.

    Layout JSON contains headers and field names only, never answer cells.
    A land entry with repeated parcels becomes one column per child record.
    """
    layout = json.loads(Path(__file__).with_name("manual_layout.json").read_text())
    columns = {sheet: [] for sheet in layout}
    named_folios = any(f.get("source_kind") == "image_folio" for f in folios)
    for folio in sorted(folios, key=lambda f: f["source_id"]):
        for original in sorted(folio["entries"], key=lambda e: (
                min((r["page"] for r in e["regions"]), default=0),
                min((r["box"][1] for r in e["regions"]), default=0))):
            image_names = entry_image_names(folio, original)
            entry = complete_capture(deepcopy(original), folio['source_id'], image_names)
            sheet = SHEET_NAMES.get(entry["entry_type"], entry["entry_type"])
            if sheet not in columns:
                continue
            attrs = entry.get("attributes", {}) if not entry.get("status") else {}
            children = attrs.get("FT_DESCRIPTION_OF_LAND_ENTRY", []) if sheet == "Description of Land" else []
            for child_index in range(max(1, len(children))):
                values = []
                for _, table_label, field in layout[sheet]:
                    table = table_label.split("|")[-1].strip()
                    field = field.strip()
                    records = parcel_records(attrs, table, child_index if children else None)
                    parts = [str(r[field]) for r in records if r.get(field) not in (None, "")]
                    # Two people can share a first/surname; do not erase a party's name.
                    value = " | ".join(parts if table == 'FOLIO_PARTY' and field not in {'IS_PERSON','FOLIO_PARTY_ID'}
                                       else dict.fromkeys(parts))
                    if field == "FOLIO_IMAGE_FILENAME": value = ";".join(image_names) if image_names else folio["source_id"]
                    elif field == "FT_ENTRY_TEXT_TYPE_NAME": value = sheet
                    elif field == "IS_ENTRY_STRIKETHROUGH": value = entry.get("status") or "Blank"
                    values.append(value or None)
                page_numbers = entry_page_numbers(folio, entry) if image_names else sorted({r["page"] for r in entry["regions"]})
                page = (page_numbers[0] if len(page_numbers) == 1 else ", ".join(map(str,page_numbers))) if image_names else page_numbers[0]
                number = entry.get("page_entry_number")
                # Never silently invent a manual entry number from a sheet column count.
                if children and len(children) > 1 and isinstance(number, int): number += child_index
                headers = [page, number]
                if named_folios: headers.append(folio.get("folio_name", folio["source_id"]))
                columns[sheet].append([*headers, None, *values])
    result = {}
    for sheet, fields in layout.items():
        labels = [[None, None, "Page No"], [None, None, "Entry No"]]
        if named_folios: labels.append([None, None, "Folio Name"])
        labels.extend([["#", "TABLE_NAME", "FIELD_NAME"], *fields])
        result[sheet] = [row + [column[i] for column in columns[sheet]] for i, row in enumerate(labels)]
    return result


def manual_xlsx_bytes(folios):
    """Runtime export for the Python service (which has no Node/artifact runtime)."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)
    header_rows = 4 if any(f.get("source_kind") == "image_folio" for f in folios) else 3
    for title, matrix in manual_sheets(folios).items():
        sheet = book.create_sheet(title)
        sheet.freeze_panes = f'D{header_rows+1}'
        sheet.sheet_view.showGridLines = False
        widths = [5, 44, 42] + [28] * (len(matrix[0]) - 3)
        for c, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(c)].width = width
        for r, row in enumerate(matrix, 1):
            lines = max((len(str(v or '')) + 23) // 24 for v in row)
            sheet.row_dimensions[r].height = 24 if r <= header_rows else max(30, (lines+1)*14)
            for c, value in enumerate(row, 1):
                cell = sheet.cell(r, c, value)
                # OCR text is text even if it starts with a spreadsheet formula marker.
                if isinstance(value, str): cell.data_type = 's'
                cell.font = Font(name='Arial', size=10, bold=r <= header_rows)
                cell.alignment = Alignment(vertical='center', wrap_text=True)
                if r <= header_rows: cell.fill = PatternFill('solid', fgColor='E7EDF1')
                elif c <= 3: cell.fill = PatternFill('solid', fgColor='F3F5F6')
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
