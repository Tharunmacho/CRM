# Staff attendance and activity

Attendance is a deliberate staff check-in/check-out. Activity is an estimate of interaction with the CRM browser. They have separate tables and separate meanings.

## Access and screens

Staff use the shell clock or Attendance page to check in/out and view their own sessions. Admin and Manager view the team, filter by staff and agency date, and inspect the attendance/activity timeline. Only Admin can correct a session; a reason is required and the before/after values are saved in the audit log. Corrections reject future times and overlapping sessions.

The clock's current state is labelled separately from the selected day's totals. An overnight shift contributes only its overlap with each agency day. `AGENCY_TIMEZONE` defaults to `Asia/Kolkata`; database timestamps are UTC.

## Activity rules

- The browser sends a heartbeat every 30 seconds while a Staff account is signed in. A visible, focused page is online after a genuine pointer, key, touch or scroll interaction.
- A page with no interaction for 5 minutes becomes idle. A newly opened page begins idle until interaction occurs.
- Hiding the page queues a visibility update; page exit also sends a best-effort leave beacon. Every update has a per-page increasing sequence. The server retains the highest sequence, including a leave that arrives before its first heartbeat, so delayed updates cannot reverse a newer hide/show. A crash or lost connection expires after 90 seconds from the last accepted heartbeat. The background worker closes expired rows; reports enforce this cap independently.
- Interaction age is measured with `performance.now()`, not the device's wall clock. Queued updates recompute the age when dispatched. The server uses that bounded age only to classify online/idle; it owns every stored timestamp. Changing the device clock cannot extend activity or change recorded attendance times.
- Multiple tabs are merged. Online takes priority over idle during an overlap, so opening more tabs cannot increase the time counted.
- Online, idle and offline durations are calculated only inside checked-in periods. Offline is the remaining checked-in time after observed activity intervals. Historical timelines contain all three states.
- Logout, session expiry, password/session revocation, CLI password reset and deactivation end presence. They do not fabricate a checkout. Staff should check out explicitly; Admin can correct a forgotten checkout with a reason.

Only CRM browser signals are collected. The application does not monitor other apps, device activity, screenshots or keystroke contents. Browser activity is not proof of work or a payroll calculation.

## Storage and retries

`staff_attendance_sessions` stores server-timestamped check-in/out, original request keys, correction actor and reason. A partial unique index permits one open shift per staff account. `staff_attendance_requests` records **every accepted request key**, including a second tab's request that returns an already-open shift and a repeated checkout of an already-closed shift. Retrying that key after the shift closes, another shift opens, or an Admin corrects the original session cannot create or alter a different attendance action.

`staff_presence_intervals` stores the staff account, browser client identifier, login-session relationship, online/idle state, start/end and last heartbeat. `staff_presence_clients` stores each page's login binding and highest accepted sequence separately from activity intervals. An early leave is an ordering checkpoint, not a fabricated interval. Replayed or older sequences neither change state nor renew the 90-second deadline. Fresh page instances use fresh client IDs, including duplicated tabs. Historical activity remains when login rows are removed.

All attendance/presence writes and logout/revocation take locks in the order User → Login → Presence. Mutations revalidate the persisted login after acquiring the locks, so a request authenticated before a concurrent logout cannot recreate presence afterward. Regular browser updates are serialized and coalesced; obsolete responses cannot overwrite a later visibility state. Reports derive offline intervals and union overlapping clients.

The heartbeat body is `{clientId, sequence, visible, interactionAgeSeconds}`; age is null before the first interaction and bounded to 0–86400 seconds. Leave sends `{clientId, sequence}`. Absolute client interaction timestamps are rejected. These signals remain browser-reported estimates, not proof that a person is working.

Run `python -m app.runtime` for the API and supervised presence worker together. The worker checks expiry every 30 seconds. Attendance requires migrations through `0014_attendance_ordering`. The migration backfills the retry ledger from original request keys, preserves attendance/history, and caps pre-upgrade open presence intervals. Deploy API and frontend together and reload existing pages for the sequence/age contract. The database dictionary and release guide describe coordinated upgrade and backups.
