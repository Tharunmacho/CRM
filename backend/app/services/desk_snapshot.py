"""Read the desk's existing wire fields in one PostgreSQL statement.

Independent JSON aggregates avoid a Cartesian join and repeated network round trips.
Only API fields and the small set of fields used for assembly cross the connection:
password hashes, raw OCR output, search vectors and file storage keys are not read.
This is a fresh database snapshot, not a cross-user cache.
"""

from sqlalchemy import JSON, Select, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApplicationOut, CandidateOut, ClientOut, CompanyOut, DesignationOut,
    NotificationOut, OrderOut, RemarkOut, StageRecordOut, TaskOut, TodoOut, UserOut,
)
from app.db.models import (
    Application, ApplicationStage, Attachment, Candidate, Client, Company,
    Designation, Notification, Order, Remark, Task, TodoItem, User,
)


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


def snapshot_statement(user_id: str) -> Select:
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
    return select(
        _rows("users", _fields(User, UserOut)),
        _rows("clients", _fields(Client, ClientOut)),
        _rows("companies", _fields(Company, CompanyOut)),
        _rows("designations", _fields(Designation, DesignationOut)),
        _rows("orders", _fields(Order, OrderOut)),
        _rows(
            "candidates",
            _fields(Candidate, CandidateOut, "created_at", "call_called",
                    "call_interested", "call_remarks", "call_recorded_at"),
            ("created_at", True), ("id", False),
        ),
        _rows("files", candidate_files, ("version", False)),
        _rows("agreements", agreements),
        _rows("applications", _fields(Application, ApplicationOut)),
        _rows(
            "stages", _fields(ApplicationStage, StageRecordOut, "application_id"),
            ("stage", False), ("attempt", False),
        ),
        _rows("tasks", _fields(Task, TaskOut)),
        _rows("remarks", _fields(Remark, RemarkOut)),
        _rows("todos", _fields(TodoItem, TodoOut).where(TodoItem.owner_id == user_id)),
        _rows(
            "notifications",
            _fields(Notification, NotificationOut).where(Notification.user_id == user_id),
            ("created_at", True), ("id", False),
        ),
    )


def read_snapshot(db: Session, user_id: str) -> dict[str, list[dict]]:
    return {name: rows or [] for name, rows in db.execute(snapshot_statement(user_id)).mappings().one().items()}
