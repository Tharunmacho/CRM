# Dashboard

Dashboard answers what arrived today, what needs action and where candidates are in recruitment. It is an entry point into the underlying records, not a separate copy of their status. Source: PRD pages 2–3. Requirements: **DASH-01, DASH-02, DASH-03**.

## Roles and preparation

Admin sees team totals and can select a staff member. Manager and Staff see their own dashboard numbers. The personal task and to-do sections remain personal even when Admin selects a team scope. Admin alone sees connected-source health. Create representative candidates, tasks and applications before accepting the figures; a database containing only zeros cannot demonstrate drill-down correctness.

## User journey

1. Sign in and review today's candidate intake, separated by email and WhatsApp, with other manual sources distinguished.
2. Review open, due-today, overdue, escalated and not-updated-today work. Select a card to open the corresponding task queue.
3. Select one of the eleven pipeline stages to see the active applications at that stage. Open a candidate/application from that result.
4. Review open orders. Compare openings, currently matched candidates, selected candidates and deployed candidates. Open an order to inspect its actual applications.
5. Work the personal task list in due-time order. An overdue item stays visibly overdue until the relevant workflow action closes it.
6. Admin reviews source health: last sync/receipt, worker activity and queued, failed or review-needed files. Open the source or recovery list to resolve a failure.
7. Admin and Manager can open their private to-dos from the dashboard without mixing them with recruitment tasks.

## Screen and backend contract

Each count must carry the same date and staff scope into its destination. “Today” uses the configured agency timezone. Source intake is based on saved candidate provenance; mailbox receipt count is not the candidate count. Pipeline figures count active applications, not closed or deployed history.

Overdue uses the stored due time; escalated uses persisted escalation state; missing daily updates use a current-owner remark for the agency day. A supervisor's remark does not satisfy the owner's obligation. Order selected/deployed figures must use actual stage/application results rather than the reference CRM's submitted-candidate count.

Loading must not appear as confirmed zero. Refresh failures should be visible. The health card must distinguish disconnected sources, no recent receipt, queued work and failed extraction, rather than equating every quiet source with a failure.

## Recovery and handoffs

A stale task can close between opening a card and opening the record; show the completed/cancelled context. A source failure leads to [intake recovery](10-automated-intake.md), not an automatic duplicate import. Task actions follow [Tasks and SLA](16-tasks-sla-daily-updates.md); order actions follow [matching](13-order-matching.md).

## Acceptance checklist

- [ ] Compare cards and destination rows for Admin team scope, one staff filter and Staff self scope.
- [ ] Test records just before and after agency midnight.
- [ ] Verify all eleven stage cards and an order with matched, selected and deployed candidates.
- [ ] Complete or reassign a task and refresh; every related number agrees.
- [ ] Stop a test worker and verify source health points to the correct recovery action.
- [ ] Confirm another person's private to-dos never appear in team scope.

## Current implementation and remaining acceptance

The dashboard uses a dedicated operational database projection instead of computing all six historical reports on every refresh. It retains the same scoped counts and selected-stage history rules. Refresh has a pending state; the exported scope comes from the server result. Tables use indexed workspace relationships rather than repeating full collection scans for each cell.

Server metrics, stage/task drill-downs and minimal source-health endpoints are implemented with isolated scope/date tests. Real worker health, actual provider traffic and deployed timing still require acceptance.

Implementation: [Dashboard screen](../../frontend/src/screens/dashboard/dashboard.tsx), [reporting service](../../backend/app/services/reporting.py). Related guide: [Reports](17-reports.md).
