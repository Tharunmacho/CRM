# Designations and job orders

A designation describes a type of job. An order records a particular employer's openings and terms for that designation. Source: PRD page 3. Requirement: **JOB-01**.

## Roles and preparation

All three roles can create designations and orders. Prepare an active employer and its client contact first. Required skills must describe actual capabilities; the system does not infer an industry or collapse different trades into aliases.

## User journey

1. Open **Jobs / Positions** and create a designation, such as Welder. Enter title, category, description, required skills and minimum experience.
2. Save and reopen the designation to check that duplicate case/spacing variants of a skill have been normalized without losing distinct skills.
3. Create an order for the designation. Select the employer and record openings, work country/city, salary and currency, benefits, contract period, interview date and deadline.
4. Saving a new order opens its detail. Confirm the employer, designation, terms and counts before matching anyone. **Candidate requirements** shows the saved skills and minimum experience alongside the commercial terms; the back link returns to that employer's hiring workspace.
5. Use **Match candidates** to move into the [matching workflow](13-order-matching.md). Each successful match creates an application rather than merely adding a name to a shortlist.
6. Change an order to **On hold** when new matching should pause. Reopen it when appropriate. Existing application history remains visible.
7. Review selected and deployed counts separately. The final ticket stage determines deployment and filled status; a selection or submission does not fill an opening.
8. Close or delete only when the server permits it. A designation or employer with linked history cannot be casually removed.

## Screen and backend contract

Designation editing must expose the saved skills and minimum experience used by matching. Order forms validate numeric openings/amounts and supplied dates, maintain explicit currency and require valid active parents. Editing must preserve the order reference and application relationships.

The order page shows openings, active matched applications, selected candidates, deployed candidates and closed history. **Filled** depends on deployments against openings. Closing requires resolution of active applications. Lowering capacity cannot bypass existing deployment constraints. Concurrent final-stage completions recheck capacity under the order lock.

## Recovery and handoffs

Invalid amounts, dates or parent choices stay visible for correction. If an order changes to On hold while a matching drawer is open, the save must reject the stale action. A saved edit followed by a failed refresh should reload the order rather than create it again. Unmapping and remapping are covered separately; they are not an order-delete workaround.

## Acceptance checklist

- [ ] Create and edit a designation with several distinct skills and an experience threshold.
- [ ] Create an order with all employment terms and reopen it without data loss.
- [ ] Hold the order and verify a stale matching attempt fails clearly.
- [ ] Confirm selected candidates do not increment deployed count.
- [ ] Complete two final stages competing for the last opening; capacity cannot be exceeded.
- [ ] Attempt parent/designation deletion with linked history and verify protection.

## Current implementation and remaining acceptance

Designation/order CRUD, validation, active-parent checks, history protection and deployment rules are implemented and covered by master-data/application tests. The employer filter scopes order counts and lists; creation from that workspace preselects the employer. Missing active employers produces a direct **Manage employers** handoff. Order detail refreshes its context before showing actions and explains why a held/closed/filled order cannot accept new matches. Currency and commercial terms still need owner review with actual agency examples.

Implementation: [Jobs screen](../../frontend/src/screens/jobs/jobs.tsx), [order detail](../../frontend/src/screens/jobs/order-detail.tsx), [job dialogs](../../frontend/src/features/jobs/components/job-dialogs.tsx), [application API](../../backend/app/api/routers/applications.py).
