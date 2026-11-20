"""scripts/fix_imported_candidate.py on the guarded disposable PostgreSQL database (migrated to head).

Candidates are imported through the real bulk import path (the "Import candidates" code), then put back into the state an import
made BEFORE the stand-in-name fix left them in (no field_sources, the folder name as the name), with a fake reading of their
documents in `ocr_data`. The script is driven through its main(); the shared-database guard is faked (the real guard looks at
backend/.env and the SSH tunnel, which a test must never open).
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.db import models as m
from test_database_naming import schema_database  # noqa: F401  (fixture the import screen's env builds on)
from test_bulk_candidate_import import (  # noqa: F401  (env is a fixture)
    NEW_PREFIX, STANDARD_FOLDER, _add_attachment, _file_names, _file_references, env, send,
)

# The rows the script repairs are the OLD ones, whose folder-derived name stood in for a name (new imports never do that).
OLD_PREFIX = "Gunavelan_alnoor-"

BACKEND = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fix_imported_candidate", BACKEND / "scripts" / "fix_imported_candidate.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

NAME_KEY = "personal.candidateName"
GUNAVELAN = {"passport": {"mrz": {"given_names": "GUNAVELAN", "surname": "R"}}}       # read as "Gunavelan R"


@pytest.fixture
def fix(env, monkeypatch):
    """The script pointed at the test database, with the shared-database guard faked as 'a plain local database'."""
    monkeypatch.setattr(script, "SessionLocal", env.sessions)
    monkeypatch.setattr(script, "engine", env.sessions.kw["bind"])
    monkeypatch.setattr(script, "shared_database_reason", lambda: None)
    return env


def old_import(env, folder="Gunavelan-alnoor", names=STANDARD_FOLDER, phone="9876500080", reading=GUNAVELAN):
    """An import made before the fix: the folder name is the name, nothing marks it as a stand-in, and the documents were read."""
    result = send(env, folder, names, {"phone": phone}, default_order="ORD-1")
    with env.sessions() as db:
        candidate = db.get(m.Candidate, result["candidateId"])
        candidate.field_sources = {}
        candidate.ocr_data = reading
        db.commit()
    return result


def run(*argv):
    return script.main(list(argv))


def state(env, candidate_id):
    """Everything the script may change (and the audit log), to prove a dry run or a refusal wrote nothing."""
    with env.sessions() as db:
        candidate = db.get(m.Candidate, candidate_id)
        application_ids = list(db.scalars(select(m.Application.id).where(m.Application.candidate_id == candidate_id)))
        attachments = {a.id: a.file_name for a in db.scalars(select(m.Attachment).where(
            (m.Attachment.entity_type == "candidate") & (m.Attachment.entity_id == candidate_id)
            | (m.Attachment.entity_type == "application") & m.Attachment.entity_id.in_(application_ids)))}
        audits = sorted((a.action, str(a.meta)) for a in db.scalars(select(m.AuditEntry).where(m.AuditEntry.entity_id == candidate_id)))
        return candidate.name, dict(candidate.field_sources or {}), attachments, _file_references(env, candidate_id), audits


def fixed_audits(env, candidate_id):
    with env.sessions() as db:
        return list(db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == script.AUDIT_ACTION, m.AuditEntry.entity_id == candidate_id)))


def test_dry_run_changes_nothing_and_prints_every_old_to_new_name(fix, capsys):
    result = old_import(fix)
    cid, care = result["candidateId"], result["careId"]
    before, names = state(fix, cid), _file_names(fix, cid)
    assert run("--candidate", care) == 0
    out = capsys.readouterr().out
    assert state(fix, cid) == before
    assert "dry run" in out.lower() and "Dry run: nothing was written" in out
    assert "'Gunavelan-alnoor'  ->  'Gunavelan R'" in out and "passport document" in out
    assert len(names) >= 10 and any(owner == "application" for owner, _, _ in names)
    for old in names.values():
        assert f"{old}  ->  {NEW_PREFIX + old[len(OLD_PREFIX):]}" in out
    assert "Would change 1 candidate(s)" in out and "stage file reference(s)" in out


def test_apply_renames_the_candidate_every_attachment_and_the_stage_file_references(fix, capsys):
    result = old_import(fix)
    cid, care = result["candidateId"], result["careId"]
    other = send(fix, "Ravi Kumar", ["Passport.pdf", "Resume.pdf"], {"phone": "9876500081"}, default_order="ORD-1")
    names, refs, others_before = _file_names(fix, cid), _file_references(fix, cid), state(fix, other["candidateId"])
    with fix.sessions() as db:           # files the import did not make: a stage file added by hand, a document of an unknown slot
        application_id = db.scalar(select(m.Application.id).where(m.Application.candidate_id == cid))
        _add_attachment(db, "extra-1", "candidate", cid, "mystery", "Hand made.pdf")
        _add_attachment(db, "extra-2", "application", application_id, "stage_2_file", "Interview note.pdf")
        db.commit()
    assert {"stage 5", "stage 9", "stage 10", "draft"} <= {where for where, _ in refs}

    assert run("--candidate", care, "--apply") == 0
    out = capsys.readouterr().out
    assert "Changed 1 candidate(s)" in out
    after = {key: name for key, name in _file_names(fix, cid).items() if "extra-" not in key[1]}
    assert set(after) == set(names)
    for key, old in names.items():      # the candidate's own copies AND the stage copies: only the name part changes
        assert after[key] == NEW_PREFIX + old[len(OLD_PREFIX):], key
    assert any(owner == "application" for owner, _, _ in after) and not any(n.startswith(OLD_PREFIX) for n in after.values())
    assert after[("application", "stage_9_receipt", 1)] == NEW_PREFIX + "Advance_receipt_stage_9.pdf"
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Gunavelan R"
        assert candidate.field_sources[NAME_KEY] == "extracted" and "_provisionalName" not in candidate.field_sources
        assert candidate.field_sources["_fromPassport"] == [NAME_KEY]
        assert {a.id: a.file_name for a in db.scalars(select(m.Attachment).where(m.Attachment.id.like("extra-%")))} == {
            "extra-1": "Hand made.pdf", "extra-2": "Interview note.pdf"}                         # not imported documents: untouched
        attachments = {a.id: a.file_name for a in db.scalars(select(m.Attachment))}
    # the file references the stages and the draft keep follow their attachment
    new_refs = _file_references(fix, cid)
    assert set(new_refs) == set(refs)
    for key, ref in new_refs.items():
        assert ref["name"] == NEW_PREFIX + refs[key]["name"][len(OLD_PREFIX):] == attachments[ref["id"]], key
        assert {k: v for k, v in ref.items() if k != "name"} == {k: v for k, v in refs[key].items() if k != "name"}
    # the audit entry records the old name and old file names
    (entry,) = fixed_audits(fix, cid)
    assert entry.actor_id is None and entry.target == "Gunavelan R"
    assert entry.meta["old_name"] == "Gunavelan-alnoor" and entry.meta["new_name"] == "Gunavelan R"
    assert set(entry.meta["old_file_names"].values()) == set(names.values()) and len(entry.meta["old_file_names"]) == len(names)
    assert all(old.startswith(OLD_PREFIX) for old in entry.meta["old_file_names"].values())
    assert all(attachments[file_id] == NEW_PREFIX + old[len(OLD_PREFIX):] for file_id, old in entry.meta["old_file_names"].items())
    assert entry.meta["stage_references_updated"] == len(refs)
    assert state(fix, other["candidateId"]) == others_before                                    # another candidate is untouched


def test_files_that_do_not_follow_the_pattern_get_standard_names_and_a_second_file_of_one_kind_gets_a_number(fix, capsys):
    result = old_import(fix, names=["Passport.pdf", "Passport copy.pdf", "Resume.pdf", "Advance receipt.pdf"])
    cid = result["candidateId"]
    with fix.sessions() as db:          # an even older import: original names, not "<folder>-<Document>"
        for row in db.scalars(select(m.Attachment).where(m.Attachment.entity_id == cid, m.Attachment.slot == "passport")):
            row.file_name = f"IMG 00{row.version}.PDF"
        resume = db.scalar(select(m.Attachment).where(m.Attachment.entity_id == cid, m.Attachment.slot == "resume"))
        resume.file_name = "cv final (1).pdf"
        db.commit()
    assert run("--candidate", result["careId"]) == 0
    out = capsys.readouterr().out
    assert "IMG 001.PDF  ->  Gunavelan_R-Passport.pdf" in out and "IMG 002.PDF  ->  Gunavelan_R-Passport-2.pdf" in out
    assert "cv final (1).pdf  ->  Gunavelan_R-Resume.pdf" in out
    assert run("--candidate", result["careId"], "--apply") == 0
    names = _file_names(fix, cid)
    assert names[("candidate", "passport", 1)] == "Gunavelan_R-Passport.pdf" and names[("candidate", "passport", 2)] == "Gunavelan_R-Passport-2.pdf"
    assert names[("candidate", "resume", 1)] == "Gunavelan_R-Resume.pdf"
    assert names[("application", "stage_9_receipt", 1)] == "Gunavelan_R-Advance_receipt_stage_9.pdf"
    assert len(set(n.lower() for n in names.values() if n != names[("application", "stage_9_receipt", 1)])) >= 3


def test_a_second_run_changes_nothing_and_writes_no_audit_entry(fix, capsys):
    result = old_import(fix)
    cid, care = result["candidateId"], result["careId"]
    assert run("--candidate", care, "--apply") == 0
    first = state(fix, cid)
    capsys.readouterr()
    assert run("--candidate", care, "--apply") == 0
    out = capsys.readouterr().out
    assert state(fix, cid) == first and len(fixed_audits(fix, cid)) == 1
    assert "nothing to do" in out and "Changed 0 candidate(s)" in out
    assert run("--candidate", care) == 0 and state(fix, cid) == first      # and a dry run reports nothing to do as well
    assert "Would change 0 candidate(s)" in capsys.readouterr().out


def test_the_name_is_cleaned_from_the_passport_and_the_passport_outranks_the_aadhaar_card_and_the_resume(fix, capsys):
    reading = {"passport": {"mrz": {"given_names": "RASHIDKHAN NAWAZKHAN", "surname": "MANSUR"}},
               "aadhaar": {"aadhaar": {"name": "RASHID KHAN"}}, "resume": {"name": "Rashid K"}}
    result = old_import(fix, folder="rashid khan-alnoor", names=["Passport.pdf", "Resume.pdf"], phone="9876500082", reading=reading)
    names = _file_names(fix, result["candidateId"])
    assert all(n.startswith("rashid_khan_alnoor-") for n in names.values())
    assert run("--candidate", result["careId"], "--apply") == 0
    with fix.sessions() as db:
        assert db.get(m.Candidate, result["candidateId"]).name == "Rashidkhan Nawazkhan Mansur"
    assert {n for (o, _, _), n in _file_names(fix, result["candidateId"]).items() if o == "candidate"} == {
        "Rashidkhan_Nawazkhan_Mansur-Passport.pdf", "Rashidkhan_Nawazkhan_Mansur-Resume.pdf"}


def test_name_order_is_passport_then_aadhaar_then_resume_and_unreadable_names_are_skipped():
    pick = lambda data: script.name_from_documents(SimpleNamespace(ocr_data=data))
    assert pick({"resume": {"name": "RESUME NAME"}, "aadhaar": {"aadhaar": {"name": "Aadhaar Name"}}}) == ("Aadhaar Name", "aadhaar")
    assert pick({"resume": {"name": "RAVI KUMAR"}}) == ("Ravi Kumar", "resume")
    assert pick({"passport": {"mrz": {}}, "resume": {"name": "Ravi Kumar"}}) == ("Ravi Kumar", "resume")
    assert pick({"additional_documents": [{"passport": {"mrz": {"given_names": "ARUN", "surname": "MENON"}}}]}) == ("Arun Menon", "passport")
    assert pick({"resume": {"name": "Name needs review"}}) == (None, None) and pick(None) == (None, None) and pick({}) == (None, None)


def test_a_candidate_whose_name_is_already_the_document_name_only_gets_the_file_names_in_line(fix, capsys):
    result = old_import(fix, phone="9876500083")
    cid = result["candidateId"]
    with fix.sessions() as db:
        db.get(m.Candidate, cid).name = "Gunavelan R"
        db.commit()
    assert run("--candidate", result["careId"], "--apply") == 0
    out = capsys.readouterr().out
    assert "name: 'Gunavelan R' (unchanged" in out
    assert not any(n.startswith("Gunavelan_alnoor-") for n in _file_names(fix, cid).values())      # the old slug is gone from the files
    assert all(n.startswith(NEW_PREFIX) for n in _file_names(fix, cid).values())


def test_a_name_typed_by_a_person_is_kept_and_the_files_take_it_unless_name_is_given(fix):
    result = old_import(fix, phone="9876500084")
    cid, care = result["candidateId"], result["careId"]
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        candidate.name, candidate.field_sources = "Gunavelan Ganesan", {NAME_KEY: "manual"}
        db.commit()
    assert run("--candidate", care, "--apply") == 0
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Gunavelan Ganesan" and candidate.field_sources == {NAME_KEY: "manual"}
    assert all(n.startswith("Gunavelan_Ganesan-") for n in _file_names(fix, cid).values())
    # --name replaces even a typed name, and stays "manual"
    assert run("--candidate", care, "--name", "  Gunavelan  Ganesan Pillai ", "--apply") == 0
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Gunavelan Ganesan Pillai" and candidate.field_sources[NAME_KEY] == "manual"
    assert all(n.startswith("Gunavelan_Ganesan_Pillai-") for n in _file_names(fix, cid).values())
    assert len(fixed_audits(fix, cid)) == 2


KEPT = "name kept: it looks like a name the office or the reader gave; use --name to change it"


def set_name_state(env, cid, name, sources):
    with env.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        candidate.name, candidate.field_sources = name, sources
        db.commit()


def test_a_name_that_came_with_the_import_sheet_is_kept_and_the_files_take_it_not_the_passport_name(fix, capsys):
    result = old_import(fix, phone="9876500101")        # the passport says "Gunavelan R"
    cid, care = result["candidateId"], result["careId"]
    set_name_state(fix, cid, "Rahul Kumar", {})         # imported WITH a sheet name: no field source, nothing provisional
    assert run("--candidate", care) == 0
    out = capsys.readouterr().out
    assert KEPT in out and "the passport document says 'Gunavelan R'" in out
    assert "'Rahul Kumar'  ->" not in out and "name: 'Rahul Kumar' (unchanged" in out
    assert run("--candidate", care, "--apply") == 0
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Rahul Kumar" and candidate.field_sources == {}          # name and sources untouched
    names = _file_names(fix, cid)
    assert all(n.startswith("Rahul_Kumar-") for n in names.values()) and names[("candidate", "resume", 1)] == "Rahul_Kumar-Resume.pdf"
    (entry,) = fixed_audits(fix, cid)
    assert entry.meta["old_name"] == entry.meta["new_name"] == "Rahul Kumar"
    assert run("--candidate", care, "--apply") == 0 and len(fixed_audits(fix, cid)) == 1      # and a second run changes nothing


def test_a_name_already_read_from_the_resume_is_not_replaced_by_the_passport_spelling_unless_it_is_provisional(fix, capsys):
    result = old_import(fix, phone="9876500102")
    cid, care = result["candidateId"], result["careId"]
    # Read from the resume (field source "extracted"): kept, even with a hyphen in it.
    set_name_state(fix, cid, "Abdul-Rahman", {NAME_KEY: "extracted"})
    assert run("--candidate", care, "--apply") == 0
    assert KEPT in capsys.readouterr().out
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Abdul-Rahman" and candidate.field_sources == {NAME_KEY: "extracted"}
    assert all(n.startswith("Abdul_Rahman-") for n in _file_names(fix, cid).values())
    # Marked provisional by the current import (a plain name, but only a stand-in): replaced by the passport name.
    set_name_state(fix, cid, "Gunavelan", {"_provisionalName": True})
    assert run("--candidate", care, "--apply") == 0
    out = capsys.readouterr().out
    assert KEPT not in out and "'Gunavelan'  ->  'Gunavelan R'" in out
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Gunavelan R" and candidate.field_sources[NAME_KEY] == "extracted"
    assert all(n.startswith("Gunavelan_R-") for n in _file_names(fix, cid).values())


@pytest.mark.parametrize("stand_in", ["Gunavelan-alnoor", "gunavelan alnoor", "Gunavelan_R2", "01 - Gunavelan", "GUNAVELAN ALNOOR"])
def test_a_folder_like_name_with_no_field_source_is_a_stand_in_and_is_replaced(fix, capsys, stand_in):
    result = old_import(fix, phone="9876500103")
    set_name_state(fix, result["candidateId"], stand_in, {})
    assert script.looks_like_folder_name(stand_in)
    assert run("--candidate", result["careId"], "--apply") == 0
    assert KEPT not in capsys.readouterr().out
    with fix.sessions() as db:
        assert db.get(m.Candidate, result["candidateId"]).name == "Gunavelan R"


def test_plain_names_do_not_look_like_a_folder_name():
    for plain in ("Rahul Kumar", "Mary Joseph", "Gunavelan R", "Rashidkhan Nawazkhan Mansur", ""):
        assert not script.looks_like_folder_name(plain), plain
    for folder in ("Gunavelan-alnoor", "a_b", "Ravi 2", "Ravi ALNOOR"):
        assert script.looks_like_folder_name(folder), folder


def test_name_replaces_a_kept_sheet_name_and_stays_manual(fix, capsys):
    result = old_import(fix, phone="9876500104")
    cid, care = result["candidateId"], result["careId"]
    set_name_state(fix, cid, "Rahul Kumar", {})
    assert run("--candidate", care, "--name", "Rahul K Singh", "--apply") == 0
    assert KEPT not in capsys.readouterr().out
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Rahul K Singh" and candidate.field_sources[NAME_KEY] == "manual"
    assert all(n.startswith("Rahul_K_Singh-") for n in _file_names(fix, cid).values())


def test_the_original_upload_kept_as_received_is_never_renamed(fix, capsys):
    result = old_import(fix, phone="9876500105")
    cid, care = result["candidateId"], result["careId"]
    with fix.sessions() as db:
        db.add(m.Attachment(id="bundle-1", entity_type="candidate", entity_id=cid, slot="source_bundle", version=1, file_name="Original scan (1).pdf",
                            size_kb=1, content_type="application/pdf", object_key="extra/bundle-1", content_hash="bundle-1", uploaded_by="admin"))
        db.commit()
    assert run("--candidate", care) == 0
    out = capsys.readouterr().out
    assert "Original scan (1).pdf  (left as it is: not an imported document)" in out
    assert run("--candidate", care, "--apply") == 0
    with fix.sessions() as db:
        assert db.get(m.Attachment, "bundle-1").file_name == "Original scan (1).pdf"        # the original name is the evidence
        (entry,) = fixed_audits(fix, cid)
        assert "bundle-1" not in entry.meta["old_file_names"] and "bundle-1" not in entry.meta["new_file_names"]
    assert all(n.startswith(NEW_PREFIX) for key, n in _file_names(fix, cid).items() if key[1] != "source_bundle")


def test_file_names_are_at_most_150_characters_and_keep_their_extension(fix, capsys):
    long_name = " ".join(["Abcdefghij"] * 20)                               # 219 characters, cut to 200 by the script
    result = old_import(fix, phone="9876500106")
    cid, care = result["candidateId"], result["careId"]
    assert run("--candidate", care, "--name", long_name, "--apply") == 0
    capsys.readouterr()
    with fix.sessions() as db:
        rows = list(db.scalars(select(m.Attachment).where(m.Attachment.id.in_(select(m.Attachment.id).where(
            (m.Attachment.entity_id == cid) | (m.Attachment.entity_id.in_(select(m.Application.id).where(m.Application.candidate_id == cid))))))))
        assert db.get(m.Candidate, cid).name == long_name[:200].strip()
    names = {row.object_key: row.file_name for row in rows}
    assert len(names) >= 8 and all(len(n) <= 150 and n.rsplit(".", 1)[-1] in {"pdf", "jpg"} for n in names.values()), names
    assert len({n.lower() for n in names.values()}) == len(names)           # still one name per stored file
    assert any(n.endswith("-2.pdf") or n.endswith("-3.pdf") for n in names.values())    # the counters survived the cut
    refs = _file_references(fix, cid)
    assert refs and all(ref["name"] == {row.id: row.file_name for row in rows}[ref["id"]] for ref in refs.values())
    before = state(fix, cid)
    assert run("--candidate", care, "--name", long_name, "--apply") == 0     # a second run changes no file name
    after = state(fix, cid)
    assert after[:3] == before[:3] and after[3] == before[3]                 # name, sources, file names, stage references


def test_fit_name_and_standard_name_never_cut_the_extension_or_repeat_a_name():
    assert script.fit_name("A" * 300 + ".pdf").endswith(".pdf") and len(script.fit_name("A" * 300 + ".pdf")) == 150
    assert script.fit_name("Gunavelan_R-Passport.pdf") == "Gunavelan_R-Passport.pdf"
    taken: set[str] = set()
    every_kind = list(script.bulk.KIND_LABELS)
    first = script.standard_name("Ravi", every_kind, "scan.PDF", taken)           # the labels alone are longer than 150 characters
    second = script.standard_name("Ravi", every_kind, "scan.PDF", taken)
    assert len(first) <= 150 and len(second) <= 150 and first != second and first.endswith(".pdf") and second.endswith(".pdf")
    long_person = "Ravi " * 60
    names = {script.standard_name(long_person, ["passport"], "p.pdf", taken) for _ in range(3)}
    assert len(names) == 3 and all(len(n) <= 150 and n.endswith(".pdf") for n in names)
    assert script.standard_name("Ravi Kumar", ["passport"], "p.pdf", set()) == "Ravi_Kumar-Passport.pdf"


def test_the_applications_and_stages_are_locked_before_their_file_references_are_rewritten(fix, capsys):
    """A recruiter saving a stage while the script runs must not be overwritten: the script waits for the row lock, then rewrites
    the freshly saved data (a lost update would drop the note saved in between)."""
    import threading
    import time

    result = old_import(fix, phone="9876500107")
    cid, care = result["candidateId"], result["careId"]
    with fix.sessions() as db:
        stage_id = db.scalar(text("SELECT id FROM crm_application_stages WHERE data::text LIKE '%Gunavelan_alnoor%' ORDER BY id LIMIT 1"))
    assert stage_id
    recruiter = fix.sessions()
    recruiter.execute(select(m.ApplicationStage).where(m.ApplicationStage.id == stage_id).with_for_update())      # their save has begun
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("code", run("--candidate", care, "--apply")))
    thread.start()
    try:
        waited = 0.0
        while waited < 20:
            with fix.sessions() as watcher:
                blocked = watcher.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"))
            if blocked:
                break
            time.sleep(0.2)
            waited += 0.2
        assert blocked, "the script did not wait for the stage row lock"
        assert thread.is_alive()
        recruiter.execute(text("UPDATE crm_application_stages SET data = data || CAST(:note AS jsonb) WHERE id = :id"),
                          {"note": '{"recruiterNote": "saved at that moment"}', "id": stage_id})
        recruiter.commit()
    finally:
        recruiter.rollback()
        recruiter.close()
        thread.join(60)
    assert not thread.is_alive() and outcome == {"code": 0}
    with fix.sessions() as db:
        stage = db.get(m.ApplicationStage, stage_id)
        assert stage.data["recruiterNote"] == "saved at that moment"                         # the recruiter's save survived
        assert "Gunavelan_alnoor" not in str(stage.data) and "Gunavelan_R-" in str(stage.data)   # and the names were still updated


def test_name_sets_the_name_of_one_candidate_whose_documents_have_no_readable_name(fix, capsys):
    result = old_import(fix, phone="9876500085", reading={"passport": {"mrz": {}}})
    cid, care = result["candidateId"], result["careId"]
    before = state(fix, cid)
    assert run("--candidate", care, "--apply") == 0                      # nothing readable and no --name: nothing at all changes
    out = capsys.readouterr().out
    assert "no name could be read" in out and "Use --name" in out and state(fix, cid) == before
    assert run("--candidate", care, "--name", "Arif Basha") == 0 and state(fix, cid) == before        # dry run with --name
    assert run("--candidate", care, "--name", "Arif Basha", "--apply") == 0
    with fix.sessions() as db:
        candidate = db.get(m.Candidate, cid)
        assert candidate.name == "Arif Basha" and candidate.field_sources[NAME_KEY] == "manual"
        assert "_provisionalName" not in candidate.field_sources
    assert all(n.startswith("Arif_Basha-") for n in _file_names(fix, cid).values())


def test_name_is_refused_for_several_candidates_and_when_empty(fix, capsys):
    a, b = old_import(fix, phone="9876500086"), old_import(fix, folder="Ravi-alnoor", names=["Passport.pdf"], phone="9876500087")
    before = state(fix, a["candidateId"]), state(fix, b["candidateId"])
    assert run("--candidate", a["careId"], b["careId"], "--name", "Someone", "--apply") == 2
    assert "only be used with one candidate" in capsys.readouterr().err
    assert run("--candidate", a["careId"], "--name", "   ", "--apply") == 2
    assert (state(fix, a["candidateId"]), state(fix, b["candidateId"])) == before


def test_a_candidate_who_was_not_bulk_imported_is_refused_and_untouched(fix, capsys):
    result = old_import(fix, phone="9876500088")
    cid = result["candidateId"]
    with fix.sessions() as db:
        db.get(m.Candidate, cid).source_detail = "Walk-in desk"
        db.commit()
    before = state(fix, cid)
    assert run("--candidate", result["careId"], "--apply") == 1
    assert "not a bulk-imported candidate" in capsys.readouterr().out
    assert run("--candidate", result["careId"], "--name", "Someone Else", "--apply") == 1
    assert state(fix, cid) == before


def test_an_unknown_candidate_is_reported_and_the_others_are_still_fixed(fix, capsys):
    result = old_import(fix, phone="9876500089")
    assert run("--candidate", "CI-9999", result["careId"].lower(), "--apply") == 1           # IDs are case-insensitive
    out = capsys.readouterr().out
    assert "CI-9999" in out and "not found" in out
    with fix.sessions() as db:
        assert db.get(m.Candidate, result["candidateId"]).name == "Gunavelan R"


def test_one_candidate_failing_does_not_stop_the_others_and_leaves_nothing_half_written(fix, monkeypatch, capsys):
    a = old_import(fix, phone="9876500090")
    b = old_import(fix, folder="Ravi-alnoor", names=["Passport.pdf", "Resume.pdf"], phone="9876500091",
                   reading={"passport": {"mrz": {"given_names": "RAVI", "surname": "KUMAR"}}})
    before_a = state(fix, a["candidateId"])
    real = script.plan_files

    def broken(db, candidate, name):
        if candidate.id == a["candidateId"]:
            candidate.name = "half written"          # something already changed in the session when it blows up
            raise RuntimeError("boom")
        return real(db, candidate, name)

    monkeypatch.setattr(script, "plan_files", broken)
    assert run("--candidate", a["careId"], b["careId"], "--apply") == 3
    captured = capsys.readouterr()
    assert "FAILED" in captured.err and a["careId"] in captured.err
    assert state(fix, a["candidateId"]) == before_a                                  # rolled back
    with fix.sessions() as db:
        assert db.get(m.Candidate, b["candidateId"]).name == "Ravi Kumar"
    assert all(n.startswith("Ravi_Kumar-") for n in _file_names(fix, b["candidateId"]).values())


def test_the_shared_database_is_refused_without_allow_remote(fix, monkeypatch, capsys):
    result = old_import(fix, phone="9876500092")
    cid = result["candidateId"]
    monkeypatch.setattr(script, "shared_database_reason", lambda: "this is the shared database.")
    before = state(fix, cid)
    assert run("--candidate", result["careId"], "--apply") == 2
    assert run("--candidate", result["careId"]) == 2                                  # not even a dry run reads it
    assert "Refused" in capsys.readouterr().err and state(fix, cid) == before
    assert run("--candidate", result["careId"], "--apply", "--allow-remote") == 0
    with fix.sessions() as db:
        assert db.get(m.Candidate, cid).name == "Gunavelan R"
