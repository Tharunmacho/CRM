# Application form (order-wise) - API contract

Backend contract for the "Job Application Form" screen (Careindia Overseas Pvt Ltd), built from
`CareIndia_Application_Form_Order_Wise.docx`. One form per candidate and order (application). All keys are
camelCase like the rest of the API. Dates are `YYYY-MM-DD` strings, timestamps ISO-8601 with offset.
Every response is `Cache-Control: private, no-store`. Errors are the usual `{"detail": "message"}` (422 for
validation, 403 permission, 404 unknown candidate/application).

Router: `backend/app/api/routers/application_form.py`. Logic: `backend/app/services/application_form.py`
(payload, saving, tracking, completeness) and `backend/app/services/form_extraction.py` (reading OCR output into the
form columns).

## 1. Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/candidates/{id}/application-form?orderId=&applicationId=` | any signed-in user | The whole form |
| PUT | `/api/candidates/{id}/application-form` | admin, manager, the candidate's recruiter | Partial manual save |
| POST | `/api/candidates/{id}/application-form/documents` | admin, manager, the candidate's recruiter (or the creator within 24 h) | Store resume / passport / photo without starting reading |
| POST | `/api/candidates/{id}/application-form/refresh` | same as PUT | Re-apply the already stored OCR output (fills empty / still-extracted fields) |

Which application (order) the form shows: `applicationId` wins, then `orderId` (the candidate's application for
that order), otherwise the candidate's newest *active* application, otherwise the newest one, otherwise none (then
`application` is `null`, section 6 is blank and every tracking row is `not_started`). `applications` always lists
all of the candidate's orders so the screen can offer a switcher.

Unknown `orderId`/`applicationId` for this candidate: 404.

### Files / documents

Resume, passport and photo are ordinary candidate attachments (`entity_type=candidate`, slots `resume`,
`passport`, `photo`). Use the **POST `.../application-form/documents`** endpoint for the form's upload buttons - it
stores the file exactly like `POST /api/files` does and in addition returns the form and the reading status in one answer.
Form uploads defer reading by default. Form/profile clients explicitly send multipart `defer_extraction=true`, add all front/back/additional files, then call `POST .../application-form/extract` with `{attachmentIds:[...]}`. The generic `POST /api/files` default remains automatic for existing intake integrations; those integrations are not changed by this form flow. Download/preview with `GET /api/files/{attachmentId}` (the `url` in the payload).

Reading is asynchronous: the document-extraction worker (started by `python -m app.runtime`) picks the job up,
and when done fills the form columns and commits. The commit publishes a live change event, so an open form just
refetches (section 8). The explicit extraction POST answers `202` with `{form,queued,existing,jobIds}`. Upload alone creates no reading job and performs no inspection or autofill; `extraction` is null and `extractionPending=true` until started.

## 2. GET response (also the PUT / POST / refresh response, `form` key for POST)

```jsonc
{
  "header": {                                   // static text printed at the top of the docx
    "company": "Careindia Overseas Pvt Ltd",
    "title": "JOB APPLICATION FORM",
    "address": "743/Dhanalakshmi Apartments, 1st Avenue, near reliance digital, Ashok Nagar, Chennai - 600083.",
    "phone": "044 - 24711234",
    "email": "info@careindiaoverseas.com"
  },
  "candidate": { "id": "...", "ref": "CND-00012", "name": "...", "status": "new", "needsReview": false,
                 "ownerId": "...|null", "ownerName": "...|null", "updatedAt": "2026-11-04T10:00:00+00:00",
                 "canEdit": true },             // canEdit: whether THIS user may PUT / upload
  "application": null | { "id": "...", "orderId": "...", "orderRef": "ORD-1001", "status": "active|closed|deployed",
                          "closureReason": "prescreen_failed|...|null", "currentStage": 3, "createdAt": "..." },
  "applications": [ { "id": "...", "orderId": "...", "orderRef": "...", "clientName": "...", "position": "...",
                      "country": "...", "status": "active", "currentStage": 3, "selected": true } ],

  "photo": null | Document,                     // same object as documents.photo.current, handy for the photo box

  "personal": {                                 // section 1
    "candidateName": "string",                  // "" while the name is still a placeholder ("Name needs review")
    "fatherName": "string|null",
    "dateOfBirth": "YYYY-MM-DD|null",
    "maritalStatus": "string|null",             // free text from the document, normalised to Single / Married / Divorced / Widowed / Separated when recognisable
    "gender": "male|female|...|null",
    "nationality": "string|null",
    "languagesKnown": ["English", "Hindi"],
    "mobileNumber": "string",                   // "" when unknown
    "currentAddress": "string|null",
    // extras (not printed on the docx, shown under the section):
    "whatsapp": "string|null", "email": "string|null", "currentCity": "string|null",
    "whatsappSameAsPhone": true                 // derived: the WhatsApp number equals the mobile number
  },
  "passport": {                                 // section 2
    "passportNumber": "string|null", "placeOfIssue": "string|null",
    "issueDate": "YYYY-MM-DD|null", "expiryDate": "YYYY-MM-DD|null",
    "expiry": { "status": "valid|expiring_soon|expired|unknown", "daysLeft": 812 },  // expiring_soon = within 180 days
    "available": true                           // derived: a passport number is on file OR a passport scan was uploaded
  },
  "screening": {                                // "Additional details" from the telemarketer's intake form (own card on the screen)
    "nativePlace": "string|null",
    "familyMemberNumber": "string|null",        // a family member's phone number
    "jobPreference": ["Welder", "Fitter"],      // = the candidate's designations (job titles wanted)
    "countryPreference": "Qatar, UAE|null",     // countries looking for, free text
    "jobDurationPreference": "2 years|null",    // free text
    "bestTimeToContact": "Evenings|null",
    "serviceChargesStatus": "not_discussed",   // default; any ACTIVE value of the service_charges_status list (section 12), seeded: not_discussed|agreed|pending|declined
    "interestLevel": null,                     // any ACTIVE value of the interest_level list (seeded: more_interested|interested|somewhat_interested|not_interested) or null
    "nextFollowUpDate": "YYYY-MM-DD|null",      // also mirrored to the recruiter's to-do list, see section 11
    "skills": ["Welding"],
    "callOutcome": null | { "called": true, "interested": "yes|no|unreachable", "remarks": "telemarketer notes", "recordedAt": "..." }  // read only: the initial call (POST /api/candidates/{id}/call); remarks = call_remarks
  },
  "education": [                                // section 3, in the order shown (max 20)
    { "level": "B.Tech Mechanical Engineering", "passedYear": 2016, "institute": "ABC College, Chennai" }
  ],
  "experience": {                               // section 4
    "totalExperience": 6,                       // whole years, integer
    "experienceIndia": 4,                       // whole years
    "experienceAbroad": 2,                      // whole years (the candidate's existing gulfExperience)
    "history": [
      { "company": "...", "designation": "...", "country": "India", "period": "Jan 2018 - Mar 2020 (2 years 2 months)" }
    ]
  },
  "documents": {                                // section 5
    "resume":   DocumentSlot,
    "passport": DocumentSlot,
    "photo":    DocumentSlot
  },
  "recruitment": {                              // section 6, read only, comes from the order / candidate
    "client": "Company name (or the client contact when no company)|null",
    "clientContact": "Client contact person|null",
    "country": "string|null", "position": "string|null", "orderRef": "string|null",
    "source": "Email|WhatsApp|Walk-in|Referral|Other", "sourceCode": "email|whatsapp|walk_in|referral|other",
    "sourceDetail": "string|null",
    "recruiter": { "id": "...", "name": "..." } | null
  },
  "tracking": {                                 // section 7, derived live from crm_application_stages
    "summary": { "completed": 3, "tracked": 11, "percent": 27, "currentStage": 4, "currentStageLabel": "Interview result",
                 "applicationStatus": "active|closed|deployed|none", "closureLabel": "Failed prescreening|null" },
    "rows": [ TrackingRow ]
  },
  "completeness": { "percent": 67, "filled": 12, "total": 18, "missing": [ MissingItem ],
                    "optional": { "filled": 3, "total": 9, "missing": [ MissingItem ] } },   // screening details: listed, not in the percentage
  "fieldSources": { "personal.fatherName": "extracted", "passport.passportNumber": "manual" },
  "warnings": [ "Check the Indian passport number against the uploaded passport (one letter and seven digits, or two letters and six digits)." ],
  "processing": false                           // true while any document of this candidate is still being read
}
```

`Document` (a stored file):
`{ "attachmentId": "...", "fileName": "passport.pdf", "sizeKb": 220, "version": 2, "url": "/api/files/<id>", "contentType": "application/pdf", "uploadedAt": "...", "uploadedBy": { "id": "...", "name": "..." } | null }`

`DocumentSlot`:
```jsonc
{ "slot": "resume|passport|photo", "label": "Resume|Passport|Passport size photo",
  "uploaded": true,                    // false -> show "Passport not uploaded" and an upload button
  "current": Document | null,          // newest version
  "versions": 2,
  "extraction": null | Extraction }    // null for photo and for slots never read
```

`Extraction` (also returned by the POST):
```jsonc
{ "status": "processing|done|failed|unreadable|needs_review",
  "message": "human readable, safe to show|null",
  "jobId": "...|null", "updatedAt": "...",
  "kindsFound": ["passport"],          // what the reader identified in the file: resume / passport / aadhaar
  "filled": ["passport.passportNumber", "personal.dateOfBirth"],   // form fields this read filled or refreshed
  "kept": ["personal.fatherName"] }    // form fields it found but did not apply because a person had entered them
```
- `processing`: queued or being read (also while a failed attempt is waiting for its automatic retry; `message` then says why).
- `done`: read and applied (`filled` may be empty when nothing new was found).
- `needs_review`: read, but some pages could not be classified/read, or the document mixes people. Applied what was safe.
- `unreadable`: no resume / passport pages were found in the file (wrong or blank file). Nothing applied.
- `failed`: reading failed after all retries (message from the server; the original file is kept).

`TrackingRow`:
```jsonc
{ "key": "prescreening", "label": "Pre-screening",          // docx wording
  "systemLabel": "Prescreening",                              // the CRM stage title, null for untracked rows
  "stage": 1,                                                 // 1..11, null when the CRM has no such stage
  "extra": false,                                             // true for CRM stages that the docx does not list
  "tracked": true,                                            // false for PCC / Invoice Generated
  "status": "not_started|in_progress|completed|failed|not_applicable",
  "checked": true,                                            // status === "completed" (the docx checkbox)
  "date": "YYYY-MM-DD|null",                                  // completion date in the agency time zone
  "completedAt": "ISO timestamp|null",
  "completedBy": { "id": "...", "name": "..." } | null,
  "outcome": "Passed|Forwarded|Selected|Fit|...|null",        // the stage's recorded outcome text
  "attempt": 1,                                               // medical can repeat
  "note": "string|null" }
```
Status rules: a recorded, not-reopened attempt = `completed`, except outcomes that close the application
(`Failed`, `Not forwarded`, `Rejected`, `Unfit`) = `failed`; the stage the active application is currently on (no record
yet, or a repeat after "Unfit: reapplying") = `in_progress`; a stage that was never reached = `not_started`; once the
application is closed or unmapped, never-reached stages and the untracked rows are `not_applicable`. Reopening a stage
(Applications > reopen) makes it leave `completed` at once.

`MissingItem`:
`{ "key": "documents.passport", "label": "Passport", "section": "documents|personal|passport|education|experience", "kind": "document|field", "message": "Passport not uploaded" }`
The 19 counted items: personal.candidateName, fatherName, dateOfBirth, maritalStatus, gender, nationality,
languagesKnown, mobileNumber, currentAddress; passport.passportNumber, placeOfIssue, issueDate, expiryDate;
education (at least one row); experience.totalExperience, experience.history (at least one row);
documents.resume, passport, photo. `percent = round(100 * filled / 19)`. Section 6 and 7 are not counted.
`completeness.optional` lists the screening items that are not recorded yet (`section: "screening"`): nativePlace,
familyMemberNumber, jobPreference, countryPreference, jobDurationPreference, bestTimeToContact, interestLevel,
serviceChargesStatus (missing while it is `not_discussed`) and, only when the interest level is more_interested / interested /
somewhat_interested, nextFollowUpDate. They are not part of `percent`.

## 3. Tracking rows: docx to CRM stage mapping

| docx row | `key` | CRM stage |
|---|---|---|
| Pre-screening | `prescreening` | 1 Prescreening |
| Forwarded to Client | `forwarded` | 2 Forwarded to client |
| (extra) Interview scheduling | `interview_scheduling` | 3 |
| (extra) Interview result / selection | `interview_result` | 4 |
| Offer Letter | `offer_letter` | 5 Offer letter |
| PCC | `pcc` | none: the CRM has no police-clearance stage -> `tracked:false`, `not_applicable` |
| Medical | `medical` | 6 Medical |
| (extra) Passport collection | `passport_collection` | 7 Passport |
| (extra) Advance payment | `advance_payment` | 8 Advance payment |
| Visa | `visa` | 9 Visa arrival + Google review |
| Visa Stamping | `visa_stamping` | 10 Visa stamping |
| Ticket | `ticket` | 11 Air ticket |
| Invoice Generated | `invoice_generated` | none: invoicing is not a CRM stage -> `tracked:false`, `not_applicable` |

Rows come back in exactly this order (13 rows). Extra rows should be shown after the docx rows' styling but in this
order, labelled so the recruiter sees they are additional CRM stages.

## 4. PUT /api/candidates/{id}/application-form

Partial: send only what the person changed. Every section and key is optional; unknown keys are rejected (422).
Send `null` or `""` to clear a text field (a cleared field is empty again, so a later document read may fill it).

```jsonc
{ "personal":   { "candidateName", "fatherName", "dateOfBirth", "maritalStatus", "gender", "nationality",
                  "languagesKnown": [..], "mobileNumber", "currentAddress", "whatsapp", "email", "currentCity" },
  "passport":   { "passportNumber", "placeOfIssue", "issueDate", "expiryDate" },
  "education":  [ { "level", "passedYear", "institute" } ],            // replaces the whole list
  "experience": { "totalExperience", "experienceIndia", "experienceAbroad",
                  "history": [ { "company", "designation", "country", "period" } ] },  // history replaces the whole list
  "screening":  { "nativePlace", "familyMemberNumber", "jobPreference": [..], "countryPreference", "jobDurationPreference",
                  "bestTimeToContact", "serviceChargesStatus", "interestLevel", "nextFollowUpDate", "skills": [..] }
}
// personal.whatsappSameAsPhone: true  -> copies the (new or stored) mobile number into whatsapp. It is a helper, not a stored field;
// whatsapp is then marked manual like any typed value. Lists (jobPreference, skills, languagesKnown) replace the stored list and are de-duplicated.
// serviceChargesStatus and interestLevel must be an ACTIVE option of their list (section 12; 422 otherwise) unless it is the value the
// candidate already has; "" / null clears interestLevel, and serviceChargesStatus back to not_discussed. nextFollowUpDate must be within the last 10 and the next 5 years.
```
Response: `200` with the full form payload (section 2). Rules:
- Each changed field is stamped `fieldSources[...] = "manual"` and is never overwritten by a later document read.
  A value sent that equals the stored one keeps an `extracted` stamp.
- Hard errors (422): unparseable date, date of birth in the future, passport expiry before issue, `passedYear`
  outside 1950..(this year + 6), negative or > 80 years of experience, more than 20 education / 30 history rows, a
  text longer than its limit, phone/e-mail/passport number that already belongs to another candidate (409 with the
  same wording as candidate correction), removing the name or phone entirely.
- Warnings (200, in `warnings`): passport number not in the Indian pattern, passport already expired or expiring in
  under 180 days, mobile number that is not a plausible number, experience in India + abroad not matching total.
- Audit-logged ("Updated application form") with the changed field keys; commits a `crm_candidates` update, so the live
  feed announces it.

## 5. POST /api/candidates/{id}/application-form/documents

`multipart/form-data`: `slot` = `resume` | `passport` | `photo`, `file` (PDF, Word, JPG/PNG/WebP/HEIC, 25 MB max;
a photo must be an image). Response `202`:
```jsonc
{ "document": DocumentSlot,                   // the slot after the upload
  "extraction": Extraction | null,            // null for stored readable files awaiting Extract details
  "form": <full payload> }
```
- A new file is retained for every distinct passport front/back/additional upload. Repeating any older passport file reuses its attachment. Deferred upload never retries a reading; explicit Extract details requeues failed jobs and reuses queued/completed jobs.
- Files uploaded to the wrong slot are still read by what they contain (the reader classifies pages), and the
  `message` says so ("This file holds a passport, not a resume.").
- Resume read: fills education, work history, experience totals, address, father's name, marital status, languages,
  nationality, DOB, gender, skills. Passport read: fills passport number / place / issue / expiry, name, DOB, gender,
  nationality, father's name (when printed), address (when printed). The passport is the authority for passport fields and
  identity fields; a resume's passport section only fills what is still empty.
- Photo: just stored (no reading); the form's photo box shows `photo.url`.

### Explicit extraction start

`POST /api/candidates/{id}/application-form/extract` accepts `{attachmentIds:[...]}` (1 to 100 stored resume/passport/Aadhaar files for that candidate), plus optional `applicationId` / `orderId` query parameters. The candidate's recruiter or a supervisor starts the batch. All identifiers and scope are validated before any job is queued; a candidate lock makes repeated/concurrent starts idempotent. The response is `{form,queued:number,existing:number,jobIds:string[]}`. Each file retains its own result and page coverage. Worker writes protect manually entered fields and preserve complementary fields from the other files.

`capabilities.deferredDocumentReading=true` advertises this API; clients must gate the new flow on this capability. Stored unread files expose `extractionPending=true` on form files/current/slot and profile/bootstrap file versions. More files can be added while an earlier reading runs; they wait for the next explicit start. Every source page is either selected for the applicable reader or listed in coverage with a reason; unread/unresolved pages prevent a complete result. Partial extracted totals must remain visibly under review.

## 6. POST /api/candidates/{id}/application-form/refresh

Body: none. Re-applies the OCR output already stored in `crm_candidates.ocr_data` using the same fill rules. Returns the
full payload. Also available as a CLI for existing data: `python -m app.cli backfill-application-forms [--dry-run]
[--candidate <id>]` (idempotent).

## 7. Fill rules (extraction vs manual) - what the screen can rely on

A field is overwritten by a document read only when it is **empty** or its `fieldSources` entry is `"extracted"`.
A value with `"manual"` is never touched. A value with no entry (data from before this feature) is treated as
manual unless it equals what the document says, in which case it is simply stamped `"extracted"`. So re-uploading a
better passport refreshes the extracted values, and anything typed in the form survives every read. Lists (`education`,
`experience.history`) are one field each: `fieldSources["education"]`, `fieldSources["experience.history"]`.

`fieldSources` keys: `personal.<key>`, `passport.<key>`, `education`, `experience.totalExperience`,
`experience.experienceIndia`, `experience.experienceAbroad`, `experience.history`, `screening.nativePlace`, `screening.familyMemberNumber`,
`screening.jobPreference`, `screening.countryPreference`, `screening.jobDurationPreference`, `screening.bestTimeToContact`,
`screening.serviceChargesStatus`, `screening.interestLevel`, `screening.nextFollowUpDate`, `screening.skills` (only fields that
have a source appear). The screening details are never produced by a document read (nothing in a resume or passport states
them), except `screening.skills` and `screening.jobPreference`, which a resume's skills and job title fill under the same rule.

## 8. Live updates

Subscribe to `GET /api/events` (SSE, see `api/routers/events.py`) and refetch the form (debounced ~300 ms) on any of
the topics `candidates`, `documents`, `applications` while the form is open (staff events carry topic names only, no
ids, so refetch whenever one of these arrives rather than matching ids; admins/managers also get
`ids.candidates`). A document read finishing publishes `candidates` + `documents`; completing or reopening a stage
publishes `applications`. If any `documents.*.extraction.status` is `processing` or `processing` is `true` and no event
arrives, poll the GET every 4 s as a fallback.

## 9. Not provided

`GET .../export.docx` is not offered: the backend has no docx library (python-docx / lxml are not in
`requirements.txt`). The screen should print to PDF with the browser.

## 10. Add candidate page and candidate APIs carry the same fields

The same form fields are also part of the older candidate APIs, backward compatible (nothing existing changed or was
removed; every new field is optional with an empty default).

### 10.1 Scan preview (before the candidate exists)

`POST /api/ocr/{resume|passport|aadhaar}` (`fields`) and the document-job results `GET /api/ocr/jobs/{id}` and
`GET /api/ocr/jobs/groups/{groupId}` (`result.scans.<kind>.fields`) now also return, camelCase like the existing keys
(`currentCity`, `passportNo`, ...), whenever the document states them:

| key | type | from |
|---|---|---|
| `fatherName` | string | resume personal info; passport "Name of Father / Legal Guardian" |
| `maritalStatus` | string | resume; normalised to Single / Married / Divorced / Widowed / Separated when recognisable |
| `currentAddress` | string | resume contact address (lines joined with ", "); passport address if printed |
| `experienceIndia` | integer years | resume (Indian experience) |
| `education` | `[{ "level", "passedYear": int\|null, "institute" }]` | resume education |
| `workHistory` | `[{ "company", "designation", "country", "period" }]` | resume experience |

Already present and unchanged: `name`, `phone`, `whatsapp`, `email`, `dob`, `gender`, `nationality`, `currentCity`,
`languages`, `passportNo`, `passportIssue`, `passportExpiry`, `passportPlace`, `designation`, `totalExperience`
(years), `gulfExperience` (= "Experience Abroad", years), `skills`. An absent key means "not read" (never guess).
Results of documents read before this feature get the new keys too (computed from the stored reading). The page applies
them with the existing rule: a scanned value only fills a field that is empty and that the person has not edited
(`editedFields`); the draft allow-list (`PUT /api/ocr/jobs/groups/{id}/draft`, `form` strings and `editedFields`) accepts the new keys
`fatherName`, `maritalStatus`, `currentAddress`, `experienceIndia`, and `education` / `workHistory` as JSON-encoded strings.

### 10.2 Create / correct / duplicate-check / merge bodies

`POST /api/candidates`, `PATCH /api/candidates/{id}`, `POST /api/candidates/{id}/merge`, `POST /api/candidates/check-duplicate`
accept these extra optional keys. The body is still snake_case (`current_city`, `passport_no`, ...); **the camelCase
names below are accepted too** (if both spellings of a key are sent the snake_case one wins):

| snake_case | camelCase alias | type |
|---|---|---|
| `father_name` | `fatherName` | string, max 200 |
| `marital_status` | `maritalStatus` | string, max 30 |
| `current_address` | `currentAddress` | string, max 500 |
| `experience_india` | `experienceIndia` | int 0..80 |
| `gulf_experience` (existing) | `experienceAbroad` | int 0..80 |
| `native_place` | `nativePlace` | string, max 120 |
| `family_member_number` | `familyMemberNumber` | string, max 40 |
| `job_duration_preference` | `jobDurationPreference` | string, max 60 |
| `country_preference` | `countryPreference` | string, max 200 |
| `best_time_to_contact` | `bestTimeToContact` | string, max 60 |
| `service_charges_status` | `serviceChargesStatus` | active option of the `service_charges_status` list (section 12), default `not_discussed` |
| `interest_level` | `interestLevel` | active option of the `interest_level` list (section 12), or null |
| `next_follow_up_date` | `nextFollowUpDate` | `YYYY-MM-DD` \| null |
| (not stored) | `whatsappSameAsPhone` | `true` copies the phone into `whatsapp` when no WhatsApp number is sent |
| `designations` (existing) | `jobPreference` | list of job titles wanted (the screening form's job preference) |
| `education` | `education` | `[{level, passedYear, institute}]`, max 20 rows, `passedYear` 1950..this year + 6 |
| `work_history` | `workHistory` | `[{company, designation, country, period}]`, max 30 rows |
| `passport_no` / `passport_place` / `passport_issue` / `passport_expiry` (existing) | `passportNumber` / `placeOfIssue` / `issueDate` / `expiryDate` | |
| `name` / `phone` / `dob` / `languages` (existing) | `candidateName` / `mobileNumber` / `dateOfBirth` / `languagesKnown` | |

Photo: unchanged. Upload it as a document job of kind `photo` (attached on save into the candidate's `photo` slot), or later
with `POST /api/files` / the form's document endpoint (section 5). The candidate's `documents` list already has a `photo` entry.

Provenance on these writes: `PATCH` marks every changed form field `manual`. `POST` marks the typed form fields `manual`
when the save carries no scanned documents; when it does carry them (`ocr_data`), a value equal to what the document says is
marked `extracted` and one the person changed stays protected. In both cases the application-form-only columns (fatherName,
maritalStatus, currentAddress, experienceIndia, education, workHistory) that the body left empty are filled from the saved scans
afterwards; the older columns (name, dob, passport fields, skills, ...) are left exactly as the body sent them. So a form-only field the
person deliberately left blank on Add candidate can still be filled from a document; the person can clear it again in the form.

### 10.3 Candidate read APIs

`GET /api/candidates/{id}` returns, in addition to everything it returned before: `fatherName`, `maritalStatus`,
`currentAddress`, `experienceIndia`, `nativePlace`, `familyMemberNumber`, `jobDurationPreference`, `countryPreference`,
`bestTimeToContact`, `serviceChargesStatus`, `interestLevel`, `nextFollowUpDate`, `education[]`, `workHistory[]`. The bootstrap
snapshot and the Data Bank list carry the scalar ones but NOT the two lists (they are heavy);
the application form (section 2) is the place for the lists.

## 11. Screening details: reuse of existing data, interest level and the follow-up reminder

Reference form field -> where it lives (nothing is duplicated):

| Reference form | Stored in | Notes |
|---|---|---|
| Source / agent | `source` + `source_detail` (`recruitment.source`, `recruitment.sourceDetail`) | read only on the form; set when the candidate is entered |
| Assigned staff | `owner_id` (`recruitment.recruiter`) | changed with the existing assign/reassign action |
| Telemarketer notes | `call_remarks` (`screening.callOutcome.remarks`) | written by the initial-call action `POST /api/candidates/{id}/call`; read only on the form |
| Skills | `skills` (`screening.skills`) | |
| Phone / WhatsApp | `phone`, `whatsapp` (`personal.mobileNumber`, `personal.whatsapp`, derived `whatsappSameAsPhone`) | |
| Passport available | derived: `passport.available` | passport number on file or a passport scan uploaded |
| Job preference | `designations` (`screening.jobPreference`) | |
| Country looking for | `country_preference` (new) | |
| Native place, family member number, job duration, best time to contact, service charges status, interest level, next follow-up date | new columns on `crm_candidates` (migration 0021) | |

`interestLevel` is the finer grading (the interest_level list: more interested / interested / somewhat interested / not interested). The existing
`call_interested` (`yes` / `no` / `unreachable`, in `screening.callOutcome.interested`) stays what the initial call recorded and is the
one that closes the initial-call task and moves the candidate's status; `interestLevel` never changes status or tasks. The form
warns when the two disagree (call = no with an "interested" level, call = yes with "not interested").

`nextFollowUpDate` is mirrored to the existing to-do list: saving a date (from this form, `POST /api/candidates` or `PATCH`) creates one
to-do for the candidate's recruiter ("Follow up: <name>", due 09:00 agency time on that day, link `/candidates/{id}`, label
"Follow-up"); changing the date moves that to-do and re-arms its reminder; clearing the date removes it; a to-do the recruiter has
already ticked is left alone. A candidate with no recruiter gets no to-do (the date is still stored).

## 12. Dynamic option lists (admin-managed) and suggestions

Nothing in the form's selects needs to be hardcoded any more: the choices are rows of `crm_form_options` (migration 0022, seeded with the
old defaults) that an administrator edits under settings, and open screens follow through the live feed. Read them once on opening the
form (and again on a `settings` live event), and render the selects / cards / pills from them.

### 12.1 Read: `GET /api/form-options` (any signed-in user)

```jsonc
{ "categories": [
  { "key": "interest_level", "label": "Interest level",
    "strict": true,            // true: only an active option may be newly stored (422 otherwise). false: the list only suggests, free text is allowed
    "allowCustom": false,      // = !strict, for the screen's "Other..." input
    "fixedValues": false,      // true (candidate_source): backed by a database enum, values cannot be added, only relabelled / reordered / hidden
    "options": [ { "id": "...", "value": "more_interested", "label": "More interested", "hint": "Keen to go abroad now",
                   "tone": "success",       // neutral | success | info | warning | danger | null: the colour of the card / pill
                   "sortOrder": 10, "isActive": true, "isSystem": true } ] } ] }
```
Categories (all always present, in this order): `service_charges_status` (strict), `interest_level` (strict), `candidate_source` (strict,
fixed values: email, whatsapp, walk_in, referral, other), `job_duration_preference`, `best_time_to_contact`, `marital_status`, `gender`,
`education_level` (suggestions for the "Educational level / course" input), `language` (suggestions for languages known). Options come
sorted by `sortOrder`, only active ones, except for an administrator calling with `?includeInactive=true` (anyone else: 403).
**What a screen stores is `value`** (for the free-text lists that is the text itself, e.g. `"2 years"`, `"Single"`, `"male"`); show `label`.
A candidate may hold a value whose option is hidden or unknown (older data, a document reading): show it as is - for a strict list, look the
value up in `?includeInactive`-less data and fall back to the raw value as its label; do not drop it.

Response headers: `ETag` and `Cache-Control: private, no-cache`; send `If-None-Match` and expect `304 Not Modified` with no body.
Live: every change publishes the topic **`form_options`** (and also `settings`, which agency settings, currencies and job categories use), so refetch
`/api/form-options` (ETag makes it cheap) when either arrives. Staff events carry the topic name only, no ids.

### 12.2 Change (administrator only; everyone else 403)

| Call | Body | Notes |
|---|---|---|
| `POST /api/form-options` | `{category, value, label, hint?, tone?, sortOrder?}` | 201 + the option. 409 if the value exists (even hidden: show it again with PUT). 422 for an unknown category, a fixed-value category (`candidate_source`), a value with odd characters, a bad tone. `sortOrder` defaults to the end. |
| `PUT /api/form-options/{id}` | any of `{label, hint, tone, sortOrder, isActive}` | `value` can never be changed (422 if sent). `hint` / `tone` accept `null` to clear. The default `service_charges_status.not_discussed` cannot be hidden (422). |
| `POST /api/form-options/reorder` | `{category, ids:[...]}` | Gives those options that order (ids of other options of the category follow). Returns every list including hidden ones. |
| `DELETE /api/form-options/{id}` | - | An option you added that no candidate uses is deleted: `{deleted:true}`. One that came with the product or is in use is only hidden: `{deleted:false, deactivated:true, inUse:n, option}`. The protected default cannot be removed (422). |

Every change is audit-logged (entity type `form_option`).

### 12.3 Where the lists are enforced

* `POST /api/candidates`, `PATCH /api/candidates/{id}` and `PUT /api/candidates/{id}/application-form` accept a **strict** list value only if
  it is an active option, or already the candidate's current value, or the default (`not_discussed`); otherwise 422
  "<List>: choose one of the available options." A hidden `candidate_source` option cannot be chosen for a new candidate.
* The older check constraints on `service_charges_status` / `interest_level` are gone (0022): the values are whatever the active options are.
* Free-text lists (`marital_status`, `gender`, `job_duration_preference`, `best_time_to_contact`, `education_level`, `language`) are
  never rejected: they only suggest, because documents read by the OCR bring wording of their own.

### 12.4 Data-derived suggestions: `GET /api/form-options/suggestions` (any signed-in user)

```jsonc
{ "countries": ["Qatar", "UAE", "Oman"],      // order, company and candidate country-preference values, most used first (max 100)
  "jobPreferences": ["Welder", "Fitter"],      // the job titles (crm_job_designations), alphabetical (max 300)
  "skills": ["Welding", "Rigging"],            // most used first, case-insensitive duplicates merged (max 200)
  "institutes": ["Govt ITI Kochi"],            // from candidates' education rows, most used first (max 100)
  "companies": ["ACME", "Gulf Cons"] }         // from candidates' previous employers, most used first (max 100)
```
Use them as datalist / autocomplete sources for: country preference, job preference (also the `jobPreference` titles), skills, the education
institute input and the previous-company input. `Cache-Control: private, max-age=60`; refetch when opening the form.

### 12.5 Not made dynamic

Document slots (resume / passport / photo) stay fixed: they are tied to storage and reading logic, not to a list an admin can change.

## 13. Intake draft autosave carries the form fields

`PUT /api/ocr/jobs/groups/{groupId}/draft` (`form: {key: string}` and `editedFields: [key]`) now accepts, besides the older keys, all of:
`fatherName`, `maritalStatus`, `currentAddress`, `experienceIndia`, `experienceAbroad`, `education` and `workHistory` (JSON-encoded strings
of the row arrays), `nativePlace`, `familyMemberNumber`, `jobDurationPreference`, `countryPreference`, `jobPreference`,
`serviceChargesStatus`, `bestTimeToContact`, `interestLevel`, `nextFollowUpDate`, `whatsappSameAsPhone`. Values are strings of at most 20000
characters; unknown keys are still rejected with 422, so the screen can stop stripping these keys before autosaving.

## 14. Aadhaar copies in the application form (13E)

An Aadhaar copy is read like a resume or a passport (`kind: "aadhaar"`; the Add candidate page already scanned it, and a profile upload to `aadhaar` is queued by explicit Extract details after files are ready). What the card says fills the form under the same rules as every other document (section 7):

| Aadhaar value | Form field (`fieldSources` key) | Rule |
| --- | --- | --- |
| name | `personal.candidateName` | fills an empty / extracted field |
| date of birth (a bare year of birth is not a date and is ignored) | `personal.dateOfBirth` | same |
| gender | `personal.gender` | same |
| the S/O, D/O or W/O line of the card (`care_of`, or the first clause of the address) | `personal.fatherName` | same. `W/O` is the husband's name; the recruiter checks it. `C/O` and a bare name name no relation and are ignored |
| address | `personal.currentAddress` | fills an EMPTY current address only (it is the registered address, not necessarily where the person lives); a resume read later may replace the extracted Aadhaar address |

* Provenance is `"extracted"`; a value a person typed (`"manual"`) is never overwritten. Internally the server also remembers which fields came from the card (`_fromAadhaar`, never sent to the client).
* **Precedence for identity fields (name, date of birth, gender, father's name): passport > Aadhaar > resume**, whatever order the documents arrive in. The resume still wins experience, education and everything else an Aadhaar card never states.
* The card's mobile number, VID and enrolment id are never mapped. There is no Aadhaar column on the candidate and none was added.
* **The Aadhaar number is never in a response, an audit entry or a log of these endpoints.** It stays in `ocr_data` (the verbatim Veris result). Beside the candidate the server keeps only the last four digits (`field_sources._aadhaar`, internal). `GET /api/candidates/{id}/application-form` carries `"aadhaar": {"maskedNumber": "XXXX XXXX 1234"}` (or `null` when no card was read), and the Documents section shows that line; the screen re-masks whatever it receives to the last four digits. Audit entries list only field keys (`filled_fields`).
* Replaying stored documents (`POST .../refresh`, the backfill, the save hook) applies them in the order resume, Aadhaar, passport, so the passport outranks the card and the card outranks the resume.
* Known gap, not changed here: `GET /api/candidates/{id}/ocr` returns the stored Aadhaar number unmasked to an admin, a manager and the candidate's owner (everyone else gets it masked to the last four digits); the document job result of an Aadhaar read (`/api/ocr/jobs/...`) also holds the verbatim card for the person who read it. Masking those too is a product decision.

Tests: `backend/tests/test_aadhaar_form_mapping.py` (mapping, fill rule, precedence in every order, masking, audit and log checks), `backend/tests/test_ocr_mapping.py`, `frontend/tests/application-form.test.mjs`, `frontend/tests/candidate-ocr.test.mjs`.

Passport and Aadhaar keep every uploaded front/back/additional file, with per-file reading state and all files included in candidate exports. Aadhaar and application-form uploads are optional and never lower completeness. A failed file retains its failure message and `extractionPending: true` until staff explicitly request another reading. Resume keeps its existing latest-version form view; every page of the selected file is accounted for.
