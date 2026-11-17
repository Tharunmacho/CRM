"""Finding which person each file and page belongs to, by content (synthetic people; the inspection layer is faked for speed)."""
import json
import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import people_by_content as pbc
from app.services.people_by_content import FileRead, PageRead, file_name_key, identify_people, name_tokens, plausible_name, relation, tail
from identify_fixtures import (
    FakeReader, GARBLED_NAME, aadhaar_number, aadhaar_text, file_of, garbled_aadhaar_text, medical_text, offer_text, page, passport_text,
    pcc_text, pdf_of, resume_continuation, resume_text, ticket_text, unrelated_text, visa_text,
)

THARUN = ("Tharun Kumar", "Raman", "K1234567")
MEERA = ("Meera", "Nair Menon", "P7654321")


def run(reads, **kw):
    kw.setdefault("workers", 1)
    reader = kw.pop("reader", None) or FakeReader(reads)
    result = identify_people([(path, b"x") for path in reads], reader=reader, **kw)
    return result, reader


def by_name(result):
    return {person["name"]: person for person in result.people}


def placed_files(person):
    return {f["path"]: f["pages"] for f in person["files"]}


def assert_accounting(result, reads=None):
    """Every page of every file is in exactly one of assigned / unplaced / unreadable, and the totals add up."""
    totals = result.totals
    assert totals["assigned"] + totals["unplaced"] + totals["unreadable"] == totals["pages"]
    assert totals["files"] == len(result.files)
    counted = {"assigned": 0, "unplaced": 0, "unreadable": 0}
    for entry in result.files:
        numbers = [p["n"] for p in entry["pages"]]
        assert numbers == sorted(set(numbers)) and len(numbers) == entry["pageCount"]       # every page once
        for p in entry["pages"]:
            counted[p["status"]] += 1
            assert (p["person"] is not None) or p["status"] != "assigned"
    assert counted == {k: totals[k] for k in counted}
    # the lists the screen shows say the same as the per-page table
    rows = {"unplaced": 0, "unreadable": 0}
    sizes = {entry["path"]: entry["pageCount"] for entry in result.files}
    for kind in rows:
        for row in getattr(result, kind):
            rows[kind] += len(row["pages"]) if row["pages"] else sizes[row["path"]]
    in_people = sum(sum(len(f["pages"]) if f["pages"] else sizes[f["path"]] for f in p["files"]) for p in result.people)
    # an unreadable page of a person's file is both attached to the person and counted unreadable; nothing else overlaps
    attached_unreadable = sum(len(f["unreadable"]) for p in result.people for f in p["files"])
    assert in_people == totals["assigned"] + attached_unreadable
    assert rows["unplaced"] == totals["unplaced"] and rows["unreadable"] == totals["unreadable"] - attached_unreadable


# ------------------------------------------------------------------ names

def test_names_are_read_conservatively():
    assert name_tokens("Mr. RASHIDKHAN NAWAZKHAN MANSUR S/o NAWAZKHAN") == ("rashidkhan", "nawazkhan", "mansur")
    assert name_tokens("Mr.Tharun Kumar, holder of passport") == ("tharun", "kumar")
    assert name_tokens("K.Sivakumar") == ("k", "sivakumar")
    for junk in ("oe e2 Feass", "Government of India", "12345", "xxxxx"):
        assert name_tokens(junk) is None
    assert not pbc.usable(name_tokens("Mohamed"))            # a bare first name tells nobody apart
    assert pbc.usable(name_tokens("Mohamed Ali"))


def test_name_relations_never_treat_a_shared_first_name_as_the_same_person():
    t = name_tokens
    assert relation(t("Mohamed Ali Khan"), t("Mohamed Asif Khan")) == "weak"
    assert relation(t("Mohamed Ali"), t("Mohamed Asif")) == "weak"
    assert relation(t("Rashid Khan"), t("Rashidkhan Nawazkhan Mansur")) == "contains"      # glued words
    assert relation(t("Rashiid Khan"), t("Rashid Khan")) == "equal"                         # a slip of a letter
    assert relation(t("Mansur Rashidkhan Nawazkhan"), t("Rashidkhan Nawazkhan Mansur")) == "equal"   # order
    assert relation(t("Ramesh Gupta"), t("Sunil Verma")) == "none"


# ------------------------------------------------------------------ the owner's scenario

def tharun_files():
    reads = {}
    reads["scan01.pdf"] = file_of(passport_text(*THARUN), passport_text(*THARUN))
    reads["scan02.pdf"] = file_of(aadhaar_text("Tharun Kumar Raman", 5))
    reads["IMG_2231.jpg"] = file_of(visa_text(THARUN[2]))                 # visa: only the passport number
    reads["scan04.pdf"] = file_of(medical_text(THARUN[2]))                # medical: only the passport number
    reads["scan05.pdf"] = file_of(offer_text("Tharun Kumar Raman"))       # offer letter: only a name
    reads["scan06.pdf"] = file_of(pcc_text("THARUN KUMAR RAMAN", THARUN[2]))
    reads["scan07.pdf"] = file_of(ticket_text("THARUN KUMAR RAMAN"), unrelated_text())
    reads["resume.pdf"] = file_of(page(resume_text("Tharun Kumar Raman", "9876543210"), doc_type="resume"), resume_continuation())
    reads["scan09.pdf"] = file_of(offer_text("Tharun Kumar Raman", THARUN[2]))
    reads["scan10.pdf"] = file_of(aadhaar_text("Tharun Kumar Raman", 5))
    return reads


def meera_files():
    reads = {}
    reads["img1.jpg"] = file_of(passport_text(*MEERA))
    reads["img2.jpg"] = file_of(visa_text(MEERA[2]))
    reads["img3.jpg"] = file_of(medical_text(MEERA[2]))
    reads["doc4.pdf"] = file_of(aadhaar_text("Meera Nair Menon", 9))
    reads["doc5.pdf"] = file_of(page(resume_text("Meera Nair Menon", "9123456780"), doc_type="resume"), resume_continuation())
    return reads


def test_ten_and_five_generic_files_of_two_people_are_told_apart():
    reads = {**tharun_files(), **meera_files()}
    result, _ = run(reads)
    people = by_name(result)
    assert sorted(people) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    assert set(placed_files(people["Tharun Kumar Raman"])) == set(tharun_files())
    assert set(placed_files(people["Meera Nair Menon"])) == set(meera_files())
    assert all(pages is None for p in people.values() for pages in placed_files(p).values())         # whole files
    assert result.unplaced == [] and result.unreadable == []
    assert result.totals == {"files": 15, "pages": 19, "assigned": 19, "unplaced": 0, "unreadable": 0}
    assert not any(p["needsLook"] for p in result.people)
    assert_accounting(result)
    # the visa / medical pages (a passport number and no name) joined by the number alone
    assert "passport number" in people["Meera Nair Menon"]["evidence"]
    # the resume's second page has no evidence of its own: it travels with its file, and is reported as carried
    carried = {f["path"]: f["carried"] for f in people["Meera Nair Menon"]["files"]}
    assert carried["doc5.pdf"] == [2]


def test_the_same_files_give_the_same_answer_in_any_order():
    reads = {**tharun_files(), **meera_files(), "note.pdf": file_of(unrelated_text()), "pic.jpg": file_of(page("", status="blank", method="photo_no_text"))}
    paths = list(reads)
    outputs = []
    for seed in range(6):
        random.Random(seed).shuffle(paths)
        result = identify_people([(p, b"x") for p in paths], reader=FakeReader(reads), workers=1)
        outputs.append(json.dumps(result.to_dict(), sort_keys=True))
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])["people"][0]["key"] == "p1"


def test_two_mohameds_with_different_surnames_stay_apart_and_a_bare_first_name_is_never_given_to_either():
    reads = {
        "a.pdf": file_of(offer_text("Mohamed Ali Khan")),
        "b.pdf": file_of(offer_text("Mohamed Asif Siddiqui")),
        "c.pdf": file_of(offer_text("Mohamed")),                       # only a first name
        "d.pdf": file_of(ticket_text("MOHAMED ALI KHAN")),
        "e.pdf": file_of(ticket_text("MOHAMED ASIF SIDDIQUI")),
    }
    result, _ = run(reads)
    people = by_name(result)
    assert sorted(people) == ["Mohamed Ali Khan", "Mohamed Asif Siddiqui"]
    assert set(placed_files(people["Mohamed Ali Khan"])) == {"a.pdf", "d.pdf"}
    assert set(placed_files(people["Mohamed Asif Siddiqui"])) == {"b.pdf", "e.pdf"}
    assert [(row["path"], row["reason"]) for row in result.unplaced] == [("c.pdf", "Only a first name is on it: not enough to tell who it is.")]
    assert_accounting(result)


def test_a_shorter_name_that_could_be_either_of_two_people_is_left_for_a_human():
    reads = {"a.pdf": file_of(offer_text("Mohamed Ali Khan")), "b.pdf": file_of(offer_text("Mohamed Ali Siddiqui")),
             "c.pdf": file_of(ticket_text("MOHAMED ALI"))}
    result, _ = run(reads)
    assert sorted(by_name(result)) == ["Mohamed Ali Khan", "Mohamed Ali Siddiqui"]
    assert [row["path"] for row in result.unplaced] == ["c.pdf"] and "more than one person" in result.unplaced[0]["reason"]
    # one person only: the shorter spelling joins
    solo, _ = run({"a.pdf": reads["a.pdf"], "c.pdf": reads["c.pdf"]})
    assert len(solo.people) == 1 and set(placed_files(solo.people[0])) == {"a.pdf", "c.pdf"}


def test_a_passport_number_beats_a_spelling_difference_in_the_name():
    reads = {"p.pdf": file_of(passport_text("Rashidkhan Nawazkhan", "Mansur", "W3456789")),
             "v.pdf": file_of("STATE OF KUWAIT\nElectronic Visa\nName: RASHEED KHAAN NAWAZ MAN SUR\nPassport No W3456789")}
    result, _ = run(reads)
    assert len(result.people) == 1 and set(placed_files(result.people[0])) == {"p.pdf", "v.pdf"}
    assert result.people[0]["name"] == "Rashidkhan Nawazkhan Mansur"                  # the machine-readable zone's spelling wins
    # the two names still share words, so nothing is flagged
    assert not result.people[0]["needsLook"]


# ------------------------------------------------------------------ a file with several people

def test_a_pdf_whose_pages_belong_to_different_people_is_assigned_page_by_page():
    mixed = file_of(passport_text(*THARUN), aadhaar_text("Tharun Kumar Raman", 5), passport_text(*MEERA), visa_text(MEERA[2]),
                    unrelated_text(), passport_text(*MEERA)[:0] + offer_text("Tharun Kumar Raman"))
    reads = {"all.pdf": mixed}
    result, _ = run(reads)
    people = by_name(result)
    assert placed_files(people["Tharun Kumar Raman"])["all.pdf"] == [1, 2, 6]
    assert placed_files(people["Meera Nair Menon"])["all.pdf"] == [3, 4]
    assert [(row["path"], row["pages"]) for row in result.unplaced] == [("all.pdf", [5])]      # between two different people: not guessed
    assert_accounting(result)


def test_an_evidenceless_page_between_two_pages_of_one_person_joins_them_by_adjacency_and_a_page_at_the_edge_does_not():
    mixed = file_of(passport_text(*THARUN), unrelated_text(), aadhaar_text("Tharun Kumar Raman", 5), passport_text(*MEERA), unrelated_text())
    result, _ = run({"all.pdf": mixed})
    people = by_name(result)
    assert placed_files(people["Tharun Kumar Raman"])["all.pdf"] == [1, 2, 3]
    assert [row["pages"] for row in result.unplaced] == [[5]]
    adjacent = [f for f in people["Tharun Kumar Raman"]["files"]][0]
    assert adjacent["adjacent"] == [2]
    assert_accounting(result)


def test_a_file_of_one_person_stays_whole_with_its_evidenceless_pages_reported_as_carried():
    reads = {"cv.pdf": file_of(passport_text(*THARUN), unrelated_text(), unrelated_text())}
    result, _ = run(reads)
    person = result.people[0]
    assert person["files"][0]["pages"] is None and person["files"][0]["carried"] == [2, 3]
    assert [p["via"] for p in result.files[0]["pages"]] == ["evidence", "file", "file"]


# ------------------------------------------------------------------ nothing evident

def test_a_nameless_photo_or_blank_page_stays_unplaced_with_a_reason():
    reads = {"a.pdf": file_of(passport_text(*THARUN)), "photo.jpg": file_of(page("", status="blank", method="photo_no_text")),
             "scan.pdf": file_of(unrelated_text()), "blank.pdf": file_of(page("", status="blank", method="rendered_blank"))}
    result, _ = run(reads)
    assert len(result.people) == 1 and set(placed_files(result.people[0])) == {"a.pdf"}
    reasons = {row["path"]: row["reason"] for row in result.unplaced}
    assert reasons["photo.jpg"] == "A picture with no text on it." and reasons["blank.pdf"] == "A blank page."
    assert "No name, number or contact" in reasons["scan.pdf"]
    assert all(row["pages"] is None for row in result.unplaced)
    assert_accounting(result)


def test_only_unplaced_evidence_never_creates_a_person():
    reads = {"a.pdf": file_of(offer_text("Mohamed")), "b.pdf": file_of("Mobile: 9876501234\nSome note about a visit to the site office.")}
    result, _ = run(reads)
    assert result.people == [] and len(result.unplaced) == 2
    assert_accounting(result)


def test_an_agencys_phone_number_never_joins_two_people_but_a_personal_number_does():
    agency = "Contact: 9876500000"
    reads = {"a1.pdf": file_of(passport_text(*THARUN)), "a2.pdf": file_of(offer_text("Tharun Kumar Raman", THARUN[2]) + "\n" + agency),
             "b1.pdf": file_of(passport_text(*MEERA)), "b2.pdf": file_of(offer_text("Meera Nair Menon", MEERA[2]) + "\n" + agency)}
    result, _ = run(reads)
    assert sorted(by_name(result)) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    # a number on the CV and on a form that names nobody else: one person
    own = {"cv.pdf": file_of(page(resume_text("Tharun Kumar Raman", "9876543210"), doc_type="resume")),
           "form.pdf": file_of("Application form\nCandidate mobile: 9876543210\nPosition: Welder")}
    joined, _ = run(own)
    assert len(joined.people) == 1 and set(placed_files(joined.people[0])) == {"cv.pdf", "form.pdf"}
    assert "phone" in joined.people[0]["evidence"]


# ------------------------------------------------------------------ guarantees

def test_a_file_that_cannot_be_read_is_listed_as_unreadable_and_never_raises():
    reads = {"ok.pdf": file_of(passport_text(*THARUN)), "damaged.pdf": FileRead(error="We cannot open this PDF."),
             "boom.pdf": RuntimeError("the reader crashed")}
    result, _ = run(reads, rejected=[("huge.pdf", "The file is over 25 MB and was not read."), ("notes.txt", "Only PDF, Word and image files can be read.")])
    assert len(result.people) == 1
    assert {(row["path"], row["reason"]) for row in result.unreadable} == {
        ("damaged.pdf", "We cannot open this PDF."), ("boom.pdf", "The file could not be read."),
        ("huge.pdf", "The file is over 25 MB and was not read."), ("notes.txt", "Only PDF, Word and image files can be read.")}
    assert result.totals["files"] == 5 and result.totals["unreadable"] == 4                      # one unknown page each
    assert_accounting(result)


def test_the_real_reader_lists_corrupt_unsupported_and_empty_files_instead_of_failing():
    files = [("good.pdf", pdf_of(passport_text(*THARUN))), ("corrupt.pdf", b"%PDF-1.4 this is not a pdf"), ("notes.txt", b"hello"), ("empty.pdf", b"")]
    result = identify_people(files, reader=pbc.LocalReader(), workers=1)
    assert len(result.people) == 1 and result.people[0]["name"] == "Tharun Kumar Raman"
    assert {row["path"] for row in result.unreadable} == {"corrupt.pdf", "notes.txt", "empty.pdf"}
    assert all(row["reason"] for row in result.unreadable)
    assert_accounting(result)


def test_the_real_reader_reads_text_layers_and_is_bounded():
    data = pdf_of(passport_text(*THARUN), aadhaar_text("Tharun Kumar Raman", 5))
    with patch("app.integrations.ocr.inspection.inspect_document", wraps=__import__("app.integrations.ocr.inspection", fromlist=["x"]).inspect_document) as read:
        result = identify_people([("scan.pdf", data), ("other.pdf", pdf_of(passport_text(*MEERA)))], reader=pbc.LocalReader(), workers=1)
    assert sorted(by_name(result)) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    first = read.call_args_list[0].kwargs
    assert first["max_pages"] == 40 and first["page_timeout"] == 8 and first["document_timeout"] == 120
    assert_accounting(result)


def test_a_stopped_reading_lists_what_it_did_not_read():
    reads = {"a.pdf": file_of(passport_text(*THARUN)), "b.pdf": file_of(passport_text(*MEERA))}
    result, reader = run(reads, should_stop=lambda: True)
    assert result.stopped and reader.calls == [] and {r["path"] for r in result.unreadable} == {"a.pdf", "b.pdf"}
    assert_accounting(result)


def test_pages_beyond_what_could_be_read_are_unreadable_not_lost():
    pages = [page(passport_text(*THARUN), 1), page("", 2, status="unread", method="failed", reason="Reading this page went over the time limit.")]
    result, _ = run({"a.pdf": FileRead(pages, 2)})
    assert result.totals == {"files": 1, "pages": 2, "assigned": 1, "unplaced": 0, "unreadable": 1}
    assert result.people[0]["files"][0]["pages"] is None and result.people[0]["files"][0]["unreadable"] == [2]    # still travels with its file
    assert_accounting(result)
    # in a file of several people an unreadable page is not attached to anybody: it is listed for a human
    mixed, _ = run({"m.pdf": FileRead([page(passport_text(*THARUN), 1), page("", 2, status="unread", reason="too faint"),
                                       page(passport_text(*MEERA), 3)], 3)})
    assert [(r["path"], r["pages"], r["reason"]) for r in mixed.unreadable] == [("m.pdf", [2], "too faint")]
    assert_accounting(mixed)


@pytest.mark.parametrize("scenario", range(4))
def test_page_accounting_adds_up_in_many_scenarios(scenario):
    scenarios = [
        {**tharun_files(), **meera_files()},
        {"x.pdf": file_of(passport_text(*THARUN), unrelated_text(), passport_text(*MEERA), page("", status="blank", method="rendered_blank"))},
        {"u.pdf": FileRead(error="bad"), "p.jpg": file_of(page("", status="blank", method="photo_no_text")), "n.pdf": file_of(unrelated_text(), unrelated_text())},
        {"a.pdf": file_of(offer_text("Mohamed"), page("", 2, status="unread", reason="faint")), "b.pdf": file_of(aadhaar_text("A Name Here", 3), medical_text("Z1234567"))},
    ]
    result, _ = run(scenarios[scenario])
    assert_accounting(result)
    assert result.totals["pages"] == sum(max(1, len(r.pages)) for r in scenarios[scenario].values())


# ------------------------------------------------------------------ reading harder before giving up

def faint(number=1):
    return page("", number, status="unread", confidence=0.2, method="tesseract", reason="No readable text")


def test_a_page_with_no_evidence_is_read_again_with_the_full_ladder_and_then_placed():
    reads = {"a.pdf": file_of(passport_text(*THARUN)), "b.pdf": FileRead([PageRead(1, "read", "Medical  rep0rt\nfaint scan", 0.3, "tesseract")], 1)}
    deep = {"b.pdf": {1: PageRead(1, "read", medical_text(THARUN[2]), 0.85, "tesseract", "unknown")}}
    reader = FakeReader(reads, deep)
    result, reader = run(reads, reader=reader)
    assert reader.deep_calls == [("b.pdf", [1])]
    assert len(result.people) == 1 and set(placed_files(result.people[0])) == {"a.pdf", "b.pdf"}
    entry = next(e for e in result.files if e["path"] == "b.pdf")["pages"][0]
    assert entry["deep"] is True and entry["method"].endswith("+deep") and entry["confidence"] == 0.85
    assert_accounting(result)


def test_a_page_that_stays_unreadable_after_the_deeper_read_is_reported_honestly():
    reads = {"a.pdf": file_of(passport_text(*THARUN)), "b.pdf": FileRead([faint()], 1)}
    reader = FakeReader(reads, {"b.pdf": {1: faint()}})
    result, reader = run(reads, reader=reader)
    assert reader.deep_calls == [("b.pdf", [1])]
    assert [(r["path"], r["reason"]) for r in result.unreadable] == [("b.pdf", "No readable text")]
    assert_accounting(result)


def test_the_deeper_read_is_bounded_and_skips_text_layers_photos_and_pages_that_already_have_evidence():
    weak = [PageRead(n, "read", "faint scan text", 0.3, "tesseract") for n in range(1, 11)]
    native = PageRead(11, "read", unrelated_text(), 1.0, "native_text")
    photo = PageRead(12, "blank", "", 1.0, "photo_no_text")
    evident = PageRead(13, "read", passport_text(*THARUN), 0.9, "tesseract", "passport")
    reads = {"a.pdf": FileRead(weak + [native, photo, evident], 13)}
    reader = FakeReader(reads, {})
    _, reader = run(reads, reader=reader)
    (path, numbers), = reader.deep_calls
    assert path == "a.pdf" and len(numbers) == pbc.DEEP_MAX_PAGES and all(n <= 10 for n in numbers)
    # a reader that fails at it (or has no deeper read at all) leaves the first read standing
    class Plain:
        def read(self, path, data):
            return reads[path]
    result = identify_people([("a.pdf", b"x")], reader=Plain(), workers=1)
    assert_accounting(result)


# ------------------------------------------------------------------ strong links only, and conflicts shown

def test_an_aadhaar_number_that_fails_its_check_digit_links_nothing():
    good = aadhaar_number(5)
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    text = lambda number, name: f"Government of India\n{name}\nDOB: 01/01/1990\nMALE\n{number[:4]} {number[4:8]} {number[8:]}"
    reads = {"a.pdf": file_of(text(bad, "Tharun Kumar Raman"), doc_type="aadhaar"), "b.pdf": file_of(text(bad, "Ramesh Gupta Rao"), doc_type="aadhaar")}
    result, _ = run(reads)
    assert sorted(by_name(result)) == ["Ramesh Gupta Rao", "Tharun Kumar Raman"]           # not joined by a number that is not an Aadhaar number
    linked = {"a.pdf": file_of(text(good, "Tharun Kumar Raman"), doc_type="aadhaar"), "b.pdf": file_of(text(good, "Tharun Kumar Raman"), doc_type="aadhaar")}
    one, _ = run(linked)
    assert len(one.people) == 1


def test_passport_numbers_one_character_apart_with_the_same_name_are_one_probable_person_flagged_for_review():
    reads = {"a.pdf": file_of(passport_text("Tharun Kumar", "Raman", "K1234567")),
             "b.pdf": file_of("Medical report\nName: THARUN KUMAR RAMAN\nPassport No. K1234561\nDate of Birth: 01/01/1990")}
    result, _ = run(reads)
    assert len(result.people) == 1
    person = result.people[0]
    assert person["needsLook"] and person["conflicts"][0]["kind"] == "passport_numbers_differ" and person["conflicts"][0]["probable"] is True
    assert {v["tail"] for v in person["conflicts"][0]["values"]} == {"…567", "…561"}
    where = {w["path"] for v in person["conflicts"][0]["values"] for w in v["where"]}
    assert where == {"a.pdf", "b.pdf"}


def test_two_people_with_the_same_name_and_different_passport_numbers_stay_two_and_both_are_flagged():
    reads = {"a.pdf": file_of(passport_text("Mohamed Ali", "Khan", "K1234567")), "b.pdf": file_of(passport_text("Mohamed Ali", "Khan", "P7654321"))}
    result, _ = run(reads)
    assert len(result.people) == 2 and all(p["needsLook"] for p in result.people)
    assert all(p["conflicts"][0]["kind"] == "same_name_other_numbers" for p in result.people)


def test_evidence_that_disagrees_inside_one_person_marks_them_needs_a_look_with_the_exact_pages():
    reads = {"a.pdf": file_of(passport_text("Tharun Kumar", "Raman", "K1234567", birth="900101")),
             "b.pdf": file_of(passport_text("Ramesh", "Gupta", "K1234567", birth="850505"))}     # the same number, another name and date of birth
    result, _ = run(reads)
    assert len(result.people) == 1
    person = result.people[0]
    kinds = {c["kind"] for c in person["conflicts"]}
    assert person["needsLook"] and {"birth_dates_differ", "names_differ"} <= kinds
    names = next(c for c in person["conflicts"] if c["kind"] == "names_differ")
    assert {w["path"] for v in names["values"] for w in v["where"]} == {"a.pdf", "b.pdf"}


def test_no_full_identity_number_ever_appears_in_the_result():
    reads = {**tharun_files(), **meera_files(), "x.pdf": file_of(passport_text("Tharun Kumar", "Raman", "K1234561"))}
    result, _ = run(reads)
    dumped = json.dumps(result.to_dict())
    for number in ("K1234567", "K1234561", "P7654321", aadhaar_number(5), aadhaar_number(9), "9876543210", "9123456780"):
        assert number not in dumped
    assert tail("K1234567") == "…567" and tail("") == ""
    assert all(p["identifiers"]["passport"].startswith("…") and len(p["identifiers"]["passport"]) == 4 for p in result.people)


def test_a_low_confidence_page_placed_only_by_its_neighbours_is_flagged():
    shaky = PageRead(2, "read", unrelated_text(), 0.4, "tesseract")
    mixed = FileRead([page(passport_text(*THARUN), 1), shaky, page(aadhaar_text("Tharun Kumar Raman", 5), 3), page(passport_text(*MEERA), 4)], 4)
    result, _ = run({"m.pdf": mixed})
    person = by_name(result)["Tharun Kumar Raman"]
    assert person["files"][0]["lowConfidence"] == [2] and person["warnings"][0]["kind"] == "low_confidence_placement"
    pages = {p["n"]: p for p in result.files[0]["pages"]}
    assert pages[2]["low"] is True and pages[2]["via"] == "adjacency" and pages[1]["low"] is False
    assert not person["needsLook"]                       # flagged, not blocked


# ------------------------------------------------------------------ the acceptance suite's findings

def test_initials_like_pk_belong_to_the_name_and_section_headings_never_are_one():
    assert name_tokens("Mr. THARUN PK KUMAR, Adult") == ("tharun", "pk", "kumar")
    assert name_tokens("Passenger P K") is None                       # initials alone name nobody
    for heading in ("EDUCATIONAL QUALIFICATION", "Work Experience", "Career Objective", "PERSONAL DETAILS", "Declaration", "Skills",
                    "LANGUAGES KNOWN", "References", "Professional Experience"):
        assert name_tokens(heading) is None, heading
    resume2 = "EDUCATIONAL QUALIFICATION\nBachelor of Commerce, Mysore University, 2019\nPERSONAL DETAILS\nPassport No.: K1234567\nDECLARATION"
    result, _ = run({"cv1.pdf": file_of(page(resume_text("Tharun Kumar Raman", "9876543210"), doc_type="resume"), page(resume2, 2, doc_type="resume")),
                     "pp.pdf": file_of(passport_text(*THARUN))})
    assert [p["name"] for p in result.people] == ["Tharun Kumar Raman"]


def test_a_ticket_names_its_passenger_and_the_name_links_it_to_the_person():
    reads = {"pp.pdf": file_of(passport_text("Tharun Pk", "Kumar", "K1234567")),
             "t.pdf": file_of("Booking Confirmed\nJourney Details\nPassenger Details\nPassenger Name: Mr. THARUN PK KUMAR, Adult\nBaggage Details: Cabin 7kg"),
             "o.pdf": file_of("Dear Mr. Tharun Pk Kumar\nSubject: Employment Offer"),
             "m.pdf": file_of("Detailed candidate report\nName: THARUN PK KUMAR\nMedical examination fit")}
    result, _ = run(reads)
    assert [p["name"] for p in result.people] == ["Tharun Pk Kumar"] and set(placed_files(result.people[0])) == set(reads) and result.unplaced == []


def test_an_aadhaar_back_with_no_name_joins_its_front_and_never_becomes_a_candidate_n():
    number = aadhaar_number(5)
    group = f"{number[:4]} {number[4:8]} {number[8:]}"
    front = f"Government of India\nTharun Pk Kumar\nDOB: 01/01/1990\nMALE\n{group}\nAadhaar - Mera Aadhaar, Meri Pehchan"
    back = f"Unique Identification Authority of India\nAddress:\nS/O Some Father, 12 Example Road\n{group}\nAadhaar is a proof of identity"
    reads = {"pp.pdf": file_of(passport_text("Tharun Pk", "Kumar", "K1234567")), "f.pdf": file_of(front, doc_type="aadhaar"),
             "b.pdf": file_of(back, doc_type="aadhaar")}
    result, _ = run(reads)
    assert [p["name"] for p in result.people] == ["Tharun Pk Kumar"] and set(placed_files(result.people[0])) == set(reads)
    # a card whose name the reader missed joins the one named person with the same date of birth, flagged as resting on that alone
    nameless = {"pp.pdf": reads["pp.pdf"], "card.pdf": file_of(f"Government of India\nDOB: 01/01/1990\n{group}", doc_type="aadhaar")}
    joined, _ = run(nameless)
    assert [p["name"] for p in joined.people] == ["Tharun Pk Kumar"]
    assert joined.people[0]["warnings"][0]["kind"] == "joined_by_birth_date"
    # without anything to join it to, an identity number alone is still a candidate (unnamed, never a made-up name), never lost
    alone, _ = run({"card.pdf": nameless["card.pdf"]})
    assert [p["name"] for p in alone.people] == [f"Unnamed candidate (Aadhaar ending {number[-3:]})"] and alone.totals["assigned"] == 1


def test_a_virtual_id_beside_the_aadhaar_number_is_never_taken_for_a_second_aadhaar_number():
    number, vid = aadhaar_number(5), "7945 2669 8572 4917"
    group = f"{number[:4]} {number[4:8]} {number[8:]}"
    one_line = f"Government of India\n{group}\nVID : {vid}"
    ocr_split = f"Government of India\n{group}\n4917\nVib : 7945 2669 8572"            # the fourth group came out on another line
    for text in (one_line, ocr_split):
        assert pbc.extract(text, "aadhaar").aadhaars == {number}


def test_a_name_read_only_with_low_confidence_never_starts_a_candidate_but_may_join_one():
    blurry = PageRead(1, "read", "Self-Declaration\nName: RASHIOKHAN NAWAZ KHAN MANSUR\nContact no: 9876500871", 0.3, "tesseract")
    result, _ = run({"decl.jpg": FileRead([blurry], 1)})
    assert result.people == [] and "low confidence" in result.unplaced[0]["reason"]
    reads = {"decl.jpg": FileRead([blurry], 1), "pp.pdf": file_of(passport_text("Rashiokhan Nawaz", "Mansur", "K1234567"))}
    joined, _ = run(reads)
    assert len(joined.people) == 1 and set(placed_files(joined.people[0])) == {"decl.jpg", "pp.pdf"}


def test_a_resume_with_a_name_and_an_initial_joins_the_one_person_it_can_be_and_a_label_line_is_not_a_heading():
    assert relation(name_tokens("Gunavelan R"), name_tokens("Gunavelank Ramakrishnan")) == "contains"
    bio = "BIO DATA\n\nName : Gunavelan R\nFather's Name : Ramakrishnan S\nGender : Male\nMarital Status : Single\nDate of Birth : 11.09.2000"
    assert [(h.source, h.tokens) for h in pbc.extract(bio, "resume").names] == [("label", ("gunavelan", "r"))]
    reads = {"cv.pdf": file_of(page(bio, doc_type="resume"), page("Education\nB.Sc Computer Science", 2, doc_type="resume")),
             "pp.pdf": file_of(passport_text("Gunavelank", "Ramakrishnan", "V2345678", birth="000911"))}
    result, _ = run(reads)
    assert len(result.people) == 1 and result.people[0]["name"] == "Gunavelan Ramakrishnan"           # the junk letter of the zone is dropped
    assert set(placed_files(result.people[0])) == {"cv.pdf", "pp.pdf"}


def test_a_person_with_no_readable_name_needs_a_look():
    result, _ = run({"card.pdf": file_of(f"Government of India\n{aadhaar_number(5)[:4]} {aadhaar_number(5)[4:8]} {aadhaar_number(5)[8:]}", doc_type="aadhaar")})
    person = result.people[0]
    assert person["name"] == f"Unnamed candidate (Aadhaar ending {aadhaar_number(5)[-3:]})" and person["needsLook"] and person["conflicts"][0]["kind"] == "no_name"


def test_the_spelling_most_pages_agree_on_wins_over_a_machine_zone_misread_once():
    # an OCR'd zone read the filler between two given names as a letter: "THARUNKPK"; the card, the letter and the CV spell it "Tharun Pk"
    reads = {"pp.pdf": file_of(passport_text("Tharunkpk", "Kumar", "K1234567")), "card.pdf": file_of(aadhaar_text("Tharun Pk Kumar", 5), doc_type="aadhaar"),
             "offer.pdf": file_of(offer_text("Tharun Pk Kumar", "K1234567")), "cv.pdf": file_of(page(resume_text("Tharun Pk Kumar", "9876543210"), doc_type="resume"))}
    result, _ = run(reads)
    assert [p["name"] for p in result.people] == ["Tharun Pk Kumar"]


def test_two_files_with_the_same_path_stay_two_files_and_every_page_is_accounted_for():
    reads = {"same.pdf": file_of(passport_text(*THARUN), aadhaar_text("Tharun Kumar Raman", 5))}
    reader = FakeReader(reads)
    result = identify_people([("same.pdf", b"one"), ("same.pdf", b"two")], reader=reader, workers=1)
    assert reader.calls == ["same.pdf", "same.pdf"]                  # both were read, under the path the caller gave
    assert result.totals["files"] == 2 and result.totals["pages"] == 4
    assert result.totals["assigned"] + result.totals["unplaced"] + result.totals["unreadable"] == 4
    assert [entry["path"] for entry in result.files] == ["same.pdf", "same.pdf"]
    assert "\x00" not in json.dumps(result.to_dict())               # the internal key never leaks out


# ------------------------------------------------------------------ is it a person's name at all? (OCR junk is never a name)

PLAUSIBLE = [
    "Gunavelan Ramakrishnan", "Rashidkhan Nawazkhan Mansur", "Tharun Pk Kumar", "Meera Sundaram", "Mohamed Ali", "S. Dinesh Kumar",
    "K. Sivakumar", "Gunavelan R", "Thirunavukkarasu", "Meenakshisundaram Iyer", "Ramesh Gupta", "Sunil Verma", "Abdul Rahman",
    "Divya Menon", "Vijayalakshmi Venkataraman", "Sathyanarayanan", "Arumugam Selvaraj", "Aadhavan", "Poovarasan",
    "Veeraraghavan Krishnamoorthy", "Sooriyakumar", "Muthuraman", "Jeevanandham", "Karthikeyan R", "Priyadharshini", "Anandakumar",
    "Ravichandran", "Lakshmanan", "Harikrishnan Nair", "Fathima Beevi", "Gurpreet Singh", "Mary Joseph", "Thomas Varghese",
    "Nithyanandam", "Soorya Prakash", "Aravindhan", "Thiruvengadam", "Kaliyaperumal", "Seeniammal", "Raghavendra Rao",
    "Subramaniam Swamy", "Abdul Kalam", "Shahid Afridi", "Parvathi Ammal", "Vaiyapuri", "Manikandan P", "Dhanalakshmi S",
    "Ayyappan Nair", "Muthusamy", "Sivasubramanian", "Narayanan Unni", "Mohammed Rafi", "Syed Ibrahim", "John Peter",
    "Aaron Fernandes", "Tamilselvi", "Pandiarajan", "Easwaramoorthy", "Gnanaprakasam", "Thangapandian", "Chidambaram",
    "GUNAVELAN RAMAKRISHNAN", "gunavelan ramakrishnan", "Mansur Rashidkhan Nawazkhan", "P. K. Kumar Raman", "Ali",
]
IMPLAUSIBLE = [
    "Aaharapef Dio Tdliadledf", "Aaharapef", "Tdliadledf", "Gonsrmets Gauche", "Epurhes", "Aufwniication", "Xqzvtkpl", "Qwertyui",
    "Bnmqwrt", "Aaaaaa", "Zxcvbnmaa", "Asdfghjkl", "AaHarapEf", "aoe e2", "G0vernment", "12345", "Rrrrrrr", "Ssssss", "Sbhxdkrt",
    "Caypalabratay", "Frowinicteerey", "Aieniiy Eanah Seteog", "Airyeqerw", "Aomgseogo", "Doladaahaerapp", "குணவேலன்",
    "Gunavelan குணவேலன்", "Gunavelan123", "Mr.", "K", "P K", "Ab", "One Two Three Four Five Six", "Aaaaaaaaaaaaaaaaaaaaaaaaa",
    "Bcdfghjkl", "Cifverabipa", "Efehany", "Horletre Veeruherrie", "Funeerneed Ween", "Gunavelan@home", "Tttharun", "", "   ",
    "Gunavelan (o)", "Aadhaar No 2489",
]


def test_plausible_name_accepts_real_names_of_every_kind():
    assert len(PLAUSIBLE) >= 30
    assert [name for name in PLAUSIBLE if not plausible_name(name)] == []


def test_plausible_name_rejects_ocr_junk_and_non_names():
    assert len(IMPLAUSIBLE) >= 30
    assert [name for name in IMPLAUSIBLE if plausible_name(name)] == []


def test_a_garbled_name_line_is_never_a_name_but_is_remembered_as_printed():
    assert name_tokens("Aaharapef dio tdliadledf(o)") is None
    sink: list = []
    assert name_tokens("Aaharapef dio tdliadledf(o)", sink=sink) is None and sink == ["aaharapef dio tdliadledf"]
    ev = pbc.extract(garbled_aadhaar_text(5), "aadhaar")
    assert ev.names == [] and ev.garbled == 1 and ev.aadhaars == {aadhaar_number(5)}
    # a machine-readable zone with correct check digits is not guessed from how it sounds
    zone = pbc.extract(passport_text("Gunavelank", "Ramakrishnan", "V2345678"))
    assert [h.tokens for h in zone.names if h.source == "passport"] == [("gunavelank", "ramakrishnan")]


def test_the_name_at_the_start_of_a_file_name_is_read_by_the_folder_import_rules():
    assert file_name_key("Gunavelan-aadhar.jpeg") == ("gunavelan",)
    assert file_name_key("folder/sub/Gunavelan-signed offer,pp,aadhar.pdf") == ("gunavelan",)
    assert file_name_key("Gunavelan.pdf") == ("gunavelan",)
    assert file_name_key("K.Sivakumar-offer.jpg") == ("sivakumar",)
    assert file_name_key("Rashid khan - PP.pdf") == ("rashid", "khan")
    for nobody in ("IMG_0042.jpg", "Scan_0001.pdf", "EVisa-123456789.pdf", "Itinerary AB1CD2 (1).pdf", "CamScanner 04-10-2026.pdf", "Ravi-PP.pdf",
                   "passport.pdf", "New Doc 6.pdf", "DOC00002.pdf"):
        assert file_name_key(nobody) is None, nobody


# ------------------------------------------------------------------ one candidate whose files all say the same name (the Gunavelan folder)

GUNA = ("Gunavelan", "Ramakrishnan", "V2345678")
GUNA_BIO = "BIO DATA\n\nName : Gunavelan R\nFather's Name : Ramakrishnan S\nGender : Male\nMarital Status : Single\nDate of Birth : 01.01.1990"
OWN_NAMES = ["Gunavelan-PP.pdf", "Gunavelan-aadhar.jpeg", "Gunavelan-aadhar.pdf", "Gunavelan-signed offer,pp,aadhar.pdf",
             "Gunavelan-signed offer.pdf", "Gunavelan.pdf"]
NEUTRAL_NAMES = ["scan01.pdf", "IMG_0002.jpeg", "scan03.pdf", "Scan_0004.pdf", "DOC00005.pdf", "New Doc 6.pdf"]


def guna_reads(names: list[str]) -> dict:
    """The six files of the failing folder, in invented text: a passport scan, two copies of a bilingual Aadhaar whose English name
    OCR made into junk, a 4-page scan (offer letter to 'Gunavellan', passport, a photo page, the Aadhaar), the offer letter alone, and a
    5-page resume file (resume, passport, passport photo)."""
    def card(conf):
        return page(garbled_aadhaar_text(7), doc_type="aadhaar", confidence=conf, method="tesseract")

    def photo():
        return page("", doc_type="aadhaar", confidence=0.29, method="tesseract")

    def pp(conf=0.43):
        return page(passport_text(*GUNA), doc_type="passport", confidence=conf, method="tesseract")

    return {
        names[0]: file_of(pp(), photo()),
        names[1]: file_of(card(0.5)),
        names[2]: file_of(card(0.37)),
        names[3]: file_of(page(offer_text("Gunavellan"), confidence=0.93, method="tesseract"), pp(), photo(), card(0.31)),
        names[4]: file_of(page(offer_text("Gunavellan"), confidence=0.93, method="tesseract")),
        names[5]: file_of(page(GUNA_BIO, doc_type="resume"), page(resume_continuation(2), doc_type="resume", confidence=0.67, method="tesseract"),
                          pp(0.63), page("", doc_type="passport", confidence=0.46, method="tesseract"), page("", status="blank", method="photo_no_text", confidence=0.35)),
    }


def via(result, path):
    return {p["n"]: p["via"] for entry in result.files if entry["path"] == path for p in entry["pages"]}


def test_the_failing_folder_is_one_candidate_with_every_file_placed_and_no_junk_person():
    result, _ = run(guna_reads(OWN_NAMES))
    assert [p["name"] for p in result.people] == ["Gunavelan Ramakrishnan"]
    person = result.people[0]
    assert result.totals == {"files": 6, "pages": 14, "assigned": 14, "unplaced": 0, "unreadable": 0}
    assert result.unplaced == [] and result.unreadable == []
    assert set(placed_files(person)) == set(OWN_NAMES) and all(pages is None for pages in placed_files(person).values())
    assert "Aaharapef" not in json.dumps(result.to_dict()) and "aaharapef" not in json.dumps(result.to_dict())
    # how each page was placed: by what is on it, by the file name (the Aadhaar cards, the offer letters), carried with its file (photo pages)
    assert via(result, "Gunavelan-aadhar.jpeg") == {1: "filename"} and via(result, "Gunavelan-aadhar.pdf") == {1: "filename"}
    assert via(result, "Gunavelan-signed offer,pp,aadhar.pdf") == {1: "filename", 2: "evidence", 3: "file", 4: "filename"}
    assert via(result, "Gunavelan-signed offer.pdf") == {1: "filename"}
    assert via(result, "Gunavelan.pdf") == {1: "evidence", 2: "file", 3: "evidence", 4: "file", 5: "file"}
    carried = {f["path"]: f["carried"] for f in person["files"]}
    assert carried["Gunavelan-signed offer,pp,aadhar.pdf"] == [3] and carried["Gunavelan.pdf"] == [2, 4, 5]
    warning = next(w for w in person["warnings"] if w["kind"] == "placed_by_file_name")
    assert warning["message"] == "5 pages were placed by their file name: check."
    assert sorted((w["path"], tuple(w["pages"])) for w in warning["where"]) == [
        ("Gunavelan-aadhar.jpeg", (1,)), ("Gunavelan-aadhar.pdf", (1,)), ("Gunavelan-signed offer,pp,aadhar.pdf", (1, 4)), ("Gunavelan-signed offer.pdf", (1,))]
    assert "file name" in person["evidence"] and not person["needsLook"]
    assert_accounting(result)


def test_the_same_folder_with_neutral_file_names_gets_one_candidate_and_one_flagged_aadhaar_card_never_a_junk_person():
    result, _ = run(guna_reads(NEUTRAL_NAMES))
    assert [p["name"] for p in result.people] == ["Gunavelan Ramakrishnan"] and "aaharapef" not in json.dumps(result.to_dict()).lower()
    person = result.people[0]
    ending = tail(aadhaar_number(7))
    reasons = {(row["path"], tuple(row["pages"] or ()), row["reason"]) for row in result.unplaced}
    assert reasons == {(NEUTRAL_NAMES[1], (), f"This Aadhaar card (ending {ending}) has no readable name."),
                       (NEUTRAL_NAMES[2], (), f"This Aadhaar card (ending {ending}) has no readable name."),
                       (NEUTRAL_NAMES[3], (4,), f"This Aadhaar card (ending {ending}) has no readable name.")}
    assert all(row["kind"] == "aadhaar_no_name" and row["ending"] == ending for row in result.unplaced)       # the screen offers a choice of person
    # the offer letters only say "Gunavellan": one person in the folder has that first name (with a slip), so they are placed, flagged as probable
    assert via(result, NEUTRAL_NAMES[3])[1] == "firstname" and via(result, NEUTRAL_NAMES[4]) == {1: "firstname"}
    assert next(w for w in person["warnings"] if w["kind"] == "placed_by_first_name")["message"] == "2 pages were placed by a first name only: check."
    assert all(page_["probable"] for entry in result.files for page_ in entry["pages"] if page_["via"] == "firstname")
    assert {f["path"]: f["probable"] for f in person["files"] if f["probable"]} == {NEUTRAL_NAMES[3]: [1], NEUTRAL_NAMES[4]: [1]}
    assert via(result, NEUTRAL_NAMES[3])[3] == "file"            # the photo page travels with its file
    assert result.totals == {"files": 6, "pages": 14, "assigned": 11, "unplaced": 3, "unreadable": 0}
    assert_accounting(result)


def test_a_garbled_card_that_is_the_only_cluster_stays_a_flagged_candidate_named_by_its_number_never_by_its_junk():
    for confidence, method in ((0.4, "tesseract"), (1.0, "native_text")):         # junk is junk however sure the reader was
        result, _ = run({"card.pdf": file_of(page(garbled_aadhaar_text(5), doc_type="aadhaar", confidence=confidence, method=method))})
        assert [p["name"] for p in result.people] == [f"Unnamed candidate (Aadhaar ending {aadhaar_number(5)[-3:]})"]
        person = result.people[0]
        assert person["needsLook"] and person["nameSource"] == "" and person["conflicts"][0]["kind"] == "no_name"
        assert "aaharapef" not in json.dumps(result.to_dict()).lower() and result.totals["assigned"] == 1
    # one such card per person when several unnamed cards are all there is
    two, _ = run({"a.pdf": file_of(page(garbled_aadhaar_text(5), doc_type="aadhaar")), "b.pdf": file_of(page(garbled_aadhaar_text(6, "Eanah seteog"), doc_type="aadhaar"))})
    assert sorted(p["name"] for p in two.people) == sorted(f"Unnamed candidate (Aadhaar ending {aadhaar_number(s)[-3:]})" for s in (5, 6))


def test_a_garbled_card_with_the_same_number_as_a_named_persons_page_joins_that_person():
    number = aadhaar_number(7)
    group = f"{number[:4]} {number[4:8]} {number[8:]}"
    reads = {"a.pdf": file_of(f"Government of India\nGunavelan Ramakrishnan\nDOB: 11/09/2000\nMALE\n{group}", doc_type="aadhaar"),
             "b.pdf": file_of(page(garbled_aadhaar_text(7), doc_type="aadhaar", confidence=0.4, method="tesseract"))}
    result, _ = run(reads)
    assert [p["name"] for p in result.people] == ["Gunavelan Ramakrishnan"] and set(placed_files(result.people[0])) == {"a.pdf", "b.pdf"}
    assert via(result, "b.pdf") == {1: "evidence"} and result.unplaced == [] and result.people[0]["warnings"] == []


def test_a_first_name_with_a_typo_is_placed_only_when_exactly_one_person_fits():
    persons = {"a.pdf": file_of(passport_text("Gunavelan", "Ramakrishnan", "V2345678")), "o.pdf": file_of(offer_text("Gunavellan"))}
    one, _ = run(persons)
    assert [p["name"] for p in one.people] == ["Gunavelan Ramakrishnan"] and one.unplaced == [] and via(one, "o.pdf") == {1: "firstname"}
    two, _ = run({**persons, "b.pdf": file_of(passport_text("Gunavelan", "Sundaresan", "W1234567"))})
    assert sorted(p["name"] for p in two.people) == ["Gunavelan Ramakrishnan", "Gunavelan Sundaresan"]
    assert [(row["path"], row["reason"]) for row in two.unplaced] == [("o.pdf", "Only a first name is on it: not enough to tell who it is.")]
    assert all(not any(w["kind"] == "placed_by_first_name" for w in p["warnings"]) for p in two.people)
    # a first name that is nobody's in the folder places nothing
    other, _ = run({"a.pdf": persons["a.pdf"], "o.pdf": file_of(offer_text("Ravindra"))})
    assert [row["path"] for row in other.unplaced] == ["o.pdf"]
    assert_accounting(two)


def test_two_people_in_one_folder_with_their_names_in_the_file_names_are_still_told_apart():
    reads = {"Tharun-PP.pdf": file_of(page(passport_text(*THARUN), doc_type="passport")),
             "Tharun-aadhar.pdf": file_of(page(garbled_aadhaar_text(11), doc_type="aadhaar", confidence=0.4, method="tesseract")),
             "Tharun-offer.pdf": file_of(offer_text("Tharun")),
             "Meera-PP.pdf": file_of(page(passport_text(*MEERA), doc_type="passport")),
             "Meera-aadhar.pdf": file_of(page(garbled_aadhaar_text(12, "Eanah seteog horletre"), doc_type="aadhaar", confidence=0.4, method="tesseract")),
             "Meera-offer.pdf": file_of(offer_text("Meera"))}
    result, _ = run(reads)
    people = by_name(result)
    assert sorted(people) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    assert set(placed_files(people["Tharun Kumar Raman"])) == {"Tharun-PP.pdf", "Tharun-aadhar.pdf", "Tharun-offer.pdf"}
    assert set(placed_files(people["Meera Nair Menon"])) == {"Meera-PP.pdf", "Meera-aadhar.pdf", "Meera-offer.pdf"}
    assert result.unplaced == [] and not any(p["needsLook"] for p in result.people)
    assert via(result, "Tharun-aadhar.pdf") == {1: "filename"} and via(result, "Meera-aadhar.pdf") == {1: "filename"}
    assert_accounting(result)
    # with names that say nothing, the two cards wait for a person, each with its own ending
    neutral = {f"scan{n}.pdf": value for n, value in enumerate(reads.values(), 1)}
    again, _ = run(neutral)
    assert sorted(p["name"] for p in again.people) == ["Meera Nair Menon", "Tharun Kumar Raman"]
    assert sorted(row["ending"] for row in again.unplaced if row.get("kind") == "aadhaar_no_name") == sorted(tail(aadhaar_number(s)) for s in (11, 12))
    assert_accounting(again)


def test_a_file_name_never_beats_an_identity_number_that_says_otherwise():
    number = aadhaar_number(3)
    group = f"{number[:4]} {number[4:8]} {number[8:]}"
    reads = {"Gunavelan-PP.pdf": file_of(page(passport_text(*GUNA), doc_type="passport")),
             "Gunavelan-card.pdf": file_of(f"Government of India\nGunavelan Ramakrishnan\nDOB: 01/01/1990\nMALE\n{group}", doc_type="aadhaar"),
             "Gunavelan-aadhar-2.pdf": file_of(page(garbled_aadhaar_text(4), doc_type="aadhaar", confidence=0.4, method="tesseract"))}
    result, _ = run(reads)
    assert [p["name"] for p in result.people] == ["Gunavelan Ramakrishnan"]
    person = result.people[0]
    assert set(placed_files(person)) == {"Gunavelan-PP.pdf", "Gunavelan-card.pdf"}              # the second card has another number: kept apart
    assert [(row["path"], row["kind"]) for row in result.unplaced] == [("Gunavelan-aadhar-2.pdf", "aadhaar_no_name")]
    mismatch = next(c for c in person["conflicts"] if c["kind"] == "file_name_id_mismatch")
    assert person["needsLook"] and mismatch["values"][0]["where"] == [{"path": "Gunavelan-aadhar-2.pdf", "pages": [1]}]
    assert aadhaar_number(4) not in json.dumps(result.to_dict())
    assert_accounting(result)


def test_when_every_file_names_one_person_another_plausible_name_is_never_pulled_in():
    reads = {"Gunavelan-PP.pdf": file_of(page(passport_text(*GUNA), doc_type="passport")),
             "Gunavelan-ticket.pdf": file_of(ticket_text("Ravi Shankar")),
             "Gunavelan-aadhar.pdf": file_of(page(garbled_aadhaar_text(7), doc_type="aadhaar", confidence=0.4, method="tesseract"))}
    result, _ = run(reads)
    assert sorted(p["name"] for p in result.people) == ["Gunavelan Ramakrishnan", "Ravi Shankar"]
    assert set(placed_files(by_name(result)["Gunavelan Ramakrishnan"])) == {"Gunavelan-PP.pdf", "Gunavelan-aadhar.pdf"}
    assert set(placed_files(by_name(result)["Ravi Shankar"])) == {"Gunavelan-ticket.pdf"}
    assert_accounting(result)


def test_a_file_with_nothing_on_any_page_belongs_to_the_one_person_its_name_says_and_a_nameless_one_stays_unplaced():
    reads = {"Gunavelan-PP.pdf": file_of(page(passport_text(*GUNA), doc_type="passport")),
             "Gunavelan-photo.jpg": file_of(page("", status="blank", method="photo_no_text", confidence=0.3)),
             "IMG_0042.jpg": file_of(page("", status="blank", method="photo_no_text", confidence=0.3))}
    result, _ = run(reads)
    assert set(placed_files(result.people[0])) == {"Gunavelan-PP.pdf", "Gunavelan-photo.jpg"} and via(result, "Gunavelan-photo.jpg") == {1: "filename"}
    assert [row["path"] for row in result.unplaced] == ["IMG_0042.jpg"]
    assert_accounting(result)


def test_the_gunavelan_folder_gives_the_same_answer_in_any_order():
    reads = guna_reads(OWN_NAMES)
    paths = list(reads)
    outputs = set()
    for seed in range(5):
        random.Random(seed).shuffle(paths)
        outputs.add(json.dumps(identify_people([(p, b"x") for p in paths], reader=FakeReader(reads), workers=1).to_dict(), sort_keys=True))
    assert len(outputs) == 1


def test_a_name_in_a_file_name_that_fits_two_people_places_nothing():
    reads = {"a.pdf": file_of(passport_text("Mohamed", "Ali Khan", "K1234567")), "b.pdf": file_of(passport_text("Mohamed", "Asif Siddiqui", "P7654321")),
             "Mohamed-aadhar.pdf": file_of(page(garbled_aadhaar_text(9), doc_type="aadhaar", confidence=0.4, method="tesseract"))}
    result, _ = run(reads)
    assert sorted(p["name"] for p in result.people) == ["Mohamed Ali Khan", "Mohamed Asif Siddiqui"]
    assert [row["path"] for row in result.unplaced] == ["Mohamed-aadhar.pdf"]
    assert_accounting(result)
