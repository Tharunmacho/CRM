"""The OpenCV deskew angle follows its contract: rotating the page by it (PIL, counter-clockwise) levels the lines."""
import pytest
from PIL import Image

from app.integrations.ocr import page_enhance as enhance
from ocr_synthetic import passport_lines, render_lines

pytestmark = pytest.mark.skipif(not enhance.has_cv(), reason="OpenCV is not installed")

_TEXT = [f"Name of the applicant and some more words to fill line {i} of this page" for i in range(40)]


def _tilted(lines, degrees):
    page = render_lines(lines, dpi=200)
    return page.rotate(degrees, resample=Image.BICUBIC, fillcolor=255) if degrees else page


@pytest.mark.parametrize("lines", [_TEXT, passport_lines(1)], ids=["text", "passport"])
@pytest.mark.parametrize("tilt", [5, -5, 3, -3])
def test_rotating_by_the_angle_levels_the_page(lines, tilt):
    tilted = _tilted(lines, tilt)
    angle = enhance.cv_deskew_angle(tilted)
    assert angle * tilt < 0, "the angle must undo the tilt, not double it"
    assert abs(angle + tilt) <= 1.0
    level = tilted.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    assert abs(enhance.cv_deskew_angle(level)) <= 1.0


def test_a_straight_page_is_left_alone():
    assert enhance.cv_deskew_angle(_tilted(_TEXT, 0)) == 0.0
