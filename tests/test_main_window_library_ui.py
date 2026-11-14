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
