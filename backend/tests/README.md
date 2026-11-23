# Backend tests

Database-backed tests only run when `TEST_DATABASE_URL` names a **loopback** PostgreSQL database called `crm_schema_test*`
(psycopg driver, no query overrides). They reset the `public` schema, so never point it at a real database.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres@127.0.0.1:54329/crm_schema_test_a"
$env:PYTHONPATH = "<repo>\backend"
cd <repo>\backend
python -m pytest -p no:cacheprovider -q tests            # the whole suite
python -m pytest -p no:cacheprovider -q tests\test_daily_work_views.py
```

Two pytest processes at the same time need **different** database names (for example `crm_schema_test_a` and `crm_schema_test_b`).

## Optional: template database for `upgrade head` (OFF by default)

Most tests start from an empty database and call `_migrate(engine, "head")` (`test_database_naming.py`), i.e. about sixty alembic
migrations per test. `tests/template_db.py` can answer that one call by cloning a once-migrated template database instead:

* it is **opt-in**: `CRM_TEST_TEMPLATE=1` turns it on, `CRM_TEST_NO_TEMPLATE=1` always forces the real migrations;
* measured on the dev machine (2026-11-23) the real migrations took 2.2-4.3 s and the clone 1.5-3 s (12 MB template), and a
  side-by-side run of ten test files took 749 s (real) against 960 s (clone), so it is not a speed-up here and stays off;
* it is proven equivalent: `test_template_database.py` compares catalog, enums, functions, triggers, sequences and seeded rows
  of a clone with a real migration, and the clone with its template byte for byte;
* upgrades from an older revision, downgrades, `base`, `connection=` calls and `fresh=True` always run the real migrations;
* other pooled connections to the replaced database are discarded automatically; the template is dropped when the process exits.

Faster full runs that do work here: run the suite in several pytest processes at once, each with its own database name
(for example two halves of the file list on `crm_schema_test_a` and `crm_schema_test_b`).
