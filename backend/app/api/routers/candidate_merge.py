"""Merge a duplicate candidate into the existing one (admin only). The work is `app.services.candidate_merge`."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import require_role, require_admin_level
from app.db.models import Role, User
from app.db.session import get_db
from app.services import candidate_merge

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    confirm_identity: bool = Field(default=False, alias="confirmIdentity")
    dry_run: bool = Field(default=False, alias="dryRun")


@router.post("/{source_id}/merge-into/{target_id}")
def merge_into(source_id: str, target_id: str, background: BackgroundTasks, response: Response, body: MergeIn | None = None,
               db: Session = Depends(get_db), user: User = Depends(require_admin_level())) -> dict:
    """Move everything worth keeping from the duplicate `source_id` onto the existing `target_id`, then remove the duplicate.

    Same permission as the admin candidate delete. `{"dryRun": true}` answers what would move, be skipped or refuse, and changes nothing.
    A refusal is HTTP 409 with a plain-English reason and nothing changed. A repeat of a merge that worked answers `alreadyMerged`."""
    response.headers["Cache-Control"] = "no-store"
    body = body or MergeIn()
    return candidate_merge.merge_candidate(db, user, source_id, target_id, confirm_identity=body.confirm_identity,
                                           dry_run=body.dry_run, background=background)
