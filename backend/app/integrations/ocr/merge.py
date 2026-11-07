"""Combine the results of several Veris jobs that read different pages of one document.

Nothing a chunk returned may be lost and nothing may be silently overwritten:

* scalars keep the first non-empty value; a later, different value is recorded as a conflict
  (the caller sends the extraction to review) instead of replacing it;
* lists (education, experience, skills, languages, certifications ...) are concatenated and
  de-duplicated case-insensitively, entries describing the same job or course are merged field by field;
* the page list is renumbered 1..N across the whole selection (a page two chunks both read is kept once);
* totals that are derived from the work history are re-added across chunks, because every chunk only saw its
  own part of the history.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

# Added together across chunks.
_SUM = {"processing_time_ms", "total_processing_time_ms", "page_count"}
# The most cautious of the chunks.
_MIN = {"confidence", "average_confidence"}
# Differences in these between chunks are not a disagreement about the candidate.
_QUIET = {"request_id", "languages_used", "document_type", "mrz_source", "language"}
# Paths whose values are identifiers: a conflict is recorded without printing either value.
_SENSITIVE = re.compile(r"(aadhaar_number|vid|enrollment_id|mobile_number|passport_number|personal_number|raw_mrz)$")

# How the entries of a list are recognised as the same one.
_ENTRY_KEYS = {
    "experience": ("title", "company", "start"),
    "education": ("degree", "institution"),
    "projects": ("name",),
    "social": ("platform", "url"),
    "fields": ("label", "value"),
}
_EXPERIENCE_TOTALS = (
    ("total_experience_months", "total_experience_years", "total_experience_human"),
    ("indian_experience_months", "indian_experience_years", "indian_experience_human"),
    ("overseas_experience_months", "overseas_experience_years", "overseas_experience_human"),
)


def _blank(value: Any) -> bool:
    if value is None or value is False:
        return value is None
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, (str, list, dict)):
        return not (value.strip() if isinstance(value, str) else value)
    return False


def _fold(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return json.dumps(value, sort_keys=True, default=str).casefold()


def _shown(path: str, value: Any) -> str | None:
    if _SENSITIVE.search(path):
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= 120 else text[:117] + "..."


class _Merge:
    def __init__(self) -> None:
        self.conflicts: list[dict] = []
        self.notes: list[str] = []
        self.chunk = 0

    def conflict(self, path: str, kept: Any, other: Any) -> None:
        self.conflicts.append({"field": re.sub(r"\[\d+\]", "[]", path), "chunk": self.chunk,
                               "kept": _shown(path, kept), "other": _shown(path, other)})

    # ------------------------------------------------------------------ values

    def value(self, path: str, a: Any, b: Any, *, quiet: bool = False) -> Any:
        key = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _blank(b):
            return a
        if _blank(a):
            return b
        if isinstance(a, dict) and isinstance(b, dict):
            for name, item in b.items():
                a[name] = self.value(f"{path}.{name}" if path else name, a.get(name), item, quiet=quiet)
            return a
        if isinstance(a, list) and isinstance(b, list):
            return self.lists(path, a, b)
        if isinstance(a, bool) or isinstance(b, bool):
            if a is b:
                return a
            if key in ("all_check_digits_valid", "aadhaar_number_valid") or "check_digits" in path:
                return bool(a) and bool(b)  # one chunk's failed check cannot be hidden by another's pass
            if not quiet:
                self.conflict(path, a, b)
            return a
        if key in _SUM and isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a + b
        if key in _MIN and isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return min(a, b)
        if key == "document_side" and {a, b} == {"front", "back"}:
            return "both"
        if _fold(a) == _fold(b):
            return a
        if isinstance(a, str) and isinstance(b, str) and key in ("description", "duration_human"):
            return a if len(a) >= len(b) else b
        # Derived totals differ because each chunk only saw its own part of the work history; they are re-added later.
        if quiet or key in _QUIET or ("." not in path and key.endswith(("_months", "_years", "_human"))):
            return a
        self.conflict(path, a, b)
        return a

    # ------------------------------------------------------------------ lists

    def lists(self, path: str, a: list, b: list) -> list:
        name = path.rsplit(".", 1)[-1].split("[", 1)[0]
        fields = _ENTRY_KEYS.get(name)
        out = list(a)
        index: dict[str, int] = {}
        for position, item in enumerate(out):
            index.setdefault(self._identity(item, fields), position)
        for item in b:
            if _blank(item):
                continue
            identity = self._identity(item, fields)
            if identity not in index:
                index[identity] = len(out)
                out.append(item)
            elif isinstance(item, dict) and isinstance(out[index[identity]], dict):
                # The same job or course seen from two chunks: fill what the first copy lacks, keep the longer text.
                out[index[identity]] = self.value(f"{path}[{index[identity]}]", out[index[identity]], item, quiet=True)
        return out

    @staticmethod
    def _identity(item: Any, fields: tuple[str, ...] | None) -> str:
        if isinstance(item, dict) and fields:
            return "|".join(_fold(item.get(field)) for field in fields)
        return _fold(item)


def _experience_totals(merged: dict, parts: list[dict], notes: list[str]) -> None:
    entries = [e for e in merged.get("experience") or [] if isinstance(e, dict)]

    def months(entry: dict) -> int:
        value = entry.get("duration_months")
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0

    summed = {
        "total_experience_months": sum(months(e) for e in entries),
        "overseas_experience_months": sum(months(e) for e in entries if e.get("is_overseas") is True),
        "indian_experience_months": sum(months(e) for e in entries if e.get("is_overseas") is not True),
    }
    changed = False
    for names in _EXPERIENCE_TOTALS:
        key = names[0]
        values = [(p.get(key) or 0, i) for i, p in enumerate(parts)]
        best, source = max(values)
        total = max(best, summed[key])
        if total == best:
            for name in names:
                if parts[source].get(name) not in (None, ""):
                    merged[name] = parts[source][name]
        else:
            merged[key] = total
            merged[names[1]] = round(total / 12, 1)
            years, rest = divmod(total, 12)
            merged[names[2]] = " ".join(
                bit for bit in (f"{years} year{'s' if years != 1 else ''}" if years else "",
                                f"{rest} month{'s' if rest != 1 else ''}" if rest else "") if bit)
        if len({v for v, _ in values if v}) > 1 or total != best:
            changed = True
    if changed:
        notes.append("Experience totals were added up across the parts of the document. Check them against the work history.")


def merge_results(
    kind: str, chunks: list[tuple[list[int], dict]],
) -> tuple[dict, list[dict], list[str]]:
    """``chunks`` are (positions, payload): ``positions[i]`` is the 1-based place, within the pages sent for this
    kind, of the chunk's page ``i + 1``. Returns (merged payload, conflicts, notes)."""
    merge = _Merge()
    ordered = sorted(range(len(chunks)), key=lambda i: chunks[i][0][0] if chunks[i][0] else 0)
    pages: dict[int, dict] = {}
    base: dict | None = None
    for turn, i in enumerate(ordered):
        positions, payload = chunks[i]
        payload = deepcopy(payload)
        for page in payload.pop("pages", None) or []:
            number = page.get("page_number") if isinstance(page, dict) else None
            if type(number) is int and 1 <= number <= len(positions):
                page["page_number"] = positions[number - 1]
                pages.setdefault(positions[number - 1], page)
            elif isinstance(page, dict):
                # Out of range: kept so the coverage check can see the fault instead of it vanishing.
                pages.setdefault(10_000 + len(pages), page)
        merge.chunk = turn + 1
        base = payload if base is None else merge.value("", base, payload)
    merged = base or {}
    notes = merge.notes
    if kind == "resume" and len(chunks) > 1:
        # The first chunk's dict was merged into; each chunk's own totals come from the untouched input.
        own = [deepcopy(chunks[i][1]) for i in ordered]
        _experience_totals(merged, own, notes)
    merged["pages"] = [pages[key] for key in sorted(pages)]
    return merged, merge.conflicts, notes
