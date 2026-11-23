# Recruiter attendance and activity

Attendance for a Recruiter or Manager starts when they **sign in** (or press **Check in**) and ends when they **sign out**, press **Check out**, or go silent for 30 minutes (see "Sign-in counts as attendance" below). The monthly policy uses those clock records. Activity is a separate estimate of Recruiter interaction with the CRM browser. Administrators have no attendance.

## Sign-in counts as attendance

This replaces the earlier rule that only the Check in button created attendance (owner's decision: "when I log in it should count").

| Event | What happens |
|---|---|
| **Sign in** (Recruiter or Manager) | Today's session starts at the sign-in time, in the same transaction as the sign-in, with the audit entry "Recruiter checked in" (`source: login`). Idempotent: a second sign-in, a second browser or a second tab finds the open session; the one-open-session index allows only one. An attendance problem never stops anyone signing in. |
| **First page of the day for someone already signed in** (cookie outlived the day, or signed in before this rule) | The first visible heartbeat of a new page starts a session (`source: heartbeat`) when there is no open session and none yet today. |
| **Page comes back after the 30-minute silence limit** (tab hidden, laptop asleep) | The sweep (or the page itself) closed the old session at its last heartbeat; the returning page starts a new one, so the rest of the day counts. The gap is never counted. A deliberate check-out or sign-out is **never** undone by a heartbeat. Constant `RESTART_AFTER_INACTIVITY` in `staff_activity.py` switches this off (strict rule: a heartbeat only starts a session when there is none today). |
| **Sign out** | The open session is closed at the sign-out time (`source: logout`), so *Last logout* and *Hours worked* fill in. If the person is still active in another browser (a heartbeat in the last 90 s under a different login) the day goes on. A later sign-in the same day starts a new session. |
| **Silence for 30 minutes** (`ABANDONED_AFTER_SECONDS`) | The presence worker's sweep closes a **Recruiter's** open session at their **last heartbeat** (never "now"), `source: inactivity`. With no heartbeat at all the session closes at its own start (no invented hours). Managers send no heartbeat, so silence proves nothing and they are never swept. |
| **A Manager's forgotten session from an earlier day** | At the next sign-in it is closed at its own start (no hours, flagged as a missed check-out) and today's session starts. |
| **Check in / Check out buttons** | Still work as a manual override. If a session is already open, Check in returns it (the button shows as checked in). |

The session's request key records who opened and closed it (`login:`, `auto:`, `logout:`, `sweep:`, `missed:`, or a browser id for a button press); the attendance API returns `checkInSource` and `checkOutSource` for every session. No migration is involved.

## Reference policy adopted on 21 October 2026

The user explicitly confirmed: **Use the same reference attendance rules.** The implementation adapts the attendance workflow from `Tharunmacho/email-automation` at `30f7ce6b7402553116bbe6a2b8b2142bc72c8915` to this CRM's PostgreSQL data and three roles.

| Rule | Behavior |
| --- | --- |
| Standard duty | 10:00–19:00 with a 60-minute break: 480 payable minutes. Effective-dated timings and single-date duty plans can change the shift. |
| Flexible hours | A 60-minute window on either side of the shift. First check-in to last check-out, less the shift break, determines coverage; browser activity does not determine pay. |
| Monthly allowance | 60 minutes of grace, allocated chronologically; at most two approved permission occasions, with a warning at 45 minutes. An Admin may record a reasoned emergency exception to the occasion limit. |
| Missing punches | Provisional until shift end plus two days; then the full duty becomes unpaid. Missing days and unpaid leave do not consume grace. |
| Paid leave | One approved paid day per calendar month. Further paid-leave approvals convert to unpaid leave and expose that conversion. |
| Weekly off | Sunday by default. Recruiter and Manager can choose Friday until Thursday 23:59:59 in the agency timezone, for this week or the following three weeks. A separately approved rotational off fixes that week's choice. |
| Permissions | Late arrival, early exit, early check-in, official duty and work from home have durable requests and reasoned decisions. Late arrival and early check-in must be requested before shift start; early exit before leaving. WFH must be requested and approved before shift start. |
| Overtime | Explicit approved extra OT offsets only the month's partial-day late/short time. It never cancels absence or unpaid leave; unused OT does not create a cash payment. Daily rows show raw unpaid time; monthly totals show the offset separately. |
| Roster | A single-date duty plan overrides a weekly off or holiday, but preserves approved leave. Recurring timings are effective-dated. |
| Regularisation | Reasoned added punches and time recovery preserve raw clock records. A status override changes the displayed label only; calendar/permission/recovery actions change the calculation explicitly. |

The monthly report starts at the employee's earliest recorded attendance, request, calendar entry or adjustment. It does not invent attendance before that date or charge future dates. Inactive employees retain historical records; accrual stops at the recorded deactivation date (or account update date when older deactivation history is unavailable).

## Approvals and leave cover

Recruiter see their own monthly record and requests; Manager sees their own record and Recruiter; Admin sees both employee roles. No employee can approve their own request. Ordinary permissions require Manager or Admin. Leave and extra OT require managerial and financial approval; **Admin fills the reference Finance Manager role** in this three-role CRM and may satisfy both stages. Manager requests therefore require Admin. Only Admin can directly grant or replace paid/unpaid calendar entries. Manager can manage Recruiter scheduling and regularisation, but cannot change their own records. Admin-only raw-session correction is retained.

Leave requests nominate available Recruiter cover when a colleague is available. The colleague accepts before approval, or declines with a note so the requester can nominate again. Cover remains Recruiter-only because recruitment ownership in this CRM is Recruiter-only. Manager leave can still be recorded without inventing Manager-owned candidate work.

The supervised presence/attendance worker starts accepted, approved cover on the leave date when the calendar still records leave. It transfers uncontacted candidates and their open work, retaining deadlines and recording candidate/task ownership events. On the next day it returns unfinished work only when ownership is still unchanged and the original owner remains active Recruiter without the manual on-leave switch. Completed work and later human assignments are preserved. Consecutive leave days settle the previous handover before starting the next. A missed leave window is recorded as settled without retroactively transferring work. A durable cover ledger makes retries safe; loading a page never triggers reassignment. Approved leave also excludes absent Recruiter from new candidate allocation.

The reference's separate salary, payroll and payslip module is not included. Employer-specific holidays and hard-coded employee exceptions are not imported; use the explicit calendar/timing controls.

## Access and screens

Recruiter and Manager follow the shell attendance shortcut to the Attendance page's check-in/out controls. The workspace provides monthly overview, approvals, employee record, duty roster and a primary **Sessions & activity** tab. Each daily employee record opens that employee's sessions and activity for the selected date. Only Admin can correct a raw session; a reason is required and before/after values are audited. Corrections reject future times and overlapping sessions.

The monthly overview shows attendance percentage, credited days out of scheduled days, absence/late/incomplete counts, requests and the unpaid balance after approved OT. Scheduled days exclude weekly offs and holidays; credited days include present, late, early exit, paid permission, official duty, work from home, paid leave and provisional days with a check-in. An empty schedule shows no percentage. The absence badge includes unpaid leave; late and incomplete badges follow the displayed `LT` and `MP` statuses. These display metrics do not change work-minute or unpaid-time calculations.

Open **View record** for the employee's daily sessions and allowances, or their request badge for that employee's requests. **More** contains correction history. The overview, daily records and sessions/activity roster become cards on narrow screens. This presentation update adds computed API fields and requires no migration beyond `0015_attendance_hr`; deploy its frontend and API together.

The clock's current state is labelled separately from the selected day's totals. Sessions & presence clips an overnight shift to its overlap with each agency day. The monthly policy instead attributes the whole overnight shift to its check-in date. `AGENCY_TIMEZONE` defaults to `Asia/Kolkata`; database timestamps are UTC.

### Reports > Attendance: one reading of a day (real time, no invented figures)

| Situation | Live board (one calendar day) | Month grid and Excel (policy) |
| --- | --- | --- |
| **Today** | Only today's sessions and presence: hours are the minutes logged in inside today, an open shift runs to now ("Still logged in"). | The same day, read by the policy. |
| **A shift crossing midnight** (23:50 to 00:20) | Split at the agency's midnight: 10 minutes on the first day, 20 on the next; the two add up to the whole shift. A time that is not on the day shown is written with its date ("20 Nov 23:50"), and a shift carried in from the day before is never that day's arrival, so it is never "late". | The whole shift belongs to the day it started (login and logout on that day's row). |
| **A shift never checked out, once its day is over** | A missed check-out: no last logout, **no hours** and no online/idle time. | The same: a half-punched day has no worked minutes (provisional until the shift end plus two days, then unpaid). |
| **A shift never checked out, on today** | "Still logged in", hours run to now. | Provisional. |
| **Yesterday / another day** | Exactly that day's sessions. The person's current clock state (shown separately as "Now") never turns another day into "still logged in". | Exactly that day. |
| **Online / idle / away** | Online or idle only while the last heartbeat is at most **90 seconds** old, judged by the server at every read and again by the page every 10 seconds, so a closed tab stops showing as online within about 10 seconds of the 90-second rule. | Not used. |

Hours on the board are time logged in on that day; the month grid shows the policy's covered minutes (flexible window, less the break), so the two can differ by design. A person who is not checked in shows a dash, never "0h 00m / 0h 00m". On the day shown "Today", the first summary card reads **Not in yet** (it becomes **Absent** once the day is over). The board and month are read again the moment attendance, leave, timing or calendar data changes (the `attendance` live topic; every table above pushes it) and at least every 20 seconds as a safety net; at midnight a board that was on "today" moves to the new day by itself and shows "Loading" rather than yesterday's numbers.

## Activity rules

- The browser sends a heartbeat every 30 seconds while a Recruiter account is signed in. A visible, focused page is online after a genuine pointer, key, touch or scroll interaction.
- A page with no interaction for 5 minutes becomes idle. A newly opened page begins idle until interaction occurs.
- Hiding the page queues a visibility update; page exit also sends a best-effort leave beacon. Every update has a per-page increasing sequence. The server retains the highest sequence, including a leave that arrives before its first heartbeat, so delayed updates cannot reverse a newer hide/show. A crash or lost connection expires after 90 seconds from the last accepted heartbeat. The background worker closes expired rows; reports enforce this cap independently.
- Interaction age is measured with `performance.now()`, not the device's wall clock. Queued updates recompute the age when dispatched. The server uses that bounded age only to classify online/idle; it owns every stored timestamp. Changing the device clock cannot extend activity or change recorded attendance times.
- Multiple tabs are merged. Online takes priority over idle during an overlap, so opening more tabs cannot increase the time counted.
- Online, idle and offline durations are calculated only inside checked-in periods. Offline is the remaining checked-in time after observed activity intervals. Historical timelines contain all three states.
- Logout, session expiry, password/session revocation, CLI password reset and deactivation end presence. **Logout also ends the day's attendance** at the logout time (unless the person is still active in another browser); the others do not fabricate a checkout, but the 30-minute silence sweep closes the session at the last heartbeat. Admin can still correct any session with a reason.

Only CRM browser signals are collected. The application does not monitor other apps, device activity, screenshots or keystroke contents. Browser activity is not proof of work or a payroll calculation.

The user-facing label for the API's `offline` state is **Away / no signal**: the server cannot distinguish a hidden tab, closed browser, network interruption or work performed outside the CRM. Managers have attendance but no browser collector; their activity is explicitly **Not tracked** and their checked-in time is excluded from activity/offline totals. The sessions view shows server-generated snapshot time, per-interval durations and current-state badges separately from the selected day's totals.

## Storage and retries

`staff_attendance_sessions` stores server-timestamped check-in/out, original request keys, correction actor and reason. A partial unique index permits one open shift per Recruiter/Manager account. `staff_attendance_requests` records **every accepted request key**, including a second tab's request that returns an already-open shift and a repeated checkout of an already-closed shift. Retrying that key after the shift closes, another shift opens, or an Admin corrects the original session cannot create or alter a different attendance action.

Unconfirmed browser clock actions also retain their original key and checkout session in tab session storage. A one-way, non-credential login marker scopes recovery to the same user/login. Reload shows an explicit **Retry** action; it never dispatches a stored action automatically. A changed login invalidates the pending intent, and clock POSTs reject a mismatching marker before mutation. Storage failures leave the in-memory retry available.

`staff_presence_intervals` stores the recruiter account, browser client identifier, login-session relationship, online/idle state, start/end and last heartbeat. `staff_presence_clients` stores each page's login binding and highest accepted sequence separately from activity intervals. An early leave is an ordering checkpoint, not a fabricated interval. Replayed or older sequences neither change state nor renew the 90-second deadline. Fresh page instances use fresh client IDs, including duplicated tabs. Historical activity remains when login rows are removed.

Clock/presence writes and logout/revocation follow User → Login → Presence lock ordering. These mutations revalidate the persisted login after acquiring locks, so a request authenticated before a concurrent logout cannot recreate presence afterward. HR requests, decisions, schedules and cover use assignment cursor → employees → requests/candidates/tasks, matching recruitment allocation and account changes. Regular browser updates are serialized and coalesced; obsolete responses cannot overwrite later visibility state. Reports derive offline intervals and union overlapping clients.

The heartbeat body is `{clientId, sequence, visible, interactionAgeSeconds}`; age is null before the first interaction and bounded to 0–86400 seconds. Leave sends `{clientId, sequence}`. Absolute client interaction timestamps are rejected. These signals remain browser-reported estimates, not proof that a person is working.

Run `python -m app.runtime` for the API and supervised presence/attendance worker together. The worker checks presence expiry, abandoned attendance sessions (30 minutes of silence) and leave cover every 30 seconds in separate transactions. The monthly workflow requires `0015_attendance_hr`, which adds seven tables while preserving existing clocks, presence and recruitment records. Revision `0014` retains the clock retry ledger and ordered browser checkpoints. Deploy API and frontend together. The database dictionary and release guide describe coordinated upgrade and backups.
