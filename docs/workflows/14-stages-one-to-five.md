# Recruitment stages one to five

These stages establish suitability, employer forwarding, interview arrangements, the interview decision and the offer. Source: PRD pages 7–9. Requirements: **STAGE-01, STAGE-02, STAGE-03, STAGE-04, STAGE-05**. The next six stages are in [stages six to eleven](15-stages-six-to-eleven.md).

## Access and stage modal

Open an existing application from its order, candidate profile, mapped list or task. Admin/Manager can work any application; Staff can work their own assigned candidate's application. A candidate without an application has no editable stage workflow.

The modal checks the current application before editing. The header identifies candidate name/photo, phone, passport, employer and client contact, order, designation, owner, current stage and overall status. The progress strip identifies the current step among eleven stages. Completed sections remain above, collapsed/read-only with actor and completion time. Only the current stage is editable; future stage forms stay hidden. Historical attempts invalidated by reopening remain readable in retained history.

Use **Save** to retain a draft or waiting answer. **Before completing this stage** lists the existing completion requirements before a failed submission is necessary. Once those fields are ready, the panel explains the next stage, closure or medical repeat. The task shows its actual due date. Use **Complete stage** only when its completion conditions are satisfied. A destructive outcome changes the button to **Complete and close** with the reason. Uploads must finish before completion. The modal shows missing fields, unsaved changes, save progress and version/conflict errors. Completion/reopening require an active owner; authorized users can still preserve draft details while assignment is corrected.

## Stage 1 Prescreening

1. Inspect the candidate's profile and documents against the order.
2. Select **Passed** or **Failed** and record relevant prescreen remarks.
3. Passed + completion closes the prescreen task, records the result and creates stage 2's task.
4. Failed + explicit close completes this decision and closes the application as failed prescreening. The candidate returns to the shared Data Bank for other suitable orders.

**Completion:** a selected result. Prescreen remarks are recorded but are not an extra mandatory completion field in the PDF. No result means the stage remains incomplete. This is a negative-exit stage; failure must not silently advance. Admin reopening stage 1 invalidates its former result and later completions while retaining their history.

## Stage 2 Forwarded to client

1. Record **Forwarded: Yes/No** and the forwarding date when known.
2. Yes + completion advances to interview scheduling. The PDF's completion condition is Yes; the date is recorded and validated when supplied, not an additional hidden blocker.
3. For No, select **Candidate not interested** or **Resume not received**.
4. Confirm the close. Both reasons close/unmap the application and put the candidate in the Not interested pool, preserving the exact reason for later review.

**Waiting and correction:** leave an incomplete decision in Save rather than inventing Yes. No without a reason cannot close. The No choice is an explicit exit, not a pause. Reopening stage 2 requires Admin reason and invalidates later results/tasks; the previous forwarding decision remains in history.

## Stage 3 Interview scheduling

1. Record whether the interview is scheduled.
2. If Yes, enter the date; record time, in-person/online mode and venue/link as available.
3. Complete when Scheduled is Yes and the date is valid. The next task is to record the interview result.
4. If not scheduled, Save No and add the owner's daily task remark explaining the follow-up.

**Completion:** Yes plus date. Supplied time/mode/venue are validated; they do not add unapproved progression blockers. No keeps stage 3 and its existing task/deadline open. There is no negative close at stage 3. An Admin correction reopens this stage and removes later completions from the effective timeline without deleting evidence.

## Stage 4 Interview result

1. Obtain the interview outcome and select **Selected** or **Rejected**.
2. Enter nonblank interview remarks explaining the result.
3. Selected + remarks advances to the offer letter task.
4. Rejected + remarks explicitly closes the application as interview rejected and returns the candidate to the Data Bank.

**Completion:** result and remarks are both required. Blank/whitespace remarks cannot pass validation. A draft can wait for the decision but cannot create an invented selection. Reopening stage 4 invalidates the selected/rejected result and any later work's effective completion; files and past remarks remain preserved.

## Stage 5 Offer letter

1. Record **Offer letter obtained: Yes/No**.
2. Upload the genuine offer letter to this application's offer field. Inspect the original/version if replacing an earlier file.
3. Yes plus the saved upload completes stage 5 and creates the medical task.
4. If the letter is not obtained, Save No and continue daily follow-up remarks.

**Completion:** Yes and an actual associated file. A filename or fabricated attachment reference is insufficient. No keeps the same stage/task and does not reset the SLA. There is no negative exit at stage 5. Admin reopen preserves offer versions and invalidates later progression.

## Shared backend transaction and recovery

Completion validates typed values, file associations, ownership and the current application version. It records stage history, closes the relevant task, changes application state and creates the next task in one transaction. A negative close cancels remaining open application work. Waiting saves neither complete the task nor pause/reset its 48-hour clock.

If two tabs edit the same application, a conflict preserves the unsaved values for review; reload current state before resubmitting. If the write committed but refresh failed, retry the refresh. Do not upload another version or repeat completion just to repair the display.

Only Admin may reopen a completed stage, with a mandatory reason. Reopening cancels obsolete open tasks and creates current work again; it cannot revive an old application over a newer mapping. If the current stage has unsaved edits, the Admin must confirm discarding them before reopening an earlier stage. Closing and competing stage actions stay disabled while reopening saves. Manager/Staff cannot bypass this with an ordinary edit. A saved draft followed by a failed read offers **Refresh saved stage** rather than repeating the write.

## Acceptance checklist

- [ ] Verify header context and completed/current/future section behavior for all three roles.
- [ ] Complete the positive path through stages 1–5 and check each task handoff.
- [ ] Test failed prescreen, both stage-2 reasons and rejected interview independently.
- [ ] Save waiting answers in stages 3/5 and confirm the deadline does not move.
- [ ] Reject stage 4 without remarks and stage 5 without a genuine application file.
- [ ] Save an incomplete draft, reload and recover its fields.
- [ ] Reopen each completed stage as Admin; reason, invalidated downstream history and new current task are correct.
- [ ] Race completion/unmap or two completions; only one current transition succeeds.
- [ ] Edit the current stage, start reopening a prior stage and cancel the discard confirmation; unsaved fields remain unchanged.
- [ ] Fail the refresh after a successful draft save and restore the view through **Refresh saved stage**.

## Current implementation and remaining acceptance

The modal, typed completion rules, waiting/negative branches, immutable history and concurrency guards are implemented with rule/database tests. Owner walkthrough of representative documents and all eleven visible stages remains a release acceptance checklist, not implied by testing only the happy path.

Implementation: [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx), [field definitions](../../frontend/src/shared/workflow/stages.ts), [backend stage rules](../../backend/app/domain/stages.py), [application transactions](../../backend/app/api/routers/applications.py). Daily work: [Tasks and SLA](16-tasks-sla-daily-updates.md).
