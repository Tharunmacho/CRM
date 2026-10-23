# Candidate skills and matching — 22 October 2026

Compared the original recruitment specification, pages 3–5 (`docs/requirements/recruitment-crm-developer-specification.pdf`), with the reference repository at `30f7ce6b7402553116bbe6a2b8b2142bc72c8915` and the current Care India implementation. The reference was reviewed from its local checkout; its application and external providers were not run.

## Findings and decisions

| Workflow | Reference | Care India integration |
| --- | --- | --- |
| Skill grouping | `CandidatesView.tsx` infers broad industry groups from substrings in skills/designation; for example, an `engineer` designation falls into IT. | Group by the actual saved skill. A candidate may appear in several groups. Unicode compatibility, spacing and case variants count together; distinct trades remain distinct. No inferred industry is written to a candidate. |
| Skill coverage | Profile skills exist, but inferred `General` groups obscure missing evidence. | Data Bank shows how many candidates have recorded skills and a direct **need skills recorded** filter. Blank and whitespace-only legacy skill arrays are included in that filter. |
| Group counts | Reference filters and Care India's previous facet counts were computed over browser-loaded records. | `GET /api/candidates/skill-facets` aggregates the full current database pool. Each person contributes once per normalized skill. Staff and candidate-pool filters apply to both the group counts and the directory. |
| Finding candidates for an order | Reference `JobOrders.tsx` computes weighted suitability scores and skill aliases in the browser. Care India's previous exact filters also ran over bootstrap candidates. | `GET /api/candidates/matching` searches the database in pages of 20. It supports exact designation, minimum experience, exact skill ALL/ANY, location, passport number, assigned staff and full-text resume search. No score or unverified alias is presented. |
| Explaining a match | Reference shows matched and missing evidence alongside its score. | The matching drawer shows the order's saved requirements, required skills recorded on each profile, requirements not recorded, all profile skills and experience shortfalls. Missing evidence is described as **not recorded**, not proof that the person lacks the skill. |
| Selection and confirmation | Reference submission and order updates are separate writes. | Care India's existing application transaction remains authoritative: it rechecks order status, active owner and active application under row locks, then creates stage 1 and its task together. Partial batch failures remain visible. |

The original PRD requires a shared Data Bank visible to every staff user, Admin-only Excel export, a Not interested pool, resume search, and order matching filters. The integration retains those permissions and the eleven-stage application workflow. It does not adopt the reference's separate submission model or replace a completed recruitment stage with a recruiter score.

## User workflow

1. Open **Data Bank**. **Browse candidates by skill** shows the most common recorded skills and the missing-skills count for the selected pool and staff scope.
2. Click one or several skill groups. Choose **All selected skills** or **Any selected skill**, or use the complete skill dropdown. Existing search, column filters and Admin Excel export use the same backend candidate predicates.
3. Open a candidate to review the documents and correct their skills with the existing authorized profile editor. Returning to Data Bank refreshes ownership and the skill groups from the backend.
4. Open a job order and select **Match candidates**. The drawer loads current candidates from the server, never a truncated local collection.
5. Use **Use designation requirements** to apply the title, minimum experience and required skills, or adjust the filters explicitly. Compare the recorded/missing skill evidence on each row. **View profile** opens a new tab so the current matching filters and selection remain available.
6. Select candidates on the current page and **Match … and start stage 1**. Selection clears when filters or pages change, preventing an invisible candidate from being mapped. The backend still validates every candidate at save time.

Only candidates with no active application, no deployed status and an active owner appear in the matching pool, matching the existing application-save checks. Not interested candidates remain discoverable and may be offered another order. Approved leave does not silently reassign existing candidates; attendance and staff assignment rules remain responsible for ownership changes.

## Implementation and validation

- Backend: `services/candidate_search.py`, `services/candidate_matching.py`, static routes in `api/routers/candidates.py`.
- Frontend: `screens/candidates/data-bank.tsx`, `features/candidates/use-skill-facets.ts`, `features/jobs/components/candidate-match-drawer.tsx`, mounted from `screens/jobs/order-detail.tsx`.
- Explicit loading, empty, error/retry, refresh, closed-order and partial-save states. Stale result rows are hidden when the request scope changes.
- Existing normalized skill writes and protected human corrections remain in place. The database read normalizer now also handles legacy leading/trailing tabs and newlines consistently with the save path.
- No schema change or migration is required.
- PostgreSQL tests run only against an explicitly named loopback `crm_schema_test_skills_followup` database. New coverage checks distinct facet counts, fullwidth/whitespace normalization, missing skills, staff/pool scope, pagination beyond the first 20 records, current ownership, active-application/deployed exclusion, active-owner requirement, all matching filters, full-text resume search and matched/missing evidence. Test results and real-browser acceptance evidence are recorded under `artifacts/workflow-followup/`.

The Supabase PostgreSQL best-practices skill informed the bounded read and aggregate approach. The existing numbered-page contract is retained for consistent Data Bank navigation; this is not a high-volume performance benchmark or a claim that arbitrary deep OFFSET pages have constant cost.
