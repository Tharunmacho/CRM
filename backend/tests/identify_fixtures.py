"""Synthetic people and their documents (every name and number is invented) for the identify-by-content tests.

Page builders return the TEXT a local read would give for that kind of page; `pdf_of` makes real PDFs from such texts (an
embedded text layer, so reading them needs no OCR) for the tests that go through the real reader.
"""
from __future__ import annotations

import pymupdf as fitz

from app.integrations.ocr.validators import mrz_check_digit
from app.services.people_by_content import FileRead, PageRead
from ocr_hard_cases import valid_aadhaar


def mrz_lines(given: str, surname: str, number: str, birth: str = "900101") -> tuple[str, str]:
    """Two 44-character TD3 lines with correct ICAO check digits."""
    number9 = number.ljust(9, "<")
    expiry, personal = "310102", "<" * 14
    names = f"{surname.upper().replace(' ', '<')}<<{given.upper().replace(' ', '<')}"
    line1 = f"P<IND{names}".ljust(44, "<")
    line2 = (number9 + str(mrz_check_digit(number9)) + "IND" + birth + str(mrz_check_digit(birth)) + "M" + expiry
             + str(mrz_check_digit(expiry)) + personal + str(mrz_check_digit(personal)))
    line2 += str(mrz_check_digit(line2[0:10] + line2[13:20] + line2[21:43]))
    return line1, line2


def passport_text(given: str, surname: str, number: str, birth: str = "900101") -> str:
    line1, line2 = mrz_lines(given, surname, number, birth)
    return "\n".join(["REPUBLIC OF INDIA", "PASSPORT", f"Type P    Country Code IND    Passport No. {number}", f"Surname {surname.upper()}",
                      f"Given Name(s) {given.upper()}", "Nationality INDIAN", "Sex M    Date of Birth 01/01/1990", "Place of Birth EXAMPLEPUR",
                      "Date of Issue 02/02/2020    Date of Expiry 01/02/2030", "", line1, line2])


def aadhaar_text(name: str, seed: int) -> str:
    n = valid_aadhaar(seed)
    return "\n".join(["Government of India", "Unique Identification Authority of India", name, "DOB: 01/01/1990", "MALE",
                      f"{n[:4]} {n[4:8]} {n[8:]}", "Aadhaar - Mera Aadhaar, Meri Pehchan"])


GARBLED_NAME = "Aaharapef dio tdliadledf(o)"       # what English-only OCR makes of a Tamil name line (real output from a real card)


def garbled_aadhaar_text(seed: int, garbled: str = GARBLED_NAME) -> str:
    """A bilingual (Tamil + English) Aadhaar card whose English name line came out as Latin-looking junk."""
    n = valid_aadhaar(seed)
    return "\n".join(["Government of India", "Unique Identification Authority of India", garbled, "Year of Birth : 2000", "MALE",
                      f"{n[:4]} {n[4:8]} {n[8:]}", "Aadhaar - Mera Aadhaar, Meri Pehchan"])


def aadhaar_number(seed: int) -> str:
    return valid_aadhaar(seed)


def resume_text(name: str, phone: str = "", email: str = "") -> str:
    lines = [name.upper(), "Curriculum Vitae", f"Mobile: {phone}" if phone else "", f"Email: {email}" if email else "", "",
             "PROFESSIONAL EXPERIENCE", "Welding Supervisor, Harbour Engineering, 2018 - 2022",
             "Managed twelve workers and inspected welds per month under the site code."]
    return "\n".join(lines)


def resume_continuation(n: int = 2) -> str:
    return f"Page {n}\nEDUCATION AND SKILLS\nDiploma in Mechanical Engineering 2015 grade sixty percent.\nSkills: arc welding, TIG welding, blueprint reading, safety audits."


def offer_text(name: str, passport: str = "") -> str:
    return "\n".join(["ALNOOR GENERAL TRADING", "Date: 28 July 2026", f"Dear Mr. {name}", f"Passport No. {passport}" if passport else "",
                      "Subject: Employment Offer", "After reviewing your credentials we extend this offer of employment.",
                      "Position: Picker", "Basic Monthly Salary: KD 130"])


def visa_text(passport: str) -> str:
    return "\n".join(["STATE OF KUWAIT", "Electronic Visa", "Private Sector Work Visa", f"Passport No {passport}", "Occupation WORKER",
                      "Ministry of Interior | Electronic Visa"])


def medical_text(passport: str) -> str:
    return "\n".join(["Detailed candidate report", "Medical center name Shifa Diagnostic Center", "Candidate information",
                      f"Passport No. {passport}", "Height 168.0 cm", "MEDICAL EXAMINATION: GENERAL INVESTIGATION", "Blood pressure 130/80"])


def pcc_text(name: str, passport: str) -> str:
    return (f"POLICE CLEARANCE CERTIFICATE\nIt is certified that there is no adverse information against Mr. {name} S/o SOME FATHER, "
            f"holder of Indian Passport No {passport}, issued at BENGALURU")


def ticket_text(name: str) -> str:
    return "\n".join(["Booking Confirmed", "Booking Reference: ABC123", "Journey Details", "Flight No. J9432", "Passenger Details",
                      f"Mr. {name}", "Adult", "Baggage Details 20kg"])


def unrelated_text() -> str:
    return "Terms and conditions of carriage. Liability is limited as described below. Please keep this document for your records."


def page(text: str, number: int = 1, *, status: str = "read", confidence: float = 1.0, method: str = "native_text", doc_type: str = "unknown",
         reason: str = "") -> PageRead:
    return PageRead(number, status, text, confidence, method, doc_type, reason)


def file_of(*texts, **kw) -> FileRead:
    """A FileRead from page texts (or ready PageReads)."""
    pages = []
    for n, item in enumerate(texts, 1):
        if isinstance(item, PageRead):
            item.number = n
            pages.append(item)
        else:
            pages.append(page(item, n, **kw))
    return FileRead(pages, len(pages))


class FakeReader:
    """Serves prepared reads by path; counts the reads. `deep` maps path -> {page: PageRead} given on a re-read."""

    def __init__(self, reads: dict, deep: dict | None = None):
        self.reads, self.deep, self.calls, self.deep_calls = reads, deep or {}, [], []

    def read(self, path: str, data: bytes) -> FileRead:
        self.calls.append(path)
        value = self.reads[path]
        if isinstance(value, Exception):
            raise value
        # a fresh copy each time: callers may replace pages
        return FileRead([PageRead(p.number, p.status, p.text, p.confidence, p.method, p.doc_type, p.reason) for p in value.pages],
                        value.page_count, value.error)

    def reread(self, path: str, data: bytes, numbers: list, deadline: float) -> dict:
        self.deep_calls.append((path, list(numbers)))
        return {n: self.deep[path][n] for n in numbers if n in self.deep.get(path, {})}


def pdf_of(*texts: str) -> bytes:
    """A real PDF with these page texts as an embedded text layer."""
    with fitz.open() as document:
        for text in texts:
            sheet = document.new_page()
            y = 60
            for line in text.splitlines():
                if line:
                    sheet.insert_text((50, y), line, fontsize=9.5, fontname="helv")
                y += 14
        return document.tobytes(deflate=True)
