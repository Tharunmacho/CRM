"""Bounded Data Bank query and export use the same parameterized predicates."""
import json
from dataclasses import dataclass, field
from typing import Literal
from fastapi import HTTPException
from sqlalchemy import String, and_, case, cast, exists, func, literal_column, or_, select, true
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import set_committed_value
from app.api.schemas import CandidateOut
from app.db.models import Application, Candidate, Order, User
from app.services.candidate_identity import normalized_labels
from app.services.parallel_reads import parallel_reads
from app.services.report_exports import xlsx_rows

Pool = Literal["all", "unmapped", "not_interested", "unassigned", "needs_review"]
STATUS_LABELS = {"new": "New", "in_process": "In process", "not_interested": "Not interested", "deployed": "Deployed", "needs_review": "Needs review"}
UNASSIGNED_LABEL = "Needs assignment"


@dataclass
class Search:
    q: str = ""
    pool: str = "all"
    skills: list[str] = field(default_factory=list)
    skill_mode: str = "all"
    missing_skills: bool = False
    column_filters: str = "{}"
    sort: str = "added"
    direction: str = "desc"
    staff: str | None = None
    designation: str = ""
    min_experience: float | None = None
    passport: str = "any"
    location: str = ""
    matchable: bool = False


def normalized(expression):
    return func.lower(func.trim(func.regexp_replace(func.normalize(expression, literal_column("NFKC")), r"\s+", " ", "g")))


def literal_contains(expression, term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return expression.ilike(f"%{escaped}%", escape="\\")


def search_statement(filters: Search):
    app = select(Application.candidate_id, Application.current_stage, Application.order_id).where(Application.status == "active").subquery("current_app")
    source = case((Candidate.source == "email", "Email"), (Candidate.source == "whatsapp", "WhatsApp"),
                  (Candidate.source == "walk_in", "Walk-in"), (Candidate.source == "referral", "Referral"), else_="Other")
    expressions = {
        "name": Candidate.name, "phone": Candidate.phone, "designation": cast(Candidate.designations, String),
        "skills": cast(Candidate.skills, String), "experience": Candidate.total_experience,
        "passport": case((func.length(func.trim(func.coalesce(Candidate.passport_no, ""))) > 0, "Yes"), else_="No"),
        "source": source, "owner": func.coalesce(User.name, UNASSIGNED_LABEL), "order": func.coalesce(Order.ref, "Not mapped"),
        "stage": func.coalesce(app.c.current_stage, 0), "status": cast(Candidate.status, String),
        "added": Candidate.created_at,
    }
    query = select(Candidate).outerjoin(User, User.id == Candidate.owner_id).outerjoin(app, app.c.candidate_id == Candidate.id).outerjoin(Order, Order.id == app.c.order_id)
    if filters.staff:
        query = query.where(Candidate.owner_id == filters.staff)
    if filters.matchable:
        query = query.where(Candidate.status != "deployed", User.active.is_(True), app.c.candidate_id.is_(None))
    if filters.designation.strip():
        titles = func.jsonb_array_elements_text(Candidate.designations).table_valued("value").alias("saved_designation")
        key = normalized_labels([filters.designation])[0].lower()
        query = query.where(exists(select(1).select_from(titles).where(normalized(titles.c.value) == key)).correlate(Candidate))
    if filters.min_experience is not None:
        query = query.where(Candidate.total_experience >= filters.min_experience)
    if filters.location.strip():
        query = query.where(literal_contains(normalized(Candidate.current_city), normalized_labels([filters.location])[0].lower()))
    has_passport = func.length(func.trim(func.coalesce(Candidate.passport_no, ""))) > 0
    if filters.passport == "present":
        query = query.where(has_passport)
    elif filters.passport == "missing":
        query = query.where(~has_passport)
    if filters.pool == "unmapped":
        query = query.where(app.c.candidate_id.is_(None))
    elif filters.pool == "not_interested":
        query = query.where(Candidate.status == "not_interested")
    elif filters.pool == "unassigned":
        query = query.where(Candidate.owner_id.is_(None))
    elif filters.pool == "needs_review":
        query = query.where(or_(Candidate.needs_review.is_(True), Candidate.status == "needs_review"))
    elif filters.pool != "all":
        raise HTTPException(422, "Unknown candidate pool.")
    term = filters.q.strip()
    if term:
        query = query.where(or_(
            *(literal_contains(column, term) for column in [Candidate.name, Candidate.phone, Candidate.email, Candidate.passport_no, Candidate.ref, cast(Candidate.skills, String), cast(Candidate.designations, String)]),
            Candidate.resume_tsv.op("@@")(func.websearch_to_tsquery("simple", term)),
        ))
    elements = func.jsonb_array_elements_text(Candidate.skills).table_valued("value").alias("saved_skill")
    has_skill = lambda key: exists(select(1).select_from(elements).where(normalized(elements.c.value) == key)).correlate(Candidate)
    keys = [label.lower() for label in normalized_labels(filters.skills)]
    if keys:
        clauses = [has_skill(key) for key in keys]
        query = query.where(or_(*clauses) if filters.skill_mode == "any" else and_(*clauses))
    if filters.missing_skills:
        query = query.where(~exists(select(1).select_from(elements).where(normalized(elements.c.value) != "")).correlate(Candidate))
    try:
        column_filters = json.loads(filters.column_filters)
        if not isinstance(column_filters, dict) or any(key not in expressions or not isinstance(val, str) or len(val) > 200 for key, val in column_filters.items()):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Column filters must contain recognized column names and text values.")
    for key, term in column_filters.items():
        if not term.strip():
            continue
        expression = expressions[key]
        if key in ("owner", "status"):
            # Match what the table shows ("Needs assignment", "Needs review") as well as
            # the stored value ("Unassigned" is the older label, "needs_review" the enum).
            if key == "status":
                label = case(*((Candidate.status == value, name) for value, name in STATUS_LABELS.items()), else_=cast(Candidate.status, String))
            else:
                label = case((Candidate.owner_id.is_(None), "Unassigned"), else_=User.name)
            query = query.where(or_(literal_contains(cast(expression, String), term.strip()), literal_contains(label, term.strip())))
            continue
        if key in ("experience", "stage"):
            try:
                number = int(term)
            except ValueError:
                raise HTTPException(422, f"{key} filter must be a whole number.")
            query = query.where(expression == number)
        else:
            expression = cast(expression, String)
            query = query.where(literal_contains(expression, term.strip()))
    if filters.sort not in expressions:
        raise HTTPException(422, "Unknown sort column.")
    ordering = expressions[filters.sort]
    return query.order_by(ordering.desc().nullslast() if filters.direction == "desc" else ordering.asc().nullslast(), Candidate.id)


def pool_counts(db, staff: str | None = None) -> dict[str, int]:
    """How many candidates each Data Bank pool holds for the same optional staff scope.

    One statement with a scalar subquery per pool: five separate counts cost five round trips."""
    pools = ("all", "unmapped", "not_interested", "unassigned", "needs_review")
    counts = [
        select(func.count()).select_from(search_statement(Search(pool=pool, staff=staff)).order_by(None).subquery()).scalar_subquery()
        for pool in pools
    ]
    return {pool: value or 0 for pool, value in zip(pools, db.execute(select(*counts)).one())}


def search_candidates(db, filters, page, page_size):
    query = search_statement(filters)
    # Resume text is never returned in a list, so it is not read (it can be many KB per row).
    listing = query.options(defer(Candidate.resume_text), defer(Candidate.resume_tsv))

    def count(session):
        return session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0

    def fetch(at):
        return lambda session: session.scalars(listing.offset(at * page_size).limit(page_size)).all()

    # The count and the requested page do not depend on each other: ask for both at once.
    total, candidates = parallel_reads(db, count, fetch(page))
    clamped = min(page, max(0, (total - 1) // page_size))
    if clamped != page:  # asked for a page past the end: show the last one
        page, candidates = clamped, fetch(clamped)(db)
    rows = []
    for candidate in candidates:
        set_committed_value(candidate, "resume_text", "")
        item = CandidateOut.model_validate(candidate).model_dump(by_alias=True, mode="json")
        item["addedAt"] = candidate.created_at.isoformat()
        rows.append(item)
    return {"items": rows, "total": total, "page": page, "pageSize": page_size}


def candidate_skill_facets(db, filters: Search):
    """Aggregate every saved skill in the requested pool without loading profiles.

    A candidate contributes once to each normalized skill, even if legacy data
    contains duplicates. Missing skills include empty/whitespace-only arrays.
    """
    pool = search_statement(filters).with_only_columns(Candidate.id, Candidate.skills).order_by(None).subquery("skill_pool")
    elements = func.jsonb_array_elements_text(pool.c.skills).table_valued("value").alias("recorded_skill")
    label = func.trim(func.regexp_replace(func.normalize(elements.c.value, literal_column("NFKC")), r"\s+", " ", "g"))
    key = func.lower(label)
    has_recorded_skill = exists(select(1).select_from(elements).where(normalized(elements.c.value) != "")).correlate(pool)
    rows, (total, recorded) = parallel_reads(
        db,
        lambda session: session.execute(select(key.label("key"), func.min(label).label("label"), func.count(func.distinct(pool.c.id)).label("count"))
                          .select_from(pool.join(elements, true())).where(key != "").group_by(key).order_by(key)).all(),
        lambda session: session.execute(select(func.count(), func.count().filter(has_recorded_skill)).select_from(pool)).one(),
    )
    return {"skills": [{"key": row.key, "label": row.label, "count": row.count} for row in rows],
            "total": total, "recorded": recorded, "missing": total - recorded}


def export_candidates(db, filters):
    candidates = db.scalars(search_statement(filters)).all()
    users = {u.id: u.name for u in db.scalars(select(User))}
    current_apps = {a.candidate_id: a for a in db.scalars(select(Application).where(Application.status == "active"))}
    orders = {o.id: o.ref for o in db.scalars(select(Order))}
    rows = [["Reference", "Name", "Phone", "Email", "Designation", "Skills", "Experience", "Passport available", "Source", "Assigned staff", "Mapped order", "Stage", "Status", "Added at"]]
    for candidate in candidates:
        app = current_apps.get(candidate.id)
        rows.append([candidate.ref, candidate.name, candidate.phone, candidate.email, ", ".join(candidate.designations), ", ".join(candidate.skills),
                     candidate.total_experience, "Yes" if candidate.passport_no else "No", candidate.source.value,
                     users.get(candidate.owner_id, UNASSIGNED_LABEL), orders.get(app.order_id, "") if app else "", app.current_stage if app else "",
                     candidate.status.value, candidate.created_at.isoformat()])
    return xlsx_rows([("Candidates", rows), ("Filters", [[key, str(val)] for key, val in vars(filters).items()])])
