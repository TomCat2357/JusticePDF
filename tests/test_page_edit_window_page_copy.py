from __future__ import annotations

import fitz
from PyQt6.QtCore import QByteArray, QMimeData, QPoint, Qt

from src.utils.constants import PAGETHUMBNAIL_MIME_TYPE
from src.utils.pdf_utils import get_page_count
from tests.helpers import create_page_edit_window, make_pdf


def _make_labeled_pdf(path, labels: list[str]) -> None:
    """各ページにラベル文字列を書き込んだPDFを作る(コピー先の内容検証用)。"""
    doc = fitz.open()
    for label in labels:
        page = doc.new_page(width=320, height=420)
        page.insert_text((40, 60), label, fontsize=18)
    doc.save(str(path))
    doc.close()


def _page_labels(path) -> list[str]:
    doc = fitz.open(str(path))
    try:
        return [doc[i].get_text().strip() for i in range(len(doc))]
    finally:
        doc.close()


class _FakePosition:
    def __init__(self, point: QPoint) -> None:
        self._point = point

    def toPoint(self) -> QPoint:
        return self._point


class _FakeDropEvent:
    """dropEvent/dragMoveEvent が参照する最小限のインターフェースを持つフェイク。"""

    def __init__(self, mime_data: QMimeData, point: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        self._mime_data = mime_data
        self._position = _FakePosition(point)
        self._modifiers = modifiers
        self.accepted = False

    def mimeData(self) -> QMimeData:
        return self._mime_data

    def position(self) -> _FakePosition:
        return self._position

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def acceptProposedAction(self) -> None:
        self.accepted = True


def _make_page_thumbnail_event(pdf_path: str, pages: list[int], modifiers: Qt.KeyboardModifier) -> _FakeDropEvent:
    payload = f"{pdf_path}|{','.join(str(p) for p in pages)}"
    mime_data = QMimeData()
    mime_data.setData(PAGETHUMBNAIL_MIME_TYPE, QByteArray(payload.encode('utf-8')))
    # 全サムネイルの外側の座標を指すことで、_get_drop_page_index() が末尾への
    # 挿入(len(thumbnails))を返すようにする。
    far_point = QPoint(999999, 999999)
    return _FakeDropEvent(mime_data, far_point, modifiers)


def test_ctrl_drag_drop_copies_page_and_keeps_original(qtbot, tmp_path):
    pdf_path = tmp_path / "copy.pdf"
    make_pdf(pdf_path, pages=3)
    window = create_page_edit_window(qtbot, pdf_path)

    event = _make_page_thumbnail_event(str(pdf_path), [0], Qt.KeyboardModifier.ControlModifier)
    window.dropEvent(event)

    assert event.accepted
    assert get_page_count(str(pdf_path)) == 4


def test_ctrl_drag_drop_copy_is_undoable(qtbot, tmp_path):
    pdf_path = tmp_path / "copy_undo.pdf"
    make_pdf(pdf_path, pages=3)
    window = create_page_edit_window(qtbot, pdf_path)

    event = _make_page_thumbnail_event(str(pdf_path), [0], Qt.KeyboardModifier.ControlModifier)
    window.dropEvent(event)
    assert get_page_count(str(pdf_path)) == 4

    window._on_undo()
    assert get_page_count(str(pdf_path)) == 3


def test_ctrl_drag_drop_copies_correct_page_at_correct_position(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "copy_content.pdf"
    _make_labeled_pdf(pdf_path, ["page0", "page1", "page2"])
    window = create_page_edit_window(qtbot, pdf_path)

    # ウィジェット実レイアウトに依存せず、挿入位置(index=1)を固定する。
    monkeypatch.setattr(window, "_get_drop_page_index", lambda pos: 1)

    event = _make_page_thumbnail_event(str(pdf_path), [2], Qt.KeyboardModifier.ControlModifier)
    window.dropEvent(event)

    assert _page_labels(pdf_path) == ["page0", "page2", "page1", "page2"]

    window._on_undo()
    assert _page_labels(pdf_path) == ["page0", "page1", "page2"]


def test_drag_drop_without_ctrl_still_moves_instead_of_copying(qtbot, tmp_path):
    pdf_path = tmp_path / "move.pdf"
    make_pdf(pdf_path, pages=3)
    window = create_page_edit_window(qtbot, pdf_path)

    event = _make_page_thumbnail_event(str(pdf_path), [0], Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)

    assert event.accepted
    assert get_page_count(str(pdf_path)) == 3
