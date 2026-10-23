# Care India CRM module workflow guide

Use this guide to review and accept the CRM one module at a time, from account setup and candidate intake through recruitment, staff supervision and deployment. Each module explains the user actions, required screens, backend rules, failure recovery and practical checks. The guide covers **all 56 original requirement IDs across all 12 PRD pages**, the **10 later mail and attendance requirements**, and the approved Staff Directory and skill-segregation extensions.

The [original PRD](../requirements/recruitment-crm-developer-specification.pdf) defines the recruitment requirements. The [requirements matrix](../requirements-matrix.md) retains their IDs and distinguishes later decisions. The reference CRM at [commit 30f7ce6](https://github.com/Tharunmacho/email-automation/tree/30f7ce6b7402553116bbe6a2b8b2142bc72c8915) informs approved interaction patterns; its separate recruitment stages, inferred industry scores, finance roles and payroll are not automatically Care India requirements.

The 22 October implementation pass connects the module handoffs and improves API reads, loading/retry states and mobile screens. Review the [current integration evidence](../integration-audit.md) and [measured performance](../performance.md) alongside each guide. Attendance provides the approved UI example; reference features are adapted only where they fit the Care India workflow.

## How to finish one module at a time

1. Choose a module below and read its purpose, roles and preparation. Set up its prerequisite records first.
2. Follow the numbered journey in the actual CRM using representative test records. Read the expected frontend and backend behavior together.
3. Run the acceptance checklist as the relevant roles, including failure, refresh and retry paths. Confirm the result on the next screen, not only the success message.
4. Record any difference as a specific example: account/role, action, expected result and actual result. Separate a code defect from a product decision or missing live-service configuration.
5. Mark the module accepted only after its required local and live checks are complete. Move to the next dependent module.

The unchecked boxes are a review checklist, **not a claim that all listed behavior is missing**. “Implemented” means the source path is connected. “Isolated validation” means named synthetic cases passed. “Live pending” requires configured providers or the deployed environment. No guide entry itself approves a production deployment or changes the database.

## Module index

| Module | Review the full journey | Current boundary |
| --- | --- | --- |
| 01 [Access navigation and audit](01-access-navigation-audit.md) | Sign in → permitted navigation/actions → audit → logout/revocation | Connected; deployed session behavior and extra navigation need review |
| 02 [Dashboard](02-dashboard.md) | Today/workload → filtered tasks/pipeline/orders → source recovery | Connected; live source health needs acceptance |
| 03 [Clients and companies](03-clients-companies.md) | Contact → employer → agreement versions → orders/candidates | Connected; contact/company hierarchy differs from PDF terminology |
| 04 [Designations and orders](04-designations-orders.md) | Job requirements → employer order → hold/close/capacity rules | Connected; commercial examples and capacity need owner review |
| 05 [Candidate entry and profile](05-candidate-entry-profile.md) | Manual/OCR entry → duplicate decision → saved recovery → profile correction | Connected; latest entry/recovery follow-up validated locally |
| 06 [Documents and OCR](06-documents-ocr.md) | Preserve original → extract/review → attach/version → download/recover | Connected; real OCR/conversion/storage restore pending |
| 07 [Data Bank and skills](07-data-bank-skills.md) | Shared directory → skill groups/missing evidence → exact filters/export | Connected; server facets and browser flow validated locally |
| 08 [Zoho Inbox](08-zoho-inbox.md) | Connect → receive/read → compose/reply/draft → reconcile/import | Connected common flows; real mailbox/provider acceptance pending |
| 09 [WhatsApp](09-whatsapp.md) | Connect → receive/chat → review/split → save → optional acknowledgement → delivery/recovery | Connected screens and durable outbox; real delivery pending |
| 10 [Automated intake](10-automated-intake.md) | Receive → extract → deduplicate → save/assign → retry/review | Connected; multi-provider live/restart acceptance pending |
| 11 [Staff Directory and assignment](11-staff-assignment-directory.md) | Eligibility/rotation → workload drill-down → reasoned transfer/history | Connected approved extension; legacy history remains bounded |
| 12 [Initial call](12-initial-call.md) | Contact → Called/Interested outcome → task/pool/next action | Connected; operating sequence needs recruiter review |
| 13 [Order matching](13-order-matching.md) | Backend search/evidence → application → unmap → fresh remap | Connected; bounded matching validated, scale benchmark separate |
| 14 [Stages one to five](14-stages-one-to-five.md) | Prescreen → forward → schedule → result → offer, including exits/reopen | Connected; review every branch with representative records |
| 15 [Stages six to eleven](15-stages-six-to-eleven.md) | Repeated medical → passport → advance → visa → stamping → deployment | Connected; document/capacity/reopen acceptance required |
| 16 [Tasks SLA and daily updates](16-tasks-sla-daily-updates.md) | Event task → daily remark → overdue/escalation → transfer/reset/closure | Connected; deployed scheduler timing needs acceptance |
| 17 [Reports](17-reports.md) | Six reports → shared filters/role scope → workbook/PDF | Connected; legacy history and volume limits disclosed |
| 18 [Personal to-dos](18-personal-todos.md) | Private item → due reminder → complete/reopen/delete | Connected; deployed reminder delay needs acceptance |
| 19 [User Management](19-user-management.md) | Create → eligibility → password/session → atomic deactivation/transfer | Connected; live role/account acceptance required |
| 20 [Attendance clock and activity](20-attendance-clock-activity.md) | Intentional clock → online/idle/away intervals → corrections/retry | Connected; Staff activity is approximate, Manager activity not tracked |
| 21 [Monthly attendance and HR](21-attendance-monthly-hr.md) | Policy → requests/approvals → roster/weekly off → leave cover/return | Connected adopted reference rules; no payroll module |
| 22 [Deployment and operations](22-deployment-operations.md) | Configure → coordinated migration/release → workers → restore/acceptance | Operational checks; no deployment is implied by this guide |

## Recommended review order

Start with access and users (**01, 19**), then employer/job setup (**03, 04**). Review manual candidate entry and documents (**05, 06**) before connecting real sources (**08, 09, 10**). Check assignment/calls (**11, 12**) before Data Bank, matching and all stages (**07, 13, 14, 15**).

Review task deadlines and daily updates (**16**) across those same candidate records, then cross-check Dashboard and Reports (**02, 17**). Review private to-dos (**18**) separately from recruitment tasks. Complete clock/activity and monthly attendance (**20, 21**) before accepting leave-driven assignment behavior. Finish with deployment/restore and real provider acceptance (**22**).

## Product decisions and explicit limits

- **Employer structure:** the current contact → employer company hierarchy and extra navigation are retained. Changing to literal employer-as-Client is a separate data-model decision.
- **Mail connection:** Zoho-only setup is the user's explicit replacement for the PDF's Gmail/Microsoft connection choices. Legacy non-Zoho evidence stays readable.
- **Reference features:** the user adopted attendance rules and Staff Directory workflow. This does not import payroll, payslips, Sourcing Hub, B2B enquiries, custom finance roles or the reference's shorter candidate pipeline.
- **Skill matching:** recorded skills, ALL/ANY filters and missing evidence are used. No inferred industry, alias-driven suitability percentage or invented experience is accepted as fact.
- **Assignment notifications:** new intake and manual/account/leave-cover transfers notify the new owner and active supervisors in the same transaction, with no duplicates on unchanged or repeated ownership. Task-only repairs link to their task. See [module 16](16-tasks-sla-daily-updates.md#assignment-notifications).
- **Intake:** opt-in acknowledgements and reviewed person/page splitting are implemented. Acknowledgements default off; ambiguous groups require confirmation. See [communication and split intake](../communications-and-split-intake.md).
- **Mail scope:** composition is plain text. Permanent purge and folder rename/delete are not implemented. Stored-history attachment search is not a search of every unopened remote message; uncertain/partial sending needs reconciliation.
- **Attendance:** intentional clocking and monthly duty calculations are separate from browser activity. Away/no signal can include hidden tabs, network loss or work outside the CRM. Manager browser activity is not tracked. Presence is not productivity or a salary calculation.
- **Historical data and scale:** reliable new assignment/escalation/daily events do not reconstruct missing pre-migration history. Server Data Bank/matching do not imply every global picker or report is proven at large volume.
- **Live acceptance:** real Zoho sending/receiving, WhatsApp delivery, paid OCR, production storage restore and deployed worker timing still require configured acceptance. Synthetic checks do not certify those services.

## Original PRD coverage

Every original requirement ID appears once below with a module and section. Related modules repeat the operational details where a handoff needs them.

| ID | Required outcome | Module and section |
| --- | --- | --- |
| NAV-01 | Three roles and permitted actions | [01 Roles and access](01-access-navigation-audit.md#roles-and-access) |
| NAV-02 | Navigation and role-specific menus | [01 User journey](01-access-navigation-audit.md#user-journey) |
| AUD-01 | Actor/time audit for mutations | [01 Screen and backend contract](01-access-navigation-audit.md#screen-and-backend-contract) |
| DASH-01 | Personal/team today and task metrics | [02 User journey](02-dashboard.md#user-journey) |
| DASH-02 | Eleven-stage/order/task drill-downs | [02 Screen and backend contract](02-dashboard.md#screen-and-backend-contract) |
| DASH-03 | Admin source health and failures | [02 Recovery and handoffs](02-dashboard.md#recovery-and-handoffs) |
| INBOX-01 | Account connection controls, superseded by Zoho instruction | [08 Roles and preparation](08-zoho-inbox.md#roles-and-preparation) |
| INBOX-02 | Mail search/filter/preview and same-account reply | [08 User journey](08-zoho-inbox.md#user-journey) |
| INBOX-03 | Auto-ingest, manual attachment intake and candidate link | [08 User journey](08-zoho-inbox.md#user-journey) |
| CLIENT-01 | Employer/contact fields, agreement and status | [03 User journey](03-clients-companies.md#user-journey) |
| CLIENT-02 | Employer orders and candidates | [03 Screen and backend contract](03-clients-companies.md#screen-and-backend-contract) |
| JOB-01 | Designations, requirements and order terms | [04 User journey](04-designations-orders.md#user-journey) |
| MAP-01 | Filter candidates and create an application | [13 User journey](13-order-matching.md#user-journey) |
| MAP-02 | One active order, unmap and fresh remap | [13 Screen and backend contract](13-order-matching.md#screen-and-backend-contract) |
| MAP-03 | Application-only stage access and order candidate context | [13 Roles and preparation](13-order-matching.md#roles-and-preparation) |
| CAND-01 | Complete manual entry, documents and work/source fields | [05 User journey](05-candidate-entry-profile.md#user-journey) |
| CAND-02 | Phone/email/passport duplicate, Merge or Cancel | [05 Screen and backend contract](05-candidate-entry-profile.md#screen-and-backend-contract) |
| BANK-01 | Shared directory, column/resume search and Not interested pool | [07 User journey](07-data-bank-skills.md#user-journey) |
| BANK-02 | Admin-only filtered Excel export | [07 Screen and backend contract](07-data-bank-skills.md#screen-and-backend-contract) |
| TASK-01 | Task tabs, scope and due/context fields | [16 User journey](16-tasks-sla-daily-updates.md#user-journey) |
| TASK-02 | System-generated tasks closed by workflow actions | [16 How tasks enter and leave the list](16-tasks-sla-daily-updates.md#how-tasks-enter-and-leave-the-list) |
| TODO-01 | Private Admin/Manager to-dos and record links | [18 User journey](18-personal-todos.md#user-journey) |
| TODO-02 | Date tabs and private due reminder | [18 Day buckets and reminder timing](18-personal-todos.md#day-buckets-and-reminder-timing) |
| REPORT-01 | All six report types | [17 What each report means](17-reports.md#what-each-report-means) |
| REPORT-02 | Shared five filters and Staff scope | [17 Screen and backend contract](17-reports.md#screen-and-backend-contract) |
| REPORT-03 | Real Excel and PDF exports | [17 User journey](17-reports.md#user-journey) |
| USER-01 | Admin create/edit/deactivate | [19 Add and edit](19-user-management.md#add-and-edit-an-account), [19 Deactivate](19-user-management.md#deactivate-or-remove-a-staff-role) |
| USER-02 | Eligibility/leave and transfer on deactivation | [19 Screen and backend contract](19-user-management.md#screen-and-backend-contract) |
| USER-03 | Password reset, forced logout and user audit | [19 Password sessions and history](19-user-management.md#password-reset-session-sign-out-and-audit-history) |
| INGEST-01 | Queued receiving separate from extraction | [10 Roles and preparation](10-automated-intake.md#roles-and-preparation) |
| INGEST-02 | Formats, original storage, provenance and hash reuse | [06 Screen and backend contract](06-documents-ocr.md#screen-and-backend-contract) |
| INGEST-03 | Veriis field extraction and document classification | [06 User journey](06-documents-ocr.md#user-journey) |
| INGEST-04 | Empty-only duplicate enrichment, retained owner | [10 User journey](10-automated-intake.md#user-journey) |
| INGEST-05 | Shared database, New/Needs review handling | [10 User journey](10-automated-intake.md#user-journey) |
| INGEST-06 | Three retries, failed list and Admin retry | [10 Retry and review states](10-automated-intake.md#retry-and-review-states) |
| INGEST-07 | Optional same-channel acknowledgement | [10 Current implementation and remaining acceptance](10-automated-intake.md#current-implementation-and-remaining-acceptance) |
| ASSIGN-01 | Locked strict eligible-Staff rotation | [11 Roles and preparation](11-staff-assignment-directory.md#roles-and-preparation) |
| ASSIGN-02 | Immediate new assignment and initial task; duplicates retain owner | [11 User journey](11-staff-assignment-directory.md#user-journey) |
| ASSIGN-03 | Supervisor bulk transfer, open tasks and history | [11 Screen and backend contract](11-staff-assignment-directory.md#screen-and-backend-contract) |
| ASSIGN-04 | Called/Interested/remarks and Not interested pool | [12 User journey](12-initial-call.md#user-journey) |
| NOTIFY-01 | Persisted assignment notifications and links | [16 Assignment notifications](16-tasks-sla-daily-updates.md#assignment-notifications) |
| STAGE-01 | Sequential application modal/header/history | [14 Access and stage modal](14-stages-one-to-five.md#access-and-stage-modal) |
| STAGE-02 | Admin reason-required completed-stage reopen | [14 Shared backend transaction and recovery](14-stages-one-to-five.md#shared-backend-transaction-and-recovery) |
| STAGE-03 | Atomic stage/history/task handoff | [14 Shared backend transaction and recovery](14-stages-one-to-five.md#shared-backend-transaction-and-recovery) |
| STAGE-04 | Only stages 1/2/4/6 close negatively | [14 Stages and shared recovery](14-stages-one-to-five.md#shared-backend-transaction-and-recovery), [15 Medical](15-stages-six-to-eleven.md#stage-6-medical-and-repeated-examinations) |
| FILE-01 | Immutable upload versions | [06 Screen and backend contract](06-documents-ocr.md#screen-and-backend-contract) |
| STAGE-05 | Exact stages 1–5 fields and completion/exit rules | [14 Stage 1 onward](14-stages-one-to-five.md#stage-1-prescreening) |
| STAGE-06 | Repeated medical and Fit/Unfit branches | [15 Stage 6](15-stages-six-to-eleven.md#stage-6-medical-and-repeated-examinations) |
| STAGE-07 | Passport custody and variable advance/receipt | [15 Stage 7](15-stages-six-to-eleven.md#stage-7-passport), [15 Stage 8](15-stages-six-to-eleven.md#stage-8-advance-payment) |
| STAGE-08 | Visa, review, stamping and ticket fields | [15 Stage 9 onward](15-stages-six-to-eleven.md#stage-9-visa-arrival-and-google-review) |
| DEPLOY-01 | Stage 11 deployment and filled capacity | [15 Stage 11](15-stages-six-to-eleven.md#stage-11-air-ticket-and-deployment) |
| SLA-01 | 48-hour event tasks and no waiting pause | [16 Deadline and daily-check rules](16-tasks-sla-daily-updates.md#deadline-and-daily-check-rules) |
| SLA-02 | Fifteen-minute escalation scan and repeat policy | [16 Deadline and daily-check rules](16-tasks-sla-daily-updates.md#deadline-and-daily-check-rules) |
| SLA-03 | Reassignment preserves clock; explicit Admin reset and history | [16 User journey](16-tasks-sla-daily-updates.md#user-journey) |
| DAILY-01 | Immutable nonblank owner daily remarks | [16 User journey](16-tasks-sla-daily-updates.md#user-journey) |
| DAILY-02 | Missing-update badges, grouped day-end alerts and performance | [16 Deadline and daily-check rules](16-tasks-sla-daily-updates.md#deadline-and-daily-check-rules) |

## Later authorized requirement coverage

| ID | Authorized addition or override | Module and section |
| --- | --- | --- |
| MAIL-01 | Zoho-only in-app connection | [08 Roles and preparation](08-zoho-inbox.md#roles-and-preparation) |
| MAIL-02 | Compose/reply-all/forward and safe attachment sends | [08 Screen and backend contract](08-zoho-inbox.md#screen-and-backend-contract) |
| MAIL-03 | Private/provider drafts and supported folder operations | [08 User journey](08-zoho-inbox.md#user-journey) |
| MAIL-04 | Bounded explicit historical import | [08 User journey](08-zoho-inbox.md#user-journey) |
| ATT-01 | Intentional clocking, sessions and corrections | [20 User journey](20-attendance-clock-activity.md#user-journey) |
| ATT-02 | Visible Staff browser activity and duration limits | [20 Screen and backend contract](20-attendance-clock-activity.md#screen-and-backend-contract) |
| ATT-03 | Adopted monthly hours, allowance and OT calculation | [21 Approved calculation rules](21-attendance-monthly-hr.md#approved-calculation-rules) |
| ATT-04 | Permission/leave/OT requests and staged approvals | [21 Request and approval journey](21-attendance-monthly-hr.md#request-and-approval-journey) |
| ATT-05 | Weekly off, effective timings, duty plans and regularisation | [21 Weekly off and roster](21-attendance-monthly-hr.md#weekly-off-and-roster-journey), [21 Corrections](21-attendance-monthly-hr.md#corrections-journey) |
| ATT-06 | Accepted leave cover, handover/return and assignment eligibility | [21 Recruitment leave cover journey](21-attendance-monthly-hr.md#recruitment-leave-cover-journey) |

The approved **Staff Directory** extension is detailed in [module 11](11-staff-assignment-directory.md). The approved **visible skill segregation and evidence** extension is detailed in [module 07](07-data-bank-skills.md) and [module 13](13-order-matching.md). These are identified separately rather than inventing new original-PRD IDs.

## Verification and review record

The [integration audit](../integration-audit.md) records the current code/test boundary. Latest focused comparisons are [candidate entry](../audit/candidate-entry-followup.md), [skills and matching](../audit/candidate-skills-followup.md), [attendance activity](../audit/attendance-presence-followup.md) and [Staff Directory](../staff-directory.md). Local browser reports are stored under `artifacts/workflow-followup/browser/` as `entry-report.json`, `skills-report.json`, `attendance-report.json` and `profile-report.json`. These generated, gitignored artifacts may not accompany a repository checkout; committed tests and the linked audit remain the portable evidence.

Use this compact record for each module after walkthrough: **reviewer and date; environment; roles exercised; passed checklist items; specific failures; product decisions; remaining live checks; accepted or follow-up required**. An accepted screen must agree with its backend result and downstream record.

Run `python docs/workflows/verify_coverage.py` from the repository root to check that every matrix ID has exactly one coverage row and that the guide's local links and section anchors resolve. This checks documentation completeness, not runtime correctness.
