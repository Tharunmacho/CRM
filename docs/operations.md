# Release, provider setup and document recovery

This guide describes the implemented runtime. Local tests do not mean production was migrated or an external account was authorized. Keep credentials in the deployment secret store, not source control or chat.

## Run the complete CRM

After applying migrations, run `python -m app.runtime` from `backend`. The supervisor starts the API, task-monitoring worker, presence-expiry worker and manual document worker. It adds the email receiver plus independent email extraction worker when `EMAIL_INGESTION_ENABLED=true`, and the WhatsApp worker when `WHATSAPP_ENABLED=true`. Any child exit stops the group so the container manager can restart a consistent runtime. Nixpacks and Docker both invoke this supervisor; email being disabled must not disable manual OCR or task monitoring.

For local development, run `npm run dev:backend` and `npm run dev:frontend` from the repository root in separate terminals. The backend command runs the complete supervisor with API reload on port 8000; restart it after worker changes. The supervisor checks the database revision before launching any process and refuses an outdated schema without migrating it. Running only `uvicorn app.main:app` serves requests but does not process background work. PostgreSQL is the durable queue; Redis is optional and is not a substitute for the database.

Task escalation and daily-update checks run every 15 minutes. Personal to-do reminders have an independent 30-second scan in the same worker, so a due reminder does not wait for the next task scan. The notification and delivery marker commit together; another worker skips a locked reminder, and rollback leaves it retryable. The browser bell refreshes periodically, so displayed delivery also includes that polling delay.

## Coordinated release

1. Back up the existing PostgreSQL database and all documents; retain the current release and environment configuration, especially the token-encryption key.
2. Stop older API instances and workers that write to this database. Confirm the deployment points to the intended database and persistent storage.
3. From the repository root, run `python -m alembic -c backend/alembic.ini upgrade head` in the configured deployment environment. The expected head is `0016_intake_communications`.
4. Start the matching release. Verify `/api/health`, existing account login, an old uploaded document, a new upload, task-monitoring heartbeat and ingestion source health.
5. Configure the selected services below, then run live acceptance with dedicated test records. Keep synthetic tests separate from production candidate data.

Nixpacks checks the revision before startup and changes it only when explicitly configured with `RUN_MIGRATIONS_ON_START=1`; remove that temporary setting after a coordinated release. Docker retains its migration-on-start command. Multiple replicas must share the same uploads storage and OAuth encryption key.

### Shared Dokploy database incident, 21 October 2026

The local configuration was confirmed to use the hosted CRM's database. Read-only inspection found revision `0007_email_intake`, while this source requires `0014_attendance_ordering`. Missing columns/tables caused `/api/bootstrap` to return HTTP 500. Redis was also configured but unavailable; that is a separate readiness issue.

The current code reports the revision mismatch in `/api/health`, skips startup initialization on an old schema, and returns a clear HTTP 503 when a query encounters missing schema. These checks do not apply migrations. A direct supervisor launch stops before API/workers start.

Before releasing in Dokploy, publish the tested source (including migrations 0008–0014), build it, take and verify a PostgreSQL backup using a compatible server-major-version tool, and preserve the current document volume. Stop the old hosted writers and the developer backend sharing this database, then apply the migration once and start the matching release. Resolve Redis with the intended service URL, or leave `REDIS_URL` empty if Redis is intentionally unused. Follow the acceptance checks above before returning traffic. Do not run `npm run db:migrate` casually from a developer shell pointing at this shared database.

The upgrade from 0007 and recovery of health/bootstrap are tested with synthetic records on disposable PostgreSQL. Source publication, production migration and deployment have not been performed in this quality pass.

On 21 October at 19:02 IST, the user selected a direct database backup without S3. A read-only exported snapshot of the configured PostgreSQL 18.6 `care_india` database was saved under `artifacts/backups/before-0014-20261021T133241Z/`. The custom-format archive is 79,468 bytes with SHA-256 `8352cd81e84d3558e948bcb13041eefd18f40d3645bc7fb710ba6509d4b20ebc`. The restore verification at 19:07 IST passed in a disposable PostgreSQL 18.6 container with networking disabled, no published ports and temporary data storage. All 22 user tables (including Alembic metadata), 43 rows and deterministic content fingerprints matched the source snapshot; the verifier removed its container afterward. The dump and verification report are ignored local artifacts, not GitHub content.

This backup covers database schema and records at that snapshot time. It excludes uploaded files and global PostgreSQL roles; restoring with ownership/ACL changes disabled does not validate the source's owners/grants. Preserve and back up the hosted document volume and retain deployment secrets separately. Refresh the database backup at cutover if further writes have occurred. Current source still requires an explicit coordinated release. When launched against the production database at revision 0007, the backend exits before opening port 8000, causing Vite proxy connection refusals.

### Local preview independent of the production release

On 21 October, the verified snapshot was restored into the separate local Docker container `careindia-local-preview-db`, database `care_india_preview`, published only at `127.0.0.1:55435`. Migrations 0008 through 0014 were applied to that local copy. The source database and hosted application were not changed. Preview edits are local and are not synchronized to production.

`CRM_ENV_FILE` selects a complete alternate dotenv file instead of `backend/.env`. An explicit missing or empty path fails rather than falling back to production configuration. Normal process environment values still take precedence. When this variable is absent, existing configuration behavior is unchanged.

This workstation's ignored `artifacts/local-preview/preview.env` and `environment.json` contain only the isolated preview configuration. Mail, WhatsApp, remote OCR and Redis are disabled; S3 credentials and its endpoint point to nonfunctional local preview values. The full API/task/presence/manual-document supervisor runs on port 8000, with the existing Vite server at `http://localhost:5173`. Its anonymous session check, preview login and authenticated bootstrap returned HTTP 200 through Vite.

Existing account passwords from the snapshot remain valid in the local copy. A separate local admin is recorded in ignored `artifacts/local-preview/login.json`; do not commit that file. Hosted document bytes were not included in the database snapshot, so their previews require the pending upload recovery. New preview uploads use `artifacts/local-preview/uploads`.

With Docker Desktop and the preview database container running, restart the preview backend from the repository root using `backend\.venv\Scripts\python.exe artifacts\local-preview\run_backend.py`. Keep the frontend running with `npm run dev:frontend`. Ordinary `npm run dev:backend` still uses `backend/.env` unless `CRM_ENV_FILE` is explicitly set; use the preview launcher to keep this preview isolated.

The preview passed 26 real-browser screen checks (13 screens at desktop 1440px and mobile 360px), including candidate and order details, with no API errors, JavaScript errors or page-width overflow. Screenshots and the browser report are local artifacts under `artifacts/local-preview/browser/`. These checks establish local screen loading, not live provider integration or production acceptance.

## Live updates (Server-Sent Events)

Every screen is pushed changes the moment they commit, so one user's save appears on other desks without a reload.

How it works: each write transaction ends with `pg_notify('crm_changes', ...)` on its own connection (topic names and opaque ids only, never personal data; a rolled-back transaction announces nothing). Each server process keeps one extra PostgreSQL connection that `LISTEN`s and fans events out to browsers through `GET /api/events`. Browsers refetch through the normal permission-checked API. Polling stays as a slow safety net (2 minutes) while the stream is up and returns to the old fast intervals if it drops.

Deployment checklist:

- **Proxy buffering must be off for `/api/events`.** The response already sends `X-Accel-Buffering: no` and `Cache-Control: no-cache, no-transform`, which nginx and Traefik honour. Behind another proxy or CDN, disable response buffering and compression for that path and allow a read timeout above 60 seconds (heartbeats arrive every 20 seconds). Dokploy/Traefik needs no extra setting; for a custom nginx use `proxy_buffering off; proxy_read_timeout 3600s;` on `/api/events`.
- **The database must allow `LISTEN`/`NOTIFY` on a direct connection.** A pooler in *transaction* mode (PgBouncer `pool_mode=transaction`) delivers no notifications; point `DATABASE_URL` at the server directly or at a session-mode pooler. If it cannot, the stream answers 503 and the app polls as before.
- **One extra database connection per server process** (the listener). With N workers, N connections; each worker serves its own browsers.
- **Switch off** with `CRM_LIVE_EVENTS=0` (no notifications sent, no listener, `/api/events` answers 503 and browsers poll). Run workers with the same setting as the web process.
- Per account at most 8 concurrent streams (a further tab gets 429 and polls). A session that is signed out, expired or whose account is deactivated receives `bye` and is closed within the 20-second heartbeat, usually at once.
- The browser workspace cache is **off**: no workspace, candidate or report data is stored in the browser, and entries left by older versions (`crm-swr:v1:*`) are deleted at startup. To bring the fast-reload cache back set `SNAPSHOT_CACHE_ENABLED = true` in `frontend/src/shared/state/snapshot-cache.ts` (live events then erase it whenever data changes). Unsent mail/WhatsApp draft text kept in the browser is user input, not business data, and is untouched.
- Verify after deploy: open the CRM in two browsers, create a client in one, and watch it appear in the other's list within a second. In the browser network tab `/api/events` should stay open (type `eventsource`) and receive a `ping` every 20 seconds.

## Zoho Mail inside the CRM

Follow [email connections](email-connections.md) for the Zoho account-specific IMAP/SMTP details. Set `MAIL_TOKEN_ENCRYPTION_KEY` once and back it up securely; replacing it invalidates existing encrypted credentials. Admin can connect Zoho later from the CRM. The app does not require a connected mailbox to use recruitment or attendance. New Gmail/Microsoft connections and live operations are disabled; existing historical records remain readable.

Connection checks use TLS and Zoho's allowed hosts. The mailbox password/app password is encrypted on the server and is never returned in API responses. Enable IMAP in the Zoho account where supported. Use the server names shown for that account and region. Disconnect retains mirrored messages, files and send history.

Live acceptance, after the account owner connects a test mailbox, should cover folder listing/creation, paging/search, read/star changes, move/archive/trash/restore, local and remote drafts, To/Cc/Bcc, reply/reply-all/forward, attachments and one explicitly authorized send. Confirm the Sent copy, reconnect, partial/unknown delivery handling and candidate intake from a received attachment. Do not retry an uncertain SMTP send as if it were a confirmed failure. Older-mail import has bounded controls separate from live reception. No live mailbox connection or send was performed for this implementation.

## Staff attendance and activity

Staff and Manager use the shell shortcut to reach Attendance check-in/out controls. Admin views all employee records; Manager views their own and Staff records. Only Admin can correct a raw session with a recorded reason. The presence/attendance worker closes stale activity intervals and processes approved leave-cover handover/return every 30 seconds in separate transactions. Presence reports independently cap stale intervals, but cover requires this worker; run the complete supervised runtime.

Use [the attendance guide](attendance.md) for timezone, monthly policy and role behavior. Verify check-in survives reload, duplicate requests do not create shifts, checkout persists, and logout ends presence while retaining an open attendance session. After `0015`, also verify a Staff request, cover acceptance, Manager/Admin decisions, monthly leave/OT figures, weekly-off cutoff and worker handover/return. Activity remains limited to CRM browser interaction.

## WhatsApp Business

Configure `WHATSAPP_ENABLED`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_GRAPH_VERSION`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN` on the server. Use the Graph version supported/configured for your Meta application; the code does not assume a permanent latest version.

The public HTTPS callback is `https://YOUR_CRM_DOMAIN/api/whatsapp/webhook`. Configure the same verification token with Meta and subscribe the intended Business phone's message events. The callback checks the GET verification challenge and the POST HMAC signature before persisting a receipt. Meta's [webhook SDK reference](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/) describes those verification mechanisms.

The UI shows channel state, originals, candidates, failures and Admin review/retry actions. Automatic intake can be paused. Sender number is retained as provenance and supplies an empty WhatsApp contact field; it is never used as candidate identity. The channel now includes outgoing text/media, approved templates and signed delivery states. Automatic acknowledgements are opt-in per channel and default off. Multi-person intake uses the existing document worker. Configure `WHATSAPP_BUSINESS_ACCOUNT_ID` for template sync; see [activation and acceptance](communications-and-split-intake.md).

Live acceptance must complete webhook verification, receive a real test document, check signature rejection and duplicate delivery, verify original/selected documents and assignment, restart during a pending extraction, and review a non-extractable document. No live WhatsApp delivery was performed during local verification.

## Local or S3 document storage

Local mode uses `STORAGE_BACKEND=local` and a persistent `STORAGE_DIR`. Mount the same directory in every API/worker process. S3 mode uses `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, optional `S3_ENDPOINT_URL`, `S3_PREFIX` and server-side access credentials (or the supported deployment credential chain). The bucket is private; browsers receive bytes through authenticated CRM endpoints. A compatible provider must support conditional immutable object creation.

Changing the backend setting does not copy existing files. Historical local object keys continue to require their original local volume until migrated. Never remove that volume merely because new uploads now use S3.

Run from `backend`, with the intended environment:

```powershell
python -m app.storage_admin audit --output document-audit.json
python -m app.storage_admin migrate-s3 --output storage-migration-preview.json
```

The audit inventories attachment rows, draft attachment files, immutable send snapshots and source/derived keys inside email, WhatsApp and manual-extraction checkpoints. It reads objects and compares known SHA-256 hashes. Output contains storage keys, sizes and hashes, not OCR payloads. Protect it as operational metadata. A missing/corrupt object makes the audit fail.

`migrate-s3` is a dry run by default. Configure the target S3 settings while retaining the source local directory. Review the preview, back up the database/documents and stop **all** API and worker writers before applying:

```powershell
python -m app.storage_admin migrate-s3 --apply --writers-stopped --output storage-migration-result.json
```

The offline command takes a defensive database write lock, rechecks audited sources, copies immutable objects and reads them back for checksum verification. Only after every copy succeeds does it update direct keys, nested checkpoints and mailbox storage identity in one transaction. It retains all local originals. On failure, database references remain unchanged; already copied immutable objects can be reused on a retry. Existing S3 references outside the target namespace are rejected rather than silently remapped. Endpoint-to-endpoint S3 migration needs a separate reviewed procedure.

After success, set `STORAGE_BACKEND=s3`, restart all services, audit again and preview a historical and a new document from the application. Keep the local backup until the recovery policy permits removal. No live S3 migration was run during this implementation.

## Backup and restore verification

Back up PostgreSQL, document bytes/object versions, local volume marker where applicable, and the encrypted-token key as a coordinated recovery set. A database backup without its referenced files is incomplete. Restore into an isolated database/storage namespace, keep provider workers disabled, run the document audit, check user login/roles and representative old/new candidate/application documents, then rehearse manual job and task recovery. Enable external delivery only after connecting the restored environment intentionally.

Use retention/versioning appropriate to the agency's document policy. This release does not delete old versions or run automatic orphan cleanup. Failed uploads with uncertain database commit outcomes retain bytes for reconciliation.

## Verification artifacts

Automated backend tests exercise real disposable PostgreSQL migrations, role access, candidate matching, all stage rules, ownership history, worker retries and file recovery. Frontend tests cover shared parsing/matching/workflow rules. Browser artifacts under `artifacts/` use synthetic identities and include a real-API matrix across Admin, Manager and Staff at 1440, 768, 360 and 390 pixels; provider-specific browser cases intercept external services. Artifacts are generated/ignored, not production records. The [integration audit](integration-audit.md) records final results and remaining acceptance steps.
