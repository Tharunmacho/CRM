# WhatsApp receipt and candidate intake

WhatsApp receives candidate documents/images through the configured business channel and hands them to the same intake workflow as email. Source: PRD page 6, with a later dedicated source-management screen. Requirements covered with intake: **INGEST-01, INGEST-02, INGEST-04**.

## Roles and preparation

Admin manages connection, automatic intake, review and recovery. The backend must already have the intended business phone/channel credentials and webhook configuration. A number typed into the UI does not create an independently configured business channel.

## User journey

1. Admin opens **WhatsApp**, verifies the configured business number and explicitly connects it.
2. Send a test document or image to that business number. Confirm that the receipt appears with sender/message/time provenance and the original file.
3. With automatic intake enabled, inspect queued/processing/ready/review/failed status. Open the candidate once processing succeeds.
4. Check the candidate's identity against the extracted document. The sender number may fill an empty WhatsApp contact; it must not automatically become the candidate's duplicate identity.
5. For skipped or failed messages, use the permitted Process/Retry action. For ambiguous matches, review and link evidence with a reason instead of creating an arbitrary duplicate.
6. Search/filter message history by the supported status and paging controls. Inspect originals without needing to repeat provider delivery.
7. Disconnect the channel when required. Confirm new live work stops while stored receipts, originals and processing checkpoints remain available for later recovery.

## Screen and backend contract

The webhook verifies authenticity and accepts documents/images only for an enabled, explicitly connected configured channel. Replay of the same provider receipt must not duplicate a candidate or task. Media retrieval validates the expected provider host and file limits.

Same-channel completed content hashes may reuse verified extraction, while preserving each receipt's provenance. Candidate identity uses phone, email or passport from candidate evidence; sender contact alone must not merge unrelated people who used the same sender. Duplicate enrichment preserves existing owner and populated human corrections.

## Recovery and handoffs

If the provider or media download fails, the durable receipt explains the processing error and supports allowed retry. An unconfigured or disconnected channel cannot quietly ingest messages. A reviewer can link retained evidence or ignore it with a reason. Resume parsing, assignment and initial calling follow [intake](10-automated-intake.md), [assignment](11-staff-assignment-directory.md) and [initial call](12-initial-call.md).

## Acceptance checklist

- [ ] Verify one real signed provider receipt and reject an invalid signature.
- [ ] Replay the same message and confirm one processing result/candidate assignment.
- [ ] Send two different candidates through one sender; they are not merged by sender number.
- [ ] Test duplicate document reuse while retaining both message receipts.
- [ ] Disconnect with queued work and verify evidence/checkpoints survive.
- [ ] Confirm source-health timestamps and failure links reflect the actual worker.
- [ ] Send text and an attachment within the reply window; verify signed sent/delivered/read updates and replay the same request without a duplicate.
- [ ] Outside the reply window, select an approved template, review filled parameters and confirm the saved message text matches the provider template.
- [ ] Verify only supervisors or the explicitly linked candidate's assigned Staff can send, including after reassignment.
- [ ] Interrupt a send response, recover the same request after reload and confirm uncertain provider outcomes are not automatically retried.
- [ ] Review a multi-person source, save each candidate's own pages and find every resulting profile from the source receipt.

## Current implementation and remaining acceptance

Message results are tied to the signed-in user, query, status and page. Changing filters clears the previous result immediately; a failed refresh labels a retained result as previously loaded. Initial loading is not displayed as a zero count. Visible/focused views refresh, pending writes prevent overlapping actions, and retry controls preserve the chosen filters.

Receipt verification, explicit connection, originals, intake, duplicate handling and review paths are implemented with isolated tests. Live WhatsApp delivery, provider permission/configuration and deployment acceptance remain pending. The Conversations tab provides text/media sends, approved templates, candidate linkage and durable delivery history. Channel settings provide opt-in acknowledgements; Candidate intake opens reviewed person/page separation. See [the complete workflow](../communications-and-split-intake.md).

Implementation: [WhatsApp screen](../../frontend/src/screens/inbox/whatsapp.tsx), [WhatsApp API](../../backend/app/api/routers/whatsapp.py), [intake service](../../backend/app/services/whatsapp_intake.py).
