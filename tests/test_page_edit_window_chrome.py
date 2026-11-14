"""個別画面の検索バーの入力と、拡大ビューの右クリック / 右ドラッグの判別。"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt

from tests.helpers import create_page_edit_window, make_pdf, open_zoom, page_click_pos


def test_single_keys_do_not_fire_while_typing_in_search_bar(qtbot, tmp_path):
    pdf_path = tmp_path / "typing.pdf"
    make_pdf(pdf_path, pages=3)
    window = create_page_edit_window(qtbot, pdf_path)
    qtbot.waitUntil(lambda: window._page_count == 3)
    window._select_pages([0])

    fired: list[int] = []
    guarded = window._unless_typing(lambda: fired.append(1))
    guarded()  # 対照: 入力欄にフォーカスが無ければ発火する
    assert fired == [1]

    window._open_search_bar()
    edit = window._search_bar._input
    qtbot.waitUntil(edit.hasFocus)
    guarded()
    assert fired == [1]

    # 実際にキーを打っても、R(回転)・Delete(削除)・T/V/H(選択モード)は発火せず文字として入る。
    qtbot.keyClicks(edit, "rtvh")
    qtbot.keyClick(edit, Qt.Key.Key_Delete)
    assert window._search_bar.text() == "rtvh"
    assert not window._undo_manager.can_undo()
    assert window._page_count == 3
    assert window._select_mode is None


def test_zoom_right_click_opens_menu_and_right_drag_zooms(qtbot, tmp_path):
    pdf_path = tmp_path / "rightclick.pdf"
    make_pdf(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    label = window._zoom_label
    start = page_click_pos(window, 60, 60)

    # 動かさずに離す = メニュー（範囲拡大はしない）
    with qtbot.assertNotEmitted(label.zoom_region_requested):
        with qtbot.waitSignal(label.context_requested) as blocker:
            qtbot.mouseClick(label, Qt.MouseButton.RightButton, pos=start)
    assert blocker.args[0] == start
    menu = window._zoom_context_menu(start)
    assert "右に90°回転" in [a.text().split("\t")[0] for a in menu.actions()]

    # しきい値を超えて動かす = 範囲拡大（メニューは出さない）
    end = QPoint(start.x() + 120, start.y() + 90)
    with qtbot.assertNotEmitted(label.context_requested):
        with qtbot.waitSignal(label.zoom_region_requested):
            qtbot.mousePress(label, Qt.MouseButton.RightButton, pos=start)
            qtbot.mouseMove(label, end)
            qtbot.mouseRelease(label, Qt.MouseButton.RightButton, pos=end)
