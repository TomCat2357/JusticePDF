"""Acrobat手書き(Ink)注釈の表示/非表示トグルのテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.views.page_edit_window import ZoomPageLayout
from tests.helpers import create_page_edit_window, make_pdf, open_zoom


pytestmark = pytest.mark.usefixtures("qtbot")

INK_RECT = (40.0, 40.0, 90.0, 90.0)


def _add_ink_annot(pdf_path, page_num: int, rect: tuple[float, float, float, float] = INK_RECT) -> None:
    x0, y0, x1, y1 = rect
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_num]
        annot = page.add_ink_annot([[(x0, y0), (x1, y1), (x0, y1), (x1, y0)]])
        annot.set_colors(stroke=(1.0, 0.0, 0.0))
        annot.set_border(width=4)
        annot.update()
        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)


def _count_reddish_pixels(image, rect: tuple[float, float, float, float], zoom: float) -> int:
    x0, y0, x1, y1 = [int(round(v * zoom)) for v in rect]
    count = 0
    for x in range(max(x0, 0), min(x1, image.width())):
        for y in range(max(y0, 0), min(y1, image.height())):
            color = image.pixelColor(x, y)
            if color.red() > 180 and color.green() < 100 and color.blue() < 100:
                count += 1
    return count


def test_ink_visibility_default_shown(qtbot, tmp_path):
    pdf_path = tmp_path / "ink-default.pdf"
    make_pdf(pdf_path)
    _add_ink_annot(pdf_path, 0)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    # 既定は表示。
    assert window._show_ink_annots is True
    assert window._zoom_ink_visibility_btn.text() == "Acrobat手書きを表示"
    assert window._zoom_ink_visibility_btn.isChecked() is True
    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) > 0


def test_ink_visibility_toggle_hides_and_restores_in_single_page(qtbot, tmp_path):
    pdf_path = tmp_path / "ink-single.pdf"
    make_pdf(pdf_path)
    _add_ink_annot(pdf_path, 0)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_ink_visibility_btn.setChecked(False)
    assert window._show_ink_annots is False
    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) == 0

    window._zoom_ink_visibility_btn.setChecked(True)
    assert window._show_ink_annots is True
    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) > 0


def test_ink_visibility_persists_across_drawer_tab_switch(qtbot, tmp_path):
    """アノテーション/しおりドロワーの切替(タブ切替相当)で状態がリセットされない。"""
    pdf_path = tmp_path / "ink-tabs.pdf"
    make_pdf(pdf_path)
    _add_ink_annot(pdf_path, 0)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_ink_visibility_btn.setChecked(False)
    assert window._show_ink_annots is False

    # しおりドロワーを開く(アノテーションドロワーは排他で自動的に閉じる)。
    window._toggle_bookmarks_drawer()
    assert window._bookmarks_panel.is_open is True
    assert window._zoom_annotation_open is False
    assert window._show_ink_annots is False

    # アノテーションドロワーへ戻る。
    window._toggle_zoom_annotation_drawer()
    assert window._zoom_annotation_open is True
    assert window._show_ink_annots is False
    assert window._zoom_ink_visibility_btn.isChecked() is False

    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) == 0


def test_ink_visibility_hidden_in_multi_page_layout(qtbot, tmp_path):
    """複数ページ同時表示(見開き)でも非表示設定が反映される。"""
    pdf_path = tmp_path / "ink-spread.pdf"
    make_pdf(pdf_path, pages=2)
    _add_ink_annot(pdf_path, 0)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._zoom_ink_visibility_btn.setChecked(False)

    window._set_zoom_page_layout(ZoomPageLayout.HORIZONTAL)

    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) == 0

    window._zoom_ink_visibility_btn.setChecked(True)
    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) > 0


def test_ink_visibility_shown_in_multi_page_layout_by_default(qtbot, tmp_path):
    """既定(表示)のまま見開きへ切り替えても Ink は焼き込んで表示される。"""
    pdf_path = tmp_path / "ink-spread-default.pdf"
    make_pdf(pdf_path, pages=2)
    _add_ink_annot(pdf_path, 0)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._set_zoom_page_layout(ZoomPageLayout.HORIZONTAL)

    image = window._zoom_label._pixmap.toImage()
    assert _count_reddish_pixels(image, INK_RECT, window._zoom_factor) > 0
