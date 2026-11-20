"""A bulk-import folder that holds a person's standalone resume AND a mixed packet containing the same resume pages.

Only NEW eligible pages are sent to the (fake, paid) reader: the packet's resume pages that repeat the standalone resume are not
read a second time, while its passport, Aadhaar and education pages are read / filed once each. Each person's pages stay their own:
two people may have look-alike pages, but nothing is ever matched across candidates. The packet stays downloadable byte for byte.

Synthetic documents (text layer, no OCR program) and a fake Veris: no network, no key, no real database (disposable PostgreSQL).
"""
import hashlib
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from sqlalchemy import func, select

from app.db.models import Attachment, DocumentExtractionJob, User
from app.integrations.ocr import inspection
from app.services import document_extraction, manual_extraction
from app.services.application_form import build_form
from app.services.document_reading import packet_sources
from test_bulk_candidate_import import _jobs, _read_again, _send_files, env, import_only_stores  # noqa: F401  (fixtures and helpers)
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_document_extraction import pdf
from test_kind_classifier import MEDICAL, SSLC
from test_ocr_classification import AADHAAR, CONTINUATION, CV, PASSPORT
from test_veris_handoff import FakeVeris


def person_pages(name: str, email: str) -> dict[str, str]:
    """The pages of one synthetic person. Two people's pages share their layout (look-alikes) and differ in the person's own details."""
    stamp = "".join(name.split()).upper()
    number = int(hashlib.sha256(stamp.encode()).hexdigest()[:6], 16) % 9000000 + 1000000
    return {
        "resume_1": CV.replace("Alex Example", name).replace("alex@example.test", email),
        "resume_2": CONTINUATION + f"Reference file for {name} kept by the recruiter {stamp}.\n",
        "passport": PASSPORT.replace("P1234567", f"P{number}").replace("ALEX", name.split()[0].upper()),
        "aadhaar": AADHAAR.replace("Alex Example", name),
        "education": SSLC.replace("TESTER SAMPLE", name.upper()),
        "medical": MEDICAL.replace("TESTER SAMPLE", name.upper()),
    }


ALEX = person_pages("Alex Example", "alex@example.test")
SAM = person_pages("Sam Roberts", "sam.roberts@example.test")
ORDER = ("resume_1", "resume_2", "passport", "aadhaar", "education", "medical")


def packet_of(person: dict[str, str]) -> bytes:
    return pdf(*[person[key] for key in ORDER])


def resume_of(person: dict[str, str]) -> bytes:
    return pdf(person["resume_1"], person["resume_2"])


def folder_for(person: dict[str, str]) -> dict:
    return {"Resume.pdf": (resume_of(person), "application/pdf"), "scan 001.pdf": (packet_of(person), "application/pdf")}


@pytest.fixture
def veris(env, monkeypatch):
    """The online reader, faked and recording every submission (mode and the text of each page sent)."""
    settings = SimpleNamespace(
        storage_dir=str(env.storage), storage_backend="local", tesseract_cmd=None, ocr_languages="eng", ocr_max_pages=300,
        ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600, veris_ocr_base_url="https://ocr.invalid",
        veris_ocr_api_key="synthetic-key", ocr_provider_wait_seconds=0.0, veris_parallel_jobs=1, veris_chunk_pages=20,
        veris_chunk_overlap_pages=1)
    fake = FakeVeris()

    class Provider:
        def __init__(self, **options):
            self.jobs = fake

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(document_extraction, "VerisOCR", Provider)
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)

    def read_all():
        with env.sessions() as db:
            ids = list(db.scalars(select(DocumentExtractionJob.id).where(DocumentExtractionJob.status.in_(manual_extraction.READY))
                                  .order_by(DocumentExtractionJob.created_at)))
        for identifier in ids:
            manual_extraction.process_job(identifier, sessions=env.sessions, settings=settings)
        return ids

    fake.read_all = read_all
    return fake


# ------------------------------------------------------------------------------------------------------------ helpers

MARKS = (("CURRICULUM VITAE", "resume_1"), ("JOB RESPONSIBILITIES", "resume_2"), ("PASSPORT", "passport"), ("AADHAAR", "aadhaar"),
         ("Karnataka", "education"), ("Wafid", "medical"))


def label(text: str) -> str:
    """"alex:resume_1" for a page: the person is the first name found on it, the page is told by its heading."""
    person = next((name for name in ("alex", "sam") if name in text.lower()), "?")
    return person + ":" + next((kind for mark, kind in MARKS if mark in text), "?")


def sent(veris) -> dict[str, list[tuple[str, ...]]]:
    """What reached the reader, by mode: one tuple of page labels per submission, in a stable order."""
    found: dict[str, list[tuple[str, ...]]] = {}
    for item in veris.submissions:
        found.setdefault(item["mode"], []).append(tuple(label(text) for text in item["texts"]))
    return {mode: sorted(items) for mode, items in found.items()}


def sent_texts(veris, mode: str) -> list[str]:
    return [text for item in veris.submissions if item["mode"] == mode for text in item["texts"]]


def live_rows(env, candidate):
    with env.sessions() as db:
        return list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate,
                                                        Attachment.deleted_at.is_(None)).order_by(Attachment.slot, Attachment.version)))


def filed(env, candidate) -> dict[str, list[tuple[str, ...]]]:
    """slot -> the pages each live file of the candidate holds (labels)."""
    found: dict[str, list[tuple[str, ...]]] = {}
    for row in live_rows(env, candidate):
        with fitz.open(env.storage / row.object_key) as document:
            found.setdefault(row.slot, []).append(tuple(label(page.get_text()) for page in document))
    return found


def people_in(held) -> set[str]:
    return {page.split(":")[0] for items in held.values() for item in items for page in item}


def imported(env, folder: str, files: dict, phone: str, **options) -> str:
    result = _send_files(env, folder, files, row={"phone": phone}, **options)
    assert result["status"] == "imported", result
    return result["candidateId"]


def original_of(env, candidate: str):
    """The retained packet as the folder's file listing shows it (the candidate form's original files)."""
    with env.sessions() as db:
        listed = build_form(db, candidate, db.get(User, "admin"))["originalFiles"]
        return [(file["fileName"], db.get(Attachment, file["attachmentId"])) for file in listed]


FOLDER_ONE = {"resume": [("alex:education",), ("alex:resume_1", "alex:resume_2")], "passport": [("alex:passport",)],
              "aadhaar": [("alex:aadhaar",)]}


# ---------------------------------------------------------------------------------------------- (a) one person, two files

@pytest.mark.parametrize("packet_first", [False, True])
def test_a_standalone_resume_and_the_same_pages_in_a_packet_read_the_resume_once_and_every_other_page_once(env, veris, packet_first):
    files = folder_for(ALEX)
    if packet_first:
        files = dict(reversed(list(files.items())))
    candidate = imported(env, "Alex Example", files, "9876500101")

    assert _read_again(env, [candidate]) == 4          # the resume, the passport, the Aadhaar card and the education certificate
    veris.read_all()
    # The resume pages once; the education page by itself (read in resume mode); the passport and Aadhaar once each.
    assert sent(veris) == FOLDER_ONE

    held = filed(env, candidate)
    assert held["resume"] == [("alex:resume_1", "alex:resume_2")]                      # one resume document: the standalone file
    assert held["passport"] == [("alex:passport",)] and held["aadhaar"] == [("alex:aadhaar",)]
    assert held["education_certificate"] == [("alex:education",)] and held["medical"] == [("alex:medical",)]
    assert {row.version for row in live_rows(env, candidate) if row.slot == "resume"} == {1}
    with env.sessions() as db:
        assert packet_sources(db, [candidate]) == {}       # a packet with a repeated page is never read as one whole file

    # nothing more is read, whoever asks again
    assert _read_again(env, [candidate]) == 0
    veris.read_all()
    assert len(veris.submissions) == 4
    assert filed(env, candidate)["resume"] == [("alex:resume_1", "alex:resume_2")]

    # the packet stays downloadable byte for byte and the folder's file listing still shows it
    (name, original), = [item for item in original_of(env, candidate) if "scan" in item[0]]
    assert name.startswith("scan") and (env.storage / original.object_key).read_bytes() == files["scan 001.pdf"][0]


# ------------------------------------------------------------------------------------------------ (b) people stay apart

def test_two_people_with_look_alike_files_are_each_read_in_full_and_never_matched_to_each_other(env, veris):
    alex = imported(env, "Alex Example", folder_for(ALEX), "9876500101")
    sam = imported(env, "Sam Roberts", folder_for(SAM), "9876500102")
    assert _read_again(env, [alex, sam]) == 8
    veris.read_all()

    # every page of both people reached the reader exactly once: look-alike resumes of two people are two resumes
    assert sent(veris) == {
        "resume": [("alex:education",), ("alex:resume_1", "alex:resume_2"), ("sam:education",), ("sam:resume_1", "sam:resume_2")],
        "passport": [("alex:passport",), ("sam:passport",)], "aadhaar": [("alex:aadhaar",), ("sam:aadhaar",)]}
    for candidate, name in ((alex, "alex"), (sam, "sam")):
        held = filed(env, candidate)
        assert people_in(held) == {name}                                                 # nothing of the other person
        assert held == {"resume": [(f"{name}:resume_1", f"{name}:resume_2")], "passport": [(f"{name}:passport",)],
                        "aadhaar": [(f"{name}:aadhaar",)], "education_certificate": [(f"{name}:education",)],
                        "medical": [(f"{name}:medical",)], "source_bundle": [tuple(f"{name}:{key}" for key in ORDER)]}


def test_a_packet_shared_by_two_people_gives_each_only_their_own_pages(env, veris):
    shared = pdf(ALEX["resume_1"], ALEX["resume_2"], ALEX["passport"],
                 SAM["resume_1"], SAM["resume_2"], SAM["aadhaar"], SAM["education"])
    alex = imported(env, "Alex Example", {"Resume.pdf": (resume_of(ALEX), "application/pdf"), "scan 001.pdf": (shared, "application/pdf")},
                    "9876500101", pages={"scan 001.pdf": [1, 2, 3]})
    sam = imported(env, "Sam Roberts", {"Resume.pdf": (resume_of(SAM), "application/pdf"), "scan 001.pdf": (shared, "application/pdf")},
                   "9876500102", pages={"scan 001.pdf": [4, 5, 6, 7]})
    _read_again(env, [alex, sam])
    veris.read_all()

    assert sent(veris) == {
        "resume": [("alex:resume_1", "alex:resume_2"), ("sam:education",), ("sam:resume_1", "sam:resume_2")],
        "passport": [("alex:passport",)], "aadhaar": [("sam:aadhaar",)]}
    held = {"alex": filed(env, alex), "sam": filed(env, sam)}
    assert people_in(held["alex"]) == {"alex"} and people_in(held["sam"]) == {"sam"}
    for name, kinds in (("alex", {"resume": 1, "passport": 1}), ("sam", {"resume": 1, "aadhaar": 1, "education_certificate": 1})):
        assert {slot: len(items) for slot, items in held[name].items() if slot != "source_bundle"} == kinds, name
    assert held["alex"]["resume"] == [("alex:resume_1", "alex:resume_2")] and held["sam"]["resume"] == [("sam:resume_1", "sam:resume_2")]


# --------------------------------------------------------------------------------------------- (c) an updated resume is new

UPDATED = {
    "resume_1": ("CURRICULUM VITAE\nAlex Example\nalex@example.test\nProfile updated on 14/03/2026\nWork Experience\n"
                 "Senior pipe fitter, Gulf Offshore Contractors, 03/2019 to 04/2026, fabrication of high pressure piping\n"
                 "Education\nDiploma in Mechanical Engineering and a certificate in advanced welding\nTechnical Skills\n"
                 "TIG and MIG welding, hydrostatic testing, isometric drawing, quality inspection\n"),
    "resume_2": ("JOB RESPONSIBILITIES\nSenior pipe fitter, Gulf Offshore Contractors, 2019 - 2026\n"
                 "Led a crew of twelve, planned shutdown work and trained apprentices in safe lifting and hot work permits.\n"
                 "Reported weekly progress to the project manager and closed every inspection finding within a week on site.\n"),
}


@pytest.mark.parametrize("changed", [("resume_1",), ("resume_1", "resume_2")])
def test_a_packet_resume_page_that_differs_from_the_standalone_resume_is_new_and_is_read(env, veris, changed):
    newer = {**ALEX, **{key: UPDATED[key] for key in changed}}
    files = {"Resume.pdf": (resume_of(ALEX), "application/pdf"), "scan 001.pdf": (packet_of(newer), "application/pdf")}
    candidate = imported(env, "Alex Example", files, "9876500101")
    _read_again(env, [candidate])
    veris.read_all()

    modes = sent(veris)
    assert modes["passport"] == [("alex:passport",)] and modes["aadhaar"] == [("alex:aadhaar",)]      # still once each
    texts = sent_texts(veris, "resume")
    assert sum("Gulf Offshore" in text for text in texts) == len(changed)             # each new resume page was sent, once
    assert sum("Installed and maintained" in text for text in texts) == 1             # the old first page: once (the standalone file)
    assert sum("Reference file for" in text for text in texts) == 1                    # the old continuation: once, never again
    assert sum("Karnataka" in text for text in texts) == 1                             # the education page: once
    assert len(texts) == 3 + len(changed)                                              # the two old pages, the new ones, the education page


# ------------------------------------------------------------------------------------------------ (d) running it again

def _snapshot(env, candidate):
    with env.sessions() as db:
        jobs = db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate))
    return jobs, [(row.slot, row.version, row.file_name, row.content_hash) for row in live_rows(env, candidate)]


@pytest.mark.parametrize("action", ["skip", "add_documents"])
@pytest.mark.parametrize("read_first", [False, True])
def test_running_the_same_import_again_files_nothing_new_and_makes_no_new_paid_read(env, veris, action, read_first):
    files = folder_for(ALEX)
    candidate = imported(env, "Alex Example", files, "9876500101")
    if read_first:
        _read_again(env, [candidate])
        veris.read_all()
    before = _snapshot(env, candidate)

    for _ in range(2):
        again = _send_files(env, "Alex Example", files, row={"phone": "9876500101"}, duplicate_action=action)
        assert again["candidateId"] == candidate
        assert again["status"] == ("duplicate" if action == "skip" else "documents_added"), again
        assert _snapshot(env, candidate) == before                                       # no new file, version or reading job
        if action == "add_documents":
            assert not any(item["added"] for item in again["documents"]), again["documents"]

    assert _read_again(env, [candidate]) == (0 if read_first else 4)                       # nothing extra: at most the first import's own reads
    veris.read_all()
    assert sent(veris) == FOLDER_ONE
    assert _read_again(env, [candidate]) == 0
    veris.read_all()
    assert len(veris.submissions) == 4


def test_adding_the_folder_to_an_existing_candidate_reads_the_resume_once_and_the_other_pages_once(env, veris):
    candidate = imported(env, "Alex Example", {"Medical.pdf": (pdf(ALEX["medical"]), "application/pdf")}, "9876500101")
    added = _send_files(env, "Alex Example", folder_for(ALEX), row={"phone": "9876500101"}, duplicate_action="add_documents")
    assert added["status"] == "documents_added" and added["candidateId"] == candidate
    assert _read_again(env, [candidate]) == 4
    veris.read_all()
    assert sent(veris) == FOLDER_ONE
    held = filed(env, candidate)
    assert held["resume"] == [("alex:resume_1", "alex:resume_2")] and held["passport"] == [("alex:passport",)]
    assert held["aadhaar"] == [("alex:aadhaar",)] and held["education_certificate"] == [("alex:education",)]
    assert _read_again(env, [candidate]) == 0
