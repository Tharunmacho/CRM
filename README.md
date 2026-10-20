# Care India Overseas — Recruitment CRM

A recruitment CRM with a React/TypeScript frontend, a FastAPI backend, and PostgreSQL. The existing application covers candidate entry, employers and orders, candidate mapping, the eleven recruitment stages, documents, tasks, and reporting screens. Several PRD features still need implementation; the phase plan distinguishes working code from planned work.

## Requirements and implementation plan

- [Developer specification — original 12-page PDF](docs/requirements/recruitment-crm-developer-specification.pdf)
- [Architecture and module boundaries](docs/architecture.md)
- [Email/Gmail automation references and adopted design decisions](docs/reference-architecture-review.md)
- [Database table dictionary, pgAdmin guide and migration plan](docs/database-design.md)
- [Implementation phases and acceptance criteria](docs/implementation-phases.md)
- [Screen-by-screen integration order](docs/screen-integration-plan.md)
- [Loading performance, measurements and regression checks](docs/performance.md)
- [PRD requirements mapped to current code and phases](docs/requirements-matrix.md)

The structural foundation is implemented. The later phases are a development plan, not a claim that every PRD feature is complete. The requirements matrix also records differences between the existing implementation and the specification, including employer modelling and duplicate handling.

## Project layout

```text
crm/
├── frontend/
│   ├── src/
│   │   ├── app/                 # Composition, routing, providers, layout, agency config
│   │   ├── features/            # Screens and components grouped by business area
│   │   ├── shared/              # API, models, state, UI, workflow rules, utilities
│   │   ├── styles/              # Global styles and fonts
│   │   ├── mocks/               # Existing fixtures; not the live data store
│   │   └── main.tsx
│   ├── public/                  # All local fonts, flags, and asset licences
│   ├── package.json             # All frontend dependencies and scripts
│   └── .env.example
├── backend/
│   ├── app/
│   │   ├── api/                 # Request/response schemas and API routes
│   │   ├── core/                # Settings, authentication, request dependencies
│   │   ├── db/                  # Database session and SQLAlchemy models
│   │   ├── domain/              # Recruitment stage rules and currencies
│   │   ├── integrations/ocr/    # OCR provider client and field mapping
│   │   ├── services/            # Existing bootstrap service; more services in later phases
│   │   ├── main.py              # FastAPI composition and static frontend serving
│   │   └── cli.py               # Existing operational commands
│   ├── alembic/                 # Schema history through 0007_email_intake
│   ├── tests/                   # Existing OCR tests
│   ├── requirements.txt         # Pinned production Python dependencies
│   ├── requirements-dev.txt     # Production dependencies plus test dependencies
│   └── .env.example
├── docs/                        # Architecture, database, phases, screen plan and original PRD
├── scripts/                     # Cross-platform workspace command runner
├── package.json                 # npm workspace and convenient root commands
├── package-lock.json            # Single JavaScript dependency lock for the workspace
├── Dockerfile                   # Frontend build + Python runtime
├── docker-compose.yml           # Local PostgreSQL/app services and persistent volumes
└── .env.example                 # Local Docker Compose settings
```

Frontend feature areas are `auth`, `dashboard`, `clients`, `companies`, `jobs`, `candidates`, `workflow`, `tasks`, `todos`, `reports`, `users`, and `inbox`. All 16 existing screens are under `frontend/`. Shared code should not import feature pages. Route composition lives in `app/`; business-specific screens and components live with their feature.

The backend is a modular monolith. Existing request handlers retain their behaviour; extracting the remaining business operations from routes into domain services is scheduled in the phase plan. Do not create separate services or duplicate models just to add another screen.

## Dependencies

Use Node.js 22+, npm 10+, Python 3.12, and PostgreSQL. Docker Compose supplies PostgreSQL 16 for local development; the configured Dokploy database reports PostgreSQL 18.6. Keep these environments distinct when verifying migrations.

Run `npm ci` at the repository root. npm workspaces installs every dependency declared by `frontend/package.json` using the root lockfile. A root `node_modules/` directory is expected: npm shares installed packages across workspaces. Do not create a second frontend lockfile or install packages manually into unrelated folders.

Production Python packages remain pinned in `backend/requirements.txt`. Development installs use `backend/requirements-dev.txt`, which includes the production file. Keep Python dependencies in `backend/.venv`; this is ignored by Git and Docker.

## Local development on Windows

Run these commands from `M:\crm` in PowerShell. `npm.cmd` also works when PowerShell blocks the `npm.ps1` launcher.

```powershell
npm.cmd ci
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt

Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
Copy-Item frontend/.env.example frontend/.env.local

docker compose up -d db
npm.cmd run db:migrate
```

Copy the examples only during initial setup; keep any existing local configuration. The local Python database URL uses host port **5433**, matching Compose. If database credentials change, update `POSTGRES_*` and the complete `DATABASE_URL` in the root `.env`, plus the URL in `backend/.env`. The Compose app URL uses `db:5432`; the host Python URL uses `localhost:5433`. Percent-encode reserved characters in URL usernames/passwords; keep `POSTGRES_PASSWORD` as the actual unencoded password. Existing database volumes retain the credentials with which they were initialized.

Start the API in a terminal opened in `backend/`:

```powershell
Set-Location M:\crm\backend
npm.cmd run dev
```

This runs the Python API on port **8000** with automatic reload and live logs in
that terminal. Startup reports PostgreSQL and Redis connection status. Requests
taking at least one second produce a `Slow API` log with their route and duration;
API responses also include a `Server-Timing` header for browser Network timing.
`npm.cmd start` runs the same API without automatic reload. These
scripts use `backend/.venv`; the backend still uses Python dependencies from its
requirements files, so no separate backend npm installation is needed.
From the repository root, `npm.cmd run dev:backend` remains available.

Start the frontend in another terminal opened in `frontend/`:

```powershell
Set-Location M:\crm\frontend
npm.cmd run dev
```

Keep both terminals open; press **Ctrl+C** in a terminal to stop its service.
Only one backend should use port 8000 at a time.

Open `http://localhost:5173`. Vite forwards `/api` requests and session cookies to `http://127.0.0.1:8000`; no separate API origin is needed. API documentation is at `http://127.0.0.1:8000/api/docs`.

To enable local Redis, set a generated `REDIS_PASSWORD` in the root `.env`
and leave `REDIS_PORT=6380`. Set `REDIS_URL` in `backend/.env` to
`redis://:<URL-encoded-password>@127.0.0.1:6380/0`, then run:

```powershell
docker compose --profile redis up -d redis
```

Restart the API and open `http://127.0.0.1:8000/api/health`; its `redis.status`
should be `ok`. The Redis service stores data in the `redisdata` volume and binds
only to this computer. For the API running inside Compose, use
`redis://:<URL-encoded-password>@redis:6379/0` in the root `.env` instead.
These settings do not create or configure a Redis service in Dokploy.
Redis currently provides connection/readiness support; CRM records and permissions
are still read freshly from PostgreSQL. Enabling Redis alone does not cache queries
or start background workers.

On Linux/macOS use `npm` and `backend/.venv/bin/python` for installation. The root backend commands select the correct virtual environment automatically. `CRM_PYTHON` can override the interpreter for a managed environment or CI.

## Configuration ownership

| File or setting | Purpose |
| --- | --- |
| Root `.env` | Docker Compose interpolation only |
| `backend/.env` | FastAPI settings, read independently of the command's working directory |
| `frontend/.env.local` | Vite configuration; only `VITE_` variables are exposed to browser code |
| `API_PROXY_TARGET` | Development proxy target; defaults to `http://127.0.0.1:8000` |
| `VITE_API_URL` | Leave empty for the local proxy and same-origin production hosting |
| `VERIS_OCR_BASE_URL`, `VERIS_OCR_API_KEY` | Optional backend extraction connection; local inspection precedes extraction and manual entry works without it |
| `TESSERACT_CMD`, `OCR_LANGUAGES`, `OCR_MAX_PAGES`, `OCR_PAGE_TIMEOUT_SECONDS`, `OCR_DOCUMENT_TIMEOUT_SECONDS` | Local document reader configuration; see the first OCR stage below |
| `EMAIL_INGESTION_ENABLED`, `EMAIL_POLL_SECONDS`, `IMAP_*`, `EMAIL_ACCOUNTS_JSON` | Incoming-mail worker and server-only mailbox configuration; see connected email intake below |
| `STORAGE_DIR` | Backend file storage; defaults to `backend/data/uploads` locally |
| `STATIC_DIR` | Built frontend location; defaults to `backend/static` locally |
| `REDIS_URL` | Optional backend Redis connection; use a local URL for development and Dokploy's internal URL in production |

After revision `0006_domain_table_names`, accounts, assigned roles, activation status and Argon2id password hashes live in PostgreSQL's `auth_users` table, and sign-ins live in `auth_sessions`. Login reads the saved account and creates a database session; it has no default-account fallback. Startup initializes only `workflow_assignment_cursor`, preserving existing users and its current position. Deleted accounts stay deleted, disabled accounts stay disabled, and changed passwords survive restarts. The former account seed, seed-password environment overrides, legacy account SQL and unused frontend demo accounts have been removed.

For a new empty database, create accounts explicitly from `backend/` after migrations. The role must be one of the CRM's supported roles (`admin`, `manager`, `staff`); each account's assigned role is stored in the database.

```powershell
Set-Location M:\crm\backend
.\.venv\Scripts\python.exe -m app.cli create-user you@example.com --name "Your Name" --role admin
.\.venv\Scripts\python.exe -m app.cli list-users
```

Account creation prompts for a password twice without displaying or logging it and refuses to overwrite an existing email. For an existing account, use `python -m app.cli set-password EMAIL` with the same interpreter. This also prompts securely, revokes that user's sessions and preserves their activation status. In a deployed container, run these commands with `docker exec -it <container> python -m app.cli ...`. Updating local source does not update an already deployed container.

Existing accounts need no recreation. The earlier account-persistence correction required no schema migration; the table-naming update requires `0006_domain_table_names` and the matching backend. In pgAdmin, refresh **care_india → Schemas → public → Tables**, then open `auth_users` for accounts or `crm_candidates` for candidate records. The [database guide](docs/database-design.md#inspect-accounts-and-records-in-pgadmin) includes account/session queries that omit password hashes and session tokens. Password hashes are not recoverable plaintext passwords. Full user-management screen mutations and the remaining access/audit policies are still assigned to the implementation phases.

The [table dictionary](docs/database-design.md#table-dictionary) maps every old name to its new name, purpose and key relationships. Revision `0006` renames existing tables and adds descriptions without adding another schema or deleting feature tables. The API paths, JSON collection keys and entity-type values remain unchanged. The existing contact/employer hierarchy is preserved for Phase 2 reconciliation; the naming update does not complete model normalization or Phase 1 business rules.

Apply the naming migration in a coordinated release: stop API writers, back up the database, run migrations, then restart the compatible API and verify the revision/readiness. Its downgrade restores the earlier names and must be paired with the earlier backend version. See [migration and maintenance rules](docs/database-design.md#migration-and-maintenance-rules). Documentation of `0006` is not evidence that a particular live database has been upgraded.

## First OCR stage: manual document inspection

The Add candidate **Read documents** action now inspects document content locally before calling Veris. Native PDF text is used where available; scanned pages and images use Tesseract, including bounded rotation attempts. Each page is accounted for as read, blank or unread, with a content classification of resume, passport, other or unknown. Only the pages matching the selected resume/passport mode are sent for extraction, in their original order. Unread or uncertain pages prevent submission; a resume input without resume pages returns a report without extraction.

PDF, DOC/DOCX converted once to PDF, and supported single-frame Pillow images are accepted. Word conversion requires LibreOffice. HEIC and multi-frame images are not supported by this OCR path; general attachment upload is separate. The original document's page numbers remain visible in the report and extracted evidence.

### Local setup and limits

1. Update the backend environment with `backend/requirements-dev.txt` as described above.
2. Install Tesseract with English and orientation data (`eng`, `osd`). The reader checks `PATH` and the usual Windows location, `C:\Program Files\Tesseract-OCR\tesseract.exe`; use `TESSERACT_CMD` for another location. Tesseract is installed in the current development environment.
3. Configure the backend OCR provider URL/key for field extraction. Keep these values server-side. Local inspection still returns actionable review information when provider configuration or extraction fails.
4. For Word documents, install LibreOffice and make its executable discoverable. It is not installed in the current local environment, so export Word documents to PDF there. Both Docker and Nixpacks install Tesseract (`eng`/`osd`) and LibreOffice Writer.
5. Restart the API after configuration changes.

The public settings are documented in `backend/.env.example`:

```dotenv
TESSERACT_CMD=
OCR_LANGUAGES=eng
OCR_MAX_PAGES=300
OCR_PAGE_TIMEOUT_SECONDS=20
OCR_DOCUMENT_TIMEOUT_SECONDS=600
```

The upload limit remains **25 MB**. The local reader defaults to **300 pages**, **20 seconds per OCR page**, and a **600-second document budget**; the reader limits and languages are configurable above. Pages skipped after a limit/deadline are shown as unread and are not counted as inspected. English is the default; install the corresponding Tesseract language data before changing it. Long documents can take several minutes. The browser allows up to **20 minutes** for an OCR request; the normal JSON API deadline remains 30 seconds.

### Local verification flow and remaining boundary

1. Open **Add candidate**, choose a resume or passport, and select **Read documents**.
2. Review the actual inspected count, selected page numbers, unread/uncertain pages and individual-page reasons. A review report is retained even when no fields were extracted or the provider failed after inspection.
3. Correct the suggested fields. Edits made before or during a scan, including intentionally cleared fields, survive rescans. Passport values take precedence only for fields the recruiter has not edited. Replacing a file clears that file's stale report and automatic values.
4. Enter required name/phone manually when extraction cannot proceed. **Continue manually** stops the browser waiting and enables saving; it does not cancel an already-running server/provider job.
5. **Save and assign** saves the candidate and valid extraction evidence, then uploads the selected originals. Empty or review-only OCR output is not saved as a successful scan; upload failures are shown separately.

This remains a request-bound manual flow. Reading alone does not persist a candidate, original file, job or review report, and refreshing/leaving the page loses the draft. Stable selected-page PDF bytes and submission keys reduce duplicate provider submissions, and returned page coverage is checked before fields are applied. This form does not use the separate durable email queue described below.

Verification on 20 October 2026 covered local inspection/classification, API behavior with synthetic provider results, **23 frontend tests**, the production build and isolated mobile Chrome checks. A synthetic 100-page scanned PDF was processed with **99 real Tesseract calls in 127.61 seconds**: all 100 pages were accounted for, only resume pages 77–78 were selected, passport page 100 was classified separately, page 10 was blank and rotated page 5 was read. No paid provider or database call was made in that smoke check. These results verify this manual stage, not connected-email or production end-to-end acceptance.

## Connected email intake

The incoming IMAP path now has a PostgreSQL queue, a separate worker, Inbox controls and dashboard source health. It stores original attachments before advancing the mailbox cursor, inspects their pages, and separately extracts resume, passport and Aadhaar content. Candidate links retain the original source bundle and generated document slots. Accepted provider job IDs and results are checkpointed before candidate writes; retries resume saved work instead of launching a synchronous fallback. Completed extraction can be reused for matching attachment bytes.

### Setup

1. Back up PostgreSQL and uploaded files, stop older API/worker processes, and apply `0007_email_intake` with the coordinated migration procedure below. Restart compatible code afterward. The migration adds account cursor/health state and `email_ingestion_jobs`; it does not recreate users. Downgrading to `0006_domain_table_names` removes the new job ledger and account checkpoint columns, so preserve the backup and reconcile intake before rollback.
2. Add the following settings to `backend/.env` locally, or the Backend application's environment in Dokploy. Fill credentials privately; they never belong in a `VITE_` variable or an Inbox form.

   ```dotenv
   EMAIL_INGESTION_ENABLED=true
   EMAIL_POLL_SECONDS=120
   IMAP_SERVER=YOUR_IMAP_HOST
   IMAP_PORT=993
   IMAP_USERNAME=YOUR_MAILBOX_ADDRESS
   IMAP_PASSWORD=YOUR_MAILBOX_APP_PASSWORD
   IMAP_USE_SSL=true
   IMAP_FOLDER=INBOX
   ```

   Configure Veris and the local document tools as described above. For multiple mailboxes, `EMAIL_ACCOUNTS_JSON` replaces the single `IMAP_*` account; its object format is in `backend/.env.example`. Only configured addresses are exposed to Admin, never credentials.
3. For local development, keep the API and frontend running and start a third terminal:

   ```powershell
   Set-Location M:\crm\backend
   npm.cmd run worker
   ```

   The Nixpacks production launcher starts a supervised API and separate worker when ingestion is enabled. The Docker runtime uses the same supervisor. Both processes must share the persistent `STORAGE_DIR` volume. The queue is PostgreSQL-backed and does not require Redis.

   The worker pins each account to the storage identity in `STORAGE_DIR/.email-storage-id`. When handing processing between local development and Dokploy against the same database, stop the old worker and mount or copy the complete storage directory, **including this marker and all original/selected documents**, before starting the replacement. A different directory/marker blocks processing with a visible storage-mismatch error. Do not delete or regenerate the marker to bypass that guard. Starting a local worker against a production database with unrelated local storage is not a valid handoff.
4. Sign in as Admin, open **Inbox → Connect account**, select the configured address, choose auto-ingest and connect. The connection establishes a cursor at the current mailbox end: **only mail arriving after connection is imported**. Send new test mail afterward; historical mail is not backfilled.
5. Leave the worker running: connected mailboxes sync automatically, every two minutes by default. No manual sync action is needed or exposed. Check the heartbeat and last-sync time; an enabled setting alone does not establish that the worker is running. Inbox status updates automatically while the page is visible.

### Review and verification

Inbox shows queued, reading, retry, needs-review, completed, ignored and failed outcomes, with attachment page classifications and candidate links. Auto-ingest controls attachment processing only: switching it off still imports new messages automatically, and **Create candidate from attachments** queues manual intake. Admin can retry a failed/review/ignored job after correcting its cause. Pending provider jobs retain their ID and are revisited; processing failures use backoff for the initial attempt plus up to three retries. Failed provider executions retain their history when a new retry generation is submitted.

Conflicting identities or unread/unknown pages enter review without silently changing a candidate. A recognized new person with missing details or no eligible owner is visibly marked for review. An existing match by normalized phone, email or passport receives document evidence and empty-field updates while keeping populated fields and ownership. This email policy does not finish the manual merge/cancel workflow.

Completed intake refreshes Data Bank state, and Admin's dashboard shows source sync, worker heartbeat, queued/reading/review/failure counts and links back to Inbox. Inbox loads up to 200 messages and 100 jobs and polls every five seconds while visible; the dashboard uses a smaller health endpoint. Attachment previews, OAuth/Graph connections, account removal, SMTP replies/acknowledgements, WhatsApp and S3 storage remain open work. **Send reply** is disabled. The adapter does not send, move, delete or mark messages read.

Verification on 20 October 2026 covered **379 backend tests**, including the opt-in PostgreSQL migration/intake cases, plus **30 frontend tests** and the production build. The full backend run passed 378 cases; its one timestamp-format failure was resolved by pinning the disposable test connections to UTC, and all seven bootstrap tests then passed. No application timestamp behavior changed.

Isolated mobile Chrome checks used synthetic API responses for connection, processing, candidate links, page summaries, dashboard health, review/retry and role controls. After removing manual sync controls, the automatic-arrival path through candidate creation was also verified; later navigation checks encountered browser-harness interception/time-out errors, which are recorded in the ignored verification artifacts. No browser fixture accessed a real mailbox, provider or database.

The configured PostgreSQL database was backed up, restore-tested and upgraded to `0007_email_intake`, with the existing contents of all 20 application tables unchanged. A separate read-only login to the configured IMAP mailbox passed without fetching messages. A real Veris job extracted a synthetic résumé and resumed from its saved job ID; it wrote no candidate into the CRM. Passport/Aadhaar routing and response validation have fixture-based coverage, while a new-email test through the running deployment and live identity-document extraction remain acceptance steps. This slice does not complete Phase 5.

The updated Nixpacks 1.41 Linux image also built successfully. Using its normal login-shell entrypoint, an offline container read a native PDF, ran real Tesseract on a scanned image and converted a real DOCX through LibreOffice; all three selected the expected résumé page. Keep the generated Nixpacks entrypoint: it initializes the library paths needed by the Nix Python runtime. This image check used synthetic documents with networking disabled and made no provider or database calls.

## Build and verification

```powershell
npm.cmd run typecheck
npm.cmd run build
npm.cmd test --workspace frontend
npm.cmd run test:backend -- -q
```

`npm run check` combines TypeScript checking and backend tests. The production frontend is emitted to `frontend/dist/`. The Python suite covers OCR mapping/provider polling plus account persistence, database-backed login/roles, explicit CLI provisioning, session revocation and startup recovery. The authentication regression tests use isolated SQLite identity tables; they do not replace PostgreSQL concurrency, full permissions, eleven-stage workflow or live provider acceptance tests assigned to later phases.

Phase 0 verification on 19 October 2026 passed: frontend type checking and production build; all 41 existing Python tests in `backend/.venv`; Python dependency consistency; API/OpenAPI import with 20 model tables; unauthenticated API guard, SPA and original static assets; development proxy/cookie forwarding; and offline SQL generation through all five migrations, including percent-encoded connection credentials. All existing direct dependencies and locked JavaScript dependency versions were retained. The frontend build still reports its existing large-bundle warning. No live database was created, migrated or connected during Phase 0, and no Docker image/provider integration was exercised.

Account persistence verification on 20 October 2026 passed: all 56 backend tests, frontend type checking/build, and live login, current-user lookup, bootstrap and logout for the three existing roles through the updated local API against the configured Dokploy PostgreSQL database. At that check the database was at `0005_candidate_ocr_data` and stored Argon2id hashes. A checksum of all user columns before startup and after these checks confirmed the accounts were unchanged. Test login sessions were logged out. No migration or account recreation was required for that account-persistence correction; a separately deployed backend must receive the source update to stop the old automatic resets. This historical check does not verify the later `0006` naming migration.

Naming verification on 20 October 2026 initially passed all 73 backend tests, including six migration integration scenarios on an isolated PostgreSQL 17 database: preserved rows/relationships, existing sessions, fresh installation, downgrade/re-upgrade, collision rollback and caller-controlled transaction rollback.

The coordinated live rollout subsequently applied `0006_domain_table_names` to Care India's PostgreSQL 18.6 database. Before migration, a custom-format database backup was restored in an isolated PostgreSQL 18 container and all 20 business-table counts and fingerprints matched. The live migration checked the same contents and table object identities inside its transaction before committing. The persisted revision and new names were verified afterward; the normal local API and frontend proxy report PostgreSQL and Redis healthy. Backup files and verification reports stay in ignored local artifacts. The existing attachment was unavailable in local upload storage, so the old application's storage must be retained until its file is located and attached to the new deployment. Production HTTP readiness and that file's availability remain deployment acceptance checks.

Nixpacks/Redis verification passed 106 backend tests plus the six PostgreSQL migration scenarios run separately. The Nixpacks Linux image built successfully; isolated runtime checks covered explicit migration, refusal to modify an unmigrated database by default, SPA assets, healthy Redis and HTTP 503 for unavailable Redis. These infrastructure checks do not complete the remaining business-feature phases.

The Docker image builds the same npm workspace, includes all `frontend/public/` assets, installs pinned production Python packages, and serves the built frontend with FastAPI. Local full-stack startup is:

```powershell
docker compose up --build
```

Open `http://localhost:8000`. Compose stores PostgreSQL data in `pgdata` and uploaded documents in `uploads`. Its ports bind to localhost for local development. In hosted deployment, configure HTTPS, environment variables, and persistent database/upload volumes. The container runs migrations before starting the API; review migrations before deploying them to an existing database.

## Dokploy deployment with Nixpacks

Select repository `Tharunmacho/CRM`, branch `main`, build path `/`, and build type
**Nixpacks**. Leave Publish Directory empty and static-site serving disabled.
Remove any custom `NIXPACKS_INSTALL_CMD`, `NIXPACKS_BUILD_CMD` or
`NIXPACKS_START_CMD` overrides so the root `nixpacks.toml` controls the build.

The plan explicitly installs Python 3.12 and Node.js 22, creates `/opt/venv`,
installs the pinned backend requirements and frontend workspace dependencies,
builds the frontend, and copies its complete output to `/app/static`. Node is
used to build the website; FastAPI serves both the website and API at runtime.
No database connection or migration runs during the build.

Set these service environment variables, retaining any configured OCR settings:

```dotenv
DATABASE_URL=YOUR_EXISTING_POSTGRESQL_INTERNAL_CONNECTION_URL
AGENCY_TIMEZONE=Asia/Kolkata
STATIC_DIR=/app/static
STORAGE_DIR=/app/data/uploads
PORT=8000
```

Replace the database placeholder with the existing database's internal URL in
Dokploy; never commit it. Route the domain to container port **8000**. Mount
persistent uploads at `/app/data/uploads`. When reusing a database, attach its
existing uploads volume or copy the files before switching traffic; a new empty
volume does not contain the files referenced by existing records.

The Nixpacks start command uses `scripts/start-production.py`. It verifies that
the frontend exists and the database revision matches this release before
starting Uvicorn. By default it does **not** change the database. This prevents
an automatic GitHub deployment from silently renaming tables while the older
CRM application still uses them.

For the first coordinated deployment to the existing database:

1. Back up PostgreSQL and uploaded files.
2. Stop every older CRM backend using this database, including local development
   backends; keep PostgreSQL running.
3. Either run `python -m alembic -c backend/alembic.ini upgrade head` from the
   repository root with the deployment environment, or temporarily add
   `RUN_MIGRATIONS_ON_START=1` in Dokploy and deploy.
4. Confirm `/api/health` reports ready, verify sign-in and an existing uploaded
   document, then remove `RUN_MIGRATIONS_ON_START`. Later schema updates also
   require an explicit migration step.

A failed migration stops startup. Database migrations remain reversible as
documented in the database guide; code and schema versions must match when
rolling back. The Dockerfile deployment path retains its existing automatic
migration command; the explicit migration control above is for Nixpacks.

### Optional Redis connection

Create a Redis service on the same Dokploy server/network as the CRM and set a
unique password. Keep it internal; no external Redis port is needed for the
deployed application. Copy that service's **Internal Connection URL** into the
Backend service environment:

```dotenv
REDIS_URL=YOUR_DOKPLOY_REDIS_INTERNAL_CONNECTION_URL
```

Keep `DATABASE_URL` pointing to PostgreSQL. `REDIS_URL` is a separate connection;
Redis does not replace the CRM's relational database. A Redis container running
in Docker Desktop on a developer's computer is not the Redis service available
inside Dokploy.

The backend creates a shared Redis connection pool when needed and reports its
connection status under `redis` in `/api/health`. An unset or empty `REDIS_URL`
reports `disabled`; a working authenticated connection reports `ok`; a configured
but unreachable or invalid connection reports `unavailable` and overall readiness
becomes `degraded` with HTTP 503. Healthy readiness returns HTTP 200. Errors omit
credentials and connection URLs. Connections close on application shutdown.

This integration provides Redis connectivity and readiness checks. It does not
add query caching or move login sessions out of PostgreSQL. The incoming email
worker uses its own PostgreSQL-backed queue; task escalation and reminder
schedulers remain future work.

## Current implementation boundaries

Candidate creation, automatic assignment, client/company/order creation, mapping, stage drafts and completion, file versions, task remarks, reassignment, and personal to-dos have backend implementations. Manual OCR now includes local page/content inspection, selected-page extraction, review reports and protection of recruiter edits. Dashboard and report screens compute results from the bootstrap payload. Dashboard CSV export is implemented.

Incoming IMAP connection, durable document intake, Inbox job controls and dashboard source health are implemented. OAuth/Graph, SMTP sending, attachment preview, account removal, WhatsApp automation, S3 storage, user administration actions, complete notification delivery, scheduled escalation jobs, stage reopening, and Excel/PDF export remain incomplete. Ordinary record editing and full PRD duplicate merging are also outstanding. Backend audit writes exist for some operations, but the current audit viewer still reads an empty legacy fixture.

The phased plan records these gaps, role enforcement and concurrency issues, reporting scope fixes, and the acceptance tests required before release. Implement each phase against the original PRD and its traceability rows, and update the matrix when behaviour is verified.
