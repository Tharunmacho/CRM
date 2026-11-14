"""Lead sheet numbers for the places an admin manages staff: the dashboard cards and the weekly staff report.

Both read `crm_leads` and `crm_lead_activities` (migration 0024) with ONE set-based statement each, never one statement
per person, and both are cheap to repeat because the callers sit behind the response cache with the `leads` topic.
Definitions live here so the dashboard, the report, the Excel export and the tests agree (docs/workflows/02-dashboard.md,
17-reports.md, lead-sheet-contract.md):

  Dashboard (role scoped like the rest of the page: staff their own leads, admin/manager the team or the chosen person)
    newToday         leads that arrived today (the lead's own created time, else when it was stored)
    notContacted     assigned, status still `new` and never called (same rule as the Lead sheet's "Not contacted")
    followUpsDue     open leads whose follow-up date has arrived
    resumesReceived  leads sitting at `resume_received` (waiting to be converted)
    converted        leads converted to Data Bank candidates, by period (last 7/30/90 days, this year, all time)
    conversionPercent  converted leads / leads that were ever assigned, over the same scope
    unassigned       open leads nobody owns yet (admin and manager only; always the global number)

  Weekly report (per staff member, selected period; leads are credited to their current owner, calls to who logged them)
    assigned         leads assigned to the person during the period
    contacted        of those, leads called at least once during the period
    calls            calls logged by the person during the period (any lead)
    worked           distinct leads the person called during the period
    interested       distinct leads the person marked interested (call outcome or status change) in the period
    resumesReceived  leads of the person that received a resume in the period
    converted        leads of the person converted to candidates in the period
    conversionPercent  converted / worked
    followUpsOverdue open leads of the person whose follow-up date has already passed (as of now)
    open             open leads currently held
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

log = logging.getLogger(__name__)

CLOSED = "('converted','not_interested','invalid','wrong_number')"
CARDS = ("newToday", "notContacted", "followUpsDue", "resumesReceived", "converted", "unassigned")
PERIODS = ("week", "month", "quarter", "year", "all")
ITEM_LIMIT = 8


def _value(raw):
    return getattr(raw, "value", raw)


def sees_team(user) -> bool:
    """Lead numbers of everybody: admin only. Managers and staff count only the leads assigned to them."""
    return _value(user.role) == "admin"


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _iso(moment):
    return moment.isoformat() if moment is not None else None


def percent(part, whole):
    return round(part / whole * 100, 1) if whole else None


def _windows(now: datetime, zone: str) -> dict:
    """UTC instants the dashboard counts from: start of today and of each period (agency time zone)."""
    tz = ZoneInfo(zone)
    local = _aware(now).astimezone(tz)
    today = datetime.combine(local.date(), time.min, tz)
    return {
        "today": today.astimezone(timezone.utc),
        "week": _aware(now) - timedelta(days=7), "month": _aware(now) - timedelta(days=30),
        "quarter": _aware(now) - timedelta(days=90),
        "year": datetime(local.year, 1, 1, tzinfo=tz).astimezone(timezone.utc),
        "now": _aware(now),
    }


def dashboard_scope(user, staff_id):
    """Whose leads the dashboard counts: None = everybody (admin/manager without a chosen person)."""
    return staff_id if sees_team(user) else user.id


# ------------------------------------------------------------------ dashboard cards

_CARD_SQL = text(f"""
SELECT l.new_today, l.not_contacted, l.due, l.resumes, l.total, l.assigned, l.converted_total, l.open_total,
       CASE WHEN :team THEN (SELECT count(*) FROM crm_leads WHERE owner_id IS NULL AND status NOT IN {CLOSED}) END AS unassigned,
       c.c_week, c.c_month, c.c_quarter, c.c_year, c.c_all
FROM (
  SELECT count(*) FILTER (WHERE coalesce(created_time, created_at) >= :today) AS new_today,
         count(*) FILTER (WHERE status = 'new' AND call_count = 0 AND owner_id IS NOT NULL) AS not_contacted,
         count(*) FILTER (WHERE next_follow_up_at <= :now AND status NOT IN {CLOSED}) AS due,
         count(*) FILTER (WHERE status = 'resume_received') AS resumes,
         count(*) AS total,
         count(*) FILTER (WHERE owner_id IS NOT NULL) AS assigned,
         count(*) FILTER (WHERE status = 'converted') AS converted_total,
         count(*) FILTER (WHERE status NOT IN {CLOSED}) AS open_total
  FROM crm_leads WHERE (:everyone OR owner_id = :scope)
) l CROSS JOIN (
  SELECT count(*) FILTER (WHERE a.created_at >= :week) AS c_week, count(*) FILTER (WHERE a.created_at >= :month) AS c_month,
         count(*) FILTER (WHERE a.created_at >= :quarter) AS c_quarter, count(*) FILTER (WHERE a.created_at >= :year) AS c_year,
         count(*) AS c_all
  FROM crm_lead_activities a JOIN crm_leads cl ON cl.id = a.lead_id
  WHERE a.kind = 'convert' AND cl.status = 'converted' AND (:everyone OR cl.owner_id = :scope)
) c
""")


def dashboard_leads(db, user, staff_id, zone: str, now: datetime) -> dict | None:
    """The `leads` key of the dashboard response, or None when the lead tables are not in this database yet."""
    scope = dashboard_scope(user, staff_id)
    team = sees_team(user)
    w = _windows(now, zone)
    try:
        row = db.execute(_CARD_SQL, {"everyone": scope is None, "scope": scope or "", "team": team, "today": w["today"], "now": w["now"],
                                      "week": w["week"], "month": w["month"], "quarter": w["quarter"], "year": w["year"]}).one()
    except (SQLAlchemyError, AttributeError) as failure:  # an older database, or a stand-in session in a unit test
        if isinstance(failure, ProgrammingError) and hasattr(db, "rollback"):
            db.rollback()
        log.warning("Dashboard lead numbers unavailable: %s", type(failure).__name__)
        return None
    n = [int(v or 0) for v in row]
    new_today, not_contacted, due, resumes, total, assigned, converted_total, open_total, unassigned = n[:9]
    return {
        "scope": "mine" if not team else ("person" if scope else "team"), "staffId": scope,
        "newToday": new_today, "notContacted": not_contacted, "followUpsDue": due, "resumesReceived": resumes,
        "converted": {"week": n[9], "month": n[10], "quarter": n[11], "year": n[12], "all": n[13]},
        "total": total, "assigned": assigned, "open": open_total, "convertedTotal": converted_total,
        "conversionPercent": percent(converted_total, assigned),
        # The manager's to-do: the number is global on purpose (an unassigned lead belongs to nobody's list).
        "unassigned": unassigned if team else None,
    }


# ------------------------------------------------------------------ the list behind a card

_CARD_ROWS = {
    "newToday": ("coalesce(l.created_time, l.created_at) >= :today", "coalesce(l.created_time, l.created_at) DESC", "created"),
    "notContacted": ("l.status = 'new' AND l.call_count = 0 AND l.owner_id IS NOT NULL", "l.assigned_at ASC NULLS LAST, l.id", "assigned"),
    "followUpsDue": (f"l.next_follow_up_at <= :now AND l.status NOT IN {CLOSED}", "l.next_follow_up_at ASC, l.id", "followUp"),
    "resumesReceived": ("l.status = 'resume_received'", "l.resume_received_at DESC NULLS LAST, l.id", "resume"),
    "unassigned": (f"l.owner_id IS NULL AND l.status NOT IN {CLOSED}", "coalesce(l.created_time, l.created_at) DESC, l.id", "created"),
}


def dashboard_lead_items(db, user, staff_id, card: str, period: str, zone: str, now: datetime, limit: int = ITEM_LIMIT) -> dict:
    """The few leads behind one dashboard card (newest or most urgent first) and the full count."""
    scope = dashboard_scope(user, staff_id)
    w = _windows(now, zone)
    params = {"everyone": scope is None, "scope": scope or "", "today": w["today"], "now": w["now"], "limit": limit}
    scope_sql = "(:everyone OR l.owner_id = :scope)"
    if card == "unassigned":
        if not sees_team(user):
            return {"card": card, "total": 0, "items": []}
        scope_sql = "TRUE"  # the unassigned pool is shared: it is the same list whichever person is chosen
    if card == "converted":
        since = None if period == "all" else w[period]
        sql = (f"SELECT l.id, l.name, l.position_applied, l.country_interest, l.status, u.name AS owner_name, l.call_count, "
               "l.next_follow_up_at, c.at AS at, count(*) OVER () AS total FROM crm_leads l "
               "JOIN LATERAL (SELECT max(a.created_at) AS at FROM crm_lead_activities a WHERE a.lead_id = l.id AND a.kind = 'convert') c ON true "
               "LEFT JOIN auth_users u ON u.id = l.owner_id "
               f"WHERE {scope_sql} AND l.status = 'converted' AND (CAST(:since AS timestamptz) IS NULL OR c.at >= CAST(:since AS timestamptz)) "
               "ORDER BY c.at DESC NULLS LAST, l.id LIMIT :limit")
        params["since"] = since
        kind = "converted"
    else:
        where, order, kind = _CARD_ROWS[card]
        at = {"created": "coalesce(l.created_time, l.created_at)", "assigned": "l.assigned_at", "followUp": "l.next_follow_up_at",
              "resume": "l.resume_received_at"}[kind]
        sql = (f"SELECT l.id, l.name, l.position_applied, l.country_interest, l.status, u.name AS owner_name, l.call_count, "
               f"l.next_follow_up_at, {at} AS at, count(*) OVER () AS total FROM crm_leads l LEFT JOIN auth_users u ON u.id = l.owner_id "
               f"WHERE {scope_sql} AND {where} ORDER BY {order} LIMIT :limit")
    rows = db.execute(text(sql), params).all()
    return {"card": card, "period": period if card == "converted" else None, "at": kind,
            "total": int(rows[0].total) if rows else 0,
            "items": [{"id": r.id, "name": r.name or "No name", "position": r.position_applied, "country": r.country_interest,
                       "status": r.status, "ownerName": r.owner_name, "callCount": int(r.call_count or 0),
                       "nextFollowUpAt": _iso(r.next_follow_up_at), "at": _iso(r.at)} for r in rows]}


# ------------------------------------------------------------------ weekly staff report

_WEEKLY_SQL = text(f"""
WITH staff AS (SELECT id FROM auth_users WHERE role = 'staff' AND (CAST(:staff AS varchar) IS NULL OR id = :staff))
SELECT owner, metric, n FROM (
  SELECT l.owner_id AS owner, 'assigned' AS metric, count(*) AS n FROM crm_leads l
   WHERE l.owner_id IN (SELECT id FROM staff) AND l.assigned_at >= :start AND l.assigned_at < :end GROUP BY l.owner_id
  UNION ALL
  SELECT l.owner_id, 'contacted', count(*) FROM crm_leads l
   WHERE l.owner_id IN (SELECT id FROM staff) AND l.assigned_at >= :start AND l.assigned_at < :end
     AND EXISTS (SELECT 1 FROM crm_lead_activities a WHERE a.lead_id = l.id AND a.kind = 'call' AND a.created_at >= :start AND a.created_at < :end)
   GROUP BY l.owner_id
  UNION ALL
  SELECT a.user_id, 'calls', count(*) FROM crm_lead_activities a
   WHERE a.kind = 'call' AND a.user_id IN (SELECT id FROM staff) AND a.created_at >= :start AND a.created_at < :end GROUP BY a.user_id
  UNION ALL
  SELECT a.user_id, 'worked', count(DISTINCT a.lead_id) FROM crm_lead_activities a
   WHERE a.kind = 'call' AND a.user_id IN (SELECT id FROM staff) AND a.created_at >= :start AND a.created_at < :end GROUP BY a.user_id
  UNION ALL
  SELECT a.user_id, 'interested', count(DISTINCT a.lead_id) FROM crm_lead_activities a
   WHERE a.user_id IN (SELECT id FROM staff) AND a.created_at >= :start AND a.created_at < :end
     AND ((a.kind = 'call' AND a.meta ->> 'outcome' = 'interested') OR (a.kind = 'status' AND a.meta ->> 'to' = 'interested'))
   GROUP BY a.user_id
  UNION ALL
  SELECT l.owner_id, 'resumes', count(DISTINCT a.lead_id) FROM crm_lead_activities a JOIN crm_leads l ON l.id = a.lead_id
   WHERE a.kind = 'resume' AND l.owner_id IN (SELECT id FROM staff) AND a.created_at >= :start AND a.created_at < :end GROUP BY l.owner_id
  UNION ALL
  SELECT l.owner_id, 'converted', count(DISTINCT a.lead_id) FROM crm_lead_activities a JOIN crm_leads l ON l.id = a.lead_id
   WHERE a.kind = 'convert' AND l.status = 'converted' AND l.owner_id IN (SELECT id FROM staff)
     AND a.created_at >= :start AND a.created_at < :end GROUP BY l.owner_id
  UNION ALL
  SELECT l.owner_id, 'overdue', count(*) FROM crm_leads l
   WHERE l.owner_id IN (SELECT id FROM staff) AND l.status NOT IN {CLOSED} AND l.next_follow_up_at < :now GROUP BY l.owner_id
  UNION ALL
  SELECT l.owner_id, 'open', count(*) FROM crm_leads l
   WHERE l.owner_id IN (SELECT id FROM staff) AND l.status NOT IN {CLOSED} GROUP BY l.owner_id
) t
""")

WEEKLY_KEYS = {"assigned": "assigned", "contacted": "contacted", "calls": "calls", "worked": "worked",
               "interested": "interested", "resumes": "resumesReceived", "converted": "converted",
               "overdue": "followUpsOverdue", "open": "open"}


def weekly_lead_numbers(session, start: datetime, end: datetime, now: datetime, staff_id: str | None) -> dict | None:
    """Per staff id: the lead numbers of the period, from one statement. None when the lead tables are missing."""
    try:
        rows = session.execute(_WEEKLY_SQL, {"staff": staff_id, "start": start, "end": end, "now": _aware(now)}).all()
    except (ProgrammingError, AttributeError):
        if hasattr(session, "rollback"):
            session.rollback()
        return None
    out: dict[str, dict] = {}
    for owner, metric, n in rows:
        out.setdefault(owner, {})[WEEKLY_KEYS[metric]] = int(n)
    return out


def person_numbers(raw: dict | None) -> dict:
    """The `leads` block of one staff member from the statement's numbers (zeros for what is missing)."""
    raw = raw or {}
    block = {key: int(raw.get(key, 0)) for key in WEEKLY_KEYS.values()}
    block["notContacted"] = max(0, block["assigned"] - block["contacted"])
    block["conversionPercent"] = percent(block["converted"], block["worked"])
    block["available"] = True
    return block


def unavailable_numbers() -> dict:
    return {**{key: 0 for key in WEEKLY_KEYS.values()}, "notContacted": 0, "conversionPercent": None, "available": False}
