"""Lead sheet bulk actions and delete. Contract: docs/workflows/lead-sheet-contract.md ("Bulk actions and delete").

POST /api/leads/bulk (assign | status | not_interested | export | delete) and DELETE /api/leads/{id} (admin). The rules live in
app/services/lead_bulk.py; this file only parses the request and commits.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.schemas import Wire
from app.core.deps import current_user, require_role
from app.db.models import Role, User
from app.db.session import get_db
from app.services import lead_bulk

router = APIRouter(prefix="/api/leads", tags=["leads"])


class BulkBody(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    action: Literal["assign", "status", "not_interested", "export", "delete"]
    ids: list[str] | None = Field(default=None, max_length=100_000)
    filter: dict | None = None
    owner_id: str | None = Field(default=None, max_length=36)
    status: str | None = Field(default=None, max_length=20)
    only_unassigned: bool = False
    confirm: str | None = Field(default=None, max_length=20)
    expected_count: int | None = Field(default=None, ge=0)
    sort: str | None = Field(default=None, max_length=20)
    direction: Literal["asc", "desc"] | None = None


@router.post("/bulk")
def bulk(body: BulkBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    camel = {"ownerId": body.owner_id, "onlyUnassigned": body.only_unassigned, "expectedCount": body.expected_count}
    request = {"action": body.action, "ids": body.ids, "filter": body.filter, "status": body.status, "confirm": body.confirm,
               "sort": body.sort, "direction": body.direction, **camel}
    try:
        result = lead_bulk.run(db, user, request)
    except Exception:
        db.rollback()
        raise
    if isinstance(result, tuple):  # export: nothing was written
        db.rollback()
        workbook, count = result
        return Response(workbook, media_type=lead_bulk.EXPORT_MEDIA,
                        headers={"Content-Disposition": 'attachment; filename="lead-sheet-selected.xlsx"', "Cache-Control": "private, no-store",
                                 "X-Lead-Count": str(count), "Access-Control-Expose-Headers": "X-Lead-Count, Content-Disposition"})
    db.commit()
    lead_bulk.remove_stored_files(db)
    return result


@router.delete("/{lead_id}")
def delete_lead(lead_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    """Admin only. Refused (409 `lead_linked`) for a converted / linked lead. Anyone in a later upload is added again."""
    try:
        result = lead_bulk.delete_one(db, user, lead_id)
    except Exception:
        db.rollback()
        raise
    db.commit()
    lead_bulk.remove_stored_files(db)
    return result
