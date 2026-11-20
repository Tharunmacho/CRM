# Recruitment stages six to twelve and deployment

These stages handle the medical, the police clearance certificate (PCC), passport custody, advance payment, visa, stamping and travel. Requirements: **STAGE-06, STAGE-07, STAGE-08, DEPLOY-01**. There are twelve stages in all: stage 6 Medical, stage 7 PCC, stage 8 Passport, stage 9 Advance payment, stage 10 Visa arrival + Google review, stage 11 Visa stamping, stage 12 Air ticket. PCC was split out of the old combined "Medical/PCC" stage 6 (which only ever stored the medical) by migration `0048_pcc_stage`, which also renumbered every stored stage >= 7 by one (see [PCC as stage 7](#stage-7-pcc-and-the-twelve-stage-migration)). The owner's 2026-11-12 manual-stage instruction supersedes the original PRD's mandatory document gates. Apply the permissions, required feedback, manual confirmation and retained history rules in [stages one to five](14-stages-one-to-five.md).

## Shared behavior

Every stage movement requires written feedback. Details and documents are optional, except that the Medical result (stage 6, like stages 4 and 5) must be chosen: see [decision stages](14-stages-one-to-five.md#decision-stages-4-5-and-6). Elsewhere a move with no outcome details records Completed manually and does not fabricate payment or travel facts. Document completeness is tracked separately. Save feedback alone leaves the stage and deadline unchanged; uploads and OCR never move stages automatically.

Every stage here also has the **stopping exit** described in [stages one to five](14-stages-one-to-five.md#stopping-exit-at-every-stage): Rejected or Declined at stages 6 to 11 (with PCC not cleared, Passport not available, Not paid, Visa rejected and Visa stamping refused at stages 7 to 11), and only **Not travelling** at stage 12, which closes the application without deploying.

## Stage 6 Medical and repeated examinations

The medical result is a required choice: Fit or Unfit. Optional details include the application status and date. Fit records Fit and advances to stage 7 (PCC). Unfit records Unfit and stops the candidate: the application closes as medically unfit and the candidate does not go to the next stage. The stage cannot be moved with no result (the API refuses it with a sentence asking for Fit or Unfit).

Unfit may instead plan another medical (*Plan another medical instead of stopping* = Yes). That requires a new medical date, which cannot precede the previous medical date when both are supplied. Confirming this attempt records Unfit: reapplying, retains stage 6 and creates the next follow-up task. Leaving it at No stops the candidate. Every attempt has its own feedback, actor and time. Admin reopening retains these attempts, and is the only way to undo a final Unfit.

## Stage 7 PCC and the twelve-stage migration

The police clearance certificate has its own stage, after the medical and before the passport. It looks and behaves like the other stages: its form is rendered from the stage config (`frontend/src/shared/workflow/stages.ts`, mirrored by `backend/app/domain/stages.py`), completion needs only the written feedback, and the next task is titled "Collect the police clearance (PCC)". Optional details: **PCC status** (Received or Pending), the date applied, the date received (shown for Received) and the PCC copy (shown for Received; it may be the candidate's own PCC document, and a PCC file uploaded here is also filed under the candidate's PCC document). Received records Received, Pending records PCC pending, and a feedback-only move records Completed manually; nothing is invented. The stopping exit is Rejected, Declined or **PCC not cleared** (closure reason `pcc_not_cleared`, outcome Not cleared). The stage is not a decision and does not repeat. The Medical stage keeps only the medical answers.

Migration `0048_pcc_stage` (applied once on deploy, one transaction, set-based SQL, a no-op on an empty database) moves every stored stage number >= 7 up by one: applications' current stage, stage records and their attempts, tasks, daily-work items, application notes, the stage documents (`stage_7_copy` becomes `stage_8_copy` ... `stage_11_file` becomes `stage_12_file`), the audit trail (`Recorded stage 8: ...`, `meta.stage`) and stored escalation notices. Each application that had moved past the medical receives a completed stage-7 record: the old stage-6 record never held PCC fields, so it is recorded as Completed manually with no data, and any PCC fields a stage-6 record does hold are copied across and removed from the medical record. An application sitting at the medical stays at stage 6. The downgrade nests each PCC record into its stage-6 record, deletes the PCC records and shifts back down (an application open at PCC goes to Passport, one closed at PCC to stage 6; `pcc_not_cleared` becomes `unmapped`).

## Stage 8 Passport

Optional details include the copy, original-collected answer, collection date and custodian. An existing candidate passport copy may be prefilled. Collected Yes records Collected; otherwise a manual move records Completed manually. Supplied file/user references are validated, but no copy or collection answer is required to move.

## Stage 9 Advance payment

Optional details include collected Yes/No, amount, currency, date, mode, receipt number and file. Collected Yes records Collected; otherwise a manual move records Completed manually. No amount or receipt is required. A supplied amount must be a valid positive monetary value with supported currency. An omitted amount remains unknown and is not invented in financial reports. Superseded attempts stay excluded from effective collection reports.

## Stage 10 Visa arrival and Google review

Optional details include arrived Yes/No, date, visa copy and Google-review status. Arrived Yes records Arrived; otherwise a manual move records Completed manually. None of these details/files is a movement gate. Recruiters can retain the current task by saving feedback without moving.

## Stage 11 Visa stamping

Optional details include done Yes/No, date and stamped-copy upload. Done Yes records Stamped; otherwise a manual move records Completed manually. The copy is optional. Supplied references must belong to the application/stage.

## Stage 12 Air ticket and deployment

Optional details include booked-by, travel date, airline/flight number, PNR and ticket file. A supplied ticket records Booked; feedback-only completion records Completed manually. Completing the final stage manually makes the application and candidate Deployed, closes its task and creates no further recruitment task.

Missing tickets or checklist documents never prevent deployment. An unknown travel date remains unknown in Travelled; neither the completion timestamp nor today's date is substituted. The checklist continues to show missing items after deployment.

The backend still locks and checks order capacity so concurrent candidates cannot claim the same final opening. When deployments reach openings, the order becomes Filled. Admin reopening removes the effective deployment, recalculates count/status, retains previous feedback/files and creates current work. A newer active or deployed placement prevents reopening an older one over it.

## Two Care India ID series

Both public IDs start with `CI` (the agency), but they are two simple, separate, **continuous** series: no financial-year part, no restart each April, numbers never reused. Neither replaces the long-lived `CND-*` reference (`ref`), which stays on every record and in links, and in the search box, but people are shown only the Care India ID (notifications, Data Bank and Travelled exports, candidate record export, invoices).

| | Data Bank ID | Traveller ID |
| --- | --- | --- |
| Example | `CI-001`, `CI-002`, ... `CI-152`, `CI-153` | `CI-T-001`, `CI-T-002`, ... `CI-T-116`, `CI-T-117` |
| Field | `careId` (`crm_candidates.care_id`) | `travellerId` (`crm_candidates.traveller_id`) |
| Numbers | everybody who enters the Data Bank | only people who travelled |
| Order | the order candidates are created | the order candidates become Deployed (not the travel date) |
| Counter | `crm_candidate_id_counters` (single row `GLOBAL`) | `crm_traveller_id_counters` (single row `GLOBAL`) |
| Assigned | at creation (every intake path), never later | when the placement becomes Deployed, never before |

The serial is zero-padded to at least three digits and simply grows (`CI-1000`, `CI-T-1000`) beyond 999. The next number is always one more than the highest ever issued: if the last traveller is `CI-T-116` the next one is `CI-T-117`. Deleting a candidate leaves a hole; nobody is renumbered and the number is not issued again. Both IDs are unique and immutable once assigned (a database trigger refuses any change). Reversing a deployment (admin reopen) keeps the Traveller ID, and completing the stage again returns the same number and uses up no new one; a rolled-back transaction consumes no number. Concurrent intakes or completions serialise on the single counter row (one `INSERT .. ON CONFLICT DO UPDATE .. RETURNING`), so two people never get the same number.

The Traveller ID is assigned explicitly in the same transaction that deploys the placement and under the candidate row lock: the stage-12 `/complete` endpoint, the bulk and historical (travelled) import, and the travelled reconciliation script. Migration 0044 replaced the earlier financial-year formats (`CI-26-27-078`, `CI-T-26-27-012`, migrations 0040/0041): it renumbered every existing candidate contiguously from `CI-001` in their previous order (so a deleted candidate's gap closes) and every existing traveller from `CI-T-001` in their previous travel order, then set both counters to the new maximum. Its downgrade restores a valid financial-year numbering by `created_at` / travel date, but cannot recover the original numbers after rows were deleted or moved. The format lives in one place, [traveller_id.py](../../backend/app/services/traveller_id.py) (Data Bank ID: [care_india_id.py](../../backend/app/services/care_india_id.py)).

Every payload that names a candidate carries `careId` next to `ref` so screens can show only the Care India ID: lead rows (`candidateCareId`, `matchedCandidateCareId`), the lead convert / unconvert-check / `candidate_exists` candidate, duplicate matches, bulk-import results (`careId`, `duplicateOfCareId`), fee-collections rows, staff-report candidate and move rows, the WhatsApp conversation candidate and the document-bundle duplicate list. Server-written notification, activity and audit text (assignment notices, lead conversion, move back to Lead, candidate deletion, duplicate-file notes) quotes the Care India ID and falls back to the CND reference only when a candidate has none.

`travellerId` (string or null) is returned wherever `careId` is: the desk bootstrap and candidate lists, the candidate profile, the application-form candidate block and, for the Travelled screen, every row of `GET /api/travelled`. The Travelled CSV/XLSX export has a first "Traveller ID" column and no Care India ID or CND reference column. Data Bank search finds a candidate by the CND reference (`CND-01096`), the Care India ID (`CI-096`, `ci-96`) or the bare serial (`96`), and a traveller by `CI-T-007`; the Data Bank export has a "Traveller ID" column after "Care India ID" and no CND reference column.

## Recovery and validation

Upload failure or draft save does not advance the application. A completion error retains the saved draft. Stale versions, changed capacity and failed post-save refreshes require refresh/review before another mutation.

Automated tests cover all twelve feedback-only movements with no attachments, unknown travel dates, negative/repeated medical outcomes, typed supplied details, fresh feedback for reopened attempts, duplicate submits, permissions and deployment capacity. See [backend rules](../../backend/app/domain/stages.py), [application API](../../backend/app/api/routers/applications.py), [stage fields](../../frontend/src/shared/workflow/stages.ts), [the 0048 migration](../../backend/alembic/versions/0048_pcc_stage.py) and its test `test_pcc_stage_migration.py`, and [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx).
