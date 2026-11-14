from __future__ import annotations

from src.views.page_edit_annotations import CreateMode
from tests.helpers import create_page_edit_window, make_pdf


def test_closing_annotation_panel_clears_create_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "a.pdf"
    make_pdf(pdf_path, pages=1)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(0)
    window._set_zoom_annotation_drawer_open(True)
    window._activate_create_mode(CreateMode.NOTE)
    assert window._create_mode is CreateMode.NOTE
    window._set_zoom_annotation_drawer_open(False)
    assert window._create_mode is CreateMode.NONE
