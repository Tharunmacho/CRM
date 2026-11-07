"""Checks and repairs for what the local OCR reads on identity documents, and a cross-check hook.

* Passport machine-readable zone (ICAO 9303, TD3 two lines of 44): check digits for the passport
  number, date of birth, date of expiry, personal number and the composite, with automatic repair of
  the usual OCR confusions (O/0, I/1, B/8, S/5, Z/2, G/6) until the checksums pass.
* Aadhaar 12-digit number: Verhoeff checksum, the first digit rule, and a single-digit repair
  only when exactly one repair passes.
* Dates, Indian mobile numbers and e-mail syntax.

Values found here are kept in memory for ``cross_check``; the records that are stored or logged
carry only booleans and page numbers (``LocalPatterns.flags``).
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import date

from .read_merge import compact, normalise_mrz_line, verhoeff_valid

# ---------------------------------------------------------------------------- MRZ

_MRZ_WEIGHTS = (7, 3, 1)
_LETTER_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "B": "8", "S": "5", "Z": "2", "G": "6", "T": "7"}
# Both directions for alphanumeric fields: any of these may stand for the other.
_CONFUSABLE = {"0": "OQD", "O": "0", "Q": "0", "D": "0", "1": "IL", "I": "1", "L": "1", "8": "B", "B": "8",
               "5": "S", "S": "5", "2": "Z", "Z": "2", "6": "G", "G": "6", "7": "T", "T": "7", "<": "K"}
_MRZ_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")


def mrz_check_digit(field_text: str) -> int | None:
    total = 0
    for position, char in enumerate(field_text):
        if char == "<":
            value = 0
        elif char.isdigit():
            value = int(char)
        elif "A" <= char <= "Z":
            value = ord(char) - 55
        else:
            return None
        total += value * _MRZ_WEIGHTS[position % 3]
    return total % 10


def _check_ok(field_text: str, check_char: str) -> bool:
    if check_char == "<":
        check_char = "0"
    return check_char.isdigit() and mrz_check_digit(field_text) == int(check_char)


def _numeric(text: str) -> str:
    return "".join(_LETTER_TO_DIGIT.get(c, c) for c in text)


def _repair_field(text: str, check: str, *, numeric: bool, limit: int = 2) -> tuple[str, str, bool] | None:
    """The smallest set of look-alike substitutions that makes the checksum pass, or ``None``.

    A numeric field (a date) only ever has letters read for digits, which is a fixed mapping, not a search.
    In an alphanumeric field (a document number) at most ``limit`` substitutions are tried and a repair is
    accepted only when exactly one candidate passes at the smallest count: a checksum is a one-in-ten test,
    so an ambiguous search would invent data.
    """
    if numeric:
        text, check = _numeric(text), _numeric(check)
        return (text, check, False) if _check_ok(text, check) else None
    if _check_ok(text, check):
        return text, check, False
    positions = [i for i, c in enumerate(text) if c in _CONFUSABLE]
    for count in range(1, limit + 1):
        passing: set[str] = set()
        for chosen in itertools.combinations(positions, count):
            options = [list(_CONFUSABLE[text[i]]) for i in chosen]
            for combo in itertools.product(*options):
                attempt = list(text)
                for index, char in zip(chosen, combo):
                    attempt[index] = char
                candidate = "".join(attempt)
                if _check_ok(candidate, check):
                    passing.add(candidate)
        if len(passing) == 1:
            return next(iter(passing)), check, True
        if passing:
            return None  # ambiguous: several readings fit, so none is trusted
    # The check digit itself may be the misread character.
    fixed = {r for r in set(_CONFUSABLE.get(check, "")) | set(_LETTER_TO_DIGIT.get(check, "")) if r.isdigit() and _check_ok(text, r)}
    if len(fixed) == 1:
        return text, next(iter(fixed)), True
    return None


@dataclass
class MrzResult:
    valid: bool
    repaired: bool
    checks: dict = field(default_factory=dict)  # which check digits pass: number, birth, expiry, personal, composite
    line1: str = field(default="", repr=False)
    line2: str = field(default="", repr=False)
    document_number: str = field(default="", repr=False)
    nationality: str = field(default="", repr=False)
    birth_date: date | None = field(default=None, repr=False)
    expiry_date: date | None = field(default=None, repr=False)
    sex: str = field(default="", repr=False)
    surname: str = field(default="", repr=False)
    given_names: str = field(default="", repr=False)


def _mrz_date(yymmdd: str, *, future_ok: bool) -> date | None:
    if not re.fullmatch(r"\d{6}", yymmdd):
        return None
    year, month, day = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    this_year = date.today().year % 100
    century = 2000 if (future_ok and year <= this_year + 20) or (not future_ok and year <= this_year) else 1900
    try:
        return date(century + year, month, day)
    except ValueError:
        return None


def _fit(line: str) -> str | None:
    line = re.sub(r"[^A-Z0-9<]", "", normalise_mrz_line(line))
    if 40 <= len(line) < 44:
        line += "<" * (44 - len(line))
    if 44 < len(line) <= 46:
        line = line[:44]
    return line if len(line) == 44 else None


def parse_mrz_td3(line1: str, line2: str) -> MrzResult | None:
    """Parse and, where needed, repair a two-line passport MRZ. ``None`` when it is not TD3-shaped."""
    first, second = _fit(line1), _fit(line2)
    if not first or not second:
        return None
    first = "P" + first[1:]
    number, number_check = second[0:9], second[9]
    birth, birth_check = second[13:19], second[19]
    expiry, expiry_check = second[21:27], second[27]
    personal, personal_check = second[28:42], second[42]
    composite_check = second[43]
    repaired = False
    checks = {}
    for key, (text, check, numeric) in {
        "number": (number, number_check, False), "birth": (birth, birth_check, True),
        "expiry": (expiry, expiry_check, True), "personal": (personal, personal_check, False),
    }.items():
        outcome = _repair_field(text, check, numeric=numeric)
        if outcome is None:
            checks[key] = False
            continue
        new_text, new_check, changed = outcome
        checks[key] = True
        repaired = repaired or changed
        if key == "number":
            number, number_check = new_text, new_check
        elif key == "birth":
            birth, birth_check = new_text, new_check
        elif key == "expiry":
            expiry, expiry_check = new_text, new_check
        else:
            personal, personal_check = new_text, new_check
    nationality = second[10:13]
    sex = second[20] if second[20] in "MF<" else "<"
    composite_text = number + number_check + birth + birth_check + expiry + expiry_check + personal + personal_check
    checks["composite"] = _check_ok(composite_text, composite_check) or _check_ok(composite_text, _numeric(composite_check))
    if not checks["composite"] and checks["number"] and checks["birth"] and checks["expiry"]:
        # The personal-number field is usually empty (all filler) and its filler is the easiest thing to misread.
        # When number, birth and expiry already pass, an empty field that makes the composite check pass too is
        # accepted: the composite digit independently confirms the whole line.
        for filler_check in ("<", "0"):
            attempt = number + number_check + birth + birth_check + expiry + expiry_check + "<" * 14 + filler_check
            if _check_ok(attempt, composite_check) or _check_ok(attempt, _numeric(composite_check)):
                personal, personal_check = "<" * 14, filler_check
                checks["personal"], checks["composite"], repaired = True, True, True
                break
    names = first[5:].split("<<", 1)
    surname = names[0].replace("<", " ").strip()
    given = names[1].replace("<", " ").strip() if len(names) > 1 else ""
    return MrzResult(
        all(checks.values()), repaired, checks, first, second, number.replace("<", ""),
        nationality.replace("<", ""), _mrz_date(birth, future_ok=False), _mrz_date(expiry, future_ok=True),
        sex.replace("<", ""), surname, given)


def find_mrz(text: str) -> MrzResult | None:
    """The best MRZ in a page's text: a checksum-valid pair if any, otherwise the closest parsed pair.

    The second line carries every check digit, so a pair is accepted when that line passes them all even if the
    first line's leading letter was misread; a second line alone (no first line) is accepted the same way.
    """
    candidates = []
    for raw in text.splitlines():
        flat = compact(raw).upper()
        if len(flat) >= 28 and flat.count("<") >= 2 and set(normalise_mrz_line(flat)) <= _MRZ_ALPHABET:
            candidates.append(normalise_mrz_line(flat))
    best: MrzResult | None = None
    blank_first = "P<" + "<" * 42
    for index, second in enumerate(candidates):
        for first in ([candidates[index - 1]] if index else []) + [blank_first]:
            first = "P" + first[1:]
            result = parse_mrz_td3(first, second)
            if result is None:
                continue
            if result.valid:
                return result
            if best is None or sum(result.checks.values()) > sum(best.checks.values()):
                best = result
    return best


# ---------------------------------------------------------------------------- Aadhaar

_DIGIT_LOOKALIKES = {"O": "0", "o": "0", "I": "1", "l": "1", "B": "8", "S": "5", "Z": "2", "G": "6"}
_DIGIT_CONFUSIONS = {"0": "869", "1": "7", "2": "7", "3": "8", "4": "9", "5": "68", "6": "508", "7": "12",
                     "8": "0369", "9": "480"}
_AADHAAR_GROUPS = re.compile(r"(?<![A-Za-z0-9])([0-9OoIlBSZG]{4})[ \t-]?([0-9OoIlBSZG]{4})[ \t-]?([0-9OoIlBSZG]{4})(?![A-Za-z0-9])")


@dataclass
class AadhaarResult:
    valid: bool
    repaired: bool = False
    ambiguous: bool = False
    number: str = field(default="", repr=False)


def check_aadhaar(candidate: str) -> AadhaarResult | None:
    digits = "".join(_DIGIT_LOOKALIKES.get(c, c) for c in candidate)
    if len(digits) != 12 or not digits.isdigit():
        return None
    if digits[0] in "01":
        return AadhaarResult(False, number=digits)
    if verhoeff_valid(digits):
        return AadhaarResult(True, number=digits)
    fixes = set()
    for position, char in enumerate(digits):
        for replacement in _DIGIT_CONFUSIONS.get(char, ""):
            attempt = digits[:position] + replacement + digits[position + 1:]
            if attempt[0] not in "01" and verhoeff_valid(attempt):
                fixes.add(attempt)
    if len(fixes) == 1:
        return AadhaarResult(True, repaired=True, number=next(iter(fixes)))
    return AadhaarResult(False, ambiguous=len(fixes) > 1, number=digits)


def find_aadhaar_numbers(text: str) -> list[AadhaarResult]:
    results: list[AadhaarResult] = []
    for match in _AADHAAR_GROUPS.finditer(text):
        real_digits = sum(c.isdigit() for g in match.groups() for c in g)
        if real_digits < 8:
            continue
        outcome = check_aadhaar("".join(match.groups()))
        if outcome and outcome.number not in {r.number for r in results}:
            results.append(outcome)
    return results


# ---------------------------------------------------------------------------- contact + dates

_MOBILE = re.compile(r"(?<![\d])(?:(?:\+|00)?91[\s-]?)?([6-9]\d{4}[\s-]?\d{5})(?![\d])")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")


def find_mobiles(text: str) -> list[str]:
    found = []
    for match in _MOBILE.finditer(text):
        number = re.sub(r"\D", "", match.group(1))
        if len(number) == 10 and number not in found:
            found.append(number)
    return found


def find_emails(text: str) -> list[str]:
    return list(dict.fromkeys(m.group().lower() for m in _EMAIL.finditer(text)))


def dates_sane(birth: date | None, issue: date | None = None, expiry: date | None = None) -> bool:
    """Birth before issue before expiry; none in an impossible place."""
    today = date.today()
    if birth and (birth > today or birth.year < 1900):
        return False
    if birth and issue and not birth < issue:
        return False
    if issue and expiry and not issue < expiry:
        return False
    if birth and expiry and not birth < expiry:
        return False
    return True


# ---------------------------------------------------------------------------- the record

@dataclass
class LocalPatterns:
    """What the local read found, by page. Values stay in memory; ``flags`` is what may be stored or logged."""
    mrz_pages: list[int] = field(default_factory=list)
    mrz_valid_pages: list[int] = field(default_factory=list)
    mrz_repaired: bool = False
    mrz_fields_ok: list[str] = field(default_factory=list)
    aadhaar_pages: list[int] = field(default_factory=list)
    aadhaar_valid_pages: list[int] = field(default_factory=list)
    aadhaar_repaired: bool = False
    email_pages: list[int] = field(default_factory=list)
    mobile_pages: list[int] = field(default_factory=list)
    dates_sane: bool | None = None
    mrz: MrzResult | None = field(default=None, repr=False)
    aadhaar: AadhaarResult | None = field(default=None, repr=False)
    emails: list[str] = field(default_factory=list, repr=False)
    mobiles: list[str] = field(default_factory=list, repr=False)

    def flags(self) -> dict:
        return {
            "mrz_found": bool(self.mrz_pages), "mrz_checksums_valid": bool(self.mrz_valid_pages),
            "mrz_repaired": self.mrz_repaired, "mrz_pages": list(self.mrz_pages),
            # which fields passed their own check digit (names only): number, birth, expiry, personal, composite
            "mrz_fields_verified": list(self.mrz_fields_ok),
            "aadhaar_found": bool(self.aadhaar_pages), "aadhaar_checksum_valid": bool(self.aadhaar_valid_pages),
            "aadhaar_repaired": self.aadhaar_repaired, "aadhaar_pages": list(self.aadhaar_pages),
            "email_found": bool(self.email_pages), "email_pages": list(self.email_pages),
            "mobile_found": bool(self.mobile_pages), "mobile_pages": list(self.mobile_pages),
            "dates_sane": self.dates_sane,
        }


def scan_pages(texts: dict[int, str]) -> LocalPatterns:
    """Patterns over ``{page number: text}``."""
    result = LocalPatterns()
    for number in sorted(texts):
        text = texts[number]
        if not text:
            continue
        mrz = find_mrz(text)
        if mrz is not None:
            result.mrz_pages.append(number)
            if mrz.valid:
                result.mrz_valid_pages.append(number)
            if result.mrz is None or (mrz.valid and not result.mrz.valid):
                result.mrz = mrz
                result.mrz_repaired = mrz.repaired
                result.mrz_fields_ok = [name for name, ok in mrz.checks.items() if ok]
        numbers = find_aadhaar_numbers(text)
        if numbers:
            result.aadhaar_pages.append(number)
            valid = next((n for n in numbers if n.valid), None)
            if valid:
                result.aadhaar_valid_pages.append(number)
            if result.aadhaar is None or (valid and not result.aadhaar.valid):
                result.aadhaar = valid or numbers[0]
                result.aadhaar_repaired = bool(valid and valid.repaired)
        emails = find_emails(text)
        if emails:
            result.email_pages.append(number)
            result.emails.extend(e for e in emails if e not in result.emails)
        mobiles = find_mobiles(text)
        if mobiles:
            result.mobile_pages.append(number)
            result.mobiles.extend(m for m in mobiles if m not in result.mobiles)
    if result.mrz is not None:
        result.dates_sane = dates_sane(result.mrz.birth_date, None, result.mrz.expiry_date)
    return result


# ---------------------------------------------------------------------------- cross-check

def _dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _walk(value, wanted: set[str], found: list):
    value = _dump(value)
    if isinstance(value, dict):
        for key, inner in value.items():
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in wanted and isinstance(_dump(inner), (str, int, date)):
                found.append(_dump(inner))
            else:
                _walk(inner, wanted, found)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _walk(inner, wanted, found)


def _first(veris_result, *keys: str):
    found: list = []
    _walk(veris_result, {re.sub(r"[^a-z0-9]", "", k.lower()) for k in keys}, found)
    return next((v for v in found if v not in ("", None)), None)


def _norm_id(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper().replace("O", "0")) if value is not None else ""


def _to_date(value) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern, order in ((r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", "ymd"), (r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", "dmy")):
        m = re.fullmatch(pattern, text[:10])
        if m:
            a, b, c = (int(x) for x in m.groups())
            try:
                return date(a, b, c) if order == "ymd" else date(c, b, a)
            except ValueError:
                return None
    return None


def _name_tokens(value) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", str(value or "").casefold()) if len(t) > 1}


def _near(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 4:
        return False
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b)) <= 1


def local_patterns(kind: str, text: str) -> dict:
    """Identifiers read locally from the pages of one document kind, for ``cross_check`` (JSON-safe).

    The MRZ fields carry their own check-digit results so that only a locally *verified* field is ever held
    against Veris. The page text itself is not returned.
    """
    out: dict = {}
    if not text:
        return out
    if kind == "passport":
        mrz = find_mrz(text)
        if mrz is not None:
            out.update({
                "passport_number": mrz.document_number or None,
                "date_of_birth": mrz.birth_date.isoformat() if mrz.birth_date else None,
                "date_of_expiry": mrz.expiry_date.isoformat() if mrz.expiry_date else None,
                "surname": mrz.surname or None, "given_names": mrz.given_names or None,
                "nationality": mrz.nationality or None,
                "mrz_checksums_valid": mrz.valid, "mrz_repaired": mrz.repaired,
                "number_ok": bool(mrz.checks.get("number")), "birth_ok": bool(mrz.checks.get("birth")),
                "expiry_ok": bool(mrz.checks.get("expiry")),
            })
    elif kind == "aadhaar":
        numbers = find_aadhaar_numbers(text)
        pick = next((n for n in numbers if n.valid), numbers[0] if numbers else None)
        if pick is not None:
            out.update({"aadhaar_number": pick.number, "aadhaar_checksum_valid": pick.valid,
                        "aadhaar_repaired": pick.repaired})
    else:
        emails, mobiles = find_emails(text), find_mobiles(text)
        if emails:
            out["email"] = emails[0]
        if mobiles:
            out["mobile"] = mobiles[0]
    return {k: v for k, v in out.items() if v is not None}


def patterns_dict(local: "LocalPatterns") -> dict:
    """The same shape as ``local_patterns`` from a page scan (used when only a ``LocalPatterns`` is at hand)."""
    out: dict = {}
    mrz = local.mrz
    if mrz is not None:
        out.update({
            "passport_number": mrz.document_number or None,
            "date_of_birth": mrz.birth_date.isoformat() if mrz.birth_date else None,
            "date_of_expiry": mrz.expiry_date.isoformat() if mrz.expiry_date else None,
            "surname": mrz.surname or None, "given_names": mrz.given_names or None,
            "mrz_checksums_valid": mrz.valid, "number_ok": bool(mrz.checks.get("number")),
            "birth_ok": bool(mrz.checks.get("birth")), "expiry_ok": bool(mrz.checks.get("expiry")),
        })
    if local.aadhaar is not None:
        out.update({"aadhaar_number": local.aadhaar.number, "aadhaar_checksum_valid": local.aadhaar.valid})
    if local.emails:
        out["email"] = local.emails[0]
    if local.mobiles:
        out["mobile"] = local.mobiles[0]
    return {k: v for k, v in out.items() if v is not None}


def cross_check(local, veris_result) -> dict:
    """Compare what the local read found with what Veris returned.

    ``local`` is the dict from ``local_patterns`` (or a ``LocalPatterns``); ``veris_result`` is a Veris
    response, a dict or a list of them (the structure is searched, so passport, Aadhaar and resume
    responses all work). Returns ``{"agreements": [...], "conflicts": [...], "local_only": [...],
    "veris_only": [...]}`` holding field names only, never values. A conflict should become a review
    flag. A field read locally whose own check digit failed is not held against Veris (it is not
    reliable enough to contradict it); an unverified field that Veris returned is ``veris_only``.
    """
    if isinstance(local, LocalPatterns):
        local = patterns_dict(local)
    local = dict(local or {})
    result: dict[str, list[str]] = {"agreements": [], "conflicts": [], "local_only": [], "veris_only": []}

    def compare(name: str, mine, theirs, same, reliable: bool = True) -> None:
        if mine in (None, "") and theirs in (None, ""):
            return
        if theirs in (None, ""):
            result["local_only"].append(name)
        elif mine in (None, "") or not reliable:
            result["veris_only"].append(name)
        elif same(mine, theirs):
            result["agreements"].append(name)
        else:
            result["conflicts"].append(name)

    if "passport_number" in local or "date_of_birth" in local or "date_of_expiry" in local:
        compare("passport_number", local.get("passport_number"),
                _first(veris_result, "passport_number", "passport_no", "document_number"),
                lambda a, b: _norm_id(a) == _norm_id(b), bool(local.get("number_ok", local.get("mrz_checksums_valid"))))
        compare("date_of_birth", _to_date(local.get("date_of_birth")),
                _to_date(_first(veris_result, "date_of_birth", "dob", "birth_date")), lambda a, b: a == b,
                bool(local.get("birth_ok", local.get("mrz_checksums_valid"))))
        compare("date_of_expiry", _to_date(local.get("date_of_expiry")),
                _to_date(_first(veris_result, "date_of_expiry", "expiry_date", "expiry", "valid_until")),
                lambda a, b: a == b, bool(local.get("expiry_ok", local.get("mrz_checksums_valid"))))
        mine = _name_tokens(f"{local.get('surname', '')} {local.get('given_names', '')}")
        theirs_text = " ".join(str(_first(veris_result, k) or "") for k in ("surname", "given_names", "given_name", "name", "full_name"))
        compare("name", mine or None, _name_tokens(theirs_text) or None,
                lambda a, b: all(any(_near(x, y) for y in b) for x in a) or all(any(_near(y, x) for x in a) for y in b),
                bool(local.get("mrz_checksums_valid")))
    if "aadhaar_number" in local:
        theirs = _first(veris_result, "aadhaar_number", "uid", "aadhaar")
        if theirs is not None and "x" in str(theirs).casefold():
            theirs = None  # a masked number cannot be compared
        compare("aadhaar_number", local.get("aadhaar_number"), theirs,
                lambda a, b: re.sub(r"\D", "", str(a)) == re.sub(r"\D", "", str(b)), bool(local.get("aadhaar_checksum_valid")))
    if "email" in local:
        compare("email", local["email"], _first(veris_result, "email", "email_address"),
                lambda a, b: str(a).lower() == str(b).strip().lower())
    if "mobile" in local:
        theirs = _first(veris_result, "mobile", "mobile_number", "phone", "phone_number", "contact_number")
        compare("mobile", local["mobile"], re.sub(r"\D", "", str(theirs))[-10:] if theirs else None, lambda a, b: a == b)
    return result


def identity_evidence(text: str) -> dict:
    """Checksum-backed evidence that a page holds a passport zone or an Aadhaar number.

    ``{"passport": bool, "aadhaar": bool}``: a passport when an MRZ passes its composite check or at least three of
    its field checks, an Aadhaar when a 12-digit number passes the Verhoeff check. The page classifier can use this as
    strong evidence, because ordinary text cannot pass these tests by accident.
    """
    mrz = find_mrz(text or "")
    passport = bool(mrz and (mrz.valid or sum(bool(v) for k, v in mrz.checks.items() if k != "composite") >= 3))
    return {"passport": passport, "aadhaar": any(n.valid for n in find_aadhaar_numbers(text or ""))}
