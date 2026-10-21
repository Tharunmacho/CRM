"""Report scope, event attribution, filters and real file formats; synthetic data only."""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pymupdf
import pytest
from sqlalchemy.orm import Session

from app.api.routers import reports
from app.core.deps import current_user
from app.db.session import get_db
from app.db import models as m
from app.services.reporting import Filters, dashboard_data, read_reporting_data, report_data
from app.services.report_exports import REPORTS, pdf, xlsx
from test_database_naming import schema_database, _migrate

NOW = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
DAY = date(2026, 10, 21)
FILTERS = Filters(DAY, DAY)
STAFF = SimpleNamespace(id="a", role=m.Role.staff)
ADMIN = SimpleNamespace(id="admin", role=m.Role.admin)
MANAGER = SimpleNamespace(id="manager", role=m.Role.manager)


@pytest.fixture
def dataset():
    users = [{"id": uid, "name": uid.upper(), "role": role, "active": True}
             for uid, role in [("a", "staff"), ("b", "staff"), ("admin", "admin"), ("manager", "manager")]]
    candidates = []
    apps = []
    for index, (initial, current, source, stage, status) in enumerate([
        ("a", "b", "email", 9, "active"), ("b", "b", "whatsapp", 4, "closed"),
        ("a", "a", "walk_in", 2, "active"), ("a", "a", "referral", 2, "closed"),
        ("a", "a", "other", 11, "deployed"), ("a", "a", "email", 8, "active")], 1):
        candidates.append({"id": f"c{index}", "ref": f"C{index}", "name": f"Candidate {index}", "phone": str(index),
                           "owner_id": current, "intake_owner_id": initial, "created_at": NOW - timedelta(hours=5),
                           "source": source, "source_detail": "alpha@example.test" if initial == "a" else "hidden@example.test"})
        apps.append({"id": f"app{index}", "candidate_id": f"c{index}", "order_id": "o2" if index == 2 else "o1",
                     "owner_id": initial, "current_stage": stage, "status": status,
                     "closure_reason": "interview_rejected" if index == 2 else "resume_not_received" if index == 4 else None,
                     "created_at": NOW - timedelta(days=20) if index == 1 else NOW - timedelta(hours=4), "updated_at": NOW})
    def stage(id, app, number, amount=None, currency=None, reopened=None):
        return {"id": id, "application_id": app, "stage": number, "attempt": 1, "data": {"result": "selected"} if number == 4 else {},
                "outcome": "Done", "amount": amount, "amount_currency": currency, "owner_id": "b" if app == "app2" else "a",
                "completed_by": "admin", "completed_at": NOW - timedelta(hours=1), "reopened_reason": reopened}
    stages = [stage("receipt-inr", "app1", 8, "100.25", "INR"), stage("receipt-aed", "app5", 8, "70.00", "AED"),
              stage("void-receipt", "app6", 8, "999.00", "INR", "Admin correction"),
              stage("selected", "app5", 4), stage("rejected", "app2", 4), stage("closed", "app4", 2)]
    stages[-2]["data"] = {"result": "rejected"}
    tasks = [{"id": "t1", "title": "Advance", "candidate_id": "c1", "application_id": "app1", "stage": 8,
              "owner_id": "a", "created_at": NOW - timedelta(hours=13), "completed_at": NOW - timedelta(hours=1), "cancelled_at": None,
              "due_at": NOW + timedelta(hours=35), "sla_started_at": None, "escalated": False, "escalated_at": None},
             {"id": "t2", "title": "Resume", "candidate_id": "c3", "application_id": "app3", "stage": 2,
              "owner_id": "a", "created_at": NOW - timedelta(days=5), "completed_at": None, "cancelled_at": None,
              "due_at": NOW + timedelta(hours=3), "sla_started_at": NOW - timedelta(hours=45), "escalated": False, "escalated_at": None},
             {"id": "t3", "title": "Old task", "candidate_id": "c1", "application_id": "app1", "stage": 9,
              "owner_id": "b", "created_at": NOW - timedelta(days=3), "completed_at": None, "cancelled_at": None,
              "due_at": NOW - timedelta(hours=1), "sla_started_at": None, "escalated": True, "escalated_at": NOW},
             {"id": "tm", "title": "Manager task", "candidate_id": "c3", "application_id": None, "stage": None,
              "owner_id": "manager", "created_at": NOW, "completed_at": None, "cancelled_at": None,
              "due_at": NOW + timedelta(hours=1), "sla_started_at": None, "escalated": False, "escalated_at": None}]
    events = [{"id": "ev1", "task_id": "t3", "candidate_id": "c1", "owner_id": "a", "kind": "escalation", "occurred_at": NOW},
              {"id": "ev2", "task_id": "t3", "candidate_id": "c1", "owner_id": "a", "kind": "missed_update", "occurred_at": NOW},
              {"id": "ev3", "task_id": "t3", "candidate_id": "c1", "owner_id": "b", "kind": "escalation", "occurred_at": NOW}]
    return {"users": users, "clients": [{"id": "cl1", "name": "Client 1"}, {"id": "cl2", "name": "Client 2"}],
            "companies": [{"id": "co1", "name": "Employer 1", "client_id": "cl1"}, {"id": "co2", "name": "Employer 2", "client_id": "cl2"}],
            "designations": [{"id": "d1", "title": "Welder"}, {"id": "d2", "title": "Electrician"}],
            "orders": [{"id": f"o{i}", "ref": f"O{i}", "company_id": f"co{i}", "designation_id": f"d{i}", "openings": 5, "status": "open", "created_at": NOW} for i in [1, 2]],
            "candidates": candidates, "applications": apps, "stages": stages, "tasks": tasks, "events": events,
            "remarks": [{"id": "r1", "task_id": "t2", "author_id": "a", "created_at": NOW}]}


def test_staff_reports_retain_initial_and_event_ownership_after_reassignment(dataset):
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    assert result["summary"] == {"applications": 4, "active": 2, "deployed": 1, "intake": 5}
    assert result["accounts"] == [{"account": "alpha@example.test", "count": 2}]
    assert result["staff"] == [{"id": "a", "name": "A", "assigned": 5, "completedTasks": 1,
                                "averageStageHours": 12, "durationSamples": 1, "escalations": 1, "missedUpdates": 1}]
    assert all(row["id"] != "o2" for row in result["orders"])
    assert result["options"]["staff"] == [{"id": "a", "label": "A"}]
    assert result["rejections"] == [{"stage": 2, "reason": "resume_not_received", "label": "Resume not received", "count": 1}]


def test_waiting_is_not_dropoff_and_reopened_receipts_do_not_count(dataset):
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    assert result["pipeline"][1]["active"] == 1
    assert result["pipeline"][1]["closed"] == 1
    assert result["advanceTotals"] == [{"currency": "AED", "amount": "70.00"}, {"currency": "INR", "amount": "100.25"}]
    assert {r["id"] for r in result["advances"]} == {"receipt-inr", "receipt-aed"}
    # The old application still contributes its actual receipt and task event this day.
    assert result["summary"]["applications"] == 4
    assert result["orders"][0]["selected"] == 2


@pytest.mark.parametrize("keyword,match,other", [("client_id", "cl1", "cl2"), ("order_id", "o1", "o2"), ("designation_id", "d1", "d2"), ("staff_id", "a", "b")])
def test_common_filters_apply_to_every_report(dataset, keyword, match, other):
    wanted = report_data(dataset, ADMIN, Filters(DAY, DAY, **{keyword: match}), now=NOW)
    excluded = report_data(dataset, ADMIN, Filters(DAY, DAY, **{keyword: other}), now=NOW)
    assert wanted["summary"]["intake"] == 5
    assert sum(row["count"] for row in excluded["sources"]) == 1
    assert len(excluded["orders"]) == 1
    assert excluded["pipeline"][3]["closed"] == 1
    assert excluded["advanceTotals"] == []
    assert excluded["rejections"][0]["reason"] == "interview_rejected"
    if keyword != "staff_id":
        assert sum(row["escalations"] for row in excluded["staff"]) == 0


def test_date_filter_includes_local_midnight_and_excludes_next_day(dataset):
    dataset["candidates"][0]["created_at"] = datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc)
    dataset["candidates"][-1]["created_at"] = datetime(2026, 10, 21, 18, 30, tzinfo=timezone.utc)
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    assert result["summary"]["intake"] == 4
    assert result["accounts"] == [{"account": "alpha@example.test", "count": 1}]
    empty = report_data(dataset, STAFF, Filters(date(2026, 11, 1), date(2026, 11, 1)), now=NOW)
    assert empty["summary"]["applications"] == 0
    assert empty["advances"] == []
    assert sum(r["completedTasks"] + r["escalations"] for r in empty["staff"]) == 0


def test_source_accounts_resolve_durable_message_provenance(dataset):
    dataset["candidates"][0]["source_detail"] = "email_message:origin"
    dataset["mailSources"] = [{"id": "origin", "address": "original@example.test"}]
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    assert result["accounts"] == [{"account": "alpha@example.test", "count": 1}, {"account": "original@example.test", "count": 1}]


def test_dashboard_current_ownership_deadlines_and_roles(dataset):
    own = dashboard_data(dataset, STAFF, now=NOW)
    assert own["today"]["total"] == 4  # c1 is now B's operational candidate.
    assert own["taskCounts"] == {"open": 1, "dueToday": 1, "overdue": 0, "escalated": 0, "notUpdated": 0}
    assert len(own["pipeline"]) == 11
    assert own["tasks"][0]["id"] == "t2"  # reset due_at wins over original creation+48h.
    manager = dashboard_data(dataset, MANAGER, now=NOW)
    assert manager["today"]["total"] == 0
    assert [row["id"] for row in manager["myTasks"]] == ["tm"]
    admin = dashboard_data(dataset, ADMIN, now=NOW)
    assert [row["id"] for row in admin["tasks"]] == ["t3", "tm", "t2"]
    assert admin["taskCounts"]["escalated"] == 1


def test_late_stage_labels_identify_work_still_due_not_deployment(dataset):
    dataset["applications"][0]["current_stage"] = 10
    dataset["applications"][0]["created_at"] = NOW
    dashboard = dashboard_data(dataset, ADMIN, now=NOW)
    report = report_data(dataset, ADMIN, FILTERS, now=NOW)
    expected = ["Visa arrival", "Visa stamping", "Air ticket"]
    assert [row["label"] for row in dashboard["pipeline"][8:]] == expected
    assert [row["label"] for row in report["pipeline"][8:]] == expected
    assert dashboard["pipeline"][9]["count"] == 1
    assert report["pipeline"][9]["active"] == 1


def make_client(data, user=STAFF):
    app = FastAPI()
    app.include_router(reports.router); app.include_router(reports.dashboard_router)
    app.dependency_overrides[get_db] = lambda: object()
    if user:
        app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/reports?staff_id=b", "/api/reports/advance/export?format=xlsx&staff_id=b", "/api/reports/advance/export?format=pdf&staff_id=b", "/api/dashboard?staff_id=b"])
def test_scope_tampering_is_rejected_before_database_access(dataset, path):
    with patch.object(reports, "read_reporting_data") as read:
        response = make_client(dataset).get(path)
    assert response.status_code == 403
    read.assert_not_called()


def test_reports_require_authentication(dataset):
    assert make_client(dataset, user=None).get('/api/reports').status_code == 401


def test_invalid_dates_and_format_rejected(dataset):
    with patch.object(reports, "read_reporting_data") as read:
        assert make_client(dataset).get('/api/reports?from_date=2026-11-01&to_date=2026-10-01').status_code == 422
        assert make_client(dataset).get('/api/reports/advance/export?format=csv').status_code == 422
    read.assert_not_called()


@pytest.mark.parametrize("report", list(REPORTS))
def test_excel_and_pdf_are_real_readable_formats_with_scope_parity(dataset, report):
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    workbook = xlsx(report, result)
    assert workbook[:2] == b'PK'
    with ZipFile(BytesIO(workbook)) as archive:
        names = archive.namelist()
        assert 'xl/workbook.xml' in names
        for name in names:
            ElementTree.fromstring(archive.read(name))
        text = ''.join(archive.read(name).decode() for name in names)
        assert '2026-10-21' in text
        assert 'Candidate 2' not in text
        assert '999.00' not in text
        if report == 'advance':
            assert '<v>100.25</v>' in text and 'AED' in text and 'INR' in text
    document = pdf(report, result)
    assert document.startswith(b'%PDF')
    with pymupdf.open(stream=document, filetype='pdf') as doc:
        text = ''.join(page.get_text() for page in doc)
        assert '2026-10-21' in text and 'Candidate 2' not in text
        if report == 'advance':
            assert '100.25' in text and '70.00' in text and '999.00' not in text


def test_api_exports_use_the_identical_filter_contract(dataset):
    client = make_client(dataset)
    query = 'from_date=2026-10-21&to_date=2026-10-21&client_id=cl1&order_id=o1&designation_id=d1'
    with patch.object(reports, "read_reporting_data", return_value=dataset):
        result = client.get(f'/api/reports?{query}').json()
        response = client.get(f'/api/reports/advance/export?format=xlsx&{query}')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/vnd.openxmlformats')
    assert response.headers['cache-control'] == 'private, no-store'
    with ZipFile(BytesIO(response.content)) as archive:
        text = ''.join(archive.read(name).decode() for name in archive.namelist())
    for row in result['advanceTotals']:
        assert f'<v>{row["amount"]}</v>' in text


def test_real_postgres_report_contract_and_export_authorization(schema_database, dataset):
    """Uses only the explicitly guarded loopback crm_schema_test_reports database."""
    _migrate(schema_database, 'head')
    with Session(schema_database) as db:
        for row in dataset['users']:
            db.add(m.User(**row, email=f'{row["id"]}@example.test'))
        db.flush()
        for key, model in [('clients', m.Client), ('companies', m.Company), ('designations', m.Designation),
                           ('orders', m.Order), ('candidates', m.Candidate), ('applications', m.Application),
                           ('stages', m.ApplicationStage), ('tasks', m.Task)]:
            db.add_all(model(**row, **({'kind': 'stage' if row['stage'] else 'initial_call'} if key == 'tasks' else {})) for row in dataset[key]); db.flush()
        db.add_all(m.TaskEvent(**row, event_key=row['id']) for row in dataset['events'])
        db.add_all(m.Remark(**row, body='Daily update') for row in dataset['remarks'])
        db.commit()
        loaded = read_reporting_data(db)
        actual = report_data(loaded, STAFF, FILTERS, now=NOW)
        assert actual['staff'][0]['escalations'] == 1
        assert actual['summary']['intake'] == 5
        assert actual['advanceTotals'] == [{'currency': 'AED', 'amount': '70.00'}, {'currency': 'INR', 'amount': '100.25'}]
        app = FastAPI(); app.include_router(reports.router)
        app.dependency_overrides[current_user] = lambda: STAFF
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        query = 'from_date=2026-10-21&to_date=2026-10-21&order_id=o1'
        assert client.get('/api/reports?staff_id=b').status_code == 403
        response = client.get('/api/reports?' + query)
        assert response.status_code == 200
        assert response.json()['summary']['intake'] == 5
        exported = client.get('/api/reports/advance/export?format=pdf&' + query)
        assert exported.status_code == 200
        with pymupdf.open(stream=exported.content, filetype='pdf') as document:
            text = ''.join(page.get_text() for page in document)
            assert 'Candidate 2' not in text and '100.25' in text
