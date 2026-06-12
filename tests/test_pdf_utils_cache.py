from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.utils.pdf_utils import clear_pixmap_cache_for_path, get_pdf_card_info
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def _center_rgb(pixmap) -> tuple[int, int, int]:
    image = pixmap.toImage()
    color = image.pixelColor(image.width() // 2, image.height() // 2)
    return (color.red(), color.green(), color.blue())


def test_get_pdf_card_info_refreshes_when_same_path_is_replaced_with_same_mtime(tmp_path):
    pdf_path = tmp_path / "same-name.pdf"
    replacement_path = tmp_path / "replacement.pdf"
    fixed_ns = 1_700_000_000_000_000_000

    make_pdf(pdf_path, pages=1, width=120, height=120, fill=(1.0, 0.0, 0.0))
    os.utime(pdf_path, ns=(fixed_ns, fixed_ns))
    first_pixmap, first_page_count = get_pdf_card_info(str(pdf_path), size=64)

    make_pdf(replacement_path, pages=2, width=120, height=120, fill=(0.0, 0.0, 1.0))
    os.utime(replacement_path, ns=(fixed_ns, fixed_ns))
    os.replace(replacement_path, pdf_path)
    os.utime(pdf_path, ns=(fixed_ns, fixed_ns))

    second_pixmap, second_page_count = get_pdf_card_info(str(pdf_path), size=64)

    assert first_page_count == 1
    assert second_page_count == 2
    assert _center_rgb(first_pixmap)[0] > _center_rgb(first_pixmap)[2]
    assert _center_rgb(second_pixmap)[2] > _center_rgb(second_pixmap)[0]

    clear_pixmap_cache_for_path(str(pdf_path))
