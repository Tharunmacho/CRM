# Data Bank and skill segregation

Data Bank is the shared directory of every candidate. Skill segregation means finding people by skills actually recorded on their profiles, including identifying profiles with no skills recorded. Source: PRD pages 4–5. Requirements: **BANK-01, BANK-02**; the visible skill-group workflow is a later authorized enhancement.

## Roles and preparation

All three roles can read the whole directory. Admin alone can export the Data Bank to Excel. Admin and Manager can select candidates for reassignment. Prepare candidates with several skills, no skills, different owners, sources and application statuses to review the filters meaningfully.

## User journey

1. Open **Data Bank**. Review the candidate summary and choose Everyone, Not mapped or Not interested.
2. For supervisor review, select an employee. Candidate results and skill group counts now share that owner scope.
3. In **Browse candidates by skill**, select one or several recorded skills. Use the complete skill dropdown when the desired skill is not among the common groups.
4. Choose **All selected skills** when every selected capability must be recorded, or **Any selected skill** when one is sufficient. “TIG welding” and “TIG welding inspection” remain distinct.
5. Select **need skills recorded** to review profiles with an empty skill list. This means information is missing, not that the candidate has no ability.
6. Search by name/contact/passport/skills or resume text. Use column filters and sorting for designation, experience, passport availability, source, owner, mapped order, stage, status and date added.
7. Open a row to inspect the profile. An unmatched candidate has profile actions; application links open the relevant recruitment workflow.
8. Admin uses **Export to Excel** to download the same filtered population. A supervisor can select visible candidates and use **Reassign**, with the [assignment rules](11-staff-assignment-directory.md).

## Screen and backend contract

Results, pagination and skill groups come from the backend. A candidate counts once in each normalized skill group even if legacy data repeats that skill. A person can belong to multiple groups, so skill counts should not be summed as unique people. Group counts describe the selected pool/staff scope, not every optional text/column filter.

The server uses exact normalized skills for ALL/ANY and full-text resume search separately. It does not guess industry from a title such as Engineer. Empty and whitespace-only skill arrays enter the missing-skills filter. Excel applies the same candidate predicates and protects formula-like text.

## Recovery and handoffs

Loading must not show confirmed zero results. Search failures preserve filter input and show retry/error feedback; failed skill-group refresh hides unconfirmed counts. Changing filter/page scope clears hidden selections. Reopening from Staff Directory refreshes current ownership. Correct missing skills through the authorized profile editor, then refresh the directory.

## Acceptance checklist

- [ ] A skill group's count equals its filtered candidate population across pages.
- [ ] Test ALL versus ANY, fullwidth/case/spacing variants and distinct substring skills.
- [ ] Missing-skills results include empty and whitespace-only legacy values.
- [ ] Staff can read all candidates but cannot export or reassign.
- [ ] The Excel workbook contains the same filtered records, including owner scope.
- [ ] A reassignment by another session changes counts and rows after refresh.

## Current implementation and remaining acceptance

Server paging, filtering, export, fresh skill groups and missing coverage are implemented. Route entry checks current directory data while reusing a recently verified workspace; explicit refresh still fetches current records. Resume search remains a backend operation even when the workspace summary omits full extracted text; opening the profile loads that evidence separately. The latest skill tests and real local browser flows cover exact evidence and result counts. Large-volume query performance remains a separate acceptance exercise.

Implementation: [Data Bank](../../frontend/src/screens/candidates/data-bank.tsx), [search service](../../backend/app/services/candidate_search.py), [skill comparison](../audit/candidate-skills-followup.md). Next: [order matching](13-order-matching.md).
