# DCRM implementation and PRD integration audit

Updated 21 October 2026 after the broader implementation authorized by the user. This document supersedes the current-state conclusions in the [pre-implementation review](integration-audit-baseline-2026-10-21.md). That review and the original [CRM screen](audit/crm-screen-inventory.md), [backend](audit/backend-prd-audit.md) and [reference screen](audit/reference-screen-inventory.md) appendices remain a dated baseline, not a list of unresolved defects in the new source.

## Current conclusion and evidence boundary

The principal CRM paths are now connected from intake and candidate correction through assignment, order matching, all eleven stages, tasks, reporting and User Management. The earlier report-scope defect, arbitrary stage payload/file acceptance, incomplete user deactivation and inconsistent manual duplicate/assignment behavior have implementing fixes and targeted regression coverage. The latest user direction replaces new Gmail/Microsoft connections with Zoho only and adds the full mail workspace and attendance. Zoho connection/folders/compose/replies/forward/drafts, WhatsApp receipt controls, local/S3 storage, scheduler events and durable manual OCR are connected. Legacy non-Zoho receipts remain readable.

This is an implementation and isolated-validation statement. It is not a declaration that the full PRD is production accepted. The contact → company data model and extra navigation remain deliberate differences from the PDF; some selectors/matching still use bootstrap data; historical event coverage begins with the new migrations; exact-time reminder delivery and real provider/production acceptance remain outstanding. The requirement ledger identifies these limits rather than treating every screen as complete.

The review uses all 12 pages of the [14 October PRD](requirements/recruitment-crm-developer-specification.pdf), the current uncommitted workspace, test source files and isolated test/browser artifacts. The reference remains pinned to [Tharunmacho/email-automation at 30f7ce6b7402553116bbe6a2b8b2142bc72c8915](https://github.com/Tharunmacho/email-automation/tree/30f7ce6b7402553116bbe6a2b8b2142bc72c8915). GitHub default-branch HEAD was reverified on 21 October and still matches that commit. Its mounted screens, unmounted components, preview fixtures, roles and service calls were inspected statically; its application and providers were not launched. No result here comes from copying its database or replacing DCRM's PostgreSQL/eleven-stage model.

The [requirements matrix](requirements-matrix.md) retains all 56 original PRD IDs and adds six explicit user-authorized mail/attendance rows. Schema head is 0014 with 35 tables. “Implemented” means connected source; “isolated tests” means the named scenarios passed using test records. Provider calls in those tests are mocked. Tests that were skipped in the earlier baseline are not promoted to passing results; later PostgreSQL results below are separate guarded runs. Production data was not used for fixture resets, and no production migration or deployment is claimed.

## Changes that close the earlier critical defects

| Earlier defect | Implemented contract | Evidence |
| --- | --- | --- |
| Staff report scope and filters were browser-only/inconsistent | One backend filter contract authorizes Staff own scope before reads and applies date/client/order/designation/staff to all six reports and exports; Data Bank remains shared | `services/reporting.py`, `api/routers/reports.py`, `tests/test_reporting.py` |
| Stage saves accepted loose values/file-shaped objects and could race | Typed drafts/completion, genuine entity/stage/field attachment checks, shared row-lock order and atomic expected-version transitions; conflicts return actionable failures | `domain/stages.py`, `api/routers/applications.py`, `test_stage_rules.py`, `test_application_workflow.py` |
| Deactivation could transfer work without actually disabling the user | One transaction validates replacement, transfers candidates/open tasks, records assignment history, changes activation and revokes sessions; rollback leaves all work unchanged | `services/user_admin.py`, `api/routers/users.py`, `test_user_admin.py` |
| Manual duplicates, no-eligible staff and calls differed from intake policy | Shared normalized phone/email/passport identity, explicit Merge/Cancel, empty-only enrichment, Needs review/unassigned fallback, initial task/snapshot/notification, explicit Called yes/no and owner guards | `candidate_identity.py`, candidate/data routers, `test_candidate_workflows.py`, `test_workflow_scheduler.py` |

Additional correctness work includes immutable file publication under concurrent Windows uploads, checksum checks before attaching cached extraction documents, strict WhatsApp signature/media-host checks, completed-hash reuse with separate receipt provenance, and historical daily-update attribution after reassignment. These are covered by focused storage/WhatsApp/scheduler tests.

## Screen-by-screen current implementation

All route patterns below come from `frontend/src/app/routes.tsx`; dialogs belong to their feature rather than an unmounted demonstration screen.

| Route/screen | Connected behavior | Source and remaining limit |
| --- | --- | --- |
| `/login` | Persistent account/session login, errors, redirect, responsive Poppins layout | Auth page/API; isolated login/mobile checks. Real deployed cookie/domain configuration still needs acceptance |
| `/` Dashboard | Admin team/staff scope; Manager/Staff own metrics; agency-day source intake, due/open/overdue/escalated/missing-update tasks; eleven-stage candidate drill-down; order progress; due-sorted personal tasks and private to-dos; Admin email/WhatsApp health | Dashboard feature, reporting service and minimal health endpoints. New WhatsApp health has API role tests and production-build validation; live worker signals require deployment |
| `/inbox` | Zoho-only Admin connect/reconnect/disconnect; live folder search/paging/read/star, new folder/move/archive/trash/restore; stored-history account/read/attachment filters; compose/reply/reply-all/forward; attachments, paginated private drafts/send history, shared remote draft editing and uncertain-operation recovery; candidate intake/review/history | `zoho-mailbox.tsx`, `mail-compose.tsx`, Zoho/mail routers and services. Plain-text composition; attachment filters explicitly cover stored history. SMTP acceptance is not delivery; no live mailbox accepted |
| `/whatsapp` | Explicit Admin connect configured server phone, disconnect/reconnect, channel status, message/search/status paging, originals, auto-intake, retry/process and reasoned link review | Webhook and worker enforce enabled configured channel; disconnect preserves receipts/documents/queued checkpoints. No outgoing WhatsApp conversation UI |
| `/clients` | Contact list/create/edit/status/activity | Client dialog and master-data API. Client remains a person/contact above employer companies, differing from PDF terminology |
| `/clients/:id` | Contact detail, associated employers/orders/candidates, edit and activity | Client detail and master-record audit; same retained hierarchy |
| `/companies` | Employer create/edit/status/fields including independent WhatsApp, agreement original/version viewing/download, saved-ID retry and record activity | Company dialog, file API, master-data service. Extra route and contact/company hierarchy differ from literal PRD naming |
| `/jobs` | Designation create/edit/skills/experience/category, protected deletion; order creation/edit/status and terms | Job dialogs and master-data API. Active-parent and history/deployment guards prevent invalid edits |
| `/orders/:id` | Order context, actual mapped/selected/deployed counts, editable terms, candidate rows, match drawer and unmap/history | Order page and application API; six combined matching filters and exact normalized skill ALL/ANY. Drawer still searches bootstrap candidates |
| `/add-candidate` | Basic/passport/work/source fields; six durable file inputs; explicit OCR for resume/passport/Aadhaar; recovery of original/job/draft/edited keys/saved candidate; review corrections; duplicate Open/Merge/Cancel; safe upload/attach retry | Add Candidate, `use-document-intake.ts`, document-job router/service/worker. Supporting photo/education/experience files persist without unnecessary identity OCR |
| `/data-bank` | Shared candidate directory and status pools; server pagination/sort/per-column search, indexed resume text, exact skill ALL/ANY/no-skills facets, supervisor reassignment and Admin XLSX export | Candidate search/router plus DataTable controlled server mode. A failed request preserves current rows/filter inputs |
| `/candidates/:id` | Profile/work/passport, documents and versions, application links, owner/supervisor correction and explicit review acceptance, Called yes/no dialog, linked email evidence history | Profile/edit/call components and candidate API. Profile field corrections preserve original extraction evidence and human zero values |
| `/mapped` | Application list and stage-modal entry points | Existing navigation extension; backend application summary remains shared while Staff detailed work is ownership scoped |
| Stage modal | Scrolling collapsed completed history, current editable section and hidden future sections; draft, daily remark, real document versions, completion/conflict recovery, repeat medical and Admin reasoned reopen | Workflow modal plus typed application transaction. Historical invalidation is retained using `reopened_reason`, not deleted history |
| `/tasks` | Open/due/overdue/completed/escalated views, staff filters, task details, daily remarks/history, not-updated badges, supervisor reassignment and Admin SLA reset; historical missed-day drill-down | Tasks page, workflow/data APIs, scheduler events. Current owner and historical event owner are intentionally distinct |
| `/todo` | Private Admin/Manager create/edit/tick/reopen/delete, priority/date/time, record links, tabs and dashboard summary | To-do API/audit; independent 30-second reminders commit with notification, plus bell polling delay; task SLA scans remain fifteen minutes |
| `/attendance` | Staff clock in/out and own sessions; Admin/Manager date/staff-filtered team view, timelines/totals; Admin reasoned corrections; separate visible foreground presence | Attendance API, durable clock intents and ordered presence checkpoints; monotonic browser age/coalescing/stale-response guards. No payroll or hidden productivity inference |
| `/reports` | Six server reports, shared five filters, scope/definition notes, genuine Excel/PDF exports and separate currency totals | Reports feature/service/export helper. Legacy ownership/event limitations are stated in outputs; large-volume report scaling remains unmeasured |
| `/users` | Admin create/edit/activate/deactivate, Staff auto-assignment/leave, replacement prompt, password reset, force logout, session effects and real cursor-paginated user activity | Users page/service/router. No partial-success deactivation warning remains |
| Global shell/search/bell | Role-aware navigation, recipient notification read/read-all and meaningful candidate/task links; global record/page search; responsive layout | App shell/store. Global candidate search still uses bootstrap rather than the Data Bank's bounded server query |

## End-to-end behavior and ownership

1. Admin configures Staff eligibility and sources. Zoho is connected inside CRM with server-encrypted app-password credentials; WhatsApp must be explicitly connected against the configured server phone. Disconnect stops incoming work and preserves evidence. Manual, email and WhatsApp write into the same candidate/document tables. Incoming originals are retained before extraction progress is acknowledged. Slow OCR runs separately from HTTP requests and mailbox polling.
2. Candidate identity uses normalized phone OR email OR passport. A manual duplicate requires explicit Merge or Cancel; automated matches fill only empty fields and retain human edits, owner and status. Ambiguous identities remain reviewable receipts. A WhatsApp sender fills an empty WhatsApp contact but is never used as phone/email/passport identity.
3. New candidates rotate through active, opted-in, non-leave Staff under a locked pointer. Initial owner is snapshotted; a 48-hour call task and persisted notifications are created. If nobody is eligible, the candidate stays visible as unassigned/Needs review. Reassignment can create the initial task later without pretending review has occurred.
4. Recruiters explicitly record Called yes/no and interest outcome. No-call/unreachable leaves the call task open. Corrections and review acceptance persist separately from source OCR evidence. Failure leaves entered values available for retry.
5. An eligible candidate maps to one active order/application. Map, unmap, complete and deployment use locks/active uniqueness; unmap retains history and cancels obsolete tasks, while a fresh mapping starts stage1. Current profile/task links select the correct application.
6. Each application moves through the eleven typed stages below. Real attachment IDs must refer to valid associated evidence. Waiting does not pause the SLA. Negative exits occur only at 1/2/4/6. Medical repeats preserve attempts and create follow-up work. Admin reopening retains invalidated history and recomputes downstream task/deployment effects.
7. Scheduled checks persist escalation/reminder/missed-update events. A daily catch-up resolves the responsible owner at the agency-day boundary from assignment events, so today's transfer does not blame the new owner for yesterday. Completing the task on that day exempts the daily remark. Notification links use stored event task IDs.
8. Stage11 deploys once; order fill is based on actual deployed applications. Reports use effective completion records and historical snapshots/events; operational dashboard work uses current ownership. Advances keep their currencies, and waiting candidates are not labeled as rejected drop-off.

9. Zoho messages can be read and organized, replied to or used to start a candidate intake. Draft text/files persist before sending. Changed draft content requires review; lost/unknown send outcomes retain the same request and check provider state before any new send. CRM draft lists are actor-private; synchronized Zoho Drafts and other folders are shared with mailbox users.
10. Staff intentionally clock attendance. Foreground presence is a separate visible signal based on trusted interaction, not a clock-in/out substitute. Admin corrections require a reason and preserve audit history.

## Eleven-stage completion contract

The PDF's completion column controls mandatory progression fields. Additional recorded details are type-checked when supplied; they do not silently become extra blockers. Every completion records owner/actor/time and updates matching task/history in one transaction.

| Stage | Implemented completion and waiting/exit rule |
| --- | --- |
| 1 Prescreen | Passed advances; Failed closes; exact result enum required |
| 2 Forwarded | Yes advances; No requires not interested or resume not received, and both put the candidate in Not interested |
| 3 Interview scheduling | Scheduled Yes plus date advances; No waits; supplied time/mode/location are validated |
| 4 Interview result | Selected/Rejected plus nonblank remarks; rejection closes |
| 5 Offer | Obtained Yes plus real associated offer upload; otherwise waits |
| 6 Medical | Latest Fit advances; Unfit/no reapply closes; reapply with new medical date records an immutable attempt and fresh re-medical task |
| 7 Passport | Original collected Yes advances; profile copy can prefill and replace with version history; supplied custodian/reference/date are checked |
| 8 Advance | Collected Yes, positive amount and real receipt upload; currency recorded and separately reported |
| 9 Visa arrival | Arrived Yes advances; Google review is recorded and nonblocking |
| 10 Stamping | Done Yes plus real stamped copy; otherwise waits |
| 11 Air ticket | Real ticket copy completes and deploys; booking/date/airline/PNR are validated when supplied; no next task |

Admin reopen requires a reason and cannot reopen an old application over a newer mapping. Superseded target/downstream records remain readable for audit but are excluded from effective stage/advance/deployment aggregates.

## Role and data-scope contract

| Capability | Enforced behavior |
| --- | --- |
| Data Bank and profile/application summaries | All three roles can read all candidates; shared discovery is retained |
| Candidate correction/calls/document replacement | Owner or supervisor; initial manual creator may attach files during the first 24 hours after automatic assignment |
| Stage work and detailed bootstrap work records | Admin/Manager any; Staff own current candidates/work; other Staff drafts/details are not exposed through bootstrap |
| Mapping | All roles under active-order/application rules; mapping does not grant permission to edit another Staff member's stage |
| Reassignment | Admin/Manager; validated replacement; all open work moves without resetting deadlines |
| Dashboard | Admin team/selected staff; Manager/Staff personal metrics |
| Reports | Admin/Manager all/selected staff; Staff own scope, rejected server-side if forged |
| Data Bank export | Admin only, using the same filtered server query |
| Source connection/retry/review and health | Admin controls; authenticated Inbox/source evidence reads retain shared CRM visibility |
| Mail drafting/sending | Authenticated shared mailbox operations; private CRM draft/recovery records are actor-owned, including against Admin reads; synced Zoho draft contents remain shared at the provider |
| Attendance/presence | Staff own clock/detail; Admin/Manager team reads; only Admin reasoned corrections; presence never modifies attendance |
| Personal To-do | Admin/Manager, private to owner |
| User lifecycle/reopen/SLA reset | Admin-only mutations with audit; stage reopen and reset require a reason |
| Notification read state | Recipient only; another user's notification ID cannot be acknowledged |

## Reference integration decisions retained

The reference's upload recovery, visible extraction/review states, candidate correction, source evidence and operational user screens informed interaction patterns. DCRM retains its own PostgreSQL records, locked strict round-robin assignment, shared Data Bank, owner-scoped work and eleven-stage deployment lifecycle.

The reference has no mounted unified PRD Inbox or dedicated six-report module; transport services and loose components are not evidence that those screens existed. Its six-step recruitment flow, desk/country routing, submission-based fulfilment, Finance/custom permissions, payroll, Sourcing Hub and B2B flows are not adopted as DCRM requirements. Attendance is now explicitly authorized by the user and implemented independently; that does not import the reference payroll rules. Mounted/unmounted/demo distinctions and source links remain in the [reference inventory](audit/reference-screen-inventory.md). Its runtime is still unverified.

## Workflow consistency and shared database follow-up

The subsequent screen-to-API review corrected these mismatches:

- Candidate corrections validate the combined saved profile; duplicate merges validate retained and incoming dates before mutation. New manual records normalize phone/WhatsApp consistently with matching and editing.
- Deployment rechecks locked order capacity, preventing multiple already-mapped candidates from filling the final opening concurrently. Draft reads enforce current ownership/supervisor access.
- Client, company and order deletion preserves linked documents and closed application history by rejecting deletion and using the existing inactive/closed status controls. Parent deletion and order reparenting serialize safely.
- Profile correction and mapping distinguish a committed write from a failed refresh; retries reload data instead of resubmitting the mutation. Failed dialogs retain their inputs, and pending remark saves protect later edits and dialog closure.
- Deployed/active candidate actions agree with backend eligibility, invalid Tasks stage links no longer crash, and medical Fit does not validate hidden Unfit retest dates.
- WhatsApp callback URLs respect a separately hosted API. Development scripts start the required background workers, and the supervisor refuses to start against a mismatched database revision.

The user's local workspace error was traced to the database shared with Dokploy production: revision `0007_email_intake` versus code head `0014_attendance_ordering`. Redis was also unavailable. The new health/schema handling reports the mismatch without auto-migrating or initializing records on the old schema. A disposable PostgreSQL regression verifies that the old database remains unchanged on failed startup/bootstrap, then verifies health, existing login session, candidate and task recovery after an explicit test upgrade. Offline review SQL for 0007→0014 is retained in `artifacts/quality-migration-0007-to-0014.sql`.

Dokploy's build type is confirmed as Nixpacks. The shared production database remains unmodified by this pass. The user-authorized direct database backup was created at 19:02 IST on 21 October and restored in an isolated PostgreSQL 18.6 container at 19:07 IST: all 22 tables/43 rows and content fingerprints matched. It excludes hosted upload files and global roles. Code publication, uploaded-file preservation, stopping old writers, migration and matching deployment remain necessary; the backup must reflect the cutover point if subsequent writes occur. See the [coordinated release steps](operations.md#shared-dokploy-database-incident-21-october-2026).

## Verification evidence

These runs overlap and must not be added together as a unique-test total. Counts describe the recorded run, not every later edit to unrelated files. Database runs use guarded disposable loopback PostgreSQL databases, never the configured live CRM database. Browser fixtures block external traffic and use synthetic API responses unless a later row explicitly records real local API validation.

| Evidence | Recorded result | Scope and limit |
| --- | --- | --- |
| Latest complete backend suite | **720 passed, zero failed/skipped** | Guarded disposable PostgreSQL, head0014, schema/model parity, real storage fixtures and the workflow/schema fixes above; provider transports mocked. XML retained in artifacts/quality-final-backend.xml. Five existing deprecation warnings. Windows process cleanup received a subsequent focused runtime verification; no production database changes |
| Candidate/report/email targeted suite | 69 passed | Real isolated PostgreSQL for identity, correction, search/vector, assignment, Staff report authorization, filters, historical owner and export formats, plus email intake contracts |
| Scheduler/WhatsApp/storage review suite | 26 passed | Strict deadlines, repeat events, six before/at/after reassignment daily-update cases, missing-original guard, cached selected-file corruption, concurrent local publication and mocked S3 |
| Final WhatsApp extension suite | 14 passed | Real PostgreSQL plus mocked Meta transport: sender-contact identity exclusion, verified same-hash reuse/provenance, corrupt-cache fallback, Admin-only health/connect/disconnect, disabled receipt/queue pause and reconnect |
| Stage/mail focused suite | 60 passed | Typed stage rules, real PostgreSQL application transitions/concurrency and mocked OAuth/IMAP/SMTP/review contracts. Earlier broader-run fixture failures were corrected and rerun; not counted as unexplained passing cases |
| Final Zoho/mail suite | 172 passed, then 104 focused filter regressions passed | Includes 24 PostgreSQL Zoho cases and 36 adapter cases, encrypted connect, folders/drafts/attachments/private scope/idempotency/reconciliation; exact stored-history attachment EXISTS and provider read filters. Counts overlap; no live provider |
| To-do cadence and workflow suite | 16 passed | Due boundary, concurrent reminder workers, rollback retry, 30-second reminder cadence; SLA checks retain fifteen-minute cadence |
| User lifecycle suite | 24 passed | Validation, permission/session controls, atomic transfer/deactivation rollback, last-admin/self rules, real audit pagination and assignment-event history |
| Master-data suite | 18 passed | PostgreSQL edits/status/parent guards/history, atomic references, rollback and independent Company.whatsapp |
| Durable manual document suite | 11 passed | PostgreSQL jobs plus real local storage/synthetic documents/fake provider; original/checkpoint recovery, owner scope, durable drafts, repeat-safe attach, uncertain-commit retention and known-failure cleanup |
| Shared bootstrap scope suite | 4 passed | Real PostgreSQL: Staff see own detailed drafts/stages/tasks/remarks while all candidate/application summaries remain shared; Admin/Manager retain full work details |
| Storage administration suite | 4 passed | Real PostgreSQL reconciliation/copy, source changes between audit/copy, and draft-only/send-snapshot-only retained files; no real bucket or restore deployment |
| Schema suite | 14 passed | Disposable PostgreSQL head/model parity and downgrade/upgrade checks; this is not a production migration |
| Frontend tests | Final run: 65 passed, zero failed/skipped | Skills/matching, stage rules, file/OCR/API/session tests, attendance transport, medical Fit/retest consistency and callback URLs for separately hosted APIs |
| Latest production build | Final TypeScript/Vite build passed; 2,749 transformed modules | Includes the candidate/mapping retry, retained-record deletion, task/stage guards, Zoho workspace and attendance changes. Build output is local; production deployment was not performed |
| Workflow consistency synthetic browser | Passed, eight flow groups; zero runtime errors | Delayed/failed profile saves and mapping, refresh recovery without repeat mutation, invalid Tasks URL, pending remarks, deployed actions and closed-history deletion. Evidence: artifacts/ui-consistency-browser/report.json |
| Candidate/report synthetic browser | Passed | All six report tabs for all three roles; filters/export query parity, currencies, skill ALL/ANY and server column filters, explicit duplicate Cancel/Merge, correction retry, mobile390 and no runtime errors |
| User/login/manual-intake synthetic browser | Passed | Create failure/retry, atomic transfer request, activity, login layouts, durable job recovery/reload/human-edit preservation and attach-failure retry without duplicate candidate; mobile390 |
| Earlier mail synthetic browser | Passed before Zoho-only override | Paging/search/read/original links, lost reply response without duplicate send, unknown-delivery lock, review failure preservation/retry, bounded historical import, profile source history and Staff/mobile controls. Historical OAuth checks do not mean those providers remain available in the current UI |
| Zoho synthetic browser | Passed, ten flow groups | Live/stored filter scope, unknown move, reply-all/forward attachments, Bcc-only sends, same-ID sync, save failure, lost/unknown send recovery, older-attempt isolation, changed-draft review, rejected-intent edit, draft paging/search, desktop/mobile folder creation, connect retry/no browser credential persistence; screenshots at 1440/390 |
| Attendance synthetic browser | Final five-group run passed | Explicit Staff clock survives an unknown response with the same idempotency key; checkout targets the active session; untrusted events do not count; trusted input updates ordered presence; Manager team filters/detail and Admin reasoned correction failure/retry. Desktop/mobile screenshots retained |
| Browser against real local API/PostgreSQL | Final run: 210 checks passed; 1,032 API responses | Seventeen routes × three roles × widths1440/768/360/390 =204 route visits, real form login, persisted Staff check-in/reload/check-out/reload, private to-do CRUD, notification acknowledgement and task remark. Zero JavaScript/unexpected HTTP errors; the expected Staff `/users`403 remains a negative permission check. No provider-backed acceptance is implied |
| Runtime/startup subset after fixture updates | 103 targeted tests passed | Includes four email/WhatsApp flag combinations and startup contracts; production launcher always supervises task/manual workers even with email disabled. The final complete backend suite above supersedes this subset |
| Windows runtime follow-up | 46 passed after the complete suite | Twelve runtime tests and 34 production-launcher tests. Real synthetic Uvicorn reload processes verify delayed job assignment, assignment/resume failure and cleanup whether the reload parent survives or has already exited. Windows starts owned children suspended, assigns a private kill-on-close Job Object, then resumes; no production services used |
| Attendance backend | 19 PostgreSQL cases in a 78-test focused run, plus two focused regressions | Durable clock intent/session targeting, role scope, reasoned audit corrections, presence sequence/expiry and session revocation; counts overlap with other suites |

Local replay evidence: [candidate/report report](../artifacts/candidate-reports-browser/report.json), [user report](../artifacts/user-admin-browser/users-report.json), [manual intake report](../artifacts/user-admin-browser/manual-intake-report.json), [mail report](../artifacts/mail-workflow-browser/report.json), [Zoho report](../artifacts/zoho-mailbox-browser/report.json), [attendance report](../artifacts/user-admin-browser/attendance-report.json), and [real local API browser report](../artifacts/integration-browser/report.json). Generated browser artifacts are ignored by Git; the implementing tests and this audit remain reviewable repository source. Earlier all-route and file/browser evidence is retained in the dated baseline and is not relabeled as a new all-mutation acceptance run. Earlier broad runs exposed incomplete/import-order-dependent test settings fixtures. After fixing fixture isolation, the affected combined 47-test group and final full 695-test suite passed. Production application behavior was unchanged by those fixture corrections.

## Remaining acceptance and product limits

1. **Production integration:** connect an authorized Zoho test mailbox later inside CRM, configure WhatsApp Business webhook/phone, Veriis and an S3 test bucket. Exercise real authentication/reconnect, folder/draft synchronization, same-account delivery, document inspection/provider restart, replay and recovery. No credentials were requested or live mail sent in this session; mocks do not establish provider acceptance.
2. **Deployment and recovery:** apply reviewed migrations to an isolated deployment, start all supervised workers, verify persistent volumes/token encryption key/object namespace, backups, checksum reconciliation and a restore drill. PostgreSQL fixture migrations and mocked S3 calls do not validate a production cutover.
3. **Employer and navigation alignment:** the implemented contact → company extension and extra routes remain different from literal PRD wording. Do not rename/migrate real employer records without a deliberate product/data migration decision.
4. **Large-volume behavior:** order matching/global search/record pickers still use bootstrap data; reports aggregate selected fields in server memory. Load-test representative candidate, task, mail and document counts; define bounded matching and report query limits before claiming large-scale readiness.
5. **Legacy history:** migration backfills can infer prior owner from current records but cannot reconstruct transfers, escalation or daily-update events that were never stored. Historical reports disclose this and should not be represented as a complete retrospective ledger.
6. **Communication scope:** SMTP success means provider acceptance, not final delivery. Unknown/partial sends require reconciliation; no automatic resend occurs. Plain-text compose/reply/forward and attachments are implemented, with 20 files/18 MB combined. Attachment search is exact over stored CRM messages, not all unopened remote mail. Source review links existing identities; automatic multi-person splitting and optional acknowledgements remain outside the completed path. WhatsApp currently implements inbound receipt/document intake for one configured number; outbound chat/templates, Embedded Signup, Business App coexistence, mobile message echoes and history synchronization are not implemented. The later coexistence request is outstanding, not implied by the inbound webhook integration.
7. **Time and UI acceptance:** to-do reminders have a 30-second scan plus browser bell polling; SLA/daily scans remain fifteen minutes. Presence is a separate approximate foreground signal, not attendance or productivity. Deployed cross-role mutations, outage recovery and elapsed-time/browser acceptance remain necessary; fixture success is not live-environment acceptance.

See [email connection operations](email-connections.md), [runtime/storage operations](operations.md), [database design](database-design.md) and the runtime/storage deployment documentation for configuration. The current state is a substantially implemented and locally verified CRM with explicit product and external-acceptance limits, not an unsupported claim that every requirement is production complete.
