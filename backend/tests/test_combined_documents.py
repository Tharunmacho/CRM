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


def test_candidate_packet_sends_education_with_resume_and_files_them_separately(combined):
    from test_document_extraction import pdf
    from test_kind_classifier import EXPERIENCE, MEDICAL, SSLC
    from test_ocr_classification import CV, PASSPORT, AADHAAR

    # A packet of 25 pages. The provider must see just the slices identified locally.
    data = pdf(CV, CV + "\nPrevious employment", PASSPORT, AADHAAR, SSLC,
               *(f"{MEDICAL}\nSheet {n}" for n in range(19)), EXPERIENCE)
    output = combined.extract(data, candidate_filing=True)
    report = output["inspection"]
    assert combined.plan() == {"resume": [1, 2, 5], "passport": [3], "aadhaar": [4]}
    assert report["document_pages"] == {"resume": [1, 2], "passport": [3], "aadhaar": [4]}
    assert report["supporting_pages"]["5"] == "education_certificate"
    assert set(map(int, report["supporting_pages"])) == set(range(5, 26))
    assert len(combined.veris.submissions) == 3
    again = combined.extract(data, candidate_filing=True)
    assert again["inspection"]["document_pages"] == report["document_pages"]
    assert len(combined.veris.submissions) == 3


def test_candidate_keeps_local_page_groups_when_the_reader_cannot_read_any_fields(combined):
    from test_document_extraction import pdf
    from test_ocr_classification import PASSPORT
    from test_veris_handoff import unreadable

    combined.veris.script[("passport", 0)] = "failed"
    combined.veris.errors[("passport", 0)] = unreadable(False)
    output = combined.extract(pdf(PASSPORT), candidate_filing=True)

    assert output["status"] == "needs_review"
    assert output["inspection"]["document_pages"]["passport"] == [1]
    assert output["extractions"] == {}
    assert output["coverage"]["failed"]


def test_reviewed_education_page_is_read_with_resume_and_later_placement_is_respected(combined):
    from test_document_extraction import pdf, UNCLASSIFIED
    from test_ocr_classification import CV

    data = pdf(CV, UNCLASSIFIED)
    combined.state["page_corrections"] = {"2": {"kind": "education_certificate"}}
    accepted = combined.extract(data, candidate_filing=True)
    assert combined.plan() == {"resume": [1, 2]}
    assert accepted["inspection"]["document_pages"]["resume"] == [1]
    assert accepted["inspection"]["supporting_pages"] == {"2": "education_certificate"}

    combined.state["page_corrections"] = {"2": {"kind": "medical"}}
    corrected = combined.extract(data, candidate_filing=True)
    assert combined.plan() == {"resume": [1]}
    assert corrected["inspection"]["supporting_pages"] == {"2": "medical"}


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
            data, state=options.pop("state", state), save=lambda s: saved.append(deepcopy(s)), settings=settings,
            designations=["Welder"], **options)

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
    assert output["status"] == "complete"  # page 3 was read but is nothing known: it is filed under Other documents, no alarm ...
    assert {p["page"]: p["blocking"] for p in output["coverage"]["pages"]["not_sent"]} == {3: False}
    assert not any("Pages 3" in w for w in output["warnings"])
    # ... and it was not sent anywhere: nothing about it looked like an identity document.
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


def test_an_unclassified_page_that_only_mentions_an_identity_word_is_not_sent_to_the_reader(combined, monkeypatch):
    """A page beside a passport that merely says "date of birth" or "government" carries no cue of a passport page (the machine
    zone, the country, the printed captions, an address with a PIN): it is accounted for, not paid for."""
    from app.integrations.ocr import classification

    data = composite_pdf(["resume", "passport", "unrelated", "aadhaar"], form="native")
    monkeypatch.setattr(classification, "_IDENTITY_HINT", classification.re.compile(r"garden committee", classification.re.I))

    output = combined.extract(data)

    assert combined.plan() == {"resume": [1], "passport": [2], "aadhaar": [4]}
    assert output["coverage"]["pages"]["probed"] == {}
    assert output["extractions"]["passport"]["selected_pages"] == [2]
    assert [p["page"] for p in output["coverage"]["pages"]["not_sent"]] == [3]  # named in the coverage, with its warning


def test_a_kind_without_pages_is_probed_with_the_suspected_page_and_dropped_when_nothing_is_found(combined):
    data = composite_pdf(["resume", "certificate", "passport_damaged"], form="native")
    combined.veris.build = lambda mode, number, texts: {"mrz": {}, "fields": []} if mode == "passport" else {"aadhaar": {}}

    output = combined.extract(data)

    assert combined.plan() == {"resume": [1], "passport": [3]}  # the suspected page goes to the passport mode only
    assert set(output["extractions"]) == {"resume"}
    assert any("looked like a passport but could not be read" in w for w in output["warnings"])
    assert output["inspection"]["passport_pages"] == [] and output["inspection"]["pages"][2]["reason"] == "Looked like a passport but could not be read."


def test_a_kind_without_pages_takes_the_suspected_page_when_veris_confirms_it(combined):
    combined.veris.build = lambda mode, number, texts: (
        {"mrz": {"passport_number": "Z1234567", "surname": "SAMPLE", "given_names": "TESTER", "all_check_digits_valid": True}}
        if mode == "passport" else {"aadhaar": {}})

    output = combined.extract(composite_pdf(["resume", "certificate", "passport_damaged"], form="native"))

    assert set(output["extractions"]) == {"resume", "passport"}
    assert output["extractions"]["passport"]["selected_pages"] == [3]
    # The page is now an identified page of the file, so the saved inspection and the extraction agree.
    assert output["inspection"]["passport_pages"] == [3] and output["inspection"]["unknown_pages"] == []
    assert combined.saved[-1]["inspection"]["passport_pages"] == [3]
    assert any("looked like a passport and were confirmed by the document reader" in w for w in output["warnings"])


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


# ------------------------------------------------------------------------------ the slot the person chose (expected_kind)

OLD_STOP = "Some pages are unread or unclassified. Review the document before extraction."
PASSPORT_NOT_READ = ("We could not read this passport scan. Upload a clearer copy of the data page (sharp, upright, "
                     "the machine-readable lines visible) or enter the details by hand.")
NOTHING = lambda mode, number, texts: {"mrz": {}, "fields": []} if mode == "passport" else {"aadhaar": {}}


def lose_the_text(monkeypatch, *numbers):
    """The local check could not read these pages at all (a photographed or faint scan): unread, no text."""
    real = subject.inspect_document

    def inspect_and_lose(data, **options):
        found = real(data, **options)
        for page in found.pages:
            if page.page_number in numbers:
                page.status, page.document_type, page.method, page.text = "unread", "unknown", "tesseract", ""
                page.reason = "We could not read enough text on this page"
        return found

    monkeypatch.setattr(subject, "inspect_document", inspect_and_lose)


def hint_on_unrelated(monkeypatch):
    """The pages of the synthetic 'unrelated' minutes show an identity sign (a hint only, never a classification)."""
    from app.integrations.ocr import classification
    monkeypatch.setattr(classification, "_IDENTITY_HINT", classification.re.compile(r"garden committee", classification.re.I))


def form_result(output, person, monkeypatch, slot="passport"):
    """What the application form does with a reading (the same call the worker makes), without a database."""
    from types import SimpleNamespace as Namespace
    from app.services import application_form

    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args, **kwargs: {})
    audit = []
    # The file's soft-delete lookup answers "not deleted" (None); every other lookup is the candidate.
    db = Namespace(scalar=lambda statement, *args: None if "deleted_at" in str(statement) else person, add=audit.append)
    job = Namespace(id="job", candidate_id=person.id, owner_id="staff", status=output["status"], last_error=None,
                    draft={"applicationForm": {"slot": slot, "attachmentId": "file"}})
    application_form.apply_job_output(db, job, output)
    return job, job.draft["applicationForm"]["result"]


@pytest.mark.parametrize("how", ["unidentified", "unread"])
def test_an_unclassified_scan_in_the_passport_slot_reaches_the_reader_and_fills_the_form(combined, monkeypatch, how):
    from test_document_identity import candidate
    from app.services.reading_notes import plain_notes

    lose_the_text(monkeypatch, 1) if how == "unread" else hint_on_unrelated(monkeypatch)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")

    assert [s["mode"] for s in combined.veris.submissions] == ["passport"] and combined.plan() == {"passport": [1]}
    assert set(output["extractions"]) == {"passport"} and output["extractions"]["passport"]["selected_pages"] == [1]
    assert output["status"] == "needs_review"
    assert any("identified as a passport by the document reader, not by the local check" in w for w in output["warnings"])
    assert output["inspection"]["passport_pages"] == [1] and output["inspection"]["unknown_pages"] == []
    assert combined.state["probe_plan"] == {"passport": [1]}
    # What the person is told: it was read by the reader and wants a check (never the vague "some pages could not be read").
    first = plain_notes(output["warnings"])[0]
    assert first.startswith("The passport was read by the document reader") and first == output["warnings"][0]

    person = candidate("Alex Example")
    job, shown = form_result(output, person, monkeypatch)
    assert shown["status"] == "needs_review" and shown["message"] == first and shown["filled"] and shown["kindsFound"] == ["passport"]
    assert person.passport_no == "P0000000"


@pytest.mark.parametrize("how", ["unidentified", "unread"])
def test_when_the_reader_finds_no_passport_nothing_is_filled_and_the_message_is_specific(combined, monkeypatch, how):
    from test_document_identity import candidate
    from app.services.reading_notes import PAGES_NOTE, plain_notes

    lose_the_text(monkeypatch, 1) if how == "unread" else hint_on_unrelated(monkeypatch)
    combined.veris.build = NOTHING

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")

    assert combined.plan() == {"passport": [1]} and output["extractions"] == {}
    assert output["status"] == "needs_review"
    assert output["warnings"][0] == PASSPORT_NOT_READ
    assert any("checked as a possible passport and none was found" in w for w in output["warnings"])
    assert output["coverage"]["failed"] == [PASSPORT_NOT_READ]  # the reason shown on the upload row
    assert output["inspection"]["passport_pages"] == []
    # The note survives plain_notes as it is and comes first: it, not the vague pages note, is what the form shows.
    assert plain_notes(output["warnings"])[0] == PASSPORT_NOT_READ != PAGES_NOTE
    person = candidate("Alex Example")
    before = dict(person.__dict__)
    _job, shown = form_result(output, person, monkeypatch)
    assert shown["status"] == "unreadable" and shown["message"] == PASSPORT_NOT_READ and shown["filled"] == []
    assert person.__dict__ == before


def test_a_passport_for_another_person_found_this_way_is_still_rejected(combined, monkeypatch):
    from test_document_identity import candidate
    from app.services.document_identity import IDENTITY_REVIEW_MESSAGE

    hint_on_unrelated(monkeypatch)
    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")
    assert set(output["extractions"]) == {"passport"}  # read by the reader as "ALEX EXAMPLE"

    person = candidate("Original Person")
    job, shown = form_result(output, person, monkeypatch)

    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    assert shown["identityMismatch"] is True and shown["filled"] == [] and person.passport_no is None


@pytest.mark.parametrize("expected", [None, "medical", "photo", ""])
def test_without_a_readable_slot_an_unclassified_file_behaves_as_before(combined, expected):
    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind=expected)

    assert combined.veris.submissions == [] and output["extractions"] == {}
    assert output["status"] == "needs_review" and output["warnings"][-1] == OLD_STOP
    assert not any("scan" in w for w in output["warnings"])
    assert not combined.state.get("probe_plan")


@pytest.mark.parametrize("expected", ["passport", "aadhaar", "resume"])
def test_a_page_read_fine_as_plain_unrelated_text_is_never_sent_as_the_slot_kind(combined, expected):
    output = combined.extract(composite_pdf(["unrelated"] * 2, form="native"), expected_kind=expected)

    assert combined.veris.submissions == [] and output["extractions"] == {}
    assert output["status"] == "needs_review" and output["warnings"][-1] == OLD_STOP
    assert not combined.state.get("probe_plan")


def test_not_passing_expected_kind_is_the_same_as_passing_none(combined):
    plain = combined.extract(composite_pdf(["unrelated"], form="native"))
    again = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind=None, state={})
    assert {k: v for k, v in plain.items() if k != "inspection"} == {k: v for k, v in again.items() if k != "inspection"}
    assert plain["warnings"][-1] == OLD_STOP and plain["inspection"]["pages"][0]["status"] == "read"


def test_a_file_with_one_classified_page_gets_no_extra_probes_from_the_slot(combined):
    data = composite_pdf(["resume", "passport", "unrelated", "aadhaar"], form="native")

    output = combined.extract(data, expected_kind="passport")

    # Exactly the outcome of test_an_unrelated_page_never_blocks_the_documents_that_are_there.
    assert combined.plan() == {"resume": [1], "passport": [2], "aadhaar": [4]}
    assert len(combined.veris.submissions) == 3 and combined.state["probe_plan"] == {}
    assert output["coverage"]["pages"]["probed"] == {}
    assert {p["page"]: p["blocking"] for p in output["coverage"]["pages"]["not_sent"]} == {3: False}
    assert output["status"] == "complete" and not any("scan" in w for w in output["warnings"])


def test_at_most_six_pages_of_an_unidentified_file_are_sent_and_the_rest_stay_in_the_original(combined, monkeypatch):
    hint_on_unrelated(monkeypatch)
    output = combined.extract(composite_pdf(["unrelated"] * 10, form="native"), expected_kind="passport")

    # The passport mode takes one page per job (see _new_record): six pages are six one-page jobs, never more.
    assert len(combined.veris.submissions) == 6 and all(len(s["texts"]) == 1 for s in combined.veris.submissions)
    assert combined.plan() == {"passport": [1, 2, 3, 4, 5, 6]}
    assert output["extractions"]["passport"]["selected_pages"] == [1, 2, 3, 4, 5, 6]
    assert any(w.startswith("Pages 7, 8, 9, 10 could not be read or classified and were not extracted") for w in output["warnings"])
    assert output["coverage"]["pages"]["unresolved"] == [7, 8, 9, 10]


def test_an_unidentified_aadhaar_or_resume_slot_is_probed_as_that_kind_and_dropped_when_nothing_is_found(combined, monkeypatch):
    hint_on_unrelated(monkeypatch)
    combined.veris.build = lambda mode, number, texts: {"aadhaar": {}} if mode == "aadhaar" else {"name": "", "pages": []}

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="aadhaar")

    assert combined.plan() == {"aadhaar": [1]} and output["extractions"] == {}
    assert output["warnings"][0].startswith("We could not read this Aadhaar card scan. Upload a clearer copy of the front and back")


def test_the_probe_plan_is_kept_so_a_retry_after_a_pending_reader_sends_the_same_pages(combined, monkeypatch):
    hint_on_unrelated(monkeypatch)
    data = composite_pdf(["unrelated"] * 3, form="native")
    combined.veris.script[("passport", 0)] = "running"
    with pytest.raises(subject.ExtractionPending):
        combined.extract(data, expected_kind="passport")
    assert combined.saved[-1]["probe_plan"] == {"passport": [1, 2, 3]}

    combined.veris.script.clear()
    first = [s["key"] for s in combined.veris.submissions]
    output = combined.extract(data, expected_kind="passport", state=deepcopy(combined.saved[-1]))

    keys = [s["key"] for s in combined.veris.submissions]
    assert keys[:len(first)] == first and len(keys) == 3 == len(set(keys))  # one job per page, none sent twice
    assert set(output["extractions"]) == {"passport"} and output["extractions"]["passport"]["selected_pages"] == [1, 2, 3]


def test_the_pages_chosen_are_unread_or_unidentified_with_an_identity_sign_and_capped_at_six():
    def page(number, status="read", kind="unknown", method="native_text", text="Surname of the holder", reason=""):
        return SimpleNamespace(page_number=number, status=status, document_type=kind, method=method, text=text, reason=reason)

    pages = [page(1, "unread", method="tesseract", text=""),            # unread: counts
             page(2, text="Plain minutes of a committee meeting"),       # read fine, no identity sign: never
             page(14, "unread", method="tesseract", text="lorem ipsum nothing to see here"),  # unread but plainly prose: never
             page(15, "unread", method="tesseract", text="xq zv 7 ,,"),  # unread, scrap of noise: counts
             page(3), page(4), page(5),                                  # unidentified with an identity sign
             page(6, "blank", "other", "photo_no_text"),
             page(7, "unread", method="not_processed", text="", reason=f"This document has more than {PAGE_CAP} pages, which is too many to read."),
             page(8, "unread", method="not_processed", text="", reason=f"This document has more than {PAGE_CAP} pages, which is too many to read."),
             page(9, "read", "other")]
    inspected = SimpleNamespace(pages=pages, has_classified_pages=False)

    assert subject._expected_probe_pages(inspected, "passport") == {"passport": [1, 3, 4, 5, 15]}
    inspected.pages = pages + [page(number) for number in range(10, 14)]
    assert subject._expected_probe_pages(inspected, "passport") == {"passport": [3, 4, 5, 10, 11, 12]}  # signs first, then the first pages
    assert subject._expected_probe_pages(inspected, "medical") == {} and subject._expected_probe_pages(inspected, None) == {}
    inspected.has_classified_pages = True
    assert subject._expected_probe_pages(inspected, "passport") == {}
    assert subject._expected_probe_pages(SimpleNamespace(pages=[page(2, text="Plain minutes"), page(6, "blank", "other", "photo_no_text"), page(9, "read", "other")], has_classified_pages=False), "passport") == {}


PAGE_CAP = 50
NOISE = "= i =. i� el Fal �a, a rig ,, ; xk tq. " * 25          # a noisy phone photo read as hundreds of stray characters
RESUME_TEXT = "Experienced engineer managing large projects and delivering reliable software for customers. " * 12
TIME_LIMIT = "Reading this document went over the time limit."
TOO_LARGE = "This page is too large to read."
PAGE_CAP_REASON = f"This document has more than {PAGE_CAP} pages, which is too many to read."


def reshape(monkeypatch, number, **fields):
    """Make the local check's verdict on one page of the file what a hard scan gives (status, method, text, reason)."""
    real = subject.inspect_document

    def inspect_and_change(data, **options):
        found = real(data, **options)
        for page in found.pages:
            if page.page_number == number:
                for name, value in fields.items():
                    setattr(page, name, value)
        return found

    monkeypatch.setattr(subject, "inspect_document", inspect_and_change)


def probe_page(number, status="read", kind="unknown", method="tesseract", text="", reason=""):
    return SimpleNamespace(page_number=number, status=status, document_type=kind, method=method, text=text, reason=reason)


def test_the_noise_and_the_resume_text_used_here_are_what_they_claim_to_be():
    assert subject._reads_as_noise(NOISE) and not subject._reads_as_noise(RESUME_TEXT)
    assert not subject._reads_as_noise("") and not subject._reads_as_noise("xq zv 7 ,,") and not subject._reads_as_noise("lorem ipsum nothing to see here")


def test_a_page_read_as_hundreds_of_stray_characters_in_the_passport_slot_reaches_the_reader(combined, monkeypatch):
    reshape(monkeypatch, 1, status="read", document_type="unknown", method="tesseract", text=NOISE)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")

    assert [s["mode"] for s in combined.veris.submissions] == ["passport"] and combined.plan() == {"passport": [1]}
    assert set(output["extractions"]) == {"passport"} and combined.state["probe_plan"] == {"passport": [1]}


def test_a_normal_page_of_text_in_the_passport_slot_does_not_reach_the_reader(combined, monkeypatch):
    reshape(monkeypatch, 1, status="read", document_type="unknown", method="tesseract", text=RESUME_TEXT)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")

    assert combined.veris.submissions == [] and output["extractions"] == {} and not combined.state.get("probe_plan")
    assert output["warnings"][-1] == OLD_STOP


def test_without_a_slot_a_page_of_stray_characters_is_left_alone(combined, monkeypatch):
    reshape(monkeypatch, 1, status="read", document_type="unknown", method="tesseract", text=NOISE)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind=None)

    assert combined.veris.submissions == [] and output["extractions"] == {} and output["warnings"][-1] == OLD_STOP


@pytest.mark.parametrize("reason", [TIME_LIMIT, TOO_LARGE])
def test_a_page_stopped_by_a_time_limit_or_an_error_reaches_the_reader_in_the_passport_slot(combined, monkeypatch, reason):
    method = "not_processed" if reason == TIME_LIMIT else "failed"
    reshape(monkeypatch, 1, status="unread", document_type="unknown", method=method, text="", reason=reason)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")

    assert [s["mode"] for s in combined.veris.submissions] == ["passport"] and combined.plan() == {"passport": [1]}
    assert set(output["extractions"]) == {"passport"}


@pytest.mark.parametrize("method", ["not_processed", "failed"])
def test_a_page_left_out_for_the_page_cap_never_reaches_the_reader(combined, monkeypatch, method):
    reshape(monkeypatch, 1, status="unread", document_type="unknown", method=method, text="", reason=PAGE_CAP_REASON)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind="passport")

    assert combined.veris.submissions == [] and output["extractions"] == {} and not combined.state.get("probe_plan")


@pytest.mark.parametrize("method, reason", [("not_processed", TIME_LIMIT), ("failed", TOO_LARGE), ("not_processed", PAGE_CAP_REASON)])
def test_without_a_slot_stopped_pages_are_left_alone(combined, monkeypatch, method, reason):
    reshape(monkeypatch, 1, status="unread", document_type="unknown", method=method, text="", reason=reason)

    output = combined.extract(composite_pdf(["unrelated"], form="native"), expected_kind=None)

    assert combined.veris.submissions == [] and output["extractions"] == {}


def test_noise_and_stopped_pages_are_chosen_after_the_pages_with_an_identity_sign_and_capped_at_six():
    pages = [probe_page(1, text=NOISE),
             probe_page(2, "unread", method="not_processed", reason=TIME_LIMIT),
             probe_page(3, "unread", method="failed", reason=TOO_LARGE),
             probe_page(4, text=RESUME_TEXT),                                                       # real text: never
             probe_page(5, "unread", method="not_processed", reason=PAGE_CAP_REASON),               # the page cap: never
             probe_page(6, "unread", method="failed", reason=PAGE_CAP_REASON),
             probe_page(7, text="Surname of the holder"),                                           # identity sign: first
             probe_page(8, "blank", "other", "photo_no_text"),
             probe_page(9, text=NOISE, kind="other"),                                               # classified as another kind: never
             probe_page(10, text="Given names " + NOISE)]
    inspected = SimpleNamespace(pages=pages, has_classified_pages=False)

    assert subject._expected_probe_pages(inspected, "passport") == {"passport": [1, 2, 3, 7, 10]}
    inspected.pages = pages + [probe_page(number, text=NOISE) for number in range(11, 16)]
    assert subject._expected_probe_pages(inspected, "passport") == {"passport": [1, 2, 3, 7, 10, 11]}
    pages_with_signs = [probe_page(n, text="Surname of the holder") for n in (8, 9, 10)] + [probe_page(n, text=NOISE) for n in (1, 2, 3, 4)]
    assert subject._expected_probe_pages(SimpleNamespace(pages=pages_with_signs, has_classified_pages=False), "aadhaar") == {"aadhaar": [1, 2, 3, 8, 9, 10]}
    assert subject._expected_probe_pages(inspected, None) == {} and subject._expected_probe_pages(inspected, "medical") == {}
    inspected.has_classified_pages = True
    assert subject._expected_probe_pages(inspected, "passport") == {}


def test_the_notes_this_adds_are_kept_by_plain_notes_exactly():
    from app.services.reading_notes import plain_notes
    for kind in ("resume", "passport", "aadhaar"):
        for note in (subject._expected_unread_note(kind), subject._expected_read_note(kind)):
            assert plain_notes([note]) == [note]
            assert plain_notes([note], kind=kind) == [note]
