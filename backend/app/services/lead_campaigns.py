"""Campaign performance of the Lead sheet: one row per campaign (with its forms) from ONE set-based statement.

Contract: docs/workflows/lead-sheet-contract.md ("Campaigns"). Admin and manager see every lead; a staff member sees only the
leads assigned to them, so their counts are their own work. All rates are percentages with one decimal (0 to 100).

Definitions (per campaign, or per campaign and form):
  leads            people stored
  newToday         arrived since midnight in the agency time zone (`created_time`, else the day we stored them)
  last7Days        arrived in the last 7 days including today
  assignedPct      leads that have an owner, over leads
  contactedPct     leads someone has worked (a call, or any status past New), over leads
  interested       leads that said yes: Interested, Resume requested, Resume received or Converted
  resumeReceived   leads with a resume (Resume received status or an uploaded resume)
  converted        leads that became candidates; conversionRate = converted / leads
  lastUploadAt     newest upload that brought in any of these leads
  platform         {fb, ig, other} lead counts
  topPositions     up to 3 {name, count}; topCountries the same for the country interest
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Role, User

MAX_CAMPAIGNS = 500
TOP = 3

_SQL = text(
    """
WITH scoped AS (
  SELECT l.campaign_name AS c, l.form_name AS f, l.status, l.owner_id, l.call_count, l.platform, l.position_applied AS pos,
         l.country_interest AS country, l.resume_received_at, coalesce(l.created_time, l.created_at) AS arrived, b.created_at AS uploaded
  FROM crm_leads l LEFT JOIN crm_lead_batches b ON b.id = l.batch_id
  WHERE (:everyone OR l.owner_id = :me)
),
stat AS (
  SELECT c, f, count(*) AS leads,
         count(*) FILTER (WHERE arrived >= :today) AS new_today,
         count(*) FILTER (WHERE arrived >= :week) AS last7,
         count(*) FILTER (WHERE owner_id IS NOT NULL) AS assigned,
         count(*) FILTER (WHERE call_count > 0 OR status <> 'new') AS contacted,
         count(*) FILTER (WHERE status IN ('interested', 'resume_requested', 'resume_received', 'converted')) AS interested,
         count(*) FILTER (WHERE status = 'resume_received' OR resume_received_at IS NOT NULL) AS resumes,
         count(*) FILTER (WHERE status = 'converted') AS converted,
         max(uploaded) AS last_upload,
         count(*) FILTER (WHERE platform = 'fb') AS fb, count(*) FILTER (WHERE platform = 'ig') AS ig
  FROM scoped GROUP BY c, f
),
pos AS (SELECT c, f, pos AS name, count(*) AS n FROM scoped WHERE pos IS NOT NULL GROUP BY c, f, pos),
cty AS (SELECT c, f, country AS name, count(*) AS n FROM scoped WHERE country IS NOT NULL GROUP BY c, f, country)
SELECT 'stat' AS kind, c, f, NULL AS name, leads, new_today, last7, assigned, contacted, interested, resumes, converted, last_upload, fb, ig FROM stat
UNION ALL SELECT 'position', c, f, name, n, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL FROM pos
UNION ALL SELECT 'country', c, f, name, n, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL FROM cty
"""
)

_FIELDS = ("leads", "newToday", "last7Days", "assigned", "contacted", "interested", "resumeReceived", "converted", "fb", "ig")


def _empty() -> dict:
    return {**{key: 0 for key in _FIELDS}, "lastUploadAt": None, "_pos": {}, "_cty": {}}


def _add(into: dict, row: dict) -> None:
    for key in _FIELDS:
        into[key] += row[key]
    if row["lastUploadAt"] and (into["lastUploadAt"] is None or row["lastUploadAt"] > into["lastUploadAt"]):
        into["lastUploadAt"] = row["lastUploadAt"]


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _top(counts: dict) -> list[dict]:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:TOP]
    return [{"name": name, "count": n} for name, n in ranked]


def _out(name, form, data: dict, forms: list[dict] | None = None) -> dict:
    leads = data["leads"]
    row = {
        "campaign": name, "form": form, "leads": leads, "newToday": data["newToday"], "last7Days": data["last7Days"],
        "assignedPct": _pct(data["assigned"], leads), "contactedPct": _pct(data["contacted"], leads), "assigned": data["assigned"],
        "contacted": data["contacted"], "interested": data["interested"], "resumeReceived": data["resumeReceived"],
        "converted": data["converted"], "conversionRate": _pct(data["converted"], leads),
        "lastUploadAt": data["lastUploadAt"].isoformat() if data["lastUploadAt"] else None,
        "platform": {"fb": data["fb"], "ig": data["ig"], "other": max(0, leads - data["fb"] - data["ig"])},
        "topPositions": _top(data["_pos"]), "topCountries": _top(data["_cty"]),
    }
    if forms is not None:
        row["forms"] = forms
    return row


def campaigns(db: Session, user: User, now: datetime | None = None) -> dict:
    """The campaign table: `{items, total, scope, truncated}`; items sorted by leads, biggest first (forms too)."""
    from app.services.daily_work import agency_today
    from app.services.candidate_search import agency_midnight
    now = now or datetime.now(timezone.utc)
    today = agency_midnight(agency_today(now).isoformat(), "Today")
    everyone = user.role in (Role.admin, Role.manager)
    rows = db.execute(_SQL, {"everyone": everyone, "me": user.id, "today": today, "week": today - timedelta(days=6)}).all()

    leaves: dict[tuple, dict] = {}
    for kind, c, f, name, n, new_today, last7, assigned, contacted, interested, resumes, converted, last_upload, fb, ig in rows:
        leaf = leaves.setdefault((c, f), _empty())
        if kind == "stat":
            leaf.update(leads=n, newToday=new_today, last7Days=last7, assigned=assigned, contacted=contacted, interested=interested,
                        resumeReceived=resumes, converted=converted, lastUploadAt=last_upload, fb=fb, ig=ig)
        else:
            leaf["_pos" if kind == "position" else "_cty"][name] = n

    by_campaign: dict[str | None, dict] = {}
    forms_of: dict[str | None, list[dict]] = {}
    total = _empty()
    for (c, f), leaf in leaves.items():
        whole = by_campaign.setdefault(c, _empty())
        _add(whole, leaf)
        _add(total, leaf)
        for source, target in ((leaf["_pos"], whole["_pos"]), (leaf["_cty"], whole["_cty"]), (leaf["_pos"], total["_pos"]), (leaf["_cty"], total["_cty"])):
            for key, n in source.items():
                target[key] = target.get(key, 0) + n
        if f is not None:
            forms_of.setdefault(c, []).append(_out(c, f, leaf))
    items = []
    for c, data in by_campaign.items():
        forms = sorted(forms_of.get(c, []), key=lambda r: (-r["leads"], (r["form"] or "").lower()))
        items.append(_out(c, None, data, forms))
    items.sort(key=lambda r: (-r["leads"], (r["campaign"] or "~").lower()))
    return {"scope": "all" if everyone else "mine", "items": items[:MAX_CAMPAIGNS], "truncated": len(items) > MAX_CAMPAIGNS,
            "total": _out(None, None, total), "generatedAt": now.isoformat()}
