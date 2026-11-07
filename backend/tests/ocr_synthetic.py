"""Synthetic documents (no real people) with known ground truth for OCR engine tests."""
from __future__ import annotations

import io
import re
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

_FONTS = (r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
          "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")

_JOBS = ("Welding Supervisor", "Piping Fitter", "Site Electrician", "Crane Operator", "Scaffolding Foreman",
         "Quality Inspector", "Mechanical Technician", "Safety Officer")
_FIRMS = ("Northwind Fabrication", "Harbour Engineering", "Delta Marine Works", "Summit Projects",
          "Orchid Industrial", "Granite Contracting")


def page_lines(index: int) -> list[str]:
    """About 40 lines of resume-like text, unique per page, rich in numbers and e-mail/phone strings."""
    n = index + 1
    lines = [
        f"CURRICULUM VITAE - Candidate {n:03d}",
        f"Email: candidate{n:03d}.sample@example.test    Phone: +91 98{n:03d}4 5{n % 10}210",
        f"Passport No: Z{7000000 + n * 137}    Date of birth: {1 + n % 27:02d}/{1 + n % 12:02d}/{1980 + n % 20}",
        "",
        "PROFESSIONAL EXPERIENCE",
    ]
    for k in range(5):
        job, firm = _JOBS[(n + k) % len(_JOBS)], _FIRMS[(n * 3 + k) % len(_FIRMS)]
        start = 2004 + (n + k * 3) % 12
        lines += [f"{job}, {firm}, {start} - {start + 3}",
                  f"Managed {10 + (n + k) % 40} workers and inspected {200 + n * 7 + k} welds per month under code {1000 + n + k}.",
                  f"Responsible for site safety, permit {5000 + n * 11 + k} and daily progress reports."]
    lines += ["", "EDUCATION AND SKILLS"]
    for k in range(4):
        lines += [f"Diploma in Mechanical Engineering {1995 + (n + k) % 10}, Certificate {900000 + n * 13 + k}, grade {60 + (n + k) % 35} percent.",
                  f"Skills: arc welding, TIG welding, blueprint reading, safety audits, level {k + 1} certified."]
    lines += ["", f"Declaration: the details above are true. Reference sheet {n} of the synthetic test set."]
    return lines


def words_of(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9@.+/_-]+", text)


def _font(size: int):
    for candidate in _FONTS:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def render_lines(lines: list[str], *, dpi: int = 200, point_size: float = 11, noise: bool = False) -> Image.Image:
    """An A4 grayscale page with the lines as pixels, like a scan."""
    width, height = int(8.27 * dpi), int(11.69 * dpi)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    size = int(point_size * dpi / 72)
    font = _font(size)
    y, left = int(0.9 * dpi), int(0.9 * dpi)
    for line in lines:
        draw.text((left, y), line, fill=20, font=font)
        y += int(size * 1.45)
    if noise:
        import random
        rng = random.Random(7)
        for _ in range(width * height // 900):
            draw.point((rng.randrange(width), rng.randrange(height)), fill=rng.randrange(120, 200))
    return image


def jpeg_page(image: Image.Image, quality: int = 80) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def scanned_pdf(page_indexes: list[int | None], *, dpi: int = 200, rotate: dict[int, int] | None = None,
                point_size: float = 11) -> bytes:
    """Image-only PDF. ``None`` is a white page; ``rotate`` maps page position -> degrees to turn the pixels."""
    rotate = rotate or {}
    with fitz.open() as document:
        for position, index in enumerate(page_indexes):
            image = render_lines([] if index is None else page_lines(index), dpi=dpi, point_size=point_size)
            if rotate.get(position):
                image = image.rotate(rotate[position], expand=True, fillcolor=255)
            page = document.new_page(width=image.width * 72 / dpi, height=image.height * 72 / dpi)
            page.insert_image(page.rect, stream=jpeg_page(image))
        return document.tobytes(deflate=True)


def native_pdf(page_indexes: list[int | None]) -> bytes:
    with fitz.open() as document:
        for index in page_indexes:
            page = document.new_page()
            if index is not None:
                y = 60
                for line in page_lines(index):
                    if line:
                        page.insert_text((50, y), line, fontsize=9.5, fontname="helv")
                    y += 14
        return document.tobytes(deflate=True)


def mixed_pdf(kinds: list[tuple[str, int | None]]) -> bytes:
    """Per page ('native'|'scan'|'blank', text index); built page by page into one PDF."""
    with fitz.open() as document:
        for kind, index in kinds:
            if kind == "native":
                piece = fitz.open(stream=native_pdf([index]), filetype="pdf")
            elif kind == "scan":
                piece = fitz.open(stream=scanned_pdf([index]), filetype="pdf")
            else:
                piece = fitz.open(stream=native_pdf([None]), filetype="pdf")
            document.insert_pdf(piece)
            piece.close()
        return document.tobytes(deflate=True)


def partial_native_pdf(index: int) -> bytes:
    """A scan with a tiny invisible-looking native text layer (a stamp or footer) over the real content."""
    with fitz.open() as document:
        image = render_lines(page_lines(index))
        page = document.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
        page.insert_image(page.rect, stream=jpeg_page(image))
        page.insert_text((40, 30), "Scanned by DocuScan 12 pages total footer", fontsize=8)
        return document.tobytes(deflate=True)


def multiframe_tiff(indexes: list[int]) -> bytes:
    frames = [render_lines(page_lines(i)).convert("1") for i in indexes]
    out = io.BytesIO()
    frames[0].save(out, format="TIFF", save_all=True, append_images=frames[1:], compression="tiff_lzw")
    return out.getvalue()


def recall(truth_lines: list[str], recognised: str) -> float:
    """Share of ground-truth words found in the recognised text (multiset-free, token level)."""
    truth = words_of("\n".join(truth_lines))
    if not truth:
        return 1.0
    found = set(words_of(recognised))
    # Compare trailing punctuation leniently: "2004," vs "2004".
    norm = {w.strip(".,;:") for w in found}
    return sum(w.strip(".,;:") in norm for w in truth) / len(truth)


# ---- identity documents (fake data only) ----------------------------------------------------

def passport_lines(n: int = 1) -> list[str]:
    """The photo page of an Indian-style passport, all values invented."""
    serial = 7000000 + n * 137  # the same number page_lines(n - 1) prints, so one person's pages agree
    return [
        "REPUBLIC OF INDIA",
        "PASSPORT",
        f"Type P    Country Code IND    Passport No. Z{serial}",
        f"Surname SAMPLE{n}",
        "Given Name(s) TESTER",
        "Nationality INDIAN",
        "Sex M    Date of Birth 01/01/1990",
        "Place of Birth EXAMPLEPUR",
        "Place of Issue EXAMPLEPUR",
        "Date of Issue 02/02/2020    Date of Expiry 01/02/2030",
        "",
        f"P<INDSAMPLE{n}<<TESTER<<<<<<<<<<<<<<<<<<<<<<<",
        f"Z{serial}<4IND9001015M3001012<<<<<<<<<<<<<<04",
    ]


def aadhaar_lines(n: int = 1) -> list[str]:
    """The front of an Aadhaar-style card, all values invented."""
    group = f"{1000 + n:04d} {2000 + n:04d} {3000 + n:04d}"
    return [
        "Government of India",
        "Unique Identification Authority of India",
        f"Tester Sample{n}",
        "DOB: 01/01/1990",
        "MALE",
        group,
        "Aadhaar - Mera Aadhaar, Meri Pehchan",
        "VID : 1234 5678 9012 3456",
    ]


def aadhaar_back_lines(n: int = 1) -> list[str]:
    return [
        "Unique Identification Authority of India",
        "Address:",
        f"S/O Parent Sample{n}, 12 Example Road, Examplepur, Example State - 000000",
        f"{1000 + n:04d} {2000 + n:04d} {3000 + n:04d}",
        "Aadhaar is a proof of identity, not of citizenship",
    ]


_UNRELATED = ["Minutes of the neighbourhood garden committee", "Agenda: bench repairs, watering rota, summer picnic",
              "Attendees: nine members; apologies from two", "Next meeting is on the first Monday of the month"] * 3

_PAGE_LINES = {
    "passport": lambda n: passport_lines(n),
    "aadhaar": lambda n: aadhaar_lines(n),
    "aadhaar_back": lambda n: aadhaar_back_lines(n),
    "unrelated": lambda n: list(_UNRELATED),
    "certificate": lambda n: ["CERTIFICATE OF COMPLETION", "This is to certify that Tester Sample has completed the training.",
                              f"Certificate number {n:06d}. Authorised signatory."],
}


def composite_pdf(pages: list[str], *, form: str = "native") -> bytes:
    """One file made of the listed pages, in that order: ``resume:N`` (a résumé page), ``passport``, ``aadhaar``,
    ``aadhaar_back``, ``passport+aadhaar`` or ``resume:1+passport`` (both on one sheet), ``certificate`` or ``unrelated``.
    ``form`` is ``native`` (text layer) or ``scan`` (pixels only, like a scanner or a phone photo)."""
    with fitz.open() as document:
        for position, spec in enumerate(pages):
            lines: list[str] = []
            for part in spec.split("+"):  # "passport+aadhaar" puts both on one sheet
                name, _, arg = part.partition(":")
                lines += ([""] if lines else []) + (page_lines(int(arg or 0)) if name == "resume" else _PAGE_LINES[name](1))
            if form == "scan":
                image = render_lines(lines)
                page = document.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
                page.insert_image(page.rect, stream=jpeg_page(image))
            else:
                page = document.new_page()
                y = 60
                for line in lines:
                    if line:
                        page.insert_text((50, y), line, fontsize=9.5, fontname="helv")
                    y += 14
        return document.tobytes(deflate=True)
