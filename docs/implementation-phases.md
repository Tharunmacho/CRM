# Recruitment CRM implementation phases

Source: [Recruitment CRM — Developer Specification](requirements/recruitment-crm-developer-specification.pdf), 14 October 2026, 12 pages. All 12 pages, including the stage diagram, were reviewed again on 20 October 2026 against the current source and the [architecture](architecture.md). All mandatory PRD behavior remains v1 scope; phases are delivery checkpoints, not permission to omit later modules.

The [email/Gmail automation reference review](reference-architecture-review.md) informs the implementation patterns below. Reference-specific roles, databases, assignment policies and HR features do not expand or override this PRD.

**There are nine numbered phases: Phase 0 is the foundation, followed by Phases 1–8.** Phase 0 is delivered; the account-persistence correction is also delivered, but Phase 1 as a whole remains pending. The detailed work below is a plan for review before phase implementation starts. Existing connected functionality is retained and extended within each phase rather than rebuilt indiscriminately. Check the [requirements matrix](requirements-matrix.md) for the distinction between an implemented path, a partial path and a UI/schema placeholder.

Physical table references below use the names introduced by `0006_domain_table_names`; verify the database revision before running queries. This separate naming/description revision preserves existing records and API contracts, with reversible renames and a coordinated backend restart. It does not complete Phase 1 or reconcile the contact/employer model assigned to Phase 2. The [database design](database-design.md) owns the table dictionary, pgAdmin instructions and release/rollback procedure.

| Phase | Deliverable | Dependency | Current position |
| --- | --- | --- | --- |
| 0 | Organized frontend/backend workspace, dependency ownership and source-based documentation | Existing repository | Structure delivered in this change; validation recorded in root handoff |
| 1 | Safe identity lifecycle, permissions, audit and concurrency foundation | 0 | Database-backed account correction delivered; remaining work is planned below |
| 2 | PRD-aligned employer/order/candidate data and assignment | 1 | Core create flows exist; gaps and policy differences remain |
| 3 | Reliable matching, complete 11-stage workflow and task transitions | 2 | Core stage flow exists; important missing branches remain |
| 4 | Durable worker, notification, escalation and daily-update engine | 1, 3 | Models/UI exist; runtime automation is missing |
| 5 | Production document ingestion and unified inbox | 2, 4 | Manual OCR exists; provider ingestion is missing |
| 6 | User administration and complete private to-dos | 1, 2, 4 | To-do CRUD partly connected; user admin is a placeholder |
| 7 | Correct dashboard, six reports, filters and exports | 2–6 | Charts exist; scope/filter/export work remains |
| 8 | End-to-end validation, migration rehearsal and release readiness | 1–7 | Not established by current OCR tests |

## Phase 0 — Repository foundation

**Purpose:** establish a place for every part of the project before extending business behavior.

Deliver frontend source/assets/tooling under `frontend/`; backend API/core/database/domain/integration/service modules under `backend/`; requirements and design documents under `docs/`. Root npm scripts orchestrate the frontend workspace and use one lockfile. Preserve existing runtime/build dependencies, source files, public assets, database migrations and tests. Update imports, build contexts, static output paths and development API routing.

Acceptance:

- Frontend screens/components are no longer scattered at repository root; backend-only code remains under `backend/`.
- Existing imports and frontend type checking/build pass after moves; backend imports and the existing OCR tests pass after moves.
- Docker build inputs reference the new frontend path and include its public assets. Uploads use a named persistent volume, and the health probe checks database/bootstrap readiness in the JSON response. Environment examples contain development defaults or placeholders rather than live credentials.
- Architecture and phase documents reference the actual PRD and label unimplemented services clearly.
- The structural move does not claim to have delivered missing ingestion, schedulers, reports or admin actions.

Validation evidence belongs in the root handoff/README. Passing build/unit checks does not establish live PostgreSQL, browser or provider integration behavior.

## Phase 1 — Identity, access and consistency

**PRD:** roles/navigation/audit, pp. 1–2; shared candidate visibility and matching, p. 4; private to-dos, p. 5; account administration, p. 6; ownership/reassignment, p. 7; immutable files and stage completion, p. 9; task ownership and clock preservation, pp. 11–12. **Depends on:** Phase 0.

**Outcome:** the existing CRM reads real users and enforces who may perform each action, saves required audit history, and protects current writes from simultaneous requests. This is the foundation for expanding candidate and workflow features in Phases 2–3.

### Already delivered and retained

| Area | Current evidence | Limit of the completed work |
| --- | --- | --- |
| Account source | `auth_users` stores name, email, password hash, role and activation; `auth.py` verifies the saved hash and returns the saved role | The three PRD role names remain supported application roles. Assigning a role to a person comes from their database row, not their email address or a fixture. |
| Restart/login behavior | `services/bootstrap.py` no longer creates accounts, reapplies fixed passwords or reactivates disabled users; login does not invoke account bootstrap | Existing accounts are preserved. An empty database stays without accounts until an explicit creation action. |
| Operator account management | `cli.py` explicitly creates users with a selected role and a securely prompted password; password reset revokes that user's sessions and preserves activation | This is an operational command, not a completed User Management screen. |
| Sessions | Login creates a database session; request authentication reads the current user, role and activation; logout removes its session | Existing persistence tests cover role changes and deactivation. Browser behavior and migrated PostgreSQL lifecycle acceptance still need the checks below. |
| Login feedback | Password visibility toggle and field-specific email/password errors are connected to structured API responses | This completes the requested sign-in feedback; the broader permission and session acceptance below remains part of Phase 1. |
| Shared startup state | Assignment cursor initialization uses conflict-safe insertion; readiness retries after a startup database outage | This does not prove the complete assignment, reference-allocation or workflow transactions are concurrency-safe. |
| Regression coverage | Isolated account/bootstrap/auth/CLI tests accompany the existing OCR tests | SQLite auth tests do not verify PostgreSQL row locks, partial indexes, migrations or concurrent workflow writes. |

### Remaining work, in delivery order

**1.1 — Confirm the identity and session contract.** Keep the database as the only source of accounts; retain explicit CLI provisioning until Phase 6. Verify login, page refresh, session expiry, logout, reset-induced revocation and deactivation against the same API used by the frontend. Verify local-development and deployed cookie behavior. A role/status change must affect authorization on the next request, and the UI must reload the current identity rather than keeping a selected/demo user. No reset, role reassignment or user insertion occurs just because the API restarts. Relevant files: `core/security.py`, `core/deps.py`, `api/routers/auth.py`, `cli.py`, login page and shared store. The account operations on PRD p. 6 define the lifecycle; implementation details such as cookie policy are supporting engineering decisions.

**1.2 — Apply one permission policy to existing API actions and screens.** Inventory the current routes and put shared role/owner checks behind named backend helpers, then align controls and error handling in the existing screens. Preserve the PRD distinctions:

| Capability | Required behavior in the foundation | PRD |
| --- | --- | --- |
| Candidate discovery | Admin, Manager and Staff can see all Data Bank candidates and shared order/mapping summaries | pp. 2, 4 |
| Create clients/designations/orders; map/unmap | All three roles may act; do not accidentally apply the narrower stage-work ownership rule to these capabilities | p. 2 |
| Stage/call work and candidate-stage document changes | Admin/Manager can work any candidate; Staff can modify their owned work. Apply checks before changing a draft, saving a call, completing a stage or attaching/replacing a work document | pp. 2, 7, 9 |
| Daily task remarks | The task owner supplies the required remark. A supervisor's stage action does not impersonate the owner or count as the owner's daily update | p. 12 |
| Reassignment | Only Admin/Manager; validate every candidate and target, move the candidate and its open tasks together, preserve existing task clocks | pp. 2, 7, 11 |
| Personal to-dos | Only Admin/Manager, private to their owner on create/read/update/delete, including direct API requests | pp. 2, 5 |
| Reports and administrative capabilities | Establish all-team versus own-number scopes; source administration/user management/reopening remain Admin-only. Full provider/admin/report features ship in their assigned later phases | pp. 1–2, 5–6 |

Scope the current task/private-data portions of `/api/bootstrap` and subsequent reads at the server; retain the shared candidate fields needed for the all-candidate Data Bank. Return only the user fields required for identity and assignment displays. Do not rely on frontend report filters to enforce Staff scope. Keep recipient-scoped notifications intact. The full paginated Data Bank and report queries remain Phases 2 and 7.

**1.3 — Validate current write contracts before they reach a transaction.** Reject blank-after-trimming names/titles/remarks, unsupported call outcomes and enums, invalid record references, and mismatched attachment entity/slot references. Check a real candidate/application/client/company exists before an upload is attached to it. Validate the entire bulk reassignment request rather than silently skipping missing candidates and reporting the requested count. Define eligible manual targets separately from automated round-robin eligibility. Return useful validation/authorization/conflict responses to the affected form without claiming a save succeeded. Typed validation for every one of the eleven stage payloads and reconciliation of their completion fields remain Phase 3; complete form field coverage remains Phase 2.

**1.4 — Make the audit trail part of each existing successful write.** Introduce a shared audit writer and an endpoint-to-event checklist covering current record creates, draft/call edits, remarks, stage changes, reassignment and uploads, as required by PRD p. 2. Every event records actor, UTC time, action, entity type and stable entity ID; bulk changes record the affected entities and old/new owner. Save the business row and audit row in one transaction so a failure rolls both back. Include explicit CLI account changes with an honest operational-actor context, not a fabricated signed-in user. Exclude passwords, hashes, session tokens and provider secrets from audit metadata.

Keep remarks and prior stage/file history immutable (pp. 9, 12). Check existing parent deletion paths for cascading removal of that history; block a deletion that would erase protected history until the entity archive/status behavior is delivered in Phase 2. The Admin audit viewer belongs to Phase 6; this step supplies its real events.

**1.5 — Make the existing concurrent writes atomic.** This repairs current transaction boundaries without introducing the missing workflow branches:

- Replace candidate/order `COUNT(*) + 1` references with an atomic PostgreSQL allocator. Preserve existing references and seed any new allocator above the saved values; keep unique constraints as a final guard. A reference gap is acceptable, a collision is not.
- Keep cursor selection and advancement inside the candidate/initial-task transaction. Remove any remaining racing first-row creation fallback, use the already-safe startup initializer, and verify the current rotation locks under concurrent requests (p. 7). Phase 2 still owns duplicate-aware intake and the no-eligible-staff outcome.
- Make draft-save/complete/unmap coordinate on the application row or an atomic version comparison. Two requests using the same version cannot both commit; a failed/stale request must not add another stage record or next task (pp. 4, 9, 11).
- Protect existing deployment/order-status updates with the corresponding order lock so two current stage-11 completions cannot lose an update (p. 10). Over-capacity policy and the full eleven-stage acceptance remain Phase 3.
- Allocate replacement file versions atomically for an entity and slot, retain earlier bytes, and audit each accepted upload (p. 9). Handle a failed metadata commit without exposing a successful upload or overwriting another version. Keep local storage for this phase; object storage and provider recovery ship in Phase 5.

Document a consistent lock order and transaction ownership for these paths. Add only the migrations required for these invariants; do not restructure the employer hierarchy or add worker tables in this phase.

**1.6 — Integrate and demonstrate the foundation.** Update the existing login/session flow, call/remark forms, stage-save/conflict messages, upload feedback and role-dependent controls to use the corrected responses. Do not silently discard a user's edited draft after a conflict. Run authorization and concurrency checks against a separate migrated PostgreSQL test database; use isolated test accounts and synthetic records. Demonstrate permitted and denied paths in the browser for each role. Preserve the existing OCR regression suite and frontend type/build checks. Record the result and remaining gaps in the requirements matrix before marking Phase 1 complete.

### Concrete acceptance and demonstration

1. **Identity persists:** explicitly create test users, change a password/role/activation, restart the API, and show the same saved account state. Deleted users are not recreated. An empty migrated test database receives no default users.
2. **Session state is real:** login creates an `auth_sessions` row; refresh remains signed in; logout removes the current row; reset revokes only the target user's sessions; expired/deactivated/revoked access is rejected. No credential/hash/token appears in ordinary API responses or audit metadata.
3. **Permissions survive direct API calls:** Staff A can discover Staff B's candidate but cannot alter B's stage/call/document or add B's required daily remark. Admin/Manager can perform the specified candidate work; Staff cannot reassign or access another person's private to-dos. All three roles retain permitted matching/unmapping and record-creation actions.
4. **Rejected writes leave no partial result:** invalid enums/IDs/empty remarks, forbidden owners, stale versions and failed audit writes change neither business rows nor successful-action audit history. The frontend presents the corresponding error.
5. **Audit is traceable:** create, edit, call, stage, assignment and upload demonstrations produce committed `system_audit_log` rows with the acting user/time/entity ID. Earlier remarks, stage attempts and document versions remain available.
6. **Concurrency is proven in PostgreSQL:** simultaneous candidate/order creations get distinct references; competing stage completions create one transition and one next task; competing file replacements retain distinct versions and retrievable bytes; current rotation/deployment writes preserve their shared state.
7. **Existing data survives:** apply required migrations to a representative prior schema in the test environment, preserve account/candidate/order references and existing histories, and verify recovery/rollback instructions. Do not use the Dokploy production database for destructive test scenarios.

The user's review demonstration will use the CRM plus pgAdmin: query `auth_users` without exposing password hashes, see an `auth_sessions` count increase/decrease without exposing tokens, perform an allowed/denied action, inspect its `system_audit_log` event, and verify that a restart retained the saved state. Real-time persistence means successful actions are committed to PostgreSQL; pgAdmin refreshes or reruns its query to show the latest rows.

### Phase boundary and decisions

The full User Management screen, Admin account APIs, assignment/leave toggles, deactivation-and-reassignment prompt, force-logout control and per-user audit viewer remain **Phase 6** (PRD p. 6). Phase 1 establishes the enforcement and audit dependencies; current CLI creation/reset lets development proceed without moving that whole module forward. Phase 2 owns employer/client semantics, full candidate CRUD/merge/search and assignment fallbacks. Phase 3 owns missing stage branches/reopening/re-medical logic. Phases 4–5 own background automation and providers; Phase 7 owns complete report/export behavior.

Before the relevant Phase 1 write policy is finalized, record the manual-reassignment target policy: PRD p. 7 explicitly defines eligibility for **automatic** assignment but does not say whether an Admin's deliberate reassignment may override leave or the auto-assign toggle. Proposed default: active Staff may receive a deliberate reassignment; auto-assign/leave flags govern automatic rotation. This is a design assumption, not an additional PRD rule.

Do not delay the foundation for later business decisions. Resolve employer hierarchy and no-eligible-staff behavior in Phase 2; downstream reopening, completion-field differences and order capacity in Phase 3; the agency's exact end-of-day cutoff in Phase 4; OCR confidence and retry interpretation in Phase 5. The [architecture decision table](architecture.md#9-prd-reconciliation-and-explicit-design-assumptions) records the remaining proposals.

## Phase 2 — Core records and candidate ownership

**PRD:** clients/designations/orders, pp. 3–4; Add Candidate/Data Bank, p. 4; duplicate handling/assignment, pp. 6–7. **Depends on:** Phase 1.

Work:

- Resolve employer-as-client versus the existing client-person/company extension. Keep existing history and define a reversible migration if changing the hierarchy. Finish create/edit/status operations and all PRD fields for clients, designations and orders.
- Finish candidate create/edit/profile fields, document slots and resume-prefill behavior. Validate OCR data before committing user-corrected values.
- Add the incomplete-identity Needs review contract described in the database design: provisional OCR records may omit name/phone, manual entry still requires them, and first-call review validates corrections before clearing the corresponding review reasons. Keep original documents and never fabricate identity values.
- Replace the current phone+DOB duplicate policy with normalized phone OR email OR passport matching. Manual entry offers merge/cancel; automation fills empty fields and attaches files without changing the existing owner. Conflicting identity matches require review.
- Centralize strict round-robin assignment and initial-call task creation. Respect Staff role, active state, auto-assign toggle and leave flag. Define and expose the no-eligible-staff fallback.
- Complete initial-call outcomes and owner/task reassignment with stable due times and audit entries.
- Capture assignment/reassignment owner history now, so later reports do not attribute past work using only the current owner.
- Introduce paginated Data Bank search/filter APIs, including resume full text and the Not interested pool. Populate/refresh `resume_tsv` when accepted resume text changes and backfill existing rows; its current index alone does not maintain searchable content. Add missing matching filters over the same query implementation.

Acceptance:

- All PRD candidate/client/designation/order fields round-trip through UI, API and PostgreSQL; existing records survive any hierarchy migration.
- A manual duplicate can be cancelled or merged without creating a second person. Tests call the shared ingestion-facing candidate service directly to verify automated duplicate preservation of owner and nonempty verified fields; complete provider intake is accepted in Phase 5.
- Concurrent new candidates rotate strictly over eligible Staff; inactive/on-leave/opted-out users receive none. If eligibility is empty, the agreed fallback is visible and recoverable.
- A new assigned candidate has exactly one initial-call task due 48 hours after creation. Recording a valid outcome closes it and Not interested candidates remain searchable.
- Reassignment moves every open task with the candidate and does not reset the clock.
- An ingestion-facing service can save and assign an incomplete extraction as Needs review, then accept corrections on the same record. Manual creation still rejects missing required identity fields. Search finds both new and backfilled resume content.
- Staff can discover all candidates, while personal work permissions remain enforced server-side.

## Phase 3 — Applications, all 11 stages and task lifecycle

**PRD:** matching/unmapping, p. 4; stage modal, pp. 7–10; task event table, p. 11. **Depends on:** Phase 2.

Work:

- Make map/unmap operations transactional and idempotent. Keep one active application per candidate; a new mapping starts a fresh stage history and stage-1 task.
- Apply typed server-side validators for every stage and verify file ownership/existence. Reconcile existing extra completion blockers with the PRD completion column.
- Complete all negative branches: stages 1/2/4/6 close; other No responses remain at their stage with the task open. Both stage-2 negative reasons enter the specified Not interested pool.
- Record repeat medical attempts and follow-up tasks without overwriting the previous date/result. Separate the scheduled medical appointment from the default 48-hour task deadline.
- Implement Admin reopening with mandatory reason, preserved stage history and defined downstream invalidation/task/deployment behavior. The PRD does not limit reopening to closed applications.
- Make task completion/cancellation follow only the relevant workflow event. Complete stage-11 deployment and order-filled updates under concurrency control.
- Store the owner at each stage/task outcome alongside the actor and timestamp, preserving past attribution after reassignment.

Acceptance:

- A candidate without a mapping can open a profile but not a stage modal. Matching exposes stage 1, while completed sections remain collapsed/read-only and future sections are hidden.
- Each successful stage creates exactly the next task, closes the previous one, and records actor/time. Stage 11 creates no next-stage task.
- Test each of the 11 happy paths, all four exit stages, waiting outcomes and repeated medical outcomes. Uploaded offer/receipt/stamped-visa/ticket requirements cannot be bypassed with arbitrary strings.
- Unmap/negative closure cancels application tasks; remap starts a new stage 1 while preserving closed history.
- Only Admin can reopen; reason is required and audited; downstream state follows the documented policy and a newer active mapping cannot be overwritten.
- Concurrent deployments produce correct counts and Filled status. Repeated completion requests never double-count a deployment.

## Phase 4 — Worker, SLA, notifications and daily updates

**PRD:** task UI, p. 5; assignment notification, p. 7; escalation and daily updates, pp. 11–12. **Depends on:** Phases 1 and 3.

Work:

- Introduce the backend worker/queue/scheduler runtime, transaction outbox and durable retryable jobs. Pin its dependencies and add services/configuration only when executable code is present.
- Make one scheduler own polling/recovery schedules. Expose outages and retain queued work; do not run the ingestion pipeline inline in the API when the worker is unavailable. Broker failure after a committed outbox write must be recoverable.
- Persist due times and audit admin-only SLA resets separately from original task creation time.
- Implement the 15-minute overdue scan, one initial escalation event, configurable 24-hour reminder, Admin/Manager notifications and reassignment from the notification.
- Implement owner-only nonempty append-only task remarks, the Tasks and stage-modal entry boxes, history and Not updated today indicator.
- Configure agency timezone/end-of-day cutoff. Persist missing-update events, group notices by Staff, and exclude tasks completed on that day.
- Capture task owner snapshots on escalation and missed-update events when they occur; retain due/reset/reassignment history for later reporting.
- Complete recipient-scoped notification listing/read state and assignment notices. Use the same notification service for later ingestion health and to-do reminders.

Acceptance:

- With a controlled clock, a task is not escalated at exactly 48 hours and is found by the next 15-minute scan after the deadline; client/embassy waiting never pauses the clock.
- Repeat scheduler execution and worker redelivery do not duplicate escalation events, notifications or missed-update counts.
- Admin/Manager receive the required escalation detail and can reassign; only an explicit Admin reset changes due time. Owner tasks show overdue status.
- One owner remark during the configured local day satisfies that day's requirement; another user's remark does not. Remarks cannot be edited/deleted.
- End-of-day alerts group missing remarks by staff and omit tasks completed that day; historical missed-update counts remain reportable.
- Stopping/restarting the worker or temporarily losing Redis does not lose a committed job. Operators can see worker/scheduler health.

## Phase 5 — Documents, inbox and automatic ingestion

**PRD:** Inbox, p. 3; automation, pp. 6–7; version history, p. 9. **Depends on:** Phases 2 and 4.

Work:

- Implement the object-storage adapter, versioned document writes and source provenance. Keep development local storage supported behind the same interface.
- Connect Gmail OAuth, Microsoft 365 OAuth and IMAP/SMTP with admin-only lifecycle operations, encrypted credentials, refresh handling and health timestamps. No artificial per-account count limit.
- Implement unified account-filtered/searchable mail, unread state, attachment indicators/preview, reply from the receiving account, auto-ingest toggles, manual attachment ingestion and candidate badges/links.
- Implement verified WhatsApp Business webhook reception for documents/images and persist sender WhatsApp number. Protect receipt handling against duplicate deliveries.
- Support PDF/DOC/DOCX/JPG/PNG, content hashes and provider-message identity, worker-based Veriis calls, passport classification, duplicate reconciliation and Needs review outcomes.
- Use per-account source receipts and attachment/mode job state with persisted provider job IDs and stable submission keys. Keep unread state separate from intake checkpoints, include IMAP folder/UIDVALIDITY in receipt identity, and reconcile submitted jobs before retrying extraction.
- Share the candidate contract across all channels and test persisted fields/provenance. Manual corrections and existing owners must survive later OCR results; one attachment's failure must not discard successful siblings.
- Implement the agreed three-retry policy with backoff and admin failure/retry views. Feed source sync/failure health into the dashboard.
- Optional acknowledgement messages can be a separate toggle after mandatory intake is reliable; they do not block v1 acceptance.

Acceptance:

- A supported resume from each provider is stored, parsed, saved, assigned and given its first task without an operator action. HTTP reception remains responsive while OCR is slow.
- Auto-ingest off prevents automatic processing for that account; the manual button still works and shares deduplication behavior.
- Redelivering a webhook/email or processing the same file concurrently does not create extra candidates/tasks; an existing person's new resume preserves ownership and verified fields.
- A crash after provider submission resumes the known job; a broker outage after DB commit replays pending work. Identical message IDs in different accounts stay distinct, manually reading mail does not skip intake, and partial attachment failures retry only the affected work.
- Passport input attaches to the passport slot; missing name/phone or low confidence produces visible review state without dropping the original file.
- OCR failure reaches the failure list after the configured attempts; a manual retry is safe and traceable.
- Reply uses the originating account. Disconnecting or expired credentials are visible and do not expose secrets in responses/logs.
- Existing file versions remain retrievable after replacement and object-store restore is rehearsed before release.

## Phase 6 — User administration and personal planning

**PRD:** roles, pp. 1–2; To-do List, p. 5; User Management, p. 6. **Depends on:** Phases 1, 2 and 4. This may run alongside Phase 5 once those dependencies are stable.

Work:

- Replace user-admin placeholders with Admin-only create/edit/deactivate, eligibility/leave toggles, password reset, force logout and audit views backed by the API.
- Deactivation prompts for reassignment of candidates/open tasks, then applies the agreed reassignment and session revocation behavior transactionally. Prevent automatic bootstrap from undoing it.
- Finish private Admin/Manager to-do fields, Today/Upcoming/Overdue/Done views, optional entity links and due-time bell reminders; retain manual checkbox completion.
- Preserve a clear distinction between manually completed personal to-dos and system-closed recruitment tasks.

Acceptance:

- Staff and Managers cannot administer users through direct API requests; Admin changes persist across restart.
- Auto-assignment immediately respects updated eligibility/leave/deactivation states. Reassignment covers the user's candidates and all open tasks.
- Password reset and force logout invalidate access according to the documented session policy.
- User audit views show real recorded events, not a fixture array.
- Admin/Manager to-dos are private, remind once at the configured due time, show overdue state, and appear in their Dashboard card. Staff has no to-do access.

## Phase 7 — Dashboard, reports, search exports

**PRD:** dashboard, pp. 2–3; Data Bank export, p. 4; reports, pp. 5–6. **Depends on:** Phases 2–6 so source and workflow events exist.

Work:

- Replace browser-only aggregates with scoped server queries. Staff reports expose own numbers; Admin/Manager reports expose all staff. Dashboard defaults to the logged-in user; Admin gets team/staff filtering.
- Complete today ingestion-by-channel, open/due/overdue/escalated task cards, clickable 11-stage pipeline, order counts, task ordering, private to-dos and admin ingestion-health sections.
- Implement pipeline/drop-off, staff performance, per-source/account, order status, rejection reason/stage, and advance-collection reports.
- Apply date range, client, order, designation and staff filters consistently; document each metric's event/date semantics. Include missed daily updates, average hours per stage and historical escalations.
- Read ownership and outcome history captured in Phases 2–4, rather than reconstructing past performance from today's owner. Clearly label any historical period for which older records lack that evidence.
- Implement Admin-only Data Bank Excel and scoped report Excel/PDF exports. Downloads must match filters, row access and currency grouping shown on screen.

Acceptance:

- Fixture data with known outcomes produces independently checked totals for all six reports and dashboard cards.
- Every report respects every applicable filter; a Staff user's direct API/export request cannot return another staff member's performance data.
- Stage drill-down counts match candidate lists, task counts match the task query, and order selected/deployed counts match recorded stage/application history.
- Excel/PDF downloads contain real filtered data and valid document formats; export buttons have observable behavior.
- All Data Bank columns support the required filters and full-text resume search remains responsive with a representative dataset.

## Phase 8 — Release verification and operations

**PRD:** complete v1 chain, p. 1; all module acceptance criteria above. **Depends on:** Phases 1–7.

Run end-to-end scenarios using a migrated PostgreSQL test environment and provider test accounts: manual candidate → owner → initial call → order match → all 11 stages → deployment; rejection/unmap/remap; repeated medical; stage reopen; provider resume → OCR retry → duplicate reconciliation; overdue escalation → reassignment → daily remark; user deactivation; private to-do reminder; report/export scope.

Acceptance:

- Database migrations are tested from an empty database and a representative prior schema. Back up database and objects, restore them and verify document references/history.
- Rehearse release and rollback with compatible API/worker versions, explicit migrations, production secrets and stable storage volumes.
- Validate worker crashes/retries, duplicate provider events, simultaneous assignment/transitions and no-eligible-staff recovery.
- Verify permissions with all three roles through browser flows and direct API tests. Check keyboard navigation, validation messages and file previews on supported screen sizes.
- Monitor source sync age, queue backlog, OCR failures, scheduler execution, API readiness and failed notifications; document recovery procedures.
- Mark each matrix requirement accepted only after its behavior is demonstrated. The existing 41 OCR tests are valuable regression coverage but are not an end-to-end release gate by themselves.

## Delivery rule for every phase

Each phase ships its vertical slice: UI, validated API, transaction/service behavior, database migration if needed, permissions, audit, required dependency/configuration changes, and meaningful verification. Update this plan and the requirements matrix with the result. Keep an unimplemented button or table explicitly marked as incomplete until the complete behavior works.
