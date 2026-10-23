# Tasks deadlines escalation and daily updates

Tasks tell each recruiter what must happen next for a candidate, when it is due and what has happened since assignment. They connect recruitment actions to the supervisor's attention queue. Source: PRD pages 5, 7, 11 and 12. Requirements: **TASK-01, TASK-02, SLA-01, SLA-02, SLA-03, DAILY-01, DAILY-02, NOTIFY-01**.

## Roles and preparation

Staff work their own tasks. Admin and Manager can inspect team work, add supervisor remarks and reassign candidates with their open tasks. Only Admin can reset a deadline or change escalation reminder settings. Viewing a shared candidate profile does not authorize Staff to modify another recruiter's task.

A candidate, application or medical follow-up must exist before its task exists. Recruitment tasks are created by the system; users do not create them freely or complete them with a checkbox. The workflow worker must run for escalations, historical missing-update checks and notification delivery.

## How tasks enter and leave the list

| Recruitment event | Task created | What finishes it |
| --- | --- | --- |
| New candidate receives an owner | Initial call: confirm interest | A valid confirmed call outcome; an unanswered or unmade call remains open |
| Candidate maps to an order | Stage 1: prescreening | Stage 1 completion |
| Stages 1 through 10 complete | Next stage's task | Completion of that matching stage |
| Medical is unfit and the candidate reapplies | Re-medical follow-up | Recording the next medical result through the medical workflow |
| Stage 11 completes | No next task | Application becomes Deployed |
| Application closes or is unmapped | No next task for that application | Its remaining open tasks are cancelled, not falsely counted as completed |

An Admin reopen cancels obsolete downstream work and creates appropriate current work while retaining history. See the [initial call](12-initial-call.md) and recruitment-stage guides for the actions that finish each task.

## User journey

1. Open **Tasks** from the sidebar, a dashboard card, Staff Directory count or bell notification. Confirm the employee, stage or historical date shown above the list; these links can open a filtered view.
2. Start with **Overdue** and **Due today**, then review **Open**. Initial-call and missing-update views provide more focused queues. Supervisors can choose a staff member and inspect escalated work. **Completed** supports an inclusive completion-date range; that range does not hide current open work.
3. Inspect candidate, order, stage, owner, creation time, due time and time remaining. Open the task to reach its initial-call action or the matching application stage. A stage task must not open an unrelated candidate or order.
4. Carry out the recruitment action and save its outcome in the call or stage workflow. Saving a draft, uploading a document or writing a daily remark does not by itself complete a stage task. A successful stage transition closes the matching task and creates the next one in the same workflow transaction.
5. For every task still open, open **Daily updates**, write what happened today and choose **Add remark**. The stage modal also exposes the task's daily remarks. Examples include “Called; candidate will send the passport tomorrow” or “Waiting for client feedback.”
6. Read the timestamped history to confirm the remark saved. Remarks cannot be edited or deleted. Only a remark by the task owner satisfies that owner's daily-update requirement; a supervisor's note remains useful history but does not substitute for it.
7. As a supervisor, open an escalation notification and inspect the overdue task. Use **Reassign** if another recruiter should take responsibility, select an eligible Staff member and enter a reason. All open tasks belonging to the selected candidate move together; completed work remains unchanged.
8. Only when a new deadline is explicitly justified, Admin chooses **Reset SLA**, enters a reason and confirms. This starts a new 48-hour window and retains the previous escalation and reset history. An ordinary transfer does not reset the clock.
9. Open **Completed** or the candidate history to verify the result. A notification for a task already completed or cancelled explains its current state and offers the relevant completed task or candidate history instead of presenting an actionable open task.

## Deadline and daily-check rules

Every new event task stores its start and a due time 48 hours later. Waiting for a client, embassy, documents or a candidate never pauses that clock. For example, a Monday 10:00 task due Wednesday 10:00 remains due Wednesday 10:00 after reassignment on Tuesday.

The workflow scheduler checks every 15 minutes. A task becomes eligible for escalation only when the current time is strictly later than its deadline. Therefore, crossing the deadline and receiving a bell alert are not guaranteed to happen in the same second. The owner and active supervisors receive stored notifications. Admin's task settings choose one escalation per task or reminders every 24 hours while it remains open; repeat checks do not create the same event twice.

**Not updated today** describes current open tasks in the agency timezone. After an agency day ends, the scheduler records missing owner remarks and sends grouped Admin/Manager notifications. Tasks completed during that day are exempt. After downtime it checks unprocessed days; stored assignment events identify the responsible owner at the historical day boundary. Older transfers without recorded history cannot be reconstructed reliably.

## Screen and backend contract

Entering a task scope reloads current records before showing actionable counts. Loading, failed refresh and retry states distinguish an unavailable service from a genuinely empty queue. A historical missing-update link preserves the original employee/date even when the task has since moved or closed. An inaccessible task link explains that the task is unavailable in the current account's access.

The backend checks role, ownership and open/completed/cancelled state on writes. Remarks must contain nonblank text. Stage transitions, task creation/closure and the related audit entries commit together. Reassignment locks the affected records and preserves due times. Scheduler events use stable uniqueness keys, and notifications and processing checkpoints commit together so a retry does not fabricate extra escalations or missed days.

## Assignment notifications

For a newly created manual, email or WhatsApp candidate, the implemented intake paths notify the assigned owner and active Admin/Manager accounts. The stored bell item links to the candidate. If no recruiter is eligible, supervisors receive the needs-assignment state instead of a notification to an invented owner. Reading the notification does not complete the initial call.

Manual reassignment, account-deactivation/demotion transfers and attendance cover/return notify the new candidate owner and active Admin/Manager accounts. These candidate-linked notifications commit with the ownership change and history. A repeated request that leaves ownership unchanged does not deliver another assignment alert; a rolled-back transaction does not leave a notice for work that never moved. Completed work retained by a leave-cover recruiter does not produce a false return notification.

## Recovery and handoffs

Cancel a remark or reset dialog before saving to leave existing records unchanged. A failed remark save retains the entered text. A successful deadline reset followed by a failed list refresh reports that the reset saved; refresh the list before repeating it. If another user changed ownership or completed the task, refresh and work from the new state instead of forcing the stale action.

Assignments link to [Staff Directory](11-staff-assignment-directory.md), completed actions to the candidate/application history, and persisted escalation/missing-update events to [Reports](17-reports.md). Bell **read** and **read all** affect the current recipient's notification state; they do not complete a task or erase an escalation.

## Acceptance checklist

- [ ] Create a candidate, map an application, complete a stage and repeat a medical attempt; verify one appropriate task per event.
- [ ] Verify stage 11 creates no further task, and unmapping cancels open application tasks.
- [ ] Check a task exactly at its deadline and immediately after it; only the latter is overdue.
- [ ] Run repeated scheduler checks and verify one initial escalation, with 24-hour repeats only when enabled.
- [ ] Transfer an overdue candidate and verify deadlines, remarks and completed work remain unchanged.
- [ ] Reset as Admin with a reason; reject the same request as Manager or Staff.
- [ ] Add an owner remark and clear today's badge; add only a supervisor remark and keep the requirement outstanding.
- [ ] Cross the agency day boundary, including a completed-day task and a later reassignment, and verify historical alerts and report attribution.
- [ ] Open old, completed, cancelled and inaccessible notification links; verify understandable states and permitted actions.
- [ ] Verify owner and active supervisor recipients for new intake, manual/account transfers and leave cover/return; replay a no-op and roll back a transfer to confirm neither produces extra notices.

## Current implementation and remaining acceptance

Task generation, stage-linked closure, daily remarks, recorded history, supervisor transfers, Admin resets and scheduler rules are implemented with isolated workflow tests. Production acceptance still requires the workflow worker to remain running and actual supervisor/owner accounts to receive the expected bell events. The fifteen-minute scan and browser polling mean notifications are not instant push delivery.

Implementation: [Tasks screen](../../frontend/src/screens/tasks/tasks.tsx), [workflow API](../../backend/app/api/routers/workflow.py), [task scheduler](../../backend/app/services/workflow_scheduler.py), [data and reassignment API](../../backend/app/api/routers/data.py).
