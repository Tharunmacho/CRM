"""Bounded Data Bank query and export use the same parameterized predicates."""
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import String, and_, case, cast, exists, func, literal_column, or_, select, true
from sqlalchemy.orm import aliased, defer
from sqlalchemy.orm.attributes import set_committed_value
from app.api.schemas import CandidateOut
from app.core.config import get_settings
from app.db.models import Application, Attachment, Candidate, CandidateStatus, Client, Company, Designation, FormOption, Order, Source, User
from app.domain.stages import STAGES
from app.services.candidate_identity import normalized_labels
from app.services.agency_display import agency_display_expression
from app.services.parallel_reads import parallel_reads
from app.services.report_exports import xlsx_rows

Pool = Literal["all", "unmapped", "not_interested", "unassigned", "needs_review"]
STATUS_LABELS = {"new": "New", "in_process": "In process", "not_interested": "Not interested", "deployed": "Deployed", "needs_review": "Needs review"}
UNASSIGNED_LABEL = "Needs assignment"
# Extra column_filters keys: a range on Experience (whole years) and on Date added (YYYY-MM-DD, agency timezone).
# "experience" (exact years) and "added" (one day, or text) keep working as before.
RANGE_KEYS = {"experience_min", "experience_max", "added_from", "added_to"}
SOURCE_LABELS = {"email": "Email", "whatsapp": "WhatsApp", "walk_in": "Walk-in", "referral": "Referral", "other": "Other"}
SERVICE_CHARGE_ORDER = ("agreed", "pending", "declined", "not_discussed")
INTEREST_ORDER = ("more_interested", "interested", "somewhat_interested", "not_interested")
MAX_SEARCH_TERMS = 10
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def agency_midnight(value: str, label: str, days: int = 0) -> datetime:
    """Midnight at the start of the given calendar day (plus `days`) in the agency timezone, as an aware datetime."""
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(422, f"{label} must be a date written as YYYY-MM-DD.")
    return datetime.combine(day + timedelta(days=days), time.min, tzinfo=ZoneInfo(get_settings().agency_timezone))


def whole_years(value: str, label: str) -> int:
    try:
        years = int(value.strip())
    except ValueError:
        raise HTTPException(422, f"{label} must be a whole number of years.")
    if years < 0 or years > 80:
        raise HTTPException(422, f"{label} must be between 0 and 80 years.")
    return years


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
    # Only the resumes of one position, or "none" without an active or deployed placement.
    order_id: str = ""


def normalized(expression):
    return func.lower(func.trim(func.regexp_replace(func.normalize(expression, literal_column("NFKC")), r"\s+", " ", "g")))


def literal_contains(expression, term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return expression.ilike(f"%{escaped}%", escape="\\")


def search_terms(q: str) -> list[str]:
    """Comma-separated search terms: trimmed, whitespace collapsed, empty and repeated terms dropped."""
    terms: list[str] = []
    for part in q.split(","):
        term = " ".join(part.split())
        if term and term.lower() not in (known.lower() for known in terms):
            terms.append(term)
    if len(terms) > MAX_SEARCH_TERMS:
        raise HTTPException(422, f"Use at most {MAX_SEARCH_TERMS} search words separated by commas.")
    return terms


def displayed_applications():
    """One placement per candidate: active first, otherwise their latest deployed placement.

    Closed applications remain history. Ranking before joining prevents multiple
    deployments from duplicating candidate rows, counts, skills and export rows.
    """
    ranked = select(
        Application.id, Application.candidate_id, Application.current_stage, Application.order_id,
        func.row_number().over(
            partition_by=Application.candidate_id,
            order_by=(case((Application.status == "active", 0), else_=1),
                      Application.created_at.desc(), Application.id.desc()),
        ).label("placement_rank"),
    ).where(Application.status.in_(("active", "deployed"))).subquery("ranked_applications")
    return select(ranked.c.id, ranked.c.candidate_id, ranked.c.current_stage, ranked.c.order_id).where(
        ranked.c.placement_rank == 1,
    ).subquery("current_app")


def search_statement(filters: Search):
    app = displayed_applications()
    source = case((Candidate.source == "email", "Email"), (Candidate.source == "whatsapp", "WhatsApp"),
                  (Candidate.source == "walk_in", "Walk-in"), (Candidate.source == "referral", "Referral"), else_="Other")
    expressions = {
        "name": Candidate.name, "phone": Candidate.phone, "designation": cast(Candidate.designations, String),
        "skills": cast(Candidate.skills, String), "experience": Candidate.total_experience,
        "passport": case((func.length(func.trim(func.coalesce(Candidate.passport_no, ""))) > 0, "Yes"), else_="No"),
        "source": source, "owner": func.coalesce(User.name, UNASSIGNED_LABEL), "order": func.coalesce(Order.ref, "Not mapped"),
        "stage": func.coalesce(app.c.current_stage, 0), "status": cast(Candidate.status, String),
        "added": Candidate.created_at, "country": Candidate.country_preference,
    }
    # Sorted by meaning, not alphabet (Agreed before Pending; More interested before Not interested). Sort only: not column filters.
    ranks = {
        "service": case(*((Candidate.service_charges_status == value, rank) for rank, value in enumerate(SERVICE_CHARGE_ORDER, 1)), else_=len(SERVICE_CHARGE_ORDER) + 1),
        "interest": case(*((Candidate.interest_level == value, rank) for rank, value in enumerate(INTEREST_ORDER, 1)), else_=len(INTEREST_ORDER) + 1),
    }
    intake = aliased(User)
    query = (select(Candidate).outerjoin(User, User.id == Candidate.owner_id).outerjoin(intake, intake.id == Candidate.intake_owner_id)
             .outerjoin(app, app.c.candidate_id == Candidate.id).outerjoin(Order, Order.id == app.c.order_id)
             .outerjoin(Designation, Designation.id == Order.designation_id).outerjoin(Company, Company.id == Order.company_id)
             .outerjoin(Client, Client.id == Company.client_id))
    agent = agency_display_expression()

    def term_matches(term: str):
        """True when this one term appears (any case) in at least one of the candidate's details."""
        lowered = term.lower()
        text_fields = [Candidate.name, Candidate.phone, Candidate.whatsapp, Candidate.email, Candidate.passport_no, Candidate.ref, Candidate.nationality,
                       Candidate.current_city, Candidate.country_preference, cast(Candidate.skills, String), cast(Candidate.designations, String), agent, User.name, intake.name,
                       Order.ref, Designation.title, Company.name, Client.name, Order.country, Order.city]
        clauses = [literal_contains(field, term) for field in text_fields]
        clauses.append(Candidate.resume_tsv.op("@@")(func.websearch_to_tsquery("simple", term)))
        squeezed = term.replace(" ", "")
        if len(squeezed) >= 3:  # "sureshagent" still finds "Suresh Agent": names and agents are also compared without spaces
            clauses += [literal_contains(func.replace(field, " ", ""), squeezed) for field in (Candidate.name, agent, User.name, intake.name, Designation.title, Company.name, Client.name)]
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 4 and re.fullmatch(r"[\d\s+\-()]+", term):  # a phone typed with spaces or a country code
            clauses += [literal_contains(func.regexp_replace(field, r"\D", "", "g"), digits) for field in (Candidate.phone, Candidate.whatsapp)]
        sources = [Source(key) for key, label in SOURCE_LABELS.items() if lowered in label.lower() or lowered in key.replace("_", " ")]
        if sources:
            clauses.append(Candidate.source.in_(sources))
        statuses = [CandidateStatus(key) for key, label in STATUS_LABELS.items() if lowered in label.lower()]
        if statuses:
            clauses.append(Candidate.status.in_(statuses))
            if CandidateStatus.needs_review in statuses:
                clauses.append(Candidate.needs_review.is_(True))
        stages = {stage.no for stage in STAGES if lowered in stage.title.lower()}
        number = re.fullmatch(r"(?:stage\s*)?(\d{1,2})", lowered)
        if number:
            stages.add(int(number.group(1)))
        if stages:
            clauses.append(app.c.current_stage.in_(sorted(stages)))
        return or_(*clauses)
    if filters.staff:
        query = query.where(Candidate.owner_id == filters.staff)
    if filters.order_id == "none":
        query = query.where(app.c.candidate_id.is_(None))
    elif filters.order_id:
        query = query.where(app.c.order_id == filters.order_id)
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
        raise HTTPException(422, "We do not recognise this candidate list.")
    # "ravi, agent kumar": every comma-separated term must match somewhere in the same candidate.
    for term in search_terms(filters.q):
        query = query.where(term_matches(term))
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
        if not isinstance(column_filters, dict) or any((key not in expressions and key not in RANGE_KEYS) or not isinstance(val, str) or len(val) > 200 for key, val in column_filters.items()):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "One of the column filters is not valid. Clear the filters and try again.")
    for key, term in column_filters.items():
        if not term.strip():
            continue
        if key in RANGE_KEYS:
            if key == "experience_min":
                query = query.where(Candidate.total_experience >= whole_years(term, "Minimum experience"))
            elif key == "experience_max":
                query = query.where(Candidate.total_experience <= whole_years(term, "Maximum experience"))
            elif key == "added_from":
                query = query.where(Candidate.created_at >= agency_midnight(term, "Added from"))
            else:
                query = query.where(Candidate.created_at < agency_midnight(term, "Added to", days=1))
            continue
        if key == "added" and ISO_DAY.match(term.strip()):
            # One calendar day in the agency timezone (the old filter compared text in the database timezone).
            query = query.where(Candidate.created_at >= agency_midnight(term, "Added on"), Candidate.created_at < agency_midnight(term, "Added on", days=1))
            continue
        expression = expressions[key]
        if key in ("owner", "status"):
            # Match what the table shows ("Needs assignment", "Needs review") as well as
            # the stored value ("Unassigned" is the older label, "needs_review" the enum).
            if key == "status":
                label = case(*((Candidate.status == value, name) for value, name in STATUS_LABELS.items()), else_=cast(Candidate.status, String))
            else:
                label = case((Candidate.owner_id.is_(None), "Unassigned"), else_=User.name)
            clauses = [literal_contains(cast(expression, String), term.strip()), literal_contains(label, term.strip())]
            if key == "status" and any(term.strip().lower() in value for value in ("needs_review", "needs review")):
                clauses.append(Candidate.needs_review.is_(True))
            query = query.where(or_(*clauses))
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
    sortable = {**expressions, **ranks}
    if filters.sort not in sortable:
        raise HTTPException(422, "We cannot sort by this column.")
    ordering = sortable[filters.sort]
    return query.order_by(ordering.desc().nullslast() if filters.direction == "desc" else ordering.asc().nullslast(), Candidate.id)


def position_groups(db, filters: Search):
    """The positions (orders) holding the candidates this search finds, with how many resumes each holds.

    One grouped statement over the same predicates as the list, so the numbers always agree with it.
    Candidates without an active or deployed placement form one last group with a null order id."""
    pool = (search_statement(filters).order_by(None)
            .with_only_columns(Candidate.id.label("candidate_id"), Order.id.label("order_id"), Order.ref.label("ref"), Designation.title.label("title"),
                               Company.name.label("company"), Client.name.label("client"), Order.country.label("country"), Order.city.label("city"),
                               Order.status.label("order_status"))
            .subquery("position_pool"))
    resumes = func.count(pool.c.candidate_id)
    keys = [pool.c.order_id, pool.c.ref, pool.c.title, pool.c.company, pool.c.client, pool.c.country, pool.c.city, pool.c.order_status]
    rows = db.execute(select(*keys, resumes.label("resumes")).group_by(*keys).order_by(pool.c.order_id.is_(None), resumes.desc(), pool.c.title, pool.c.ref)).all()
    groups = [{"orderId": row.order_id, "ref": row.ref, "title": row.title, "company": row.company, "client": row.client, "country": row.country,
               "city": row.city, "orderStatus": row.order_status.value if row.order_status else None, "resumes": row.resumes} for row in rows]
    return {"groups": groups, "positions": sum(1 for group in groups if group["orderId"]), "resumes": sum(group["resumes"] for group in groups)}


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
    # The total rides along with the page (`count(*) OVER ()` is evaluated before LIMIT), so a page is ONE statement on ONE
    # connection instead of a count and a page side by side.
    listing = query.add_columns(func.count().over().label("total")).options(defer(Candidate.resume_text), defer(Candidate.resume_tsv))

    def fetch(at):
        return lambda session: session.execute(listing.offset(at * page_size).limit(page_size)).all()

    found = fetch(page)(db)
    if found:
        total = found[0].total
    elif page == 0:
        total = 0
    else:  # a page past the end carries no rows, so no total: count, then show the last page
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    clamped = min(page, max(0, (total - 1) // page_size))
    if clamped != page:  # asked for a page past the end: show the last one
        page, found = clamped, fetch(clamped)(db)
    candidates = [row[0] for row in found]
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
    # The cleaned label and its key are computed once per skill element (OFFSET 0 stops the planner from pulling the
    # expressions back into the outer query, where they would be evaluated for the filter, the group and the minimum).
    cleaned = (select(pool.c.id.label("id"), label.label("label"), key.label("key"))
               .select_from(pool.join(elements, true())).offset(0).subquery("cleaned_skill"))
    rows, (total, recorded) = parallel_reads(
        db,
        lambda session: session.execute(select(cleaned.c.key.label("key"), func.min(cleaned.c.label).label("label"), func.count(func.distinct(cleaned.c.id)).label("count"))
                          .where(cleaned.c.key != "").group_by(cleaned.c.key).order_by(cleaned.c.key)).all(),
        lambda session: session.execute(select(func.count(), func.count().filter(has_recorded_skill)).select_from(pool)).one(),
    )
    return {"skills": [{"key": row.key, "label": row.label, "count": row.count} for row in rows],
            "total": total, "recorded": recorded, "missing": total - recorded}


def export_candidates(db, filters):
    candidates = db.scalars(search_statement(filters)).all()
    users = {u.id: u.name for u in db.scalars(select(User))}
    shown = displayed_applications()
    current_apps = {a.candidate_id: a for a in db.scalars(select(Application).join(shown, Application.id == shown.c.id))}
    orders = {o.id: o.ref for o in db.scalars(select(Order))}
    # The labels an admin has set for service charges and interest level; a stored value with no option reads as itself.
    labels: dict[str, dict[str, str]] = {}
    for category, value, label in db.execute(select(FormOption.category, FormOption.value, FormOption.label).where(FormOption.category.in_(("service_charges_status", "interest_level")))):
        labels.setdefault(category, {})[value] = label
    from app.services.application_form import LEGACY_SLOTS, required_for_deployment
    required = required_for_deployment()
    matching_ids = search_statement(filters).order_by(None).with_only_columns(Candidate.id)
    uploaded = {}
    for candidate_id, slot in db.execute(select(Attachment.entity_id, Attachment.slot).where(
        Attachment.entity_type == "candidate", Attachment.entity_id.in_(matching_ids),
    ).distinct()):
        uploaded.setdefault(candidate_id, set()).add(LEGACY_SLOTS.get(slot, slot))
    rows = [["Reference", "Name", "Phone", "Email", "Designation", "Skills", "Experience", "Passport available", "Source", "Assigned staff", "Mapped order", "Stage", "Status", "Added at",
             "Date of birth", "WhatsApp", "Preferred country", "Service charges", "Interest level",
             *[label + " document" for _slot, label in required], "Missing mandatory documents"]]
    for candidate in candidates:
        app = current_apps.get(candidate.id)
        service = labels.get("service_charges_status", {}).get(candidate.service_charges_status, candidate.service_charges_status)
        interest = labels.get("interest_level", {}).get(candidate.interest_level, candidate.interest_level) if candidate.interest_level else ""
        saved = uploaded.get(candidate.id, set())
        rows.append([candidate.ref, candidate.name, candidate.phone, candidate.email, ", ".join(candidate.designations), ", ".join(candidate.skills),
                     candidate.total_experience, "Yes" if candidate.passport_no else "No", candidate.source.value,
                     users.get(candidate.owner_id, UNASSIGNED_LABEL), orders.get(app.order_id, "") if app else "", app.current_stage if app else "",
                     candidate.status.value, candidate.created_at.isoformat(),
                     candidate.dob.isoformat() if candidate.dob else "", candidate.whatsapp or "", candidate.country_preference or "", service, interest,
                     *["Uploaded" if slot in saved else "Missing" for slot, _label in required],
                     ", ".join(label for slot, label in required if slot not in saved)])
    return xlsx_rows([("Candidates", rows), ("Filters", [[key, str(val)] for key, val in vars(filters).items()])])
