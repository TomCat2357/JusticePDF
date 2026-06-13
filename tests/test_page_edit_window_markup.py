"""ズームビューのテキストマークアップ（ハイライト/下線/取り消し線）UI のテスト。"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from src.utils.pdf_utils import (
    MarkupType,
    list_markup_annots,
)
from src.views import page_edit_window as page_edit_window_module
from tests.helpers import create_page_edit_window, open_zoom


def _make_text_pdf(path, *, width: int = 320, height: int = 420) -> None:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((40, 60), "Hello markup world", fontsize=18)
    doc.save(str(path))
    doc.close()


def _select_all_words(window) -> None:
    label = window._zoom_label
    label._selected_word_indices = list(range(len(label._words)))


@pytest.mark.usefixtures("qtbot")
def test_markup_button_creates_highlight_from_selection(qtbot, tmp_path):
    pdf_path = tmp_path / "markup-ui.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    assert len(window._zoom_label._words) > 0
    _select_all_words(window)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    markup = list_markup_annots(str(pdf_path), 0)[0]
    assert markup.markup_type == MarkupType.HIGHLIGHT
    assert len(markup.quads) == len(window._zoom_label._words)


@pytest.mark.usefixtures("qtbot")
def test_markup_button_without_selection_does_not_create(qtbot, tmp_path):
    pdf_path = tmp_path / "markup-none.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_label._selected_word_indices = []
    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    qtbot.wait(50)
    assert list_markup_annots(str(pdf_path), 0) == []


@pytest.mark.usefixtures("qtbot")
def test_markup_color_change_applies_to_selected(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "markup-color.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _select_all_words(window)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    created = list_markup_annots(str(pdf_path), 0)[0]
    # 作成直後は自動選択される
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)

    monkeypatch.setattr(
        page_edit_window_module.QColorDialog,
        "getColor",
        staticmethod(lambda *a, **k: QColor(0, 120, 255)),
    )
    qtbot.mouseClick(window._zoom_markup_color_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: list_markup_annots(str(pdf_path), 0)[0].color == pytest.approx((0.0, 120 / 255, 1.0), abs=0.02)
    )


@pytest.mark.usefixtures("qtbot")
def test_markup_type_switch_changes_existing(qtbot, tmp_path):
    pdf_path = tmp_path / "markup-switch.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _select_all_words(window)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)

    # 選択中のマークアップの種類を取り消し線へ変更
    qtbot.mouseClick(window._markup_buttons[MarkupType.STRIKEOUT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: list_markup_annots(str(pdf_path), 0)[0].markup_type == MarkupType.STRIKEOUT
    )
    # 数は増えない（差し替え）
    assert len(list_markup_annots(str(pdf_path), 0)) == 1


@pytest.mark.usefixtures("qtbot")
def test_markup_delete_via_button(qtbot, tmp_path):
    pdf_path = tmp_path / "markup-delete.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _select_all_words(window)

    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)

    window._delete_selected_zoom_annotation()
    qtbot.waitUntil(lambda: list_markup_annots(str(pdf_path), 0) == [])
