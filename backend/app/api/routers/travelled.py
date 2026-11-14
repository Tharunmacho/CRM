"""Travelled candidates: who flew out, when, for which client, through which referral agency.

A placement counts as travelled when its application is deployed. A booked ticket alone is not confirmation of travel. The
travel date is that stage's own `travelDate`, so history imported from a folder of documents and placements finished on
the stage screen read the same. One SQL statement reads every placement the person may see; the filters, the year / month /
day totals and the filter options are then worked out from those rows, so the screen costs a single database round trip.

Each row also says how many of the mandatory documents are on file (`documents`): the people loaded from a folder of papers
often lack some (education certificate, Google review...), and the owner wants to see that at a glance. One more statement reads
the documents of every listed candidate together, so the screen costs two round trips, never one per row.

Who sees what follows the other reports: admin and manager see every placement, staff only the placements they are the
recruiter of.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from io import StringIO
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import current_user
from app.db.models import (
    Application, ApplicationStage, Attachment, ApplicationStatus, Candidate, Client, Company, Designation, Order, User,
)
from app.db.session import get_db
from app.services.agency_display import agency_display_expression
from app.services.application_form import LEGACY_SLOTS, required_for_deployment
from app.services.invoice_access import can_see_invoices

router = APIRouter(prefix="/api/travelled", tags=["travelled"])

NOT_RECORDED = "Not recorded"   # the label for a placement with no agency (or no country) on file; it can be filtered on too
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXPORT_COLUMNS = ("Traveller ID", "Travel date", "Candidate", "Ref", "Client", "Country", "Job", "Order", "Referral agency", "Airline and flight",
                  "PNR", "Recruiter", "Status", "Missing documents", "Passport")


class Filters:
    """The query string, checked once so the list and the export read it the same way."""

    def __init__(self, year: int | None = None, month: int | None = None, day: int | None = None,
                 from_: str | None = None, to: str | None = None, client: str | None = None, agency: str | None = None,
                 country: str | None = None, q: str | None = None, incomplete: bool = False, undated: bool = False,
                 order: str | None = None):
        if year is not None and not 1990 <= year <= 2100:
            raise HTTPException(422, "Choose a year between 1990 and 2100.")
        if month is not None and year is None:
            raise HTTPException(422, "Choose a year as well to filter by month.")
        if day is not None and month is None:
            raise HTTPException(422, "Choose a month as well to filter by day.")
        if month is not None and not 1 <= month <= 12:
            raise HTTPException(422, "The month must be between 1 and 12.")
        if day is not None:
            try:
                date(year, month, day)
            except ValueError as exc:
                raise HTTPException(422, "That day does not exist in the chosen month.") from exc
        self.year, self.month, self.day = year, month, day
        self.from_ = self._date(from_, "from")
        self.to = self._date(to, "to")
        if self.from_ and self.to and self.from_ > self.to:
            raise HTTPException(422, "The start date must be on or before the end date.")
        self.client, self.agency, self.country = (_text(client), _text(agency), _text(country))
        self.q = _text(q)
        self.order = _text(order)
        self.incomplete = bool(incomplete)
        self.undated = bool(undated)
        if self.undated and self.dated:
            raise HTTPException(422, "Choose either a travel date or 'date not recorded', not both.")

    @staticmethod
    def _date(raw: str | None, label: str) -> str | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            if not _DAY.fullmatch(raw):
                raise ValueError("Expected YYYY-MM-DD")
            return date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise HTTPException(422, f"The '{label}' date must be written YYYY-MM-DD.") from exc

    @property
    def dated(self) -> bool:
        """A date filter hides placements that have no travel date."""
        return any(v is not None for v in (self.year, self.month, self.day, self.from_, self.to))

    def keeps(self, row: dict) -> bool:
        when = row["travelDate"]
        if self.undated and when is not None:    # "date not recorded": travelled, but no travel date is on file
            return False
        if self.dated:
            if when is None:
                return False
            year, month, day = (int(part) for part in when.split("-"))
            if self.year is not None and year != self.year:
                return False
            if self.month is not None and month != self.month:
                return False
            if self.day is not None and day != self.day:
                return False
            if self.from_ and when < self.from_:
                return False
            if self.to and when > self.to:
                return False
        if self.incomplete and not row["documents"]["missing"]:
            return False
        if self.order and row["orderId"] != self.order:
            return False
        for wanted, have in ((self.client, row["client"]), (self.agency, row["agency"] or NOT_RECORDED),
                             (self.country, row["country"] or NOT_RECORDED)):
            if wanted and (have or "").strip().lower() != wanted.lower():
                return False
        if self.q:
            needle = self.q.lower()
            if not any(needle in (row[key] or "").lower() for key in (
                    "name", "ref", "travellerId", "passportNumber", "orderRef", "pnr", "client", "job", "agency", "country", "airline")):
                return False
        return True


def _text(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


def query_filters(year: int | None = None, month: int | None = None, day: int | None = None,
                  from_: str | None = Query(default=None, alias="from"), to: str | None = None, client: str | None = None,
                  agency: str | None = None, country: str | None = None, q: str | None = None, incomplete: bool = False,
                  undated: bool = False, order: str | None = None) -> Filters:
    return Filters(year, month, day, from_, to, client, agency, country, q, incomplete, undated, order)


def read_rows(db: Session, user: User) -> list[dict]:
    """Every travelled placement the user may see, newest travel first. One statement."""
    ranked = select(
        ApplicationStage.application_id.label("application_id"), ApplicationStage.data.label("data"),
        ApplicationStage.reopened_reason.label("reopened_reason"),
        func.row_number().over(partition_by=ApplicationStage.application_id, order_by=ApplicationStage.attempt.desc()).label("rank"),
    ).where(ApplicationStage.stage == 11).subquery()
    ticket = select(ranked.c.application_id, ranked.c.data).where(
        ranked.c.rank == 1, ranked.c.reopened_reason.is_(None)).subquery()
    travel = ticket.c.data["travelDate"].as_string()
    statement = (
        select(Application.id, Application.status, Candidate.id, Candidate.ref, Candidate.name,
               agency_display_expression().label("agency"), Order.ref, Order.country, Company.name, Company.country, Client.id, Designation.title,
               User.name, travel, ticket.c.data["airline"].as_string(), ticket.c.data["pnr"].as_string(),
               Candidate.passport_no, Order.id, Candidate.traveller_id, Candidate.care_id)
        .select_from(Application)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Order, Order.id == Application.order_id)
        .join(Company, Company.id == Order.company_id)
        .join(Client, Client.id == Company.client_id)
        .join(Designation, Designation.id == Order.designation_id)
        .outerjoin(User, User.id == Application.owner_id)
        .outerjoin(ticket, ticket.c.application_id == Application.id)
        .where(Application.status == ApplicationStatus.deployed)
    )
    if user.role.value == "staff":
        statement = statement.where(Application.owner_id == user.id)
    rows = []
    for (app_id, status, cand_id, ref, name, agency, order_ref, order_country, company, company_country, client_id,
         job, recruiter, travel_date, airline, pnr, passport, order_id, traveller_id, care_id) in db.execute(statement).all():
        day = None
        if isinstance(travel_date, str) and _DAY.fullmatch(travel_date):
            try:
                day = date.fromisoformat(travel_date).isoformat()
            except ValueError:
                pass
        rows.append({
            "applicationId": app_id, "candidateId": cand_id, "ref": ref, "travellerId": traveller_id, "careId": care_id, "name": name, "client": company, "clientId": client_id,
            "country": _text(order_country) or _text(company_country), "job": job, "orderRef": order_ref,
            "orderId": order_id, "passportNumber": passport or None,
            "agency": agency,
            "travelDate": day, "airline": airline or None, "pnr": pnr or None, "recruiter": recruiter,
            "status": getattr(status, "value", status),
        })
    _add_documents(db, rows, user)
    rows.sort(key=lambda r: (r["name"] or "").lower())
    rows.sort(key=lambda r: r["travelDate"] or "", reverse=True)   # stable: newest first, then by name; undated last
    return rows


def _add_documents(db: Session, rows: list[dict], user: User | None = None) -> None:
    """`documents` on every row: {have, required, missing[labels]} against the mandatory list, from ONE statement for all candidates.

    The Invoice document is the admin's, so for anyone else it is neither required nor listed as missing."""
    required = required_for_deployment(user is None or can_see_invoices(user))
    from app.services.document_identity import rejected_expression
    stored: dict[str, set[str]] = {}
    ids = sorted({row["candidateId"] for row in rows})
    if ids:
        for candidate_id, slot in db.execute(select(Attachment.entity_id, Attachment.slot).where(
                Attachment.entity_type == "candidate", Attachment.entity_id.in_(ids), ~rejected_expression()).distinct()).all():
            stored.setdefault(candidate_id, set()).add(LEGACY_SLOTS.get(slot, slot))   # an older slot key counts for its new slot
    for row in rows:
        have = stored.get(row["candidateId"], set())
        missing = [label for slot, label in required if slot not in have]
        row["documents"] = {"have": len(required) - len(missing), "required": len(required), "missing": missing}


def _counts(values) -> list[tuple]:
    seen: dict = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    return list(seen.items())


def _by_name(pairs: list[tuple]) -> list[tuple]:
    """Most travellers first; ties alphabetical; the "not recorded" bucket last."""
    return sorted(pairs, key=lambda p: (p[0] == NOT_RECORDED, -p[1], str(p[0]).lower()))


def build(rows: list[dict], wanted: Filters) -> dict:
    kept = [row for row in rows if wanted.keeps(row)]
    dated = [row["travelDate"] for row in kept if row["travelDate"]]
    summary = {
        "byYear": [{"year": int(k), "count": n} for k, n in sorted(_counts(d[:4] for d in dated), reverse=True)],
        "byMonth": [{"month": k, "count": n} for k, n in sorted(_counts(d[:7] for d in dated), reverse=True)],
        "byDay": [{"date": k, "count": n} for k, n in sorted(_counts(dated), reverse=True)],
        "byClient": [{"client": k, "count": n} for k, n in _by_name(_counts(r["client"] for r in kept))],
        "byAgency": [{"agency": k, "count": n} for k, n in _by_name(_counts(r["agency"] or NOT_RECORDED for r in kept))],
        "byCountry": [{"country": k, "count": n} for k, n in _by_name(_counts(r["country"] or NOT_RECORDED for r in kept))],
    }

    def names(values) -> list[str]:
        values = list(values)
        real = sorted({v for v in values if v}, key=str.lower)
        return real + ([NOT_RECORDED] if any(not v for v in values) else [])

    months = _counts(r["travelDate"][:7] for r in rows if r["travelDate"])
    options = {
        "years": sorted({int(r["travelDate"][:4]) for r in rows if r["travelDate"]}, reverse=True),
        # every month somebody travelled in, newest first: the "Travel month" picker lists exactly these, whatever is filtered
        "months": [{"month": k, "count": n} for k, n in sorted(months, reverse=True)],
        # travelled but no travel date on file (e.g. deployed without a ticket date): the picker's last entry, so nobody is unfindable
        "undated": sum(1 for r in rows if not r["travelDate"]),
        "clients": names(r["client"] for r in rows),
        "orders": sorted({r["orderId"]: {"id": r["orderId"], "ref": r["orderRef"], "job": r["job"], "client": r["client"]}
                          for r in rows}.values(), key=lambda r: (r["client"].lower(), (r["job"] or "").lower(), r["ref"] or "")),
        "agencies": names(r["agency"] for r in rows),
        "countries": names(r["country"] for r in rows),
    }
    return {"rows": kept, "total": len(kept), "summary": summary, "options": options}


@router.get("")
def travelled(wanted: Filters = Depends(query_filters), user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return build(read_rows(db, user), wanted)


def _cell(value):
    """Keep a spreadsheet from running text that starts like a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_table(rows: list[dict]) -> list[list]:
    return [list(EXPORT_COLUMNS)] + [[_cell(v) for v in (
        r["travellerId"] or "", r["travelDate"] or "", r["name"], r["ref"], r["client"], r["country"] or "", r["job"], r["orderRef"], r["agency"] or "",
        r["airline"] or "", r["pnr"] or "", r["recruiter"] or "", r["status"], ", ".join(r["documents"]["missing"]),
        r["passportNumber"] or "")] for r in rows]


@router.get("/export")
def export(format: Literal["xlsx", "csv"], wanted: Filters = Depends(query_filters), user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> Response:
    table = export_table(build(read_rows(db, user), wanted)["rows"])
    if format == "xlsx":
        from app.services import lead_xlsx
        payload = lead_xlsx.build_workbook([{"name": "Travelled candidates", "rows": table, "widths": lead_xlsx.fit_widths(table),
                                              "header": True, "freeze": True, "filter": True}])
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        out = StringIO()
        csv.writer(out, lineterminator="\r\n").writerows(table)
        payload = ("﻿" + out.getvalue()).encode("utf-8")   # a byte-order mark so Excel reads names correctly
        mime = "text/csv; charset=utf-8"
    return Response(payload, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="travelled-candidates.{format}"', "Cache-Control": "private, no-store"})
