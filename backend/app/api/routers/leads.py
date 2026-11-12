"""Lead sheet: import platform lead exports, call the people, collect resumes, convert to candidates.

Contract: docs/workflows/lead-sheet-contract.md. Import/parse rules: app/services/lead_parsing.py and lead_import.py;
workflow rules: app/services/lead_sheet.py. Reads are open to every signed-in user; working a lead is the assigned staff
member's (after the daily tasks) or a manager's / admin's.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.schemas import Wire
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, Role, User
from app.db.session import get_db
from app.services import lead_import, lead_parsing, lead_report, lead_sheet
from app.services.lead_sheet import fail

router = APIRouter(prefix="/api/leads", tags=["leads"])
supervisors = require_role(Role.admin, Role.manager)


def lead_filters(q: str = Query(default="", max_length=500), status: list[str] = Query(default=[]), campaign: list[str] = Query(default=[]),
                 form: list[str] = Query(default=[]), position: list[str] = Query(default=[]), country: list[str] = Query(default=[]),
                 platform: list[str] = Query(default=[]), owner: str | None = Query(default=None, max_length=36),
                 created_from: str | None = Query(default=None, max_length=10), created_to: str | None = Query(default=None, max_length=10),
                 has_resume: bool | None = None, matched_candidate: bool | None = None,
                 follow_up: Literal["due", "none"] | None = None, batch_id: str | None = Query(default=None, max_length=36)) -> dict:
    return {"q": q, "status": status, "campaign": campaign, "form": form, "position": position, "country": country, "platform": platform,
            "owner": owner, "created_from": created_from, "created_to": created_to, "has_resume": has_resume,
            "matched_candidate": matched_candidate, "follow_up": follow_up, "batch_id": batch_id}


# ------------------------------------------------------------------ import and batches

@router.post("/import")
def import_leads(file: UploadFile = File(...), dry_run: bool = Form(False), mapping: str | None = Form(None), assign_to: str | None = Form(None),
                 campaign_label: str | None = Form(None), fill_empty: bool = Form(False), source: Literal["meta_ads", "other"] = Form("meta_ads"),
                 db: Session = Depends(get_db), user: User = Depends(supervisors)):
    data = file.file.read(lead_parsing.MAX_UPLOAD + 1)
    if len(data) > lead_parsing.MAX_UPLOAD:
        raise fail(413, "file_too_large", "Lead sheets must be under 25 MB. Split the file and upload the parts.")
    override = None
    if mapping:
        try:
            override = json.loads(mapping)
            if not isinstance(override, dict) or any(not isinstance(k, str) or (v is not None and not isinstance(v, str)) for k, v in override.items()):
                raise ValueError()
        except ValueError:
            raise fail(422, "invalid_request", 'mapping must be JSON like {"header": "field" or null}.') from None
    try:
        result = lead_import.run_import(db, user, data, (file.filename or "leads").rsplit("/", 1)[-1].rsplit("\\", 1)[-1], dry_run=dry_run,
                                        mapping=override, assign_to=(assign_to or "").strip() or None, campaign_label=campaign_label,
                                        fill_empty=fill_empty, source=source)
    except lead_import.ImportError_ as exc:
        db.rollback()
        raise fail(exc.status, exc.code, exc.message) from None
    if dry_run:
        db.rollback()
        return result
    db.add(AuditEntry(actor_id=user.id, action="Imported lead sheet", target=result["fileName"], entity_type="lead_batch", entity_id=result["batchId"],
                      meta={"new": result["rowsNew"], "duplicate": result["rowsDuplicate"], "invalid": result["rowsInvalid"]}))
    db.commit()
    return result


@router.get("/batches")
def batches(page: int = Query(default=0, ge=0), page_size: int = Query(default=20, ge=1, le=100),
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    return lead_sheet.list_batches(db, page, page_size)


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return lead_sheet.get_batch(db, batch_id)


@router.get("/batches/{batch_id}/report.csv")
def batch_report(batch_id: str, db: Session = Depends(get_db), user: User = Depends(supervisors)):
    return Response(lead_report.report_csv(db, batch_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{lead_report.download_name(db, batch_id, "csv")}"', "Cache-Control": "private, no-store"})


@router.get("/batches/{batch_id}/report.xlsx")
def batch_report_excel(batch_id: str, db: Session = Depends(get_db), user: User = Depends(supervisors)):
    """The default import report: Summary, All rows and Ignored & invalid, as an Excel workbook."""
    return Response(lead_report.report_workbook(db, batch_id), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{lead_report.download_name(db, batch_id, "xlsx")}"', "Cache-Control": "private, no-store"})


@router.delete("/batches/{batch_id}")
def batch_revert(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    result = lead_sheet.revert_batch(db, user, batch_id)
    db.commit()
    return result


@router.get("/batches/{batch_id}/deletion-preview")
def batch_deletion_preview(batch_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    from app.services import lead_upload_delete
    response.headers["Cache-Control"] = "private, no-store"
    return lead_upload_delete.preview(db, user, batch_id)


class PermanentBatchDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previewToken: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectedLeadCount: int = Field(ge=0, le=100000)
    confirm: Literal["DELETE"]


@router.delete("/batches/{batch_id}/permanent")
def batch_delete_permanently(batch_id: str, body: PermanentBatchDeletion, background: BackgroundTasks,
                             db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    from app.services import lead_upload_delete
    try:
        result, keys = lead_upload_delete.remove(db, user, batch_id, body.previewToken, body.expectedLeadCount, body.confirm)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if keys:
        background.add_task(lead_upload_delete.cleanup_files, db.get_bind(), keys)
    return result


# ------------------------------------------------------------------ reads

@router.get("")
def list_leads(filters: dict = Depends(lead_filters), page: int = Query(default=0, ge=0), page_size: int = Query(default=25, ge=1, le=1000),
               sort: str = "created", direction: Literal["asc", "desc"] = "desc", extra: list[str] = Query(default=[], max_length=12),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    return lead_sheet.search_leads(db, user, filters, page, page_size, sort, direction, extra_keys=[k[:200] for k in extra])


@router.get("/summary")
def lead_summary(scope: Literal["mine", "all"] | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return lead_sheet.summary(db, user, scope)


@router.get("/facets")
def lead_facets(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return lead_sheet.facets(db)


@router.get("/columns")
def lead_columns(campaign: list[str] = Query(default=[]), form: list[str] = Query(default=[]), batch_id: str | None = Query(default=None, max_length=36),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The custom question columns of the stored sheets (optionally of one campaign, form or upload): `{columns: [{key, label, count}]}`."""
    return {"columns": lead_sheet.extra_columns(db, {"campaign": campaign, "form": form, "batch_id": batch_id})}


@router.get("/export.{fmt}")
def export(fmt: Literal["csv", "xlsx"], filters: dict = Depends(lead_filters), sort: str = "created", direction: Literal["asc", "desc"] = "desc",
           db: Session = Depends(get_db), user: User = Depends(supervisors)):
    body, media_type, name = lead_sheet.export_leads(db, user, filters, fmt, sort, direction)
    return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"})


@router.get("/{lead_id}")
def lead_detail(lead_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return lead_sheet.get_lead_detail(db, user, lead_id)


# ------------------------------------------------------------------ work

class LeadPatch(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    status: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=5000)
    next_follow_up_at: datetime | None = None
    position_applied: str | None = Field(default=None, max_length=200)
    country_interest: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    whatsapp: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    gender: str | None = Field(default=None, max_length=20)
    dob: date | None = None
    marital_status: str | None = Field(default=None, max_length=120)
    education_level: str | None = Field(default=None, max_length=200)
    experience_restaurant_years: float | None = Field(default=None, ge=0, le=60)
    experience_total_years: float | None = Field(default=None, ge=0, le=60)


@router.patch("/{lead_id}")
def edit_lead(lead_id: str, body: LeadPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lead = lead_sheet.load_lead(db, lead_id, lock=True)
    lead_sheet.ensure_can_work_lead(db, lead, user)
    sent = {}
    for name in body.model_fields_set:
        value = getattr(body, name)
        camel = body.model_fields[name].alias or name
        sent[camel] = value
    lead_sheet.patch_lead(db, user, lead, sent)
    db.commit()
    return lead_sheet.lead_response(db, lead_id)


class CallBody(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    outcome: Literal["answered", "interested", "not_interested", "no_answer", "wrong_number"]
    remarks: str | None = Field(default=None, max_length=2000)
    next_follow_up_at: datetime | None = None


@router.post("/{lead_id}/call")
def call_lead(lead_id: str, body: CallBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lead = lead_sheet.load_lead(db, lead_id, lock=True)
    lead_sheet.ensure_can_work_lead(db, lead, user)
    lead_sheet.log_call(db, user, lead, body.outcome, body.remarks, body.next_follow_up_at, "next_follow_up_at" in body.model_fields_set)
    db.commit()
    return lead_sheet.lead_response(db, lead_id)


class AssignBody(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    ids: list[str] | None = Field(default=None, max_length=20000)
    filter: dict | None = None
    owner_id: str = Field(min_length=1, max_length=36)
    only_unassigned: bool = False


@router.post("/assign")
def assign(body: AssignBody, db: Session = Depends(get_db), user: User = Depends(supervisors)):
    result = lead_sheet.assign_leads(db, user, body.ids, body.filter, body.owner_id, body.only_unassigned)
    db.commit()
    return result


@router.post("/{lead_id}/convert")
def convert(lead_id: str, resume: UploadFile | None = File(default=None), overrides: str | None = Form(default=None),
            resume_check_id: str | None = Form(default=None),
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    allowed = {"name", "phone", "email", "dob", "gender", "currentCity", "designations", "totalExperience", "countryPreference", "sourceDetail", "assignTo"}
    values = {}
    if overrides:
        try:
            values = json.loads(overrides)
            if not isinstance(values, dict) or set(values) - allowed:
                raise ValueError()
        except ValueError:
            raise fail(422, "invalid_request", "overrides must be a JSON object using only: " + ", ".join(sorted(allowed))) from None
    return lead_sheet.convert_lead(db, user, lead_id, values, resume, resume_check_id=resume_check_id)


@router.post("/{lead_id}/resume-check")
def check_resume(lead_id: str, resume: UploadFile | None = File(default=None), overrides: str | None = Form(default=None),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    from app.services import lead_resume_check
    try:
        values = json.loads(overrides) if overrides else {}
        if not isinstance(values, dict) or set(values) - {"name"} or any(not isinstance(v, str) for v in values.values()):
            raise ValueError()
    except ValueError:
        raise fail(422, "invalid_request", "Resume check overrides must contain only the candidate name.") from None
    return lead_resume_check.start(db, user, lead_id, resume, values)


@router.get("/{lead_id}/resume-check/{job_id}")
def resume_check_status(lead_id: str, job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from app.services import lead_resume_check
    return lead_resume_check.status(db, user, lead_id, job_id)


class LinkBody(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=36)


class UnconvertBody(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=1000)


@router.get("/{lead_id}/unconvert-check")
def unconvert_check(lead_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    from app.services import lead_reversal
    return lead_reversal.check(db, user, lead_id)


@router.post("/{lead_id}/unconvert")
def unconvert(lead_id: str, body: UnconvertBody, background: BackgroundTasks, db: Session = Depends(get_db),
              user: User = Depends(require_role(Role.admin))):
    from app.services import lead_reversal
    return lead_reversal.reverse(db, user, lead_id, body.candidate_id, body.reason, background)


@router.post("/{lead_id}/link-candidate")
def link(lead_id: str, body: LinkBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lead = lead_sheet.load_lead(db, lead_id, lock=True)
    lead_sheet.ensure_can_work_lead(db, lead, user)
    lead_sheet.link_candidate(db, user, lead, body.candidate_id)
    db.commit()
    return lead_sheet.lead_response(db, lead_id)
