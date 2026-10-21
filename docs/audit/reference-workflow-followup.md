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
| Order matching | Existing designation, experience, exact normalized skill ALL/ANY, location, passport and owner filters feed the application transaction. Active-application uniqueness and retained unmapping history protect the workflow. Matching still uses bootstrap records and is not a large-directory scale acceptance result. |
| Eleven stages | Current typed stage rules, document association, drafts, optimistic versions, repeat medical, closure, reopen and actual deployment are retained. Reporting now derives names from the canonical stage definition: 9 Visa arrival, 10 Visa stamping, 11 Air ticket. |
| SLA and daily updates | Assignment/cover changes preserve task deadlines. The scheduler keeps 48-hour event tasks, historical agency-day ownership, daily updates, repeat-safe escalations and personal reminders. |
| WhatsApp extraction | Fixed automatic retries that reused a confirmed-failed OCR submission. Only confirmed failure creates the next budgeted retry; unknown or active provider jobs retain their identity and checkpoint. |
| Mail intake | Disconnected or historical unsupported accounts can no longer enqueue intake/retry work that their worker will not consume. The UI explains recovery while retaining original receipts, documents and review actions. |
| Zoho mailbox identity | A changed UIDVALIDITY still blocks unsafe UID reuse. Guidance now states that reconnecting alone cannot reconcile a changed mailbox generation. A dedicated generation reconciliation/backfill operation remains unimplemented; no cursor was reset or silently skipped. |
| Screens and shell | All 19 routed screen implementations live under `frontend/src/screens/`. Shared Poppins typography, navigation, dashboard and controls remain in the current local changes. Attendance follows the reference's monthly overview, approvals, employee record and roster layout. |

## Adaptations and limits

- Care India has Admin, Manager and Staff. Admin fills the reference Finance Manager approval stage; Manager cannot approve their own requests. Emergency occasion-limit exceptions remain Admin-only. Cover recipients are Staff because recruitment ownership is Staff-only.
- PostgreSQL transactions and durable workers replace the reference's MongoDB-specific persistence. Reference employee-email exceptions and office-specific holiday data are not imported. Calendar and effective-dated timing controls record explicit changes.
- The recruitment PRD keeps 48-hour initial-call tasks and deployment-based order fill. Reference behavior that stops review SLA on merely viewing a profile, creates 24-hour callbacks or fills orders from submitted counts is not substituted.
- Weighted candidate ranking, persistent shortlists and 1–5 recruiter evaluation history exist in the reference but are not part of the implemented PRD matching/call flow. They remain explicit reference-only enhancements, not claimed parity.
- Separate payroll/salary/payslips, reimbursements, B2B enquiries, sourcing-partner modules and reference chatbot menus remain outside this recruitment/attendance integration. Attendance reports unpaid minutes and OT offsets; it does not calculate salaries.
- Real Zoho authentication/delivery, WhatsApp callbacks/media, paid OCR and deployed storage/worker recovery still require live acceptance. SMTP acceptance is not proof of recipient delivery. Validation did not migrate or deploy production; source publication and deployment status are reported separately.

## Validation

The current run and artifact paths are recorded in the [integration audit](../integration-audit.md#reference-workflow-follow-up-validation). Migration tests exercise `0014 → 0015 → 0014 → 0015` and compare retained clocks, presence checkpoints, accounts and candidates. Workflow tests exercise permissions, calculations, leave quota, handover return and historical attribution. Browser evidence uses a separate synthetic database and real local HTTP API; it is not production acceptance.
