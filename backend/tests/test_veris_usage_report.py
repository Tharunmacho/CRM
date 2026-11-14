"""Veris usage report: exact numbers on a small, realistic seeded database (scratch PostgreSQL only, see TEST_DATABASE_URL)."""
import csv
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.models import (Candidate, CandidateStatus, DocumentExtractionJob, DocumentPersonGroup, EmailIngestionJob, MailAccount, MailAttachment,
    MailMessage, MultiPersonIntake, Source)
from app.integrations.ocr.client import plan_chunks
from scripts import veris_usage_report as report
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture)

PHONES = ("9000000011", "9000000022", "9000000033")


def at(month, day):
    return datetime(2026, month, day, 10, 0, tzinfo=timezone.utc)


def leaf(job_id, pages, status="succeeded", generation=0, key=None):
    return {"job_id": job_id, "selected_pages": pages, "status": status, "retry_generation": generation, "idempotency_key": key or "k-" + job_id}


def state(jobs=None, history=None, total=None, ignored=(), unresolved=(), sent=()):
    value = {"provider_jobs": jobs or {}}
    if history:
        value["provider_job_history"] = history
    if total is not None:
        value["coverage"] = {"pages": {"total": total, "sent": list(sent), "ignored": list(ignored), "unresolved": list(unresolved)}}
        value["inspection"] = {"page_count": total}
    return value


def job(identifier, group, kind, candidate, created, status="completed", content="", name=None, owner="owner", **fields):
    return DocumentExtractionJob(id=identifier, owner_id=owner, group_id=group, kind=kind, filename=name or f"{identifier}.pdf",
                                 content_type="application/pdf", object_key="unused/" + identifier, content_hash=content or identifier.ljust(64, "0"),
                                 size_kb=1, status=status, candidate_id=candidate, created_at=created, **fields)


@pytest.fixture
def seeded(workspace):
    chunks = plan_chunks(list(range(1, 46)), 20, 1)
    assert [len(c) for c in chunks] == [15, 16, 16]  # a 45-page resume: three jobs, two overlap pages
    with workspace() as db:
        db.add_all([
            Candidate(id="cand-a", ref="CND-A", care_id="CI-26-27-101", traveller_id="CI-T-26-27-101", name="Asha Traveller", phone=PHONES[0],
                      source=Source.walk_in, owner_id="owner", status=CandidateStatus.deployed),
            Candidate(id="cand-b", ref="CND-B", care_id="CI-26-27-102", name="Bala Current", phone=PHONES[1], source=Source.walk_in,
                      owner_id="owner", status=CandidateStatus.in_process),
            Candidate(id="cand-c", ref="CND-C", care_id="CI-26-27-103", name="Chitra Current", phone=PHONES[2], source=Source.walk_in,
                      owner_id="other", status=CandidateStatus.new),
        ])
        db.flush()
        same = "a" * 64
        db.add_all([
            # J1: a single-job passport (page 2 of a 5-page file; the other four pages stayed with the local OCR)
            job("j1", "g1", "passport", "cand-a", at(3, 10), content=same, name="passport A1234567 asha@example.test.pdf",
                state=state({"passport": leaf("v-p1", [2])}, total=5, ignored=[1, 3, 4, 5], sent=[2])),
            # J7: the same content uploaded again; it reuses the checkpoint, so Veris is not called again
            job("j7", "g7", "passport", "cand-a", at(3, 12), content=same, state=state({"passport": leaf("v-p1", [2])}, total=5, ignored=[1, 3, 4, 5], sent=[2])),
            # J2: a 45-page resume read as three chunks with one overlap page each
            job("j2", "g2", "resume", "cand-b", at(3, 15), state=state({"resume": {
                "selected_pages": list(range(1, 46)), "status": "succeeded",
                "chunks": [leaf(f"v-r{i}", pages) for i, pages in enumerate(chunks, 1)]}}, total=45, sent=list(range(1, 46)))),
            # J3: read by the local OCR only, nothing sent
            job("j3", "g3", "resume", "cand-b", at(4, 2), state=state({}, total=3)),
            # J8: an Aadhaar page whose candidate is only known through its upload group (g2); no page count saved
            job("j8", "g2", "aadhaar", None, at(3, 16), status="needs_review", state=state({"aadhaar": leaf("v-a1", [1])})),
            # J4: a passport page that failed and was sent again under a new key (retry generation 1)
            job("j4", "g4", "passport", "cand-c", at(4, 20), state=state(
                {"passport": {"selected_pages": [1], "status": "succeeded", "chunks": [leaf("v-p3", [1], generation=1)]}},
                history={"passport": [{"selected_pages": [1], "chunks": [leaf("v-p2", [1], status="failed")]}]}, total=2, unresolved=[2], sent=[1])),
            # J5: an abandoned draft: no candidate at all (excluded, but listed)
            job("j5", "g5", "resume", None, at(4, 21), state=state({"resume": leaf("v-x1", [1, 2])}, total=2, sent=[1, 2])),
            # J6: dismissed even though it belongs to a candidate (excluded)
            job("j6", "g6", "resume", "cand-a", at(4, 22), status="dismissed", state=state({"resume": leaf("v-d1", [1])}, total=1, sent=[1])),
        ])
        db.add(MailAccount(id="acct", address="hr@example.test", provider="imap"))
        db.flush()
        db.add(MailMessage(id="msg", account_id="acct", provider_message_id="p1", received_at=at(4, 5), candidate_id="cand-a"))
        db.flush()
        db.add(MailAttachment(id="att", message_id="msg", file_name="resume.pdf", kind="resume", content_hash="e" * 64))
        db.add(EmailIngestionJob(id="email-job", message_id="msg", status="completed", candidate_id="cand-a", created_at=at(4, 5),
                                 state={"att": state({"resume": leaf("v-e1", [1, 2, 3])}, total=3, sent=[1, 2, 3])}))
        # a multi-person review: the pages of one person re-read for candidate C
        db.add(MultiPersonIntake(id="bundle", source_type="manual", source_id="g9", created_by="admin", status="completed", created_at=at(4, 6),
                                 sources=[{"id": "s1", "name": "family.pdf", "hash": "f" * 64, "group_extractions": {
                                     "grp": {"finished": True, "state": state({"resume": leaf("v-s1", [1, 2])}, total=4, sent=[1, 2])}}}]))
        db.flush()
        db.add(DocumentPersonGroup(id="grp", bundle_id="bundle", ordinal=1, label="Person 1", candidate_id="cand-c", status="committed"))
        db.commit()
    return workspace


def collected(engine, **kwargs):
    with engine.connect() as conn:
        report.begin_read_only(conn)
        data = report.collect(conn, **kwargs)
        conn.rollback()
    return data


def section(rows, title):
    """{metric label: [values]} for the table that starts with `title` on the Summary sheet."""
    start = next(i for i, row in enumerate(rows) if row and str(row[0]).startswith(title))
    found = {}
    for row in rows[start + 2:]:
        if not row:
            break
        found[row[0]] = row[1:]
    return found


def test_billable_local_cohort_and_excluded_numbers(seeded):
    engine = seeded().get_bind()
    data = collected(engine)
    sheets = report.build_sheets(data)
    summary = sheets["Summary"]

    billable = section(summary, "A. BILLABLE")           # resume, passport, aadhaar, total
    assert billable["Files that had pages sent to Veris"] == [3, 2, 1, 6]
    assert billable["Veris jobs submitted (each chunk counted separately)"] == [5, 3, 1, 9]
    assert billable["Pages sent to Veris - including chunk-overlap and retried pages"] == [52, 3, 1, 56]
    assert billable["Pages sent to Veris - distinct (overlap and retries counted once)"] == [50, 2, 1, 53]
    assert billable["  pages sent more than once (overlap + retries)"] == [2, 1, 0, 3]
    assert billable["Jobs retried or re-sent (possible double billing)"] == [0, 2, 0, 2]
    assert billable["  pages in those retried jobs"] == [0, 2, 0, 2]
    assert billable["Veris jobs: succeeded"] == [5, 2, 1, 8]
    assert billable["Veris jobs: failed or timed out"] == [0, 1, 0, 1]

    cohort = section(summary, "B. BILLABLE")             # Travelled, Current, total
    assert cohort["Files that had pages sent to Veris"] == [2, 4, 6]
    assert cohort["Veris jobs submitted (each chunk counted separately)"] == [2, 7, 9]
    assert cohort["Pages sent to Veris - including chunk-overlap and retried pages"] == [4, 52, 56]
    assert cohort["Pages sent to Veris - distinct (overlap and retries counted once)"] == [4, 49, 53]

    local = section(summary, "C. LOCAL OCR ONLY")        # Travelled, Current, total: never added to the billable figures
    assert local["Uploaded files (documents read)"] == [3, 5, 8]
    assert local["Unique files (distinct content)"] == [2, 5, 7]
    assert local["Files read by local OCR only (no Veris job)"] == [0, 1, 1]
    assert local["Files that reused another file's Veris jobs (no new job)"] == [1, 0, 1]
    assert local["Pages in files (where the page count is known)"] == [13, 54, 67]
    assert local["Pages not sent to Veris (local OCR / ignored)"] == [8, 6, 14]
    assert local["  of which ignored (blank / not a resume, passport or Aadhaar page)"] == [8, 0, 8]
    assert local["  of which could not be identified"] == [0, 1, 1]
    assert local["Files whose page count is unknown (left out of the page figures)"] == [0, 1, 1]
    assert local["Files: completed"] == [3, 4, 7] and local["Files: needs review"] == [0, 1, 1]

    # files = sent + local only + reused only (nothing is lost, nothing is billable twice)
    assert 6 + 1 + 1 == 8

    # excluded: listed, never in the totals above
    text_rows = [" | ".join(map(str, row)) for row in summary]
    assert any("not linked to a candidate | 1 files | 1 sent to Veris | 1 Veris jobs | 2 pages sent" in row for row in text_rows)
    assert any("dismissed | 1 files | 1 sent to Veris | 1 Veris jobs | 1 pages sent" in row for row in text_rows)
    excluded = sheets[report.EXCLUDED_SHEET]
    assert excluded[0][0].startswith(report.EXCLUDED_TITLE)
    assert sorted(row[0] for row in excluded[2:-1]) == ["dismissed", "not linked to a candidate"]
    assert excluded[-1][4:8] == [3, 3, 2, 0]             # pages incl, distinct, jobs, retried (v-x1 + v-d1)
    assert all("Asha" not in str(cell) for row in excluded for cell in row)


def test_per_candidate_per_month_and_per_file(seeded):
    sheets = report.build_sheets(collected(seeded().get_bind()))
    candidates = {row[0]: row for row in sheets["Per candidate"][1:-1]}
    head = sheets["Per candidate"][0]
    assert set(candidates) == {"Asha Traveller", "Bala Current", "Chitra Current"}  # candidate "Synthetic candidate" has no documents
    cell = lambda name, column: candidates[name][head.index(column)]
    asha, bala, chitra = "Asha Traveller", "Bala Current", "Chitra Current"
    assert (cell(asha, "Care India ID"), cell(asha, "Traveller ID"), cell(asha, "Cohort")) == ("CI-26-27-101", "CI-T-26-27-101", report.TRAVELLED)
    assert [cell(asha, c) for c in ("Files (all)", "Files sent to Veris", "Files local OCR only (not billed)")] == [3, 2, 0]
    assert [cell(asha, c) for c in ("Resume jobs", "Passport jobs", "Aadhaar jobs", "Total pages (incl. overlap)")] == [1, 1, 0, 4]
    assert [cell(bala, c) for c in ("Files (all)", "Files sent to Veris", "Files local OCR only (not billed)")] == [3, 2, 1]
    assert [cell(bala, c) for c in ("Resume jobs", "Resume pages (incl. overlap)", "Resume pages (distinct)", "Aadhaar jobs", "Total pages (distinct)")] == [3, 47, 45, 1, 46]
    assert cell(bala, "Pages not sent (local, not billed)") == 3  # j3's three pages; j8 has no page count, so it adds nothing
    assert [cell(chitra, c) for c in ("Files (all)", "Files sent to Veris", "Passport jobs", "Resume jobs", "Total jobs", "Jobs retried / re-sent")] == [2, 2, 2, 1, 3, 2]
    assert sheets["Per candidate"][-1][head.index("Total jobs")] == 9

    months = {row[0]: row for row in sheets["Per month"][1:]}
    mhead = sheets["Per month"][0]
    assert [months["2026-03"][mhead.index(c)] for c in ("Files (all)", "Files sent to Veris", "Veris jobs")] == [4, 3, 5]
    assert [months["2026-04"][mhead.index(c)] for c in ("Files (all)", "Files sent to Veris", "Veris jobs")] == [4, 3, 4]
    assert months["Total"][mhead.index("Veris jobs")] == 9

    files = sheets["Per file"]
    fhead = files[0]
    by_name = {}
    for row in files[1:]:
        by_name.setdefault(row[0], []).append(row)
    assert len(files) - 1 == 8
    passport = next(row for row in by_name[asha] if row[fhead.index("Created")] == "2026-03-10")
    assert "A1234567" not in passport[fhead.index("File name")] and "example.test" not in passport[fhead.index("File name")]
    assert [passport[fhead.index(c)] for c in ("Pages in file", "Veris jobs (billable)", "Passport pages sent")] == [5, 1, 1]
    again = next(row for row in by_name[asha] if row[fhead.index("Created")] == "2026-03-12")
    assert [again[fhead.index(c)] for c in ("Veris jobs (billable)", "Reused another file's jobs", "Same content seen in files")] == [0, "yes", 2]
    resume = next(row for row in by_name[bala] if row[fhead.index("Resume pages sent")] == 47)
    assert [resume[fhead.index(c)] for c in ("Pages in file", "Veris jobs (billable)", "Distinct pages sent")] == [45, 3, 45]
    assert next(row for row in by_name[bala] if row[fhead.index("Linked via")] == "same upload group")[fhead.index("Aadhaar pages sent")] == 1
    assert next(row for row in by_name[bala] if row[fhead.index("Local OCR only (not billed)")] == "yes")[fhead.index("Veris jobs (billable)")] == 0


def test_date_filter_and_no_personal_data_in_any_output(seeded, tmp_path):
    data = collected(seeded().get_bind(), date_from=date(2026, 4, 1), date_to=date(2026, 4, 30))
    sheets = report.build_sheets(data)
    total = section(sheets["Summary"], "A. BILLABLE")
    assert total["Veris jobs submitted (each chunk counted separately)"][3] == 4  # v-p2, v-p3, v-e1, v-s1
    paths = report.write_report(report.build_sheets(collected(seeded().get_bind())), tmp_path, "csv")
    blob = "\n".join(Path(p).read_text(encoding="utf-8-sig") for p in paths)
    for secret in (*PHONES, "A1234567", "example.test", "hr@"):
        assert secret not in blob
    assert "Asha Traveller" in blob and "CI-T-26-27-101" in blob  # the per-file / per-candidate sheets do carry the name and IDs
    names = {Path(p).name.split("-", 4)[-1] for p in paths}
    assert any(name.startswith("excluded-not-linked") for name in names) and any(name.startswith("per-candidate") for name in names)
    with open(next(p for p in paths if "summary" in str(p)), newline="", encoding="utf-8-sig") as handle:
        assert next(csv.reader(handle))[0].startswith("VERIS OCR USAGE")


def test_xlsx_output_when_openpyxl_is_installed(seeded, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    (path,) = report.write_report(report.build_sheets(collected(seeded().get_bind())), tmp_path, "xlsx")
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["Summary", "Per candidate", "Per file", "Per month", "Per recruiter", report.EXCLUDED_SHEET, "Notes"]


def test_the_report_cannot_write(seeded):
    url = os.environ["TEST_DATABASE_URL"]
    engine = read_only = report.read_only_engine(url)
    with engine.connect() as conn:
        report.begin_read_only(conn)
        with pytest.raises(report.WriteRefused):
            conn.execute(text("UPDATE crm_candidates SET name = 'x'"))
        conn.rollback()
    # even a statement that slips past the text guard is refused by the server: the transaction is read only
    with seeded().get_bind().connect() as conn:
        report.begin_read_only(conn)
        with pytest.raises(Exception, match="read-only"):
            conn.exec_driver_sql("UPDATE crm_candidates SET name = 'x'")
        conn.rollback()
    with seeded() as db:
        assert db.get(Candidate, "cand-a").name == "Asha Traveller"
    read_only.dispose()
