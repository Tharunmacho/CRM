# Zoho Inbox drafts and sending

Inbox connects mailbox work to candidate intake while preserving the originating account. The original PRD page 3 requested Gmail, Microsoft and IMAP/SMTP connections; the user's later Zoho-only instruction supersedes new Gmail/Microsoft setup. Requirements: **INBOX-01, INBOX-02, INBOX-03, MAIL-01, MAIL-02, MAIL-03, MAIL-04**.

## Roles and preparation

Admin connects, reconnects and disconnects accounts, controls automatic intake and imports history. Authenticated mailbox users can use permitted shared-mailbox actions. Private CRM drafts/send recovery belong to their author; synchronized Zoho Drafts are shared provider content. Use a configured test mailbox before accepting real sending.

The supplied `jobs@careindiaoverseas.com` screenshot showed `imappro.zoho.com` and `smtppro.zoho.com`. Select the datacenter matching the account's displayed server configuration; do not choose India simply because the company is in India. Enable IMAP and enter a generated app password only in the CRM connection form.

## User journey

1. Admin opens **Inbox → Connect Zoho Mail**, enters mailbox address, correct region, organization/personal account type and app password, then connects. A failed live connection test remains visible for correction.
2. Choose a mailbox/folder, search and move through its pages. Open a message, inspect read/unread state and preview/download originals.
3. Enable **Auto-ingest resumes** for future incoming attachments when ready. For a skipped message, use **Create candidate from attachment**. Follow the resulting candidate badge to its profile.
4. Reply from the account that received the message. Use Reply all or Forward deliberately and review recipients and retained attachments.
5. Compose a message with To/Cc/Bcc, subject/body and attachments. Save a private CRM draft, reopen it and inspect the exact version before sending.
6. After Send, read the outcome. A successful SMTP submission is not proof of final delivery to every recipient. Unknown or partial outcomes require reconciliation before any resend.
7. Manage supported folder operations: create a folder, read/unread, star, move, archive, trash and restore. A failed or uncertain provider operation must not be displayed as confirmed success.
8. To inspect older mail, Admin starts an explicit since-date historical import in bounded batches, optionally enabling OCR. Pause/resume or request the next batch without rewinding future-mail intake.
9. Disconnect when necessary. Stored evidence, drafts and receipt history remain; live receiving/sending work stops as defined by the connection state.

## Screen and backend contract

Live provider search/folders and stored CRM-message history are distinct views. Attachment filtering over stored history does not claim to search every unopened remote message. Account, search, read state and paging must agree with the shown total.

Credentials are encrypted server-side, never returned or saved in browser state. Send attempts capture a stable key and draft version. Up to 20 attachments and 18 MB combined are supported by the current composer. A newer edit in another tab must not be silently replaced by an older send. Historical import supports 1–500 messages per batch with separate checkpoints and OCR off by default.

## Recovery and handoffs

Reconnect invalid credentials through the form. Retry a definitely rejected unsent action only after correcting it. For a lost send response, open send history/reconciliation rather than automatically submitting again. Resume saved drafts after refresh. Intake failures follow [automated intake](10-automated-intake.md); candidate ambiguity goes to reasoned review.

## Acceptance checklist

- [ ] Connect the correct real Zoho datacenter and verify receive, folders and same-account reply.
- [ ] Exercise compose, reply-all, forward and attachments, including Cc/Bcc-only delivery.
- [ ] Lose a send response and verify recovery does not duplicate mail.
- [ ] Edit the same draft in two tabs and handle version conflicts clearly.
- [ ] Import two historical batches without moving the live incoming cursor.
- [ ] Disconnect/reconnect without losing receipt provenance, drafts or documents.

## Current implementation and remaining acceptance

Stored Inbox polling runs every 15 seconds when idle and every five seconds while its visible jobs are queued, processing or retrying. Explicit refreshes and post-write reads remain immediate. Opening an already loaded candidate navigates directly to the profile, whose fresh-record check owns recovery. Newly ingested candidates still refresh the desk before navigation.

These common mailbox paths are implemented with isolated adapters/browser tests. Real credentials, provider limits, delivery and restart acceptance remain pending. Opt-in acknowledgements and reviewed multi-person splitting are connected to mailbox accounts and source documents; see [the complete workflow](../communications-and-split-intake.md). Permanent purge, folder rename/delete and rich-text composition are not implemented. See [mail setup and limits](../email-connections.md).

Implementation: [Inbox screen](../../frontend/src/screens/inbox/inbox.tsx), [Zoho API](../../backend/app/api/routers/zoho_mailbox.py), [compose API](../../backend/app/api/routers/mail_compose.py), [mail service](../../backend/app/services/zoho_mail.py).
