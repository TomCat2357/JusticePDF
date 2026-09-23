"""ズームビューのテキストマークアップ（ハイライト/下線/取り消し線）UI のテスト。"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from src.utils.pdf_utils import (
    MarkupType,
    TextMarkupAnnotData,
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
    label._selected_char_indices = list(range(len(label._chars)))


def _char_widget_pos(window, idx: int) -> QPoint:
    """Widget-space point at the center of char `idx` on the zoom label."""
    label = window._zoom_label
    offset = label._pixmap_offset()
    center = label._char_rects[idx].center()
    return QPoint(int(offset.x() + center.x()), int(offset.y() + center.y()))


def _char_index_for_substring(window, substring: str) -> int:
    """substring の先頭に対応する _chars のインデックスを返す。"""
    label = window._zoom_label
    text = "".join(ch["c"] for ch in label._chars)
    return text.index(substring)


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
    # Selection now merges per line into one clean bar; this text is a single line.
    distinct_lines = len({ch["line_id"] for ch in window._zoom_label._chars})
    assert len(markup.quads) == distinct_lines == 1
    # The single quad spans the full width of the selected words.
    qx0, _qy0, qx1, _qy1 = markup.quads[0]
    word_x0 = min(w[0] for w in window._zoom_label._words)
    word_x1 = max(w[2] for w in window._zoom_label._words)
    assert qx0 == pytest.approx(word_x0, abs=1.0)
    assert qx1 == pytest.approx(word_x1, abs=1.0)


@pytest.mark.usefixtures("qtbot")
def test_markup_button_without_selection_does_not_create(qtbot, tmp_path):
    pdf_path = tmp_path / "markup-none.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_label._selected_char_indices = []
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


# ---------------------------------------------------------------------------
# 通常モード: 既存マークアップの上でのクリック/ドラッグの挙動
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_click_on_markup_selects_annotation_not_word(qtbot, tmp_path):
    """マークアップされた文字の上をドラッグなしでクリックすると、そのマークアップ
    注釈が選択される(単語選択にはならない)。"""
    pdf_path = tmp_path / "markup-click-select.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    label = window._zoom_label

    markup_idx = _char_index_for_substring(window, "markup")
    label._selected_char_indices = list(range(markup_idx, markup_idx + len("markup")))
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    label.clear_text_selection()
    window._set_selected_zoom_annotation(None)

    pos = _char_widget_pos(window, markup_idx + 1)
    qtbot.mousePress(label, Qt.MouseButton.LeftButton, pos=pos)
    qtbot.mouseRelease(label, Qt.MouseButton.LeftButton, pos=pos)

    assert isinstance(window._selected_zoom_annotation, TextMarkupAnnotData)
    assert window._selected_zoom_annotation.markup_type == MarkupType.HIGHLIGHT
    assert label._selected_char_indices == []


@pytest.mark.usefixtures("qtbot")
def test_drag_over_markup_selects_text_not_annotation(qtbot, tmp_path):
    """マークアップされた文字の上からドラッグすると、その下のテキストが選択され、
    マークアップ注釈自体は選択されない。"""
    pdf_path = tmp_path / "markup-drag-select-text.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    label = window._zoom_label

    markup_idx = _char_index_for_substring(window, "markup")
    label._selected_char_indices = list(range(markup_idx, markup_idx + len("markup")))
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    label.clear_text_selection()
    window._set_selected_zoom_annotation(None)

    start_pos = _char_widget_pos(window, markup_idx)
    end_pos = _char_widget_pos(window, markup_idx + len("markup") - 1)
    # 確実にドラッグとして扱われるよう、開始位置からしきい値以上動かす。
    end_pos = QPoint(end_pos.x() + QApplication.startDragDistance() + 2, end_pos.y())

    qtbot.mousePress(label, Qt.MouseButton.LeftButton, pos=start_pos)
    qtbot.mouseMove(label, end_pos)
    qtbot.mouseRelease(label, Qt.MouseButton.LeftButton, pos=end_pos)

    assert label._selected_char_indices != []
    assert window._selected_zoom_annotation is None
