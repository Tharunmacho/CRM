"""Bulk import of candidates who already went through the whole process (a folder of documents per person).

Typing 30 finished placements by hand means 30 candidate forms, 30 x 12 uploads and 30 x 12 stage screens. This module is the
shared brain behind the "Import candidates" screen so that work becomes: pick the folder, fill one sheet, press import.

Three pieces, all pure except where a database is passed in:

* `classify_path` - which document a file is, from its name (and the folders it sits in);
* `read_manifest` - the stage sheet (.xlsx / .csv): one row per candidate, normalised to canonical keys;
* `plan_candidate` / `import_candidate` - the dry run and the real thing. Both run the *same* stage rules as the stage
  screens (`app.domain.stages`), so an imported placement can never be one the stage screens would have refused.

An imported placement is recorded as history: every stage row carries the date the sheet gives (never "today" unless no date
exists), no stage tasks are created and nothing counts towards today's daily work.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from time import perf_counter   # (``time`` here is datetime.time)
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from difflib import SequenceMatcher
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate, CandidateStatus, Order,
    OrderStatus, Role, Source, Task, TaskKind, User,
)
from app.domain.stages import ADVANCE_STAGE, PASSPORT_STAGE, STAMPING_STAGE, TOTAL_STAGES, StageOptions, stage_by_no, validate_payload
from app.services import document_storage
from app.services.application_form import SLOT_LABELS, SLOT_SPECS, deployment_gaps
from app.services.invoice_access import can_see_invoices
from app.services.candidate_identity import identity_matches, normalize_candidate_values, normalize_email, normalize_passport, normalize_phone

# --------------------------------------------------------------------------------------------- document kinds

ADVANCE_RECEIPT = "advance_receipt"
SOURCE_BUNDLE = "source_bundle"   # the scan as received, kept when pages cut from it were not filed again (Source document evidence)
# Every document the folder can hold: the form's own slots, plus the stage 9 receipt that is not a candidate document.
KIND_LABELS: dict[str, str] = {**SLOT_LABELS, ADVANCE_RECEIPT: "Advance receipt (stage 9)", "aadhaar": "Aadhaar card"}
CANDIDATE_SLOTS = tuple(spec[0] for spec in SLOT_SPECS)
READ_SLOTS = ("resume", "passport", "aadhaar")  # the three identity documents: pages of these kinds are told apart by content
# Only these slots supply candidate form fields. Other document types are sorted and filed locally.
SENT_TO_READER = tuple(spec[0] for spec in SLOT_SPECS if spec[5])
IMAGE_ONLY = ("photo",)
# Candidate document -> (stage, field) whose evidence it is. The same stored file is attached to both places.
STAGE_FILES: dict[str, tuple[int, str]] = {
    "signed_offer": (5, "file"), "pcc": (7, "file"), ADVANCE_RECEIPT: (9, "receipt"), "visa": (10, "file"),
    "visa_stamping": (11, "file"), "air_ticket": (12, "file"),
}
SUPPORTED_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".doc", ".docx")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic")

# (kind, substrings that match anywhere in the squashed name, whole words). Order matters: the first rule that matches wins,
# so the more specific names ("visa stamping", "passport size photo") come before the general ones ("visa", "passport").
_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("photo", ("photo", "picture", "selfie", "passportsize", "ppsize"), ("pic",)),
    ("application_form", ("applicationform", "jobapplication"), ()),
    ("undertaking_letter", ("undertaking",), ()),
    ("insurance", ("insurance",), ()),
    ("invoice", ("invoice",), ()),
    (ADVANCE_RECEIPT, ("receipt", "advance", "payment", "servicecharge"), ()),
    ("visa_stamping", ("stamp",), ("vfs",)),
    ("signed_offer", ("offer", "appointment", "contract"), ("loa",)),
    ("air_ticket", ("ticket", "flight", "itinerary", "boarding"), ("pnr", "pax")),
    ("google_review", ("google", "review"), ()),
    ("emigration", ("emigra", "protector"), ("ecr", "ecnr", "pge")),
    ("pcc", ("police", "clearance"), ("pcc", "pvc")),
    ("medical", ("medical", "gamca", "health"), ("fit", "hc")),
    # "visa" only as a whole word: a candidate called Visakh must not turn every one of his files into a visa.
    ("visa", ("workpermit", "visaletter", "visacopy", "visascan", "visaapproval", "visamedical"), ("visa", "visas", "evisa", "workvisa")),
    ("education_certificate", ("education", "qualification", "degree", "diploma", "marksheet", "marklist"), ("sslc", "hsc", "iti", "ssc")),
    ("experience_certificate", ("experience",), ("exp",)),
    ("passport", ("passport",), ("ppt", "pp")),
    ("resume", ("resume", "curriculum", "biodata"), ("cv",)),
    ("aadhaar", ("aadhaar", "aadhar", "adhar"), ()),
    ("other", ("pancard",), ("pan", "id")),
)


def _words(text: str) -> tuple[str, list[str]]:
    lowered = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()  # AirTicket -> air ticket
    pieces = [piece for piece in re.split(r"[^a-z]+", lowered) if piece]
    return "".join(pieces), pieces


# Office habit: a CV is saved under the trade it was written for ("K.Sivakumar-Steel fixer.pdf"). Only used when nothing else matches.
_TRADES = frozenset({"fixer", "welder", "fitter", "mason", "carpenter", "electrician", "plumber", "painter", "rigger", "foreman",
                     "driver", "helper", "cook", "chef", "mechanic", "operator", "scaffolder", "labourer", "laborer", "technician",
                     "supervisor", "engineer", "steward", "cleaner", "gardener", "pipefitter", "shuttering", "bartender", "waiter"})


def _kinds_in(text: str) -> list[str]:
    squashed, words = _words(text)
    found = [kind for kind, substrings, tokens in _RULES
             if any(sub in squashed for sub in substrings) or any(token in words for token in tokens)]
    if "photo" in found:
        return ["photo"]                       # "passport size photo" is a photo, not a passport
    if "visa_stamping" in found:
        found = [kind for kind in found if kind != "visa"]
    if len(found) > 1:
        found = [kind for kind in found if kind != "other"]
    if not found and any(word in _TRADES for word in words):
        return ["resume"]
    return found


def classify_path_kinds(path: str) -> list[str]:
    """Every document a file is, judged first by its own name and then by the folders it sits in; [] if unrecognised.

    One scan often holds several documents ("signed offer,pp,aadhar.pdf", "visa medical pcc.pdf"); the one file then
    counts for each of them.
    """
    parts = [part for part in PurePosixPath(path.replace("\\", "/")).parts if part not in ("", "/")]
    if not parts:
        return []
    # Name first, then each folder from the closest outwards ("Visa/scan-001.pdf" is a visa).
    for text in [PurePosixPath(parts[-1]).stem] + list(reversed(parts[:-1])):
        kinds = _kinds_in(text)
        if kinds:
            return kinds
    return []


def classify_path(path: str) -> str | None:
    """The first document kind a file is by its name; None if unrecognised."""
    kinds = classify_path_kinds(path)
    return kinds[0] if kinds else None


def classify_content(data: bytes) -> list[str]:
    """For a file whose name says nothing: is it a resume, a passport or an Aadhaar card? [] if none (it stays "Other documents").

    This is the only place a document is read just to be sorted, and only for those three. The first pages are read
    locally (embedded text, else Tesseract), quickly; every other kind of document is sorted by its name and never read.
    A failure to read is never an error.
    """
    try:
        from app.integrations.ocr.inspection import inspect_document
        settings = get_settings()
        inspected = inspect_document(
            data, tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng"),
            max_pages=3, page_timeout=8, document_timeout=25)
    except Exception:
        return []
    return [kind for kind, pages in (("resume", inspected.resume_pages), ("passport", inspected.passport_pages),
                                     ("aadhaar", inspected.aadhaar_pages)) if pages]


_NOT_A_NAME = {"resume", "passport", "photo", "visa", "medical", "offer", "signed", "ticket", "pcc", "police", "clearance", "stamping",
               "stamp", "stamped", "emigration", "education", "certificate", "experience", "receipt", "advance", "google", "review",
               "scan", "image", "document", "page", "copy", "front", "back", "steel", "fixer", "welder", "fitter", "driver", "helper",
               "mason", "carpenter", "electrician", "plumber", "painter", "foreman", "rigger", "cook", "chef", "mechanic", "operator",
               # what a scanner, a phone or an office program calls a file when nobody renamed it: never a person
               "camscanner", "scanner", "scanned", "scans", "new", "doc", "docs", "documents", "img", "images", "dsc", "dscn", "pxl",
               "pic", "picture", "pictures", "photos", "whatsapp", "screenshot", "screen", "file", "files", "untitled", "pages", "pdf",
               "adobe", "office", "lens", "noname"}


def _name_in(path: str) -> tuple[str, list[str]] | None:
    """(initials, name words) when the file name starts with a person's name: "K.Sivakumar-offer.jpg" -> ("K.", ["Sivakumar"]),
    "Rashid khan - PP.pdf" -> ("", ["Rashid", "khan"]). The name runs until the first word that is a document, a trade or a
    number, so "EVisa-123.pdf" and "Itinerary AB1CD2.pdf" name nobody. At most three words."""
    stem = PurePosixPath(path.replace("\\", "/")).stem
    # The office names a file "<name>-<document>" ("Rashid khan - PP", "K.Sivakumar-signed offer"): a hyphen or underscore ends the name.
    head = re.split(r"\s*[-_]\s*", stem.strip(), maxsplit=1)[0]
    tokens = [token for token in re.split(r"[,()\s]+", head) if token]
    initials = ""
    while tokens:
        match = re.match(r"^((?:[A-Za-z]\.)+)(.*)$", tokens[0])
        if not match:
            break
        initials += match.group(1)
        if match.group(2):
            tokens[0] = match.group(2)
            break
        tokens.pop(0)
    words: list[str] = []
    if tokens and re.match(r"^[A-Za-z]+\d", tokens[0]):
        return None                       # DOC00014, IMG_0042, DSC01234, PXL_2024...: a camera or scanner counter, not a person
    for token in tokens[:3]:
        if not (token.isalpha() and len(token) >= 2 and token.lower() not in _NOT_A_NAME and not _kinds_in(token)):
            break
        words.append(token)
    if not words or len(words[0]) < 2 or len("".join(words)) < 4:
        return None
    return initials, words


def _same_person(left: str, right: str) -> bool:
    """Two spellings of one person's name (spaces and case ignored): equal, or a slip of a letter ("rashid" / "rashiid",
    "rashidkhan" / "rashidkhand"). Different people stay apart: "mohamedali" / "mohamedasif" (ratio 0.86), "kumar" / "kumaran"."""
    if left == right:
        return True
    return min(len(left), len(right)) >= 5 and SequenceMatcher(None, left, right).ratio() >= 0.9


def _extends(short: str, long: str) -> bool:
    """`long` is `short` with more of the name after it ("rashid" -> "rashidkhan"): at least three more letters."""
    return len(short) >= 5 and long.startswith(short) and len(long) - len(short) >= 3


def _cluster_names(named: list[dict]) -> tuple[list[dict], set[int]]:
    """Group the named files by person: every pair of names that are spellings of one person is linked, and each linked set is
    one person (extra words in a file name, like "WhatsApp" or "picker", only lengthen a name and never split it). A bare first
    name that begins several different people's names ("Mohamed" beside "Mohamed Ali" and "Mohamed Asif") fits no one in
    particular: its files come back as `ambiguous` instead of being given to a guess."""
    parent = list(range(len(named)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            if find(i) != find(j) and _same_person(named[i]["key"], named[j]["key"]):
                parent[find(j)] = find(i)
    grouped: dict[int, list[dict]] = {}
    for i, item in enumerate(named):
        grouped.setdefault(find(i), []).append(item)
    clusters = [{"items": items, "keys": {item["key"] for item in items}} for items in grouped.values()]
    ambiguous: set[int] = set()
    # A shorter name that more of the name follows in other files ("rashid" -> "rashidkhan", "rashidkhan nawazkhan") joins them when
    # they are all one person. When it could begin two different people ("mohamed" -> "mohamed ali", "mohamed asif") it is
    # ambiguous, and its files are left out for a person to place.
    for cluster in sorted(clusters, key=lambda c: len(min(c["keys"], key=len))):
        if cluster not in clusters:
            continue
        shortest = min(cluster["keys"], key=len)
        hosts = [other for other in clusters if other is not cluster and any(_extends(shortest, key) for key in other["keys"])]
        if not hosts:
            continue
        lineages: list[list[dict]] = []
        for host in hosts:
            for lineage in lineages:
                if any(_extends(a, b) or _extends(b, a) or _same_person(a, b) for other in lineage for a in host["keys"] for b in other["keys"]):
                    lineage.append(host)
                    break
            else:
                lineages.append([host])
        if len(lineages) == 1:
            for host in lineages[0]:
                cluster["items"] += host["items"]
                cluster["keys"] |= host["keys"]
                clusters.remove(host)
        else:
            ambiguous |= {item["index"] for item in cluster["items"]}
            clusters.remove(cluster)
    return clusters, ambiguous


def split_people(paths: list[str]) -> dict | None:
    """Separate a folder that holds several people, by the name each file name starts with. None for an ordinary folder.

    {"people": [{"name", "paths"}], "shared": [paths given to everyone, e.g. a group air ticket], "left_out": [paths
    that cannot be told apart]}. Every file goes to ONE person (or is shared, for an air ticket that covers everyone), so
    two people's files never mix. A file whose name says nobody's, or says a name that fits two different people, is left
    out for a person to place; it is never guessed.
    """
    named = []
    for index, path in enumerate(paths):
        found = _name_in(path)
        if found is not None:
            initials, words = found
            named.append({"index": index, "path": path, "initials": initials, "words": words, "key": "".join(words).lower()})
    clusters, ambiguous = _cluster_names(named)
    if len(clusters) < 2:
        return None
    people = []
    for cluster in sorted(clusters, key=lambda c: min(i["index"] for i in c["items"])):
        items = sorted(cluster["items"], key=lambda entry: entry["index"])
        spellings = [" ".join(entry["words"]) for entry in items]
        best = max(dict.fromkeys(spellings), key=spellings.count)             # the most common spelling, first seen on a tie
        initials = next((entry["initials"].upper() for entry in items if entry["initials"]), "")
        name = (initials.replace(".", ". ").strip() + " " + " ".join(word.capitalize() for word in best.split())).strip()
        people.append({"name": name, "paths": [entry["path"] for entry in items]})
    placed = {entry["path"] for entry in named if entry["index"] not in ambiguous}
    nameless = [path for path in paths if path not in placed]
    shared = [path for path in nameless if classify_path(path) == "air_ticket"]
    for person in people:
        person["paths"] += shared
    return {"people": people, "shared": shared, "left_out": [path for path in nameless if path not in shared]}


def file_problem(name: str) -> str | None:
    if not name.lower().endswith(SUPPORTED_EXTENSIONS):
        return "Only PDF, Word and image files can be imported."
    return None


# --------------------------------------------------------------------------------------------- the stage sheet

@dataclass(frozen=True)
class Column:
    key: str
    header: str
    hint: str
    required: bool = False
    aliases: tuple[str, ...] = ()


COLUMNS: tuple[Column, ...] = (
    Column("folder", "Folder", "The candidate's folder name exactly as picked. Leave empty to match on Name.", aliases=("foldername", "candidatefolder")),
    Column("name", "Name", "Full name. Defaults to the folder name.", aliases=("candidatename", "fullname", "candidate")),
    Column("phone", "Phone", "Mobile number. Phone, email or passport number is needed to tell candidates apart.", True,
           ("mobile", "mobilenumber", "phonenumber", "mobileno", "contactnumber", "contact")),
    Column("email", "Email", "Optional.", aliases=("emailid", "emailaddress", "mail")),
    Column("passport_no", "Passport number", "Optional. Also read from the passport scan.", aliases=("passport", "passportno", "passportnumber")),
    Column("order", "Order", "Order reference, e.g. ORD-1001. Leave empty to use the order chosen on the screen.", aliases=("orderref", "orderno", "ordernumber", "orderid", "jobref")),
    Column("recruiter", "Recruiter", "Recruiter's email or full name. Leave empty to use the recruiter chosen on the screen.", aliases=("owner", "assignedto", "staff", "recruiteremail")),
    Column("agency", "Referral agency", "The agency that sent the candidate. Optional; shown in the travelled report.",
           aliases=("referralagency", "agencyname", "referredby", "referenceagency")),
    Column("prescreen_remarks", "Prescreen remarks", "Stage 1. Optional.", aliases=("prescreening",)),
    Column("forwarded_date", "Forwarded to client on", "Stage 2 date. Optional.", aliases=("forwarded", "dateforwarded", "cvforwarded")),
    Column("interview_date", "Interview date", "Stage 3. Needed to record the interview.", True, ("interview", "interviewon")),
    Column("interview_time", "Interview time", "Stage 3, HH:MM. Optional.", aliases=()),
    Column("interview_mode", "Interview mode", "Stage 3: online or in person. Optional.", aliases=("mode",)),
    Column("interview_remarks", "Interview remarks", "Stage 4. Defaults to a note that the result was imported.", aliases=("remarks", "interviewresult", "interviewfeedback")),
    Column("medical_date", "Medical date", "Stage 6, the day the medical was applied for. Optional.", aliases=("medical", "medicalapplied", "medicalapplieddate")),
    Column("passport_collected_date", "Passport collected on", "Stage 8. Optional.", aliases=("passportcollected", "passportdate")),
    Column("advance_amount", "Advance amount", "Stage 9, the amount collected. Needed.", True, ("advance", "amount", "advancepayment", "advancecollected")),
    Column("advance_currency", "Advance currency", "Stage 9, e.g. INR. Defaults to the agency's advance currency.", aliases=("currency",)),
    Column("advance_status", "Advance status", "Stage 9: collected or pending. Optional; collected when an amount is given. With pending, the amount is the advance expected.",
           aliases=("advancestatus", "paymentstatus", "advancestate")),
    Column("service_charge_total", "Total service charge", "Stage 9, the whole service charge, in the same currency. Optional.",
           aliases=("servicecharge", "totalservicecharge", "servicefee", "totalservicefee", "totalcharge", "totalfee")),
    Column("advance_date", "Advance date", "Stage 9. Optional.", aliases=("advancedate", "paymentdate")),
    Column("advance_mode", "Advance mode", "Stage 9: cash, bank, upi or cheque. Optional.", aliases=("paymentmode",)),
    Column("receipt_no", "Receipt number", "Stage 9. Optional.", aliases=("receipt", "receiptno", "receiptnumber")),
    Column("visa_arrival_date", "Visa arrived on", "Stage 10. Optional.", aliases=("visadate", "visaarrived", "visaarrival")),
    Column("google_review", "Google review", "Stage 10: yes or no. Optional.", aliases=("review", "googlereview")),
    Column("stamping_date", "Visa stamped on", "Stage 11. Optional.", aliases=("stamping", "visastamping", "stampeddate")),
    Column("booked_by", "Ticket booked by", "Stage 12: employer or agency. Optional.", aliases=("ticketbookedby", "bookedby")),
    Column("travel_date", "Travel date", "Stage 12. Optional.", aliases=("traveldate", "flightdate", "departure")),
    Column("airline", "Airline and flight", "Stage 12. Optional.", aliases=("flight", "airlineflight", "airlineandflight")),
    Column("pnr", "PNR", "Stage 12. Optional.", aliases=("pnrno", "bookingreference")),
)
COLUMN_KEYS = tuple(column.key for column in COLUMNS)


def _squash(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", header.lower())


_HEADER_LOOKUP: dict[str, str] = {}
for _column in COLUMNS:
    for _name in (_column.key, _column.header, *_column.aliases):
        _HEADER_LOOKUP.setdefault(_squash(_name), _column.key)


def read_manifest(data: bytes, filename: str = "") -> dict:
    """Parse the stage sheet. Returns {rows: [{rowNumber, <canonical key>: text}], recognised: [headers], ignored: [headers]}."""
    from app.services import lead_parsing as lp
    try:
        table = lp.read_table(data, filename)
    except lp.LeadFileError as exc:
        raise ManifestError(str(exc)) from exc
    keys: list[str | None] = [_HEADER_LOOKUP.get(_squash(header)) for header in table.headers]
    recognised = [header for header, key in zip(table.headers, keys) if key]
    ignored = [header for header, key in zip(table.headers, keys) if not key and header.strip()]
    if not recognised:
        raise ManifestError("None of the column headings were recognised. Download the sheet from this screen and fill that one in.")
    rows = []
    for number, cells in table.rows:
        row: dict[str, Any] = {"rowNumber": number}
        for key, cell in zip(keys, cells):
            if key and (cell or "").strip() and key not in row:
                row[key] = cell.strip()
        if len(row) > 1:
            rows.append(row)
    return {"rows": rows, "recognised": recognised, "ignored": ignored}


class ManifestError(Exception):
    pass


# --------------------------------------------------------------------------------------------- reading cell values

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y",
                 "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d/%b/%Y")


def parse_date(raw: Any) -> date | None:
    """A day written the way an Indian office writes it (day first), an ISO date or an Excel serial. None when unreadable."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    text = re.sub(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*([AaPp][Mm])?$", "", text).strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        serial = int(float(text))
        if 30000 < serial < 80000:
            return date.fromordinal(date(1899, 12, 30).toordinal() + serial)
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_time(raw: Any) -> str | None:
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?::\d{2})?\s*([AaPp][Mm])?", str(raw or "").strip())
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def parse_amount(raw: Any) -> tuple[Decimal | None, str | None]:
    """(amount, currency written in the cell, if any). Handles '15,000', 'INR 15000', 'Rs. 15000/-'."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    code = re.search(r"\b([A-Za-z]{3})\b", text)
    currency = code.group(1).upper() if code and code.group(1).upper() not in {"RS", "THE"} else None
    found = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not found:
        return None, currency
    try:
        value = Decimal(found.group(0).replace(",", ""))
    except InvalidOperation:
        return None, currency
    return (value if value > 0 else None), currency


def parse_advance_status(raw: Any) -> str | None:
    """'collected' or 'pending' from the sheet's Advance status cell; None when it is empty or not understood."""
    text = re.sub(r"[^a-z]", "", str(raw or "").lower())
    if text.startswith(("pend", "unpaid", "notpaid", "notyet", "due", "awaiting", "balance")):
        return "pending"
    if text.startswith(("collect", "paid", "receiv", "done", "yes")):
        return "collected"
    return None


def parse_yes_no(raw: Any) -> bool | None:
    text = str(raw or "").strip().lower()
    if text in {"yes", "y", "true", "1", "done", "ok", "obtained"}:
        return True
    if text in {"no", "n", "false", "0", "not done"}:
        return False
    return None


def name_slug(name: str) -> str:
    """A person's name as it appears at the start of a stored file name: "Gunavelan R" -> "Gunavelan_R"."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_") or "Candidate"


def import_file_name(person: str, kinds: list[str], original: str, taken: set[str]) -> str:
    """The name an imported document is stored under: "<Name>-<Document>.<ext>", e.g. "Gunavelan_R-Passport.pdf".

    A scan that is several documents carries all their names ("Gunavelan_R-Signed_offer_Passport_Aadhaar_card.pdf"); a second
    file of the same kind gets "-2". `taken` holds the names already used for this candidate. The original name is kept in the
    audit entry of the upload.
    """
    extension = PurePosixPath(original.replace("\\", "/")).suffix.lower()
    label = "_".join(name_slug(KIND_LABELS.get(kind, kind)) for kind in kinds) or "Document"
    stem = f"{name_slug(person)}-{label}"
    candidate, number = f"{stem}{extension}", 1
    while candidate.lower() in taken:
        number += 1
        candidate = f"{stem}-{number}{extension}"
    taken.add(candidate.lower())
    return candidate


def _clean_name(folder: str) -> str:
    name = re.sub(r"^\s*\d+\s*[-_.)]\s*", "", folder)  # "01 - Rahul" -> "Rahul"
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip()


def row_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


# --------------------------------------------------------------------------------------------- the plan

@dataclass
class Context:
    """What the plan is checked against; loaded once per request."""
    orders: dict[str, Order]                 # lower-case reference -> order
    deployed: dict[str, int]                 # order id -> placements already deployed
    users: list[User]                        # active users who can own a candidate
    options: StageOptions
    default_order: str | None = None
    default_recruiter: str | None = None     # user id
    only: User | None = None                 # a staff member's own import: every candidate is theirs, whatever the sheet says
    me: User | None = None                   # whoever is importing: the recruiter when nobody else is named
    planned: dict[str, int] = field(default_factory=dict)  # order id -> placements this batch has already claimed
    # Candidates who have already travelled: what the folder cannot hold (the advance receipt, a copy of the offer, the stamped
    # visa) does not stop the placement being recorded, and the documents still missing are listed instead of blocking it.
    historical: bool = False
    # The office says this person has travelled although the folder holds no ticket (so no travel date). The ticket file is then not
    # asked for; the placement is recorded as travelled with the date left blank, to be filled in on the stage screen later.
    travel_confirmed: bool = False

    def recruiter(self, text: str | None) -> User | None:
        if self.only is not None:
            return self.only
        wanted = (text or "").strip().lower()
        if not wanted:
            chosen = next((user for user in self.users if user.id == self.default_recruiter), None)
            return chosen or next((user for user in self.users if self.me is not None and user.id == self.me.id), None)
        by_email = [user for user in self.users if (user.email or "").lower() == wanted]
        if by_email:
            return by_email[0]
        by_name = [user for user in self.users if user.name.strip().lower() == wanted]
        return by_name[0] if len(by_name) == 1 else None

    def order(self, text: str | None) -> Order | None:
        wanted = (text or "").strip().lower() or (self.default_order or "").strip().lower()
        return self.orders.get(wanted) if wanted else None


def load_context(db: Session, *, default_order: str | None, default_recruiter: str | None, wanted_orders: set[str],
                 only: User | None = None, me: User | None = None) -> Context:
    from app.services import agency_settings
    refs = {ref.strip().lower() for ref in wanted_orders if ref and ref.strip()}
    if default_order:
        refs.add(default_order.strip().lower())
    orders = {order.ref.lower(): order for order in db.scalars(select(Order).where(func.lower(Order.ref).in_(refs)))} if refs else {}
    deployed = {}
    if orders:
        deployed = dict(db.execute(
            select(Application.order_id, func.count()).where(
                Application.order_id.in_([order.id for order in orders.values()]), Application.status == ApplicationStatus.deployed,
            ).group_by(Application.order_id)).all())
    users = list(db.scalars(select(User).where(User.active.is_(True), User.role.in_([Role.staff, Role.manager, Role.admin]))))
    return Context(orders=orders, deployed=deployed, users=users, options=agency_settings.stage_options(db),
                   default_order=default_order, default_recruiter=default_recruiter, only=only, me=me)


def _file_ref(kind: str, files: dict[str, dict]) -> dict | None:
    return files.get(kind)


def stage_inputs(row: dict, files: dict[str, dict], options: StageOptions) -> tuple[dict[int, dict], list[dict]]:
    """The twelve stage payloads for a finished placement, and the problems with the sheet's values.

    `files` maps a document kind to the file reference to store (`{id, name, url, sizeKb, version}`); the plan passes
    placeholders. A stage the rules cannot complete simply shows up in the evaluation, with the rule's own wording.
    """
    problems: list[dict] = []

    def day(key: str, label: str, future_ok: bool = False) -> str | None:
        raw = row.get(key)
        if raw in (None, ""):
            return None
        parsed = parse_date(raw)
        if parsed is None:
            problems.append({"level": "error", "message": f"{label}: '{raw}' is not a date. Write it like 14/03/2026."})
            return None
        if parsed > date.today() and not future_ok:
            problems.append({"level": "error", "message": f"{label}: {parsed.isoformat()} is in the future."})
        return parsed.isoformat()

    interview = day("interview_date", "Interview date")
    forwarded = day("forwarded_date", "Forwarded date")
    medical = day("medical_date", "Medical date")
    collected = day("passport_collected_date", "Passport collected date")
    advance_date = day("advance_date", "Advance date")
    visa = day("visa_arrival_date", "Visa arrival date")
    stamping = day("stamping_date", "Stamping date")
    travel = day("travel_date", "Travel date", future_ok=True)

    amount, written_currency = parse_amount(row.get("advance_amount"))
    if row.get("advance_amount") and amount is None:
        problems.append({"level": "error", "message": f"Advance amount: '{row['advance_amount']}' is not an amount."})
    currency = (row.get("advance_currency") or written_currency or "").strip().upper() or None
    # The status the sheet's Advance column needs. Collected unless the sheet says pending; nothing at all when there is
    # neither an amount nor a status (the stage then reads as it always did).
    status = parse_advance_status(row.get("advance_status"))
    if row.get("advance_status") and status is None:
        problems.append({"level": "warning", "message": f"Advance status '{row['advance_status']}' was not understood (use collected or pending) and is left out."})
    if status is None and amount is not None:
        status = "collected"
    service_total, _total_currency = parse_amount(row.get("service_charge_total"))
    if row.get("service_charge_total") and service_total is None:
        problems.append({"level": "error", "message": f"Total service charge: '{row['service_charge_total']}' is not an amount."})
    if service_total is not None and amount is not None and amount > service_total:
        problems.append({"level": "error", "message": "Total service charge cannot be less than the advance amount."})

    interview_time = None
    if row.get("interview_time"):
        interview_time = parse_time(row["interview_time"])
        if interview_time is None:
            problems.append({"level": "warning", "message": f"Interview time '{row['interview_time']}' was not understood and is left out."})
    mode_text = re.sub(r"[^a-z]", "", (row.get("interview_mode") or "").lower())
    mode = "online" if mode_text.startswith("online") else "in_person" if mode_text.startswith(("inperson", "person", "offline", "physical")) else None
    pay_text = re.sub(r"[^a-z]", "", (row.get("advance_mode") or "").lower())
    pay_mode = next((m for m in ("cash", "bank", "upi", "cheque") if pay_text.startswith(m)), "bank" if pay_text.startswith(("neft", "imps", "transfer")) else None)
    booked_text = (row.get("booked_by") or "").strip().lower()
    booked = "employer" if booked_text.startswith(("employer", "company", "client")) else "agency" if booked_text.startswith(("agency", "careindia", "us", "own")) else None
    review = parse_yes_no(row.get("google_review"))

    def clean(payload: dict) -> dict:
        return {key: value for key, value in payload.items() if value not in (None, "")}

    stages = {
        1: clean({"result": "passed", "remarks": row.get("prescreen_remarks")}),
        2: clean({"forwarded": "yes", "dateForwarded": forwarded}),
        3: clean({"scheduled": "yes", "date": interview, "time": interview_time, "mode": mode}),
        4: {"result": "selected", "remarks": row.get("interview_remarks") or "Selected. Recorded from the candidate's file when the history was imported."},
        # Imported history only ever records the positive outcomes (Selected, Agreed, Fit): the folder is the evidence that
        # the candidate moved on, and a candidate who stopped at one of these stages is not imported as having travelled.
        5: clean({"result": "agreed", "obtained": "yes", "file": _file_ref("signed_offer", files)}),
        6: clean({"applied": "yes", "appliedDate": medical, "result": "fit"}),
        # Police clearance: only what the folder shows (a PCC file); an empty stage is recorded as done manually like the others.
        7: clean({"pccStatus": "received" if _file_ref("pcc", files) else None, "file": _file_ref("pcc", files)}),
        8: clean({"collected": "yes", "collectedDate": collected, "copy": _file_ref("passport", files)}),
        # A pending advance has no date, mode or receipt yet: the amount is the advance expected.
        9: clean({"collected": "no", "advanceStatus": "pending", "amount": str(amount) if amount is not None else None,
                  "amountCurrency": currency, "serviceChargeTotal": str(service_total) if service_total is not None else None})
        if status == "pending" else
        clean({"collected": "yes", "advanceStatus": status, "amount": str(amount) if amount is not None else None, "amountCurrency": currency,
               "serviceChargeTotal": str(service_total) if service_total is not None else None,
               "date": advance_date, "mode": pay_mode, "receiptNo": row.get("receipt_no"), "receipt": _file_ref(ADVANCE_RECEIPT, files)}),
        10: clean({"arrived": "yes", "date": visa, "file": _file_ref("visa", files), "googleReview": review}),
        11: clean({"done": "yes", "date": stamping, "file": _file_ref("visa_stamping", files)}),
        12: clean({"bookedBy": booked, "travelDate": travel, "airline": row.get("airline"), "pnr": row.get("pnr"), "file": _file_ref("air_ticket", files)}),
    }
    return stages, problems


HISTORY_FILE = {"id": "history"}  # stands in for a file only while the rules are asked; it is never stored


def for_rules(number: int, data: dict, historical: bool = False, travel_confirmed: bool = False) -> dict:
    """What the stage rules are asked about an imported stage.

    Two things a folder of documents cannot say are allowed to stay blank for imported history: the interview date and the
    advance amount (the receipt is the evidence). The stored record keeps them blank; only the check is satisfied.

    For someone who has already travelled (`historical`), the later stages prove the earlier ones happened, so the files
    those earlier stages would normally need (offer copy, advance receipt and amount, stamped visa) may be missing too. The
    ticket and its date (stage 12) are never waived by the folder alone: they are the proof of travel. Only the office's own word
    that the person has flown (`travel_confirmed`) stands in for them, and then both stay blank in the stored record.
    """
    if number == 3 and data.get("scheduled") and not data.get("date"):
        return {**data, "date": "2000-01-01"}
    if number == ADVANCE_STAGE and data.get("collected") == "yes" and not data.get("amount") and data.get("receipt"):
        return {**data, "amount": "1"}
    if historical:
        if number == 5 and data.get("obtained") and not data.get("file"):
            return {**data, "file": HISTORY_FILE}
        if number == ADVANCE_STAGE and data.get("collected") == "yes":
            return {**data, "amount": data.get("amount") or "1", "receipt": data.get("receipt") or HISTORY_FILE}
        if number == STAMPING_STAGE and data.get("done") and not data.get("file"):
            return {**data, "file": HISTORY_FILE}
        if number == TOTAL_STAGES and travel_confirmed and not (data.get("file") and data.get("travelDate")):
            # The office's word that they flew stands in for the ticket and its date; both stay blank in the stored record.
            return {**data, "file": data.get("file") or HISTORY_FILE, "travelDate": data.get("travelDate") or "2000-01-01"}
    return data


def evaluate_stages(stages: dict[int, dict], options: StageOptions, historical: bool = False, travel_confirmed: bool = False,
                    level: int = TOTAL_STAGES) -> list[dict]:
    """[{stage, title, ok, missing}] - the same completion rules the stage screens apply.

    Staff may move a stage "manually" on the stage screen, so an empty stage passes the rules; here an empty stage above
    `level` (the furthest stage the folder and sheet give evidence for) is never recorded as done.
    """
    report = []
    for number in range(1, TOTAL_STAGES + 1):
        definition = stage_by_no(number)
        result = definition.evaluate(for_rules(number, stages[number], historical, travel_confirmed), options if number == ADVANCE_STAGE else None)
        report.append({"stage": number, "title": definition.title,
                       "ok": number <= level and bool(result.can_complete and not result.closes and not result.repeats),
                       "missing": result.missing})
    return report


def stage_dates(stages: dict[int, dict], now: datetime, zone) -> dict[int, datetime]:
    """When each stage is recorded as completed: its own date at noon, never earlier than the stage before, never in the future."""
    keys = {2: "dateForwarded", 3: "date", 6: "appliedDate", 7: "pccReceivedDate", 8: "collectedDate", 9: "date", 10: "date", 11: "date", 12: "travelDate"}
    stamps: dict[int, datetime] = {}
    previous: datetime | None = None
    known = [parse_date(stages[n].get(key)) for n, key in keys.items() if stages[n].get(key)]
    first = min((d for d in known if d), default=None)
    for number in range(1, TOTAL_STAGES + 1):
        own = parse_date(stages[number].get(keys.get(number, ""))) if keys.get(number) else None
        # Travel is in the future for some; the ticket was booked well before, so never record it after now.
        day = own or (previous.date() if previous else first)
        moment = datetime.combine(day, time(12, 0), tzinfo=zone) if day else now
        if previous is not None and moment < previous:
            moment = previous
        moment = min(moment, now)
        stamps[number] = previous = moment
    return stamps


def evidence_level(row: dict, kinds: set[str]) -> int:
    """The furthest stage the sheet and the folder show real evidence for (0 when there is none).

    A stage is recorded as done only up to this point: a candidate with a visa certainly passed the medical, but a folder
    with just a resume says nothing about the interview. Stages below the level that carry no evidence of their own
    (prescreening, forwarding, the interview result) are recorded as passed.
    """
    shown = {
        3: bool(row.get("interview_date")), 5: "signed_offer" in kinds, 6: bool(row.get("medical_date")) or "medical" in kinds,
        7: "pcc" in kinds, 8: bool(row.get("passport_collected_date")),
        9: bool(row.get("advance_amount")) or parse_advance_status(row.get("advance_status")) is not None or ADVANCE_RECEIPT in kinds,
        10: bool(row.get("visa_arrival_date")) or "visa" in kinds, 11: bool(row.get("stamping_date")) or "visa_stamping" in kinds,
        12: "air_ticket" in kinds or bool(row.get("travel_date")) or bool(row.get("pnr")),
    }
    return max((number for number, present in shown.items() if present), default=0)


def plan_candidate(db: Session, ctx: Context, *, folder: str, files: list[dict], row: dict | None, by_name: bool = True,
                   folder_names: bool = True, name_source: str | None = None, check_duplicates: bool = True) -> dict:
    """What importing this candidate would do, without writing anything.

    `files` are [{path, kind?}]; `kind` is the user's choice when they changed the suggestion. The result's `action` is
    skip (an error stops it), documents_only (no order to put them on), in_progress (stages recorded up to the first one
    that lacks something; the rest is done on the stage screen) or deploy (all twelve stages recorded).

    The candidate's name is the sheet's, or the one read from the documents (`row["name"]`, `name_source` "documents"). With
    `folder_names` False (the Import screen) the folder is only a way of grouping files and never becomes a name: a candidate
    whose documents showed no name is called `PENDING_NAME` and the first reading of a document replaces it. The folder name
    is still the name for the callers that import an organised library (`folder_names` True, the default).
    """
    from app.services.candidate_review import PENDING_NAME
    row = dict(row or {})
    issues: list[dict] = []
    name = (row.get("name") or "").strip() or (_clean_name(folder) if folder_names else PENDING_NAME)
    name_source = name_source or ("sheet" if row.get("name") else "pending" if name == PENDING_NAME else "folder")
    row["name"] = name
    planned_files, by_kind = [], {}
    for item in files:
        path = item["path"]
        kinds = [item["kind"]] if item.get("kind") else list(item.get("kinds") or classify_path_kinds(path))
        problem = file_problem(path)
        entry = {"path": path, "kind": kinds[0] if kinds else None, "kinds": kinds, "skipped": bool(problem), "problem": problem}
        if item.get("pages"):
            entry["pages"] = list(item["pages"])     # only these pages of the file belong to this candidate (a scan shared by several people)
        if not problem and "photo" in kinds and not path.lower().endswith(IMAGE_EXTENSIONS):
            entry.update(skipped=True, problem="The photo must be an image (JPG, PNG, WebP or HEIC).")
        if not kinds and not problem:
            kinds = ["other"]
            entry.update(kind="other", kinds=kinds, guessed=True)
        entry["labels"] = [KIND_LABELS.get(kind, kind) for kind in kinds]
        entry["label"] = " + ".join(entry["labels"]) or None
        planned_files.append(entry)
        if not entry["skipped"]:
            for kind in kinds:
                by_kind.setdefault(kind, []).append(entry)
    unnamed = sum(1 for entry in planned_files if entry.get("guessed"))
    if unnamed and by_name:
        issues.append({"level": "info", "message": f"{unnamed} file{'s' if unnamed != 1 else ''} with no clue in the name will be checked for a resume, passport or Aadhaar card when you import."})
    for kind, entries in by_kind.items():
        if len(entries) > 1 and kind != "other":
            issues.append({"level": "warning", "message": f"{len(entries)} files are marked {KIND_LABELS[kind]}. The last one by name is the current version; the rest are kept as earlier versions."})

    phone, email, passport = (row.get("phone") or "").strip(), (row.get("email") or "").strip(), (row.get("passport_no") or "").strip()
    if not name:
        issues.append({"level": "error", "message": "No name. Name the folder after the candidate."})
    if phone and not normalize_phone(phone):
        issues.append({"level": "error", "message": f"Phone '{phone}' is not a valid number (at least seven digits)."})
    duplicate, matched_by = None, None
    if check_duplicates and not any(i["level"] == "error" for i in issues):
        found = identity_matches(db, {"phone": phone, "email": email, "passport_no": passport}, lock=False) if (
            normalize_phone(phone) or normalize_email(email) or normalize_passport(passport)) else []
        if found:
            duplicate = found[0][0]
            matched_by = {"phone number": "phone", "email": "email", "passport number": "passport"}.get((found[0][1] or ["name"])[0], "name")
        elif name and name != PENDING_NAME:  # no phone yet: the same (real) name is the best sign this person is already in
            duplicate = db.scalar(select(Candidate).where(func.lower(func.trim(Candidate.name)) == name.lower()).limit(1))
            matched_by = "name" if duplicate is not None else None
        if duplicate is not None:
            issues.append({"level": "error", "message": f"Already in the CRM as {duplicate.care_id or duplicate.ref} ({duplicate.name}). Nothing is imported for this folder."})
    owner = ctx.recruiter(row.get("recruiter"))
    if owner is None:
        issues.append({"level": "error", "message": f"Recruiter '{row['recruiter']}' was not found among active users." if row.get("recruiter")
                       else "No recruiter could be chosen."})

    kinds = set(by_kind)
    refs = {kind: {"id": "pending", "name": entries[-1]["path"].rsplit("/", 1)[-1]} for kind, entries in by_kind.items()}
    level = TOTAL_STAGES if ctx.travel_confirmed else evidence_level(row, kinds)
    built, problems = stage_inputs(row, refs, ctx.options)
    issues.extend(problems)
    shown = {number: (built[number] if number <= level else {}) for number in range(1, TOTAL_STAGES + 1)}
    report = evaluate_stages(shown, ctx.options, ctx.historical, ctx.travel_confirmed, level)
    completed = 0
    for item in report:
        if not item["ok"]:
            break
        completed = item["stage"]
    # The Invoice document is the admin's, so a recruiter's folder is never held back for a document they cannot see.
    gaps = deployment_gaps({kind for kind in kinds if kind in CANDIDATE_SLOTS}, ctx.me is None or can_see_invoices(ctx.me))

    order = ctx.order(row.get("order"))
    action, notes = "documents_only", []
    if order is None:
        notes.append(f"Order '{row['order']}' was not found, so no job is attached." if row.get("order")
                     else "No order was given, so no job is attached. The candidate and the documents are imported.")
    elif completed < TOTAL_STAGES and order.status != OrderStatus.open:
        notes.append(f"{order.ref} is not open, so no job is attached. Open it and map the candidate from the stage screen.")
    else:
        action = "in_progress"
        if completed == TOTAL_STAGES:
            used = ctx.deployed.get(order.id, 0) + ctx.planned.get(order.id, 0)
            if used >= order.openings:
                completed = TOTAL_STAGES - 1
                notes.append(f"{order.ref} has no free opening, so the last stage is left for you to finish after raising the openings.")
            elif gaps and not ctx.historical:
                completed = TOTAL_STAGES - 1
                notes.append("The last stage is left open: these documents are still needed before deploying: " + ", ".join(g["label"] for g in gaps) + ".")
            else:
                action = "deploy"
    if action == "deploy" and order is not None:
        ctx.planned[order.id] = ctx.planned.get(order.id, 0) + 1
    current = None if action == "deploy" else completed + 1
    if action == "in_progress" and current is not None and current <= TOTAL_STAGES:
        title = stage_by_no(current).title
        missing = next((item["missing"] for item in report if item["stage"] == current), [])
        lead = f"Stages 1 to {completed} are recorded as done. " if completed else "No stage is recorded yet. "
        notes.append(lead + f"It continues at stage {current} ({title})" + (f": {'; '.join(missing)}." if missing and current <= level else "."))
    if action in {"in_progress", "deploy"}:
        if completed >= 3 and not row.get("interview_date"):
            notes.append("No interview date was given, so the interview stage is recorded without one.")
        if completed >= ADVANCE_STAGE and not row.get("advance_amount") and parse_advance_status(row.get("advance_status")) != "pending":
            notes.append("No advance amount was given, so the advance is recorded from the receipt without an amount.")
    issues.extend({"level": "info", "message": note} for note in notes)
    if gaps and by_name:        # computed from the file NAMES: wrong for a person found by reading the files, so not said then
        issues.append({"level": "info", "message": "Documents not found: " + ", ".join(g["label"] for g in gaps) + ". Upload them later from the profile."})
    if any(i["level"] == "error" for i in issues):
        action = "skip"
    return {
        "folder": folder, "name": name, "phone": phone or None, "email": email or None, "passportNo": passport or None,
        "order": order.ref if order else (row.get("order") or None), "recruiter": owner.name if owner else (row.get("recruiter") or None),
        "files": planned_files, "issues": issues, "stages": report if action != "documents_only" else [], "level": level,
        "completedThrough": completed if action in {"in_progress", "deploy"} else 0, "currentStage": current if action == "in_progress" else None,
        "missingDocuments": [gap["label"] for gap in gaps], "action": action, "duplicateOf": duplicate.ref if duplicate else None,
        "duplicateOfCareId": duplicate.care_id if duplicate else None, "duplicateOfId": duplicate.id if duplicate else None,
        "nameSource": name_source,
        "alreadyInCrm": ({"candidateId": duplicate.id, "careId": duplicate.care_id, "ref": duplicate.ref, "name": duplicate.name,
                          "matchedBy": matched_by, "files": 0, "strength": "certain" if matched_by == "passport" else "possible"} if duplicate else None),
    }


# --------------------------------------------------------------------------------------------- the import

@dataclass
class Upload:
    path: str
    kinds: list[str]
    data: bytes
    content_type: str
    # Set when the file was cut out of a larger scan by `split_by_content`: the pages (numbered in the original scan) it holds,
    # and the name the whole scan came in under (`path` then carries that name with a .pdf extension).
    pages: list[int] | None = None
    original_name: str | None = None
    # What the splitting read on each page of `data` (`duplicate_pages.PageRead`, None for a page not read): lets the pages that are
    # already filed in another file of the same document be told apart without reading anything twice.
    facts: list | None = None
    # The whole scan `data` was cut from (as received): kept as "Source document evidence" when a page of `data` is not filed again.
    source: bytes | None = None


def _register_import_packets(db: Session, candidate: Candidate, filed: list[tuple[Upload, Attachment]],
                             previously_filed: set[bytes]) -> None:
    from app.services.document_reading import register_packet

    originals = {upload.data: row for upload, row in filed if row.slot == SOURCE_BUNDLE}
    groups: dict[bytes, list[tuple[str, Attachment]]] = {}
    for upload, row in filed:
        if upload.pages and row.slot in SENT_TO_READER and upload.source in originals and upload.source not in previously_filed:
            groups.setdefault(upload.source, []).append((row.slot, row))
    for source, pieces in groups.items():
        if len({slot for slot, _ in pieces}) > 1:
            register_packet(db, candidate, originals[source], pieces)


# --------------------------------------------------------------------------------------------- splitting a scan by content

# Bounded cost: a scan the local reader cannot finish in this time is stored whole, as before.
SPLIT_MAX_PAGES = 40
SPLIT_PAGE_TIMEOUT = 8
SPLIT_DOCUMENT_TIMEOUT = 120


@dataclass
class Piece:
    kinds: list[str]
    data: bytes
    pages: list[int]          # numbered in the original scan, in order; [] when the scan was not split


@dataclass
class SplitResult:
    pieces: list[Piece]
    split: bool               # False: the scan is stored whole under `pieces[0].kinds`
    note: str | None = None
    # How each page was filed by its content: {page number: {"kind", "confidence", "reason", "inherited"}} for every page the
    # content kind classifier placed (identity documents are decided by the page classifier and are not listed here).
    page_kinds: dict[int, dict] = field(default_factory=dict)
    # What was read on every page that was inspected, by page number of the scan (`duplicate_pages.PageRead`): the text and how
    # sure the reader was. Kept so the pages already filed elsewhere can be recognised without reading the scan again.
    page_reads: dict = field(default_factory=dict)
    # The reading was cut short: pages the reader ran out of time for (`cut_short`), or the whole scan could not be read (`failed`).
    # What a split of such a reading says is not final, so a caller that files pieces for good (the background re-sort of stored files,
    # services/resort_bundles.py) does not act on it and tries again later.
    cut_short: bool = False
    failed: bool = False

    def reads_for(self, pages: list[int]) -> list | None:
        """The readings of these pages (numbers in the scan that was read), or None when any of them was not read."""
        found = [self.page_reads.get(number) for number in pages]
        return found if found and all(item is not None for item in found) else None


def wants_split(kinds: list[str], content_type: str) -> bool:
    """A PDF whose name says nothing, or says more than one document ("signed offer,aadhar,pp.pdf"), may hold several documents.
    A file named for one document is that document; an image is one page; both are kept whole."""
    return content_type == "application/pdf" and "photo" not in kinds and len(kinds) != 1


def _whole(data: bytes, kinds: list[str], note: str | None = None) -> SplitResult:
    return SplitResult([Piece(list(kinds), data, [])], False, note)


def split_by_content(data: bytes, content_type: str, kinds_from_name: list[str], original_name: str = "", *,
                     document_timeout: float | None = None) -> SplitResult:
    """Cut a multi-document scan into the documents it holds, by what is on each page (read locally, no network).

    One piece per identity document found (passport, Aadhaar, resume: its pages in original order; a page that is both goes to
    both). The other pages are then sorted by their content too (`kind_classifier`): one piece per kind found (signed offer, visa,
    medical, police clearance, air ticket, certificates ...); a page that is an education certificate or an application form
    stays with the resume it is read with and is also filed in its own field. A page with no confident kind of its own (a poor
    scan, an unfamiliar document) is left over. The left-over pages are filed under the kinds the file name gave that no page
    showed (the name is the person's claim, so it keeps what nobody else claimed), "other" when there are none. A name that
    claims a kind the reader did not find on any page, with no page left over, keeps the whole scan under it, so a misread never
    empties a slot. A photo or blank page stays next to the document it follows. Pieces covering the same pages are one piece
    with all their kinds, and a piece that is the whole scan is the original bytes, untouched. Every page ends up in at least
    one piece. Content decides before the name: a file named "visa,medical,pcc" is cut into those three by what each page says.

    Never raises: a scan that cannot be read in time is returned whole under the kinds today's rules give it (the name's, else
    what `classify_content` makes of its first pages), with an info note. `document_timeout` (seconds) overrides the default time box.
    """
    named = list(dict.fromkeys(kinds_from_name))
    if not wants_split(named, content_type):
        return _whole(data, named)
    try:
        from app.integrations.ocr.inspection import inspect_document
        settings = get_settings()
        inspected = inspect_document(
            data, tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng"),
            max_pages=SPLIT_MAX_PAGES, page_timeout=SPLIT_PAGE_TIMEOUT, document_timeout=document_timeout or SPLIT_DOCUMENT_TIMEOUT)
        return _split_inspected(inspected, data, named, original_name)
    except Exception:
        short = (original_name or "The file").rsplit("/", 1)[-1]
        whole = _whole(data, named or classify_content(data) or ["other"],
                       f"{short} could not be split into its documents; it was stored whole.")
        whole.failed = True
        return whole


def _content_kinds(inspected) -> dict:
    """The content kind of each page that was read (`kind_classifier`), by page number. Never raises: a page it cannot judge has none."""
    if isinstance(getattr(inspected, "content_kinds", None), dict):   # kinds saved from an earlier reading (intake_documents): no page text
        return inspected.content_kinds
    try:
        from app.integrations.ocr.kind_classifier import classify_kinds
        pages = inspected.pages
        guesses = classify_kinds([page.text if page.status == "read" else "" for page in pages])
        return {page.page_number: guess for page, guess in zip(pages, guesses) if guess.kind and page.status == "read"}
    except Exception:
        return {}


def _split_inspected(inspected, data: bytes, named: list[str], original_name: str) -> SplitResult:
    result = _split_pieces(inspected, data, named, original_name)
    result.cut_short = any(page.status == "unread" and getattr(page, "method", None) == "not_processed" for page in inspected.pages)
    # Pages the reader ran out of time for were filed with the left-over pages (Other); say so, so they get a look.
    unread = [page.page_number for page in inspected.pages if page.status == "unread"]
    if unread:
        short = (original_name or "The file").rsplit("/", 1)[-1]
        result.note = ((result.note + " ") if result.note else "") + (
            f"{short}: page{'s' if len(unread) != 1 else ''} {_page_list(unread)} could not be read in time and "
            "went to Other documents; check them.")
    try:
        from app.services.duplicate_pages import PageRead
        result.page_reads = {page.page_number: PageRead(page.text or "", float(page.confidence or 0.0), page.status)
                             for page in inspected.pages}
    except Exception:    # the readings only serve the duplicate check; a page without one is simply never called a repeat
        result.page_reads = {}
    return result


def _split_pieces(inspected, data: bytes, named: list[str], original_name: str) -> SplitResult:
    count = inspected.page_count
    everything = list(range(1, count + 1))
    identity = {kind: inspected._pages_of(kind) for kind in READ_SLOTS}
    content = _content_kinds(inspected)
    # A page the content classifier is sure of beats an identity guess that had no evidence of its own: one that only borrowed its
    # kind from the page next to it, or took a visa's machine-readable zone for a passport, or a flight itinerary's number for an
    # Aadhaar card.
    # A certificate or letter that took the resume kind from the page before it (scanned experience letters and certificates run on
    # from a resume and are read with it) stays in the resume as well as being filed in its own field.
    # An EDUCATION certificate is the exception: it is read from its own piece, so it leaves the resume altogether (a certificate in
    # the middle of resume pages is simply skipped by the resume piece). Experience letters and application forms stay in both.
    alongside = {number for number, guess in content.items() if guess.alongside_identity and guess.kind != "education_certificate"}
    for page in inspected.pages:
        guess = content.get(page.page_number)
        if guess and guess.kind == "education_certificate" and page.document_type == "resume":
            pages = identity.get("resume", [])
            if page.page_number in pages:
                pages.remove(page.page_number)
        elif guess and page.document_type == "resume" and page.inherited and guess.kind == "experience_certificate":
            alongside.add(page.page_number)
        elif guess and not guess.alongside_identity and (guess.overrides_identity or (page.inherited and guess.confidence >= 0.7)):
            for pages in identity.values():
                if page.page_number in pages:
                    pages.remove(page.page_number)
    # A page that shows nothing of its own and only borrowed its kind from a neighbour belongs there when it FOLLOWS that
    # document (the back of a card, the next page of a resume). One that merely sits in front of a passport is not part of it.
    for page in inspected.pages:
        pages = identity.get(page.document_type, [])
        if page.status == "read" and page.inherited and page.page_number in pages and page.page_number - 1 not in pages:
            pages.remove(page.page_number)
    identity = {kind: pages for kind, pages in identity.items() if pages}
    taken = {number for pages in identity.values() for number in pages}
    # The other documents, by content: every read page that is not an identity page, and the certificates / forms that are also a
    # resume page and so are filed twice.
    by_kind: dict[str, list[int]] = {}
    for number, guess in content.items():
        if number not in taken or number in alongside:
            by_kind.setdefault(guess.kind, []).append(number)
    if not identity and not by_kind:
        return _whole(data, named or ["other"])
    blank = {page.page_number for page in inspected.pages if page.status == "blank"}
    # A photo or blank page stays with the identity document it follows (the passport's own photo page, the back of a card, a
    # resume's last sheet). It never follows an offer, a visa, a medical or any other document: there it is anyone's guess, so it
    # goes to the leftover pieces, never inside that document's cut.
    claimed = taken | {number for pages in by_kind.values() for number in pages}
    for number in everything:
        if number in blank and number not in claimed:
            for pages in identity.values():
                if number - 1 in pages:
                    pages.append(number)
                    claimed.add(number)
                    break
    leftover = [number for number in everything if number not in claimed]
    # What the name claims that no page showed. The left-over pages are filed there; with none left over the claim keeps the whole scan.
    unclaimed = [kind for kind in named if kind not in identity and kind not in by_kind]
    leftover_kinds = unclaimed or (["other"] if leftover else [])
    plan: list[tuple[list[str], list[int]]] = [([kind], sorted(pages)) for kind, pages in identity.items()]
    plan += [([kind], sorted(pages)) for kind, pages in by_kind.items()]
    if leftover_kinds:
        plan.append((leftover_kinds, leftover or everything))   # nothing left over: the named kinds keep the whole scan
    # Kinds on the very same pages are one piece with all their kinds - except a certificate or form that is ALSO a resume page: the
    # page stays in the resume's cut, and the certificate / form is its own cut so each field gets its own file.
    merged: dict[tuple, list[str]] = {}
    for kinds, pages in plan:
        alone = kinds[0] in by_kind and kinds[0] not in identity and set(pages) <= alongside
        into = merged.setdefault((tuple(pages), kinds[0] if alone else None), [])
        into += [kind for kind in kinds if kind not in into]
    pieces = [Piece(kinds, data if list(pages) == everything else _cut_pages(inspected, list(pages)), list(pages))
              for (pages, _), kinds in merged.items()]
    if len(pieces) == 1 and pieces[0].pages == everything:
        return SplitResult([Piece(pieces[0].kinds, data, [])], False)
    if set(everything) - {number for piece in pieces for number in piece.pages}:   # cannot happen; never lose a page
        raise ValueError("a page was lost")
    short = (original_name or "The file").rsplit("/", 1)[-1]
    parts = "; ".join(f"{' + '.join(KIND_LABELS.get(kind, kind) for kind in piece.kinds)} (page{'s' if len(piece.pages) != 1 else ''} "
                      f"{_page_list(piece.pages)})" for piece in pieces)
    borrowed = sorted(number for number, guess in content.items() if guess.inherited)
    careful = (f" Page{'s' if len(borrowed) != 1 else ''} {_page_list(borrowed)} took the kind of the page before "
               "(a continuation of the same document) and should be checked." if borrowed else "")
    kinds_of = {number: {"kind": guess.kind, "confidence": guess.confidence, "reason": guess.reason, "inherited": guess.inherited}
                for number, guess in content.items()}
    return SplitResult(pieces, True, f"{short} holds several documents and was split by what is on each page: {parts}.{careful}", kinds_of)


def _cut_pages(inspected, pages: list[int]) -> bytes:
    """A PDF of just these pages (original order). Scanners often give every page the same resource list, so a plain copy of two
    pages drags the pictures of all the others along (a 2-page piece as big as the 8-page scan); cleaning the pages' content
    drops what a page does not use. If cleaning is refused, the plain copy is used."""
    import pymupdf as fitz
    try:
        with fitz.open(stream=inspected._pdf, filetype="pdf") as source, fitz.open() as subset:
            for number in pages:
                subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
            return subset.tobytes(garbage=4, deflate=True, clean=True, no_new_id=True)
    except Exception:
        return inspected.pdf_for_pages(pages, allow_unresolved=True)


def cut_pdf_pages(data: bytes, pages: list[int]) -> bytes:
    """A PDF of just these pages (numbered as in the original, kept in order). Used for a scan whose pages belong to different
    people: each person gets their own pages, and the piece is then split into documents like any other file. Raises ValueError
    when none of the pages exist or the file is not a readable PDF."""
    import pymupdf as fitz
    try:
        with fitz.open(stream=data, filetype="pdf") as source:
            wanted = sorted({number for number in pages if 1 <= number <= source.page_count})
            if not wanted:
                raise ValueError("none of the pages exist")
            with fitz.open() as subset:
                for number in wanted:
                    subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
                try:
                    return subset.tobytes(garbage=4, deflate=True, clean=True, no_new_id=True)
                except Exception:
                    return subset.tobytes(deflate=True)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("the file could not be cut") from exc


def _page_list(pages: list[int]) -> str:
    runs: list[str] = []
    start = prev = pages[0]
    for number in pages[1:] + [0]:
        if number == prev + 1:
            prev = number
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = number
    return ", ".join(runs)


class ImportRefused(Exception):
    """Nothing was written; `plan` explains why."""

    def __init__(self, plan: dict):
        super().__init__("The candidate could not be imported.")
        self.plan = plan


def _store_bytes(settings, payload: bytes, filename: str, content_type: str, written: list[str]) -> tuple[str, str]:
    from app.api.routers.files import STORAGE, _safe_name
    key = f"{uuid.uuid4().hex}-{_safe_name(filename)}"
    object_key = document_storage.storage_key(settings, f"{key[:2]}/{key}")
    if object_key.startswith("s3://"):
        document_storage.put_bytes(settings, object_key, payload, content_type)
    else:
        target = STORAGE / key[:2] / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    written.append(object_key)
    return object_key, hashlib.sha256(payload).hexdigest()


STORE_THREADS = 4   # files of ONE candidate written to the object store side by side (each upload is a round trip over the internet)


def store_many(settings, items: list[tuple[bytes, str, str]], written: list[str]) -> list[tuple[str, str, float]]:
    """Store the files of one candidate: ``items`` is ``[(bytes, file name, content type)]``; the answer is
    ``[(object key, sha256, seconds)]`` in the SAME order.

    * On a remote object store up to ``STORE_THREADS`` files are uploaded side by side (twelve files over a 300 ms link took
      twelve times as long one after another); on local disk they are written one by one as before.
    * The same bytes twice are stored ONCE: the later item shares the first one's object key (0 seconds), as the rows of a file
      that counts as several documents already do.
    * All or nothing as far as the caller can tell: when one upload fails, uploads not yet started are skipped, the running ones
      finish, and the first failure (in file order) is raised. Every key that DID get stored is in ``written``, so the caller's
      ``discard_written`` removes exactly those; nothing half-stored is left behind and no row was written.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    first_of: dict[str, int] = {}
    unique: list[int] = []
    digests: list[str] = []
    for index, (payload, _name, _type) in enumerate(items):
        digest = hashlib.sha256(payload).hexdigest()
        digests.append(digest)
        if digest not in first_of:
            first_of[digest] = index
            unique.append(index)
    stored: dict[int, tuple[str, str, float]] = {}
    abort = threading.Event()

    def one(index: int) -> None:
        if abort.is_set():
            return
        payload, name, content_type = items[index]
        began = perf_counter()
        try:
            key, digest = _store_bytes(settings, payload, name, content_type, written)
        except BaseException:
            abort.set()
            raise
        stored[index] = (key, digest, perf_counter() - began)

    remote = document_storage.storage_key(settings, "00/x").startswith("s3://")
    if remote and len(unique) > 1:
        with ThreadPoolExecutor(min(STORE_THREADS, len(unique)), thread_name_prefix="store") as pool:
            futures = [pool.submit(one, index) for index in unique]
        for future in futures:                 # the pool has finished every upload that was running
            if future.exception() is not None:
                raise future.exception()
    else:
        for index in unique:
            one(index)
    return [stored[first_of[digest]] if index == first_of[digest] else (stored[first_of[digest]][0], digest, 0.0)
            for index, digest in enumerate(digests)]


def discard_written(settings, keys: list[str]) -> None:
    """Remove bytes written for a transaction that did not commit (best effort: an orphaned file cannot be reached from any row)."""
    from app.api.routers.files import STORAGE
    for object_key in keys:
        try:
            if object_key.startswith("s3://"):
                document_storage.delete_bytes(settings, object_key)
            else:
                name = object_key.rsplit("/", 1)[-1]
                (STORAGE / name[:2] / name).unlink(missing_ok=True)
        except Exception:
            pass


def _file_ref_of(attachment: Attachment) -> dict:
    return {"id": attachment.id, "name": attachment.file_name, "url": f"/api/files/{attachment.id}",
            "sizeKb": attachment.size_kb, "version": attachment.version}


def queue_reads(db: Session, user: User, candidate: Candidate, stored: dict[str, "Attachment | list[Attachment]"]) -> None:
    """Queue the documents to be read: each stored FILE once, however many of the kinds (SENT_TO_READER) it holds.

    A slot may map to several files (a passport's data page and its back page are two files of the passport slot): every one of
    them is queued, once per stored file, so the page that carries the machine-readable zone is never skipped because another file
    of the same slot was stored after it.

    The reader finds the resume, passport and Aadhaar pages inside one file by itself, so a scan that is both a passport and an
    Aadhaar card (or a resume with both attached) is one job, not one per document. Queueing it per document read the same
    pages again and again and made the machine crawl.
    """
    from app.services.application_form import queue_reading
    seen: set[str] = set()
    for slot in SENT_TO_READER:
        held = stored.get(slot)
        for attachment in (held if isinstance(held, list) else [held] if held is not None else []):
            if attachment.object_key not in seen:
                seen.add(attachment.object_key)
                queue_reading(db, user, candidate, attachment, attachment.content_type or "application/pdf")


@dataclass
class IdentityRead:
    """Who the documents of one candidate say they are (read locally, from the text the splitting already read)."""
    name: str | None = None
    passports: set[str] = field(default_factory=set)
    aadhaars: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)


def read_identity(uploads: list[Upload], settings=None, budget: float = 60.0) -> IdentityRead:
    """The candidate's name (and identifiers) from the passport, Aadhaar card and resume files, and only from them: passport
    machine-readable zone or printed name, the name line of the Aadhaar card, the first line of the resume (the same readers and
    the same "is this a person's name" test as the folder reading in people_by_content). The pages were read by `split_by_content`
    already; a file that was stored whole by its name (never read) is read here, once, within `budget` seconds. A name that does not
    look like a person's is never used. Never raises."""
    import time
    from app.services import duplicate_pages as dup, people_by_content as pbc
    found = IdentityRead()
    hits: list = []
    verified = False
    deadline = time.monotonic() + budget
    try:
        for upload in uploads:
            kind = next((item for item in upload.kinds if item in READ_SLOTS), None)
            if kind is None:
                continue
            count = dup.page_count(upload.data, upload.content_type or "")
            if not count:
                continue
            facts = list(upload.facts) if upload.facts and len(upload.facts) == count and all(item is not None for item in upload.facts) else None
            if facts is None:
                doc = dup.Doc(upload.path, upload.data, upload.content_type or "", list(range(1, count + 1)), [None] * count, True)
                if not dup.read_pages(doc, settings=settings, deadline=deadline):
                    continue
                facts = doc.reads
                upload.facts = facts
            for index, read in enumerate(facts):
                if read is None or read.status == "blank" or not (read.text or "").strip():
                    continue
                # only the first page of a resume file can carry the heading with the name: a later page starts mid-list or mid-sentence
                evidence = pbc.extract(read.text, kind, heading_ok=index == 0)
                hits += [hit for hit in evidence.names if hit.source in ("passport", "aadhaar", "resume") and not hit.weak and pbc.usable(hit.tokens)]
                verified = verified or any(evidence.passports.values())
                found.passports |= set(evidence.passports)        # the zone's checked number, or one printed next to "passport"
                found.aadhaars |= set(evidence.aadhaars)
                found.phones |= set(evidence.phones)
                found.emails |= set(evidence.emails)
    except Exception:
        pass
    tokens, _source = pbc.agreed_name(hits, verified_passport=verified)
    if tokens:
        found.name = pbc.display_name(tokens)
    return found


def hashes_of(uploads: list[Upload]) -> list[set[str]]:
    """Per filed upload, the SHA-256 of the file as stored and of the whole scan it was cut from (either is the same file)."""
    return [{hashlib.sha256(item).hexdigest() for item in (upload.data, upload.source) if item}
            for upload in uploads if upload.kinds != [SOURCE_BUNDLE]]


def candidates_with_files(db: Session, groups: list[set[str]]) -> tuple[Candidate, int] | None:
    """The existing candidate that already holds most of these files (an indexed lookup on the stored file hashes), and how many
    of the `groups` (one set of hashes per file) it holds; None unless it holds at least half of them. One shared file (a group
    ticket) is never enough for a folder of several."""
    everything = {digest for group in groups for digest in group}
    if not groups or not everything:
        return None
    rows = db.execute(select(Attachment.entity_id, Attachment.content_hash).where(
        Attachment.entity_type == "candidate", Attachment.deleted_at.is_(None), Attachment.content_hash.in_(everything))).all()
    held: dict[str, set[str]] = {}
    for entity_id, digest in rows:
        held.setdefault(entity_id, set()).add(digest)
    best: tuple[str, int] | None = None
    for entity_id, digests in held.items():
        count = sum(1 for group in groups if group & digests)
        if best is None or count > best[1]:
            best = (entity_id, count)
    if best is None or best[1] * 2 < len(groups):
        return None
    candidate = db.get(Candidate, best[0])
    return (candidate, best[1]) if candidate is not None else None


IMPORT_KEY = "_importKey"       # field_sources key: the client's idempotency key of the import that created the candidate


STRONG = ("import key", "same file", "passport")        # the same person for certain; phone / email / name alone are "possible"


def find_duplicate(db: Session, uploads: list[Upload], identity: "IdentityRead | None", import_key: str | None, row: dict | None = None) -> dict | None:
    """Is this person already in the CRM? Checked inside the import transaction, under the lock that serialises every candidate
    creation, BEFORE a Care India ID is taken: a double click or two browsers importing one folder make ONE candidate, and a person
    who is already there (a travelled candidate imported earlier, say) is never created again. Evidence, strongest first, never the
    folder or file NAMES:
      * the client's import key, or the very same files (half of them or more are already stored on one candidate): certain;
      * a passport number: read from the documents (machine-readable zone, or printed) or given by the sheet: certain;
      * a phone number or email (given by the sheet, or read from the documents), or the same typed full name: possible.
    (The CRM keeps only the last four digits of an Aadhaar number, so a card is matched by its file, not by its number.)"""
    row = row or {}
    found: tuple[Candidate, str, int] | None = None
    if import_key:
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        candidate = db.scalar(select(Candidate).where(cast(Candidate.field_sources, JSONB)[IMPORT_KEY].astext == import_key).limit(1))
        if candidate is not None:
            found = (candidate, "import key", 0)
    if found is None:
        held = candidates_with_files(db, hashes_of(uploads))
        if held is not None:
            found = (held[0], "same file", held[1])
    if found is None:
        numbers = sorted(identity.passports) if identity else []
        if row.get("passport_no"):
            numbers.insert(0, row["passport_no"])
        for number in numbers[:4]:
            hits = identity_matches(db, {"passport_no": number}, lock=False)
            if hits:
                found = (hits[0][0], "passport", 0)
                break
    if found is None:
        for label, key, values in (("phone", "phone", ([row["phone"]] if row.get("phone") else []) + (sorted(identity.phones) if identity else [])),
                                   ("email", "email", ([row["email"]] if row.get("email") else []) + (sorted(identity.emails) if identity else []))):
            for value in values[:3]:
                hits = identity_matches(db, {key: value}, lock=False)
                if hits:
                    found = (hits[0][0], label, 0)
                    break
            if found is not None:
                break
    if found is None and (row.get("name") or "").strip():
        from app.services.candidate_review import PENDING_NAME
        typed = row["name"].strip()
        if typed != PENDING_NAME:
            candidate = db.scalar(select(Candidate).where(func.lower(func.trim(Candidate.name)) == typed.lower()).limit(1))
            if candidate is not None:
                found = (candidate, "name", 0)
    if found is None:
        return None
    candidate, matched, files = found
    strength = "certain" if matched in STRONG else "possible"
    wording = {"import key": "this folder was already imported", "same file": "the same files", "passport": "same passport", "phone": "same phone number",
               "email": "same email", "name": "same name"}[matched]
    return {"candidateId": candidate.id, "careId": candidate.care_id, "ref": candidate.ref, "name": candidate.name, "matchedBy": matched,
            "strength": strength, "files": files, "choices": ["skip", "add_documents"] + (["import_anyway"] if strength == "possible" else []),
            "message": f"Already in the CRM as {candidate.care_id or candidate.ref} · {candidate.name} ({strength}: {wording})"}


def add_documents(db: Session, user: User, candidate: Candidate, uploads: list[Upload], written: list[str], settings,
                  duplicates: list | None = None) -> list[dict]:
    """File these uploads into an EXISTING candidate (the "Add these documents" choice for a duplicate): each as the next version of
    its document field, never overwriting; a file (or, for a PDF, every page of it) the field already holds is "already filed" and
    skipped; the scan the pages were cut from is kept under Source document evidence. Only documents are written: the candidate's
    details, status, owner, placements and stages are never touched. In the caller's transaction."""
    from app.api.routers.files import _safe_name
    from app.services import document_splitting as splitting
    now = datetime.now(timezone.utc)
    existing: dict[str, list[Attachment]] = {}
    for row in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id)
                          .order_by(Attachment.version.desc())):
        existing.setdefault(row.slot, []).append(row)
    taken = {row.file_name.lower() for rows in existing.values() for row in rows}
    person = splitting._person(candidate, "")
    answer: list[dict] = []
    packet_files: list[tuple[Upload, Attachment]] = []
    # A scan with a page that repeats a page of another file of this import is read as its new cut files, never as one packet.
    previously_filed: set[bytes] = {item.source for item in duplicates or [] if item.source is not None}
    for upload in sorted(uploads, key=lambda item: (item.kinds == [SOURCE_BUNDLE], item.path.lower())):
        digest = hashlib.sha256(upload.data).hexdigest()
        extension = PurePosixPath(upload.path.replace("\\", "/")).suffix.lower()
        object_key: str | None = None
        for kind in upload.kinds:
            if kind in ("photo", ADVANCE_RECEIPT):
                answer.append({"slot": kind, "label": KIND_LABELS.get(kind, kind), "fileName": upload.path.rsplit("/", 1)[-1], "pages": list(upload.pages or []),
                               "alreadyFiled": False, "added": False, "note": "not added to an existing candidate"})
                continue
            slot = kind if (kind in CANDIDATE_SLOTS or kind == "aadhaar" or kind == SOURCE_BUNDLE) else "other"
            versions = existing.setdefault(slot, [])
            live = [row for row in versions if row.deleted_at is None]
            same = next((row for row in live if row.content_hash == digest), None)
            if same is None and slot != SOURCE_BUNDLE and upload.content_type == "application/pdf":
                same = splitting._already_filed(splitting.PlannedPiece([slot], upload.data, list(upload.pages or []), upload.facts), slot, live, settings)
            entry = {"slot": slot, "label": KIND_LABELS.get(slot, slot), "pages": list(upload.pages or []), "added": same is None, "alreadyFiled": same is not None}
            if same is not None:
                if slot == SOURCE_BUNDLE:
                    packet_files.append((upload, same))
                elif slot in SENT_TO_READER and upload.source is not None:
                    # Reading the whole scan again would include a document that was already filed.
                    previously_filed.add(upload.source)
                answer.append({**entry, "fileName": same.file_name})
                continue
            if slot == SOURCE_BUNDLE:
                file_name = _safe_name((upload.original_name or upload.path).replace("\\", "/").rsplit("/", 1)[-1])
            else:
                file_name = _safe_name(import_file_name(person, [slot], upload.path, taken))
            if object_key is None:
                object_key, _ = _store_bytes(settings, upload.data, f"document-{uuid.uuid4().hex[:6]}{extension}", upload.content_type, written)
            row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=slot, version=(versions[0].version if versions else 0) + 1,
                             file_name=file_name, size_kb=max(1, round(len(upload.data) / 1024)), content_type=upload.content_type, object_key=object_key,
                             content_hash=digest, uploaded_by=user.id, uploaded_at=now)
            db.add(row)
            db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=file_name, entity_type="candidate", entity_id=candidate.id,
                              meta={"attachment_id": row.id, "slot": slot, "version": row.version, "bulk_import": True, "added_to_existing": True,
                                    "original_name": (upload.original_name or upload.path).rsplit("/", 1)[-1][:200],
                                    **({"split_pages": upload.pages} if upload.pages else {}),
                                    **({"extraction_deferred": True} if slot in SENT_TO_READER else {})}, at=now))
            versions.insert(0, row)
            packet_files.append((upload, row))
            answer.append({**entry, "fileName": file_name})
    db.add(AuditEntry(actor_id=user.id, action="Added documents from an import", target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"bulk_import": True, "added": sum(1 for item in answer if item["added"]), "already_filed": sum(1 for item in answer if item["alreadyFiled"])}, at=now))
    db.flush()
    _register_import_packets(db, candidate, packet_files, previously_filed)
    return answer


def import_candidate(db: Session, user: User, ctx: Context, *, folder: str, row: dict | None, uploads: list[Upload],
                     written: list[str], name_is_stand_in: bool = False, name_from_files: bool = False,
                     duplicates: list | None = None, name_source: str | None = None, identity: "IdentityRead | None" = None,
                     import_key: str | None = None, on_duplicate: str = "skip") -> dict:
    """Create the candidate, store and attach every document and record the placement the plan describes.

    Names: with `name_source` ("sheet" | "documents" | "pending", what the Import screen's endpoint decided) the folder name is NEVER
    the candidate's name: `row["name"]` holds the sheet's or the document-read name, and without one the candidate is called
    `PENDING_NAME` with the provisional-name marker (the first reading of a document replaces it). Their files are named
    "<Name>-<Document>.<ext>", or "<Care India ID>-<Document>.<ext>" while no name is known. Without `name_source` (the library
    import script) the folder name is the name, as before.

    Duplicates (`find_duplicate`): under the lock that serialises candidate creation, BEFORE a Care India ID is taken, the import
    key, the files and the passport / phone / email (given by the sheet or read from the documents) are checked against the CRM.
    A person who is already there is never created again: the answer is {"status": "duplicate" | "already_imported", careId,
    candidateId, name, matchedBy, strength: "certain" | "possible", choices} and nothing is written or numbered. `on_duplicate`
    is the person's choice from `choices`: "skip" (the default: report only), "add_documents" (file these documents into the
    existing candidate, answer {"status": "documents_added", ...}) or "import_anyway" (only for a "possible" match: create the
    candidate after all).

    Everything happens in the caller's transaction; the caller commits, or rolls back and calls `discard_written(written)`.
    Raises ImportRefused, writing nothing, when the plan is a skip.

    `duplicates` (`duplicate_pages.Skipped`) are the pages that were left out because the same page is already filed: each is
    written to the audit trail and answered in `result["duplicates"]` / `result["documents"][n]["duplicates"]`. An upload of kind
    `source_bundle` is the whole scan such pages were cut from, kept as received under "Source document evidence".
    """
    from app.api.routers.applications import _sync_order, _task
    from app.api.routers.candidates import lock_assignment_cursor
    from app.api.routers.files import _safe_name
    from app.db.models import Designation
    from app.services.care_india_id import allocate_care_id
    from app.services.traveller_id import assign_traveller_id
    from app.services.lead_xlsx import agency_zone
    settings = get_settings()

    # Candidate identity, reference and ownership decisions are serialised the same way the add-candidate screen does.
    lock_assignment_cursor(db)
    if name_source is not None:
        existing = find_duplicate(db, uploads, identity, import_key, row)
        if existing is not None and not (on_duplicate == "import_anyway" and existing["strength"] == "possible"):
            if on_duplicate == "add_documents":
                target = db.get(Candidate, existing["candidateId"])
                filed = add_documents(db, user, target, uploads, written, settings, duplicates)
                return {"status": "documents_added", "action": "skip", "folder": folder, **existing, "documents": filed,
                        "issues": [{"level": "info", "message": f"{existing['message']}. Documents were added to it ({sum(1 for f in filed if f['added'])} filed, "
                                                                f"{sum(1 for f in filed if f['alreadyFiled'])} already there); nothing else of the candidate was changed."}]}
            return {"status": "already_imported" if existing["matchedBy"] == "import key" else "duplicate", "action": "skip", "folder": folder, **existing,
                    "issues": [{"level": "info", "message": f"{existing['message']}. Nothing was imported."}]}
    plan = plan_candidate(db, ctx, folder=folder, row=row, files=[{"path": u.path, "kinds": u.kinds} for u in uploads
                                                                    if u.kinds != [SOURCE_BUNDLE]],
                          folder_names=name_source is None, name_source=name_source, check_duplicates=on_duplicate != "import_anyway")
    if plan["action"] == "skip":
        raise ImportRefused(plan)
    row = dict(row or {})
    owner = ctx.recruiter(row.get("recruiter"))
    order = ctx.order(row.get("order")) if plan["action"] in {"in_progress", "deploy"} else None
    now = datetime.now(timezone.utc)

    # The slow part (writing every file to the document store) comes BEFORE the Care India ID is taken: that ID holds a global
    # lock until the commit, so taking it first made every import wait behind the slowest upload.
    # A scan that holds several documents goes first, so a dedicated file of the same kind ends up as the current version.
    # The files are stored under a neutral name; the name they are listed under ("<Name>-<Document>" or "<CI-167>-<Document>") is set
    # below, once the Care India ID exists.
    to_attach: list[tuple[Upload, str, str, str]] = []
    ordered = sorted(uploads, key=lambda item: (item.kinds == [SOURCE_BUNDLE], len(item.kinds) == 1, item.path.lower()))
    store_started = perf_counter()
    # Once, whatever the file counts as; the files of this candidate go up side by side (see store_many).
    outcome = store_many(settings, [(upload.data, f"document-{number}{PurePosixPath(upload.path.replace(chr(92), '/')).suffix.lower()}",
                                     upload.content_type) for number, upload in enumerate(ordered, 1)], written)
    for upload, (object_key, digest, _seconds) in zip(ordered, outcome):
        to_attach.append((upload, "", object_key, digest))
    store_seconds = perf_counter() - store_started
    per_file = [{"index": number, "sizeKb": max(1, round(len(upload.data) / 1024)), "storeSeconds": round(seconds, 2),
                 "sharedKey": outcome[number][0] in {item[0] for item in outcome[:number]}}
                for number, (upload, (_key, _digest, seconds)) in enumerate(zip(ordered, outcome))]

    phone = plan["phone"] or ""
    candidate = Candidate(
        ref=f"CND-{db.scalar(select(func.nextval('crm_candidate_reference_seq'))):05d}",
        care_id=allocate_care_id(db),
        name=plan["name"], phone=phone, email=plan["email"], passport_no=plan["passportNo"],
        father_name=None, marital_status=None, current_address=None, education=[], work_history=[], experience_india=0,
        native_place=None, family_member_number=None, job_duration_preference=None, country_preference=None,
        best_time_to_contact=None, service_charges_status="not_discussed", interest_level=None, next_follow_up_date=None,
        source=Source.referral if (row or {}).get("agency") else Source.other,
        source_detail=" ".join(str(row["agency"]).split())[:255] if (row or {}).get("agency") else "Bulk import",
        status=(CandidateStatus.deployed if plan["action"] == "deploy" else CandidateStatus.in_process if order
                else CandidateStatus.new if phone else CandidateStatus.needs_review),
        # No phone yet: the reading of the resume and passport fills it in and lifts the flag by itself.
        needs_review=not phone, owner_id=owner.id, intake_owner_id=owner.id, created_by=user.id,
        # The folder name is only a stand-in: the name on the resume, passport or any other document replaces it.
        # (A name read from the files on the Import screen is just as provisional, and says so: "files".)
        field_sources=({**({} if ((row or {}).get("name") and not name_is_stand_in) else {"_provisionalName": "files" if name_from_files else True})}
                       if name_source is None else
                       {**({} if name_source == "sheet" else {"_provisionalName": name_source}),
                        **({IMPORT_KEY: import_key} if import_key else {})}),
    )
    for key, value in normalize_candidate_values({"phone": phone, "email": plan["email"], "passport_no": plan["passportNo"]}).items():
        setattr(candidate, key, value)
    if order is not None:
        designation = db.get(Designation, order.designation_id)
        if designation is not None:
            candidate.designations = normalize_candidate_values({"designations": [designation.title]})["designations"]
    db.add(candidate)
    db.flush()

    # Each file is stored once. A candidate document row is written for every candidate slot; a later file of the same
    # kind becomes the next version. The stage 9 receipt is not a candidate document, only stage evidence.
    stored: dict[str, Attachment] = {}
    packet_files: list[tuple[Upload, Attachment]] = []
    receipt: Attachment | None = None
    # The listed name of a file: "<Name>-<Document>.<ext>"; "<CI_167>-<Document>.<ext>" while no name was read from the documents
    # (a later reading renames "<CI_167>-..." to the real name, see application_form.rename_imported_files).
    from app.services.candidate_review import PENDING_NAME
    person = candidate.care_id if plan["name"] == PENDING_NAME else plan["name"]
    taken: set[str] = set()
    named_attach = []
    for upload, _, object_key, digest in to_attach:
        if upload.kinds == [SOURCE_BUNDLE]:     # the scan as received: it keeps its own name
            file_name = _safe_name(upload.path.replace("\\", "/").rsplit("/", 1)[-1])
        else:
            file_name = _safe_name(import_file_name(person, upload.kinds, upload.path, taken))
        named_attach.append((upload, file_name, object_key, digest))
    to_attach = named_attach
    for upload, file_name, object_key, digest in to_attach:
        common = dict(file_name=file_name, size_kb=max(1, round(len(upload.data) / 1024)), content_type=upload.content_type,
                      object_key=object_key, content_hash=digest, uploaded_by=user.id, uploaded_at=now)
        for kind in upload.kinds:
            if kind == SOURCE_BUNDLE:
                bundle = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=SOURCE_BUNDLE, version=1, **common)
                previous = stored.get(SOURCE_BUNDLE)
                bundle.version = (previous.version + 1) if previous else 1
                db.add(bundle)
                db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=file_name, entity_type="candidate", entity_id=candidate.id,
                                  meta={"attachment_id": bundle.id, "slot": SOURCE_BUNDLE, "version": bundle.version, "bulk_import": True,
                                        "kept_original": True, "original_name": (upload.original_name or upload.path).rsplit("/", 1)[-1][:200]}, at=now))
                stored[SOURCE_BUNDLE] = bundle
                packet_files.append((upload, bundle))
                continue
            if kind not in CANDIDATE_SLOTS and kind != "aadhaar":
                receipt = Attachment(id=str(uuid.uuid4()), entity_type="application", entity_id="", slot="", version=1, **common)
                continue
            previous = stored.get(kind)
            attachment = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=kind,
                                    version=(previous.version + 1) if previous else 1, **common)
            db.add(attachment)
            db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=file_name, entity_type="candidate", entity_id=candidate.id,
                              meta={"attachment_id": attachment.id, "slot": kind, "version": attachment.version, "bulk_import": True,
                                  "original_name": (upload.original_name or upload.path).rsplit("/", 1)[-1][:200],
                                  **({"split_pages": upload.pages} if upload.pages else {}),
                                  # Stored, not read: the profile and the form offer "Extract details" for it (see document_reading.pending_expression).
                                  **({"extraction_deferred": True} if kind in SENT_TO_READER else {})}, at=now))
            stored[kind] = attachment
            packet_files.append((upload, attachment))
    db.flush()
    # A scan with a page that repeats a page already filed (the standalone resume and the same pages inside the packet) is not read as
    # one packet: that would send the repeated pages to the paid reader a second time. Its new pages are read as their own cut files.
    _register_import_packets(db, candidate, packet_files, {item.source for item in duplicates or [] if item.source is not None})
    # The import only SPLITS the files into their documents and stores them. Reading them (OCR) starts when a person asks for
    # it (Extract details on the candidate's form, or "Read the documents" on the Import screen), never by itself.
    result = {"candidateId": candidate.id, "ref": candidate.ref, "careId": candidate.care_id, "name": candidate.name, "action": plan["action"], "issues": plan["issues"],
              "files": sum(1 for upload in uploads if upload.kinds != [SOURCE_BUNDLE]), "order": None, "completedThrough": 0, "currentStage": None,
              "status": "imported", "nameSource": plan["nameSource"],
              # Where the time went (numbers only, files by their position: no names). The endpoint adds what it measured itself.
              "timings": {"storeSeconds": round(store_seconds, 2), "perFile": per_file}}
    # What was filed, and which pages were NOT filed again because the same page is already in one of these files.
    names = {id(upload): file_name for upload, file_name, _, _ in to_attach}
    skipped = list(duplicates or [])
    result["documents"] = [
        {"fileName": file_name, "kinds": list(upload.kinds), "originalName": (upload.original_name or upload.path).rsplit("/", 1)[-1],
         "pages": list(upload.pages or []),
         "duplicates": [item.as_dict(names) for item in skipped if item.keeper is upload]}
        for upload, file_name, _, _ in to_attach if upload.kinds != [SOURCE_BUNDLE]]
    result["duplicates"] = [item.as_dict(names) for item in skipped]
    for item in skipped:
        db.add(AuditEntry(actor_id=user.id, action="Skipped duplicate pages", target=item.file, entity_type="candidate", entity_id=candidate.id,
                          meta={"bulk_import": True, "slot": item.kind, "pages": item.pages, "same_as_file": item.same_as_file,
                                "same_as_pages": item.same_as_pages, "kept_as": names.get(id(item.keeper)), "reason": item.why,
                                "message": item.message()}, at=now))

    if order is None:
        db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest", candidate_id=candidate.id, owner_id=owner.id,
                    created_at=now, sla_started_at=now, due_at=now))
        db.add(AuditEntry(actor_id=user.id, action="Imported candidate with documents", target=candidate.name,
                          entity_type="candidate", entity_id=candidate.id, meta={"bulk_import": True, "placement": False}, at=now))
        return result

    deploy = plan["action"] == "deploy"
    completed, current = plan["completedThrough"], plan["currentStage"]
    locked = db.scalar(select(Order).where(Order.id == order.id).with_for_update().execution_options(populate_existing=True))
    if deploy:
        taken = db.scalar(select(func.count()).select_from(Application).where(
            Application.order_id == locked.id, Application.status == ApplicationStatus.deployed)) or 0
        if taken >= locked.openings:  # someone else took the last opening since the plan; leave the last stage open instead
            deploy, completed, current = False, TOTAL_STAGES - 1, TOTAL_STAGES
    application = Application(candidate_id=candidate.id, order_id=locked.id, owner_id=owner.id,
                              current_stage=TOTAL_STAGES if deploy else current, status=ApplicationStatus.deployed if deploy else ApplicationStatus.active,
                              lock_version=0, draft={})
    db.add(application)
    db.flush()

    # Stage evidence is a second row on the application for every stage reached so far, pointing at the same stored file.
    reach = TOTAL_STAGES if deploy else current
    refs: dict[str, dict] = {}
    for kind, (stage, field_name) in STAGE_FILES.items():
        source = receipt if kind == ADVANCE_RECEIPT else stored.get(kind)
        if source is None or stage > reach:
            continue
        copy = Attachment(id=str(uuid.uuid4()), entity_type="application", entity_id=application.id, slot=f"stage_{stage}_{field_name}",
                          version=1, file_name=source.file_name, size_kb=source.size_kb, content_type=source.content_type,
                          object_key=source.object_key, content_hash=source.content_hash, uploaded_by=user.id, uploaded_at=now)
        db.add(copy)
        refs[kind] = _file_ref_of(copy)
    if "passport" in stored:
        refs["passport"] = _file_ref_of(stored["passport"])

    built, _ = stage_inputs(row, refs, ctx.options)
    when = stage_dates({n: built[n] for n in range(1, TOTAL_STAGES + 1)}, now, agency_zone())
    for number in range(1, (TOTAL_STAGES if deploy else completed) + 1):
        data = validate_payload(number, built[number], ctx.options if number == ADVANCE_STAGE else None)
        outcome = stage_by_no(number).evaluate(for_rules(number, data, ctx.historical, ctx.travel_confirmed), ctx.options if number == ADVANCE_STAGE else None).outcome
        db.add(ApplicationStage(
            application_id=application.id, stage=number, attempt=1, data=data, outcome=outcome, completed_by=user.id,
            owner_id=owner.id, completed_at=when[number],
            amount=data.get("amount") if number == ADVANCE_STAGE and data.get("advanceStatus") != "pending" else None, amount_currency=data.get("amountCurrency") if number == ADVANCE_STAGE else None))
    if deploy:
        _sync_order(db, locked)
        # They have travelled: they get the next number in the traveller series (CI-T-001, CI-T-002 ...).
        assign_traveller_id(db, candidate)
    else:
        # The stage the recruiter continues at: its task, and what the sheet already says as the saved draft.
        if current <= plan["level"]:
            application.draft = validate_payload(current, built[current], ctx.options if current == ADVANCE_STAGE else None)
        elif current == PASSPORT_STAGE and "passport" in refs:
            application.draft = {"copy": refs["passport"]}
        if application.draft:
            application.draft_saved_at, application.draft_saved_by = now, user.id
        _task(db, application, candidate, now)
    db.add(AuditEntry(actor_id=user.id, action="Imported candidate with documents and stage history", target=candidate.name,
                      entity_type="application", entity_id=application.id, at=now,
                      meta={"bulk_import": True, "order_id": locked.id, "owner_id": owner.id, "completed_stages": TOTAL_STAGES if deploy else completed}))
    result.update(order=locked.ref, applicationId=application.id, completedThrough=TOTAL_STAGES if deploy else completed,
                  currentStage=None if deploy else current, action="deploy" if deploy else "in_progress")
    return result
