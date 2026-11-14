# Data Bank and skill segregation

Data Bank is the shared directory of every candidate. Skill segregation means finding people by skills actually recorded on their profiles, including identifying profiles with no skills recorded. Source: PRD pages 4–5. Requirements: **BANK-01, BANK-02**; the visible skill-group workflow is a later authorized enhancement.

## Roles and preparation

All three roles can read the whole directory. Admin alone can export the Data Bank to Excel. Admin and Manager can select candidates for reassignment. Prepare candidates with several skills, no skills, different owners, sources and application statuses to review the filters meaningfully.

## User journey

1. Open **Data Bank**. The default **New candidates first** order shows New, then In process, then the remaining statuses; each group has its newest candidates first. Review the candidate summary and choose Everyone, Not mapped or Not interested.
2. For supervisor review, select an employee. Candidate results and skill group counts now share that owner scope.
3. In **Browse candidates by skill**, select one or several recorded skills. Use the complete skill dropdown when the desired skill is not among the common groups.
4. Choose **All selected skills** when every selected capability must be recorded, or **Any selected skill** when one is sufficient. “TIG welding” and “TIG welding inspection” remain distinct.
5. Select **need skills recorded** to review profiles with an empty skill list. This means information is missing, not that the candidate has no ability.
6. Search by name/contact/passport/skills or resume text. Use column filters and sorting for designation, experience, passport availability, source, owner, mapped order, stage, status and date added.
7. Open a row to inspect the profile. An unmatched candidate has profile actions; application links open the relevant recruitment workflow.
8. Admin uses **Export to Excel** to download the same filtered population. A supervisor can select visible candidates and use **Reassign**, with the [assignment rules](11-staff-assignment-directory.md).

## Screen and backend contract

Results, pagination and skill groups come from the backend. A candidate counts once in each normalized skill group even if legacy data repeats that skill. A person can belong to multiple groups, so skill counts should not be summed as unique people. Group counts describe the selected pool/staff scope, not every optional text/column filter.

The default `sort=new_first` applies status priority and creation time to the complete result before pagination, with candidate ID as the final tie-break. **Newest first**, **Oldest first**, **Name A–Z**, **Name Z–A** and the other explicit sort choices remain available and replace this default grouping.

The server uses exact normalized skills for ALL/ANY and full-text resume search separately. It does not guess industry from a title such as Engineer. Empty and whitespace-only skill arrays enter the missing-skills filter. Excel applies the same candidate predicates and protects formula-like text.

## Recovery and handoffs

Loading must not show confirmed zero results. Search failures preserve filter input and show retry/error feedback; failed skill-group refresh hides unconfirmed counts. Changing filter/page scope clears hidden selections. Reopening from Recruiter Directory refreshes current ownership. Correct missing skills through the authorized profile editor, then refresh the directory.

## Acceptance checklist

- [ ] A skill group's count equals its filtered candidate population across pages.
- [ ] Older New candidates precede newer In process/Deployed candidates across page boundaries; choosing Newest first restores creation-date order across statuses.
- [ ] Test ALL versus ANY, fullwidth/case/spacing variants and distinct substring skills.
- [ ] Missing-skills results include empty and whitespace-only legacy values.
- [ ] Recruiters can read all candidates but cannot export or reassign.
- [ ] The Excel workbook contains the same filtered records, including owner scope.
- [ ] A reassignment by another session changes counts and rows after refresh.

## Current implementation and remaining acceptance

Server paging, filtering, export, fresh skill groups and missing coverage are implemented. Route entry checks current directory data while reusing a recently verified workspace; explicit refresh still fetches current records. Resume search remains a backend operation even when the workspace summary omits full extracted text; opening the profile loads that evidence separately. The latest skill tests and real local browser flows cover exact evidence and result counts. Large-volume query performance remains a separate acceptance exercise.

Implementation: [Data Bank](../../frontend/src/screens/candidates/data-bank.tsx), [search service](../../backend/app/services/candidate_search.py), [skill comparison](../audit/candidate-skills-followup.md). Next: [order matching](13-order-matching.md).

## All resumes: one table, every field, one expandable row (13E)

**All resumes** is the same kind of table as the Lead sheet: one table that always fits its box, a **Columns** menu and an arrow at the start of every row.

- **One field registry.** [data-bank-fields.ts](../../frontend/src/features/candidates/data-bank-fields.ts) lists every candidate field the list API returns (42 fields in six groups: Person, Contact, Passport, Job & experience, Screening, Workflow) with its heading, group, minimum width and how the value is written. The optional table columns, the Columns menu and the expandable row all read it, so a heading cannot differ between them. A test compares the registry with the API's `CandidateOut` fields: a new API field fails the test until it has a registry entry.
- **Columns menu.** The button beside the sort and view controls lists the columns by group with a search box, **Show all**, **Default** and **Fit to screen**, plus Roomy/Compact spacing and the opt-in **Wide table**. Fields that already have a column of their own (name over date of birth, phone with WhatsApp over e-mail, stage over order, agency over source) are listed once under that column; the rest start switched off. The table never scrolls sideways unless Wide table is chosen: when the chosen columns do not fit, the least important ones are hidden and a note says how many (checked at 1534 and 1440 CSS px).
- **Expandable row.** The arrow opens a grid of every field with its bold heading; an empty answer shows a dash so two candidates can be compared line by line. The row's actions stay inside it: **Open**, **Application form**, **Assign** (supervisors) and **Edit**. The double-arrow button beside Columns expands or collapses every row on the page.
- **Education and work history** are long and are deferred JSONB columns the list never loads (a test checks the list statement does not select them). The expanded row shows them as **Open the profile to read**, a link to the profile.
- Behaviour that did not change: By job groups, search, filters, selection and Reassign, Export Excel, the shared pager, the cards view on narrow screens.

Implementation: [field registry](../../frontend/src/features/candidates/data-bank-fields.ts), [expanded row](../../frontend/src/features/candidates/components/data-bank-expanded.tsx), [columns](../../frontend/src/features/candidates/components/data-bank-columns.tsx). Tests: `frontend/tests/data-bank-fields.test.mjs`, `backend/tests/test_data_bank_list_fields.py`.
