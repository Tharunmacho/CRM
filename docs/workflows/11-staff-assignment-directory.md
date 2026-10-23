# Staff Directory assignment and reassignment

This module makes ownership and workload visible without changing completed recruitment work. Source: original PRD page 7 for assignment, plus the user's approved Staff Directory workflow. Requirements: **ASSIGN-01, ASSIGN-02, ASSIGN-03**. Staff Directory is an authorized extension, not a missing literal PDF menu.

## Roles and preparation

Admin and Manager supervise the directory and reassign. Only eligible Staff receive automatic candidates: active, opted into automatic allocation and not on manual or approved leave. Pausing automatic allocation does not by itself forbid a deliberate manual transfer to that active Staff member. Staff cannot open the supervisor directory or change ownership themselves.

## User journey

1. Admin configures Staff accounts and availability. A new candidate advances the locked round-robin pointer to the next eligible person, regardless of workload. Duplicates retain the owner.
2. Open **Staff Directory**. Filter by employee or availability. Review assigned candidates, initial calls pending, open tasks, overdue work, escalations and missing daily updates.
3. Click a count to open the matching candidate/task view for that employee. The count and destination must describe the same backend records.
4. Open **View record** for an employee. Inspect current workload, date-scoped completion metrics, performance and permitted attendance records.
5. Open an overdue candidate/task, inspect current stage, deadline, remarks and ownership history, then choose **Reassign** when appropriate.
6. Select an available active Staff target and enter a reason. Confirm the candidate transfer; all of that candidate's open tasks move together.
7. Verify the new owner and history. Completed stages/tasks, originals, saved drafts, remarks and the original SLA deadline remain intact.
8. When no recruiter was eligible at intake, assign the review candidate. The backend creates the missing initial-call task when appropriate; it does not invent a prior call outcome.

## Screen and backend contract

Directory reads aggregate current work without mutating ownership. Current workload remains current even when the performance date range changes. Completion metrics use the selected agency-date range; they are distinct from Reports' stage-only average.

Availability comes from the backend's staff and approved-leave rules. The reassignment selector refreshes before confirmation and excludes inactive/leave targets. The transaction locks candidate/work, preserves clocks and records actor, old/new owners and reason. A concurrent assignment or changed availability must fail safely rather than partially transfer.

For example, Monday 10:00 assignment with Wednesday 10:00 due remains due Wednesday after a Tuesday transfer. An explicit Admin SLA reset is a separate reasoned action.

## Recovery and handoffs

A failed directory refresh shows retry and must not show old rows as freshly confirmed counts. Changing scope clears selections. Invalid performance dates can reset. A Staff account following a supervisor link gets an access-denied state. Approved leave connects here through [attendance cover](21-attendance-monthly-hr.md); account deactivation uses [User Management](19-user-management.md).

## Acceptance checklist

- [ ] Three eligible Staff receive consecutive new candidates in strict rotation; load does not change the sequence.
- [ ] Manual/approved leave and inactive Staff are excluded from new allocation.
- [ ] Every directory count opens the matching employee records.
- [ ] Reassign several candidates with open and completed work; only ownership/open tasks move.
- [ ] Verify unchanged deadline, saved draft, documents, remarks and completed-stage history.
- [ ] Compare directory, Data Bank, Tasks, Reports and attendance after a transfer.
- [ ] Staff cannot access the directory or reassignment API directly.

## Current implementation and remaining acceptance

The directory, backend aggregate, fresh drill-downs, availability and reasoned transfers are implemented and tested locally. Historical owner attribution before the relevant migrations can be incomplete; the UI must not invent missing history.

Assignment notifications now use consistent recipients across manual, account-deactivation and attendance cover/return transfers: the new owner and active Admin/Manager accounts receive persisted notices in the transfer transaction. Unchanged or replayed ownership produces no duplicates. A repair that moves only an open task receives a task-specific link. See [assignment notification details](16-tasks-sla-daily-updates.md#assignment-notifications).

Implementation: [directory](../../frontend/src/screens/staff/staff-directory.tsx), [employee](../../frontend/src/screens/staff/staff-detail.tsx), [directory service](../../backend/app/services/staff_directory.py). Definitions and evidence: [Staff Directory](../staff-directory.md).
