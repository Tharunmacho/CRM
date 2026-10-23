# Attendance check in and browser activity

This module records deliberate check-in/check-out and explains how long a Staff account was observed in the CRM during those attendance sessions. Requirements: **ATT-01, ATT-02**, approved extensions to the original recruitment PRD. Browser activity is separate from [monthly attendance policy](21-attendance-monthly-hr.md) and does not calculate productivity or pay.

## Roles and preparation

| Role | Attendance and activity access |
| --- | --- |
| Staff | Check in/out, view own attendance and own browser activity. |
| Manager | Check in/out, view own attendance and Staff attendance/activity. Another Manager's attendance is outside this role's scope. Manager browser activity is **Not tracked**. |
| Admin | View employee attendance/activity and correct raw sessions with a reason. Admin does not use an employee attendance clock. |

The account must be active and signed in. The matching application/database must be ready. The complete runtime includes the presence worker; reports also cap expired presence independently. Times display in the agency timezone, normally `Asia/Kolkata`, while saved timestamps use UTC.

## User journey

### Check in and check out

1. Open **Attendance** from the sidebar or the clock shortcut in the top bar. Staff sees **My attendance**; Manager can switch between the team and their own record.
2. Wait for **Your attendance today** to load. The current badge says **Checked out** or **Checked in**. A loading/error state must not be mistaken for a confirmed clock state.
3. Select **Check in**. While the request saves, the button is disabled. The server records its current time and returns the active session; the screen shows the check-in time and today's checked-in duration.
4. Use the CRM normally. Navigating between screens does not start another shift. Multiple tabs share the employee's single open attendance session.
5. At the end of the shift, select **Check out**. The request identifies the exact session being closed. Confirm the badge changes to **Checked out** and inspect the recorded time in **Sessions & activity**.
6. A later deliberate **Check in** can start another session. Daily attendance sums each session's overlap with the selected date; time between sessions is excluded.

Closing the browser, locking the device, losing the connection or signing out does **not** check the employee out. The employee must explicitly finish the session, or Admin must make a reasoned correction. No login, page refresh or recovered request automatically creates a new attendance action.

## Review the duration and history journey

1. Open the primary **Sessions & activity** tab. Choose **Attendance date** and, for supervisors, **Employee**. The view refreshes every 30 seconds while visible and on focus/reconnection.
2. Read the four summaries: **Checked-in time**, **Online in CRM**, **Idle in CRM**, and **Away / no signal**. The last three describe Staff activity during attendance only. A Manager-only view says **Not tracked** for browser activity.
3. Review each employee row. The duration columns belong to the selected date; the clock and presence badges explicitly show the **current** state. The displayed update time is the server snapshot time.
4. Select **View sessions**. The check-in/check-out panel shows each session and its selected-date duration. The activity panel shows the inferred intervals with start, end and duration.
5. Alternatively, open an employee's monthly **Daily record** and choose **View sessions & activity** on a day. This carries the employee and exact date into the activity view. A direct link can use `/attendance?view=presence&staff=ID&date=YYYY-MM-DD`.
6. On a narrow screen, employee rows become cards. The same filters, counts and actions remain available; desktop and mobile use the same API records.

## What the activity states mean

| State on screen | Actual rule |
| --- | --- |
| Online in CRM | A visible, focused CRM tab has a genuine pointer, key, touch or scroll interaction within the last five minutes. |
| Idle in CRM | The tab is visible and focused but has no recent interaction. A newly opened page begins idle until interaction occurs. |
| Away / no signal | No foreground CRM interval is observed for this part of checked-in time. This can mean a hidden tab, closed browser, connection gap or work outside the CRM. It is not proof that the person is absent or unproductive. |
| Not tracked | This role has attendance but no browser collector. Its attendance is not counted as offline activity. |

The browser sends regular heartbeats while visible/focused. Hide/blur and page-exit signals end that page's observed interval; a missing heartbeat expires after 90 seconds. Multiple tabs are merged, with online taking priority over idle during overlap, so extra tabs cannot multiply time. A single page's sequence numbers prevent delayed requests from reversing a newer hide/show/leave signal.

Only CRM browser signals are used. Other applications, screenshots and keystroke contents are not collected. The activity proportion bar is a breakdown of recorded/inferred browser time, not a work-performance score. An overnight session is clipped at agency midnight for this daily view; the monthly policy attributes the overnight shift to its check-in date.

## Correction journey

1. Admin filters the employee/date and opens **View sessions**.
2. Select **Correct** for the raw attendance session. Check the displayed device timezone used by the datetime inputs before entering times.
3. Enter the correct check-in and, when the shift is finished, check-out. Leave check-out empty only when the session should remain open. Enter the reason and save.
4. The backend rejects future times, a checkout before check-in, and overlap with another session. Successful correction retains actor, reason and before/after values in the audit history. Session detail shows the correction reason.

Monthly **Correct record** actions are separate: added punches, time recovery and status classification are described in [monthly HR](21-attendance-monthly-hr.md). They must not be confused with editing a raw clock session.

## Screen and backend contract

`GET /api/attendance` returns scoped employee/date records, current clock state, computed activity, interval durations, snapshot time and a non-credential login marker. Reads do not create sessions. Clock POSTs authenticate the current persisted login under the employee lock, use server time and commit the session/request ledger/audit together. A database constraint permits only one open session per employee.

Every accepted check-in/out key is remembered. A second tab's check-in can return the already-open session; replaying that key after checkout still refers to the original action. A checkout retry cannot close a later shift or undo a subsequent Admin correction. The browser retains an unconfirmed action's original key and session in tab session storage, bound to the user/login. The marker is not the authentication cookie and cannot sign in a caller.

Presence writes revalidate active account and login, retain ordered checkpoints, and store server-owned timestamps. Logout, revocation and activity writes use consistent locking so an in-flight heartbeat cannot recreate activity after logout. Historical presence remains available after a login session is deleted.

## Recovery and cancellation

If a clock response is lost, the screen shows **Retry Check in** or **Retry Check out**. Refreshing the page recovers that intent for the same login, but the user must select Retry explicitly. **Refresh attendance** reloads the server state; a changed login clears the old intent, and the backend rejects a stale login marker before mutation. Do not use a new action to guess whether the earlier one succeeded.

Presence connection errors show a sync warning and suppress a falsely fresh activity badge. Reports retain a clear stale-data warning after a failed refresh. A local clock estimate advances at most 30 seconds without another server snapshot. Reconnection refreshes the state instead of inventing the missing interval.

Cancel a correction before saving to leave the record unchanged. Closing a saved session cannot be cancelled by the browser; correcting it requires the authorized, audited correction action. Staff following another employee's URL and Manager following another Manager's URL must receive an access error, including direct API requests.

## Acceptance checklist

- [ ] Staff and Manager can check in/out; Admin sees supervision without an employee clock.
- [ ] Two simultaneous check-ins produce one open session.
- [ ] Lose a successful check-in response, reload, and explicitly retry: the original key returns the original session without creating another.
- [ ] Repeat checkout after a later shift or correction; the later record stays unchanged.
- [ ] Change login before retry; the old intent cannot mutate the new login's attendance.
- [ ] Exercise foreground interaction, five-minute idle, blur/hide, closed browser and heartbeat timeout; labels and history match the defined estimates.
- [ ] Open two tabs; their overlapping activity is counted once.
- [ ] Verify online + idle + away equals the selected Staff member's checked-in time, allowing displayed rounding; gaps between shifts do not count.
- [ ] Verify a Manager's recorded attendance is **Not tracked**, never offline time.
- [ ] Review yesterday while currently checked in; historical totals and current badges remain distinct.
- [ ] Test a midnight-crossing shift, explicit checkout, logout, forced logout and a reasoned correction.
- [ ] Verify desktop/mobile filtering and the monthly-day link preserve employee/date.

## Current implementation and remaining acceptance

Clock, recovery, role scopes, activity history and responsive screens are implemented. The final local follow-up passed 69 attendance backend tests and the focused clock/presence tests; browser integration also exercised real HTTP response loss, historical time totals and mobile layouts. This does not replace elapsed-time acceptance on the deployed browsers/network. Manager browser tracking, desktop monitoring and productivity/payroll scoring are not implemented features.

Implementation: [attendance screen](../../frontend/src/screens/attendance/attendance.tsx), [clock context](../../frontend/src/features/attendance/attendance-context.tsx), [attendance API](../../backend/app/api/routers/attendance.py), [activity service](../../backend/app/services/staff_activity.py). Detailed comparison: [attendance follow-up](../audit/attendance-presence-followup.md).
