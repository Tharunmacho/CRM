"""Synthetic 'hard case' pages for the OCR regression gate: invented people, known ground truth.

Each builder returns a Pillow image (an A4 page at 200 dpi unless said otherwise) plus the text it
carries. They imitate what real uploads look like: security-print backgrounds, photographed cards
on a table, faint fax copies, stamps over text, sideways pages, two documents on one sheet, a
photo with no text. Nothing here is a real document and every number is invented.
"""
from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import ocr_synthetic as synth
from app.integrations.ocr.read_merge import verhoeff_valid
from app.integrations.ocr.validators import mrz_check_digit

_MONO = (r"C:\Windows\Fonts\consolab.ttf", r"C:\Windows\Fonts\cour.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf")
W, H = 1654, 2339  # A4 at 200 dpi


def _mono(size: int):
    for candidate in _MONO:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


@dataclass
class Case:
    name: str
    image: Image.Image
    truth: list[str] = field(default_factory=list)       # lines whose words must be recovered
    mrz_number: str = ""                                    # passport number planted in a valid MRZ
    aadhaar: str = ""                                       # 12-digit number planted (Verhoeff-valid)
    photo_only: bool = False
    min_recall: float = 0.85


# ---------------------------------------------------------------------------- planted identifiers

def valid_aadhaar(seed: int) -> str:
    rng = random.Random(seed)
    while True:
        body = str(rng.randrange(2, 10)) + "".join(str(rng.randrange(10)) for _ in range(10))
        for check in "0123456789":
            if verhoeff_valid(body + check):
                return body + check


def valid_mrz(n: int = 1) -> tuple[str, str, str]:
    """Two 44-character TD3 lines with correct ICAO check digits, and the passport number."""
    number = f"K{3000000 + n * 211}"
    number = number.ljust(9, "<")
    birth, expiry = "900101", "310102"
    personal = "<" * 14
    line2 = (number + str(mrz_check_digit(number)) + "IND" + birth + str(mrz_check_digit(birth)) + "M" + expiry
             + str(mrz_check_digit(expiry)) + personal + str(mrz_check_digit(personal)))
    line2 += str(mrz_check_digit(line2[0:10] + line2[13:20] + line2[21:43]))
    line1 = f"P<INDSAMPLE{n}<<TESTER".ljust(44, "<")
    return line1, line2, number.rstrip("<")


def spaced(number: str) -> str:
    return f"{number[:4]} {number[4:8]} {number[8:]}"


# ---------------------------------------------------------------------------- page furniture

def _blank(colour=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", (W, H), colour)


def _guilloche(image: Image.Image, seed: int = 3, strength: int = 70) -> None:
    """Pale blue/green wave lines and fine noise like a security-printed background."""
    rng = random.Random(seed)
    draw = ImageDraw.Draw(image, "RGBA")
    for k in range(60):
        phase, amp, freq = rng.random() * 6.28, rng.randrange(8, 40), rng.uniform(0.004, 0.012)
        colour = (rng.randrange(120, 190), rng.randrange(170, 230), rng.randrange(180, 240), strength)
        base = 40 + k * (H // 60)
        points = [(x, base + amp * math.sin(x * freq + phase) + amp * 0.5 * math.sin(x * freq * 2.3)) for x in range(0, W, 8)]
        draw.line(points, fill=colour, width=3)
    for _ in range(W * H // 700):
        x, y = rng.randrange(W), rng.randrange(H)
        shade = rng.randrange(170, 230)
        draw.point((x, y), fill=(shade, shade + 10, shade, 120))


def _text(draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int, size: int = 36, fill=(15, 15, 25), gap: float = 1.5):
    font = synth._font(size)
    for line in lines:
        draw.text((x, y), line, fill=fill, font=font)
        y += int(size * gap)
    return y


# ---------------------------------------------------------------------------- the cases

def passport_page_textured(n: int = 1) -> Case:
    line1, line2, number = valid_mrz(n)
    image = _blank((246, 244, 232))
    _guilloche(image)
    draw = ImageDraw.Draw(image)
    body = ["REPUBLIC OF INDIA", "PASSPORT", f"Type P   Country Code IND   Passport No. {number}",
            f"Surname SAMPLE{n}", "Given Name(s) TESTER", "Nationality INDIAN",
            "Sex M   Date of Birth 01/01/1990", "Place of Birth EXAMPLEPUR", "Place of Issue EXAMPLEPUR",
            "Date of Issue 02/01/2021   Date of Expiry 02/01/2031"]
    _text(draw, body, 160, 230, 46, gap=1.9)
    mono = _mono(38)
    draw.text((90, H - 420), line1, fill=(20, 20, 20), font=mono)
    draw.text((90, H - 360), line2, fill=(20, 20, 20), font=mono)
    return Case("passport textured with MRZ", image.filter(ImageFilter.GaussianBlur(0.6)), body, mrz_number=number, min_recall=0.8)


def aadhaar_card_on_table(angle: float = 8.0, n: int = 1) -> Case:
    """A photographed Aadhaar-style card lying rotated on a dark table, with a little blur and shadow."""
    number = valid_aadhaar(100 + n)
    card = Image.new("RGB", (1011, 638), (250, 248, 240))
    d = ImageDraw.Draw(card)
    d.rectangle((0, 0, 1011, 90), fill=(225, 120, 40))
    lines = ["Government of India", f"Tester Sample{n}", "DOB: 01/01/1990", "MALE"]
    _text(d, ["Government of India"], 280, 22, 44, fill=(255, 255, 255))
    _text(d, lines[1:], 300, 140, 46, gap=1.7)
    d.rectangle((40, 130, 250, 400), fill=(150, 130, 120))  # photo block
    d.text((250, 500), spaced(number), fill=(10, 10, 10), font=synth._font(78))
    d.rectangle((0, 600, 1011, 638), fill=(30, 100, 60))
    card = card.filter(ImageFilter.GaussianBlur(0.8))
    table = Image.new("RGB", (W, H), (38, 34, 30))
    rng = random.Random(5)
    td = ImageDraw.Draw(table)
    for _ in range(4000):
        x, y = rng.randrange(W), rng.randrange(H)
        shade = rng.randrange(20, 70)
        td.point((x, y), fill=(shade, shade - 4, shade - 8))
    scaled = card.resize((int(card.width * 1.35), int(card.height * 1.35)), Image.LANCZOS)
    turned = scaled.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
    mask = Image.new("L", scaled.size, 255).rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=0)
    table.paste(turned, ((W - turned.width) // 2, 500), mask)
    table = table.filter(ImageFilter.GaussianBlur(0.7))
    return Case(f"aadhaar card on table {angle:+.0f} deg", table, ["Government of India", f"Tester Sample{n}", "DOB: 01/01/1990"],
                aadhaar=number, min_recall=0.6)


def fax_low_contrast(n: int = 3) -> Case:
    lines = synth.page_lines(n)
    image = synth.render_lines(lines).convert("RGB")
    faded = Image.eval(image.convert("L"), lambda v: 175 + (v * 70) // 255)  # ink at ~175, paper at 245
    faded = faded.filter(ImageFilter.GaussianBlur(1.0))
    rng = random.Random(9)
    noisy = Image.new("L", faded.size)
    noisy.putdata([max(0, min(255, p + rng.randrange(-12, 12))) for p in faded.getdata()])
    return Case("fax low contrast", noisy.convert("RGB"), lines, min_recall=0.75)


def stamp_over_text(n: int = 4) -> Case:
    lines = synth.page_lines(n)
    image = synth.render_lines(lines).convert("RGB")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.ellipse((520, 520, 1200, 1000), outline=(200, 30, 40, 150), width=14)
    d.ellipse((560, 560, 1160, 960), outline=(200, 30, 40, 120), width=6)
    d.text((620, 730), "RECEIVED", fill=(200, 30, 40, 170), font=synth._font(90))
    return Case("stamp over text", Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), lines, min_recall=0.85)


def sideways(angle: int = 90, n: int = 5) -> Case:
    lines = synth.page_lines(n)
    image = synth.render_lines(lines).convert("RGB").rotate(angle, expand=True, fillcolor=(255, 255, 255))
    return Case(f"page turned {angle} deg", image, lines, min_recall=0.9)


def two_documents_on_one_page(n: int = 2) -> Case:
    line1, line2, number = valid_mrz(n)
    aadhaar = valid_aadhaar(200 + n)
    image = _blank()
    draw = ImageDraw.Draw(image)
    top = ["REPUBLIC OF INDIA", "PASSPORT", f"Passport No. {number}", f"Surname SAMPLE{n}", "Given Name(s) TESTER",
           "Nationality INDIAN"]
    y = _text(draw, top, 140, 140, 42, gap=1.8)
    mono = _mono(38)
    draw.text((90, y + 20), line1, fill=(20, 20, 20), font=mono)
    draw.text((90, y + 80), line2, fill=(20, 20, 20), font=mono)
    bottom = ["Government of India", "Unique Identification Authority of India", f"Tester Sample{n}", "DOB: 01/01/1990", "MALE"]
    y2 = _text(draw, bottom, 140, 1250, 42, gap=1.8)
    draw.text((140, y2 + 20), spaced(aadhaar), fill=(10, 10, 10), font=synth._font(70))
    return Case("two documents on one page", image, top + bottom, mrz_number=number, aadhaar=aadhaar, min_recall=0.9)


def photo_only(seed: int = 4) -> Case:
    """A portrait-like picture: smooth tones, a face-like blob, no text at all."""
    rng = random.Random(seed)
    image = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(image)
    box = (300, 400, 1350, 1900)
    for step in range(60):
        shade = 60 + step * 2
        d.rectangle((box[0] + step, box[1] + step, box[2] - step, box[3] - step), fill=(shade, shade - 10, shade - 20))
    d.ellipse((560, 700, 1090, 1300), fill=(205, 160, 130))
    d.ellipse((650, 900, 740, 960), fill=(40, 30, 30))
    d.ellipse((900, 900, 990, 960), fill=(40, 30, 30))
    d.rectangle((520, 1250, 1130, 1850), fill=(30, 40, 90))
    image = image.filter(ImageFilter.GaussianBlur(3))
    noisy = Image.effect_noise(image.size, 12).convert("RGB")
    return Case("photo only", Image.blend(image, noisy, 0.06), photo_only=True)


def to_pdf(images: list[Image.Image], dpi: int = 200, quality: int = 80) -> bytes:
    with fitz.open() as document:
        for image in images:
            page = document.new_page(width=image.width * 72 / dpi, height=image.height * 72 / dpi)
            out = io.BytesIO()
            image.convert("RGB").save(out, format="JPEG", quality=quality)
            page.insert_image(page.rect, stream=out.getvalue())
        return document.tobytes(deflate=True)


def hard_matrix() -> list[Case]:
    return [
        passport_page_textured(1),
        aadhaar_card_on_table(8.0, 1), aadhaar_card_on_table(-12.0, 2),
        fax_low_contrast(3),
        stamp_over_text(4),
        sideways(90, 5), sideways(180, 6), sideways(270, 7),
        two_documents_on_one_page(2),
        photo_only(4),
    ]
