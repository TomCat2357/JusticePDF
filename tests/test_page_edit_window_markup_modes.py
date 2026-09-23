"""拡大ビューのマークアップ連続モード(sticky mode)と消しゴムのテスト。"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import Qt

from src.utils.pdf_utils import (
    MarkupType,
    list_markup_annots,
)
from src.views.page_edit_annotations import CreateMode
from tests.helpers import create_page_edit_window, open_zoom


def _make_text_pdf(path, *, width: int = 320, height: int = 420) -> None:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((40, 60), "Hello markup world", fontsize=18)
    doc.save(str(path))
    doc.close()


def _select_all_chars(window) -> None:
    label = window._zoom_label
    label._selected_char_indices = list(range(len(label._chars)))


def _char_indices_for_substring(window, substring: str) -> list[int]:
    """全文中の substring に対応する _chars のインデックス一覧を返す(最初の出現)。"""
    label = window._zoom_label
    text = "".join(ch["c"] for ch in label._chars)
    start = text.index(substring)
    return list(range(start, start + len(substring)))


def _select_chars(window, indices: list[int]) -> None:
    window._zoom_label._selected_char_indices = list(indices)


@pytest.mark.usefixtures("qtbot")
def test_markup_button_enters_and_leaves_sticky_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-toggle.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    btn = window._markup_buttons[MarkupType.HIGHLIGHT]
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP
    assert window._markup_sticky_type == MarkupType.HIGHLIGHT
    assert btn.isChecked() is True
    assert window._zoom_label._text_select_only_mode is True

    # 同じボタンをもう一度押すと解除
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.NONE
    assert window._markup_sticky_type is None
    assert btn.isChecked() is False
    assert window._zoom_label._text_select_only_mode is False


@pytest.mark.usefixtures("qtbot")
def test_markup_sticky_mode_exits_on_escape(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-escape.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_Escape)
    assert window._create_mode is CreateMode.NONE
    assert window._markup_buttons[MarkupType.UNDERLINE].isChecked() is False


@pytest.mark.usefixtures("qtbot")
def test_markup_sticky_mode_mutually_exclusive_with_note_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-exclusive.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP

    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.NOTE
    assert window._markup_buttons[MarkupType.HIGHLIGHT].isChecked() is False
    assert window._markup_sticky_type is None
    assert window._zoom_label._text_select_only_mode is False

    # 逆方向: ノートモード中にマークアップボタンを押すとノートモードが解除される
    qtbot.mouseClick(window._markup_buttons[MarkupType.STRIKEOUT], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP
    assert window._note_create_mode is False


@pytest.mark.usefixtures("qtbot")
def test_markup_sticky_mode_applies_repeatedly(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-apply.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP

    hello_idx = _char_indices_for_substring(window, "Hello")
    _select_chars(window, hello_idx)
    window._on_zoom_text_selection_released()
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    # 連続モードは維持される
    assert window._create_mode is CreateMode.MARKUP
    assert window._markup_sticky_type == MarkupType.HIGHLIGHT

    world_idx = _char_indices_for_substring(window, "world")
    _select_chars(window, world_idx)
    window._on_zoom_text_selection_released()
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 2)
    assert window._create_mode is CreateMode.MARKUP


@pytest.mark.usefixtures("qtbot")
def test_markup_sticky_mode_skips_exact_duplicate(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-dup.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    hello_idx = _char_indices_for_substring(window, "Hello")
    _select_chars(window, hello_idx)
    window._on_zoom_text_selection_released()
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    # 同じ範囲へもう一度適用しても増えない
    _select_chars(window, hello_idx)
    window._on_zoom_text_selection_released()
    qtbot.wait(50)
    assert len(list_markup_annots(str(pdf_path), 0)) == 1


@pytest.mark.usefixtures("qtbot")
def test_eraser_partial_erase_splits_remaining_quads(qtbot, tmp_path):
    pdf_path = tmp_path / "eraser-partial.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    # 全文をハイライト
    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    # 消しゴムモードへ入り、中央の "markup" だけを選択して消す
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.ERASER

    markup_idx = _char_indices_for_substring(window, "markup")
    _select_chars(window, markup_idx)
    window._on_zoom_text_selection_released()

    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    remaining = list_markup_annots(str(pdf_path), 0)[0]
    # "Hello " と " world" の2つの残り区間に分かれる
    assert len(remaining.quads) == 2
    assert window._create_mode is CreateMode.ERASER  # 連続モードは維持


@pytest.mark.usefixtures("qtbot")
def test_eraser_full_erase_deletes_annotation(qtbot, tmp_path):
    pdf_path = tmp_path / "eraser-full.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
    _select_all_chars(window)
    window._on_zoom_text_selection_released()

    qtbot.waitUntil(lambda: list_markup_annots(str(pdf_path), 0) == [])


@pytest.mark.usefixtures("qtbot")
def test_eraser_erase_is_single_undo_step(qtbot, tmp_path):
    pdf_path = tmp_path / "eraser-undo.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
    markup_idx = _char_indices_for_substring(window, "markup")
    _select_chars(window, markup_idx)
    window._on_zoom_text_selection_released()
    qtbot.waitUntil(
        lambda: len(list_markup_annots(str(pdf_path), 0)) == 1
        and len(list_markup_annots(str(pdf_path), 0)[0].quads) == 2
    )

    # 1回のUndoで元のハイライト(単一quad)へ戻る
    window._undo_manager.undo()
    qtbot.waitUntil(
        lambda: len(list_markup_annots(str(pdf_path), 0)) == 1
        and len(list_markup_annots(str(pdf_path), 0)[0].quads) == 1
    )


def test_quads_for_char_indices_splits_on_gap_and_line(qtbot, tmp_path):
    """_quads_for_char_indices: 同じ行でも隙間があれば別quadに分かれる純粋ロジックのテスト。"""
    pdf_path = tmp_path / "quads-helper.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    label = window._zoom_label

    all_idx = list(range(len(label._chars)))
    # 連続範囲はひとつの quad にまとまる
    assert len(label._quads_for_char_indices(all_idx)) == 1

    markup_idx = set(_char_indices_for_substring(window, "markup"))
    remaining = set(all_idx) - markup_idx
    quads = label._quads_for_char_indices(remaining)
    # 中抜きなので左右2本に分かれる
    assert len(quads) == 2

    assert label._quads_for_char_indices([]) == []
