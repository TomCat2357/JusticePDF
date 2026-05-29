from __future__ import annotations

import shutil
from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QMessageBox

from src.utils.pdf_utils import get_pdf_toc
from src.views import main_window, pdf_card


class FakeWatcher(QObject):
    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)
    folder_added = pyqtSignal(str)
    folder_removed = pyqtSignal(str)

    def __init__(self, folder_path: str):
        super().__init__()
        self._folder_path = folder_path

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_subfolders(self) -> list[str]:
        return []


def _make_pdf(path: Path, *, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=300, height=400)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def merge_window(monkeypatch, qtbot, tmp_path):
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

    monkeypatch.setattr(main_window, "send2trash", _fake_trash)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    window = main_window.MainWindow(str(work_dir))
    qtbot.addWidget(window)
    window.show()
    return window, work_dir, trashed


def test_merge_selected_builds_hierarchical_pdf(merge_window, qtbot):
    window, work_dir, trashed = merge_window

    # work/FolderA/{a1.pdf, Sub/s1.pdf} と work/loose.pdf
    folder_a = work_dir / "FolderA"
    _make_pdf(folder_a / "a1.pdf", pages=1)
    _make_pdf(folder_a / "Sub" / "s1.pdf", pages=1)
    loose = work_dir / "loose.pdf"
    _make_pdf(loose, pages=1)

    # カードを作って選択(フォルダ→ファイルの順)
    fc = window._add_folder_card(str(folder_a))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)
    card = window._add_card(str(loose))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._refresh_grid()

    window._on_merge_selected()
    qtbot.waitUntil(lambda: not window._operation_in_progress, timeout=5000)

    merged = work_dir / "結合_FolderA.pdf"
    assert merged.exists()
    toc = get_pdf_toc(str(merged))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "FolderA", 1),
        (2, "a1.pdf", 1),
        (2, "Sub", 2),
        (3, "s1.pdf", 2),
        (1, "loose.pdf", 3),
    ]

    # 結合元はゴミ箱へ移動済み
    assert not folder_a.exists()
    assert not loose.exists()
    assert str(folder_a) in trashed and str(loose) in trashed

    # 結合結果のカードが追加され選択されている
    assert window._get_card_by_path(str(merged)) is not None
    assert any(c.pdf_path == str(merged) for c in window._selected_cards)


def test_merge_selected_undo_restores_sources(merge_window, qtbot):
    window, work_dir, trashed = merge_window

    folder_a = work_dir / "FolderA"
    _make_pdf(folder_a / "a1.pdf", pages=1)
    loose = work_dir / "loose.pdf"
    _make_pdf(loose, pages=2)

    fc = window._add_folder_card(str(folder_a))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)
    card = window._add_card(str(loose))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._refresh_grid()

    window._on_merge_selected()
    qtbot.waitUntil(lambda: not window._operation_in_progress, timeout=5000)

    merged = work_dir / "結合_FolderA.pdf"
    assert merged.exists()

    # Undo: 結合を取り消して元のファイル/フォルダを復元
    window._undo_manager.undo()
    assert folder_a.exists()
    assert (folder_a / "a1.pdf").exists()
    assert loose.exists()
    assert window._get_folder_card_by_path(str(folder_a)) is not None
    assert window._get_card_by_path(str(loose)) is not None
