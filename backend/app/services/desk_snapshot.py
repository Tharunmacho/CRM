"""Read the desk's existing wire fields in one PostgreSQL statement.

Independent JSON aggregates avoid a Cartesian join and repeated network round trips.
Only API fields and the small set of fields used for assembly cross the connection:
password hashes, raw OCR output, search vectors and file storage keys are not read.
This is a fresh database snapshot, not a cross-user cache.
"""

from sqlalchemy import JSON, Select, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApplicationOut, CandidateOut, ClientOut, CompanyOut, DesignationOut,
    NotificationOut, OrderOut, RemarkOut, StageRecordOut, TaskOut, TodoOut, UserOut,
)
from app.db.models import (
    AgencySettings, Application, ApplicationStage, Attachment, Candidate, Client, Company,
    Currency, Designation, JobCategory, Notification, Order, Remark, Role, Task, TodoItem, User,
)
from app.services import agency_settings
from app.services.ingestion_revision import revision_expression


def _fields(model, schema, *extra):
    """Use the response schema as an allowlist, excluding computed wire fields."""
    table = model.__table__
    names = dict.fromkeys([*schema.model_fields, *extra])
    return select(*(table.c[name] for name in names if name in table.c))


def _rows(name: str, statement: Select, *ordering):
    rows = statement.subquery(name)
    record = func.row_to_json(rows.table_valued())
    if ordering:
        record = aggregate_order_by(
            record,
            *(rows.c[column].desc() if descending else rows.c[column].asc()
              for column, descending in ordering),
        )
    return select(func.json_agg(record, type_=JSON)).scalar_subquery().label(name)


def snapshot_statement(user_id: str, *, compact: bool = False, settings: bool = True, categories: bool = True) -> Select:
    supervisor = select(User.role.in_([Role.admin, Role.manager])).where(User.id == user_id).scalar_subquery()
    owned_candidates = select(Candidate.id).where(Candidate.owner_id == user_id)
    visible_tasks = or_(supervisor, Task.owner_id == user_id)
    visible_applications = or_(supervisor, Application.candidate_id.in_(owned_candidates))
    application_fields = _fields(Application, ApplicationOut)
    candidate_fields = _fields(Candidate, CandidateOut, "created_at", "call_called",
                              "call_interested", "call_remarks", "call_recorded_at")
    if compact:
        # Resume text can dwarf the rest of the desk. Full profile reads and
        # server-side resume search retain it; list navigation does not need it.
        candidate_fields = candidate_fields.with_only_columns(*(
            literal("").label("resume_text") if column.name == "resume_text" else column
            for column in candidate_fields.selected_columns
        ))
    # Placement summaries remain shared for Data Bank availability; another
    # recruiter's draft and detailed stage/task history do not cross the boundary.
    application_fields = application_fields.with_only_columns(*(
        case((visible_applications, column), else_=cast(literal('{}'), JSONB)).label("draft") if column.name == "draft" else column
        for column in application_fields.selected_columns
    ))
    candidate_files = select(
        Attachment.id, Attachment.entity_id, Attachment.slot, Attachment.version,
        Attachment.file_name, Attachment.size_kb, Attachment.uploaded_by, Attachment.uploaded_at,
    ).where(Attachment.entity_type == "candidate")
    # DISTINCT ON returns only the latest agreement, not all historical versions.
    agreements = (
        select(Attachment.entity_id, Attachment.id, Attachment.file_name)
        .where(Attachment.entity_type == "company", Attachment.slot == "agreement")
        .distinct(Attachment.entity_id)
        .order_by(Attachment.entity_id, Attachment.version.desc())
    )
    # The agency profile and currency list ride in the same statement: no extra request or round trip.
    # `settings=False` leaves them out for a database that has not had migration 0017 yet.
    agency_row = select(
        AgencySettings.name, AgencySettings.short_name, AgencySettings.default_order_currency,
        AgencySettings.default_advance_currency, AgencySettings.home_calling_code,
        AgencySettings.default_nationality, AgencySettings.acknowledgement_text,
        AgencySettings.updated_by, AgencySettings.updated_at,
    ).where(AgencySettings.id == 1).subquery("agency_row")
    settings_columns = [
        select(func.row_to_json(agency_row.table_valued())).scalar_subquery().label("agency"),
        _rows("currencies", select(Currency.code, Currency.name, Currency.symbol, Currency.active, Currency.sort_order),
              ("sort_order", False), ("code", False)),
    ] if settings else []
    # The job category list needs migration 0019; `categories=False` leaves it out for a database without it.
    if settings and categories:
        settings_columns.append(_rows("categories", select(JobCategory.id, JobCategory.name, JobCategory.is_active, JobCategory.sort_order),
                                      ("sort_order", False), ("name", False)))
    return select(
        # Use the candidates' SQL snapshot, so a completion between bootstrap
        # and the first browser poll cannot be mistaken for an initial baseline.
        revision_expression().label("ingestionRevision"),
        _rows("users", _fields(User, UserOut)),
        _rows("clients", _fields(Client, ClientOut)),
        _rows("companies", _fields(Company, CompanyOut)),
        _rows("designations", _fields(Designation, DesignationOut)),
        _rows("orders", _fields(Order, OrderOut)),
        _rows(
            "candidates",
            candidate_fields,
            ("created_at", True), ("id", False),
        ),
        _rows("files", candidate_files, ("version", False)),
        _rows("agreements", agreements),
        _rows("applications", application_fields),
        _rows(
            "stages", _fields(ApplicationStage, StageRecordOut, "application_id").where(ApplicationStage.application_id.in_(select(Application.id).where(visible_applications))),
            ("stage", False), ("attempt", False),
        ),
        _rows("tasks", _fields(Task, TaskOut).where(visible_tasks)),
        _rows("remarks", _fields(Remark, RemarkOut).where(Remark.task_id.in_(select(Task.id).where(visible_tasks)))),
        _rows("todos", _fields(TodoItem, TodoOut).where(TodoItem.owner_id == user_id)),
        _rows(
            "notifications",
            _fields(Notification, NotificationOut).where(Notification.user_id == user_id),
            ("created_at", True), ("id", False),
        ),
        *settings_columns,
    )


def read_snapshot(db: Session, user_id: str, *, compact: bool = False) -> dict[str, list[dict] | dict | str | None]:
    use_settings = agency_settings.assume_present()
    use_categories = use_settings and agency_settings.assume_categories_present()
    try:
        row = db.execute(snapshot_statement(user_id, compact=compact, settings=use_settings, categories=use_categories)).mappings().one()
    except ProgrammingError as error:
        if not use_settings:
            raise
        if agency_settings.is_missing_table(error):
            # An un-migrated database: read the desk without the settings and remember that for a little while.
            db.rollback()
            agency_settings.mark_present(False)
            use_settings = use_categories = False
        elif use_categories and agency_settings.is_missing_categories(error):
            # Settings are there, the category list (migration 0019) is not yet.
            db.rollback()
            agency_settings.mark_categories_present(False)
            use_categories = False
        else:
            raise
        row = db.execute(snapshot_statement(user_id, compact=compact, settings=use_settings, categories=use_categories)).mappings().one()
        if use_settings:
            agency_settings.mark_present(True)
    else:
        if use_settings:
            agency_settings.mark_present(True)
        if use_categories:
            agency_settings.mark_categories_present(True)
    snapshot = {name: rows or [] for name, rows in row.items() if name not in ("agency", "categories")}
    snapshot["agency"] = row["agency"] if "agency" in row else None
    # None (not an empty list) says the server has no category table.
    snapshot["categories"] = (row["categories"] or []) if "categories" in row else None
    agency_settings.note_snapshot(snapshot["agency"])
    return snapshot
