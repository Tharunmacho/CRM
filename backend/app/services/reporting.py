"""Authorized operational reports with explicit event dates and ownership.

No aggregate comes from the browser's shared Data Bank. Dates use the agency's
timezone, and every report applies the same organizational filters. Waiting in a
stage is not a rejection. Historical task events retain the owner at the event.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import mean
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import JSON, func, or_, select

from app.db import models as m
from app.domain.stages import STAGES as WORKFLOW_STAGES


STAGES = [stage.title for stage in WORKFLOW_STAGES]
REASONS = {"prescreen_failed": "Prescreen failed", "not_interested": "Not interested",
           "resume_not_received": "Resume not received", "interview_rejected": "Interview rejected",
           "medical_unfit": "Medical unfit", "unmapped": "Unmapped"}
SOURCE_LABELS = {"email": "Email", "whatsapp": "WhatsApp", "walk_in": "Walk-in",
                 "referral": "Referral", "other": "Other"}


def value(raw):
    return getattr(raw, "value", raw)


def aware(raw):
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw


def owner(row, snapshot="owner_id", fallback=None):
    return row.get(snapshot) or (fallback or {}).get("owner_id")


@dataclass(frozen=True)
class Filters:
    from_date: date
    to_date: date
    client_id: str | None = None
    order_id: str | None = None
    designation_id: str | None = None
    staff_id: str | None = None

    def authorize(self, user):
        if self.from_date > self.to_date:
            raise HTTPException(422, "The start date must be on or before the end date.")
        if value(user.role) == "staff" and self.staff_id not in (None, user.id):
            raise HTTPException(403, "Staff can report only their own numbers.")
        return Filters(self.from_date, self.to_date, self.client_id, self.order_id,
                       self.designation_id, user.id if value(user.role) == "staff" else self.staff_id)


def read_reporting_data(db, filters: Filters | None = None, zone="Asia/Kolkata"):
    """Read report inputs without changing historical attribution.

    An authorized period bounds events and execution tasks. Latest effective
    stages are selected BEFORE date filtering, preserving superseded receipts.
    The SQL projection has exact full-response parity with the original reader
    across 60 real-PostgreSQL role/date/relation cases in
    test_reporting_read_projection, including reopened attempts and old tasks
    referenced by current events. Omitting filters preserves the original reader.
    """
    fields = {
        "users": (m.User, "id name role active"),
        "clients": (m.Client, "id name"),
        "companies": (m.Company, "id client_id name"),
        "designations": (m.Designation, "id title"),
        "orders": (m.Order, "id ref company_id designation_id openings status created_at"),
        "candidates": (m.Candidate, "id ref name owner_id intake_owner_id created_at source source_detail"),
        "applications": (m.Application, "id candidate_id order_id owner_id current_stage status closure_reason created_at updated_at"),
        "stages": (m.ApplicationStage, "id application_id stage attempt data outcome amount amount_currency owner_id completed_by completed_at reopened_reason"),
        "tasks": (m.Task, "id title candidate_id application_id stage owner_id created_at completed_at cancelled_at due_at sla_started_at escalated escalated_at"),
        "events": (m.TaskEvent, "id task_id candidate_id owner_id kind occurred_at"),
        "remarks": (m.Remark, "id task_id author_id created_at"),
    }
    # One statement sees a consistent committed snapshot across all tables and
    # avoids twelve round trips to a remotely hosted PostgreSQL instance.
    if filters is not None:
        tz = ZoneInfo(zone)
        start = datetime.combine(filters.from_date, time.min, tzinfo=tz)
        end = datetime.combine(filters.to_date + timedelta(days=1), time.min, tzinfo=tz)
        events_in_period = (m.TaskEvent.occurred_at >= start, m.TaskEvent.occurred_at < end)
        event_tasks = select(m.TaskEvent.task_id).where(*events_in_period)
    collections = []
    for name, (model, names) in fields.items():
        columns = [getattr(model, field) for field in names.split()]
        if filters is not None and name == "stages":
            columns = [func.json_build_object("result", model.data["result"], "amount", model.data["amount"],
                "currency", model.data["currency"]).label("data") if field.key == "data" else field for field in columns]
        query = select(*columns)
        if filters is not None:
            if name == "events":
                query = query.where(*events_in_period)
            elif name == "tasks":
                # Do not filter by current task owner: historical stage/event
                # ownership can differ after reassignment.
                query = query.where(or_((m.Task.completed_at >= start) & (m.Task.completed_at < end), m.Task.id.in_(event_tasks)))
            elif name == "stages":
                query = (query.where(or_(model.reopened_reason.is_(None), model.reopened_reason == ""))
                    .distinct(model.application_id, model.stage)
                    .order_by(model.application_id, model.stage, model.attempt.desc(), model.completed_at.desc()))
            elif name == "remarks":
                # Report missed-update totals use persisted events, not remarks.
                query = query.where(False)
        rows = query.subquery(name)
        collections.append(select(func.json_agg(func.row_to_json(rows.table_valued()), type_=JSON)).scalar_subquery().label(name))
    mail = select(m.MailMessage.id, m.MailAccount.address).join(m.MailAccount, m.MailAccount.id == m.MailMessage.account_id)
    if filters is not None:
        # Account attribution follows candidate intake date, never message date.
        # Other messages cannot affect this period's source-account totals.
        mail = mail.where(select(m.Candidate.id).where(m.Candidate.source == m.Source.email,
            m.Candidate.created_at >= start, m.Candidate.created_at < end,
            m.Candidate.source_detail == "email_message:" + m.MailMessage.id).exists())
    mail = mail.subquery("mail_sources")
    collections.append(select(func.json_agg(func.row_to_json(mail.table_valued()), type_=JSON)).scalar_subquery().label("mailSources"))
    return {key: rows or [] for key, rows in db.execute(select(*collections)).mappings().one().items()}


def read_dashboard_data(db, user, staff_id=None, zone="Asia/Kolkata", now=None):
    """One fresh projection of operational data, excluding historical report input."""
    if value(user.role) != "admin" and staff_id not in (None, user.id):
        raise HTTPException(403, "Only administrators can change dashboard staff scope.")
    scope = staff_id if value(user.role) == "admin" else user.id
    today = aware(now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone)).date()
    start = datetime.combine(today, time.min, ZoneInfo(zone))
    end = datetime.combine(today + timedelta(days=1), time.min, ZoneInfo(zone))
    open_tasks = (m.Task.completed_at.is_(None), m.Task.cancelled_at.is_(None))
    if scope:
        open_tasks += (m.Task.owner_id.in_([scope, user.id]),)
    apps = select(m.Application.id)
    if scope:
        apps = apps.where(m.Application.candidate_id.in_(select(m.Candidate.id).where(m.Candidate.owner_id == scope)))
    fields = {
        "users": (m.User, "id name"), "clients": (m.Client, "id name"),
        "companies": (m.Company, "id client_id name"), "designations": (m.Designation, "id title"),
        "orders": (m.Order, "id ref company_id designation_id openings status created_at"),
        "candidates": (m.Candidate, "id name owner_id created_at source"),
        "applications": (m.Application, "id candidate_id order_id current_stage status created_at"),
        "tasks": (m.Task, "id title candidate_id application_id stage owner_id created_at due_at sla_started_at escalated"),
        "remarks": (m.Remark, "task_id author_id created_at"),
    }
    collections = []
    for name, (model, names) in fields.items():
        query = select(*(getattr(model, field) for field in names.split()))
        if name == "applications":
            query = query.where(m.Application.id.in_(apps))
        elif name == "tasks":
            query = query.where(*open_tasks)
        elif name == "remarks":
            query = query.join(m.Task, m.Task.id == m.Remark.task_id).where(*open_tasks,
                m.Remark.author_id == m.Task.owner_id, m.Remark.created_at >= start, m.Remark.created_at < end)
        rows = query.subquery(name)
        collections.append(select(func.json_agg(func.row_to_json(rows.table_valued()), type_=JSON)).scalar_subquery().label(name))
    # Only the effective interview result affects the dashboard's selected count.
    # Never transfer stage attachments, remarks, receipts or other stage payloads.
    stages = (select(m.ApplicationStage.application_id, m.ApplicationStage.stage, m.ApplicationStage.attempt,
        func.json_build_object("result", m.ApplicationStage.data["result"].astext).label("data"), m.ApplicationStage.completed_at)
        .where(m.ApplicationStage.application_id.in_(apps), m.ApplicationStage.stage == 4,
            (m.ApplicationStage.reopened_reason.is_(None)) | (m.ApplicationStage.reopened_reason == ""))
        .distinct(m.ApplicationStage.application_id)
        .order_by(m.ApplicationStage.application_id, m.ApplicationStage.attempt.desc(), m.ApplicationStage.completed_at.desc())
        .subquery("stages"))
    collections.append(select(func.json_agg(func.row_to_json(stages.table_valued()), type_=JSON)).scalar_subquery().label("stages"))
    return {key: rows or [] for key, rows in db.execute(select(*collections)).mappings().one().items()}


def _effective_stages(stages):
    effective = defaultdict(dict)
    for row in stages:
        if row.get("reopened_reason"):
            continue
        current = effective[row["application_id"]].get(row["stage"])
        if current is None or (row["attempt"], aware(row["completed_at"])) > (current["attempt"], aware(current["completed_at"])):
            effective[row["application_id"]][row["stage"]] = row
    return effective


def _order_rows(orders, cohort, companies, clients, designations, effective, staff_id, inside):
    by_order = defaultdict(list)
    for app in cohort:
        by_order[app["order_id"]].append(app)
    output = []
    for order in orders:
        order_apps = by_order[order["id"]]
        if not order_apps and (staff_id or not inside(order["created_at"])):
            continue
        company = companies.get(order["company_id"], {})
        output.append({"id": order["id"], "ref": order["ref"], "company": company.get("name", ""),
            "client": clients.get(company.get("client_id"), {}).get("name", ""),
            "designation": designations.get(order["designation_id"], {}).get("title", ""),
            "status": value(order["status"]), "openings": order["openings"], "matched": len(order_apps),
            "selected": sum((effective.get(a["id"], {}).get(4, {}).get("data") or {}).get("result") == "selected"
                or a["current_stage"] > 4 or value(a["status"]) == "deployed" for a in order_apps),
            "deployed": sum(value(a["status"]) == "deployed" for a in order_apps)})
    return output


def report_data(data, user, filters: Filters, zone="Asia/Kolkata", now=None):
    filters = filters.authorize(user)
    tz = ZoneInfo(zone)
    now = aware(now or datetime.now(timezone.utc))
    start = datetime.combine(filters.from_date, time.min, tzinfo=tz)
    end = datetime.combine(filters.to_date + timedelta(days=1), time.min, tzinfo=tz)
    inside = lambda raw: bool(raw and start <= aware(raw) < end)
    staff_matches = lambda who: not filters.staff_id or who == filters.staff_id
    candidates = {row["id"]: row for row in data["candidates"]}
    companies = {row["id"]: row for row in data["companies"]}
    clients = {row["id"]: row for row in data["clients"]}
    designations = {row["id"]: row for row in data["designations"]}
    users = {row["id"]: row for row in data["users"]}
    orders = {row["id"]: row for row in data["orders"]}
    applications = {row["id"]: row for row in data["applications"]}
    # A repeated stage contributes once to its current effective result.
    effective = _effective_stages(data["stages"])

    def order_matches(order):
        if not order:
            return False
        company = companies.get(order["company_id"], {})
        return (not filters.client_id or company.get("client_id") == filters.client_id) and (
            not filters.order_id or order["id"] == filters.order_id) and (
            not filters.designation_id or order["designation_id"] == filters.designation_id)

    matching_orders = {key for key, order in orders.items() if order_matches(order)}
    related = {app["candidate_id"] for app in applications.values() if app["order_id"] in matching_orders}
    relational_filter = bool(filters.client_id or filters.order_id or filters.designation_id)
    candidate_matches = lambda cid: not relational_filter or cid in related
    app_owner = lambda app: owner(app, fallback=candidates.get(app["candidate_id"]))
    app_scope = [app for app in applications.values() if app["order_id"] in matching_orders and staff_matches(app_owner(app))]
    cohort = [app for app in app_scope if inside(app["created_at"])]
    intake = [c for c in candidates.values() if inside(c["created_at"]) and candidate_matches(c["id"])
              and staff_matches(owner(c, "intake_owner_id", c))]

    pipeline = []
    for no, title in enumerate(STAGES, 1):
        reached = [app for app in cohort if app["current_stage"] >= no or no in effective.get(app["id"], {})]
        closed = sum(value(app["status"]) == "closed" and app["current_stage"] == no for app in reached)
        waiting = sum(value(app["status"]) == "active" and app["current_stage"] == no for app in reached)
        passed = sum(app["current_stage"] > no or value(app["status"]) == "deployed" for app in reached)
        pipeline.append({"stage": no, "label": title, "reached": len(reached), "active": waiting,
                         "progressed": passed, "closed": closed,
                         "dropOffPercent": round(closed / len(reached) * 100, 1) if reached else 0})

    def task_matches(task):
        if not staff_matches(task["owner_id"]):
            return False
        app = applications.get(task.get("application_id"))
        return app["order_id"] in matching_orders if app else candidate_matches(task["candidate_id"])

    scoped_tasks = [t for t in data["tasks"] if task_matches(t)]
    task_by_id = {t["id"]: t for t in data["tasks"]}
    completed = [t for t in scoped_tasks if t.get("completed_at") and not t.get("cancelled_at") and inside(t["completed_at"])]
    events = []
    for event in data["events"]:
        task = task_by_id.get(event["task_id"], {})
        app = applications.get(task.get("application_id"))
        relation_matches = app["order_id"] in matching_orders if app else candidate_matches(event.get("candidate_id"))
        if inside(event["occurred_at"]) and staff_matches(event["owner_id"]) and relation_matches:
            events.append(event)
    completed_tasks_by_stage = defaultdict(list)
    for task in data["tasks"]:
        if task.get("application_id") and task.get("completed_at"):
            completed_tasks_by_stage[(task["application_id"], task.get("stage"), aware(task["completed_at"]))].append(task)
    durations = []
    for app in applications.values():
        if app["order_id"] not in matching_orders:
            continue
        for no, record in effective.get(app["id"], {}).items():
            who = record.get("owner_id") or app_owner(app)
            if not inside(record["completed_at"]) or not staff_matches(who):
                continue
            matching_tasks = completed_tasks_by_stage[(app["id"], no, aware(record["completed_at"]))]
            if not matching_tasks:
                # No fabricated duration for imported/legacy stages without an execution task.
                continue
            task = max(matching_tasks, key=lambda t: aware(t["created_at"]))
            hours = (aware(record["completed_at"]) - aware(task["created_at"])).total_seconds() / 3600
            if hours >= 0:
                durations.append({"ownerId": who, "stage": no, "hours": hours})
    staff = []
    staff_ids = {u["id"] for u in users.values() if value(u["role"]) == "staff" and staff_matches(u["id"])}
    staff_ids.update(t["owner_id"] for t in completed if t["owner_id"])
    staff_ids.update(e["owner_id"] for e in events if e["owner_id"])
    for uid in sorted(staff_ids, key=lambda key: users.get(key, {}).get("name", key)):
        own_durations = [d["hours"] for d in durations if d["ownerId"] == uid]
        staff.append({"id": uid, "name": users.get(uid, {}).get("name", "Former user"),
                      "assigned": sum(owner(c, "intake_owner_id", c) == uid for c in intake),
                      "completedTasks": sum(t["owner_id"] == uid for t in completed),
                      "averageStageHours": round(mean(own_durations), 2) if own_durations else None,
                      "durationSamples": len(own_durations),
                      "escalations": sum(e["owner_id"] == uid and e["kind"] == "escalation" for e in events),
                      "missedUpdates": sum(e["owner_id"] == uid and e["kind"] == "missed_update" for e in events)})
    stage_hours = []
    for no, title in enumerate(STAGES, 1):
        samples = [d["hours"] for d in durations if d["stage"] == no]
        stage_hours.append({"stage": no, "label": title, "completed": len(samples),
                            "averageHours": round(mean(samples), 2) if samples else None})

    sources = [{"source": key, "label": label, "count": sum(value(c["source"]) == key for c in intake)}
               for key, label in SOURCE_LABELS.items()]
    mail_sources = {row["id"]: row["address"] for row in data.get("mailSources", [])}
    def source_account(candidate):
        detail = candidate.get("source_detail") or ""
        if detail.startswith("email_message:"):
            return mail_sources.get(detail.split(":", 1)[1], "Account not recorded")
        return detail or "Account not recorded"
    accounts = Counter(source_account(c) for c in intake if value(c["source"]) == "email")
    source_accounts = [{"account": account, "count": count} for account, count in sorted(accounts.items())]
    order_rows = _order_rows((order for order in orders.values() if order["id"] in matching_orders),
        cohort, companies, clients, designations, effective, filters.staff_id, inside)

    # Rejections and receipts use the date of the actual terminal/collection event,
    # allowing a long-running application created before this period to contribute.
    rejection_counts = Counter()
    receipts = []
    for app in applications.values():
        if app["order_id"] not in matching_orders:
            continue
        records = effective.get(app["id"], {})
        terminal = records.get(app["current_stage"])
        if value(app["status"]) == "closed" and staff_matches((terminal or {}).get("owner_id") or app_owner(app)):
            closed_at = terminal["completed_at"] if terminal else app["updated_at"]
            if inside(closed_at):
                rejection_counts[(app["current_stage"], value(app.get("closure_reason")) or "unknown")] += 1
        record = records.get(8)
        if not record or not inside(record["completed_at"]) or not staff_matches(record.get("owner_id") or app_owner(app)):
            continue
        amount = record.get("amount")
        if amount is None:
            amount = (record.get("data") or {}).get("amount")
        try:
            amount = Decimal(str(amount))
        except Exception:
            continue
        if not amount.is_finite() or amount <= 0:
            continue
        currency = record.get("amount_currency") or (record.get("data") or {}).get("currency") or "UNSPECIFIED"
        candidate = candidates.get(app["candidate_id"], {})
        receipts.append({"id": record["id"], "candidateId": app["candidate_id"], "candidate": candidate.get("name", ""),
                         "orderId": app["order_id"], "order": orders[app["order_id"]]["ref"],
                         "date": aware(record["completed_at"]).astimezone(tz).date().isoformat(),
                         "month": aware(record["completed_at"]).astimezone(tz).strftime("%Y-%m"),
                         "amount": str(amount.quantize(Decimal("0.01"))), "currency": currency})
    totals = defaultdict(Decimal)
    monthly = defaultdict(Decimal)
    for receipt in receipts:
        totals[receipt["currency"]] += Decimal(receipt["amount"])
        monthly[(receipt["month"], receipt["currency"])] += Decimal(receipt["amount"])

    option_owner = user.id if value(user.role) == "staff" else None
    own_apps = [app for app in applications.values() if not option_owner or app_owner(app) == option_owner]
    option_orders = [order for order in orders.values() if not option_owner or any(a["order_id"] == order["id"] for a in own_apps)]
    company_ids = {o["company_id"] for o in option_orders}
    client_ids = {companies[cid]["client_id"] for cid in company_ids if cid in companies}
    designation_ids = {o["designation_id"] for o in option_orders}
    notes = ["Pipeline and order counts use applications mapped in the date range; openings are the order's total capacity.",
             "Sources and assigned candidates use intake date and the initial assignment owner. Task completions, stage durations, rejections, advances and persisted events use their event date.",
             "Client, order and designation filters restrict candidates/tasks to linked matching orders. Waiting applications are shown separately from closures.",
             "Legacy ownership may be inferred from current ownership; historical events before tracking began are not reconstructed. Missing duration samples are not treated as zero."]
    return {
        "generatedAt": now.isoformat(), "timezone": zone,
        "filters": {"fromDate": filters.from_date.isoformat(), "toDate": filters.to_date.isoformat(),
                    "clientId": filters.client_id, "orderId": filters.order_id,
                    "designationId": filters.designation_id, "staffId": filters.staff_id},
        "notes": notes,
        "summary": {"applications": len(cohort), "active": sum(value(a["status"]) == "active" for a in cohort),
                    "deployed": sum(value(a["status"]) == "deployed" for a in cohort), "intake": len(intake)},
        "options": {"clients": [{"id": r["id"], "label": r["name"]} for r in clients.values() if not option_owner or r["id"] in client_ids],
                    "orders": [{"id": r["id"], "label": r["ref"]} for r in option_orders],
                    "designations": [{"id": r["id"], "label": r["title"]} for r in designations.values() if not option_owner or r["id"] in designation_ids],
                    "staff": [{"id": r["id"], "label": r["name"]} for r in users.values() if not option_owner or r["id"] == option_owner]},
        "pipeline": pipeline, "staff": staff, "stageHours": stage_hours, "sources": sources,
        "accounts": source_accounts, "orders": order_rows,
        "rejections": [{"stage": stage, "reason": reason, "label": REASONS.get(reason, reason), "count": count}
                       for (stage, reason), count in sorted(rejection_counts.items())],
        "advances": sorted(receipts, key=lambda r: (r["date"], r["id"]), reverse=True),
        "advanceTotals": [{"currency": currency, "amount": str(amount)} for currency, amount in sorted(totals.items())],
        "advanceMonths": [{"month": month, "currency": currency, "amount": str(amount)} for (month, currency), amount in sorted(monthly.items())],
    }


def dashboard_data(data, user, staff_id=None, zone="Asia/Kolkata", now=None):
    """The admin can inspect the team; everyone else lands on their own workload."""
    if value(user.role) != "admin" and staff_id not in (None, user.id):
        raise HTTPException(403, "Only administrators can change dashboard staff scope.")
    scope = staff_id if value(user.role) == "admin" else user.id
    now = aware(now or datetime.now(timezone.utc))
    tz = ZoneInfo(zone)
    today = now.astimezone(tz).date()
    candidates = {c["id"]: c for c in data["candidates"]}
    own_candidates = [c for c in candidates.values() if not scope or c.get("owner_id") == scope]
    own_ids = {c["id"] for c in own_candidates}
    apps = [a for a in data["applications"] if a["candidate_id"] in own_ids]
    today_candidates = [c for c in own_candidates if aware(c["created_at"]).astimezone(tz).date() == today]
    tasks = [t for t in data["tasks"] if not t.get("completed_at") and not t.get("cancelled_at") and (not scope or t["owner_id"] in (scope, user.id))]
    task_owners = {t["id"]: t["owner_id"] for t in data["tasks"]}
    remarks_today = {r["task_id"] for r in data["remarks"] if r.get("author_id") == task_owners.get(r["task_id"])
                     and aware(r["created_at"]).astimezone(tz).date() == today}
    orders = {o["id"]: o for o in data["orders"]}
    users = {u["id"]: u for u in data["users"]}
    task_rows = []
    for task in tasks:
        due = aware(task.get("due_at") or (aware(task.get("sla_started_at") or task["created_at"]) + timedelta(hours=48)))
        candidate = candidates.get(task["candidate_id"], {})
        task_rows.append({"id": task["id"], "title": task["title"], "candidateId": task["candidate_id"],
                          "candidate": candidate.get("name", ""), "applicationId": task.get("application_id"),
                          "stage": task.get("stage"), "ownerId": task["owner_id"],
                          "owner": users.get(task["owner_id"], {}).get("name", ""),
                          "dueAt": due.isoformat(), "overdue": due < now,
                          "dueToday": due.astimezone(tz).date() == today,
                          "escalated": bool(task.get("escalated")), "notUpdated": task["id"] not in remarks_today})
    task_rows.sort(key=lambda t: (aware(t["dueAt"]), t["id"]))
    scoped_task_rows = [t for t in task_rows if not scope or t["ownerId"] == scope]
    # Share the order definitions without calculating six unused reports. Current
    # candidate ownership governs the desk; reports retain historical ownership.
    start = datetime(1970, 1, 1, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    inside = lambda raw: bool(raw and start <= aware(raw) < end)
    active_orders = _order_rows((order for order in orders.values() if value(order["status"]) == "open"),
        [app for app in apps if inside(app["created_at"])],
        {c["id"]: c for c in data["companies"]}, {c["id"]: c for c in data["clients"]},
        {d["id"]: d for d in data["designations"]}, _effective_stages(data["stages"]), scope, inside)
    active_apps = [a for a in apps if value(a["status"]) == "active"]
    return {"generatedAt": now.isoformat(), "timezone": zone, "date": today.isoformat(), "staffId": scope,
            "staffOptions": [{"id": u["id"], "label": u["name"]} for u in users.values()] if value(user.role) == "admin" else [],
            "today": {"total": len(today_candidates), **{key: sum(value(c["source"]) == key for c in today_candidates) for key in SOURCE_LABELS}},
            "taskCounts": {"open": len(scoped_task_rows), "dueToday": sum(t["dueToday"] for t in scoped_task_rows),
                           "overdue": sum(t["overdue"] for t in scoped_task_rows), "escalated": sum(t["escalated"] for t in scoped_task_rows),
                           "notUpdated": sum(t["notUpdated"] for t in scoped_task_rows)},
            "pipeline": [{"stage": n, "label": title, "count": sum(a["current_stage"] == n for a in active_apps),
                          "applications": [{"id": a["id"], "candidateId": a["candidate_id"],
                                            "candidate": candidates[a["candidate_id"]]["name"],
                                            "order": orders.get(a["order_id"], {}).get("ref", "")}
                                           for a in active_apps if a["current_stage"] == n]}
                         for n, title in enumerate(STAGES, 1)],
            "orders": active_orders, "tasks": scoped_task_rows,
            "myTasks": [t for t in task_rows if t["ownerId"] == user.id]}
