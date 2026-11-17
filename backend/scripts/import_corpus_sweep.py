"""Sweep the bulk-import pipeline over the owner's hand-organised library: a free answer key for MESSY uploads.

READ-ONLY and DATABASE-FREE. It never writes into the library, never touches backend/.env or any database (it points
`CRM_ENV_FILE` at a generated scratch file with a dummy, never-contacted DATABASE_URL), and keeps everything it makes in `--out`.

The library layout is  <library>/<Full Name>/<Full Name>-<Document>.<ext>  (folders starting with "_" are helpers and skipped).
The folder name is the person, the part of the file name after "<Full Name>-" is the document kind (classified with
`bulk_candidate_import.classify_path_kinds`), so every file has a known (person, kind) truth.

MESSY scenarios are built the way staff upload (copies in --out/scenarios, seeded and deterministic):

    a  neutral names     one person's files renamed scan01.pdf / IMG_0042.jpg / document (3).pdf / CamScanner 12-10-2026.pdf
    b  mixed             two or three people in ONE folder with neutral names (the hard grouping case); b-sim = similar names
    c  one big file      the single-document PDFs of ONE person concatenated into one PDF (page -> kind truth kept);
                         c2 also appends a photo page, a white blank page and a noisy "scanner" blank page
    d  partially named   the people's first names kept, the document word dropped ("Ashwin-2.pdf"); d-same = two people
                         who share a first name (the names cannot tell them apart)

and run through the same code the app runs, without a database, in the same order as the Import screen:

    candidate_import._units  ->  (reading offered? same rule as the frontend's needsPersonReading)
    -> people_by_content.identify_people (LocalReader, workers=1)  -> candidate_import._units with the people found
    -> per candidate, the import_one sequence: classify_path_kinds by name, cut_pdf_pages for a person's own pages,
       split_by_content / wants_split for a file whose name says nothing or several things, classify_content otherwise.

Every page is then judged against the truth: person (placed ok / wrong person flagged / wrong person SILENT / unplaced /
unreadable / lost) and kind (ok / ok with an extra field / wrong flagged / wrong SILENT / needs-you "other").
"Silent" = filed with no warning and wrong (the dangerous ones); "needs you" = left for a human (acceptable).

Usage (from M:\\crm\\backend; always with -I):

    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py list   --out OUT            # what the library holds, suggested picks
    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py sweep  --out OUT --pick auto:8
    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py sweep  --out OUT --pick "Full Name 1,Full Name 2" --types a,b,c,d
    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py build  --out OUT --pick auto:2        # scenarios only
    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py run    --out OUT                      # run built scenarios (resumable)
    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py report --out OUT                      # report.md + report.json
    .venv/Scripts/python.exe -I scripts/import_corpus_sweep.py peek   --out OUT --scn a-C01 --file "scan01.pdf" --page 3   # document-word flags of one page (truth-noise audit)

Options: --library (default M:/crm/candidates_organized), --seed, --types, --budget-min (stop starting scenarios after this
many minutes), --min-free-mb (pause/stop when the machine's free memory is below it), --smoke (the harness check: 2 people, a+b only),
--max-files / --max-pages (per person), --fresh (rebuild scenarios and forget results).

Privacy: people are labelled C01.. in everything the script writes except `mapping.json` (label -> real folder, local only).
Reports and logs hold no names, identity numbers or OCR text: only labels, kinds, counts and OCR-quality figures.
Settings: one OCR page worker, no network. Results are per scenario in OUT/results/<id>.json, so a stopped sweep resumes.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT_LIBRARY = Path("M:/crm/candidates_organized")
DOC_EXT = {".pdf", ".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".3gp", ".webm", ".m4v"}
KIND_PRIORITY = ["passport", "aadhaar", "resume", "signed_offer", "education_certificate", "experience_certificate", "medical", "pcc",
                 "visa", "visa_stamping", "emigration", "air_ticket", "insurance", "photo", "advance_receipt", "other"]
MAX_FILE_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------------------------- environment (before app imports)

def prepare_env(out: Path) -> None:
    """Point the settings at a scratch env file: dummy database that is never contacted, one OCR page worker, no online reader."""
    out.mkdir(parents=True, exist_ok=True)
    env = out / "_sweep.env"
    env.write_text("DATABASE_URL=postgresql+psycopg://sweep:sweep@127.0.0.1:1/never_contacted\nOCR_PAGE_WORKERS=1\n", encoding="utf-8")
    os.environ["CRM_ENV_FILE"] = str(env)
    os.environ["OCR_PAGE_WORKERS"] = "1"
    for key in ("VERIS_OCR_BASE_URL", "VERIS_OCR_API_KEY"):
        os.environ.pop(key, None)
    try:   # be kind to the other work on this machine
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL
    except Exception:
        pass


def free_mb() -> int:
    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        stat = MEM()
        stat.dwLength = ctypes.sizeof(MEM)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullAvailPhys // (1024 * 1024))
    except Exception:
        return 10_000


class Log:
    def __init__(self, path: Path):
        self.path = path

    def __call__(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


# --------------------------------------------------------------------------------------------- the library (truth)

def natural_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def doc_part(folder: str, file_stem: str) -> str:
    prefix = folder + "-"
    if file_stem.lower().startswith(prefix.lower()):
        return file_stem[len(prefix):]
    return file_stem.split("-", 1)[1] if "-" in file_stem else file_stem


def probe_pdf(path: Path) -> tuple[int, bool, str]:
    """(page count, mostly embedded text, script of that text) without any OCR."""
    import pymupdf as fitz
    try:
        with fitz.open(path) as doc:
            if doc.needs_pass:
                return 0, False, "locked"
            count, with_text, tamil = doc.page_count, 0, False
            for index in range(min(count, 6)):
                text = doc[index].get_text() or ""
                if len(text.strip()) >= 40:
                    with_text += 1
                if re.search("[\u0B80-\u0BFF]", text):
                    tamil = True
            return count, with_text >= max(1, min(count, 6) // 2), "tamil" if tamil else "latin"
    except Exception:
        return 0, False, "unreadable"


def load_library(root: Path) -> dict[str, list[dict]]:
    """{person folder: [file records]}: only document files, '_*' helper folders and non-folders skipped, videos ignored."""
    from app.services import bulk_candidate_import as bulk
    library: dict[str, list[dict]] = {}
    for folder in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        children = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
        files = [child for child in children if child.is_file()]
        if not files and any(child.is_dir() for child in children):
            continue                                        # a container of folders ("Current candidates"), not a person
        records = []
        for path in files:
            ext = path.suffix.lower()
            if ext in VIDEO_EXT or ext not in DOC_EXT:
                continue
            size = path.stat().st_size
            part = doc_part(folder.name, path.stem)
            kinds = bulk.classify_path_kinds(part + ext)
            if ext == ".pdf":
                pages, native, script = probe_pdf(path)
            else:
                pages, native, script = 1, False, "image"
            records.append({"path": str(path), "ext": ext, "size": size, "kinds": kinds or ["other"], "known": bool(kinds), "pages": pages,
                            "native": native, "script": script})
        if records:
            library[folder.name] = records
    return library


def usable(record: dict) -> bool:
    return record["known"] and record["pages"] >= 1 and record["size"] <= MAX_FILE_BYTES and "other" not in record["kinds"]


def library_features(files: list[dict]) -> list[float]:
    good = [f for f in files if usable(f)]
    pages = sum(f["pages"] for f in good)
    return [len(good) / 4.0, pages / 12.0, sum(1 for f in good if f["native"]) / max(1, len(good)) * 3,
            sum(1 for f in good if f["ext"] != ".pdf") / 2.0, sum(1 for f in good if len(f["kinds"]) > 1) / 2.0,
            len({k for f in good for k in f["kinds"]}) / 3.0, 1.5 if any(f["script"] == "tamil" for f in good) else 0.0]


def eligible(files: list[dict], min_files: int = 4, max_pages: int = 70) -> bool:
    good = [f for f in files if usable(f)]
    kinds = {k for f in good for k in f["kinds"]}
    return len(good) >= min_files and sum(f["pages"] for f in good) <= max_pages and bool(kinds & {"passport", "aadhaar", "resume"})


def first_word(folder: str) -> str:
    words = [w for w in re.split(r"[\s.]+", folder) if len(w) >= 3]
    return words[0].lower() if words else folder.lower()


def auto_pick(library: dict[str, list[dict]], count: int, seed: int) -> list[str]:
    """Diverse picks (farthest point on document count / pages / digital-vs-scanned / images / multi-kind files / Tamil text),
    always including one pair of people who share a first name (the hard case for names) when the library has one."""
    rng = random.Random(seed)
    names = sorted(n for n, f in library.items() if eligible(f))
    feats = {n: library_features(library[n]) for n in names}
    picked: list[str] = []
    by_first: dict[str, list[str]] = defaultdict(list)
    for n in names:
        by_first[first_word(n)].append(n)
    pairs = [group for group in by_first.values() if len(group) >= 2]
    if pairs and count >= 4:
        picked += rng.choice(pairs)[:2]
    if not picked:
        picked.append(rng.choice(names))
    while len(picked) < min(count, len(names)):
        best = max((n for n in names if n not in picked),
                   key=lambda n: (min(sum((a - b) ** 2 for a, b in zip(feats[n], feats[p])) for p in picked), rng.random()))
        picked.append(best)
    return picked


# --------------------------------------------------------------------------------------------- building scenarios

NEUTRAL_PDF = ["scan{n:02d}.pdf", "document ({n}).pdf", "CamScanner {d:02d}-10-2026.pdf", "New Doc {n}.pdf", "Scan_{n}.pdf", "document.pdf"]
NEUTRAL_IMG = ["IMG_{r:04d}{ext}", "WhatsApp Image 2026-10-{d:02d} at 10.{r2:02d}.07{ext}", "PXL_202610{d:02d}_{r:04d}{ext}"]


def neutral_names(files: list[dict], rng: random.Random, taken: set[str]) -> list[str]:
    names, counter = [], 0
    for rec in files:
        for _ in range(50):
            counter += 1
            if rec["ext"] == ".pdf":
                style = NEUTRAL_PDF[(counter + rng.randrange(len(NEUTRAL_PDF))) % len(NEUTRAL_PDF)]
                name = style.format(n=rng.randrange(1, 40), d=rng.randrange(1, 28))
            else:
                style = rng.choice(NEUTRAL_IMG)
                name = style.format(r=rng.randrange(10, 9999), r2=rng.randrange(10, 59), d=rng.randrange(1, 28), ext=rec["ext"])
            if name.lower() not in taken:
                taken.add(name.lower())
                names.append(name)
                break
        else:
            raise RuntimeError("no free neutral name")
    return names


def partial_names(first: str, files: list[dict], rng: random.Random, taken: set[str]) -> list[str]:
    """The first name only, no document word: 'Ashwin-1.pdf', 'Ashwin (2).pdf', 'Ashwin.jpg'."""
    names = []
    for i, rec in enumerate(files):
        for _ in range(50):
            style = ["{f}-{n}{e}", "{f} ({n}){e}", "{f}_{n}{e}", "{f}{e}"][rng.randrange(4)]
            name = style.format(f=first.capitalize(), n=rng.randrange(1, 30), e=rec["ext"])
            if name.lower() not in taken:
                taken.add(name.lower())
                names.append(name)
                break
        else:
            raise RuntimeError("no free partial name")
    return names


def pick_files(files: list[dict], max_files: int, max_pages: int, rng: random.Random, *, singles_only: bool = False,
               must_identity: bool = True) -> list[dict]:
    """The person's usable files in kind-priority order within the file and page caps (identity documents first)."""
    good = [f for f in files if usable(f) and (not singles_only or (len(f["kinds"]) == 1 and f["ext"] == ".pdf"))]

    def rank(rec):
        return min(KIND_PRIORITY.index(k) if k in KIND_PRIORITY else 99 for k in rec["kinds"])

    good.sort(key=lambda r: (rank(r), r["pages"], natural_key(r["path"])))
    chosen, pages, seen_kinds, seen_sha = [], 0, Counter(), set()
    for rec in good:
        key = tuple(rec["kinds"])
        if len(chosen) >= max_files or pages + rec["pages"] > max_pages or seen_kinds[key] >= 2 or sha_of(rec["path"]) in seen_sha:
            continue
        seen_sha.add(sha_of(rec["path"]))
        chosen.append(rec)
        pages += rec["pages"]
        seen_kinds[key] += 1
    return chosen


def sha_of(path: str) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def as_pdf_pages(path: Path):
    """The file as an open pymupdf PDF (an image becomes a one-page PDF)."""
    import pymupdf as fitz
    if path.suffix.lower() == ".pdf":
        return fitz.open(path)
    with fitz.open(path) as image:
        return fitz.open("pdf", image.convert_to_pdf())


def noisy_blank_png(rng: random.Random) -> bytes:
    """The back of a scanned sheet: paper-white with light speckle and a faint edge shadow, no text."""
    import io
    from PIL import Image
    width, height = 1240, 1754
    image = Image.new("L", (width, height), 244)
    px = image.load()
    for _ in range(9000):
        x, y = rng.randrange(width), rng.randrange(height)
        px[x, y] = rng.randrange(150, 235)
    for x in range(0, 26):
        for y in range(height):
            px[x, y] = max(190, 244 - (26 - x) * 2)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


_HASHES: dict[str, list[str]] = {}


def page_hashes(path: str) -> list[str]:
    """A fingerprint of every page's look (tiny grey render): the same scanned page held in two library files gets the same one."""
    if path in _HASHES:
        return _HASHES[path]
    import pymupdf as fitz
    result: list[str] = []
    try:
        with as_pdf_pages(Path(path)) as doc:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), colorspace=fitz.csGRAY, alpha=False)
                result.append(hashlib.md5(pix.samples).hexdigest())
    except Exception:
        result = []
    _HASHES[path] = result
    return result


def label_files(label: str, files: list[dict]) -> dict[str, str]:
    return {f["path"]: f"{label}.f{i + 1:02d}" for i, f in enumerate(files)}


class Builder:
    def __init__(self, library, mapping, out: Path, seed: int, args):
        self.library, self.mapping, self.out, self.seed, self.args = library, mapping, out, seed, args
        self.scn_dir = out / "scenarios"
        self.specs: list[dict] = []
        self._kind_of_hash: dict[str, dict] = {}

    def _folder(self, sid: str) -> Path:
        folder = self.scn_dir / sid
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        return folder

    def truth_kinds(self, label: str, rec: dict, index: int) -> list[str]:
        """The owner's kind(s) of this file, plus the kinds of any other file of the same person that holds the identical page (a
        'Resume' packet that also holds the education certificate the owner filed separately): the truth is every kind the page is."""
        if label not in self._kind_of_hash:
            table: dict[str, set] = defaultdict(set)
            for other in self.library_pick(label):
                if usable(other):
                    for h in page_hashes(other["path"]):
                        table[h].update(k for k in other["kinds"] if k != "other")
            self._kind_of_hash[label] = table
        hashes = page_hashes(rec["path"])
        kinds = set(rec["kinds"])
        if index < len(hashes):
            kinds |= self._kind_of_hash[label].get(hashes[index], set())
        return sorted(kinds)

    def _truth_pages(self, rec: dict, name: str, label: str, flabel: str) -> list[dict]:
        return [{"file": name, "page": n, "person": label, "kinds": self.truth_kinds(label, rec, n - 1), "src": flabel} for n in range(1, rec["pages"] + 1)]

    def person_files(self, label: str, max_files: int, max_pages: int, singles_only=False) -> list[dict]:
        return pick_files(self.library[self.mapping[label]], max_files, max_pages, random.Random(self.seed), singles_only=singles_only)

    # a ---------------------------------------------------------------------------------------------------
    def scenario_a(self, label: str):
        rng = random.Random(f"{self.seed}-a-{label}")
        files = self.person_files(label, self.args.max_files, self.args.max_pages)
        if len(files) < 2:
            return
        return self._neutral(f"a-{label}", "a", [(label, files)], rng)

    # b ---------------------------------------------------------------------------------------------------
    def scenario_b(self, labels: list[str], tag: str = "b"):
        rng = random.Random(f"{self.seed}-b-{'-'.join(labels)}")
        groups = [(label, self.person_files(label, max(3, self.args.max_files * 2 // 3), max(8, self.args.max_pages * 2 // 3))) for label in labels]
        if any(len(files) < 2 for _, files in groups):
            return
        # a file two people hold identically (a group air ticket) is one file: truth would be ambiguous, so it is left out
        seen = Counter(sha_of(f["path"]) for _, files in groups for f in files)
        groups = [(label, [f for f in files if seen[sha_of(f["path"])] == 1]) for label, files in groups]
        return self._neutral(f"{tag}-{'+'.join(labels)}", tag, groups, rng)

    def _neutral(self, sid: str, kind: str, groups: list[tuple[str, list[dict]]], rng: random.Random):
        folder = self._folder(sid)
        all_files = [(label, rec) for label, files in groups for rec in files]
        rng.shuffle(all_files)
        taken: set[str] = set()
        names = neutral_names([rec for _, rec in all_files], rng, taken)
        spec = {"id": sid, "type": kind, "folder": "New folder", "persons": [label for label, _ in groups], "files": [], "pages": [], "notes": {}}
        for (label, rec), name in zip(all_files, names):
            flabel = label_files(label, [r for r in self.library_pick(label)])[rec["path"]]
            shutil.copyfile(rec["path"], folder / name)
            spec["files"].append({"name": name, "person": label, "src": flabel, "kinds": rec["kinds"], "pages": rec["pages"], "ext": rec["ext"],
                                  "native": rec["native"], "script": rec["script"]})
            spec["pages"] += self._truth_pages(rec, name, label, flabel)
        self._finish(spec, folder)
        return spec

    def library_pick(self, label: str) -> list[dict]:
        return self.library[self.mapping[label]]

    # d ---------------------------------------------------------------------------------------------------
    def scenario_d(self, labels: list[str], tag: str = "d"):
        rng = random.Random(f"{self.seed}-d-{'-'.join(labels)}")
        sid = f"{tag}-{'+'.join(labels)}"
        groups = [(label, self.person_files(label, max(3, self.args.max_files // 2), max(8, self.args.max_pages // 2))) for label in labels]
        if any(len(files) < 2 for _, files in groups):
            return
        seen = Counter(sha_of(f["path"]) for _, files in groups for f in files)
        groups = [(label, [f for f in files if seen[sha_of(f["path"])] == 1]) for label, files in groups]
        folder = self._folder(sid)
        taken: set[str] = set()
        spec = {"id": sid, "type": tag, "folder": "New folder", "persons": labels, "files": [], "pages": [], "notes": {}}
        for label, files in groups:
            first = first_word(self.mapping[label])
            for rec, name in zip(files, partial_names(first, files, rng, taken)):
                flabel = label_files(label, self.library_pick(label))[rec["path"]]
                shutil.copyfile(rec["path"], folder / name)
                spec["files"].append({"name": name, "person": label, "src": flabel, "kinds": rec["kinds"], "pages": rec["pages"], "ext": rec["ext"],
                                      "native": rec["native"], "script": rec["script"]})
                spec["pages"] += self._truth_pages(rec, name, label, flabel)
        self._finish(spec, folder)
        return spec

    # c ---------------------------------------------------------------------------------------------------
    def scenario_c(self, label: str, extras: bool):
        import pymupdf as fitz
        rng = random.Random(f"{self.seed}-c-{label}-{extras}")
        files = self.person_files(label, 6, 12, singles_only=True)
        if len(files) < 3:
            return
        sid = f"{'c2' if extras else 'c1'}-{label}"
        folder = self._folder(sid)
        order = files[:]
        rng.shuffle(order)
        flabels = label_files(label, self.library_pick(label))
        name = "CamScanner 12-10-2026.pdf" if rng.random() < 0.5 else "document (3).pdf"
        spec = {"id": sid, "type": "c2" if extras else "c1", "folder": "New folder", "persons": [label], "files": [], "pages": [], "notes": {}}
        photos = [f for f in self.library_pick(label) if f["kinds"] == ["photo"] and f["size"] < 3_000_000]
        merged = fitz.open()
        number = 0
        try:
            for rec in order:
                with as_pdf_pages(Path(rec["path"])) as part:
                    merged.insert_pdf(part)
                for offset in range(rec["pages"]):
                    number += 1
                    spec["pages"].append({"file": name, "page": number, "person": label, "kinds": self.truth_kinds(label, rec, offset), "src": flabels[rec["path"]]})
                if extras and rng.random() < 0.3 and number > 2 and not spec["notes"].get("noisy"):
                    png = noisy_blank_png(rng)
                    with fitz.open("pdf", fitz.open(stream=png, filetype="jpeg").convert_to_pdf()) as blank:
                        merged.insert_pdf(blank)
                    number += 1
                    spec["notes"]["noisy"] = number
                    spec["pages"].append({"file": name, "page": number, "person": label, "kinds": ["blank"], "src": f"{label}.noisy-blank"})
            if extras:
                if photos:
                    with as_pdf_pages(Path(photos[0]["path"])) as part:
                        merged.insert_pdf(part)
                    number += 1
                    spec["pages"].append({"file": name, "page": number, "person": label, "kinds": ["photo"], "src": flabels[photos[0]["path"]]})
                merged.new_page(width=595, height=842)
                number += 1
                spec["pages"].append({"file": name, "page": number, "person": label, "kinds": ["blank"], "src": f"{label}.white-blank"})
                if not spec["notes"].get("noisy"):
                    png = noisy_blank_png(rng)
                    with fitz.open("pdf", fitz.open(stream=png, filetype="jpeg").convert_to_pdf()) as blank:
                        merged.insert_pdf(blank)
                    number += 1
                    spec["notes"]["noisy"] = number
                    spec["pages"].append({"file": name, "page": number, "person": label, "kinds": ["blank"], "src": f"{label}.noisy-blank"})
            merged.save(folder / name, garbage=3, deflate=True)
        finally:
            merged.close()
        spec["files"].append({"name": name, "person": label, "src": f"{label}.merged", "kinds": sorted({k for r in order for k in r["kinds"]}), "pages": number,
                              "ext": ".pdf", "native": all(r["native"] for r in order), "script": "mixed"})
        self._finish(spec, folder)
        return spec

    def _finish(self, spec: dict, folder: Path) -> None:
        (folder.parent / f"{spec['id']}.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        self.specs.append(spec)


def build(args, log: Log, out: Path) -> list[dict]:
    library = load_library(Path(args.library))
    log(f"library: {len(library)} people, {sum(len(v) for v in library.values())} document files")
    picks = auto_pick(library, int(args.pick.split(":")[1]), args.seed) if args.pick.startswith("auto") else [p.strip() for p in args.pick.split(",") if p.strip()]
    missing = [p for p in picks if p not in library]
    if missing:
        raise SystemExit(f"{len(missing)} picked folder(s) not in the library")
    labels = {f"C{i + 1:02d}": name for i, name in enumerate(picks)}
    mapping = out / "mapping.json"
    mapping.write_text(json.dumps({"labels": labels, "note": "PRIVATE: label -> real folder; never paste into reports"}, indent=1, ensure_ascii=False), encoding="utf-8")
    types = set(args.types.split(","))
    b = Builder(library, labels, out, args.seed, args)
    rng = random.Random(args.seed)
    names = list(labels)
    pair_first = {}
    for label, real in labels.items():
        pair_first.setdefault(first_word(real), []).append(label)
    same_first = next((g for g in pair_first.values() if len(g) >= 2), None)
    for label in names:
        if "a" in types:
            b.scenario_a(label)
    shuffled = names[:]
    rng.shuffle(shuffled)
    groups, rest = [], shuffled[:]          # pairs, with one trio when the count is odd
    while rest:
        take = 3 if len(rest) == 3 else 2
        groups.append(rest[:take])
        rest = rest[take:]
    if "b" in types:
        for group in groups:
            if len(group) >= 2:
                b.scenario_b(group, "b")
        if same_first:
            b.scenario_b(same_first[:2], "b-sim")      # two people whose names start alike, mixed with neutral names
    if "c" in types:
        for label in names:
            b.scenario_c(label, extras=False)
        for label in names[:3]:
            b.scenario_c(label, extras=True)
    if "d" in types:
        for group in groups:
            if len(group) >= 2 and len({first_word(labels[l]) for l in group[:2]}) == 2:
                b.scenario_d(group[:2], "d")
        if same_first:
            b.scenario_d(same_first[:2], "d-same")     # two people with the same first name: the names cannot tell them apart
    log(f"built {len(b.specs)} scenarios: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(s['type'] for s in b.specs).items())))
    (out / "features.json").write_text(json.dumps({label: {"files": sum(1 for f in library[real] if usable(f)), "pages": sum(f["pages"] for f in library[real] if usable(f)),
                                                          "kinds": sorted({k for f in library[real] if usable(f) for k in f["kinds"]}),
                                                          "native_pdfs": sum(1 for f in library[real] if usable(f) and f["native"]),
                                                          "images": sum(1 for f in library[real] if usable(f) and f["ext"] != ".pdf"),
                                                          "tamil_native_text": any(f["script"] == "tamil" for f in library[real])}
                                                   for label, real in labels.items()}, indent=1), encoding="utf-8")
    return b.specs


# --------------------------------------------------------------------------------------------- reading hook (stats + shared cache)

def script_of(text: str) -> str:
    tamil = sum(1 for c in text if "\u0B80" <= c <= "\u0BFF")
    deva = sum(1 for c in text if "\u0900" <= c <= "\u097F")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    other = sum(1 for c in text if not c.isascii() and c.isalpha()) - tamil - deva
    total = tamil + deva + latin + max(other, 0)
    if total < 15:
        return "none"
    if tamil / total >= 0.1:
        return "tamil"
    if deva / total >= 0.1:
        return "devanagari"
    if max(other, 0) / total >= 0.15:
        return "other"
    return "latin"


class OcrHook:
    """Wraps `inspect_document`: keeps per-page OCR-quality figures (never the text) and shares identical reads between scenarios
    (the same bytes with the same settings give the same inspection, so a file read for scenario a is not read again for b)."""

    def __init__(self, cap_bytes: int = 220 * 1024 * 1024):
        self.cache: OrderedDict = OrderedDict()
        self.cap, self.held = cap_bytes, 0
        self.stats: dict[str, dict[int, dict]] = {}
        self.cold_seconds = 0.0
        self.cold_pages = 0
        self.cold_calls = 0
        self.hits = 0
        self.original = None

    def install(self):
        import app.integrations.ocr.inspection as insp
        self.original = insp.inspect_document
        insp.inspect_document = self.call

    def clear(self):
        self.cache.clear()
        self.held = 0

    def call(self, data, **kwargs):
        from app.integrations.ocr.inspection import _real_words
        key = (hashlib.sha256(data).hexdigest(), kwargs.get("max_pages"), kwargs.get("page_timeout"), kwargs.get("languages"),
               kwargs.get("two_pass", True), kwargs.get("need_more") is None, kwargs.get("page_workers"))
        if key in self.cache:
            self.hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        started = time.perf_counter()
        inspected = self.original(data, **kwargs)
        self.cold_seconds += time.perf_counter() - started
        self.cold_pages += len(inspected.pages)
        self.cold_calls += 1
        detail = {row.get("page"): row.get("confidence") for row in (inspected.completeness or {}).get("pages_detail", [])}
        per_page = {}
        for page in inspected.pages:
            text = page.text or ""
            conf = detail.get(page.page_number)
            per_page[page.page_number] = {"status": page.status, "doc_type": page.document_type, "method": page.method,
                                          "conf": round(float(conf if conf is not None else page.confidence or 0.0), 3),
                                          "words": _real_words(text), "chars": sum(c.isalnum() for c in text), "script": script_of(text),
                                          "inherited": bool(page.inherited), "reason": (page.reason or "")[:60]}
        self.stats.setdefault(key[0], {}).update(per_page)
        size = len(getattr(inspected, "_pdf", b"") or b"")
        self.cache[key] = inspected
        self.held += size
        while self.held > self.cap and len(self.cache) > 1:
            _, old = self.cache.popitem(last=False)
            self.held -= len(getattr(old, "_pdf", b"") or b"")
        return inspected


# --------------------------------------------------------------------------------------------- the pipeline (mirrors the app)

DOCUMENT_WORDS = set(("resume cv curriculum vitae passport aadhaar aadhar adhar aadhaaar aadhar card offer signed letter medical pcc police clearance certificate visa ticket air "
                      "photo scan scanned scans img image images doc docs document documents page pages copy front back education experience degree diploma marksheet sslc hsc "
                      "form application undertaking emigration insurance invoice stamping stamp whatsapp final new old file files pax and the for jpeg jpg png pdf heic webp docx").split())


def name_in_path(path: str) -> bool:
    """Port of the frontend's nameInPath (import-view.ts): a word of 3+ letters that is not a document word."""
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", path.split("/")[-1])
    return any(len(w) >= 3 and w.lower() not in DOCUMENT_WORDS for w in re.findall(r"[^\W\d_]+", base))


def needs_person_reading(units, picked_folders: int = 1) -> bool:
    """Port of needsPersonReading (import-view.ts): is reading the files offered for this picked folder?"""
    if not units:
        return False
    if any(u.identified for u in units):
        return False
    if any(u.left_out for u in units):
        return True
    if len(units) > 1:
        return False
    if picked_folders > 1:
        return False
    files = units[0].files
    return len(files) > 1 and any(not name_in_path(f.path) for f in files)


def run_pipeline(spec: dict, folder: Path, hook: OcrHook) -> dict:
    """The Import screen's sequence for ONE picked folder. Returns per-page predictions + per-unit/people info."""
    from app.api.routers import candidate_import as ci
    from app.api.routers.files import _content_type
    from app.services import bulk_candidate_import as bulk
    from app.services import people_by_content as pbc

    names = sorted((f["name"] for f in spec["files"]), key=natural_key)
    data = {name: (folder / name).read_bytes() for name in names}
    picked = ci.FolderIn(folder=spec["folder"], files=[ci.FileIn(path=n) for n in names])
    started = time.perf_counter()
    units = ci._units([picked])
    offered = needs_person_reading(units)
    result_info: dict = {"reading_offered": offered, "reading_ran": offered}
    pbc_pages: dict[tuple[str, int], dict] = {}
    people_dicts: list[dict] = []
    identify_seconds = 0.0
    if offered:
        t0 = time.perf_counter()
        res = pbc.identify_people([(n, data[n]) for n in names], reader=pbc.LocalReader(None, "eng"), workers=1, should_stop=None)
        identify_seconds = time.perf_counter() - t0
        for entry in res.files:
            for page in entry["pages"]:
                pbc_pages[(entry["path"], page["n"])] = page
        people_dicts = res.people
        persons = [ci.PersonIn(name=p["name"], files=[ci.FileIn(path=f["path"], pages=f.get("pages")) for f in p["files"]],
                               needsLook=bool(p["needsLook"]), warnings=p["warnings"]) for p in res.people]
        units = ci._units([picked], people={picked.folder: persons})
        result_info["totals"] = res.totals
        result_info["identify_seconds"] = round(identify_seconds, 1)
    unit_out = []
    page_pred: dict[tuple[str, int], list[dict]] = defaultdict(list)
    page_counts = {f["name"]: f["pages"] for f in spec["files"]}
    for index, unit in enumerate(units):
        entries = simulate_import(unit, data, page_counts, bulk, _content_type)
        taken_names: set[str] = set()
        out_files = []
        for entry in entries:
            pieces = entry["pieces"]
            for kinds, original_pages, flags in pieces:
                for n in original_pages:
                    page_pred[(entry["name"], n)].append({"unit": index, "kinds": kinds, "inherited": flags.get(n, {}).get("inherited", False),
                                                          "kind_conf": flags.get(n, {}).get("confidence"), "kind_reason": flags.get(n, {}).get("reason", "")})
            out_files.append({"name": entry["name"], "split": entry["split"], "pieces": [{"kinds": k, "pages": p} for k, p, _ in pieces]})
        unit_out.append({"index": index, "name_len": len(unit.name or ""), "identified": unit.identified, "needs_look": unit.needs_look,
                         "files": out_files, "left_out": list(unit.left_out), "name_text": unit.name or ""})
        del taken_names
    placed_in_unit: dict[tuple[str, int], list[int]] = defaultdict(list)
    for index, unit in enumerate(units):
        for item in unit.files:
            for n in (item.pages or list(range(1, page_counts[item.path] + 1))):
                placed_in_unit[(item.path, n)].append(index)
    result_info.update({"units": unit_out, "seconds": round(time.perf_counter() - started, 1),
                        "people": [{"needsLook": p["needsLook"], "evidence": p["evidence"], "pageCount": p["pageCount"],
                                    "conflict_kinds": [c["kind"] for c in p["conflicts"]], "warning_kinds": [w["kind"] for w in p["warnings"]]} for p in people_dicts]})
    pages = []
    for truth in spec["pages"]:
        key = (truth["file"], truth["page"])
        pb = pbc_pages.get(key, {})
        stats = hook.stats.get(hashlib.sha256(data[truth["file"]]).hexdigest(), {}).get(truth["page"], {})
        pages.append({"file": truth["file"], "page": truth["page"], "units": placed_in_unit.get(key, []), "pred": page_pred.get(key, []),
                      "pbc": {k: pb[k] for k in ("status", "person", "via", "low", "deep", "reason", "confidence", "method") if k in pb} if pb else None,
                      "ocr": stats})
    result_info["pages"] = pages
    return result_info


def simulate_import(unit, data: dict[str, bytes], page_counts: dict[str, int], bulk, content_type_of) -> list[dict]:
    """candidate_import.import_one without the database: the same decisions on the same bytes. Returns, per file, its pieces as
    (kinds, original page numbers, {page: kind-classifier detail})."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: F401  (import_one reads side by side; the sweep reads one at a time)
    candidates = []
    for item in unit.files:
        name = item.path
        blob = data[name]
        ct = content_type_of(None, name)
        kinds = [item.kind] if item.kind in bulk.KIND_LABELS else bulk.classify_path_kinds(name)
        cut = None
        if item.pages and ct == "application/pdf":
            try:
                blob, cut = bulk.cut_pdf_pages(blob, item.pages), list(item.pages)
            except ValueError:
                candidates.append({"name": name, "failed": True})
                continue
        candidates.append({"name": name, "data": blob, "content_type": ct, "kinds": kinds, "cut": cut})
    first_of: dict[str, dict] = {}
    for entry in candidates:
        if entry.get("failed"):
            continue
        if bulk.wants_split(entry["kinds"], entry["content_type"]) or not entry["kinds"]:
            digest = hashlib.sha256(entry["data"]).hexdigest()
            if digest not in first_of:
                first_of[digest] = entry
                if bulk.wants_split(entry["kinds"], entry["content_type"]):
                    entry["split"] = bulk.split_by_content(entry["data"], entry["content_type"], entry["kinds"], entry["name"])
                    if not entry["split"].split:
                        entry["kinds"] = entry["split"].pieces[0].kinds
                else:
                    entry["kinds"] = bulk.classify_content(entry["data"]) or ["other"]
    for entry in candidates:
        if entry.get("failed"):
            continue
        original = first_of.get(hashlib.sha256(entry["data"]).hexdigest())
        if original is not None and original is not entry and "split" not in entry and (original.get("split") or not entry["kinds"]):
            entry["kinds"], entry["split"] = original["kinds"], original.get("split")
    out = []
    for entry in candidates:
        if entry.get("failed"):
            out.append({"name": entry["name"], "split": False, "pieces": []})
            continue
        result = entry.get("split")
        cut = entry.get("cut")
        if result is not None and result.split:
            pieces = []
            for piece in result.pieces:
                numbered = [cut[n - 1] for n in piece.pages] if cut else list(piece.pages)
                detail = {}
                for n in piece.pages:
                    info = result.page_kinds.get(n)
                    if info:
                        detail[cut[n - 1] if cut else n] = info
                pieces.append((list(piece.kinds), numbered, detail))
            out.append({"name": entry["name"], "split": True, "pieces": pieces})
        else:
            numbered = list(cut) if cut else list(range(1, page_counts[entry["name"]] + 1))   # kept whole: every page of the original
            out.append({"name": entry["name"], "split": False, "pieces": [(list(entry["kinds"]), numbered, {})]})
    return out


# --------------------------------------------------------------------------------------------- judging

ALSO_RESUME = {"education_certificate", "experience_certificate", "application_form"}
FLAG_VIA = {"filename", "firstname"}


def name_matches(found: str, real_folder: str) -> bool:
    def tokens(text):
        return {t for t in re.split(r"[^a-z]+", text.lower()) if len(t) >= 3}
    a, b = tokens(found), tokens(real_folder)
    return bool(a and b and len(a & b) >= min(2, len(b)))


def quality_of(ocr: dict) -> dict:
    if not ocr:
        return {"scan": "unknown", "conf": None, "words": None, "script": "unknown", "bucket": "unknown"}
    method = ocr.get("method", "")
    native = method == "native_text"
    conf = ocr.get("conf", 0.0)
    words = ocr.get("words", 0)
    scan = "digital" if native else "photo-like" if "photo" in method or ocr.get("status") == "blank" else "scanned"
    bucket = "digital" if native else "low-conf" if conf < 0.5 else "mid-conf" if conf < 0.75 else "good-conf"
    if not native and words < 8:
        bucket += "+sparse"
    return {"scan": scan, "conf": conf, "words": words, "script": ocr.get("script", "unknown"), "bucket": bucket}


def overall_of(person_outcome: str, kind_outcome: str) -> str:
    if person_outcome in ("wrong_person_silent", "placed_in_several") or kind_outcome == "wrong_kind_silent":
        return "silent_mistake"
    if person_outcome == "wrong_person_flagged" or kind_outcome == "wrong_kind_flagged":
        return "flagged_mistake"
    if person_outcome == "placed_in_split_unit":
        return "split_off"
    if person_outcome == "unreadable":
        return "unreadable"
    if person_outcome == "unplaced":
        return "unplaced"
    if kind_outcome == "needs_you":
        return "needs_you_kind"
    if kind_outcome == "no_piece":
        return "lost"
    return "ok"


def judge(spec: dict, res: dict, mapping: dict[str, str]) -> dict:
    """Per page: person and kind outcome, plus the scenario's person-level summary."""
    truth_by_key = {(p["file"], p["page"]): p for p in spec["pages"]}
    units = res["units"]
    # which truth person each unit stands for: the majority of its pages' truth persons
    votes: dict[int, Counter] = defaultdict(Counter)
    for page in res["pages"]:
        for unit in page["units"]:
            votes[unit][truth_by_key[(page["file"], page["page"])]["person"]] += 1
    unit_person = {u: votes[u].most_common(1)[0][0] for u in votes if votes[u]}
    persons = spec["persons"]
    claimed: dict[str, list[int]] = defaultdict(list)
    for u, person in unit_person.items():
        claimed[person].append(u)
    biggest = {person: max(us, key=lambda u: sum(votes[u].values())) for person, us in claimed.items()}
    summary = {"expected": len(persons), "found_units": len(units), "persons_matched": len(biggest),
               "persons_without_unit": [p for p in persons if p not in biggest],
               "split_extra_units": sum(len(us) - 1 for us in claimed.values()),
               "merged_units": [], "junk_units": 0, "name_ok": 0, "name_checked": 0, "needs_look_units": sum(1 for u in units if u["needs_look"])}
    for u in range(len(units)):
        v = votes.get(u)
        if not v:
            summary["junk_units"] += 1          # a candidate made of no page at all
            continue
        if len(v) > 1:
            summary["merged_units"].append({"unit": u, "mix": sorted(v.values(), reverse=True)})
        if units[u]["identified"] and biggest.get(unit_person[u]) == u:
            summary["name_checked"] += 1
            summary["name_ok"] += int(name_matches(units[u]["name_text"], mapping[unit_person[u]]))
    # junk = extra candidates that hold only a sliver (<= 1 page or a different person's stray pages) next to a real one
    junk = [u for person, us in claimed.items() for u in us if u != biggest[person]]
    summary["junk_units"] += len(junk)
    pages = []
    for page in res["pages"]:
        truth = truth_by_key[(page["file"], page["page"])]
        entry = {"scn": spec["id"], "file": page["file"], "page": page["page"], "src": truth["src"], "truth_person": truth["person"],
                 "truth_kinds": truth["kinds"], "q": quality_of(page["ocr"]), "via": (page["pbc"] or {}).get("via")}
        pb = page["pbc"] or {}
        units_of = page["units"]
        unreadable = pb.get("status") == "unreadable" or (page["ocr"] or {}).get("status") == "unread"
        flagged_person = False
        if not units_of:
            person_outcome = "unreadable" if pb.get("status") == "unreadable" else "unplaced"
            entry["person_reason"] = pb.get("reason", "") if pb else "file's name names nobody and no reading was run" if not res["reading_ran"] else ""
        else:
            unit = units_of[0]
            predicted = unit_person.get(unit)
            flagged_person = bool(units[unit]["needs_look"]) or pb.get("via") in FLAG_VIA or bool(pb.get("low") and pb.get("via") in ("file", "adjacency")) \
                or bool(pb.get("probable"))
            if len(units_of) > 1:
                person_outcome = "placed_in_several"
            elif predicted == truth["person"]:
                person_outcome = "placed_ok" if biggest.get(predicted) == unit else "placed_in_split_unit"
            else:
                person_outcome = "wrong_person_flagged" if flagged_person else "wrong_person_silent"
            if unreadable and person_outcome == "placed_ok":
                person_outcome = "unreadable"
            entry["person_reason"] = ""
        entry["person"] = person_outcome
        entry["flagged_person"] = flagged_person
        # kind
        preds = page["pred"]
        predicted_kinds = sorted({k for p in preds for k in p["kinds"]})
        inherited = any(p["inherited"] for p in preds)
        entry["pred_kinds"] = predicted_kinds
        entry["inherited"] = inherited
        entry["via_file_name"] = False
        t = set(truth["kinds"])
        real = set(predicted_kinds) - {"other"}
        if not preds:
            kind_outcome = "no_piece"
        elif t == {"blank"}:
            kind_outcome = "blank_attached" if real else "blank_other"
        elif not real:
            kind_outcome = "needs_you"
        elif real & t:
            extra = real - t - ({"resume"} if t & ALSO_RESUME else set())
            kind_outcome = "kind_ok" if not extra else "kind_ok_plus_extra"
        else:
            kind_outcome = "wrong_kind_flagged" if inherited else "wrong_kind_silent"
        entry["kind"] = kind_outcome
        overall = overall_of(person_outcome, kind_outcome)
        entry["overall"] = overall
        pages.append(entry)
    return {"summary": summary, "pages": pages}


# --------------------------------------------------------------------------------------------- running + reporting

def run_all(args, log: Log, out: Path, specs: list[dict] | None = None) -> None:
    prepare_env(out)
    from app.core.config import get_settings
    settings = get_settings()
    log(f"settings: ocr_page_workers={settings.ocr_page_workers}, ocr_languages={settings.ocr_languages}, "
        f"aadhaar_languages={settings.ocr_aadhaar_languages}, online reader configured={bool(settings.veris_ocr_base_url)} (never called)")
    hook = OcrHook()
    hook.install()
    mapping = json.loads((out / "mapping.json").read_text(encoding="utf-8"))["labels"]
    spec_files = sorted((out / "scenarios").glob("*.json"))
    specs_by_file = {f: json.loads(f.read_text(encoding="utf-8")) for f in spec_files}
    # Order: people who share a folder in b / d are run together (a, b, b-sim, d, d-same) so the reads of their files are shared while
    # they are in memory; the one-big-file scenarios (new bytes, nothing to share) come last.
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for spec in specs_by_file.values():
        for person in spec["persons"][1:]:
            parent[find(person)] = find(spec["persons"][0])
        find(spec["persons"][0])
    order = {"a": 0, "b": 1, "b-sim": 2, "d": 3, "d-same": 4, "c1": 5, "c2": 6}
    group_of = {f: find(spec["persons"][0]) for f, spec in specs_by_file.items()}
    spec_files.sort(key=lambda f: (specs_by_file[f]["type"] in ("c1", "c2"), group_of[f] if specs_by_file[f]["type"] not in ("c1", "c2") else "",
                                   order.get(specs_by_file[f]["type"], 9), f.stem))
    (out / "results").mkdir(exist_ok=True)
    started = time.time()
    done = 0
    for spec_file in spec_files:
        spec = specs_by_file[spec_file]
        target = out / "results" / f"{spec['id']}.json"
        if target.exists() and not args.fresh:
            continue
        if (time.time() - started) / 60 > args.budget_min:
            log(f"budget of {args.budget_min} min reached: stopping before {spec['id']}")
            break
        waited = 0
        while free_mb() < args.min_free_mb:
            if waited >= 240:
                log(f"free memory stays under {args.min_free_mb} MB: stopping before {spec['id']}")
                return finish_partial(out, log)
            log(f"free memory {free_mb()} MB < {args.min_free_mb}: waiting 30 s")
            time.sleep(30)
            waited += 30
        pages = len(spec["pages"])
        log(f"START {spec['id']}: {len(spec['persons'])} people, {len(spec['files'])} files, {pages} pages, free={free_mb()} MB")
        t0 = time.perf_counter()
        cold0, hits0, pages0 = hook.cold_seconds, hook.hits, hook.cold_pages
        try:
            res = run_pipeline(spec, out / "scenarios" / spec["id"], hook)
            judged = judge(spec, res, mapping)
            payload = {"spec": {k: spec[k] for k in ("id", "type", "persons", "notes")} | {"files": [{k: v for k, v in f.items() if k != "name"} for f in spec["files"]]},
                       "pipeline": {k: v for k, v in res.items() if k not in ("pages", "units")}, "units": [{k: v for k, v in u.items() if k != "name_text"} for u in res["units"]],
                       "judged": judged, "wall_seconds": round(time.perf_counter() - t0, 1), "cold_ocr_seconds": round(hook.cold_seconds - cold0, 1),
                       "cold_ocr_pages": hook.cold_pages - pages0, "cache_hits": hook.hits - hits0, "pages_total": pages}
            target.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
            counts = Counter(p["overall"] for p in judged["pages"])
            log(f"DONE  {spec['id']}: {payload['wall_seconds']}s (cold OCR {payload['cold_ocr_seconds']}s / {payload['cold_ocr_pages']} pages, "
                f"{payload['cache_hits']} cache hits), units={len(res['units'])}, " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        except Exception as exc:          # a harness or pipeline crash is itself a finding: recorded, never hidden
            import traceback
            tb = traceback.format_exc().splitlines()
            target.write_text(json.dumps({"spec": {"id": spec["id"], "type": spec["type"], "persons": spec["persons"]}, "crash": f"{type(exc).__name__}: {str(exc)[:160]}",
                                          "where": [l.strip() for l in tb if "File" in l][-3:], "pages_total": pages}, indent=1), encoding="utf-8")
            log(f"CRASH {spec['id']}: {type(exc).__name__}")
        done += 1
        later = [f for f in spec_files[spec_files.index(spec_file) + 1:] if group_of[f] == group_of[spec_file] and specs_by_file[f]["type"] not in ("c1", "c2")]
        if spec["type"] in ("c1", "c2") or not later:
            hook.clear()
    log(f"run finished: {done} scenarios run this session, OCR cold {hook.cold_seconds:.0f}s over {hook.cold_pages} pages in {hook.cold_calls} reads")


def finish_partial(out: Path, log: Log) -> None:
    log("stopped early; partial results kept (resume with the same command)")



# --------------------------------------------------------------------------------------------- the report

FIXES = {
    "P0": "frontend import-view.needsPersonReading / candidate_import._units: offer (or simply run) the content reading when the names cannot tell the files apart, "
          "e.g. when no file name carries a person's name, or when only one of several files has a 'name' (a CamScanner counter counts as a name in nameInPath).",
    "P1": "people_by_content._analyse / _place_by_names: a page placed with no evidence of its own (file carry, adjacency, name) must be flagged or left unplaced; "
          "tighten the carry rule so a file with identity pages of two people never carries its loose pages to one of them.",
    "P2": "people_by_content: pages unplaced for lack of a name/number: a stronger second reading (page_enhance, Tamil pack) before giving up; keep as 'needs you' otherwise.",
    "P4": "people_by_content._cluster_pages: pages of one document (resume sheets) joined to the same person only by their own printed name; join pages of ONE file that read as one document, "
          "or merge a cluster that only has weak, single-page evidence into the person whose file it came from.",
    "P3": "inspection.inspect_document / page reading: the page could not be read (timeout / unreadable): check PAGE_TIMEOUT for large scans and the Tesseract ladder.",
    "K1": "kind_classifier / bulk_candidate_import._split_inspected: a page filed under a wrong kind without a flag; require two independent signals and flag (inherited) "
          "pages, or leave 'other' when only one kind-specific signal fired.",
    "K2": "kind_classifier: pages left as 'other' for a human: add the wording these pages carry (see the OCR-quality column: sparse/low-confidence pages need a better read first).",
    "K3": "bulk_candidate_import._split_inspected: a page filed in an extra field next to the right one: restrict the 'alongside' rule to the documented kinds.",
    "K4": "bulk_candidate_import.classify_content: image files with a neutral name only get resume/passport/aadhaar from content, never the other kinds.",
}


FIXES_BY_LABEL = [
    ("signed_offer: No name", "people_by_content.extract: signed offers carry the candidate's name in a labelled table row or a 'Dear' line that may sit only on page 1: carry the "
                              "later pages of the SAME file to the person page 1 names (they are one document), and add the offer wording ('Name of employee/candidate') to the labelled patterns."),
    ("air_ticket: ", "people_by_content.extract: e-tickets print the passenger as 'SURNAME/GIVENNAME MR' under 'Passenger'; add that pattern (checked against the passport name of the "
                     "same folder, never alone) so a ticket joins its holder; a ticket with a phone/e-mail only stays 'needs you'."),
    ("aadhaar+passport: This Aadhaar", "people_by_content: an Aadhaar page whose Tamil/English name is unreadable is placed by its number only if another page of the folder carries that number; "
                                       "otherwise read the name line again at higher resolution with eng+tam (page_enhance) before leaving it."),
    ("medical: It names more than one person", "people_by_content._why_unplaced: a medical report names the candidate AND the doctor; prefer the name next to a 'Name' label / the one that "
                                               "matches a passport name of the folder instead of refusing the page."),
    ("left as 'other'", "kind_classifier: add the layout/wording of these pages (page 1 of an offer, resume sheets without a 'resume' heading, a visa's second page); see the OCR figures: "
                        "they are readable (conf >= 0.55, >100 real words), so this is a wording gap, not an OCR gap. Photos and IMAGE files only get identity kinds from classify_content "
                        "(bulk_candidate_import.classify_content) - extend it to the other kinds the same way split_by_content does."),
    ("extra candidate", "people_by_content._cluster_pages / _person_clusters: a cluster made of a single page (a resume page 4, an education certificate, an e-mail- or phone-only page) "
                        "should not become a candidate: fold it into the person whose pages share its FILE, or list it under 'needs you'. The no_name / phone-only candidates are at least needsLook."),
    ("reading not offered", "frontend import-view.needsPersonReading + candidate_import._units: when the names are first names only (one word) for 2+ files that read as different "
                            "people, or several files whose names are identical, offer/run the content reading automatically; today the folder becomes ONE candidate and only the "
                            "post-import identity check can catch the mix."),
]


def fix_for(code: str, label: str) -> str:
    for needle, text in FIXES_BY_LABEL:
        if needle in label:
            return text
    return FIXES.get(code, "investigate")


def class_of(page: dict, scn: dict) -> tuple[str, str, bool]:
    """(code, label, silent) of one failing page."""
    t = "+".join(page["truth_kinds"])
    pred = "+".join(page["pred_kinds"]) or "(none)"
    q = page["q"]
    where = f"{q['scan']}/{q['bucket']}"
    multi = len(scn["persons"]) > 1
    if page["overall"] in ("silent_mistake", "flagged_mistake"):
        silent = page["overall"] == "silent_mistake"
        flag = "SILENT" if silent else "flagged"
        if page["person"].startswith("wrong_person") or page["person"] == "placed_in_several":
            if multi and not scn["reading_ran"]:
                return "P0", f"P0 [{flag}] several people in one folder, reading not offered: files of different people merged into one candidate", silent
            return "P1", f"P1 [{flag}] page placed with the wrong person (via {page.get('via') or 'file'})", silent
        return "K1", f"K1 [{flag}] {t} filed as {pred}", silent
    if page["overall"] == "split_off":
        return "P4", f"P4 [split off] the page of one person became (part of) an extra candidate, {t}", False
    if page["overall"] == "unplaced":
        reason = page.get("person_reason") or "no reason given"
        if multi and not scn["reading_ran"]:
            return "P0", "P0 [unplaced] several people in one folder, reading not offered: file left out (name says nobody's)", False
        return "P2", f"P2 [unplaced] {t}: {reason[:70]}", False
    if page["overall"] == "unreadable":
        return "P3", f"P3 [unreadable] {t}: page not read", False
    if page["overall"] == "needs_you_kind":
        return "K2", f"K2 [needs you] {t} left as 'other' ({where})", False
    if page["overall"] == "lost":
        return "K0", f"K0 [LOST] {t}: page in no piece", True
    return "K3", f"K3 {t} also filed as {pred}", False


_NAME_WORDS: set[str] = set()


def example_text(page: dict) -> str:
    q = page["q"]
    conf = "n/a" if q["conf"] is None else f"{q['conf']:.2f}"
    return (f"{page['scn']} {page['src']} p{page['page']} ({page['file']}): {q['scan']}, conf {conf}, {q['words']} real words, {q['script']} script"
            f" -> person {page['person']}, kind {'+'.join(page['pred_kinds']) or '(none)'}")


def scrub(text: str) -> str:
    """Nothing of a real name in a report: a first name kept in a partially named file ('Ashwin-2.pdf') becomes NAME."""
    for word in _NAME_WORDS:
        text = re.sub(re.escape(word), "NAME", text, flags=re.I)
    return text


def make_report(out: Path, log: Log) -> None:
    try:
        for real in json.loads((out / "mapping.json").read_text(encoding="utf-8"))["labels"].values():
            _NAME_WORDS.update(w for w in re.split(r"[^A-Za-z]+", real) if len(w) >= 3)
    except Exception:
        pass
    results = []
    for path in sorted((out / "results").glob("*.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    crashes = [r for r in results if "crash" in r]
    good = [r for r in results if "judged" in r]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in good:
        by_type[r["spec"]["type"]].append(r)
    order = ["a", "b", "b-sim", "d", "d-same", "c1", "c2"]
    types = [t for t in order if t in by_type] + [t for t in by_type if t not in order]
    title = {"a": "neutral names (one person)", "b": "2-3 people mixed, neutral names", "b-sim": "2 people with alike names, mixed", "c1": "one big file",
             "c2": "one big file + photo page + blank pages", "d": "first name only (2 people)", "d-same": "first name only, same first name (2 people)"}
    noise = {}
    noise_file = out / "truth_noise.json"
    if noise_file.exists():          # pages the owner's file name mislabels (a 'Resume' packet that holds a passport copy ...), audited with `peek`
        noise = json.loads(noise_file.read_text(encoding="utf-8"))
    src_page: dict[tuple[str, str, int], int] = {}
    for spec_path in (out / "scenarios").glob("*.json"):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        seen_src: Counter = Counter()
        for pg in spec["pages"]:
            seen_src[pg["src"]] += 1
            src_page[(spec["id"], pg["file"], pg["page"])] = seen_src[pg["src"]]
    noise_hits = Counter()
    for r in good:
        for p in r["judged"]["pages"]:
            accepted = (noise.get(p["src"]) or {}).get(str(src_page.get((p["scn"], p["file"], p["page"]), 0)))
            if accepted and p["kind"] in ("wrong_kind_silent", "wrong_kind_flagged") and set(p["pred_kinds"]) - {"other"} <= set(accepted):
                p["kind"], p["noise"] = "kind_ok", True
                p["overall"] = overall_of(p["person"], p["kind"])
                noise_hits[r["spec"]["type"]] += 1
    summary, classes = {}, defaultdict(list)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for t in types:
        group = by_type[t]
        pages = [p for r in group for p in r["judged"]["pages"]]
        n = len(pages)
        c = Counter(p["overall"] for p in pages)
        silent_person = sum(1 for p in pages if p["person"] in ("wrong_person_silent", "placed_in_several"))
        silent_kind = sum(1 for p in pages if p["kind"] == "wrong_kind_silent")
        sums = [r["judged"]["summary"] for r in group]
        wall = sum(r["wall_seconds"] for r in group)
        cold = sum(r["cold_ocr_seconds"] for r in group)
        cold_pages = sum(r["cold_ocr_pages"] for r in group)
        summary[t] = {"title": title.get(t, t), "scenarios": len(group), "pages": n, "ok": c["ok"],
                      "ok_with_extra_field": sum(1 for p in pages if p["kind"] == "kind_ok_plus_extra"), "ok_truth_noise": noise_hits[t],
                      "silent_mistake": c["silent_mistake"], "silent_wrong_person": silent_person, "silent_wrong_kind": silent_kind,
                      "flagged_mistake": c["flagged_mistake"], "unplaced": c["unplaced"], "unreadable": c["unreadable"], "split_off": c["split_off"], "needs_you_kind": c["needs_you_kind"],
                      "lost": c["lost"], "people_expected": sum(s["expected"] for s in sums), "candidates_found": sum(s["found_units"] for s in sums),
                      "people_without_candidate": sum(len(s["persons_without_unit"]) for s in sums), "merged_candidates": sum(len(s["merged_units"]) for s in sums),
                      "split_extra_candidates": sum(s["split_extra_units"] for s in sums), "junk_candidates": sum(s["junk_units"] for s in sums),
                      "names_ok": f"{sum(s['name_ok'] for s in sums)}/{sum(s['name_checked'] for s in sums)}",
                      "wall_s_per_page": round(wall / max(1, n), 1), "cold_ocr_s_per_page": round(cold / cold_pages, 1) if cold_pages else None,
                      "reading_offered": f"{sum(1 for r in group if r['pipeline']['reading_offered'])}/{len(group)}"}
        for r in group:
            scn = {"persons": r["spec"]["persons"], "reading_ran": r["pipeline"]["reading_ran"], "id": r["spec"]["id"]}
            for p in r["judged"]["pages"]:
                tl = "+".join(p["truth_kinds"])
                confusion[t][(tl, "+".join(p["pred_kinds"]) or ("(unplaced)" if p["person"] in ("unplaced", "unreadable") else "(none)"))] += 1
                if p["overall"] != "ok" or p["kind"] == "kind_ok_plus_extra":
                    code, label, silent = class_of(p, scn)
                    classes[(code, label, silent)].append(dict(p, scn_type=t))
    ranked = sorted(classes.items(), key=lambda kv: (not kv[0][2], -len(kv[1])))
    catalogue = []
    for (code, label, silent), items in ranked:
        items_sorted = sorted(items, key=lambda p: (p["q"]["conf"] if p["q"]["conf"] is not None else 2, p["scn"], p["page"]))
        picks = [items_sorted[0], items_sorted[len(items_sorted) // 2], items_sorted[-1]] if len(items_sorted) >= 3 else items_sorted
        uniq = list({id(e): e for e in picks}.values())
        catalogue.append({"code": code, "class": label, "silent": silent, "pages": len(items), "scenarios": len({i["scn"] for i in items}),
                          "types": sorted({i["scn_type"] for i in items}), "examples": [scrub(example_text(e)) for e in uniq], "fix": fix_for(code, label)})
    total_pages = sum(s["pages"] for s in summary.values())
    timing = {"scenario_seconds": {r["spec"]["id"]: r["wall_seconds"] for r in good},
              "cold_ocr_seconds_total": round(sum(r["cold_ocr_seconds"] for r in good), 1), "cold_ocr_pages_total": sum(r["cold_ocr_pages"] for r in good)}
    report = {"summary_by_type": summary, "catalogue": catalogue, "crashes": [{"id": r["spec"]["id"], "crash": r["crash"], "where": r.get("where")} for r in crashes],
              "pages_total": total_pages, "timing": timing,
              "confusion": {t: {f"{a} -> {b}": n for (a, b), n in sorted(c.items())} for t, c in confusion.items()}}
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    md = ["# Import corpus sweep", "", f"{len(good)} scenarios, {total_pages} pages judged, {len(crashes)} crashed. People are labelled C01..; no names, numbers or OCR text here.", "",
          "## Metrics per scenario type", "",
          "| type | scen | pages | ok | SILENT (person/kind) | flagged | split off | unplaced | unreadable | other (needs you) | lost | people exp -> candidates | merged | split | junk | no cand | names ok | reading offered | s/page | cold OCR s/page |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in types:
        s = summary[t]
        md.append(f"| {t} | {s['scenarios']} | {s['pages']} | {s['ok']} ({s['ok_truth_noise']} audited truth noise) | {s['silent_mistake']} ({s['silent_wrong_person']}/{s['silent_wrong_kind']}) | "
                  f"{s['flagged_mistake']} | {s['split_off']} | {s['unplaced']} | {s['unreadable']} | {s['needs_you_kind']} | {s['lost']} | {s['people_expected']} -> {s['candidates_found']} | "
                  f"{s['merged_candidates']} | {s['split_extra_candidates']} | {s['junk_candidates']} | {s['people_without_candidate']} | {s['names_ok']} | {s['reading_offered']} | "
                  f"{s['wall_s_per_page']} | {s['cold_ocr_s_per_page']} |")
    md += ["", "ok = page with the right person AND a right kind (pages of an owner's file the audit showed to be another document, e.g. a passport copy inside a 'Resume' packet, count as ok when the classifier agreed with the audit: see truth_noise.json); SILENT = filed with no warning and wrong; "
           "flagged = wrong but the app warned (needsLook / placed by name / low confidence / inherited kind); other (needs you) = right person, kind left as 'other'.", "",
           "## Failure catalogue (silent mistakes first, then volume)", ""]
    for i, item in enumerate(catalogue, 1):
        md += [f"### {i}. {item['class']}", f"- pages: {item['pages']} in {item['scenarios']} scenarios ({', '.join(item['types'])}){'  **SILENT**' if item['silent'] else ''}",
               "- examples (anonymised):"] + [f"    - {e}" for e in item["examples"]] + [f"- proposed fix: {item['fix']}", ""]
    md += ["## Kind confusion (truth -> what the page was filed as), pages", ""]
    for t in types:
        md += [f"### {t}", "", "| truth | filed as | pages |", "|---|---|---|"] + [f"| {a} | {b} | {n} |" for (a, b), n in sorted(confusion[t].items(), key=lambda kv: -kv[1])] + [""]
    if crashes:
        md += ["## Crashes (harness or pipeline)", ""] + [f"- {r['spec']['id']}: {r['crash']} at {r.get('where')}" for r in crashes] + [""]
    md += ["## Timing", "", f"cold OCR total {timing['cold_ocr_seconds_total']} s over {timing['cold_ocr_pages_total']} pages; per scenario wall seconds:", ""]
    md += [f"- {k}: {v}" for k, v in sorted(timing["scenario_seconds"].items())]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"report written: {out / 'report.md'}")


# --------------------------------------------------------------------------------------------- entry point

def list_library(args, out: Path, log: Log) -> None:
    library = load_library(Path(args.library))
    good = {n: f for n, f in library.items() if eligible(f)}
    kinds = Counter(k for files in library.values() for f in files for k in f["kinds"])
    log(f"{len(library)} people with documents; {len(good)} eligible for scenarios; files by kind: " + ", ".join(f"{k}={v}" for k, v in kinds.most_common()))
    log(f"native-text PDFs {sum(1 for fs in library.values() for f in fs if f['native'])}, scanned PDFs {sum(1 for fs in library.values() for f in fs if f['ext'] == '.pdf' and not f['native'])}, "
        f"images {sum(1 for fs in library.values() for f in fs if f['ext'] != '.pdf')}, unrecognised names {sum(1 for fs in library.values() for f in fs if not f['known'])}")


PEEK_WORDS = ["passport", "republic of india", "resume", "curriculum", "objective", "declaration", "education", "qualification", "certificate", "experience", "visa", "medical",
              "ticket", "offer", "aadhaar", "government", "date of birth", "police", "clearance", "employment", "salary", "marks", "university", "photograph", "signature", "worked", "designation", "employee", "relieving", "service", "to whom it may concern", "bonafide", "examination", "board", "hereby", "certify", "reference", "address", "mobile", "nationality", "place of issue", "type", "country code", "dear", "name", "candidate", "passenger", "mr.", "mr ", "mrs", "e-ticket", "booking", "joining", "agreement", "terms", "employer", "contract"]


def peek(args, out: Path, log: Log) -> None:
    """Truth-noise audit: which document words a page carries (flags and counts only; never the text, never a number)."""
    from app.services import bulk_candidate_import as bulk
    hook = OcrHook()
    hook.install()
    folder = out / "scenarios" / args.scn
    blob = bulk.cut_pdf_pages((folder / args.file).read_bytes(), [args.page]) if args.file.lower().endswith(".pdf") else (folder / args.file).read_bytes()
    from app.integrations.ocr.inspection import inspect_document
    inspected = inspect_document(blob, languages="eng", max_pages=1, page_timeout=20, document_timeout=60, two_pass=False)
    page = inspected.pages[0]
    text = (page.text or "").lower()
    log(f"peek {args.scn} {args.file} p{args.page}: status={page.status} doc_type={page.document_type} method={page.method} conf={page.confidence:.2f} "
        f"words={len(text.split())} flags=" + ",".join(f"{w}:{text.count(w)}" for w in PEEK_WORDS if w in text))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["list", "build", "run", "report", "sweep", "peek"])
    parser.add_argument("--scn")
    parser.add_argument("--file")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--out", required=True)
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY))
    parser.add_argument("--pick", default="auto:8")
    parser.add_argument("--types", default="a,b,c,d")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-files", type=int, default=6)
    parser.add_argument("--max-pages", type=int, default=14)
    parser.add_argument("--budget-min", type=float, default=70)
    parser.add_argument("--min-free-mb", type=int, default=900)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--clear-cache-between", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    prepare_env(out)
    log = Log(out / "progress.log")
    if args.smoke:
        args.types = "a,b"
        if "," not in args.pick:
            args.pick = "auto:2"
    if args.command == "list":
        list_library(args, out, log)
        return
    if args.command == "peek":
        peek(args, out, log)
        return
    if args.command in ("build", "sweep"):
        if args.fresh and (out / "results").exists():
            shutil.rmtree(out / "results")
        build(args, log, out)
    if args.command in ("run", "sweep"):
        run_all(args, log, out)
    if args.command in ("report", "sweep"):
        make_report(out, log)


if __name__ == "__main__":
    main()
