"""Lead conversion must verify the exact resume before writing any candidate."""
from sqlalchemy import func, select
import pytest
from app.db import models as m
from app.services import lead_resume_check
from app.api.routers import document_jobs
from lead_resume_fixtures import checked_resume
from lead_sheet_fixtures import meta_rows
from test_database_naming import schema_database  # noqa: F401
from test_lead_sheet import env, act_as, first_lead, imported, PDF  # noqa: F401


def lead(env):
    imported(env, meta_rows(0, 1), assign_to="a")
    item = env.db.get(m.Lead, first_lead(env)["id"])
    item.name = "Alex Example"
    env.db.commit()
    return item


def convert(env, lid, job=None, payload=PDF, overrides=None):
    import json
    data = {"resume_check_id": job.id} if job else {}
    if overrides: data["overrides"] = json.dumps(overrides)
    return env.client.post(f"/api/leads/{lid}/convert", files={"resume": ("cv.pdf", payload, "application/pdf")}, data=data)


def candidate_count(env):
    return env.db.scalar(select(func.count()).select_from(m.Candidate))


def test_resume_cannot_bypass_verification_with_direct_convert(env):
    item = lead(env)
    result = convert(env, item.id)
    assert result.status_code == 409 and result.json()["detail"]["code"] == "resume_verification_required"
    assert candidate_count(env) == 0 and item.candidate_id is None


@pytest.mark.parametrize("raw,status,expected", [({"name": "Completely Different"}, "completed", "rejected"),
    ({"contact": {"phones": ["9000000011"]}}, "completed", "needs_review"),
    ({"name": "Alex Example"}, "pending", "pending"), ({"name": "Alex Example"}, "needs_review", "needs_review")])
def test_unverified_or_wrong_identity_never_creates_candidate(env, raw, status, expected):
    item = lead(env); job = checked_resume(env, item.id, PDF, raw=raw, status=status)
    outcome = env.client.get(f"/api/leads/{item.id}/resume-check/{job.id}")
    assert outcome.status_code == 200 and outcome.json()["status"] == expected and not outcome.json()["canConvert"]
    assert convert(env, item.id, job).status_code == 409
    assert candidate_count(env) == 0 and item.candidate_id is None


def test_verified_resume_reuses_reading_and_field_provenance_atomically(env, monkeypatch):
    item = lead(env)
    job = checked_resume(env, item.id, PDF, raw={"name": item.name, "personal_info": {"father_name": "Parent Example"}})
    monkeypatch.setattr(document_jobs, "retry", lambda *a, **k: pytest.fail("Conversion must not queue another paid reading"))
    result = convert(env, item.id, job)
    assert result.status_code == 200 and result.json()["resumeAttached"], result.text
    candidate = env.db.get(m.Candidate, result.json()["candidate"]["id"])
    assert candidate.name == item.name and candidate.father_name == "Parent Example"
    assert candidate.field_sources["personal.fatherName"] == "extracted"
    assert env.db.scalar(select(func.count()).select_from(m.DocumentExtractionJob)) == 1
    assert job.candidate_id == candidate.id and item.candidate_id == candidate.id


@pytest.mark.parametrize("change", ["bytes", "lead_identity", "override", "other_owner", "other_lead"])
def test_verification_is_bound_to_exact_file_owner_lead_and_identity(env, change):
    item = lead(env); job = checked_resume(env, item.id, PDF)
    payload, overrides, target = PDF, None, item.id
    if change == "bytes": payload += b"changed"
    elif change == "lead_identity": item.name = "Another Person"; env.db.commit()
    elif change == "override": overrides = {"name": "Another Person"}
    elif change == "other_owner": job.owner_id = "b"; env.db.commit()
    else:
        other = m.Lead(name="Alex Example", phone="9990000000", status="new")
        env.db.add(other); env.db.commit(); target = other.id
    result = convert(env, target, job, payload, overrides)
    assert result.status_code in (404, 409), result.text
    assert candidate_count(env) == 0


def test_cached_check_revalidates_new_name_without_another_read(env, monkeypatch):
    item = lead(env); job = checked_resume(env, item.id, PDF)
    monkeypatch.setattr(document_jobs, "create", lambda **kwargs: {"id": job.id})
    monkeypatch.setattr(document_jobs, "retry", lambda *a, **k: pytest.fail("Completed output must be reused"))
    checked = env.client.post(f"/api/leads/{item.id}/resume-check", files={"resume": ("cv.pdf", PDF, "application/pdf")})
    assert checked.status_code == 200 and checked.json()["canConvert"]
    item.name = "Different Person"; env.db.commit()
    checked = env.client.post(f"/api/leads/{item.id}/resume-check", files={"resume": ("cv.pdf", PDF, "application/pdf")})
    assert checked.json()["status"] == "rejected"


def test_attachment_failure_rolls_back_new_candidate_and_lead_link(env, monkeypatch):
    item = lead(env); job = checked_resume(env, item.id, PDF)
    def failure(*args): raise OSError("Synthetic storage failure")
    monkeypatch.setattr(lead_resume_check, "attach_verified", failure)
    result = convert(env, item.id, job)
    assert result.status_code == 409 and candidate_count(env) == 0
    env.db.expire_all()
    assert env.db.get(m.Lead, item.id).candidate_id is None and env.db.get(m.DocumentExtractionJob, job.id).candidate_id is None


def test_final_identity_guard_rejection_rolls_back_candidate_attachment_and_link(env, monkeypatch):
    from app.services import application_form
    item = lead(env); job = checked_resume(env, item.id, PDF)
    def reject(db, record, output):
        record.status = "needs_review"
        record.draft = {**record.draft, "applicationForm": {**record.draft["applicationForm"],
                        "result": {"status": "needs_review", "identityMismatch": True}}}
    monkeypatch.setattr(application_form, "apply_job_output", reject)
    result = convert(env, item.id, job)
    assert result.status_code == 409 and candidate_count(env) == 0
    env.db.expire_all()
    assert env.db.get(m.Lead, item.id).candidate_id is None
    assert env.db.get(m.DocumentExtractionJob, job.id).attachment_id is None
    assert env.db.scalar(select(func.count()).select_from(m.Attachment)) == 0


def test_verified_source_is_retained_on_lead_when_candidate_is_reversed(env, monkeypatch):
    from app.api.routers import candidates
    item = lead(env); job = checked_resume(env, item.id, PDF)
    result = convert(env, item.id, job)
    assert result.status_code == 200, result.text
    source = lead_resume_check.source_attachment(env.db, item)
    assert source is not None and source.content_hash == job.content_hash
    monkeypatch.setattr(candidates, "_remove_stored_files", lambda *a: None)
    reversed_ = env.client.post(f"/api/leads/{item.id}/unconvert", json={"candidateId": result.json()["candidate"]["id"], "reason": "Synthetic reversal"})
    assert reversed_.status_code == 200, reversed_.text
    env.db.expire_all()
    assert env.db.get(m.Attachment, source.id) is not None and candidate_count(env) == 0


def test_replaced_saved_resume_invalidates_previous_check(env):
    item = lead(env)
    first = m.Attachment(entity_type="lead", entity_id=item.id, slot="resume", version=1, file_name="cv.pdf",
                         size_kb=1, object_key="synthetic/first.pdf", content_hash=__import__("hashlib").sha256(PDF).hexdigest())
    env.db.add(first); env.db.commit()
    job = checked_resume(env, item.id, PDF, original=True)
    env.db.add(m.Attachment(entity_type="lead", entity_id=item.id, slot="resume", version=2, file_name="replacement.pdf",
                           size_kb=1, object_key="synthetic/second.pdf", content_hash=first.content_hash)); env.db.commit()
    outcome = env.client.get(f"/api/leads/{item.id}/resume-check/{job.id}").json()
    assert not outcome["canConvert"] and "saved resume changed" in outcome["message"]
    result = env.client.post(f"/api/leads/{item.id}/convert", data={"resume_check_id": job.id})
    assert result.status_code == 409 and candidate_count(env) == 0
