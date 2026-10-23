# Loading and API performance

The October 20, 2026 investigation found repeated remote database round trips and
eager loading of every feature page. Adding a Redis connection did not change those
paths: Redis is currently a readiness integration, not a CRM response cache.

## Changes

- Anonymous page loads use `/api/auth/session`, which returns `{ "me": null }`
  without a database query when no session cookie exists. Protected API endpoints
  still require authentication.
- Session validation joins the session and current account in one query. Revocation,
  account deactivation and role changes continue to take effect on the next request.
- `/api/bootstrap` uses one PostgreSQL statement containing independent JSON
  aggregates, after the session check. Explicit response-field projections omit
  password hashes, raw OCR, search vectors and storage keys. Personal to-dos and
  notifications retain their user filters. This is a fresh snapshot, not a cache.
- The frontend shares startup requests, rejects obsolete responses after identity
  changes, and loads feature bundles on demand. Loading and retry screens replace
  blank or misleading empty states. Ordinary JSON requests have a 30-second deadline.
  Vite warms the login entry at development-server startup.
- Upload database and filesystem work runs in FastAPI's worker pool, with a bounded
  file read, so it does not block the API event loop.
- Health checks reuse one database connection. Startup logs show PostgreSQL and
  Redis status. API responses include `Server-Timing`; requests taking at least
  one second log the route template, status and duration, without credentials,
  query strings or record identifiers.

PostgreSQL aggregation uses the supported
[JSON aggregate functions](https://www.postgresql.org/docs/18/functions-aggregate.html)
and SQLAlchemy's
[table-valued row expressions](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#table-types-passed-to-functions).
Independent aggregates avoid multiplying rows across unrelated collections.

## Measurements

These are medians of three sequential read-only requests from the Windows development
machine to the existing remote PostgreSQL database. The original backend used port
8000; the updated verification process used port 8001 with the same database and
Redis configuration. These are local measurements, not production guarantees.

| Check | Before | After |
| --- | ---: | ---: |
| Dashboard bootstrap HTTP response | 3,782.6 ms | 1,645.0 ms |
| Bootstrap data reads inside an open session | 2,698.3 ms | 272.1 ms |
| Data SQL statements per bootstrap | 14 | 1 |
| Authenticated bootstrap SQL statements including session check | 16 | 2 |
| Initial production JavaScript bundle, uncompressed | 891.43 KB | 250.69 KB |

The response still contains the same collection counts and 16,100 JSON bytes for the
measured data. Speed comes from fewer round trips and deferred code downloads, not
from dropping records or returning cached permissions.

In a 360x800 Chrome check without artificial API delays, the login form appeared
after 521 ms on the warmed development server and 120 ms on a repeat visit. The
locally served production build took 141 ms with a fresh browser and 37 ms on a
repeat visit. Each visit issued one anonymous session request and zero bootstrap
requests; authenticated feature bundles were not downloaded. These samples measure
local frontend rendering, not the deployed site's internet latency.

## Verification and limits

Backend regression tests cover session freshness, query budgets, complete wire
payloads, latest agreements, document versions, stage history, personal-data
isolation, empty arrays, fresh mutations, upload threading and timing-log privacy.
PostgreSQL cases require the guarded disposable `crm_schema_test*` database described
in `backend/tests/test_database_naming.py`; never point them at a working CRM database.

Run the frontend request tests with `npm test --workspace frontend`, build with
`npm run build`, and run backend tests with `npm run test:backend -- -q`.

Bootstrap still returns the desk collections in full. Large datasets will need
server-side pagination and dashboard aggregates rather than silently truncating
results. External OCR and email-provider latency require separate measurements;
this change does not claim to speed up those providers. Local API-to-database
network latency also remains. In deployment, use the database's internal address
on the same server network.

No database migration or new environment variable is needed for these changes.
Redeploy the new repository revision to update production.

## October 22 backend read audit

Monthly attendance was the largest concrete query problem. The previous reader
looked up shift, sessions, approvals, corrections, calendar and weekly off separately
for every employee and every day. The team screen can now load those inputs in
nine SQL statements, or ten when inactive employees require deactivation-history
lookups. The same day and month calculation functions still apply the adopted
attendance rules. The data lives only for that request; permissions and attendance
are not cached between requests.

Attendance approval lists also batch employee and cover names in at most three
reads. Daily activity already fetched users, sessions and browser intervals in
three queries; it now groups intervals once per employee instead of scanning the
whole team's intervals repeatedly. Staff Directory already uses a single aggregate
query and needed no query-count change.

Dashboard now reads only operational inputs in one statement: open tasks,
today's owner-authored updates, candidate/application summaries, and the effective
interview result used by the selected count. Historical task events, completed
tasks, mailbox provenance and unrelated stage payloads no longer cross the database
connection for this screen. Its order metrics share the report calculation helper,
without calculating all six reports on every dashboard visit. Report duration
matching and order cohorts now use keyed groups instead of repeated full scans.

Report and export reads now also apply the authorized reporting period in SQL.
Only in-period task events and the execution tasks needed for completion counts,
stage durations or those events are transferred. Stage history contributes only
the latest non-reopened attempt for each application/stage, with three JSON fields
used by reporting: result, amount and currency. Choosing that effective attempt
happens before date filtering, so a later attempt correctly supersedes an earlier
receipt. Source-account lookup reads only messages referenced by candidates
whose intake falls in the period; a message's receipt date does not change intake
attribution. Task remarks are unnecessary because missed updates use durable events.

Opening a candidate can fetch `/api/candidates/{id}` in one data query. Its full
response is identical to the existing bootstrap candidate, including resume text,
document versions and initial-call outcome. It retains the PRD's shared Data Bank
read access for authenticated Admin, Manager and Staff. Application drafts,
stage/task history, raw OCR, storage keys and credentials are not included by this
endpoint. The frontend can therefore defer resume text until the profile is opened.

The following measurements use generated records only in the isolated loopback
PostgreSQL 17 test database. They exclude authentication and HTTP/network overhead.
Elapsed times are individual local samples, not production latency promises.

| Synthetic check | Previous | Updated |
| --- | ---: | ---: |
| Monthly attendance: six tracked employees, one inactive cutoff, 31-day month | 1,243 queries | 10 queries |
| Same monthly response, two local samples | 1,331.7 / 3,315.1 ms | 91.3 / 182.7 ms |
| Single employee monthly read | Scaled with recorded days | 9 queries |
| Approval list with 20 incoming cover requests | Per-request name lookups | 3 queries |
| Dashboard database JSON, 100 historical stage records/tasks/events | 630,847 bytes | 1,304 bytes |
| Same dashboard database read, one local sample | 54.7 ms | 39.4 ms |
| Report database JSON for the same historical fixture | 630,847 bytes | 1,926 bytes |

The attendance test compares the entire response against the previous per-day
reader, including local-midnight tracking, overnight work, effective timings,
single-date duty plans, Friday weekly off, paid leave, pending versus approved
permissions, overtime, corrections and inactive cutoffs. Dashboard tests compare
the full response across Admin team/employee scope, Manager and Staff, including
repeated/reopened interviews, future records and current versus historical ownership.
Report pruning additionally matches every output field across 60 combinations of
roles, dates and client/order/designation/staff filters. Its fixtures include future
superseding receipts, reopened attempts, legacy amount/currency fallback, old tasks
with current events, reassigned execution ownership and message/ intake-date differences.

Focused verification passed **87 tests** across `test_candidate_detail.py`,
`test_reporting.py`, `test_attendance_hr.py`, `test_attendance_overview.py`,
`test_staff_activity.py`, `test_attendance_read_performance.py` and
`test_dashboard_read_performance.py` and `test_reporting_read_projection.py`. Query-budget tests protect against returning
to per-row lookups; elapsed time is reported but is not a brittle pass/fail threshold.

Reports still load candidate/application/order summary collections and effective
stage summaries across history to preserve filter choices, relational attribution
and legacy ownership fallback. Bootstrap still contains directory/record-picker
collections. Very large installations need a separate volume benchmark and
query-plan review, including the completed-task date predicate; these changes do
not establish unlimited-scale acceptance. Report transfer became smaller, but the
small local timing samples do not prove a faster SQL execution plan. No schema
migration is required for these read changes.


## Workspace startup and screen reads ? 22 October 2026

The browser now requests `/api/auth/session?workspace=true&compact=true` once at
startup. The endpoint checks the current session and returns the authorized desk
in the same response: two SQL statements for an authenticated account, no SQL for
an anonymous visitor, and one authentication statement for a revoked session.
It keeps `Cache-Control: no-store`; no CRM records are cached in local storage.
The previous startup required separate session and bootstrap HTTP requests.

Compact mode omits only extracted resume text from the shared desk. Document
versions, candidate fields, tasks, applications and private recipient/owner scopes
remain identical. Full text loads from the candidate detail endpoint when needed.
A synthetic 145,000-character resume is excluded from the compact response while
the ordinary bootstrap and candidate detail contracts still provide the full text.
`test_workspace_startup.py` checks field parity, payload reduction, revocation and
account switching; `test_candidate_detail.py` checks the profile projection.

Route entry shares an in-flight desk read and reuses a successful snapshot for
up to five seconds. Explicit Refresh and post-write reads always request current
records. Collection indexes replace repeated relationship scans in table cells;
notification merging now uses sets rather than nested scans. Background stored
Inbox polling is 15 seconds when idle and five seconds during active processing.
Writes refresh the currently selected mailbox/filter even if it changed while the
write was pending; late responses after sign-out cannot update the next account.

Slow startup offers a connection retry after six seconds. Filtered WhatsApp and
report results are scoped to the requested filters, with loading/retry states and
no previous-filter records presented as current. Export downloads have a 90-second
deadline. These are recovery controls, not a claim that an unavailable server can
be repaired by refreshing. All measurements and tests use isolated local data;
production network latency, deployment capacity and cold database connections
still require a deployment-specific measurement.
