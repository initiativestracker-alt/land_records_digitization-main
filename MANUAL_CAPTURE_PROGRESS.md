# Manual capture iteration checkpoint — 5 October 2026

Objective remains active: match the manual output format and maximize extraction accuracy
and coverage, stopping when perfect or at the user's weekly usage threshold. The working
interpretation is 50% total weekly Codex usage, pending the user's clarification.

## Verified progress

The same 67 labelled occurrences and 965 populated attribute cells in the 18-PDF manual
workbook were evaluated throughout. Filename and type are scored separately from attributes.
No manual answer values were copied into the extraction pipeline.

| Candidate | Correct populated fields | Coverage |
|---|---:|---:|
| Saved current export before changes | 612/965 | 63.4% |
| Schema defaults and visible/local references | 677/965 | 70.2% |
| Address field placement and label normalization | 730/965 | 75.6% |
| Registration-reference placement and party qualifiers | 748/965 | 77.5% |

Latest: 803/965 expected populated fields filled (83.2%); 748/803 of these agree (93.2%).
There are 162 missing expected values, 55 differing populated values, and 48 values on
manual-blank fields. Including those additional values gives precision 748/851 = 87.9%.
All-cell agreement is 2,123/2,442 = 86.9%, including blank-to-blank matches.
No previously matching benchmark cell regressed in the latest local pass.
These are agreement scores against the supplied manual, not independently adjudicated truth.

The improvements above are deterministic processing of existing saved extraction evidence.
Revised model prompts have NOT yet been evaluated with new API calls. The original batch
archives and manual workbooks remain unchanged.

## Format and integration

- Six-sheet workbook exporter implemented with the manual row labels/order, one entry or
  parcel per column, page numbers, and entry numbers where established.
- Standalone workbook: `output/manual-v2/Manual_Format_Extraction.xlsx`.
- Extraction matrices: `output/manual-v2/manual_sheets.json`.
- Current candidate archive: `output/manual-v2/current.zip`.
- Field-by-field evaluation: `output/manual-v2/evaluation/field_comparison.csv`.
- API supports `format=manual` XLSX download, with authentication and review gates.
- Frontend primary download points to the manual-format workbook. Frontend build passes.
- `test_manual.py` passes, including literal spreadsheet text, parcel-specific dates,
  nested address relationships, snapshot preservation, defaults and idempotence.
- `test_capture.py` passes outside the sandbox. The sandbox stalls the Starlette test
  client's thread bridge; a timed stack trace confirmed that environment issue.
- Normalization is idempotent across all 228 saved entries.
- Changes are local and have not been deployed to the live Modal app.

## Source evidence checked

- `24875`, page 1: the land description is active. The place name is underlined.
  Geometry-based suppression was a pipeline error. The engine now retains active text
  and flags strokes for review; the old suppressed snapshot still needs fresh extraction.
- `23548`, page 4: the first transfer is active, with only some ancillary text crossed out.
  The existing uncertain entry needs re-extraction with correctly scoped cancellation.
- `665254`, page 4: two transfer rows really occur on this page, repeating references
  also present on page 1. Do not remove them from occurrence coverage as duplicates.
- The 12-PDF workbook has an additional duplicate Covenant FOLIO_ENTRY_ID template row.
  The implemented canonical layout follows the 18-PDF workbook, with that field once.

## Outstanding work / approvals

1. Await explicit approval to send the supplied land-record PDF pages/crops, including
   names and property details, to Google's Gemini API using the existing project account.
   Automatic approval review rejected the trial command because the payload/destination
   had not been explicitly authorized. Do not reroute around that rejection.
2. Then run the prepared three-entry trial, inspect its output and benchmark changes,
   expand to fresh inventory and extraction, reconcile new inventory IDs, and iterate.
3. Locate the source PDFs for the second workbook. No matching PDFs were found across
   the home directory by the supplied identifiers. Its 27 entries remain unprocessed.
4. Establish original page entry numbers; legacy snapshots do not reliably contain them.
   They remain blank in the interim workbook rather than being copied from manual labels.
5. Re-adjudicate differing values against PDFs, separating source errors, manual-label
   errors, and capture conventions. Perfect matching has not been achieved.
6. Deploy and verify the live app only after the candidate is concrete and validated,
   following the existing single-writer/drain requirement in README.md.

Weekly usage started at 7% and was last checked at 23% during this turn. Read the latest
`token_count` event's `rate_limits` from this thread's rollout file before further sustained
work; a 10080-minute window is the weekly counter. Do not expose credentials or unrelated
session data. Do not mark the goal complete at this checkpoint.

## Follow-up checkpoint

Trial caches now use a fingerprint of the requested model, baseline archive, capture
schema and processing code. `manifest.json` records fresh/cached entry counts and refresh
failures (baseline retained), preventing configuration changes from silently reusing old
trial results. `test_iteration.py` passes for repeatability and model/source isolation.
A local-only rerun preserves the 748/965 benchmark and confirms zero fresh or cached model
calls. Gemini approval and the additional source PDFs remain pending; this is the second
consecutive goal turn encountering that approval blocker, with useful local work completed.

## Blocked audit

The same external-extraction approval blocker remains for a third consecutive goal turn.
The current manifest confirms no extraction has run, and the saved evaluation remains
748/965 correct expected fields. Weekly usage is 27%, below the requested threshold.
Local format, normalization and trial-infrastructure work is complete enough for the next
experiment; further substantive system accuracy validation requires fresh extraction.
No explicit Gemini transfer approval or location for the additional 12 PDFs has arrived.
Goal marked blocked pending that user input, not complete. Do not retry the rejected
transfer without explicit approval.

## Approved trials and delivery checkpoint

The user explicitly approved the full available 18-PDF batch and repeated Gemini trials,
including page/crop payloads containing names and property details. The earlier transfer
approval blocker is resolved; do not request this authorization again. The user also
confirmed that all available source files are under this project's `data` directory and
selected source-faithful values with manual discrepancies reported separately.

Actual Gemini trials have now run. Flash remains the selected model: the small Pro
comparison did not improve benchmark agreement. The first full pass refreshed 96/100
active detailed entries; all four failures were subsequently resolved in targeted trials.
Inventory refinement recovered previously suppressed active text, embedded leases and
indentures, and repeated transfer rows. Full-paragraph transcript requests were removed
from the manual profile, resolving two RECITATION failures. Targeted geometry validation
feedback resolved malformed evidence-box responses. All trial sessions are terminal.

Latest delivery: `output/manual-v2-delivery/Manual_Format_Extraction.xlsx`.
Report: `output/manual-v2-delivery/report.md`.
Metrics: `output/manual-v2-delivery/evaluation/summary.json`.

Final current checkpoint on 67 labelled occurrences / 965 expected populated fields:
- Correct: 897/965 = 93.0% (original baseline 612/965 = 63.4%).
- Filled: 943/965 = 97.7%.
- Agreement among filled expected fields: 897/943 = 95.1%.
- Including 71 values on manual-blank cells: precision 897/1014 = 88.5%.
- 22 expected values missing, 46 populated values differ.
- All 67 labelled entries linked; no active detailed entry has empty attributes.
- Page-entry numbers: 49/67 agree; 18 differ. This remains unfinished.
- Both workbooks together: 897/1317 correct fields (68.1%); 27 entries from the
  additional 12 sources are still untested because the PDFs are absent.

The exported 125 columns include unlabelled and suppressed entries. Benchmark entry
linkage does not establish precision for those additional entries. The evaluator now
follows parcel relationships and preserves repeated party names; the original baseline
and final scores were rerun under the same comparator and remain 612 and 897.

The manual's October=08 and 8th=06 labels for 3120225 are confirmed source conflicts;
source crops are linked in the report. Ten currency-field differences conflict with the
supplied pre-decimal capture rule. These remain in the raw denominator. No adjusted
perfect accuracy claim has been made and the original manuals were not changed.

All six workbook previews visually reviewed. Every workbook cell, dimensions, sheet order,
freeze panes and absence of formulas verified against extraction matrices. Manual,
iteration/reconciliation/geometry tests, mocked capture/API tests and frontend build pass.
Changes remain local; no live deployment has occurred. Pipeline version is now
`folio-v2.1-manual`; deployed process caches will invalidate when it is deployed.

Latest weekly usage check: 48%. The user requested stopping at 50% total weekly usage.
Do not launch sustained additional work without checking the current counter. The goal
is not complete: numbering, remaining field disagreements, absent sources, and deployment
remain outstanding. The goal tool still reports the older blocked status; the tool does
not expose an active/resume status mutation, and approval is no longer the actual blocker.

Final handoff: original archive SHA-256 hashes verified against pilot-report.json; all 67 linked labels have matching entry types. Nine deliverable artifacts are checksummed in artifact_sha256.json. Weekly usage last observed at 49%; further trials stopped to stay within the 50% cap. No perfect-completion claim or live deployment.

## Resumed goal: numbering and source review

The active goal now records the user's resume instruction to use the entire weekly
allowance. Automatic approval review nevertheless rejected the renewed Gemini trial
twice, applying the former 50% stop condition and stating that a direct user reply
is required. A concise async continuation-approval question is pending. Do not retry
or bypass that rejection until the direct reply arrives. The previously authorized
18-PDF payload scope has not changed.

The user clarified that manual Entry No values are assigned by counting. Inventory
instructions now distinguish substantive paragraphs/schedule rows from continuation
text, dependent residue statements, headings and stamps. A numbers-only application
mode preserves existing entries and extraction values. Local execution against saved
inventories verified that all data across 240 entries was unchanged except numbering
metadata. test_iteration.py passed with mocked API calls. No fresh numbering trial
has run, so the delivered 49/67 numbering agreement remains the current result.

Additional source review is saved in output/manual-v3-source-review/review.md and
adjudications.csv. Visually confirmed the description's printed Tryone spelling and
Holywood versus the manual's Hollywood. The 24652 lease lists one organization and
two people across two contractual party groups; entity and party-group counts differ.
Its marginal instrument association remains unresolved and was not auto-corrected.
Manual labels and raw scores were not changed by this review.

## Gemini authorization resolved and v3 progress

The user directly confirmed: "i confirm you can use gemini for whatever" in response
to the pending continuation approval. Automatic approval review accepted the subsequent
Gemini calls. Do not ask for the old 50% cap or available-batch payload permission again.

Numbering trials completed under output/manual-v3-numbering-inventory,
manual-v3-numbering-refined and manual-v3-numbering-final. Source-based rules now handle
attached notes, lease clauses, independent post-transfer rights and separate BURDEN
headings. Header agreement progressed 49 -> 57 -> 63 -> 67 out of 67 labelled entries.

Latest completed candidate: output/manual-v3-numbering-result. Its six-sheet workbook
has 125 columns; all 5,967 cells were read back exactly and all six previews inspected.
Metrics: 898/965 correct (93.1%), 944/965 populated (97.8%), 898/944 populated agreement
(95.1%), 70 manual-blank extras, 21 expected missing and 46 populated differences.
All 67 labelled entries link and all 67 entry numbers agree. Combined manuals 898/1317
correct (68.2%); the other 12 PDFs remain absent. Report.md records the definitions.
The Pro detail trial improved one expected document-label field and removed one extra
stamp value; it did not change the source-correct Registry-map-only descriptions.

A full Flash repeat is running in exec session 25838, output/manual-v3-repeat.
Poll this confirmed live handle; do not restart solely because a poll is quiet. It uses
the numbered candidate as its inventory baseline and freshly extracts every active
six-type entry. Its extraction code was loaded before the following maintenance fixes;
field prompts are unchanged. No result from this run is included in the reported v3
candidate metrics yet. Latest observed checkpoint count exceeded 80, with no terminal
failures. Gemini inventory and Pro detail trial sessions are terminal.

Pipeline version is now folio-v2.2-manual. process_folio no longer treats a lone/self
parent_key as a split; actual shared source parents still count. Manual normalization
reindexes document evidence when earlier records move to the marginal table. A focused
mixed-document regression test and mocked full API/pipeline tests pass. Existing v3
candidate and repeat checkpoints had zero document-reference evidence/value mismatches.
No live redeployment occurred.

## Latest completed v3 delivery and repeatability audit

All trial sessions are now terminal, including full repeat 25838, spelling 79453,
regions 22341 and registration 19871. No model job is running.

Latest delivery is output/manual-v3-delivery/Manual_Format_Extraction.xlsx, with report.md,
evaluation CSVs/JSON, repeatability.json, evidence JSONs and artifact_sha256.json.
All 5,967 workbook cells read back exactly; six-sheet layout previews checked (final
changed Description/Easement previews rechecked). All 67 labelled entries link and
all 67 entry numbers match. Current raw metrics are 898/965 correct (93.1%), 943/965
populated (97.7%), 898/943 filled agreement (95.2%), 69 manual-blank extras (precision
898/1012 = 88.7%), 22 missing and 45 populated differences. Both manuals 898/1317 = 68.2%.

Full Flash repeat completed 111/111 active six-type entries, no failed calls, no cached
entries. It retained the candidate inventory/OCR and scored 888/965 correct (92.0%),
940/965 populated (97.4%) and 888/940 filled agreement (94.5%). This is evidence of
run-to-run variance, not the final candidate's score. The delivery combines selected,
traceable trials; do not claim 93.1% is a guaranteed fresh-run result. Full repeat files
remain under output/manual-v3-repeat, with changed_agreements.csv in evaluation.

Source review selected two full-repeat entries for actual source-fidelity improvements:
3835250 p2_e2 now has both mapped and unmapped child rights; 1551671 p1_desc_1 omits an
unsupported positive ALL_OF_PROPERTY value. Their selection manifest is in
output/manual-v3-source-corrections; intermediate baseline manual-v3-source-corrected.

New source inventories give tighter description boundaries for 612898 and 2359507.
apply_inventory --regions-only updates source regions without changing membership or
numbering. It is mutually exclusive with --numbers-only. Reconciliation now clears stale
inventory_proposals so unmatched old proposals cannot masquerade as fresh matches.
Filtered source inventories are in manual-v3-description-regions; manual-v3-regions-baseline
and manual-v3-regions-trial preserve that experiment. Tighter geometry alone did not
prevent one neighbouring-registration error, so registration_outside_entry now triggers
an isolated four-field registration recheck when evidence is in a different vertical
block. It preserves other fields and validates exact target IDs/fields. Mocked integration
and adjacent-margin-versus-neighbour tests pass. A fresh two-description trial in
manual-v3-registration-trial captured both correctly; its first passes were correct,
so the live repair branch itself was not exercised in that trial. The final delivery
uses its two captures and the tighter regions. Final configuration full-batch repeat
is NOT yet established; do not overstate that validation.

The one-entry Pro spelling trial did not improve manual agreement and was not promoted.
A source crop shows FASGLASHACH in 2359507; other similar C/G readings remain ambiguous.
Source-review ledger has 10 documented findings, including map-definition and rent-case
rule conflicts. Original labels remain unchanged. No independent adjusted accuracy claim.

Current pipeline code remains folio-v2.2-manual; no live deployment. Latest weekly counter
observed 76%, with direct user authorization to use the entire weekly allowance. The old
approval blocker is resolved. The goal tool still showed blocked from the earlier refusal;
its exposed API cannot mark it active. The objective is not complete: unavailable 12
sources, remaining source/benchmark disagreements, and final-configuration repeatability
remain unresolved. Do not report the former Gemini permission refusal as still blocking.
