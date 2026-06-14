"""拡大モードの見開き表示(閲覧専用・左→右)のテスト。"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from src.utils.pdf_utils import FreeTextAnnotData, create_freetext_annot
from tests.helpers import (
    create_page_edit_window,
    make_pdf,
    open_zoom,
    page_click_pos,
)


@pytest.mark.usefixtures("qtbot")
def test_spread_toggle_sets_view_only_and_label(qtbot, tmp_path):
    pdf_path = tmp_path / "spread.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    assert window._zoom_spread_mode is False
    assert window._zoom_page_label.text() == "1 / 4"

    window._toggle_zoom_spread_view()

    assert window._zoom_spread_mode is True
    assert window._zoom_label._view_only is True
    assert window._zoom_spread_btn.isChecked() is True
    # 見開き中はアノテーション(付箋)ドロワーを無効化する。
    assert window._zoom_object_btn.isEnabled() is False
    assert window._zoom_page_label.text() == "1-2 / 4"

    window._toggle_zoom_spread_view()

    assert window._zoom_spread_mode is False
    assert window._zoom_label._view_only is False
    assert window._zoom_spread_btn.isChecked() is False
    assert window._zoom_object_btn.isEnabled() is True
    assert window._zoom_page_label.text() == "1 / 4"


@pytest.mark.usefixtures("qtbot")
def test_spread_next_prev_step_by_two(qtbot, tmp_path):
    pdf_path = tmp_path / "spread-nav.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_zoom_spread_view()

    assert window._zoom_page_num == 0
    assert window._zoom_prev_btn.isEnabled() is False
    assert window._zoom_next_btn.isEnabled() is True

    window._on_zoom_next_page()
    assert window._zoom_page_num == 2
    assert window._zoom_page_label.text() == "3-4 / 4"
    # 最後の見開きに到達したので「次」は無効。
    assert window._zoom_next_btn.isEnabled() is False
    assert window._zoom_prev_btn.isEnabled() is True

    window._on_zoom_prev_page()
    assert window._zoom_page_num == 0
    assert window._zoom_page_label.text() == "1-2 / 4"


@pytest.mark.usefixtures("qtbot")
def test_spread_odd_last_page_shows_single(qtbot, tmp_path):
    pdf_path = tmp_path / "spread-odd.pdf"
    make_pdf(pdf_path, pages=3)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_zoom_spread_view()

    assert window._zoom_page_label.text() == "1-2 / 3"

    window._on_zoom_next_page()
    assert window._zoom_page_num == 2
    # 右ページが無いので単独表示。
    assert window._zoom_page_label.text() == "3 / 3"
    assert window._zoom_next_btn.isEnabled() is False


@pytest.mark.usefixtures("qtbot")
def test_spread_view_only_blocks_selection_but_allows_scroll(qtbot, tmp_path):
    pdf_path = tmp_path / "spread-viewonly.pdf"
    make_pdf(pdf_path, pages=4)
    create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="x",
            fontsize=14,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.6),
            border_color=(0.0, 0.0, 0.0),
            border_width=2,
            opacity=1.0,
        ),
    )
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_zoom_spread_view()

    # 注釈位置を左クリックしても選択されない(閲覧専用)。
    pos = page_click_pos(window, 80, 80)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=pos)
    assert window._selected_zoom_annotation is None

    # 矢印キーによるビュースクロールは引き続き有効。
    window._zoom_label.setFocus()
    step = window._zoom_label.ZOOM_SCROLL_STEP
    with qtbot.waitSignal(window._zoom_label.scroll_requested, timeout=1000) as blocker:
        qtbot.keyClick(window._zoom_label, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    assert blocker.args == [0, step]


@pytest.mark.usefixtures("qtbot")
def test_exit_zoom_resets_spread_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "spread-exit.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_zoom_spread_view()
    assert window._zoom_spread_mode is True

    window._exit_zoom_view()

    assert window._zoom_spread_mode is False
    assert window._zoom_label._view_only is False
    assert window._zoom_spread_btn.isChecked() is False
    assert window._zoom_object_btn.isEnabled() is True

    # 再度開くと単ページで開始する。
    window._open_zoom_view(0)
    assert window._zoom_spread_mode is False
    assert window._zoom_page_label.text() == "1 / 4"
