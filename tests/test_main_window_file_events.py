from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from src.views import main_window, pdf_card
from src.views import page_edit_window as page_edit_window_module
from src.models.undo_manager import UndoManager


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

    state = {"page_count": 1}
    monkeypatch.setattr(
        pdf_card,
        "get_pdf_card_info",
        lambda _path, _size: (QPixmap(), state["page_count"]),
    )

    def _create_window():
        window = main_window.MainWindow()
        qtbot.addWidget(window)
        window.show()
        return window, state

    return _create_window


def test_file_added_refreshes_existing_card_for_reused_path(window_factory, tmp_path):
    window, state = window_factory()
    pdf_path = tmp_path / "same-name.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    card = window._add_card(str(pdf_path))
    assert card.page_count == 1

    state["page_count"] = 2
    window._on_file_added(str(pdf_path))

    assert len(window._cards) == 1
    assert window._cards[0] is card
    assert card.page_count == 2


def test_file_removed_ignores_stale_remove_when_path_exists(window_factory, tmp_path):
    window, state = window_factory()
    pdf_path = tmp_path / "same-name.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    card = window._add_card(str(pdf_path))
    state["page_count"] = 2

    window._on_file_added(str(pdf_path))
    window._on_file_removed(str(pdf_path))

    assert len(window._cards) == 1
    assert window._cards[0] is card
    assert card.page_count == 2


def test_card_double_click_ignores_hidden_page_window(window_factory, monkeypatch):
    window, _state = window_factory()
    pdf_path = Path(window._work_dir) / "same-name.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    card = window._add_card(str(pdf_path))

    class HiddenPageEditWindow:
        instances: list["HiddenPageEditWindow"] = []

        def __init__(self, pdf_path: str, undo_manager, parent=None):
            self._pdf_path = pdf_path
            self._visible = False
            self._state = Qt.WindowState.WindowNoState
            self.moved_to = None
            HiddenPageEditWindow.instances.append(self)

        def isVisible(self) -> bool:
            return self._visible

        def isMinimized(self) -> bool:
            return False

        def windowState(self):
            return self._state

        def setWindowState(self, state):
            self._state = state

        def show(self) -> None:
            self._visible = True

        def raise_(self) -> None:
            pass

        def activateWindow(self) -> None:
            pass

        def move(self, x: int, y: int) -> None:
            self.moved_to = (x, y)

        def width(self) -> int:
            return 800

        def height(self) -> int:
            return 600

    hidden = HiddenPageEditWindow(str(pdf_path), window._undo_manager)
    monkeypatch.setattr(page_edit_window_module, "PageEditWindow", HiddenPageEditWindow)
    monkeypatch.setattr(
        main_window.QApplication,
        "topLevelWidgets",
        staticmethod(lambda: [hidden]),
    )

    window._on_card_double_clicked(card)

    assert len(HiddenPageEditWindow.instances) == 2
    assert HiddenPageEditWindow.instances[0] is hidden
    assert HiddenPageEditWindow.instances[1] is not hidden
    assert HiddenPageEditWindow.instances[1].isVisible() is True


def test_page_edit_window_deletes_on_close(qtbot, tmp_path):
    pdf_path = tmp_path / "same-name.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    window = page_edit_window_module.PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()

    assert window.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose) is True
