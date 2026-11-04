"""Daily candidate quota and goals: each recruiter's list for today, the supervisors' per-staff view, goal editing."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, DailyGoal, Role, User
from app.db.session import get_db, writes_on_get
from app.services import daily_work

router = APIRouter(prefix="/api", tags=["daily-work"])


@router.get("/daily-work")
@writes_on_get  # opening the screen tops the quota up, so it needs a real transaction
def daily_work_today(staff: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Staff get their own list. Admin and Manager get every recruiter's progress and, for `staff=<id>` or `all`,
    the matching rows. Opening this tops the quota up first (idempotent), so no one has to wait for the worker.
    """
    now = datetime.now(timezone.utc)
    supervisor = user.role in (Role.admin, Role.manager)
    roster, everyone = [], []
    if supervisor:
        roster = list(db.execute(select(User.id, User.name, User.active).where(User.role == Role.staff)
                                 .order_by(User.name, User.id)).all())
        everyone = [r.id for r in roster if r.active]
        if staff in (None, "all"):
            shown = everyone
        elif staff in {r.id for r in roster}:
            shown = [staff]
        else:
            raise HTTPException(404, "Staff member not found")
        scope = everyone  # summaries cover every recruiter, whichever rows were asked for
    else:
        if staff not in (None, "all", user.id):
            raise HTTPException(403, "You can only see your own daily tasks.")
        scope = shown = [user.id] if user.role == Role.staff else []
    if scope:
        daily_work.ensure_allocations(db, scope, now=now)
    today_date = daily_work.agency_today(now)
    today = today_date.isoformat()
    progress = daily_work.goal_progress(db, scope, today_date, persist=True)
    db.commit()
    rows = daily_work.load_rows(db, scope, now=now)
    by_owner: dict[str, list[dict]] = {}
    for row in rows:
        by_owner.setdefault(row["ownerId"], []).append(row)
    items = [r for r in rows if r["ownerId"] in shown]
    blocks = [progress[uid] for uid in shown]
    # The goals shown are per recruiter: one person's own, or the average per recruiter for the team. The summed team
    # figures travel separately so the screen can label them as a total, never as the quota.
    fallback_goals = daily_work.load_goals(db, ["__agency__"])["__agency__"]
    fallback = {"candidates": {"target": fallback_goals["candidates"][0], "morning": fallback_goals["candidates"][1],
                               "evening": fallback_goals["candidates"][0] - fallback_goals["candidates"][1],
                               "done": 0, "morningDone": 0, "eveningDone": 0},
                "forwardedToClient": {"target": fallback_goals["forwarded_to_client"], "actual": 0},
                "stageMoves": {"target": fallback_goals["stage_moves"], "actual": 0}}
    goals, targets_vary = (blocks[0], False) if len(blocks) == 1 else daily_work.average_goals(blocks, fallback)
    team = daily_work.add_goal_totals(blocks)
    quota = goals["candidates"]
    off_today = daily_work.shifts_and_days_off(db, scope, today_date)[1] if scope else set()
    if scope:
        off_today |= set(db.scalars(select(User.id).where(User.id.in_(scope), User.on_leave.is_(True))))
    shortfalls = daily_work.work_shortfalls(db, progress, by_owner, today, off_today, scope)
    result = {
        "agencyDate": today, "serverTime": now,
        "dailyQuota": quota["target"], "morningQuota": quota["morning"], "eveningQuota": quota["evening"],
        "goalsMode": "person" if len(blocks) == 1 else "average", "targetsVary": targets_vary,
        "staffCount": len(shown), "teamGoals": team,
        "shortfall": shortfalls.get(shown[0]) if len(shown) == 1 else None,
        "offToday": bool(len(shown) == 1 and shown[0] in off_today),
        "summary": daily_work.summarise(items, now, today), "items": items, "goals": goals,
        # Hourly checkpoints only make sense for one person's day.
        "checkpoints": daily_work.checkpoints(items, now, today) if len(shown) == 1 else None,
    }
    if supervisor:
        result["staff"] = [{"userId": r.id, "name": r.name, "active": r.active, "offToday": r.id in off_today, "shortfall": shortfalls.get(r.id),
                            "summary": daily_work.summarise(by_owner.get(r.id, []), now, today),
                            "goals": progress.get(r.id)}
                           for r in roster if r.active or by_owner.get(r.id)]
    return result


# ------------------------------------------------------------------ goals

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
    db.add(AuditEntry(actor_id=actor.id, action="Changed agency daily goals" if user_id is None else "Changed staff daily goals",
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
        raise HTTPException(404, "Staff member not found")
    _apply(db, staff_id, body, user)
    db.commit()
    return next(s for s in daily_goals(db, user)["staff"] if s["userId"] == staff_id)


@router.delete("/daily-goals/staff/{staff_id}")
def reset_staff_goals(staff_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    """Remove the person's own goals so the agency defaults apply again."""
    person = db.get(User, staff_id)
    if person is None or person.role != Role.staff:
        raise HTTPException(404, "Staff member not found")
    db.execute(delete(DailyGoal).where(DailyGoal.user_id == staff_id))
    db.add(AuditEntry(actor_id=user.id, action="Reset staff daily goals to the agency default", entity_type="daily_goal", entity_id=staff_id))
    db.commit()
    return next(s for s in daily_goals(db, user)["staff"] if s["userId"] == staff_id)
