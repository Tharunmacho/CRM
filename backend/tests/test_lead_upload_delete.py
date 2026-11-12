"""Permanent upload deletion uses only the existing disposable PostgreSQL fixture."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import models as m
from app.services import lead_followups, lead_upload_delete as subject
from lead_sheet_fixtures import csv_bytes
from test_database_naming import schema_database  # noqa: F401
from test_lead_sheet import env, upload, act_as  # noqa: F401


def make_batch(env, names=("First synthetic", "Second synthetic")):
    rows = [[name, f"90000800{index:02d}", "Yes"] for index, name in enumerate(names)]
    result = upload(env, csv_bytes(rows, headers=["Name", "Phone", "Work experience"]), name="synthetic-upload.csv")
    assert result.status_code == 200, result.text
    return result.json()["batchId"]


def preview(env, batch):
    result = env.client.get(f"/api/leads/batches/{batch}/deletion-preview")
    assert result.status_code == 200, result.text
    return result.json()


def remove(env, batch, state=None, **overrides):
    state = state or preview(env, batch)
    body = {"previewToken": state["previewToken"], "expectedLeadCount": state["leadCount"], "confirm": "DELETE", **overrides}
    return env.client.request("DELETE", f"/api/leads/batches/{batch}/permanent", json=body)


def leads(env, batch):
    return list(env.db.scalars(select(m.Lead).where(m.Lead.batch_id == batch).order_by(m.Lead.id)))


def job(env, lead, status="completed", candidate=None, shared=False):
    attachment = m.Attachment(entity_type="lead", entity_id=lead.id, slot="resume", version=1,
        file_name="synthetic.pdf", object_key="test/source", content_type="application/pdf", content_hash="a"*64, uploaded_by="admin")
    env.db.add(attachment); env.db.flush()
    reading = m.DocumentExtractionJob(owner_id="admin", group_id=str(uuid4()), kind="resume", filename="synthetic.pdf",
        content_type="application/pdf", object_key="test/source", content_hash="a"*64, size_kb=1, status=status,
        attachment_id=attachment.id, candidate_id=candidate,
        draft={"leadCheck": {"leadId":lead.id,"sourceAttachmentId":attachment.id}},
        state={"documents":{"resume":{"object_key":"test/derived.pdf"}}})
    env.db.add(reading);env.db.commit()
    return reading, attachment


def test_preview_is_readonly_and_deletion_removes_only_owned_upload_with_confirmed_retry(env, monkeypatch):
    first=make_batch(env, ("Earlier synthetic",))
    second=make_batch(env, ("Earlier synthetic", "New synthetic"))
    state=preview(env, second)
    assert (state["leadCount"],state["duplicateRows"],state["protectedCount"],state["canDelete"])==(1,1,0,True)
    assert preview(env,second)==state
    survivor=leads(env,first)[0].id
    owned=leads(env,second)[0]
    owned.owner_id="a";owned.next_follow_up_at=datetime.now(timezone.utc)+timedelta(days=1)
    owned.call_count=1;owned.notes="Synthetic call notes"
    env.db.add(m.LeadActivity(lead_id=owned.id,user_id="admin",kind="call",summary="Synthetic answered call"))
    lead_followups.sync(env.db,[owned.id]);env.db.commit()
    reading, attachment=job(env,owned)
    reading_id,attachment_id=reading.id,attachment.id
    state=preview(env,second)
    observed=[]
    def cleanup(engine,keys):
        with Session(engine) as db:
            assert db.get(m.LeadBatch,second) is None and db.get(m.Lead,owned.id) is None
        observed.append(keys)
    monkeypatch.setattr(subject,"cleanup_files",cleanup)
    result=remove(env,second,state)
    assert result.status_code==200,result.text
    data=result.json()
    assert data["deletedLeads"]==1 and data["deletedActivities"]==2 and data["deletedFollowUps"]==1
    assert data["deletedAttachments"]==data["deletedExtractionJobs"]==1
    assert data["duplicateRows"]==1 and data["preservedExistingLeadCount"]==1 and observed==[{"test/source","test/derived.pdf"}]
    env.db.expire_all()
    assert env.db.get(m.Lead,survivor) is not None and env.db.get(m.LeadBatch,first) is not None
    assert env.db.get(m.LeadBatch,second) is None
    assert env.db.get(m.DocumentExtractionJob,reading_id) is None and env.db.get(m.Attachment,attachment_id) is None
    assert remove(env,second,state).json()==data
    assert env.db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action==subject.ACTION))==1
    assert len(observed)==1


@pytest.mark.parametrize("role",["manager","a"])
def test_non_admin_cannot_preview_or_delete(env,role):
    batch=make_batch(env);state=preview(env,batch);act_as(env,role)
    assert env.client.get(f"/api/leads/batches/{batch}/deletion-preview").status_code==403
    assert remove(env,batch,state).status_code==403
    assert len(leads(env,batch))==2


@pytest.mark.parametrize("change",["lead","activity","attachment","count","actor","typed"])
def test_confirmation_refuses_changed_data_count_actor_or_missing_typed_delete(env,change):
    batch=make_batch(env);state=preview(env,batch);lead=leads(env,batch)[0]
    options={}
    if change=="lead":lead.notes="Changed after preview"
    elif change=="activity":env.db.add(m.LeadActivity(lead_id=lead.id,user_id="admin",kind="note",summary="New note"))
    elif change=="attachment":job(env,lead)
    elif change=="count":options["expectedLeadCount"]=1
    elif change=="typed":options["confirm"]=True
    else:
        env.db.add(m.User(id="other-admin",name="Other admin",email="other-admin@example.test",role=m.Role.admin));env.db.commit();act_as(env,"other-admin")
    env.db.commit()
    result=remove(env,batch,state,**options)
    assert result.status_code==(422 if change=="typed" else 409),result.text
    assert len(leads(env,batch))==2


@pytest.mark.parametrize("protection",["converted","linked","pending","processing","retry","candidate-job","review","foreign-source"])
def test_protected_record_refuses_entire_upload_without_partial_deletion(env,protection):
    batch=make_batch(env);lead=leads(env,batch)[0]
    if protection in {"linked","candidate-job"}:
        env.db.add(m.Candidate(id="protected-candidate",ref="CND-TEST",name="Synthetic candidate",phone="9000099988",source=m.Source.other));env.db.flush()
    if protection=="converted":lead.status="converted"
    elif protection=="linked":lead.candidate_id="protected-candidate"
    else:
        reading,_=job(env,lead,status=protection if protection in subject.ACTIVE else "completed",candidate="protected-candidate" if protection=="candidate-job" else None)
        if protection=="review":env.db.add(m.MultiPersonIntake(source_type="manual",source_id=reading.group_id,created_by="admin",status="review"))
        if protection=="foreign-source":reading.draft={"leadCheck":{"leadId":lead.id,"sourceAttachmentId":"another-record-source"}}
    env.db.commit();state=preview(env,batch)
    assert state["canDelete"] is False and state["protectedCount"]==1
    assert remove(env,batch,state).status_code==409
    assert len(leads(env,batch))==2
    assert env.db.get(m.LeadBatch,batch) is not None


def test_duplicate_only_upload_can_be_removed_without_touching_existing_person(env):
    first=make_batch(env);second=make_batch(env)
    state=preview(env,second);assert state["leadCount"]==0 and state["duplicateRows"]==2
    assert remove(env,second,state).status_code==200
    assert len(leads(env,first))==2 and env.db.get(m.LeadBatch,first) is not None


def test_resume_check_lock_survives_other_session_commits_and_always_releases(env):
    batch=make_batch(env);lead=leads(env,batch)[0];state=preview(env,batch)
    admin=env.db.get(m.User,"admin");env.db.commit()
    with pytest.raises(ValueError,match="synthetic failure"):
        with subject.resume_check_guard(env.db,lead.id):
            env.db.execute(text("SELECT 1"));env.db.commit()
            with Session(env.engine) as other:
                with pytest.raises(HTTPException) as refused:
                    subject.remove(other,admin,batch,state["previewToken"],2,"DELETE")
                assert refused.value.status_code==409 and refused.value.detail["code"]=="upload_busy"
                other.rollback()
            raise ValueError("synthetic failure")
    with subject.resume_check_guard(env.db,lead.id):
        pass
    assert remove(env,batch,state).status_code==200


def test_in_progress_edit_is_refused_without_deadlock_or_partial_delete(env):
    batch=make_batch(env);state=preview(env,batch);lead=leads(env,batch)[0];env.db.commit()
    with env.engine.connect() as editing:
        editing.execute(text("SELECT id FROM crm_leads WHERE id=:id FOR UPDATE"),{"id":lead.id})
        assert remove(env,batch,state).status_code==409
        editing.rollback()
    assert remove(env,batch,state).status_code==200


def test_cleanup_preserves_shared_files_and_failure_does_not_undo_success(env,monkeypatch):
    from app.services import document_storage
    batch=make_batch(env);owned=leads(env,batch)[0];job(env,owned)
    env.db.add(m.Attachment(entity_type="client",entity_id="synthetic-client",slot="agreement",version=1,file_name="shared.pdf",object_key="test/source"));env.db.commit()
    removed=[]
    def deletion(settings,key):
        removed.append(key)
        raise OSError("synthetic storage outage")
    monkeypatch.setattr(document_storage,"delete_bytes",deletion)
    result=remove(env,batch)
    assert result.status_code==200 and result.json()["deleted"] is True
    assert removed==["test/derived.pdf"]


def test_old_partial_revert_endpoint_remains_compatible(env):
    batch=make_batch(env)
    result=env.client.delete(f"/api/leads/batches/{batch}")
    assert result.status_code==200 and result.json()=={"deleted":2,"kept":0}
    assert env.db.get(m.LeadBatch,batch) is not None


def test_internal_duplicate_is_not_claimed_as_preserved_person_and_reimport_is_new(env):
    first=make_batch(env,("External existing",))
    content=csv_bytes([["External existing","9000080000","Yes"], ["New person","9000080011","No"],
                      ["Repeated new person","9000080011","No"]],headers=["Name","Phone","Work experience"])
    imported=upload(env,content,name="mixed.csv").json();batch=imported["batchId"]
    state=preview(env,batch)
    assert (state["leadCount"],state["duplicateRows"],state["preservedExistingLeadCount"])==(1,2,1)
    assert remove(env,batch,state).status_code==200
    again=upload(env,content,name="mixed.csv").json()
    assert again["rowsNew"]==1 and again["rowsDuplicate"]==2 and again["sameFileBefore"] is None
    assert len(leads(env,first))==1


def test_delete_then_same_file_reupload_creates_all_leads_again(env):
    batch=make_batch(env);assert remove(env,batch).status_code==200
    repeated=make_batch(env)
    assert repeated!=batch and len(leads(env,repeated))==2
    assert env.db.get(m.LeadBatch,batch) is None


def test_uncommitted_deletion_blocks_new_resume_check_and_rollback_preserves_upload(env):
    batch=make_batch(env);state=preview(env,batch);lead_id=leads(env,batch)[0].id;admin=env.db.get(m.User,"admin")
    env.db.commit()
    with Session(env.engine) as deleting:
        subject.remove(deleting,admin,batch,state["previewToken"],2,"DELETE")
        with pytest.raises(HTTPException) as refused:
            with subject.resume_check_guard(env.db,lead_id):
                pytest.fail("A check must not start while deletion holds its transaction lock")
        assert refused.value.status_code==409
        deleting.rollback()
    assert len(leads(env,batch))==2
    with subject.resume_check_guard(env.db,lead_id):
        pass
