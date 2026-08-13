"""拡大モードの見開き表示(閲覧専用・左→右)のテスト。"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from src.views.page_edit_window import ZoomPageLayout
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

    assert window._zoom_page_layout is ZoomPageLayout.SINGLE
    assert window._zoom_page_label.text() == "1 / 4"

    window._toggle_zoom_spread_view()

    assert window._zoom_page_layout is ZoomPageLayout.HORIZONTAL
    assert window._zoom_label._view_only is True
    # 見開き中はアノテーション(付箋)ドロワーを無効化する。
    assert window._zoom_object_btn.isEnabled() is False
    assert window._zoom_page_label.text() == "1-2 / 4"

    window._toggle_zoom_spread_view()

    assert window._zoom_label._view_only is False
    assert window._zoom_object_btn.isEnabled() is True
    assert window._zoom_page_label.text() == "1 / 4"


@pytest.mark.usefixtures("qtbot")
def test_spread_disables_rotate_delete_and_bookmark_edit(qtbot, tmp_path):
    pdf_path = tmp_path / "spread-readonly.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    # 拡大ビュー(単ページ)では回転・削除が有効。
    assert window._rotate_btn.isEnabled() is True
    assert window._delete_btn.isEnabled() is True

    window._toggle_zoom_spread_view()

    # 見開き中は回転・削除ボタンが無効化される。
    assert window._rotate_btn.isEnabled() is False
    assert window._delete_btn.isEnabled() is False
    assert window._undo_btn.isEnabled() is False
    assert window._redo_btn.isEnabled() is False
    # しおりパネルは閲覧専用(作成系も無効)。
    panel = window._bookmarks_panel
    assert panel._read_only is True
    assert panel._add_current_btn.isEnabled() is False
    assert panel._add_btn.isEnabled() is False

    # ショートカット相当のハンドラ直接呼び出しでもページは変化しない(no-op)。
    window._on_rotate()
    window._on_delete()
    assert window._zoom_page_layout is ZoomPageLayout.HORIZONTAL
    assert window._zoom_page_label.text() == "1-2 / 4"

    window._toggle_zoom_spread_view()

    # 見開きを抜けると回転・削除・しおり作成が復帰する。
    assert window._rotate_btn.isEnabled() is True
    assert window._delete_btn.isEnabled() is True
    assert panel._read_only is False
    assert panel._add_current_btn.isEnabled() is True


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
    assert window._zoom_page_layout is ZoomPageLayout.HORIZONTAL

    window._exit_zoom_view()

    assert window._zoom_page_layout is ZoomPageLayout.SINGLE
    assert window._zoom_label._view_only is False
    assert window._zoom_object_btn.isEnabled() is True

    # 再度開くと単ページで開始する。
    window._open_zoom_view(0)
    assert window._zoom_page_layout is ZoomPageLayout.SINGLE
    assert window._zoom_page_label.text() == "1 / 4"


@pytest.mark.usefixtures("qtbot")
def test_page_layout_menu_has_four_exclusive_choices(qtbot, tmp_path):
    pdf_path = tmp_path / "page-layout-menu.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    assert [action.text() for action in window._zoom_layout_menu.actions()] == [
        "1枚",
        "横2枚",
        "縦2枚",
        "4枚",
    ]
    assert window._zoom_spread_btn.isCheckable() is False
    assert window._zoom_page_layout is ZoomPageLayout.SINGLE
    assert window._zoom_layout_actions[ZoomPageLayout.SINGLE].isChecked() is True

    window._zoom_layout_actions[ZoomPageLayout.GRID].trigger()

    assert window._zoom_page_layout is ZoomPageLayout.GRID
    assert window._zoom_layout_actions[ZoomPageLayout.GRID].isChecked() is True
    assert sum(
        action.isChecked() for action in window._zoom_layout_actions.values()
    ) == 1


@pytest.mark.usefixtures("qtbot")
@pytest.mark.parametrize(
    ("layout", "first_label", "next_label", "next_page"),
    [
        (ZoomPageLayout.SINGLE, "1 / 7", "2 / 7", 1),
        (ZoomPageLayout.HORIZONTAL, "1-2 / 7", "3-4 / 7", 2),
        (ZoomPageLayout.VERTICAL, "1-2 / 7", "3-4 / 7", 2),
        (ZoomPageLayout.GRID, "1-4 / 7", "5-7 / 7", 4),
    ],
)
def test_page_layout_controls_render_and_page_by_layout(
    qtbot, tmp_path, layout, first_label, next_label, next_page
):
    pdf_path = tmp_path / f"page-layout-{layout.key}.pdf"
    make_pdf(pdf_path, pages=7)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._set_zoom_page_layout(layout)

    assert window._zoom_page_label.text() == first_label
    assert window._zoom_page_num == 0
    assert window._zoom_next_btn.isEnabled() is True

    window._on_zoom_next_page()

    assert window._zoom_page_num == next_page
    assert window._zoom_page_label.text() == next_label


@pytest.mark.usefixtures("qtbot")
def test_one_page_choice_restores_editable_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "page-layout-single.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._set_zoom_page_layout(ZoomPageLayout.GRID)
    assert window._zoom_label._view_only is True
    assert window._rotate_btn.isEnabled() is False

    window._zoom_layout_actions[ZoomPageLayout.SINGLE].trigger()

    assert window._zoom_page_layout is ZoomPageLayout.SINGLE
    assert window._zoom_label._view_only is False
    assert window._rotate_btn.isEnabled() is True
