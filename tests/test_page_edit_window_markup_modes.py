"""拡大ビューの文字装飾: 「連続」トグルによる sticky モードと消しゴムのテスト。"""

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


def _enable_continuous(window, qtbot) -> None:
    assert window._markup_continuous_btn.isChecked() is False
    qtbot.mouseClick(window._markup_continuous_btn, Qt.MouseButton.LeftButton)
    assert window._markup_continuous_mode is True


# ---------------------------------------------------------------------------
# 通常モード（連続 OFF、既定）: e4c00ce 時点の挙動 + 消しゴムの単発動作
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_normal_mode_button_without_selection_shows_hint_and_stays_normal(qtbot, tmp_path):
    """回帰テスト: 未選択でボタンを押しても連続モードへ入らない(box-drag化しない)。"""
    pdf_path = tmp_path / "normal-hint.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_label._selected_char_indices = []
    btn = window._markup_buttons[MarkupType.HIGHLIGHT]
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    qtbot.wait(50)

    assert list_markup_annots(str(pdf_path), 0) == []
    assert window._create_mode is CreateMode.NONE
    assert window._markup_sticky_type is None
    assert btn.isChecked() is False
    # 連続モードでないので、既存注釈へのヒットテスト/ドラッグ選択は抑止されない。
    assert window._zoom_label._text_select_only_mode is False


@pytest.mark.usefixtures("qtbot")
def test_normal_mode_markup_button_not_left_checked_after_apply(qtbot, tmp_path):
    pdf_path = tmp_path / "normal-oneshot.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _select_all_chars(window)

    btn = window._markup_buttons[MarkupType.HIGHLIGHT]
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    assert btn.isChecked() is False
    assert window._create_mode is CreateMode.NONE
    assert window._zoom_label._text_select_only_mode is False


@pytest.mark.usefixtures("qtbot")
def test_normal_mode_eraser_partial_erase_with_selection(qtbot, tmp_path):
    pdf_path = tmp_path / "normal-eraser-partial.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    markup_idx = _char_indices_for_substring(window, "markup")
    _select_chars(window, markup_idx)
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    remaining = list_markup_annots(str(pdf_path), 0)[0]
    assert len(remaining.quads) == 2
    assert window._eraser_btn.isChecked() is False
    assert window._create_mode is CreateMode.NONE
    assert window._zoom_label._text_select_only_mode is False


@pytest.mark.usefixtures("qtbot")
def test_normal_mode_eraser_without_selection_deletes_selected_annot(qtbot, tmp_path):
    pdf_path = tmp_path / "normal-eraser-delete.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)

    window._zoom_label._selected_char_indices = []
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: list_markup_annots(str(pdf_path), 0) == [])
    assert window._eraser_btn.isChecked() is False


@pytest.mark.usefixtures("qtbot")
def test_normal_mode_eraser_without_selection_or_annot_shows_hint(qtbot, tmp_path):
    pdf_path = tmp_path / "normal-eraser-hint.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_label._selected_char_indices = []
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
    qtbot.wait(50)

    assert list_markup_annots(str(pdf_path), 0) == []
    assert window._eraser_btn.isChecked() is False
    assert window._create_mode is CreateMode.NONE


# ---------------------------------------------------------------------------
# 連続モード（トグル ON）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_continuous_markup_button_enters_and_leaves_sticky_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-toggle.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

    btn = window._markup_buttons[MarkupType.HIGHLIGHT]
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP
    assert window._markup_sticky_type == MarkupType.HIGHLIGHT
    assert btn.isChecked() is True
    assert window._zoom_label._text_select_only_mode is True

    # 同じボタンをもう一度押すと解除(連続モード自体は ON のまま)
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.NONE
    assert window._markup_sticky_type is None
    assert btn.isChecked() is False
    assert window._zoom_label._text_select_only_mode is False
    assert window._markup_continuous_btn.isChecked() is True


@pytest.mark.usefixtures("qtbot")
def test_continuous_sticky_mode_exits_on_escape_keeps_continuous_on(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-escape.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_Escape)
    assert window._create_mode is CreateMode.NONE
    assert window._markup_buttons[MarkupType.UNDERLINE].isChecked() is False
    # Esc はツールだけを解除し、「連続」トグル自体は ON のまま維持する。
    assert window._markup_continuous_btn.isChecked() is True
    assert window._markup_continuous_mode is True


@pytest.mark.usefixtures("qtbot")
def test_continuous_off_deactivates_active_sticky_tool(qtbot, tmp_path):
    pdf_path = tmp_path / "continuous-off.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP

    qtbot.mouseClick(window._markup_continuous_btn, Qt.MouseButton.LeftButton)
    assert window._markup_continuous_mode is False
    assert window._create_mode is CreateMode.NONE
    assert window._markup_buttons[MarkupType.HIGHLIGHT].isChecked() is False
    assert window._zoom_label._text_select_only_mode is False


@pytest.mark.usefixtures("qtbot")
def test_continuous_sticky_mode_mutually_exclusive_with_note_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-exclusive.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP

    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.NOTE
    assert window._markup_buttons[MarkupType.HIGHLIGHT].isChecked() is False
    assert window._markup_sticky_type is None
    assert window._zoom_label._text_select_only_mode is False
    # 「連続」トグル自体はモード切替の影響を受けない。
    assert window._markup_continuous_btn.isChecked() is True

    # 逆方向: ノートモード中にマークアップボタンを押すとノートモードが解除される
    qtbot.mouseClick(window._markup_buttons[MarkupType.STRIKEOUT], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.MARKUP
    assert window._note_create_mode is False


@pytest.mark.usefixtures("qtbot")
def test_continuous_sticky_mode_applies_repeatedly(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-apply.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

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
def test_continuous_sticky_mode_does_not_apply_to_existing_selection(qtbot, tmp_path):
    """連続モードでツールを選んでも、選んだ時点の既存テキスト選択には適用しない。

    選択はクリアされ、ツールが armed になるだけ。適用は装着後の新しい
    ドラッグ確定(text_selection_released)でのみ行われる。
    """
    pdf_path = tmp_path / "sticky-no-immediate.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

    hello_idx = _char_indices_for_substring(window, "Hello")
    _select_chars(window, hello_idx)

    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.wait(50)
    assert list_markup_annots(str(pdf_path), 0) == []
    assert window._create_mode is CreateMode.MARKUP
    assert window._markup_sticky_type == MarkupType.HIGHLIGHT
    # ツール装着時に既存選択はクリアされる。
    assert window._zoom_label._selected_char_indices == []

    # 装着後にあらためて選択し、ドラッグ確定すれば適用される。
    _select_chars(window, hello_idx)
    window._on_zoom_text_selection_released()
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)


@pytest.mark.usefixtures("qtbot")
def test_enabling_continuous_clears_existing_selection(qtbot, tmp_path):
    """「連続」トグルを ON にした瞬間、既存のテキスト選択を解除する。"""
    pdf_path = tmp_path / "sticky-toggle-clears.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    hello_idx = _char_indices_for_substring(window, "Hello")
    _select_chars(window, hello_idx)
    assert window._zoom_label._selected_char_indices != []

    _enable_continuous(window, qtbot)
    assert window._zoom_label._selected_char_indices == []


@pytest.mark.usefixtures("qtbot")
def test_continuous_sticky_mode_skips_exact_duplicate(qtbot, tmp_path):
    pdf_path = tmp_path / "sticky-dup.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _enable_continuous(window, qtbot)

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
def test_continuous_eraser_partial_erase_splits_remaining_quads(qtbot, tmp_path):
    pdf_path = tmp_path / "eraser-partial.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    # 全文をハイライト(通常モードで一発適用)
    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    _enable_continuous(window, qtbot)

    # 消しゴムの連続モードへ入り、中央の "markup" だけを選択して消す
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
def test_continuous_eraser_full_erase_deletes_annotation(qtbot, tmp_path):
    pdf_path = tmp_path / "eraser-full.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    _enable_continuous(window, qtbot)
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
    _select_all_chars(window)
    window._on_zoom_text_selection_released()

    qtbot.waitUntil(lambda: list_markup_annots(str(pdf_path), 0) == [])


@pytest.mark.usefixtures("qtbot")
def test_eraser_erase_is_single_undo_step(qtbot, tmp_path):
    """単発動作(消しゴムボタンの一発適用)でも Undo は1ステップにまとまる。"""
    pdf_path = tmp_path / "eraser-undo.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 1)

    markup_idx = _char_indices_for_substring(window, "markup")
    _select_chars(window, markup_idx)
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
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
