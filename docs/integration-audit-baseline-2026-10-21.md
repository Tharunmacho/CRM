> Historical baseline: this is the review recorded before the broader implementation authorized on 21 October 2026. Its gaps and test counts describe that earlier state. See [the current integration audit](integration-audit.md) for the implementation update.

# DCRM and email-automation: complete PRD integration review

Reviewed 21 October 2026. This is the implementation decision document requested before the next round of integration.

## Conclusion and review boundary

The current CRM has a useful connected foundation: persistent login, candidate creation, strict staff rotation, employers/orders, application mapping, an eleven-stage engine, task records, versioned documents, local document inspection and durable incoming IMAP processing. It does **not** yet satisfy the complete v1 PRD. Several screens expose partial or placeholder workflows, and important server rules, user administration, scheduled alerts and reporting remain incomplete.

The reference is useful for upload/intake recovery, candidate correction, communication history and operational screens. It is **not** a completed implementation of this PRD: its recruitment lifecycle, assignment policies, fulfilment calculations, roles and HR modules differ. Copying the reference wholesale would replace working CRM decisions with incompatible behavior.

The review covers:

- All 12 pages of the [14 October PRD](requirements/recruitment-crm-developer-specification.pdf), including its role table, every specified screen, eleven stages, task events and daily-update rules.
- The current local CRM routes, dialogs, API handlers, models, workers, storage, tests and deployment configuration, including the focused changes completed in this session.
- The reference repository [Tharunmacho/email-automation](https://github.com/Tharunmacho/email-automation/tree/30f7ce6b7402553116bbe6a2b8b2142bc72c8915), pinned to `30f7ce6b7402553116bbe6a2b8b2142bc72c8915`. Its connected GitHub source and matching local source clone were inspected. Older comparisons with `M:\gmail automation` are historical context, not proof of current behavior.
- Mounted screens, unmounted components, fixture/demo routes, visible actions, persistence calls, role boundaries and cross-screen handoffs. A file or button existing does not count as a completed feature.

This is source-backed coverage plus automated and isolated browser verification of the CRM. The reference application was not launched. No live mailbox, paid OCR provider, production database or deployed environment was exercised in this review. Database-dependent tests that skipped are not counted as passing acceptance. Absolute absence of defects cannot be established by a source review; the acceptance cases below identify what still needs runtime proof.

Detailed evidence is divided into three appendices:

1. [Every CRM screen and dialog](audit/crm-screen-inventory.md).
2. [Every reference screen, role and integration](audit/reference-screen-inventory.md).
3. [Backend contracts, reproduced rule defects and PRD gaps](audit/backend-prd-audit.md).

The [requirement-by-requirement ledger](requirements-matrix.md) remains the full PRD ID index. This review supplements it with the reference comparison and delivery dependencies; it does not declare planned work complete.

## Focused changes completed before the broader audit

These changes belong to the original request to check uploads, viewing and skill segregation. The wider integrations below remain recommendations.

- Add Candidate now exposes preferred designations, skills, total/overseas experience, expected salary, source, WhatsApp and city. Photo, education and experience attachments are selectable. Recruiters can review or correct extracted work fields before saving.
- A successful candidate creation retains its ID even when the subsequent directory refresh fails. Failed document uploads remain visible and retry against that saved candidate; successful documents are not deliberately uploaded again by the retry action.
- Data Bank displays saved skills and supports exact normalized skill selection, ALL/ANY matching, candidate counts and a no-skills-recorded view. Case, whitespace and Unicode presentation differences are normalized. It does not guess trades from resume substrings.
- Order matching combines designation, experience, skills, current location, passport-number presence and staff filters. It shows matching/selected counts and waits for mapping outcomes. Changing filters clears stale selections; failed mappings remain visible.
- Document controls expose viewing and explicit downloads for current and earlier versions, uploader names, validation and persistent errors. New workflow attachments use distinct stage/field slots so offer, visa, stamping and ticket histories do not share one generic `file` slot. Legacy known references remain readable.
- The file API checks parent records, locks the parent before version allocation, writes upload audit records, cleans up known failed writes, retains bytes when commit outcome is uncertain, and supports `?download=true`. Application uploads follow existing stage ownership rules.

Remaining limits: no general candidate-edit/merge workflow was added; uploads remain local storage; uncertain upload responses can still produce an additional immutable version on retry; manual draft OCR is not a durable job. Candidate upload authorization needs creator/upload-session provenance before owner-only restrictions can safely coexist with immediate round-robin assignment. PDF and supported image viewing use the browser; Word/HEIC behavior depends on browser support and generally requires download. This is not a new embedded Office viewer.

## End-to-end flow: what is connected and what remains

| Handoff | Current CRM | Reference value or mismatch | Required integration |
| --- | --- | --- | --- |
| Administrator prepares users | Database accounts and CLI commands work; web admin mutations mostly do not | Reference has richer user/permission screens, with extra roles | Implement Admin account lifecycle, leave/assignment flags, session revocation and audited deactivation/reassignment using the PRD's three roles |
| Administrator connects sources | Server-configured IMAP accounts can be connected; worker runs separately | Reference has Gmail/multiple mailbox and WhatsApp paths, but no mounted unified Inbox | Add Gmail/Microsoft OAuth, account removal, health and WhatsApp Business webhook; keep credentials server-side |
| Message/document arrives | New IMAP mail is stored with receipt identity and originals; manual files are held until Save | Reference has useful storage and intake services | Preserve original-before-checkpoint ordering; add object storage and a common channel contract |
| OCR starts or resumes | Local page inspection, specialist resume/passport/Aadhaar extraction, durable email checkpoints and provider IDs | Reference has recovery/checkpoint patterns worth retaining | Shared durable manual/email/WhatsApp job state, review resolution and provider acceptance; retain manual fallback |
| Extracted identity is resolved | Email uses phone OR email OR passport; manual entry uses phone AND DOB, or passport | Reference has identity handling, but several contextual policies differ | One normalized identity service; explicit manual merge/cancel; preserve existing owner and populated fields; review conflicting matches |
| Candidate is created or enriched | Same PostgreSQL candidate table; original/extracted evidence retained | Reference MongoDB/GridFS is a storage implementation, not a target schema | Keep PostgreSQL and existing entities; add correction history and field provenance, not a second candidate database |
| Candidate receives owner/task | Locked strict rotation works for eligible staff; no-eligible manual behavior is inconsistent | Reference desk/country assignment differs from PRD | Consistent Needs review/unassigned handling, eventual assignment, initial task and owner/Admin notification for every channel |
| Recruiter reviews first call | Call outcome can save; UI always records Called=true; server lacks sufficient ownership/enum checks | Reference review/verification screens can inform UX | Explicit Called yes/no, validated interest outcome, immutable notes, candidate corrections and task closure rules |
| Recruiter finds relevant talent | Saved-skill filtering and combined order filters now exist | Reference industry inference is weak; matching scores are heuristics | Add bounded server search, real column filters, controlled skill taxonomy/aliases only with reviewed rules, transparent requirement matching |
| Candidate maps to order | Application record, active uniqueness and initial stage task exist | Reference submission/offer flow differs from eleven stages | Preserve application-per-order; enforce eligible/open orders, concurrency, useful errors and atomic map/unmap/remap |
| Staff processes eleven stages | Forms/drafts/history/task transitions exist but rule validation and concurrency are incomplete | Reference six-step recruitment flow is unsuitable | Typed per-stage validation, attachment association, locks, medical attempts, admin reopen and PRD layout |
| Waiting work ages | Browser computes overdue appearance from 48 hours | Reference first-view SLA and callback task semantics differ | Real 15-minute escalation scheduler, 24-hour repeat setting, owner/Admin/Manager notifications and persistent escalation events |
| Staff adds daily updates | Remarks persist and display | Reference activity/review history can inform presentation | Owner-only nonblank updates, agency-day boundaries, grouped end-of-day missed-update events and completion-day exemption |
| Final ticket deploys candidate | Stage 11 updates application/order in source | Reference submitted/offer counts are not deployment counts | Atomic order count/fill handling, capacity policy, remap restrictions and concurrent deployment acceptance |
| Management measures outcomes | Six report panels exist; several filters and Staff scope are wrong; exports are placeholders | Reference overview/reporting does not provide the PRD's historical metrics | Scoped backend aggregates, historical ownership/events, all common filters and working Excel/PDF exports |
| Operators recover failures | Incoming email retries/checkpoints exist; file failure behavior improved | Reference offers useful diagnostics, but some screens swallow write errors | Review/resolve tools, storage reconciliation, restores, restart tests, alert monitoring and observable failure states |

## Screen crosswalk

The appendices enumerate actions and dialogs in detail. This table gives the integration decision for every current CRM route family, including extensions beyond the ten-item PRD sidebar.

| CRM screen | Relevant reference screen | Decision / missing work |
| --- | --- | --- |
| Login | Login | Keep database-backed sessions; complete expired/disabled/revoked-session acceptance and Admin lifecycle |
| Dashboard | Overview | Keep useful charts; reconcile metrics with PRD and role scope; add actual escalation/daily-update events and correct drill-downs |
| Inbox | No mounted unified Inbox | Build PRD preview/reply/account management on current incoming-mail screen; reference transport services alone do not supply this UI |
| Clients list/create | B2B/client-related records differ | Decide employer-client naming; implement edit/status and complete contacts rather than import a B2B sales workflow |
| Client detail | Reference job/client context | Ensure direct employer → orders → candidates navigation under the chosen hierarchy |
| Companies | Reference company/order fields | Existing extension must have an agreed place; complete agreement upload recovery and edit/status workflows |
| Jobs / designations | Job Orders | Keep designation → order model; finish required field validation, editing and lifecycle controls |
| Order detail / matching drawer | Job Orders / candidate matching | Retain new skill/location filters; add server search, reliable bulk outcomes, per-application history and deployment-based progress |
| Add Candidate | Candidate Upload | Keep manual entry with OCR assistance; finish durable intake/correction/merge and complete validation |
| Data Bank | Candidates | Keep all-candidate visibility and normalized skill facets; implement all column filters, server pagination and Admin Excel export |
| Candidate profile | Candidate profile / edit / review | Add real edit/review resolution, source and correction timeline, explicit call fields and authorized file replacements |
| Mapped Candidates | Assigned Candidates / recruitment modal | Keep as optional navigation extension; align source of truth to CRM applications and PRD stage rules |
| Stage modal | Six-step recruitment modal | Implement the PRD's eleven scrolling sections, hidden future sections, medical repeats and admin reopen; do not import the reference lifecycle |
| Tasks | Tasks / callbacks | Keep system-generated event tasks; add missing re-medical, escalation, daily-update and reassignment behaviors |
| Personal To-do | Reference task concepts differ | Keep private Admin/Manager list distinct from recruitment tasks; complete links, edit and reminders |
| Reports, all six tabs | Overview; no dedicated mounted reports module | Complete server-scoped PRD metrics, shared filters, historical attribution and exports |
| User Management and dialogs | User Management / Staff / Settings | Implement genuine persisted actions with three-role constraints and session effects; remove deceptive partial success behavior |
| Global shell/search/bell/profile | Top bar / search / notifications | Connect missing actions, read state, meaningful links and role scope; desktop/mobile behavior must remain consistent |

Additional reference-only areas are inventoried rather than silently adopted: Sourcing Hub, B2B enquiries, Data Management, attendance, payroll, HR settings/approval policies, granular permission/Finance roles, preview fixtures, and unmounted email-rule/activity screens. They are not mandatory v1 features in the supplied PRD. Aadhaar extraction is already an explicitly documented extension in this CRM; that does not implicitly authorize the remaining reference-specific policies.

## Admin, Manager and Staff contracts

| Capability | PRD role behavior | Current gap / acceptance |
| --- | --- | --- |
| Connect/remove email and WhatsApp | Admin only | Incoming IMAP connect/toggle guarded; removal, OAuth and WhatsApp absent |
| Create employers/designations/orders | All three roles | Create paths exist; complete schema checks and edit/status requirements |
| View Data Bank | All candidates visible to all three | Preserve shared reads; keep ownership checks on modification actions. The pinned reference also supports shared profile reads |
| Map/unmap | All three roles | Verify API allows intended mapping scope while preventing invalid concurrent applications; stage editing has a different permission rule |
| Work stages | Admin/Manager any; Staff own candidates | Existing stage guard useful; validate every mutation and actual file association |
| Initial call, remarks, candidate corrections | Owner workflow; supervisors where policy permits | Calls/remarks currently lack consistent server ownership checks; correction API absent |
| Reassign | Admin/Manager only | Guard exists; target must be eligible, open tasks move atomically, old/new owner audited, clock preserved |
| Receive automatic assignments | Eligible Staff only | Locked algorithm exists; test inactive/on-leave/opt-out/no-staff cases across channels |
| Receive escalations/missed-update alerts | Admin and Manager | Producers and durable event records absent |
| Reopen completed stage | Admin only, mandatory reason | UI/API not implemented; stage dependencies and task/SLA consequences need one transaction |
| Reports | Admin/Manager all; Staff own numbers | Current report panels can expose team aggregates to Staff; enforce report scope server-side without narrowing Data Bank visibility |
| Personal To-do | Admin/Manager, each user's private list | Core privacy exists; complete reminder and record-link UX |
| User Management | Admin only | Route is gated; real CRUD/lifecycle APIs and buttons missing |
| Excel export from Data Bank | Admin only | Visible button is not wired; export must repeat filtering and authorization on server |
| Notification bell | Recipient-specific records | Email assignment creates some events; read/acknowledgment, other producers and action workflows incomplete |
| Audit log | Every create/edit/stage/reassignment/upload attributed to user/time | Upload audit added; remaining write coverage and usable user/entity audit views incomplete |

**Immediate correctness issue:** the existing Deactivate User dialog can reassign candidates first and then report that deactivation is unavailable. Ownership may change while the account stays active. Replace this with one supported transaction before calling the administration flow complete. See the screen appendix for the exact path.

## All eleven stages compared with the PRD

All stages need typed values, ownership checks, atomic version handling and persisted audit/history. A file-shaped string is not proof of an uploaded attachment. These shared gaps apply even when the form looks complete.

| Stage | PRD completion / negative behavior | Remaining work |
| --- | --- | --- |
| 1 Prescreening | Passed advances; Failed closes; result required | Reject unknown result enums; persist remarks/history and exact closure/task behavior |
| 2 Forwarded to client | Yes advances; No with not interested or resume not received closes into Not interested pool | Both negative reasons must set candidate Not interested; reconcile extra forward-date blocker with PRD completion column |
| 3 Interview scheduling | Scheduled Yes plus date; No stays | Validate date/time/mode/link; distinguish recorded details from mandatory completion fields |
| 4 Interview result | Selected/Rejected plus mandatory remarks; rejection closes | Reject unknown enums and whitespace-only remarks; retain who/when |
| 5 Offer letter | Obtained Yes plus actual file; No stays | Verify attachment belongs to this application/stage; retain new separate offer history |
| 6 Medical | Latest result Fit advances; Unfit and no reapply closes; reapply may repeat | Persist every attempt/date/result and create/close a dedicated re-medical task; do not overwrite failed attempts in one draft |
| 7 Passport | Original collected Yes; profile copy prefilled/replaceable, collection date/custodian recorded | Complete profile-file prefill and reference handling; validate actual custodian; decide extra blockers explicitly |
| 8 Advance payment | Collected Yes, amount and receipt; No stays | Positive/valid monetary input, real receipt, date/mode/number validation; distinguish number/upload completion policy precisely; currency-aware reporting |
| 9 Visa arrival / Google review | Visa arrived Yes advances; Google review is recorded, not blocking | Keep Google review nonblocking; validate date/file evidence and separate visa history |
| 10 Visa stamping | Done Yes plus actual stamped copy; No stays | Validate real stamped attachment, date and history |
| 11 Air ticket | Ticket copy required; completion deploys, no next task | Validate ticket ownership and recorded booking/travel data; reconcile stricter booking/date blockers; lock order deployment/fill updates |

The PRD page 8 caption says “3 exits,” but its text and tables specify stages **1, 2, 4 and 6**. Use those four explicit exits. The current modal is a tabbed stage editor with future stages visible but disabled; the PRD requests one scrolling page with completed collapsed sections and future sections hidden. Functional stages and layout compliance are separate acceptance items.

Admin reopening must work for any completed stage as the PRD states, not only a closed application. Define downstream invalidation, deployment/order count adjustments, history retention and new task/SLA handling before implementing the button.

## What to reuse from the reference

| Reference pattern | Adopt in this CRM | Avoid copying |
| --- | --- | --- |
| Candidate upload service and original storage descriptor | Shared intake service, explicit saved/error states, immutable source files, cleanup/recovery | MongoDB/GridFS schema as a second database; requiring configured OCR for every document upload. The reference permits manual entry without documents |
| Identity and source receipt handling | Normalized phone/email/passport, human correction preservation, explicit source links | Nationality/desk/country assumptions that are absent from PRD |
| OCR checkpoints/provider jobs | Resume accepted jobs, persist extraction before candidate writes, retain good sibling results | Resubmit-on-timeout behavior or swallowed persistence failures |
| Candidate profile/edit/review interactions | Editable factual fields, inspection evidence, original document access, meaningful review states | Treating an arbitrary industry substring as a verified candidate category |
| Search and matching controls | Combined filters, result counts, pagination reset and visible match reasons | Opaque heuristic scores, “engineer means IT,” or unreviewed aliases |
| Communication history | Source messages, attachments and candidate-linked history | Authentication tokens in download URLs; keep current session-cookie scheme |
| Staff/user administration | Operational controls for leave/assignment/deactivation | Finance roles, granular custom permissions and payroll administration without a separate requirement |
| Notifications and diagnostics | Persisted recipient events, observable worker errors and recovery actions | Using UI animations/config flags as proof that a worker or scheduler runs |
| Job management screens | Useful list/detail/edit interaction patterns | Marking an order filled by submitted candidates or locking a placement at offer acceptance |

Several reference screens update browser caches after catching API errors; the Candidates screen's Export CSV button has no handler. Adopt the supported server interaction and error-state design, not optimistic success that conceals a failed write. Mounted source, unmounted examples and preview fixtures are separated in the reference appendix.

## Proposed integration order

These are bounded work packages for the next implementation cycle. Each package needs API, database rules, screen controls and verification together.

### 1. Correct the existing contracts before expanding automation

- Enforce owner/supervisor checks on calls, remarks, candidate corrections and document writes; keep all-candidate Data Bank reads.
- Add typed per-stage schemas, actual attachment lookups, nonblank remarks, valid enums/dates/amounts and appropriate foreign-key checks.
- Serialize candidate/application/order updates with row locks or atomic conditional updates; translate expected conflicts into actionable 409 responses.
- Fix report scope, stage-2 pool status, no-eligible manual assignment and partial Deactivate behavior.
- Cover every write with actor/time/entity audit and preserve historical owner attribution.

Acceptance: forged attachment, other-owner mutation, invalid enum/amount/date, simultaneous save/complete/map/deploy and refresh-after-save failure all have deterministic outcomes without losing legitimate data.

### 2. Finish user administration and the employer/order model

- Implement Admin user create/edit/active/leave/auto-assign controls, password reset, force logout and audit history.
- Deactivation must atomically reassign the selected candidates/open tasks or clearly leave the whole operation unapplied; reject invalid target users.
- Decide whether a Client is the employer as in PRD or whether the current person → company extension remains. Migrate existing links carefully if changing it.
- Complete employer/designation/order edit/status controls, validated values, reference generation and agreement upload recovery.

Acceptance: an Admin can configure a real team and an employer/order from the UI; flags persist after restart; deactivated sessions are rejected; no reference collisions or half-applied reassignments occur.

### 3. Unify candidate intake, correction and skill search

- One service should handle manual/email/WhatsApp identity, empty-field enrichment, owner preservation and provenance.
- Add the required merge/cancel dialog and matching endpoint; conflicting identities need a review decision, not silent overwrites.
- Add candidate edit/review acceptance with persisted corrections; preserve extraction evidence beside recruiter changes.
- Make manual original upload/OCR durable with explicit queued/processing/review/complete/error states where needed.
- Add paginated server candidate search and structured filters; populate/use the existing PostgreSQL search vector. Retain normalized saved-skill matching and explain any later aliases/category rules.

Acceptance: corrected skills survive rescans/restarts and immediately affect Data Bank/order search; duplicate deliveries enrich the same candidate without a second rotation/task; missing identity has a usable review route.

### 4. Complete the application and eleven-stage workflow

- Atomic map/unmap/remap with one active application, correct candidate status and task cancellation.
- PRD scrolling modal, complete header, completed/read-only history, hidden future stages.
- Complete all eleven validation contracts, file associations and version history.
- Repeating medical attempts and follow-up tasks; Admin reasoned reopen with defined downstream effects.
- Deployment-based order fill logic, including concurrent stage-11 completion and reopened deployment.

Acceptance: one candidate can travel from stage 1 through 11, a negative branch at each of 1/2/4/6 returns them correctly to the pool, and repeated medical/reopen/remap leave a coherent history.

### 5. Add the task, daily-update and notification scheduler

- Persist explicit task deadlines/escalation events and use the agency timezone for daily boundaries.
- Run escalation every 15 minutes after 48 continuous hours, optionally repeat at the configured 24-hour interval; never pause on waiting.
- Create re-medical tasks, preserve clocks across reassignment, provide Admin-only explicit clock reset with audit.
- End-of-day grouped missing-owner-remark events, completion-day exemption and performance history.
- To-do due reminders; manual and automated assignment notifications; recipient read/action APIs and bell links.

Acceptance: time-controlled tests prove events exactly at boundaries, retry idempotency, no duplicate reminders, clock preservation and correct Admin/Manager/owner recipients.

### 6. Complete communications and storage

- Gmail/Microsoft OAuth, IMAP/SMTP account lifecycle, remove/disconnect and per-account health.
- Inbox attachment preview/download, server pagination/search, read state and originating-account replies with sent/failed/unknown outcome tracking.
- WhatsApp Business verified webhook, document/image retrieval, sender-number provenance and common intake queue.
- Review/resolve/retry workflows for bad pages, missing fields, conflicting identities and multiple candidates in one source bundle; make batch/sibling behavior explicit.
- Object-storage adapter, immutable versions, migration/checksum verification, orphan reconciliation and backup/restore.
- Separate mailbox receive cadence from long extraction execution as volume grows; verify the two-minute requirement under slow OCR.

Acceptance: real test mailboxes/new messages and a test WhatsApp channel create or enrich one candidate with originals intact; provider restarts, duplicate receipts, account isolation and storage outage recover without lost data.

### 7. Complete dashboards, all reports and exports

- Backend aggregates for pipeline/drop-off, staff performance, source/account, order status, rejection reasons and advance collections.
- Every report applies date/client/order/designation/staff filters consistently; Staff see only authorized own metrics.
- Persist historical ownership, completion, escalation and missed-update events needed for truthful metrics.
- Complete Admin Data Bank Excel and report Excel/PDF exports; values and scope must match the visible filtered result.
- Align Dashboard today/source/task/stage/order cards and drill-downs with the same definitions; distinguish candidate intake from application events.

Acceptance: a fixed fixture with reassignment, rejection, medical repeat, deployment, two source accounts and multiple currencies produces reconciled figures in UI/API/export for all three roles.

### 8. Production acceptance and operations

- Run full PostgreSQL integration/concurrency tests and real channel/provider acceptance against isolated test resources.
- Verify API/worker startup, restart recovery, persistent volumes/object store, migration compatibility, session revocation and rollback/restore.
- Test all routes, drawers, forms, file versions, empty/loading/error states and mobile layouts as Admin, Manager and Staff.
- Measure pagination/search/large-file/large-inbox behavior; add monitoring for stalled jobs, missing originals and delayed schedules.

Acceptance: the PRD flow is demonstrable from source arrival to deployment and report, with an evidence ledger marking each scenario passed or explicitly unresolved. A build or a screen count alone is not v1 acceptance.

## Decisions to settle before the broader implementation

1. **Employer model:** follow the PRD's employer-as-Client directly, or retain person → company as an explicit extension with clear labels/navigation.
2. **Stage mandatory fields:** use the PRD completion columns; any stricter blocker for forwarding, interview mode, passport copy/date or ticket booking/date must be deliberately agreed.
3. **Skill classification:** exact recorded skills are now supported. A category taxonomy, synonyms, certification equivalences or ranked matching needs explicit agency rules and reviewable evidence.
4. **Legacy mail/backfill and multi-person bundles:** current connect imports new mail from the connection checkpoint. Historical import and splitting a bundle into several people require defined limits and operator review.
5. **No eligible staff and reopening:** define the visible queue/reassignment policy and downstream task/deployment consequences; never invent an eligible owner or silently discard history.
6. **Reference extras:** attendance/payroll/Sourcing/B2B/Finance permissions remain separate scope unless requested.

## Acceptance checklist for the full project

| Scenario | Required proof |
| --- | --- |
| Admin setup | Create/edit/deactivate/reset/force logout/leave/assignment flags persist and are correctly authorized |
| Staff and Manager roles | All Data Bank reads; Staff own work; Manager supervision; report/to-do/user-page boundaries enforced server-side |
| Manual candidate without OCR | All PRD fields persist, required data validated, owner/task/notification created |
| Manual OCR and correction | Mixed pages classified, errors visible, human edits preserved, skills searchable |
| Upload and recovery | Supported/unsupported/empty/oversized files, two versions, read/download, partial failure and retry, storage/DB uncertainty |
| Duplicate and concurrent intake | Phone/email/passport normalization, conflict review, merge/cancel, no duplicate owner rotation/task |
| Email | Multiple accounts, receive/auto-toggle/manual intake, checkpoint/restart/backoff, preview/reply, candidate links |
| WhatsApp | Verified messages/images/documents, sender provenance, retries and duplicate handling |
| Order matching | Every required filter, one active application, bulk partial failure, open-order rule and concurrency |
| Initial call | Called yes/no and all interest outcomes, correct task/status updates and ownership |
| Stages 1–11 | Required fields, proper exits/waiting, attachments, persisted history, owner scope and atomic transitions |
| Medical / reopen / remap | Repeat attempts/tasks, audited Admin reason, downstream consistency, new stage-1 application |
| Deployment | Last ticket deploys once; concurrent deployment gives correct filled state/count |
| SLA | 48h continuous, 15m scan, 24h configured repeat, preserved clock, audited reset, no duplicate events |
| Daily remarks | Nonblank owner updates, immutable history, timezone/day boundary, grouped alerts, completed-day exemption |
| To-do / bell | Private CRUD, record links, due reminder, recipient read state and actionable links |
| Reports / exports | All six reports, all common filters, historical attribution, Staff scope, Excel/PDF parity |
| Operations / UI | Every route/dialog and mobile state, persistence across restarts, provider/storage outage, restore and performance |

## Verification record

| Check | Result | Meaning and limit |
| --- | --- | --- |
| Backend regression suite | **365 passed, 44 skipped** | Skipped cases require an explicitly configured disposable PostgreSQL test database; they were not run against the configured CRM database. Existing dependency deprecations and a test-cache permission warning were reported |
| Focused file suite | **27 passed** | Synthetic sessions/temp files exercise validation, versions, audit/failure cleanup, uncertain commits, reads/downloads and application access checks; this is a subset of backend coverage |
| Frontend regression suite | **52 passed** | Includes normalized skills, candidate matching, file validation/version isolation, OCR merging, API failures and refresh/session coordination |
| Production build | **Passed**, 2,726 modules | TypeScript build plus Vite production bundle |
| All-role route checks | **45 passed**, plus logged-out login | Fifteen workspace routes for each of Admin, Manager and Staff; checks include expected route gates. Rendering is not proof that every mutation is implemented |
| Stage editors | **11 rendered** | Each current stage editor opens; future tabs are visibly disabled, confirming the documented PRD layout difference. Full backend stage transitions were not simulated as successful acceptance |
| Targeted browser journeys | **Passed** | ALL/ANY/no-skill filtering, column-filter preservation, combined order filters, mapping success followed by failed refresh, current document download links, save/refresh/upload failure and retry without duplicate candidate creation, and new document visible on the saved profile |
| Mobile checks | **Passed** at 390 px | Add Candidate, Data Bank and candidate profile have no page-level horizontal overflow; Data Bank retains its horizontally scrollable table |
| Browser runtime errors | **None** in the passing run | Fresh isolated Chrome profile; all API responses synthetic; external requests blocked |
| Patch whitespace | **Passed** | `git diff --check`; line-ending normalization notices are not whitespace failures |

The browser report is [artifacts/crm-flow-browser/report.json](../artifacts/crm-flow-browser/report.json), with the replay script and screenshots in the same directory. These generated artifacts are intentionally ignored by Git; the source audit appendices remain reviewable repository documentation. No production deployment or database migration was performed.

Still unverified: the reference's runtime, live PostgreSQL concurrency/migrations/restore, real mailbox arrivals and account OAuth, paid OCR success/restart, WhatsApp delivery and every cross-role production mutation. Those are explicit next-phase acceptance requirements, not passing results inferred from this review.
