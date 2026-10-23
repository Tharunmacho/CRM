# Reference workflow follow-up — 21 October 2026

Reference: [email-automation at 30f7ce6b7402553116bbe6a2b8b2142bc72c8915](https://github.com/Tharunmacho/email-automation/tree/30f7ce6b7402553116bbe6a2b8b2142bc72c8915). The clean local checkout and remote default-branch revision matched during this review. Reference source was read statically; its application, databases and providers were not run. Care India validation uses separate synthetic PostgreSQL databases, local API/browser sessions and mocked external transports.

## Why the omission happened

The earlier integration treated attendance as explicit clocking plus visible browser presence. It did not port the reference's monthly policy, permission approvals, paid leave, weekly-off selection, overtime accounting, duty roster or cover handover. The recruitment PRD did not define those HR rules, and the earlier architecture review excluded HR. Screen and build checks therefore did not establish reference workflow parity. That scope interpretation was too narrow for the user's intended attendance workflow.

The user now explicitly confirmed **“Use the same reference attendance rules.”** Revision `0015_attendance_hr` adds the missing workflow and seven tables; the current schema has 42 application tables. The full policy, role adaptation and deployment requirement are documented in [attendance.md](../attendance.md).

## End-to-end comparison and corrections

| Area | Current path and outcome |
| --- | --- |
| Attendance | Monthly team/own record → request → cover acceptance when required → staged Manager/Admin decision → calendar/calculation → durable worker handover/return → candidate/task audit. Weekly-off, duty-roster, recurring timings and reasoned regularisation are connected to real APIs. Raw clocks and browser presence stay separate. |
| Attendance and allocation | Approved paid/unpaid calendar leave excludes Staff from new intake, manual reassignment and account-change replacement. Manual availability switches retain their independent meaning. Pending requests do not remove Staff from allocation. |
| Candidate identity | Manual/email/WhatsApp share normalized phone OR email OR passport matching. Ambiguous matches require review; manual merges are explicit and fill empty fields without overwriting human corrections or ownership. |
| Candidate ownership | Manual reassignment now requires a reason and writes candidate-level from/to/actor/time history even when all tasks are already closed. Profile History pages the audit from the server. Cover and account-change transfers use the same history view. Legacy changes without events cannot be reconstructed. |
| Order matching | Designation, experience, exact normalized skill ALL/ANY, location, passport and owner filters now query the backend in bounded pages. Recorded/missing requirement evidence comes from the same response. Active-application uniqueness and retained unmapping history protect the save transaction. See the [22 October skill follow-up](candidate-skills-followup.md); this is not a large-directory performance benchmark. |
| Eleven stages | Current typed stage rules, document association, drafts, optimistic versions, repeat medical, closure, reopen and actual deployment are retained. Reporting now derives names from the canonical stage definition: 9 Visa arrival, 10 Visa stamping, 11 Air ticket. |
| SLA and daily updates | Assignment/cover changes preserve task deadlines. The scheduler keeps 48-hour event tasks, historical agency-day ownership, daily updates, repeat-safe escalations and personal reminders. |
| WhatsApp extraction | Fixed automatic retries that reused a confirmed-failed OCR submission. Only confirmed failure creates the next budgeted retry; unknown or active provider jobs retain their identity and checkpoint. |
| Mail intake | Disconnected or historical unsupported accounts can no longer enqueue intake/retry work that their worker will not consume. The UI explains recovery while retaining original receipts, documents and review actions. |
| Zoho mailbox identity | A changed UIDVALIDITY still blocks unsafe UID reuse. Guidance now states that reconnecting alone cannot reconcile a changed mailbox generation. A dedicated generation reconciliation/backfill operation remains unimplemented; no cursor was reset or silently skipped. |
| Screens and shell | All 19 routed screen implementations live under `frontend/src/screens/`. Shared Poppins typography, navigation, dashboard and controls remain in the current local changes. Attendance follows the reference's monthly overview, approvals, employee record and roster layout. |

## Adaptations and limits

- Care India has Admin, Manager and Staff. Admin fills the reference Finance Manager approval stage; Manager cannot approve their own requests. Emergency occasion-limit exceptions remain Admin-only. Cover recipients are Staff because recruitment ownership is Staff-only.
- PostgreSQL transactions and durable workers replace the reference's MongoDB-specific persistence. Reference employee-email exceptions and office-specific holiday data are not imported. Calendar and effective-dated timing controls record explicit changes.
- The recruitment PRD keeps 48-hour initial-call/stage tasks and deployment-based order fill. The reference's current review SLA checks unopened **or** unevaluated candidates, defaults to 48 hours, and adds a separate 72-hour escalation; viewing alone does not clear it. Its evaluation workflow, 24-hour callback reminders and submission-based order fill are not substitutes for the PRD's completed-action tasks and actual deployments. This corrects the earlier view-only SLA description after tracing the repository query.
- Weighted candidate ranking, persistent shortlists and 1–5 recruiter evaluation history exist in the reference but are not part of the implemented PRD matching/call flow. They remain explicit reference-only enhancements, not claimed parity.
- Separate payroll/salary/payslips, reimbursements, B2B enquiries, sourcing-partner modules and reference chatbot menus remain outside this recruitment/attendance integration. Attendance reports unpaid minutes and OT offsets; it does not calculate salaries.
- Real Zoho authentication/delivery, WhatsApp callbacks/media, paid OCR and deployed storage/worker recovery still require live acceptance. SMTP acceptance is not proof of recipient delivery. Validation did not migrate or deploy production; source publication and deployment status are reported separately.

## Validation

The current run and artifact paths are recorded in the [integration audit](../integration-audit.md#reference-workflow-follow-up-validation). Migration tests exercise `0014 → 0015 → 0014 → 0015` and compare retained clocks, presence checkpoints, accounts and candidates. Workflow tests exercise permissions, calculations, leave quota, handover return and historical attribution. Browser evidence uses a separate synthetic database and real local HTTP API; it is not production acceptance.

## Staff Directory compared with the original PRD

Reviewed the current reference `main` through the GitHub connector and confirmed it still matches `30f7ce6b7402553116bbe6a2b8b2142bc72c8915`. This follow-up traces source, not a live reference deployment. The original twelve-page PDF, rather than earlier audit summaries, defines the requirements below.

The reference's **Staff Directory** opens `AdminStaffManagement` (heading **Staff & allocation**). It loads the employee workload matrix and SLA breaches, shows a roster, opens one employee's candidate queue, filters All/Unviewed/Overdue/Evaluated, and lets authorized users open or reassign candidates. Separate Unallocated and Orphaned queues expose work needing an owner. Account actions, a manual SLA scan and orphan recovery also appear there. These are operational workflows, beyond an address book.

| Reference workflow | PRD connection | Care India status at review, before the approved integration below |
| --- | --- | --- |
| Staff identity, contact, active status and account actions | Pages 2 and 6: Admin-only User Management, including roles, activation, availability, password reset, force logout and audit | Connected in `screens/users/users.tsx`, the users router and `services/user_admin.py`. Deactivation transfers candidates/open tasks and revokes sessions. |
| Workload matrix with progress and overdue flags | Pages 2, 5, 11–12: staff filters, performance, escalations and daily-update monitoring | Metrics exist in Dashboard, Tasks and Reports. The User Management directory has a candidate count but no combined operational matrix or employee drill-through. |
| Open an employee's queue and act on its candidates | Pages 2 and 7: supervisors may reassign one or many candidates and their open tasks | Data Bank supports filtering and reasoned bulk reassignment. There is no staff-directory row linking directly to that employee's candidates, tasks, performance and attendance. |
| Overdue queue → candidate → reassignment | Page 11: Admin/Manager can reassign an escalated task from its notification, without resetting the clock | Notification opens `/tasks?task=...`, but Tasks has no reassignment action. The operator must separately find/select the candidate in Data Bank. This required notification-to-action path remains incomplete. |
| Availability used by allocation and reassignment | Page 7: active Staff, auto-assignment enabled, not on leave; later approved attendance-leave extension | Backend excludes manual leave and approved calendar PL/UL. Directory availability and replacement pickers only inspect the manual flag, so approved-leave staff can appear available and be rejected after selection. |
| Automatic assignment and recovery queues | Page 7: strict locked round-robin, duplicates retain ownership, no load balancing | Care India uses its locked rotation. Reference normal assignment also selects by rotation history despite stale least-loaded UI/docstrings; its explicit rebalance/orphan-rehome operations still use least-loaded redistribution and should not be copied as the default. |
| Review progress based on Unviewed/Pending/Judged | Pages 7 and 11: initial call outcome and completed stage actions close tasks | Adapt the useful queue presentation to Initial call pending/Open tasks/Overdue/Missing daily updates/Completed. A reference evaluation verdict is not the PRD's task completion contract. |
| Branch filters, Finance Manager and hard deletion | No corresponding requirement in the original three-role PRD | Keep Admin/Manager/Staff and auditable deactivation. Treat branch operations and reference-specific account permissions as separate product extensions. |

Recommended integration: a supervisor-facing **Staff Directory** for Admin and Manager, with each employee's assigned candidates, initial calls pending, open/overdue/escalated tasks, missing updates, effective leave availability and links to the relevant records. Keep create/edit/deactivate/password/session controls in Admin-only **User Management**. Reuse existing assignment and history services; preserve task deadlines and completed work. The absence of the literal menu name in the PRD does not remove these required operational workflows.

Reference caveats established from code: `GET /staff/workload` can allocate previously unassigned candidates, so it is not a read-only report; staff account mutations use the Staff-page grant, broader than the PRD's Admin-only account policy. The ordinary manual assignment route calls `repository.assign`, which resets the assignment timestamp, viewed flag, evaluation verdict, score and notes. Care India must preserve its existing deadlines, completed work and history during reassignment. A Care India directory should read without changing ownership and separate read/supervision authority from account administration.

Evidence: reference `frontend/src/screens/AdminStaffManagement.tsx`, `app/api/routes.py:2245`, `app/assignment/balancer.py:139`, `app/db/repository.py:1592`, `app/config.py:335`; Care India `frontend/src/screens/users/users.tsx:30`, `frontend/src/screens/tasks/tasks.tsx:299`, `frontend/src/screens/candidates/data-bank.tsx:350`, `backend/app/services/workflow_scheduler.py:75`, `backend/app/services/user_admin.py:77`, `backend/app/services/attendance_availability.py:11`. Findings are source-verified; this review did not run either application, access production data, implement Staff Directory or push to GitHub.

## Approved Staff Directory integration — 22 October 2026

The user approved the recommended workflow. Care India now implements supervisor-only
Staff Directory and employee records, clickable current workload counts, performance
periods, filtered candidate/task/report links and scoped attendance navigation. Effective
approved/manual leave is connected to User Management and assignment selectors. Tasks
now offers reasoned reassignment directly from the notification workflow, preserving
the existing ownership/deadline/history transaction. Account administration remains
Admin-only. Reading the directory never assigns candidates.

The [Staff Directory guide](../staff-directory.md) defines the metrics, role boundaries,
availability distinctions, API contracts and validation evidence. No new migration is
needed for this integration. The earlier comparison table records the pre-integration
gaps; it is not the current feature status.
