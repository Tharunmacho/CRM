# Recruitment reports and exports

Reports explain recruitment progress, staff performance, intake sources, order fulfilment, closure reasons and advance collections. Their dates and ownership rules must be understood before comparing totals with today's operational screens. Source: PRD pages 2, 5 and 6. Requirements: **REPORT-01, REPORT-02, REPORT-03**.

## Roles and preparation

Admin and Manager can report across staff and filter to an employee. Staff can report only their own numbers; the server enforces that restriction for both the screen and exports. The six reports use saved candidates, applications, tasks, effective stage results and recorded workflow events. They do not derive totals by counting whichever rows happen to be loaded in Data Bank.

Before reviewing a result, choose the business question and reporting period. Dates are inclusive in the agency timezone. A candidate's intake date, application mapping date and task completion date can be different, so the reports do not all count the same population.

## User journey

1. Open **Reports**. A normal visit begins with Pipeline and a recent 90-day date range. Opening performance from Staff Directory carries the chosen employee and date range into Staff performance.
2. Set **From date** and **To date**, then choose Client, Order, Designation and, for supervisors, Staff. Filters apply to the whole reporting result and its exports. An order/client/designation filter restricts candidates and tasks to related matching orders; an unmapped intake may therefore disappear from that scope.
3. Read the summary cards: applications mapped in the selected period, the current active/deployed subset of those applications and candidates added in the period. These are not interchangeable with all-time totals or today's workload.
4. Select the required report tab and use its specific definition below. Review **Scope and calculation notes**, including the agency timezone, update time and historical limitations, before judging an employee or making a financial comparison.
5. Where a row supports navigation, open an order from Order status or a candidate from Advance collections to inspect the source record. Other tables are summaries rather than automatic links to every underlying record.
6. Choose **Excel** or **PDF** to export the currently selected report using the same authorized filters. The button shows preparation while the server generates the file. Open the downloaded file and check its period, scope and totals against the screen.
7. Use **Refresh** after changing source records or when a result is unavailable. Changing scope clears the previous scope's visible result while the new one loads; a failed refresh in the same scope can show the last loaded result with an explicit warning.

## What each report means

| Report tab | What the owner reads | Date and calculation rule |
| --- | --- | --- |
| Pipeline | Reached, Active/waiting, Progressed, Closed here and closure rate for all eleven stages | Starts with applications mapped in the chosen period. Waiting is shown separately; it is not counted as rejection or drop-off. An application can appear as having reached several stages. |
| Staff performance | Initial assignments, tasks completed, average stage hours, duration samples, escalations and missed daily updates; a second table shows average time per stage | Assignments use candidate intake/initial ownership. Completions, valid stage execution samples and stored events use their own event dates and recorded attribution. Missing duration samples are not zero-hour completions. |
| Sources | Candidate counts by Email, WhatsApp, Walk-in, Referral and Other, plus email-account breakdown | Uses candidates first added in the period and their saved source. Later duplicate documents do not become new candidates or new initial-source counts. |
| Order status | Order, client, company, designation, status, openings, matched, selected and deployed | Matched/selected/deployed counts describe the period's mapped applications, compared with the order's total openings. Selected includes a candidate who was selected and later deployed or closed after selection. |
| Rejections | Counts by stage and recorded closure reason | Uses applications closed in the period. Administrative **Unmapped** closures are identified explicitly, so they can be distinguished from prescreen failure, interview rejection or medical unfitness. |
| Advance collections | Effective stage 8 amounts per candidate/order, totals per currency and monthly currency totals | Uses effective collection records completed in the period. Reopened/invalidated stage results are excluded. Different currencies are never added into a single total; older missing currency remains **UNSPECIFIED**. |

For example, a candidate added in September, mapped in October and completing a task in November can appear in September Sources, October Pipeline and November task-completion performance. That is the intended event-based meaning, not three conflicting copies of the candidate.

Staff Directory's average task-completion time and Reports' average stage hours are different measures. The former can include initial-call tasks; the latter requires a corresponding valid stage execution sample. Historical records before ownership/event tracking began may use available current ownership or omit unavailable events. The report notes disclose this limit instead of inventing old history.

## Screen and backend contract

The server validates date ordering and authorizes staff scope before reading reporting data. It selects reporting fields from a consistent committed database snapshot; resumes, passwords and email bodies are not part of the report aggregate. Reopened stage records do not inflate current effective outcomes.

The screen has loading and error states, zero-result tables and **No collections in this period** when applicable. It refreshes visible reports periodically. A missing average is displayed as unavailable, not as a false numeric zero. Export is disabled until a result exists. An export failure appears on screen and does not imply a successful file download.

Excel is an actual XLSX workbook and PDF is generated as a paginated report, both from the same authorized calculation path. A Staff user cannot bypass their own-number restriction by changing a URL or export request. Exports are newly calculated at request time; if another user changes data between the screen load and export, refresh the screen before comparing that difference.

## Recovery and handoffs

Correct an invalid date range and refresh. If the network fails, retain the chosen filters, retry the report and verify its update time. If an export fails, retry that format after the report is available; there is no candidate or workflow mutation to undo. To change a reported result, correct the authorized source workflow rather than editing report totals.

Review workload in [Staff Directory](11-staff-assignment-directory.md), investigate event details in [Tasks](16-tasks-sla-daily-updates.md), and open the relevant candidate/order for recruitment evidence. Reports summarize those records and do not approve leave, reset task deadlines or change payments.

## Acceptance checklist

- [ ] Review all six tabs with known candidates, applications, stage results and workflow events.
- [ ] Use one candidate whose intake, mapping and completion occur in different periods; confirm the intended report attribution.
- [ ] Test inclusive agency-date boundaries and an invalid reversed date range.
- [ ] Apply client, order, designation and staff filters individually and together; compare screen/export scope.
- [ ] Confirm waiting applications are not reported as rejected and Unmapped appears as an explicit closure reason.
- [ ] Reopen a completed stage and verify invalidated results are excluded from effective stage metrics/collections.
- [ ] Enter two currencies and a legacy unspecified currency; verify separate totals and no guessed conversion.
- [ ] Compare XLSX and PDF content with the corresponding report, including multiple pages and zero results.
- [ ] Sign in as Staff and reject another employee's report/export scope.

## Current implementation and remaining acceptance

Report refresh controls expose the pending read and prevent repeated clicks. Returning to the visible/focused report reloads its scoped result. Export requests have a 90-second deadline, with a clear shorter-period/retry message instead of an indefinite pending state. Export amounts and dates still come from the authorized calculation path.

All six reports, common filters, role enforcement and XLSX/PDF exports are implemented with isolated calculation and file-content tests. Local source coverage is not a claim that production historical data is complete. The owner should reconcile representative production records after deployment, particularly pre-tracking ownership, missing historic events and unspecified payment currencies. Broader finance features such as currency conversion or accounting reconciliation are not part of these reports.

Implementation: [Reports screen](../../frontend/src/screens/reports/reports.tsx), [report API](../../backend/app/api/routers/reports.py), [report calculations](../../backend/app/services/reporting.py), [screen loading and export client](../../frontend/src/features/reports/report-api.ts).
