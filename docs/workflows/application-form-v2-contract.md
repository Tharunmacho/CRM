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
| `resume` | Resume | yes | always |
| `passport` | Passport | yes | always |
| `photo` | Photo | no | always (was "Passport-size photo") |
| `education_certificate` | Education certificate | no | always |
| `experience_certificate` | Experience certificate | no | optional (never counted as missing) |
| `signed_offer` | Signed offer | no | from stage 5 (Offer letter) |
| `medical` | Medical | no | from stage 6 |
| `pcc` | Police clearance certificate (PCC) | no | from stage 7 (Passport) |
| `visa` | Visa | no | from stage 9 (Visa arrival) |
| `google_review` | Google review | no | from stage 9 (it comes BEFORE visa stamping) |
| `visa_stamping` | Visa stamping | no | from stage 10 |
| `emigration` | Emigration | no | from stage 10 |
| `air_ticket` | Air ticket | no | from stage 11 |
| `other` | Other documents | no | optional, several files allowed |

Each entry: `{ slot, label, hint, required: bool, requiredFromStage: number|null, readable: bool, multiple: bool, current: Document|null, history: Document[], files: Document[] }`
(`files` only for `other`, newest first, `[]` for the other slots). The entry also keeps the older keys `uploaded: bool`, `versions: number`,
`extraction: Extraction|null` (null unless the slot is read) and adds `optional: bool` (experience certificate and other documents: never counted).
`history` = the older versions of the slot, newest first (everything but `current`). `required` is computed from the application's current stage (no application: only the
"always" ones). Allowed file types/size as today (PDF, Word, JPG, PNG, WebP, HEIC up to 25 MB); `photo` image only.
`POST /api/candidates/{id}/application-form/documents` accepts every slot (multipart `slot`, `file`); `resume` and `passport` queue reading,
the others are only stored (and appear in the candidate's Documents card). Plain `POST /api/files` with `entity_type=candidate` must accept the same slots.

**Completeness** (`completeness`: `{ percent, filled, total, missing: [{key,label,section}] }` as today) now counts: every personal/passport/etc.
field as before, plus every document with `required: true`. Uploading a document or filling a field raises the percentage live.
Optional documents never lower it and are listed under `completeness.optionalDocuments: [{slot,label,uploaded}]`. With no application the
total is 20 (16 fields + resume, passport, photo, education certificate); each required-by-stage document adds one.
Slot keys stored before v2 (`education`, `experience`) are folded into `education_certificate` / `experience_certificate` (by upload time).
The candidate profile (`GET /api/candidates/{id}`) lists the 14 slots after the older keys in its `documents`; the desk snapshot keeps its
seven older keys (labels now "Passport" and "Photo").

**Deployment gate.** "All the documents that are necessary are required, and only then are they deployed." Completing the final stage
(11, Air ticket) is the only path that sets an application and the candidate to Deployed (`POST /api/applications/{id}/complete`). It is refused,
for every role including admins, with `409 {"detail": "Upload these documents before deploying: Signed offer, Medical, ..."}` unless every
document that is required by stage 11 is uploaded: resume, passport, photo, education certificate, signed offer, medical, PCC, visa, Google
review, visa stamping, emigration, air ticket. The labels in the message are in the order of the table above. Experience certificate and other
documents never block. One function does the check (`application_form.missing_required_documents(db, candidate_id)`, one statement; older
`education`/`experience` slot keys count for their new slots). The form payload carries the same answer, live:
`"deployment": { "ready": bool, "missing": [ { "slot": "signed_offer", "label": "Signed offer" } ] }` (always computed against stage 11,
whatever stage the application is on; `missing` is in contract order). The gate is checked inside the completing transaction after the
stage's own checks, before the "openings already filled" check.

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
