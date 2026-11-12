# Recruitment stages one to five

These stages establish suitability, forwarding, interview arrangements, interview decisions and offers. Requirements: **STAGE-01, STAGE-02, STAGE-03, STAGE-04, STAGE-05**. The original PRD is supplemented by the owner's manual-stage instruction of 2026-11-12: staff decide when to move, write feedback for every move, and documents never block a stage. The later stages are described in [stages six to eleven](15-stages-six-to-eleven.md).

## Access and candidate form

All eleven stages are available from the stage control next to Recruiter in the candidate application's recruitment card. Selecting a stage opens its saved details and feedback; selection itself does not change the stage. The same stage modal is accessible from orders, profiles, mapped lists and tasks.

Admin/Manager can work any application; Staff can work their assigned candidate's application. An application and active recruiter are required to move a stage. Only the current stage is editable. Completed stages and previous attempts remain readable with actor, time, feedback and optional recorded details. Future stages can be inspected but cannot be completed ahead of the current stage.

## Feedback, saving and manual movement

**Save feedback** retains the current draft without moving the stage or changing its task deadline. **Save and move** asks for confirmation and atomically saves the feedback with the completed stage before creating the next task. Blank or whitespace feedback is refused by the API, including when a caller bypasses the UI. Feedback is trimmed, limited to 10,000 characters and stored in `ApplicationStage.data.feedback`.

Stage details and document uploads are optional. A feedback-only move records **Completed manually**; it must not invent Passed, Selected, Fit, a collection, payment amount or travel date. Explicit positive answers retain their corresponding outcome labels. Explicit negative outcomes still take their recorded closure/retest path, with any necessary follow-up decision. Feedback is required for these movements too.

Uploading documents and saving drafts never advance a stage automatically. Missing documents remain visible in the independent checklist. Supplied file references, dates, monetary amounts and user references are still validated. An unrelated file cannot be attached as evidence. Missing storage bytes do not prevent a manual move.

## Stage 1 Prescreening

Optional result: Passed or Failed, with optional prescreen remarks. Passed records Passed; an omitted result records Completed manually. Failed explicitly closes the application as failed prescreening and preserves the feedback.

## Stage 2 Forwarded to client

Optional details: Forwarded Yes/No and forwarding date. Yes records Forwarded. No requires Candidate not interested or Resume not received and explicitly closes the application with that reason. Leaving the decision blank permits a feedback-only manual move without claiming the CV was forwarded.

## Stage 3 Interview scheduling

Optional details: Scheduled Yes/No, date, time, mode and venue/link. Yes records Scheduled. Otherwise a manual move records Completed manually. No negative closure exists here. Staff may save a draft and retain the existing task while following up.

## Stage 4 Interview result

Optional result: Selected or Rejected, with optional interview remarks. Selected records Selected. Rejected explicitly closes the application as interview rejected. Written transition feedback is always required, so separate interview remarks are not another gate. An omitted result records Completed manually without inventing selection.

## Stage 5 Offer letter

Optional details: Offer obtained Yes/No and the offer file. Yes records Obtained; otherwise a manual move records Completed manually. An absent offer file never blocks progression. Saving a draft alone keeps the offer task and deadline open.

## Transaction, history and recovery

Completion checks ownership, current application state and lock version. Feedback, stage attempt, task completion, application movement and next task are written in one transaction. Duplicate or stale submissions cannot complete the next stage with the same feedback. Ordinary saves do not complete tasks or pause their clocks.

Only Admin may reopen a completed stage, with a new written reason. Reopening preserves original feedback and details in superseded attempt history, cancels obsolete tasks and starts current work again. Previous feedback is removed from the new draft so the next completion needs fresh feedback. An older application cannot reopen over a newer placement. Pending edits must be dealt with before reopening.

If a write committed but the following refresh failed, refresh the saved stage instead of repeating its mutation. Existing version-conflict and permissions protections apply at every entry point.

## Validation

Rule and guarded PostgreSQL tests cover feedback-only movement, optional documents, negative exits, blank-feedback rejection, saves without movement, permissions, retained reopening history and competing transitions. See [application transactions](../../backend/app/api/routers/applications.py), [stage rules](../../backend/app/domain/stages.py), [stage fields](../../frontend/src/shared/workflow/stages.ts) and [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx).
