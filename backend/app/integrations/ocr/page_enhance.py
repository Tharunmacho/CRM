"""Image clean-up for pages that the plain OCR pass reads badly (Pillow only, no OpenCV/NumPy).

Every function takes and returns Pillow images and works on a small copy wherever a global
measure is enough, so the cost stays at a few tens of milliseconds per page. Nothing here
reads or stores text; it only changes pixels.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


def to_grey(image: Image.Image) -> Image.Image:
    return image if image.mode == "L" else image.convert("L")


def _small(image: Image.Image, longest: int = 600) -> Image.Image:
    small = image.copy()
    small.thumbnail((longest, longest), Image.BILINEAR)
    return small


def red_channel(image: Image.Image) -> Image.Image:
    """The red channel: security-print backgrounds (blue/green guilloche) mostly vanish in it."""
    if image.mode == "L":
        return image
    return image.convert("RGB").getchannel("R")


def flatten_background(grey: Image.Image, *, radius: int = 24) -> Image.Image:
    """Divide out uneven lighting and pale textured backgrounds.

    The page background is estimated on a copy 1/``radius`` the size (a grey closing removes the
    dark text, a blur smooths it), scaled back up and subtracted, so paper becomes white and ink
    keeps its darkness relative to the paper around it.
    """
    grey = to_grey(grey)
    factor = max(2, min(8, radius // 3))
    small = grey.resize((max(1, grey.width // factor), max(1, grey.height // factor)), Image.BOX)
    size = 5 if min(small.size) > 40 else 3
    background = small.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
    background = background.filter(ImageFilter.GaussianBlur(max(1, size)))
    background = background.resize(grey.size, Image.BICUBIC)
    flat = ImageChops.subtract(grey, background, scale=1.0, offset=255)
    return ImageOps.autocontrast(flat, cutoff=1)


def local_threshold(grey: Image.Image, *, window: int = 31, offset: int = 12) -> Image.Image:
    """Black ink on white paper by comparing each pixel with the mean of its neighbourhood."""
    grey = to_grey(grey)
    mean = grey.filter(ImageFilter.BoxBlur(max(2, window // 2)))
    # ink where the pixel is clearly darker than the local mean
    diff = ImageChops.subtract(mean, grey, scale=1.0, offset=0)
    return diff.point(lambda v, t=offset: 0 if v > t else 255)


def local_contrast(grey: Image.Image, *, radius: int = 40, strength: float = 1.0) -> Image.Image:
    """CLAHE-like: stretch contrast against the local mean (fax-grey and faded print)."""
    grey = to_grey(grey)
    mean = grey.filter(ImageFilter.BoxBlur(max(2, radius)))
    # grey + strength*(grey - mean), on a 128-centred scale
    diff = ImageChops.subtract(grey, mean, scale=1.0 / max(0.1, strength), offset=128)
    stretched = ImageOps.autocontrast(diff, cutoff=1)
    return stretched


def denoise(grey: Image.Image) -> Image.Image:
    return to_grey(grey).filter(ImageFilter.MedianFilter(3))


def sharpen(grey: Image.Image, *, radius: float = 2.0, percent: int = 160) -> Image.Image:
    return to_grey(grey).filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))


def upscale(image: Image.Image, factor: float) -> Image.Image:
    if factor == 1:
        return image.copy()
    size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    return image.resize(size, Image.LANCZOS)


def ink_density(grey: Image.Image) -> float:
    """Share of clearly dark pixels on a small copy."""
    histogram = _small(to_grey(grey), 400).histogram()
    return sum(histogram[:120]) / max(1, sum(histogram))


def contrast_stats(grey: Image.Image) -> dict:
    small = _small(to_grey(grey), 400)
    stat = ImageStat.Stat(small)
    histogram = small.histogram()
    total = max(1, sum(histogram))
    low = next((i for i, c in enumerate(histogram) if c > total * 0.01), 0)
    high = next((255 - i for i, c in enumerate(reversed(histogram)) if c > total * 0.01), 255)
    return {"mean": stat.mean[0], "std": stat.stddev[0], "range": high - low, "dark": sum(histogram[:80]) / total}


# ---------------------------------------------------------------------------------------------
# Photographed cards: find the document on a dark or cluttered surface
# ---------------------------------------------------------------------------------------------

def _otsu(histogram: list[int]) -> int:
    total = sum(histogram)
    if not total:
        return 128
    sum_all = sum(i * c for i, c in enumerate(histogram))
    best, best_var, weight_b, sum_b = 128, -1.0, 0, 0.0
    for level in range(256):
        weight_b += histogram[level]
        if not weight_b:
            continue
        weight_f = total - weight_b
        if not weight_f:
            break
        sum_b += level * histogram[level]
        mean_b, mean_f = sum_b / weight_b, (sum_all - sum_b) / weight_f
        between = weight_b * weight_f * (mean_b - mean_f) ** 2
        if between > best_var:
            best, best_var = level, between
    return best


def find_document_quad(grey: Image.Image) -> tuple[list[tuple[float, float]], float] | None:
    """Corners (tl, tr, br, bl, in full-image pixels) of a bright document on a darker surface.

    Returns ``(corners, coverage)`` where coverage is the share of the image the document takes,
    or ``None`` when the page is already a full sheet (no darker surround) or nothing stands out.
    """
    grey = to_grey(grey)
    scale = 240 / max(grey.size)
    if scale >= 1:
        scale = 1.0
    small = grey.resize((max(8, round(grey.width * scale)), max(8, round(grey.height * scale))), Image.BOX)
    small = small.filter(ImageFilter.GaussianBlur(1.2))
    threshold = _otsu(small.histogram())
    mask = small.point(lambda v, t=threshold: 255 if v > t else 0)
    w, h = mask.size
    # The surround must be dark and touch the border; a full white sheet has none.
    border = [mask.getpixel((x, y)) for x in range(0, w, max(1, w // 24)) for y in (0, h - 1)]
    border += [mask.getpixel((x, y)) for y in range(0, h, max(1, h // 24)) for x in (0, w - 1)]
    dark_border = sum(v == 0 for v in border) / max(1, len(border))
    if dark_border < 0.35:
        return None
    # Open the mask so a hand, a thread or glare joins nothing to the card.
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    pixels = mask.load()
    seen = bytearray(w * h)
    best_cells: list[int] = []
    for start in range(w * h):
        if seen[start] or not pixels[start % w, start // w]:
            continue
        stack, cells = [start], []
        seen[start] = 1
        while stack:
            cell = stack.pop()
            cells.append(cell)
            x, y = cell % w, cell // w
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    n = ny * w + nx
                    if not seen[n] and pixels[nx, ny]:
                        seen[n] = 1
                        stack.append(n)
        if len(cells) > len(best_cells):
            best_cells = cells
    coverage = len(best_cells) / (w * h)
    if coverage < 0.08 or coverage > 0.92:
        return None
    xs = [c % w for c in best_cells]
    ys = [c // w for c in best_cells]

    def extreme(key):
        best = min(range(len(xs)), key=lambda i: key(xs[i], ys[i]))
        return xs[best], ys[best]

    tl = extreme(lambda x, y: x + y)
    br = extreme(lambda x, y: -(x + y))
    tr = extreme(lambda x, y: y - x)
    bl = extreme(lambda x, y: x - y)
    inv = 1 / scale
    corners = [(tl[0] * inv, tl[1] * inv), (tr[0] * inv, tr[1] * inv), (br[0] * inv, br[1] * inv), (bl[0] * inv, bl[1] * inv)]
    return corners, coverage


def crop_to_document(image: Image.Image, quad: list[tuple[float, float]], pad: float = 0.01) -> Image.Image:
    """Cut the document out and straighten it (perspective) into an upright rectangle."""
    tl, tr, br, bl = quad
    cx = sum(p[0] for p in quad) / 4
    cy = sum(p[1] for p in quad) / 4

    def grow(p):  # move each corner slightly outwards so edge text is not clipped
        return (p[0] + (p[0] - cx) * pad, p[1] + (p[1] - cy) * pad)

    tl, tr, br, bl = (grow(p) for p in (tl, tr, br, bl))

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    width = int(max(dist(tl, tr), dist(bl, br)))
    height = int(max(dist(tl, bl), dist(tr, br)))
    if width < 100 or height < 100:
        return image.copy()
    # Image.transform(QUAD) takes the source corners as NW, SW, SE, NE.
    data = (*tl, *bl, *br, *tr)
    return image.transform((width, height), Image.QUAD, data, resample=Image.BICUBIC, fillcolor=255)


# ---------------------------------------------------------------------------------------------
# Coverage and photo measures (numbers only)
# ---------------------------------------------------------------------------------------------

def text_coverage(grey: Image.Image, boxes: list[tuple[float, float, float, float]]) -> float | None:
    """Share of the page's ink that lies inside the recognised word boxes (normalised 0..1 boxes).

    A page read at 20 words while carrying ink everywhere else was read badly, whatever the
    confidence of those 20 words. ``None`` when the page has too little ink to judge.
    """
    from PIL import ImageDraw

    small = _small(to_grey(grey), 500)
    ink = local_threshold(small, window=15, offset=25).point(lambda v: 255 - v)
    total = ink.histogram()[255]
    if total < small.width * small.height * 0.002:
        return None
    mask = Image.new("L", small.size, 0)
    draw = ImageDraw.Draw(mask)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0 * small.width - 2, y0 * small.height - 2, x1 * small.width + 2, y1 * small.height + 2), fill=255)
    inside = ImageChops.multiply(ink, mask).histogram()[255]
    return inside / total


def photo_metrics(grey: Image.Image) -> dict:
    """Shares of non-paper and of dark pixels on a small copy: a photo is big and dark, text is thin."""
    small = _small(to_grey(grey), 400)
    histogram = small.histogram()
    total = max(1, sum(histogram))
    running, paper = 0, 255
    for level, count in enumerate(histogram):
        running += count
        if running >= 0.9 * total:
            paper = level
            break
    cut = max(0, paper - 40)
    return {"paper": paper, "nonpaper": sum(histogram[:cut]) / total, "dark": sum(histogram[:min(cut, 90)]) / total}


def looks_like_photo(metrics: dict) -> bool:
    return metrics["dark"] >= 0.04 or metrics["nonpaper"] >= 0.30


# ---------------------------------------------------------------------------------------------
# OpenCV versions (used when opencv and numpy are installed; the Pillow functions above are the fallback)
# ---------------------------------------------------------------------------------------------

try:  # pragma: no cover - depends on the environment
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None
    np = None


def has_cv() -> bool:
    return cv2 is not None


def _arr(grey: Image.Image):
    return np.asarray(to_grey(grey))


def _img(array) -> Image.Image:
    return Image.fromarray(array)


def cv_clahe(grey: Image.Image, clip: float = 3.0, tiles: int = 8) -> Image.Image:
    """Contrast-limited adaptive histogram equalisation: faded and fax-grey print."""
    return _img(cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles)).apply(_arr(grey)))


def cv_adaptive(grey: Image.Image, block: int = 41, offset: int = 14) -> Image.Image:
    """Gaussian adaptive threshold: black ink on white paper whatever the lighting."""
    block |= 1
    return _img(cv2.adaptiveThreshold(cv2.medianBlur(_arr(grey), 3), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, block, offset))


def cv_flatten(grey: Image.Image, kernel: int = 31) -> Image.Image:
    """Divide out the background (morphological closing then blur): textured and unevenly lit pages."""
    array = _arr(grey)
    shape = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel | 1, kernel | 1))
    background = cv2.medianBlur(cv2.morphologyEx(array, cv2.MORPH_CLOSE, shape), (kernel | 1))
    return _img(cv2.normalize(cv2.divide(array, background, scale=255), None, 0, 255, cv2.NORM_MINMAX))


def cv_denoise(grey: Image.Image) -> Image.Image:
    return _img(cv2.bilateralFilter(_arr(grey), 5, 40, 5))


def cv_deskew_angle(grey: Image.Image) -> float:
    """Degrees to rotate (PIL convention, counter-clockwise) so text lines run level; 0 when unsure."""
    array = _arr(grey)
    scale = 900 / max(array.shape)
    small = cv2.resize(array, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else array
    _, ink = cv2.threshold(cv2.GaussianBlur(small, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 1)))
    lines = cv2.HoughLinesP(ink, 1, np.pi / 720, threshold=80, minLineLength=small.shape[1] // 6, maxLineGap=8)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = float(np.degrees(np.arctan2(y1 - y2, x2 - x1)))
        if abs(angle) <= 12:
            angles.append(angle)
    if len(angles) < 5:
        return 0.0
    median = float(np.median(angles))
    spread = float(np.percentile(np.abs(np.array(angles) - median), 75))
    return round(median, 1) if abs(median) >= 0.7 and spread < 1.5 else 0.0


def cv_document_quad(grey: Image.Image) -> tuple[list[tuple[float, float]], float] | None:
    """Corners (tl, tr, br, bl) of the largest document-like region on a darker surface, or ``None``."""
    array = _arr(grey)
    scale = 500 / max(array.shape)
    small = cv2.resize(array, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else array
    small = cv2.GaussianBlur(small, (7, 7), 0)
    height, width = small.shape
    border = np.concatenate([small[0], small[-1], small[:, 0], small[:, -1]])
    _, mask = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float((np.concatenate([mask[0], mask[-1], mask[:, 0], mask[:, -1]]) == 0).mean()) < 0.35:
        return None  # the border is paper: a full sheet, not a card on a table
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    coverage = cv2.contourArea(contour) / (width * height)
    if coverage < 0.08 or coverage > 0.92:
        return None
    hull = cv2.convexHull(contour)
    approx = cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True)
    points = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
    points = np.array(points, dtype="float32")
    total, difference = points.sum(axis=1), np.diff(points, axis=1).ravel()
    corners = [points[np.argmin(total)], points[np.argmin(difference)], points[np.argmax(total)], points[np.argmax(difference)]]
    inv = 1 / scale if scale < 1 else 1.0
    return [(float(x) * inv, float(y) * inv) for x, y in corners], float(coverage)


def cv_warp(image: Image.Image, quad: list[tuple[float, float]], pad: float = 0.01) -> Image.Image:
    """Perspective-correct the quadrilateral into an upright rectangle."""
    tl, tr, br, bl = (np.array(p, dtype="float32") for p in quad)
    centre = (tl + tr + br + bl) / 4
    tl, tr, br, bl = (p + (p - centre) * pad for p in (tl, tr, br, bl))
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if width < 100 or height < 100:
        return image.copy()
    matrix = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype="float32"),
                                         np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32"))
    source = np.asarray(image)
    warped = cv2.warpPerspective(source, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=255 if source.ndim == 2 else (255, 255, 255))
    return Image.fromarray(warped)
