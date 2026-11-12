# Lead sheet contract (backend API)

Status: implemented by migration `0024_lead_sheet`, `app/api/routers/leads.py`, `app/services/lead_parsing.py`,
`app/services/lead_import.py` and `app/services/lead_sheet.py`. The frontend builds against this file; if the
backend changes, this file changes first. User-level description: [Lead sheet workflow](23-lead-sheet.md).

A **lead** is a person who answered a social-media ad (Meta Lead Ads, Google lead forms or any spreadsheet). The agency
uploads the platform's export; every person is stored once; staff call them, ask for an updated resume and either
convert the lead to a Data Bank candidate or close it.

JSON is camelCase (like the other routers). Query parameters are snake_case (like `/api/candidates`). Dates are ISO 8601.
`page` is **0-based** (same as `/api/candidates`). All endpoints need a signed-in session cookie.

## Errors

Most business errors use `detail = {"code": "...", "message": "..."}` (the client shows `detail.message`),
plus extra keys named below. Resume-check and reversal errors can also use a plain `detail` string. FastAPI's own 422 body validation list and the 401 for a missing session stay as usual.

| HTTP | code | when |
| --- | --- | --- |
| 403 | `forbidden_role` | import, assign, export, batch delete by a role that may not |
| 403 | `lead_not_yours` | staff works a lead assigned to someone else, or to nobody |
| 403 | `daily_work_pending` | staff still has open daily work (see **Work gate**) |
| 404 | `not_found` | unknown lead / batch |
| 409 | `already_converted` | call/convert on a converted lead |
| 409 | `duplicate_lead` | PATCH would give the lead a phone/e-mail another lead already has. Extra: `existingLeadId` |
| 409 | `candidate_exists` | convert: the phone/email already belongs to a candidate. Extra: `candidate` `{id, ref, name, phone, ownerId, ownerName, matchedOn[], canMerge}` so the UI can offer **link-candidate** |
| 409 | `resume_verification_required` | an attached/saved resume has no current verified check, its identity/file changed, or final attachment failed; no candidate is created |
| 409 | `resume_identity_mismatch` | the checked document belongs to another person or contains several people; no candidate is created |
| 413 | `file_too_large` | import file over 25 MB, or an xlsx that unpacks to over 50 MB |
| 422 | `unsupported_file` | legacy `.xls`, macro-enabled workbook, zip bomb, empty file, no header row |
| 422 | `no_usable_columns` | no column could be mapped to a phone, e-mail, lead id or name |
| 422 | `too_many_rows` | more than 100,000 data rows |
| 422 | `invalid_request` | bad filter, bad mapping JSON, owner not assignable... |

## Statuses

`status` is one of (in working order): `new`, `contacted`, `interested`, `resume_requested`, `resume_received`,
`converted`, `not_interested`, `invalid`, `wrong_number`. Labels for the UI: New, Contacted, Interested, Resume requested,
Resume received, Converted, Not interested, Invalid, Wrong number. Enforced by a check constraint.

Call outcomes (`POST /{id}/call`): `answered`, `interested`, `not_interested`, `no_answer`, `wrong_number`.

| outcome | status becomes | other effect |
| --- | --- | --- |
| `answered` | `contacted` if it was `new`, else unchanged | |
| `interested` | `interested` (unless already `resume_requested` / `resume_received`) | |
| `not_interested` | `not_interested` | |
| `wrong_number` | `wrong_number` | |
| `no_answer` | unchanged | |

Every call increments `callCount` and sets `lastContactedAt`. `resume_requested` is set by `PATCH` (status). A resume
uploaded to a lead (below) sets `resume_received` automatically.

Activity kinds: `call`, `note`, `status`, `assign`, `import`, `convert`, `resume`, `edit`.

## Work rules (who may do what)

| action | admin | manager | staff |
| --- | --- | --- | --- |
| read list / summary / detail / batches | yes | yes | yes |
| import, dry-run, delete a batch (admin only), assign, export | yes (delete batch: admin only) | import / assign / export yes | **403 `forbidden_role`** |
| call, PATCH, check resume, convert, link-candidate, upload resume | any lead | any lead | **only leads assigned to them** (`lead_not_yours`) and only when not gated |
| check or perform Move back to Lead | yes, subject to unused-candidate guards | no | no |

Staff cannot claim leads; only admin/manager assign.

### Work gate (daily tasks first)

A staff member must finish the daily work quota (Tasks > Daily work: today's items plus anything postponed) before they
may work the lead sheet. For a **staff** user the server answers `403 daily_work_pending` on
`POST /{id}/call`, `PATCH /{id}`, `POST /{id}/convert`, `POST /{id}/link-candidate` and on a resume upload to a lead
(`POST /api/files` with `entity_type=lead`) while the gate is locked. Resume-check start and status endpoints also enforce the work gate. Reading the lead list and saved detail is never gated. Admin and manager are never gated.

```json
{ "code": "daily_work_pending",
  "message": "Finish today's tasks first. You have 3 daily tasks left (1 postponed). Then you can work the lead sheet.",
  "dailyWorkGate": { "locked": true, "open": 3, "total": 5, "postponed": 1, "nextDueAt": "2026-11-06T06:30:00+00:00", "message": "..." } }
```

`dailyWorkGate` (also returned by `GET /summary`, and by `GET /{id}`):
`locked` is true when the user is staff and has at least one `workflow_daily_work_items` row with `status='open'` and
`work_date <= today` (agency timezone). `open` = such rows; `postponed` = the ones from earlier days; `total` = open plus
completed items of today; `nextDueAt` = earliest open `due_at` (or null); `message` is the text above (null when unlocked).
Nothing allocated (day off, leave, quota already done, no candidates) means unlocked. The gate is computed per request (never
cached). It changes when daily work changes, which already publishes the live topic `tasks`.

## Lead object

Used in list rows and `GET /{id}` (detail adds `extra`, `raw`, `activities`, `resume`, `dailyWorkGate`).

```json
{
  "id": "uuid", "externalId": "179…", "batchId": "uuid", "importRow": 12,
  "createdTime": "2026-11-04T19:53:51+05:30",
  "name": "Full Name", "phone": "+919876543210", "phoneCountryCode": "91", "email": "a@b.com",
  "whatsapp": null, "whatsappSame": null,
  "city": "Kochi", "state": "Kerala", "currentLocation": "Kerala",
  "positionApplied": "South Indian Chef (CDP)", "countryInterest": "Dubai",
  "experienceRestaurantYears": 10, "experienceRestaurantText": "10+ years",
  "experienceTotalYears": 1, "experienceTotalText": "1-3 years",
  "gender": "Male", "maritalStatus": "Married", "dob": "1990-09-25",
  "educationLevel": "High school / GED", "militaryStatus": "No",
  "platform": "ig", "isOrganic": false, "leadStatusSource": "complete",
  "campaignId": "c:…", "campaignName": "Dubai-Chef-03-11", "adsetId": "…", "adsetName": "…",
  "adId": "…", "adName": "…", "formId": "…", "formName": "…",
  "status": "new", "ownerId": "uuid|null", "ownerName": "Asha|null", "assignedAt": null,
  "lastContactedAt": null, "nextFollowUpAt": null, "callCount": 0, "notes": null,
  "candidateId": null, "candidateRef": null,
  "matchedCandidateId": null, "matchedCandidateRef": null, "matchedCandidateName": null,
  "hasResume": false, "resumeReceivedAt": null,
  "createdAt": "…", "updatedAt": "…"
}
```

Detail only: `extra` (every file column that has no CRM field, header to raw text), `raw` (the whole original row, header to text),
`activities` `[{id, kind, summary, userId, userName, meta, createdAt}]` newest first (max 100),
`resume` `{id, name, sizeKb, version, url, uploadedAt}|null` (latest resume of the lead; open with `GET /api/files/{id}`),
`dailyWorkGate`. For a converted lead the candidate's resume is the one to show: `candidateId` is set.

Normalisation of a stored lead (all editable by `PATCH`): see **Import parsing** below.

## Endpoints

### POST /api/leads/import  (admin, manager)

`multipart/form-data`:

| field | meaning |
| --- | --- |
| `file` | required. `.csv`, `.tsv`, `.txt`, `.xlsx` (25 MB max) |
| `dry_run` | `true`: parse and report only, nothing is stored (default `false`) |
| `mapping` | optional JSON `{"<header as in file>": "<field>" | null}` overriding the automatic header mapping; `null` sends the column to `extra` |
| `assign_to` | optional: a staff user id, or `auto` (spread the new leads evenly over available staff). Default: unassigned |
| `campaign_label` | optional text used as campaign name for rows whose file has none |
| `fill_empty` | `true`: for a duplicate of a stored lead, copy values the stored lead is missing (never contact fields, never workflow data). Default `false` |
| `source` | `meta_ads` (default) or `other` |

Response `200` (the same shape for a dry run; `batchId` is null and nothing was written):

```json
{
  "dryRun": false, "batchId": "uuid|null",
  "fileName": "x.csv", "fileSize": 24242, "fileSha256": "…",
  "sameFileBefore": { "batchId": "uuid", "uploadedAt": "…", "rowsNew": 30 },
  "format": "tsv", "encoding": "utf-16-le", "delimiter": "\t", "source": "meta_ads",
  "rowsTotal": 30, "rowsNew": 28, "rowsDuplicate": 1, "rowsInvalid": 1, "rowsMatchedCandidates": 2, "rowsUpdated": 0,
  "columns": [ { "header": "full_name", "field": "name", "confidence": "exact" },
               { "header": "how_many_years_of_experience_do_you_have_in_the_restaurant_industry?", "field": "experienceRestaurant", "confidence": "alias" },
               { "header": "my_custom_question", "field": null, "confidence": null } ],
  "unmappedColumns": ["my_custom_question"],
  "duplicates": [ { "row": 7, "reason": "phone", "scope": "database", "name": "…", "phone": "+91…", "existingLeadId": "uuid", "existingRow": null } ],
  "duplicatesTruncated": false,
  "invalid": [ { "row": 9, "reason": "bad_phone", "message": "Phone number is not valid", "name": "…", "phone": "…" } ],
  "invalidTruncated": false,
  "sample": [ /* dry run only: first 20 normalised rows, camelCase lead fields (no ids) with "outcome": "new"|"duplicate"|"invalid" */ ],
  "assignedTo": null, "assignedCount": 0
}
```

* `columns[].field` and the values of `mapping` are these keys: `externalId, createdTime, name, firstName, lastName, phone, whatsapp, email, city, state,
  currentLocation, positionApplied, countryInterest, experienceRestaurant, experienceTotal, gender, maritalStatus, dob, educationLevel, militaryStatus, platform,
  isOrganic, leadStatusSource, campaignId, campaignName, adsetId, adsetName, adId, adName, formId, formName` (`null` = keep the column in `extra`). `firstName` + `lastName` are joined into the name when there is no full-name column.
* `confidence`: `exact` (header equals the field name), `alias` (known variant such as `full_name`, `Mobile`, `E-mail`), `fuzzy` (recognised by a keyword such as "phone" in the header), `override` (from `mapping`), `null` when unmapped.
* `sample` rows (dry run only): `{row, externalId, createdTime, name, phone, email, city, state, currentLocation, positionApplied, countryInterest, experienceRestaurantYears, experienceTotalYears, gender, maritalStatus, dob, educationLevel, militaryStatus, platform, campaignName, adName, formName, outcome}`.
* `row` is the spreadsheet row number (header = row 1, so the first lead is row 2).
* Row outcomes: `new` (stored), `duplicate` (ignored), `invalid` (ignored). Blank rows are skipped and not counted.
* `duplicates` / `invalid` hold the first 200 in this response; `GET /batches/{id}/report.csv` has up to 5,000 of each.
* `reason` for duplicates: `lead_id`, `phone`, `email`. `scope`: `file` (an earlier row of this file; then `existingRow` is its row) or `database` (a stored lead; `existingLeadId`).
* `reason` for invalid: `no_contact` (no usable phone and no e-mail), `bad_phone` (a phone was given but is not 7 to 15 digits, and there is no e-mail).
* Re-uploading the very same file stores 0 new leads. `sameFileBefore` (matching SHA-256) lets the UI warn first.
* A real import is one transaction under an advisory lock (two simultaneous imports cannot both insert the same person).

### GET /api/leads/batches?page=0&page_size=20  (all)

`{ "items": [ Batch ], "total": n, "page": 0, "pageSize": 20 }`, newest first.

```json
Batch = { "id", "fileName", "fileSize", "fileSha256", "source", "format", "encoding", "delimiter",
          "uploadedBy", "uploadedByName", "createdAt", "status": "completed", "campaignLabel", "assignedTo", "fillEmpty",
          "rowsTotal", "rowsNew", "rowsDuplicate", "rowsInvalid", "rowsMatchedCandidates", "rowsUpdated",
          "columnMap": { "header": "field" }, "unmappedColumns": [], "notes": null,
          "leadsRemaining": 28 }
```

### GET /api/leads/batches/{id}  (all)

Batch plus `duplicates` and `invalid` (first 200 each, shapes as in the import response, and the `…Truncated` flags).

### GET /api/leads/batches/{id}/report.xlsx  (admin, manager)

The default import report, always with content. Sheets: **Summary** (file name, uploaded by, uploaded on in the agency time zone, detected
format and encoding, rows in file, new leads added, duplicates ignored, invalid rows, already in the Data Bank, and a plain sentence such as
"No duplicates or invalid rows: all 30 rows were added."), **All rows** (one line per file row: row, outcome `Added` / `Ignored: duplicate` /
`Invalid`, reason, name, phone, e-mail, city, position, campaign, and for a duplicate the matched stored lead and candidate) and
**Ignored & invalid** (only those, with a friendly line when there are none). Bold filled header, frozen header row, auto-filter, column widths;
phone numbers are text cells and dates are real Excel dates; every string is an inline string, so a value starting with `=` is never a formula.
Added rows come from the batch's own leads (`import_row`), ignored and invalid rows from the batch `report` JSON (first 5,000 of each; a note
says so). A batch with no stored row details says "Row details were not stored for this older upload." File name:
`lead-import-<yyyy-mm-dd>-<file name>.xlsx`.

### GET /api/leads/batches/{id}/report.csv  (admin, manager)

The same rows as **All rows**, UTF-8 with BOM (opens in Excel): `Row,Outcome,Reason,Name,Phone,E-mail,City,Position,Campaign,Matched stored lead: name,…`,
plus `Note` lines. Text cells starting with `= + - @` are prefixed with `'` (formula safe).

### DELETE /api/leads/batches/{id}  (admin)

Undo a wrong upload: removes only that batch's leads that are still untouched (`status=new`, no owner, no calls, no
candidate). Returns `{ "deleted": n, "kept": m }`. The batch row stays (marked `status: "reverted"` when all its leads are gone).

### GET /api/leads  (all)

`?page=0&page_size=25&sort=created&direction=desc&q=…` plus filters (all optional, combined with AND):

| param | meaning |
| --- | --- |
| `q` | case-insensitive; comma-separated terms must **all** match; each term is a substring of name, phone digits (a phone typed with spaces/+ also works), e-mail, city, state, position, country interest, campaign, ad, ad set, form, owner name, notes, status (label or key) |
| `status` | one or more (repeat the param or comma-separate) |
| `campaign`, `form`, `position`, `country`, `platform` | exact (case-insensitive) match on the stored value; comma-separate for several |
| `owner` | `me`, `unassigned`, or a user id |
| `created_from`, `created_to` | `YYYY-MM-DD`, agency timezone, on the lead's `createdTime` |
| `has_resume` | `true` / `false` |
| `matched_candidate` | `true`: already in the Data Bank (flagged at import); `false` |
| `follow_up` | `due` (follow-up date has arrived) or `none` |
| `batch_id` | one upload |

`sort`: `created` (default), `name`, `status`, `owner`, `campaign`, `position`, `follow_up`, `last_contacted`, `calls`, `experience`. Ties break on id.
Response: `{ "items": [Lead], "total": n, "page": 0, "pageSize": 25 }`. A page past the end returns the last page.

### GET /api/leads/columns  (all)  `?campaign=&form=&batch_id=`

The custom question columns found in the stored sheets (the importer keeps the answers it has no lead field for in `extra`, header to text):
`{ "columns": [{ "key": "<sheet header>", "label": "Do you have a passport", "count": 12 }] }`, most answered first, at most 60. Narrowed to the
campaign, form or upload when given. The Lead sheet offers one optional table column per entry, headed with the sheet's own wording.

`GET /api/leads` also accepts `extra=*` (every custom answer of each lead, `item.extra`) or `extra=<key>` (just those; up to 12 keys) and
`page_size` up to 1000 (the "All" page size; the screen says when more exist).

### GET /api/leads/facets  (all)

Distinct values for the filter drop-downs: `{ "campaigns": [..], "forms": [..], "positions": [..], "countries": [..], "platforms": [..], "owners": [{id,name,count}] }`.

### GET /api/leads/summary  (all)  `?scope=mine|all`

Default `scope` is `mine` for staff and `all` for admin/manager. Numbers are over the leads in scope (`mine` = assigned to the viewer).

```json
{ "scope": "mine", "total": 120, "newToday": 14, "unassigned": 30, "uncontacted": 80, "interested": 9, "resumeRequested": 4,
  "resumeReceived": 3, "converted": 2, "followUpsDue": 5, "matchedCandidates": 7, "withResume": 5,
  "byStatus": { "new": 80, "contacted": 20 }, "byCampaign": [ { "campaign": "Dubai-Chef-03-11", "count": 120, "converted": 2 } ],
  "lastImportAt": "…", "dailyWorkGate": { "locked": false, "open": 0, "total": 5, "postponed": 0, "nextDueAt": null, "message": null } }
```

(`unassigned` is always the global count of unassigned leads, since it is the manager's to-do.)

### GET /api/leads/export.xlsx and /export.csv  (admin, manager)

Same filter parameters as the list (no paging); at most 50,000 rows. Formula safe (xlsx uses inline strings, csv prefixes `'`).

### GET /api/leads/{id}  (all)

Lead detail (see above).

### PATCH /api/leads/{id}  (owner staff, admin, manager)

Body (camelCase, all optional): `status`, `notes`, `nextFollowUpAt` (ISO, or null to clear), `positionApplied`, `countryInterest`, `name`,
`phone`, `email`, `whatsapp`, `city`, `state`, `gender`, `dob`, `maritalStatus`, `educationLevel`, `experienceRestaurantYears`, `experienceTotalYears`.
Changing a contact field re-computes the dedupe keys. `status: "converted"` is refused (use convert). Writes one activity row per kind of change
(`status`, `note`, `edit`). Returns the Lead.

### POST /api/leads/{id}/call  (owner staff, admin, manager)

Body: `{ "outcome": "answered|interested|not_interested|no_answer|wrong_number", "remarks": "text", "nextFollowUpAt": "ISO|null" }`.
Applies the table in **Statuses**. Returns the Lead.

### POST /api/leads/assign  (admin, manager)

Body: `{ "ids": ["…"] | null, "filter": { …same keys as the list query, snake_case… } | null, "ownerId": "user-id" | "auto", "onlyUnassigned": false }`.
Give `ids` or `filter`. `auto` spreads the leads evenly over active staff who receive auto-assignment and are not on leave (the staff
with the fewest open leads first); it uses its own cursor, so the candidate round robin is not disturbed. A specific `ownerId` must be an
assignable staff member. Converted leads are never moved. Returns `{ "assigned": n, "skipped": m, "owners": [ {id, name, count} ] }`.
Each new owner gets one notification (`kind: lead_assignment`, link `/leads`) per assignment.

### Check resume before conversion (owner staff, admin, manager)

`POST /api/leads/{id}/resume-check` accepts multipart `resume` (optional; otherwise the latest saved lead resume) and `overrides` (optional JSON object containing only a string `name`, the candidate name shown in the conversion dialog). With no selected or saved resume it returns 422. It starts or reuses a durable reading without creating a candidate.

`GET /api/leads/{id}/resume-check/{jobId}` reads that user's check for the same lead. Both endpoints return:

```json
{ "jobId": "uuid", "status": "pending|processing|verified|rejected|needs_review|failed", "canConvert": false, "message": "..." }
```

Only a completed, identity-readable resume with complete output and no conflicting identity or multiple-person result is `verified` with `canConvert: true`. Wrong identity is `rejected`; incomplete or uncertain identity stays `needs_review`. Neither can be bypassed by the conversion UI.

The proof binds the user-owned job to the lead ID/name/phone/email/date-of-birth snapshot, effective candidate name, exact file hash and, when using the saved resume, its source attachment ID. A changed identity, different file or replaced saved attachment requires another check. Identity is revalidated against current records on status reads and conversion; there is no time-based proof expiry.

### POST /api/leads/{id}/convert  (owner staff, admin, manager)

`multipart/form-data`: `resume` (optional PDF / Word / image), `resume_check_id` (required whenever a selected or saved resume exists), `overrides` (optional JSON string with any of `name, phone, email, dob,
gender, currentCity, designations[], totalExperience, countryPreference, sourceDetail, assignTo` to correct the lead's values before the candidate is created). Conversion with no selected or saved resume remains allowed.
Creates a Data Bank candidate through the normal candidate path (duplicate detection, owner assignment, initial-call task, notifications):
`source = other`, `sourceDetail = campaign name`, `designations = [positionApplied]`, `countryPreference = countryInterest`,
owner = the lead's owner when that person is assignable, else the usual next free recruiter. A selected or saved resume must match the current verified check. Its stored extraction is reused through the normal identity and field-provenance guards, without another provider extraction. Candidate creation, the verified candidate attachment, retention of the original with the lead, and the converted lead link commit together; final identity rejection or attachment failure rolls the conversion back. If a candidate with the same
phone/email exists: `409 candidate_exists` (nothing is created). On success the lead becomes `converted` (`candidateId` set) and the response is
`{ "lead": Lead, "candidate": {id, ref, name, ownerId, ownerName, taskId}, "resumeAttached": true, "resumeError": null }`.

### Move back to Lead (admin only)

`GET /api/leads/{id}/unconvert-check` returns `{allowed, reason, candidate: {id, ref, name}|null, restoredStatus}`. A refusal explains the actual blocking history. No record is changed by this check.

`POST /api/leads/{id}/unconvert` accepts `{candidateId, reason}`; the normalized reason must contain 3–1000 characters. The exact candidate must have been created by this lead, proven by the conversion activity and origin audit, with a verifiable previous lead status. Linking an existing candidate is never reversible through this action.

Other linked leads or inbox/document sources, any application (including closed ones), invoice/payment records, ticket/invoice attachments, notes/feedback, task events or work already started block removal. Only the untouched automatic initial-call task from conversion may be removed. The server locks and rechecks these conditions when saving.

Success removes the unused candidate, clears its link, restores the prior lead status, retains the lead's original fields/files/history, syncs its follow-up and records the actor/reason in lead history and audit. Response: `{lead: Lead, removedCandidateId, restoredStatus, alreadyReversed}`. Retrying the same candidate ID after a successful reversal returns `alreadyReversed: true`; a different/reconverted candidate is refused with 409.

### POST /api/leads/{id}/link-candidate  (owner staff, admin, manager)

Body `{ "candidateId": "…" }`. Links the lead to an existing candidate and marks it `converted`. Returns the Lead.

### Resume for a lead (before conversion)

Use the existing file endpoints with `entity_type=lead`: `POST /api/files` (`entity_type=lead`, `entity_id=<lead id>`, `slot=resume`, `file`),
`GET /api/files?entity_type=lead&entity_id=…`, `GET /api/files/{id}`. Same permission rule as call (assigned staff, admin, manager;
staff gated by the daily tasks). The upload sets the lead to `resume_received` and logs a `resume` activity. Each upload is a new version.
Uploading alone does not read a lead's resume. **Check resume** reads and verifies it before conversion; conversion reuses that verified output.

## Import parsing

* **Encoding**: BOM UTF-16 LE/BE, UTF-8 with BOM, UTF-8, else Windows-1252. **Delimiter**: tab, comma or semicolon (header-line vote, quote aware).
  Meta's `.csv` is UTF-16 LE with TAB delimiters. CRLF/LF/CR, quoted fields with embedded delimiters/newlines and `""` quotes are handled.
* **Format** by content, not extension: ZIP signature = `.xlsx` (no extra library: zip + XML; shared and inline strings; numbers; Excel date serials only
  in columns whose header looks like a date; first sheet; empty and merged-away rows skipped). Limits: 25 MB upload, 50 MB unpacked, 100,000 rows, macro
  (`vbaProject.bin`) rejected, XML entities never resolved. Legacy `.xls` (OLE signature) → `422 unsupported_file` "Save as .xlsx or .csv".
* **Header mapping**: case-insensitive; spaces, underscores, hyphens and a trailing `?` are ignored; a dictionary of aliases (Meta, Google Lead Forms, custom sheets:
  `Full Name`, `candidate name`, `Phone`, `Mobile`, `phone_number`, `Contact`, `WhatsApp`, `E-mail`, `DOB`, `Position`, `Job`, `Experience` …). Question-style Meta
  headers (`which_position_are_you_applying_for?`, `how_many_years_of_experience_…restaurant_industry?`, `what_is_your_total_years_of_work_experience?`)
  map to position / restaurant experience / total experience. Any other column is kept in `extra`; the full row is kept in `raw`.
* **Phone** (`phone`, `phoneCountryCode`, dedupe key `phone_key`): drop a leading `p:` and every non-digit, drop leading `00`. Valid length is 7 to 15 digits.
  More than 10 digits: the country code is read from a list of common calling codes (`91`, `971`, `966`, `974`, `965`, `968`, `973`, `977`, `94`, `880`, `92`, `44`, `1` ...; else the digits before the last 10)
  (`+919876543210` → code `91`, `+971501234567` → `971`).
  Exactly 11 digits starting with `0`: the trunk `0` is dropped. `phone` is shown as `+<code><national>` when a code is present, else the digits. A bare `p:` counts as empty.
  **Matching uses the last 10 digits** (all digits when fewer than 10; made for Indian 10-digit numbers), so `+91 98765 43210`, `098765 43210` and `9876543210` are the same person.
* **E-mail** (`email`, key `email_key`): trimmed, lowercased, must contain `@`.
* **Lead id** (`externalId`): Meta's `l:179…` without the `l:` prefix; ids of ads/forms/campaigns keep their value without the 1-2 letter prefix (`ag:`, `as:`, `c:`, `f:`).
* **Dates**: `createdTime` ISO with offset (naive values are taken in the agency timezone). `dob`: ISO `YYYY-MM-DD`; `MM/DD/YYYY` (Meta) unless the first number
  is above 12, then `DD/MM/YYYY`; `DD-MM-YYYY`; Excel serials; unparseable or future/before 1920 → null (the raw text stays in `raw`).
* **Experience**: `10+ years` → 10, `1-3 years` → 1 (the minimum), `22` → 22, `6 months` → 0.5, free text with no number → null, a number above 60 → null; the text is kept in `…Text`.
* **Prettified values**: snake_case to words (`south_indian_chef_(cdp)` → `South Indian Chef (CDP)`); state aliases (`Tamilnadu`, `tamil nadu`, `TN` → `Tamil Nadu`, `kerala`, `KL` → `Kerala`, …);
  gender → `Male` / `Female` / `Other`; platform `ig`/`fb` kept lower case (`ig` = Instagram, `fb` = Facebook).
* **Country interest** (suggestion, editable): the first known country/city word in the campaign, ad, ad set or form name (`Dubai-Chef-03-11` → `Dubai`).

## Dedupe rules

Applied to every row, against earlier rows of the same file and against all stored leads. The first match wins and decides the reason:

1. same `externalId` (Meta lead id) → duplicate (`lead_id`);
2. else same `phone_key` → duplicate (`phone`);
3. else same `email_key` → duplicate (`email`).

A duplicate is **ignored** (counted, listed with its reason and which row/lead it matched) and never overwrites the stored lead's CRM data.
With `fill_empty=true` only the still-empty descriptive fields of the stored lead are filled from the newer row (never phone, e-mail, status, owner, notes, follow-up).
Uploading more of the same ad later therefore continues where the last upload stopped: only people not yet stored are added.
Leads whose phone/e-mail already exists in the Data Bank (`crm_candidates`) are **not blocked**: they are stored with `matchedCandidateId` set and counted in `rowsMatchedCandidates`.
A database unique index on `external_id` backs rule 1 even if two imports race.

## Live updates

Table `crm_leads`, `crm_lead_batches` and `crm_lead_activities` feed the live topic **`leads`** (client-visible, no ids for staff). The lead screens subscribe to
`leads` and `tasks` (the daily-work gate). Uploading a resume also touches `documents`/`candidates` through `document_attachments`.

## Storage (for reference)

`crm_lead_batches` (one row per upload), `crm_leads` (one row per person; unique index on `external_id`, indexes on `phone_key`, `email_key`, `(owner_id, status)`,
`campaign_name`, `created_time`), `crm_lead_activities` (call/note/status/assign/import/convert history). Resume files use `document_attachments`
with `entity_type = 'lead'`, `slot = 'resume'`.

## Campaigns, follow-up reminders, late matching, WhatsApp templates (Task 13B)

No migration: everything below uses existing tables (`crm_leads`, `personal_todos`, `crm_form_options`, `crm_lead_activities`).
Implemented in `app/services/lead_campaigns.py`, `lead_followups.py`, `lead_matching.py`, `lead_templates.py` and the router
`app/api/routers/lead_campaigns.py` (registered **before** the leads router in `app/main.py`, because `/api/leads/campaigns` would
otherwise be read as `/api/leads/{lead_id}`).

### GET /api/leads/campaigns  (all)

One set-based statement, whatever the number of campaigns. Admin and manager see every lead; **staff see only the leads assigned to them**
(`scope: "mine"`, so every count is their own). Rates are percentages with one decimal (0 to 100).

```json
{ "scope": "all", "truncated": false, "generatedAt": "…",
  "total": { "…same fields as an item, campaign null…": 0 },
  "items": [ { "campaign": "Dubai-A|null", "form": null, "leads": 10, "newToday": 3, "last7Days": 5,
               "assigned": 6, "assignedPct": 60.0, "contacted": 6, "contactedPct": 60.0, "interested": 4, "resumeReceived": 1,
               "converted": 1, "conversionRate": 10.0, "lastUploadAt": "…|null",
               "platform": { "fb": 5, "ig": 4, "other": 1 },
               "topPositions": [ { "name": "South Indian Chef (CDP)", "count": 4 } ], "topCountries": [ { "name": "Dubai", "count": 10 } ],
               "forms": [ { "…same fields…": 0, "form": "Form-A1" } ] } ] }
```

Sorted by `leads` descending (forms too); at most 500 campaigns (`truncated`). Definitions: **newToday** arrived since midnight in the agency
time zone (`created_time`, else the day it was stored); **last7Days** the last 7 days including today; **assigned** has an owner; **contacted** has a
call or a status past New; **interested** is Interested, Resume requested, Resume received or Converted; **resumeReceived** is the Resume received
status or an uploaded resume; **conversionRate** = converted / leads; **lastUploadAt** the newest upload that brought in any of these leads; `platform` splits
`fb` / `ig` / everything else. A lead without a campaign name is listed as `campaign: null` ("No campaign").

### Follow-up reminders (personal to-dos)

A lead with `nextFollowUpAt`, an owner and an open status has exactly one open to-do for its owner: title `Follow up lead: <name>`, due 09:00 agency time on the
follow-up day, `linkTo = /leads?lead=<lead id>`, `linkLabel = "Lead follow-up"`, priority normal (the same to-do list and reminder worker as the candidate
follow-up). `app.services.lead_followups.sync(db, lead_ids)` keeps them in step, set-based (three statements for any number of leads), idempotent, in the caller's
transaction. Every path that changes the date, the status, the owner, the name or deletes leads calls it: `PATCH /{id}`, `POST /{id}/call`, `POST /assign`, convert,
link-candidate and the bulk actions (status, assign, delete).

* date set or moved: the to-do is created or moved (its reminder is re-armed);
* date cleared, lead converted / not interested / invalid / wrong number, lead deleted, lead with no owner: the open to-do is removed;
* lead reassigned: the open to-do moves to the new owner;
* a to-do already ticked off is left alone and is not created again for the same due time; a new date makes a new one.

`GET /api/leads/summary` adds `followUpsDueMine` (the viewer's own open leads whose follow-up day is today or earlier) next to `followUpsDue` (in the summary's scope).
`follow_up=due` (list, export, bulk filter) now means **the follow-up day has arrived**: `nextFollowUpAt` before the end of today in the agency time zone, lead still open
(it used to mean "the moment has passed"). Opening `/leads?lead=<id>` shows that lead's drawer.

### Late matching of candidates to stored leads

When a candidate is created, or its phone, WhatsApp number or e-mail changes, every stored lead with the same person key (last 10 digits of the phone, lower-case
e-mail) that is not yet flagged and not yet linked gets `matchedCandidateId`, in the **same transaction**, with **one set-based statement** (`app.services.lead_matching`).
It is a SQLAlchemy `after_flush` hook installed by `app/db/session.py` next to the change feed, so every path that saves a Candidate through the ORM (create, patch, merge,
e-mail and WhatsApp intake, manual extraction) is covered. An existing flag is never overwritten; converting or linking a lead clears a flag that names the same candidate.
Writes that bypass the ORM are caught up by `python -m app.cli backfill-lead-matches [--dry-run]` (it also creates the follow-up to-dos of leads that had a date before this feature).

`GET /api/leads/for-candidate/{candidate_id}` (all): `{ "leads": [ { id, name, campaignName, formName, status, converted } ] }` (at most 5): the leads this candidate came from
(`converted: true`) or matches by phone / e-mail. The candidate profile shows "From lead sheet: <campaign>" linking to `/leads?lead=<id>`.

### WhatsApp templates and the message log

* `GET /api/leads/wa-templates` (all): `{ templates: [ { key, title, body, isDefault, defaultTitle, defaultBody } ], placeholders: ["{name}", "{position}", "{company_name}"], companyName, titleMax: 120, bodyMax: 255 }`.
  Keys: `first_contact`, `request_resume`, `follow_up`. The defaults are constants in `lead_templates.py`.
* `PUT /api/leads/wa-templates` (**admin**): `{ templates: [ { key, title, body } | { key, reset: true } ] }`. Stored as rows of `crm_form_options` with category `lead_wa_template`
  (value = key, label = title, hint = message), so no migration; saving the default wording or `reset` removes the row. Unknown key or placeholder, empty or over-long text: 422.
  The category is not part of the candidate form's option lists. Changing it is audited.
* `POST /api/leads/{id}/whatsapp` `{ template, message }` (same permission as a call, including the daily-work gate): writes a history entry of kind `note`,
  summary `WhatsApp message opened: <text>`, meta `{ kind: "whatsapp", template, length }`. The screen opens `https://wa.me/<digits>?text=<message>` itself.

## Bulk actions and delete (13A)

Code: `app/api/routers/lead_bulk.py` (thin), `app/services/lead_bulk.py` (rules), screen: `frontend/src/features/leads/use-lead-bulk.tsx`, `lead-bulk-model.ts`.

### POST /api/leads/bulk

```json
{ "action": "assign | status | not_interested | export | delete",
  "ids": ["..."]  or  "filter": { "q": "chef", "status": "new", "owner": "unassigned" },
  "ownerId": "<user id> | auto",      // assign
  "status": "contacted",              // status
  "onlyUnassigned": false,            // assign: skip leads that already have an owner
  "confirm": "DELETE",                // delete of more than 20 leads
  "expectedCount": 3000,              // filter selections: the count the person confirmed
  "sort": "created", "direction": "desc" }   // export only
```

* **Selection**: `ids` (at most 10,000) **or** `filter`, which has exactly the query parameters of `GET /api/leads` (snake_case: `q`, `status`, `campaign`, `form`, `position`, `country`,
  `platform`, `owner`, `created_from`, `created_to`, `has_resume`, `matched_candidate`, `follow_up`, `batch_id`; a list or a comma-separated string) and is resolved with the same
  `apply_filters` as the list, so "select all N matching" acts on exactly the rows the list shows. An unknown filter key is `422` (never silently ignored). `owner=none` is an alias of `unassigned`
  (the Lead sheet's "Unassigned" choice sends `none`). Neither `ids` nor `filter` is `422 invalid_request`.
* **Hard cap**: 10,000 leads per call. More (ids, or a filter matching more) is `422 too_many_leads` with `limit: 10000` and a message asking to narrow the filter or work in parts.
* **expectedCount** (filter selections): when the number of matching leads differs from it (the list changed while the person confirmed) the call is refused with `409 selection_changed`
  (`matching: n`) and nothing changes.
* **Set-based**: the rows are resolved and **locked** (`SELECT ... FOR UPDATE`, newest first) in one statement; then one insert of the activity rows (`crm_lead_activities`, kinds `status` / `assign`,
  `meta.bulk = true`) and one `UPDATE` per new owner (or one for the status). 5,000 leads is a few dozen statements at most, never one per lead. Every write statement starts with INSERT / UPDATE / DELETE so the change
  feed announces the live topic `leads`. A lead changed or converted by someone else while the call waited for the lock is read after that writer committed: it is skipped (or handled) without an error.
* **Result** `200`: `{ action, requested, updated, skipped, skippedReasons: [{code, count, message}], ... }` (+ `owners: [{id, name, count}]` for assign, `status` for status). Reason codes:
  `converted` (converted or linked to a candidate: never changed, moved or deleted by a bulk action), `already_set` (already had that status), `already_owner`, `not_yours` (staff), `gone` (an id that no longer exists),
  `changed`. The screen shows "N leads updated, K skipped (2 converted or linked to a candidate; ...)".
* **Permissions**: `assign` and `export`: admin / manager (`403 forbidden_role`). `delete`: admin only. `status` / `not_interested`: admin / manager on any lead; **staff** only on leads assigned
  to them (others are skipped as `not_yours`, a filter selection is narrowed to `owner=me`) and only when the daily-work gate is open (`403 daily_work_pending`, same body as elsewhere).
  `converted` can not be set (`422`; use Convert). The Lead sheet screen shows the selection bar to admin / manager only.
* **assign**: same rules as `POST /assign` (an `ownerId` must be an assignable staff member; `auto` spreads evenly over available staff by open lead count; converted leads never move; leads that already belong
  to the person are skipped as `already_owner`), one notification (`lead_assignment`) per new owner.
* **export**: the response is the styled workbook (`lead-sheet-selected.xlsx`, header `X-Lead-Count`), the same columns and writer as `GET /export.xlsx` (`lead_xlsx`) with the custom question columns
  of the exported leads; no write, no live change. Order: `sort` / `direction` (default newest first).
* **delete** (admin): deletes the non-converted leads of the selection. More than 20 leads need `confirm: "DELETE"` (`422 confirmation_required`, `count: n`). Converted / linked leads are skipped as `converted`.
* **Audit** (`system_audit_log`): one summary entry per call (`Bulk status change`, `Bulk lead assignment`, `Bulk lead delete`, entity `lead_bulk`), and for delete one entry per deleted lead
  (`Deleted lead`, target = the name, meta `externalId`, `status`, `campaign`, `bulk`) written in one set-based insert before the rows go.

### DELETE /api/leads/{id}  (admin)

Deletes one lead: `{ "deleted": 1, "id": "..." }`. `404 not_found`; `403 forbidden_role` for manager and staff; **`409 lead_linked`** when the lead was converted or is linked to a candidate
(the message names the candidate ref; nothing is unlinked, the person is managed from the Data Bank). Removes the lead's activities (cascade), its resume attachment rows and, once committed and when no other
attachment uses it, the stored file; writes a `Deleted lead` audit entry.

**Decision: no tombstone.** A deleted lead is simply gone, so uploading a later sheet that contains the same person adds them again (dedupe only knows stored leads). The confirmation says so:
"If this person is in a later upload they will be added again." The upload's own counts (`rowsNew`) stay as they were; `leadsRemaining` is counted live and drops. (A "deleted leads" memory would need a migration.)

### Status change and undo

A single status change uses the existing `PATCH /api/leads/{id}` `{status}` (row menu "Change status" / "Not interested", drawer). The screen remembers the previous status and offers an **Undo** toast for 10 seconds
("Marked not interested. Undo"); Undo is a second `PATCH` with the previous status, so both changes are in the lead's history (`status` activities). Bulk status changes have no undo.
