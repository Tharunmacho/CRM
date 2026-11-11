"""Handing documents to Veris: side-by-side jobs, chunked long documents, merging, coverage, fewer saves.

Everything here is synthetic and runs against a fake Veris (no network, no API key, no database).
"""

from copy import deepcopy
import hashlib
import logging
import threading
import time
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from pydantic import ValidationError
from veriis import VerisOCRConnectionError
from veriis.errors import VerisOCRRateLimitError, VerisOCRServerError
from veriis.models import AadhaarResponse, PassportResponse, ResumeResponse

from app.integrations.ocr import client as ocr_client
from app.integrations.ocr import field_coverage, inspection
from app.integrations.ocr.merge import merge_results
from app.services import document_extraction as subject
from app.services.application_form import extraction_view
from test_document_extraction import pdf, result
from test_ocr_classification import AADHAAR, AADHAAR_BACK, CERTIFICATE, CONTINUATION, CV, PASSPORT

UNCLASSIFIED = "This is correspondence about travel arrangements and several unrelated meeting dates. " * 3


def long_resume(pages: int) -> bytes:
    """A synthetic résumé whose page n carries the token PAGE-n."""
    return pdf(CV + "\nPAGE-1", *[f"{CONTINUATION}\nPAGE-{n}" for n in range(2, pages + 1)])


class FakeVeris:
    """Thread-safe fake Veris. ``delay`` is how long a submit and a job take; ``build`` shapes a chunk's result."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.lock = threading.Lock()
        self.submissions: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.polls: list[str] = []
        self.active = self.peak = 0
        self.script: dict = {}            # (mode, chunk number) -> status the job reports ("failed", "running", ...)
        self.build = None                 # (mode, index, texts) -> extra keys merged into the default result
        self.submit_faults: list[Exception] = []
        self.errors: dict = {}            # (mode, chunk number) -> the reader's own reason for a failed job
        self.duplicates: set = set()      # (mode, chunk number) of submissions the reader reports as duplicates (same key seen before)

    def submit(self, document, *, mode, idempotency_key):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            fault = self.submit_faults.pop(0) if self.submit_faults else None
        try:
            if self.delay:
                time.sleep(self.delay)
            if fault:
                raise fault
            with fitz.open(stream=document.data, filetype="pdf") as sent:
                texts = [page.get_text() for page in sent]
            with self.lock:
                number = len([s for s in self.submissions if s["mode"] == mode])
                job_id = f"job-{mode}-{len(self.submissions)}"
                self.submissions.append({"mode": mode, "key": idempotency_key, "texts": texts, "job_id": job_id, "number": number})
                self.jobs[job_id] = {"mode": mode, "number": number, "texts": texts, "polls": 0}
            return SimpleNamespace(job_id=job_id, status="queued", duplicate=(mode, number) in self.duplicates)
        finally:
            with self.lock:
                self.active -= 1

    def get(self, job_id):
        with self.lock:
            self.polls.append(job_id)
            job = self.jobs[job_id]
            job["polls"] += 1
        status = self.script.get((job["mode"], job["number"]), "succeeded")
        if callable(status):
            status = status(job["polls"])
        if status != "succeeded":
            return SimpleNamespace(status=status, result=None, error=self.errors.get((job["mode"], job["number"])))
        payload = result(job["mode"], len(job["texts"]))
        for page, text in zip(payload["pages"], job["texts"]):
            page["text"] = text or "blank"
        if self.build:
            payload.update(self.build(job["mode"], job["number"], job["texts"]))
        return SimpleNamespace(status="succeeded", result=payload)


@pytest.fixture
def world(monkeypatch):
    settings = SimpleNamespace(
        tesseract_cmd=None, ocr_languages="eng", ocr_max_pages=300, ocr_page_timeout_seconds=20,
        ocr_document_timeout_seconds=600, veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key",
        ocr_provider_wait_seconds=0.0, veris_parallel_jobs=4, veris_chunk_pages=20, veris_chunk_overlap_pages=1,
    )
    veris = FakeVeris()
    saved: list[dict] = []
    state: dict = {}

    class Provider:
        def __init__(self, **options):
            assert options["timeout"] == 30.0 and options["max_retries"] == 0
            self.jobs = veris

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(subject, "VerisOCR", Provider)
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(inspection.subprocess, "run", lambda *a, **k: pytest.fail("No external processes"))
    sleeps: list[float] = []
    monkeypatch.setattr(ocr_client, "_sleep", sleeps.append)

    def extract(data, **overrides):
        return subject.extract_attachment(
            data, state=overrides.pop("state", state), save=overrides.pop("save", lambda s: saved.append(deepcopy(s))),
            settings=settings, designations=["Welder"], **overrides,
        )

    return SimpleNamespace(extract=extract, veris=veris, settings=settings, saved=saved, state=state, sleeps=sleeps,
                           monkeypatch=monkeypatch)


# ------------------------------------------------------------------------------ side by side


def test_the_kinds_of_one_document_are_sent_side_by_side(world):
    world.veris.delay = 0.3
    started = time.monotonic()

    output = world.extract(pdf(CV, PASSPORT, AADHAAR, AADHAAR_BACK))

    elapsed = time.monotonic() - started
    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport", "aadhaar"}
    # Three submissions of 0.3 s: one after the other would take 0.9 s before any polling.
    assert world.veris.peak == 3
    assert elapsed < 0.8, elapsed
    keys = {s["mode"]: s["key"] for s in world.veris.submissions}
    assert len(set(keys.values())) == 3 and all(key.startswith("crm-") for key in keys.values())


def test_parallel_jobs_are_bounded_by_the_setting(world):
    world.settings.veris_parallel_jobs = 2
    world.veris.delay = 0.05

    output = world.extract(long_resume(100))

    assert output["status"] == "complete"
    assert len(world.veris.submissions) == 5
    assert world.veris.peak <= 2


def test_each_job_keeps_its_own_stable_idempotency_key_across_a_restart(world):
    data = long_resume(45)
    world.veris.script[("resume", 1)] = "running"
    with pytest.raises(subject.ExtractionPending):
        world.extract(data)
    keys = {s["number"]: s["key"] for s in world.veris.submissions}
    assert len(keys) == 3 and len(set(keys.values())) == 3

    world.veris.script.clear()
    output = world.extract(data, state=deepcopy(world.saved[-1]))

    assert output["status"] == "complete"
    assert len(world.veris.submissions) == 3  # nothing was submitted again
    chunks = world.saved[-1]["provider_jobs"]["resume"]["chunks"]
    assert {c["index"]: c["idempotency_key"] for c in chunks} == keys


# ------------------------------------------------------------------------------ polling


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def scripted(*statuses):
    items = list(statuses)
    polls = []

    def get():
        polls.append(1)
        status = items.pop(0) if len(items) > 1 else items[0]
        return SimpleNamespace(status=status, result={} if status == "succeeded" else None)

    return get, polls


def test_polling_starts_fast_and_eases_off_to_one_and_a_half_seconds():
    clock = Clock()
    get, polls = scripted("queued", "running", "running", "running", "running", "running", "succeeded")

    job = ocr_client.settle(get, budget=60, sleep=clock.sleep, clock=clock)

    assert job.status == "succeeded" and len(polls) == 7
    assert clock.sleeps == [0.3, 0.6, 1.0, 1.5, 1.5, 1.5]


def test_a_job_that_finishes_at_once_costs_no_waiting():
    clock = Clock()
    get, polls = scripted("succeeded")
    assert ocr_client.settle(get, budget=60, sleep=clock.sleep, clock=clock).status == "succeeded"
    assert clock.sleeps == [] and len(polls) == 1


def test_polling_stops_at_the_budget_and_reports_the_last_status():
    clock = Clock()
    get, _ = scripted("running")
    job = ocr_client.settle(get, budget=2.0, sleep=clock.sleep, clock=clock)
    assert job.status == "running"
    assert clock.sleeps == pytest.approx([0.3, 0.6, 1.0, 0.1]) and clock.now == pytest.approx(2.0)


def test_a_zero_budget_polls_once():
    clock = Clock()
    get, polls = scripted("running")
    ocr_client.settle(get, budget=0, sleep=clock.sleep, clock=clock)
    assert len(polls) == 1 and clock.sleeps == []


def test_a_document_job_uses_the_adaptive_schedule(world):
    world.settings.ocr_provider_wait_seconds = 30.0
    world.veris.script[("resume", 0)] = lambda polls: "succeeded" if polls >= 4 else "running"
    clock = Clock()
    world.monkeypatch.setattr(ocr_client, "_clock", clock)
    world.monkeypatch.setattr(ocr_client, "_sleep", clock.sleep)

    assert world.extract(pdf(CV))["status"] == "complete"
    assert clock.sleeps == [0.3, 0.6, 1.0]


def test_the_deadline_grows_with_the_page_count_and_is_capped():
    assert ocr_client.provider_deadline(1) == 63
    assert ocr_client.provider_deadline(20) == 120
    assert ocr_client.provider_deadline(100) == 360
    assert ocr_client.provider_deadline(5000) == 600


def test_a_job_still_running_past_its_deadline_is_timed_out_and_stays_retryable(world):
    data = pdf(CV)
    world.veris.script[("resume", 0)] = "running"
    clock = {"now": 1_000_000.0}
    world.monkeypatch.setattr(subject, "_wall", lambda: clock["now"])
    with pytest.raises(subject.ExtractionPending):
        world.extract(data)

    clock["now"] += 62  # a one-page job gets 63 seconds
    with pytest.raises(subject.ExtractionPending):
        world.extract(data, state=deepcopy(world.saved[-1]))
    clock["now"] += 5
    restarted = deepcopy(world.saved[-1])
    with pytest.raises(subject.ExtractionFailure, match="took too long"):
        world.extract(data, state=restarted)

    timed_out = world.saved[-1]["provider_jobs"]["resume"]
    assert timed_out["status"] == "timed_out" and timed_out["job_id"]  # the job is remembered, not forgotten
    saved = deepcopy(world.saved[-1])
    assert subject.request_extraction_retry(saved, automatic=True) == ["resume"]
    world.veris.script.clear()
    assert world.extract(data, state=saved)["status"] == "complete"
    assert len(world.veris.submissions) == 2
    assert world.veris.submissions[0]["key"] != world.veris.submissions[1]["key"]


# ------------------------------------------------------------------------------ long documents


def test_a_hundred_page_resume_is_cut_into_five_jobs_that_overlap_by_one_page(world):
    output = world.extract(long_resume(100))

    assert output["status"] == "complete"
    ranges = []
    for submission in world.veris.submissions:
        numbers = [int(t.split("PAGE-")[1].split()[0]) for t in submission["texts"]]
        ranges.append((numbers[0], numbers[-1], len(numbers)))
    assert sorted(ranges) == [(1, 20, 20), (20, 40, 21), (40, 60, 21), (60, 80, 21), (80, 100, 21)]
    # Every page reached Veris at least once, and the merged result numbers them 1..100 once each.
    resume = output["extractions"]["resume"]
    assert resume["selected_pages"] == list(range(1, 101))
    assert [p["page_number"] for p in resume["raw"]["pages"]] == list(range(1, 101))
    assert all(f"PAGE-{n}" in resume["raw"]["pages"][n - 1]["text"] for n in range(1, 101))
    assert resume["merge"]["chunks"] == 5 and resume["coverage"]["chunks"] == 5
    assert output["coverage"]["pages"]["total"] == 100 and output["coverage"]["pages"]["sent"] == list(range(1, 101))


def test_a_passport_or_aadhaar_is_not_overlapped_and_a_document_of_20_pages_is_one_job(world):
    assert ocr_client.plan_chunks(list(range(1, 21)), 20, 1) == [list(range(1, 21))]
    plan = ocr_client.plan_chunks(list(range(1, 101)), 20, 0)
    assert [len(c) for c in plan] == [20] * 5 and sum(plan, []) == list(range(1, 101))
    world.extract(long_resume(20))
    assert len(world.veris.submissions) == 1


def test_chunks_record_where_each_page_sits_in_the_original_file(world):
    world.extract(pdf(CERTIFICATE, CV + "\nPAGE-1", *[f"{CONTINUATION}\nPAGE-{n}" for n in range(2, 31)]))
    record = world.saved[-1]["provider_jobs"]["resume"]
    assert record["selected_pages"] == list(range(2, 32))
    assert len(record["chunks"]) == 2
    first, second = record["chunks"]
    assert first["selected_pages"][0] == 2 and first["positions"][0] == 1
    # The last page of the first chunk opens the second one (the overlap), and positions count within the pages sent.
    assert second["selected_pages"][0] == first["selected_pages"][-1] and second["positions"][0] == first["positions"][-1]
    assert second["selected_pages"][-1] == 31 and second["positions"][-1] == 30


# ------------------------------------------------------------------------------ merging


def education(degree, institution="ITI", start="2015", end="2018"):
    return {"degree": degree, "institution": institution, "start": start, "end": end}


def test_lists_are_concatenated_and_deduplicated_ignoring_case():
    a = {"request_id": "a", "name": "ALEX EXAMPLE", "skills": ["TIG", "Welding"], "certifications": ["6G"],
         "education": [education("Diploma")], "contact": {"emails": ["alex@example.test"], "phones": ["0000000001"]},
         "personal_info": {"languages_known": ["English"]}, "pages": [{"page_number": 1, "text": "a"}]}
    b = {"request_id": "b", "name": "Alex  Example", "skills": ["welding", "MIG"], "certifications": ["6g", "NDT"],
         "education": [education("diploma"), education("B.Tech", "College")],
         "contact": {"emails": ["ALEX@example.test", "alt@example.test"], "phones": ["0000000001", "0000000002"]},
         "personal_info": {"languages_known": ["english", "Hindi"]}, "pages": [{"page_number": 1, "text": "b"}]}

    merged, conflicts, _ = merge_results("resume", [([1], a), ([2], b)])

    assert merged["skills"] == ["TIG", "Welding", "MIG"]
    assert merged["certifications"] == ["6G", "NDT"]
    assert [e["degree"] for e in merged["education"]] == ["Diploma", "B.Tech"]
    assert merged["contact"]["emails"] == ["alex@example.test", "alt@example.test"]
    assert merged["contact"]["phones"] == ["0000000001", "0000000002"]
    assert merged["personal_info"]["languages_known"] == ["English", "Hindi"]
    assert conflicts == []  # same name in other capitals and spacing is not a disagreement
    assert [p["page_number"] for p in merged["pages"]] == [1, 2]


def test_scalar_conflicts_keep_the_first_value_and_are_reported_not_overwritten():
    a = {"name": "Alex Example", "designation": "Welder", "personal_info": {"date_of_birth": "1990-01-01"}, "pages": []}
    b = {"name": "Alexander Example", "designation": "", "personal_info": {"date_of_birth": "1991-02-02", "gender": "male"}, "pages": []}

    merged, conflicts, _ = merge_results("resume", [([1], a), ([2], b)])

    assert merged["name"] == "Alex Example" and merged["designation"] == "Welder"
    assert merged["personal_info"] == {"date_of_birth": "1990-01-01", "gender": "male"}
    assert sorted(c["field"] for c in conflicts) == ["name", "personal_info.date_of_birth"]
    assert {c["field"]: (c["kept"], c["other"]) for c in conflicts}["name"] == ("Alex Example", "Alexander Example")


def test_identifiers_are_never_printed_in_a_conflict():
    a = {"aadhaar": {"aadhaar_number": "111122223333"}, "pages": []}
    b = {"aadhaar": {"aadhaar_number": "444455556666"}, "pages": []}
    _, conflicts, _ = merge_results("aadhaar", [([1], a), ([2], b)])
    assert conflicts and conflicts[0]["kept"] is None and conflicts[0]["other"] is None


def test_a_job_seen_from_two_chunks_is_one_entry_with_the_longer_description():
    one = {"title": "Welder", "company": "ACME", "start": "2019-01", "end": None, "duration_months": 24, "description": "TIG"}
    two = {"title": "welder", "company": "Acme", "start": "2019-01", "end": "2021-01", "duration_months": 24,
           "description": "TIG welding of pipes and vessels"}
    other = {"title": "Fitter", "company": "Beta", "start": "2015-01", "end": "2018-12", "duration_months": 48}
    a = {"experience": [one], "pages": []}
    b = {"experience": [two, other], "pages": []}

    merged, conflicts, _ = merge_results("resume", [([1], a), ([2], b)])

    assert [e["title"] for e in merged["experience"]] == ["Welder", "Fitter"]
    assert merged["experience"][0]["description"] == "TIG welding of pipes and vessels"
    assert merged["experience"][0]["end"] == "2021-01"
    assert conflicts == []


def test_experience_totals_are_added_up_across_chunks():
    a = {"experience": [{"title": "A", "company": "X", "duration_months": 24, "is_overseas": True}],
         "total_experience_months": 24, "overseas_experience_months": 24, "pages": []}
    b = {"experience": [{"title": "B", "company": "Y", "duration_months": 36}],
         "total_experience_months": 36, "pages": []}

    merged, conflicts, notes = merge_results("resume", [([1], a), ([2], b)])

    assert merged["total_experience_months"] == 60 and merged["overseas_experience_months"] == 24
    assert merged["indian_experience_months"] == 36
    assert notes and conflicts == []


def test_a_page_read_by_two_chunks_is_kept_once_in_order():
    a = {"pages": [{"page_number": 1, "text": "p1"}, {"page_number": 2, "text": "p2"}]}
    b = {"pages": [{"page_number": 1, "text": "p2 again"}, {"page_number": 2, "text": "p3"}]}
    merged, _, _ = merge_results("resume", [([1, 2], a), ([2, 3], b)])
    assert [(p["page_number"], p["text"]) for p in merged["pages"]] == [(1, "p1"), (2, "p2"), (3, "p3")]


def test_a_conflict_between_chunks_sends_the_extraction_to_review_but_keeps_the_fields(world):
    world.veris.build = lambda mode, number, texts: {"name": "Alex Example" if number == 0 else "Someone Else"}

    output = world.extract(long_resume(45))

    assert output["status"] == "needs_review"
    assert output["extractions"]["resume"]["fields"]["name"] == "Alex Example"
    assert output["extractions"]["resume"]["raw"]["name"] == "Alex Example"
    assert any("different values for name" in w for w in output["warnings"])
    assert output["coverage"]["complete"] is False and output["coverage"]["issues"]


def test_merged_chunks_fill_the_candidate_from_every_part_of_the_document(world):
    def build(mode, number, texts):
        return {"name": "ALEX EXAMPLE", "skills": [f"Skill{number}", "Shared"],
                "education": [education(f"Course{number}", f"School{number}")],
                "experience": [{"title": f"Job{number}", "company": f"Firm{number}", "duration_months": 12, "start": "2020", "end": "2021"}],
                "total_experience_months": 12}
    world.veris.build = build

    output = world.extract(long_resume(45))

    fields = output["extractions"]["resume"]["fields"]
    assert fields["skills"] == ["Skill0", "Shared", "Skill1", "Skill2"]
    assert [row["level"] for row in fields["education"]] == ["Course0", "Course1", "Course2"]
    assert [row["designation"] for row in fields["work_history"]] == ["Job0", "Job1", "Job2"]
    assert fields["total_experience"] == 3
    assert output["status"] == "complete"


# ------------------------------------------------------------------------------ failures


def test_a_failed_chunk_is_retried_alone_and_the_good_chunks_are_kept(world):
    data = long_resume(45)
    world.veris.script[("resume", 1)] = "failed"

    with pytest.raises(subject.ExtractionFailure, match="could not read the resume pages \\(part 2 of 3\\)"):
        world.extract(data)

    state = deepcopy(world.saved[-1])
    chunks = state["provider_jobs"]["resume"]["chunks"]
    assert [c["status"] for c in chunks] == ["succeeded", "failed", "succeeded"]
    assert "result" in chunks[0] and "result" in chunks[2]  # paid work is cached under each chunk's key
    first_keys = {s["number"]: s["key"] for s in world.veris.submissions}

    assert subject.request_extraction_retry(state, automatic=True) == ["resume"]
    assert state["provider_job_history"]["resume"][0]["index"] == 1
    world.veris.script.clear()
    output = world.extract(data, state=state)

    assert output["status"] == "complete"
    again = world.veris.submissions[3:]
    assert len(again) == 1  # only the failed chunk was sent again
    assert again[0]["key"] != first_keys[1]  # a new key, because the old job is known to have failed
    assert [p["page_number"] for p in output["extractions"]["resume"]["raw"]["pages"]] == list(range(1, 46))


def test_a_failed_chunk_without_a_retry_request_is_not_resubmitted(world):
    data = long_resume(45)
    world.veris.script[("resume", 2)] = "failed"
    with pytest.raises(subject.ExtractionFailure):
        world.extract(data)
    with pytest.raises(subject.ExtractionFailure):
        world.extract(data, state=deepcopy(world.saved[-1]))
    assert len(world.veris.submissions) == 3


def test_a_chunk_still_running_is_waited_for_on_the_next_visit_while_the_others_are_done(world):
    data = long_resume(45)
    world.veris.script[("resume", 0)] = lambda polls: "running" if polls <= 2 else "succeeded"  # (the early pass polls once)
    with pytest.raises(subject.ExtractionPending) as pending:
        world.extract(data)
    assert pending.value.kind == "resume"
    chunks = world.saved[-1]["provider_jobs"]["resume"]["chunks"]
    assert [c["status"] for c in chunks] == ["running", "succeeded", "succeeded"]

    output = world.extract(data, state=deepcopy(world.saved[-1]))
    assert output["status"] == "complete" and len(world.veris.submissions) == 3


def test_a_chunk_that_does_not_cover_its_pages_needs_review_and_is_restarted_on_explicit_retry(world):
    data = long_resume(45)
    world.veris.build = lambda mode, number, texts: {"pages": result("resume", 1)["pages"]} if number == 1 else {}

    output = world.extract(data)

    assert output["status"] == "needs_review" and output["extractions"] == {}
    assert any("did not cover every selected page" in w for w in output["warnings"])
    state = world.saved[-1]
    assert state["provider_jobs"]["resume"]["chunks"][1]["coverage_status"] == "needs_review"
    # Automatic retries leave a paid but incomplete result for an operator; an explicit retry resends that chunk only.
    assert subject.request_extraction_retry(state, automatic=True) == []
    world.veris.build = None
    assert subject.request_extraction_retry(state) == ["resume"]
    assert world.extract(data, state=state)["status"] == "complete"
    assert len(world.veris.submissions) == 4


def test_a_malformed_payload_is_unusable_but_never_lost_and_stays_retryable(world):
    data = long_resume(45)
    world.veris.build = lambda mode, number, texts: {"skills": "not a list"} if number == 2 else {}
    with pytest.raises(subject.ExtractionFailure, match="could not be used"):
        world.extract(data)
    state = world.saved[-1]
    chunk = state["provider_jobs"]["resume"]["chunks"][2]
    assert chunk["validation_status"] == "failed" and chunk["result"]["skills"] == "not a list"
    assert all("result" in c for c in state["provider_jobs"]["resume"]["chunks"])

    assert subject.request_extraction_retry(state) == ["resume"]
    world.veris.build = None
    assert world.extract(data, state=state)["status"] == "complete"
    assert len(world.veris.submissions) == 4


def test_rate_limits_defer_and_server_errors_retry_with_the_same_key(world, monkeypatch):
    world.veris.submit_faults = [VerisOCRRateLimitError("slow down", status=429, retry_after=1.0),
                                 VerisOCRServerError("down", status=503)]

    data = pdf(CV)
    with pytest.raises(subject.ExtractionDeferred):
        world.extract(data)
    assert not world.veris.submissions and not world.sleeps
    state = deepcopy(world.saved[-1])
    key = state["provider_jobs"]["resume"]["idempotency_key"]
    later = subject._wall() + 5
    monkeypatch.setattr(subject, "_wall", lambda: later)
    output = world.extract(data, state=state)

    assert output["status"] == "complete" and len(world.veris.submissions) == 1
    assert world.veris.submissions[0]["key"] == key
    assert world.sleeps == [0.5]  # Capacity releases the worker; an ordinary server error has a short inline retry.


def test_backoff_grows_and_gives_up_after_the_attempts(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ocr_client, "_sleep", sleeps.append)
    calls = []

    def always_busy():
        calls.append(1)
        raise VerisOCRServerError("down", status=502)

    with pytest.raises(VerisOCRServerError):
        ocr_client.retrying(always_busy)
    assert len(calls) == ocr_client.RETRY_ATTEMPTS and sleeps == [0.5, 1.0, 2.0]


def test_a_dropped_connection_on_submit_is_left_to_the_worker_retry(world):
    world.veris.submit_faults = [VerisOCRConnectionError("https://secret.invalid/?api_key=private")]
    data = pdf(CV)
    with pytest.raises(subject.ExtractionFailure) as raised:
        world.extract(data)
    assert "secret.invalid" not in str(raised.value)
    key = world.saved[-1]["provider_jobs"]["resume"]["idempotency_key"]
    assert world.extract(data, state=deepcopy(world.saved[-1]))["status"] == "complete"
    assert world.veris.submissions[0]["key"] == key


def test_an_unexpected_fault_in_a_job_thread_reaches_the_caller(world):
    world.veris.submit_faults = [RuntimeError("synthetic bug")]
    with pytest.raises(RuntimeError, match="synthetic bug"):
        world.extract(pdf(CV))


# ------------------------------------------------------------------------------ nothing missed


def test_coverage_says_which_pages_were_sent_and_which_were_not_and_why(world):
    output = world.extract(pdf(CERTIFICATE, CV, UNCLASSIFIED, PASSPORT), partial=True)

    pages = output["coverage"]["pages"]
    assert pages["total"] == 4 and pages["sent"] == [2, 4]
    reasons = {p["page"]: p for p in pages["not_sent"]}
    assert set(reasons) == {1, 3}
    assert reasons[1]["blocking"] is False and "stays in the stored original" in reasons[1]["reason"]
    assert reasons[3]["blocking"] is True and "could not be identified" in reasons[3]["reason"]
    assert output["status"] == "needs_review" and output["coverage"]["complete"] is False
    assert world.state["coverage"] == output["coverage"]


def test_local_text_of_pages_veris_never_sees_is_kept_with_the_saved_inspection_only(world):
    secret = "Readable correspondence about unrelated meeting arrangements."
    data = pdf(CV, (secret + " ") * 4)

    output = world.extract(data, partial=True)

    assert world.state["inspection"]["unsent_page_text"]["2"].startswith(secret)
    assert "unsent_page_text" not in output["inspection"]
    # A later visit that restores the inspection (no local OCR) does not lose it.
    again = world.extract(data, partial=True, state=deepcopy(world.saved[-1]))
    assert "unsent_page_text" not in again["inspection"]
    assert world.saved[-1]["inspection"]["unsent_page_text"]["2"].startswith(secret)


def test_every_veris_field_is_accounted_for_and_unmapped_ones_are_listed_and_preserved(world):
    extra = {"contact": {"linkedin": "https://linkedin.example/alex", "emails": ["a@example.test", "b@example.test"]},
             "personal_info": {"religion": "n/a", "mother_name": "Parent Example"}, "industry": "Construction",
             "certifications": ["6G", "NDT"], "brand_new_veris_field": {"kept": True},
             "experience": [{"title": "Welder", "company": "ACME", "description": "TIG welding", "unseen": "x"}]}
    world.veris.build = lambda mode, number, texts: deepcopy(extra)

    output = world.extract(pdf(CV))

    raw = output["extractions"]["resume"]["raw"]
    assert raw["brand_new_veris_field"] == {"kept": True} and raw["certifications"] == ["6G", "NDT"]  # verbatim
    listed = {(g["path"], g["tier"]): g for g in output["coverage"]["unmapped"]}
    assert listed[("contact.linkedin", "content")]["values"] == ["https://linkedin.example/alex"]
    assert listed[("contact.emails[]", "content")]["values"] == ["b@example.test"]  # only the first e-mail is read
    assert listed[("certifications[]", "content")]["count"] == 2
    assert listed[("experience[].description", "content")]["values"] == ["TIG welding"]
    assert ("brand_new_veris_field.kept", "unknown") in listed and ("experience[].unseen", "unknown") in listed
    fields = output["coverage"]["kinds"]["resume"]["fields"]
    assert fields["received"] > fields["mapped"] > 0 and fields["unknown"] == 2
    # By default the extraction is complete, but never silently: the new fields are named in the warnings.
    assert output["status"] == "complete" and any("not seen before" in w for w in output["warnings"])


def test_strict_mode_sends_any_unmapped_detail_to_review(world):
    world.settings.veris_unmapped_needs_review = True
    world.veris.build = lambda mode, number, texts: {"certifications": ["6G"]}

    output = world.extract(pdf(CV))

    assert output["status"] == "needs_review" and output["coverage"]["issues"]
    assert any("no place on the form" in w for w in output["warnings"])
    assert output["extractions"]["resume"]["fields"]["name"] == "Alex Example"  # the fields are still applied


def test_unknown_fields_hold_the_extraction_back_in_strict_mode(world):
    world.settings.veris_unmapped_needs_review = True
    world.veris.build = lambda mode, number, texts: {"brand_new": "x"}
    assert world.extract(pdf(CV))["status"] == "needs_review"


def test_a_clean_document_is_complete_with_nothing_unmapped(world):
    output = world.extract(pdf(CV, PASSPORT))
    assert output["status"] == "complete"
    assert output["coverage"]["complete"] is True and output["coverage"]["issues"] == []
    assert [g for g in output["coverage"]["unmapped"] if g["tier"] == "unknown"] == []


def test_rows_the_form_cannot_hold_are_reported_not_dropped(world):
    world.veris.build = lambda mode, number, texts: {
        "experience": [{"title": f"Job{i}", "company": f"Firm{i}", "start": "2000", "end": "2001", "duration_months": 12} for i in range(35)],
    }
    output = world.extract(pdf(CV))
    assert len(output["extractions"]["resume"]["fields"]["work_history"]) == 30
    assert len(output["extractions"]["resume"]["raw"]["experience"]) == 35  # all 35 stay in the stored document
    assert output["status"] == "needs_review"
    assert any("5 of 35 work history" in w for w in output["warnings"])


def test_sensitive_identifiers_are_listed_without_their_values(world):
    world.veris.build = lambda mode, number, texts: {"aadhaar": {"name": "ALEX EXAMPLE", "vid": "1111222233334444", "mobile_number": "0000000000"}} if mode == "aadhaar" else {}
    output = world.extract(pdf(AADHAAR, AADHAAR_BACK))
    listed = {g["path"]: g for g in output["coverage"]["unmapped"]}
    assert listed["aadhaar.vid"]["tier"] == "sensitive" and listed["aadhaar.vid"]["values"] == []
    assert "1111222233334444" not in str(output["coverage"])


@pytest.mark.parametrize("kind, model", [("resume", ResumeResponse), ("passport", PassportResponse), ("aadhaar", AadhaarResponse)])
def test_every_field_of_the_sdk_models_is_classified(kind, model):
    registry = field_coverage.REGISTRY[kind]
    known = set().union(*registry.values())
    missing = []
    for path in field_coverage.schema_paths(model):
        probe = path.replace("[]", "[1]")
        if kind == "passport" and path.startswith("fields[]."):
            continue
        if field_coverage.classify(kind, probe) == "unknown":
            missing.append(path)
    assert missing == []
    # And the registry names nothing the models no longer have.
    schema = field_coverage.schema_paths(model)
    stale = [p for p in known if p.replace("[0]", "[]") not in schema]
    assert stale == []


def test_the_form_payload_carries_the_unmapped_details(world):
    output = world.extract(pdf(CV))
    job = SimpleNamespace(
        id="job-1", status="completed", attempts=0, last_error=None, updated_at=None,
        draft={"applicationForm": {"result": {"status": "done", "message": None, "kindsFound": ["resume"], "filled": [], "kept": [],
                                              "unmapped": [{"kind": "resume", "path": "industry", "count": 1, "values": ["Construction"], "tier": "content"}],
                                              "coverage": {"pages": output["coverage"]["pages"], "issues": [], "notes": [], "complete": True}}}},
    )
    view = extraction_view(job)
    assert view["unmapped"][0]["path"] == "industry" and view["coverage"]["pages"]["sent"] == [1]
    assert extraction_view(SimpleNamespace(**{**job.__dict__, "draft": {}}))["unmapped"] == []


# ------------------------------------------------------------------------------ fewer saves


def test_saves_happen_at_checkpoints_only(world):
    saves = []
    output = world.extract(pdf(CV, PASSPORT, AADHAAR, AADHAAR_BACK), save=lambda s: saves.append(deepcopy(s)))
    assert output["status"] == "complete"
    # keys + inspection, job ids, one save for each kind that finishes while another is still running (so its fields are
    # visible at once), results, final. (It used to be 14 for this document.)
    assert len(saves) <= 6, len(saves)
    # The first look settles every page of this small document, so the keys are saved before the inspection is complete.
    assert set(saves[0]["provider_jobs"]) == {"resume", "passport", "aadhaar"} and "inspection" in saves[-1]
    assert all("job_id" in saves[1]["provider_jobs"][k] for k in saves[1]["provider_jobs"])


def test_a_single_kind_document_is_saved_at_most_four_times(world):
    saves = []
    world.extract(pdf(CV), save=lambda s: saves.append(deepcopy(s)))
    assert len(saves) <= 4, len(saves)


def test_polling_a_job_that_has_not_changed_writes_nothing(world):
    world.settings.ocr_provider_wait_seconds = 10.0
    clock = Clock()
    world.monkeypatch.setattr(ocr_client, "_clock", clock)
    world.monkeypatch.setattr(ocr_client, "_sleep", clock.sleep)
    world.veris.script[("resume", 0)] = "running"
    saves = []
    with pytest.raises(subject.ExtractionPending):
        world.extract(pdf(CV), save=lambda s: saves.append(deepcopy(s)))
    polls = len(world.veris.polls)
    assert polls > 5  # it did look many times ...
    assert len(saves) <= 3  # ... but wrote the state only for the keys, the job id and the running status

    again = len(saves)
    with pytest.raises(subject.ExtractionPending):
        world.extract(pdf(CV), state=deepcopy(saves[-1]), save=lambda s: saves.append(deepcopy(s)))
    assert len(saves) - again == 0  # a revisit that finds the same status writes nothing


def test_a_long_document_saves_once_for_its_submissions_and_once_for_the_results(world):
    saves = []
    world.extract(long_resume(100), save=lambda s: saves.append(deepcopy(s)))
    assert len(saves) <= 6, len(saves)  # 5 chunks, two waves of at most 4 jobs


# ------------------------------------------------------------------------------ chosen pages and the synchronous scan


def test_pages_a_person_chose_are_read_side_by_side_and_chunked(world):
    world.veris.delay = 0.2
    pages = {"resume": long_resume(45), "passport": pdf(PASSPORT)}
    started = time.monotonic()

    output = subject.extract_selected_pages(
        pages, state={}, save=lambda s: world.saved.append(deepcopy(s)), settings=world.settings, designations=["Welder"],
    )

    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport"}
    assert len(world.veris.submissions) == 4  # three resume chunks and the passport
    assert time.monotonic() - started < 0.2 * 4  # not one after the other
    assert [p["page_number"] for p in output["extractions"]["resume"]["raw"]["pages"]] == list(range(1, 46))


def test_the_synchronous_scan_reads_a_long_pdf_as_merged_chunks(monkeypatch):
    veris = FakeVeris()

    class Provider:
        def __init__(self, **options):
            self.jobs = veris

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(ocr_client, "VerisOCR", Provider)
    from veriis import FileDescriptor

    merged = ocr_client.scan("resume", FileDescriptor(long_resume(45), filename="resume.pdf"),
                             base_url="https://ocr.invalid", api_key="k", idempotency_key="crm-base")

    assert len(veris.submissions) == 3
    assert {s["key"] for s in veris.submissions} == {"crm-base:chunk0", "crm-base:chunk1", "crm-base:chunk2"}
    assert [p["page_number"] for p in merged["pages"]] == list(range(1, 46))
    ResumeResponse.model_validate(merged)


# ------------------------------------------------------------------------------ one kind at a time, degrading


def unreadable(retryable: bool):
    return SimpleNamespace(code="document_unreadable", message="Synthetic reason", retryable=retryable)


COMBINED = (CV, PASSPORT, AADHAAR, AADHAAR_BACK)


def test_kinds_are_sent_one_at_a_time_in_order_and_each_step_is_logged(world, caplog):
    import logging
    caplog.set_level(logging.INFO, logger=subject.log.name)
    world.settings.veris_parallel_jobs = 1
    world.settings.veris_early_dispatch = False  # (early dispatch sends what the first look settled, in that order)
    world.veris.delay = 0.05
    data = pdf(*COMBINED)

    output = world.extract(data)

    assert output["status"] == "complete"
    assert [s["mode"] for s in world.veris.submissions] == ["resume", "passport", "aadhaar"]
    assert world.veris.peak == 1
    text = " | ".join(record.getMessage() for record in caplog.records)
    tag = f"Reading [file {hashlib.sha256(data).hexdigest()[:8]}]"
    for line in (f"{tag}: local check of 4 pages done in", f"{tag} resume: sent pages 1", f"{tag} resume: done in",
                 f"{tag} passport: sent pages", f"{tag} aadhaar: done in", f"{tag}: finished in", "resume ok, passport ok, aadhaar ok"):
        assert line in text, line
    assert "(job resume" in text  # the short job id, without the "job-" prefix
    assert text.index("resume: done") < text.index("passport: sent")
    assert text.index("passport: done") < text.index("aadhaar: sent")
    assert {record.name for record in caplog.records} == {"crm.reading"}
    assert not any(record.levelno > logging.INFO for record in caplog.records)


def test_a_running_kind_holds_the_later_kinds_back_and_a_failed_kind_does_not(world):
    world.settings.veris_parallel_jobs = 1
    world.settings.veris_early_dispatch = False
    world.veris.script[("passport", 0)] = "running"
    with pytest.raises(subject.ExtractionPending):
        world.extract(pdf(*COMBINED))
    assert [s["mode"] for s in world.veris.submissions] == ["resume", "passport"]  # aadhaar waits for the passport

    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = unreadable(False)
    output = world.extract(pdf(*COMBINED), state=deepcopy(world.saved[-1]))
    assert [s["mode"] for s in world.veris.submissions] == ["resume", "passport", "aadhaar"]  # sent although passport failed
    assert set(output["extractions"]) == {"resume", "aadhaar"}


def test_a_terminally_failed_kind_degrades_the_file_and_explicit_retry_reads_only_it(world, caplog):
    import logging
    caplog.set_level(logging.INFO, logger=subject.log.name)
    world.settings.veris_early_dispatch = False
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = unreadable(False)

    output = world.extract(pdf(*COMBINED))

    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume", "aadhaar"}
    notice = "The passport pages could not be read. Enter the passport details by hand"
    assert any(w.startswith(notice) for w in output["warnings"])
    assert notice in " ".join(output["coverage"]["issues"]) and output["coverage"]["complete"] is False
    assert [s["mode"] for s in world.veris.submissions] == ["resume", "passport", "aadhaar"]  # one failure, no repeats
    line = next(r.getMessage() for r in caplog.records if "passport: failed after" in r.getMessage())
    assert "the reader said 'document_unreadable'" in line and "will not be retried" in line and "Synthetic reason" not in line
    summary = next(r.getMessage() for r in caplog.records if ": finished in" in r.getMessage())
    assert "resume ok, passport failed, aadhaar ok" in summary and "(needs review)" in summary
    saved = world.saved[-1]["provider_jobs"]
    assert saved["passport"]["status"] == "failed" and saved["resume"]["status"] == "succeeded"
    assert subject.provider_codes(world.saved[-1]) == "passport=document_unreadable"

    # Another visit with the same state does not fail or pay again; it gives the same degraded answer.
    again = world.extract(pdf(*COMBINED), state=deepcopy(world.saved[-1]))
    assert again["status"] == "needs_review" and len(world.veris.submissions) == 3

    # The explicit retry restarts the passport alone.
    state = deepcopy(world.saved[-1])
    assert subject.request_extraction_retry(state) == ["passport"]
    world.veris.script.clear()
    fixed = world.extract(pdf(*COMBINED), state=state)
    assert fixed["status"] == "complete" and set(fixed["extractions"]) == {"resume", "passport", "aadhaar"}
    assert [s["mode"] for s in world.veris.submissions] == ["resume", "passport", "aadhaar", "passport"]


def test_a_retryable_failure_raises_for_the_automatic_retry_and_degrades_on_the_last_attempt(world):
    world.settings.veris_early_dispatch = False
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = unreadable(True)
    state: dict = {}

    with pytest.raises(subject.ExtractionFailure, match="passport"):
        world.extract(pdf(*COMBINED), state=state)
    assert "result" in state["provider_jobs"]["resume"] and "result" in state["provider_jobs"]["aadhaar"]  # paid work kept
    assert subject.request_extraction_retry(state, automatic=True) == ["passport"]

    world.veris.script[("passport", 1)] = "failed"
    world.veris.errors[("passport", 1)] = unreadable(True)
    output = world.extract(pdf(*COMBINED), state=state, final_attempt=True)
    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume", "aadhaar"}
    assert [s["mode"] for s in world.veris.submissions] == ["resume", "passport", "aadhaar", "passport"]


def test_nothing_usable_still_fails_and_a_failure_without_a_reason_is_retried(world):
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = unreadable(False)
    with pytest.raises(subject.ExtractionFailure, match="passport"):
        world.extract(pdf(PASSPORT), final_attempt=True)

    world.veris.script[("resume", 0)] = "failed"  # no reason given: unknown, so not terminal before the last attempt
    with pytest.raises(subject.ExtractionFailure, match="resume"):
        world.extract(pdf(CV, PASSPORT), state={})
    assert subject.provider_codes({"provider_jobs": {"resume": {"status": "failed"}}}) == "none"


def test_the_defaults_wait_a_minute_per_visit_and_prepare_the_payload():
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.ocr_provider_wait_seconds == 60.0  # a job of 20-45 s is polled to completion in the same visit
    assert settings.veris_passport_one_page is True and settings.veris_job_max_bytes >= 1_000_000


# ------------------------------------------------------------------------------ speed: the jobs of one file overlap


def test_the_default_sends_every_kind_of_a_file_at_once():
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.veris_parallel_jobs == 4  # was 1: a file took the sum of its jobs instead of the longest
    assert settings.veris_passport_pages_parallel is False  # a second scan of the passport data page is still found not needed


@pytest.mark.parametrize("early", [True, False])
def test_resume_passport_and_aadhaar_are_all_in_flight_together_when_nothing_is_configured(world, early):
    del world.settings.veris_parallel_jobs  # the built-in default
    world.settings.veris_early_dispatch = early
    gate = threading.Barrier(3, timeout=5)  # a submit only returns once all three are being sent: one at a time would break it
    real_submit = world.veris.submit

    def submit(document, *, mode, idempotency_key):
        gate.wait()
        return real_submit(document, mode=mode, idempotency_key=idempotency_key)

    world.veris.submit = submit
    started = time.monotonic()

    output = world.extract(pdf(*COMBINED))

    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport", "aadhaar"}
    assert not gate.broken and time.monotonic() - started < 5
    assert {s["mode"] for s in world.veris.submissions} == {"resume", "passport", "aadhaar"}


def _unit(kind: str, pages: list[int], *, record: dict | None = None):
    from veriis import FileDescriptor

    record = record if record is not None else {"selected_pages": pages, "status": "submitting"}
    return subject._Unit(kind, record, pages, f"key-{kind}-{pages}",
                         document=FileDescriptor(pdf(*[f"page {n}" for n in pages]), filename="x.pdf", content_type="application/pdf"))


def test_the_late_pages_of_a_passport_are_sent_beside_the_first_ones_but_two_scans_of_one_page_are_not(world):
    world.veris.delay = 0.2
    settings = SimpleNamespace(veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="k", ocr_provider_wait_seconds=0.0,
                               veris_parallel_jobs=4)
    state: dict = {}

    first, late = _unit("passport", [8]), _unit("passport+", [11])
    subject._run_jobs([first, late], state=state, save=lambda s: None, settings=settings)
    assert world.veris.peak == 2 and first.outcome == late.outcome == "ok"  # "later pages" no longer wait for the first job

    # The chunks (pages) of one passport still go one at a time, so that a repeated scan can be called off.
    world.veris.peak = 0
    chunks = [{"index": i, "selected_pages": [n], "status": "submitting"} for i, n in enumerate((8, 9))]
    units = [_unit("passport", [c["selected_pages"][0]], record=c) for c in chunks]
    subject._run_jobs(units, state=state, save=lambda s: None, settings=settings)
    assert world.veris.peak == 1 and [u.outcome for u in units] == ["ok", "ok"]

    world.veris.peak = 0
    settings.veris_passport_pages_parallel = True
    units = [_unit("passport", [n], record={"index": i, "selected_pages": [n], "status": "submitting"}) for i, n in enumerate((8, 9))]
    subject._run_jobs(units, state=state, save=lambda s: None, settings=settings)
    assert world.veris.peak == 2


def test_a_slow_job_does_not_delay_the_others_and_the_file_takes_about_the_slowest_job(world):
    """Jobs of 0.3 s (sending) side by side: the whole file is about one job long, not three."""
    del world.settings.veris_parallel_jobs
    world.veris.delay = 0.3
    started = time.monotonic()
    output = world.extract(pdf(*COMBINED))
    assert output["status"] == "complete" and world.veris.peak == 3
    assert time.monotonic() - started < 0.75


def test_a_busy_reader_is_deferred_and_the_log_says_when(world, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=subject.log.name)
    world.veris.submit_faults = [VerisOCRRateLimitError("slow down", status=429, retry_after=2.0)]
    data = pdf(CV)
    with pytest.raises(subject.ExtractionDeferred):
        world.extract(data)
    later = subject._wall() + 3
    monkeypatch.setattr(subject, "_wall", lambda: later)
    output = world.extract(data, state=deepcopy(world.saved[-1]))
    assert output["status"] == "complete"
    text = " | ".join(r.getMessage() for r in caplog.records)
    assert "reader capacity is full; saved work will resume in 2s" in text
    assert text.count("resume: sent pages") == 2 and text.count("resume: done in") == 1


def test_httpx_request_lines_are_not_logged_at_info():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
