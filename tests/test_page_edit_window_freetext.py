from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QPoint, Qt

from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import FreeTextAnnotData, create_freetext_annot, get_page_count, list_freetext_annots
from src.views.page_edit_window import PageEditWindow


def _make_pdf(path, *, width: int = 320, height: int = 420) -> None:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    doc.save(path)
    doc.close()


def _create_window(qtbot, pdf_path) -> PageEditWindow:
    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()
    return window


def _open_zoom(window: PageEditWindow, qtbot) -> None:
    window._open_zoom_view(0)
    qtbot.waitUntil(
        lambda: window._zoom_view.isVisible()
        and window._zoom_label is not None
        and window._zoom_label._pixmap is not None
        and not window._zoom_label._pixmap.isNull()
    )


def _page_click_pos(window: PageEditWindow, x: int, y: int) -> QPoint:
    offset = window._zoom_label._pixmap_offset()
    return QPoint(int(offset.x() + x), int(offset.y() + y))


def _annotation_for(window: PageEditWindow, xref: int):
    return next(annot for annot in window._zoom_annotations if annot.xref == xref)


@pytest.mark.usefixtures("qtbot")
def test_zoom_drawer_starts_closed_and_can_create_freetext(qtbot, tmp_path):
    pdf_path = tmp_path / "drawer-create.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    assert window._zoom_annotation_open is False
    assert window._zoom_annotation_toggle_btn.isVisible()

    qtbot.mouseClick(window._zoom_annotation_toggle_btn, Qt.MouseButton.LeftButton)
    assert window._zoom_annotation_open is True

    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    assert window._zoom_annotation_new_btn.isChecked() is True

    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=_page_click_pos(window, 60, 80))
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    annots = list_freetext_annots(str(pdf_path), 0)
    assert len(annots) == 1
    assert window._selected_zoom_annotation is not None
    assert window._selected_zoom_annotation.content == ""
    assert window._zoom_annotation_open is True
    assert window._zoom_annotation_new_btn.isChecked() is False
    assert window._zoom_label.has_active_text_editor() is True


@pytest.mark.usefixtures("qtbot")
def test_zoom_selects_existing_freetext_and_applies_direct_edit_and_form_changes(qtbot, tmp_path):
    pdf_path = tmp_path / "select-apply.pdf"
    _make_pdf(pdf_path)

    doc = fitz.open(str(pdf_path))
    page = doc[0]
    page.add_freetext_annot(
        fitz.Rect(50, 60, 170, 120),
        "existing",
        richtext=True,
        opacity=0.8,
        style=(
            "font-size:14pt; font-family:Helvetica; color:#112233; "
            "border:2px solid #334455; background-color:#ffeeaa;"
        ),
    )
    doc.saveIncr()
    doc.close()

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    annot = list_freetext_annots(str(pdf_path), 0)[0]
    rect = window._zoom_label._annotation_widget_rect(annot)
    qtbot.mouseDClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())

    qtbot.waitUntil(lambda: window._zoom_label.has_active_text_editor())
    assert window._zoom_annotation_open is True
    editor = window._zoom_label._inline_editor
    assert editor is not None
    assert editor.toPlainText() == "existing"
    assert editor.document().documentMargin() == 0
    assert "border: 0px solid transparent" in editor.styleSheet()
    assert "padding: 0px" in editor.styleSheet()

    editor.selectAll()
    qtbot.keyClicks(editor, "changed")
    window._zoom_annotation_width_spin.setFocus()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].content == "changed")

    window._on_undo()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].content == "existing")
    window._on_redo()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].content == "changed")

    window._zoom_annotation_width_spin.setValue(200)
    window._zoom_annotation_height_spin.setValue(110)
    window._zoom_annotation_fontsize_spin.setValue(22)
    window._zoom_annotation_opacity_slider.setValue(65)
    window._zoom_annotation_border_width_spin.setValue(4)
    window._zoom_annotation_text_color = (1.0, 0.0, 0.0)
    window._zoom_annotation_fill_color = (0.8, 1.0, 0.8)
    window._zoom_annotation_border_color = (0.0, 0.0, 0.0)
    window._set_color_button_preview(window._zoom_annotation_text_color_btn, window._zoom_annotation_text_color)
    window._set_color_button_preview(window._zoom_annotation_fill_color_btn, window._zoom_annotation_fill_color)
    window._set_color_button_preview(window._zoom_annotation_border_color_btn, window._zoom_annotation_border_color, allow_none=True)
    window._apply_zoom_annotation_form()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].content == "changed")

    changed = list_freetext_annots(str(pdf_path), 0)[0]
    assert changed.content == "changed"
    assert changed.rect == (50.0, 60.0, 250.0, 170.0)
    assert changed.fontsize == 22.0
    assert abs(changed.opacity - 0.65) < 0.02
    assert changed.border_width == 4.0
    assert window._zoom_annotation_opacity_label.text() == "65%"


@pytest.mark.usefixtures("qtbot")
def test_zoom_can_move_resize_and_undo_redo_freetext(qtbot, tmp_path):
    pdf_path = tmp_path / "drag-resize.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="drag",
            fontsize=14,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.6),
            border_color=(0.0, 0.0, 0.0),
            border_width=2,
            opacity=1.0,
        ),
    )

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    annot = _annotation_for(window, created.xref)
    rect = window._zoom_label._annotation_widget_rect(annot)
    center = rect.center().toPoint()

    qtbot.mousePress(window._zoom_label, Qt.MouseButton.LeftButton, pos=center)
    qtbot.mouseMove(window._zoom_label, QPoint(center.x() + 30, center.y() + 20))
    qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.LeftButton, pos=QPoint(center.x() + 30, center.y() + 20))
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].rect != (40.0, 50.0, 170.0, 120.0))

    moved_rect = list_freetext_annots(str(pdf_path), 0)[0].rect
    moved_annot = _annotation_for(window, window._selected_zoom_annotation.xref)
    handle = window._zoom_label._handle_rects(window._zoom_label._annotation_widget_rect(moved_annot))["se"].center().toPoint()

    qtbot.mousePress(window._zoom_label, Qt.MouseButton.LeftButton, pos=handle)
    qtbot.mouseMove(window._zoom_label, QPoint(handle.x() + 25, handle.y() + 20))
    qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.LeftButton, pos=QPoint(handle.x() + 25, handle.y() + 20))
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].rect != moved_rect)

    resized_rect = list_freetext_annots(str(pdf_path), 0)[0].rect
    assert resized_rect[2] > moved_rect[2]
    assert resized_rect[3] > moved_rect[3]

    window._on_undo()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].rect == moved_rect)

    window._on_redo()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].rect == resized_rect)


@pytest.mark.usefixtures("qtbot")
def test_zoom_delete_key_removes_selected_freetext_instead_of_page(qtbot, tmp_path):
    pdf_path = tmp_path / "delete-selected.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="delete",
            fontsize=14,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.6),
            border_color=(0.0, 0.0, 0.0),
            border_width=2,
            opacity=1.0,
        ),
    )

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    annot = _annotation_for(window, created.xref)
    rect = window._zoom_label._annotation_widget_rect(annot)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None and window._selected_zoom_annotation.xref == created.xref)

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0) == [])

    assert get_page_count(str(pdf_path)) == 1


@pytest.mark.usefixtures("qtbot")
def test_zoom_delete_key_removes_active_freetext_editor_annotation(qtbot, tmp_path):
    pdf_path = tmp_path / "delete-active-editor.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    qtbot.mouseClick(window._zoom_annotation_toggle_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=_page_click_pos(window, 60, 80))
    qtbot.waitUntil(lambda: window._zoom_label.has_active_text_editor())

    editor = window._zoom_label._inline_editor
    assert editor is not None
    qtbot.keyClicks(editor, "delete me")
    qtbot.keyClick(editor, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0) == [])

    assert get_page_count(str(pdf_path)) == 1
