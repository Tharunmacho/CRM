"""Dashboard transfer cost must not grow with completed work and stage documents."""
from datetime import datetime, timedelta, timezone
import json

from app.db import models as m
from app.services.reporting import Filters, dashboard_data, read_dashboard_data, read_reporting_data, report_data
from test_attendance_read_performance import measured
from test_database_naming import schema_database
from test_workflow_scheduler import workspace

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def test_dashboard_retains_counts_without_transferring_historical_details(workspace):
    with workspace() as db:
        db.add(m.Client(id="client", name="Client")); db.flush()
        db.add(m.Company(id="company", name="Employer", client_id="client"))
        db.add(m.Designation(id="designation", title="Welder")); db.flush()
        db.add(m.Order(id="order", ref="ORD-PERF", company_id="company", designation_id="designation", openings=10))
        db.flush()
        db.add(m.Application(id="application", candidate_id="candidate", order_id="order", owner_id="owner",
            current_stage=5, status="active", created_at=NOW - timedelta(days=30)))
        db.flush()
        for number in range(100):
            finished = NOW - timedelta(days=10, minutes=number)
            db.add(m.ApplicationStage(application_id="application", stage=2, attempt=number + 1,
                data={"remarks": "Synthetic document detail " * 200}, outcome="Received", owner_id="owner",
                completed_by="owner", completed_at=finished))
            db.add(m.Task(id=f"old-task-{number}", kind=m.TaskKind.stage, title="Completed stage task",
                candidate_id="candidate", application_id="application", stage=2, owner_id="owner",
                created_at=finished - timedelta(hours=1), completed_at=finished))
        db.add(m.ApplicationStage(application_id="application", stage=4, attempt=1,
            data={"result": "selected", "remarks": "Private stage detail " * 500}, owner_id="owner", completed_by="owner", completed_at=NOW))
        db.add(m.Task(id="open-task", kind=m.TaskKind.stage, title="Offer letter", candidate_id="candidate",
            application_id="application", stage=5, owner_id="owner", created_at=NOW - timedelta(hours=1), due_at=NOW + timedelta(hours=47)))
        db.flush()
        for number in range(100):
            db.add(m.TaskEvent(task_id=f"old-task-{number}", candidate_id="candidate", owner_id="owner",
                kind="escalation", event_key=f"perf-event-{number}", occurred_at=NOW - timedelta(days=12)))
            db.add(m.Remark(task_id=f"old-task-{number}", author_id="owner", body="Historical owner update",
                created_at=NOW - timedelta(days=12)))
        db.commit()
        actor = db.get(m.User, "admin")
        with measured(db) as full_measurement:
            full = read_reporting_data(db)
        with measured(db) as desk_measurement:
            desk = read_dashboard_data(db, actor, now=NOW)
        assert desk_measurement["queries"] == full_measurement["queries"] == 1
        assert dashboard_data(desk, actor, now=NOW) == dashboard_data(full, actor, now=NOW)
        full_bytes = len(json.dumps(full).encode())
        desk_bytes = len(json.dumps(desk).encode())
        assert desk_bytes < full_bytes / 20
        assert len(desk["tasks"]) == len(desk["stages"]) == 1
        assert desk["remarks"] == []
        period = Filters(NOW.date(), NOW.date())
        with measured(db) as report_measurement:
            pruned_report = read_reporting_data(db, period)
        assert report_measurement["queries"] == 1
        assert report_data(pruned_report, actor, period, now=NOW) == report_data(full, actor, period, now=NOW)
        report_bytes = len(json.dumps(pruned_report).encode())
        assert report_bytes < full_bytes / 20
        assert len(pruned_report["stages"]) == 2
        assert pruned_report["events"] == pruned_report["tasks"] == pruned_report["remarks"] == []
        print({"dashboard_previous_read": {**full_measurement, "bytes": full_bytes},
               "dashboard_operational_read": {**desk_measurement, "bytes": desk_bytes},
               "reports_period_read": {**report_measurement, "bytes": report_bytes}})
