# Deployment provider setup and operational acceptance

This guide keeps the application, database, background workers and document storage consistent when releasing or recovering the CRM. It is a cross-module operational workflow, not a newly invented PRD feature. A local screen/test pass does not connect a production provider or deploy the application.

## Roles and preparation

The deployment operator needs the intended application release, access to its environment/secret store, PostgreSQL backup/restore facilities and the existing document storage. CRM Admin handles supported mailbox/channel connection and administrative acceptance inside the application. Staff/Manager accounts are used to verify their actual role scopes after deployment.

Keep credentials in server secrets. Confirm which database an environment uses before starting a local backend or running migrations: `localhost` in the browser does not prove the backend database is local. `CRM_ENV_FILE` can select a complete isolated configuration; an explicitly missing file fails instead of silently falling back. Synthetic tests use separate loopback test databases with providers disabled.

## User journey

### Release the matching application and database

1. Review the final tested application changes and migration files. Build the frontend/backend from the same release. Preserve the previous release for recovery.
2. Back up PostgreSQL, referenced uploaded documents and the required encryption/configuration secrets as one recovery set. Verify the backup can restore into an isolated environment.
3. Confirm the target database, persistent uploads/S3 configuration, agency timezone, domain/HTTPS and container port. Preserve the existing uploads volume; a new empty volume cannot serve old attachment records.
4. Stop older API instances and workers writing to that database, including developer backends accidentally using the same database. Keep PostgreSQL running.
5. Apply only the pending schema migrations in the configured deployment environment. The current source head is `0016_intake_communications`, which adds acknowledgement, person/page review and outgoing WhatsApp records. Earlier applied revisions are not repeated.
6. Start the matching complete runtime. Check `/api/health`, sign in, open a historical candidate/document, upload a new document and verify the background workers are running.
7. Exercise the module acceptance checks using dedicated test records and authorized provider accounts. Return normal traffic only after application/data compatibility and critical workflow checks pass.

Alembic records which revisions have already been applied. Running `upgrade head` applies pending revisions, then becomes a no-op when the database is current. It does not migrate every record on every browser refresh. A new schema-changing release needs a coordinated upgrade; a normal frontend refresh or a release with no schema change does not need a fresh migration.

## Startup paths and database readiness

| Startup path | Actual behavior |
| --- | --- |
| Local `npm run dev:backend` | Runs the complete supervisor with API reload. It checks the schema before starting services and does not apply migrations automatically. Restart after worker code changes. |
| Nixpacks production | Builds the frontend without connecting to the database. Startup validates static assets and matching database revision. `RUN_MIGRATIONS_ON_START=1` enables an explicit coordinated upgrade; remove the temporary setting afterward. |
| Repository Docker image | Runs `alembic upgrade head` before the supervisor. Pending migrations apply once; already-applied revisions are skipped. Review and back up before redeploying this path against an existing database. |
| Direct `uvicorn app.main:app` | Serves HTTP only. It does not run the workers required for extraction, monitoring, reminders or cover. Use the supervisor for the complete CRM. |

For an explicit deployment upgrade, the existing runbook uses `python -m alembic -c backend/alembic.ini upgrade head` from the repository root in the intended deployment environment. It must not be run casually from a developer shell pointing at a shared production database. Never substitute `alembic stamp` for actually applying missing changes.

A schema mismatch shows a clear migration-required readiness state; the supervisor refuses to start API/workers against it. A screen may show the database-update message/HTTP 503. Refresh can confirm a corrected release, but cannot install tables. Read current/expected revision and backend logs, deploy matching code/schema, then retry. Do not assume an old startup log establishes the database's current revision.

## Runtime and background work journey

1. Launch `python -m app.runtime` through the selected production startup path. It starts API, workflow monitor, presence/leave-cover worker and manual document extraction worker.
2. Enable email ingestion only when its configured receiver/extractor should run. They start independently of manual document extraction. Enable WhatsApp only for the intended configured channel.
3. Watch startup logs for database readiness and each child service. On Windows, supervised services keep logs in the npm terminal while running without separate visible windows.
4. Verify an overdue task/daily-update scan and a due personal reminder. Task monitoring scans every 15 minutes; reminders have a separate 30-second scan, followed by the browser's notification polling delay.
5. Verify presence expiry and accepted leave-cover transfer/return. These run every 30 seconds in separate worker transactions. A page load does not substitute for this worker.
6. Restart the runtime during a controlled pending extraction or reminder. Durable PostgreSQL job/delivery state must recover without duplicate candidate/task/notification effects.

If a supervised child exits, the supervisor stops the service group so the process/container manager can restart a consistent runtime. Running only a healthy HTTP endpoint is insufficient evidence that worker-dependent features are progressing.

## Provider configuration and live acceptance

| Service | Configuration and end-to-end acceptance |
| --- | --- |
| Zoho Mail | Admin connects the intended account with its actual region/account type and authorized app password. Keep `MAIL_TOKEN_ENCRYPTION_KEY` stable and backed up. Verify TLS connection, folders, messages, attachments, received candidate intake and one explicitly authorized send/reply. Check Sent copy and uncertain delivery handling before retrying. See [Zoho workflow](08-zoho-inbox.md). |
| WhatsApp Business | Configure the intended Business phone, access/app/verification secrets, supported Graph version and public HTTPS webhook. Complete verification, test signed incoming document delivery, duplicate delivery, extraction and assignment. Verify the Conversations composer, approved template sync, delivery callbacks and per-channel acknowledgement settings using authorized test recipients. See [WhatsApp workflow](09-whatsapp.md). |
| Document extraction | Confirm supported local OCR tools/languages and configured remote OCR when used. Submit representative real test resume/passport/document files, review quality and recover a failed job. Test PDFs and image scans. For multi-person bundles, verify proposed groups, page assignment/exclusion, duplicate resolution and confirmed per-person splitting. Synthetic fixtures do not prove all real OCR quality. |
| Local storage | Mount persistent `STORAGE_DIR` for every API/worker that needs the files. Verify historical and new documents remain available across restart/redeploy. |
| S3 storage | Configure private bucket/region/endpoint/access and supported immutable conditional writes. Verify upload, authenticated download, integrity and restart. Changing `STORAGE_BACKEND` does not copy old local documents. |
| Redis | Optional. An empty `REDIS_URL` means intentionally disabled; a configured unreachable instance makes readiness degraded. PostgreSQL still owns sessions and durable work queues. Redis does not replace the database. |

Real provider credentials, production account authorization, inbound webhook delivery, outbound email and S3 acceptance remain environment-specific live checks. Local adapter/tests cannot mark those steps complete. The account-specific server settings shown in Zoho must take precedence over guessing a region from the email address.

## Storage change and recovery journey

1. Inventory referenced files using the documented `app.storage_admin audit` command. A database backup without its referenced file bytes is incomplete.
2. For local-to-S3 migration, configure the target while retaining the source volume, run the dry-run migration and review the audit/output. This is a file-storage migration, separate from Alembic schema revisions.
3. Back up data and stop every writer before the explicit `--apply --writers-stopped` operation described in [operations](../operations.md). The command copies immutable objects, reads them back to verify hashes, then updates database references together only after successful copying.
4. If the operation fails, retain the source volume and inspect the report. References remain unchanged; verified copied immutable objects can be reused on retry. Do not delete originals simply because some target files exist.
5. Start all services with the final storage configuration, audit again and open both historical and new documents in the CRM. Keep the old volume/backup until the agency's recovery policy permits removal.
6. For disaster recovery, restore into an isolated database/storage namespace with provider workers disabled. Restore the required encryption key, verify login/roles and files, then intentionally reconnect services before enabling delivery.

## Screen and backend contract

Readiness reports database compatibility and configured-service health without exposing connection secrets. When schema is unavailable, the frontend shows a service/update error and retry control instead of pretending empty lists mean no data. A failed provider operation shows the relevant connection/job/send state; retry must preserve the specific operation's idempotency and uncertainty rules.

API and frontend must agree on response fields, permissions and actions. Workers must use the same database, encryption configuration and document storage as the API. All application replicas share persistent document storage and the stable token-encryption key. Release acceptance includes role boundaries and cross-screen consistency, not just whether a page renders.

## Recovery and cancellation

If startup fails, inspect the explicit failing service/readiness state and stop repeated manual retries that cannot change it. Correct missing configuration or release/schema mismatch first. A migration failure stops startup; inspect the failing revision and transaction outcome before retrying. Do not reset, drop or recreate a production database to make a screen load.

Rollback is a coordinated code/schema/data decision. Review the relevant downgrade and compatibility before using it; restoring the verified backup may be required. Switching to an old application image alone is not a guaranteed rollback after an incompatible schema change. A cancelled release should preserve backups, previous code, file volumes and secrets.

Unknown SMTP delivery is not a confirmed failure: inspect the send attempt/provider copy before another explicit send. Provider reconnection must retain existing mirrored history. Retry extraction/recovery through its existing job/intake controls so a restart or lost response does not create a second candidate.

## Acceptance checklist

- [ ] Confirm the intended deployment database and prevent local test/development writers from using it accidentally.
- [ ] Back up and restore-check database, referenced files and required secrets as one recovery set.
- [ ] Apply only pending schema revisions; verify current revision equals this release's expected head.
- [ ] Confirm a second startup with no pending revisions does not reapply historical migrations.
- [ ] Verify matching frontend/API, HTTPS, login, role scope and explicit unauthorized API rejection.
- [ ] Open a historical document, upload a new one and verify both after restart/redeploy.
- [ ] Verify task monitoring, personal reminders, manual extraction, presence expiry and leave-cover worker operation.
- [ ] Complete real Zoho connection/intake and an explicitly authorized send with delivery-state checks.
- [ ] Complete WhatsApp webhook verification and signed real inbound document intake when that channel is enabled.
- [ ] Verify configured OCR quality/recovery and selected local/S3 storage with real test files.
- [ ] Rehearse worker restart, response loss and recovery without duplicate business records.
- [ ] Rehearse a compatible rollback or backup restore without losing existing documents/history.

## Current implementation and remaining acceptance

The runtime supervisor, schema readiness, durable queues, release paths and storage tools are implemented. The current local verification results are recorded in the [integration audit](../integration-audit.md) and [performance review](../performance.md). Tests use isolated synthetic data; the latest read optimizations require no new schema revision. Production deployment/migration and real provider configuration/acceptance remain separate pending operational actions; this documentation does not perform them.

Detailed configuration and recovery commands: [operations](../operations.md), [README deployment](../../README.md#dokploy-deployment-with-nixpacks), [integration evidence](../integration-audit.md). Implementation: [runtime supervisor](../../backend/app/runtime.py), [schema readiness](../../backend/app/db/schema_readiness.py), [production launcher](../../scripts/start-production.py), [Docker startup](../../Dockerfile).
