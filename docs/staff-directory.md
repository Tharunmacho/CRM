# Staff Directory

Staff Directory connects recruitment supervision to the existing candidate, task,
reporting and attendance workflows. Admin and Manager can open `/staff-directory`
and `/staff-directory/:id`; Staff cannot access the routes or their APIs. User
Management remains Admin-only.

## Team overview and employee record

The team overview shows current employee identity, automatic-intake availability,
assigned candidates, initial calls pending, open tasks, overdue tasks, escalations
and missing daily updates. Search, employee and availability filters apply to the
visible rows and overview cards. Counts link to the corresponding filtered records.

The employee record contains current workload, attention queues, candidate management,
performance dates and links to Reports and Attendance. Attendance links select the
employee and the month containing the performance end date, subject to existing
attendance permissions. Admin accounts do not have an employee attendance link;
Managers can open their own attendance or a Staff employee's attendance.

The white header, summary cards, neutral surfaces and responsive employee cards follow
the Attendance layout. Routed screen implementations live in `frontend/src/screens/staff/`.

## Count definitions

| Field | Definition |
| --- | --- |
| Assigned candidates | Candidates currently owned by the employee, across candidate statuses. |
| Initial calls pending | Open, uncancelled initial-call tasks currently owned by the employee. |
| Open tasks | Tasks with no completion or cancellation timestamp. |
| Overdue tasks | Open tasks whose stored deadline has passed; legacy tasks fall back to the SLA start/creation plus 48 hours. |
| Escalated tasks | Open tasks carrying an escalation flag. Overdue tasks may await the existing scheduler before becoming escalated. |
| Missing updates | Open tasks without a remark by their current owner on today's agency calendar date. Another employee's remark does not satisfy this count. |
| Completed tasks | Uncancelled tasks completed within the selected inclusive agency-date period, attributed to the recorded task owner. |
| Average task completion time | Mean elapsed hours from task creation to completion for completed tasks in that period, including initial calls. This differs from Reports' stage-only duration metric. |

Performance dates never hide current overdue work or change current ownership counts.
The default performance period is the start of the agency month through today.
During a period change, previous-period completion metrics are hidden while the new
period loads. Visible directories refresh every 30 seconds and on window focus;
failed refreshes identify retained records as potentially out of date.
Task and candidate drilldowns reload their shared records on entry and employee-scope
changes before showing counts or assignment actions. This keeps list summaries aligned
with the directory after another supervisor changes ownership.

## Effective availability

Availability is derived from current account flags and today's authoritative attendance
calendar. Pending requests and browser presence do not change assignment eligibility.

| State | Automatic intake | Manual reassignment / account replacement |
| --- | --- | --- |
| Active Staff, auto-assignment enabled, no leave | Yes | Yes |
| Active Staff, auto-assignment paused, no leave | No | Yes |
| Approved paid/unpaid leave today | No | No |
| Manual on-leave flag | No | No |
| Inactive account | No | No |
| Admin or Manager | No | No |

Approved leave is shown independently of the manual leave switch. Clearing manual
leave does not cancel an approved attendance leave entry. Reassignment and account
replacement dialogs fetch fresh availability; the server rechecks eligibility on save.

## Reassignment

Admin and Manager can reassign from an open task, including a task opened from an
escalation notification, or select one/many candidates in Data Bank. The shared dialog
requires a recipient and reason, explains that all open candidate tasks move, and
excludes unavailable recipients. A failed availability request cannot fall back to
cached manual flags.

The existing transaction preserves task deadlines, SLA starts, escalation flags,
completed/cancelled task ownership, initial-call outcomes, application stages, drafts,
documents and remarks. Candidate ownership history and open-task ownership events
record the transfer. Explicit Admin SLA reset remains separate. No hard deletion,
automatic workload rebalance or reference evaluation reset is introduced.

## API and navigation

- `GET /api/staff-directory?from=YYYY-MM-DD&to=YYYY-MM-DD`: supervisor-only, one
  aggregate database statement for public employee identity, availability and counts.
- `GET /api/staff-directory/availability`: supervisor-only availability for selectors.
- Both endpoints use `Cache-Control: private, no-store`; reading never assigns candidates.
- `/data-bank?staff=ID` applies an exact owner filter to the server list and export.
- `/tasks?staff=ID&view=open|initial|overdue|escalated|missing|completed` selects a
  workload queue. `from`/`to` apply only to completed tasks.
- `/reports?staff=ID&from=YYYY-MM-DD&to=YYYY-MM-DD` opens Staff performance.
- `/attendance?staff=ID&month=YYYY-MM&view=employee` opens the monthly employee record.

This feature adds no database tables, columns or Alembic revision. It uses the existing
attendance schema through `0015_attendance_hr`; matching frontend and backend code
must be deployed together.

## Validation

Backend verification uses an explicitly guarded, disposable local PostgreSQL database.
The focused suite covers API authorization, read-only queries, count and date boundaries,
leave eligibility, candidate filter/export, account replacement and reassignment retention.
Browser acceptance uses separate synthetic staff/candidate/attendance records; it does
not access production or external email/OCR providers. Evidence is recorded under
`artifacts/staff-directory/` and `artifacts/staff-directory-backend.xml`.

Verified on 22 October 2026:

- 58 backend tests passed across Staff Directory, workflow drilldown, User Management
  and candidate workflows, using disposable PostgreSQL records.
- All 74 frontend tests passed, including workload filtering, availability, completion
  periods, completed-task notification context and the exact SLA deadline boundary.
- TypeScript checking and the Vite production build passed.
- Browser acceptance passed against the real isolated API: all five workload queues
  matched backend counts; candidate, attendance and report links retained employee scope;
  Manager reassignment preserved deadlines, completed work, drafts, documents and remarks;
  Staff routes and API access were denied.
- Nine directory/detail/dialog layout checks passed, including widths 360, 768, 1024,
  1440 and 1918 pixels, with no horizontal page overflow.
- Four recovery checks passed: invalid performance dates, account-replacement eligibility,
  completed-task notifications and unknown employee navigation.
- A separate ownership-freshness browser check passed: after reassignment outside the
  current screen, the refreshed directory count, Data Bank summary, selected employee
  and candidate rows all agreed. Its desktop layout check also passed.
