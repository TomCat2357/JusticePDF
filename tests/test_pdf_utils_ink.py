"""Ink(手書き)注釈の xref 列挙ユーティリティのテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    get_page_thumbnail,
    list_ink_annot_xrefs,
    list_ink_annot_xrefs_by_page,
    render_page_thumbnails_batch,
)
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def _count_reddish_pixels_in_pixmap(pixmap) -> int:
    image = pixmap.toImage()
    count = 0
    for x in range(image.width()):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if color.red() > 180 and color.green() < 100 and color.blue() < 100:
                count += 1
    return count


def test_list_ink_annot_xrefs_returns_only_ink(tmp_path):
    pdf_path = tmp_path / "ink-list.pdf"
    make_pdf(pdf_path, pages=2)

    with fitz.open(str(pdf_path)) as doc:
        page0 = doc[0]
        ink_annot = page0.add_ink_annot([[(10, 10), (50, 50)]])
        ink_annot.update()
        ink_xref = ink_annot.xref

        rect_annot = page0.add_rect_annot(fitz.Rect(60, 60, 100, 100))
        rect_annot.update()

        page1 = doc[1]
        ink_annot2 = page1.add_ink_annot([[(20, 20), (60, 60)]])
        ink_annot2.update()
        ink_xref2 = ink_annot2.xref

        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    # ページ指定: そのページの Ink だけを返す(非 Ink は含まない)。
    assert list_ink_annot_xrefs(str(pdf_path), 0) == [ink_xref]
    assert list_ink_annot_xrefs(str(pdf_path), 1) == [ink_xref2]

    # ページ省略: 文書全体の Ink をまとめて返す。
    assert set(list_ink_annot_xrefs(str(pdf_path))) == {ink_xref, ink_xref2}


def test_list_ink_annot_xrefs_empty_when_no_ink(tmp_path):
    pdf_path = tmp_path / "ink-empty.pdf"
    make_pdf(pdf_path)

    with fitz.open(str(pdf_path)) as doc:
        page = doc[0]
        rect_annot = page.add_rect_annot(fitz.Rect(10, 10, 50, 50))
        rect_annot.update()
        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    assert list_ink_annot_xrefs(str(pdf_path), 0) == []


def test_list_ink_annot_xrefs_missing_file_returns_empty() -> None:
    assert list_ink_annot_xrefs("/nonexistent/path/does-not-exist.pdf") == []


def test_list_ink_annot_xrefs_by_page_groups_per_page(tmp_path):
    pdf_path = tmp_path / "ink-by-page.pdf"
    make_pdf(pdf_path, pages=3)

    with fitz.open(str(pdf_path)) as doc:
        page0 = doc[0]
        ink0 = page0.add_ink_annot([[(10, 10), (50, 50)]])
        ink0.update()
        xref0 = ink0.xref

        # 1ページ目(index 1)は Ink 無し。
        page2 = doc[2]
        ink2a = page2.add_ink_annot([[(10, 10), (50, 50)]])
        ink2a.update()
        ink2b = page2.add_ink_annot([[(20, 20), (60, 60)]])
        ink2b.update()
        xref2a, xref2b = ink2a.xref, ink2b.xref

        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    result = list_ink_annot_xrefs_by_page(str(pdf_path))
    assert result == {0: [xref0], 2: [xref2a, xref2b]}
    assert 1 not in result


def test_list_ink_annot_xrefs_by_page_missing_file_returns_empty() -> None:
    assert list_ink_annot_xrefs_by_page("/nonexistent/path/does-not-exist.pdf") == {}


def test_get_page_thumbnail_hides_specified_xref(tmp_path):
    pdf_path = tmp_path / "ink-thumb.pdf"
    make_pdf(pdf_path)

    with fitz.open(str(pdf_path)) as doc:
        page = doc[0]
        annot = page.add_ink_annot([[(10, 10), (90, 90), (10, 90), (90, 10)]])
        annot.set_colors(stroke=(1.0, 0.0, 0.0))
        annot.set_border(width=6)
        annot.update()
        ink_xref = annot.xref
        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    shown = get_page_thumbnail(str(pdf_path), 0, size=128)
    hidden = get_page_thumbnail(str(pdf_path), 0, size=128, hide_xrefs={ink_xref})

    assert _count_reddish_pixels_in_pixmap(shown) > 0
    assert _count_reddish_pixels_in_pixmap(hidden) == 0


def test_render_page_thumbnails_batch_hides_xrefs_per_page(tmp_path):
    pdf_path = tmp_path / "ink-batch.pdf"
    make_pdf(pdf_path, pages=2)

    with fitz.open(str(pdf_path)) as doc:
        page0 = doc[0]
        annot0 = page0.add_ink_annot([[(10, 10), (90, 90), (10, 90), (90, 10)]])
        annot0.set_colors(stroke=(1.0, 0.0, 0.0))
        annot0.set_border(width=6)
        annot0.update()
        xref0 = annot0.xref

        page1 = doc[1]
        annot1 = page1.add_ink_annot([[(10, 10), (90, 90), (10, 90), (90, 10)]])
        annot1.set_colors(stroke=(1.0, 0.0, 0.0))
        annot1.set_border(width=6)
        annot1.update()

        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    # ページ0だけ隠す指定。ページ1は指定に含まれないので表示されたまま。
    result = render_page_thumbnails_batch(
        str(pdf_path), [0, 1], size=128, hide_xrefs={0: [xref0]}
    )

    assert _count_reddish_pixels_in_pixmap(result[0]) == 0
    assert _count_reddish_pixels_in_pixmap(result[1]) > 0
