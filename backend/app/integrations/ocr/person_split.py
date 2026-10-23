"""Conservative local identity evidence for proposed document/person groups.

This is a proposal, never authority to create people or attach their pages. A
human must confirm page ownership. Contact details in source-message headers
are intentionally absent from this detector.
"""
from __future__ import annotations

import re
import unicodedata

from app.services.candidate_identity import name_relation, normalize_email, normalize_passport, normalize_phone

NAME = re.compile(r"^\s*(?:candidate\s+name|full\s+name|name\s+of\s+(?:the\s+)?(?:candidate|applicant)|name)\s*[:\-]\s*([^\n|;]{3,150})", re.I | re.M)
PHONE = re.compile(r"(?:mobile|phone|telephone|contact\s*(?:no\.?|number)?)\s*[:\-]?\s*(\+?[\d ()\-]{7,24})", re.I)
EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,}", re.I)
# One token (letter + 5-8 letters/digits, at least one digit), never spaces: otherwise the
# next label on the line ("Date of Issue") is absorbed into the number.
PASSPORT = re.compile(r"(?:passport\s*(?:no\.?|number))\s*[:\-]?\s*([A-Z](?=[A-Z0-9]*\d)[A-Z0-9]{5,8})\b", re.I)
# Headings after which "Name:" lines belong to other people, not the candidate.
_OTHER_PEOPLE = re.compile(r"^\s*(?:references?|referees?|reporting\s+(?:to|manager|authority)|supervisor)\b[^\n]*$", re.I | re.M)
_NEXT_SECTION = re.compile(
    r"^\s*(?:personal\s+(?:details|information|data)|declaration|education(?:al)?(?:\s+qualifications?)?|"
    r"work\s+experience|professional\s+experience|employment\s+history|skills|languages|projects|"
    r"certifications?|passport\s+details?)\s*:?\s*$", re.I | re.M)


def _candidate_text(text):
    """Drop reference/referee sections, whose 'Name:' lines name other people."""
    kept, skipping = [], False
    for line in text.splitlines():
        if _OTHER_PEOPLE.match(line):
            skipping = True
            continue
        if skipping and _NEXT_SECTION.match(line):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def name_key(value):
    return " ".join(re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKC", value or "").casefold()))


def page_identity(text):
    """Return only explicit labels/contacts, with competing names kept visible."""
    text = unicodedata.normalize("NFKC", text or "")
    # Referee / reporting-manager "Name:" lines are other people and never the candidate.
    names = list(dict.fromkeys(" ".join(match.strip().split()) for match in NAME.findall(_candidate_text(text))))
    names = [name for name in names if len(name_key(name).split()) >= 2 and len(name) <= 150]
    phones = sorted({value for raw in PHONE.findall(text) if (value := normalize_phone(raw))})
    emails = sorted({normalize_email(raw.rstrip('.,;')) for raw in EMAIL.findall(text)})
    passports = sorted({normalize_passport(raw) for raw in PASSPORT.findall(text)})
    # A single page with multiple explicit candidate names cannot be safely split
    # at page boundaries. No whole-page attachment is allowed for that page.
    # Several names alone are not proof (a form may name a spouse or a nominee): the page
    # must also carry more than one phone, email or passport number.
    several_names = len({name_key(value) for value in names}) > 1
    mixed = several_names and (len(phones) > 1 or len(emails) > 1 or len(passports) > 1)
    return {"names": names[:10], "phones": phones[:10], "emails": emails[:10], "passports": passports[:10],
            "mixed": mixed, "reliable": bool(names and (phones or emails or passports)) and not several_names}


def incompatible(left, right):
    # Names may be written in a different order, with initials or a missing middle name;
    # only names sharing no word at all differ. Passport numbers are compared exactly.
    lnames, rnames = left.get("names", []), right.get("names", [])
    if lnames and rnames and all(name_relation(x, y) == "different" for x in lnames for y in rnames):
        return True
    a, b = {normalize_passport(value) for value in left.get("passports", [])}, {normalize_passport(value) for value in right.get("passports", [])}
    return bool(a and b and a.isdisjoint(b))


def same_person(left, right):
    if left.get("mixed") or right.get("mixed") or incompatible(left, right):
        return False
    # A shared name alone is insufficient; two people can have the same name.
    # Matching labelled name plus contact, or matching passport, supports a proposal.
    passports = set(left.get("passports", [])) & set(right.get("passports", []))
    names = {name_key(name) for name in left.get("names", [])} & {name_key(name) for name in right.get("names", [])}
    contacts = (set(left.get("emails", [])) & set(right.get("emails", []))) | (set(left.get("phones", [])) & set(right.get("phones", [])))
    return bool(passports or (names and contacts))


def propose_groups(pages):
    """Group corroborated pages; leave continuations/ambiguous pages unassigned."""
    groups = []
    for page in pages:
        identity = page.get("identity", {})
        if not identity.get("reliable") or page.get("status") != "read":
            continue
        matches = [group for group in groups if same_person(group["identity"], identity)]
        if len(matches) > 1:
            continue
        if matches:
            group = matches[0]
        else:
            group = {"label": identity["names"][0], "identity": identity, "pages": [],
                     "fields": {"name": identity["names"][0], "phone": next(iter(identity.get("phones", [])), ""),
                                "email": next(iter(identity.get("emails", [])), ""), "passport_no": next(iter(identity.get("passports", [])), "")}}
            groups.append(group)
        group["pages"].append({"sourceId": page["sourceId"], "pageNumber": page["pageNumber"], "kind": page.get("kind") if page.get("kind") in {"resume", "passport", "aadhaar", "education", "experience", "photo"} else "source_bundle"})
    return groups


def multiple_people(pages):
    identities = [page_identity(page.text) for page in pages if page.status == "read"]
    if any(identity["mixed"] for identity in identities):
        return True
    explicit = [identity for identity in identities if identity.get("names")]
    return any(incompatible(left, right) for index, left in enumerate(explicit) for right in explicit[index + 1:])
