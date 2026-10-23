# Attendance clocks and browser activity follow-up — 22 October 2026

The user requested another end-to-end comparison of candidate entry, skill segregation and attendance, including check-in/out and time online/offline. This document records the attendance portion. The reference is the local checkout of `Tharunmacho/email-automation` at `30f7ce6b7402553116bbe6a2b8b2142bc72c8915`; the original recruitment PRD remains `docs/requirements/recruitment-crm-developer-specification.pdf`.

## Scope established from the reference and approved requirements

The reference's `app/attendance/service.py`, `engine.py`, `api.py` and `frontend/src/screens/AttendanceScreen.tsx` implement intentional punches, server-recorded time, monthly attendance, permissions, leave, schedules and corrections. They do not implement an equivalent foreground CRM online/idle/offline monitor. Its real-time connection indicator describes the application connection, not employee working time.

The original recruitment PRD does not define the reference's HR policy. The user subsequently approved the same attendance rules explicitly. Care India already implements those approved rules, adapted to Admin/Manager/Staff, as documented in [attendance.md](../attendance.md). This follow-up retains that policy, its existing migrations and its leave-cover integration. It does not add salary calculations, device surveillance or background desktop activity tracking.

Care India intentionally permits both Staff and Manager attendance clocks, but records browser presence for Staff only. Admin supervises attendance and can make reasoned raw-session corrections; Manager can see Staff and their own records, not another Manager's attendance. Staff see only themselves. These boundaries remain unchanged.

## Findings and changes

| Finding | Integrated behavior |
| --- | --- |
| Manager attendance was included in the calculated offline remainder even though no Manager presence collector runs. | The backend now declares `presence.tracked=false`, returns zero activity durations and an empty timeline for untracked accounts. The screen explicitly says **Not tracked**, including an employee-only summary. Manager checked-in time remains visible as attendance. |
| The old sessions view buried activity behind **More**, used a wide table on mobile, and did not show interval durations. | **Sessions & activity** is a primary attendance tab. It shows clear date/employee filters, four summary cards, an activity proportion bar, current-state badges, a responsive employee record and timed interval history. |
| Opening activity from an employee/day could lose that context. | Every daily employee record has **View sessions & activity**. It carries the employee and exact date. Direct links support `/attendance?view=presence&staff=ID&date=YYYY-MM-DD`. Existing Staff Directory employee/month links are retained. |
| “Offline” could be interpreted as not working or disconnected from the internet. | The UI labels this interval **Away / no signal** and explains that a hidden tab, closed browser or missing heartbeat all produce the same inference. Raw API states remain `online`, `idle`, `offline`; the explanation is explicit and activity never changes payroll hours. |
| A failed clock response retained its idempotency key only in memory, so refreshing the page lost the safe retry intent. | The pending action, original request key and original checkout session are stored in tab session storage with an opaque login marker. Reload recovers the pending action only for the same user/login and presents an explicit **Retry** action. Nothing automatically clocks in or out. |
| A recovered intent needed protection against a changed login, including the same employee signing in again. | GET attendance supplies a one-way, non-credential `clockContext`. Browser clock POSTs send it; the backend rejects a mismatching context before mutation. A refreshed snapshot clears old-login intent. No session cookie or password is exposed or stored. Existing server request-ledger protection remains authoritative. |
| Foreground resumes could wait for a periodic refresh, and an offline browser could display an old online badge. | Attendance and record views refresh on focus, visibility resume and reconnection. Network loss immediately displays a sync warning; stale activity badges are suppressed in the employee clock. The local attendance counter extrapolates at most 30 seconds between server snapshots. |
| Raw-session correction reasons were available in the API but omitted from session detail. | Corrected sessions now display the actor and reason beside their clock times. Admin-only corrections still preserve the audit record and reject future or overlapping times. |

## Complete workflow

1. Staff or Manager opens **Attendance** and explicitly selects **Check in**. The server records the current UTC time, returns the current shift and audits the action. Duplicate clicks/tabs reuse the existing open shift.
2. Staff presence reports real trusted pointer/key/touch/scroll interactions only while the CRM is visible and focused. A new page begins idle until interaction; five minutes without interaction becomes idle. Hiding or leaving the page closes its visible interval, with ordered signals protecting against delayed responses.
3. Missing heartbeats expire after 90 seconds. Multiple tabs are unioned, with online taking priority over idle, so concurrent tabs cannot inflate durations. Activity is intersected with checked-in sessions; time between shifts is excluded.
4. **Sessions & activity** shows selected-date attendance and activity durations separately from the current check-in/presence badges. Supervisors can filter an employee and inspect exact clock events and inferred activity intervals. The report records its server-generated snapshot time.
5. **Check out** closes the specific open attendance session. Closing the browser, losing the connection or signing out does not fabricate checkout. A lost response is recovered with the same original request key; an explicit retry cannot close a later shift or undo an Admin correction.
6. The monthly record continues using attendance punches, shifts, approved permissions, leave and corrections. Browser activity never determines payable or unpaid time.

## Storage and release

No new migration is required. All added API values are computed from the existing attendance sessions, idempotency ledger, presence intervals and login sessions. Deploy matching frontend/backend together. This work was validated only against an isolated local PostgreSQL database; it did not inspect production data, alter production configuration or push a release.

## Validation

Focused frontend tests cover monotonic activity age, ordered/coalesced heartbeats, hide/show/leave races, stale responses, durable clock recovery, user/login isolation, malformed stored intent and storage failures. Backend coverage includes actual API calls, clock replay, concurrent check-in, logout/revocation races, multiple tabs, idle/offline intervals, midnight clipping, role scopes, reasoned corrections, untracked Manager time and timeline duration totals across separate shifts.

The isolated regression run passed **69 backend tests** across `test_staff_activity.py`, `test_attendance_policy.py`, `test_attendance_hr.py` and `test_attendance_overview.py`. The focused frontend run passed **12 tests** across attendance helpers, presence reporting and clock-intent recovery. Backend evidence: `artifacts/workflow-followup/attendance-tests.xml`. `git diff --check` passed for the attendance patch.

The integration run and final browser evidence are recorded under `artifacts/workflow-followup/`. Root browser acceptance additionally exercises actual check-in response loss followed by reload/retry, foreground signals, check-out, historical intervals, Manager scope and responsive layouts.
