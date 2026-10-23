# Candidate entry workflow follow-up — 22 October 2026

The comparison uses the original Recruitment CRM Developer Specification, page 4, and the reference repository at `30f7ce6b7402553116bbe6a2b8b2142bc72c8915`. The implementation follows Care India's candidate table, automatic recruiter assignment, initial-call task and eleven-stage application model.

## Reference and PRD comparison

| Workflow | Reference behavior | Care India behavior |
| --- | --- | --- |
| Entry | Upload resume/identity files or enter available details and preferences | All PRD contact, passport, work-profile and source fields; name and usable phone required by the existing backend |
| Original documents | Resume and identity originals with extraction evidence | Originals saved before reading; resumable document jobs, optional manual entry and versioned candidate attachments |
| Extraction review | Structured document extraction | Resume, passport and Aadhaar suggestions; passport precedence for unedited identity fields; human corrections survive late OCR |
| Duplicate identity | Shared identity matching during intake | Phone, email or passport match presents explicit Merge or Cancel; ambiguous identity conflicts cannot merge |
| Assignment | Reference allocation policy | Care India round-robin across active eligible Staff, excluding approved/manual leave and paused automatic intake |
| First action | Reference candidate workflow | One initial-call task with a 48-hour deadline; no eligible recruiter leaves a saved candidate awaiting supervisor assignment |
| Recruitment progress | Reference-specific statuses and evaluations | One active order and eleven sequential recruitment stages, as required by the PRD |

The reference's optional-identity creation and destination-country allocation rules are not substituted for Care India's existing required identity and PRD assignment rules.

## Gaps corrected

1. **Interrupted save recovery.** Creating or explicitly merging a candidate now records the document intake's saved candidate ID in the same database transaction. A lost HTTP response can be recovered by reopening the intake; retrying returns the existing candidate without a second task, assignment or merge audit. This uses existing document-job draft storage and requires no schema migration.
2. **Queued draft protection.** A delayed draft backup cannot clear or replace the saved candidate binding. Legacy draft bindings require an existing candidate and creator, assigned-recruiter or supervisor access. A group cannot move to another candidate.
3. **Clear saved-intake continuation.** Restored intakes with a saved candidate always show **Finish saved intake** and **Open saved profile**. Previously the form could be disabled without either continuation action.
4. **Separate candidate drafts.** Opening another intake restores only that intake's saved details and edited-field markers. A confirmation explains whether the current entry will be backed up or its document-free unsaved form replaced. Different candidates' human edits are no longer combined.
5. **Frontend/backend validation.** Entry and correction screens show inline errors for usable phone, future birth date, passport date ordering, whole-number experience limits, Gulf experience exceeding total experience and salary limits. An unassigned candidate can be corrected but cannot be marked reviewed until assigned.
6. **Saved-profile next action.** The profile fetches current records on entry, provides retry on load failure, shows the real initial-call deadline, links to the permitted task, and offers supervisor assignment/reassignment using the existing availability-aware workflow. Its next-step guidance follows saved ownership and application state.

## Preserved behavior

Merging fills empty fields and retains existing human values, original ownership, completed work and SLA deadlines. OCR is never required for manual save. Failed reading leaves the original available. Attachment retries keep successful versions, and originals remain downloadable from the candidate profile. Staff can view all candidate profiles but can modify only their own assigned candidates; their newly created intake documents may still be attached after automatic assignment to another recruiter.

Files saved before document reading remain under **Original document bundles**. Resume/passport/Aadhaar slots are populated only with extracted pages or direct uploads to those slots. The profile explains this and points empty identity slots to the originals, so a saved unclassified file is not presented as lost.

## Verification

- 34 existing backend candidate-workflow and manual-document-extraction tests passed against a disposable loopback database.
- Six additional backend regression tests passed for transaction binding, lost-response replay, stale backup protection, draft isolation, access control and conservative merge recovery.
- 13 targeted frontend entry/OCR tests passed, including validation and intake restoration isolation.
- Live browser acceptance, responsive layout inspection and the final integrated frontend build are recorded in the parent workflow verification artifacts under `artifacts/workflow-followup/`.

No production database, real mailbox or OCR provider was used for these tests. No push or deployment is part of this change.
