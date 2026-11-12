# Documents extraction and file versions

This module preserves the original evidence, extracts useful suggestions and keeps every replacement as a separate version. It serves manual entry, automated intake, employer agreements and recruitment stages. Source: PRD pages 4, 6 and 9. Requirements: **INGEST-02, INGEST-03, FILE-01**, with entry behavior under CAND-01.

## Roles and preparation

Authenticated users can view permitted originals. Candidate uploads require the owner or supervisor; the initial creator has a limited attachment window after automatic assignment. Stage files must belong to the correct candidate application, stage and field. Provider credentials and storage configuration belong on the server.

## User journey

1. Upload the original PDF, DOC, DOCX, JPG or PNG through the appropriate candidate/source field. Wait for storage confirmation before treating it as preserved. Passport and Aadhaar accept several files, including front and back pages, together or one at a time. Each file has its own upload result; retrying failed uploads does not resend successful ones.
2. In candidate entry, the application form and the profile, add all readable files/pages first, then press **Extract details** (or the entry screen's reading action). Saving these uploads does not start extraction automatically. Upload and reading statuses are separate: a stored file can be Uploaded while it is waiting to be read. Adding another page remains available while an earlier file is being read.
3. Review each file's reading result and page coverage. Every page must be accounted for; continuation pages, including later resume employers and passport/Aadhaar back pages, must not disappear from the reading. Unresolved pages remain visible with their page numbers. Partial experience totals are labelled as incomplete, not presented as the whole resume.
4. Correct permitted suggestions while retaining the original extraction evidence. A wrong-person document fills no fields, stays available for inspection and shows **Wrong document** with an action to upload the correct file. It does not satisfy the accepted-document checklist and cannot be manually marked accepted or sent through Extract details again. Manual field corrections remain authoritative when a correct file is read later.
5. Save or finish the candidate intake. Open the profile's documents and download the original to confirm it is the uploaded file.
6. Replace a resume when a corrected version arrives, or add the next passport/Aadhaar file. Inspect retained files and version history rather than expecting old bytes to be overwritten. View and Download appear only for actual saved files.
7. Stages move manually after saved feedback and confirmation; documents are not mandatory stage gates. A pending checklist item can be marked uploaded manually with an optional note and attribution, then returned to pending when no file is attached. A manual mark creates no file, extraction or invoice; automatic invoicing still requires an actual ticket file.

## Screen and backend contract

Original storage records provenance, checksum and immutable versions. Duplicate content can reuse verified processing where allowed, but every source receipt keeps its own provenance. Storage supports the configured local/S3 adapter. Checksums guard reuse of missing or altered cached evidence.

Durable document jobs keep checkpoints so browser reload or worker restart does not discard the original or redo a committed save. Candidate form/profile uploads send `defer_extraction=true`; the form exposes `capabilities.deferredDocumentReading`. `POST /api/candidates/{id}/application-form/extract` with `attachmentIds` queues the selected owned files together and safely reuses existing jobs on repeated requests. Automated intake keeps its own ingestion rules; lead conversion uses the separate **Check resume** gate described in [Lead sheet](23-lead-sheet.md).

The backend validates actual attachment ownership/association; a fabricated file-shaped object or another stage's attachment is rejected if supplied. Every published replacement retains actor/time/version history. Application-form upload evidence and Aadhaar are optional; the empty Application form row does not create a pending item or reduce completeness.

## Recovery and handoffs

A reading failure leaves the original available and offers the permitted explicit retry/manual-entry path; a known wrong-person file requires a correct replacement instead. OCR success is not required to save valid manually entered candidate details, while an attached lead resume must pass its identity check before conversion. If a candidate saved before an upload failed, retry against that candidate. If upload succeeded but stage completion failed, retain the attachment and correct the completion error. Do not manufacture a new version merely to retry an uncertain completion.

## Acceptance checklist

- [ ] Fail the profile's document-evidence read, verify the saved profile remains usable, and use **Retry document evidence** to restore extracted details without uploading again.

- [ ] Upload each supported source format and inspect its original download.
- [ ] Exercise a mixed resume/passport document and an unsupported/corrupt file.
- [ ] Add front and back files one at a time: no reading starts until Extract details; every retained file and unresolved page remains visible.
- [ ] A rejected identity fills no fields, remains viewable, cannot be retried or manually accepted, and does not count as an accepted document.
- [ ] Reload during processing and resume the same durable job.
- [ ] Replace a document twice; all original versions remain available.
- [ ] Attempt to complete a stage with a foreign or fabricated attachment; it is rejected.
- [ ] Validate real converter/OCR dependencies and storage restore on the target environment.

## Current implementation and remaining acceptance

Immutable storage, durable manual jobs, selected-page extraction and attachment association checks are implemented with synthetic/mocked-provider tests. Live Veriis quality, real DOC/DOCX conversion, S3 retention and restore require configured acceptance. No synthetic test proves a real candidate's OCR accuracy.

Implementation: [file API](../../backend/app/api/routers/files.py), [document jobs](../../backend/app/api/routers/document_jobs.py), [storage service](../../backend/app/services/document_storage.py), [manual extraction](../../backend/app/services/manual_extraction.py). Recovery continues in [automated intake](10-automated-intake.md).
