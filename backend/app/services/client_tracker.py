"""The client tracker: one running list of every placement still in progress (and, optionally, the ones that travelled), grouped by client.

It is the Excel sheet the clients ask for, built only from what the CRM already stores:

    S.No | Client | Country | Name | Position | Ref | Advance | Stage | Remarks

* Client / Country / Position: the placement's order (the employer company's name, the order's country, else the company's, and
  the order's job title).
* Name: the candidate. Ref: the candidate's referral agency (the assignment form's agency value, see agency_display).
* Advance: read only from the placement's stage-8 advance record (never from invoices or receipts), worded by
  advance_text.advance_cell: "60k Collected", "10k collected/50k" (collected / total service charge), "Pending/45k", "Pending/10k"
  (advance expected), "Pending"; blank when there is no stage-8 record. Every role sees the same cell.
* Stage: the placement's current stage in plain words, or "Travelled on 11th NOV 2026" for a deployed placement.
* Remarks: the newest remark written on the placement's tasks (else, for a placement still at its first stage, the newest remark on
  the candidate's initial call); "Travelled" for a deployed placement with no remark. Blank when there is nothing to say.

Which placements: status active (any stage, a candidate mapped to an order and still at the initial call included) and deployed
(unless left out). Closed placements (rejected, declined, not travelling...) never appear. A recruiter sees the placements they own;
admin and MD see all (the rule of the travelled report).

Cost: the placements, the stage-8 / stage-11 records, the newest remarks and the agency's default currency are independent reads,
run side by side: one round trip whatever the number of rows. Filters and the S.No numbering are
applied in memory on those rows, so changing a filter costs nothing.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from io import StringIO

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Candidate, CandidateStatus, Company, Designation, Order, Remark, Task, User,
)
from app.domain.stages import TOTAL_STAGES
from app.services import agency_settings
from app.services.agency_display import agency_display_expression
from app.services.advance_text import advance_cell
from app.services.parallel_reads import parallel_reads
from app.services.response_cache import cached, scope_of

COLUMNS = ("S.No", "Client", "Country", "Name", "Position", "Ref", "Advance", "Stage", "Remarks")
SHEET_NAME = "Client tracker"
# The widths of the client's sheet, in characters, one per column. Wide enough for the real names and job titles on it
# ("KRISHNAMOORTHY THIRUVENGADAM", "Dosa & parotta master"); the S.No and Country headers leave room for the filter buttons.
# Anything longer wraps inside its cell (the grid style) instead of being cut off by the next column.
WIDTHS = [9, 24, 14, 34, 24, 16, 24, 32, 50]

# The placement's current stage in plain words, as the client's sheet writes it: what the placement is waiting on.
STAGE_WORDS = {
    1: "Prescreening",
    2: "CV to be forwarded to client",
    3: "Interview to be scheduled",
    4: "Waiting for interview result",
    5: "Waiting for signed offer",
    6: "Medical in progress",
    7: "Waiting for passport",
    8: "Waiting for advance",
    9: "Waiting for visa",
    10: "Waiting for stamp",
    11: "Waiting for air ticket",
}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

# What the rows are built from; a change to any of them drops the kept answer at once.
TOPICS = ("applications", "candidates", "orders", "companies", "designations", "reference_agencies", "tasks", "reports", "settings")
SECONDS = 30


# ------------------------------------------------------------------------------------------------ wording

def _text(value) -> str:
    return " ".join(str(value or "").split())


def ordinal(day: int) -> str:
    return f"{day}{'th' if 10 <= day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')}"


def travel_words(travel_date: str | None) -> str:
    """'Travelled on 11th NOV 2026'; plain 'Travelled' when the travel date is not on file."""
    if not travel_date:
        return "Travelled"
    year, month, day = (int(part) for part in travel_date.split("-"))
    return f"Travelled on {ordinal(day)} {_MONTHS[month - 1]} {year}"


# ------------------------------------------------------------------------------------------------ filters

class Filters:
    """The query string, checked once so the list and the downloads read it the same way."""

    def __init__(self, client: str | None = None, country: str | None = None, agency: str | None = None, stage: int | None = None,
                 include_travelled: bool = True):
        if stage is not None and not 1 <= stage <= TOTAL_STAGES:
            raise HTTPException(422, f"The stage must be between 1 and {TOTAL_STAGES}.")
        self.client, self.country, self.agency = (_text(client) or None, _text(country) or None, _text(agency) or None)
        self.stage = stage
        self.include_travelled = bool(include_travelled)

    def keeps(self, row: dict) -> bool:
        if row["travelled"] and (not self.include_travelled or self.stage is not None):
            return False    # a stage filter is about placements still in progress
        if self.stage is not None and row["stageNo"] != self.stage:
            return False
        for wanted, have in ((self.client, row["client"]), (self.country, row["country"]), (self.agency, row["referral"])):
            if wanted and (have or "").lower() != wanted.lower():
                return False
        return True


def query_filters(client: str | None = None, country: str | None = None, agency: str | None = None, stage: int | None = None,
                  include_travelled: bool = True) -> Filters:
    return Filters(client, country, agency, stage, include_travelled)


# ------------------------------------------------------------------------------------------------ reading

def read_rows(db: Session, user: User) -> list[dict]:
    """Every placement the user may see, in sheet order (client, order, candidate), without S.No. Shared between requests: read only."""
    return cached(("client-tracker", scope_of(db), user.id if user.role.value == "staff" else None), TOPICS, SECONDS,
                  lambda: _read_rows(db, user))


def _scope(user: User) -> list:
    clauses = [or_(Application.status == ApplicationStatus.deployed,
                   (Application.status == ApplicationStatus.active) & (Candidate.status != CandidateStatus.not_interested))]
    if user.role.value == "staff":
        clauses.append(Application.owner_id == user.id)
    return clauses


def _read_rows(db: Session, user: User) -> list[dict]:
    placements = (
        select(Application.id, Application.candidate_id, Application.current_stage, Application.status, Candidate.name,
               agency_display_expression().label("agency"), Company.name, Company.country, Order.id, Order.ref, Order.country,
               Designation.title)
        .select_from(Application)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Order, Order.id == Application.order_id)
        .join(Company, Company.id == Order.company_id)
        .join(Designation, Designation.id == Order.designation_id)
        .where(*_scope(user)))
    app_ids = select(Application.id).join(Candidate, Candidate.id == Application.candidate_id).where(*_scope(user))
    candidate_ids = select(Application.candidate_id).join(Candidate, Candidate.id == Application.candidate_id).where(*_scope(user))
    # Stage 8 (the advance) and stage 11 (the ticket and travel date) records, never a reopened one.
    stages = (select(ApplicationStage.application_id, ApplicationStage.stage, ApplicationStage.attempt, ApplicationStage.completed_at,
                     ApplicationStage.amount, ApplicationStage.amount_currency, ApplicationStage.data)
              .where(ApplicationStage.stage.in_((8, 11)), ApplicationStage.reopened_reason.is_(None),
                     ApplicationStage.application_id.in_(app_ids)))
    # The newest remark of every placement's tasks; tasks with no placement (the initial call) give one per candidate.
    remarks = (select(Task.application_id, Task.candidate_id, Remark.body)
               .select_from(Remark).join(Task, Task.id == Remark.task_id)
               .where(Task.candidate_id.in_(candidate_ids), or_(Task.application_id.is_(None), Task.application_id.in_(app_ids)))
               .distinct(Task.application_id, Task.candidate_id)
               .order_by(Task.application_id, Task.candidate_id, Remark.created_at.desc(), Remark.id))
    reads = [lambda s: s.execute(placements).all(), lambda s: s.execute(stages).all(), lambda s: s.execute(remarks).all(),
             lambda s: agency_settings.default_advance_currency(s)]
    found, stage_rows, remark_rows, default_currency = parallel_reads(db, *reads)
    return _assemble(found, stage_rows, remark_rows, default_currency)


def _assemble(found, stage_rows, remark_rows, default_currency: str) -> list[dict]:
    from app.services.daily_work import stage_facts   # the stage-8 advance rule of the Tasks screen
    advance_records = [row for row in stage_rows if row[1] == 8]
    advances = stage_facts(advance_records)
    travel: dict[str, tuple] = {}
    for application_id, stage, attempt, completed_at, _amount, _currency, data in stage_rows:
        if stage == 11 and (application_id not in travel or (attempt, completed_at) > travel[application_id][0]):
            travel[application_id] = ((attempt, completed_at), (data or {}).get("travelDate"))
    by_application: dict[str, str] = {}
    by_candidate: dict[str, str] = {}
    for application_id, candidate_id, body in remark_rows:
        (by_application if application_id else by_candidate)[application_id or candidate_id] = _text(body)
    rows = []
    for (app_id, candidate_id, stage_no, status, name, agency, company, company_country, order_id, order_ref, order_country,
         job) in found:
        deployed = getattr(status, "value", status) == "deployed"
        raw_date = travel.get(app_id, (None, None))[1]
        day = raw_date if isinstance(raw_date, str) and _DAY.fullmatch(raw_date) and _valid_day(raw_date) else None
        remark = by_application.get(app_id) or (by_candidate.get(candidate_id) if stage_no == 1 and not deployed else None)
        rows.append({
            "applicationId": app_id, "candidateId": candidate_id, "orderId": order_id, "orderRef": order_ref,
            "client": _text(company), "country": _text(order_country) or _text(company_country), "name": _text(name),
            "position": _text(job), "referral": _text(agency),
            "advance": advance_cell(advances.get(app_id), default_currency) or "",
            "stage": travel_words(day) if deployed else STAGE_WORDS.get(stage_no, ""),
            "remarks": remark or ("Travelled" if deployed else ""),
            "stageNo": stage_no, "travelled": deployed, "travelDate": day if deployed else None,
        })
    rows.sort(key=lambda r: (r["client"].lower(), r["orderRef"] or "", r["name"].lower(), r["applicationId"]))
    return rows


def _valid_day(raw: str) -> bool:
    try:
        date.fromisoformat(raw)
    except ValueError:
        return False
    return True


# ------------------------------------------------------------------------------------------------ the answer

def build(rows: list[dict], wanted: Filters) -> dict:
    """The filtered rows numbered 1..N down the sheet (the numbers follow the filter), and the lists the screen's pickers offer."""
    kept = [{"sNo": number, **row} for number, row in enumerate((row for row in rows if wanted.keeps(row)), 1)]

    def names(key: str) -> list[str]:
        return sorted({row[key] for row in rows if row[key]}, key=str.lower)

    return {"columns": list(COLUMNS), "rows": kept, "total": len(kept),
            "options": {"clients": names("client"), "countries": names("country"), "agencies": names("referral"),
                        "stages": [{"no": no, "label": label} for no, label in STAGE_WORDS.items()]}}


def export_table(rows: list[dict], *, for_csv: bool = False) -> list[list]:
    """The header and one list per row, in the sheet's columns. A csv guards text that Excel would run as a formula."""
    def cell(value):
        if isinstance(value, str):
            value = "".join(ch for ch in value if ord(ch) >= 32)   # no control characters (a stray bell would corrupt the sheet's XML)
        if for_csv and isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
        return value
    return [list(COLUMNS)] + [[cell(v) for v in (r["sNo"], r["client"], r["country"], r["name"], r["position"], r["referral"],
                                                 r["advance"], r["stage"], r["remarks"])] for r in rows]


def xlsx_bytes(rows: list[dict]) -> bytes:
    from app.services import lead_xlsx
    return lead_xlsx.build_workbook([{"name": SHEET_NAME, "rows": export_table(rows), "widths": WIDTHS, "header": True, "freeze": True, "grid": True}])


def csv_bytes(rows: list[dict]) -> bytes:
    out = StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(export_table(rows, for_csv=True))
    return ("﻿" + out.getvalue()).encode("utf-8")   # a byte-order mark so Excel reads names correctly
