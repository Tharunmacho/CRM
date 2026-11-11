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
  notifications retain their user filters. Since the November 4 pass (below) the snapshot is kept for
  up to a minute per user and dropped by any commit that touches a collection it holds.
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

## API speed pass - 4 November 2026

The hosted database is 40-300 ms away from the server, so a request costs (sequential statements) x (round trip)
plus whatever Python does with the rows. This pass measured both on a scratch stack and removed the biggest of each.

How it was measured (repeatable): a throwaway PostgreSQL 17 cluster with 4,000 candidates (resume text, OCR JSON,
documents), 700 placements with stage history, 20 staff, 3,000 mails, WhatsApp and manual-upload jobs, and a small TCP
proxy that delays every packet by 50 ms each way (100 ms round trip, including connection set-up and idle pings). The
benchmark script logs in as admin, manager and recruiter and calls every hot endpoint cold and warm, then fires the
start-up burst the SPA sends (12 requests at once) after an idle gap, with and without a preceding write. The scripts live
in the session scratchpad (`perf/seed_perf.py`, `latency_proxy.py`, `bench.py`); nothing in the application ships latency
emulation, and nothing was run against the shared database.

### What was slow and why

- `GET /api/auth/session?workspace=true` (the whole desk): one SQL statement but 1.5-2.6 s. About 1.3 s of that was
  FastAPI's `jsonable_encoder` walking an 8.7 MB, already JSON-shaped dict; the rest was building ~4,000 candidates
  through pydantic (0.5 s), the statement (0.4 s) and gzip in the event loop (0.2 s).
- `GET /api/inbox/attention` ran seven statements (three counts, a second statement for attachment names...).
- `GET /api/daily-work` runs 26-28 statements, many of them the same read twice (reworked below).
- `skill-facets`, `pool-counts`, `position-groups`, the dashboard, reports, form options and `suggestions` recomputed
  their answers for every tab on every poll even though the Data Bank and settings change rarely.
- Only the desk snapshot was compressed: the 500 KB admin dashboard and every list went out as plain JSON.
- After a quiet moment every pooled connection paid its own `SELECT 1` on the request path, and a burst of parallel
  reads opened new connections (~1 s each) when the pool had shrunk: time that never shows as "db" in `Server-Timing`.

### What changed

- `services/response_cache.py`: a short-lived in-process memo. Entries name the change-feed topics they were built from
  and remember those topics' generation counters; a commit in this process (after it has committed, at connection
  check-in) or a NOTIFY from another process (the live hub) bumps the counters, so the entry is dropped at once. Counters
  are read before the value is built, so a commit that lands during a build leaves the entry stale. Entries also expire
  (5-120 s) to cover what the feed ignores (worker heartbeats) or a dropped listener, and concurrent identical requests
  share one build. Keys include the user id and role wherever the answer differs by person; sign-in and role checks run
  before the cache, so a response is never served to someone who may not have it. `CRM_RESPONSE_CACHE=0` turns it off;
  under pytest it is off unless `CRM_RESPONSE_CACHE=force`.
- Desk snapshot (`/api/auth/session?workspace=true`, `/api/bootstrap`): built once per user and kept already encoded and
  gzipped (60 s, dropped by any commit to a collection it holds). Encoding uses `json.dumps` directly, documents are written
  as plain dictionaries, and compression happens in the worker thread instead of the event loop.
- Kept for 5-10 s and dropped by their topics: `/api/inbox/health`, `/pulse`, `/revision`, `/unread-count`,
  `/attention` (per user), `/api/whatsapp/health`. Kept for 60 s: `/api/candidates/pool-counts`, `skill-facets`,
  `position-groups` (the Data Bank is shared, so one entry serves everyone), `/api/form-options/suggestions`. Kept until the
  topic changes: `/api/form-options` (with its ETag/304), `/api/settings`. Kept for 20-30 s per person: `/api/dashboard` (also dropped by the `leads` topic: it carries the Lead sheet card numbers from one aggregate statement read beside the pipeline read; `/api/dashboard/leads` is kept 10 s per person and card),
  `/api/reports`, `/api/reports/staff-weekly` (admin only).
- `/api/inbox/attention`: seven statements became five (sign-in check, three lists, one statement for the three totals;
  attachment names ride along as an array). `GET /api/candidates/{id}/application-form`: two rounds became one.
- Compression: every JSON/text API reply of 1 KB or more is gzipped (not attachments, spreadsheets, images or the
  `/api/events` stream, which must never be buffered).
- Connection pool: a keeper thread pings idle connections in parallel every few seconds and keeps six resident, so
  requests no longer pay a ping or a 1 s reconnect; the checkout ping is skipped for connections the keeper just proved.

### Daily work and the Data Bank list (second round)

- `GET /api/daily-work` went from 26-28 statements to 16-17 (about 9 sequential rounds instead of 26): the goals (with the agency
  fallback), the four attendance reads and the owned-candidate counts are read once, side by side, and handed to the allocation,
  progress and shortfall steps instead of being read two or three times; the two stage counts are one statement; remark
  authors come in the remark statement; the placement lookups and the stage remarks run together after the rows; the
  on-leave list comes from the roster. Single call at a 100 ms round trip: 3.9 s -> 2.2 s. What is left is the allocation
  transaction itself (reconcile, have-count, done-count, stage counts, progress snapshot, commit): they must see each
  other's uncommitted changes, so they stay sequential; a PostgreSQL pipeline or one combined statement would be the next step.
  A recruiter with fewer candidates than the quota retries their top-up (lock, used slots, pool) on every call (+5 statements).
- Data Bank list (`GET /api/candidates`): the page and its total are one statement (`count(*) OVER ()`), and the answer is kept
  for 15 s per search (dropped by candidate, placement, order, recruiter commits). `skill-facets` computes each cleaned skill
  label once (`OFFSET 0` fence) instead of three times per element.
- The desk response (`/api/auth/session?workspace=true`, `/api/bootstrap`) now carries `inboxUnread` (the sidebar unread badge,
  same shape as `unread` in `/api/inbox/pulse`: `{"total": n, "accounts": {accountId: n}}`), read in the same round trip as
  the snapshot, so a page load needs no separate pulse request. Mail changes empty the kept desk.

No migration was needed: EXPLAIN on the scratch data showed sequential scans only on tables of a few thousand rows
(candidates, placements, jobs), all of them cheaper than one network round trip. Candidate list default order is
`created_at DESC, id`: ~45 ms at 4,000 rows; an index on `(created_at DESC, id)` becomes worthwhile beyond ~50,000
candidates. If `email_ingestion_jobs` or `whatsapp_messages` grow past ~100,000 rows, add partial indexes on
`status IN ('failed', 'needs_review')`.

### Measurements (100 ms round trip, 4,000 candidates, medians of three)

Queries are statements per request (parallel reads count each); "kept" = answered from the response memo.

| Endpoint | Statements before -> after (cold / kept) | Cold ms before -> after | Repeat ms before -> after |
| --- | --- | --- | --- |
| Desk snapshot, admin (`/api/auth/session?workspace=true`) | 2 -> 3 (incl. unread badge) / 0 | 2,609 -> 1,330 | 1,640 -> 4 |
| Desk snapshot, recruiter | 1 -> 2 / 0 | 1,550 -> 505 | 1,769 -> 3 |
| Dashboard, admin (488 KB -> 40 KB on the wire) | 1 / 0 | 361 -> 335 | 340 -> 4 |
| Data Bank list | 2 -> 1 / 0 | 582 -> 145 | 175 -> 6 |
| pool-counts / skill-facets / position-groups | 1, 2, 1 / 0 | 209 -> 138, 365 -> 191, 174 -> 123 | 185 -> 3, 360 -> 2, 142 -> 4 |
| Inbox health / pulse / whatsapp health | 2 / 0 | 141, 176, 143 (unchanged) | 152, 141, 154 -> 5, 3, 4 |
| Inbox attention, admin | 7 -> 4 / 0 | 537 -> 195 | 271 -> 8 |
| Application form | 6 (2 rounds -> 1) | 660 -> 130 | 279 -> 135 |
| Reports / staff weekly (19 statements) | 1 / 19 | 441 -> 238 / 865 -> 876 | 344 -> 2 / 457 -> 2 |
| Form options / suggestions | 1 / 7 | 151 -> 133 / 347 -> 378 | 142 -> 4 / 375 -> 3 |
| Settings | 3 -> 4 / 0 | 390 -> 499 | 372 -> 6 |
| Daily work, recruiter / supervisor | 27 -> 16 / 17 | 3,870 -> 2,095 / 4,270 -> 2,329 | 3,749 -> 2,174 / 3,914 -> 2,289 |
| Start-up burst of 12 requests, admin, after a write | 21 | 2,646 -> 764 | |
| Start-up burst, admin, nothing changed | 31 -> 21 | 1,947 -> 616 | |
| Start-up burst incl. two daily-work calls, admin, after a write | 75 -> 51 | 4,877 -> 2,954 | |

The first request after a change pays the build (cold); everything in the burst after it, in any tab and for any user
the entry is valid for, is a memory read. Cold times of single-statement endpoints are bounded by the 100 ms round trip
(plus ~25 ms of Python); they cannot go lower without fewer statements. The staff-weekly report (19 statements) belongs to
the Reports screen and is only kept, not restructured. Hosted-database latency of 300 ms scales every "statements" figure
accordingly.

## Frontend speed pass - 4 November 2026

The browser side was measured before it was changed, on a scratch stack only (throwaway PostgreSQL 17, local API, a
production build served by `vite preview` and the development server), never against the shared database or the user's
own API. Server latency is dominated by the hosted database, so the goal here is fewer, shared and later requests, less
JavaScript before the first screen, and less rendering when nothing changed.

### What was measured, and what it found

- **Bundle.** `node frontend/scripts/bundle-report.mjs <dist>` prints the first-load files (entry plus static imports), the
  per-route first load and the largest lazy chunks. The entry statically imported the whole charts library (375 KB, 104 KB
  gzip) on every page, the login included: `clsx` is used by every screen and by recharts, and the manual chunk rules left it
  in the charts chunk. It now sits in `vendor-react`, so charts load only with Reports. The application dialog (`StageModal`,
  imported by nine screens, opened by a click) is a lazy wrapper around `stage-modal-body.tsx` and is fetched when idle.
- **Requests and render cost.** `node frontend/scripts/measure-requests.mjs --base <url> --email .. --password ..` drives
  headless Chrome through the DevTools protocol (no dependency to install) and reports, for each screen, API requests,
  duplicates (same GET within 2 s), bytes, script time, long tasks, and time to shell and to "no skeleton left". `--idle N`
  watches the dashboard for polling, `--noevents` blocks the live stream to see the polling fallback, `--live` fires live
  events that change nothing on screen, `--hover` checks link prefetch, `--spa` clicks through the sidebar. In development
  (React StrictMode, what `npm run dev` runs) the same page fired most reads twice, because StrictMode cancels the first
  effect's request and starts it again: `dashboard`, `inbox/attention`, `inbox/health`, `whatsapp/health`, `daily-work`,
  `reports`, `inbox/drafts` and four reads of the candidate profile.

### What changed

- `shared/api/client.ts` (the request layer): identical GETs share one request while it is in flight (5 s join window);
  cancelling is delayed 100 ms so an unmount followed by a remount reuses the request; the early read started by `index.html`
  is handed over a second time within 1.5 s (StrictMode). New per-call `cache: { ttlMs, staleMs, tags, errorTtlMs,
  onRevalidated }` and `fresh: true`: a fresh copy is answered without a request, a stale one at once with a background
  re-read, and a failed read is remembered so a missing route is not hammered. Every write drops the copies that share its
  first path word or a declared tag (and in-flight reads; presence heartbeats are exempt); sign-in/out and any 401 clear all;
  uploads clear candidate/document copies; the live hub calls `invalidateReads(topics)` before it tells screens, so a refetch
  caused by an event never reads a cache that predates it; a read in flight when its data was invalidated is never stored.
  Cached: form options (5 min, failures 30 s), form suggestions (60 s), the daily list (3 s). Staff availability stays uncached
  on purpose (it must never fall back to cached manual-leave flags).
- `shared/lib/poll.ts` and `liveInterval`: timers pause while the tab is hidden, catch up once when shown, and back off
  (doubling, at most 8x and five minutes) while the poll keeps failing. `shared/lib/coalesce.ts` is the debounce helper (trailing,
  leading, max wait). Live-driven workspace reads run one at a time with a single follow-up, so a burst of events no longer aborts
  and restarts the 8 MB snapshot read in a loop.
- `shared/state/reconcile.ts`: a refreshed workspace keeps the old objects wherever the content is identical, and the old
  snapshot when nothing differs, so a live event about something else renders nothing and does not re-run the effects that depend
  on `store.candidates` (the Data Bank re-read three lists after every snapshot refresh). Cost: 12-30 ms for 5,000 candidates.
- Application form: background reads that return exactly what is on screen are dropped, the reading poll backs off from 4 s to
  30 s while nothing changes, live bursts are coalesced for 1.2 s, and a change the server names as another candidate's is ignored.
- `app/prefetch.ts`: hovering or focusing a link (one delegated listener) downloads that route's code, plus the form lists for
  screens that start with them, when the browser is idle; never on data-saver or 2G.
- The desk response carries `inboxUnread` (same shape as `unread` in `/api/inbox/pulse`); the store seeds the sidebar badge from it
  and skips the first `/api/inbox/pulse` 1.2 s after load (one request fewer per page load). Later polling and live updates are
  unchanged, and a server that omits the field still gets that first pulse.
- `index.html` preloads Poppins 400, 500 and 600. The jobs rail counts open orders per title once instead of once per render.

### Results (production build, scratch data, admin, warm cache unless stated)

Bundle (`bundle-report.mjs`; raw KB / gzip KB; includes the 148 KB global stylesheet):

| | Before | After |
| --- | ---: | ---: |
| Entry, first load of every page (JS + CSS) | 793.3 / 218.6 | 428.3 / 118.8 |
| Entry JavaScript alone | 645 raw | 280 raw |
| Dashboard first load (entry + shell + screen + its imports) | 1,553.7 / 415.4 | 1,099.5 / 284.4 |
| Data Bank | 1,511.3 / 403.4 | 1,088.7 / 285.1 |
| Tasks | 1,533.4 / 409.2 | 1,059.8 / 271.3 |
| Candidate profile | 1,567.4 / 423.4 | 1,129.2 / 298.2 |
| Clients / Jobs / Inbox | 1,408 / 367, 1,436 / 378, 1,458 / 379 | 1,045 / 268, 1,073 / 280, 1,094 / 280 |
| Reports (charts, still lazy) | 1,439.5 / 373.9 | 1,075.7 / 275.3 |
| Dashboard transfer, cold cache (bytes on the wire) | 974.8 KB | 883.6 KB |

API requests per page load (production build, admin): Dashboard 6, Data Bank 5 (7 in resumes mode), Clients 2, Jobs 2, Inbox 4,
Candidate profile 7, Application form 4, Add candidate 5, Reports 3, all with 0 duplicates before and after. Tasks went from 5 with a
repeated `daily-work` and a second snapshot read (one run; timing-dependent) to 3 for admin, and from 6 to 5 for a recruiter
(duplicates 1 to 0).

The development server (StrictMode), where the original log came from, showed 11 duplicate GETs per full pass before (Dashboard 4,
Candidate profile 4, Tasks 1, Inbox 1, Reports 1) and 0 after, for admin and recruiter.

Other measurements: idle dashboard with the live stream up, 0 requests in 40 s (unchanged); with the stream blocked, 9 in 70 s before
and after (the polling fallback keeps its pace; it now pauses while the tab is hidden and backs off while failing). Twelve live events
that change nothing on screen: main-thread script time 188 / 78 / 21 ms on Dashboard / Data Bank / Jobs before, 5 / 3 / 3 ms after;
162 DOM mutations on the Data Bank before, 0 after; and the Data Bank no longer re-reads its three lists (35 requests) after each
snapshot refresh. Time to "no skeleton left" moved within noise (200-330 ms) on the local stack, where the API answers in 1-2 ms; the
gains are in bytes, requests and avoided work, which matter far more at 150-300 ms per database round trip.

### Re-measure

```
cd frontend && npx vite build --outDir /scratch/dist && node scripts/bundle-report.mjs /scratch/dist
API_PROXY_TARGET=http://127.0.0.1:<scratch api port> npx vite preview --outDir /scratch/dist --port <port>   # and `npx vite --port <port>` for StrictMode
node scripts/measure-requests.mjs --base http://localhost:<port> --email <admin> --password <pw> --idle 40 --live --spa --hover
```

### Not done, and why

- The workspace snapshot cache in `sessionStorage` stays off (`SNAPSHOT_CACHE_ENABLED = false` was a deliberate privacy decision);
  the shell still waits for the one session+workspace read, which index.html starts before the bundle has loaded.
- CSS (148 KB, 27 KB gzip) is global and render-blocking; splitting it needs a per-class audit. Lucide icons are tree-shaken per
  icon (about 75 chunks of 0.5 KB, 40 KB in total); merging them into one chunk would lower the file count but raise the login page.
- No list windowing: the Jobs and Clients lists already slice their cards, and nothing measured long enough to justify it.

## Daily work allocation (Tasks screen), task 13F

`GET /api/daily-work` tops every working recruiter up to the daily goal before it answers, so it has to cope with a first call of
the day (the workflow worker normally pre-allocates every 60 s, but a request can still hit the cold path) and with a repeat call
that finds nothing to do. Before this pass both were sequential and per recruiter.

### What was slow

- Cold path: the top-up ran per recruiter (advisory lock, used slots, pool, then one savepoint + INSERT per item), and the day's
  progress snapshot was written one INSERT per recruiter and metric. For 20 recruiters that is 450+ statements, one after the other.
- Steady state: goals, four attendance tables, owned counts, item counts, three progress reads and the list rows were separate
  statements, reconcile / have-count / progress ran one after another on the request's transaction, and a recruiter with fewer
  candidates than the quota retried lock + used slots + pool on every call.
- Every parallel read and every transaction also paid psycopg's separate `BEGIN` and the pool's rollback-on-return (3 round trips
  for a one-statement read).

### What changed

- `services/daily_work.py`
  - `pool_for(db, {user: need}, day)`: the candidate pool for ALL recruiters in one statement. Each recruiter's stage tasks come from
    a LATERAL subquery with the same filters, order and `LIMIT need` as the per-recruiter `_pool` (kept as the reference
    implementation and used by the tests), then the owned-candidate picks fill what is left (`need` minus the stage picks).
  - `ensure_allocations` is set-based: the short recruiters' advisory locks are taken in ONE statement in sorted key order (so
    overlapping requests cannot deadlock; same per-recruiter keys as before), free slots are read in one statement, the pool in
    one, and every new item goes in with one `INSERT ... ON CONFLICT DO NOTHING RETURNING`. A conflict (another request took a
    candidate) is retried for the affected recruiters, at most twice, instead of a savepoint per item. Slot numbers, morning /
    evening split, shift-based due times and the "an hour per candidate after a missed deadline" rule are unchanged.
  - `read_state`: goals, the four attendance tables, owned counts, today's item counts and the progress inputs (candidates done,
    both stage counts, stored snapshot rows) in ONE statement (JSON sub-selects). `goal_progress` uses it and `save_progress`
    writes the missing snapshot rows with one INSERT and the changed ones with one `UPDATE ... executemany`.
  - `pool_waiting`: one statement saying which recruiters have anything left to hand out. This replaces the "pool empty" marker:
    nothing is cached, so nothing can be stale; a short-handed recruiter whose pool is empty is simply not locked or topped up.
  - `fetch_rows` / `load_rows(fetched=...)`: the list's rows query can run in the first round.
  - `run_beside` / `read_together`: independent reads on autocommit connections of their own (one round trip, no BEGIN, nothing to
    roll back), optionally beside the request's own write (the reconcile).
- `api/routers/daily_work.py`: round 1 is the reconcile beside the state read, the rows and `pool_waiting`; when the reconcile
  changed nothing and nobody needs a top-up (a repeat call) those reads are the answer and nothing is written or committed. When
  something changed, the allocation runs on the request's transaction, is committed, and the progress inputs and rows are read
  again in one round. Round 2 is the placements / remarks behind the rows beside the snapshot write (autocommit).
- Not changed: the allocation rules, `reconcile`, release / carry-over behaviour, goals, alerts, the `kind` column, the live
  `tasks` topic, the worker (`scan_daily_work` calls the same `ensure_allocations`, now ~7 statements for any number of staff).

### Results (scratch PostgreSQL behind a 100 ms round-trip proxy, 20 recruiters x 30 owned candidates, manager view)

The "before" column is the previous route and allocation run on the same data (reconstructed from the old code); times include the
test client waiting for the request's final rollback, one more round trip than a browser waits for.

| Call | Statements before | Statements after | Time before | Time after |
| --- | ---: | ---: | ---: | ---: |
| First call of a new day (cold, 100 items) | 456 | 15 | 58.1 s | 1.8 s |
| Repeat call (steady state) | 16 | 8 | 2.1 s | 0.64 s |
| First call, one recruiter with 1 candidate | 447 | 15 | 56.7 s | 1.6 s |
| Repeat call, one recruiter with 1 candidate | 20 | 8 | 2.5 s | 0.65 s |

A repeat call is four round trips before the response: the roster (autocommit), the reconcile beside the state, rows and pool check,
and the placement lookups; no commit and no lock.

Regression tests (`backend/tests/test_daily_work_fast.py`, `test_daily_work.py`): the cold path for 20 recruiters runs at most 30
statements with one lock statement, one item INSERT and one snapshot INSERT; a repeat call at most 12 (10 for one recruiter) and
takes no lock and writes nothing even with short-handed recruiters; a candidate who arrives for a short-handed recruiter is still
picked up on the next call; the set-based allocation produces exactly the per-recruiter result (same candidates, slots, sessions and
due times) on mixed seeded data; two simultaneous first calls (and four overlapping groups of recruiters) never double-allocate.

### Not done

- A time-based or change-feed-invalidated "pool empty" cache: `pool_waiting` is read in the same round trip, so it saves the same
  work without a staleness window.
- The first request on a cold connection pool still pays the connection handshakes (not specific to this endpoint).

## November 11 local loading verification

The Candidate screen's generic skeleton is the route-code download fallback. Its
closed application-form preview previously imported the full printable form before
Candidate could render. That preview now loads only when opened, removing seven
application modules (42,320 source bytes) from Candidate's initial static import
graph. Vite warms the shell and Candidate entry during development startup; it
does not warm every feature. A route download that takes over six seconds offers
a manual reload instead of leaving an unexplained skeleton indefinitely.

Read-only checks through the restored Hostinger SSH tunnel returned HTTP 200 for
twelve handlers: compact bootstrap, candidate list/counts/groups/facets, form
options/suggestions, dashboard, reports, inbox attention/health and settings.
First calls took 110–1,063 ms; repeated cached calls took 4–17 ms. A separate cold
database connection plus `SELECT 1` took 2,070 ms, versus 112–119 ms on a reused
connection. These are individual local samples, not latency guarantees.

The HTTP harness supplied an existing admin dependency, enforced database
read-only mode and skipped startup hooks. It measures handler/query behavior,
not browser rendering or sign-in latency. Aggregate evidence is in the ignored
`artifacts/performance-20261111-read.json`; no record contents or credentials are
included. Thirty bootstrap, dashboard, connection-pool and response-cache tests
passed against a separate local `crm_schema_test_performance` database.
