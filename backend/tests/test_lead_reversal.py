"""Move back to Lead retains original lead evidence and refuses shared/worked records."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.api.routers import candidates
from app.db import models as m
from test_database_naming import schema_database  # noqa: F401
from test_lead_sheet import act_as, add_candidate, env, first_lead, imported  # noqa: F401
from lead_resume_fixtures import convert_with_resume
from lead_sheet_fixtures import meta_rows


def converted(env):
    imported(env, meta_rows(0, 1), assign_to="a")
    lead = first_lead(env)
    response = convert_with_resume(env, lead["id"])  # a lead becomes a candidate only with a checked resume
    assert response.status_code == 200, response.text
    return lead["id"], response.json()["candidate"]["id"]


def undo(env, lead_id, candidate_id, reason="Wrong resume was selected"):
    return env.client.post(f"/api/leads/{lead_id}/unconvert", json={"candidateId": candidate_id, "reason": reason})


def test_reverse_keeps_original_lead_evidence_and_is_retry_safe(env, monkeypatch):
    lid, cid = converted(env)
    lead = env.db.get(m.Lead, lid)
    before = (lead.name, lead.phone, lead.email, lead.notes, lead.owner_id)
    activities_before = env.db.scalar(select(func.count()).select_from(m.LeadActivity).where(m.LeadActivity.lead_id == lid))
    # the conversion kept the checked resume on the lead and attached the same stored file to the candidate
    from app.services import lead_resume_check
    lead_resume = lead_resume_check.source_attachment(env.db, lead)
    candidate_resume = env.db.scalar(select(m.Attachment).where(m.Attachment.entity_type == "candidate", m.Attachment.entity_id == cid,
                                                                m.Attachment.slot == "resume"))
    assert lead_resume is not None and candidate_resume is not None
    shared_key, lead_resume_id, candidate_resume_id = candidate_resume.object_key, lead_resume.id, candidate_resume.id
    assert lead_resume.object_key == shared_key
    cleanup = []
    monkeypatch.setattr(candidates, "_remove_stored_files", lambda *args: cleanup.append(args))
    check = env.client.get(f"/api/leads/{lid}/unconvert-check")
    assert check.status_code == 200 and check.json()["allowed"] and check.json()["restoredStatus"] == "new", check.text
    result = undo(env, lid, cid)
    assert result.status_code == 200, result.text
    assert result.json()["lead"]["candidateId"] is None and result.json()["restoredStatus"] == "new"
    env.db.expire_all()
    assert env.db.get(m.Candidate, cid) is None and env.db.get(m.Attachment, candidate_resume_id) is None
    assert env.db.get(m.Attachment, lead_resume_id).object_key == shared_key, "the lead's own resume evidence is kept"
    lead = env.db.get(m.Lead, lid)
    assert (lead.name, lead.phone, lead.email, lead.notes, lead.owner_id) == before
    assert env.db.scalar(select(func.count()).select_from(m.LeadActivity).where(m.LeadActivity.lead_id == lid)) == activities_before + 1
    assert undo(env, lid, cid).json()["alreadyReversed"] is True and len(cleanup) == 1


@pytest.mark.parametrize("work", ["completed", "remark", "event", "daily_work", "new_task"])
def test_untouched_conversion_task_can_be_removed_but_started_work_blocks(env, work):
    from datetime import timedelta
    lid, cid = converted(env)
    task = env.db.scalar(select(m.Task).where(m.Task.candidate_id == cid))
    assert task is not None
    assert env.client.get(f"/api/leads/{lid}/unconvert-check").json()["allowed"]
    now = datetime.now(timezone.utc)
    if work == "completed": task.completed_at = now
    elif work == "remark": env.db.add(m.Remark(task_id=task.id, author_id="a", body="Actual work"))
    elif work == "event": env.db.add(m.TaskEvent(task_id=task.id, candidate_id=cid, kind="assignment", event_key="synthetic:assignment", occurred_at=now))
    elif work == "daily_work": env.db.add(m.DailyWorkItem(owner_id="a", candidate_id=cid, kind="call", work_date=now.date(), slot_no=1, session="morning", due_at=now+timedelta(hours=1), started_at=now))
    else: env.db.add(m.Task(kind=m.TaskKind.initial_call, candidate_id=cid, title="Later added call", owner_id="a", created_at=now+timedelta(seconds=10), due_at=now+timedelta(hours=1)))
    env.db.commit()
    assert not env.client.get(f"/api/leads/{lid}/unconvert-check").json()["allowed"]
    assert undo(env, lid, cid).status_code == 409


@pytest.mark.parametrize("who", ["a", "b", "manager"])
def test_reversal_removes_candidate_so_only_admin_can_use_it(env, who):
    lid, cid = converted(env)
    act_as(env, who)
    assert env.client.get(f"/api/leads/{lid}/unconvert-check").status_code == 403
    assert undo(env, lid, cid).status_code == 403


def test_existing_candidate_link_cannot_be_reversed_as_a_deletion(env):
    imported(env, meta_rows(0, 1))
    lid = first_lead(env)["id"]
    candidate = add_candidate(env)
    assert env.client.post(f"/api/leads/{lid}/link-candidate", json={"candidateId": candidate.id}).status_code == 200
    check = env.client.get(f"/api/leads/{lid}/unconvert-check").json()
    assert not check["allowed"] and "existing candidate" in check["reason"]
    assert undo(env, lid, candidate.id).status_code == 409


@pytest.mark.parametrize("guard", ["shared", "financial", "ticket", "invoice", "feedback", "application"])
def test_candidate_history_blocks_reversal(env, guard):
    lid, cid = converted(env)
    if guard == "shared":
        env.db.add(m.Lead(id="other-lead", name="Other synthetic lead", candidate_id=cid, status="converted"))
    elif guard == "financial":
        env.db.add(m.CandidateInvoice(candidate_id=cid, scope_key="", service_fee=5000, status="pending_ticket"))
    elif guard in ("ticket", "invoice"):
        env.db.add(m.Attachment(entity_type="candidate", entity_id=cid, slot="air_ticket" if guard == "ticket" else "invoice",
                               version=1, file_name="Synthetic.pdf", size_kb=1, object_key="synthetic/protected.pdf", uploaded_by="admin"))
    elif guard == "feedback":
        env.db.add(m.ApplicationFeedback(candidate_id=cid, author_id="admin", kind="update", body="Recruiter note"))
    else:
        env.db.add(m.Client(id="cl", name="Client")); env.db.flush()
        env.db.add(m.Company(id="co", client_id="cl", name="Company"))
        env.db.add(m.Designation(id="de", title="Synthetic title")); env.db.flush()
        env.db.add(m.Order(id="order", ref="ORD-TEST", company_id="co", designation_id="de", openings=1)); env.db.flush()
        env.db.add(m.Application(candidate_id=cid, order_id="order", owner_id="a", status=m.ApplicationStatus.closed, current_stage=1))
    env.db.commit()
    check = env.client.get(f"/api/leads/{lid}/unconvert-check").json()
    assert not check["allowed"] and check["reason"]
    assert undo(env, lid, cid).status_code == 409
    env.db.expire_all()
    assert env.db.get(m.Candidate, cid) is not None and env.db.get(m.Lead, lid).candidate_id == cid


def test_reason_and_stale_candidate_are_validated_before_reversal(env):
    lid, cid = converted(env)
    assert undo(env, lid, cid, "   ").status_code == 422
    assert undo(env, lid, cid, "x" * 1001).status_code == 422
    assert undo(env, lid, "stale-id").status_code == 409
    assert env.db.get(m.Candidate, cid) is not None
