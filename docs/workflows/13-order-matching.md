# Matching unmapping and remapping candidates

Matching creates a candidate × order application and unlocks stage 1. An unmatched person has a profile, not a standalone stage modal. Source: PRD page 4. Requirements: **MAP-01, MAP-02, MAP-03**.

## Roles and preparation

All three roles can map/unmap under the application rules. Staff stage-edit permission still follows ownership. Prepare an open order and a candidate with an active owner, no active application and no deployed placement. Confirm interest before matching as the operating practice described in [initial call](12-initial-call.md).

## User journey

1. Open the order and choose **Match candidates**. Review **What this order needs**: saved designation, required skills and minimum experience.
2. Search current backend candidates by designation, experience, exact skills, current location, passport number availability and assigned staff. Resume text can also be searched.
3. Select **Use designation requirements** to apply all saved requirements, or deliberately adjust/clear the filters. ALL and ANY skill matching have different meanings.
4. Inspect each result's required skills recorded, requirements not recorded, full profile skills and experience shortfall. Open **View profile** in a new tab without losing the drawer's filters.
5. Select visible candidates and choose **Match … and start stage 1**. The result creates applications and stage tasks; it does not merely mark a shortlist.
6. Open an application from the order's candidate table. Inspect current stage, owner and last update.
7. To move a candidate, choose **Unmap** on the active application and confirm. The application closes as Unmapped, its open tasks are cancelled and history remains.
8. Match the candidate to the new order. A new application starts at stage 1; completed work from the old application is historical, not copied as new completion.

## Screen and backend contract

The matching drawer uses a bounded database query, not the browser's bootstrap collection. Skill evidence is exact saved evidence, without scores, invented experience or industry aliases. Missing skills mean not recorded. Filters/page changes clear selection so hidden candidates cannot be mapped accidentally.

From a candidate profile, **Map to an order** first checks current candidate/order state. Each open order shows its designation match separately from exact required-skill matches and experience. A title match is never labelled a complete profile match. Changing the search clears the previous selection; the selected employer and order reference remain visible before confirmation. An unassigned/inactive owner explains the assignment prerequisite. Skill or experience shortfalls remain review evidence, not additional backend eligibility restrictions.

Saving rechecks the locked order and candidate. Only one active application is allowed. Mapping creates the application, stage-1 task and audit together, and supersedes any outstanding initial-call task without inventing call data. Closed/on-hold/filled orders cannot accept stale matching attempts. A candidate needs an active owner; approved leave does not automatically rewrite existing ownership.

Unmap/complete/reopen use consistent locks and expected versions. An old application cannot reopen over a newer active/deployed placement. A batch can have separate successful and failed candidate results; the UI reports both rather than pretending the whole batch succeeded.

## Recovery and handoffs

Retry failed search explicitly. If mapping committed but directory refresh failed, reload results rather than map the same person again. On a concurrent conflict, inspect current application state. The next workflow is [stages 1–5](14-stages-one-to-five.md), then [stages 6–11](15-stages-six-to-eleven.md).

## Acceptance checklist

- [ ] Verify each filter and ALL/ANY combination against actual database results beyond the first page.
- [ ] Compare matched/missing skill evidence with the saved designation/profile.
- [ ] Race two orders for one candidate; only one active application is created.
- [ ] Change an order to On hold while the drawer is open; the save fails safely.
- [ ] Unmap with completed stages and open tasks; history remains and open work cancels.
- [ ] Remap to another order and confirm a fresh stage-1 application.
- [ ] Test partial batch failure and committed-save/failed-refresh recovery.
- [ ] From one candidate, search after selecting an order; verify the hidden selection cannot be submitted.
- [ ] Use a candidate with a matching title but missing skills/experience; show the shortfall without claiming suitability.

## Current implementation and remaining acceptance

Backend matching, visible evidence, bounded paging and transactional map/unmap are implemented with isolated tests and local browser coverage. Older audit text referring to bootstrap matching is superseded by the [skill follow-up](../audit/candidate-skills-followup.md). Large-data performance remains a separate benchmark.

Implementation: [matching drawer](../../frontend/src/features/jobs/components/candidate-match-drawer.tsx), [matching service](../../backend/app/services/candidate_matching.py), [application API](../../backend/app/api/routers/applications.py).
