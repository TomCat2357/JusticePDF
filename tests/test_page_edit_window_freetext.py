from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import (
    FreeTextAnnotData,
    PdfWritePermissionError,
    create_freetext_annot,
    get_page_count,
    list_freetext_annots,
)
from src.views import page_edit_window as page_edit_window_module
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


def _page_global_pos(window: PageEditWindow, x: int, y: int) -> QPoint:
    return window._zoom_label.mapToGlobal(_page_click_pos(window, x, y))


def _set_cursor_pos(qtbot, pos: QPoint) -> None:
    QCursor.setPos(pos)
    QApplication.processEvents()
    qtbot.waitUntil(
        lambda: abs(QCursor.pos().x() - pos.x()) <= 1
        and abs(QCursor.pos().y() - pos.y()) <= 1
    )


def _drag_on_zoom_label(qtbot, window: PageEditWindow, start: tuple[int, int], end: tuple[int, int]) -> None:
    qtbot.mousePress(
        window._zoom_label,
        Qt.MouseButton.LeftButton,
        pos=_page_click_pos(window, *start),
    )
    qtbot.mouseMove(window._zoom_label, _page_click_pos(window, *end))
    qtbot.mouseRelease(
        window._zoom_label,
        Qt.MouseButton.LeftButton,
        pos=_page_click_pos(window, *end),
    )


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

    _drag_on_zoom_label(qtbot, window, (60, 80), (160, 150))
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    annots = list_freetext_annots(str(pdf_path), 0)
    assert len(annots) == 1
    assert annots[0].rect == (60.0, 80.0, 160.0, 150.0)
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

    base_pixel = window._zoom_label._pixmap.toImage().pixelColor(110, 90)
    assert base_pixel.red() > 240
    assert base_pixel.green() > 240
    assert base_pixel.blue() > 240

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
def test_zoom_delete_key_in_editor_deletes_char_not_annotation(qtbot, tmp_path):
    pdf_path = tmp_path / "delete-char-in-editor.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    qtbot.mouseClick(window._zoom_annotation_toggle_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    _drag_on_zoom_label(qtbot, window, (60, 80), (160, 140))
    qtbot.waitUntil(lambda: window._zoom_label.has_active_text_editor())

    editor = window._zoom_label._inline_editor
    assert editor is not None
    qtbot.keyClicks(editor, "abc")
    # Move cursor to beginning so DELETE removes the first character
    qtbot.keyClick(editor, Qt.Key.Key_Home)
    qtbot.keyClick(editor, Qt.Key.Key_Delete)
    assert editor.toPlainText() == "bc"
    # The annotation must still exist (not deleted by DELETE key during editing)
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not window._zoom_label.has_active_text_editor())
    assert list_freetext_annots(str(pdf_path), 0) != []


@pytest.mark.usefixtures("qtbot")
def test_zoom_can_clear_fill_and_border_colors(qtbot, tmp_path):
    pdf_path = tmp_path / "clear-transparent-colors.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="transparent options",
            fontsize=16,
            text_color=(0.1, 0.2, 0.3),
            fill_color=(0.9, 1.0, 0.7),
            border_color=(0.3, 0.2, 0.1),
            border_width=3,
            opacity=0.6,
        ),
    )

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    annot = _annotation_for(window, created.xref)
    rect = window._zoom_label._annotation_widget_rect(annot)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(
        lambda: window._selected_zoom_annotation is not None
        and window._selected_zoom_annotation.xref == created.xref
    )

    qtbot.mouseClick(window._zoom_annotation_fill_color_clear_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].fill_color is None)

    qtbot.mouseClick(window._zoom_annotation_border_color_clear_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].border_color is None)

    changed = list_freetext_annots(str(pdf_path), 0)[0]
    assert changed.fill_color is None
    assert changed.border_color is None
    assert changed.border_width == 3.0
    assert abs(changed.opacity - 0.6) < 0.02
    assert window._zoom_annotation_fill_color_btn.text() == "透明"
    assert window._zoom_annotation_border_color_btn.text() == "透明"


@pytest.mark.usefixtures("qtbot")
def test_zoom_can_copy_paste_freetext_and_undo_redo(qtbot, tmp_path):
    pdf_path = tmp_path / "copy-paste.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="copied box",
            fontsize=18,
            text_color=(0.1, 0.2, 0.3),
            fill_color=(0.9, 1.0, 0.7),
            border_color=(0.3, 0.2, 0.1),
            border_width=3,
            opacity=0.6,
            fontname="Helv",
            subject="copied-subject",
        ),
    )

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    source = _annotation_for(window, created.xref)
    rect = window._zoom_label._annotation_widget_rect(source)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(
        lambda: window._selected_zoom_annotation is not None
        and window._selected_zoom_annotation.xref == created.xref
    )

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_C, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window._copied_zoom_annotation is not None
    assert window._copied_zoom_annotation.content == source.content
    assert window._copied_zoom_annotation.fontsize == source.fontsize
    assert window._copied_zoom_annotation.text_color == source.text_color
    assert window._copied_zoom_annotation.fill_color == source.fill_color
    assert window._copied_zoom_annotation.border_color == source.border_color
    assert window._copied_zoom_annotation.border_width == source.border_width
    assert window._copied_zoom_annotation.opacity == source.opacity
    assert window._copied_zoom_annotation.subject == source.subject

    _set_cursor_pos(qtbot, _page_global_pos(window, 150, 190))
    page_point = window._zoom_label.page_point_from_global_pos(QCursor.pos())
    assert page_point is not None
    expected_rect = window._zoom_label.annotation_rect_for_page_point(source, page_point)
    assert expected_rect is not None
    qtbot.keyClick(window._zoom_label, Qt.Key.Key_V, modifier=Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)
    assert window._zoom_label.has_annotation_paste_mode() is False

    annots = list_freetext_annots(str(pdf_path), 0)
    pasted = next(annot for annot in annots if annot.xref != created.xref)
    assert pasted.content == source.content
    assert pasted.fontsize == source.fontsize
    assert pasted.text_color == source.text_color
    assert pasted.fill_color == source.fill_color
    assert pasted.border_color == source.border_color
    assert pasted.border_width == source.border_width
    assert pasted.opacity == source.opacity
    assert pasted.subject == source.subject
    assert pasted.rect == window._zoom_label._qrectf_to_rect_tuple(expected_rect)

    window._on_undo()
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    window._on_redo()
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)
    pasted_again = max(list_freetext_annots(str(pdf_path), 0), key=lambda annot: annot.xref)
    assert pasted_again.content == "copied box"


@pytest.mark.usefixtures("qtbot")
def test_zoom_paste_ignores_cursor_outside_page(qtbot, tmp_path):
    pdf_path = tmp_path / "copy-paste-outside.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="outside",
            fontsize=18,
            text_color=(0.1, 0.2, 0.3),
            fill_color=(0.9, 1.0, 0.7),
            border_color=(0.3, 0.2, 0.1),
            border_width=3,
            opacity=0.6,
        ),
    )

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    source = _annotation_for(window, created.xref)
    rect = window._zoom_label._annotation_widget_rect(source)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(
        lambda: window._selected_zoom_annotation is not None
        and window._selected_zoom_annotation.xref == created.xref
    )

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_C, modifier=Qt.KeyboardModifier.ControlModifier)
    outside = window._zoom_label.mapToGlobal(
        QPoint(window._zoom_label.width() + 20, window._zoom_label.height() + 20)
    )
    _set_cursor_pos(qtbot, outside)

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_V, modifier=Qt.KeyboardModifier.ControlModifier)

    assert len(list_freetext_annots(str(pdf_path), 0)) == 1
    assert window._zoom_label.has_annotation_paste_mode() is False


@pytest.mark.usefixtures("qtbot")
def test_zoom_editor_ctrl_c_v_prioritize_text_editing(qtbot, tmp_path):
    pdf_path = tmp_path / "editor-priority.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="editor copy",
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
    qtbot.keyClick(window._zoom_label, Qt.Key.Key_C, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window._copied_zoom_annotation is not None

    qtbot.mouseDClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(lambda: window._zoom_label.has_active_text_editor())

    editor = window._zoom_label._inline_editor
    assert editor is not None

    clipboard = QApplication.clipboard()
    editor.selectAll()
    qtbot.keyClick(editor, Qt.Key.Key_C, modifier=Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: clipboard.text() == "editor copy")

    clipboard.setText("replaced via paste")
    editor.selectAll()
    qtbot.keyClick(editor, Qt.Key.Key_V, modifier=Qt.KeyboardModifier.ControlModifier)
    window._zoom_annotation_width_spin.setFocus()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].content == "replaced via paste")

    assert len(list_freetext_annots(str(pdf_path), 0)) == 1
    assert window._zoom_label.has_annotation_paste_mode() is False
    assert window._copied_zoom_annotation is not None


@pytest.mark.usefixtures("qtbot")
def test_zoom_text_edit_shows_warning_and_exits_editor_when_pdf_is_locked(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked-edit.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="locked",
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
    qtbot.mouseDClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(lambda: window._zoom_label.has_active_text_editor())

    captured: dict[str, str] = {}

    def _raise_locked(*_args, **_kwargs):
        raise PdfWritePermissionError(str(pdf_path))

    monkeypatch.setattr(page_edit_window_module, "replace_freetext_annot", _raise_locked)
    monkeypatch.setattr(
        page_edit_window_module.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    editor = window._zoom_label._inline_editor
    assert editor is not None
    editor.selectAll()
    qtbot.keyClicks(editor, "changed")
    window._zoom_annotation_width_spin.setFocus()

    qtbot.waitUntil(lambda: captured.get("title") == "PDFを編集できません")
    assert not window._zoom_label.has_active_text_editor()
    assert list_freetext_annots(str(pdf_path), 0)[0].content == "locked"
    assert window._selected_zoom_annotation is not None
    assert window._selected_zoom_annotation.xref == created.xref
    assert "locked-edit.pdf" in captured["text"]


@pytest.mark.usefixtures("qtbot")
def test_zoom_rotate_shows_warning_when_pdf_is_locked(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked-rotate.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    captured: dict[str, str] = {}

    def _raise_locked(*_args, **_kwargs):
        raise PdfWritePermissionError(str(pdf_path))

    monkeypatch.setattr(page_edit_window_module, "rotate_pages", _raise_locked)
    monkeypatch.setattr(
        page_edit_window_module.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    window._on_rotate()

    assert captured["title"] == "PDFを編集できません"
    assert "locked-rotate.pdf" in captured["text"]


@pytest.mark.usefixtures("qtbot")
def test_page_edit_rename_shows_warning_when_pdf_is_locked(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked-rename.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)

    captured: dict[str, str] = {}

    monkeypatch.setattr(
        page_edit_window_module.QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: ("renamed.pdf", True)),
    )
    monkeypatch.setattr(
        page_edit_window_module.os,
        "rename",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError(13, "Permission denied", str(pdf_path))),
    )
    monkeypatch.setattr(
        page_edit_window_module.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    window._on_rename()

    assert captured["title"] == "名前変更できません"
    assert "locked-rename.pdf" in captured["text"]
    assert window._undo_manager.undo_count() == 0


@pytest.mark.usefixtures("qtbot")
def test_page_edit_title_rename_shows_warning_when_pdf_is_locked(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked-title.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)

    captured: dict[str, str] = {}

    monkeypatch.setattr(
        page_edit_window_module.QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: ("new title", True)),
    )
    monkeypatch.setattr(
        page_edit_window_module,
        "update_pdf_metadata_title",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PdfWritePermissionError(str(pdf_path))),
    )
    monkeypatch.setattr(
        page_edit_window_module.QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: captured.update(title=title, text=text)),
    )

    window._on_rename_pdf_title()

    assert captured["title"] == "PDFを編集できません"
    assert "locked-title.pdf" in captured["text"]
    assert window._undo_manager.undo_count() == 0


@pytest.mark.usefixtures("qtbot")
def test_zoom_copy_without_selected_annotation_still_copies_selected_text(qtbot, tmp_path):
    pdf_path = tmp_path / "selected-text-copy.pdf"
    _make_pdf(pdf_path)

    doc = fitz.open(str(pdf_path))
    page = doc[0]
    page.insert_text((40, 80), "hello world")
    doc.saveIncr()
    doc.close()

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    assert len(window._zoom_label._words) >= 2
    window._zoom_label._selected_word_indices = list(range(len(window._zoom_label._words)))
    window._zoom_label.setFocus()

    clipboard = QApplication.clipboard()
    clipboard.clear()
    qtbot.keyClick(window._zoom_label, Qt.Key.Key_C, modifier=Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: clipboard.text() == "hello world")

    assert window._copied_zoom_annotation is None
