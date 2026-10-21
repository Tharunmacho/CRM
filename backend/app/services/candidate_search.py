"""Bounded Data Bank query and export use the same parameterized predicates."""
import json
from dataclasses import dataclass, field
from typing import Literal
from fastapi import HTTPException
from sqlalchemy import String, and_, case, cast, exists, func, literal_column, or_, select
from app.api.schemas import CandidateOut
from app.db.models import Application, Candidate, Order, User
from app.services.candidate_identity import normalized_labels
from app.services.report_exports import xlsx_rows


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


def normalized(expression):
    return func.lower(func.regexp_replace(func.trim(func.normalize(expression, literal_column("NFKC"))), r"\s+", " ", "g"))


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
        "source": source, "owner": func.coalesce(User.name, "Unassigned"), "order": func.coalesce(Order.ref, "Not mapped"),
        "stage": func.coalesce(app.c.current_stage, 0), "status": cast(Candidate.status, String),
        "added": Candidate.created_at,
    }
    query = select(Candidate).outerjoin(User, User.id == Candidate.owner_id).outerjoin(app, app.c.candidate_id == Candidate.id).outerjoin(Order, Order.id == app.c.order_id)
    if filters.pool == "unmapped":
        query = query.where(app.c.candidate_id.is_(None))
    elif filters.pool == "not_interested":
        query = query.where(Candidate.status == "not_interested")
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


def search_candidates(db, filters, page, page_size):
    query = search_statement(filters)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    page = min(page, max(0, (total - 1) // page_size))
    candidates = db.scalars(query.offset(page * page_size).limit(page_size)).all()
    rows = []
    for candidate in candidates:
        item = CandidateOut.model_validate(candidate).model_dump(by_alias=True, mode="json")
        item["addedAt"] = candidate.created_at.isoformat()
        item["resumeText"] = ""  # Full resume text stays in the protected profile/search store.
        rows.append(item)
    return {"items": rows, "total": total, "page": page, "pageSize": page_size}


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
                     users.get(candidate.owner_id, "Unassigned"), orders.get(app.order_id, "") if app else "", app.current_stage if app else "",
                     candidate.status.value, candidate.created_at.isoformat()])
    return xlsx_rows([("Candidates", rows), ("Filters", [[key, str(val)] for key, val in vars(filters).items()])])
