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
