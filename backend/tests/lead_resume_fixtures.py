"""Synthetic completed readings; no provider, document storage or worker is used."""
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select
from app.db import models as m
from app.services import lead_resume_check


def checked_resume(env, lead_id, payload, *, owner=None, name=None, filename="cv.pdf", original=False, raw=None, status="completed", overrides=None):
    lead = env.db.get(m.Lead, lead_id)
    attachment = lead_resume_check.source_attachment(env.db, lead) if original else None
    data = raw if raw is not None else {"name": name or lead.name}
    job = m.DocumentExtractionJob(id=str(uuid4()), owner_id=owner or env.who["id"], group_id=str(uuid4()), kind="resume",
          filename=filename, content_type="application/pdf", object_key=attachment.object_key if attachment else "synthetic/checked.pdf",
          content_hash=sha256(payload).hexdigest(), size_kb=1, status=status,
          state={"output": {"status": "complete", "extractions": {"resume": {"raw": data}}}},
          draft={"leadCheck": {"leadId": lead_id, "identityKey": lead_resume_check.identity_key(lead),
                 "candidateIdentity": lead_resume_check.candidate_identity(lead, overrides),
                 "sourceAttachmentId": attachment.id if attachment else None}})
    env.db.add(job); env.db.commit()
    return job


RESUME = b"%PDF-1.7 synthetic resume"


def convert_with_resume(env, lead_id, *, overrides=None, payload=RESUME, filename="cv.pdf"):
    """Convert a lead the way the screen does once a resume is in hand: the resume rides with the request together with
    a completed, matching resume check (conversion needs a resume; a lead without one is refused with resume_required)."""
    import json
    job = checked_resume(env, lead_id, payload, filename=filename, overrides=overrides,
                         name=(overrides or {}).get("name"))
    data = {"resume_check_id": job.id}
    if overrides:
        data["overrides"] = json.dumps(overrides)
    return env.client.post(f"/api/leads/{lead_id}/convert", files={"resume": (filename, payload, "application/pdf")}, data=data)
