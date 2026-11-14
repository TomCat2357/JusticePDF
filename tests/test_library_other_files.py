"""PDF 以外のファイルもライブラリ（一覧）に出ることのテスト。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap
from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileMovedEvent

from src.controllers.folder_watcher import FolderWatcher
from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def work(tmp_path):
    """QSettings などが tmp_path 直下に作る物と混ざらない専用の作業フォルダ。"""
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture
def make_window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _p, _s: (QPixmap(), 1))

    def _create(work_dir: Path):
        window = main_window.MainWindow(str(work_dir))
        qtbot.addWidget(window)
        window.show()
        return window

    return _create


def _names(window) -> list[str]:
    return sorted(c.filename for c in window._cards)


def test_load_existing_files_lists_non_pdf(make_window, work):
    make_pdf(work / "a.pdf")
    (work / "b.docx").write_bytes(b"x")
    (work / "c.txt").write_text("hi")
    (work / "~$b.docx").write_bytes(b"x")
    (work / "scratch.tmp").write_bytes(b"x")
    (work / "desktop.ini").write_text("x")
    (work / "sub").mkdir()

    window = make_window(work)

    assert _names(window) == ["a.pdf", "b.docx", "c.txt"]
    kinds = {c.filename: (c.kind, c.is_pdf, c.page_count) for c in window._cards}
    assert kinds["a.pdf"] == ("pdf", True, 1)
    assert kinds["b.docx"] == ("word", False, 0)
    assert kinds["c.txt"] == ("text", False, 0)
    assert len(window._folder_cards) == 1


def test_non_pdf_card_never_opens_with_fitz(make_window, work, monkeypatch):
    (work / "pic.png").write_bytes(b"not really a png")
    calls = []
    monkeypatch.setattr(
        pdf_card, "get_pdf_card_info", lambda p, s: calls.append(p) or (QPixmap(), 1)
    )
    window = make_window(work)
    assert _names(window) == ["pic.png"]
    assert calls == []
    card = window._cards[0]
    assert card._page_count_label.text() == "PNG"
    assert not card._size_label.isHidden()
    assert card._size_label.text() == "16 B"


def test_folder_card_counts_library_files(make_window, work):
    sub = work / "sub"
    sub.mkdir()
    (sub / "a.pdf").write_bytes(b"x")
    (sub / "b.xlsx").write_bytes(b"x")
    (sub / "~$b.xlsx").write_bytes(b"x")
    (sub / "inner").mkdir()
    window = make_window(work)
    assert window._folder_cards[0]._item_count == 3


def test_reconcile_adds_removes_and_does_not_rebuild_non_pdf(make_window, work):
    window = make_window(work)
    f = work / "sheet.xlsx"
    f.write_bytes(b"PK")

    # 追加（同じシグネチャを 2 回見てから載る）
    window._reconcile_with_disk()
    window._reconcile_with_disk()
    assert _names(window) == ["sheet.xlsx"]

    # 作り直されない
    card = window._cards[0]
    refreshed = []
    card.refresh = lambda: refreshed.append(1)
    for _ in range(3):
        window._reconcile_with_disk()
    assert window._cards == [card]
    assert refreshed == []

    # 中身が変わったら refresh（作り直しではなく同じカードを更新）
    f.write_bytes(b"PK-changed-longer")
    window._reconcile_with_disk()  # 1 回目: 安定待ち
    window._reconcile_with_disk()  # 2 回目: 更新
    assert window._cards == [card]
    assert refreshed == [1]

    # 削除
    f.unlink()
    window._reconcile_with_disk()
    assert window._cards == []


def test_reconcile_adds_empty_non_pdf_file(make_window, work):
    window = make_window(work)
    (work / "empty.txt").write_bytes(b"")
    window._reconcile_with_disk()
    window._reconcile_with_disk()
    assert _names(window) == ["empty.txt"]


def test_file_events_add_and_remove_non_pdf(make_window, work):
    window = make_window(work)
    f = work / "memo.md"
    f.write_text("# hi")

    window._on_file_added(str(f))
    window._reconcile_with_disk()
    assert _names(window) == ["memo.md"]

    f.unlink()
    window._on_file_removed(str(f))
    assert window._cards == []


def test_watcher_emits_for_non_pdf_and_skips_junk(work):
    watcher = FolderWatcher(str(work))
    added, removed = [], []
    watcher.file_added.connect(added.append)
    watcher.file_removed.connect(removed.append)
    h = watcher._handler

    h.on_created(FileCreatedEvent(str(work / "a.docx")))
    h.on_created(FileCreatedEvent(str(work / "~$a.docx")))
    h.on_created(FileCreatedEvent(str(work / "x.tmp")))
    h.on_deleted(FileDeletedEvent(str(work / "a.docx")))
    h.on_moved(FileMovedEvent(str(work / "x.tmp"), str(work / "x.xlsx")))

    assert added == [str(work / "a.docx"), str(work / "x.xlsx")]
    assert removed == [str(work / "a.docx")]


def test_watcher_lists_library_files(work):
    make_pdf(work / "a.pdf")
    (work / "b.png").write_bytes(b"x")
    (work / "~$c.docx").write_bytes(b"x")
    (work / "sub").mkdir()
    watcher = FolderWatcher(str(work))
    assert sorted(Path(p).name for p in watcher.get_library_files()) == ["a.pdf", "b.png"]
    assert [Path(p).name for p in watcher.get_pdf_files()] == ["a.pdf"]


def test_double_click_non_pdf_opens_with_default_app(make_window, work, monkeypatch):
    f = work / "slides.pptx"
    f.write_bytes(b"PK")
    window = make_window(work)
    opened = []
    monkeypatch.setattr(
        main_window.QDesktopServices, "openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    from src.views import page_edit_window

    monkeypatch.setattr(
        page_edit_window, "PageEditWindow",
        lambda *a, **k: pytest.fail("PDF 以外で編集画面を開いた"),
    )
    card = window._cards[0]
    window._on_card_double_clicked(card)

    assert [Path(p).name for p in opened] == ["slides.pptx"]
    assert not card.is_locked
