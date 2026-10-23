# Staff attendance and activity

Attendance is a deliberate Staff or Manager check-in/check-out. The monthly policy uses those clock records. Activity is a separate estimate of Staff interaction with the CRM browser.

## Reference policy adopted on 21 October 2026

The user explicitly confirmed: **Use the same reference attendance rules.** The implementation adapts the attendance workflow from `Tharunmacho/email-automation` at `30f7ce6b7402553116bbe6a2b8b2142bc72c8915` to this CRM's PostgreSQL data and three roles.

| Rule | Behavior |
| --- | --- |
| Standard duty | 10:00–19:00 with a 60-minute break: 480 payable minutes. Effective-dated timings and single-date duty plans can change the shift. |
| Flexible hours | A 60-minute window on either side of the shift. First check-in to last check-out, less the shift break, determines coverage; browser activity does not determine pay. |
| Monthly allowance | 60 minutes of grace, allocated chronologically; at most two approved permission occasions, with a warning at 45 minutes. An Admin may record a reasoned emergency exception to the occasion limit. |
| Missing punches | Provisional until shift end plus two days; then the full duty becomes unpaid. Missing days and unpaid leave do not consume grace. |
| Paid leave | One approved paid day per calendar month. Further paid-leave approvals convert to unpaid leave and expose that conversion. |
| Weekly off | Sunday by default. Staff and Manager can choose Friday until Thursday 23:59:59 in the agency timezone, for this week or the following three weeks. A separately approved rotational off fixes that week's choice. |
| Permissions | Late arrival, early exit, early check-in, official duty and work from home have durable requests and reasoned decisions. Late arrival and early check-in must be requested before shift start; early exit before leaving. WFH must be requested and approved before shift start. |
| Overtime | Explicit approved extra OT offsets only the month's partial-day late/short time. It never cancels absence or unpaid leave; unused OT does not create a cash payment. Daily rows show raw unpaid time; monthly totals show the offset separately. |
| Roster | A single-date duty plan overrides a weekly off or holiday, but preserves approved leave. Recurring timings are effective-dated. |
| Regularisation | Reasoned added punches and time recovery preserve raw clock records. A status override changes the displayed label only; calendar/permission/recovery actions change the calculation explicitly. |

The monthly report starts at the employee's earliest recorded attendance, request, calendar entry or adjustment. It does not invent attendance before that date or charge future dates. Inactive employees retain historical records; accrual stops at the recorded deactivation date (or account update date when older deactivation history is unavailable).

## Approvals and leave cover

Staff see their own monthly record and requests; Manager sees their own record and Staff; Admin sees both employee roles. No employee can approve their own request. Ordinary permissions require Manager or Admin. Leave and extra OT require managerial and financial approval; **Admin fills the reference Finance Manager role** in this three-role CRM and may satisfy both stages. Manager requests therefore require Admin. Only Admin can directly grant or replace paid/unpaid calendar entries. Manager can manage Staff scheduling and regularisation, but cannot change their own records. Admin-only raw-session correction is retained.

Leave requests nominate available Staff cover when a colleague is available. The colleague accepts before approval, or declines with a note so the requester can nominate again. Cover remains Staff-only because recruitment ownership in this CRM is Staff-only. Manager leave can still be recorded without inventing Manager-owned candidate work.

The supervised presence/attendance worker starts accepted, approved cover on the leave date when the calendar still records leave. It transfers uncontacted candidates and their open work, retaining deadlines and recording candidate/task ownership events. On the next day it returns unfinished work only when ownership is still unchanged and the original owner remains active Staff without the manual on-leave switch. Completed work and later human assignments are preserved. Consecutive leave days settle the previous handover before starting the next. A missed leave window is recorded as settled without retroactively transferring work. A durable cover ledger makes retries safe; loading a page never triggers reassignment. Approved leave also excludes absent Staff from new candidate allocation.

The reference's separate salary, payroll and payslip module is not included. Employer-specific holidays and hard-coded employee exceptions are not imported; use the explicit calendar/timing controls.

## Access and screens

Staff and Manager follow the shell attendance shortcut to the Attendance page's check-in/out controls. The workspace provides monthly overview, approvals, employee record, duty roster and a primary **Sessions & activity** tab. Each daily employee record opens that employee's sessions and activity for the selected date. Only Admin can correct a raw session; a reason is required and before/after values are audited. Corrections reject future times and overlapping sessions.

The monthly overview shows attendance percentage, credited days out of scheduled days, absence/late/incomplete counts, requests and the unpaid balance after approved OT. Scheduled days exclude weekly offs and holidays; credited days include present, late, early exit, paid permission, official duty, work from home, paid leave and provisional days with a check-in. An empty schedule shows no percentage. The absence badge includes unpaid leave; late and incomplete badges follow the displayed `LT` and `MP` statuses. These display metrics do not change work-minute or unpaid-time calculations.

Open **View record** for the employee's daily sessions and allowances, or their request badge for that employee's requests. **More** contains correction history. The overview, daily records and sessions/activity roster become cards on narrow screens. This presentation update adds computed API fields and requires no migration beyond `0015_attendance_hr`; deploy its frontend and API together.

The clock's current state is labelled separately from the selected day's totals. Sessions & presence clips an overnight shift to its overlap with each agency day. The monthly policy instead attributes the whole overnight shift to its check-in date. `AGENCY_TIMEZONE` defaults to `Asia/Kolkata`; database timestamps are UTC.

## Activity rules

- The browser sends a heartbeat every 30 seconds while a Staff account is signed in. A visible, focused page is online after a genuine pointer, key, touch or scroll interaction.
- A page with no interaction for 5 minutes becomes idle. A newly opened page begins idle until interaction occurs.
- Hiding the page queues a visibility update; page exit also sends a best-effort leave beacon. Every update has a per-page increasing sequence. The server retains the highest sequence, including a leave that arrives before its first heartbeat, so delayed updates cannot reverse a newer hide/show. A crash or lost connection expires after 90 seconds from the last accepted heartbeat. The background worker closes expired rows; reports enforce this cap independently.
- Interaction age is measured with `performance.now()`, not the device's wall clock. Queued updates recompute the age when dispatched. The server uses that bounded age only to classify online/idle; it owns every stored timestamp. Changing the device clock cannot extend activity or change recorded attendance times.
- Multiple tabs are merged. Online takes priority over idle during an overlap, so opening more tabs cannot increase the time counted.
- Online, idle and offline durations are calculated only inside checked-in periods. Offline is the remaining checked-in time after observed activity intervals. Historical timelines contain all three states.
- Logout, session expiry, password/session revocation, CLI password reset and deactivation end presence. They do not fabricate a checkout. Staff should check out explicitly; Admin can correct a forgotten checkout with a reason.

Only CRM browser signals are collected. The application does not monitor other apps, device activity, screenshots or keystroke contents. Browser activity is not proof of work or a payroll calculation.

The user-facing label for the API's `offline` state is **Away / no signal**: the server cannot distinguish a hidden tab, closed browser, network interruption or work performed outside the CRM. Managers have attendance but no browser collector; their activity is explicitly **Not tracked** and their checked-in time is excluded from activity/offline totals. The sessions view shows server-generated snapshot time, per-interval durations and current-state badges separately from the selected day's totals.

## Storage and retries

`staff_attendance_sessions` stores server-timestamped check-in/out, original request keys, correction actor and reason. A partial unique index permits one open shift per Staff/Manager account. `staff_attendance_requests` records **every accepted request key**, including a second tab's request that returns an already-open shift and a repeated checkout of an already-closed shift. Retrying that key after the shift closes, another shift opens, or an Admin corrects the original session cannot create or alter a different attendance action.

Unconfirmed browser clock actions also retain their original key and checkout session in tab session storage. A one-way, non-credential login marker scopes recovery to the same user/login. Reload shows an explicit **Retry** action; it never dispatches a stored action automatically. A changed login invalidates the pending intent, and clock POSTs reject a mismatching marker before mutation. Storage failures leave the in-memory retry available.

`staff_presence_intervals` stores the staff account, browser client identifier, login-session relationship, online/idle state, start/end and last heartbeat. `staff_presence_clients` stores each page's login binding and highest accepted sequence separately from activity intervals. An early leave is an ordering checkpoint, not a fabricated interval. Replayed or older sequences neither change state nor renew the 90-second deadline. Fresh page instances use fresh client IDs, including duplicated tabs. Historical activity remains when login rows are removed.

Clock/presence writes and logout/revocation follow User → Login → Presence lock ordering. These mutations revalidate the persisted login after acquiring locks, so a request authenticated before a concurrent logout cannot recreate presence afterward. HR requests, decisions, schedules and cover use assignment cursor → employees → requests/candidates/tasks, matching recruitment allocation and account changes. Regular browser updates are serialized and coalesced; obsolete responses cannot overwrite later visibility state. Reports derive offline intervals and union overlapping clients.

The heartbeat body is `{clientId, sequence, visible, interactionAgeSeconds}`; age is null before the first interaction and bounded to 0–86400 seconds. Leave sends `{clientId, sequence}`. Absolute client interaction timestamps are rejected. These signals remain browser-reported estimates, not proof that a person is working.

Run `python -m app.runtime` for the API and supervised presence/attendance worker together. The worker checks presence expiry and leave cover every 30 seconds in separate transactions. The monthly workflow requires `0015_attendance_hr`, which adds seven tables while preserving existing clocks, presence and recruitment records. Revision `0014` retains the clock retry ledger and ordered browser checkpoints. Deploy API and frontend together. The database dictionary and release guide describe coordinated upgrade and backups.
