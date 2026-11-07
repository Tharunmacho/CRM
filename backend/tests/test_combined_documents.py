"""One file holding a résumé, a passport and an Aadhaar card, in any order and any combination.

Every page is classified whatever the upload was called; each kind found is sent to its own Veris mode as a separate
job. Documents here are synthetic composites (tests/ocr_synthetic.py) and Veris is a fake: no network, no key.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.integrations.ocr import inspection
from app.services import document_extraction as subject
from ocr_synthetic import composite_pdf
from test_veris_handoff import FakeVeris

needs_tesseract = pytest.mark.skipif(inspection.find_tesseract(None) is None, reason="Tesseract is not installed")
FORMS = ["native", pytest.param("scan", marks=needs_tesseract)]


@pytest.fixture
def combined(monkeypatch):
    settings = SimpleNamespace(
        tesseract_cmd=None, ocr_languages="eng", ocr_max_pages=300, ocr_page_timeout_seconds=20,
        ocr_document_timeout_seconds=600, veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key",
        ocr_provider_wait_seconds=0.0, veris_parallel_jobs=4, veris_chunk_pages=20, veris_chunk_overlap_pages=1,
    )
    veris = FakeVeris()
    state: dict = {}
    saved: list[dict] = []

    class Provider:
        def __init__(self, **options):
            self.jobs = veris

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(subject, "VerisOCR", Provider)

    def extract(data, **options):
        return subject.extract_attachment(
            data, state=state, save=lambda s: saved.append(deepcopy(s)), settings=settings, designations=["Welder"], **options)

    def plan():
        """kind -> original page numbers sent to that Veris mode (page numbers only)."""
        plan: dict[str, list[int]] = {}
        for key, job in state.get("provider_jobs", {}).items():  # "passport+" holds pages found after the first dispatch
            plan[key.rstrip("+")] = sorted({*plan.get(key.rstrip("+"), []), *job["selected_pages"]})
        return plan

    return SimpleNamespace(extract=extract, veris=veris, state=state, saved=saved, plan=plan, settings=settings)


@pytest.mark.parametrize("form", FORMS)
def test_one_file_with_all_three_documents_in_shuffled_order_makes_three_separate_submissions(combined, form):
    pages = ["aadhaar", "resume", "resume", "passport", "resume", "aadhaar_back"]

    output = combined.extract(composite_pdf(pages, form=form))

    assert sorted(s["mode"] for s in combined.veris.submissions) == ["aadhaar", "passport", "resume"]
    assert combined.plan() == {"resume": [2, 3, 5], "passport": [4], "aadhaar": [1, 6]}
    assert set(output["extractions"]) == {"resume", "passport", "aadhaar"}
    assert output["status"] == "complete" and not output["inspection"]["unknown_pages"]
    sent = {s["mode"]: s for s in combined.veris.submissions}
    assert len(sent["resume"]["texts"]) == 3 and len(sent["passport"]["texts"]) == 1 and len(sent["aadhaar"]["texts"]) == 2
    if form == "native":
        assert all("CURRICULUM VITAE" in t for t in sent["resume"]["texts"])
        assert "REPUBLIC OF INDIA" in sent["passport"]["texts"][0]
        assert all("Government of India" in t or "Unique Identification" in t for t in sent["aadhaar"]["texts"])
    assert output["coverage"]["pages"]["sent"] == [1, 2, 3, 4, 5, 6] and output["coverage"]["complete"]


@pytest.mark.parametrize("form", FORMS)
def test_the_name_of_the_upload_slot_does_not_limit_what_is_looked_for(combined, form):
    # extract_attachment takes no slot at all: a file uploaded "as the résumé" is searched for all three kinds.
    output = combined.extract(composite_pdf(["resume", "passport", "aadhaar"], form=form))
    assert set(output["extractions"]) == {"resume", "passport", "aadhaar"}


@pytest.mark.parametrize("form", FORMS)
def test_a_file_with_only_a_passport(combined, form):
    output = combined.extract(composite_pdf(["passport"], form=form))
    assert combined.plan() == {"passport": [1]} and set(output["extractions"]) == {"passport"}
    assert output["status"] == "complete"


@pytest.mark.parametrize("form", FORMS)
def test_a_file_with_only_an_aadhaar_card_front_and_back(combined, form):
    output = combined.extract(composite_pdf(["aadhaar", "aadhaar_back"], form=form))
    assert combined.plan() == {"aadhaar": [1, 2]} and set(output["extractions"]) == {"aadhaar"}


@pytest.mark.parametrize("form", FORMS)
def test_a_passport_and_an_aadhaar_card_on_one_sheet_go_to_both_modes(combined, form):
    output = combined.extract(composite_pdf(["resume", "passport+aadhaar"], form=form))
    assert combined.plan() == {"resume": [1], "passport": [2], "aadhaar": [2]}
    assert set(output["extractions"]) == {"resume", "passport", "aadhaar"}


@pytest.mark.parametrize("form", FORMS)
def test_pages_of_other_documents_are_ignored_but_recorded(combined, form):
    output = combined.extract(composite_pdf(["certificate", "resume", "passport", "certificate"], form=form))
    assert combined.plan() == {"resume": [2], "passport": [3]}
    not_sent = {p["page"]: p for p in output["coverage"]["pages"]["not_sent"]}
    assert set(not_sent) == {1, 4} and not any(p["blocking"] for p in not_sent.values())
    assert output["status"] == "complete"


@pytest.mark.parametrize("form", FORMS)
def test_an_unrelated_page_never_blocks_the_documents_that_are_there(combined, form):
    output = combined.extract(composite_pdf(["resume", "passport", "unrelated", "aadhaar"], form=form))
    assert combined.plan() == {"resume": [1], "passport": [2], "aadhaar": [4]}
    assert set(output["extractions"]) == {"resume", "passport", "aadhaar"}
    assert output["status"] == "needs_review"  # page 3 is named for a person to check ...
    assert {p["page"]: p["blocking"] for p in output["coverage"]["pages"]["not_sent"]} == {3: True}
    assert any("Pages 3" in w for w in output["warnings"])
    # ... but it was not sent anywhere: nothing about it looked like an identity document.
    assert combined.veris.peak <= 3


def test_a_full_page_of_text_straight_after_a_resume_is_read_as_its_continuation(combined):
    output = combined.extract(composite_pdf(["resume", "unrelated"], form="native"))
    assert combined.plan() == {"resume": [1, 2]}
    assert output["inspection"]["inherited_pages"] == [2]


def test_a_page_with_a_resume_and_a_passport_copy_goes_to_both(combined):
    output = combined.extract(composite_pdf(["resume+passport", "resume"], form="native"))
    assert combined.plan() == {"resume": [1, 2], "passport": [1]}
    assert set(output["extractions"]) == {"resume", "passport"}


def test_each_kind_is_visible_as_soon_as_it_has_been_read(combined):
    """The first kind to finish is saved with its mapped fields while the others are still being read."""
    combined.veris.script[("passport", 0)] = lambda polls: "running" if polls < 2 else "succeeded"
    combined.settings.ocr_provider_wait_seconds = 5.0
    combined.settings.veris_early_dispatch = False  # (with it, the kinds are already in before the final pass)

    output = combined.extract(composite_pdf(["resume", "passport", "aadhaar"], form="native"))

    assert output["status"] == "complete"
    partial = [s for s in combined.saved if s.get("partial_extractions")]
    assert partial, "no snapshot showed a kind that was done while another was still running"
    first = partial[0]["partial_extractions"]
    assert "passport" not in first and set(first) & {"resume", "aadhaar"}
    assert all("fields" in item and item["selected_pages"] for item in first.values())
    assert "partial_extractions" not in combined.saved[-1]


def test_unresolved_pages_beside_a_passport_are_sent_to_the_passport_mode_as_candidates(combined, monkeypatch):
    """A badly read page next to a passport might be its address page: Veris decides, the page is accounted for."""
    from app.integrations.ocr import classification

    data = composite_pdf(["resume", "passport", "unrelated", "aadhaar"], form="native")
    # The page beside the passport mentions the passport office, which is a hint (not a classification).
    monkeypatch.setattr(classification, "_IDENTITY_HINT", classification.re.compile(r"garden committee", classification.re.I))

    output = combined.extract(data)

    assert combined.plan()["passport"] == [2, 3] and combined.plan()["aadhaar"] == [3, 4]
    assert output["coverage"]["pages"]["probed"] == {"passport": [3], "aadhaar": [3]}
    assert output["extractions"]["passport"]["selected_pages"] == [2]  # the identified page; the candidate is read along
    assert output["extractions"]["passport"]["source"]["selected_pages"] == [2, 3]
    assert {p["page"] for p in output["coverage"]["pages"]["not_sent"]} == set()


def test_a_kind_without_pages_is_probed_with_the_unresolved_pages_and_dropped_when_nothing_is_found(combined, monkeypatch):
    from app.integrations.ocr import classification

    monkeypatch.setattr(classification, "_IDENTITY_HINT", classification.re.compile(r"garden committee", classification.re.I))
    data = composite_pdf(["resume", "certificate", "unrelated"], form="native")
    combined.veris.build = lambda mode, number, texts: {"mrz": {}, "fields": []} if mode == "passport" else {"aadhaar": {}}

    output = combined.extract(data)

    assert combined.plan() == {"resume": [1], "passport": [3], "aadhaar": [3]}
    assert set(output["extractions"]) == {"resume"}
    assert any("checked as a possible passport and none was found" in w for w in output["warnings"])


def test_a_kind_without_pages_takes_the_candidates_when_veris_finds_one(combined, monkeypatch):
    from app.integrations.ocr import classification

    monkeypatch.setattr(classification, "_IDENTITY_HINT", classification.re.compile(r"garden committee", classification.re.I))
    combined.veris.build = lambda mode, number, texts: (
        {"mrz": {"passport_number": "Z1234567", "surname": "SAMPLE", "given_names": "TESTER", "all_check_digits_valid": True}}
        if mode == "passport" else {"aadhaar": {}})

    output = combined.extract(composite_pdf(["resume", "certificate", "unrelated"], form="native"))

    assert set(output["extractions"]) == {"resume", "passport"}
    assert output["extractions"]["passport"]["selected_pages"] == [3]
    # The page is now an identified page of the file, so the saved inspection and the extraction agree.
    assert output["inspection"]["passport_pages"] == [3] and output["inspection"]["unknown_pages"] == []
    assert combined.saved[-1]["inspection"]["passport_pages"] == [3]
    assert any("identified as a passport by the document reader" in w for w in output["warnings"])


def test_a_file_of_unrelated_pages_costs_nothing(combined):
    output = combined.extract(composite_pdf(["unrelated", "unrelated"], form="native"))
    assert output["status"] == "needs_review" and combined.veris.submissions == []


def test_the_coverage_summary_says_what_was_read_and_what_was_ignored(combined):
    pages = ["resume"] * 5 + ["passport", "certificate"] + ["aadhaar", "aadhaar_back"]
    output = combined.extract(composite_pdf(pages, form="native"))
    coverage = output["coverage"]
    assert coverage["summary"] == "Read: resume pages 1-5, passport page 6, Aadhaar pages 8-9; 1 page ignored (7)."
    assert coverage["pages"]["ignored"] == [7] and coverage["pages"]["unresolved"] == []


def test_a_conflict_between_the_local_reading_and_veris_is_flagged_without_printing_values(combined, monkeypatch):
    import sys
    import types

    fake = types.ModuleType("app.integrations.ocr.validators")
    fake.local_patterns = lambda kind, text: {"passport_number": "Z7000137"} if kind == "passport" else {}
    fake.cross_check = lambda patterns, payload: {
        "agreements": [{"field": "date_of_birth"}], "conflicts": [{"field": "passport_number"}]}
    monkeypatch.setitem(sys.modules, "app.integrations.ocr.validators", fake)
    import app.integrations.ocr as package
    monkeypatch.setattr(package, "validators", fake, raising=False)

    output = combined.extract(composite_pdf(["resume", "passport"], form="native"))

    check = output["coverage"]["kinds"]["passport"]["crossCheck"]
    assert check == {"verified": ["date_of_birth"], "conflicts": ["passport_number"]}
    assert output["status"] == "needs_review"
    assert any("disagree on passport_number" in w for w in output["warnings"])
    assert "Z7000137" not in str(output["coverage"]) + str(output["warnings"])
    assert combined.state["local_patterns"] == {"passport": {"passport_number": "Z7000137"}}


def test_two_hundred_pages_are_read_as_bounded_parallel_chunks_and_merged_completely(combined):
    from test_veris_handoff import long_resume

    combined.settings.veris_parallel_jobs = 3
    combined.veris.delay = 0.03
    combined.veris.build = lambda mode, number, texts: {"skills": [f"Skill{number}"]}

    output = combined.extract(long_resume(200))

    record = combined.state["provider_jobs"]["resume"]
    assert len(record["chunks"]) == 10  # ceil(200 / 20)
    sizes = [len(chunk["selected_pages"]) for chunk in record["chunks"]]
    assert max(sizes) <= 21 and sum(sizes) == 200 + 9  # nine one-page overlaps
    covered = sorted({page for chunk in record["chunks"] for page in chunk["selected_pages"]})
    assert covered == list(range(1, 201))
    assert 1 < combined.veris.peak <= 3  # side by side, never more than VERIS_PARALLEL_JOBS
    assert len(combined.veris.submissions) == 10
    resume = output["extractions"]["resume"]
    assert [p["page_number"] for p in resume["raw"]["pages"]] == list(range(1, 201))
    assert sorted(resume["raw"]["skills"]) == sorted(f"Skill{n}" for n in range(10))  # every chunk's list is kept
    assert output["status"] == "complete" and output["coverage"]["pages"]["sent"] == list(range(1, 201))


def test_pages_found_after_the_first_dispatch_are_sent_as_a_late_part_and_merged(combined):
    import hashlib

    data = composite_pdf(["resume"] * 4, form="native")
    inspected = inspection.inspect_document(data)
    report = inspected.to_dict()
    digest = hashlib.sha256(data).hexdigest()
    state: dict = {}

    def run(pages):
        return subject._run_kinds({"resume": list(pages)}, inspected, dict(report), digest, state=state,
                                  save=lambda s: None, settings=combined.settings, designations=[])

    run([1, 2])
    assert len(combined.veris.submissions) == 1
    first_key = combined.veris.submissions[0]["key"]

    extractions, _warnings, covered, _ = run([1, 2, 3, 4])

    assert covered and len(combined.veris.submissions) == 2
    assert combined.veris.submissions[0]["key"] == first_key  # the first job is not sent again
    assert len(combined.veris.submissions[1]["texts"]) == 2   # only the late pages
    assert set(state["provider_jobs"]) == {"resume", "resume+"}
    resume = extractions["resume"]
    assert [p["page_number"] for p in resume["raw"]["pages"]] == [1, 2, 3, 4]
    assert resume["source"]["selected_pages"] == [1, 2, 3, 4] and resume["coverage"]["chunks"] == 2

    run([1, 2, 3, 4])  # a retry sends nothing new
    assert len(combined.veris.submissions) == 2


def test_a_page_that_was_already_sent_is_never_taken_back(combined):
    import hashlib

    data = composite_pdf(["resume"] * 3, form="native")
    inspected = inspection.inspect_document(data)
    digest = hashlib.sha256(data).hexdigest()
    state: dict = {}
    for pages in ([1, 2, 3], [1, 2]):
        extractions, *_ = subject._run_kinds({"resume": pages}, inspected, inspected.to_dict(), digest, state=state,
                                             save=lambda s: None, settings=combined.settings, designations=[])
    assert len(combined.veris.submissions) == 1 and extractions["resume"]["source"]["selected_pages"] == [1, 2, 3]


@needs_tesseract
def test_settled_pages_are_handed_out_after_the_first_look_while_the_rest_are_still_being_read():
    seen = []

    def first_look(partial, provisional, again):
        # Cutting pages out is safe while the second look is about to read the others.
        seen.append((dict(zip(("resume", "passport", "aadhaar"), (
            [p.page_number for p, v in zip(partial.pages, provisional) if v.document_type == kind and p.page_number not in again]
            for kind in ("resume", "passport", "aadhaar")))), list(again), len(partial.pdf_for_pages([1], allow_unresolved=True)) > 0))

    done = inspection.inspect_document(composite_pdf(["resume", "passport", "unrelated", "aadhaar"], form="scan"), on_first_pass=first_look)

    assert len(seen) == 1
    settled, again, cut = seen[0]
    assert cut and settled["resume"] == [1] and settled["passport"] == [2] and settled["aadhaar"] == [4]
    assert again == [3]  # the unclassified page gets the second, slower look
    assert done.resume_pages == [1] and done.passport_pages == [2] and done.aadhaar_pages == [4]
