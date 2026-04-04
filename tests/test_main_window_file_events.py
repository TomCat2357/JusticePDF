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


def test_delete_shows_warning_when_file_is_in_use(window_factory, monkeypatch, tmp_path):
    window, _state = window_factory()
    pdf_path = tmp_path / "locked.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    card = window._add_card(str(pdf_path))
    card.set_selected(True)
    window._selected_cards.append(card)

    captured: dict[str, str] = {}

    def _raise_locked(path: str) -> None:
        raise OSError(None, "OLE error 0x80270027", path, -2144927705)

    monkeypatch.setattr(main_window, "send2trash", _raise_locked)
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    window._on_delete()

    assert captured["title"] == "削除できません"
    assert "使用中" in captured["text"]
    assert "locked.pdf" in captured["text"]
    assert pdf_path.exists()
    assert window._get_card_by_path(str(pdf_path)) is card
    assert window._undo_manager.undo_count() == 0


def test_rename_shows_warning_when_file_is_in_use(window_factory, monkeypatch, tmp_path):
    window, _state = window_factory()
    pdf_path = tmp_path / "locked-rename.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    card = window._add_card(str(pdf_path))
    card.set_selected(True)
    window._selected_cards.append(card)

    captured: dict[str, str] = {}

    monkeypatch.setattr(
        main_window.QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: ("renamed.pdf", True)),
    )
    monkeypatch.setattr(
        main_window.MainWindow,
        "_perform_rename",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError(13, "Permission denied", str(pdf_path))),
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    window._on_rename()

    assert captured["title"] == "名前変更できません"
    assert "locked-rename.pdf" in captured["text"]
    assert window._undo_manager.undo_count() == 0


def test_rename_pdf_title_shows_warning_when_file_is_in_use(window_factory, monkeypatch, tmp_path):
    window, _state = window_factory()
    pdf_path = tmp_path / "locked-title.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    card = window._add_card(str(pdf_path))
    card.set_selected(True)
    window._selected_cards.append(card)

    captured: dict[str, str] = {}

    monkeypatch.setattr(
        main_window.QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: ("new title", True)),
    )
    monkeypatch.setattr(
        main_window,
        "update_pdf_metadata_title",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(main_window.PdfWritePermissionError(str(pdf_path))),
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    window._on_rename_pdf_title()

    assert captured["title"] == "PDFを編集できません"
    assert "locked-title.pdf" in captured["text"]
    assert window._undo_manager.undo_count() == 0
