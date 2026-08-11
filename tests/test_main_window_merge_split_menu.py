from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QMessageBox

from src.views import main_window, main_window_fileops, pdf_card
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def make_window(monkeypatch, qtbot, tmp_path):
    """MainWindow を組み立てる factory を返す fixture。

    ハンドラの monkeypatch（_on_merge_selected 等）はウィンドウ生成前に
    行う必要があるため（ツールバー構築時に接続される）、window の生成自体は
    factory 呼び出し側に委ねる。
    """
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

    def _build():
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        window = main_window.MainWindow(str(work_dir))
        qtbot.addWidget(window)
        window.show()
        return window, work_dir, trashed

    return _build


@pytest.fixture
def menu_window(make_window):
    return make_window()


def _select_files(window, work_dir, count: int) -> None:
    for i in range(count):
        p = work_dir / f"f{i}.pdf"
        make_pdf(p, pages=1)
        card = window._add_card(str(p))
        card.set_selected(True)
        window._selected_cards.append(card)
    window._refresh_grid()
    window._update_button_states()


def _select_folder(window, work_dir) -> None:
    folder = work_dir / "FolderA"
    make_pdf(folder / "a1.pdf", pages=1)
    fc = window._add_folder_card(str(folder))
    fc.set_selected(True)
    window._selected_folder_cards.append(fc)
    window._refresh_grid()
    window._update_button_states()


def test_no_selection_disables_button_and_hides_both_actions(menu_window):
    window, _work_dir, _trashed = menu_window
    window._update_button_states()

    assert not window._merge_split_btn.isEnabled()
    assert not window._merge_action.isVisible()
    assert not window._split_action.isVisible()


def test_one_file_shows_only_split(menu_window):
    window, work_dir, _trashed = menu_window
    _select_files(window, work_dir, 1)

    assert window._merge_split_btn.isEnabled()
    assert not window._merge_action.isVisible()
    assert window._split_action.isVisible()


def test_two_files_shows_only_merge(menu_window):
    window, work_dir, _trashed = menu_window
    _select_files(window, work_dir, 2)

    assert window._merge_split_btn.isEnabled()
    assert window._merge_action.isVisible()
    assert not window._split_action.isVisible()


def test_one_folder_shows_only_merge(menu_window):
    window, work_dir, _trashed = menu_window
    _select_folder(window, work_dir)

    assert window._merge_split_btn.isEnabled()
    assert window._merge_action.isVisible()
    assert not window._split_action.isVisible()


def test_busy_disables_button(menu_window):
    window, work_dir, _trashed = menu_window
    _select_files(window, work_dir, 2)
    assert window._merge_split_btn.isEnabled()

    window._operation_in_progress = True
    window._update_button_states()

    assert not window._merge_split_btn.isEnabled()


def test_merge_action_triggers_handler(monkeypatch, make_window):
    # トリガー時に接続済みのハンドラが呼ばれることを確認するため、ツールバー
    # 構築（=接続）より前にクラスメソッドを差し替える。
    called = []
    monkeypatch.setattr(
        main_window.MainWindow, "_on_merge_selected", lambda self: called.append(True)
    )
    window, work_dir, _trashed = make_window()
    _select_files(window, work_dir, 2)

    window._merge_action.trigger()

    assert called == [True]


def test_split_action_triggers_handler(monkeypatch, make_window):
    called = []
    monkeypatch.setattr(
        main_window.MainWindow, "_on_split_selected", lambda self: called.append(True)
    )
    window, work_dir, _trashed = make_window()
    _select_files(window, work_dir, 1)

    window._split_action.trigger()

    assert called == [True]
