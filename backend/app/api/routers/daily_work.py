"""Daily work: each recruiter's own workable candidates as one flat list, the supervisors' per-recruiter view, goal settings."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, DailyGoal, Role, User
from app.db.session import get_db
from app.services import daily_work

router = APIRouter(prefix="/api", tags=["daily-work"])


def _who(user: User, staff: str | None):
    """Whose candidates the caller may read: (supervisor, owner ids or a select of them for the rows, select of every recruiter or None)."""
    supervisor = user.role in (Role.admin, Role.manager)
    if supervisor:
        everyone = select(User.id).where(User.role == Role.staff)
        return supervisor, [staff] if staff not in (None, "all") else everyone, everyone
    if staff not in (None, "all", user.id):
        raise HTTPException(403, "You can only see your own candidates.")
    return supervisor, [user.id] if user.role == Role.staff else [], None


def _read(db: Session, user: User, staff: str | None, counts_of, rows_of):
    """Run the counts, the rows and (for supervisors) the roster in one round; returns (supervisor, selector, shown, roster, counts, rows)."""
    supervisor, selector, everyone = _who(user, staff)
    if supervisor:
        counts, roster, fetched = daily_work.read_together(
            db, lambda s: counts_of(s, everyone),
            lambda s: list(s.execute(select(User.id, User.name, User.active).where(User.role == Role.staff).order_by(User.name, User.id)).all()),
            lambda s: rows_of(s, selector))
        if staff not in (None, "all") and staff not in {r.id for r in roster}:
            raise HTTPException(404, "Recruiter not found")
        shown = [staff] if staff not in (None, "all") else [r.id for r in roster]
        return supervisor, selector, shown, roster, counts, fetched
    if not selector:
        return supervisor, selector, [], [], {}, []
    counts, fetched = daily_work.read_together(db, lambda s: counts_of(s, selector), lambda s: rows_of(s, selector))
    return supervisor, selector, selector, [], counts, fetched


@router.get("/daily-work")
def daily_work_today(staff: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Today's candidates. A recruiter gets their own candidates that still have prescreening to do (new, in process or needs review,
    with no application yet or one at stage 1; no daily cap, slots or deadlines): the ones still to work first, then "done today".
    Admin and Manager get every recruiter's, or one recruiter's for `staff=<id>`, plus a per-recruiter count (to work, done today).
    A read only: nothing is allocated.
    """
    now = datetime.now(timezone.utc)
    supervisor, selector, shown, roster, counts, fetched = _read(
        db, user, staff, lambda s, owners: daily_work.owner_counts(s, owners, now=now), lambda s, owners: daily_work.fetch_rows(s, owners, now=now))
    # Then the placements and open tasks behind the rows, side by side, and last the remarks.
    items = daily_work.load_rows(db, selector, now=now, parallel=True, fetched=fetched) if fetched else []
    summary = daily_work.summarise([counts.get(uid, (0, 0)) for uid in shown])
    result = {
        "agencyDate": daily_work.agency_today(now).isoformat(), "serverTime": now,
        "staffCount": len(shown), "summary": summary, "items": items,
        "truncated": len(items) < summary["total"] and len(items) >= daily_work.MAX_ROWS,
    }
    if supervisor:
        result["staff"] = [{"userId": r.id, "name": r.name, "active": r.active, "summary": daily_work.summarise([counts.get(r.id, (0, 0))])}
                           for r in roster if r.active or counts.get(r.id)]
    return result


@router.get("/follow-up")
def follow_up(staff: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Follow-up: the candidates a recruiter owns whose application is active at stage 2 to 11 (still going forward), stalest first.
    A closed application (a negative exit) or a deployed one (it shows on Travelled) is not listed. Same scoping as `/daily-work`:
    a recruiter sees their own, admin and manager everyone's or `staff=<id>`'s, with a per-recruiter count. A read only.
    """
    now = datetime.now(timezone.utc)
    supervisor, selector, shown, roster, counts, fetched = _read(
        db, user, staff, lambda s, owners: daily_work.follow_up_counts(s, owners), lambda s, owners: daily_work.fetch_follow_up(s, owners))
    items = daily_work.load_follow_up(db, selector, fetched=fetched) if fetched else []
    total = sum(counts.get(uid, 0) for uid in shown)
    result = {"serverTime": now, "staffCount": len(shown), "summary": {"total": total}, "items": items,
              "truncated": len(items) < total and len(items) >= daily_work.MAX_ROWS}
    if supervisor:
        result["staff"] = [{"userId": r.id, "name": r.name, "active": r.active, "summary": {"total": counts.get(r.id, 0)}}
                           for r in roster if r.active or counts.get(r.id)]
    return result


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
def daily_goals(db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))):
    """The agency defaults, and for every recruiter the goals in force and which ones are their own override."""
    staff = list(db.execute(select(User.id, User.name, User.active).where(User.role == Role.staff).order_by(User.name, User.id)).all())
    goals = daily_work.load_goals(db, [s.id for s in staff] + ["__agency__"])
    return {
        "default": _goals_json(goals["__agency__"]),
        "staff": [{"userId": s.id, "name": s.name, "active": s.active, "goals": _goals_json(goals[s.id]),
                   "overrides": sorted(goals[s.id]["own"])} for s in staff],
    }


@router.put("/daily-goals/default")
def set_default_goals(body: GoalIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    _apply(db, None, body, user)
    db.commit()
    return daily_goals(db, user)["default"]


@router.put("/daily-goals/staff/{staff_id}")
def set_staff_goals(staff_id: str, body: GoalIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    person = db.get(User, staff_id)
    if person is None or person.role != Role.staff:
        raise HTTPException(404, "Recruiter not found")
    _apply(db, staff_id, body, user)
    db.commit()
    return next(s for s in daily_goals(db, user)["staff"] if s["userId"] == staff_id)


@router.delete("/daily-goals/staff/{staff_id}")
def reset_staff_goals(staff_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    """Remove the person's own goals so the agency defaults apply again."""
    person = db.get(User, staff_id)
    if person is None or person.role != Role.staff:
        raise HTTPException(404, "Recruiter not found")
    db.execute(delete(DailyGoal).where(DailyGoal.user_id == staff_id))
    db.add(AuditEntry(actor_id=user.id, action="Reset recruiter daily goals to the agency default", entity_type="daily_goal", entity_id=staff_id))
    db.commit()
    return next(s for s in daily_goals(db, user)["staff"] if s["userId"] == staff_id)
