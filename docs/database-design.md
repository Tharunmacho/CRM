# CRM database and document storage

The CRM uses PostgreSQL, SQLAlchemy models and an ordered Alembic migration chain. The current head is `0014_attendance_ordering`; it contains **35 application tables** plus `alembic_version`. File bytes live in private local/shared-volume or S3-compatible storage. No MongoDB collections are required.

## Find the right table

| Domain | Table | Meaning and important relationships |
| --- | --- | --- |
| Sign-in | `auth_users` | CRM accounts: name, sign-in email, role, activation, assignment eligibility and leave. Passwords are one-way Argon2id hashes. |
| Sign-in | `auth_sessions` | Revocable sign-in sessions linked to `auth_users`; expiry is UTC. |
| Recruitment | `crm_client_contacts` | Agency contact people. A contact can represent several employer companies. |
| Recruitment | `crm_employer_companies` | Employer details, country, industry, contact and address; belongs to a client contact. Agreements use `document_attachments`. |
| Recruitment | `crm_job_designations` | Job titles, required skills and minimum experience. |
| Recruitment | `crm_job_orders` | Employer recruitment orders: designation, openings, salary/currency, benefits, dates and status. |
| Recruitment | `crm_candidates` | Current candidate profile, skills, source, current recruiter, intake recruiter, manual creator, review state and searchable resume text. |
| Recruitment | `crm_applications` | A candidate's placement against an order; current stage, status, draft, optimistic lock version and mapping-time recruiter. |
| Recruitment | `crm_application_stages` | Completed stage attempts, outcomes, fields, referenced documents, completing actor and responsible recruiter. Reopened attempts remain history. |
| Assignment | `workflow_assignment_cursor` | Singleton round-robin cursor, serialized with candidate creation/transfers and account eligibility changes. |
| Workflow | `workflow_tasks` | Initial call, stage or re-medical work; current owner, opening/closing times, persisted 48-hour deadline and escalation state. |
| Workflow | `workflow_task_remarks` | Permanent task notes with author and timestamp. Only the responsible recruiter's daily update satisfies their obligation. |
| Workflow | `workflow_task_events` | Durable escalation, reminder, SLA-reset, missed-update and assignment history; unique event key prevents repeated delivery. |
| Workflow | `workflow_settings` | Singleton monitoring settings and daily/periodic worker checkpoints; repeats disabled by default or every 24 hours. |
| Personal | `personal_todos` | Private Admin/Manager reminders, due time, completion and delivered-reminder timestamp. |
| Email | `email_accounts` | Zoho mailbox configuration, encrypted credentials, cursor, auto-ingest, connection state and worker heartbeats. Legacy provider records remain historical; new connections and live operations are Zoho-only. |
| Email | `email_oauth_states` | Legacy authorization handshakes retained for migration/history. New OAuth connections are disabled by the Zoho-only policy. |
| Email | `email_messages` | Mirrored messages, folder and UID/UIDVALIDITY identity, recipients, thread headers, read/star state, remote operation reconciliation and optional linked candidate. |
| Email | `email_attachments` | Original message file metadata, immutable object key and SHA-256. |
| Email | `email_ingestion_jobs` | Durable attachment inspection, OCR submission IDs/results, attempts, review state and candidate outcome. |
| Email | `email_replies` | Explicit human-requested replies from the originating mailbox, idempotency key and delivery outcome. Unknown outcomes are never automatically resent. |
| Email | `email_drafts` | Actor-owned compose/reply/reply-all/forward/edit-draft state; recipients, body, version, remote draft identity and synchronization outcome. |
| Email | `email_draft_attachments` | Immutable draft file metadata, storage key, size and SHA-256; linked to its draft. |
| Email | `email_send_attempts` | Immutable message/attachment snapshot, unique request key, delivery outcome and refused recipients. Unknown or partial sends require review. |
| Staff | `staff_attendance_sessions` | Explicit check-in/out, idempotency keys and Admin correction reason/actor. One open session per staff account. |
| Staff | `staff_presence_intervals` | Online/idle intervals per browser client and login session, server timestamps and heartbeat expiry. Offline time is derived within checked-in time. |
| Staff | `staff_attendance_requests` | Accepted clock request identifiers linked to their session; retries from another tab cannot accidentally open a new shift after checkout. |
| Staff | `staff_presence_clients` | Per-browser login binding and last accepted sequence; delayed heartbeat/leave requests cannot reverse newer activity state. |
| WhatsApp | `whatsapp_channels` | Configured Business phone, intake setting and reception/worker health. Credentials stay in server secrets. |
| WhatsApp | `whatsapp_messages` | Deduplicated signed webhook receipts, sender provenance, original media, extraction checkpoints, review and linked candidate. |
| Documents | `document_attachments` | Versioned candidate, company and application documents. Owner is `entity_type` + `entity_id`; the file service validates owner/access. |
| Documents | `document_extraction_jobs` | Owner-scoped manual uploads grouped by an intake session, original bytes, OCR state, saved human form edits and idempotent candidate attachment. |
| Intake | `ingestion_failures` | Retained failed-intake records and resolution details; inspect alongside each channel's durable job ledger. |
| System | `system_notifications` | Recipient-owned notification bell items and persisted read state. |
| System | `system_audit_log` | Recorded actor, action, affected entity, timestamp and structured context. User Management reads this history. |

The contact-to-company hierarchy preserves this project's existing business records. The PRD calls the employer a client; employer fields, agreements and orders live on Companies. This terminology difference is explicit rather than silently merging existing contacts/employers.

## Relationships and ownership

`client contact -> employer company -> job order -> application -> candidate` is the placement chain. Applications own stage history and stage tasks. Candidate ownership is mutable, while `intake_owner_id`, application mapping owner, stage completion owner and task events preserve historical attribution. Reassignment moves open tasks without restarting their deadlines. Assignment events reconstruct the responsible recruiter for a past agency day, even when the worker catches up after downtime.

The `0008` backfill uses the existing current recruiter where older records contain no historical owner. That legacy attribution is inferred; migrations cannot recover unrecorded past transfers. New events retain actual ownership at the time of the action.

All timestamps are UTC. `AGENCY_TIMEZONE` (default `Asia/Kolkata`) defines dashboard/report day boundaries and daily-update checks. Store date-only employment fields as dates. Salary and advances keep their currency; reports do not add unlike currencies into one misleading total.

Database guarantees include unique sign-in email/references, one active placement per candidate, unique stage attempts, unique document versions, unique provider receipts, unique OCR sources and unique workflow event keys. Candidate and order references use sequences, not counts. Application commands validate lock versions under row locks. Stage completion, next task, closure/deployment and audit commit together.

Candidate identity matching uses normalized phone **OR** email **OR** passport. Different matches require human review. Empty values never count as identity. Skills use normalized exact matching with ALL/ANY controls. `crm_refresh_resume_search` maintains the GIN-indexed resume search vector, including a migration backfill.

## Documents and recovery

Original documents are retained separately from selected resume/passport/Aadhaar PDFs. Uploads are immutable: replacing a slot creates another version. Manual document jobs persist the original before the user requests OCR, retain provider job IDs and recover the saved form after a reload. Human-edited values take precedence over extraction. Candidate attachment is idempotent, including retry after a committed candidate save.

Slow OCR/provider work runs outside ordinary record-change transactions. Email reception, email extraction, manual document extraction, WhatsApp extraction, presence expiry and task monitoring are separately supervised processes. Durable PostgreSQL checkpoints survive restart; locks prevent duplicate workers from processing the same queue. User-facing pending, failed and needs-review states distinguish an accepted remote job from a confirmed failure.

Local storage requires a persistent `STORAGE_DIR` shared by the API and workers. Mailbox state binds to `.email-storage-id`; copy the entire uploads volume including that marker when moving a local installation. S3 objects use explicit `s3://bucket/prefix/key` references; mailbox identity binds to endpoint/bucket/prefix rather than a particular local directory. Changing `STORAGE_BACKEND` does not migrate old objects automatically.

The authenticated file endpoint serves local and S3 documents with private/no-store responses. Originals and historical versions remain available; automatic orphan deletion is deliberately absent. See [storage and release operations](operations.md) for checksum audit, migration and backup/restore procedures, and [mail connections](email-connections.md) for encrypted mailbox credential management.

## Mail and staff persistence

Draft edits use optimistic versions to protect against another tab overwriting newer work. A send attempt stores its own recipient/body/attachment snapshot before SMTP dispatch. A repeated request key refers to that same attempt. Partial or uncertain delivery is shown for review and is never automatically resent. Folder and message actions retain the remote operation until synchronization confirms the result. Disconnecting a mailbox preserves existing messages, documents and delivery history.

Attendance is an explicit clock: hiding the tab or logging out ends activity tracking, not the checked-in shift. The server timestamps check-in/out and serializes competing requests per staff account. Presence reports union overlapping browser intervals, so multiple tabs cannot multiply time. The report splits time at agency-day boundaries and shows online, idle and offline durations only during checked-in periods. These are browser activity estimates, not proof of work. See [attendance policy](attendance.md).

## Inspect records in pgAdmin

Refresh **Schemas > public > Tables** after applying migrations. The domain prefixes group related tables. Table comments explain their purpose. `auth_users` holds CRM login accounts; `email_accounts` holds external mailboxes.

```sql
SELECT id, name, email, role, active, receives_auto_assign, on_leave,
       (password_hash IS NOT NULL AND password_hash <> '') AS password_set
FROM auth_users ORDER BY role, email;

SELECT c.ref, c.name, u.name AS recruiter, c.status, c.source
FROM crm_candidates c LEFT JOIN auth_users u ON u.id = c.owner_id
ORDER BY c.created_at DESC;

SELECT t.title, c.ref, u.name AS recruiter, t.due_at,
       t.escalated, t.completed_at, t.cancelled_at
FROM workflow_tasks t
JOIN crm_candidates c ON c.id = t.candidate_id
JOIN auth_users u ON u.id = t.owner_id
ORDER BY t.created_at DESC;

SELECT m.subject, j.status, j.attempts, j.last_error, j.candidate_id, j.updated_at
FROM email_ingestion_jobs j JOIN email_messages m ON m.id = j.message_id
ORDER BY j.updated_at DESC;

SELECT d.id, d.subject, d.status, a.status AS delivery_status, a.created_at
FROM email_drafts d LEFT JOIN email_send_attempts a ON a.draft_id = d.id
ORDER BY d.updated_at DESC;

SELECT u.name, s.checked_in_at, s.checked_out_at, s.correction_reason
FROM staff_attendance_sessions s JOIN auth_users u ON u.id = s.user_id
ORDER BY s.checked_in_at DESC;

SELECT version_num FROM alembic_version;
```

Avoid exporting password hashes, session IDs, OAuth secrets or raw identity-document OCR into support logs. Provision/reset accounts through User Management or the account CLI. Startup creates only required system state, never default login accounts. Deactivation/password changes revoke sessions; the last active Admin cannot be removed.

## Migration policy

The chain is `0001` through `0014`. Revisions `0001` through `0007` are retained; this integration adds:

- `0008_workflow_history`: ownership snapshots, persisted SLA timing, task history/settings and personal reminder delivery state.
- `0009_communication_channels`: encrypted OAuth handshake/reply metadata, WhatsApp receipts, separate mail-worker heartbeat, atomic reference sequences and maintained resume full-text search.
- `0010_manual_document_jobs`: recoverable manual uploads/OCR/drafts and task-assignment events.
- `0011_company_whatsapp`: distinct employer WhatsApp contact without rewriting client/contact relationships.
- `0012_zoho_mailbox`: folder/UID state, recoverable drafts, immutable attachments and explicit send-attempt history.
- `0013_staff_attendance`: staff check-in/out, corrections and browser activity intervals with partial uniqueness indexes.
- `0014_attendance_ordering`: durable clock-request ledger and browser-presence sequencing, including earlier request-key backfill.

Migrations and model parity, fresh upgrade, earlier-record preservation and rollback/upgrade are checked against disposable PostgreSQL. This is not evidence that a production migration has been run. Apply the schema once during a coordinated release with the matching API; stop older writers first. Do not rewrite an applied migration. A future change must add a new revision.

Downgrading removes the new feature ledgers/settings. `0014` removes clock-request and presence-ordering ledgers; `0013` removes attendance/activity history; `0012` removes drafts/send attempts and mailbox action state; `0011` removes employer WhatsApp. `0010` downgrade removes manual extraction jobs and assignment events; `0009` removes channel/OAuth/reply data; `0008` removes workflow history/snapshots. Preserve a database plus document backup before rollback. Prefer a forward correction after real intake has begun. No downgrade deletes stored document bytes, and no migration recreates default accounts.
