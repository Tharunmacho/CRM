# Recruitment stages six to eleven and deployment

These stages handle medical, passport custody, advance payment, visa, stamping and travel. Requirements: **STAGE-06, STAGE-07, STAGE-08, DEPLOY-01**. The owner's 2026-11-12 manual-stage instruction supersedes the original PRD's mandatory document gates. Apply the permissions, required feedback, manual confirmation and retained history rules in [stages one to five](14-stages-one-to-five.md).

## Shared behavior

Every stage movement requires written feedback. Details and documents are optional, except that the Medical result (stage 6, like stages 4 and 5) must be chosen: see [decision stages](14-stages-one-to-five.md#decision-stages-4-5-and-6). Elsewhere a move with no outcome details records Completed manually and does not fabricate payment or travel facts. Document completeness is tracked separately. Save feedback alone leaves the stage and deadline unchanged; uploads and OCR never move stages automatically.

Every stage here also has the **stopping exit** described in [stages one to five](14-stages-one-to-five.md#stopping-exit-at-every-stage): Rejected or Declined at stages 6 to 10 (with Passport not available, Not paid, Visa rejected and Visa stamping refused at stages 7 to 10), and only **Not travelling** at stage 11, which closes the application without deploying.

## Stage 6 Medical and repeated examinations

The medical result is a required choice: Fit or Unfit. Optional details include the application status and date. Fit records Fit and advances to stage 7. Unfit records Unfit and stops the candidate: the application closes as medically unfit and the candidate does not go to the next stage. The stage cannot be moved with no result (the API refuses it with a sentence asking for Fit or Unfit).

Unfit may instead plan another medical (*Plan another medical instead of stopping* = Yes). That requires a new medical date, which cannot precede the previous medical date when both are supplied. Confirming this attempt records Unfit: reapplying, retains stage 6 and creates the next follow-up task. Leaving it at No stops the candidate. Every attempt has its own feedback, actor and time. Admin reopening retains these attempts, and is the only way to undo a final Unfit.

## Stage 7 Passport

Optional details include the copy, original-collected answer, collection date and custodian. An existing candidate passport copy may be prefilled. Collected Yes records Collected; otherwise a manual move records Completed manually. Supplied file/user references are validated, but no copy or collection answer is required to move.

## Stage 8 Advance payment

Optional details include collected Yes/No, amount, currency, date, mode, receipt number and file. Collected Yes records Collected; otherwise a manual move records Completed manually. No amount or receipt is required. A supplied amount must be a valid positive monetary value with supported currency. An omitted amount remains unknown and is not invented in financial reports. Superseded attempts stay excluded from effective collection reports.

## Stage 9 Visa arrival and Google review

Optional details include arrived Yes/No, date, visa copy and Google-review status. Arrived Yes records Arrived; otherwise a manual move records Completed manually. None of these details/files is a movement gate. Recruiters can retain the current task by saving feedback without moving.

## Stage 10 Visa stamping

Optional details include done Yes/No, date and stamped-copy upload. Done Yes records Stamped; otherwise a manual move records Completed manually. The copy is optional. Supplied references must belong to the application/stage.

## Stage 11 Air ticket and deployment

Optional details include booked-by, travel date, airline/flight number, PNR and ticket file. A supplied ticket records Booked; feedback-only completion records Completed manually. Completing the final stage manually makes the application and candidate Deployed, closes its task and creates no further recruitment task.

Missing tickets or checklist documents never prevent deployment. An unknown travel date remains unknown in Travelled; neither the completion timestamp nor today's date is substituted. The checklist continues to show missing items after deployment.

The backend still locks and checks order capacity so concurrent candidates cannot claim the same final opening. When deployments reach openings, the order becomes Filled. Admin reopening removes the effective deployment, recalculates count/status, retains previous feedback/files and creates current work. A newer active or deployed placement prevents reopening an older one over it.

## Two Care India ID series

Both public IDs start with `CI` (the agency) and both follow the Indian April-March financial year, but they are different series with different counters. Neither replaces the long-lived `CND-*` reference (`ref`), which stays on every record and in links.

| | Data Bank ID | Traveller ID |
| --- | --- | --- |
| Example | `CI-26-27-001` | `CI-T-26-27-001` |
| Field | `careId` (`crm_candidates.care_id`) | `travellerId` (`crm_candidates.traveller_id`) |
| Numbers | everybody who enters the Data Bank | only people who travelled |
| Year decided by | the day the candidate entered the CRM | the TRAVEL DATE (Asia/Kolkata) |
| Counter | `crm_candidate_fiscal_counters` | `crm_traveller_fiscal_counters` |
| Assigned | at intake (every intake path) | when the placement becomes Deployed |

The serial is zero-padded to three digits and simply grows (`...-1000`) beyond 999. It restarts at 001 on 1 April (India time) of the travel date's year, so a candidate who flies on 31 March 2027 is `CI-T-26-27-nnn` and one who flies on 1 April 2027 is `CI-T-27-28-001`, whoever was recorded first. A placement with no usable travel date on file is numbered by the day its travel stage was completed. Both IDs are unique and immutable once assigned (a database trigger refuses any change). Reversing a deployment (admin reopen) keeps the Traveller ID, and completing the stage again, even with a travel date in another financial year, returns the same number and uses up no new one; a rolled-back transaction consumes no number.

The Traveller ID is assigned explicitly in the same transaction that deploys the placement and under the candidate row lock: the stage-11 `/complete` endpoint, the bulk and historical (travelled) import, and the travelled reconciliation script. Migration 0041 numbered everyone already travelled (an application with status Deployed), ordered by travel date, then by when the candidate entered the CRM. The format lives in one place, [traveller_id.py](../../backend/app/services/traveller_id.py) (Data Bank ID: [care_india_id.py](../../backend/app/services/care_india_id.py)).

`travellerId` (string or null) is returned wherever `careId` is: the desk bootstrap and candidate lists, the candidate profile, the application-form candidate block and, for the Travelled screen, every row of `GET /api/travelled` and a first "Traveller ID" column of its CSV/XLSX export. Data Bank search finds a candidate by either ID, and the Data Bank export has a "Traveller ID" column after "Care India ID".

## Recovery and validation

Upload failure or draft save does not advance the application. A completion error retains the saved draft. Stale versions, changed capacity and failed post-save refreshes require refresh/review before another mutation.

Automated tests cover all eleven feedback-only movements with no attachments, unknown travel dates, negative/repeated medical outcomes, typed supplied details, fresh feedback for reopened attempts, duplicate submits, permissions and deployment capacity. See [backend rules](../../backend/app/domain/stages.py), [application API](../../backend/app/api/routers/applications.py), [stage fields](../../frontend/src/shared/workflow/stages.ts) and [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx).
