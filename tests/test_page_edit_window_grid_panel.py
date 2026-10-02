"""ページ一覧(グリッド)モードでも「パネル」を使える(画面が要る操作はグレーアウト)ことの確認。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QToolBar

from src.utils.pdf_utils import get_pdf_toc
from tests.helpers import create_page_edit_window, make_pdf, open_zoom


def _grid_window(qtbot, tmp_path, pages: int = 4, toc: list | None = None):
    pdf_path = tmp_path / "grid-panel.pdf"
    make_pdf(pdf_path, pages=pages, toc=toc)
    window = create_page_edit_window(qtbot, pdf_path)
    qtbot.waitUntil(lambda: window._page_count == pages)
    return window


def test_grid_panel_button_follows_search_and_has_four_items(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    assert window._grid_panel_btn.text() == "パネル"
    assert [a.text() for a in window._grid_panel_menu.actions()] == [
        "アノテーション",
        "個人情報検出",
        "OCR",
        "しおり",
    ]
    # ツールバー上で検索の直後に並ぶ。
    bar = window.findChild(QToolBar)
    actions = bar.actions()
    search_idx = next(
        i for i, a in enumerate(actions) if bar.widgetForAction(a) is window._search_btn
    )
    assert bar.widgetForAction(actions[search_idx + 1]) is window._grid_panel_btn


def test_bookmarks_opens_in_grid_mode_and_actions_are_shared(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    assert not window._grid_scroll.isHidden()

    window._zoom_bookmark_action.trigger()

    assert window._bookmarks_panel.is_open is True
    assert not window._grid_scroll.isHidden()
    assert window._zoom_view.isHidden()
    # 共有の QAction なのでチェック状態が両ボタンで連動する。
    assert window._zoom_bookmark_action.isChecked()
    assert window._grid_panel_btn.text() == "しおり" and window._grid_panel_btn.isChecked()
    assert window._zoom_panel_btn.text() == "しおり" and window._zoom_panel_btn.isChecked()
    assert window._grid_panel_menu.actions()[3] is window._zoom_bookmark_action

    window._zoom_object_btn.trigger()
    assert window._bookmarks_panel.is_open is False
    assert window._zoom_annotation_open is True
    assert window._grid_panel_btn.text() == "アノテーション"

    window._zoom_object_btn.trigger()
    assert window._grid_panel_btn.text() == "パネル" and not window._grid_panel_btn.isChecked()


def test_annotation_controls_disabled_in_grid_enabled_in_zoom(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    window._zoom_object_btn.trigger()

    assert window._zoom_annotation_body.isEnabled() is False
    assert not window._zoom_annotation_hint_label.isHidden()
    assert window._zoom_annotation_new_btn.isEnabled() is False
    # Acrobat手書きの表示切替だけは使える。
    assert window._zoom_ink_visibility_btn.isEnabled() is True

    open_zoom(window, qtbot)
    assert window._zoom_annotation_body.isEnabled() is True
    assert window._zoom_annotation_hint_label.isHidden()
    assert window._zoom_annotation_new_btn.isEnabled() is True

    window._zoom_annotation_new_btn.click()
    assert window._create_mode.name != "NONE"
    window._exit_zoom_view()
    assert window._create_mode.name == "NONE"
    assert window._zoom_annotation_body.isEnabled() is False


def test_pii_current_page_controls_gated_by_canvas(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    window._zoom_pii_btn.trigger()
    panel = window._pii_panel

    assert panel._detect_current_btn.isEnabled() is False
    assert panel._mask_markup_btn.isEnabled() is False
    assert panel._mask_rect_btn.isEnabled() is False
    assert panel._mask_ellipse_btn.isEnabled() is False
    # ページ一覧でも使える操作。
    assert panel._detect_all_btn.isEnabled() is True
    assert panel._settings_btn.isEnabled() is True

    open_zoom(window, qtbot)
    assert panel._detect_current_btn.isEnabled() is True
    assert panel._mask_markup_btn.isEnabled() is True
    assert panel._mask_rect_btn.isEnabled() is True

    window._exit_zoom_view()
    assert panel._detect_current_btn.isEnabled() is False


def test_ocr_page_controls_disabled_in_grid(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    window._zoom_ocr_action.trigger()
    panel = window._ocr_panel

    assert panel._ocr_page_btn.isEnabled() is False
    assert panel._clear_page_btn.isEnabled() is False
    assert panel._clear_all_btn.isEnabled() is True

    open_zoom(window, qtbot)
    assert panel._clear_page_btn.isEnabled() is True


def test_bookmark_jump_in_grid_selects_thumbnail_and_stays(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    window._bookmarks_panel.set_open(True)

    window._jump_zoom_to_page(3)

    assert window._zoom_view.isHidden()
    assert list(window._selected_pages) == [2]


def test_bookmark_page_provider_uses_selected_thumbnail(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    assert window._bookmark_current_page() is None

    window._on_page_clicked(2)
    assert window._bookmark_current_page() == 3
    # 複数選択では最も若いページ。
    window._select_pages([1], clear=False)
    assert window._bookmark_current_page() == 2

    # 拡大表示では表示中のページ。
    window._open_zoom_view(3)
    assert window._bookmark_current_page() == 4


def test_add_bookmark_from_grid_uses_selected_page(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    window._bookmarks_panel.set_open(True)
    window._on_page_clicked(2)

    window._bookmarks_panel._on_add_current_page()
    # 編集を確定して初めて TOC に書かれる(Enter 確定と同じ経路)。
    window._bookmarks_panel._commit_pending_editor()

    assert [e.page for e in get_pdf_toc(str(window._pdf_path))] == [3]
    assert window._zoom_view.isHidden()


def test_bookmarks_tree_reloads_after_delete_in_grid(qtbot, tmp_path, monkeypatch):
    window = _grid_window(
        qtbot, tmp_path, pages=4, toc=[[1, "A", 1], [1, "B", 2], [1, "C", 4]]
    )
    window._bookmarks_panel.set_open(True)
    reloaded = []
    original = window._reload_bookmarks_tree
    monkeypatch.setattr(
        window, "_reload_bookmarks_tree", lambda: (reloaded.append(1), original())
    )

    window._on_page_clicked(0)
    window._on_delete()
    qtbot.waitUntil(lambda: window._page_count == 3)

    assert reloaded
    assert window._bookmarks_panel._tree.topLevelItemCount() == len(
        get_pdf_toc(str(window._pdf_path))
    )


def test_toolbar_panel_button_hidden_while_zoom_view_shown(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    assert window._grid_panel_action.isVisible()

    window._open_zoom_view(0)
    assert not window._grid_panel_action.isVisible()
    qtbot.waitUntil(lambda: not window._grid_panel_btn.isVisible())

    window._exit_zoom_view()
    assert window._grid_panel_action.isVisible()
    qtbot.waitUntil(lambda: window._grid_panel_btn.isVisible())


def test_canvas_only_widgets_really_disabled_in_grid(qtbot, tmp_path):
    window = _grid_window(qtbot, tmp_path)
    window._zoom_object_btn.trigger()
    for widget in (
        window._zoom_annotation_delete_btn,
        window._zoom_annotation_order_back_btn,
        window._zoom_annotation_order_front_btn,
        window._zoom_annotation_width_spin,
        window._zoom_annotation_fontsize_spin,
        window._zoom_annotation_border_width_spin,
        window._zoom_note_list,
    ):
        assert widget.isEnabled() is False


def _click_with(window, monkeypatch, page, modifiers=Qt.KeyboardModifier.NoModifier):
    monkeypatch.setattr(QApplication, "keyboardModifiers", staticmethod(lambda: modifiers))
    window._on_page_clicked(page)


def test_shift_click_range_anchor_semantics(qtbot, tmp_path, monkeypatch):
    window = _grid_window(qtbot, tmp_path, pages=10)
    shift = Qt.KeyboardModifier.ShiftModifier
    ctrl = Qt.KeyboardModifier.ControlModifier

    _click_with(window, monkeypatch, 2)
    assert list(window._selected_pages) == [2]
    _click_with(window, monkeypatch, 5, shift)
    # 範囲は添字順に追加され、起点(最後のキー)は範囲の端へ移る。
    assert list(window._selected_pages) == [2, 3, 4, 5]
    _click_with(window, monkeypatch, 7, shift)
    assert list(window._selected_pages) == [2, 3, 4, 5, 6, 7]
    # 起点より前へのシフトクリックは添字順(昇順)で追加される。
    _click_with(window, monkeypatch, 0, shift)
    assert list(window._selected_pages) == [2, 3, 4, 5, 6, 7, 0, 1]
    assert [window._thumbnails[i].is_selected for i in range(10)] == [
        True, True, True, True, True, True, True, True, False, False,
    ]

    # Ctrl は切り替え。
    _click_with(window, monkeypatch, 3, ctrl)
    assert 3 not in window._selected_pages
    assert not window._thumbnails[3].is_selected

    # 選択済みページの通常クリックは選択を保つ。未選択ページなら選び直す。
    _click_with(window, monkeypatch, 4)
    assert 4 in window._selected_pages and len(window._selected_pages) == 7
    _click_with(window, monkeypatch, 9)
    assert list(window._selected_pages) == [9]
    assert not window._thumbnails[4].is_selected
