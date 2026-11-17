"""
Months of work experience from a resume's work-history entries. Pure functions, no I/O.

The reader (Veris) sometimes gives each job's ``duration_months``, sometimes only the start/end text
("Jan 2018" / "Mar 2020"), sometimes one free-text period ("2018 - 2020", "2019 to present", "2 years 3 months").
Everything here works from whatever is there and returns 0 months, never a guess, when nothing can be read.

Rules:
* A job's months are the reader's ``duration_months`` when given, else the months between its start and end
  (end "Present"/"Current"/"Till date" = today), else a stated duration ("2 years 3 months").
* Dates are counted in whole calendar months (Jan 2018 -> Mar 2020 = 26). A year-only date takes the month of the
  other end, so "2018 - 2020" is exactly 24 months and "2019 to present" counts whole years only.
* A job with no end and no stated duration is NOT assumed to be current: it adds nothing.
* Jobs whose months were worked out from their dates are merged as intervals, so overlapping jobs are not counted
  twice. Jobs carrying the reader's own ``duration_months`` are added as given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

_MONTH_NAMES = (
    "january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|"
    "september|sept|sep|october|oct|november|nov|december|dec"
)
_MONTH_NUMBER = {name[:3]: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

_TOKEN = re.compile(
    rf"""
    (?P<present>\b(?:present|currently|current|till\s+(?:date|now|today)|to\s+date|until\s+now|now|ongoing|today)\b)
  | (?P<dmy>\b(?P<d1>\d{{1,2}})[/.\-](?P<m1>\d{{1,2}})[/.\-](?P<y1>\d{{4}})\b)
  | (?P<ymd>\b(?P<y2>\d{{4}})[/.\-](?P<m2>\d{{1,2}})[/.\-](?P<d2>\d{{1,2}})\b)
  | (?P<my>\b(?P<m3>\d{{1,2}})[/.\-](?P<y3>\d{{4}})\b)
  | (?P<ym>\b(?P<y4>\d{{4}})[/.\-](?P<m4>\d{{1,2}})\b)
  | (?P<mon>\b(?P<mn>{_MONTH_NAMES})\b\.?\s*(?:,\s*)?(?:(?P<y5>\d{{4}})\b|['’]\s*(?P<y6>\d{{2}})\b))
  | (?P<y>\b(?P<y7>(?:19|20)\d{{2}})\b)
    """,
    re.I | re.X,
)
_YEARS_TEXT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?|y)\b", re.I)
_MONTHS_TEXT = re.compile(r"(\d+)\s*(?:months?|mths?|mos?|m)\b", re.I)

_START_KEYS = ("start", "start_date", "from")
_END_KEYS = ("end", "end_date", "to")
_PERIOD_KEYS = ("period", "dates", "date_range", "tenure", "duration", "duration_human", "years")
_DURATION_KEYS = ("duration_human", "duration", "period", "tenure")

_PRESENT = "present"
_INVALID = "invalid"
MAX_MONTHS = 12 * 60  # a single job longer than sixty years is a misread, not experience

_INDIA = re.compile(r"\b(?:india|indian|bharat|ind)\b", re.I)


def _today() -> date:
    return date.today()


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = " ".join(value.replace("\x00", " ").split())
    return value or None


def _first(entry: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        found = _text(entry.get(key))
        if found:
            return found
    return None


def _positive(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


def _tokens(text: str, today: date) -> list:
    """Dates and "present" words in reading order: ("present",), (year, month or None) or the INVALID marker."""
    out: list = []
    for match in _TOKEN.finditer(text):
        if match.group("present"):
            out.append(_PRESENT)
            continue
        if match.group("dmy"):
            day, month, year = int(match.group("d1")), int(match.group("m1")), int(match.group("y1"))
            valid = 1 <= day <= 31 and 1 <= month <= 12
        elif match.group("ymd"):
            year, month, day = int(match.group("y2")), int(match.group("m2")), int(match.group("d2"))
            valid = 1 <= day <= 31 and 1 <= month <= 12
        elif match.group("my"):
            month, year = int(match.group("m3")), int(match.group("y3"))
            valid = 1 <= month <= 12
        elif match.group("ym"):
            year, month = int(match.group("y4")), int(match.group("m4"))
            valid = 1 <= month <= 12
        elif match.group("mon"):
            month = _MONTH_NUMBER[match.group("mn").casefold()[:3]]
            year = int(match.group("y5")) if match.group("y5") else 2000 + int(match.group("y6"))
            valid = True
        else:
            year, month, valid = int(match.group("y7")), None, True
        if not valid or not 1950 <= year <= today.year + 1:
            out.append(_INVALID)
        else:
            out.append((year, month))
    return out


def _dates(entry: dict, today: date) -> tuple[int, int] | None:
    """(start, end) as month numbers (year * 12 + month - 1), or None when the entry's dates cannot be read exactly."""
    start = end = None
    start_text, end_text = _first(entry, _START_KEYS), _first(entry, _END_KEYS)
    if start_text:
        found = _tokens(start_text, today)
        if found:
            start = found[0]
            if len(found) == 2 and not end_text:
                end = found[1]  # the whole range was put in one field
    if end_text:
        found = _tokens(end_text, today)
        if found:
            end = found[0]
    if start is None or end is None:
        for key in _PERIOD_KEYS:
            text = _text(entry.get(key))
            found = _tokens(text, today) if text else []
            if len(found) == 2:
                start = found[0] if start is None else start
                end = found[1] if end is None else end
                break
    if start in (None, _INVALID, _PRESENT) or end in (None, _INVALID):
        return None
    sy, sm = start
    if end == _PRESENT:
        ey, em = today.year, today.month
    else:
        ey, em = end
    if sm is None and em is None:
        sm = em = 1
    elif sm is None:
        sm = em
    elif em is None:
        em = sm
    first, last, now = sy * 12 + sm - 1, ey * 12 + em - 1, today.year * 12 + today.month - 1
    if first > now:
        return None
    last = min(last, now)
    return (first, last) if last >= first else None


def duration_from_text(text: str | None) -> int:
    """Months in a stated duration such as "2 years 3 months", "2.5 yrs" or "18 months"; 0 when there is none."""
    text = _text(text)
    if not text:
        return 0
    years, months = _YEARS_TEXT.search(text), _MONTHS_TEXT.search(text)
    total = 0
    if years:
        total += round(float(years.group(1)) * 12)
    if months:
        total += int(months.group(1))
    return total if 0 < total <= MAX_MONTHS else 0


@dataclass(frozen=True)
class Span:
    months: int
    interval: tuple[int, int] | None = None  # (first month, end month) when the job's place on the calendar is known


def entry_span(entry: dict, today: date | None = None) -> Span:
    today = today or _today()
    explicit = _positive(entry.get("duration_months"))
    dates = _dates(entry, today)
    if explicit:
        # The reader's own months are taken as they are and added up, not merged: its months are authoritative and
        # a resume can legitimately list distinct jobs with identical year-only ranges.
        return Span(min(explicit, MAX_MONTHS))
    if dates and dates[1] > dates[0]:
        months = dates[1] - dates[0]
        if months <= MAX_MONTHS:
            return Span(months, dates)
    return Span(next((m for m in (duration_from_text(_first(entry, (key,))) for key in _DURATION_KEYS) if m), 0))


def country_of(entry: dict) -> str | None:
    return _first(entry, ("country",))


def is_india(entry: dict) -> bool:
    country = country_of(entry)
    if country:
        return bool(_INDIA.search(country))
    return bool(_INDIA.search(_first(entry, ("location",)) or ""))


# Foreign countries (and the Gulf cities a work history names instead of the country), written as plain lower-case words with
# no dots: "U.A.E" is read as "uae". A job counts as abroad only when its country names one of these; an Indian state or city,
# or any text this list does not know, is not evidence of working abroad (it still counts toward the total).
_FOREIGN_PLACES = (
    # Gulf and the Middle East
    "kuwait", "kuwait city", "saudi arabia", "saudi", "ksa", "kingdom of saudi arabia", "uae", "u a e", "united arab emirates", "emirates",
    "dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "ras al khaimah", "umm al quwain", "al ain", "qatar", "doha", "oman", "muscat",
    "sohar", "salalah", "bahrain", "manama", "riyadh", "jeddah", "jiddah", "dammam", "jubail", "yanbu", "khobar", "al khobar", "mecca",
    "makkah", "medina", "jordan", "amman", "iraq", "baghdad", "iran", "israel", "lebanon", "syria", "yemen", "palestine", "egypt", "cairo",
    "turkey", "turkiye", "istanbul", "cyprus",
    # Asia and the Pacific
    "singapore", "malaysia", "kuala lumpur", "maldives", "sri lanka", "colombo", "nepal", "kathmandu", "bangladesh", "dhaka", "pakistan",
    "afghanistan", "bhutan", "myanmar", "burma", "thailand", "vietnam", "laos", "cambodia", "indonesia", "philippines", "brunei",
    "china", "hong kong", "taiwan", "japan", "south korea", "korea", "mongolia", "kazakhstan", "uzbekistan", "turkmenistan", "tajikistan",
    "kyrgyzstan", "azerbaijan", "armenia", "georgia", "australia", "new zealand", "fiji", "papua new guinea",
    # Europe
    "uk", "u k", "united kingdom", "great britain", "britain", "england", "scotland", "wales", "northern ireland", "ireland", "london",
    "germany", "france", "italy", "spain", "portugal", "netherlands", "holland", "belgium", "luxembourg", "switzerland", "austria",
    "denmark", "norway", "sweden", "finland", "iceland", "poland", "czech republic", "czechia", "slovakia", "hungary", "romania",
    "bulgaria", "greece", "serbia", "croatia", "slovenia", "bosnia", "albania", "macedonia", "montenegro", "ukraine", "russia", "belarus",
    "lithuania", "latvia", "estonia", "moldova", "malta",
    # Americas
    "usa", "us", "u s", "u s a", "united states", "united states of america", "america", "canada", "mexico", "brazil", "argentina", "chile",
    "peru", "colombia", "venezuela", "ecuador", "bolivia", "uruguay", "paraguay", "cuba", "jamaica", "trinidad", "panama", "costa rica",
    "guatemala", "honduras", "nicaragua", "el salvador", "haiti", "dominican republic", "bahamas", "barbados", "guyana", "suriname",
    # Africa
    "south africa", "nigeria", "ghana", "kenya", "tanzania", "uganda", "ethiopia", "sudan", "south sudan", "libya", "algeria", "tunisia",
    "morocco", "angola", "zambia", "zimbabwe", "mozambique", "malawi", "botswana", "namibia", "madagascar", "mauritius", "seychelles",
    "senegal", "ivory coast", "cameroon", "congo", "gabon", "rwanda", "burundi", "somalia", "djibouti", "eritrea", "liberia",
    "sierra leone", "gambia", "mali", "niger", "chad", "togo", "benin", "lesotho", "eswatini", "swaziland",
)
_FOREIGN = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(name).replace(r"\ ", " ")
                                                      for name in sorted(_FOREIGN_PLACES, key=len, reverse=True)) + r")(?![a-z0-9])")


def _is_foreign_place(text: str | None) -> bool:
    """Whether the text names a country (or Gulf city) outside India. "U.A.E", "Dubai, UAE" and "KUWAIT" do; "Tamil Nadu" does not."""
    if not text:
        return False
    plain = " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold().replace(".", "")).split())
    return bool(_FOREIGN.search(plain))


def is_abroad(entry: dict) -> bool:
    """A job abroad: the reader flagged it overseas, or its country is a known foreign one. An explicit ``is_overseas: False``
    keeps a job that names no foreign country in India / unknown (the reader's default model sends False for every job, so it
    cannot outvote a country that is plainly foreign). A country the list does not know is never counted as abroad."""
    if entry.get("is_overseas") is True:
        return True
    country = country_of(entry)
    return bool(country) and not _INDIA.search(country) and _is_foreign_place(country)


def union_months(intervals: list[tuple[int, int]]) -> int:
    total, reach = 0, None
    for first, last in sorted(intervals):
        if reach is None or first > reach:
            total += last - first
            reach = last
        elif last > reach:
            total += last - reach
            reach = last
    return total


@dataclass(frozen=True)
class Totals:
    total: int = 0
    india: int = 0          # jobs known to be in India
    abroad: int = 0
    naive_total: int = 0    # the plain sum of every job (what counting overlaps twice would give)
    naive_abroad: int = 0
    naive_india: int = 0


def summarize(entries: list, today: date | None = None) -> Totals:
    """Months across work-history entries; dated jobs are merged so overlapping jobs are counted once."""
    today = today or _today()
    seen: set[tuple] = set()
    jobs: list[tuple[dict, Span]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        span = entry_span(entry, today)
        if not span.months:
            continue
        key = tuple(str(entry.get(name) or "").casefold() for name in ("company", "title", "start", "end")) + (span.months,)
        if key in seen:
            continue
        seen.add(key)
        jobs.append((entry, span))

    def count(chosen: list[tuple[dict, Span]]) -> tuple[int, int]:
        dated = [span.interval for _, span in chosen if span.interval]
        undated = sum(span.months for _, span in chosen if not span.interval)
        return union_months(dated) + undated, sum(span.months for _, span in chosen)

    abroad = [(e, s) for e, s in jobs if is_abroad(e)]
    india = [(e, s) for e, s in jobs if not is_abroad(e) and is_india(e)]
    total, naive = count(jobs)
    (abroad_months, abroad_naive), (india_months, india_naive) = count(abroad), count(india)
    return Totals(total, india_months, abroad_months, naive, abroad_naive, india_naive)


def resolve(explicit: int, computed: int, naive: int) -> int:
    """The reader's own total against the one worked out from the jobs: the larger wins, except that a reader total
    equal to the plain sum of overlapping jobs is that double count and is replaced by the merged figure."""
    if explicit and computed and computed < naive and explicit == naive:
        return computed
    return max(explicit, computed)
