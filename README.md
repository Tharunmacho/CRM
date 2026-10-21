# Care India Overseas Recruitment CRM

A recruitment CRM with a React/TypeScript frontend, FastAPI backend and PostgreSQL. It integrates candidate intake and documents, employers and job orders, eleven recruitment stages, tasks, reports, user management, Zoho Mail and staff attendance. The requirement matrix distinguishes implemented behavior from live provider acceptance.

## Requirements and implementation plan

- [PRD/reference integration audit, screens, roles and verification evidence](docs/integration-audit.md)
- [Developer specification â€” original 12-page PDF](docs/requirements/recruitment-crm-developer-specification.pdf)
- [Architecture and module boundaries](docs/architecture.md)
- [Email automation reference review and adopted design decisions](docs/reference-architecture-review.md)
- [Database table dictionary, pgAdmin guide and migration plan](docs/database-design.md)
- [Implementation phases and acceptance criteria](docs/implementation-phases.md)
- [Screen-by-screen integration order](docs/screen-integration-plan.md)
- [Loading performance, measurements and regression checks](docs/performance.md)
- [PRD requirements mapped to current code and phases](docs/requirements-matrix.md)

Use the current integration audit and requirement matrix for implementation status. Earlier phase/screen plans preserve the original delivery sequence and do not override current behavior.

## Project layout

```text
frontend/src/app/          Routing, providers and application shell
frontend/src/features/     Screens grouped by business area, including inbox and attendance
frontend/src/shared/       API, models, reusable UI and workflow rules
frontend/src/styles/       Poppins typography and responsive styling
frontend/tests/            Frontend behavior and domain checks
backend/app/api/           Request schemas and role-protected routes
backend/app/db/            SQLAlchemy models and database sessions
backend/app/domain/        Recruitment stage rules
backend/app/integrations/  Mail, WhatsApp and OCR adapters
backend/app/services/      Business operations and durable workflows
backend/app/runtime.py     API and worker supervisor
backend/alembic/           Ordered schema history through 0014_attendance_ordering
backend/tests/             Unit, API, migration and PostgreSQL integration checks
docs/                     PRD, requirements, database dictionary and operations
scripts/                  Workspace commands and production launcher
```

The backend is a modular monolith. Keep API contracts, domain services and provider adapters separate. Shared frontend code should not import feature pages. PostgreSQL owns application state; file bytes live in private persistent storage.

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

This runs the Python API on port **8000** with automatic reload, plus the workflow,
presence and document workers. Configured email/WhatsApp workers also start.
Restart the command after changing worker code; automatic reload applies to the API.
The supervisor checks the database revision before starting any process and refuses
an outdated schema without migrating it. Startup reports PostgreSQL and Redis connection status. Requests
taking at least one second produce a `Slow API` log with their route and duration;
API responses also include a `Server-Timing` header for browser Network timing.
`npm.cmd start` runs the same API and workers without automatic reload. These
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
| `EMAIL_INGESTION_ENABLED`, `EMAIL_POLL_SECONDS`, `MAIL_TOKEN_ENCRYPTION_KEY` | Zoho reception/extraction workers and encrypted mailbox connections; see the email connection guide |
| `AGENCY_TIMEZONE` | Agency-day boundaries for reports, task monitoring and staff attendance |
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

Existing accounts need no recreation. Admin User Management supports creation, editing, roles, activation, assignment eligibility, leave, password reset, session revocation and audit history. Deactivation transfers open work as required and protects the final active Admin. Password hashes are not recoverable plaintext passwords.

The [database dictionary](docs/database-design.md) maps the 35 application tables to their purpose and relationships. It includes safe inspection queries and migration policy. Existing client/contact and employer-company relationships are preserved. Apply migrations in a coordinated release: back up the database/documents, stop older writers, migrate, then restart the matching code. Local verification is not evidence that production has been upgraded.

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

### Durable manual intake

Selecting any Add Candidate file saves its original into private storage with a database job. Resume, passport and Aadhaar extraction starts only when requested; photo, education and experience documents can be saved without OCR. Recoverable sessions retain uploaded files, inspection results and human-edited form fields across reloads. The manual extraction worker checkpoints accepted provider jobs and resumes them after restart.

Review suggested fields, correct them and save/assign the candidate. Duplicate identity conflicts require an explicit merge/cancel choice. Saving attaches the original source bundle and generated document slots idempotently. Upload, extraction and attachment outcomes stay visible so retries do not silently duplicate candidates or overwrite documents.

## Zoho Mail and candidate intake

The CRM Inbox provides Zoho folders, paging/search, read/star state, message moves, archive/trash/restore, drafts, compose/reply/reply-all/forward, To/Cc/Bcc and attachments. Admin can connect the account later inside the CRM; configure the server encryption key first. Follow [email connections](docs/email-connections.md) for account-specific Zoho server settings. Earlier Gmail/Microsoft receipts remain historical; new connections and live mailbox operations are Zoho-only.

Drafts, attachment metadata, message state and immutable send attempts persist in PostgreSQL. Partial or unknown SMTP delivery is shown for review and is never automatically resent. Original bytes are retained in private local/S3 storage. A mailbox disconnect preserves existing evidence and history.

The independent email receiver and extraction worker retain originals before advancing their cursor. Candidate intake matches normalized phone, email or passport, preserves existing human-filled fields and ownership, and routes conflicts or uncertain documents to review. Bounded historical import does not reset the live cursor. Auto-ingest controls candidate extraction independently from mailbox viewing.

Run the complete backend from `backend/` with `python -m app.runtime`. It always starts the API, workflow, presence and manual document workers. Email workers start when `EMAIL_INGESTION_ENABLED=true`; WhatsApp starts when configured/enabled. Processes must share the same PostgreSQL database, storage and encryption key. See [operations](docs/operations.md) for storage identity, migration and backup procedures.

## Staff attendance and activity

Staff can check in/out from the application shell and Attendance page. The database retains sessions, browser activity intervals and Admin corrections. Admin/Manager see team records; Staff see their own. Online, idle and offline totals cover checked-in time, merge overlapping browser tabs and use the agency timezone. Logout stops presence without silently checking out. See [attendance policy](docs/attendance.md) for exact timing and permissions.

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
worker uses its own PostgreSQL-backed queue; task escalation, daily-update monitoring and reminder delivery run in separately supervised workers.

## Integration status and operations

The CRM now includes candidate create/edit/review and duplicate handling; server-side Data Bank search and skill matching; master-data editing and agreements; all placement stages, reopening and repeat medical attempts; durable document uploads/OCR; persisted task monitoring and notifications; private to-dos; scoped database reports with XLSX/PDF export; and Admin user lifecycle/audit history. Screens share responsive Poppins styling and a redesigned login.

Zoho mailbox operations, WhatsApp Business intake and local/S3 document storage are implemented behind server configuration. Provider calls are covered with controlled adapters and database tests; live Zoho connection, inbound delivery, outbound send and S3 acceptance still need configured test services. No production migration or live communication is implied by local verification.

Read [the requirement matrix](docs/requirements-matrix.md) and [integration audit](docs/integration-audit.md) for detailed evidence and remaining product differences. Use [the database dictionary](docs/database-design.md), [release/storage operations](docs/operations.md) and [email connection setup](docs/email-connections.md) to maintain and deploy the project.
