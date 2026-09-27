"""拡大ビューの消しゴム: 塗りつぶし候補(pii_entity 付き)を消去対象から除外することの確認。

架空のテストPDF・架空の pii_entity ラベル("TEST_ENTITY")のみを使用し、
実データは含まない。
"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import Qt

from src.utils.pdf_utils import (
    MarkupType,
    TextMarkupAnnotData,
    create_markup_annot,
    list_markup_annots,
)
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
    label = window._zoom_label
    text = "".join(ch["c"] for ch in label._chars)
    start = text.index(substring)
    return list(range(start, start + len(substring)))


def _select_chars(window, indices: list[int]) -> None:
    window._zoom_label._selected_char_indices = list(indices)


def _create_pii_candidate(window, pdf_path, substring: str) -> tuple:
    """substring の範囲に架空の塗りつぶし候補(pii_entity付き)を直接作成する。"""
    idx = _char_indices_for_substring(window, substring)
    _select_chars(window, idx)
    quads = window._zoom_label.selected_markup_quads()
    assert quads
    candidate = TextMarkupAnnotData(
        page_num=0,
        xref=0,
        quads=tuple(quads),
        markup_type=MarkupType.HIGHLIGHT,
        color=(1.0, 0.8, 0.2),
        opacity=0.4,
        pii_entity="TEST_ENTITY",
    )
    saved = create_markup_annot(str(pdf_path), candidate)
    window._refresh_current_zoom_page()
    return idx, saved


@pytest.mark.usefixtures("qtbot")
def test_eraser_selection_skips_pii_candidate_but_erases_overlapping_marker(qtbot, tmp_path):
    """1回の選択消しゴムで、重なる塗りつぶし候補は変化せず、通常マーカーだけ縮む。"""
    pdf_path = tmp_path / "eraser-pii-skip-selection.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    markup_idx, _saved = _create_pii_candidate(window, pdf_path, "markup")

    # 通常のマーカーを全文に付ける(塗りつぶし候補と範囲が重なる)。
    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.HIGHLIGHT], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 2)

    before = list_markup_annots(str(pdf_path), 0)
    pii_before = next(a for a in before if a.pii_entity)
    assert len(pii_before.quads) == 1

    # "markup" の範囲を選んで消しゴムを一発適用する(連続モードOFFの単発動作)。
    _select_chars(window, markup_idx)
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)

    def _normal_marker_shrunk() -> bool:
        annots = list_markup_annots(str(pdf_path), 0)
        normal = [a for a in annots if not a.pii_entity]
        return len(normal) == 1 and len(normal[0].quads) == 2

    qtbot.waitUntil(_normal_marker_shrunk)

    after = list_markup_annots(str(pdf_path), 0)
    pii_after = [a for a in after if a.pii_entity]
    # 塗りつぶし候補は縮小・分割・削除されず、そのまま残る。
    assert len(pii_after) == 1
    assert pii_after[0].quads == pii_before.quads
    assert pii_after[0].pii_entity == "TEST_ENTITY"
    assert pii_after[0].markup_type == MarkupType.HIGHLIGHT


@pytest.mark.usefixtures("qtbot")
def test_continuous_eraser_skips_pii_candidate(qtbot, tmp_path):
    """連続モードの消しゴムでも、塗りつぶし候補は消去対象から除外される。"""
    pdf_path = tmp_path / "eraser-pii-skip-continuous.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    markup_idx, _saved = _create_pii_candidate(window, pdf_path, "markup")

    _select_all_chars(window)
    qtbot.mouseClick(window._markup_buttons[MarkupType.UNDERLINE], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf_path), 0)) == 2)

    # 「連続」トグルON、消しゴムを装着してから "markup" 範囲を選んで確定する。
    qtbot.mouseClick(window._markup_continuous_btn, Qt.MouseButton.LeftButton)
    assert window._markup_continuous_mode is True
    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)

    _select_chars(window, markup_idx)
    window._on_zoom_text_selection_released()

    def _normal_marker_shrunk() -> bool:
        annots = list_markup_annots(str(pdf_path), 0)
        normal = [a for a in annots if not a.pii_entity]
        return len(normal) == 1 and len(normal[0].quads) == 2

    qtbot.waitUntil(_normal_marker_shrunk)

    after = list_markup_annots(str(pdf_path), 0)
    pii_after = [a for a in after if a.pii_entity]
    assert len(pii_after) == 1
    assert pii_after[0].pii_entity == "TEST_ENTITY"


@pytest.mark.usefixtures("qtbot")
def test_eraser_whole_annot_fallback_does_not_delete_pii_candidate(qtbot, tmp_path):
    """テキスト未選択・注釈選択中の「注釈ごと削除」フォールバックも、
    塗りつぶし候補には適用されない(削除されない)。
    """
    pdf_path = tmp_path / "eraser-pii-skip-fallback.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _create_pii_candidate(window, pdf_path, "markup")
    before = list_markup_annots(str(pdf_path), 0)
    assert len(before) == 1

    pii_annot = before[0]
    window._set_selected_zoom_annotation(pii_annot)
    window._zoom_label._selected_char_indices = []

    qtbot.mouseClick(window._eraser_btn, Qt.MouseButton.LeftButton)
    qtbot.wait(50)

    after = list_markup_annots(str(pdf_path), 0)
    assert len(after) == 1
    assert after[0].pii_entity == "TEST_ENTITY"
    assert after[0].quads == pii_annot.quads


@pytest.mark.usefixtures("qtbot")
def test_normal_marker_eraser_still_works_without_pii_candidates(qtbot, tmp_path):
    """回帰確認: 塗りつぶし候補が存在しない通常のケースでは、消しゴムは従来通り動作する。"""
    pdf_path = tmp_path / "eraser-normal-regression.pdf"
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
    assert remaining.pii_entity == ""
