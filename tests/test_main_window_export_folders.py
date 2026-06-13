"""フォルダ単位エクスポート・エクスポート/名前変更ボタンの活性・新規フォルダ自動命名のテスト。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap

from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def export_window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _path, _size: (QPixmap(), 1))
    # モーダルな結果ダイアログがヘッドレステストをブロックしないよう no-op 化
    monkeypatch.setattr(
        main_window.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        main_window.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
    )

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    window = main_window.MainWindow(str(work_dir))
    qtbot.addWidget(window)
    window.show()
    return window, work_dir


# --- ボタン活性 -----------------------------------------------------------

def test_export_button_disabled_without_selection(export_window):
    window, work_dir = export_window
    make_pdf(work_dir / "a.pdf", pages=1)
    card = window._add_card(str(work_dir / "a.pdf"))  # noqa: F841
    window._refresh_grid()

    # 何も選択していなければエクスポートは無効
    window._update_button_states()
    assert window._export_btn.isEnabled() is False


def test_export_button_enabled_with_file_selected(export_window):
    window, work_dir = export_window
    make_pdf(work_dir / "a.pdf", pages=1)
    card = window._add_card(str(work_dir / "a.pdf"))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._update_button_states()
    assert window._export_btn.isEnabled() is True


def test_export_button_enabled_with_folder_selected(export_window):
    window, work_dir = export_window
    folder = work_dir / "Folder"
    folder.mkdir()
    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)
    window._update_button_states()
    assert window._export_btn.isEnabled() is True


# --- 名前変更メニューの文脈表示 ------------------------------------------

def test_rename_menu_shows_folder_name_when_folder_selected(export_window):
    window, work_dir = export_window
    folder = work_dir / "Folder"
    folder.mkdir()
    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)
    window._update_button_states()

    assert window._rename_btn.isEnabled() is True
    assert window._rename_folder_action.isVisible() is True
    assert window._rename_file_action.isVisible() is False
    assert window._rename_title_action.isVisible() is False


def test_rename_menu_shows_file_name_when_file_selected(export_window):
    window, work_dir = export_window
    make_pdf(work_dir / "a.pdf", pages=1)
    card = window._add_card(str(work_dir / "a.pdf"))
    card.set_selected(True)
    window._selected_cards.append(card)
    window._update_button_states()

    assert window._rename_btn.isEnabled() is True
    assert window._rename_file_action.isVisible() is True
    assert window._rename_title_action.isVisible() is True
    assert window._rename_folder_action.isVisible() is False


def test_rename_disabled_for_mixed_selection(export_window):
    window, work_dir = export_window
    make_pdf(work_dir / "a.pdf", pages=1)
    folder = work_dir / "Folder"
    folder.mkdir()
    card = window._add_card(str(work_dir / "a.pdf"))
    card.set_selected(True)
    window._selected_cards.append(card)
    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)
    window._update_button_states()
    assert window._rename_btn.isEnabled() is False


# --- 新規フォルダ自動命名 ------------------------------------------------

def test_new_folder_auto_names_and_increments(export_window):
    window, work_dir = export_window

    window._on_new_folder()
    assert (work_dir / "新規フォルダ").is_dir()

    window._on_new_folder()
    assert (work_dir / "新規フォルダ (2)").is_dir()

    window._on_new_folder()
    assert (work_dir / "新規フォルダ (3)").is_dir()


# --- フォルダ単位エクスポート ---------------------------------------------

def test_collect_export_jobs_preserves_structure_and_skips_non_pdf(export_window):
    window, work_dir = export_window
    folder = work_dir / "Folder"
    make_pdf(folder / "a.pdf", pages=1)
    make_pdf(folder / "Sub" / "b.pdf", pages=1)
    (folder / "note.txt").write_text("not a pdf", encoding="utf-8")
    (folder / "Sub" / "image.png").write_bytes(b"\x89PNG\r\n")

    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)

    jobs = window._collect_export_jobs()
    rels = sorted(rel for _src, rel in jobs)
    assert rels == [
        str(Path("Folder") / "Sub" / "b.pdf"),
        str(Path("Folder") / "a.pdf"),
    ]


def test_export_as_pdf_reproduces_folder_tree(export_window, tmp_path):
    window, work_dir = export_window
    folder = work_dir / "Folder"
    make_pdf(folder / "a.pdf", pages=1)
    make_pdf(folder / "Sub" / "b.pdf", pages=1)
    (folder / "note.txt").write_text("not a pdf", encoding="utf-8")

    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)

    dst = tmp_path / "out"
    dst.mkdir()
    window._export_as_pdf(window._collect_export_jobs(), str(dst), optimize_level=0)

    assert (dst / "Folder" / "a.pdf").is_file()
    assert (dst / "Folder" / "Sub" / "b.pdf").is_file()
    # 非 PDF はエクスポートされない
    assert not (dst / "Folder" / "note.txt").exists()


def test_export_mixed_file_and_folder(export_window, tmp_path):
    window, work_dir = export_window
    make_pdf(work_dir / "loose.pdf", pages=1)
    folder = work_dir / "Folder"
    make_pdf(folder / "a.pdf", pages=1)

    card = window._add_card(str(work_dir / "loose.pdf"))
    card.set_selected(True)
    window._selected_cards.append(card)
    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)

    dst = tmp_path / "out"
    dst.mkdir()
    window._export_as_pdf(window._collect_export_jobs(), str(dst), optimize_level=0)

    assert (dst / "loose.pdf").is_file()
    assert (dst / "Folder" / "a.pdf").is_file()
