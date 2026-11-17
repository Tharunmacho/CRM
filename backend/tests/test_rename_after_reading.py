"""A candidate whose name could not be read at import is "Name not read yet" and its files are "<CI_167>-<Document>.ext".

When the first reading names the person, every file carrying the care id stem (or the placeholder's) is renamed to the real name:
the candidate's own copies, the stage copies and the stage / draft references to them. DB-backed (disposable PostgreSQL).
"""
import pytest
from sqlalchemy import select

from app.db.models import Application, ApplicationStage, Attachment, Candidate
from app.services import application_form, form_extraction as fx
from app.services.bulk_candidate_import import name_slug
from app.services.candidate_review import PENDING_NAME
from test_application_form import as_user, desk, form, url  # noqa: F401  (fixtures and helpers)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_identity import _job_output, _resume_of
from test_identity_confirmation import _file, _job, _quiet  # noqa: F401  (autouse fixture)

REAL = "Rashidkhan_Nawazkhan_Mansur-"


def _pending(env, *, extra=()):
    """The candidate as the import leaves it when no document showed a name, with its files, a stage copy and the stage / draft references."""
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name, candidate.field_sources = PENDING_NAME, {fx.PROVISIONAL_NAME: "pending"}
        stem = name_slug(candidate.care_id) + "-"        # the care id is set on creation and never changes: "CI_001-"
        resume = _file(db, "resume", stem + "Resume.pdf")
        passports = [_file(db, "passport", stem + name) for name in ("Passport.pdf", "Passport-2.pdf")]
        others = [_file(db, "aadhaar", name) for name in extra]
        application = Application(id="app-1", candidate_id="candidate", order_id="order", owner_id="staff", current_stage=3)
        db.add(application)
        db.flush()
        db.add(Attachment(id="stage-copy", entity_type="application", entity_id=application.id, slot="stage_2_file", version=1, file_name=stem + "Passport.pdf",
                          size_kb=1, content_type="application/pdf", object_key="k/stage", content_hash="stage", uploaded_by="staff"))
        db.add(ApplicationStage(application_id=application.id, stage=2, data={"passport": {"id": passports[0].id, "name": stem + "Passport.pdf"}}))
        application.draft = {"file": {"id": resume.id, "name": stem + "Resume.pdf"}}
        db.commit()
        return resume.id, [item.id for item in passports], [item.id for item in others]


def _read_resume(env, attachment_id, name="RASHIDKHAN NAWAZKHAN MANSUR"):
    output = _job_output(resume=_resume_of(name))
    with env.sessions() as db:
        job = _job(db, db.get(Attachment, attachment_id), output)
        application_form.apply_job_output(db, job, output)
        db.commit()
        return job.status


def _names(env):
    with env.sessions() as db:
        return {row.id: row.file_name for row in db.scalars(select(Attachment))}


def test_the_first_reading_renames_every_care_id_file_and_every_reference(desk):
    resume_id, passport_ids, _ = _pending(desk)
    assert _read_resume(desk, resume_id) == "completed"
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.name == "Rashidkhan Nawazkhan Mansur" and not fx.provisional_name(candidate)
        assert db.get(Attachment, resume_id).file_name == REAL + "Resume.pdf"
        assert [db.get(Attachment, item).file_name for item in passport_ids] == [REAL + "Passport.pdf", REAL + "Passport-2.pdf"]   # "-2" kept
        assert db.get(Attachment, "stage-copy").file_name == REAL + "Passport.pdf"
        assert db.scalar(select(ApplicationStage.data)) == {"passport": {"id": passport_ids[0], "name": REAL + "Passport.pdf"}}
        assert db.get(Application, "app-1").draft == {"file": {"id": resume_id, "name": REAL + "Resume.pdf"}}
        assert not any(name.startswith(name_slug(candidate.care_id) + "-") for name in db.scalars(select(Attachment.file_name)))


def test_a_file_a_person_renamed_stays_and_a_second_rename_changes_nothing(desk):
    resume_id, passport_ids, (own,) = _pending(desk, extra=["Scan of my passport.pdf"])
    assert _read_resume(desk, resume_id) == "completed"
    names = _names(desk)
    assert names[own] == "Scan of my passport.pdf"                       # neither stem: left alone
    assert names[passport_ids[0]] == REAL + "Passport.pdf"
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert application_form.rename_imported_files(db, candidate, PENDING_NAME, candidate.name) == 0
        assert application_form.rename_imported_files(db, candidate, "Rashidkhan Nawazkhan Mansur", candidate.name) == 0
        db.commit()
    assert _names(desk) == names


def test_both_stems_are_old_when_the_placeholder_is_replaced(desk):
    """A legacy row may carry the placeholder's own stem as well as the care id's: both become the real name."""
    resume_id, passport_ids, (legacy,) = _pending(desk, extra=["Name_not_read_yet-Visa.pdf"])
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name = "Rashidkhan Nawazkhan Mansur"
        assert application_form.rename_imported_files(db, candidate, PENDING_NAME, candidate.name) == 5   # resume, two passports, the legacy file, the stage copy
        db.commit()
    names = _names(desk)
    assert names[legacy] == REAL + "Visa.pdf" and names[resume_id] == REAL + "Resume.pdf"
    assert names[passport_ids[1]] == REAL + "Passport-2.pdf"


def test_a_candidate_named_from_the_sheet_is_not_renamed(desk):
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name, candidate.field_sources = "Gunavelan R", {}
        resume = _file(db, "resume", "Gunavelan_R-Resume.pdf")
        other = _file(db, "passport", name_slug(candidate.care_id) + "-Passport.pdf")
        db.commit()
        resume_id, other_id = resume.id, other.id
    before = _names(desk)
    _read_resume(desk, resume_id, "G GUNAVELAN")
    assert _names(desk) == before
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").name == "Gunavelan R"
        assert application_form.rename_imported_files(db, db.get(Candidate, "candidate"), "Gunavelan R", "Gunavelan R") == 0
    assert _names(desk)[other_id] == before[other_id]
