# Communication and multi-person intake

These features are implemented in the CRM code. Production activation and delivery must be checked with the configured accounts after deployment.

## Automatic acknowledgements

1. An Admin opens **Inbox → Mailbox accounts** or **WhatsApp → Channel settings**.
2. Under **Intake acknowledgements**, edit the message, review the preview and explicitly enable it for that mailbox/channel. It starts off.
3. A new eligible source receipt is processed and its candidate records/documents are saved successfully. Receipts still in review do not get a success acknowledgement.
4. The candidate transaction creates one acknowledgement intent per source message. A confirmed multi-person source still gets one acknowledgement, not one per candidate.
5. The worker sends the saved text. Delivery history shows pending, sending, sent, failed, paused, suppressed or unknown.
6. Disconnecting the account or disabling acknowledgements prevents queued automatic sends. Old receipts and historical imports are not acknowledged retroactively.
7. An Admin can retry a definite email failure. For an uncertain result, check the provider before marking it sent or suppressed. The application does not automatically resend uncertain messages.

Email acknowledgements require original sender/safety headers. Automatic messages, bulk/list messages, null/ambiguous return paths and internal/self senders are suppressed to avoid reply loops. SMTP acceptance is not proof of final recipient delivery. Automatic replies use the precautions in [RFC 3834](https://www.rfc-editor.org/rfc/rfc3834).

WhatsApp acknowledgements use the same durable outbox and current reply window as ordinary chat. If processing finishes after the window closes, the saved candidate remains valid and the acknowledgement shows a failure; an authorized user can follow up with an approved template.

## Documents containing several people

1. Open **Add candidate → Documents → Review people in documents**, **Inbox → Candidate documents → Review people in documents**, or **WhatsApp → Candidate intake → Review people**.
2. The source is reserved for this review. Its original files are preserved, and the ordinary single-person retry/link path cannot attach that mixed source.
3. The document worker reads page evidence locally and proposes person groups where identities are sufficiently clear. The API returns promptly; preparation progress and errors are saved.
4. Review each page preview, the proposed identity, and the editable candidate fields. Add or remove groups and move pages to the right person.
5. Every page must belong to one person or be explicitly excluded with a reason. A page known to contain conflicting identities must be excluded and reuploaded as separate pages; the CRM does not crop people out of a page.
6. Save incomplete review work and return later. Conflicting edits are rejected using a review revision.
7. Confirm the final groups. If an identity already exists, explicitly choose the matching profile. Staff can merge only into profiles they are permitted to edit.
8. One transaction creates or enriches the reviewed candidate records, assigns eligible recruiters, creates initial-call tasks and attaches only that person's selected pages.
9. Retrying a successful commit returns the same candidate IDs. Source history lists every resulting profile; completed stages and existing ownership are preserved when merging.
10. Manual entry resets to a new form after completion. In either candidate profile, the original uploader can use **Your original document intakes** to reopen the completed review or download the unchanged source files. These private manual originals are not exposed to other users.

The detector is conservative: poor scans or unclear identities still need human review. This is automatic detection and grouping with confirmed splitting, not unattended creation from ambiguous documents. Preparation uses the existing document worker and local PDF/OCR tools; it does not require a paid extraction call to propose groups.

## Outgoing WhatsApp

1. Configure the intended Business phone and open **WhatsApp → Conversations**.
2. Select an incoming contact or start a conversation using the full number with country code.
3. A supervisor can link the correct candidate with a reason. A sender is not automatically treated as the candidate, since an agent may send several people's documents.
4. Admin/Manager may send; a Staff user may send for the explicitly linked candidate they currently own. Other viewers see a read-only explanation.
5. Within the reply window, send text or a supported image/document/audio/video attachment. Outside it, choose an approved template and fill its parameters.
6. Admin uses **Sync templates** to load the provider catalogue. Supported templates have positional text body parameters and optional static content; unsupported dynamic/media template formats are visibly unavailable.
7. Each click saves a durable send intent before contacting Meta. A lost API response can recover the same request, including after a page reload. It does not create a second send.
8. The outbox runs separately from document extraction. Permission, channel state, reply window and template approval are checked again before sending.
9. Signed delivery callbacks update accepted/sent/delivered/read/failed status. Out-of-order and duplicate callbacks do not roll a delivered/read message backward. Uncertain sends stay stopped pending reconciliation.

The connection requires a Meta business portfolio, WhatsApp Business Account and Business phone. The token needs the appropriate messaging/management permissions described in [Meta's Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api). The customer reply window follows the 24-hour behavior documented in [Meta's SDK quickstart](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/); that SDK itself is archived and is not used by this implementation.

## Deployment and acceptance

Apply migration **0016_intake_communications** together with this application version, then restart the API and workers. The migration adds communication/review records and safe original mail metadata, and backfills existing WhatsApp conversations without guessing candidate links or queueing messages. Existing candidate and receipt records remain.

Use the existing Zoho connection. WhatsApp needs the existing server-side token, phone ID, app secret, verification token and supported Graph version; add **WHATSAPP_BUSINESS_ACCOUNT_ID** for template synchronization. Keep secrets on the server. Automatic acknowledgements remain off until the Admin enables a previewed message.

Before marking live acceptance complete, use authorized test recipients to verify receive → review/save → acknowledgement, ordinary chat → signed delivery state, an approved template, a disconnected channel, and a multi-person PDF with each resulting profile's own pages. Local automated checks use synthetic data and mocked providers; they do not establish production delivery.

