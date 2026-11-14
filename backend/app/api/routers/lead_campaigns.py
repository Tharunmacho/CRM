"""Lead sheet extras: campaign performance, WhatsApp templates and message log, and "which leads is this candidate from".

Contract: docs/workflows/lead-sheet-contract.md (sections "Campaigns", "WhatsApp templates", "Candidate link").
Registered in app/main.py BEFORE the leads router, because `/api/leads/campaigns` would otherwise be read as `/api/leads/{lead_id}`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Wire
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, Lead, Role, User
from app.db.session import get_db
from app.services import agency_settings, lead_campaigns, lead_sheet, lead_templates

router = APIRouter(prefix="/api/leads", tags=["leads"])
admin_only = require_role(Role.admin)


@router.get("/campaigns")
def campaign_table(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """One row per campaign (and its forms): counts, rates, platform split, top positions and countries. Staff: their own leads only."""
    return lead_campaigns.campaigns(db, user)


# ------------------------------------------------------------------ WhatsApp templates

def _company_name(db: Session) -> str:
    row = agency_settings.agency_row(db)
    return (row.short_name or row.name) if row else agency_settings.FALLBACK_NAME


@router.get("/wa-templates")
def wa_templates(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"templates": lead_templates.templates(db), "placeholders": list(lead_templates.PLACEHOLDERS),
            "companyName": _company_name(db), "bodyMax": lead_templates.BODY_MAX, "titleMax": lead_templates.TITLE_MAX}


class TemplateEntry(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    key: str = Field(max_length=40)
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=2000)
    reset: bool = False


class TemplatesBody(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    templates: list[TemplateEntry] = Field(min_length=1, max_length=10)


@router.put("/wa-templates")
def save_wa_templates(body: TemplatesBody, db: Session = Depends(get_db), user: User = Depends(admin_only)):
    """Administrator: change the wording of the WhatsApp templates (or reset one to the default)."""
    result = lead_templates.save(db, [entry.model_dump() for entry in body.templates])
    db.add(AuditEntry(actor_id=user.id, action="Updated lead WhatsApp templates", target="Lead sheet", entity_type="lead_templates", entity_id="wa",
                      meta={"keys": [entry.key for entry in body.templates]}))
    db.commit()
    return {"templates": result, "placeholders": list(lead_templates.PLACEHOLDERS), "companyName": _company_name(db),
            "bodyMax": lead_templates.BODY_MAX, "titleMax": lead_templates.TITLE_MAX}


class WhatsAppLog(Wire):
    model_config = ConfigDict(alias_generator=Wire.model_config["alias_generator"], populate_by_name=True, extra="forbid")
    template: str | None = Field(default=None, max_length=40)
    message: str = Field(default="", max_length=2000)


@router.post("/{lead_id}/whatsapp")
def log_whatsapp(lead_id: str, body: WhatsAppLog, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Record that a WhatsApp message to the lead was opened (the chosen text goes into the history). Same permission as a call."""
    lead = lead_sheet.load_lead(db, lead_id, lock=True)
    lead_sheet.ensure_can_work_lead(db, lead, user)
    message = " ".join(body.message.split())
    lead_sheet.log_activity(db, lead.id, user.id, "note", ("WhatsApp message opened" + (f": {message}" if message else ""))[:600],
                            {"kind": "whatsapp", "template": body.template, "length": len(message)})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ candidate link

@router.get("/for-candidate/{candidate_id}")
def leads_of_candidate(candidate_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The lead-sheet entries a candidate came from (converted or linked) or matches by phone / e-mail: `{leads: [{id, name, campaignName, ...}]}`."""
    rows = db.scalars(select(Lead).where((Lead.candidate_id == candidate_id) | (Lead.matched_candidate_id == candidate_id), lead_sheet.visible_leads(user))
                      .order_by(Lead.created_time.desc().nulls_last(), Lead.id).limit(5)).all()
    return {"leads": [{"id": lead.id, "name": lead.name, "campaignName": lead.campaign_name, "formName": lead.form_name,
                       "status": lead.status, "converted": lead.candidate_id == candidate_id} for lead in rows]}
