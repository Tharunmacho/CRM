# Screen-by-screen integration order

Use this sequence after reviewing the [architecture](architecture.md) and setting up the [database](database-design.md). Each row is a vertical integration slice: the screen, validated API, persistence, permissions, audit and meaningful verification. The detailed [phase plan](implementation-phases.md) remains the delivery contract. Existing connected actions should be verified and completed, rather than rebuilt.

The 16 existing page files are already organized under `frontend/src/features/`. A page can be visible before its full integration is accepted; dashboard/report correctness is validated after their underlying events exist.

Physical database names below target revision `0006_domain_table_names`; frontend feature names and API paths are unchanged. See the [table dictionary](database-design.md#table-dictionary) for the reversible old/new mapping and verify the database revision before integration.

| Order | Screen / frontend feature | Backend and database dependencies | Acceptance focus | Phase |
| --- | --- | --- | --- | --- |
| 1 | Login — `auth` | Auth API; `auth_users`, `auth_sessions`; safe provisioning | Login/logout/refresh, disabled users, three roles and server checks | 1 |
| 2 | Clients — `clients` | Employer/client service and reconciled identity model | Required fields, create/edit/status, validation and audit | 2 |
| 3 | Companies — `companies` | Existing `crm_employer_companies` table and contact hierarchy | Keep existing data; decide nested employer view/navigation from PRD before extending | 2 |
| 4 | Client detail — `clients` | Client/company/order relationship queries | Orders and mapped candidates resolve to the correct employer | 2 |
| 5 | Jobs — `jobs` | Designations and orders | Fields, openings, status, dates, currency and employer link round-trip | 2 |
| 6 | Add candidate — `candidates` | Candidate service, duplicate handling, assignment, documents, OCR adapter | Manual entry first; extraction review; merge/cancel; owner and first task | 2; automated intake in 5 |
| 7 | Data Bank — `candidates` | Bounded candidate search, owner changes, identity rules | Search all columns/resume text, Not interested pool, reassignment and access | 2; Excel export in 7 |
| 8 | Candidate profile — `candidates` | Profile, documents, application history and initial call | Corrected fields and OCR evidence; file versions; complete call outcomes | 2–3 |
| 9 | Order detail — `jobs` | Order query, accepted candidate search and mapping commands | Matching filters, one active mapping, accurate progress and safe unmap | 2–3 |
| 10 | Mapped candidates and stage modal — `workflow` | Application/stage/task services | All 11 stages, four exits, medical retries, unmap/remap and Admin reopening | 3 |
| 11 | Tasks — `tasks` | Task/remark API, scheduler and notifications | System-controlled completion, owner remarks, 48-hour SLA and escalation | 3–4 |
| 12 | Inbox — `inbox` | Mail/WhatsApp adapters, workers, OCR, storage and duplicate service | Multi-account inbox/replies, automatic/manual intake, retries and review | 5 |
| 13 | User management — `users` | Admin API, sessions, assignment and audit | Add/edit/deactivate, leave/eligibility, reset/logout and real audit events | 6, on Phase 1 policies |
| 14 | To-do list — `todos` | Private to-dos and reminder delivery | Admin/Manager ownership, due dates, links, checkbox completion and bell reminder | 6 |
| 15 | Dashboard — `dashboard` | Scoped aggregate queries and actual workflow/intake events | Role scope, stage drill-down, task/order metrics, source health and private to-dos | 7 |
| 16 | Reports — `reports` | Shared filters and server-side report/export queries | Six report families, role scope, dates, currency groups, valid Excel/PDF files | 7 |

Early development uses controlled test accounts after the safe identity foundation. Full user-management UI delivery stays in Phase 6; automatic assignment still requires eligible Staff records during candidate integration.

For every screen, verify loading/empty/error states, successful save after refresh, invalid input, role/owner restrictions and relevant concurrent edits. Finish the DB/API contract before wiring more components to the global store. Mark the corresponding [requirements matrix](requirements-matrix.md) rows accepted only when their complete behaviour is demonstrated.

After these slices, Phase 8 validates the complete journeys and deployment/restore procedures. A screen build or mocked response alone does not satisfy that release gate.
