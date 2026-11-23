"""Daily work: each recruiter's own workable candidates as one flat list, the supervisors' per-recruiter view, goal settings."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_role, require_admin_level
from app.db.models import AuditEntry, DailyGoal, Role, User, not_deleted_user
from app.db.session import get_db
from app.services import daily_work, tasks_export
from app.services.parallel_reads import parallel_reads
from app.services.response_cache import cached, scope_of
from app.core.roles import is_admin_level

# Both lists read candidates, placements (with their order, company, client and job title), stage history, open tasks, task
# remarks and the recruiter roster. A change to any of those drops the memo at once; the time limit covers what no hook can see
# (another server, the clock: "done today" turns over at agency midnight).
WORK_TOPICS = ("candidates", "applications", "tasks", "users", "orders", "companies", "clients", "designations")
WORK_SECONDS = 10

router = APIRouter(prefix="/api", tags=["daily-work"])


def _who(user: User, staff: str | None):
    """Whose candidates the caller may read: (supervisor, owner ids or a select of them for the rows, select of every recruiter or None)."""
    supervisor = is_admin_level(user)
    if supervisor:
        everyone = select(User.id).where(User.role == Role.staff, not_deleted_user())
        return supervisor, [staff] if staff not in (None, "all") else everyone, everyone
    if staff not in (None, "all", user.id):
        raise HTTPException(403, "You can only see your own candidates.")
    return supervisor, [user.id] if user.role == Role.staff else [], None


def _roster(session):
    return list(session.execute(select(User.id, User.name, User.active).where(User.role == Role.staff, not_deleted_user())
                                .order_by(User.name, User.id)).all())


def _shown(supervisor, staff, selector, roster) -> list:
    """The recruiters the page covers; a supervisor naming one who does not exist gets a 404."""
    if not supervisor:
        return selector
    if staff not in (None, "all"):
        if staff not in {r.id for r in roster}:
            raise HTTPException(404, "Recruiter not found")
        return [staff]
    return [r.id for r in roster]


def _run(db, named: dict) -> dict:
    """Run these independent reads together (one round trip) and return their results by name."""
    return dict(zip(named, daily_work.read_together(db, *named.values()))) if named else {}


@router.get("/daily-work")
def daily_work_today(staff: str | None = None, view: Literal["current", "declined", "rejected", "travelled", "all"] = "current",
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Today's candidates. A recruiter gets their own candidates that still have prescreening to do (new, in process or needs review,
    with no application yet or one at stage 1; no daily cap, slots or deadlines): the ones still to work first, then "done today".
    Admin and MD get every recruiter's, or one recruiter's for `staff=<id>`, plus a per-recruiter count (to work, done today).
    A read only: nothing is allocated.

    `view` (default `current`, exactly the list above) also reaches the candidates whose placement ended: `declined` (their own no),
    `rejected` (any other negative closure; both are kept off today's list), `travelled` (a deployed placement) and `all` (current plus those three). Every item carries
    `outcome` {kind, label, reason, stage, at}; `viewCounts` {current, declined, rejected, travelled} counts each view for the same scope.
    """
    # Every open tab refetches when a task or candidate changes; tabs of the same person (or a second supervisor) share one build.
    return cached(("daily-work", scope_of(db), user.id, user.role.value, staff, view), WORK_TOPICS, WORK_SECONDS, lambda: _daily_work(db, user, staff, view))


def _daily_work(db: Session, user: User, staff: str | None, view: str = "current") -> dict:
    now = datetime.now(timezone.utc)
    supervisor, selector, _everyone = _who(user, staff)
    kinds = daily_work.outcome_kinds(view)
    current = view in ("current", "all")
    wide = isinstance(selector, list)     # a recruiter, or one recruiter picked by a supervisor: everything in one round of reads
    shown, roster, counts, rows, outcome_rows, counts_by_view = [], [], {}, [], [], {"declined": 0, "rejected": 0, "travelled": 0}
    items: list[dict] = []
    outcome_items: list[dict] = []
    if supervisor or selector:
        named: dict = {}
        if not current:
            named["counts"] = lambda s: daily_work.owner_counts(s, selector, now=now)
        elif wide:
            named.update(zip(("rows", "placed", "tasks", "facts", "remarks"), daily_work.wide_reads(selector, now=now)))
        else:
            named["rows"] = lambda s: daily_work.fetch_rows(s, selector, now=now)
        if supervisor:
            named["roster"] = _roster
        named["views"] = lambda s: daily_work.view_counts(s, selector)
        if kinds:
            named["outcome"] = lambda s: daily_work.fetch_outcome_rows(s, selector, kinds)
        got = _run(db, named)
        roster = got.get("roster", [])
        shown = _shown(supervisor, staff, selector, roster)
        counts_by_view = got["views"]
        rows = got.get("rows", [])
        outcome_rows = got.get("outcome", [])
        if current:
            # The rows ARE the counts unless the row cap cut them off (then one grouped statement tells the exact numbers).
            counts = daily_work.counts_from_rows(rows) if len(rows) < daily_work.MAX_ROWS else daily_work.owner_counts(db, selector, now=now)
        else:
            counts = got["counts"]
        if view == "all":   # a safety net: a declined / rejected candidate is parked (never on today's list too), but one is never listed twice
            listed = {row[0].id for row in rows}
            outcome_rows = [row for row in outcome_rows if row[0].id not in listed]
        # What needs the rows' ids (everything the recruiter's own page already read in the first round): one more round, side by side.
        later: dict = {}
        if rows and not wide:
            later.update(zip(("placed", "tasks", "facts", "remarks"), daily_work.placement_reads([row[0].id for row in rows], now=now)))
        if outcome_rows:
            later.update(zip(("outcome_remarks", "outcome_facts", "outcome_by"), daily_work.outcome_reads([row[1].id for row in outcome_rows if row[1] is not None], [row[0].id for row in outcome_rows])))
        got.update(_run(db, later))
        if rows:
            items = daily_work.assemble_rows(rows, got["placed"], got["tasks"], got["facts"], got["remarks"], now=now)
        if outcome_rows:
            outcome_items = daily_work.assemble_outcome_rows(outcome_rows, got["outcome_remarks"], got["outcome_facts"], got["outcome_by"], now=now)
    summary = daily_work.summarise([counts.get(uid, (0, 0)) for uid in shown])
    listed_outcomes = sum(counts_by_view[kind] for kind in kinds)
    result = {
        "agencyDate": daily_work.agency_today(now).isoformat(), "serverTime": now,
        "staffCount": len(shown), "summary": summary, "view": view,
        "viewCounts": {"current": summary["total"], **counts_by_view},
        "items": items + outcome_items,
        "truncated": (current and len(items) < summary["total"] and len(items) >= daily_work.MAX_ROWS)
                     or (len(outcome_rows) >= daily_work.MAX_OUTCOME_ROWS and listed_outcomes > len(outcome_rows)),
    }
    if supervisor:
        result["staff"] = [{"userId": r.id, "name": r.name, "active": r.active, "summary": daily_work.summarise([counts.get(r.id, (0, 0))])}
                           for r in roster if r.active or counts.get(r.id)]
    return result


@router.get("/follow-up")
def follow_up(staff: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Follow-up: the candidates a recruiter owns whose application is active at stage 2 to 12 (still going forward), stalest first.
    A closed application (a negative exit) or a deployed one (it shows on Travelled) is not listed. Same scoping as `/daily-work`:
    a recruiter sees their own, admin and MD everyone's or `staff=<id>`'s, with a per-recruiter count. A read only.
    """
    return cached(("follow-up", scope_of(db), user.id, user.role.value, staff), WORK_TOPICS, WORK_SECONDS, lambda: _follow_up(db, user, staff))


def _follow_up(db: Session, user: User, staff: str | None) -> dict:
    now = datetime.now(timezone.utc)
    supervisor, selector, _everyone = _who(user, staff)
    wide = isinstance(selector, list)
    shown, roster, counts, items = [], [], {}, []
    if supervisor or selector:
        named: dict = {}
        if wide:
            named.update(zip(("rows", "remarks", "tasks", "facts"), daily_work.follow_up_wide_reads(selector, now=now)))
        else:
            named["rows"] = lambda s: daily_work.fetch_follow_up(s, selector)
        if supervisor:
            named["roster"] = _roster
        got = _run(db, named)
        roster = got.get("roster", [])
        shown = _shown(supervisor, staff, selector, roster)
        rows = got["rows"]
        counts = daily_work.follow_up_counts_from_rows(rows) if len(rows) < daily_work.MAX_ROWS else daily_work.follow_up_counts(db, selector)
        if rows and not wide:
            got.update(zip(("remarks", "tasks", "facts"), daily_work.read_together(db, *daily_work.follow_up_reads([app.id for app, *_ in rows], sorted({cand.id for _, cand, *_ in rows}), now=now))))
        if rows:
            items = daily_work.assemble_follow_up(rows, got["remarks"], got["tasks"], got["facts"], now=now)
    total = sum(counts.get(uid, 0) for uid in shown)
    result = {"serverTime": now, "staffCount": len(shown), "summary": {"total": total}, "items": items,
              "truncated": len(items) < total and len(items) >= daily_work.MAX_ROWS}
    if supervisor:
        result["staff"] = [{"userId": r.id, "name": r.name, "active": r.active, "summary": {"total": counts.get(r.id, 0)}}
                           for r in roster if r.active or counts.get(r.id)]
    return result


@router.get("/daily-work/export")
def tasks_export_file(format: Literal["xlsx"] = "xlsx", staff: str | None = None,
                      view: Literal["current", "declined", "rejected", "travelled", "all"] = "current",
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    The Tasks table as an Excel file (`tasks-<agency day>.xlsx`, sheet "Tasks"): S.No, DOC, Recruiter, Job, Client, Country, Candidate,
    Care ID, Agency, Advance, Stage, Today, Remark, Notes; one row per candidate in the order the screen lists them. `staff=<userId|all>` and
    the permissions are those of `GET /api/daily-work`: admin and MD any recruiter or all, a recruiter only themself. `view` is the
    page's view: the candidates whose placement ended read "Declined at 5 · Offer letter" in the Stage column.
    """
    daily = daily_work_today(staff=staff, view=view, db=db, user=user)
    follow = follow_up(staff=staff, db=db, user=user) if view in ("current", "all") else {"items": []}
    rows = tasks_export.table(db, daily["items"], follow["items"], daily["agencyDate"])
    return Response(tasks_export.xlsx_bytes(rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={
        "Content-Disposition": f'attachment; filename="tasks-{daily["agencyDate"]}.xlsx"', "Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ goals
# Settings kept for the weekly performance report's stage-move target; the Daily work screen no longer uses them.

class GoalIn(BaseModel):
    """Any subset of goals; `candidates` and `morning` go together. Omitted ones keep their current value."""
    model_config = ConfigDict(extra="forbid")
    candidates: int | None = Field(default=None, ge=1, le=50)
    morning: int | None = Field(default=None, ge=0, le=50)
    forwardedToClient: int | None = Field(default=None, ge=0, le=200)
    stageMoves: int | None = Field(default=None, ge=0, le=200)

    @model_validator(mode="after")
    def valid(self):
        if (self.candidates is None) != (self.morning is None):
            raise ValueError("Set the daily candidates and the morning share together.")
        if self.candidates is not None and self.morning > self.candidates:
            raise ValueError("The morning share cannot be larger than the daily candidates.")
        if self.candidates is None and self.forwardedToClient is None and self.stageMoves is None:
            raise ValueError("Nothing to change.")
        return self


def _goal_rows(db, user_id):
    return {g.metric: g for g in db.scalars(select(DailyGoal).where(
        DailyGoal.user_id.is_(None) if user_id is None else DailyGoal.user_id == user_id))}


def _apply(db, user_id, body: GoalIn, actor: User):
    rows = _goal_rows(db, user_id)
    wanted = []
    if body.candidates is not None:
        wanted.append(("candidates", body.candidates, body.morning))
    if body.forwardedToClient is not None:
        wanted.append(("forwarded_to_client", body.forwardedToClient, None))
    if body.stageMoves is not None:
        wanted.append(("stage_moves", body.stageMoves, None))
    for metric, target, morning in wanted:
        row = rows.get(metric)
        if row is None:
            db.add(DailyGoal(user_id=user_id, metric=metric, target=target, morning_target=morning, updated_by=actor.id))
        else:
            row.target, row.morning_target, row.updated_by = target, morning, actor.id
    db.add(AuditEntry(actor_id=actor.id, action="Changed agency daily goals" if user_id is None else "Changed recruiter daily goals",
                      entity_type="daily_goal", entity_id=user_id, meta=body.model_dump(exclude_none=True)))


def _goals_json(goals: dict) -> dict:
    return {"candidates": goals["candidates"][0], "morning": goals["candidates"][1],
            "evening": goals["candidates"][0] - goals["candidates"][1],
            "forwardedToClient": goals["forwarded_to_client"], "stageMoves": goals["stage_moves"]}


@router.get("/daily-goals")
def daily_goals(db: Session = Depends(get_db), user: User = Depends(require_admin_level())):
    """The agency defaults, and for every recruiter the goals in force and which ones are their own override."""
    # The roster and the (few) goal rows are independent: one round trip for both (every caller has already committed its change).
    staff, rows = parallel_reads(
        db, lambda session: list(session.execute(select(User.id, User.name, User.active).where(User.role == Role.staff, not_deleted_user()).order_by(User.name, User.id)).all()),
        lambda session: session.execute(select(DailyGoal.user_id, DailyGoal.metric, DailyGoal.target, DailyGoal.morning_target)).all())
    goals = daily_work.goals_from_rows(rows, [s.id for s in staff] + ["__agency__"])
    return {
        "default": _goals_json(goals["__agency__"]),
        "staff": [{"userId": s.id, "name": s.name, "active": s.active, "goals": _goals_json(goals[s.id]),
                   "overrides": sorted(goals[s.id]["own"])} for s in staff],
    }


@router.put("/daily-goals/default")
def set_default_goals(body: GoalIn, db: Session = Depends(get_db), user: User = Depends(require_admin_level())):
    _apply(db, None, body, user)
    db.commit()
    return daily_goals(db, user)["default"]


@router.put("/daily-goals/staff/{staff_id}")
def set_staff_goals(staff_id: str, body: GoalIn, db: Session = Depends(get_db), user: User = Depends(require_admin_level())):
    person = db.get(User, staff_id)
    if person is None or person.role != Role.staff:
        raise HTTPException(404, "Recruiter not found")
    _apply(db, staff_id, body, user)
    db.commit()
    return next(s for s in daily_goals(db, user)["staff"] if s["userId"] == staff_id)


@router.delete("/daily-goals/staff/{staff_id}")
def reset_staff_goals(staff_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin_level())):
    """Remove the person's own goals so the agency defaults apply again."""
    person = db.get(User, staff_id)
    if person is None or person.role != Role.staff:
        raise HTTPException(404, "Recruiter not found")
    db.execute(delete(DailyGoal).where(DailyGoal.user_id == staff_id))
    db.add(AuditEntry(actor_id=user.id, action="Reset recruiter daily goals to the agency default", entity_type="daily_goal", entity_id=staff_id))
    db.commit()
    return next(s for s in daily_goals(db, user)["staff"] if s["userId"] == staff_id)
