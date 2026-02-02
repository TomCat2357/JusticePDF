"""Tests covering memo7 behaviors using pytest-qt."""
import os
from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import Qt, QPoint, QObject, pyqtSignal
from PyQt6.QtTest import QTest

import src.views.main_window as main_window_module
from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import get_page_count
from src.views.page_edit_window import PageEditWindow


SCREENSHOT_DIR = Path(os.environ.get("PDFAS_SCREENSHOT_DIR", "docs/plans/screenshots"))


class DummyWatcher(QObject):
    """Minimal watcher stub to avoid filesystem observers in tests."""

    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)

    def __init__(self, folder_path: str, parent=None):
        super().__init__(parent)
        self._folder_path = folder_path

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def get_pdf_files(self) -> list[str]:
        return []


def create_pdf(path: Path, pages: int = 3) -> None:
    """Create a small PDF for widget tests."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}")
    doc.save(str(path))
    doc.close()


def save_screenshot(widget, name: str, base_dir: Path) -> Path:
    """Save a widget screenshot for manual inspection."""
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{name}.png"
    widget.grab().save(str(path))
    return path


@pytest.fixture
def main_window(qtbot, monkeypatch, tmp_path):
    """Create a MainWindow with a stubbed watcher and temp work dir."""
    monkeypatch.setattr(
        main_window_module.Path,
        "home",
        staticmethod(lambda: tmp_path),
    )
    monkeypatch.setattr(main_window_module, "FolderWatcher", DummyWatcher)
    window = main_window_module.MainWindow()
    qtbot.addWidget(window)
    window.resize(800, 600)
    window.show()
    qtbot.waitExposed(window)
    yield window
    window.close()


def test_main_window_rubber_band_selects_multiple_cards(main_window, qtbot, tmp_path):
    """Rubber band selection should select multiple cards."""
    for i in range(3):
        pdf_path = tmp_path / f"card_{i}.pdf"
        create_pdf(pdf_path, pages=1)
        main_window._add_card(str(pdf_path))
    main_window._refresh_grid()
    qtbot.wait(50)

    card1, card2 = main_window._cards[0], main_window._cards[1]
    rect = card1.geometry().united(card2.geometry())
    start_pos = main_window._container.mapTo(main_window, QPoint(1, 1))
    end_pos = main_window._container.mapTo(main_window, rect.bottomRight() + QPoint(5, 5))

    QTest.mousePress(main_window, Qt.MouseButton.LeftButton, pos=start_pos)
    QTest.mouseMove(main_window, end_pos)
    QTest.mouseRelease(main_window, Qt.MouseButton.LeftButton, pos=end_pos)
    qtbot.wait(50)

    save_screenshot(main_window, "memo7_main_rubber_band", SCREENSHOT_DIR)
    assert len(main_window._selected_cards) >= 2


def test_main_window_drop_indicator_visible_between_cards(main_window, qtbot, tmp_path):
    """Drop indicator should show when hovering between cards."""
    for i in range(2):
        pdf_path = tmp_path / f"indicator_{i}.pdf"
        create_pdf(pdf_path, pages=1)
        main_window._add_card(str(pdf_path))
    main_window._refresh_grid()
    qtbot.wait(50)

    card = main_window._cards[0]
    pos = QPoint(card.geometry().right() + 2, card.geometry().center().y())
    main_window._show_drop_indicator(pos)
    qtbot.wait(20)

    save_screenshot(main_window, "memo7_main_drop_indicator", SCREENSHOT_DIR)
    assert main_window._drop_indicator.isVisible()


def test_main_window_pending_select_paths_selects_new_cards(main_window, qtbot, tmp_path):
    """Pending select paths should select added cards (split behavior)."""
    pdf1 = tmp_path / "split_1.pdf"
    pdf2 = tmp_path / "split_2.pdf"
    create_pdf(pdf1, pages=1)
    create_pdf(pdf2, pages=1)

    main_window._pending_select_paths = [str(pdf1), str(pdf2)]
    main_window._on_file_added(str(pdf1))
    main_window._on_file_added(str(pdf2))
    qtbot.wait(50)

    selected_paths = {card.pdf_path for card in main_window._selected_cards}
    save_screenshot(main_window, "memo7_main_split_selection", SCREENSHOT_DIR)
    assert str(pdf1) in selected_paths
    assert str(pdf2) in selected_paths


def test_page_extraction_removes_source_page(main_window, qtbot, tmp_path):
    """Moving a page to main window should remove it from the source PDF."""
    source_pdf = tmp_path / "source.pdf"
    create_pdf(source_pdf, pages=3)

    page_window = PageEditWindow(str(source_pdf), UndoManager(), main_window)
    qtbot.addWidget(page_window)
    page_window.show()
    qtbot.waitExposed(page_window)

    data = f"{source_pdf}|1"
    main_window._handle_page_extraction(data)
    qtbot.wait(50)

    save_screenshot(page_window, "memo7_page_after_move", SCREENSHOT_DIR)
    assert get_page_count(str(source_pdf)) == 2
    assert len(page_window._thumbnails) == 2
