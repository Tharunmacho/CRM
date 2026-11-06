"""Daily work allocation at scale: statement budgets, set-based allocation against the per-recruiter reference, concurrency.

Uses the `desk` fixture of test_daily_work (a disposable PostgreSQL schema at head, a frozen Tuesday 08:30 IST clock).
The budgets are statements, not seconds: with a database a network hop away every sequential statement costs a round
trip, so the first call of a new day for 20 recruiters must not run one top-up per recruiter (it once ran 374).
"""
import threading
import time
from datetime import timedelta

from sqlalchemy import event, func, select, text

from app.db.models import Candidate, DailyGoalProgress, DailyWorkItem, Role, Source, User
from app.services import daily_work
from app.services.daily_work import _pool, ensure_allocations, pool_for, pool_waiting, slot_plan
from test_daily_work import TUESDAY, TZ, as_user, desk, mapped_candidates, owned_candidates  # noqa: F401  (desk is a fixture)
from test_database_naming import schema_database  # noqa: F401  (fixture the desk builds on)

STAFF = [f"s{n:02}" for n in range(20)]


def staff_with_candidates(env, per_staff=30, short=None):
    """20 recruiters who own `per_staff` unmatched candidates each (`short` maps a recruiter to a smaller count)."""
    short = short or {}
    with env.sessions() as db:
        db.add_all([User(id=uid, name=f"Staff {uid}", email=f"{uid}@example.test", role=Role.staff, active=True) for uid in STAFF])
        db.flush()
        db.add_all([
            Candidate(id=f"{uid}-c{n:02}", ref=f"CND-{uid}-{n:02}", name=f"Cand {uid} {n}", phone=f"93{i:03}{n:05}", source=Source.walk_in,
                      owner_id=uid, designations=["Welder"], created_at=TUESDAY - timedelta(days=100 - n))
            for i, uid in enumerate(STAFF) for n in range(short.get(uid, per_staff))])
        db.commit()


def counted(env, user_id, path="/api/daily-work"):
    as_user(env, user_id)
    seen = []
    record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))  # noqa: E731
    engine = env.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = env.client.get(path)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    return seen, response.json()


def item_tuples(db, owners=None):
    query = select(DailyWorkItem.owner_id, DailyWorkItem.slot_no, DailyWorkItem.session, DailyWorkItem.due_at, DailyWorkItem.kind,
                   DailyWorkItem.candidate_id, DailyWorkItem.task_id, DailyWorkItem.application_id, DailyWorkItem.stage).order_by(
        DailyWorkItem.owner_id, DailyWorkItem.slot_no)
    if owners:
        query = query.where(DailyWorkItem.owner_id.in_(owners))
    return [tuple(row) for row in db.execute(query)]


# ------------------------------------------------------------------ statement budgets

def test_the_first_call_of_the_day_for_twenty_recruiters_is_not_one_top_up_per_recruiter(desk):
    staff_with_candidates(desk)
    seen, body = counted(desk, "manager")
    assert len(seen) <= 30, [sql[:70] for sql in seen]
    assert len(body["items"]) == 20 * 5 and body["summary"]["total"] == 100
    assert sum("pg_advisory_xact_lock" in sql for sql in seen) == 1  # every lock in one statement
    assert sum(sql.startswith("INSERT INTO workflow_daily_work_items") for sql in seen) == 1  # every new item in one insert
    assert sum(sql.startswith("INSERT INTO workflow_daily_goal_progress") for sql in seen) == 1  # and every snapshot row
    with desk.sessions() as db:
        slots = {uid: sorted(r.slot_no for r in db.scalars(select(DailyWorkItem).where(DailyWorkItem.owner_id == uid))) for uid in STAFF}
        assert all(v == [1, 2, 3, 4, 5] for v in slots.values())
        assert db.scalar(select(func.count()).select_from(DailyGoalProgress)) >= 60


def test_a_repeat_call_runs_few_statements_even_with_a_short_handed_recruiter(desk):
    staff_with_candidates(desk, short={"s03": 2})  # s03 has fewer candidates than the quota, asha and ravi have none at all
    counted(desk, "manager")  # the first call allocates
    seen, body = counted(desk, "manager")
    assert len(seen) <= 12, [sql[:70] for sql in seen]
    # Nobody has anything left to hand out, so there is no lock, no top-up and no write on a repeat call.
    assert not any("pg_advisory_xact_lock" in sql or sql.startswith(("INSERT", "UPDATE")) for sql in seen), [sql[:60] for sql in seen]
    shortfall = {s["userId"]: s["shortfall"] for s in body["staff"]}
    assert shortfall["s03"]["allocated"] == 2 and shortfall["s00"] is None
    # A recruiter's own screen is cheaper still.
    seen, own = counted(desk, "asha")
    assert len(seen) <= 10, [sql[:70] for sql in seen]
    assert own["shortfall"]["allocated"] == 0


def test_a_candidate_who_arrives_for_a_short_handed_recruiter_is_picked_up_on_the_next_call(desk):
    staff_with_candidates(desk, short={"s03": 2})
    counted(desk, "manager")
    with desk.sessions() as db:
        db.add(Candidate(id="late", ref="CND-LATE", name="Late", phone="9400000001", source=Source.walk_in, owner_id="s03",
                         created_at=TUESDAY - timedelta(days=1)))
        db.commit()
    seen, body = counted(desk, "manager")
    assert any("pg_advisory_xact_lock" in sql for sql in seen)
    mine = [r for r in body["items"] if r["ownerId"] == "s03"]
    assert [r["candidateId"] for r in mine][-1] == "late" and len(mine) == 3


# ------------------------------------------------------------------ the set-based allocation equals the per-recruiter one

def legacy_allocate(db, now):
    """The allocation as it was before: one lock, used-slot read, pool read and savepoint insert per recruiter."""
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.exc import IntegrityError
    today = daily_work.agency_today(now, TZ)
    daily_work.reconcile(db, now=now, tz=TZ)
    people = list(db.scalars(select(User.id).where(User.active.is_(True), User.role == Role.staff, User.on_leave.is_(False)).order_by(User.id)))
    goals = daily_work.load_goals(db, people)
    shifts, off = daily_work.shifts_and_days_off(db, people, today)
    for uid in people:
        quota, morning = daily_work.quota_for(goals[uid])
        if uid in off:
            continue
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"daily-work:{uid}"})
        used = set(db.scalars(select(DailyWorkItem.slot_no).where(
            DailyWorkItem.owner_id == uid, DailyWorkItem.work_date == today, DailyWorkItem.status != "released")))
        need = quota - len(used)
        if need <= 0:
            continue
        pool = _pool(db, uid, need, today)
        start, pause = shifts[uid]
        free = [s for s in slot_plan(today, start, pause, max(quota, max(used, default=0)), morning, TZ) if s.no not in used]
        previous = None
        for slot, pick in zip(free, pool):
            due = slot.due_at
            if previous is not None or due <= now:
                due = max(due, (previous or now) + timedelta(hours=1))
                previous = due
            try:
                with db.begin_nested():
                    db.execute(insert(DailyWorkItem).values(
                        owner_id=uid, work_date=today, kind=pick["kind"], task_id=pick["task_id"], application_id=pick["application_id"],
                        candidate_id=pick["candidate_id"], stage=pick["stage"], slot_no=slot.no, session=slot.session, due_at=due, allocated_at=now))
            except IntegrityError:
                pass


def seed_mixed_day(env):
    """Recruiters with stage tasks, unmatched and called candidates, closed ones, a different quota and a half-used day."""
    mapped_candidates(env, 4, owner="asha")  # asha: 4 stage tasks (oldest first), then owned unmatched ones
    owned_candidates(env, 4, owner="asha", start=100)
    mapped_candidates(env, 2, owner="ravi", start=20)
    with env.sessions() as db:
        db.add_all([User(id=uid, name=uid.title(), email=f"{uid}@example.test", role=Role.staff, active=True) for uid in ("kim", "lee")])
        db.flush()
        db.add_all([
            Candidate(id=f"k{n}", ref=f"CND-K{n}", name=f"Kim {n}", phone=f"95000{n:05}", source=Source.walk_in, owner_id="kim",
                      created_at=TUESDAY - timedelta(days=50 - n),
                      call_called=n % 2 == 0, call_interested="yes" if n % 2 == 0 else None,
                      call_recorded_at=TUESDAY - timedelta(days=3) if n % 2 == 0 else None) for n in range(8)])
        db.add_all([Candidate(id="kdep", ref="CND-KD", name="Deployed", phone="9500099999", source=Source.walk_in, owner_id="kim",
                              status="deployed", created_at=TUESDAY - timedelta(days=90)),
                    Candidate(id="lee1", ref="CND-L1", name="Lee 1", phone="9600000001", source=Source.walk_in, owner_id="lee",
                              created_at=TUESDAY - timedelta(days=9))])
        db.commit()
        # lee already holds a slot-2 item from earlier today, so only the other four slots are free
        db.add(DailyWorkItem(owner_id="lee", work_date=daily_work.agency_today(TUESDAY, TZ), kind="call", candidate_id="lee1", slot_no=2,
                             session="morning", due_at=TUESDAY + timedelta(hours=5), allocated_at=TUESDAY))
        db.commit()


def test_the_set_based_allocation_picks_the_same_candidates_slots_and_times_as_the_per_recruiter_one(desk):
    seed_mixed_day(desk)
    staff_with_candidates(desk, short={"s05": 3, "s06": 0})
    results = {}
    for name, run in (("legacy", lambda db: legacy_allocate(db, TUESDAY)),
                      ("set", lambda db: ensure_allocations(db, now=TUESDAY, tz=TZ))):
        with desk.sessions() as db:
            run(db)
            db.flush()
            results[name] = item_tuples(db)
            db.rollback()  # the same starting point for the other implementation
    assert results["legacy"] == results["set"]
    assert len(results["set"]) > 90  # it really allocated (20 + 4 recruiters)
    asha = [t for t in results["set"] if t[0] == "asha"]
    assert [t[4] for t in asha] == ["stage"] * 4 + ["call"]  # stage tasks first, then an owned candidate


def test_one_pool_statement_equals_a_pool_per_recruiter_for_every_need(desk):
    seed_mixed_day(desk)
    with desk.sessions() as db:
        today = daily_work.agency_today(TUESDAY, TZ)
        for need in (1, 3, 5, 9):
            needs = {uid: need for uid in ("asha", "ravi", "kim", "lee", "manager")}
            together = pool_for(db, needs, today)
            for uid, n in needs.items():
                assert together.get(uid, []) == _pool(db, uid, n, today), (uid, n)
        assert pool_for(db, {}, today) == {}
        assert pool_waiting(db, ["asha", "ravi", "kim", "lee", "manager", "admin"], today) == {"asha", "ravi", "kim"}  # lee's only candidate already has an open item


# ------------------------------------------------------------------ two requests at once

def test_two_simultaneous_first_calls_do_not_allocate_anyone_twice(desk):
    staff_with_candidates(desk)
    first_done, release = threading.Event(), threading.Event()
    outcome = {}

    def first():
        with desk.sessions() as db:
            outcome["first"] = ensure_allocations(db, now=TUESDAY, tz=TZ)
            first_done.set()
            release.wait(10)  # hold the locks (uncommitted) while the second request arrives
            db.commit()

    def second():
        with desk.sessions() as db:
            started.set()
            outcome["second"] = ensure_allocations(db, now=TUESDAY, tz=TZ)
            db.commit()

    started = threading.Event()
    one = threading.Thread(target=first)
    one.start()
    assert first_done.wait(30)
    two = threading.Thread(target=second)
    two.start()
    assert started.wait(10)
    time.sleep(1.0)
    assert two.is_alive(), "the second request must wait for the first one's recruiters"
    release.set()
    one.join(30)
    two.join(30)
    assert sum(outcome["first"].values()) == 100 and outcome["second"] == {}
    with desk.sessions() as db:
        rows = db.execute(select(DailyWorkItem.owner_id, DailyWorkItem.slot_no, DailyWorkItem.candidate_id)).all()
    assert len(rows) == 100 and len({(o, s) for o, s, _c in rows}) == 100 and len({c for _o, _s, c in rows}) == 100


def test_overlapping_groups_of_recruiters_cannot_deadlock_or_double_allocate(desk):
    staff_with_candidates(desk)
    errors, created = [], []

    def run(ids):
        try:
            with desk.sessions() as db:
                created.append(ensure_allocations(db, ids, now=TUESDAY, tz=TZ))
                db.commit()
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    groups = [STAFF[:12], STAFF[8:], list(reversed(STAFF)), STAFF[4:16]]
    threads = [threading.Thread(target=run, args=(g,)) for g in groups]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    with desk.sessions() as db:
        rows = db.execute(select(DailyWorkItem.owner_id, DailyWorkItem.slot_no, DailyWorkItem.candidate_id)).all()
    assert len(rows) == 100 and len({c for _o, _s, c in rows}) == 100
    assert sum(sum(c.values()) for c in created) == 100
