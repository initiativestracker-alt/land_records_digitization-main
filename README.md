# Folio capture

## JPEG folio folders and annotations

Select **Select folio folder** in the workspace, choose a folio or a parent root,
inspect the folder/page counts, then import and start extraction. Each directory
containing JPEG files is one folio; grouping folders such as A3/A4 are not folios.
Alternatively, submit a local root to the deployed service:

```bash
.venv/bin/modal run modal_app.py::seed --folio-root "POC Samples" --start-batch
.venv/bin/python test_folio_images.py
```

Pages sort by the leading number in their filenames. Duplicate or unnumbered pages
are rejected; numbering gaps remain explicit review issues. Inputs retain original
JPEG bytes and filenames. The folder basename is **Folio Name**, not a substitute
for the legal folio number read from the document. CSV, evidence JSON and manual
workbooks preserve original page numbers and image filenames. Directory/mixed-source
workbooks add a Folio Name header; PDF-only workbooks keep their existing layout.
Image folios share the PDF pipeline's 1–200 page and 50 MB source limits.

In **Annotated view**, the image is on the left and page attributes are on the right.
Coloured, numbered source marks link to individual attributes. Select a mark or
attribute to emphasise and navigate to its evidence. OCR-matched locations are
solid; model-only locations are dashed. These are location signals, not correctness
probabilities. Defaults, input-derived identity, human corrections without page
evidence and unlocated fields do not acquire invented source marks. The view is
read only, leaves scans/snapshots unchanged and makes no model calls when opened.

JPEGs enter OCR directly after EXIF orientation, preserving native pixel dimensions
and aspect ratio. The same normalized coordinates apply to all paper sizes.
`POST /api/sources/folio` accepts authenticated multipart `folio_name` and JPEG `files`.
Folder inputs and the supplied POC Samples are ignored by Git at their existing paths.

## Manual capture output

The batch download now offers a six-sheet Excel workbook matching the supplied manual
capture layout: Page No and Entry No across the top, then #, TABLE_NAME and FIELD_NAME
down the left, with one entry/parcel per column. The API is
`GET /api/batches/{id}/download?version=current&format=manual`.
Original CSV snapshots remain available. Workbook downloads preserve review gates,
do not modify stored snapshots, and keep source text as literal text rather than formulas.
Unknown page entry numbers remain blank until inventory/review establishes them.

`manual-v2` stores printed folio references and local party sequence numbers; these must
not be mistaken for resolved production database IDs. It applies schema defaults,
standardizes document labels, separates address components into the requested fields,
and records the derivation rules and source evidence. Model prompts now include full-page
context as well as crops. Geometry warnings require review but no longer suppress an
entire otherwise-active entry solely because an underline or table rule was detected.

Local iteration commands (the first does not call a model):

```bash
.venv/bin/python iterate_capture.py
CANDIDATE_ZIP=output/manual-v2/current.zip EVALUATION_OUTPUT=output/manual-v2/evaluation .venv/bin/python evaluate_manual.py
.venv/bin/python test_manual.py
.venv/bin/python test_capture.py
```

`iterate_capture.py --extract` sends source pages/crops to the configured Gemini API.
Do not run it until the user explicitly approves that payload and destination. It reads
source PDFs, extraction snapshots and schema rules, never manual answer cells.
Successful entry calls are checkpointed by model, source archive, schema and extraction
code fingerprint. Changed settings cannot reuse an incompatible checkpoint. The manifest
records fresh/cached entry counts and failures that retained baseline values. The evaluator
alone reads the manual labels. Its linkage is
currently fixed to the original batch inventory, so a new inventory must be reconciled
before its score can be compared fairly.

`export_manual.mjs` creates the standalone workbook and visual previews with
`@oai/artifact-tool` in the local artifact runtime. The deployed Python service uses its
existing openpyxl dependency for runtime downloads; it has no Node/artifact runtime.

Measured local progress and remaining work are recorded in `MANUAL_CAPTURE_PROGRESS.md`.
The latest local workbook and comparison are in `output/manual-v3-delivery/`. The selected
candidate matches 898/965 populated manual fields and all 67 counted entry numbers. A full
111-entry fresh extraction repeat scored 888/965, so the selected result is not a guaranteed
fresh-run accuracy. `report.md` and `repeatability.json` preserve both results and limitations.

React workspace and private Modal workers for the supplied Northern Ireland land records. The workbook is read directly, including detailed capture rules. Input PDFs are kept in `data/`; each source is split into numbered page PDFs with `pdf_to_folio.py`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python pdf_to_folio.py
cd frontend
npm ci
npm run build
```

The deployed application requires HTTP Basic credentials for the UI, assets, API, images and exports. Local credentials are in ignored `data/deployment-credentials.json`. Modal Secrets `folio-login` and `folio-gemini` hold the login and Gemini token. No token reaches the browser.

```bash
.venv/bin/modal deploy modal_app.py
.venv/bin/modal run modal_app.py::seed --start-batch
.venv/bin/python test_capture.py
```

The seed command calls the deployed services, preserving a single SQLite writer. Durable state and source/evidence files use separate Modal Volumes. Result and review mutations commit before acknowledgment; progress messages are transient and rely on background commits. Entries run with twelve threads per folio, and successful checkpoints are reused on resume. **Before redeploying a live instance, finish or interrupt active work and stop the existing Modal app to drain its state writer.** Then redeploy with the same volume names. Do not scale the state class above one container; use a transactional database before doing so.

A batch publishes one CSV per folio and a ZIP as soon as extraction finishes. Failed folios appear explicitly in the manifest and can be resumed. Original extracted results remain separate from current human corrections. Fully reviewed exports require every selected folio to finish and every entry to be approved.

Each logical entry or schedule row gets a CSV row; metadata repeats per row. Repeated attribute groups contain JSON arrays, and full table JSON retains local parent relationships. Legal folio numbers come from document headers, independently of source filenames. Lookup database IDs stay blank. Suppressed entries expose identity/type/status/source, with body attributes blank.

The pipeline renders full scans, uses Tesseract and independent PaddleOCR/layout/table recognition on L4 GPUs, inventories entries/continuations with Gemini, and extracts each active entry against workbook rules. Source boxes, quotes, rule provenance, OCR agreement, geometric cancellation checks, date/county/postcode constraints and uncovered text support human review. They are review signals, **not calibrated probabilities**. All entries initially require review. Continuation notices flag that the supplied PDF may omit history.

Reviewers can confirm folio identity, edit fields, adjust/add/split/merge regions, re-extract, acknowledge structural gaps and approve entries. Revisions prevent stale edits and every mutation has before/after audit evidence.

`evaluate.py evidence.json labels.json` compares independently human-labelled field values with the extraction, reporting exact matches and a one-sided 95% Wilson lower bound. Labels use `entry_id`, `field` (`TABLE[0].FIELD`) and `expected`. The 99.5% target is not established without a representative, independently labelled evaluation set; automatic acceptance is disabled.

The approved manual-format experiments and current deliverable are documented in
`output/manual-v2-delivery/report.md`. To evaluate a changed inventory, provide its
matching evidence directory as well as its CSV archive:

```bash
CANDIDATE_ZIP=output/manual-v2-delivery/current.zip \
CANDIDATE_EVIDENCE_DIR=output/manual-v2-delivery \
EVALUATION_OUTPUT=output/manual-v2-delivery/evaluation \
.venv/bin/python evaluate_manual.py
```

`refresh_inventory.py` re-inventories the saved source pages; `apply_inventory.py`
reconciles proposals by geometry and reference identity, retaining earlier records for
review when unconfirmed. `--captures ... --fresh-captures-only` overlays only the selected
trial's newly generated results, so it cannot replace newer results with retained baseline
entries. Reconciliation and header-number agreement require review; they are not proof of
perfect inventory. `iterate_capture.py --missing-only` fills recovered/failed empty entries.
Manual answer values are read only by evaluation, never by extraction or reconciliation.
