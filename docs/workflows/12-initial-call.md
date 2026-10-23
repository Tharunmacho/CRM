# Initial call and interest confirmation

The first task asks the assigned recruiter to contact the candidate and confirm interest. It is separate from the eleven recruitment stages. Source: PRD page 7. Requirement: **ASSIGN-04**, with task creation under ASSIGN-02 and TASK-02.

## Roles and preparation

The owner or a supervisor can record the call. The candidate must exist and have appropriate ownership; a shared Data Bank reader cannot record another recruiter's call simply because they can view the profile. A new assignment normally has a 48-hour initial-call task.

## User journey

1. Open the assignment notification, initial-call task or candidate profile. Inspect identity, phone, source documents and any Needs review warning.
2. Call the candidate. In the call dialog, record **Called: Yes/No**, **Interested: Yes/No/Not reachable**, and meaningful remarks.
3. If no call occurred or the candidate could not be reached, save the appropriate unreachable state. Keep the task visible for further action and continue daily remarks.
4. If the candidate is not interested, record the outcome. The initial-call task closes and the profile enters the Not interested pool; the candidate is retained for other relevant orders.
5. If interested, record the confirmation and review/correct the profile as authorized. Use Jobs / Positions to select a suitable order and match the candidate.
6. Reopen the profile and task list to confirm that the saved outcome, task state and next action agree.

## Screen and backend contract

Called is explicit, not inferred from a remark. **Called: No** cannot be saved as a positive or negative confirmed-interest result; only the unreachable path is valid. The backend validates outcome enums, owner/supervisor permission and application state. A failed save keeps the dialog and remarks available.

Confirming the call before matching is the intended operating sequence. The current application mapping transaction can also supersede an open initial-call task when stage 1 starts. That handoff cancels the outstanding call task; it must not fabricate a Called/Interested outcome or report a call as completed. Do not treat initial-call completion as an unimplemented hard gate on every mapping action.

## Recovery and handoffs

For a response loss, refresh the profile/task before repeating the action. If another user has already mapped the candidate or ownership changed, the dialog must show the conflict rather than overwrite the new workflow. No-interest candidates remain discoverable in [Data Bank](07-data-bank-skills.md). Interested candidates continue to [matching](13-order-matching.md).

## Acceptance checklist

- [ ] Test Called No/Not reachable and verify the open task remains actionable.
- [ ] Reject Called No with Interested Yes/No.
- [ ] Record a real Not interested outcome and verify task closure and pool visibility.
- [ ] Record interest and verify profile outcome persistence.
- [ ] Attempt a call update as unrelated Staff and after an incompatible application change.
- [ ] Map with an outstanding initial-call task; stage 1 exists and no false call outcome appears.
- [ ] Fail a save and verify entered remarks remain available.

## Current implementation and remaining acceptance

Explicit call outcomes, task transitions, validation and owner guards are implemented with isolated workflow tests. The owner should review the wording and operating sequence with actual recruiters; no provider-based telephone-call logging is implied.

Implementation: [candidate profile](../../frontend/src/screens/candidates/candidate-profile.tsx), [workflow/data API](../../backend/app/api/routers/data.py), [mapping transaction](../../backend/app/api/routers/applications.py).
