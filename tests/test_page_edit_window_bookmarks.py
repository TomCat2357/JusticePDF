from __future__ import annotations

import pytest

from src.utils.pdf_utils import get_pdf_toc
from tests.helpers import create_page_edit_window, make_pdf


def test_add_bookmark_persists_and_undo_redo(qtbot, tmp_path):
    pdf_path = tmp_path / "bm.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)

    window._open_zoom_view(2)  # 0-based page index 2 -> page 3
    window._bookmarks_panel.set_open(True)
    window._bookmarks_panel._on_add_current_page()

    toc = get_pdf_toc(str(pdf_path))
    assert len(toc) == 1
    assert toc[0].page == 3  # 1-based
    assert toc[0].level == 1

    # Undo removes the bookmark from disk
    window._on_undo()
    assert get_pdf_toc(str(pdf_path)) == []

    # Redo re-adds it
    window._on_redo()
    redone = get_pdf_toc(str(pdf_path))
    assert len(redone) == 1
    assert redone[0].page == 3


def test_jump_changes_zoom_page(qtbot, tmp_path):
    pdf_path = tmp_path / "jump.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)

    window._open_zoom_view(0)
    window._bookmarks_panel.set_open(True)

    window._jump_zoom_to_page(4)  # page 4 -> index 3
    assert window._zoom_page_num == 3

    # Out-of-range pages are clamped to the last page
    window._jump_zoom_to_page(99)
    assert window._zoom_page_num == 3


def test_hierarchy_persists_to_disk(qtbot, tmp_path):
    pdf_path = tmp_path / "hier.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)

    window._open_zoom_view(0)
    panel = window._bookmarks_panel
    panel.set_open(True)

    # Add two top-level bookmarks (page 1), then demote the second under the first.
    panel._on_add_current_page()
    window._zoom_page_num = 1
    panel._on_add_current_page()

    second = panel._tree.topLevelItem(1)
    panel._tree.setCurrentItem(second)
    panel._on_demote()

    toc = get_pdf_toc(str(pdf_path))
    assert [e.level for e in toc] == [1, 2]


def test_drawer_mutually_exclusive_with_annotation_drawer(qtbot, tmp_path):
    pdf_path = tmp_path / "excl.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(0)

    window._bookmarks_panel.set_open(True)
    window._set_zoom_annotation_drawer_open(True)
    assert window._bookmarks_panel.is_open is False

    window._bookmarks_panel.set_open(True)
    assert window._zoom_annotation_open is False
