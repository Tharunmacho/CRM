"""Bind provider page views to the physical pages submitted for one OCR job."""
from copy import deepcopy
import re
from .person_split import incompatible, page_identity


_DUAL_SCAN = re.compile(
    r"\bdual[- ]page scan detected\b.*?\bsplit\s+1\s+(?:input\s+)?page(?:\(s\)|s)?\s+into\s+2\s+halves\b",
    re.I | re.S,
)
_OLD_PASSPORT = re.compile(
    r"\b(?:old|previous|expired)\s+passport\s*(?:no\.?|number)\s*[:\-]?\s*[A-Z](?=[A-Z0-9]*\d)[A-Z0-9]{5,8}\b", re.I,
)


def source_page_positions(kind, response, expected):
    """Return one physical position per returned view, or None for unproved coverage.

    The passport provider explicitly reports splitting a single photocopied sheet
    into two logical views. That is the sole supported expansion: extra pages in
    a multi-page request, missing/blank views and unannounced expansions still fail.
    """
    pages = list(response.pages)
    if expected < 1 or any(not page.text.strip() for page in pages):
        return None
    numbers = [page.page_number for page in pages]
    if len(numbers) == expected and set(numbers) == set(range(1, expected + 1)):
        return numbers
    if (kind == "passport" and expected == 1 and len(numbers) == 2 and set(numbers) == {1, 2}
            and any(_DUAL_SCAN.search(str(warning)) for warning in (getattr(response, "warnings", None) or []))):
        identities = [page_identity(_OLD_PASSPORT.sub("", page.text)) for page in pages]
        if any(identity["mixed"] for identity in identities) or incompatible(*identities):
            return None
        return [1, 1]
    return None


def physical_passport_payload(payload, response, expected):
    """For chunk merging, group proven views under their physical source page.

    The original payload is never changed. Both complete provider page objects
    remain under provider_views; the caller also retains the exact chunk result.
    """
    if source_page_positions("passport", response, expected) != [1, 1]:
        return payload
    result = deepcopy(payload)
    views = result["pages"]
    first = deepcopy(min(views, key=lambda page: page["page_number"]))
    first.update(page_number=1, text="\n\n".join(page["text"] for page in sorted(views, key=lambda page: page["page_number"])),
                 provider_views=views)
    result["pages"] = [first]
    return result
