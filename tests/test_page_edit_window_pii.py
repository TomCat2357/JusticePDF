"""個人情報検出ドロワー(PageEditWindow統合)のテスト。

架空のダミーテキスト(山田太郎、090-1234-5678)を埋め込んだPDFを使う。
"""
from __future__ import annotations

import fitz
import pytest

from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import list_pii_markup_annots
from src.views.page_edit_window import PageEditWindow
from tests.helpers import open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


def _make_pii_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text(
        (40, 100), "山田太郎の電話番号は0901234567890", fontname="japan", fontsize=14
    )
    doc.save(str(path))
    doc.close()


def _create_window(qtbot, pdf_path) -> PageEditWindow:
    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()
    return window


def test_pii_drawer_toggle_is_exclusive_with_annotation_drawer(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._toggle_zoom_annotation_drawer()
    assert window._zoom_annotation_open is True

    window._toggle_pii_drawer()
    assert window._pii_panel.is_open is True
    assert window._zoom_annotation_open is False


def test_pii_detection_creates_highlight_and_undo_removes_it(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    assert window._pii_worker is not None

    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)

    annots = list_pii_markup_annots(str(pdf_path))
    assert len(annots) >= 1
    assert {a.pii_entity for a in annots} & {"PERSON", "PHONE_NUMBER"}

    assert window._undo_manager.can_undo() is True
    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == len(annots)


def test_pii_detection_undo_redo_multiple_cycles(qtbot, tmp_path):
    """検出→取消→やり直し→取消 を繰り返しても件数が正しいこと(_AnnotRef回帰確認)。"""
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    count = len(list_pii_markup_annots(str(pdf_path)))
    assert count >= 1

    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count

    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count


def test_pii_remove_selected_undo_redo_multiple_cycles(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    count = len(list_pii_markup_annots(str(pdf_path)))
    assert count >= 1

    window._on_pii_remove_all()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count

    window._undo_manager.redo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count


def test_pii_remove_all_clears_highlights(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    assert list_pii_markup_annots(str(pdf_path))

    window._on_pii_remove_all()
    assert list_pii_markup_annots(str(pdf_path)) == []
