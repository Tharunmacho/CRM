"""The candidate's name comes from the documents (never the folder), and the same candidate is never imported twice (disposable PostgreSQL)."""
import hashlib
import importlib.util
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import people_fixtures as pf
from app.db import models as m
from app.db.models import Attachment, Candidate
from app.services.candidate_review import PENDING_NAME
from test_bulk_candidate_import import env  # noqa: F401  (fixture)
from test_database_naming import schema_database  # noqa: F401  (fixture)

FOLDER = "rashid khan-alnoor"


def pdf(person: str, *tokens: str) -> bytes:
    return pf.build_pdf([pf.page(person, token) for token in tokens])


def post(env, files: dict[str, bytes], folder=FOLDER, *, row=None, key=None, name=None, from_files=False, action=None):
    spec = {**({"duplicateAction": action} if action else {}), "folder": folder, "row": {"phone": "9876500123"} if row is None else row, "files": [{"path": n} for n in files], "defaultOrder": None,
            "defaultRecruiterId": "staff", **({"importKey": key} if key else {}), **({"name": name, "nameFromFiles": from_files} if name else {})}
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)},
                               files=[("files", (n, d, "application/pdf")) for n, d in files.items()])
    assert response.status_code == 200, response.text
    return response.json()


def candidates(env):
    from sqlalchemy.orm import undefer
    with env.sessions() as db:
        return list(db.scalars(select(Candidate).options(undefer(Candidate.field_sources), undefer(Candidate.ocr_data)).order_by(Candidate.created_at)))


def files_of(env, candidate_id):
    with env.sessions() as db:
        return {row.slot: row.file_name for row in db.scalars(select(Attachment).where(Attachment.entity_id == candidate_id, Attachment.entity_type == "candidate"))}


# ------------------------------------------------------------------------------------------------ the name comes from the documents

def test_the_name_is_read_from_the_passport_and_the_files_are_named_after_it_never_the_folder(env):
    result = post(env, {"Passport.pdf": pdf("tharun", "passport_data", "passport_back")}, row={})
    assert result["nameSource"] == "documents" and result["status"] == "imported"
    [candidate] = candidates(env)
    assert candidate.name == "Tharun Pk Kumar" and "alnoor" not in candidate.name.lower() and "rashid" not in candidate.name.lower()
    assert candidate.field_sources.get("_provisionalName") == "documents"          # the first Veris reading may still replace it
    assert files_of(env, candidate.id) == {"passport": "Tharun_Pk_Kumar-Passport.pdf"}


def test_a_folder_whose_documents_show_no_name_gives_a_pending_name_and_ci_named_files(env):
    result = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic passport", "Resume.pdf": b"%PDF-1.7 synthetic resume"}, row={})
    assert result["nameSource"] == "pending"
    [candidate] = candidates(env)
    assert candidate.name == PENDING_NAME and candidate.field_sources.get("_provisionalName") == "pending"
    care = candidate.care_id.replace("-", "_")
    assert files_of(env, candidate.id) == {"passport": f"{care}-Passport.pdf", "resume": f"{care}-Resume.pdf"}
    # the folder's name appears nowhere: not in the name, the file names, the reading data or the field sources
    for text in (candidate.name, json.dumps(files_of(env, candidate.id)), json.dumps(candidate.ocr_data or {}), json.dumps(candidate.field_sources)):
        assert "alnoor" not in text.lower() and "rashid" not in text.lower()
    # and the first real name replaces the care-id stem of the files
    from app.services.application_form import rename_imported_files
    with env.sessions() as db:
        row = db.get(Candidate, candidate.id)
        assert rename_imported_files(db, row, row.care_id, "Rashid Khan Mansur") == 2
        db.commit()
    assert set(files_of(env, candidate.id).values()) == {"Rashid_Khan_Mansur-Passport.pdf", "Rashid_Khan_Mansur-Resume.pdf"}


def test_a_name_from_the_folder_or_the_files_of_the_screen_is_ignored_unless_it_was_read_from_documents(env):
    post(env, {"Passport.pdf": b"%PDF-1.7 synthetic"}, name="Rashid Khan Alnoor", from_files=False, row={})
    assert candidates(env)[0].name == PENDING_NAME
    post(env, {"Passport.pdf": b"%PDF-1.7 synthetic two"}, folder="another", name="Meera Sundaram", from_files=True, row={})
    assert [c.name for c in candidates(env)][1] == "Meera Sundaram"
    post(env, {"Passport.pdf": b"%PDF-1.7 synthetic three"}, folder="third", row={"name": "Typed By Office", "phone": "9876500999"})
    third = candidates(env)[2]
    assert third.name == "Typed By Office" and not third.field_sources.get("_provisionalName")


def test_two_candidates_with_a_pending_name_are_not_duplicates_of_each_other(env):
    first = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic a"}, folder="one", row={})
    second = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic b"}, folder="two", row={})
    assert first["status"] == "imported" and second["status"] == "imported" and first["careId"] != second["careId"]
    assert [c.name for c in candidates(env)] == [PENDING_NAME, PENDING_NAME]


# ------------------------------------------------------------------------------------------------ the same candidate twice

def test_importing_the_same_files_again_creates_no_second_candidate_and_uses_no_care_id(env):
    files = {"Passport.pdf": pdf("tharun", "passport_data", "passport_back"), "Medical.pdf": pdf("tharun", "medical")}
    first = post(env, files, row={})
    again = post(env, files, row={})
    assert again["status"] == "duplicate" and again["careId"] == first["careId"] and again["candidateId"] == first["candidateId"]
    assert again["matchedBy"] == "same file" and again["strength"] == "certain" and "import_anyway" not in again["choices"] and len(candidates(env)) == 1
    assert "Already in the CRM as" in again["message"] and "certain" in again["message"]
    other = post(env, {"Passport.pdf": pdf("meera", "passport_data", "passport_back")}, folder="meera folder", row={})
    assert other["status"] == "imported" and int(other["careId"].split("-")[1]) == int(first["careId"].split("-")[1]) + 1     # no number was used up
    assert len(candidates(env)) == 2


def test_the_same_import_key_answers_already_imported_whatever_the_files(env):
    first = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic a"}, row={}, key="attempt-1")
    second = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic different"}, row={}, key="attempt-1")
    assert second["status"] == "already_imported" and second["matchedBy"] == "import key" and second["careId"] == first["careId"]
    assert post(env, {"Passport.pdf": b"%PDF-1.7 synthetic different"}, row={}, key="attempt-2")["status"] == "imported"
    assert len(candidates(env)) == 2


def test_two_requests_at_the_same_moment_make_one_candidate(env):
    files = {"Passport.pdf": pdf("tharun", "passport_data", "passport_back"), "Medical.pdf": pdf("tharun", "medical")}
    answers: list[dict] = []

    def go():
        answers.append(post(env, files, row={}, key="double-click"))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answer["status"] for answer in answers) == ["already_imported", "imported"]      # (the key); without a key the files themselves answer "duplicate"
    assert len({answer["candidateId"] for answer in answers}) == 1 and len(candidates(env)) == 1


def test_a_different_person_with_the_same_first_name_is_not_flagged(env):
    one = post(env, {"Passport.pdf": pdf("ravi", "passport_data", "passport_back")}, folder="ravi one", row={"name": "Ravi Kumar", "phone": "9876500111"})
    two = post(env, {"Passport.pdf": pdf("arjun", "passport_data", "passport_back")}, folder="ravi two", row={"name": "Ravi Sharma", "phone": "9876500222"})
    assert one["status"] == "imported" and two["status"] == "imported" and len(candidates(env)) == 2


def test_a_candidate_with_the_same_phone_is_still_refused_as_before(env):
    post(env, {"Passport.pdf": b"%PDF-1.7 synthetic a"}, folder="a", row={"phone": "9876500444"})
    again = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic b"}, folder="b", row={"phone": "9876500444"})
    assert again["action"] == "skip" and again["status"] == "duplicate" and again["matchedBy"] == "phone" and again["strength"] == "possible"
    assert again["choices"] == ["skip", "add_documents", "import_anyway"] and len(candidates(env)) == 1
    anyway = post(env, {"Passport.pdf": b"%PDF-1.7 synthetic b"}, folder="b", row={"phone": "9876500444"}, action="import_anyway")      # a person's own decision
    assert anyway["status"] == "imported" and len(candidates(env)) == 2


def test_check_flags_a_folder_whose_files_are_already_in_the_crm(env):
    data = pdf("tharun", "medical")
    post(env, {"Medical.pdf": data}, row={})
    digest = hashlib.sha256(data).hexdigest()
    body = {"folders": [{"folder": "again", "files": [{"path": "Medical.pdf", "sha256": digest}]},
                        {"folder": "new", "files": [{"path": "Medical.pdf", "sha256": hashlib.sha256(b"other").hexdigest()}]}], "rows": []}
    plans = {plan["folder"]: plan for plan in env.client.post("/api/candidate-import/check", json=body).json()["plans"]}
    known = plans["again"]["alreadyInCrm"]
    assert known["matchedBy"] == "same file" and known["files"] == 1 and known["careId"] and plans["again"]["action"] == "skip"
    assert any("Already in the CRM as" in issue["message"] for issue in plans["again"]["issues"])
    assert plans["new"]["alreadyInCrm"] is None and plans["new"]["action"] != "skip"
    assert plans["new"]["name"] == PENDING_NAME and plans["new"]["nameSource"] == "pending"      # the folder is not the name


def test_the_identify_step_flags_a_person_whose_phone_or_passport_is_already_known_without_returning_numbers(env):
    from app.api.routers import candidate_import as router
    post(env, {"Passport.pdf": b"%PDF-1.7 synthetic"}, row={"phone": "9876500555", "name": "Known Person"})
    people = [{"name": "Known Person", "_match": {"passports": [], "aadhaars": [], "phones": ["9876500555"], "emails": []}},
              {"name": "Someone Else", "_match": {"passports": [], "aadhaars": [], "phones": ["9123456780"], "emails": []}}]
    with env.sessions() as db:
        found = router._people_in_crm(db, people)
    assert set(found) == {0} and found[0]["matchedBy"] == "phone" and found[0]["careId"]
    assert "9876500555" not in json.dumps(found)


# ------------------------------------------------------------------------------------------------ the read-only script

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "find_duplicate_candidates.py"
spec = importlib.util.spec_from_file_location("find_duplicate_candidates", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def test_the_script_groups_the_same_person_names_the_one_to_keep_and_changes_nothing(schema_database, tmp_path, capsys):
    from test_database_naming import _migrate
    _migrate(schema_database, "head")
    with Session(schema_database, expire_on_commit=False) as db:
        db.add(m.User(id="staff", name="Staff", email="staff@example.test", role=m.Role.staff, active=True))
        db.flush()
        for number, (key, phone, passport) in enumerate([("a", "9000000001", "K1234567"), ("b", "9000000002", "K1234567"), ("c", "9000000003", None),
                                                          ("d", "9000000003", None), ("e", "9000000009", None)], 157):
            db.add(m.Candidate(id=key, ref=f"CND-{number:05d}", care_id=f"CI-{number}", name=f"Rashid {key}", phone=phone, passport_no=passport,
                               source=m.Source.walk_in, owner_id="staff", field_sources={"personal.candidateName": "extracted"} if key == "b" else {}))
        db.flush()
        for owner, digests in (("c", ["h1", "h2", "h4", "h5"]), ("d", ["h1", "h2", "h3", "h4", "h5"]), ("e", ["h9", "h8", "h7", "h1"])):      # e shares ONE of its four files: not the same person
            for version, digest in enumerate(digests, 1):
                db.add(m.Attachment(entity_type="candidate", entity_id=owner, slot="other", version=version,
                                    file_name=f"{owner}-{digest}.pdf", object_key=f"{owner}/{digest}", content_hash=digest, uploaded_by="staff"))
        db.commit()
    groups = script.find_groups(*_load(schema_database))
    members = sorted(sorted(member["careId"] for member in group["members"]) for group in groups)
    assert members == [["CI-157", "CI-158"], ["CI-159", "CI-160"]]
    by_care = {member["careId"]: group for group in groups for member in group["members"]}
    assert by_care["CI-158"]["keep"] == "b"                    # the one with details read
    assert by_care["CI-159"]["keep"] == "d"                    # more documents
    class Args:
        json = False
    assert script.run(schema_database, Args) == 0
    shown = capsys.readouterr().out
    assert "delete_candidate_permanently.py --candidate CND-00157" in shown and "KEEP" in shown and "CND-00161" not in shown
    with Session(schema_database) as db:
        assert len(list(db.scalars(select(Candidate)))) == 5          # nothing was deleted


def _load(engine):
    from sqlalchemy.orm import undefer
    with Session(engine) as db:
        candidates_ = list(db.scalars(select(Candidate).options(undefer(Candidate.field_sources))))
        attachments = [(o, s, d) for o, s, d in db.execute(select(Attachment.entity_id, Attachment.slot, Attachment.content_hash).where(Attachment.entity_type == "candidate"))]
    return candidates_, attachments


def test_the_script_refuses_the_shared_database_without_allow_remote(monkeypatch, capsys):
    monkeypatch.delenv("CRM_ENV_FILE", raising=False)
    assert script.main([]) == 2 and "Refused" in capsys.readouterr().err
    assert script.main(["--database-url", "postgresql+psycopg://u:p@db.example.com/crm"]) == 2


# ------------------------------------------------------------------------------------------------ a person already in the CRM

def deployed_tharun(env):
    """CI-139: a travelled (deployed) candidate imported earlier, with his passport number, owner and a placement."""
    person = pf.person("tharun")
    with env.sessions() as db:
        db.add(m.Candidate(id="travelled", ref="CND-00139", care_id="CI-139", name="Tharun Pk Kumar", phone="9000000139", passport_no=person.passport_no,
                           status=m.CandidateStatus.deployed, source=m.Source.other, owner_id="staff"))
        db.flush()
        db.add(m.Application(id="app-139", candidate_id="travelled", order_id="o1", owner_id="staff", current_stage=12, status=m.ApplicationStatus.deployed, lock_version=0, draft={}))
        db.commit()


def test_named_files_whose_passport_matches_a_deployed_candidate_create_nobody_and_use_no_care_id(env):
    deployed_tharun(env)
    before = [c.care_id for c in candidates(env)]
    result = post(env, {"Passport.pdf": pdf("tharun", "passport_data", "passport_back"), "Visa.pdf": pdf("tharun", "evisa")}, folder="named files", row={})
    assert result["status"] == "duplicate" and result["careId"] == "CI-139" and result["candidateId"] == "travelled"
    assert result["matchedBy"] == "passport" and result["strength"] == "certain" and result["choices"] == ["skip", "add_documents"]
    assert "CI-139" in result["message"] and "certain: same passport" in result["message"]
    assert [c.care_id for c in candidates(env)] == before
    # "import anyway" is never offered for a certain match, and is refused if asked for
    refused = post(env, {"Passport.pdf": pdf("tharun", "passport_data", "passport_back")}, folder="named files", row={}, action="import_anyway")
    assert refused["status"] == "duplicate" and len(candidates(env)) == 1
    nobody_after = post(env, {"Passport.pdf": pdf("meera", "passport_data", "passport_back")}, folder="someone else", row={})
    assert nobody_after["status"] == "imported" and nobody_after["careId"] == "CI-140"       # nothing was numbered by the refused attempts


def test_add_documents_files_into_the_existing_candidate_without_touching_its_stages_status_or_owner(env):
    deployed_tharun(env)
    files = {"Passport.pdf": pdf("tharun", "passport_data", "passport_back"), "Visa.pdf": pdf("tharun", "evisa")}
    added = post(env, files, folder="named files", row={}, action="add_documents")
    assert added["status"] == "documents_added" and added["candidateId"] == "travelled" and len(candidates(env)) == 1
    assert {(d["slot"], d["added"]) for d in added["documents"]} == {("passport", True), ("visa", True)}
    with env.sessions() as db:
        candidate = db.get(Candidate, "travelled")
        assert candidate.status == m.CandidateStatus.deployed and candidate.owner_id == "staff" and candidate.name == "Tharun Pk Kumar"
        application = db.get(m.Application, "app-139")
        assert application.status == m.ApplicationStatus.deployed and application.current_stage == 12
        assert db.scalar(select(func.count()).select_from(m.ApplicationStage).where(m.ApplicationStage.application_id == "app-139")) == 0
        held = {row.slot: row for row in db.scalars(select(Attachment).where(Attachment.entity_id == "travelled"))}
        assert held["passport"].file_name == "Tharun_Pk_Kumar-Passport.pdf" and held["passport"].version == 1
    again = post(env, files, folder="named files", row={}, action="add_documents")            # nothing is added twice, nothing overwritten
    assert all(d["alreadyFiled"] and not d["added"] for d in again["documents"])
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == "travelled")) == len(held)


def test_a_phone_only_match_is_possible_and_offers_import_anyway(env):
    deployed_tharun(env)
    result = post(env, {"Passport.pdf": pdf("meera", "passport_data", "passport_back")}, folder="someone", row={"phone": "9000000139"})
    assert result["status"] == "duplicate" and result["matchedBy"] == "phone" and result["strength"] == "possible"
    assert result["choices"] == ["skip", "add_documents", "import_anyway"] and result["careId"] == "CI-139"
