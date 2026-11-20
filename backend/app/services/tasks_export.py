"""The Tasks page as an Excel sheet: the table the recruiter sees, with every detail, one row per candidate.

    S.No | DOC | Recruiter | Job | Client | Country | Candidate | Care ID | Agency | Advance | Stage | Today | Remark | Notes

The rows are the screen's own: the daily-work rows and the follow-up rows of `/api/daily-work` and `/api/follow-up` (same scoping), merged
and ordered exactly like `taskRows` in frontend/src/features/daily-work/daily-work-model.ts (candidates nobody touched today first, then the
ones with a stage waiting, then the furthest along; a candidate in both lists appears once, from the daily list).

* DOC: the placement's date of commencement as DD-MMM-YYYY text (blank when none).
* Agency: the recorded agency returned with the Tasks row.
* Advance: the client tracker's Advance cell (`advance_text.advance_cell` over the stage-9 record); blank when none.
* Stage: "Stage 10 - Visa arrival + Google review", or "Initial call" before a stage starts.
* Today shows times for calls and stage movement, or the latest note time on a notes-only day.
* Remark: the newest remark on the row (what the Remarks select holds; typed notes are never a remark).
* Notes: ALL the candidate's notes (typed in the NOTES column), newest first, one per line: "11:40 Asha - text" for today, "18 Nov 09:15 Asha - text" for an earlier day.
Text is written as inline strings, which Excel never evaluates, so a value such as `=cmd|...` stays text.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.db.models import ApplicationStage, User
from app.domain.stages import TOTAL_STAGES, stage_by_no
from app.services import agency_settings, daily_work, lead_xlsx
from app.services.advance_text import advance_cell

COLUMNS = ("S.No", "DOC", "Recruiter", "Job", "Client", "Country", "Candidate", "Care ID", "Agency", "Advance", "Stage", "Today", "Remark", "Notes")
SHEET_NAME = "Tasks"
WIDTHS = [7, 14, 18, 24, 24, 16, 30, 12, 18, 22, 36, 20, 50, 60]
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def doc_text(value: str | date | None) -> str:
    """'2026-12-05' -> '05-Dec-2026'; blank for no date."""
    if not value:
        return ""
    day = value if isinstance(value, date) else date.fromisoformat(str(value))
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _day(moment) -> str:
    return daily_work.agency_today(moment).isoformat() if isinstance(moment, datetime) else ""


def _clock(moment) -> str:
    return moment.astimezone(daily_work.agency_zone()).strftime("%H:%M") if isinstance(moment, datetime) else ""


def note_line(note: dict, today: str) -> str:
    """One note of the Notes cell: the time (with the date when it is not today), the author and the text."""
    at = note.get("at")
    if isinstance(at, str):   # a JSON row (the tests' listing) carries the time as text
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    when = _clock(at) if _day(at) == today else (at.astimezone(daily_work.agency_zone()).strftime("%d %b %H:%M") if isinstance(at, datetime) else "")
    return f"{when} {note.get('by') or ''} - {note.get('body') or ''}".strip()


def merge_rows(daily: list[dict], follow: list[dict], today: str) -> list[dict]:
    """`taskRows` of the screen: one dict per candidate (the daily row, else the follow-up row) with `moved_at`, in the screen's order."""
    merged: dict[str, dict] = {}
    moved_today: dict[str, datetime] = {}
    ended: list[dict] = []   # candidates whose placement ended (the page's Declined / Rejected / Travelled views): after the rest, in the page's order
    for item in daily:
        if (item.get("outcome") or {}).get("kind", "current") != "current":
            ended.append({**item, "called": False, "moved": False, "moved_at": None, "touched": 0})
            continue
        merged[item["candidateId"]] = item
    for item in follow:
        if item["candidateId"] not in merged:
            merged[item["candidateId"]] = {**item, "kind": "stage", "waiting": False, "status": "open", "completedAt": None}
    for item in (*daily, *follow):
        at = item.get("stageMovedAt")
        if at and _day(at) == today:
            cid = item["candidateId"]
            moved_today[cid] = max(at, moved_today.get(cid, at))
    rows = []
    for item in merged.values():
        called = _day(item["calledAt"]) == today if "calledAt" in item else item["status"] == "done" and item["kind"] == "call"
        moved = item["candidateId"] in moved_today or ("stageMovedAt" not in item and item["status"] == "done" and item["kind"] == "stage")
        remarked = bool(item.get("todayNotes")) or (bool(item.get("remark")) and _day(item["remark"]["at"]) == today)
        rows.append({**item, "called": called, "moved": moved, "remarked": remarked, "moved_at": moved_today.get(item["candidateId"]),
                     "touched": 1 if called or moved or remarked else 0})
    # list.sort is stable, like the screen's: the merge order breaks ties.
    rows.sort(key=lambda r: (r["touched"], -int(bool(r["waiting"])), -(r["stage"] or 0)))
    return rows + ended


def today_text(row: dict) -> str:
    parts = []
    if row["called"]:
        parts.append(" ".join(p for p in ("Called", _clock(row.get("calledAt") or row.get("completedAt"))) if p))
    if row["moved"]:
        parts.append(" ".join(p for p in ("Stage moved", _clock(row.get("moved_at") or row.get("completedAt"))) if p))
    if not parts and row.get("remarked"):
        note = next(iter(row.get("todayNotes") or []), row.get("remark") or {})
        parts.append(f"Note {_clock(note.get('at'))}".strip())
    return ", ".join(parts)


def stage_text(stage: int | None) -> str:
    if not stage or not 1 <= stage <= TOTAL_STAGES:
        return "Initial call"
    return f"Stage {stage} - {stage_by_no(stage).title}"


def outcome_text(outcome: dict) -> str:
    """'Declined at 5 \u00b7 Offer letter - Offer declined by the candidate - by Asha': what happened, where it ended, why and who recorded it."""
    stage = outcome.get("stage")
    where = f" at {stage} \u00b7 {stage_by_no(stage).title}" if stage and 1 <= stage <= TOTAL_STAGES else ""
    reason = f" - {outcome['reason']}" if outcome.get("reason") else ""
    by = f" - by {outcome['by']}" if outcome.get("by") else ""
    return f"{outcome.get('label') or ''}{where}{reason}{by}".strip()


def read_extras(db, rows: list[dict]) -> tuple[dict, dict, str]:
    """(stage-9 facts by application id, recruiter name by id, default advance currency): three statements, one round."""
    application_ids = [r["applicationId"] for r in rows if r.get("applicationId")]
    owner_ids = sorted({r["ownerId"] for r in rows if r.get("ownerId")})
    facts, owners, currency = daily_work.read_together(
        db,
        lambda s: daily_work.stage_facts(s.execute(daily_work.stage_facts_query(ApplicationStage.application_id.in_(application_ids))).all()) if application_ids else {},
        lambda s: dict(s.execute(select(User.id, User.name).where(User.id.in_(owner_ids))).all()) if owner_ids else {},
        lambda s: agency_settings.default_advance_currency(s))
    return facts, owners, currency


def table(db, daily: list[dict], follow: list[dict], today: str) -> list[list]:
    """The header and one list per task row."""
    rows = merge_rows(daily, follow, today)
    facts, owners, currency = read_extras(db, rows)

    def cell(value):
        return "".join(ch for ch in value if ord(ch) >= 32 or ch in "\t\n\r") if isinstance(value, str) else value

    out = [list(COLUMNS)]
    for number, row in enumerate(rows, 1):
        remark = (row.get("remark") or {}).get("body") or ""
        notes = "\n".join(note_line(note, today) for note in sorted(row.get("notes") or row.get("todayNotes") or [], key=lambda n: n["at"], reverse=True))
        out.append([cell(v) for v in (
            number, doc_text(row.get("commencementDate")), owners.get(row.get("ownerId")) or "", row.get("position") or "",
            row.get("client") or "", row.get("country") or "", row.get("candidate") or "", row.get("candidateCareId") or "",
            row.get("agency") or "", advance_cell(facts.get(row.get("applicationId")), currency) or "",
            outcome_text(row["outcome"]) if (row.get("outcome") or {}).get("kind", "current") != "current" else stage_text(row.get("stage")),
            today_text(row), remark.strip(), notes)])
    return out


def xlsx_bytes(rows: list[list]) -> bytes:
    return lead_xlsx.build_workbook([{"name": SHEET_NAME, "rows": rows, "widths": WIDTHS, "header": True, "freeze": True, "grid": True}])
