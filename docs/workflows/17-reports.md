# Recruitment reports and exports

Reports explain recruitment progress, recruiter performance, intake sources, order fulfilment, closure reasons and advance collections. Their dates and ownership rules must be understood before comparing totals with today's operational screens. Source: PRD pages 2, 5 and 6. Requirements: **REPORT-01, REPORT-02, REPORT-03**.

## Roles and preparation

Admin and Manager can report across recruiters and filter to an employee. Recruiters can report only their own numbers; the server enforces that restriction for both the screen and exports. The six reports use saved candidates, applications, tasks, effective stage results and recorded workflow events. They do not derive totals by counting whichever rows happen to be loaded in Data Bank.

Before reviewing a result, choose the business question and reporting period. Dates are inclusive in the agency timezone. A candidate's intake date, application mapping date and task completion date can be different, so the reports do not all count the same population.

## User journey

1. Open **Reports** (the only reports item in the menu). A normal visit begins with Pipeline and a recent 90-day date range. The chosen tab is kept in the address (`/reports?tab=staff`, `source`, `orders`, `rejections`, `advance`; Pipeline is the default), so a link or bookmark opens it. Opening performance from Recruiter Directory carries the chosen employee and date range into Recruiter performance (for an Admin the week report opens on that person's detail).
2. Set **From date** and **To date**, then choose Client, Order, Designation and, for supervisors, Recruiter. Filters apply to every tab except the Admin's Recruiter performance tab (see below, which has its own week picker) and to the exports. An order/client/designation filter restricts candidates and tasks to related matching orders; an unmapped intake may therefore disappear from that scope.
3. On Pipeline, read the summary cards above the funnel: applications mapped in the selected period, the current active/deployed subset of those applications and candidates added in the period. These are not interchangeable with all-time totals or today's workload. The other tabs carry their own totals, so the cards are not repeated there.
4. Select the required report tab and use its specific definition below. Review **Scope and calculation notes**, including the agency timezone, update time and historical limitations, before judging an employee or making a financial comparison.
5. Where a row supports navigation, open an order from Order status or a candidate from Advance collections to inspect the source record. Other tables are summaries rather than automatic links to every underlying record.
6. Choose **Excel** or **PDF** to export the currently selected report using the same authorized filters. The button shows preparation while the server generates the file. Open the downloaded file and check its period, scope and totals against the screen.
7. Use **Refresh** after changing source records or when a result is unavailable. Changing scope clears the previous scope's visible result while the new one loads; a failed refresh in the same scope can show the last loaded result with an explicit warning.

## What each report means

| Report tab | What the owner reads | Date and calculation rule |
| --- | --- | --- |
| Pipeline | Reached, Active/waiting, Progressed, Closed here and closure rate for all eleven stages, then the average hours each stage takes to finish | Starts with applications mapped in the chosen period. Waiting is shown separately; it is not counted as rejection or drop-off. An application can appear as having reached several stages. |
| Recruiter performance | **Admin:** the weekly recruiter report (below). **Manager and Recruiter:** initial assignments, tasks completed, average stage hours, duration samples, escalations and missed daily updates, for the filters on the page | Assignments use candidate intake/initial ownership. Completions, valid stage execution samples and stored events use their own event dates and recorded attribution. Missing duration samples are not zero-hour completions. |
| Sources | Candidate counts by Email, WhatsApp, Walk-in, Referral and Other, plus email-account breakdown | Uses candidates first added in the period and their saved source. Later duplicate documents do not become new candidates or new initial-source counts. |
| Order status | Order, client, company, designation, status, openings, matched, selected and deployed | Matched/selected/deployed counts describe the period's mapped applications, compared with the order's total openings. Selected includes a candidate who was selected and later deployed or closed after selection. |
| Rejections | Counts by stage and recorded closure reason | Uses applications closed in the period. Administrative **Unmapped** closures are identified explicitly, so they can be distinguished from prescreen failure, interview rejection or medical unfitness. |
| Advance collections | Effective stage 8 amounts per candidate/order, totals per currency and monthly currency totals | Uses effective collection records completed in the period. Reopened/invalidated stage results are excluded. Different currencies are never added into a single total; older missing currency remains **UNSPECIFIED**. |

For example, a candidate added in September, mapped in October and completing a task in November can appear in September Sources, October Pipeline and November task-completion performance. That is the intended event-based meaning, not three conflicting copies of the candidate.

Recruiter Directory's average task-completion time and Reports' average stage hours are different measures. The former can include initial-call tasks; the latter requires a corresponding valid stage execution sample. Historical records before ownership/event tracking began may use available current ownership or omit unavailable events. The report notes disclose this limit instead of inventing old history.

## Screen and backend contract

The server validates date ordering and authorizes recruiter scope before reading reporting data. It selects reporting fields from a consistent committed database snapshot; resumes, passwords and email bodies are not part of the report aggregate. Reopened stage records do not inflate current effective outcomes.

The screen has loading and error states, zero-result tables and **No collections in this period** when applicable. It refreshes visible reports periodically. A missing average is displayed as unavailable, not as a false numeric zero. Export is disabled until a result exists. An export failure appears on screen and does not imply a successful file download.

Excel is an actual XLSX workbook and PDF is generated as a paginated report, both from the same authorized calculation path. A Recruiter user cannot bypass their own-number restriction by changing a URL or export request. Exports are newly calculated at request time; if another user changes data between the screen load and export, refresh the screen before comparing that difference.

## Fee collections tab (admin only)

**Fee collections** (service fees, GST, invoiced totals, money received and balances, from `GET /api/invoices/finance-summary`) is the admin's alone, like the invoices behind it (capability `view_invoices`; see [candidate invoices](candidate-invoices.md)). A manager is a supervisor of the other tabs but does not see this one: the tab is not listed, `?tab=collections` opens Pipeline, and the server answers `403 Only the admin can see or change invoices.` The **Advance collections** tab is a different report (stage 8 advance money) and keeps its existing visibility.

## Recruiter performance tab: one place, by role

There is one Reports area. The former **Recruiter Report** menu item and the **Weekly recruiter report** button were removed, because recruiter performance appeared three times. `/staff-report` still works and redirects to `/reports?tab=staff` (a week and a person in its address are kept).

| Role | What the Recruiter performance tab shows | Dates |
| --- | --- | --- |
| Admin (capability `staff_report`) | The weekly recruiter report below | Its own **Period** card (previous/next week, This week, From/To up to 62 days). The Filters card is not shown on this tab, because Client, Order and Job title do not apply to it |
| Manager | The recruiter figures table and board (assigned candidates, tasks finished, average hours per stage, tasks sent to the admin, missed updates). The weekly report is not available: the server returns 403 for it | The page Filters card |
| Recruiter | The same table with only their own row | The page Filters card |

The average hours each stage takes (a team-wide measure, not a recruiter measure) moved from the old recruiter tab to the Pipeline tab so every role keeps it. Candidates-assigned counts remain in the Manager and Recruiter table only; the Admin report shows stage moves and outcomes instead.

## Weekly recruiter report (admin only)

The **Recruiter performance** tab of Reports shows, for a Monday-to-Sunday week or any custom range of up to 62 days, how each recruiter worked: attendance, hours, daily tasks and candidates from prescreening to deployment. It is computed on request from stored rows and follows the live feed, so it changes the moment attendance, tasks or a stage changes. Excel and CSV exports (buttons in the tab header, with a **Live** badge showing the feed is connected) use the same calculation. API: `GET /api/reports/staff-weekly`, `/staff-weekly/{staff_id}` (adds each candidate end to end) and `/staff-weekly/export?format=xlsx|csv`; the server returns 403 to anyone but an admin.

* Hours: check-in to check-out split by agency day. An unclosed session from an earlier day counts no hours and is flagged. Absent: a scheduled working day with no check-in (weekly offs, holidays and approved leave are not absences, same rules as Attendance). Today before a first check-in is "not in yet".
* Daily tasks: from `workflow_daily_work_items` (see `tasks-data-contract.md`): assigned, completed, postponed, overdue and the carried-over backlog as of now (or of the end of a past period). These are history from the days the fixed daily quota was in use: recruiters now work all of their own candidates with no quota (Tasks > Daily work), nothing writes new rows and the leftover open ones are retired, so periods after that change show "No data yet" here and the score leaves the tasks part out. If those tables are missing the section also says "No data yet".
* Stage moves: stage completions in the period (reopened ones excluded), credited to the responsible recruiter; the stage-by-stage matrix and team funnel also show where candidates are waiting now and how many reached each stage.
* Score (0 to 100, always shown with its parts; the tasks part only exists for periods that have daily-task history): tasks 40% (completed / assigned), hours 30% (worked / expected, capped), stage moves 30% (moves / daily stage-move goal x days worked, capped). A part with nothing to measure is dropped and the rest are rescaled. For a person who had Lead sheet leads assigned in the period an optional fourth part, lead follow-up, is added (see **Lead sheet in the recruiter report** below); everyone else keeps exactly these three.
* Questions to ask: no attendance, absent days, left open sessions, fewer than 75% of expected hours, under 60% task completion (3+ tasks), backlog of 3+, and candidates stuck 7+ days in a stage (idle recruiters are marked high).

## Recovery and handoffs

Correct an invalid date range and refresh. If the network fails, retain the chosen filters, retry the report and verify its update time. If an export fails, retry that format after the report is available; there is no candidate or workflow mutation to undo. To change a reported result, correct the authorized source workflow rather than editing report totals.

Review workload in [Recruiter Directory](11-staff-assignment-directory.md), investigate event details in [Tasks](16-tasks-sla-daily-updates.md), and open the relevant candidate/order for recruitment evidence. Reports summarize those records and do not approve leave, reset task deadlines or change payments.

## Acceptance checklist

- [ ] Review all six tabs with known candidates, applications, stage results and workflow events.
- [ ] Confirm the menu has one **Reports** item, `/staff-report` redirects to the Recruiter performance tab, and `?tab=` opens each tab. Confirm an Admin sees the week report and a Manager sees the recruiter table.
- [ ] Use one candidate whose intake, mapping and completion occur in different periods; confirm the intended report attribution.
- [ ] Test inclusive agency-date boundaries and an invalid reversed date range.
- [ ] Apply client, order, designation and recruiter filters individually and together; compare screen/export scope.
- [ ] Confirm waiting applications are not reported as rejected and Unmapped appears as an explicit closure reason.
- [ ] Reopen a completed stage and verify invalidated results are excluded from effective stage metrics/collections.
- [ ] Enter two currencies and a legacy unspecified currency; verify separate totals and no guessed conversion.
- [ ] Compare XLSX and PDF content with the corresponding report, including multiple pages and zero results.
- [ ] Sign in as a Recruiter and reject another employee's report/export scope.

## Current implementation and remaining acceptance

Report refresh controls expose the pending read and prevent repeated clicks. Returning to the visible/focused report reloads its scoped result. Export requests have a 90-second deadline, with a clear shorter-period/retry message instead of an indefinite pending state. Export amounts and dates still come from the authorized calculation path.

All six reports, common filters, role enforcement and XLSX/PDF exports are implemented with isolated calculation and file-content tests. Local source coverage is not a claim that production historical data is complete. The owner should reconcile representative production records after deployment, particularly pre-tracking ownership, missing historic events and unspecified payment currencies. Broader finance features such as currency conversion or accounting reconciliation are not part of these reports.

Implementation: [Reports screen](../../frontend/src/screens/reports/reports.tsx), [weekly recruiter report](../../frontend/src/features/staff-report/staff-weekly-report.tsx), [tab and redirect rules](../../frontend/src/features/reports/report-tabs.ts), [report API](../../backend/app/api/routers/reports.py), [report calculations](../../backend/app/services/reporting.py), [screen loading and export client](../../frontend/src/features/reports/report-api.ts).

## Lead sheet in the recruiter report

The Recruiter performance tab (admin only) now shows the Lead sheet work of every recruiter for the selected period. The numbers come from one extra aggregate query over `crm_leads` and `crm_lead_activities` (`app/services/lead_metrics.py`), never one query per person, and the report is cached with the `leads` topic so a call, assignment or conversion refreshes it at once.

| Number | Meaning |
| --- | --- |
| Assigned | leads given to the person in the period (`assigned_at`) |
| Assigned leads called | of those, leads called at least once in the period |
| Calls logged / Leads worked | calls the person logged in the period / distinct leads those calls were on |
| Interested | distinct leads the person marked interested (call outcome or status change) in the period |
| Resumes received | leads of the person that got a resume in the period |
| Converted | leads of the person converted to Data Bank candidates in the period |
| Conversion % | converted / leads worked |
| Follow-ups overdue | open leads of the person whose follow-up date has passed (as of now, also for a past week) |

Calls and leads worked are credited to whoever logged the call; assignments, resumes, conversions and overdue follow-ups to the lead's current owner. Where it shows: a **Lead sheet** table right under the recruiter table (assigned with "x of y called", interested, calls with leads worked, resumes, converted with the rate, overdue; the Assigned, Calls, Converted and Overdue headings sort the rows), a Lead sheet block in the person's drawer, two team cards (**Leads worked**, **Leads converted**), the new **Leads by recruiter** sheet and nine extra columns on **Recruiter summary** in the Excel file, and the extra columns in the CSV.

Score: **lead follow-up** is an optional fourth part, present only for a person who had leads assigned in the period: assigned leads that were called in the period / assigned leads. Then the weights are tasks 35%, hours 30%, stage moves 25%, lead follow-up 10% (a person with assigned leads and no call scores 0 on that part, it is not dropped). With no leads assigned the score is the original 40/30/30, so a week without leads scores exactly as before. The formula lines on screen and in the export state both. A part with nothing to measure is still dropped and the rest rescaled.

Questions to ask gains **Leads assigned but none called** (3 or more assigned in the period and none called) and **Lead follow-ups overdue** (5 or more). A "worked leads before the daily tasks were finished" question is not offered: the server refuses lead work until the daily tasks are done, so that data cannot exist. If the Lead sheet tables are missing, the lead columns and cards are hidden and a note says so.

Checks: [ ] a person with assigned and called leads shows the four-part score and the formula text; [ ] a person with no leads shows three parts; [ ] the Excel file has **Leads by recruiter**; [ ] a call logged on the Lead sheet updates the open report.

Implementation: [lead metrics](../../backend/app/services/lead_metrics.py), [weekly service](../../backend/app/services/staff_weekly.py), [export](../../backend/app/services/staff_weekly_export.py), [staff report model](../../frontend/src/features/staff-report/staff-report-model.ts), [table](../../frontend/src/features/staff-report/staff-weekly-report.tsx), [drawer](../../frontend/src/features/staff-report/staff-drawer.tsx).
