"""Synthetic documents of invented people, and the "one folder, several candidates, generic file names" scenario.

Nothing here is a real person or document. Every name, number and address is invented and derived from a fixed seed, so the same
call always returns the same bytes. The documents are built twice over:

``text``  a PDF page with a real text layer (the local reader needs no OCR; fast, no Tesseract).
``scan``  the same page drawn as pixels (mild noise, a slight rotation, a little blur) and embedded as a picture, so the local OCR
          path is really exercised. Slower; use it for the few files you want to prove an OCR read on.

The scenario builder `mixed_folder` returns the files exactly as an owner would find them (``scan01.pdf``, ``IMG_0042.jpg``,
``document (3).pdf`` ... nothing in a name says whose or what), plus the ground truth of every page: which person and which kind of
document it is. Checks and the acceptance suite compare the system's answer with that truth.

Page kinds in the ground truth are the CRM's own slot keys: resume, passport, aadhaar, signed_offer, visa, visa_stamping, medical,
pcc, air_ticket, education_certificate; plus ``photo`` (a picture with no text) and ``blank``.

How much evidence a page carries about its owner (`MixedFolder.evidence`):
``direct``    the person's name (and usually a passport / Aadhaar number) is printed on the page.
``indirect``  no name, only an identifier that another page of the person also carries (the back of a passport has the file
              number the police clearance repeats; the back of an Aadhaar card has the card number the front shows).
``none``      nothing at all (a photo, a blank sheet): such a page can only follow the page before it.

Run it by hand to write a folder you can look at or import::

    backend/.venv/Scripts/python.exe -I backend/tests/people_fixtures.py OUT_DIR [--scan] [--seed 7]
"""
from __future__ import annotations

import io
import json
import random
import sys
import textwrap
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

if __name__ == "__main__":   # run by hand with -I: the script's own folder and the backend folder are not on the path
    _here = Path(__file__).resolve().parent
    sys.path[:0] = [str(_here), str(_here.parent)]

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter

import ocr_synthetic as synth
from ocr_hard_cases import _mono, valid_aadhaar
from app.integrations.ocr.validators import mrz_check_digit

ANCHOR = date(2026, 10, 15)   # "today" of the documents: fixed, so a fixture never changes with the calendar
DPI = 200
A4_PT = (595.0, 842.0)
KINDS = ("resume", "passport", "aadhaar", "signed_offer", "visa", "visa_stamping", "medical", "pcc", "air_ticket",
         "education_certificate", "photo", "blank")
# What the owner wants sent to the online reader later, and the pages that carry no evidence of whose they are.
READ_KINDS = ("resume", "passport", "aadhaar")
ANONYMOUS_KINDS = ("photo", "blank")


# ---------------------------------------------------------------------------------------------------------------- people

@dataclass(frozen=True)
class Person:
    key: str
    given: str            # "Tharun Pk"
    surname: str          # "Kumar"
    sex: str              # M / F
    dob: date
    role: str
    place: str
    passport_no: str
    passport_issue: date
    passport_expiry: date
    aadhaar: str          # 12 digits, Verhoeff-valid
    vid: str
    phone: str
    email: str
    father: str
    mother: str
    address: str
    pin: str
    file_no: str          # the passport application file number: on the passport's back page and on the police clearance
    pnr: str
    flight: str
    ticket_no: str
    visa_no: str
    slip_no: str
    reg_no: str
    employers: tuple[tuple[str, str, str], ...]

    @property
    def display(self) -> str:
        return f"{self.given} {self.surname}"

    @property
    def upper(self) -> str:
        return self.display.upper()

    @property
    def title(self) -> str:
        return "Mr." if self.sex == "M" else "Ms."

    @property
    def mrz_name(self) -> str:
        return f"{self.surname.upper()}<<{self.given.upper().replace(' ', '<')}"


_KNOWN = {   # key: (given, surname, sex, role, place, father, mother)
    "tharun": ("Tharun Pk", "Kumar", "M", "Warehouse Associate", "Mysuru", "Ramesh Kumar", "Lakshmi Kumar"),
    "meera": ("Meera", "Sundaram", "F", "Housekeeping Attendant", "Coimbatore", "Sundaram Iyer", "Janaki Sundaram"),
    "arjun": ("Arjun Venkat", "Nair", "M", "Site Electrician", "Kochi", "Venkat Nair", "Sarala Nair"),
    "divya": ("Divya", "Menon", "F", "Staff Nurse", "Thrissur", "Gopal Menon", "Radha Menon"),
    "ravi": ("Ravi Shankar", "Iyer", "M", "Piping Fitter", "Madurai", "Shankar Iyer", "Meenakshi Iyer"),
}
_FIRMS = ("Northwind Logistics", "Harbour Stores", "Delta Marine Works", "Summit Projects", "Orchid Hospitality", "Granite Contracting",
          "Lakeview Facilities", "Brightway Supplies")
_STREETS = ("Temple Road", "Market Street", "Lake View Layout", "Gandhi Nagar", "Station Road", "Rose Garden Colony")
_COMPANIES = ("Al Rawda Trading Company W.L.L.", "Gulf Horizon General Contracting", "Desert Palm Facilities Management")
_PASSPORT_LETTERS = "KLMNPRSTUZ"


def person(key: str) -> Person:
    """The invented person for a key. ``tharun`` and ``meera`` are the two of the owner's scenario; any other key gets a stable name."""
    key = key.lower()
    rng = random.Random(f"people-fixture:{key}")
    seed = zlib.crc32(key.encode())
    given, surname, sex, role, place, father, mother = _KNOWN.get(key) or (
        key.capitalize() + " Test", "Sample" + key[:3].capitalize(), rng.choice("MF"), "Picker", "Examplepur",
        "Father Sample", "Mother Sample")
    dob = date(rng.randrange(1985, 2001), rng.randrange(1, 13), rng.randrange(1, 28))
    issue = date(rng.randrange(2019, 2025), rng.randrange(1, 13), rng.randrange(1, 28))
    expiry = date(issue.year + 10, issue.month, issue.day) - timedelta(days=1)
    digits = lambda n: "".join(str(rng.randrange(10)) for _ in range(n))   # noqa: E731
    first = f"{rng.randrange(2, 10)}"
    employers = tuple((firm, place, f"{2017 + 2 * n}-{2019 + 2 * n}") for n, firm in enumerate(rng.sample(_FIRMS, 2)))
    return Person(
        key=key, given=given, surname=surname, sex=sex, dob=dob, role=role, place=place,
        passport_no=rng.choice(_PASSPORT_LETTERS) + str(rng.randrange(1, 10)) + digits(6),
        passport_issue=issue, passport_expiry=expiry,
        aadhaar=valid_aadhaar(seed), vid=first + digits(15), phone=rng.choice("6789") + digits(9),
        email=f"{given.split()[0].lower()}.{surname.lower()}{rng.randrange(10, 99)}@example.test",
        father=father, mother=mother,
        address=f"{rng.randrange(2, 90)}, {rng.choice(_STREETS)}, {place}, Karnataka", pin=f"5{digits(5)}",
        file_no=f"BG{digits(13)}", pnr="".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6)),
        flight=f"J9{rng.randrange(100, 999)}", ticket_no="63" + digits(11), visa_no=first + digits(8),
        slip_no="1091" + digits(11), reg_no=digits(11), employers=employers)


def fmt(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def mrz_lines(p: Person) -> tuple[str, str]:
    """The two 44-character TD3 lines of the person's passport, every check digit correct (ICAO 9303: weights 7-3-1)."""
    number = p.passport_no.ljust(9, "<")
    birth, expiry = p.dob.strftime("%y%m%d"), p.passport_expiry.strftime("%y%m%d")
    personal = "<" * 14
    line2 = (number + str(mrz_check_digit(number)) + "IND" + birth + str(mrz_check_digit(birth)) + p.sex + expiry
             + str(mrz_check_digit(expiry)) + personal + str(mrz_check_digit(personal)))
    line2 += str(mrz_check_digit(line2[0:10] + line2[13:20] + line2[21:43]))
    line1 = f"P<IND{p.mrz_name}".ljust(44, "<")
    assert len(line1) == 44 and len(line2) == 44
    return line1, line2


# ---------------------------------------------------------------------------------------------------------------- pages

@dataclass
class PageSpec:
    """One page: who it belongs to, what it is, and the lines that are printed on it."""
    person: str
    kind: str
    token: str
    lines: list[tuple[str, str]] = field(default_factory=list)   # (text, style); "" = a gap
    layout: str = "page"          # page | card (the text sits in a card-sized box at the top) | photo | blank
    evidence: str = "direct"

    @property
    def text(self) -> str:
        return "\n".join(text for text, _ in self.lines)


_WRAP = {"title": 42, "head": 70, "body": 92, "bold": 92, "small": 104, "big": 30, "mono": 60}


def _lines(*items) -> list[tuple[str, str]]:
    """Items are "text" (body) or (text, style); long body text is wrapped to the page width."""
    out: list[tuple[str, str]] = []
    for item in items:
        text, style = (item, "body") if isinstance(item, str) else item
        if not text:
            out.append(("", style))
        elif style == "mono":
            out.append((text, style))
        else:
            out += [(part, style) for part in (textwrap.wrap(text, _WRAP[style], break_long_words=False) or [""])]
    return out


def _resume(p: Person, page: int, variant: int = 0) -> PageSpec:
    if page == 1:
        (e1, c1, y1), (e2, c2, y2) = p.employers
        lines = _lines(
            ("CURRICULUM VITAE", "title"), (p.upper, "head"),
            f"Mobile: +91 {p.phone[:5]} {p.phone[5:]}      Email: {p.email}",
            f"Address: {p.address} - {p.pin}", "",
            ("CAREER OBJECTIVE", "head"),
            f"To work as a {p.role} with a reputed organisation in the Gulf, giving my best in a team and growing with the company.", "",
            ("PROFESSIONAL EXPERIENCE", "head"),
            (f"{p.role}, {e1}, {c1}                                  {y1}", "bold"),
            "Handled daily receiving, loading and unloading, stock counting and dispatch with the supervisor's checklist.",
            "Kept the work area clean and followed all safety rules; trained two new joiners.", "",
            (f"Assistant, {e2}, {c2}                                  {y2}", "bold"),
            "Assisted the senior team with packing, labelling and record keeping; daily reports to the manager.", "",
            ("SKILLS", "head"), "Team work, stock handling, basic computer skills, punctuality, willing to work overtime.")
        return PageSpec(p.key, "resume", "resume_1", lines)
    lines = _lines(
        ("EDUCATIONAL QUALIFICATION", "head"),
        f"Bachelor of Commerce, {p.place} University, 2016 - 2019, First Class",
        f"Pre-University (PUC), {p.place} Government College, 2014 - 2016",
        f"S.S.L.C., {p.place} High School, 2014", "",
        ("LANGUAGES KNOWN", "head"), "English, Hindi, Kannada, Tamil", "",
        ("PERSONAL DETAILS", "head"),
        f"Date of birth: {fmt(p.dob)}", "Nationality: Indian", f"Marital status: {'Married' if p.sex == 'F' else 'Single'}",
        f"Passport No.: {p.passport_no}     Date of expiry: {fmt(p.passport_expiry)}", "",
        ("DECLARATION", "head"),
        "I hereby declare that the information given above is true and correct to the best of my knowledge and belief.", "",
        f"Place: {p.place}                                   Signature: {p.display}", ("Curriculum vitae - page 2 of 2", "small"))
    return PageSpec(p.key, "resume", "resume_2", lines)


def _passport_data(p: Person, variant: int = 0) -> PageSpec:
    line1, line2 = mrz_lines(p)
    lines = _lines(
        ("REPUBLIC OF INDIA", "head"), ("PASSPORT", "title"),
        f"Type P      Country Code IND      Passport No. {p.passport_no}",
        f"Surname {p.surname.upper()}", f"Given Name(s) {p.given.upper()}", "Nationality INDIAN",
        f"Sex {p.sex}      Date of Birth {fmt(p.dob)}", f"Place of Birth {p.place.upper()}", f"Place of Issue BENGALURU",
        f"Date of Issue {fmt(p.passport_issue)}      Date of Expiry {fmt(p.passport_expiry)}", "", "", "",
        (line1, "mono"), (line2, "mono"))
    return PageSpec(p.key, "passport", "passport_data", lines)


def _passport_back(p: Person, variant: int = 0) -> PageSpec:
    lines = _lines(
        ("Particulars of Father / Legal Guardian", "head"),
        f"Name of Father / Legal Guardian   {p.father.upper()}", f"Name of Mother   {p.mother.upper()}", "Name of Spouse   ", "",
        ("Address", "head"), f"{p.address.upper()}", f"PIN: {p.pin}", "",
        "Old Passport No.   NA", f"File No.   {p.file_no}", "Please see the notes and instructions at the end of this passport.")
    return PageSpec(p.key, "passport", "passport_back", lines, evidence="indirect")


def _aadhaar_front(p: Person, variant: int = 0) -> PageSpec:
    group = f"{p.aadhaar[:4]} {p.aadhaar[4:8]} {p.aadhaar[8:]}"
    lines = _lines(
        ("Government of India", "head"), (p.display, "bold"), f"DOB: {fmt(p.dob)}", "MALE" if p.sex == "M" else "FEMALE", "",
        (group, "big"), "", f"VID : {p.vid[:4]} {p.vid[4:8]} {p.vid[8:12]} {p.vid[12:]}",
        ("Aadhaar - Mera Aadhaar, Meri Pehchan", "small"))
    return PageSpec(p.key, "aadhaar", "aadhaar_front", lines, layout="card")


def _aadhaar_back(p: Person, variant: int = 0) -> PageSpec:
    group = f"{p.aadhaar[:4]} {p.aadhaar[4:8]} {p.aadhaar[8:]}"
    lines = _lines(
        ("Unique Identification Authority of India", "head"), "Address:",
        f"S/O {p.father}, {p.address} - {p.pin}", "", (group, "big"), "",
        ("Aadhaar is a proof of identity, not of citizenship", "small"), ("help@uidai.gov.in   www.uidai.gov.in", "small"))
    return PageSpec(p.key, "aadhaar", "aadhaar_back", lines, layout="card", evidence="indirect")


def _degree(p: Person, variant: int = 0) -> PageSpec:
    lines = _lines(
        (f"{p.place.upper()} UNIVERSITY", "title"), ("DEGREE CERTIFICATE", "head"), "",
        f"This is to certify that {p.upper} has been admitted to the degree of Bachelor of Commerce in the First Class with "
        f"Distinction, having passed the examinations held in April 2019 under the regulations of the university.", "",
        f"Register No.: {p.reg_no}      Date of issue: {fmt(date(2019, 9, 12))}      Convocation 2019", "", "",
        "Registrar                                                    Controller of Examinations")
    return PageSpec(p.key, "education_certificate", "degree", lines)


def _offer(p: Person, variant: int = 0) -> PageSpec:
    company = _COMPANIES[zlib.crc32(p.key.encode()) % len(_COMPANIES)]
    when = ANCHOR - timedelta(days=61 - variant)
    lines = _lines(
        (company.upper(), "head"), f"Date: {when.strftime('%d %B %Y')}", f"Dear {p.title} {p.display}",
        f"Passport No. {p.passport_no}", ("Subject: Employment Offer", "bold"),
        f"After reviewing your credentials it is our pleasure to extend to you this offer of employment as a {p.role}.",
        "Working Hours: 9+1 Hours, paid overtime if required", "Working Days: 6 working days, 1 day off",
        "Basic Monthly Salary: KD 130 (One hundred and thirty Kuwaiti Dinars)", "Accommodation: Provided by the company",
        "Transportation: Provided by the company", "Annual Leave: 30 days paid annual leave as per labour law",
        "Contract: 3 Years. Air ticket one way from India.",
        "The offer is subject to a medical examination, medical insurance and a valid visa.",
        "If you choose to accept this offer, please sign this letter and return it to us, indicating the anticipated commencement date.",
        "Best regards, Human Resource Department", "",
        f"Accepted and signed:  {p.display}                      Date: {fmt(ANCHOR - timedelta(days=55 - variant))}")
    return PageSpec(p.key, "signed_offer", "offer", lines)


def _evisa(p: Person, variant: int = 0) -> PageSpec:
    visa_no = p.visa_no if not variant else str(int(p.visa_no) + variant)
    birth, expiry = p.dob.strftime("%y%m%d"), (ANCHOR + timedelta(days=80)).strftime("%y%m%d")
    zone1 = f"VBKWT{p.mrz_name}".ljust(44, "<")
    zone2 = f"{visa_no}{mrz_check_digit(visa_no)}IND{birth}{mrz_check_digit(birth)}{p.sex}{expiry}{mrz_check_digit(expiry)}".ljust(42, "<") + "04"
    lines = _lines(
        ("STATE OF KUWAIT eVISA", "title"), "Ministry of Interior | Electronic Visa",
        f"Visa Type: Private Sector Work Visa      Visa Number {visa_no}",
        f"Issue Date {(ANCHOR - timedelta(days=9)).isoformat()}      Expiry Date {(ANCHOR + timedelta(days=80)).isoformat()}",
        f"Nationality INDIA      Passport No. {p.passport_no}", f"Name {p.upper}      Occupation {p.role.upper()}",
        "Entry Stamp      Exit Stamp", "", (zone1, "mono"), (zone2, "mono"))
    return PageSpec(p.key, "visa", "evisa", lines)


def _medical(p: Person, variant: int = 0) -> PageSpec:
    lines = _lines(
        ("Detailed candidate report            Wafid GAMCA", "head"),
        f"Medical center name: Shifa Diagnostic Center     GCC Slip No. {p.slip_no}",
        f"Candidate information: Name {p.upper}, {'Male' if p.sex == 'M' else 'Female'}, Indian, travelling to Kuwait",
        f"Passport No. {p.passport_no}      Date of birth {fmt(p.dob)}      Examination date {fmt(ANCHOR - timedelta(days=30 - variant))}",
        "MEDICAL EXAMINATION: GENERAL INVESTIGATION", "Blood pressure 120/80   Pulse 76   Chest X-Ray NAD",
        "LABORATORY INVESTIGATION   Haemoglobin 14.2", "HIV Negative   HBs Ag Negative   VDRL Negative   Malaria Absent",
        f"Mentioned above is the medical report for {p.title} {p.upper} who is Fit for the above mentioned job according to the GCC criteria.",
        "Doctor's name and signature")
    return PageSpec(p.key, "medical", "medical", lines)


def _pcc(p: Person, variant: int = 0) -> PageSpec:
    lines = _lines(
        ("GOVERNMENT OF INDIA", "head"), ("MINISTRY OF EXTERNAL AFFAIRS", "head"), ("REGIONAL PASSPORT OFFICE, BENGALURU", "head"),
        f"File Number : {p.file_no}     PCC Issuance Date : {fmt(ANCHOR - timedelta(days=40 - variant))}",
        ("POLICE CLEARANCE CERTIFICATE", "title"),
        f"It is certified that there is no adverse information against {p.title} {p.upper}, holder of Indian Passport No {p.passport_no}, "
        "which would render him ineligible for EMPLOYMENT/EMPLOYMENT VISA/WORK PERMIT for STATE OF KUWAIT. "
        "This PCC is not valid for any other purpose or country.", "",
        "Regional Passport Officer      Stamp of the Office of Issue      Signature of Passport Authority")
    return PageSpec(p.key, "pcc", "pcc", lines)


def _air_ticket(p: Person, variant: int = 0) -> PageSpec:
    pnr = p.pnr if not variant else p.pnr[:-1] + "Z"
    lines = _lines(
        ("Booking Confirmed", "title"),
        f"Your Booking is confirmed with us. Booking Reference (PNR): {pnr}      E-Ticket No. {p.ticket_no}",
        ("Journey Details", "head"),
        f"Flight No. {p.flight}      Departure: Bangalore (BLR) Terminal 1  {(ANCHOR + timedelta(days=95 + variant)).strftime('%d %b %Y')} 04:25",
        "Arrival: Kuwait International Airport Terminal 5  07:10",
        ("Passenger Details", "head"), f"Passenger Name: {p.title} {p.upper}, Adult",
        "Baggage Details: Cabin 7kg  Checked-in 30kg",
        "Jazeera Airways. This booking is non-refundable. Check-in counters open 3 hours before departure.")
    return PageSpec(p.key, "air_ticket", "air_ticket", lines)


def _visa_stamping(p: Person, variant: int = 0) -> PageSpec:
    lines = _lines(
        ("EMBASSY OF THE STATE OF KUWAIT, NEW DELHI", "head"), ("VISA STAMPING CONFIRMATION", "title"),
        f"Stamping date: {fmt(ANCHOR + timedelta(days=21 + variant))}      Slip: {p.slip_no[-8:]}",
        f"Holder: {p.upper}      Passport No. {p.passport_no}",
        "The visa stamp has been affixed on page 14 of the passport. Good for a single entry. Duration of stay 90 days.",
        "Use outside the State of Kuwait is not valid.")
    return PageSpec(p.key, "visa_stamping", "visa_stamping", lines)


def _photo(p: Person, variant: int = 0) -> PageSpec:
    return PageSpec(p.key, "photo", "photo", [], layout="photo", evidence="none")


def _blank(p: Person, variant: int = 0) -> PageSpec:
    return PageSpec(p.key, "blank", "blank", [], layout="blank", evidence="none")


_BUILDERS = {
    "resume_1": lambda p, v: _resume(p, 1, v), "resume_2": lambda p, v: _resume(p, 2, v),
    "passport_data": _passport_data, "passport_back": _passport_back, "aadhaar_front": _aadhaar_front, "aadhaar_back": _aadhaar_back,
    "degree": _degree, "offer": _offer, "evisa": _evisa, "medical": _medical, "pcc": _pcc, "air_ticket": _air_ticket,
    "visa_stamping": _visa_stamping, "photo": _photo, "blank": _blank,
}
TOKENS = tuple(_BUILDERS)


def page(person_key: str, token: str, variant: int = 0) -> PageSpec:
    """One page of the person's document. ``variant`` > 0 changes a date or reference, for a second, different copy."""
    return _BUILDERS[token](person(person_key), variant)


# ---------------------------------------------------------------------------------------------------------------- drawing

_STYLE = {"title": (17.0, "bold"), "head": (12.0, "bold"), "body": (10.5, "reg"), "bold": (10.5, "bold"), "small": (9.0, "reg"),
          "mono": (12.0, "mono"), "big": (22.0, "bold")}
_FITZ_FONT = {"reg": "helv", "bold": "hebo", "mono": "cour"}
_BOLD_FONTS = (r"C:\Windows\Fonts\arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
CARD_BOX = (40.0, 50.0, 555.0, 330.0)   # points, on the A4 page
_MARGIN_X, _MARGIN_Y = 52.0, 64.0


def _pil_font(face: str, px: int):
    from PIL import ImageFont
    if face == "mono":
        return _mono(px)
    if face == "bold":
        for candidate in _BOLD_FONTS:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, px)
    return synth._font(px)


def _step(style: str) -> float:
    size, _ = _STYLE[style]
    return size * (1.45 if style != "mono" else 1.6)


def _origin(spec: PageSpec) -> tuple[float, float]:
    return (CARD_BOX[0] + 24, CARD_BOX[1] + 30) if spec.layout == "card" else (_MARGIN_X, _MARGIN_Y)


def draw_text_page(document: "fitz.Document", spec: PageSpec) -> None:
    """A page with a real text layer (the reader does not need OCR for it). Photos are pictures, blank pages empty."""
    new = document.new_page(width=A4_PT[0], height=A4_PT[1])
    if spec.layout == "blank":
        return
    if spec.layout == "photo":
        new.insert_image(new.rect, stream=_jpeg(photo_image(random.Random(f"photo:{spec.person}")), 80))
        return
    if spec.layout == "card":
        new.draw_rect(fitz.Rect(*CARD_BOX), color=(0.4, 0.4, 0.4), width=1.2)
    x, y = _origin(spec)
    for text, style in spec.lines:
        size, face = _STYLE[style]
        if text:
            new.insert_text((x, y + size), text, fontsize=size, fontname=_FITZ_FONT[face])
        y += _step(style)


def photo_image(rng: random.Random, width: int = 1240, height: int = 1754) -> Image.Image:
    """A portrait-like picture with no text: a face-like blob on a plain backdrop."""
    skin = (rng.randrange(150, 215), rng.randrange(110, 165), rng.randrange(85, 135))
    back = rng.randrange(150, 235)
    image = Image.new("RGB", (width, height), (back, back, min(255, back + 12)))
    draw = ImageDraw.Draw(image)
    cx, top = width // 2, int(height * 0.22)
    draw.ellipse((cx - 250, top, cx + 250, top + 620), fill=skin)
    draw.ellipse((cx - 130, top + 230, cx - 70, top + 280), fill=(40, 30, 30))
    draw.ellipse((cx + 70, top + 230, cx + 130, top + 280), fill=(40, 30, 30))
    draw.rectangle((cx - 380, top + 640, cx + 380, height - 120), fill=(rng.randrange(20, 90), rng.randrange(30, 90), rng.randrange(60, 130)))
    image = image.filter(ImageFilter.GaussianBlur(2.5))
    return _grain_rgb(image, rng, 10)


def _noise(rng: random.Random, size: tuple[int, int], divisor: int) -> Image.Image:
    """Seeded grain (PIL's own noise is not seedable): 0..255 // divisor per pixel."""
    return Image.frombytes("L", size, rng.randbytes(size[0] * size[1])).point(lambda value: value // divisor)


def _grain(image: Image.Image, rng: random.Random, divisor: int) -> Image.Image:
    from PIL import ImageChops
    return ImageChops.subtract(image, _noise(rng, image.size, divisor))


def _grain_rgb(image: Image.Image, rng: random.Random, divisor: int) -> Image.Image:
    return Image.merge("RGB", [_grain(band, rng, divisor) for band in image.split()])


def _jpeg(image: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    image.convert("RGB" if image.mode not in ("L", "RGB") else image.mode).save(out, format="JPEG", quality=quality)
    return out.getvalue()


def render_scan(spec: PageSpec, rng: random.Random) -> Image.Image:
    """The page as a scanner or a phone would give it: pixels only, a slight tilt, a little blur, paper grain."""
    if spec.layout == "photo":
        return photo_image(rng)
    scale = DPI / 72
    width, height = round(A4_PT[0] * scale), round(A4_PT[1] * scale)
    image = Image.new("L", (width, height), rng.randrange(246, 256))
    if spec.layout != "blank":
        draw = ImageDraw.Draw(image)
        ink = rng.randrange(10, 50)
        if spec.layout == "card":
            draw.rectangle([round(v * scale) for v in CARD_BOX], outline=110, width=3)
        x, y = _origin(spec)
        for text, style in spec.lines:
            size, face = _STYLE[style]
            if text:
                draw.text((round(x * scale), round(y * scale)), text, fill=ink, font=_pil_font(face, round(size * scale)))
            y += _step(style)
        angle = rng.uniform(-1.3, 1.3)
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=250)
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.7)))
    return _grain(image, rng, 14)


def draw_scan_page(document: "fitz.Document", spec: PageSpec, rng: random.Random) -> None:
    image = render_scan(spec, rng)
    new = document.new_page(width=image.width * 72 / DPI, height=image.height * 72 / DPI)
    new.insert_image(new.rect, stream=_jpeg(image, 72))


def build_pdf(specs: list[PageSpec], mode: str = "text", seed: str = "0") -> bytes:
    """One PDF holding these pages in order. Deterministic for the same specs, mode and seed."""
    if mode not in ("text", "scan"):
        raise ValueError("mode is 'text' or 'scan'")
    with fitz.open() as document:
        for number, spec in enumerate(specs, 1):
            if mode == "text":
                draw_text_page(document, spec)
            else:
                draw_scan_page(document, spec, random.Random(f"{seed}:{number}:{spec.person}:{spec.token}"))
        document.set_metadata({})
        return document.tobytes(deflate=True, no_new_id=True)


def build_jpeg(spec: PageSpec, seed: str = "0") -> bytes:
    """A one-page file as a photo / scanner image (always pixels, so reading it needs OCR)."""
    return _jpeg(render_scan(spec, random.Random(f"{seed}:img:{spec.person}:{spec.token}")), 75)


# ---------------------------------------------------------------------------------------------------------------- the scenario

# What each person's folder is made of: one tuple per file, the pages in order. A token may carry another person ("meera:passport_data").
# "image" marks a file that arrives as a phone / scanner picture (.jpg) rather than a PDF.
RECIPES: dict[str, list[tuple[tuple[str, ...], bool]]] = {
    "tharun": [                                                       # 9 files of his own + the shared one = 10
        (("resume_1", "resume_2"), False),                            # a two page resume
        (("passport_back",), False),                                  # the back of the passport on its own (no name on it)
        (("aadhaar_front",), True),                                   # a photo of the card
        (("aadhaar_back",), True),                                    # and of its back (no name on it)
        (("offer",), False),
        (("evisa", "medical"), False),                                # a scan of two documents
        (("pcc",), False),
        (("air_ticket",), False),
        (("degree", "visa_stamping", "photo", "blank"), False),       # four pages: two documents, a photo and an empty sheet
    ],
    "meera": [                                                        # 4 files of her own + the shared one = 5
        (("offer", "passport_back", "aadhaar_front"), False),         # offer + passport + Aadhaar in one scan
        (("resume_1", "resume_2"), False),
        (("evisa", "air_ticket"), False),
        (("medical", "pcc"), False),
    ],
}
_SPARE = [(("medical",), False), (("pcc",), False), (("air_ticket",), False), (("evisa",), False), (("offer",), False),
          (("degree",), False), (("visa_stamping",), False)]
_GENERIC = [(("resume_1", "resume_2"), False), (("passport_data", "passport_back"), False), (("aadhaar_front",), True),
            (("aadhaar_back",), True)] + _SPARE
_SHARED = (("{a}:passport_data", "{b}:passport_data"), False)       # one PDF: a page of the first person's passport, then the second's

_PDF_NAMES = ("scan{n:02d}.pdf", "document ({n}).pdf", "Scan_{n:04d}.pdf", "DOC{n:05d}.pdf", "New Doc {n}.pdf", "CamScanner {n:02d}-10-2026.pdf")
_IMAGE_NAMES = ("IMG_{n:04d}.jpg", "IMG-20261014-WA{n:04d}.jpg", "PXL_2026101_{n:04d}.jpg")


@dataclass
class MixedFolder:
    files: list[tuple[str, bytes]]
    truth: dict[str, list[tuple[int, str, str]]]          # file -> [(page number from 1, person, kind)]
    evidence: dict[str, list[str]]                        # file -> per page: direct | indirect | none
    people: tuple[str, ...]
    mode: str
    seed: int
    mixed_file: str | None                                # the PDF whose pages belong to two different people

    def __iter__(self):                                   # `files, truth = mixed_folder(...)`
        yield self.files
        yield self.truth

    @property
    def names(self) -> dict[str, str]:
        return {key: person(key).display for key in self.people}

    @property
    def data(self) -> dict[str, bytes]:
        return dict(self.files)

    @property
    def total_pages(self) -> int:
        return sum(len(rows) for rows in self.truth.values())

    def pages_of(self, key: str) -> list[tuple[str, int, str]]:
        return [(name, number, kind) for name, rows in self.truth.items() for number, who, kind in rows if who == key]

    def files_of(self, key: str) -> list[str]:
        return [name for name, rows in self.truth.items() if any(who == key for _, who, _ in rows)]

    def content_type(self, name: str) -> str:
        return "image/jpeg" if name.lower().endswith(".jpg") else "application/pdf"

    def as_json(self) -> dict:
        return {"seed": self.seed, "mode": self.mode, "people": self.names, "mixedFile": self.mixed_file,
                "files": {name: [{"page": n, "person": who, "kind": kind, "evidence": self.evidence[name][n - 1]}
                                 for n, who, kind in rows] for name, rows in self.truth.items()}}


def _tokens(person_key: str, token: str) -> tuple[str, str]:
    owner, _, name = token.rpartition(":")
    return (owner or person_key), name


def mixed_folder(seed: int = 7, people: tuple[str, ...] = ("tharun", "meera"), counts: tuple[int, ...] = (10, 5),
                 generic_names: bool = True, scan_mode: str = "text", images: bool | None = None,
                 with_mixed: bool = True) -> MixedFolder:
    """ONE folder with the documents of several candidates, none of the files renamed.

    ``counts`` is the number of files each person has. When ``with_mixed`` is set (and there are two people or more) the first two
    people share ONE PDF whose first page is the first person's passport photo page and whose second page is the second person's:
    that file counts for both, so with (10, 5) there are 10 + 5 - 1 = 14 files on disk. ``scan_mode`` is ``text`` (text layer, fast) or
    ``scan`` (pixels, needs OCR). ``images`` makes the files marked as pictures real .jpg files; by default only in scan mode (a
    picture can only be read by OCR; in text mode they are one-page PDFs with a text layer instead). The order of the files and their
    names are shuffled by ``seed``, and with ``generic_names`` the names say nothing of whose or what (``scan07.pdf``,
    ``IMG_0042.jpg``, ``document (3).pdf``).
    """
    if len(counts) != len(people):
        raise ValueError("one count per person")
    images = (scan_mode == "scan") if images is None else images
    rng = random.Random(f"mixed:{seed}")
    shared = with_mixed and len(people) >= 2
    entries: list[tuple[list[PageSpec], bool, str]] = []        # (pages, as an image, label for descriptive names)
    for index, (key, count) in enumerate(zip(people, counts)):
        own = count - (1 if shared and index < 2 else 0)
        if own < 0:
            raise ValueError("a person who shares the mixed file needs a count of at least 1")
        pool = list(RECIPES.get(key) or _GENERIC)
        recipes = pool[:own]
        spare_turn = 0
        while len(recipes) < own:                                # more files than the recipe: further copies, with other dates
            recipes.append(_SPARE[spare_turn % len(_SPARE)])
            spare_turn += 1
        variants: dict[tuple[str, ...], int] = {}
        for tokens, as_image in recipes:
            variants[tokens] = variants.get(tokens, -1) + 1
            pages = [page(*_tokens(key, token), variant=variants[tokens]) for token in tokens]
            entries.append((pages, as_image and images and len(pages) == 1, f"{key}-{'+'.join(tokens)}"))
    if shared:
        tokens = tuple(token.format(a=people[0], b=people[1]) for token in _SHARED[0])
        entries.append(([page(*_tokens(people[0], token)) for token in tokens], False, "mixed-passports"))
    rng.shuffle(entries)

    files, truth, evidence, mixed_file = [], {}, {}, None
    used_names: set[str] = set()
    pdf_count = image_count = 0
    for position, (pages, as_image, label) in enumerate(entries, 1):
        if as_image:
            image_count += 1
            ordinal = rng.randrange(1, 9) * 100 + image_count * 7 + position
            template = _IMAGE_NAMES[(position + seed) % len(_IMAGE_NAMES)]
        else:
            pdf_count += 1
            ordinal = pdf_count + (position if generic_names and position % 3 == 0 else 0)
            template = _PDF_NAMES[(position + seed) % len(_PDF_NAMES)]
        name = template.format(n=ordinal) if generic_names else f"{label}{'.jpg' if as_image else '.pdf'}"
        while name.lower() in used_names:
            name = name.replace(".", f"_{position}.", 1)
        used_names.add(name.lower())
        tag = f"{seed}:{position}"
        data = build_jpeg(pages[0], tag) if as_image else build_pdf(pages, scan_mode, tag)
        files.append((name, data))
        truth[name] = [(number, spec.person, spec.kind) for number, spec in enumerate(pages, 1)]
        evidence[name] = [spec.evidence for spec in pages]
        if len({spec.person for spec in pages}) > 1:
            mixed_file = name
    return MixedFolder(files, truth, evidence, tuple(people), scan_mode, seed, mixed_file)


def write_folder(folder: MixedFolder, directory: Path) -> Path:
    """Write the files to ``directory/files`` and the truth to ``directory/truth.json`` (next to the folder, never inside it)."""
    target = Path(directory) / "files"
    target.mkdir(parents=True, exist_ok=True)
    for name, data in folder.files:
        (target / name).write_bytes(data)
    (Path(directory) / "truth.json").write_text(json.dumps(folder.as_json(), indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Write the synthetic mixed folder of two candidates.")
    parser.add_argument("out")
    parser.add_argument("--scan", action="store_true", help="draw pages as scans (needs OCR to read)")
    parser.add_argument("--seed", type=int, default=7)
    options = parser.parse_args()
    built = mixed_folder(options.seed, scan_mode="scan" if options.scan else "text")
    where = write_folder(built, Path(options.out))
    print(f"{len(built.files)} files, {built.total_pages} pages -> {where}")
