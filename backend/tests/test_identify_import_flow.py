"""The whole flow for a folder of mixed, generically named files: /identify -> /check (with the people found) -> /candidate per person.

Disposable PostgreSQL, real local reading of synthetic PDFs (embedded text layers, so no OCR is needed).
"""
import json

import pytest
from sqlalchemy import select

from app.db.models import Attachment, Candidate
from app.services.people_by_content import tail
from identify_fixtures import (
    aadhaar_number, aadhaar_text, garbled_aadhaar_text, offer_text, passport_text, pdf_of, resume_continuation, resume_text, unrelated_text,
)
from ocr_synthetic import page_lines
from test_bulk_candidate_import import env, stored  # noqa: F401  (the fixture and a helper of the screen's tests)
from test_database_naming import schema_database  # noqa: F401

THARUN = ("Tharun Kumar", "Raman", "K1234567")
MEERA = ("Meera", "Nair Menon", "P7654321")


def resume_page(name: str, phone: str) -> str:
    """A resume page the page classifier takes for a resume, headed by the name."""
    return "\n".join([name.upper(), f"Mobile: {phone}", ""] + page_lines(1)[4:])     # without the sample's own passport number and e-mail


def folder_files() -> dict[str, bytes]:
    """Two people, generic names; scan3.pdf is one scan whose two pages are two different people's."""
    return {
        "scan1.pdf": pdf_of(passport_text(*THARUN)),
        "scan2.pdf": pdf_of(aadhaar_text("Tharun Kumar Raman", 5)),
        "scan3.pdf": pdf_of(aadhaar_text("Meera Nair Menon", 9), resume_page("Tharun Kumar Raman", "9876543210")),
        "IMG_0001.pdf": pdf_of(passport_text(*MEERA)),
        "IMG_0002.pdf": pdf_of(resume_page("Meera Nair Menon", "9123456780")),
    }


def identify(env, files: dict[str, bytes], folder="Mixed"):
    spec = {"folder": folder, "files": [{"path": path} for path in files]}
    response = env.client.post("/api/candidate-import/identify", data={"spec": json.dumps(spec)},
                               files=[("files", (path, data, "application/pdf")) for path, data in files.items()])
    assert response.status_code == 200, response.text
    return response.json()


def test_identify_check_and_import_make_one_candidate_per_person_named_after_their_documents(env):
    files = folder_files()
    found = identify(env, files)
    assert sorted(p["name"] for p in found["people"]) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    totals = found["totals"]
    assert totals == {"files": 5, "pages": 6, "assigned": 6, "unplaced": 0, "unreadable": 0}
    mixed = {p["name"]: {f["path"]: f["pages"] for f in p["files"]} for p in found["people"]}
    assert mixed["Tharun Kumar Raman"]["scan3.pdf"] == [2] and mixed["Meera Nair Menon"]["scan3.pdf"] == [1]

    people = {"Mixed": [{"name": p["name"], "needsLook": p["needsLook"],
                         "files": [{"path": f["path"], **({"pages": f["pages"]} if f["pages"] else {})} for f in p["files"]]} for p in found["people"]]}
    body = {"folders": [{"folder": "Mixed", "files": [{"path": path} for path in files]}], "rows": [], "defaultOrder": None,
            "defaultRecruiterId": "staff", "jobs": {}, "groupMode": "auto", "grouping": {}, "assign": {}, "people": people}
    checked = env.client.post("/api/candidate-import/check", json=body)
    assert checked.status_code == 200, checked.text
    plans = checked.json()["plans"]
    assert sorted(plan["name"] for plan in plans) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    assert all(plan["identified"] and plan["parent"] == "Mixed" and not plan["needsLook"] and plan["action"] != "skip" for plan in plans)
    # nothing in the notes speaks of file NAMES any more: the people were found by reading the files
    for plan in plans:
        said = " | ".join(issue["message"] for issue in plan["issues"])
        assert "no clue in the name" not in said and "sorted by their names" not in said and "Documents not found" not in said, said
        assert "Found by reading the files" in said
    tharun_plan = next(plan for plan in plans if plan["name"] == "Tharun Kumar Raman")
    assert {f["path"]: f.get("pages") for f in tharun_plan["files"]}["scan3.pdf"] == [2]

    ids = {}
    for plan in plans:
        spec = {"folder": plan["folder"], "name": plan["name"], "row": {}, "defaultRecruiterId": "staff", "nameFromFiles": True,
                "files": [{"path": f["path"], **({"pages": f["pages"]} if f.get("pages") else {})} for f in plan["files"]]}
        response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)},
                                   files=[("files", (f["path"], files[f["path"]], "application/pdf")) for f in plan["files"]])
        assert response.status_code == 200, response.text
        assert response.json()["name"] == plan["name"] and response.json()["nameSource"] == "documents"
        ids[plan["name"]] = response.json()["candidateId"]

    with env.sessions() as db:
        candidates = {c.name: c for c in db.scalars(select(Candidate))}
        assert set(candidates) == {"Tharun Kumar Raman", "Meera Nair Menon"}
        # provisional until the online reading confirms it, and it says the name came from the documents
        assert all((c.field_sources or {}).get("_provisionalName") == "documents" for c in candidates.values())
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate")))
    slots = {name: {row.slot: row.file_name for row in rows if row.entity_id == ids[name]} for name in ids}
    assert slots["Tharun Kumar Raman"] == {"passport": "Tharun_Kumar_Raman-Passport.pdf", "aadhaar": "Tharun_Kumar_Raman-Aadhaar_card.pdf",
                                           "resume": "Tharun_Kumar_Raman-Resume.pdf"}
    assert slots["Meera Nair Menon"] == {"passport": "Meera_Nair_Menon-Passport.pdf", "aadhaar": "Meera_Nair_Menon-Aadhaar_card.pdf",
                                         "resume": "Meera_Nair_Menon-Resume.pdf"}
    # the shared scan was cut: each person's stored file holds only their own page
    import pymupdf as fitz
    from app.api.routers import files as files_router
    for name, person_slot in (("Tharun Kumar Raman", "resume"), ("Meera Nair Menon", "aadhaar")):
        row = next(r for r in rows if r.entity_id == ids[name] and r.slot == person_slot)
        key = row.object_key.rsplit("/", 1)[-1]
        with fitz.open(files_router.STORAGE / key[:2] / key) as stored_pdf:
            assert stored_pdf.page_count == 1
            text = stored_pdf[0].get_text().upper()
        assert ("THARUN" in text) == (name.startswith("Tharun")) and ("MEERA" in text) == (name.startswith("Meera"))
        audit = row.entity_id
    assert audit
    status = env.client.post("/api/candidate-import/status", json={"candidateIds": list(ids.values())}).json()["items"]
    assert {item["name"]: item["nameSource"] for item in status} == {"Tharun Kumar Raman": "documents", "Meera Nair Menon": "documents"}


def test_a_folder_sorted_by_names_still_says_so_and_its_name_is_read_from_the_passport_never_the_folder(env):
    files = {"scan1.pdf": pdf_of(passport_text(*THARUN)), "Ravi-resume.pdf": pdf_of(resume_page("Ravi Kumar", "9000011111"))}
    body = {"folders": [{"folder": "Mixed", "files": [{"path": path} for path in files]}], "rows": [], "defaultOrder": None,
            "defaultRecruiterId": "staff", "jobs": {}, "groupMode": "auto", "grouping": {}, "assign": {}, "people": {}}
    plan = env.client.post("/api/candidate-import/check", json=body).json()["plans"][0]
    said = " | ".join(issue["message"] for issue in plan["issues"])
    assert "no clue in the name" in said and "sorted by their names" in said and "Documents not found" in said and not plan["identified"]
    spec = {"folder": "Mixed", "name": "Mixed", "row": {}, "defaultRecruiterId": "staff", "files": [{"path": "scan1.pdf"}]}
    done = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=[("files", ("scan1.pdf", files["scan1.pdf"], "application/pdf"))])
    assert done.status_code == 200, done.text
    item = env.client.post("/api/candidate-import/status", json={"candidateIds": [done.json()["candidateId"]]}).json()["items"][0]
    assert item["nameSource"] == "documents" and item["name"] != "Mixed" and "mixed" not in item["name"].lower()     # read from the passport scan
    # a name the sheet gives is never a "files" name, even when the screen says the person was found by reading
    other = {**spec, "folder": "Mixed 2", "name": "Mixed 2", "nameFromFiles": True, "row": {"name": "Given Name", "phone": "9000022222"}}
    again = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(other)}, files=[("files", ("scan1.pdf", files["scan1.pdf"] + b" ", "application/pdf"))])
    assert again.status_code == 200, again.text
    assert env.client.post("/api/candidate-import/status", json={"candidateIds": [again.json()["candidateId"]]}).json()["items"][0]["nameSource"] == "sheet"


def test_identify_limits_and_lists_every_file(env):
    many = {f"f{i:03}.pdf": b"%PDF-1.4" for i in range(201)}
    spec = {"folder": "Big", "files": [{"path": path} for path in many]}
    refused = env.client.post("/api/candidate-import/identify", data={"spec": json.dumps(spec)},
                              files=[("files", (p, d, "application/pdf")) for p, d in many.items()])
    assert refused.status_code == 422                                  # more than 200 files: split the folder
    files = {"ok.pdf": pdf_of(passport_text(*THARUN)), "broken.pdf": b"%PDF-1.4 not really", "notes.txt": b"hello", "empty.pdf": b""}
    spec = {"folder": "Odd", "files": [{"path": path} for path in files]}
    response = env.client.post("/api/candidate-import/identify", data={"spec": json.dumps(spec)},
                               files=[("files", (p, d, "application/pdf")) for p, d in files.items()])
    assert response.status_code == 200
    found = response.json()
    assert [p["name"] for p in found["people"]] == ["Tharun Kumar Raman"]
    assert {row["path"] for row in found["unreadable"]} == {"broken.pdf", "notes.txt", "empty.pdf"}       # never dropped
    t = found["totals"]
    assert t["assigned"] + t["unplaced"] + t["unreadable"] == t["pages"] and t["files"] == 4


def test_a_person_whose_documents_disagree_is_flagged_and_a_folder_forced_to_one_candidate_ignores_the_people(env):
    files = {"a.pdf": pdf_of(passport_text("Mohamed Ali", "Khan", "K1234567")), "b.pdf": pdf_of(passport_text("Mohamed Ali", "Khan", "P7654321"))}
    found = identify(env, files, "Pair")
    assert len(found["people"]) == 2 and all(p["needsLook"] for p in found["people"])
    people = {"Pair": [{"name": p["name"], "needsLook": p["needsLook"], "files": [{"path": f["path"]} for f in p["files"]]} for p in found["people"]]}
    body = {"folders": [{"folder": "Pair", "files": [{"path": path} for path in files]}], "rows": [], "defaultOrder": None, "defaultRecruiterId": "staff",
            "jobs": {}, "groupMode": "auto", "grouping": {}, "assign": {}, "people": people}
    plans = env.client.post("/api/candidate-import/check", json=body).json()["plans"]
    assert len(plans) == 2 and all(plan["needsLook"] for plan in plans)         # the screen un-ticks them until a person looks
    assert any("disagree" in issue["message"] for plan in plans for issue in plan["issues"])
    forced = env.client.post("/api/candidate-import/check", json={**body, "grouping": {"Pair": "one"}}).json()["plans"]
    assert len(forced) == 1 and forced[0]["folder"] == "Pair"


def test_every_file_of_a_slot_stored_together_is_read_but_not_a_version_replaced_later(env):
    from sqlalchemy import update
    from app.db.models import DocumentExtractionJob
    from test_bulk_candidate_import import _read_again, send
    result = send(env, "Anil", ["Anil-passport data page.pdf", "Anil-passport back page.pdf"], {"phone": "9876500777"})
    candidate_id = result["candidateId"]
    assert _read_again(env, [candidate_id]) == 2                       # both files of the passport slot, not only the last stored
    with env.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)))
        assert len(jobs) == 2 and len({job.object_key for job in jobs}) == 2 and {job.kind for job in jobs} == {"passport"}
    assert _read_again(env, [candidate_id]) == 0
    second = send(env, "Bina", ["Bina-passport data page.pdf", "Bina-passport back page.pdf"], {"phone": "9876500778"})
    with env.sessions() as db:       # the older version was replaced by a separate, later upload: it is not read
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_id == second["candidateId"], Attachment.slot == "passport")))
        older = min(rows, key=lambda row: row.version)
        db.execute(update(Attachment).where(Attachment.id == older.id).values(uploaded_at=older.uploaded_at.replace(year=older.uploaded_at.year - 1)))
        db.commit()
    assert _read_again(env, [second["candidateId"]]) == 1


def check_people(env, folder: str, files: dict, found: dict) -> list[dict]:
    """/check with the people exactly as the screen sends them back (name, needsLook, warnings, files)."""
    people = {folder: [{"name": p["name"], "needsLook": p["needsLook"], "warnings": p["warnings"],
                        "files": [{"path": f["path"], **({"pages": f["pages"]} if f["pages"] else {})} for f in p["files"]]} for p in found["people"]]}
    body = {"folders": [{"folder": folder, "files": [{"path": path} for path in files]}], "rows": [], "defaultOrder": None,
            "defaultRecruiterId": "staff", "jobs": {}, "groupMode": "auto", "grouping": {}, "assign": {}, "people": people}
    checked = env.client.post("/api/candidate-import/check", json=body)
    assert checked.status_code == 200, checked.text
    return checked.json()["plans"]


def test_a_bilingual_aadhaar_with_a_garbled_name_makes_no_junk_candidate_and_what_was_placed_by_name_is_flagged(env):
    guna = ("Gunavelan", "Ramakrishnan", "V2345678")
    files = {"Gunavelan-PP.pdf": pdf_of(passport_text(*guna)), "Gunavelan-aadhar.pdf": pdf_of(garbled_aadhaar_text(7)),
             "Gunavelan-signed offer.pdf": pdf_of(offer_text("Gunavellan"))}
    found = identify(env, files, "Guna")
    assert [p["name"] for p in found["people"]] == ["Gunavelan Ramakrishnan"] and found["unplaced"] == []
    assert found["totals"] == {"files": 3, "pages": 3, "assigned": 3, "unplaced": 0, "unreadable": 0}
    assert "aaharapef" not in json.dumps(found).lower()
    warning = next(w for w in found["people"][0]["warnings"] if w["kind"] == "placed_by_file_name")
    assert warning["message"] == "2 pages were placed by their file name: check."
    plans = check_people(env, "Guna", files, found)
    assert [plan["name"] for plan in plans] == ["Gunavelan Ramakrishnan"] and not plans[0]["needsLook"] and plans[0]["leftOut"] == []
    assert {f["path"] for f in plans[0]["files"]} == set(files)
    assert any(issue["message"] == "2 pages were placed by their file name: check." and issue["level"] == "warning" for issue in plans[0]["issues"])

    # names that say nothing: the card waits (listed with its ending), the letter is placed as probable, and the person says so
    neutral = dict(zip(["scan1.pdf", "scan2.pdf", "IMG_0003.pdf"], files.values()))
    again = identify(env, neutral, "Guna2")
    assert [p["name"] for p in again["people"]] == ["Gunavelan Ramakrishnan"]
    assert [(row["path"], row["kind"], row["ending"]) for row in again["unplaced"]] == [("scan2.pdf", "aadhaar_no_name", tail(aadhaar_number(7)))]
    assert any(w["kind"] == "placed_by_first_name" for w in again["people"][0]["warnings"])
    plans = check_people(env, "Guna2", neutral, again)
    assert plans[0]["leftOut"] == ["scan2.pdf"] and any("by a first name only" in issue["message"] for issue in plans[0]["issues"])


def test_a_card_whose_name_is_junk_and_that_is_all_there_is_is_one_flagged_candidate_named_by_its_number(env):
    files = {"scan1.pdf": pdf_of(garbled_aadhaar_text(5))}
    found = identify(env, files, "Lone")
    name = f"Unnamed candidate (Aadhaar ending {aadhaar_number(5)[-3:]})"
    assert [p["name"] for p in found["people"]] == [name] and found["people"][0]["needsLook"] and found["unplaced"] == []
    plans = check_people(env, "Lone", files, found)
    assert [plan["name"] for plan in plans] == [name] and plans[0]["needsLook"]
