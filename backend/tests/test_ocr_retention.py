"""Stored Veris output is only ever added to: a later document never replaces or drops an earlier one.

The `doc` helper builds the shape saved before provenance moved out of the payload; `verbatim`
and `src` build the current one (verbatim Veris result + `_sources`). Both must keep merging."""
import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.routers.candidates import OcrDataIn
from app.services.candidate_identity import enrich_empty, merge_ocr_data


def doc(tag, *, sha=None, pages=(1,), source=None):
    value = {"request_id": tag, "pages": [{"page_number": n, "text": f"{tag} page {n}"} for n in pages],
             "local_inspection": {"selected_pages": list(pages)}}
    if sha:
        value["source_sha256"] = sha
    if source:
        value["source_file"] = {"file_name": source}
    return value


def verbatim(tag, *, pages=(1,)):
    """A Veris result as returned, with keys this app has never heard of."""
    return {"request_id": tag, "future_key": {"kept": [1, 2]}, "pages": [
        {"page_number": n, "text": f"{tag} page {n}", "html": "<p>x</p>", "lines": [{"text": "kept", "words": []}]} for n in pages]}


def src(sha, pages=(1,), name="a.pdf"):
    return {"source_file": {"channel": "email", "file_name": name, "selected_pages": list(pages)},
            "local_inspection": {"selected_pages": list(pages)}, "source_sha256": sha, "selected_pages": list(pages)}


def candidate(ocr_data=None):
    return SimpleNamespace(name="Alex", email=None, ocr_data=ocr_data)


def test_second_passport_is_kept_beside_the_first_and_the_first_stays_primary():
    first, second = doc("p1", sha="a"), doc("p2", sha="b")
    person = candidate({"passport": first, "resume": doc("r1")})

    enrich_empty(person, {}, {"passport": second})

    assert person.ocr_data["passport"] == first and person.ocr_data["resume"] == doc("r1")
    assert person.ocr_data["additional_documents"] == [{"passport": second}]


def test_a_new_kind_becomes_primary_and_nothing_else_changes():
    person = candidate({"resume": doc("r1")})

    enrich_empty(person, {}, {"aadhaar": doc("a1")})

    assert person.ocr_data == {"resume": doc("r1"), "aadhaar": doc("a1")}


def test_the_same_read_of_the_same_bytes_is_not_stored_twice_even_with_other_provenance():
    stored = doc("p1", sha="a", source="first.pdf")
    merged = merge_ocr_data({"passport": stored}, {"passport": doc("p1-reread", sha="a", source="resent.pdf")})

    assert merged == {"passport": stored}
    assert merge_ocr_data(merged, {"passport": stored}) == merged


def test_a_reread_of_other_pages_of_the_same_file_is_a_different_document():
    merged = merge_ocr_data({"resume": doc("r1", sha="a", pages=(1,))}, {"resume": doc("r2", sha="a", pages=(1, 2))})

    assert [entry["resume"]["request_id"] for entry in merged["additional_documents"]] == ["r2"]


def test_incoming_additional_documents_are_flattened_not_nested():
    existing = {"passport": doc("p1"), "additional_documents": [{"passport": doc("p2")}]}
    incoming = {"passport": doc("p3"), "additional_documents": [{"passport": doc("p4")}, {"aadhaar": doc("a1")}]}

    merged = merge_ocr_data(existing, incoming)

    assert merged["passport"] == doc("p1")
    assert [(next(iter(e)), next(iter(e.values()))["request_id"]) for e in merged["additional_documents"]] == [
        ("passport", "p2"), ("passport", "p3"), ("passport", "p4")]
    assert merged["aadhaar"] == doc("a1")


def test_merging_does_not_mutate_either_input_and_assigns_nothing_when_unchanged():
    existing, incoming = {"passport": doc("p1")}, {"passport": doc("p2")}
    merge_ocr_data(existing, incoming)
    assert existing == {"passport": doc("p1")} and incoming == {"passport": doc("p2")}

    person = candidate({"passport": doc("p1")})
    original = person.ocr_data
    enrich_empty(person, {}, {"passport": doc("p1")})
    assert person.ocr_data is original


def test_manual_save_may_send_more_than_one_document_of_a_kind():
    body = OcrDataIn.model_validate({"passport": {"a": 1}, "additional_documents": [{"passport": {"a": 2}}]})

    assert body.model_dump(exclude_none=True) == {"passport": {"a": 1}, "additional_documents": [{"passport": {"a": 2}}]}
    with pytest.raises(ValidationError):
        OcrDataIn.model_validate({"additional_documents": [{"visa": {"a": 1}}]})


def test_two_passports_are_both_retained_verbatim_with_their_own_provenance():
    first, second = verbatim("p1"), verbatim("p2")

    merged = merge_ocr_data({}, {"passport": first, "_sources": {"passport": src("a")}})
    merged = merge_ocr_data(merged, {"passport": second, "_sources": {"passport": src("b", name="b.pdf")}})

    assert merged["passport"] == first and merged["_sources"] == {"passport": src("a")}
    assert merged["additional_documents"] == [{"passport": second, "_source": src("b", name="b.pdf")}]
    assert set(merged) == {"passport", "_sources", "additional_documents"}


def test_the_same_source_and_pages_is_not_stored_twice_in_the_new_shape():
    merged = merge_ocr_data({}, {"resume": verbatim("r1"), "_sources": {"resume": src("a")}})

    assert merge_ocr_data(merged, {"resume": verbatim("r1-retry"), "_sources": {"resume": src("a", name="renamed.pdf")}}) == merged
    other_pages = merge_ocr_data(merged, {"resume": verbatim("r2"), "_sources": {"resume": src("a", pages=(1, 2))}})
    assert [e["resume"]["request_id"] for e in other_pages["additional_documents"]] == ["r2"]


def test_new_documents_merge_into_an_old_shape_row_without_touching_it():
    old = {"resume": doc("old-r", sha="a", source="old.pdf"), "passport": doc("old-p", sha="b"),
           "additional_documents": [{"passport": doc("old-p2", sha="c")}]}
    snapshot = copy.deepcopy(old)

    merged = merge_ocr_data(old, {"passport": verbatim("new-p"), "aadhaar": verbatim("new-a"),
                                  "_sources": {"passport": src("d"), "aadhaar": src("d", pages=(2,))}})

    assert old == snapshot
    assert merged["resume"] == snapshot["resume"] and merged["passport"] == snapshot["passport"]
    assert merged["additional_documents"] == [*snapshot["additional_documents"], {"passport": verbatim("new-p"), "_source": src("d")}]
    assert merged["aadhaar"] == verbatim("new-a") and merged["_sources"] == {"aadhaar": src("d", pages=(2,))}
    # The very same file arriving again in the new shape is still recognised against the old row.
    again = merge_ocr_data(old, {"resume": verbatim("reread"), "_sources": {"resume": src("a", pages=(1,))}})
    assert again == old


def test_old_shape_incoming_documents_merge_into_a_new_shape_row():
    row = merge_ocr_data({}, {"passport": verbatim("p1"), "_sources": {"passport": src("a")}})

    merged = merge_ocr_data(row, {"passport": doc("legacy", sha="z")})

    assert merged["passport"] == verbatim("p1")
    assert merged["additional_documents"] == [{"passport": doc("legacy", sha="z")}]


def test_manual_save_accepts_sources_and_stores_them_beside_the_verbatim_documents():
    body = OcrDataIn.model_validate({
        "passport": {"a": 1}, "_sources": {"passport": {"source_sha256": "a", "selected_pages": [2]}},
        "additional_documents": [{"passport": {"a": 2}, "_source": {"source_sha256": "b"}}]})

    assert body.stored() == {
        "passport": {"a": 1}, "_sources": {"passport": {"source_sha256": "a", "selected_pages": [2]}},
        "additional_documents": [{"passport": {"a": 2}, "_source": {"source_sha256": "b"}}]}
    with pytest.raises(ValidationError):
        OcrDataIn.model_validate({"_sources": {"visa": {}}})
