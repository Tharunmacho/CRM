# Documents extraction and file versions

This module preserves the original evidence, extracts useful suggestions and keeps every replacement as a separate version. It serves manual entry, automated intake, employer agreements and recruitment stages. Source: PRD pages 4, 6 and 9. Requirements: **INGEST-02, INGEST-03, FILE-01**, with entry behavior under CAND-01.

## Roles and preparation

Authenticated users can view permitted originals. Candidate uploads require the owner or supervisor; the initial creator has a limited attachment window after automatic assignment. Stage files must belong to the correct candidate application, stage and field. Provider credentials and storage configuration belong on the server.

## User journey

1. Upload the original PDF, DOC, DOCX, JPG or PNG through the appropriate candidate/source field. Wait for storage confirmation before treating it as preserved.
2. For resume/passport/Aadhaar extraction, inspect the reading status: queued, processing, ready, needs review or failed. Supporting photo/education/experience files can be retained without unnecessary identity OCR.
3. Review detected document type and suggestions. Mixed-document handling should select the relevant pages, not send unrelated pages as the requested document.
4. Apply and correct suggestions, retaining the original extraction evidence. Passport suggestions can take precedence for unedited identity fields; manual corrections remain authoritative.
5. Save or finish the candidate intake. Open the profile's documents and download the original to confirm it is the uploaded file.
6. Replace a file when a corrected resume or new passport arrives. Inspect version history rather than expecting the old bytes to be overwritten.
7. In a stage, upload through that stage's file field and wait for its genuine saved attachment before completing the stage.

## Screen and backend contract

Original storage records provenance, checksum and immutable versions. Duplicate content can reuse verified processing where allowed, but every source receipt keeps its own provenance. Storage supports the configured local/S3 adapter. Checksums guard reuse of missing or altered cached evidence.

Durable document jobs keep checkpoints so browser reload or worker restart does not discard the original or redo a committed save. The backend validates actual attachment ownership/association; a fabricated file-shaped object or another stage's attachment cannot satisfy a required upload. Every published replacement retains actor/time/version history.

## Recovery and handoffs

A reading failure must leave the original available and offer the permitted retry/manual-entry path. Do not require OCR success to save valid manually entered details. If a candidate saved before an upload failed, retry against that candidate. If upload succeeded but stage completion failed, retain the attachment and correct the completion error. Do not manufacture a new version merely to retry an uncertain completion.

## Acceptance checklist

- [ ] Fail the profile's document-evidence read, verify the saved profile remains usable, and use **Retry document evidence** to restore extracted details without uploading again.

- [ ] Upload each supported source format and inspect its original download.
- [ ] Exercise a mixed resume/passport document and an unsupported/corrupt file.
- [ ] Reload during processing and resume the same durable job.
- [ ] Replace a document twice; all original versions remain available.
- [ ] Attempt to complete a stage with a foreign or fabricated attachment; it is rejected.
- [ ] Validate real converter/OCR dependencies and storage restore on the target environment.

## Current implementation and remaining acceptance

Immutable storage, durable manual jobs, selected-page extraction and attachment association checks are implemented with synthetic/mocked-provider tests. Live Veriis quality, real DOC/DOCX conversion, S3 retention and restore require configured acceptance. No synthetic test proves a real candidate's OCR accuracy.

Implementation: [file API](../../backend/app/api/routers/files.py), [document jobs](../../backend/app/api/routers/document_jobs.py), [storage service](../../backend/app/services/document_storage.py), [manual extraction](../../backend/app/services/manual_extraction.py). Recovery continues in [automated intake](10-automated-intake.md).
