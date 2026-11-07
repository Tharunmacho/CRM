"""Check how the local reader handles a real file, printing only its structure.

    python -m app.integrations.ocr.selfcheck <file-or-folder> [--workers N] [--json]

For every file this runs the same local inspection the upload uses (no network, no database) and prints
page counts, a line per page (kind, confidence, which engine and setting read it, seconds, the reason when a
page is blank, a photo or unread) and whether a passport machine-readable zone or an Aadhaar number with a
valid check digit was found. It never prints, stores or sends text, numbers read from the document or page
images, so a problem file can be tested by support without exposing its content.

Exit status: 0 when every page of every file is accounted for as read, blank or photo; 1 when a page is
unread; 2 when a file cannot be opened.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .inspection import InspectionError, inspect_document

_SUFFIXES = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".gif"}


def describe(inspection, seconds: float) -> dict:
    """The structure report for one inspected file (numbers, page kinds, reasons; no content)."""
    c = inspection.completeness
    details = {d["page"]: d for d in c.get("pages_detail", [])}
    pages = []
    for page in inspection.pages:
        detail = details.get(page.page_number, {})
        kinds = [page.document_type, *page.also] if page.status == "read" else []
        pages.append({
            "page": page.page_number, "status": "photo" if page.method == "photo_no_text" else page.status,
            "kind": kinds, "confidence": round(page.confidence, 2), "method": page.method,
            "engine": detail.get("engine", "tesseract" if page.method.startswith("tesseract") else ""),
            "setting": detail.get("setting", ""), "seconds": detail.get("seconds"),
            "reason": page.reason if page.status != "read" else "",
            "settings_tried": len(detail.get("variants", [])),
        })
    patterns = c.get("patterns", {})
    return {
        "pages_total": c["pages_total"], "pages_read": c["pages_read"], "pages_blank": c["pages_blank"] - c.get("pages_photo", 0),
        "pages_photo": c.get("pages_photo", 0), "pages_unread": c["pages_unread"], "seconds": round(seconds, 1),
        "kinds": {"resume": inspection.resume_pages, "passport": inspection.passport_pages, "aadhaar": inspection.aadhaar_pages,
                  "unknown": inspection.unknown_pages},
        "mrz_found": patterns.get("mrz_found"), "mrz_checksums_valid": patterns.get("mrz_checksums_valid"),
        "aadhaar_found": patterns.get("aadhaar_found"), "aadhaar_checksum_valid": patterns.get("aadhaar_checksum_valid"),
        "email_found": patterns.get("email_found"), "mobile_found": patterns.get("mobile_found"),
        "pages": pages,
    }


def _print(name: str, report: dict) -> None:
    print(f"== {name}")
    print(f"   pages {report['pages_total']}: read {report['pages_read']}, blank {report['pages_blank']}, "
          f"photo {report['pages_photo']}, unread {report['pages_unread']}   ({report['seconds']} s)")
    print("   kinds: " + ", ".join(f"{k} {v}" for k, v in report["kinds"].items() if v))
    print(f"   MRZ found: {report['mrz_found']} (checksums valid: {report['mrz_checksums_valid']});  "
          f"Aadhaar number found: {report['aadhaar_found']} (checksum valid: {report['aadhaar_checksum_valid']});  "
          f"e-mail: {report['email_found']};  mobile: {report['mobile_found']}")
    for page in report["pages"]:
        kind = "+".join(page["kind"]) or "-"
        tail = f"  {page['reason']}" if page["reason"] and page["status"] != "read" else ""
        print(f"   p{page['page']:<3} {page['status']:<6} {kind:<18} conf {page['confidence']:<4} {page['engine'] or '-':<9} "
              f"{page['seconds'] if page['seconds'] is not None else '-':>5}s  {page['setting'][:28]}{tail}")


def run(paths: list[Path], *, workers: int | None = None, as_json: bool = False) -> int:
    files: list[Path] = []
    for path in paths:
        files += sorted(p for p in path.rglob("*") if p.suffix.lower() in _SUFFIXES) if path.is_dir() else [path]
    if not files:
        print("No files to check.", file=sys.stderr)
        return 2
    status, reports = 0, {}
    for file in files:
        started = time.monotonic()
        try:
            inspection = inspect_document(file.read_bytes(), languages="eng", page_workers=workers)
        except (InspectionError, OSError) as exc:
            print(f"== {file.name}\n   cannot be opened: {exc}")
            status = max(status, 2)
            continue
        report = describe(inspection, time.monotonic() - started)
        reports[file.name] = report
        if report["pages_unread"]:
            status = max(status, 1)
        if not as_json:
            _print(file.name, report)
    if as_json:
        print(json.dumps(reports, indent=2))
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.integrations.ocr.selfcheck", description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", type=Path, help="a file, or a folder searched recursively")
    parser.add_argument("--workers", type=int, default=None, help="pages read at once (default: the OCR_PAGE_WORKERS setting)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    arguments = parser.parse_args(argv)
    return run(arguments.paths, workers=arguments.workers, as_json=arguments.json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
