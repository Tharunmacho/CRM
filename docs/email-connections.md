# Mailboxes in the CRM

The Inbox is one unified inbox for **any number of mailboxes**; there is no account limit. An administrator connects each one inside **Inbox → Accounts → Connect mailbox**:

| Source | How it connects | Where credentials live |
| --- | --- | --- |
| **Gmail** | Google OAuth sign-in (IMAP/SMTP scope) | Encrypted refresh grant on the server |
| **Microsoft 365** | Microsoft OAuth sign-in (IMAP/SMTP scope) | Encrypted refresh grant on the server |
| **Zoho Mail** | Address, app password, data center and account type | Encrypted on the server |
| **Other (IMAP / SMTP)** | Address, password, IMAP and SMTP servers and ports | Encrypted on the server |

Mail from all accounts appears together with an account filter, search, unread state and an attachment icon. Opening a message previews its attachments and replies from the same account. Each account has its own **Auto-ingest resumes** switch; when on, attachments enter the ingestion pipeline automatically. **Create candidate from attachment** is available on any email, for mail that auto-ingest skipped, and each email shows a badge linking to the candidate it produced.

Credentials are encrypted in the database and never returned by the API or written to audit data. Connections always use TLS (IMAP 993 SSL or 143 STARTTLS; SMTP 465 SSL or 587 STARTTLS). Custom IMAP servers must resolve to public addresses; loopback, private-network and link-local hosts are rejected.

## Connecting Gmail and Microsoft 365

Register one OAuth application per provider, then set its values on the CRM server (never in `VITE_` variables):

* **Gmail**: Google Cloud Console → OAuth client of type *Web application*. Add the authorized redirect URI `https://<crm-host>/api/inbox/oauth/gmail/callback` and request the Gmail scope `https://mail.google.com/`. Set `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` and `GMAIL_REDIRECT_URI`. While the app is in *Testing*, only listed test users can sign in and grants expire after seven days; publish the app (and complete Google's verification for restricted scopes) for permanent grants.
* **Microsoft 365**: Microsoft Entra → App registration (web). Redirect URI `https://<crm-host>/api/inbox/oauth/microsoft/callback`. Delegated permissions `IMAP.AccessAsUser.All`, `SMTP.Send` and `offline_access`. Set `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_REDIRECT_URI` and, for a single organization, `MICROSOFT_TENANT`. The tenant must allow IMAP and authenticated SMTP for the mailbox.

The redirect URI must use HTTPS (plain HTTP is accepted only for `localhost`) and end with exactly the path above. When the browser app and API run on different origins (local development), set `APP_BASE_URL=http://localhost:5173` so the browser returns to the CRM after consent. The connection form shows a provider as unavailable, with the missing setting, until these are configured.

Consent must finish in the same signed-in browser session that started it, within ten minutes, and can be used once. Cancelling consent connects nothing.

## Zoho Mail

Zoho connects with the mailbox address, an app password, the account type and its data center. No account credentials are bundled with the project. Disconnected accounts keep their messages, documents and receipt history.

## Connection and deployment

Run migrations through the current head. Configure a persistent `MAIL_TOKEN_ENCRYPTION_KEY` generated with Python `cryptography.fernet.Fernet.generate_key()`, stored in server secret configuration. Connected mailbox credentials are encrypted in the database and never returned by the API or included in audit data. Losing this key requires reconnecting accounts. Do not put secrets in `VITE_` variables. The browser connection form does not persist its password.

If the connection form says a valid `MAIL_TOKEN_ENCRYPTION_KEY` is required, configure the backend secret before retrying. This key is separate from the Zoho app password. Reuse an existing production key when the local backend shares that database; all API and mail workers reading the same encrypted records need the same key. Do not replace an established key merely to resolve a missing local setting.

For a first setup with no established key, generate it once in PowerShell:

```powershell
& 'M:\crm\backend\.venv\Scripts\python.exe' -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))"
```

Copy that output privately into `MAIL_TOKEN_ENCRYPTION_KEY=` in `backend/.env`, preserving the trailing `=`. Set the same value in the production backend's environment/secret store when it uses the same database. Restart the complete local backend with `npm run dev:backend` from the repository root; redeploy the hosted backend after updating its secret. `CRM_ENV_FILE` selects a different configuration file when set, and process environment values override dotenv values. Refresh Inbox and retry the connection. This configuration correction does not itself require another database migration.

Copy the exact incoming/outgoing hostnames from the mailbox's **Server Configuration Details** in Zoho. Personal and paid organization accounts can use different hosts, and the data center matters. The CRM allows matching `imap`/`smtp` or `imappro`/`smtppro` Zoho hosts for the selected region; arbitrary hosts and insecure ports are rejected. Defaults are suggestions and must match the account's displayed settings. [Zoho's server configuration guide](https://www.zoho.com/mail/help/imap-access.html).

| Setting | Supported value |
| --- | --- |
| IMAP | Port 993, SSL |
| SMTP | Port 465, SSL, or 587, mandatory STARTTLS |
| Region suffix | `com`, `in`, `eu`, `com.au`, `jp`, `ca`, `com.cn`, `sa` |
| Personal host pattern | `imap.zoho.<region>` / `smtp.zoho.<region>` |
| Organization host pattern | `imappro.zoho.<region>` / `smtppro.zoho.<region>` |

IMAP must be enabled by the account/organization policy. Zoho requires an app-specific password for accounts with MFA or relevant federated-login policies. Some free plans do not provide IMAP. Make the required folders visible in Zoho's IMAP folder settings. [Zoho IMAP access and limitations](https://www.zoho.com/mail/help/imap-access.html).

Connection verifies IMAP access and folder listing. Sending uses the same account credentials over secure SMTP; an SMTP-specific policy failure is reported on the send attempt. Zoho normally saves SMTP mail in Sent itself, so the CRM does **not** append a duplicate Sent copy. Keep Zoho's **Save copy of sent emails** enabled for Sent reconciliation. [Zoho SMTP authentication and Sent-copy behavior](https://www.zoho.com/mail/help/zoho-smtp.html).

Existing server-configured IMAP accounts remain supported when their endpoints use TLS on the standard ports. Their variables are `IMAP_SERVER`, `IMAP_PORT`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_USE_SSL`, `IMAP_FOLDER`, `SMTP_SERVER`, `SMTP_PORT`, and `SMTP_USE_SSL`, or per-account `EMAIL_ACCOUNTS_JSON` entries. New connections should use the in-app form so credentials are encrypted and retained independently of worker environment files.

Set `EMAIL_INGESTION_ENABLED=true` and `EMAIL_POLL_SECONDS=120` for candidate intake. `python -m app.runtime` supervises separate receiving and extraction workers, so OCR does not delay the receiving cadence. Individual processes are `python -m app.email_worker` and `python -m app.email_extraction_worker`; each uses a separate PostgreSQL advisory lock. Interactive mailbox access works independently of automatic candidate intake.

For local storage, intake binds to the `.email-storage-id` marker in the uploads volume. S3 binds to a stable hash of endpoint, bucket and prefix rather than each worker's local directory. Existing bindings are never silently reset. A backend/namespace change requires a verified migration of original, derived and draft/send-snapshot file references before updating the binding. Preserve old local mounts while retained records still reference them. The storage administration inventory includes draft attachments and immutable send snapshots.

## Mailbox and drafts

Selecting a connected account loads its real Zoho folders, server search and paginated messages. Authenticated mailbox users can create folders; retrying an already-created name does not create another copy. Folder names and UIDVALIDITY are scoped to each remote folder, separate from the INBOX candidate-intake cursor. Read/unread and star changes are made in Zoho and confirmed before local success; interrupted changes require reconciliation. Moves, Trash and Restore update the remote mailbox and preserve local candidate evidence. Archive moves into an Archive folder; it creates that folder when missing. This is an ordinary IMAP folder, distinct from Zoho's native archived-message view.

Live folders support provider-side read/unread filtering before pagination. Attachment filtering is exact in **Stored message history**, using persisted attachment records and message counts. That view includes messages already received, imported or viewed in the CRM; it does not claim to search unimported attachments across the complete Zoho mailbox. Its account, text, read and attachment filters are applied together before pagination.

MOVE is used when supported. Its fallback requires UIDPLUS and removes only the copied source UID; there is no broad EXPUNGE. An interrupted move retains its intent as an unconfirmed operation. Explicit reconciliation checks source and destination using the original Message-ID rather than blindly copying again. Servers without either safe facility return an actionable failure. Cached history remains available during outages, and live folder failures are shown explicitly.

Compose, reply, reply-all, forward and editing an existing Zoho draft use durable CRM draft records with optimistic versions. Reply-all excludes the mailbox itself and preserves the other visible recipients. Forwarding and remote draft editing preserve original attachments. Drafts support To/Cc/Bcc, plain-text bodies and up to 20 files totaling 18 MB before MIME encoding. Browser downloads use authenticated local/S3 storage routes; active document types are downloads rather than inline content.

Saving commits the CRM draft before syncing Zoho Drafts. A failed remote save retains the local text and attachments and exposes a retryable sync state. New remote versions have deterministic Message-IDs; the prior UID is removed only after the replacement is confirmed. Saved remote drafts retain Bcc; sent MIME exposes Bcc only in the SMTP envelope. Editing/discarding never sends mail.

CRM draft lists and send-attempt records are private to their creator. Draft search and pagination include older saved drafts and send history. Synchronized contents in the shared Zoho Drafts folder are visible to users with access to that mailbox. Another CRM user cannot concurrently adopt an already active CRM-managed remote draft. Folder reads are shared with authenticated CRM users, matching the recruitment inbox model.

## Explicit sending and recovery

Send records an immutable snapshot, including recipients and attachment storage keys, before contacting SMTP. Each user action has a stable request key and outbound Message-ID. Retrying that key returns the saved result without a second submission. Accepted mail is `sent`, confirmed rejection is `failed`, partial recipient acceptance is `partial`, and an interrupted submission is `unknown`. Stale in-progress attempts become unknown. None of these outcomes causes an automatic resend.

A partial result lists rejected recipients and locks the draft for review because other recipients may already have received it. An unknown send can be checked against Zoho Sent by its stable Message-ID. Finding a copy confirms presence in Sent, not delivery to every recipient. Missing Sent mail leaves the attempt unconfirmed. A confirmed failure can be edited and deliberately submitted with a new request key. Retained send snapshots keep their attachment bytes even if a failed draft is later changed.

## Candidate intake and historical import

New-mail receiving starts after the first successful connection and resumes the saved cursor on reconnect. Browsing old folders does not automatically run OCR. The INBOX receiver can still queue eligible new mail that was viewed before its next poll. Administrators can request historical INBOX batches from an explicit date, capped at 500 messages per request and processed 25 at a time after the normal new-mail pass. History has independent progress, pause/resume and error checkpoints; candidate extraction is opt-in for historical batches.

Original attachments and selected resume/passport/Aadhaar documents remain linked to their source receipt. Administrators may link a failed/review-required receipt to an existing candidate or ignore it with a recorded reason, without forced OCR or overwriting verified candidate fields. Candidate profiles retain the source message and review history. Disconnecting removes the local credential envelope and stops future mailbox requests while preserving these records. Revoke the app password in Zoho separately if access should also be removed there.

## Verification

`test_zoho_mailbox.py` exercises real disposable PostgreSQL transactions with mocked providers: encryption, provider restrictions, folder/flag/move behavior, private/versioned drafts, remote failures, attachments, reply-all/forward/remote-draft adoption, send concurrency, idempotency and ambiguous/partial outcomes. `test_zoho_adapters.py` checks IMAP wire commands and SMTP MIME/envelope behavior without network access. Existing intake, review, historical import and storage tests remain in the regression suite.

Deployment acceptance requires a Zoho account with IMAP/SMTP enabled, its verified data-center endpoints, configured encryption/storage and an explicitly authorized test send. These live-account checks remain pending until an administrator connects the account inside the CRM.
