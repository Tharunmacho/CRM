"""Install the Tesseract language packs the CRM reads documents with, without admin rights.

Indian ID cards are bilingual (English plus the state language) and the English-only model turns the other script into
nonsense Latin letters. This script builds ONE folder, ``backend/data/tessdata`` by default (git-ignored), that holds

* ``eng`` and ``osd`` (copied from the local Tesseract install, which is only read, or downloaded when there is none),
* the extra languages (``tam`` by default), downloaded from the official tesseract-ocr/tessdata_fast repository,
* ``configs/tsv`` (and the other config files), which Tesseract needs to print its word table.

Tesseract reads a single folder per run and does not merge two, which is why everything is copied side by side. The OCR
code (``app/integrations/ocr/inspection.py``) uses this folder automatically when it holds more of the wanted languages
than the system install, and falls back to English with one warning when a pack is missing.

    python backend/scripts/install_ocr_languages.py                 # eng, osd, tam
    python backend/scripts/install_ocr_languages.py --langs tam hin # also Hindi
    python backend/scripts/install_ocr_languages.py --best          # the larger, slower, more accurate models

Safe to run again: a file that is already there and looks complete is never overwritten (use --force to replace it). On a
server that installs Tesseract with apt, ``apt-get install tesseract-ocr-tam`` does the same job and this script is not needed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DEST = BACKEND / "data" / "tessdata"
SOURCES = {
    "fast": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata",
    "best": "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata",
}
# Smaller than any real model: a download that stopped halfway, or an error page saved as a file, is below this.
MIN_BYTES = 50_000
LANGUAGE = re.compile(r"[a-z]{3}(?:_[a-z]+)?")


def find_tesseract(command: str | None) -> str | None:
    if command:
        return shutil.which(command) or (command if Path(command).is_file() else None)
    found = shutil.which("tesseract")
    if found:
        return found
    fallback = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return str(fallback) if os.name == "nt" and fallback.is_file() else None


def system_tessdata(command: str | None) -> Path | None:
    """The folder the Tesseract install keeps its language files in (only ever read)."""
    candidates: list[Path] = []
    if command:
        try:
            out = subprocess.run([command, "--list-langs"], capture_output=True, timeout=20, check=False).stdout
            match = re.search(r'in "(.+?)"', out.decode("utf-8", "replace"))
            if match:
                candidates.append(Path(match.group(1)))
        except (OSError, subprocess.SubprocessError):
            pass
        candidates.append(Path(command).resolve().parent / "tessdata")
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        candidates += [Path(prefix), Path(prefix) / "tessdata"]
    candidates += sorted(Path("/usr/share/tesseract-ocr").glob("*/tessdata"), reverse=True)
    candidates += [Path("/usr/share/tessdata"), Path("/usr/local/share/tessdata")]
    return next((c for c in candidates if (c / "eng.traineddata").is_file()), None)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def looks_complete(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_BYTES
    except OSError:
        return False


def report(action: str, path: Path) -> None:
    print(f"{action:<10} {path.name:<20} {path.stat().st_size:>10,} bytes  sha256 {digest(path)}")


def download(language: str, dest: Path, quality: str) -> bool:
    url = SOURCES[quality].format(lang=language)
    target = dest / f"{language}.traineddata"
    handle, name = tempfile.mkstemp(prefix=f"{language}-", suffix=".part", dir=dest)
    os.close(handle)
    part = Path(name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "install-ocr-languages"})
        with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as out:
            shutil.copyfileobj(response, out)
        if not looks_complete(part):
            print(f"FAILED     {language}: the download from {url} is too small to be a language file", file=sys.stderr)
            return False
        part.replace(target)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        print(f"FAILED     {language}: {error}\n           Download {url}\n           and save it as {target}", file=sys.stderr)
        return False
    finally:
        part.unlink(missing_ok=True)
    report("downloaded", target)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--langs", nargs="+", default=["tam"], help="extra languages to install (default: tam)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"target folder (default: {DEFAULT_DEST})")
    parser.add_argument("--tesseract", help="path to tesseract (default: PATH, then the usual Windows location)")
    parser.add_argument("--best", action="store_true", help="download the larger tessdata_best models instead of tessdata_fast")
    parser.add_argument("--force", action="store_true", help="replace files that are already there")
    args = parser.parse_args(argv)

    languages = [lang.lower() for lang in args.langs]
    bad = [lang for lang in languages if not LANGUAGE.fullmatch(lang)]
    if bad:
        parser.error(f"not a language code: {', '.join(bad)} (use codes such as tam, hin, mal, kan, tel)")
    quality = "best" if args.best else "fast"
    dest: Path = args.dest
    dest.mkdir(parents=True, exist_ok=True)
    command = find_tesseract(args.tesseract)
    source = system_tessdata(command)
    print(f"Target folder: {dest}")
    print(f"Tesseract:     {command or 'not found (eng and osd will be downloaded)'}")
    print(f"System data:   {source or 'not found'} (read only)")

    failed = False
    for language in ["eng", "osd"] + [lang for lang in languages if lang not in ("eng", "osd")]:
        target = dest / f"{language}.traineddata"
        if looks_complete(target) and not args.force:
            report("kept", target)
            continue
        original = source / f"{language}.traineddata" if source else None
        if language in ("eng", "osd") and original is not None and looks_complete(original):
            shutil.copy2(original, target)
            report("copied", target)
        elif not download(language, dest, quality):
            failed = True

    # Config files: ``tsv`` makes Tesseract print the word table the reader parses. Without it the output is plain text.
    for folder in ("configs", "tessconfigs"):
        if source is not None and (source / folder).is_dir() and not (dest / folder).exists():
            shutil.copytree(source / folder, dest / folder)
            print(f"copied     {folder}/")
    tsv = dest / "configs" / "tsv"
    if not tsv.is_file():
        tsv.parent.mkdir(parents=True, exist_ok=True)
        tsv.write_text("tessedit_create_tsv 1\n", encoding="ascii")
        print("created    configs/tsv")

    if command:
        listing = subprocess.run([command, "--list-langs", "--tessdata-dir", str(dest)], capture_output=True, check=False)
        found = {line.strip() for line in listing.stdout.decode("utf-8", "replace").splitlines()[1:]}
        wanted = {"eng", "osd", *languages}
        print(f"Tesseract lists: {', '.join(sorted(found)) or 'nothing'}")
        if wanted - found:
            print(f"MISSING: {', '.join(sorted(wanted - found))}", file=sys.stderr)
            failed = True
    print("Done. Restart the backend so it picks the languages up." if not failed else "Finished with problems (see above).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
