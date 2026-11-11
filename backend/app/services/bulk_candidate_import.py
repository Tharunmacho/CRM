"""Bulk import of candidates who already went through the whole process (a folder of documents per person).

Typing 30 finished placements by hand means 30 candidate forms, 30 x 12 uploads and 30 x 11 stage screens. This module is the
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
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
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
from app.domain.stages import StageOptions, stage_by_no, validate_payload
from app.services import document_storage
from app.services.application_form import SLOT_LABELS, SLOT_SPECS, deployment_gaps
from app.services.candidate_identity import identity_matches, normalize_candidate_values, normalize_email, normalize_passport, normalize_phone

# --------------------------------------------------------------------------------------------- document kinds

ADVANCE_RECEIPT = "advance_receipt"
# Every document the folder can hold: the form's own slots, plus the stage 8 receipt that is not a candidate document.
KIND_LABELS: dict[str, str] = {**SLOT_LABELS, ADVANCE_RECEIPT: "Advance receipt (stage 8)", "aadhaar": "Aadhaar card"}
CANDIDATE_SLOTS = tuple(spec[0] for spec in SLOT_SPECS)
READ_SLOTS = ("resume", "passport", "aadhaar")  # the only documents that are read (OCR); they fill in the profile
IMAGE_ONLY = ("photo",)
# Candidate document -> (stage, field) whose evidence it is. The same stored file is attached to both places.
STAGE_FILES: dict[str, tuple[int, str]] = {
    "signed_offer": (5, "file"), ADVANCE_RECEIPT: (8, "receipt"), "visa": (9, "file"),
    "visa_stamping": (10, "file"), "air_ticket": (11, "file"),
}
SUPPORTED_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".doc", ".docx")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic")

# (kind, substrings that match anywhere in the squashed name, whole words). Order matters: the first rule that matches wins,
# so the more specific names ("visa stamping", "passport size photo") come before the general ones ("visa", "passport").
_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("photo", ("photo", "picture", "selfie", "passportsize", "ppsize"), ("pic",)),
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
               "mason", "carpenter", "electrician", "plumber", "painter", "foreman", "rigger", "cook", "chef", "mechanic", "operator"}


def _person_in(path: str) -> tuple[str, str] | None:
    """(initials, name word) when the file name starts with a person's name: "K.Sivakumar-offer.jpg" -> ("K.", "Sivakumar")."""
    stem = PurePosixPath(path.replace("\\", "/")).stem
    first = re.split(r"[-_ ]+", stem.strip(), maxsplit=1)[0]
    initials = re.match(r"^((?:[A-Za-z]\.)+)", first)
    word = first[len(initials.group(1)):] if initials else first
    if len(word) >= 4 and word.isalpha() and word.lower() not in _NOT_A_NAME:
        return (initials.group(1) if initials else ""), word
    return None


def split_people(paths: list[str]) -> dict | None:
    """Separate a folder that holds several people, by the name each file name starts with. None for an ordinary folder.

    {"people": [{"name", "paths"}], "shared": [paths given to everyone, e.g. a group air ticket], "left_out": [paths
    that cannot be told apart]}. A file with no name in it is shared only when it is an air ticket (a "3 PAX" ticket
    covers everyone); any other nameless file is left out rather than put on the wrong person.
    """
    groups: dict[str, dict] = {}
    nameless: list[str] = []
    for path in paths:
        person = _person_in(path)
        if person is None:
            nameless.append(path)
            continue
        initials, word = person
        group = groups.setdefault(word[:5].lower(), {"paths": [], "words": [], "initials": []})
        group["paths"].append(path)
        group["words"].append(word)
        if initials:
            group["initials"].append(initials.upper())
    if len(groups) < 2:
        return None
    people = []
    for group in groups.values():
        word = max(dict.fromkeys(group["words"]), key=group["words"].count)   # the most common spelling, first seen on a tie
        initials = group["initials"][0] if group["initials"] else ""
        name = (initials.replace(".", ". ").strip() + " " + word.capitalize()).strip()
        people.append({"name": name, "paths": list(group["paths"])})
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
    Column("passport_collected_date", "Passport collected on", "Stage 7. Optional.", aliases=("passportcollected", "passportdate")),
    Column("advance_amount", "Advance amount", "Stage 8, the amount collected. Needed.", True, ("advance", "amount", "advancepayment", "advancecollected")),
    Column("advance_currency", "Advance currency", "Stage 8, e.g. INR. Defaults to the agency's advance currency.", aliases=("currency",)),
    Column("advance_date", "Advance date", "Stage 8. Optional.", aliases=("advancedate", "paymentdate")),
    Column("advance_mode", "Advance mode", "Stage 8: cash, bank, upi or cheque. Optional.", aliases=("paymentmode",)),
    Column("receipt_no", "Receipt number", "Stage 8. Optional.", aliases=("receipt", "receiptno", "receiptnumber")),
    Column("visa_arrival_date", "Visa arrived on", "Stage 9. Optional.", aliases=("visadate", "visaarrived", "visaarrival")),
    Column("google_review", "Google review", "Stage 9: yes or no. Optional.", aliases=("review", "googlereview")),
    Column("stamping_date", "Visa stamped on", "Stage 10. Optional.", aliases=("stamping", "visastamping", "stampeddate")),
    Column("booked_by", "Ticket booked by", "Stage 11: employer or agency. Optional.", aliases=("ticketbookedby", "bookedby")),
    Column("travel_date", "Travel date", "Stage 11. Optional.", aliases=("traveldate", "flightdate", "departure")),
    Column("airline", "Airline and flight", "Stage 11. Optional.", aliases=("flight", "airlineflight", "airlineandflight")),
    Column("pnr", "PNR", "Stage 11. Optional.", aliases=("pnrno", "bookingreference")),
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


def parse_yes_no(raw: Any) -> bool | None:
    text = str(raw or "").strip().lower()
    if text in {"yes", "y", "true", "1", "done", "ok", "obtained"}:
        return True
    if text in {"no", "n", "false", "0", "not done"}:
        return False
    return None


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
    """The eleven stage payloads for a finished placement, and the problems with the sheet's values.

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
        5: clean({"obtained": "yes", "file": _file_ref("signed_offer", files)}),
        6: clean({"applied": "yes", "appliedDate": medical, "result": "fit"}),
        7: clean({"collected": "yes", "collectedDate": collected, "copy": _file_ref("passport", files)}),
        8: clean({"collected": "yes", "amount": str(amount) if amount is not None else None, "amountCurrency": currency,
                  "date": advance_date, "mode": pay_mode, "receiptNo": row.get("receipt_no"), "receipt": _file_ref(ADVANCE_RECEIPT, files)}),
        9: clean({"arrived": "yes", "date": visa, "file": _file_ref("visa", files), "googleReview": review}),
        10: clean({"done": "yes", "date": stamping, "file": _file_ref("visa_stamping", files)}),
        11: clean({"bookedBy": booked, "travelDate": travel, "airline": row.get("airline"), "pnr": row.get("pnr"), "file": _file_ref("air_ticket", files)}),
    }
    return stages, problems


HISTORY_FILE = {"id": "history"}  # stands in for a file only while the rules are asked; it is never stored


def for_rules(number: int, data: dict, historical: bool = False, travel_confirmed: bool = False) -> dict:
    """What the stage rules are asked about an imported stage.

    Two things a folder of documents cannot say are allowed to stay blank for imported history: the interview date and the
    advance amount (the receipt is the evidence). The stored record keeps them blank; only the check is satisfied.

    For someone who has already travelled (`historical`), the later stages prove the earlier ones happened, so the files
    those earlier stages would normally need (offer copy, advance receipt and amount, stamped visa) may be missing too. The
    ticket (stage 11) is never waived: it is the proof of travel.
    """
    if number == 3 and data.get("scheduled") and not data.get("date"):
        return {**data, "date": "2000-01-01"}
    if number == 8 and data.get("collected") and not data.get("amount") and data.get("receipt"):
        return {**data, "amount": "1"}
    if historical:
        if number == 5 and data.get("obtained") and not data.get("file"):
            return {**data, "file": HISTORY_FILE}
        if number == 8 and data.get("collected"):
            return {**data, "amount": data.get("amount") or "1", "receipt": data.get("receipt") or HISTORY_FILE}
        if number == 10 and data.get("done") and not data.get("file"):
            return {**data, "file": HISTORY_FILE}
        if number == 11 and travel_confirmed and not data.get("file"):
            return {**data, "file": HISTORY_FILE}
    return data


def evaluate_stages(stages: dict[int, dict], options: StageOptions, historical: bool = False, travel_confirmed: bool = False) -> list[dict]:
    """[{stage, title, ok, missing}] - the same completion rules the stage screens apply."""
    report = []
    for number in range(1, 12):
        definition = stage_by_no(number)
        result = definition.evaluate(for_rules(number, stages[number], historical, travel_confirmed), options if number == 8 else None)
        report.append({"stage": number, "title": definition.title, "ok": bool(result.can_complete and not result.closes and not result.repeats),
                       "missing": result.missing})
    return report


def stage_dates(stages: dict[int, dict], now: datetime, zone) -> dict[int, datetime]:
    """When each stage is recorded as completed: its own date at noon, never earlier than the stage before, never in the future."""
    keys = {2: "dateForwarded", 3: "date", 6: "appliedDate", 7: "collectedDate", 8: "date", 9: "date", 10: "date", 11: "travelDate"}
    stamps: dict[int, datetime] = {}
    previous: datetime | None = None
    known = [parse_date(stages[n].get(key)) for n, key in keys.items() if stages[n].get(key)]
    first = min((d for d in known if d), default=None)
    for number in range(1, 12):
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
        7: bool(row.get("passport_collected_date")), 8: bool(row.get("advance_amount")) or ADVANCE_RECEIPT in kinds,
        9: bool(row.get("visa_arrival_date")) or "visa" in kinds, 10: bool(row.get("stamping_date")) or "visa_stamping" in kinds,
        11: "air_ticket" in kinds or bool(row.get("travel_date")) or bool(row.get("pnr")),
    }
    return max((number for number, present in shown.items() if present), default=0)


def plan_candidate(db: Session, ctx: Context, *, folder: str, files: list[dict], row: dict | None) -> dict:
    """What importing this candidate would do, without writing anything.

    `files` are [{path, kind?}]; `kind` is the user's choice when they changed the suggestion. The result's `action` is
    skip (an error stops it), documents_only (no order to put them on), in_progress (stages recorded up to the first one
    that lacks something; the rest is done on the stage screen) or deploy (all eleven stages recorded).
    """
    row = dict(row or {})
    issues: list[dict] = []
    name = (row.get("name") or _clean_name(folder)).strip()
    row["name"] = name
    planned_files, by_kind = [], {}
    for item in files:
        path = item["path"]
        kinds = [item["kind"]] if item.get("kind") else list(item.get("kinds") or classify_path_kinds(path))
        problem = file_problem(path)
        entry = {"path": path, "kind": kinds[0] if kinds else None, "kinds": kinds, "skipped": bool(problem), "problem": problem}
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
    if unnamed:
        issues.append({"level": "info", "message": f"{unnamed} file{'s' if unnamed != 1 else ''} with no clue in the name will be checked for a resume, passport or Aadhaar card when you import."})
    for kind, entries in by_kind.items():
        if len(entries) > 1 and kind != "other":
            issues.append({"level": "warning", "message": f"{len(entries)} files are marked {KIND_LABELS[kind]}. The last one by name is the current version; the rest are kept as earlier versions."})

    phone, email, passport = (row.get("phone") or "").strip(), (row.get("email") or "").strip(), (row.get("passport_no") or "").strip()
    if not name:
        issues.append({"level": "error", "message": "No name. Name the folder after the candidate."})
    if phone and not normalize_phone(phone):
        issues.append({"level": "error", "message": f"Phone '{phone}' is not a valid number (at least seven digits)."})
    duplicate = None
    if not any(i["level"] == "error" for i in issues):
        found = identity_matches(db, {"phone": phone, "email": email, "passport_no": passport}, lock=False) if (
            normalize_phone(phone) or normalize_email(email) or normalize_passport(passport)) else []
        if found:
            duplicate = found[0][0]
        elif name:  # no phone yet: the same name is the best sign this person is already in
            duplicate = db.scalar(select(Candidate).where(func.lower(func.trim(Candidate.name)) == name.lower()).limit(1))
        if duplicate is not None:
            issues.append({"level": "error", "message": f"Already in the CRM as {duplicate.ref} ({duplicate.name}). Nothing is imported for this folder."})
    owner = ctx.recruiter(row.get("recruiter"))
    if owner is None:
        issues.append({"level": "error", "message": f"Recruiter '{row['recruiter']}' was not found among active users." if row.get("recruiter")
                       else "No recruiter could be chosen."})

    kinds = set(by_kind)
    refs = {kind: {"id": "pending", "name": entries[-1]["path"].rsplit("/", 1)[-1]} for kind, entries in by_kind.items()}
    level = 11 if ctx.travel_confirmed else evidence_level(row, kinds)
    built, problems = stage_inputs(row, refs, ctx.options)
    issues.extend(problems)
    shown = {number: (built[number] if number <= level else {}) for number in range(1, 12)}
    report = evaluate_stages(shown, ctx.options, ctx.historical, ctx.travel_confirmed)
    completed = 0
    for item in report:
        if not item["ok"]:
            break
        completed = item["stage"]
    gaps = deployment_gaps({kind for kind in kinds if kind in CANDIDATE_SLOTS})

    order = ctx.order(row.get("order"))
    action, notes = "documents_only", []
    if order is None:
        notes.append(f"Order '{row['order']}' was not found, so no job is attached." if row.get("order")
                     else "No order was given, so no job is attached. The candidate and the documents are imported.")
    elif completed < 11 and order.status != OrderStatus.open:
        notes.append(f"{order.ref} is not open, so no job is attached. Open it and map the candidate from the stage screen.")
    else:
        action = "in_progress"
        if completed == 11:
            used = ctx.deployed.get(order.id, 0) + ctx.planned.get(order.id, 0)
            if used >= order.openings:
                completed = 10
                notes.append(f"{order.ref} has no free opening, so the last stage is left for you to finish after raising the openings.")
            elif gaps and not ctx.historical:
                completed = 10
                notes.append("The last stage is left open: these documents are still needed before deploying: " + ", ".join(g["label"] for g in gaps) + ".")
            else:
                action = "deploy"
    if action == "deploy" and order is not None:
        ctx.planned[order.id] = ctx.planned.get(order.id, 0) + 1
    current = None if action == "deploy" else completed + 1
    if action == "in_progress" and current is not None and current <= 11:
        title = stage_by_no(current).title
        missing = next((item["missing"] for item in report if item["stage"] == current), [])
        lead = f"Stages 1 to {completed} are recorded as done. " if completed else "No stage is recorded yet. "
        notes.append(lead + f"It continues at stage {current} ({title})" + (f": {'; '.join(missing)}." if missing and current <= level else "."))
    if action in {"in_progress", "deploy"}:
        if completed >= 3 and not row.get("interview_date"):
            notes.append("No interview date was given, so the interview stage is recorded without one.")
        if completed >= 8 and not row.get("advance_amount"):
            notes.append("No advance amount was given, so the advance is recorded from the receipt without an amount.")
    issues.extend({"level": "info", "message": note} for note in notes)
    if gaps:
        issues.append({"level": "info", "message": "Documents not found: " + ", ".join(g["label"] for g in gaps) + ". Upload them later from the profile."})
    if any(i["level"] == "error" for i in issues):
        action = "skip"
    return {
        "folder": folder, "name": name, "phone": phone or None, "email": email or None, "passportNo": passport or None,
        "order": order.ref if order else (row.get("order") or None), "recruiter": owner.name if owner else (row.get("recruiter") or None),
        "files": planned_files, "issues": issues, "stages": report if action != "documents_only" else [], "level": level,
        "completedThrough": completed if action in {"in_progress", "deploy"} else 0, "currentStage": current if action == "in_progress" else None,
        "missingDocuments": [gap["label"] for gap in gaps], "action": action, "duplicateOf": duplicate.ref if duplicate else None,
    }


# --------------------------------------------------------------------------------------------- the import

@dataclass
class Upload:
    path: str
    kinds: list[str]
    data: bytes
    content_type: str


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


def queue_reads(db: Session, user: User, candidate: Candidate, stored: dict[str, Attachment]) -> None:
    """Queue the documents to be read: each stored FILE once, however many of the three it holds.

    The reader finds the resume, passport and Aadhaar pages inside one file by itself, so a scan that is both a passport and an
    Aadhaar card (or a resume with both attached) is one job, not one per document. Queueing it per document read the same
    pages again and again and made the machine crawl.
    """
    from app.services.application_form import queue_reading
    seen: set[str] = set()
    for slot in READ_SLOTS:
        attachment = stored.get(slot)
        if attachment is not None and attachment.object_key not in seen:
            seen.add(attachment.object_key)
            queue_reading(db, user, candidate, attachment, attachment.content_type or "application/pdf")


def import_candidate(db: Session, user: User, ctx: Context, *, folder: str, row: dict | None, uploads: list[Upload],
                     written: list[str]) -> dict:
    """Create the candidate, store and attach every document and record the placement the plan describes.

    Everything happens in the caller's transaction; the caller commits, or rolls back and calls `discard_written(written)`.
    Raises ImportRefused, writing nothing, when the plan is a skip.
    """
    from app.api.routers.applications import _sync_order, _task
    from app.api.routers.candidates import lock_assignment_cursor
    from app.api.routers.files import _safe_name
    from app.db.models import Designation
    from app.services.lead_xlsx import agency_zone
    settings = get_settings()

    # Candidate identity, reference and ownership decisions are serialised the same way the add-candidate screen does.
    lock_assignment_cursor(db)
    plan = plan_candidate(db, ctx, folder=folder, row=row, files=[{"path": u.path, "kinds": u.kinds} for u in uploads])
    if plan["action"] == "skip":
        raise ImportRefused(plan)
    row = dict(row or {})
    owner = ctx.recruiter(row.get("recruiter"))
    order = ctx.order(row.get("order")) if plan["action"] in {"in_progress", "deploy"} else None
    now = datetime.now(timezone.utc)

    phone = plan["phone"] or ""
    candidate = Candidate(
        ref=f"CND-{db.scalar(select(func.nextval('crm_candidate_reference_seq'))):05d}",
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
    # kind becomes the next version. The stage 8 receipt is not a candidate document, only stage evidence.
    stored: dict[str, Attachment] = {}
    receipt: Attachment | None = None
    # A scan that holds several documents goes first, so a dedicated file of the same kind ends up as the current version.
    for upload in sorted(uploads, key=lambda item: (len(item.kinds) == 1, item.path.lower())):
        file_name = _safe_name(upload.path.rsplit("/", 1)[-1])
        object_key, digest = _store_bytes(settings, upload.data, file_name, upload.content_type, written)   # once, whatever it counts as
        common = dict(file_name=file_name, size_kb=max(1, round(len(upload.data) / 1024)), content_type=upload.content_type,
                      object_key=object_key, content_hash=digest, uploaded_by=user.id, uploaded_at=now)
        for kind in upload.kinds:
            if kind not in CANDIDATE_SLOTS and kind != "aadhaar":
                receipt = Attachment(id=str(uuid.uuid4()), entity_type="application", entity_id="", slot="", version=1, **common)
                continue
            previous = stored.get(kind)
            attachment = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=kind,
                                    version=(previous.version + 1) if previous else 1, **common)
            db.add(attachment)
            db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=file_name, entity_type="candidate", entity_id=candidate.id,
                              meta={"attachment_id": attachment.id, "slot": kind, "version": attachment.version, "bulk_import": True}, at=now))
            stored[kind] = attachment
    db.flush()
    queue_reads(db, user, candidate, stored)

    result = {"candidateId": candidate.id, "ref": candidate.ref, "name": candidate.name, "action": plan["action"], "issues": plan["issues"],
              "files": len(uploads), "order": None, "completedThrough": 0, "currentStage": None}

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
            deploy, completed, current = False, 10, 11
    application = Application(candidate_id=candidate.id, order_id=locked.id, owner_id=owner.id,
                              current_stage=11 if deploy else current, status=ApplicationStatus.deployed if deploy else ApplicationStatus.active,
                              lock_version=0, draft={})
    db.add(application)
    db.flush()

    # Stage evidence is a second row on the application for every stage reached so far, pointing at the same stored file.
    reach = 11 if deploy else current
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
    when = stage_dates({n: built[n] for n in range(1, 12)}, now, agency_zone())
    for number in range(1, (11 if deploy else completed) + 1):
        data = validate_payload(number, built[number], ctx.options if number == 8 else None)
        outcome = stage_by_no(number).evaluate(for_rules(number, data, ctx.historical, ctx.travel_confirmed), ctx.options if number == 8 else None).outcome
        db.add(ApplicationStage(
            application_id=application.id, stage=number, attempt=1, data=data, outcome=outcome, completed_by=user.id,
            owner_id=owner.id, completed_at=when[number],
            amount=data.get("amount") if number == 8 else None, amount_currency=data.get("amountCurrency") if number == 8 else None))
    if deploy:
        _sync_order(db, locked)
    else:
        # The stage the recruiter continues at: its task, and what the sheet already says as the saved draft.
        if current <= plan["level"]:
            application.draft = validate_payload(current, built[current], ctx.options if current == 8 else None)
        elif current == 7 and "passport" in refs:
            application.draft = {"copy": refs["passport"]}
        if application.draft:
            application.draft_saved_at, application.draft_saved_by = now, user.id
        _task(db, application, candidate, now)
    db.add(AuditEntry(actor_id=user.id, action="Imported candidate with documents and stage history", target=candidate.name,
                      entity_type="application", entity_id=application.id, at=now,
                      meta={"bulk_import": True, "order_id": locked.id, "owner_id": owner.id, "completed_stages": 11 if deploy else completed}))
    result.update(order=locked.ref, applicationId=application.id, completedThrough=11 if deploy else completed,
                  currentStage=None if deploy else current, action="deploy" if deploy else "in_progress")
    return result
