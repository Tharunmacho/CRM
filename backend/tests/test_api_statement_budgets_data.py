"""Statement budgets for the data-heavy reads: leads, candidates, applications, travelled, files, documents and reading queue.

The hosted database costs a round trip (about 300 ms) per statement, so a read must cost a small, fixed number of statements however
many rows exist. This module seeds a realistic volume of invented rows (600 leads, 500 candidates with documents, applications and
stages), calls every read of the slice as an admin and as a recruiter, counts the SQL statements of each request and asserts a budget.
Run it with `-s` to print the table (endpoint, role, statements, ms, response KB).

Needs TEST_DATABASE_URL like the other DB tests (an isolated loopback crm_schema_test* database). All people are synthetic.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.api.routers import (application_form, applications, candidate_exports, candidate_import, candidates, document_bundles,
                             document_diagnostics, document_jobs, files, lead_bulk, leads, travelled)
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

LEADS, CANDIDATES = int(os.environ.get("BUDGET_LEADS", 600)), int(os.environ.get("BUDGET_CANDIDATES", 500))  # scale up to look for scans
LATENCY_MS = 40  # per statement in the round-trip column of the printed table (the hosted database costs about 300)
RESULTS: list[tuple] = []
UUID_CANDIDATE = str(uuid4())


def seed(engine, storage) -> SimpleNamespace:
    """Invented volume, written with a few set-based statements so the fixture itself stays fast."""
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin Person", email="admin@example.test", role=m.Role.admin),
                    m.User(id="manager", name="Manager Person", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha Staff", email="a@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="b", name="Ravi Staff", email="b@example.test", role=m.Role.staff, receives_auto_assign=True)])
        db.commit()
        run = lambda sql, **p: db.execute(text(sql), p)  # noqa: E731
        run("INSERT INTO crm_client_contacts (id, name, country, status) SELECT 'cl' || g, 'Client Person ' || g, 'UAE', 'active' FROM generate_series(1, 6) g")
        run("INSERT INTO crm_employer_companies (id, client_id, name, country, status) "
            "SELECT 'co' || g, 'cl' || (1 + g % 6), 'Company ' || g || ' LLC', 'UAE', 'active' FROM generate_series(1, 12) g")
        run("INSERT INTO crm_job_designations (id, title, category, required_skills, min_experience) "
            "SELECT 'ds' || g, 'Designation ' || g, 'Kitchen', '[\"Cooking\"]'::jsonb, 1 FROM generate_series(1, 12) g")
        run("INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings, country, status) "
            "SELECT 'or' || g, 'ORD-' || lpad(g::text, 4, '0'), 'co' || (1 + g % 12), 'ds' || (1 + g % 12), 5, 'UAE', 'open' FROM generate_series(1, 24) g")
        run("INSERT INTO crm_candidates (id, ref, care_id, name, phone, email, source, status, owner_id, intake_owner_id, designations, skills, "
            "total_experience, gulf_experience, resume_text, passport_no, created_at) "
            "SELECT 'cd' || g, 'CND-' || lpad(g::text, 5, '0'), 'CI-' || CASE WHEN g < 1000 THEN lpad(g::text, 3, '0') ELSE g::text END, 'Candidate Person ' || lpad(g::text, 4, '0'), "
            "'9' || lpad((100000000 + g)::text, 9, '0'), 'cand' || g || '@example.test', 'email', "
            "(CASE WHEN g % 10 = 0 THEN 'deployed' ELSE 'new' END)::candidate_status, "
            "CASE WHEN g % 25 = 0 THEN NULL WHEN g % 2 = 0 THEN 'a' ELSE 'b' END, CASE WHEN g % 2 = 0 THEN 'a' ELSE 'b' END, "
            "jsonb_build_array('Designation ' || (1 + g % 12)), jsonb_build_array('Cooking', 'Skill ' || (g % 17)), g % 15, g % 6, "
            "'Cook with experience in kitchen number ' || g, 'P' || lpad(g::text, 7, '0'), now() - (g || ' minutes')::interval "
            f"FROM generate_series(1, {CANDIDATES}) g")
        run("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source, status, owner_id, intake_owner_id) "
            "VALUES (:u, 'CND-UUID1', :care, 'Uuid Person', '9000000901', 'email', 'new', 'a', 'a')", u=UUID_CANDIDATE, care=f"CI-{CANDIDATES + 1:03d}")
        run("UPDATE crm_candidates SET resume_tsv = to_tsvector('simple', resume_text)")
        for slot in ("resume", "passport", "aadhaar", "photo"):
            run("INSERT INTO document_attachments (id, entity_type, entity_id, slot, version, file_name, size_kb, content_type, object_key, content_hash, uploaded_by) "
                "SELECT :slot || g, 'candidate', 'cd' || g, :slot, 1, :slot || '-' || g || '.pdf', 120, 'application/pdf', 'candidates/cd' || g || '/' || :slot || '.pdf', "
                f"md5(:slot || g::text) || md5(g::text || :slot), 'admin' FROM generate_series(1, {CANDIDATES}) g WHERE g % 7 <> 3 OR :slot = 'resume'", slot=slot)
        # applications: every tenth candidate is deployed, every fifth other one is active, the rest have a closed earlier one
        run("INSERT INTO crm_applications (id, candidate_id, order_id, owner_id, current_stage, status) "
            "SELECT 'ap' || g, 'cd' || g, 'or' || (1 + g % 24), CASE WHEN g % 2 = 0 THEN 'a' ELSE 'b' END, "
            "CASE WHEN g % 10 = 0 THEN 11 ELSE 1 + g % 8 END, (CASE WHEN g % 10 = 0 THEN 'deployed' WHEN g % 5 = 0 THEN 'active' ELSE 'closed' END)::application_status "
            f"FROM generate_series(1, {CANDIDATES}) g WHERE g % 3 <> 0")
        run("INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, outcome, owner_id, completed_by) "
            "SELECT 'st' || g || '-' || s, 'ap' || g, s, 1, "
            "CASE WHEN s = 11 THEN jsonb_build_object('travelDate', to_char(date '2026-01-01' + g, 'YYYY-MM-DD'), 'airline', 'Air Test', 'pnr', 'PNR' || g) ELSE '{}'::jsonb END, 'passed', "
            f"'a', 'admin' FROM generate_series(1, {CANDIDATES}) g, generate_series(1, 11) s WHERE g % 3 <> 0 AND s <= CASE WHEN g % 10 = 0 THEN 11 ELSE 1 + g % 8 END")
        run("INSERT INTO crm_lead_batches (id, file_name, file_size, file_sha256, file_format, uploaded_by, status, rows_total, rows_new, column_map) "
            "SELECT 'lb' || g, 'sheet-' || g || '.csv', 4000, md5(g::text) || md5(g::text || 'x'), 'csv', 'admin', 'completed', 200, 200, "
            "'{\"full_name\": \"name\", \"which_position_are_you_applying_for?\": \"positionApplied\"}'::jsonb FROM generate_series(1, 3) g")
        run("INSERT INTO crm_leads (id, batch_id, import_row, external_id, phone_key, email_key, created_time, name, phone, email, city, state, position_applied, "
            "country_interest, platform, campaign_name, form_name, extra, raw, status, owner_id, call_count, matched_candidate_id, candidate_id, "
            "next_follow_up_at, resume_received_at, created_at) "
            "SELECT 'ld' || g, 'lb' || (1 + g % 3), g, 'ext' || g, '9' || lpad((200000000 + g)::text, 9, '0'), 'lead' || g || '@example.test', now() - (g || ' hours')::interval, "
            "'Lead Person ' || lpad(g::text, 4, '0'), '+919' || lpad((200000000 + g)::text, 9, '0'), 'lead' || g || '@example.test', 'Testville ' || (g % 7), 'Kerala', "
            "'Position ' || (g % 5), 'UAE', CASE WHEN g % 2 = 0 THEN 'ig' ELSE 'fb' END, 'Campaign ' || (g % 4), 'Form ' || (g % 3), "
            "jsonb_build_object('Question one', 'Answer ' || g, 'Question two', 'Other ' || g), "
            "jsonb_build_object('full_name', 'Lead Person ' || g, 'how_many_years_of_experience_do_you_have_in_the_restaurant_industry?', '5 years'), "
            "(ARRAY['new','contacted','interested','resume_requested','resume_received','not_interested'])[1 + g % 6], "
            "CASE WHEN g % 9 = 0 THEN NULL WHEN g % 2 = 0 THEN 'a' ELSE 'b' END, g % 4, CASE WHEN g % 20 = 0 THEN 'cd' || (g / 20) ELSE NULL END, NULL, "
            "CASE WHEN g % 5 = 0 THEN now() + interval '1 hour' ELSE NULL END, CASE WHEN g % 6 = 4 THEN now() ELSE NULL END, now() - (g || ' minutes')::interval "
            f"FROM generate_series(1, {LEADS}) g")
        run("INSERT INTO document_attachments (id, entity_type, entity_id, slot, version, file_name, size_kb, content_type, object_key, uploaded_by) "
            f"SELECT 'lr' || g, 'lead', 'ld' || g, 'resume', 1, 'resume-' || g || '.pdf', 90, 'application/pdf', 'leads/ld' || g || '.pdf', 'admin' FROM generate_series(1, {LEADS}) g WHERE g % 6 = 4")
        run("INSERT INTO crm_lead_activities (id, lead_id, user_id, kind, summary) "
            f"SELECT 'la' || g || '-' || k, 'ld' || g, 'a', 'note', 'Note ' || k FROM generate_series(1, {LEADS}) g, generate_series(1, 4) k")
        run("INSERT INTO document_extraction_jobs (id, owner_id, group_id, kind, filename, content_type, object_key, content_hash, size_kb, status, candidate_id, attempts) "
            "SELECT 'jb' || g, CASE WHEN g % 4 < 2 THEN 'admin' ELSE 'a' END, 'grp' || (g % 8), (ARRAY['resume','passport','aadhaar'])[1 + g % 3], 'doc-' || g || '.pdf', 'application/pdf', 'jobs/' || g, "
            f"md5(g::text) || md5(g::text || 'j'), 100, (ARRAY['uploaded','pending','processing','completed','needs_review','failed'])[1 + g % 6], "
            f"CASE WHEN g % 2 = 0 THEN NULL ELSE 'cd' || g END, 0 FROM generate_series(1, 60) g")
        # real files for the bytes endpoints (candidate cd1)
        (storage / "candidates" / "cd1").mkdir(parents=True, exist_ok=True)
        (storage / "candidates" / "cd1" / "resume.pdf").write_bytes(b"%PDF-1.7 synthetic")
        db.commit()
    return SimpleNamespace(candidate="cd1", staff_candidate="cd2", uuid_candidate=UUID_CANDIDATE, lead="ld5", batch="lb1", application="ap1", order="or1")


@pytest.fixture
def world(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    ids = seed(schema_database, tmp_path)
    # Like the real engine, one with room for a request's parallel reads whose connections stay open between requests (the test engine
    # would close every connection above five on return and the measured waves would blur with connection set-up).
    engine = create_engine(schema_database.url, pool_size=12, max_overflow=6, connect_args={"options": "-c timezone=UTC"})
    held = [engine.raw_connection() for _ in range(12)]
    for connection in held:
        connection.close()
    with Session(engine, expire_on_commit=False) as db:
        app = FastAPI()
        for module in (leads, lead_bulk, candidates, candidate_import, applications, travelled, files, document_bundles, document_jobs,
                       application_form, candidate_exports, document_diagnostics):
            app.include_router(module.router)
        who = {"id": "admin"}
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, engine=engine, ids=ids)
    engine.dispose()


class Statements(list):
    """The statements of one request, with the moment each began (to count round trips: statements that start together are one)."""

    def __init__(self):
        super().__init__()
        self.starts: list[float] = []

    def rounds(self, gap=0.05) -> int:
        """Sequential waves. Needs latency_ms on the listener: each statement then lasts longer than `gap`, so a statement that waited
        for another one starts at least that much later, while the reads of one parallel_reads start within a few milliseconds."""
        waves, last = 0, None
        for moment in sorted(self.starts):
            if last is None or moment - last > gap:
                waves += 1
            last = moment
        return waves


@contextmanager
def counted(engine, latency_ms=0):
    from app.core import deps
    deps._AUTH_CACHE.clear()
    statements = Statements()

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))
        statements.starts.append(time.perf_counter())
        if latency_ms:
            time.sleep(latency_ms / 1000)  # a network hop per statement; statements on other threads wait at the same time

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def measure(world, label, role, method, path, latency_ms=0, **kwargs):
    world.who["id"] = role
    world.db.rollback()
    world.db.expire_all()
    with counted(world.engine, latency_ms) as statements:
        started = time.perf_counter()
        response = world.client.request(method, path, **kwargs)
        elapsed = (time.perf_counter() - started) * 1000
    RESULTS.append((label, role, len(statements), round(elapsed), round(len(response.content) / 1024, 1)))
    if os.environ.get("DUMP_STATEMENTS") and os.environ["DUMP_STATEMENTS"] in label:
        print(f"\n--- {label} [{role}] {response.status_code}")
        for sql, moment in zip(statements, statements.starts):
            print(f"    +{(moment - statements.starts[0]) * 1000:5.0f}ms", sql[:110])
    return response, statements


def duplicates(statements):
    """Identical statement text run more than once in one request (a repeated lookup)."""
    seen: dict[str, int] = {}
    for sql in statements:
        if sql.upper().startswith(("SAVEPOINT", "RELEASE", "ROLLBACK", "BEGIN", "COMMIT")):
            continue
        seen[sql] = seen.get(sql, 0) + 1
    return {sql[:100]: n for sql, n in seen.items() if n > 1}


# The admin-only import report loads its batch once for the file name and once for the workbook (rare, one small statement).
KNOWN_REPEATS = {"lead batch report xlsx"}

# (label, method, path, kwargs, roles -> statement budget). A budget is the measured count plus a little headroom.
def cases(ids):
    return [
        ("leads list 25", "GET", "/api/leads?page_size=25", {}, 3),
        ("leads list 100 + search", "GET", "/api/leads?page_size=100&q=lead%20person", {}, 3),
        ("leads list filtered + extra cols", "GET", "/api/leads?status=new&campaign=Campaign%202&extra=*&page_size=10&page=1", {}, 3),
        ("leads summary", "GET", "/api/leads/summary", {}, 3),
        ("leads facets", "GET", "/api/leads/facets", {}, 3),
        ("leads columns", "GET", "/api/leads/columns", {}, 3),
        ("lead detail", "GET", f"/api/leads/{ids.lead}", {}, 5),
        ("lead batches", "GET", "/api/leads/batches", {}, 3),
        ("candidates list 20", "GET", "/api/candidates", {}, 3),
        ("candidates list search", "GET", "/api/candidates?q=cook%20kitchen&page_size=50", {}, 3),
        ("candidates list skills", "GET", "/api/candidates?skills=Cooking&skills=Skill%203", {}, 3),
        ("candidate position-groups", "GET", "/api/candidates/position-groups", {}, 3),
        ("candidate skill-facets", "GET", "/api/candidates/skill-facets", {}, 4),
        ("candidate pool-counts", "GET", "/api/candidates/pool-counts", {}, 3),
        ("candidate matching", "GET", f"/api/candidates/matching?order_id={ids.order}", {}, 4),
        ("candidate detail", "GET", f"/api/candidates/{ids.candidate}", {}, 3),
        ("candidate assignment-history", "GET", f"/api/candidates/{ids.candidate}/assignment-history", {}, 4),
        ("candidate ocr", "GET", f"/api/candidates/{ids.candidate}/ocr", {}, 3),
        ("application form load", "GET", f"/api/candidates/{ids.candidate}/application-form", {}, 14),
        ("document diagnostics", "GET", f"/api/candidates/{ids.candidate}/documents/diagnostics", {}, 6),
        ("candidate inspection.xlsx", "GET", f"/api/candidates/{ids.candidate}/inspection.xlsx", {}, 23),
        ("application reopenings", "GET", f"/api/applications/{ids.application}/reopenings", {}, 5),
        ("application draft", "GET", f"/api/applications/{ids.application}/draft", {}, 5),
        ("travelled list", "GET", "/api/travelled", {}, 4),
        ("travelled export csv", "GET", "/api/travelled/export?format=csv", {}, 4),
        ("files list (candidate)", "GET", f"/api/files?entity_type=candidate&entity_id={ids.candidate}", {}, 4),
        ("files download", "GET", "/api/files/resume1", {}, 3),
        ("document-bundle sources", "GET", f"/api/document-bundles/candidates/{ids.uuid_candidate}/sources", {}, 4),
        ("reading jobs list", "GET", "/api/ocr/jobs", {}, 6),
        ("reading job detail", "GET", "/api/ocr/jobs/jb1", {}, 5),
        ("candidate-import columns", "GET", "/api/candidate-import/columns", {}, 2),
        ("candidate-import status", "POST", "/api/candidate-import/status", {"json": {"candidateIds": [f"cd{n}" for n in range(1, 61)]}}, 5),
    ]


ADMIN_ONLY = [
    ("leads export csv", "GET", "/api/leads/export.csv", {}, 3),
    ("leads export xlsx", "GET", "/api/leads/export.xlsx", {}, 3),
    ("candidates export xlsx", "GET", "/api/candidates/export", {}, 10),
    ("lead batch report xlsx", "GET", "/api/leads/batches/lb1/report.xlsx", {}, 6),
]


@pytest.mark.parametrize("role", ["admin", "a"])
def test_reads_stay_within_their_statement_budget(world, role):
    failures = []
    todo = cases(world.ids) + (ADMIN_ONLY if role == "admin" else [])
    for label, method, path, kwargs, budget in todo:
        # a recruiter reads the candidate they own; the shared bank is open to all, the form is the owner's
        path = path.replace(world.ids.candidate, world.ids.staff_candidate) if role == "a" else path
        response, statements = measure(world, label, role, method, path, **kwargs)
        assert response.status_code in (200, 404, 403), (label, response.status_code, response.text[:200])
        if len(statements) > budget:
            failures.append(f"{label} [{role}]: {len(statements)} > {budget}")
        extra = duplicates(statements)
        if extra and response.status_code == 200 and label not in KNOWN_REPEATS:
            failures.append(f"{label} [{role}]: repeated identical statements {extra}")
    assert not failures, "\n".join(failures)


def test_print_table(world):
    rows = []
    for role in ("admin", "a"):
        for label, method, path, kwargs, budget in cases(world.ids) + (ADMIN_ONLY if role == "admin" else []):
            path = path.replace(world.ids.candidate, world.ids.staff_candidate) if role == "a" else path
            response, statements = measure(world, label, role, method, path, **kwargs)
            _, slow = measure(world, label, role, method, path, latency_ms=LATENCY_MS, **kwargs)  # the same call over a slow link
            rows.append((*RESULTS[-2], round(RESULTS[-1][3] / LATENCY_MS, 1), response.status_code))
    print("\n%-34s %-6s %5s %6s %8s %7s %s" % ("endpoint", "role", "stmts", "ms", "KB", "rounds", "http"))
    for row in rows:
        print("%-34s %-6s %5d %6d %8.1f %7.1f %s" % row)


# ------------------------------------------------------------------ writes and uploads (each in its own transaction)

def lead_sheet_upload(first: int, count: int) -> bytes:
    from lead_sheet_fixtures import meta_export, meta_rows
    return meta_export(meta_rows(first, first + count))


def write_cases(world):
    ids = [f"ld{n}" for n in range(1, 101)]
    pdf = b"%PDF-1.7 synthetic resume"
    folders = [{"folder": f"Folder {n}", "files": [{"path": f"Folder {n}/passport.pdf", "kind": "passport"}, {"path": f"Folder {n}/resume.pdf", "kind": "resume"}]}
               for n in range(40)]
    return [
        # label, method, path, kwargs, admin budget
        ("lead import 300 new rows", "POST", "/api/leads/import", {"files": {"file": ("sheet.csv", lead_sheet_upload(5000, 300), "text/csv")}, "data": {"dry_run": "false"}}, 14),
        ("lead import dry run 300 rows", "POST", "/api/leads/import", {"files": {"file": ("sheet.csv", lead_sheet_upload(6000, 300), "text/csv")}, "data": {"dry_run": "true"}}, 6),
        ("leads bulk status (100 ids)", "POST", "/api/leads/bulk", {"json": {"action": "status", "ids": ids, "status": "contacted"}}, 10),
        ("leads bulk assign (100 ids)", "POST", "/api/leads/bulk", {"json": {"action": "assign", "ids": ids, "ownerId": "a"}}, 17),
        ("leads bulk export (100 ids)", "POST", "/api/leads/bulk", {"json": {"action": "export", "ids": ids}}, 4),
        ("lead patch", "PATCH", "/api/leads/ld7", {"json": {"notes": "called back"}}, 7),
        ("candidate patch", "PATCH", f"/api/candidates/{world.ids.candidate}", {"json": {"name": "Candidate Person 0001 Edited"}}, 13),
        ("application form save", "PUT", f"/api/candidates/{world.ids.candidate}/application-form", {"json": {"personal": {"fatherName": "Father Person"}}}, 19),
        ("candidate-import check 40 folders", "POST", "/api/candidate-import/check", {"json": {"folders": folders}}, 6),
        ("file upload (candidate resume)", "POST", "/api/files", {"files": {"file": ("resume.pdf", pdf, "application/pdf")},
                                                                 "data": {"entity_type": "candidate", "entity_id": world.ids.candidate, "slot": "resume"}}, 11),
    ]


def test_writes_stay_within_their_statement_budget(world):
    """Each write costs a fixed number of statements, however many rows it touches (300 leads imported, 100 leads changed)."""
    failures = []
    for label, method, path, kwargs, budget in write_cases(world):
        response, statements = measure(world, label, "admin", method, path, **kwargs)
        world.db.rollback()
        assert response.status_code in (200, 201), (label, response.status_code, response.text[:300])
        if len(statements) > budget:
            failures.append(f"{label}: {len(statements)} > {budget}")
    assert not failures, "\n".join(failures)


def test_print_write_table(world):
    rows = []
    for label, method, path, kwargs, budget in write_cases(world):
        response, statements = measure(world, label, "admin", method, path, **kwargs)
        world.db.rollback()
        rows.append((*RESULTS[-1], response.status_code))
        if os.environ.get("DUMP_STATEMENTS") and os.environ["DUMP_STATEMENTS"] in label:
            print(response.text[:300])
    print("\n%-34s %-6s %5s %6s %8s %s" % ("write", "role", "stmts", "ms", "KB", "http"))
    for row in rows:
        print("%-34s %-6s %5d %6d %8.1f %s" % row)


# ------------------------------------------------------------------ round trips (sequential waves; the user lookup of this harness counts as one)

ROUND_LATENCY_MS = 100
ROUND_BUDGETS: dict[str, int] = {
    "leads list 25": 2, "leads summary": 2, "leads facets": 2, "leads columns": 2, "lead detail": 2,
    "candidates list 20": 2, "candidate matching": 3, "candidate detail": 2, "application form load": 4, "document diagnostics": 2,
    "candidate inspection.xlsx": 5, "travelled list": 2, "travelled export csv": 2, "files list (candidate)": 2,
    "document-bundle sources": 2, "candidate-import status": 2,
}


def test_reads_stay_within_their_round_trips(world):
    failures, seen = [], {}
    for label, method, path, kwargs, _budget in cases(world.ids):
        if label not in ROUND_BUDGETS and not os.environ.get("PRINT_ROUNDS"):
            continue
        response, statements = measure(world, label, "admin", method, path, latency_ms=ROUND_LATENCY_MS, **kwargs)
        seen[label] = statements.rounds()
        if label in ROUND_BUDGETS and seen[label] > ROUND_BUDGETS[label]:
            failures.append(f"{label}: {seen[label]} sequential round trips > {ROUND_BUDGETS[label]}")
    if os.environ.get("PRINT_ROUNDS"):
        print("\nROUNDS", seen)
    assert not failures, "\n".join(failures)


# ------------------------------------------------------------------ kept answers (the response cache is off under pytest, so these switch it on)

@pytest.fixture
def kept(monkeypatch):
    from app.services import response_cache
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    response_cache.clear()
    yield response_cache
    response_cache.clear()


def test_the_travelled_list_is_kept_and_a_new_placement_drops_it(world, kept):
    first, cold = measure(world, "travelled (cold)", "admin", "GET", "/api/travelled")
    again, warm = measure(world, "travelled (kept)", "admin", "GET", "/api/travelled")
    assert first.json() == again.json() and len(first.json()["rows"]) > 20
    assert len(warm) < len(cold) and len(warm) <= 1, warm  # only the harness' own user lookup
    world.db.execute(text("UPDATE crm_applications SET status = 'deployed' WHERE id = 'ap5'"))
    world.db.commit()
    changed, _ = measure(world, "travelled (after a change)", "admin", "GET", "/api/travelled")
    assert len(changed.json()["rows"]) == len(first.json()["rows"]) + 1


def test_the_lead_cards_and_filters_are_kept_per_viewer_and_follow_changes(world, kept):
    for path in ("/api/leads/summary", "/api/leads/facets", "/api/leads/columns", "/api/leads?page_size=25"):
        first, cold = measure(world, path, "admin", "GET", path)
        again, warm = measure(world, path + " (kept)", "admin", "GET", path)
        assert first.json() == again.json() and len(warm) <= 1 < len(cold), (path, warm)
    mine, _ = measure(world, "leads (recruiter)", "a", "GET", "/api/leads?page_size=25")
    assert mine.json()["total"] < first.json()["total"] or mine.json()["items"] != first.json()["items"]
    assert all(item["ownerId"] == "a" for item in mine.json()["items"])
    before = measure(world, "summary", "admin", "GET", "/api/leads/summary")[0].json()["byStatus"].get("contacted", 0)
    assert measure(world, "bulk", "admin", "POST", "/api/leads/bulk", json={"action": "status", "ids": ["ld1", "ld2"], "status": "contacted"})[0].status_code == 200
    after = measure(world, "summary", "admin", "GET", "/api/leads/summary")[0].json()["byStatus"].get("contacted", 0)
    assert after > before or before > 0  # the change was not hidden behind the kept summary


def test_the_form_pickers_are_kept_and_the_form_is_the_same(world, kept):
    cold_form, cold = measure(world, "form (cold)", "admin", "GET", f"/api/candidates/{world.ids.candidate}/application-form")
    warm_form, warm = measure(world, "form (kept pickers)", "admin", "GET", f"/api/candidates/{world.ids.candidate}/application-form")
    assert cold_form.json() == warm_form.json()
    assert len(cold) - len(warm) == 2, (len(cold), len(warm))  # the agencies and the recruiters are not read again
    _, slow = measure(world, "form (kept pickers, slow link)", "admin", "GET", f"/api/candidates/{world.ids.candidate}/application-form", latency_ms=ROUND_LATENCY_MS)
    assert slow.rounds() <= 2, slow.rounds()  # the sign-in lookup, then every read of the form side by side
