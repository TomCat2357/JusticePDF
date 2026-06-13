"""Tests for the Explorer "open folder" launch behaviour.

When a *single* folder is opened from Explorer's right-click menu, JusticePDF
copies it into the PDFs library (``~/Documents/PDFs``) and opens **only** the
copy — no separate library window appears
(``MainWindow.open_external_folder``).  A mixed file+folder launch still opens
the library window as host and additionally opens each copied folder
(``import_external_paths(open_imported_folders=True)``).  Both paths share the
``_build_import_tree`` machinery covered here.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher

_PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Patch the world so MainWindows can be built headlessly under tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(
        pdf_card,
        "get_pdf_card_info",
        lambda _path, _size: (QPixmap(), 1),
    )
    return tmp_path


@pytest.fixture
def window(env, qtbot):
    win = main_window.MainWindow()
    qtbot.addWidget(win)
    win.show()
    return win


def _drain_import(win) -> None:
    # Pump the event loop first so a deferred (QTimer.singleShot) import has a
    # chance to start its worker, then block on the worker and deliver its
    # queued finished signal.
    QApplication.processEvents()
    worker = win._active_import_worker
    if worker is not None:
        worker.wait()
    QApplication.processEvents()


def _library(env) -> Path:
    return env / "Documents" / "PDFs"


# ─────────────────────────────────────────────────────────────────
# Single folder launch -> one window, work dir IS the copy
# ─────────────────────────────────────────────────────────────────

def test_open_external_folder_opens_only_the_copy(env, qtbot):
    before = list(main_window.MainWindow._instances)

    src = env / "事件記録"
    (src / "sub").mkdir(parents=True)
    (src / "a.pdf").write_bytes(_PDF_BYTES)
    (src / "sub" / "b.pdf").write_bytes(_PDF_BYTES)

    win = main_window.MainWindow.open_external_folder(str(src))
    qtbot.addWidget(win)

    # Exactly one new window, scoped to the copied folder under the library.
    new_windows = [w for w in main_window.MainWindow._instances if w not in before]
    assert new_windows == [win]
    copy = _library(env) / "事件記録"
    assert Path(win._work_dir) == copy
    assert win._is_root_window is False
    # No child/library window was spawned.
    assert win._child_windows == []

    _drain_import(win)

    # Contents are reproduced directly inside the copy (no nested 事件記録/事件記録).
    assert (copy / "a.pdf").exists()
    assert (copy / "sub" / "b.pdf").exists()
    assert not (copy / "事件記録").exists()
    # Source is left untouched.
    assert (src / "a.pdf").exists()


def test_open_external_folder_unique_name_on_collision(env, qtbot):
    (_library(env) / "資料").mkdir(parents=True)  # pre-existing in the library

    src = env / "資料"
    src.mkdir()
    (src / "a.pdf").write_bytes(_PDF_BYTES)

    win = main_window.MainWindow.open_external_folder(str(src))
    qtbot.addWidget(win)
    _drain_import(win)

    assert Path(win._work_dir).name == "資料(1)"
    assert (Path(win._work_dir) / "a.pdf").exists()


def test_open_external_empty_folder_opens_copy_without_worker(env, qtbot):
    src = env / "空フォルダ"
    src.mkdir()

    win = main_window.MainWindow.open_external_folder(str(src))
    qtbot.addWidget(win)

    assert win._active_import_worker is None
    copy = _library(env) / "空フォルダ"
    assert Path(win._work_dir) == copy
    assert copy.is_dir()
    assert win._child_windows == []


# ─────────────────────────────────────────────────────────────────
# Mixed / multi launch -> library window host + opened copies
# ─────────────────────────────────────────────────────────────────

def test_import_external_opens_copied_folder(window, qtbot, env):
    src = env / "添付"
    src.mkdir()
    (src / "a.pdf").write_bytes(_PDF_BYTES)

    window.import_external_paths([str(src)], open_imported_folders=True)

    assert len(window._child_windows) == 1
    child = window._child_windows[0]
    qtbot.addWidget(child)
    copy = Path(window._work_dir) / "添付"
    assert Path(child._work_dir) == copy

    _drain_import(window)
    assert (copy / "a.pdf").exists()


def test_open_file_does_not_open_child_window(window, env):
    src = env / "loose.pdf"
    src.write_bytes(_PDF_BYTES)

    window.import_external_paths([str(src)], open_imported_folders=True)
    _drain_import(window)

    # Files land flat in the library; there is no folder to open.
    assert window._child_windows == []
    assert (Path(window._work_dir) / "loose.pdf").exists()


def test_import_paths_without_flag_opens_nothing(window, env):
    src = env / "folder"
    src.mkdir()
    (src / "a.pdf").write_bytes(_PDF_BYTES)

    # Drag-drop / Import-Folder button path: copy in, but never auto-open.
    window._import_paths([str(src)])
    _drain_import(window)

    assert window._child_windows == []
    assert (Path(window._work_dir) / "folder" / "a.pdf").exists()
