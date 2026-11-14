from __future__ import annotations

import pytest
from PyQt6.QtGui import QPixmap

from src.views import main_window, pdf_card
from src.views.filter_bar import name_matches, parse_name_filter
from tests.helpers import FakeWatcher


@pytest.fixture
def window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _path, _size: (QPixmap(), 1))
    win = main_window.MainWindow()
    qtbot.addWidget(win)
    win.show()
    for name in ("契約書_A社.pdf", "見積書.pdf", "請求書.pdf"):
        path = win._work_dir / name
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        win._add_card(str(path))
    win._refresh_grid()
    return win


def test_card_context_menu_lists_actions_in_group_order(window):
    card = window._cards[0]
    card.set_selected(True)
    window._selected_cards.append(card)

    menu = window._build_card_context_menu()
    labels = [("-" if a.isSeparator() else a.text().split("\t")[0]) for a in menu.actions()]

    assert labels[0] == card.filename  # 見出し
    order = ["開く", "名前変更", "エクスポート", "パスをコピー", "削除"]
    assert [x for x in labels if x in order] == order  # 節の順（開く → 編集 → 出力 → リンク → 危険）
    assert labels[-1].startswith("Shift + 右クリック")
    assert "-" in labels


def test_filter_narrows_cards_and_typing_does_not_fire_single_key_shortcuts(window, qtbot, monkeypatch):
    rotated = []
    monkeypatch.setattr(main_window.MainWindow, "_on_rotate", lambda self, angle=90: rotated.append(angle))
    for card in window._cards:
        card.set_selected(True)
        window._selected_cards.append(card)

    window._search_action.setChecked(True)
    edit = window._filter_bar.line_edit()
    qtbot.keyClicks(edit, "r ab")  # 入力欄の中の「r」は回転にならない

    assert rotated == []
    assert edit.text() == "r ab"
    assert name_matches("契約書_A社.pdf", parse_name_filter("契約 -見積"))
    assert not name_matches("見積書.pdf", parse_name_filter("契約 -見積"))
    window._filter_bar.clear()
    edit.setText("契約 OR 請求")
    visible = [c.filename for c in window._cards if window._item_visible(c)]
    assert visible == ["契約書_A社.pdf", "請求書.pdf"]
    # Esc で閉じると絞り込みが解除される
    window._on_escape()
    assert all(window._item_visible(c) for c in window._cards)


def test_navigating_into_a_folder_clears_undo_history_and_keeps_its_saved_order(window, qtbot):
    from src.models.undo_manager import UndoAction
    from src.utils import order_store

    sub = window._work_dir / "案件A"
    sub.mkdir()
    order_store.save_folder_order(
        sub, manual_files=["b.pdf", "a.pdf"], manual_subfolders=[],
        sort_order="manual", sort_ascending=True,
    )
    before = order_store.load_folder_order(sub)
    window._undo_manager.add_action(UndoAction(description="x", undo_func=lambda: None,
                                               redo_func=lambda: None))
    assert window._undo_manager.can_undo()

    assert window.navigate_to(str(sub)) is True
    qtbot.wait(700)  # 遅延保存のタイマが残っていても新フォルダの並び順は書き換わらない

    assert window._work_dir == sub
    assert not window._undo_manager.can_undo()
    assert order_store.load_folder_order(sub) == before
    assert window._nav_back and window.go_back() and window._work_dir != sub


def test_navigation_opens_new_window_while_a_page_edit_window_is_open(window, monkeypatch):
    sub = window._work_dir / "案件B"
    sub.mkdir()

    class FakePageEdit:
        def parent(self):
            return window

        def isVisible(self):
            return True

    opened = []
    monkeypatch.setattr(window, "_get_open_page_edit_windows", lambda: [FakePageEdit()])
    monkeypatch.setattr(window, "_open_folder_in_new_window", lambda p: opened.append(p))
    before = window._work_dir

    assert window.navigate_to(str(sub)) is False

    assert window._work_dir == before
    assert opened == [str(sub)]


def test_select_all_skips_hidden_items_and_back_does_not_open_new_window(window, qtbot, monkeypatch):
    window._search_action.setChecked(True)
    window._filter_bar.line_edit().setText("契約 OR 請求")
    window._on_select_all()
    assert sorted(c.filename for c in window._selected_cards) == ["契約書_A社.pdf", "請求書.pdf"]

    # 個別画面を開いたまま「戻る」を押しても新しい窓は開かず、履歴も動かない
    opened = []
    monkeypatch.setattr(main_window.MainWindow, "_open_folder_in_new_window", lambda self, f: opened.append(f))
    monkeypatch.setattr(main_window.MainWindow, "_owned_page_edit_windows", lambda self: [object()])
    window._nav_back.append(str(window._work_dir.parent))
    assert window.go_back() is False
    assert opened == [] and len(window._nav_back) == 1


def test_removed_folder_card_cannot_be_selected_and_navigation_still_works(window, qtbot):
    """取り除いて破棄待ちのカードをクリックしても選択に残らず、破棄後の移動で落ちない。"""
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication

    for name in ("a", "b"):
        (window._work_dir / name).mkdir()
        window._add_folder_card(str(window._work_dir / name))
    window._refresh_grid()
    QApplication.processEvents()
    fc = window._folder_cards[0]

    window._remove_folder_card(fc.folder_path)
    window._on_folder_card_clicked(fc)  # 破棄待ちのうちにクリックされた
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)

    assert sip.isdeleted(fc)
    assert window._selected_folder_cards == []
    assert window.navigate_to(str(window._folder_cards[0].folder_path)) is True


def test_grid_refresh_and_navigation_never_show_cards_as_top_level_windows(window, qtbot):
    """カードを親なしにした後に、積まれていた表示要求が走って窓として現れない。"""
    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtWidgets import QApplication
    from src.views.folder_card import FolderCard

    class Spy(QObject):
        shown = []

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Show and isinstance(obj, FolderCard) and obj.parent() is None:
                self.shown.append(obj)
            return False

    for name in ("a", "b", "c"):
        (window._work_dir / name).mkdir()
        window._add_folder_card(str(window._work_dir / name))
    window._refresh_grid()
    QApplication.processEvents()
    spy = Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        window._refresh_grid()
        window._refresh_grid()  # 間にイベントを回さない
        assert window.navigate_to(str(window._work_dir / "a")) is True
        QApplication.processEvents()
        qtbot.wait(30)
    finally:
        QApplication.instance().removeEventFilter(spy)
    assert spy.shown == []
