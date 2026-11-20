"""Canonical phone numbers for candidates ('+91XXXXXXXXXX'), written once so every screen and export shows the same thing.

Pure and conservative: a value that cannot be read with confidence is returned exactly as it was typed, never guessed at.
Duplicate detection is unaffected: it compares digits only (candidate_identity.normalize_phone) and drops the home calling
code from a ten-digit number, so '+919876543210', '098765 43210' and '9876543210' stay the same person.
"""
from __future__ import annotations

import re

NATIONAL_DIGITS = 10        # a home number: ten digits after the calling code (or after a trunk "0")
_ALLOWED = re.compile(r"^[\d\s\-().+/]*$")
_SPACES = re.compile(r"\s+")


def canonical_phone(value, home: str | None = None):
    """'+<code><number>' when `value` is a number we can read with confidence, else `value` itself.

    `home` is the agency's calling code as digits ('91'). Without it a number that has no country code of its own is left as typed.
    """
    if not isinstance(value, str):
        return value
    raw = _SPACES.sub(" ", value).strip()
    if not raw or not _ALLOWED.match(raw) or "/" in raw:
        return value                      # letters, an extension or a second number: leave it alone
    plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits or len(set(digits)) == 1:
        return value                      # nothing, or a placeholder such as 0000000000
    home = re.sub(r"\D", "", home or "")
    if plus or digits.startswith("00"):
        number = digits if plus else digits[2:]
        return f"+{number}" if 8 <= len(number) <= 15 and not number.startswith("0") else value
    national = digits[1:] if digits.startswith("0") and len(digits) == NATIONAL_DIGITS + 1 else digits
    if len(national) == NATIONAL_DIGITS:
        return f"+{home}{national}" if home else value
    if home and national.startswith(home) and len(national) == len(home) + NATIONAL_DIGITS:
        return f"+{national}"             # already carries the home calling code, typed without the plus
    return value


# Calling codes recognised when a number is written out in groups (the agency's own code is always recognised too).
# The same list is in frontend/src/shared/lib/phone.ts.
GROUPING_CODES = frozenset({"1", "7", "20", "27", "33", "44", "49", "60", "61", "62", "63", "65", "81", "82", "86", "90", "91", "92", "94", "98",
                            "880", "965", "966", "968", "971", "973", "974", "977"})


def format_phone(value, home: str | None = None):
    """'+91 98765 43210' for a number that can be read with confidence (home code added to a national number), else the value as typed."""
    if not isinstance(value, str):
        return value
    canonical = canonical_phone(value, home)
    if not canonical.startswith("+") or not canonical[1:].isdigit():
        return value.strip()
    digits = canonical[1:]
    home = re.sub(r"\D", "", home or "")
    code = next((digits[:n] for n in (1, 2, 3) if digits[:n] in GROUPING_CODES or (home and digits[:n] == home)), "")
    national = digits[len(code):]
    if not code or not 7 <= len(national) <= 11:
        return canonical
    groups = [national[:5], national[5:]] if len(national) == 10 else [national[:3], national[3:6], national[6:]] if len(national) == 9 else [national]
    return f"+{code} {' '.join(groups)}"
