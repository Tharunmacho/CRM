"""The Client tracker read from the management side: one line per client, with its orders and the candidates in each.

The placement sheet (`client_tracker`) answers "who is where". This answers "how is each client doing":

* Openings: the sum of the openings of the client's orders that are still open (status open or on hold), less the candidates already
  placed (travelled) against them. A filled or closed order leaves no opening.
* In process: placements still active (a candidate mapped to the order, at any stage, who has not been closed; a candidate who is not
  interested is left out, as on the placement sheet).
* Placed: placements that travelled (status deployed).
* Fill rate: placed over the openings the orders asked for (an order that was closed with nobody placed is not counted against the client).
* Last activity: the newest event on the client's orders or on any placement on them (a candidate mapped, a stage completed, closed or placed;
  an order added or edited), by the time of the event itself, never a row's last update (a bulk edit or an import touches those).
* Status: "active" when there is something open (openings left or candidates in process), "stalled" when it is active and nothing
  has changed for STALLED_DAYS days, "idle" when nothing is open.

A recruiter sees the clients and orders on which they own a placement, and only their own placements; admin and MD see everything.

Cost: three independent reads (orders, placements, newest change per order), side by side, then one pass in memory.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Candidate, CandidateStatus, Company, Designation, Order, User,
)
from app.services.client_tracker import STAGE_WORDS, _text
from app.services.parallel_reads import parallel_reads
from app.services.response_cache import cached, scope_of

STALLED_DAYS = 14
SECONDS = 30
TOPICS = ("applications", "candidates", "orders", "companies", "designations", "tasks", "reports")
SHEET_CLIENTS = "Clients"
SHEET_ORDERS = "Orders and candidates"
CLIENT_COLUMNS = ("Client", "Country", "Status", "Orders", "Open orders", "Openings", "In process", "Placed", "Fill rate %", "Last activity")
ORDER_COLUMNS = ("Client", "Order", "Position", "Country", "Order status", "Openings", "Placed", "Open", "Candidate", "Stage")
WIDTHS_CLIENTS = [30, 14, 11, 9, 12, 10, 11, 9, 11, 18]
WIDTHS_ORDERS = [30, 14, 26, 14, 12, 10, 9, 8, 30, 32]

_ORDER_OPEN = ("open", "on_hold")


def read_clients(db: Session, user: User, now: datetime | None = None) -> dict:
    """The answer for this user, shared between requests (read only) for a short while."""
    return cached(("client-tracker-clients", scope_of(db), user.id if user.role.value == "staff" else None), TOPICS, SECONDS,
                  lambda: _read(db, user, now or datetime.now(timezone.utc)))


def _read(db: Session, user: User, now: datetime) -> dict:
    staff = user.role.value == "staff"
    own = select(Application.order_id).where(Application.owner_id == user.id) if staff else None

    orders = (select(Order.id, Order.ref, Order.openings, Order.status, Order.country, Order.deadline, Order.created_at, Order.updated_at,
                     Company.id, Company.name, Company.country, Designation.title)
              .select_from(Order).join(Company, Company.id == Order.company_id).join(Designation, Designation.id == Order.designation_id))
    placements = (select(Application.id, Application.order_id, Application.current_stage, Application.status, Application.updated_at,
                         Candidate.id, Candidate.name)
                  .select_from(Application).join(Candidate, Candidate.id == Application.candidate_id)
                  .where(or_(Application.status == ApplicationStatus.deployed,
                             (Application.status == ApplicationStatus.active) & (Candidate.status != CandidateStatus.not_interested))))
    # A placement "moved" when something HAPPENED to it: it was mapped, a stage was completed, or it was closed. Never its `updated_at`: a bulk edit or an
    # import touches that column of every row, and made a client nobody had worked for weeks read "last activity today" and never "stalled".
    last_stage = (select(func.max(ApplicationStage.completed_at))
                  .where(ApplicationStage.application_id == Application.id, or_(ApplicationStage.reopened_reason.is_(None), ApplicationStage.reopened_reason == ""))
                  .correlate(Application).scalar_subquery())
    event = func.greatest(Application.created_at, func.coalesce(last_stage, Application.created_at), func.coalesce(Application.closed_at, Application.created_at))
    moved = select(Application.order_id, func.max(event)).group_by(Application.order_id)
    if staff:
        orders = orders.where(Order.id.in_(own))
        placements = placements.where(Application.owner_id == user.id)
        moved = moved.where(Application.owner_id == user.id)
    order_rows, placement_rows, moved_rows = parallel_reads(
        db, lambda s: s.execute(orders).all(), lambda s: s.execute(placements).all(), lambda s: s.execute(moved).all())
    return build(order_rows, placement_rows, moved_rows, now)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build(order_rows, placement_rows, moved_rows, now: datetime) -> dict:
    """Pure: the rows the three reads return, in; the clients, their orders and the totals, out."""
    moved = {order_id: _aware(at) for order_id, at in moved_rows}
    by_order: dict[str, list[dict]] = {}
    for app_id, order_id, stage_no, status, _updated, candidate_id, name in placement_rows:
        travelled = getattr(status, "value", status) == "deployed"
        by_order.setdefault(order_id, []).append({
            "applicationId": app_id, "candidateId": candidate_id, "name": _text(name), "stageNo": stage_no,
            "stage": "Travelled" if travelled else STAGE_WORDS.get(stage_no, ""), "travelled": travelled})
    cutoff = now - timedelta(days=STALLED_DAYS)
    clients: dict[str, dict] = {}
    for (order_id, ref, openings, status, order_country, deadline, created, updated, company_id, company, company_country, job) in order_rows:
        client = clients.setdefault(company_id, {"id": company_id, "name": _text(company), "country": _text(company_country), "orders": [], "_at": None})
        candidates = sorted(by_order.get(order_id, []), key=lambda c: (c["travelled"], c["stageNo"], c["name"].lower(), c["applicationId"]))
        placed = sum(1 for c in candidates if c["travelled"])
        in_process = len(candidates) - placed
        is_open = getattr(status, "value", status) in _ORDER_OPEN
        wanted = int(openings or 0)
        at = max([a for a in (_aware(updated), _aware(created), moved.get(order_id)) if a is not None], default=None)
        if at is not None and (client["_at"] is None or at > client["_at"]):
            client["_at"] = at
        client["orders"].append({
            "id": order_id, "ref": ref, "position": _text(job), "country": _text(order_country) or _text(company_country),
            "status": getattr(status, "value", str(status)), "isOpen": is_open, "openings": wanted, "placed": placed,
            "inProcess": in_process, "open": max(0, wanted - placed) if is_open else 0,
            "deadline": deadline.isoformat() if deadline else None, "candidates": candidates})
    rows = []
    totals = {"orders": 0, "openOrders": 0, "openings": 0, "inProcess": 0, "placed": 0, "asked": 0, "filled": 0}
    for client in clients.values():
        orders = sorted(client["orders"], key=lambda o: (not o["isOpen"], o["ref"]))
        open_orders = sum(1 for o in orders if o["isOpen"])
        openings = sum(o["open"] for o in orders)
        in_process = sum(o["inProcess"] for o in orders)
        placed = sum(o["placed"] for o in orders)
        # Closed with nobody placed is an order that never happened; everything else counts toward the rate.
        counted = [o for o in orders if o["status"] != "closed" or o["placed"]]
        asked = sum(o["openings"] for o in counted)
        filled = sum(min(o["placed"], o["openings"]) for o in counted)
        at = client["_at"]
        active = openings > 0 or in_process > 0
        stalled = active and (at is None or at < cutoff)
        rows.append({
            "id": client["id"], "name": client["name"], "country": client["country"], "orders": orders,
            "orderCount": len(orders), "openOrders": open_orders, "openings": openings, "inProcess": in_process, "placed": placed,
            "fillRate": round(filled / asked * 100, 1) if asked else None,
            "lastActivity": at.isoformat() if at else None, "active": active, "stalled": stalled,
            "status": "stalled" if stalled else "active" if active else "idle"})
        for key, value in (("orders", len(orders)), ("openOrders", open_orders), ("openings", openings), ("inProcess", in_process),
                           ("placed", placed), ("asked", asked), ("filled", filled)):
            totals[key] += value
    rows.sort(key=lambda r: (r["name"].lower(), r["id"]))
    summary = {
        "clients": len(rows), "activeClients": sum(1 for r in rows if r["active"]), "stalledClients": sum(1 for r in rows if r["stalled"]),
        "idleClients": sum(1 for r in rows if r["status"] == "idle"), "openOrders": totals["openOrders"], "openings": totals["openings"],
        "inProcess": totals["inProcess"], "placed": totals["placed"],
        "fillRate": round(totals["filled"] / totals["asked"] * 100, 1) if totals["asked"] else None, "stalledDays": STALLED_DAYS}
    return {"summary": summary, "clients": rows, "generatedAt": now.isoformat()}


# ------------------------------------------------------------------------------------------------ the file

def _cell(value):
    """Keep a spreadsheet from running text that starts like a formula (and drop control characters that corrupt the sheet's XML)."""
    if isinstance(value, str):
        value = "".join(ch for ch in value if ord(ch) >= 32)
        if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
    return value


def wanted_clients(body: dict, status: str = "all", search: str = "") -> list[dict]:
    """The clients the screen shows for a status tab and a search (the file carries the same ones)."""
    text = _text(search).lower()
    keep = []
    for client in body["clients"]:
        if status == "active" and not client["active"] or status == "stalled" and not client["stalled"]:
            continue
        if text and text not in client["name"].lower() and not any(text in (o["ref"] + " " + o["position"]).lower() for o in client["orders"]):
            continue
        keep.append(client)
    return keep


def xlsx_bytes(clients: list[dict]) -> bytes:
    from app.services import lead_xlsx
    first = [list(CLIENT_COLUMNS)]
    for c in clients:
        at = datetime.fromisoformat(c["lastActivity"]) if c["lastActivity"] else None
        first.append([_cell(v) for v in (c["name"], c["country"], c["status"].title(), c["orderCount"], c["openOrders"], c["openings"],
                                        c["inProcess"], c["placed"], c["fillRate"] if c["fillRate"] is not None else "", at)])
    second = [list(ORDER_COLUMNS)]
    for c in clients:
        for o in c["orders"]:
            head = [c["name"], o["ref"], o["position"], o["country"], o["status"].replace("_", " ").title(), o["openings"], o["placed"], o["open"]]
            if not o["candidates"]:
                second.append([_cell(v) for v in (*head, "", "")])
            for candidate in o["candidates"]:
                second.append([_cell(v) for v in (*head, candidate["name"], candidate["stage"])])
    return lead_xlsx.build_workbook([
        {"name": SHEET_CLIENTS, "rows": first, "widths": WIDTHS_CLIENTS, "header": True, "freeze": True, "filter": True},
        {"name": SHEET_ORDERS, "rows": second, "widths": WIDTHS_ORDERS, "header": True, "freeze": True, "filter": True}])
