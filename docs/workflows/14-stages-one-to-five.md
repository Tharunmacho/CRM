# Recruitment stages one to five

These stages establish suitability, forwarding, interview arrangements, interview decisions and offers. Requirements: **STAGE-01, STAGE-02, STAGE-03, STAGE-04, STAGE-05**. The original PRD is supplemented by the owner's manual-stage instruction of 2026-11-12: recruiters decide when to move, write feedback for every move, and documents never block a stage. The later stages are described in [stages six to eleven](15-stages-six-to-eleven.md).

## Access and candidate form

All eleven stages are available from the stage control next to Recruiter in the candidate application's recruitment card. Selecting a stage opens its saved details and feedback; selection itself does not change the stage. The same stage modal is accessible from orders, profiles, mapped lists and tasks.

Admin/Manager can work any application; Recruiters can work their assigned candidate's application. An application and active recruiter are required to move a stage. Only the current stage is editable. Completed stages and previous attempts remain readable with actor, time, feedback and optional recorded details. Future stages can be inspected but cannot be completed ahead of the current stage.

## Feedback, saving and manual movement

**Save feedback** retains the current draft without moving the stage or changing its task deadline. **Save and move** asks for confirmation and atomically saves the feedback with the completed stage before creating the next task. Blank or whitespace feedback is refused by the API, including when a caller bypasses the UI. Feedback is trimmed, limited to 10,000 characters and stored in `ApplicationStage.data.feedback`.

Stage details and document uploads are optional, with three exceptions that are decisions rather than details: **Interview result (4), Offer letter (5) and Medical (6)** each need an explicit outcome (see [Decision stages](#decision-stages-4-5-and-6)). On every other stage a feedback-only move records **Completed manually**; it must not invent Passed, a collection, payment amount or travel date. Explicit positive answers retain their corresponding outcome labels. Explicit negative outcomes still take their recorded closure path, with any necessary follow-up decision. Feedback is required for these movements too.

Uploading documents and saving drafts never advance a stage automatically. Missing documents remain visible in the independent checklist. Supplied file references, dates, monetary amounts and user references are still validated. An unrelated file cannot be attached as evidence. Missing storage bytes do not prevent a manual move.

## Stage 1 Prescreening

Optional result: Passed or Failed, with optional prescreen remarks. Passed records Passed; an omitted result records Completed manually. Failed explicitly closes the application as failed prescreening and preserves the feedback.

## Stage 2 Forwarded to client

Optional details: Forwarded Yes/No and forwarding date. Yes records Forwarded. No requires Candidate not interested or Resume not received and explicitly closes the application with that reason. Leaving the decision blank permits a feedback-only manual move without claiming the CV was forwarded.

## Stage 3 Interview scheduling

Optional details: Scheduled Yes/No, date, time, mode and venue/link. Yes records Scheduled. Otherwise a manual move records Completed manually. The stopping exit below is its negative closure (Candidate declined or Interview not possible). Recruiters may save a draft and retain the existing task while following up.

## Stopping exit at every stage

Every stage from 1 to 11 also has a **stopping exit**, so a candidate who is not going ahead can be ended at the stage they have reached instead of being pushed forward. The stage screen shows it as a row of buttons under the feedback box ("Not going ahead with this candidate?"). Choosing one, together with written feedback, closes the application exactly like the older negative exits: the candidate does not move to the next stage, the stage's task is closed and no further task is created (so they leave the recruiter's Follow-up), the order opening is not consumed and the candidate returns to the Data Bank and can be matched to another order. The outcome is stored on the stage record (`stop` in the stage data) and shown in the history, the application form tracking and the reports as the closure reason.

| Stage | Exit choices | Closure reason (label) |
| --- | --- | --- |
| 1 Prescreening | Rejected, Declined | `prescreen_failed` (Failed prescreening), `stage_declined` (Candidate declined) |
| 2 Forwarded to client | Rejected, Declined | `stage_rejected` (Rejected), `stage_declined` |
| 3 Interview scheduling | Rejected, Declined, Interview not possible | `stage_rejected`, `stage_declined`, `interview_not_possible` |
| 4 Interview result | Rejected, Declined | `interview_rejected` (Rejected at interview), `stage_declined` |
| 5 Offer letter | Rejected, Declined | `stage_rejected`, `offer_declined` (Offer declined) |
| 6 Medical | Rejected, Declined | `stage_rejected`, `stage_declined` |
| 7 Passport | Rejected, Declined, Passport not available | `stage_rejected`, `stage_declined`, `passport_not_available` |
| 8 Advance payment | Rejected, Declined, Not paid | `stage_rejected`, `stage_declined`, `advance_not_paid` |
| 9 Visa arrival | Rejected, Declined, Visa rejected | `stage_rejected`, `stage_declined`, `visa_rejected` |
| 10 Visa stamping | Rejected, Declined, Visa stamping refused | `stage_rejected`, `stage_declined`, `visa_stamping_refused` |
| 11 Air ticket | Not travelling | `not_travelling` |

Rules, enforced by the API (`STOP_EXITS` in `backend/app/domain/stages.py`, mirrored in `frontend/src/shared/workflow/stages.ts`; migration `0043_stage_stop_exits` adds the closure reasons):

* Feedback is required (422, missing "Feedback"). A choice that does not belong to the stage is refused with 422.
* An explicit exit wins over the stage's own answers, including an Unfit retest plan. The stage's own buttons (Failed, Not forwarded, Rejected, Declined, Not received, Unfit) keep working as before.
* Who can do it is unchanged: the assigned recruiter on their own candidate, an admin or a manager on any. Only an admin reopens, with a reason; reopening reactivates the application at that stage.
* Stage 11 only has Not travelling (the candidate cancelled or declined before departure). It never deploys, assigns no Traveller ID and needs no free opening; completing stage 11 with the ticket and travel date stays the only path to Deployed / Travelled.
* Tests: `test_stage_stop_exits.py` (every stage 1 to 11) and `stage-rules.test.mjs`.

## Decision stages 4, 5 and 6

Interview result, Offer letter and Medical are decisions. The stage screen asks for the outcome outright (equal choice buttons above the feedback box, not inside the optional details), the recruiter writes feedback, and only the positive answer lets the candidate go to the next stage. Every other answer **stops the candidate at that stage**: the application closes with a reason, the stage task is completed, the candidate goes back to the Data Bank (status New, or Needs review) and can be matched to another order at once. A stopping order never uses up an opening. Choosing a stopping answer shows "This candidate will not go to the next stage", and the confirmation repeats it.

| Stage | Choice | Outcome stored and shown | Result |
| --- | --- | --- | --- |
| 4 Interview result | Selected | Selected | goes to stage 5 |
| | Rejected | Rejected | stops, closure reason `interview_rejected` (Rejected at interview) |
| 5 Offer letter | Agreed | Agreed | goes to stage 6 |
| | Declined | Declined | stops, `offer_declined` (Offer declined) |
| | Not received | Not received | stops, `offer_not_received` (Offer not received) |
| 6 Medical | Fit | Fit | goes to stage 7 |
| | Unfit | Unfit | stops, `medical_unfit` (Medically unfit) |

Rules the API enforces, whatever the screen sends:

* A decision stage cannot be completed without its choice. The request is refused with 422 and a sentence naming the choice ("Choose the interview result, Selected or Rejected, before moving this candidate." and the offer letter and medical equivalents); the stage and its task are untouched.
* Feedback is required with every choice, positive or negative (422, missing "Feedback").
* The choice is stored in the stage data as `result` (`selected`/`rejected`, `agreed`/`declined`/`not_received`, `fit`/`unfit`) and the outcome label on the stage record, so the application form's tracking section, the stage history, Travelled, the candidate activity and the reports all read the same words. Stopping outcomes show as failed rows in the application form.
* Only the assigned recruiter, a manager or an admin can record it; only an admin can reopen a finished stage, with a written reason. Reopening a stopped stage reactivates the application at that stage, keeps the old answer and feedback in the superseded history, and requires a fresh answer and fresh feedback.
* "Not received" stops the candidate rather than holding them: the CRM has no hold state for an application, so the closest existing outcome, a closed application with its own reason, is used. An admin can reopen it if the letter arrives.
* Stage 6 keeps its retest: choosing Unfit and then *Plan another medical instead of stopping* = Yes with a new medical date records "Unfit: reapplying", keeps the candidate on stage 6 and raises a follow-up task. Leaving it at No (the default) stops the candidate. Nothing moves on from stage 6 until a later attempt is Fit.
* The older Offer "Offer letter obtained" Yes/No answer is still accepted on old records and reopened drafts so they load, but it no longer decides anything: only `result` does.

Existing data is not migrated. Applications already past these stages keep their recorded outcomes (including "Completed manually", "Obtained" and the earlier "Unfit" wording); they are not affected because the rule applies when a stage is completed. An application sitting at stage 4, 5 or 6 now needs the choice before it can move. Migration 0042 adds the two new closure reasons.

## Stage 4 Interview result

Required choice: Selected or Rejected (see above), with optional interview remarks. Written transition feedback is always required, so separate interview remarks are not another gate.

## Stage 5 Offer letter

Required choice: Agreed, Declined or Not received (see above), and the optional offer file. An absent offer file never blocks progression. Saving a draft alone keeps the offer task and deadline open.

## Transaction, history and recovery

Completion checks ownership, current application state and lock version. Feedback, stage attempt, task completion, application movement and next task are written in one transaction. Duplicate or stale submissions cannot complete the next stage with the same feedback. Ordinary saves do not complete tasks or pause their clocks.

Only Admin may reopen a completed stage, with a new written reason. Reopening preserves original feedback and details in superseded attempt history, cancels obsolete tasks and starts current work again. Previous feedback is removed from the new draft so the next completion needs fresh feedback. An older application cannot reopen over a newer placement. Pending edits must be dealt with before reopening.

If a write committed but the following refresh failed, refresh the saved stage instead of repeating its mutation. Existing version-conflict and permissions protections apply at every entry point.

## Validation

Rule and guarded PostgreSQL tests (including `test_stage_decisions.py` for every choice of stages 4, 5 and 6) cover feedback-only movement, optional documents, negative exits, blank-feedback rejection, saves without movement, permissions, retained reopening history and competing transitions. See [application transactions](../../backend/app/api/routers/applications.py), [stage rules](../../backend/app/domain/stages.py), [stage fields](../../frontend/src/shared/workflow/stages.ts) and [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx).
