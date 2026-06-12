"""テスト共通ヘルパー(テスト用PDF生成・FolderWatcher のフェイク等)。"""
from __future__ import annotations

from pathlib import Path

import fitz
from PyQt6.QtCore import QObject, QPoint, pyqtSignal

from src.models.undo_manager import UndoManager
from src.views.page_edit_window import PageEditWindow


def make_pdf(
    path,
    *,
    pages: int = 1,
    width: int = 320,
    height: int = 420,
    toc: list | None = None,
    fill: tuple[float, float, float] | None = None,
) -> None:
    """指定ページ数のテスト用PDFを生成する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        if fill is not None:
            page.draw_rect(fitz.Rect(0, 0, width, height), color=fill, fill=fill)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


class FakeWatcher(QObject):
    """FolderWatcher の差し替え用フェイク(監視を行わない)。"""

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


def create_page_edit_window(qtbot, pdf_path) -> PageEditWindow:
    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()
    return window


def open_zoom(window: PageEditWindow, qtbot) -> None:
    window._open_zoom_view(0)
    qtbot.waitUntil(
        lambda: window._zoom_view.isVisible()
        and window._zoom_label is not None
        and window._zoom_label._pixmap is not None
        and not window._zoom_label._pixmap.isNull()
    )


def page_click_pos(window: PageEditWindow, x: int, y: int) -> QPoint:
    offset = window._zoom_label._pixmap_offset()
    return QPoint(int(offset.x() + x), int(offset.y() + y))
