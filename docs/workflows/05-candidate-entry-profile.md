# Candidate entry and profile correction

Manual entry saves to the same candidate records used by email and WhatsApp. Saving a new person also runs assignment and creates the initial call when an eligible recruiter exists. Source: PRD page 4. Requirements: **CAND-01, CAND-02**.

## Roles and preparation

All authenticated roles can add candidates. Staff can correct their own assigned profiles; supervisors can correct any profile. Shared visibility does not mean shared edit permission. Prepare the candidate's identity/contact details and any resume, passport, photo, education or experience documents.

## User journey

1. Open **Add Candidate**. Start manually or upload a resume first. OCR suggestions help fill the form but are not required for a manual save.
2. Review basic details: full name, phone, WhatsApp, email, date of birth, gender, nationality, current city and languages.
3. Review passport number, issue/expiry dates and place of issue. Upload the passport copy where available.
4. Record preferred designations, total and Gulf/overseas experience, skills, expected salary and source. Keep distinct skills as separate values.
5. Attach the requested supporting documents. The adopted Aadhaar input is an additional supported identity document, not an original PDF requirement.
6. Correct extraction suggestions before save. Human edits must survive a later OCR result. A future birth date, unusable phone or inconsistent experience/date combination needs correction.
7. Save. If phone, email **or** passport matches an existing person, inspect the duplicate dialog. Choose **Open**, **Merge** or **Cancel**. Do not pick an arbitrary record when different identifiers match different people.
8. A merge fills empty fields and adds evidence; it does not replace human corrections, ownership or completed work. A new save assigns according to [rotation](11-staff-assignment-directory.md).
9. Open the saved profile. Review the actual owner, initial-call deadline, documents and next action. A supervisor can assign an unassigned profile; stage work starts only after [order matching](13-order-matching.md).

## Screen and backend contract

Entry and correction show inline validation and preserve form input on failure. Experience uses the backend's current whole-year limits; Gulf experience cannot exceed total experience. Passport issue/expiry validation considers both new and retained values. Unassigned candidates cannot be accepted as fully reviewed until assigned.

Document intake groups retain drafts, edited-field markers, original files and jobs. Creating or explicitly merging with an intake group binds its saved candidate ID in the same database transaction. After response loss, reopening that group offers **Finish saved intake** and **Open saved profile**; retry cannot create another candidate or call task. A queued draft cannot clear or redirect this saved binding.

## Recovery and handoffs

Cancel a duplicate decision without changing the existing profile. When switching intake groups, confirm replacement of the current form; fields from two candidates must never merge. If files fail after the candidate saved, finish the saved intake and retry only unfinished attachments. Open a profile again to refresh current ownership before editing.

## Acceptance checklist

- [ ] Complete a manual entry with every PRD field and supporting document type.
- [ ] Edit a field while OCR is pending; the later suggestion does not overwrite it.
- [ ] Test phone-only, email-only, passport-only and ambiguous duplicate matches.
- [ ] Merge into a profile with human corrections and verify only empty fields change.
- [ ] Lose the save response, recover the intake and verify one candidate, one assignment and one call task.
- [ ] Switch between two saved drafts and verify complete separation of identity and edits.
- [ ] Test no eligible staff, profile assignment and authorized/unauthorized correction.

## Current implementation and remaining acceptance

Entry, duplicate decisions, correction and saved-intake recovery are implemented. Entry retains separate document, identity, passport and work-profile sections; passport details are collected when available without inventing a stage-7 upload requirement. Profile refresh checks current ownership before editing and explains the next call, assignment or stage action. Mapping is unavailable until an active owner exists.

The profile loads full extracted resume text and OCR evidence separately from the compact workspace summary. Evidence failures have **Retry document evidence**; previously loaded evidence is explicitly identified while retrying. The latest follow-up has isolated backend/frontend tests; real OCR quality remains a provider acceptance item. See [entry comparison and evidence](../audit/candidate-entry-followup.md).

Implementation: [entry screen](../../frontend/src/screens/candidates/add-candidate.tsx), [profile](../../frontend/src/screens/candidates/candidate-profile.tsx), [candidate API](../../backend/app/api/routers/candidates.py), [identity service](../../backend/app/services/candidate_identity.py).
