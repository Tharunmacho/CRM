"""Merge a duplicate candidate into the existing one: the service and POST /api/candidates/{source}/merge-into/{target}.

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL, migrated to head). The app's own session settings are
used (no autoflush, no expiry on commit), so a missing flush in the merge shows up here as it would in production.
"""
import json
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.routers import candidate_merge as merge_route, candidates as candidates_route
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import application_form, form_extraction as fx
from app.services.document_identity import IDENTITY_REJECTED_ACTION, IDENTITY_RESOLVED_ACTION, IDENTITY_REVIEW_MESSAGE
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_identity import _job_output, _passport_of

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)
SRC, TGT = "dup", "keep"


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(candidates_route, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local"))
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args, **kwargs: {})
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([m.User(id=role, name=role.title(), email=f"{role}@example.test", role=m.Role(role)) for role in ("admin", "manager", "staff")])
        db.add(m.Client(id="cl", name="Contact"))
        db.flush()
        db.add_all([m.Company(id="co", client_id="cl", name="Employer"), m.Designation(id="de", title="Welder")])
        db.flush()
        db.add_all([m.Order(id="o1", ref="ORD-1", company_id="co", designation_id="de", openings=2),
                    m.Order(id="o2", ref="ORD-2", company_id="co", designation_id="de", openings=2)])
        db.commit()

    def http(who="admin"):
        app = FastAPI()
        app.include_router(merge_route.router)
        app.include_router(candidates_route.router)

        def database():
            with sessions() as db:
                yield db

        def who_am_i():
            with sessions() as db:
                return db.get(m.User, who)

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[current_user] = who_am_i
        return TestClient(app)

    yield SimpleNamespace(sessions=sessions, http=http, storage=tmp_path)


def person(db, key, **values):
    values = {"ref": f"C-{key}", "name": f"Person {key}", "phone": f"90000{abs(hash(key)) % 100000:05d}", "source": m.Source.walk_in,
              "owner_id": "staff", **values}
    db.add(m.Candidate(id=key, **values))
    db.flush()
    return key


def file(env, db, candidate, slot, name, content_hash, entity="candidate", version=None, write=True):
    key = f"{candidate}/{slot}/{name}"
    if write:
        (env.storage / candidate).mkdir(exist_ok=True)
        (env.storage / candidate / f"{slot}-{name}").write_bytes(b"data")
        key = f"{candidate}/{slot}-{name}"
    version = version or (db.scalar(select(func.count()).select_from(m.Attachment).where(
        m.Attachment.entity_type == entity, m.Attachment.entity_id == candidate, m.Attachment.slot == slot)) or 0) + 1
    attachment = m.Attachment(id=str(uuid.uuid4()), entity_type=entity, entity_id=candidate, slot=slot, version=version, file_name=name,
                              size_kb=1, content_type="application/pdf", object_key=key, content_hash=content_hash, uploaded_by="staff")
    db.add(attachment)
    db.flush()
    return attachment


def job(db, attachment, candidate, output=None, status="completed"):
    row = m.DocumentExtractionJob(
        id=str(uuid.uuid4()), owner_id="staff", group_id=str(uuid.uuid4()), kind=attachment.slot, filename=attachment.file_name,
        content_type="application/pdf", object_key=attachment.object_key, content_hash=attachment.content_hash, size_kb=1, status=status,
        candidate_id=candidate, attachment_id=attachment.id,
        state={"output": json.loads(json.dumps(output, default=str))} if output else {},
        draft={"applicationForm": {"candidateId": candidate, "slot": attachment.slot, "attachmentId": attachment.id}})
    db.add(row)
    db.flush()
    return row


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def merge(env, source=SRC, target=TGT, who="admin", **body):
    return env.http(who).post(f"/api/candidates/{source}/merge-into/{target}", json=body)


def snapshot(env):
    """Row counts of everything the merge could touch: a refused or dry-run request must leave all of them alone."""
    with env.sessions() as db:
        return {model.__tablename__: count(db, model) for model in (
            m.Candidate, m.Attachment, m.DocumentExtractionJob, m.Task, m.Remark, m.ApplicationFeedback, m.Application, m.AuditEntry,
            m.Notification, m.TodoItem, m.MailMessage, m.Lead)} | {
            "target": [(a.id, a.entity_id, a.version, a.file_name) for a in db.scalars(select(m.Attachment).order_by(m.Attachment.id))]}


def seed_pair(env):
    """CI-139 (existing, real) and CI-167 (the duplicate import), the way the owner's data looks."""
    with env.sessions() as db:
        person(db, TGT, care_id="CI-139", name="Rashid Khan", phone="9000000001", passport_no="K1234567", father_name="Human Typed",
               gender="Male", field_sources={"personal.fatherName": "manual", "personal.gender": "extracted"},
               ocr_data={"resume": {"name": "Rashid Khan", "kept": "target"}}, call_remarks="Called twice",
               email="rashid@example.test")
        person(db, SRC, care_id="CI-167", name="Rashid Khan Alnoor", phone="9000000002", father_name="Someone Else", marital_status="Married",
               nationality="Indian", dob=date(1990, 1, 1), call_remarks="Wants Dubai", gulf_experience=3,
               field_sources={"personal.fatherName": "extracted", "personal.maritalStatus": "extracted", "personal.dateOfBirth": "extracted"},
               ocr_data={"resume": {"name": "Rashid Khan", "kept": "source"}, "passport": {"mrz": {"passport_number": "K1234567"}}})
        # Documents: the resume is the same file on both (skipped), the rest moves as the next versions.
        target_resume = file(env, db, TGT, "resume", "Rashid_Khan-Resume.pdf", "h-resume")
        file(env, db, TGT, "photo", "Rashid_Khan-Photo.jpg", "h-photo")
        file(env, db, SRC, "photo", "Rashid_Khan_Alnoor-Photo.jpg", "h-photo")          # the same picture twice: skipped, no reading job
        source_resume = file(env, db, SRC, "resume", "Rashid_Khan_Alnoor-Resume.pdf", "h-resume")
        passport = file(env, db, SRC, "passport", "Rashid_Khan_Alnoor-Passport.pdf", "h-passport")
        aadhaar = file(env, db, SRC, "aadhaar", "Rashid_Khan_Alnoor-Aadhaar.pdf", "h-aadhaar")
        file(env, db, SRC, "other", "unrelated.pdf", "h-other")
        job(db, source_resume, SRC)
        passport_job = job(db, passport, SRC)
        job(db, aadhaar, SRC)
        # Work and notes.
        db.add_all([m.Task(id="call", kind=m.TaskKind.initial_call, title="First call", candidate_id=SRC, owner_id="staff"),
                    m.Task(id="done", kind=m.TaskKind.initial_call, title="Old call", candidate_id=SRC, owner_id="staff", completed_at=NOW)])
        db.flush()
        db.add_all([m.Remark(task_id="call", body="Phone was off", author_id="staff"),
                    m.ApplicationFeedback(candidate_id=SRC, author_id="staff", kind="update", body="Waiting for the passport copy"),
                    m.Notification(user_id="staff", kind="candidate_assignment", title="New", link_to=f"/candidates/{SRC}/application"),
                    m.TodoItem(id="todo", owner_id="staff", title="Follow up", due_at=NOW, link_to=f"/candidates/{SRC}", link_label="Person"),
                    m.AuditEntry(actor_id="staff", action="Imported candidate", target="Rashid Khan Alnoor", entity_type="candidate", entity_id=SRC)])
        db.commit()
        return SimpleNamespace(source_resume=source_resume.id, target_resume=target_resume.id, passport=passport.id, aadhaar=aadhaar.id,
                               passport_job=passport_job.id)


# ------------------------------------------------------------------------------------------------ what moves

def test_merge_moves_documents_jobs_notes_and_tasks_and_removes_the_duplicate(env):
    ids = seed_pair(env)
    response = merge(env)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mergedInto"] == {"id": TGT, "careId": "CI-139", "name": "Rashid Khan"} and body["removedSource"] is True
    assert body["moved"]["documents"] == 3 and body["moved"]["jobs"] == 3 and body["moved"]["notes"] == 2 and body["moved"]["tasks"] == 2
    assert [item["kind"] for item in body["skipped"]] == ["document", "document"] and "already on CI-139" in body["skipped"][0]["reason"]
    with env.sessions() as db:
        assert db.get(m.Candidate, SRC) is None and db.get(m.Candidate, TGT) is not None
        # Documents: the same id and file, now the target's next versions; the identical resume was not copied again.
        moved = {a.slot: a for a in db.scalars(select(m.Attachment).where(m.Attachment.id.in_([ids.passport, ids.aadhaar])))}
        assert {a.entity_id for a in moved.values()} == {TGT} and moved["passport"].version == 1 and moved["aadhaar"].version == 1
        assert count(db, m.Attachment, m.Attachment.entity_id == TGT) == 5          # resume, photo, passport, aadhaar, other
        assert db.get(m.Attachment, ids.source_resume) is None and db.get(m.Attachment, ids.target_resume).version == 1
        # Files keep their names but carry the target's stem.
        names = set(db.scalars(select(m.Attachment.file_name).where(m.Attachment.entity_id == TGT)))
        assert {"Rashid_Khan-Passport.pdf", "Rashid_Khan-Aadhaar.pdf", "Rashid_Khan-Resume.pdf", "unrelated.pdf"} <= names
        # Reading jobs follow the documents; the one that read the duplicate resume now reads the target's copy.
        jobs = db.scalars(select(m.DocumentExtractionJob)).all()
        assert len(jobs) == 3 and {j.candidate_id for j in jobs} == {TGT}
        assert {j.attachment_id for j in jobs} == {ids.target_resume, ids.passport, ids.aadhaar}
        assert next(j for j in jobs if j.id == ids.passport_job).draft["applicationForm"] == {"candidateId": TGT, "slot": "passport", "attachmentId": ids.passport}
        # Work and notes.
        assert count(db, m.Task, m.Task.candidate_id == TGT) == 2 and count(db, m.Remark) == 1
        assert db.scalar(select(m.ApplicationFeedback.candidate_id)) == TGT
        assert db.get(m.TodoItem, "todo").link_to == f"/candidates/{TGT}" and db.scalar(select(m.Notification.link_to)) == f"/candidates/{TGT}/application"
        assert count(db, m.Attachment, m.Attachment.entity_id == SRC) == 0 and count(db, m.DocumentExtractionJob, m.DocumentExtractionJob.candidate_id == SRC) == 0


def test_target_values_win_and_empty_target_fields_take_the_duplicates(env):
    seed_pair(env)
    assert merge(env).status_code == 200
    with env.sessions() as db:
        target = db.get(m.Candidate, TGT)
        assert target.father_name == "Human Typed" and fx.sources_of(target)["personal.fatherName"] == "manual"   # typed by a person: kept
        assert target.gender == "Male" and target.phone == "9000000001" and target.name == "Rashid Khan"
        assert target.marital_status == "Married" and target.nationality == "Indian" and target.dob == date(1990, 1, 1)   # empty here: filled
        assert target.gulf_experience == 3
        sources = fx.sources_of(target)
        assert sources["personal.maritalStatus"] == "extracted" and sources["personal.fatherName"] == "manual"
        assert target.call_remarks == "Called twice\n\nFrom duplicate CI-167: Wants Dubai"
        # What was read: the target's own reading stays primary, the duplicate's passport reading is added, nothing is lost.
        assert target.ocr_data["resume"]["kept"] == "target" and target.ocr_data["passport"]["mrz"]["passport_number"] == "K1234567"
        assert target.ocr_data["additional_documents"] == [{"resume": {"name": "Rashid Khan", "kept": "source"}}]


def test_files_of_moved_documents_stay_in_storage_and_only_the_duplicates_leftovers_go(env):
    ids = seed_pair(env)
    assert merge(env).status_code == 200
    stored = {path.name for path in (env.storage / SRC).iterdir()}
    with env.sessions() as db:
        kept = {a.object_key for a in db.scalars(select(m.Attachment).where(m.Attachment.entity_id == TGT))}
    assert {"dup/passport-Rashid_Khan_Alnoor-Passport.pdf", "dup/aadhaar-Rashid_Khan_Alnoor-Aadhaar.pdf", "dup/other-unrelated.pdf"} <= kept
    assert {"passport-Rashid_Khan_Alnoor-Passport.pdf", "aadhaar-Rashid_Khan_Alnoor-Aadhaar.pdf", "other-unrelated.pdf"} <= stored   # moved: untouched
    assert "photo-Rashid_Khan_Alnoor-Photo.jpg" not in stored                       # the skipped duplicate picture went
    assert "resume-Rashid_Khan_Alnoor-Resume.pdf" in stored                         # a moved reading job still reads that stored file
    assert (env.storage / TGT / "resume-Rashid_Khan-Resume.pdf").exists() and (env.storage / TGT / "photo-Rashid_Khan-Photo.jpg").exists()


def test_the_audit_trail_follows_and_both_records_get_an_entry(env):
    seed_pair(env)
    assert merge(env).status_code == 200
    with env.sessions() as db:
        merged = db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Merged duplicate CI-167 into this candidate"))
        assert merged.entity_id == TGT and merged.actor_id == "admin" and merged.meta["mergedFrom"]["careId"] == "CI-167"
        assert merged.meta["moved"]["documents"] == 3 and merged.meta["renamedFiles"] == 2
        tombstone = db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Duplicate candidate merged and removed"))
        assert tombstone.entity_id == SRC and tombstone.meta["mergedIntoId"] == TGT and tombstone.meta["mergedInto"]["careId"] == "CI-139"
        assert db.scalar(select(m.AuditEntry.entity_id).where(m.AuditEntry.action == "Imported candidate")) == TGT     # history follows
        assert db.scalar(select(m.AuditEntry.entity_id).where(m.AuditEntry.action == "Deleted candidate")) == SRC       # the CRM's own delete entry


def test_links_to_the_duplicate_now_point_at_the_existing_candidate(env):
    seed_pair(env)
    with env.sessions() as db:
        account = m.MailAccount(id="acct", address="hr@example.test", provider="imap")
        db.add(account)
        db.flush()
        db.add(m.MailMessage(id="msg", account_id="acct", provider_message_id="p1", received_at=NOW, candidate_id=SRC))
        db.add(m.Lead(id="lead", name="Lead", phone="9111111111", candidate_id=SRC, matched_candidate_id=SRC))
        db.commit()
    assert merge(env).status_code == 200
    with env.sessions() as db:
        assert db.get(m.MailMessage, "msg").candidate_id == TGT
        lead = db.get(m.Lead, "lead")
        assert lead.candidate_id == TGT and lead.matched_candidate_id == TGT


def test_a_placement_moves_when_only_the_duplicate_is_on_an_order(env):
    seed_pair(env)
    with env.sessions() as db:
        db.add(m.Application(id="app", candidate_id=SRC, order_id="o1", owner_id="staff", status=m.ApplicationStatus.active))
        db.flush()
        db.add(m.ApplicationStage(application_id="app", stage=1, data={}))
        file(env, db, "app", "receipt", "Rashid_Khan_Alnoor-Receipt.pdf", "h-receipt", entity="application")
        db.add(m.Task(id="stage", kind=m.TaskKind.stage, title="Stage", candidate_id=SRC, application_id="app", owner_id="staff"))
        db.commit()
    assert merge(env).status_code == 200
    with env.sessions() as db:
        application = db.get(m.Application, "app")
        assert application.candidate_id == TGT and application.status is m.ApplicationStatus.active
        assert db.get(m.Task, "stage").candidate_id == TGT
        assert count(db, m.ApplicationStage, m.ApplicationStage.application_id == "app") == 1
        assert db.scalar(select(m.Attachment.file_name).where(m.Attachment.entity_type == "application")) == "Rashid_Khan-Receipt.pdf"   # stem follows too


# ------------------------------------------------------------------------------------------------ refusals

def refused(env, expected, **body):
    before = snapshot(env)
    response = merge(env, **body)
    assert response.status_code == 409, response.text
    assert expected in response.json()["detail"], response.json()
    assert snapshot(env) == before          # nothing was changed
    return response


def test_the_same_candidate_twice_is_refused(env):
    seed_pair(env)
    response = merge(env, source=TGT, target=TGT)
    assert response.status_code == 409 and "itself" in response.json()["detail"]


def test_different_passport_numbers_are_two_people_unless_an_admin_confirms_and_they_share_a_phone_or_email(env):
    seed_pair(env)
    with env.sessions() as db:
        db.get(m.Candidate, SRC).passport_no = "Z7654321"
        db.commit()
    refused(env, "different passport numbers")
    refused(env, "different passport numbers", confirmIdentity=True)           # confirming is not enough without a shared phone / e-mail
    with env.sessions() as db:
        db.get(m.Candidate, SRC).email = "RASHID@example.test"                  # same e-mail (case does not matter)
        db.commit()
    dry = merge(env, dryRun=True).json()
    assert dry["allowed"] is False and dry["refusals"][0]["code"] == "different_passports" and dry["refusals"][0]["canConfirm"] is True
    assert merge(env, confirmIdentity=True).status_code == 200
    with env.sessions() as db:
        assert db.get(m.Candidate, TGT).passport_no == "K1234567"              # the existing candidate's passport stays


def test_no_sign_of_the_same_person_needs_the_admins_confirmation(env):
    with env.sessions() as db:
        person(db, TGT, name="Alpha One", phone="9100000001")
        person(db, SRC, name="Alpha One", phone="9100000002")
        db.commit()
    dry = merge(env, dryRun=True).json()
    assert dry["allowed"] is False and dry["refusals"][0]["code"] == "identity_unconfirmed"
    refused(env, "Nothing shows")
    assert merge(env, confirmIdentity=True).status_code == 200


def test_a_shared_phone_is_enough_evidence(env):
    with env.sessions() as db:
        person(db, TGT, name="Alpha One", phone="+91 91000 00001")
        person(db, SRC, name="Alpha Uno", phone="9100000001")
        db.commit()
    assert merge(env).status_code == 200


def test_a_deployed_travelled_or_invoiced_duplicate_is_never_merged_away(env):
    seed_pair(env)
    with env.sessions() as db:
        db.add(m.Application(id="gone", candidate_id=SRC, order_id="o1", owner_id="staff", status=m.ApplicationStatus.deployed))
        db.commit()
    refused(env, "deployed or has travelled", confirmIdentity=True)
    with env.sessions() as db:
        db.delete(db.get(m.Application, "gone"))
        db.add(m.CandidateInvoice(id="inv", candidate_id=SRC, scope_key="", number="INV-1"))
        db.commit()
    refused(env, "invoice")
    with env.sessions() as db:
        person(db, "traveller", care_id="CI-300", name="Rashid Khan", phone="9000000001", traveller_id="CI-T-001")
        db.commit()
    response = merge(env, source="traveller", target=TGT)
    assert response.status_code == 409 and "deployed or has travelled" in response.json()["detail"]


def deploy_target(env):
    """CI-139 as the owner has it: deployed at stage 11, with its own tasks, an issued invoice and a stage document."""
    with env.sessions() as db:
        db.add(m.Application(id="t-app", candidate_id=TGT, order_id="o1", owner_id="staff", status=m.ApplicationStatus.deployed, current_stage=11))
        db.flush()
        db.add(m.ApplicationStage(application_id="t-app", stage=11, data={"flown": True}))
        db.add(m.Task(id="t-task", kind=m.TaskKind.stage, title="Stage 11", candidate_id=TGT, application_id="t-app", owner_id="staff", completed_at=NOW))
        db.add(m.CandidateInvoice(id="t-inv", candidate_id=TGT, application_id="t-app", scope_key="t-app", number="INV-9"))
        db.get(m.Candidate, TGT).traveller_id = "CI-T-001"
        db.commit()


def target_state(env):
    with env.sessions() as db:
        target = db.get(m.Candidate, TGT)
        app = db.get(m.Application, "t-app")
        return {"owner": target.owner_id, "status": target.status, "traveller": target.traveller_id, "phone": target.phone,
                "app": (app.candidate_id, app.order_id, app.status, app.current_stage, app.owner_id, app.lock_version),
                "stages": [(st.stage, st.attempt, st.data) for st in db.scalars(select(m.ApplicationStage).where(m.ApplicationStage.application_id == "t-app"))],
                "tasks": sorted(db.scalars(select(m.Task.id).where(m.Task.candidate_id == TGT))),
                "invoices": sorted(db.scalars(select(m.CandidateInvoice.id).where(m.CandidateInvoice.candidate_id == TGT)))}


def test_a_deployed_existing_candidate_only_receives_documents_and_empty_details(env):
    seed_pair(env)
    deploy_target(env)
    with env.sessions() as db:   # the duplicate is on an order, has a stage task and a draft invoice: none of that goes onto the deployed candidate
        db.add(m.Application(id="s-app", candidate_id=SRC, order_id="o2", owner_id="staff", status=m.ApplicationStatus.active))
        db.flush()
        db.add(m.ApplicationStage(application_id="s-app", stage=1, data={}))
        db.add(m.Task(id="s-task", kind=m.TaskKind.stage, title="Stage", candidate_id=SRC, application_id="s-app", owner_id="staff"))
        db.add(m.CandidateInvoice(id="s-inv", candidate_id=SRC, application_id="s-app", scope_key="s-app"))
        db.commit()
    before = target_state(env)
    plan = merge(env, dryRun=True).json()
    assert plan["allowed"] is True and plan["targetDeployed"] is True
    assert plan["notice"] == "CI-139 is deployed: documents and empty details are added, its stages are not touched."
    assert plan["moved"]["applications"] == 0 and plan["moved"]["tasks"] == 0 and plan["moved"]["documents"] == 3
    assert [item["kind"] for item in plan["skipped"] if item["kind"] in ("placement", "task")] == ["placement", "task"]
    assert any("ORD-2" in item["reason"] and "not carried over" in item["reason"] for item in plan["skipped"])
    response = merge(env)
    assert response.status_code == 200, response.text
    assert response.json()["moved"]["applications"] == 0 and response.json()["notice"] == plan["notice"]
    assert target_state(env) == before                                  # stage, status, owner, tasks, invoices, deployment data untouched
    with env.sessions() as db:
        assert db.get(m.Candidate, SRC) is None and db.get(m.Application, "s-app") is None and db.get(m.Task, "s-task") is None
        assert count(db, m.Application, m.Application.candidate_id == TGT) == 1
        target = db.get(m.Candidate, TGT)
        assert target.marital_status == "Married" and target.dob == date(1990, 1, 1)       # empty details were filled
        assert target.father_name == "Human Typed" and target.gender == "Male"             # populated ones were not
        assert count(db, m.Attachment, m.Attachment.entity_id == TGT) == 5                 # documents were added
        assert count(db, m.ApplicationFeedback, m.ApplicationFeedback.candidate_id == TGT) == 1   # notes too
        order = db.get(m.Order, "o2")
        assert order.status is m.OrderStatus.open


def test_a_deployed_target_with_a_duplicate_on_an_order_still_refuses_when_the_target_is_also_active(env):
    seed_pair(env)
    with env.sessions() as db:
        db.add_all([m.Application(id="a1", candidate_id=SRC, order_id="o1", owner_id="staff", status=m.ApplicationStatus.active),
                    m.Application(id="a2", candidate_id=TGT, order_id="o2", owner_id="staff", status=m.ApplicationStatus.active),
                    m.Application(id="a3", candidate_id=TGT, order_id="o1", owner_id="staff", status=m.ApplicationStatus.deployed)])
        db.commit()
    refused(env, "Both candidates are on an order")


def test_both_on_an_order_is_refused_with_the_orders_named(env):
    seed_pair(env)
    with env.sessions() as db:
        db.add_all([m.Application(id="a1", candidate_id=SRC, order_id="o1", owner_id="staff", status=m.ApplicationStatus.active),
                    m.Application(id="a2", candidate_id=TGT, order_id="o2", owner_id="staff", status=m.ApplicationStatus.active)])
        db.commit()
    response = refused(env, "Both candidates are on an order")
    assert "ORD-1" in response.json()["detail"] and "ORD-2" in response.json()["detail"]


def test_only_admin_level_roles_may_merge(env):
    seed_pair(env)
    before = snapshot(env)
    assert merge(env, who="staff").status_code == 403
    assert merge(env, who="staff", dryRun=True).status_code == 403
    assert snapshot(env) == before
    # An MD (stored as `manager`) holds every privilege an admin holds: the preview is allowed for them too.
    assert merge(env, who="manager", dryRun=True).status_code != 403


def test_an_unknown_candidate_is_not_found(env):
    seed_pair(env)
    assert merge(env, source="nobody").status_code == 404 and merge(env, target="nobody").status_code == 404


# ------------------------------------------------------------------------------------------------ dry run and retry

def test_dry_run_reports_what_would_happen_and_changes_nothing(env):
    seed_pair(env)
    before = snapshot(env)
    dry = merge(env, dryRun=True)
    assert dry.status_code == 200, dry.text
    plan = dry.json()
    assert plan["dryRun"] is True and plan["allowed"] is True and plan["refusals"] == []
    assert plan["source"]["careId"] == "CI-167" and plan["mergedInto"]["careId"] == "CI-139"
    assert plan["moved"]["documents"] == 3 and plan["moved"]["notes"] == 2 and plan["moved"]["tasks"] == 2 and plan["moved"]["jobs"] == 3
    assert [item["name"] for item in plan["skipped"]] == ["Rashid_Khan_Alnoor-Photo.jpg", "Rashid_Khan_Alnoor-Resume.pdf"]
    assert snapshot(env) == before
    assert (env.storage / SRC / "resume-Rashid_Khan_Alnoor-Resume.pdf").exists()
    real = merge(env).json()
    assert real["moved"]["documents"] == plan["moved"]["documents"] and real["moved"]["tasks"] == plan["moved"]["tasks"]    # the preview was true


def test_a_retry_after_success_is_safe(env):
    seed_pair(env)
    assert merge(env).status_code == 200
    after = snapshot(env)
    again = merge(env)
    assert again.status_code == 200 and again.json()["alreadyMerged"] is True and again.json()["mergedInto"]["careId"] == "CI-139"
    assert again.json()["removedSource"] is True
    assert snapshot(env) == after
    assert merge(env, source="never-existed").status_code == 404
    assert merge(env, dryRun=True).json()["alreadyMerged"] is True


# ------------------------------------------------------------------------------------------------ the point of it all

def test_a_passport_read_that_said_already_in_the_crm_is_accepted_on_the_existing_candidate_after_the_merge(env):
    with env.sessions() as db:
        person(db, TGT, care_id="CI-139", name="Rashid Khan", phone="9000000001", passport_no="K1234567")
        person(db, SRC, care_id="CI-167", name="Rashid Khan", phone="9000000002")
        passport = file(env, db, SRC, "passport", "Rashid_Khan-Passport.pdf", "h-passport")
        output = _job_output(passport=_passport_of("RASHID", "KHAN", "K1234567"))
        row = job(db, passport, SRC, output)
        application_form.apply_job_output(db, row, output)
        assert row.status == "needs_review" and row.last_error == IDENTITY_REVIEW_MESSAGE
        assert row.draft["applicationForm"]["result"]["duplicateOf"]["careId"] == "CI-139"     # "Already in the CRM as CI-139"
        job_id, attachment_id = row.id, passport.id
        db.commit()
    plan = merge(env, dryRun=True).json()
    assert plan["allowed"] is True and plan["evidence"]["samePassport"] is True          # the document's passport belongs to CI-139
    response = merge(env)
    assert response.status_code == 200, response.text
    assert response.json()["identityCheck"] == {"accepted": 1, "stillNeedReview": 0, "requeued": 0}
    with env.sessions() as db:
        row = db.get(m.DocumentExtractionJob, job_id)
        assert row.candidate_id == TGT and row.attachment_id == attachment_id and row.status == "completed" and row.last_error is None
        assert row.draft["applicationForm"]["candidateId"] == TGT and not row.draft["applicationForm"]["result"].get("identityMismatch")
        resolved = db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == IDENTITY_RESOLVED_ACTION)).all()
        assert [(entry.entity_id, entry.meta["attachment_id"], entry.meta["document_job_id"]) for entry in resolved] == [(TGT, attachment_id, job_id)]
        assert db.scalar(select(m.AuditEntry.entity_id).where(m.AuditEntry.action == IDENTITY_REJECTED_ACTION)) == TGT
        target = db.get(m.Candidate, TGT)
        assert target.passport_no == "K1234567" and target.dob == date(1990, 1, 1)           # the passport now fills the existing candidate
        assert target.ocr_data["passport"]["mrz"]["passport_number"] == "K1234567"
        assert db.get(m.Attachment, attachment_id).entity_id == TGT
