"""Check the guide's requirement map and local links without touching app data."""
from collections import Counter
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

GUIDE = Path(__file__).resolve().parent
MATRIX = GUIDE.parent / "requirements-matrix.md"


def heading_anchors(source):
    counts, anchors = Counter(), set()
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*$", source, re.M):
        title = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", title)
        key = re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
        suffix = f"-{counts[key]}" if counts[key] else ""
        counts[key] += 1
        anchors.add(key + suffix)
    return anchors


def main():
    matrix_ids = re.findall(r"^\| ([A-Z]+-\d+) \|", MATRIX.read_text(encoding="utf-8"), re.M)
    index = (GUIDE / "README.md").read_text(encoding="utf-8")
    coverage = Counter(re.findall(r"^\| ([A-Z]+-\d+) \|", index, re.M))
    errors = []
    missing = sorted(set(matrix_ids) - coverage.keys())
    extra = sorted(coverage.keys() - set(matrix_ids))
    repeated = sorted(key for key, count in coverage.items() if count != 1)
    if missing or extra or repeated:
        errors.append(f"Requirement coverage: missing={missing}; extra={extra}; repeated={repeated}")
    checked = 0
    for file in sorted(GUIDE.glob("*.md")):
        source = file.read_text(encoding="utf-8")
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", source):
            parsed = urlsplit(target.strip("<>"))
            if parsed.scheme or parsed.netloc:
                continue
            path = (file.parent / unquote(parsed.path)).resolve() if parsed.path else file
            checked += 1
            if not path.is_file():
                errors.append(f"{file.name}: missing local target {target}")
            elif parsed.fragment and path.suffix.lower() == ".md":
                if unquote(parsed.fragment) not in heading_anchors(path.read_text(encoding="utf-8")):
                    errors.append(f"{file.name}: missing section {target}")
    modules = sorted(GUIDE.glob("[0-9][0-9]-*.md"))
    if len(modules) != 22:
        errors.append(f"Expected 22 module files, found {len(modules)}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"PASS: {len(matrix_ids)} requirements mapped once, {len(modules)} modules, {checked} local links and anchors checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
