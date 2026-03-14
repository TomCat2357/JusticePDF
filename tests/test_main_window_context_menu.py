from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, QPoint, pyqtSignal
from PyQt6.QtGui import QPixmap

from src.views import main_window, pdf_card


class FakeWatcher(QObject):
    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)

    def __init__(self, folder_path: str):
        super().__init__()
        self._folder_path = folder_path

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture
def window_factory(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _path, _size: (QPixmap(), 1))

    def _create_window(count: int = 3):
        window = main_window.MainWindow()
        qtbot.addWidget(window)
        window.show()

        paths: list[str] = []
        for index in range(count):
            path = tmp_path / f"doc_{index}.pdf"
            path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            window._add_card(str(path))
            paths.append(str(path))

        window._refresh_grid()
        return window, paths

    return _create_window


def test_context_menu_selects_unselected_card_only(window_factory, monkeypatch):
    window, paths = window_factory()
    captured: list[tuple[int, list[str], QPoint]] = []

    monkeypatch.setattr(
        main_window,
        "show_native_file_context_menu",
        lambda hwnd, menu_paths, global_pos: captured.append((hwnd, list(menu_paths), global_pos)) or True,
    )

    first_card = window._cards[0]
    third_card = window._cards[2]
    first_card.set_selected(True)
    window._selected_cards.append(first_card)

    menu_pos = QPoint(40, 50)
    window._on_card_context_menu_requested(third_card, menu_pos)

    assert window._selected_cards == [third_card]
    assert captured == [(int(window.winId()), [paths[2]], menu_pos)]


def test_context_menu_preserves_existing_multi_selection(window_factory, monkeypatch):
    window, paths = window_factory()
    captured: list[list[str]] = []

    monkeypatch.setattr(
        main_window,
        "show_native_file_context_menu",
        lambda _hwnd, menu_paths, _global_pos: captured.append(list(menu_paths)) or True,
    )

    first_card = window._cards[0]
    second_card = window._cards[1]
    for card in (first_card, second_card):
        card.set_selected(True)
        window._selected_cards.append(card)

    window._on_card_context_menu_requested(second_card, QPoint(10, 20))

    assert window._selected_cards == [first_card, second_card]
    assert captured == [[paths[0], paths[1]]]


def test_context_menu_paths_follow_grid_order(window_factory, monkeypatch):
    window, paths = window_factory()
    captured: list[list[str]] = []

    monkeypatch.setattr(
        main_window,
        "show_native_file_context_menu",
        lambda _hwnd, menu_paths, _global_pos: captured.append(list(menu_paths)) or True,
    )

    third_card = window._cards[2]
    first_card = window._cards[0]
    for card in (third_card, first_card):
        card.set_selected(True)
        window._selected_cards.append(card)

    window._on_card_context_menu_requested(first_card, QPoint(5, 15))

    assert captured == [[paths[0], paths[2]]]
