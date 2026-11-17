"""ACCEPTANCE: one folder, several candidates, files NOT renamed. The system must work out from the CONTENT alone who is who.

The scenario (tests/people_fixtures.py, invented people, no database, no network, nothing from backend/.env):

    Tharun Pk Kumar has 10 files, Meera Sundaram 5; ONE of the files is a PDF whose page 1 is Tharun's passport and page 2 is Meera's
    (14 files on disk, 25 pages). Names are scan01.pdf / IMG_0042.jpg / document (3).pdf ...; some files hold several documents of one
    person (resume, passport, offer, Aadhaar, e-visa, medical, police clearance, air ticket, degree, visa stamping), one has a photo
    and a blank sheet.

What must hold (each is a test below):
  1  exactly two people are found                                        (people_by_content.identify_people)
  2  they are named from their documents ("Tharun Pk Kumar", "Meera Sundaram")
  3  every page of every file is accounted for exactly once: placed / unplaced with a reason / unreadable with a reason; counts add up
  4  no page of Tharun is in Meera's set or the other way round (judged against the ground truth)
  5  every page that prints its owner's name is placed (a page with no name may only be placed rightly or reported unplaced)
  6  the mixed-person file is split per person: page 1 to one, page 2 to the other
  7  split_by_content files each page under the right kind, by content, page by page (resume, passport, aadhaar, signed offer, visa,
     medical, pcc, air ticket, visa stamping, education certificate); no page is filed under a document it is not
  8  a photo-only page and a blank page are never lost (accounted for, and inside some piece of the split)
  9  each person's files come out named '<Name>-<Document>.<ext>' with the right documents
 10  only resume / passport / aadhaar / education certificate are queued for the online reader (bulk_candidate_import.SENT_TO_READER and the stored slots)
 11  the whole flow, identification -> cutting each person's pages -> splitting into documents -> naming -> queueing, in one go
 12  the /api/candidate-import/identify endpoint says the same as the function

A module or function the other agents have not delivered yet makes the test SKIP with the reason. Nothing is mocked except the
settings (so the real .env is never read) and the online reader's queue (recorded, never called).

Run (from M:\\crm\\backend, with the project venv; ONE process, RAM is tight):

    .venv\\Scripts\\python.exe -I -m pytest tests/test_people_acceptance.py -v -p no:cacheprovider          # fast text-layer scenario (~15 s)
    set CRM_ACCEPTANCE_SCAN=1 && set OCR_PAGE_WORKERS=1 && .venv\\Scripts\\python.exe -I -m pytest tests/test_people_acceptance.py -v -k scan
                                                                                                        # real OCR on pixel-only pages
Optional: CRM_ACCEPTANCE_SEED=<int> (default 7) reshuffles the folder: file order and names change, the truth does not.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest

import people_fixtures as pf

SCAN_ON = os.environ.get("CRM_ACCEPTANCE_SCAN") == "1"
SEED = int(os.environ.get("CRM_ACCEPTANCE_SEED", "7"))
SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", tesseract_cmd=None,
                           ocr_languages="eng", agency_timezone="Asia/Kolkata", instance_name="test")
ANONYMOUS = set(pf.ANONYMOUS_KINDS)
# A degree certificate is read WITH the resume by design (the page classifier says so), so it may ride in a resume piece.
RIDES_WITH_RESUME = {"education_certificate"}
# What is sent to the online reader: the three identity documents and the education certificates (the owner's decision).
SENT_KINDS = set(pf.READ_KINDS) | RIDES_WITH_RESUME


# ------------------------------------------------------------------------------------------------------ safety + lazy modules

@pytest.fixture(autouse=True)
def never_the_real_settings(monkeypatch):
    """backend/.env is the production tunnel. Whatever the code under test asks for, it gets these inert settings."""
    import app.core.config as config
    original = config.get_settings
    monkeypatch.setattr(config, "get_settings", lambda: SETTINGS)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "get_settings", None) is original:
            monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)


def _need(module_name: str, *attrs: str):
    """Import a module of the system under test, or SKIP the test with the reason when it (or a function in it) is not there yet."""
    with patch("app.core.config.get_settings", return_value=SETTINGS):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name and (module_name == exc.name or module_name.startswith(exc.name + ".")):
                pytest.skip(f"{module_name} is not there yet")
            raise
    for attr in attrs:
        if not hasattr(module, attr):
            pytest.skip(f"{module_name}.{attr} is not there yet")
    return module


def _bulk():
    module = _need("app.services.bulk_candidate_import", "split_by_content", "import_file_name", "READ_SLOTS", "classify_path_kinds",
                   "classify_content", "wants_split", "KIND_LABELS", "name_slug")
    return module


def _pbc():
    return _need("app.services.people_by_content", "identify_people")


# ------------------------------------------------------------------------------------------------------ the scenario

@pytest.fixture(scope="module", params=["text", pytest.param("scan", marks=pytest.mark.skipif(not SCAN_ON, reason="scan mode needs CRM_ACCEPTANCE_SCAN=1 (real OCR, slow)"))])
def scenario(request) -> pf.MixedFolder:
    folder = pf.mixed_folder(SEED, scan_mode=request.param)
    assert (len(folder.files_of("tharun")), len(folder.files_of("meera")), len(folder.files), folder.total_pages) == (10, 5, 14, 25)
    return folder


def _truth_pages(folder: pf.MixedFolder) -> dict[tuple[str, int], tuple[str, str]]:
    return {(name, number): (who, kind) for name, rows in folder.truth.items() for number, who, kind in rows}


@dataclass
class View:
    """What identify_people said, flattened to one row per page."""
    placed: dict = field(default_factory=dict)       # (file, page) -> the result's person key
    unplaced: dict = field(default_factory=dict)     # (file, page) -> reason
    unreadable: dict = field(default_factory=dict)
    listed: list = field(default_factory=list)       # every (file, page) in every list, duplicates kept
    names: dict = field(default_factory=dict)        # person key -> name
    needs_look: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    raw: object = None


def _expand(path: str, pages, count: dict[str, int]) -> list[int]:
    return list(range(1, count.get(path, 1) + 1)) if pages is None else list(pages)


def view_of(result, folder: pf.MixedFolder) -> View:
    """identify_people's PeopleResult (or its to_dict()) as a View. `pages: None` means the whole file."""
    data = result.to_dict() if hasattr(result, "to_dict") else result
    count = {name: len(rows) for name, rows in folder.truth.items()}
    view = View(raw=data, totals=dict(data.get("totals") or {}))
    for person in data["people"]:
        view.names[person["key"]] = person.get("name", "")
        view.needs_look[person["key"]] = bool(person.get("needsLook"))
        for entry in person["files"]:
            for number in _expand(entry["path"], entry.get("pages"), count):
                view.placed[(entry["path"], number)] = person["key"]
                view.listed.append((entry["path"], number))
    for field_name, bucket in (("unplaced", view.unplaced), ("unreadable", view.unreadable)):
        for entry in data.get(field_name, []):
            assert entry.get("reason"), f"a page in {field_name} must say why: {entry}"
            for number in _expand(entry["path"], entry.get("pages"), count):
                bucket[(entry["path"], number)] = entry["reason"]
                view.listed.append((entry["path"], number))
    return view


def _display_tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z]+", text.lower()))


def _name_ok(found: str, expected: str, strict: bool) -> bool:
    """Same words in any order and case. From pixels (scan mode) OCR may glue or add a letter ("Tharuncpk Kumar"; the online reading
    of the passport replaces the name later), so there a near match is accepted: 85% likeness of the letters, order ignored."""
    if _display_tokens(found) == _display_tokens(expected):
        return True
    if strict:
        return False
    from difflib import SequenceMatcher
    squash = lambda text: "".join(sorted(re.findall(r"[a-z]+", text.lower())))   # noqa: E731
    return SequenceMatcher(None, squash(found), squash(expected)).ratio() >= 0.85


class Analysis:
    """Runs the system once per scenario and keeps the answers, so the many assertions do not read the files again and again."""

    def __init__(self, folder: pf.MixedFolder):
        self.folder, self.truth = folder, _truth_pages(folder)
        self._result = self._view = None
        self._pieces: dict = {}

    # -- who is who
    def people(self) -> View:
        if self._view is None:
            pbc = _pbc()
            reader = pbc.LocalReader(None, "eng") if hasattr(pbc, "LocalReader") else None
            try:
                self._result = pbc.identify_people(list(self.folder.files), **({"reader": reader} if reader else {}), workers=1)
            except TypeError:
                self._result = pbc.identify_people(list(self.folder.files))
            self._view = view_of(self._result, self.folder)
        return self._view

    def owner_map(self) -> dict[str, str]:
        """The result's person key -> the ground-truth person it is (by the majority of its pages that carry evidence)."""
        view, votes = self.people(), {}
        for (name, number), key in view.placed.items():
            who, kind = self.truth[(name, number)]
            if kind not in ANONYMOUS:
                votes.setdefault(key, {}).setdefault(who, 0)
                votes[key][who] += 1
        return {key: max(counts, key=counts.get) for key, counts in votes.items()}

    # -- what is on each page
    def pieces(self, name: str, pages: list[int] | None = None):
        """The documents `name` (or just `pages` of it, cut out for one person) is split into, the way the import does it."""
        cache_key = (name, tuple(pages) if pages else None)
        if cache_key not in self._pieces:
            self._pieces[cache_key] = split_file(self.folder, name, pages)
        return self._pieces[cache_key]


@dataclass
class Piece:
    file: str
    kinds: list[str]
    data: bytes
    origin: list[int]            # page numbers in the ORIGINAL file this piece holds
    content_type: str
    path: str                    # the name it is stored under before the "<Name>-<Document>" rename (import_candidate's Upload.path)
    split: bool = False


def _cut(bulk, data: bytes, pages: list[int]) -> bytes:
    if hasattr(bulk, "cut_pdf_pages"):
        return bulk.cut_pdf_pages(data, pages)
    with fitz.open(stream=data, filetype="pdf") as source, fitz.open() as subset:
        for number in sorted(pages):
            subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
        return subset.tobytes(garbage=4, deflate=True)


def split_file(folder: pf.MixedFolder, name: str, pages: list[int] | None = None) -> list[Piece]:
    """Mirror of candidate_import.import_one for ONE file: cut the person's own pages when only some are theirs, then let the content
    decide (split_by_content for a PDF whose name says nothing; classify_content for any other file)."""
    bulk = _bulk()
    data, ctype = folder.data[name], folder.content_type(name)
    total = len(folder.truth[name])
    origin = sorted(pages) if pages else list(range(1, total + 1))
    if pages and origin != list(range(1, total + 1)) and ctype == "application/pdf":
        data = _cut(bulk, data, origin)
    named = bulk.classify_path_kinds(name)
    stem = name.rsplit(".", 1)[0]
    if bulk.wants_split(named, ctype):
        result = bulk.split_by_content(data, ctype, named, name)
        if result.split:
            return [Piece(name, list(piece.kinds), piece.data, [origin[number - 1] for number in piece.pages], ctype, stem + ".pdf", True)
                    for piece in result.pieces]
        kinds = list(result.pieces[0].kinds)
    else:
        kinds = named or bulk.classify_content(data) or ["other"]
    return [Piece(name, kinds, data, origin, ctype, name)]


def _kinds_by_page(pieces: list[Piece]) -> dict[int, set[str]]:
    found: dict[int, set[str]] = {}
    for piece in pieces:
        for number in piece.origin:
            found.setdefault(number, set()).update(piece.kinds)
    return found


@pytest.fixture(scope="module")
def analysis(scenario) -> Analysis:
    return Analysis(scenario)


def _report(lines: list[str]) -> str:
    return "\n  " + "\n  ".join(lines)


# ------------------------------------------------------------------------------------------------------ 1-6 identification

def test_exactly_two_people_are_found(analysis):
    view = analysis.people()
    found = {key: name for key, name in view.names.items()}
    assert len(found) == 2, f"expected exactly 2 people, found {len(found)}: {found}"


def test_the_two_people_are_named_from_their_documents(analysis):
    view, owners = analysis.people(), analysis.owner_map()
    assert len(view.names) == 2, f"expected exactly 2 people, found {view.names}"
    wrong = []
    for key, name in view.names.items():
        who = owners.get(key)
        if who is None or not _name_ok(name, pf.person(who).display, analysis.folder.mode == "text"):
            wrong.append(f"{key} is {who!r} by its pages but is named {name!r}, expected {pf.person(who).display if who else '?'!r}")
    assert not wrong and set(owners.values()) == {"tharun", "meera"}, _report(wrong or [f"people map to {owners}"])


def test_every_page_of_every_file_is_accounted_for_exactly_once(analysis):
    folder, view = analysis.folder, analysis.people()
    every = set(analysis.truth)
    assert len(view.listed) == len(set(view.listed)), "a page is listed twice: " + str(sorted({p for p in view.listed if view.listed.count(p) > 1}))
    assert set(view.listed) == every, ("missing: " + str(sorted(every - set(view.listed))) + "; not in the folder: " + str(sorted(set(view.listed) - every)))
    assert len(view.placed) + len(view.unplaced) + len(view.unreadable) == folder.total_pages
    totals = view.totals
    if totals:   # the result's own totals tell the same story
        assert totals.get("pages") == folder.total_pages and totals.get("files") == len(folder.files), totals
        assert totals.get("assigned", len(view.placed)) + totals.get("unplaced", len(view.unplaced)) + totals.get("unreadable", len(view.unreadable)) == totals["pages"]
        assert (totals.get("assigned"), totals.get("unplaced"), totals.get("unreadable")) == (len(view.placed), len(view.unplaced), len(view.unreadable))


def test_no_page_of_one_person_is_in_the_others_set(analysis):
    view, owners = analysis.people(), analysis.owner_map()
    wrong = [f"{name} p{number}: truth {analysis.truth[(name, number)][0]}/{analysis.truth[(name, number)][1]}, placed with {view.names[key]!r} ({key})"
             for (name, number), key in sorted(view.placed.items()) if owners.get(key) != analysis.truth[(name, number)][0]]
    assert not wrong, _report(wrong)
    assert len(set(owners.values())) == len(owners) == 2, f"the placed pages must make two different people, got {owners}"


def test_every_page_that_prints_its_owners_name_is_placed(analysis):
    view, folder = analysis.people(), analysis.folder
    left = [f"{name} p{number} ({who} {kind}): {view.unplaced.get((name, number)) or view.unreadable.get((name, number))}"
            for name, rows in folder.truth.items() for number, who, kind in rows
            if folder.evidence[name][number - 1] == "direct" and (name, number) not in view.placed]
    assert not left, "pages that carry the person's name but were left out:" + _report(left)


def test_the_mixed_person_file_is_split_per_person(analysis):
    folder, view = analysis.folder, analysis.people()
    name = folder.mixed_file
    assert name and [who for _, who, _ in folder.truth[name]] == ["tharun", "meera"]
    first, second = view.placed.get((name, 1)), view.placed.get((name, 2))
    assert first and second and first != second, f"page 1 -> {first}, page 2 -> {second}: the two pages belong to different people"
    owners = analysis.owner_map()
    assert (owners.get(first), owners.get(second)) == ("tharun", "meera")
    # and each person's own copy holds exactly their page
    for number, who in ((1, "tharun"), (2, "meera")):
        pieces = analysis.pieces(name, pages=[number])
        assert [p.kinds for p in pieces] == [["passport"]] and len(pieces[0].origin) == 1
        if analysis.folder.mode == "text":
            cut = pieces[0].data
            with fitz.open(stream=cut, filetype="pdf") as document:
                text = document[0].get_text().lower()
            assert pf.person(who).passport_no.lower() in text and pf.person("meera" if who == "tharun" else "tharun").passport_no.lower() not in text


# ------------------------------------------------------------------------------------------------------ 7-8 documents by content

def test_each_page_is_filed_under_its_kind_by_content_and_under_no_document_it_is_not(analysis):
    folder, wrong = analysis.folder, []
    for name, rows in folder.truth.items():
        got = _kinds_by_page(analysis.pieces(name))
        for number, who, kind in rows:
            kinds = got.get(number, set())
            if kind in ANONYMOUS:
                continue
            if kind not in kinds:
                wrong.append(f"{name} p{number}: truth {kind}, filed under {sorted(kinds) or 'nothing'}")
            extra = (kinds & set(pf.READ_KINDS)) - {kind} - ({"resume"} if kind in RIDES_WITH_RESUME else set())
            if extra:
                wrong.append(f"{name} p{number}: a {kind} page was also filed as {sorted(extra)} (it would go to the online reader)")
    assert not wrong, _report(wrong)


def test_a_photo_only_page_and_a_blank_page_are_never_lost(analysis):
    folder, view = analysis.folder, analysis.people()
    anonymous = [(name, number, kind) for name, rows in folder.truth.items() for number, _, kind in rows if kind in ANONYMOUS]
    assert {kind for _, _, kind in anonymous} == ANONYMOUS
    lost = []
    for name, number, kind in anonymous:
        if (name, number) not in set(view.listed):
            lost.append(f"{name} p{number} ({kind}) is in neither placed, unplaced nor unreadable")
        if number not in _kinds_by_page(analysis.pieces(name)):
            lost.append(f"{name} p{number} ({kind}) is in no piece of the split")
    assert not lost, _report(lost)


# ------------------------------------------------------------------------------------------------------ 9-10 naming and the online reader

def _truth_grouped(analysis) -> dict[str, list[tuple[str, list[int] | None]]]:
    """Each person's files straight from the ground truth (so naming and queueing are judged on their own, whatever identification did)."""
    out: dict[str, list] = {}
    for name, rows in analysis.folder.truth.items():
        for who in dict.fromkeys(person for _, person, _ in rows):
            mine = [number for number, person, _ in rows if person == who]
            out.setdefault(who, []).append((name, None if len(mine) == len(rows) else mine))
    return out


def _uploads(analysis, person_files: list[tuple[str, list[int] | None]]) -> list[Piece]:
    return [piece for name, pages in person_files for piece in analysis.pieces(name, pages=pages)]


def _stored_names(bulk, display: str, pieces: list[Piece]) -> list[tuple[str, Piece]]:
    """The name each piece is stored under, in the order import_candidate stores them."""
    taken: set[str] = set()
    ordered = sorted(pieces, key=lambda piece: (len(piece.kinds) == 1, piece.path.lower()))
    return [(bulk.import_file_name(display, piece.kinds, piece.path, taken), piece) for piece in ordered]


def _check_names(bulk, analysis, display: str, who: str, pieces: list[Piece]) -> list[str]:
    problems, slug = [], bulk.name_slug(display)
    stored = _stored_names(bulk, display, pieces)
    if len({name.lower() for name, _ in stored}) != len(stored):
        problems.append(f"{display}: two files got the same stored name")
    for stored_name, piece in stored:
        if not re.fullmatch(re.escape(slug) + r"-[A-Za-z0-9_]+(?:-\d+)?\.(?:pdf|jpg)", stored_name):
            problems.append(f"{stored_name!r} is not '<Name>-<Document>.<ext>' for {display!r}")
            continue
        label = stored_name[len(slug) + 1:].rsplit(".", 1)[0]
        for kind in piece.kinds:
            if bulk.name_slug(bulk.KIND_LABELS.get(kind, kind)) not in label:
                problems.append(f"{stored_name!r} does not name its document {kind!r}")
        truth_kinds = {analysis.truth[(piece.file, number)][1] for number in piece.origin}
        real = truth_kinds - ANONYMOUS
        stray = set(piece.kinds) - real - {"other"} - ({"resume"} if real & RIDES_WITH_RESUME else set())
        if stray:
            problems.append(f"{stored_name!r} (from {piece.file} pages {piece.origin}) is named {sorted(stray)} but holds {sorted(truth_kinds)}")
    wanted = {kind for _, _, kind in analysis.folder.pages_of(who)} - ANONYMOUS
    missing = wanted - {kind for _, piece in stored for kind in piece.kinds}
    if missing:
        problems.append(f"{display}: no file is named for {sorted(missing)}")
    return problems


def test_each_persons_files_are_renamed_name_document_with_the_right_documents(analysis):
    bulk, problems = _bulk(), []
    for who, person_files in _truth_grouped(analysis).items():
        problems += _check_names(bulk, analysis, pf.person(who).display, who, _uploads(analysis, person_files))
    assert not problems, _report(problems)


class _Recorder:
    def __init__(self):
        self.queued: list = []

    def __call__(self, db, user, candidate, attachment, content_type):
        self.queued.append(attachment)


def _queue(bulk, display: str, pieces: list[Piece]) -> list[Piece]:
    """The pieces whose stored file would be queued for the online reader: import_candidate's slot bookkeeping, then the real queue_reads."""
    application_form = _need("app.services.application_form")
    recorder, stored, by_key = _Recorder(), {}, {}
    for stored_name, piece in _stored_names(bulk, display, pieces):
        attachment = SimpleNamespace(object_key=stored_name, content_type=piece.content_type, file_name=stored_name)
        by_key[stored_name] = piece
        for kind in piece.kinds:
            if kind in bulk.CANDIDATE_SLOTS or kind == "aadhaar":
                stored[kind] = attachment
    with patch.object(application_form, "queue_reading", recorder):
        bulk.queue_reads(None, None, SimpleNamespace(id="c"), stored)
    return [by_key[attachment.object_key] for attachment in recorder.queued]


def test_only_resume_passport_and_aadhaar_are_queued_for_the_online_reader(analysis):
    bulk, problems = _bulk(), []
    assert set(bulk.READ_SLOTS) == set(pf.READ_KINDS) == {"resume", "passport", "aadhaar"}   # the identity kinds, told apart by content
    assert set(bulk.SENT_TO_READER) == SENT_KINDS == {"resume", "passport", "aadhaar", "education_certificate"}
    for who, person_files in _truth_grouped(analysis).items():
        pieces = _uploads(analysis, person_files)
        queued = _queue(bulk, pf.person(who).display, pieces)
        assert len({id(piece) for piece in queued}) == len(queued), "the same file was queued twice"
        for piece in queued:
            real = {analysis.truth[(piece.file, number)][1] for number in piece.origin} - ANONYMOUS
            if not real & SENT_KINDS:
                problems.append(f"{who}: {piece.file} pages {piece.origin} {sorted(real)} was queued but holds no resume/passport/aadhaar/education certificate")
            leaked = real - SENT_KINDS    # nothing for visa, medical, pcc, air ticket, offer, visa stamping or experience goes online
            if leaked:
                problems.append(f"{who}: {piece.file} pages {piece.origin} was queued and also holds {sorted(leaked)}, which must not go online")
        for slot in pf.READ_KINDS:   # and what must be read is queued: every slot the person has gets one file read
            has = any(kind == slot for _, _, kind in analysis.folder.pages_of(who))
            if has and not any(slot in piece.kinds for piece in queued):
                problems.append(f"{who}: nothing was queued for the {slot}")
        for piece in pieces:
            if not piece.kinds:
                problems.append(f"{piece.file} pages {piece.origin} has no kind at all")
    assert not problems, _report(problems)


# ------------------------------------------------------------------------------------------------------ 11 the whole flow

def _files_of(view: View, key: str) -> list[tuple[str, list[int] | None]]:
    person = next(entry for entry in view.raw["people"] if entry["key"] == key)
    return [(entry["path"], entry.get("pages")) for entry in person["files"]]


def test_the_whole_flow_from_the_mixed_folder_to_each_persons_named_and_queued_files(analysis):
    bulk, view, owners = _bulk(), analysis.people(), analysis.owner_map()
    assert len(view.names) == 2 and len(owners) == 2 and set(owners.values()) == {"tharun", "meera"},         f"the flow needs the two people first: {view.names} -> {owners}"
    problems = []
    for key, name in view.names.items():
        who = owners[key]
        pieces = _uploads(analysis, _files_of(view, key))
        for piece in pieces:                                         # a person's files hold only their pages
            for number in piece.origin:
                owner_of_page, kind = analysis.truth[(piece.file, number)]
                if owner_of_page != who and kind not in ANONYMOUS:
                    problems.append(f"{name}: {piece.file} p{number} is {owner_of_page}'s {kind}")
        problems += _check_names(bulk, analysis, name, who, pieces)
        for piece in _queue(bulk, name, pieces):
            real = {analysis.truth[(piece.file, number)][1] for number in piece.origin} - ANONYMOUS
            if real - SENT_KINDS or not real & SENT_KINDS:
                problems.append(f"{name}: queued {piece.file} pages {piece.origin} holding {sorted(real)}")
    # every page of the folder ends up in somebody's files or in the list of pages the person is shown to place by hand
    assert set(view.placed) | set(view.unplaced) | set(view.unreadable) == set(analysis.truth)
    assert not problems, _report(problems)


# ------------------------------------------------------------------------------------------------------ 12 the endpoint

def test_the_identify_endpoint_says_the_same_as_the_function(analysis):
    if analysis.folder.mode == "scan":
        pytest.skip("the endpoint is exercised in text mode; the scan reading is covered by the function tests")
    _pbc()   # the endpoint is a thin wrapper over identify_people: skip until that exists
    router_module = _need("app.api.routers.candidate_import", "router")
    if not any(getattr(route, "path", "").endswith("/identify") for route in router_module.router.routes):
        pytest.skip("the /identify endpoint is not there yet")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.deps import current_user
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id="u1", role="admin", name="Acceptance")
    folder = analysis.folder
    spec = {"folder": "mixed", "files": [{"path": name} for name, _ in folder.files]}
    response = TestClient(app).post("/api/candidate-import/identify", data={"spec": json.dumps(spec)},
                                    files=[("files", (name, data, folder.content_type(name))) for name, data in folder.files])
    assert response.status_code == 200, response.text
    over_http = view_of(response.json(), folder)
    direct = analysis.people()
    assert set(over_http.placed) == set(direct.placed) and set(over_http.unplaced) == set(direct.unplaced)
    assert sorted(over_http.names.values()) == sorted(direct.names.values())
    assert len(over_http.listed) == folder.total_pages
