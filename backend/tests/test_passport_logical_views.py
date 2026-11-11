"""A provider may return two passport sides for one physical source sheet."""
from copy import deepcopy

import pytest
from veriis.models import PassportResponse, ResumeResponse

from app.integrations.ocr.page_coverage import source_page_positions
from app.services import document_extraction as subject
from test_document_extraction import pdf, result
from test_ocr_classification import PASSPORT
from test_veris_handoff import world


SPLIT = "Dual-page scan detected (MRZ unparseable on the intact scan) — split 1 page(s) into 2 halves so the bio-page MRZ band could be read on its own"
ALTERNATE = "Dual-page scan detected — split 1 input page(s) into 2 halves for per-side rotation correction"


def split_result(warning=SPLIT):
    value = result("passport", 2)
    value["warnings"] = [warning]
    return value


@pytest.mark.parametrize("warning", [SPLIT, ALTERNATE])
def test_explicit_split_maps_two_views_to_one_input_and_preserves_raw_result_on_replay(world, warning):
    value = split_result(warning)
    world.veris.build = lambda *_: deepcopy(value)
    source = pdf(PASSPORT)
    output = world.extract(source)
    assert output["status"] == "complete"
    extraction = output["extractions"]["passport"]
    assert extraction["fields"]["passport_no"] == "P0000000"
    assert extraction["raw"] == value
    assert extraction["source"]["selected_pages"] == [1]
    assert extraction["source"]["provider_page_sources"] == [1, 1]
    state = deepcopy(world.saved[-1])
    paid = deepcopy(state["provider_jobs"]["passport"])
    # Simulate the saved review output produced by the previous coverage rule.
    state["output"] = None
    state["provider_jobs"]["passport"]["coverage_status"] = "needs_review"
    replay = world.extract(source, state=state)
    assert replay["status"] == "complete" and len(world.veris.submissions) == 1
    assert state["provider_jobs"]["passport"]["job_id"] == paid["job_id"]
    assert state["provider_jobs"]["passport"]["result"] == paid["result"] == value


@pytest.mark.parametrize("change", ["missing_marker", "missing_text", "duplicate_number", "conflicting_identity", "wrong_source_count", "wrong_kind"])
def test_unproved_expansion_and_conflicting_identities_remain_unusable(change):
    payload = split_result()
    expected, kind, model = 1, "passport", PassportResponse
    if change == "missing_marker":payload["warnings"] = ["Extra page returned"]
    elif change == "missing_text":payload["pages"][1]["text"] = " "
    elif change == "duplicate_number":payload["pages"][1]["page_number"] = 1
    elif change == "conflicting_identity":
        payload["pages"][0]["text"] = "Passport No: A1234567"
        payload["pages"][1]["text"] = "Passport No: B7654321"
    elif change == "wrong_source_count":expected = 3
    else:
        kind,model = "resume",ResumeResponse
        payload = {**result("resume",2),"warnings":[SPLIT]}
    assert source_page_positions(kind,model.model_validate(payload),expected) is None


def test_chunk_merging_retains_both_complete_views_and_exact_paid_checkpoint():
    first,second = split_result(),result("passport",1)
    state = {"provider_jobs":{"passport":{"chunks":[
        {"selected_pages":[1],"positions":[1],"job_id":"accepted-first","result":deepcopy(first)},
        {"selected_pages":[2],"positions":[2],"job_id":"accepted-second","result":deepcopy(second)},
    ]}}}
    record=state["provider_jobs"]["passport"]
    merged=subject._merge_kind("passport",record,state,lambda _:None)
    assert subject._chunk_covered(PassportResponse.model_validate(merged),2)
    assert [page["page_number"] for page in merged["pages"]] == [1,2]
    assert merged["pages"][0]["provider_views"] == first["pages"]
    assert merged["pages"][0]["text"] == "\n\n".join(page["text"] for page in first["pages"])
    assert record["chunks"][0]["result"] == first
    assert record["chunks"][0]["job_id"] == "accepted-first"


def test_previous_passport_reference_is_not_a_second_current_identity():
    payload=split_result()
    payload["pages"][0]["text"]="Passport No: A1234567"
    payload["pages"][1]["text"]="Old Passport No: B7654321\nPlace of issue: Example City"
    assert source_page_positions("passport",PassportResponse.model_validate(payload),1)==[1,1]
