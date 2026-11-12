# Application form v2: assignment block, documents, recruiter feedback

Extends `application-form-contract.md` (read it first). The application form is the staff's main working screen: every staff
member works a candidate end to end from it. Shared by the backend agent and the frontend agent; keep this file in step with the code.
The backend is authoritative for shapes: if you must change a name or shape, edit this file and say so in your report.
Migration `0027_application_form_v2` (head is `0026_client_agency`; never edit applied migrations; additive and idempotent).

## 1. Assignment block ("what is this candidate for")

The form shows, above everything else and live: **Client, Job, Source, Agency, Recruiter**. All five are editable in place and the
form updates in real time (live feed) for everyone looking at it.

Payload key `assignment` (also returned by every PUT/POST of the form):

```jsonc
"assignment": {
  "client":   { "id": "…", "name": "Al Noor Staffing", "companyId": "…", "companyName": "Gulf Builders LLC" } | null,
  "job":      { "orderId": "…", "ref": "ORD-2026-00004", "title": "Welder", "applicationId": "…|null", "stage": 3, "country": "UAE" } | null,
  "source":   { "key": "whatsapp|email|walk_in|referral|other", "label": "WhatsApp" },
  "agency":   { "id": "…|null", "name": "Kerala Recruiters" } | null,      // the reference agency (free-text name allowed when no id)
  "recruiter":{ "id": "…", "name": "Asha Menon" } | null,
  "can": { "job": true, "source": true, "agency": true, "recruiter": false },  // what THIS user may change
  "options": {                                                                // everything the pickers need, in one answer
    "clients": [ { "id", "name", "companyId", "companyName", "jobs": [ { "orderId", "ref", "title", "openings", "status" } ] } ],
    "agencies": [ { "id", "name" } ],
    "recruiters": [ { "id", "name", "role" } ]                                 // only when can.recruiter
  }
}
```

`PUT /api/candidates/{id}/application-form/assignment` body (all keys optional, partial):
`{ "orderId": "…"|null, "source": "whatsapp|email|walk_in|referral|other", "agencyId": "…"|null, "agencyName": "text"|null, "recruiterId": "…" }`
Answers the whole form (same as the other PUTs). Rules:
- **Job** (`orderId`): the client is implied by the job (the picker is client first, then that client's open jobs). A candidate with no active
  application is mapped to the job exactly like `POST /api/applications` does (same checks, same client-staff routing rule, may therefore
  change the recruiter). A candidate whose active application has not passed prescreening (stage 1, nothing completed beyond it) is
  moved to the new job (the old application is closed with reason `changed_job`, a new one is created, history/audit written). Past
  prescreening: 409 `{detail: "This candidate is already past prescreening for the current job. Unmap the candidate first."}`.
  `orderId: null` unmaps (same as the existing unmap rules). Closed/filled/on-hold jobs are refused like in mapping.
- **Source**: owner, admin, manager. Sets `candidate.source`. **Agency**: owner, admin, manager; `agencyId` resolves the name from
  `crm_reference_agencies`, stored in `candidate.source_detail` (the agent/reference text that Data Bank search already reads); `agencyName`
  alone stores free text; both null clears it. Selecting an agency while source is not `referral` leaves the source unchanged.
- **Recruiter** (`recruiterId`): admin and manager only (staff: 403), through the existing reassignment path (history, notices,
  tasks move). Must be an active staff or manager.
- Every successful change writes an audit entry ("Application form: job changed", ...) and bumps the live feed.
- An application closed by a job change has `closureReason: "changed_job"` (tracking label "Moved to another job"; new value of the closure enum).
- `orderId`, `source`, `agencyId`, `agencyName`, `recruiterId` are applied in that order in one transaction: a refusal changes nothing.
  `source` and `recruiterId` cannot be null (422). Optional query `orderId`/`applicationId` select which application the answer shows (as the other PUTs);
  after a new job is mapped the answer shows the new application.

## 2. Documents

`documents` stays an object keyed by slot, now with these slots **in this order** (`documentOrder: string[]` lists it):

| slot | label (UI) | read by OCR | counted when |
|---|---|---|---|
| `application_form` | Application form | no | optional; existing files remain accessible |
| `resume` | Resume | yes | always |
| `passport` | Passport | yes | always |
| `aadhaar` | Aadhaar | yes | optional; front/back and additional files retained |
| `medical` | Medical | no | from stage 6 |
| `signed_offer` | Signed offer | no | from stage 5 (Offer letter) |
| `pcc` | Police clearance certificate (PCC) | no | from stage 7 (Passport) |
| `visa` | Visa | no | from stage 9 (Visa arrival) |
| `air_ticket` | Air ticket | no | from stage 11 |
| `undertaking_letter` | Undertaking letter | no | from stage 11, before deployment |
| `emigration` | Emigration | no | from stage 10, after visa |
| `insurance` | Insurance | no | from stage 11, before deployment |
| `invoice` | Invoice | no | from stage 11, before deployment |
| `photo` | Photo | no | optional; existing uploads retained |
| `education_certificate` | Education certificate | no | optional; existing uploads retained |
| `experience_certificate` | Experience certificate | no | optional (never counted as missing) |
| `google_review` | Google review | no | optional; existing uploads retained |
| `visa_stamping` | Visa stamping | no | optional; existing uploads retained |
| `other` | Other documents | no | optional, several files allowed |

Each entry: `{ slot, label, hint, required: bool, requiredFromStage: number|null, readable: bool, multiple: bool, current: Document|null, history: Document[], files: Document[] }`
(`files` for `passport`, `aadhaar` and `other`, newest first, `[]` for the other slots). The entry also keeps the older keys `uploaded: bool`, `versions: number`,
`extraction: Extraction|null` (null unless the slot is read) and adds `optional: bool` (the eight optional slots above are never counted).
`history` = the older versions of the slot, newest first (everything but `current`). `required` is computed from the application's current stage (no application: only the
"always" ones). Allowed file types/size as today (PDF, Word, JPG, PNG, WebP, HEIC up to 25 MB); `photo` image only.
`POST /api/candidates/{id}/application-form/documents` accepts every slot (multipart `slot`, `file`, `defer_extraction=true` by default). Files are stored first. Once all pages/files are ready, `POST .../application-form/extract` with `{attachmentIds:[...]}` queues the selected readable files atomically. Repeated starts reuse existing jobs. Per-file `extractionPending` and extraction results distinguish waiting, running, complete and review-needed files. Plain `POST /api/files` accepts the same slots; profile clients explicitly defer reading while automatic intake callers retain their existing default.

A known wrong-person file remains available to view/download and carries `identityMismatch: true` on its file metadata and reading result. It does not count as accepted document evidence (`uploaded`); `fileUploaded` still reports the retained bytes. If another non-rejected file exists in the slot, that file can satisfy the checklist. An old manual confirmation cannot override a rejected file, and staff must upload the correct document instead of retrying or manually accepting the rejected one. The same distinction applies to profile/bootstrap, Data Bank, Travelled and inspection exports. Exports identify and retain rejected files for review.

**Completeness** (`completeness`: `{ percent, filled, total, missing: [{key,label,section}] }` as today) now counts: every personal/passport/etc.
field as before, plus every document with `required: true`. Uploading a document or filling a field raises the percentage live.

The eleven non-optional slots form the management document checklist; they do not block manual stage moves or deployment.
The checklist reports missing evidence independently of candidate status. Uploads may be added before their checklist stage. The optional application
form slot retains uploaded evidence: printing the editable form alone does not mark it received. The `invoice_generated` tracking key is
retained for compatibility, labelled **Invoice**, and reflects the latest invoice attachment and its upload time, never payment status.
It does not add a workflow stage or change the eleven-stage progress denominator.
Optional documents never lower it and are listed under `completeness.optionalDocuments: [{slot,label,uploaded}]`. With no application the
total is 18 (16 fields + resume and passport); each required-by-stage document adds one.
Slot keys stored before v2 (`education`, `experience`) are folded into `education_certificate` / `experience_certificate` (by upload time).
The candidate profile (`GET /api/candidates/{id}`) adds the form-only slots after its seven older keys without duplicating Aadhaar; the desk snapshot keeps its
seven older keys (labels now "Passport" and "Photo").

**Manual stages and independent checklist (2026-11-12).** The owner's latest instruction removes document gates for every stage,
including final deployment. The form places all eleven stages next to Recruiter. Staff select a stage to inspect its saved history;
the current stage moves only after written feedback and manual confirmation. `POST /api/applications/{id}/complete` requires nonblank
`data.feedback` (up to 10,000 characters), saving feedback, actor/time, stage outcome and movement atomically. A feedback-only move records
`Completed manually`, without inventing a result, payment or date. Explicit negative/retest decisions retain their existing behavior.
The final manual completion sets the candidate/application to Deployed, subject to existing ownership, version and order-capacity checks.
An unknown travel date remains unknown. Reopening preserves earlier feedback in history and clears it from the new draft so another move
requires fresh feedback. Document uploads do not advance stages. See [stage rules](14-stages-one-to-five.md).

`application_form.missing_required_documents(db, candidate_id)` still computes the inspection checklist. The payload retains its existing shape:
`"deployment": { "ready": bool, "missing": [ { "slot": "signed_offer", "label": "Signed offer" } ] }` (always computed against stage 11,
whatever stage the application is on; `missing` is in contract order). Here `ready` means the document checklist is complete, not permission
to advance or deploy. Missing evidence remains visible even after manual deployment.

## 3. Recruiter feedback ("why is this taking time")

Table `crm_application_feedback`: `id`, `candidate_id` FK, `application_id` FK nullable, `author_id` FK users, `kind` (`update|delay|issue|client|candidate`),
`reason` (nullable short code: `waiting_candidate|waiting_client|waiting_documents|waiting_visa|waiting_payment|other`), `body` Text (1-2000),
`stage` (int, nullable: the stage when written), `created_at`. Index on `(candidate_id, created_at desc)`.

Form payload gains:
```jsonc
"feedback": { "items": [ { "id","kind","reason","body","stage","authorId","authorName","createdAt" } ],   // newest first, max 50
              "canWrite": true,
              "stageClock": { "stage": 3, "stageLabel": "Interview scheduling", "since": "…", "hours": 61, "limitHours": 48, "overdue": true,
                              "needsReason": true } | null }   // needsReason: overdue and no delay/update note written since the stage began
```
`POST /api/candidates/{id}/application-form/feedback` body `{kind, reason?, body, applicationId?}` (owner, admin, manager). Answers the whole form.
Each item also carries `canEdit` (own note, within 15 minutes) and `canDelete` (admin always, own note within 15 minutes).
`PUT /api/candidates/{id}/application-form/feedback/{feedbackId}` (body `{kind?, reason?, body?}`, own note within 15 minutes) edits a note;
POST answers 201, PUT and DELETE 200, all with the whole form. A `delay` or `issue` note notifies the admins and managers (Notification kind `feedback`, link to the form). Staff cannot edit or delete
a note after 15 minutes; admin can delete any (`DELETE /api/candidates/{id}/application-form/feedback/{feedbackId}`).
Live topic `feedback` (add the table to `change_feed.py`); the staff weekly report (`staff_weekly`) gets `feedbackNotes` per person (count in the range) if cheap.

## 4. Wording (frontend)

Every label on the form uses proper sentences/names: e.g. "Name of institution", "Address of institution", "Period of employment", "Reason for
leaving", "Place of issue", "Date of issue", "Date of expiry", "Father's name", "Contact number of a family member". No abbreviations or
shouting capitals in labels (the section bars keep their capitals as printed on the paper form). Hints are one short sentence.

## 5. Candidate inspection downloads

`GET /api/candidates/{id}/inspection.xlsx` exports saved candidate details, recruitment source, passport, education,
work history, screening, placements, stage history, saved stage drafts, document checklist, document versions and
recruiter notes. Feedback is not limited to the form's latest fifty notes. Task notes retain the normal visibility
rule: staff see their own tasks, while administrators and managers see every task for the candidate. Oversized
history is refused explicitly instead of being silently truncated.
`GET /api/candidates/{id}/documents.zip` includes that workbook, `manifest.json`, the latest file for each ordinary
document slot on the candidate and their applications, and every retained multi-file document and Invoice file. Older versions of the
ordinary slots remain listed in the manifest. Missing checklist items stay explicitly missing; no placeholder file
is counted as evidence. A stored file that cannot be read fails the whole package before the download starts.

Both routes accept optional `applicationId` or `orderId` query parameters to select the form context. The archive
still includes the candidate's own application history, never another candidate's documents. Full exports require
the assigned recruiter, an administrator or a manager because they include detailed stage history. Responses use
private, no-store caching. The UI requires pending form edits to be saved before exporting.

The existing administrator Data Bank Excel export adds a column for each of the eleven checklist documents and a
combined missing-document list, using the same checklist as the application form and Travelled. These are stored
document statuses, not a declaration that a policy or certificate is valid. Candidate invoices are generated only
after an actual air-ticket file and the staff-entered service fee are saved, with billing configured. See
[candidate invoices](candidate-invoices.md) for numbering, tax settings and payment records.

Manual checklist confirmation is separate from file storage: `uploaded` may be true while `fileUploaded` is false.
`manualStatus` records the staff member, time and note. It never creates a file, reading result or invoice. View and
download actions require an actual file URL; missing-field badges are labelled separately from upload status.

Interactive document uploads wait for the staff member's **Extract details** action. In Add candidate, each click
captures the current file IDs; a file added during reading waits for the next click. Upload retries affect only
failed uploads, and reading retries do not replace manually edited fields. Every source page has a reading result
or an exclusion/review reason. Unresolved page numbers remain visible in document reports; incomplete resume
reading also appears beside experience totals. A successful upload alone does not certify extraction accuracy.

Client options include active clients even when they have no open job. Jobs are grouped by client across its
companies and show Open, Filled, On hold or Closed. Only open jobs can receive new assignments; an existing
candidate's current job remains visible regardless of status. Saving still enforces ownership and job status.
