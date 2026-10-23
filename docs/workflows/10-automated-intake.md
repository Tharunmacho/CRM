# Automated ingestion and review recovery

The intake chain is receive → preserve original → extract → identify → save or enrich → assign → initial call. All channels write to the same CRM candidate store. Source: PRD pages 6–7. Requirements: **INGEST-01 through INGEST-07**.

## Roles and preparation

Admin configures sources and reviews failures/ambiguities. Staff work the resulting assigned candidates and corrections. Required services are the API, receiving worker, extraction worker, configured storage/OCR and eligible Staff. Email receiving runs independently from slow extraction; the configured fallback receive cadence is two minutes.

## User journey

1. A resume reaches a connected, enabled email account or WhatsApp channel. The system records the receipt and original before acknowledging processing progress.
2. A queued job classifies/inspects the file and requests the relevant structured extraction. The app remains usable while this runs.
3. Parsed identity, location, designations, experience, skills and education are reviewed by the shared mapping rules. Passport documents become passport evidence rather than an invented resume.
4. The system searches normalized phone **or** email **or** passport. One clear match enriches only empty fields and attaches a new version; existing ownership, task clocks and human corrections remain.
5. No match creates a candidate. Low confidence, missing required identity or extraction warnings produce visible Needs review. No eligible recruiter leaves an unassigned review record rather than a fictitious owner.
6. A valid new assignment creates the initial call and notifications. A duplicate does not consume another rotation turn or create a second initial-call task.
7. Admin opens failed/review items from source health. Inspect the original, extraction evidence and error. Retry a transient failure, link an ambiguous receipt to the correct record with a reason, or explicitly ignore it.

## Retry and review states

The durable job records its original, checkpoint and provider identity. The implemented limit is the initial attempt plus three automatic retries with backoff; provider-pending polls do not spend a failure attempt. Exhausted failures remain available for Admin retry. A browser refresh is not a retry command and must not lose the receipt.

When identifiers point to different existing people, intake must not select the first match. Keep the receipt in review. Linking evidence should not overwrite another person's validated profile fields. Known multi-person sources stop before combined extraction. A durable person/page review proposes groups, requires confirmation, and atomically saves each profile with only its selected pages. Ambiguous pages remain review work; see [the split workflow](../communications-and-split-intake.md).

## Screen and backend contract

Show received, queued, processing, ready/linked, needs review, failed, ignored and paused/disconnected states where relevant. The candidate link and provenance must be stable across retries. Content-hash reuse checks completed evidence and stored bytes before attaching cached output.

Database writes, assignment and task creation use transactions/locks so concurrent receipts cannot create duplicate identities or rotate twice. A slow provider cannot block two-minute receiving. Disconnect pauses live work without erasing checkpoints. A page read never silently reassigns candidates.

## Acceptance checklist

- [ ] Receive one manual, one email and one WhatsApp profile and find all in the same Data Bank.
- [ ] Deliver the same identity concurrently through two channels; retain one candidate and correct provenance.
- [ ] Verify duplicate enrichment preserves human values, owner and deadline.
- [ ] Exercise no eligible Staff, low confidence and conflicting identity review.
- [ ] Restart a worker during a provider job; resume the checkpoint without duplicate effects.
- [ ] Exhaust automatic failures, inspect the error and complete a deliberate Admin retry.
- [ ] Preview and enable the optional acknowledgement on a test channel, confirm one acknowledgement after a successful save, and verify review/failed/historical receipts do not send it. Turn it off and confirm queued confirmations pause.

## Current implementation and remaining acceptance

Durable receiving/extraction, retry, conservative duplicate handling and review paths are implemented with isolated tests. Real Veriis/Zoho/WhatsApp behavior and restart/delivery under deployment remain live acceptance. **INGEST-07 is implemented as opt-in, per-channel acknowledgements**, initially off and queued only after complete candidate intake. Automatic/bulk mail and historical imports are suppressed. Unknown delivery is never blindly retried. Explicit replies remain a separate user action.

Implementation: [email intake](../../backend/app/services/email_intake.py), [WhatsApp intake](../../backend/app/services/whatsapp_intake.py), [identity rules](../../backend/app/services/candidate_identity.py), [document extraction worker](../../backend/app/document_extraction_worker.py). Original preservation: [Documents](06-documents-ocr.md).
