"""Accuracy gate for the page sorter: run the REAL splitter on real multi-document bundles and score every page against a
hand-made ground truth. Run it before a deploy (and after any change to the OCR, the classifiers or the splitter); it exits
non-zero when the sorting got worse than the agreed thresholds.

    python scripts/score_page_sorting.py                          # OCR every bundle (minutes), score, gate
    python scripts/score_page_sorting.py --saved                  # re-score from the cached page texts: no OCR, seconds
    python scripts/score_page_sorting.py "Case A" -v              # one case, list every page, not only the wrong ones
    python scripts/score_page_sorting.py --json report.json --max-seconds 280
    python scripts/score_page_sorting.py --import-page-maps M:\\crm\\candidates_organized --pdf-dir D:\\raw_bundles

A CASE is two files in the bundles folder (default $PAGE_SORT_BUNDLES or backend/data/sort_bundles, which is git-ignored):

    <case>.truth.txt      what every page of the bundle is (format below)
    <case>.pdf            the bundle itself, OR
    <case>.pdf.path       a text file holding the absolute path of the bundle, so a real file never has to be copied

TRUTH FILE FORMAT (one rule per line, '#' starts a comment, blank lines are ignored):

    pages 1-2: resume
    page 3: passport
    pages 4,6: education_certificate
    page 5: resume+experience_certificate     # '+': the page must be filed under BOTH kinds
    page 7: photo                             # photos are ignored by the score (they stay next to the passport by design)
    page 8: other                             # correct when no kind was found for it (or only 'other')
    page 9: visa                              # 'visa' is also right when the sorter filed it as visa_stamping
    min-score: 0.55                           # optional: this bundle's own floor instead of --min-bundle (a known weak case)

A page that is not listed is not scored. Kinds are the names the splitter files under: resume, passport, aadhaar,
education_certificate, experience_certificate, medical, signed_offer, visa, other, photo ...

Worker path. Production sorts a scan of SORT_RESUMABLE_MIN_PAGES (10) or more pages that needs OCR page by page in the worker
(services/page_sort.py: units of SORT_UNIT_PAGES pages, every page saved at once, passes of SORT_SLICE_SECONDS, a total cap of
SORT_TOTAL_SECONDS), not with the one-go split_by_content. The gate reads such bundles the same way by default (--worker-path forces
it, --splitter-path forces the one-go reading). A pass is cut after --chunk-seconds so one invocation fits a time limit: it exits
3 (INCOMPLETE) and the next `... --resume` continues from the saved pages; the invocation that finishes prints the score.

Scoring: a page is right when the piece(s) that contain it carry its true kind. The bundle score is right pages / scored pages;
the total is over all pages of all bundles. A bundle whose reading failed or was cut short, or that took longer than
--max-seconds, counts as failed whatever its pages scored. Exit code: 0 passed, 1 below a threshold, 2 could not run,
3 incomplete (worker path: run again with --resume).

This gate reads page texts and PDFs that belong to real candidates: they stay in the local, git-ignored folder (backend/data
is ignored) and the cache next to them. Never commit a bundle, a truth file or a cache.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLES = BACKEND_ROOT / "data" / "sort_bundles"
CACHE_DIRNAME = ".cache"
# Kinds that count as right for a truth kind besides itself.
EQUIVALENT = {"visa": {"visa", "visa_stamping"}}
IGNORED = {"photo"}

# The owner's page maps name the kind in the output file's name ("<Person>-Education certificates.pdf").
MAP_KIND = {"resume": "resume", "passport": "passport", "aadhaar": "aadhaar", "education certificates": "education_certificate",
            "experience certificates": "experience_certificate", "photo": "photo", "medical": "medical",
            "signed offer": "signed_offer", "other": "other"}
MAP_LINE = re.compile(r"^(?:(?P<src>.*?)\s+)?p(?P<n>\d+)\s*->\s*(?P<file>.+?\.(?:pdf|jpe?g|png|docx?))\b", re.I)
MAP_HEADER = re.compile(r"^Source(?: file\(s\)| file)?:\s*(?P<src>.+)$", re.I)
FLOOR_LINE = re.compile(r"^min-score\s*:\s*(?P<value>[\d.]+)$", re.I)
TRUTH_LINE = re.compile(r"^pages?\s+(?P<pages>[\d,\s-]+?)\s*:\s*(?P<kinds>[a-z_+\s]+?)\s*$", re.I)


# ------------------------------------------------------------------------------------------------ truth files

def parse_pages(spec: str) -> list[int]:
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        low, _, high = part.partition("-")
        first, last = int(low), int(high or low)
        if first < 1 or last < first:
            raise ValueError(f"bad page range {part!r}")
        pages += range(first, last + 1)
    return pages


def parse_truth(text: str, where: str = "truth file") -> dict[int, frozenset[str]]:
    """{page number: the kinds that page must be filed under}. Raises ValueError naming the line on a malformed rule."""
    truth: dict[int, frozenset[str]] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or FLOOR_LINE.match(line):
            continue
        match = TRUTH_LINE.match(line)
        if not match:
            raise ValueError(f"{where} line {number}: cannot read {raw.strip()!r} (expected e.g. 'pages 1-2: resume')")
        kinds = frozenset(k.strip().lower() for k in match.group("kinds").split("+") if k.strip())
        if not kinds:
            raise ValueError(f"{where} line {number}: no kind given")
        try:
            pages = parse_pages(match.group("pages"))
        except ValueError as error:
            raise ValueError(f"{where} line {number}: {error}") from None
        for page in pages:
            if page in truth:
                raise ValueError(f"{where} line {number}: page {page} is listed twice")
            truth[page] = kinds
    return truth


def format_truth(truth: dict[int, frozenset[str]]) -> str:
    """Consecutive pages with the same kinds become one 'pages a-b: kind' line."""
    lines, run = [], []
    for page in sorted(truth):
        if run and (page != run[-1] + 1 or truth[page] != truth[run[0]]):
            lines.append(_truth_line(run, truth[run[0]]))
            run = []
        run.append(page)
    if run:
        lines.append(_truth_line(run, truth[run[0]]))
    return "\n".join(lines) + "\n"


def _truth_line(run: list[int], kinds: frozenset[str]) -> str:
    span = f"page {run[0]}" if len(run) == 1 else f"pages {run[0]}-{run[-1]}"
    return f"{span}: {'+'.join(sorted(kinds))}"


# ------------------------------------------------------------------------------------------------ scoring

def score_pages(truth: dict[int, frozenset[str]], pieces: list[tuple[list[int], list[str]]]) -> list[dict]:
    """One row per scored page. `pieces` is [(pages, kinds)] as the splitter returned them."""
    predicted: dict[int, set[str]] = {}
    for pages, kinds in pieces:
        for page in pages:
            predicted.setdefault(page, set()).update(kinds)
    rows = []
    for page, wanted in sorted(truth.items()):
        if wanted <= IGNORED:
            continue
        got = predicted.get(page, set())
        ok = all(_kind_ok(kind, got) for kind in wanted - IGNORED)
        rows.append({"page": page, "truth": "+".join(sorted(wanted)), "pred": sorted(got), "ok": ok})
    return rows


def _kind_ok(kind: str, got: set[str]) -> bool:
    if kind == "other":
        return not got or got <= {"other"}
    return bool(got & EQUIVALENT.get(kind, {kind}))


# ------------------------------------------------------------------------------------------------ cases

@dataclass
class Case:
    name: str
    truth_path: Path
    pdf_path: Path | None
    problem: str | None = None
    truth: dict[int, frozenset[str]] = field(default_factory=dict)
    min_score: float | None = None      # a per-case floor ('min-score: 0.55' in the truth file) for a known weak bundle


def find_cases(folder: Path, wanted: list[str]) -> list[Case]:
    cases = []
    for truth_path in sorted(folder.glob("*.truth.txt")):
        name = truth_path.name[: -len(".truth.txt")]
        if wanted and not any(w.lower() in name.lower() for w in wanted):
            continue
        case = Case(name, truth_path, None)
        pdf, pointer = folder / f"{name}.pdf", folder / f"{name}.pdf.path"
        if pdf.is_file():
            case.pdf_path = pdf
        elif pointer.is_file():
            target = next((l.strip() for l in pointer.read_text(encoding="utf-8").splitlines() if l.strip()), "")
            case.pdf_path = Path(target) if target else None
            if case.pdf_path is None or not case.pdf_path.is_file():
                case.problem, case.pdf_path = f"the PDF named in {pointer.name} is missing: {target or '(empty)'}", None
        else:
            case.problem = f"no {name}.pdf or {name}.pdf.path next to the truth file"
        try:
            text = truth_path.read_text(encoding="utf-8")
            floor = next((FLOOR_LINE.match(l.split("#", 1)[0].strip()) for l in text.splitlines()
                          if FLOOR_LINE.match(l.split("#", 1)[0].strip())), None)
            case.min_score = float(floor.group("value")) if floor else None
            case.truth = parse_truth(text, truth_path.name)
        except ValueError as error:
            case.problem = case.problem or str(error)
        if not case.truth and not case.problem:
            case.problem = "the truth file lists no pages"
        cases.append(case)
    return cases


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------------------------------------ the splitter

def saved_of(inspected) -> dict:
    """The page texts of a reading in the cache format (what --saved re-scores)."""
    detail = {d["page"]: d for d in (inspected.completeness or {}).get("pages_detail", [])}
    return {"page_count": inspected.page_count,
            "pages": [{"page": p.page_number, "status": p.status, "method": p.method, "confidence": p.confidence,
                       "text": p.text or "", "seconds": detail.get(p.page_number, {}).get("seconds")}
                      for p in inspected.pages]}


class Splitter:
    """Loads the app lazily (so --help and --import-page-maps never need the OCR stack) and runs / re-runs the real code."""

    def __init__(self) -> None:
        sys.path.insert(0, str(BACKEND_ROOT))
        from app.integrations.ocr import inspection as inspection_module
        from app.services import bulk_candidate_import as bulk
        self.inspection_module, self.bulk = inspection_module, bulk
        self.captured: dict = {}
        self._real_inspect = inspection_module.inspect_document

        def capturing(*args, **kwargs):
            inspected = self._real_inspect(*args, **kwargs)
            self.captured["inspection"] = inspected
            return inspected
        inspection_module.inspect_document = capturing

    def tesseract(self) -> str | None:
        return self.inspection_module.find_tesseract()

    def run_live(self, pdf: Path) -> tuple[object, dict | None]:
        """The real split_by_content on the bundle; also returns the page texts it read, in the cache format."""
        self.captured.clear()
        result = self.bulk.split_by_content(pdf.read_bytes(), "application/pdf", [], pdf.name)
        inspected = self.captured.get("inspection")
        return result, (saved_of(inspected) if inspected is not None else None)

    def run_worker(self, pdf: Path, state: dict, chunk_seconds: float):
        """The production worker's page-by-page reading (`services/page_sort.sort_file`: page units, per-page saves, passes, total
        cap) on one bundle, without a database: the job's state is a plain dict the caller keeps between invocations.

        One call is ONE pass of at most `chunk_seconds` (new units stop being started after that; a unit already running
        finishes). Returns ("pending", pages_saved, page_count) when pages are left, or ("done", SplitResult, saved texts)."""
        from app.core.config import get_settings
        from app.services import page_sort
        limits = dataclasses.replace(page_sort.limits_from(get_settings()), slice_seconds=chunk_seconds)
        data = pdf.read_bytes()
        store = page_sort.MemoryStore(state, name="gate")
        try:
            inspected = page_sort.sort_file(data, state, store, limits, worker="gate")
        except page_sort.SortContinues:
            count = page_sort._page_count(data)
            saved_pages = sum(1 for n in range(1, count + 1) if page_sort.page_key(n) in state)
            return "pending", saved_pages, count
        result = self.bulk._split_inspected(inspected, data, [], pdf.name)     # what sort_plan does with the assembled reading
        return "done", result, saved_of(inspected)

    def worker_applies(self, pdf: Path) -> bool:
        """True for the files production sorts page by page: a PDF of at least SORT_RESUMABLE_MIN_PAGES pages needing OCR."""
        from app.core.config import get_settings
        from app.services import page_sort
        return page_sort.applicable(pdf.read_bytes(), page_sort.limits_from(get_settings()))

    def run_saved(self, saved: dict, name: str) -> object:
        """Classify and split the cached page texts with the CURRENT classifier code. No OCR, no PDF."""
        from app.integrations.ocr.classification import classify_pages
        from app.integrations.ocr.inspection import InspectedPage, Inspection
        pages = [InspectedPage(p["page"], p["status"], "other" if p["status"] == "blank" else "unknown", p["method"], p["text"],
                               p["confidence"]) for p in saved["pages"]]
        verdicts = classify_pages([p.text if p.status == "read" else "" for p in pages])
        for page, verdict in zip(pages, verdicts):
            if page.status == "read":
                page.document_type, page.also, page.inherited = verdict.document_type, list(verdict.also), verdict.inherited
                page.confidence = min(page.confidence, verdict.confidence)
                page.reason = verdict.reason
        inspected = Inspection(pages, len(pages), "pdf", [], b"", completeness={})
        real_cut = self.bulk._cut_pages
        self.bulk._cut_pages = lambda inspected_, pages_: b""      # the pieces' bytes are not needed to score the pages
        try:
            return self.bulk._split_pieces(inspected, b"", [], name)
        finally:
            self.bulk._cut_pages = real_cut


# ------------------------------------------------------------------------------------------------ the gate

def cache_path(folder: Path, name: str) -> Path:
    return folder / CACHE_DIRNAME / f"{name}.json"


def worker_state_path(folder: Path, name: str) -> Path:
    return folder / CACHE_DIRNAME / f"{name}.worker.json"


def evaluate(case: Case, args, splitter: Splitter | None, folder: Path, chunk_seconds: float = 180.0) -> dict:
    report = {"name": case.name, "min_score": case.min_score, "pages": 0, "correct": 0, "ratio": None, "seconds": None, "split": None, "pieces": None,
              "error": None, "wrong": [], "rows": [], "path": None}
    if case.problem:
        report["error"] = case.problem
        return report
    try:
        started = time.time()
        if args.saved:
            path = cache_path(folder, case.name)
            if not path.is_file():
                report["skipped"] = f"no cached page texts ({path.name}); run once without --saved to make them"
                return report
            saved = json.loads(path.read_text(encoding="utf-8"))
            if case.pdf_path and saved.get("pdf_sha256") and case.pdf_path.is_file() and saved["pdf_sha256"] != sha256_of(case.pdf_path):
                report["error"] = "the cached texts are of a different PDF than the case's; run once without --saved"
                return report
            result = splitter.run_saved(saved, case.name)
        else:
            worker = args.worker_path or (not args.splitter_path and splitter.worker_applies(case.pdf_path))
            report["path"] = "worker" if worker else "splitter"
            if worker:
                state_file = worker_state_path(folder, case.name)
                state: dict = {}
                if args.resume and state_file.is_file():
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                state_file.parent.mkdir(parents=True, exist_ok=True)
                outcome = splitter.run_worker(case.pdf_path, state, chunk_seconds)
                if outcome[0] == "pending":
                    # `state` now holds every page read so far (page_sort wrote it through its store): kept for --resume
                    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                    meta = state.get("sort") or {}
                    report["pending"] = (f"{outcome[1]}/{outcome[2]} pages read so far ({float(meta.get('spent') or 0):.0f}s of reading); "
                                         "run the same command again with --resume")
                    report["seconds"] = float(meta.get("spent") or 0)
                    return report
                _, result, saved = outcome
                state_file.unlink(missing_ok=True)
                report["seconds"] = round(float((state.get("sort") or {}).get("spent") or 0.0), 1)
                report["passes"] = int((state.get("sort") or {}).get("passes") or 1)
            else:
                result, saved = splitter.run_live(case.pdf_path)
                report["seconds"] = round(time.time() - started, 1)
            if saved is not None:
                saved.update({"case": case.name, "pdf_sha256": sha256_of(case.pdf_path), "seconds_total": report["seconds"]})
                target = cache_path(folder, case.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            if getattr(result, "failed", False):
                report["error"] = "the page reading failed; the scan was stored whole"
            elif getattr(result, "cut_short", False):
                report["error"] = "the page reading ran out of time; some pages were not read"
            elif args.max_seconds and report["seconds"] > args.max_seconds:
                report["error"] = f"took {report['seconds']}s, over the limit of {args.max_seconds}s"
        pieces = [(list(p.pages), list(p.kinds)) for p in result.pieces]
        if not result.split:
            # a scan that was not split is filed whole: every page carries the whole file's kinds
            count = max(case.truth) if case.truth else 0
            pieces = [(list(range(1, count + 1)), list(result.pieces[0].kinds))]
        rows = score_pages(case.truth, pieces)
        report.update(pages=len(rows), correct=sum(r["ok"] for r in rows), split=bool(result.split), pieces=len(result.pieces),
                      rows=rows, wrong=[r for r in rows if not r["ok"]])
        report["ratio"] = report["correct"] / report["pages"] if report["pages"] else None
    except Exception as error:      # one broken case must not hide the others
        report["error"] = f"{type(error).__name__}: {error}"
    return report


def print_table(reports: list[dict], verbose: bool) -> None:
    width = max([len(r["name"]) for r in reports] + [10])
    print(f"{'case':{width}}  {'correct':>9}  {'score':>6}  {'secs':>6}  note")
    for r in reports:
        score = "-" if r["ratio"] is None else f"{100 * r['ratio']:.1f}%"
        secs = "-" if r["seconds"] is None else f"{r['seconds']:.0f}"
        note = (r["error"] or (r.get("skipped") and "SKIPPED: " + r["skipped"]) or (r.get("pending") and "IN PROGRESS: " + r["pending"])
                or "")
        if r.get("path") and not note:
            note = f"({r['path']} path{', ' + str(r['passes']) + ' passes' if r.get('passes', 1) > 1 else ''})"
        print(f"{r['name']:{width}}  {r['correct']:>4}/{r['pages']:<4}  {score:>6}  {secs:>6}  {note}")
        for row in r["rows"]:
            if verbose or not row["ok"]:
                print(f"    {'   ' if row['ok'] else 'XX '}p{row['page']:02d}  truth={row['truth']:<24} filed={row['pred']}")


def summarise(reports: list[dict], min_total: float, min_bundle: float) -> dict:
    incomplete = [r["name"] for r in reports if r.get("pending")]
    if not any(r["pages"] for r in reports):
        return {"pages": 0, "correct": 0, "ratio": 0.0, "min_total": min_total, "min_bundle": min_bundle, "incomplete": incomplete,
                "failures": [] if incomplete else ["no bundle was scored"], "passed": False}
    pages = sum(r["pages"] for r in reports)
    correct = sum(r["correct"] for r in reports)
    ratio = correct / pages if pages else 0.0
    failures = []
    for r in reports:
        if r.get("skipped") or r.get("pending"):
            continue                # --saved and nothing cached for it: listed in the table, not scored
        if r["error"]:
            failures.append(f"{r['name']}: {r['error']}")
        elif r["ratio"] is not None and r["ratio"] < (r.get("min_score") or min_bundle):
            floor = r.get("min_score") or min_bundle
            failures.append(f"{r['name']}: {100 * r['ratio']:.1f}% is below the bundle minimum of {100 * floor:.0f}%")
    if ratio < min_total:
        failures.append(f"total {100 * ratio:.1f}% is below the minimum of {100 * min_total:.0f}%")
    return {"pages": pages, "correct": correct, "ratio": ratio, "min_total": min_total, "min_bundle": min_bundle,
            "failures": failures, "incomplete": incomplete, "passed": not failures and not incomplete}


# ------------------------------------------------------------------------------------------------ importing the owner's page maps

def clean_source(text: str) -> str:
    """'Santhosh.pdf (15 pages)' / '[New folder/x.pdf]' -> 'Santhosh' (the file's stem, any folder dropped)."""
    text = text.strip().strip("[]").strip()
    match = re.match(r"(.+?\.(?:pdf|docx?|jpe?g|png))", text, re.I)
    text = match.group(1) if match else re.split(r"\s+\(", text, maxsplit=1)[0]
    return re.sub(r"\s+", " ", Path(text.replace("\\", "/")).stem).strip()


def parse_page_map(text: str) -> dict[str | None, dict[int, str]]:
    """{source stem or None: {page: kind}} from one `_page-map.txt`. A line's own 'Source ... pN ->' prefix wins over the header.

    The map writes a passport-style photo or a dish photo as 'Other' and visas as 'Other' too; the first becomes `photo`
    (ignored by the score), the second `visa`, as the scratch scorer did, so the old numbers stay comparable."""
    header, maps = None, {}
    for raw in text.splitlines():
        line = raw.strip()
        head = MAP_HEADER.match(line)
        if head:
            header = clean_source(head.group("src"))
            continue
        match = MAP_LINE.match(line)
        if not match:
            continue
        stem = re.sub(r"\s+\d+$", "", os.path.splitext(match.group("file"))[0].rsplit("-", 1)[-1].strip().lower())
        if stem not in MAP_KIND:
            continue
        kind, rest = MAP_KIND[stem], line[match.end():]
        if kind == "other" and re.match(r"\s*(?:\[[^\]]*\]\s*)?\(\s*(?:a\s+)?photo", rest, re.I):
            kind = "photo"
        elif kind == "other" and re.search(r"visa", rest, re.I) and not re.search(r"visa form", rest, re.I):
            kind = "visa"
        source = clean_source(match.group("src")) if match.group("src") else header
        maps.setdefault(source or None, {})[int(match.group("n"))] = kind
    return maps


def index_pdfs(folders: list[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for folder in folders:
        for path in folder.rglob("*.pdf"):
            found.setdefault(re.sub(r"\s+", " ", path.stem).lower(), []).append(path)
    return found


def import_page_maps(root: Path, bundles: Path, pdf_dirs: list[Path], overwrite: bool, min_pages: int = 3) -> int:
    maps = sorted(set(root.glob("*/_page-map.txt")) | set(root.glob("*/*/_page-map.txt")))
    if not maps:
        print(f"no _page-map.txt files under {root}")
        return 2
    pdfs = index_pdfs(pdf_dirs)
    bundles.mkdir(parents=True, exist_ok=True)
    written = unmatched = 0
    for path in maps:
        person = path.parent.name
        by_source = parse_page_map(path.read_text(encoding="utf-8", errors="replace"))
        for source, kinds in sorted(by_source.items(), key=lambda item: str(item[0])):
            name = re.sub(r'[<>:"/\\|?*]', "_", person if len(by_source) == 1 else f"{person} - {source or 'unknown'}")
            truth_file = bundles / f"{name}.truth.txt"
            if len(kinds) < min_pages:
                print(f"skip   {name}: only {len(kinds)} mapped page(s) (a page map that mostly lists duplicates or other files)")
                continue
            if truth_file.exists() and not overwrite:
                print(f"keep   {name}: already there (use --overwrite to replace)")
                continue
            matches = pdfs.get(source.lower(), []) if source else []
            note = f"# imported from {path.name}; source file: {source or 'unknown'}\n"
            truth_file.write_text(note + format_truth({page: frozenset({kind}) for page, kind in kinds.items()}), encoding="utf-8")
            pointer = bundles / f"{name}.pdf.path"
            if matches:
                pointer.write_text(str(matches[0].resolve()) + "\n", encoding="utf-8")
                state = "pdf found" + (f" ({len(matches)} candidates, took the first)" if len(matches) > 1 else "")
            else:
                unmatched += 1
                state = f"PDF NOT FOUND for source {source!r}: add {name}.pdf.path by hand (or pass --pdf-dir)"
            written += 1
            print(f"wrote  {name}: {len(kinds)} pages; {state}")
    print(f"{written} case(s) written into {bundles}; {unmatched} without a PDF pointer")
    return 0


def import_saved_texts(folder: Path, bundles: Path) -> int:
    """Copy page-text files of the scratch format ({'person', 'pages': [...]}) into the cache of the case with that name."""
    done = 0
    for path in sorted(folder.glob("*.json")):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            name = saved.get("person") or path.stem
            if not saved.get("pages"):
                continue
        except (OSError, ValueError):
            continue
        if not (bundles / f"{name}.truth.txt").is_file():
            # a person whose map covers several files has one case per file, named '<person> - <file stem>'
            stem = re.sub(r"\s+", " ", Path(str(saved.get("source", "")).replace("\\", "/")).stem).lower()
            options = [t.name[: -len(".truth.txt")] for t in bundles.glob(f"{name} - *.truth.txt")]
            options = [o for o in options if stem and o.lower().endswith(" - " + stem)]
            if len(options) != 1:
                print(f"skip   {path.name}: no case named {name!r}")
                continue
            name = options[0]
        case_pdf = bundles / f"{name}.pdf.path"
        pdf = None
        if case_pdf.is_file():
            candidate = Path(case_pdf.read_text(encoding="utf-8").strip())
            pdf = candidate if candidate.is_file() else None
        elif (bundles / f"{name}.pdf").is_file():
            pdf = bundles / f"{name}.pdf"
        saved["case"] = name
        if pdf is not None:
            saved["pdf_sha256"] = sha256_of(pdf)
        target = cache_path(bundles, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        done += 1
        print(f"cached {name}: {len(saved['pages'])} pages" + ("" if pdf else " (no PDF to fingerprint)"))
    print(f"{done} cache file(s) written")
    return 0


# ------------------------------------------------------------------------------------------------ command line

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accuracy gate for the page sorter (see the top of this file).")
    parser.add_argument("cases", nargs="*", help="only the cases whose name contains one of these words")
    parser.add_argument("--bundles-dir", type=Path, default=Path(os.environ.get("PAGE_SORT_BUNDLES") or DEFAULT_BUNDLES),
                        help="folder of <case>.truth.txt + <case>.pdf[.path] (default $PAGE_SORT_BUNDLES or backend/data/sort_bundles)")
    parser.add_argument("--saved", action="store_true", help="score from the cached page texts: no OCR, no PDF needed")
    parser.add_argument("--min-total", type=float, default=0.90, help="lowest accepted share of right pages over all bundles (0.90)")
    parser.add_argument("--min-bundle", type=float, default=0.70, help="lowest accepted share of right pages in any one bundle (0.70)")
    parser.add_argument("--max-seconds", type=float, default=0, help="a bundle that takes longer fails (live runs only; 0 = no limit)")
    path = parser.add_mutually_exclusive_group()
    path.add_argument("--worker-path", action="store_true",
                      help="read like the production worker (services/page_sort: page units, saved per page, passes, total cap) "
                           "for every bundle. The default for PDFs of at least SORT_RESUMABLE_MIN_PAGES pages that need OCR")
    path.add_argument("--splitter-path", action="store_true",
                      help="read every bundle in one go with split_by_content (the 120 s whole-file reading), as short files are")
    parser.add_argument("--resume", action="store_true",
                        help="worker path: continue from the pages already read by an earlier invocation (otherwise it starts over)")
    parser.add_argument("--chunk-seconds", type=float, default=180,
                        help="worker path: one invocation starts no new page unit after this many seconds (default 180; units "
                             "already running finish, so keep it well under the time you can run for)")
    parser.add_argument("--budget-seconds", type=float, default=0,
                        help="worker path: total seconds this invocation may spend over all cases; later cases wait for the next "
                             "--resume invocation (0 = no budget: every case gets --chunk-seconds)")
    parser.add_argument("--json", type=Path, dest="json_out", help="also write the report to this JSON file")
    parser.add_argument("-v", "--verbose", action="store_true", help="list every scored page, not only the wrong ones")
    parser.add_argument("--import-page-maps", type=Path, metavar="ROOT",
                        help="one-time: write truth files and PDF pointer files from the _page-map.txt files under ROOT")
    parser.add_argument("--pdf-dir", type=Path, action="append", default=[],
                        help="with --import-page-maps: a folder searched (recursively) for the source PDFs named in the maps; repeatable")
    parser.add_argument("--min-pages", type=int, default=3,
                        help="with --import-page-maps: skip maps that give fewer than this many pages for a source (3)")
    parser.add_argument("--overwrite", action="store_true", help="with --import-page-maps: replace truth files that exist")
    parser.add_argument("--import-saved-texts", type=Path, metavar="DIR",
                        help="one-time: load page-text json files of the scratch format (split_texts/<person>.json) into the cache")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.import_page_maps:
        return import_page_maps(args.import_page_maps, args.bundles_dir, args.pdf_dir, args.overwrite, args.min_pages)
    if args.import_saved_texts:
        return import_saved_texts(args.import_saved_texts, args.bundles_dir)

    if not args.bundles_dir.is_dir():
        print(f"bundles folder not found: {args.bundles_dir} (see docs/sorting-gate.md; --import-page-maps creates it)")
        return 2
    cases = find_cases(args.bundles_dir, args.cases)
    if not cases:
        print(f"no <case>.truth.txt in {args.bundles_dir}" + (f" matching {args.cases}" if args.cases else ""))
        return 2

    splitter = None
    try:
        splitter = Splitter()
    except Exception as error:
        print(f"cannot load the app code: {type(error).__name__}: {error}")
        return 2
    if not args.saved and splitter.tesseract() is None:
        print("Tesseract is not installed: a live run cannot read scans (use --saved for the cached texts)")
        return 2

    reports = []
    began = time.time()
    for case in cases:
        remaining = args.budget_seconds - (time.time() - began) if args.budget_seconds else None
        if remaining is not None and remaining < 30 and not args.saved:
            reports.append({"name": case.name, "min_score": case.min_score, "pages": 0, "correct": 0, "ratio": None, "seconds": None,
                            "error": None, "wrong": [], "rows": [], "path": None,
                            "pending": "not started: this invocation's --budget-seconds is used up; run again with --resume"})
            continue
        print(f"... {case.name}", file=sys.stderr, flush=True)
        chunk = args.chunk_seconds if remaining is None else max(10.0, min(args.chunk_seconds, remaining - 60))
        reports.append(evaluate(case, args, splitter, args.bundles_dir, chunk))
    print_table(reports, args.verbose)
    summary = summarise(reports, args.min_total, args.min_bundle)
    print(f"\nTOTAL correct {summary['correct']}/{summary['pages']} = {100 * summary['ratio']:.1f}%   "
          f"(minimum {100 * args.min_total:.0f}% total, {100 * args.min_bundle:.0f}% per bundle)")
    for failure in summary["failures"]:
        print(f"FAIL  {failure}")
    if summary["incomplete"]:
        print(f"INCOMPLETE  {', '.join(summary['incomplete'])}: not all pages read yet; run the same command again with --resume "
              "(the score above covers only the finished bundles)")
    else:
        print("GATE PASSED" if summary["passed"] else "GATE FAILED")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        report = {"mode": "saved" if args.saved else "live", "bundles_dir": str(args.bundles_dir), **summary,
                  "bundles": [{k: v for k, v in r.items() if k != "rows"} for r in reports]}
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if summary["incomplete"]:
        return 3
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
