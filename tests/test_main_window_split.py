from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QMessageBox

from src.views import main_window, main_window_fileops, main_window_split, pdf_card
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def split_window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _path, _size: (QPixmap(), 1))
    # 確認ダイアログは常に Yes
    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )

    # send2trash は実際のゴミ箱を使わず、その場で削除して記録する
    trashed: list[str] = []

    def _fake_trash(path):
        trashed.append(str(path))
        if Path(path).is_dir():
            shutil.rmtree(path)
        else:
            Path(path).unlink()

    monkeypatch.setattr(main_window_fileops, "send2trash", _fake_trash)
    monkeypatch.setattr(main_window_split, "send2trash", _fake_trash)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    window = main_window.MainWindow(str(work_dir))
    qtbot.addWidget(window)
    window.show()
    return window, work_dir, trashed


def test_split_selected_creates_expected_cards(split_window, qtbot):
    window, work_dir, trashed = split_window

    src = work_dir / "報告書.pdf"
    make_pdf(
        src,
        pages=4,
        toc=[[1, "第1章", 1], [1, "第2章", 3]],
    )

    card = window._add_card(str(src))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._refresh_grid()

    window._on_split_selected()
    qtbot.waitUntil(lambda: not window._operation_in_progress, timeout=5000)

    part1 = work_dir / "第1章.pdf"
    part2 = work_dir / "第2章.pdf"
    assert part1.exists()
    assert part2.exists()

    # 元カードは消え、生成された2枚のカードが並び選択されている
    assert window._get_card_by_path(str(src)) is None
    assert window._get_card_by_path(str(part1)) is not None
    assert window._get_card_by_path(str(part2)) is not None
    selected_paths = {c.pdf_path for c in window._selected_cards}
    assert selected_paths == {str(part1), str(part2)}

    # 元ファイルはゴミ箱へ移動済み
    assert not src.exists()
    assert str(src) in trashed


def test_split_selected_page_mode(split_window, qtbot):
    window, work_dir, trashed = split_window

    src = work_dir / "no_bookmarks.pdf"
    make_pdf(src, pages=3)

    card = window._add_card(str(src))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._refresh_grid()

    window._on_split_selected()
    qtbot.waitUntil(lambda: not window._operation_in_progress, timeout=5000)

    for n in (1, 2, 3):
        assert (work_dir / f"no_bookmarks_{n:03d}p.pdf").exists()
    assert not src.exists()


def test_split_selected_undo_redo(split_window, qtbot):
    window, work_dir, trashed = split_window

    src = work_dir / "資料.pdf"
    make_pdf(src, pages=3, toc=[[1, "A", 1], [1, "B", 2]])

    card = window._add_card(str(src))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._refresh_grid()

    window._on_split_selected()
    qtbot.waitUntil(lambda: not window._operation_in_progress, timeout=5000)

    part_a = work_dir / "A.pdf"
    part_b = work_dir / "B.pdf"
    assert part_a.exists() and part_b.exists()
    assert not src.exists()

    # Undo: 分解を取り消し、元のファイルを復元
    window._undo_manager.undo()
    assert src.exists()
    assert not part_a.exists()
    assert not part_b.exists()
    assert window._get_card_by_path(str(src)) is not None
    assert window._get_card_by_path(str(part_a)) is None

    # Redo: 再度分解する
    window._undo_manager.redo()
    assert part_a.exists() and part_b.exists()
    assert not src.exists()
    assert window._get_card_by_path(str(part_a)) is not None
    assert window._get_card_by_path(str(part_b)) is not None
    assert window._get_card_by_path(str(src)) is None

    # Redo のあとにもう一度 Undo しても、Redo で作られたファイルが確実に消えること
    window._undo_manager.undo()
    assert src.exists()
    assert not part_a.exists()
    assert not part_b.exists()
