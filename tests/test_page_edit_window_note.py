"""ズームビューの付箋（ノート/コメント）UI のテスト。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor

from src.utils.pdf_utils import list_note_annots
from src.views import page_edit_window as page_edit_window_module
from tests.helpers import create_page_edit_window, make_pdf, open_zoom


def _place_note(window, x: float, y: float) -> None:
    window._on_note_btn_clicked(True)
    assert window._note_create_mode is True
    window._on_note_create_requested((x, y))


@pytest.mark.usefixtures("qtbot")
def test_note_button_enters_placement_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "note-mode.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    assert window._note_create_mode is True
    assert window._zoom_label._note_create_mode is True

    # もう一度押すと解除
    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    assert window._note_create_mode is False


@pytest.mark.usefixtures("qtbot")
def test_place_note_creates_and_selects(qtbot, tmp_path):
    pdf_path = tmp_path / "note-place.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _place_note(window, 100.0, 120.0)
    qtbot.waitUntil(lambda: len(list_note_annots(str(pdf_path), 0)) == 1)
    # 配置後は作成モードを抜け、その付箋が選択される
    assert window._note_create_mode is False
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)
    assert window._zoom_note_editor.isVisible() is True


@pytest.mark.usefixtures("qtbot")
def test_note_content_edit_persists(qtbot, tmp_path):
    pdf_path = tmp_path / "note-edit.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _place_note(window, 80.0, 90.0)
    qtbot.waitUntil(lambda: len(list_note_annots(str(pdf_path), 0)) == 1)

    window._zoom_note_editor.setPlainText("確認: 第三条")
    window._commit_note_editor_if_dirty()
    qtbot.waitUntil(lambda: list_note_annots(str(pdf_path), 0)[0].content == "確認: 第三条")


@pytest.mark.usefixtures("qtbot")
def test_note_color_change(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "note-color.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _place_note(window, 80.0, 90.0)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)

    monkeypatch.setattr(
        page_edit_window_module.QColorDialog,
        "getColor",
        staticmethod(lambda *a, **k: QColor(255, 120, 0)),
    )
    qtbot.mouseClick(window._zoom_note_color_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: list_note_annots(str(pdf_path), 0)[0].color == pytest.approx((1.0, 120 / 255, 0.0), abs=0.02)
    )


@pytest.mark.usefixtures("qtbot")
def test_note_list_populated_and_click_selects(qtbot, tmp_path):
    pdf_path = tmp_path / "note-list.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _place_note(window, 60.0, 70.0)
    qtbot.waitUntil(lambda: len(list_note_annots(str(pdf_path), 0)) == 1)
    window._zoom_note_editor.setPlainText("first note")
    window._commit_note_editor_if_dirty()
    qtbot.waitUntil(lambda: window._zoom_note_list.count() == 1)

    # 選択を解除してから一覧クリックで再選択
    window._set_selected_zoom_annotation(None)
    assert window._selected_zoom_annotation is None
    item = window._zoom_note_list.item(0)
    window._on_note_list_item_clicked(item)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)


@pytest.mark.usefixtures("qtbot")
def test_note_delete(qtbot, tmp_path):
    pdf_path = tmp_path / "note-del.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _place_note(window, 60.0, 70.0)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)

    window._delete_selected_zoom_annotation()
    qtbot.waitUntil(lambda: list_note_annots(str(pdf_path), 0) == [])


@pytest.mark.usefixtures("qtbot")
def test_note_hover_popup(qtbot, tmp_path):
    pdf_path = tmp_path / "note-hover.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    _place_note(window, 100.0, 100.0)
    qtbot.waitUntil(lambda: window._selected_zoom_annotation is not None)
    window._zoom_note_editor.setPlainText("hover text")
    window._commit_note_editor_if_dirty()
    qtbot.waitUntil(lambda: list_note_annots(str(pdf_path), 0)[0].content == "hover text")

    note = window._selected_zoom_annotation
    rect = window._zoom_label._note_widget_rect(note)
    window._zoom_label._update_note_hover(QPoint(int(rect.center().x()), int(rect.center().y())))
    assert window._zoom_label._note_popup is not None
    # アイコンから外れるとポップアップは消える
    window._zoom_label._update_note_hover(QPoint(5, 5))
    assert window._zoom_label._note_popup is None
