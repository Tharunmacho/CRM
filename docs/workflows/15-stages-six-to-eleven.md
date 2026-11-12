# Recruitment stages six to eleven and deployment

These stages handle medical, passport custody, advance payment, visa, stamping and travel. Requirements: **STAGE-06, STAGE-07, STAGE-08, DEPLOY-01**. The owner's 2026-11-12 manual-stage instruction supersedes the original PRD's mandatory document gates. Apply the permissions, required feedback, manual confirmation and retained history rules in [stages one to five](14-stages-one-to-five.md).

## Shared behavior

Every stage movement requires written feedback. Details and documents are optional. A move with no outcome details records Completed manually and does not fabricate medical fitness, payment or travel facts. Document completeness is tracked separately. Save feedback alone leaves the stage and deadline unchanged; uploads and OCR never move stages automatically.

## Stage 6 Medical and repeated examinations

Optional details include application status/date and Fit/Unfit. Explicit Fit records Fit and advances. An omitted result records Completed manually on a manual move.

Explicit Unfit requires a reapplication decision. Reapplying Yes requires a new medical date, which cannot precede the previous medical date when both are supplied. Confirming this attempt records Unfit: reapplying, retains stage 6 and creates the next follow-up task. Reapplying No explicitly closes the application as medically unfit. Every attempt has its own feedback, actor and time. Admin reopening retains these attempts.

## Stage 7 Passport

Optional details include the copy, original-collected answer, collection date and custodian. An existing candidate passport copy may be prefilled. Collected Yes records Collected; otherwise a manual move records Completed manually. Supplied file/user references are validated, but no copy or collection answer is required to move.

## Stage 8 Advance payment

Optional details include collected Yes/No, amount, currency, date, mode, receipt number and file. Collected Yes records Collected; otherwise a manual move records Completed manually. No amount or receipt is required. A supplied amount must be a valid positive monetary value with supported currency. An omitted amount remains unknown and is not invented in financial reports. Superseded attempts stay excluded from effective collection reports.

## Stage 9 Visa arrival and Google review

Optional details include arrived Yes/No, date, visa copy and Google-review status. Arrived Yes records Arrived; otherwise a manual move records Completed manually. None of these details/files is a movement gate. Staff can retain the current task by saving feedback without moving.

## Stage 10 Visa stamping

Optional details include done Yes/No, date and stamped-copy upload. Done Yes records Stamped; otherwise a manual move records Completed manually. The copy is optional. Supplied references must belong to the application/stage.

## Stage 11 Air ticket and deployment

Optional details include booked-by, travel date, airline/flight number, PNR and ticket file. A supplied ticket records Booked; feedback-only completion records Completed manually. Completing the final stage manually makes the application and candidate Deployed, closes its task and creates no further recruitment task.

Missing tickets or checklist documents never prevent deployment. An unknown travel date remains unknown in Travelled; neither the completion timestamp nor today's date is substituted. The checklist continues to show missing items after deployment.

The backend still locks and checks order capacity so concurrent candidates cannot claim the same final opening. When deployments reach openings, the order becomes Filled. Admin reopening removes the effective deployment, recalculates count/status, retains previous feedback/files and creates current work. A newer active or deployed placement prevents reopening an older one over it.

## Recovery and validation

Upload failure or draft save does not advance the application. A completion error retains the saved draft. Stale versions, changed capacity and failed post-save refreshes require refresh/review before another mutation.

Automated tests cover all eleven feedback-only movements with no attachments, unknown travel dates, negative/repeated medical outcomes, typed supplied details, fresh feedback for reopened attempts, duplicate submits, permissions and deployment capacity. See [backend rules](../../backend/app/domain/stages.py), [application API](../../backend/app/api/routers/applications.py), [stage fields](../../frontend/src/shared/workflow/stages.ts) and [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx).
