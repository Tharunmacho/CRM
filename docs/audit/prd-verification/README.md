# PRD coverage verification (25 Oct 2026)

Six independent checks of the Developer Specification (`artifacts/requirements-review.txt`) against the code **and** the running website
(headless Chrome, as Admin, Manager and Staff). Each file lists every requirement with its status, where it lives on screen, the evidence
(file:line, test name, screenshot) and any gap.

| File | PRD pages | Subject |
| --- | --- | --- |
| prd-verify-A.md | 1-3 | Roles, navigation, dashboard, Inbox/WhatsApp, Clients, Jobs/Orders, matching |
| prd-verify-B.md | 4-5 | Add Candidate, duplicate check, Data Bank, Tasks, To-do |
| prd-verify-C.md | 6-7 | Reports, User Management, background intake, assignment, notifications |
| prd-verify-D.md | 8-9 | Stage modal, stages 1-5, negative exits, reopen, file versions |
| prd-verify-E.md | 10-11 | Stages 6-11, deployment, 48-hour clock, escalation |
| prd-verify-F.md | 12 + whole product | Daily updates, role matrix, audit log, lifecycle walk |

Status words: COVERED (code and website verified), COVERED-CODE-ONLY (the fake test API cannot prove it; code and tests named),
PARTIAL, NOT COVERED, DIFFERS-BY-DECISION (a choice the product owner made).
