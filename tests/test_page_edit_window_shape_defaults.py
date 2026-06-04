"""注釈未選択（作成モード）時に、新規図形/FreeText のデフォルト値（線色など）を
編集できることを確認するテスト。"""

from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import (
    ShapeType,
    list_shape_annots,
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


def _drag_on_zoom_label(qtbot, window, start, end) -> None:
    qtbot.mousePress(window._zoom_label, Qt.MouseButton.LeftButton, pos=_page_click_pos(window, *start))
    qtbot.mouseMove(window._zoom_label, _page_click_pos(window, *end))
    qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.LeftButton, pos=_page_click_pos(window, *end))


def _enter_shape_mode(qtbot, window, shape_type: ShapeType) -> None:
    qtbot.mouseClick(window._shape_buttons[shape_type], Qt.MouseButton.LeftButton)


@pytest.mark.usefixtures("qtbot")
def test_shape_button_enables_style_controls_without_selection(qtbot, tmp_path):
    pdf_path = tmp_path / "shape-controls.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    # 何も選択していない初期状態では、線色などのコントロールは無効
    assert window._selected_zoom_annotation is None
    assert window._zoom_annotation_border_color_btn.isEnabled() is False
    assert window._zoom_annotation_border_width_spin.isEnabled() is False

    # 線図形ボタンを押すと作成モードに入り、線色・線幅・透明度が編集可能になる
    _enter_shape_mode(qtbot, window, ShapeType.LINE)
    assert window._zoom_create_mode == ShapeType.LINE
    assert window._selected_zoom_annotation is None
    assert window._zoom_annotation_border_color_btn.isEnabled() is True
    assert window._zoom_annotation_border_width_spin.isEnabled() is True
    assert window._zoom_annotation_opacity_slider.isEnabled() is True
    # 矢印オプションは LINE 作成時に表示、幅/高さは描画で確定するため非表示
    assert window._zoom_shape_arrow_options.isVisible() is True
    assert window._zoom_annotation_width_spin.isVisible() is False
    # 図形には文字サイズ・文字色は無関係なので非表示
    assert window._zoom_annotation_fontsize_spin.isVisible() is False

    # もう一度押して作成モードを抜けると、コントロールは再び無効になる
    _enter_shape_mode(qtbot, window, ShapeType.LINE)
    assert window._zoom_create_mode is None
    assert window._zoom_annotation_border_color_btn.isEnabled() is False


@pytest.mark.usefixtures("qtbot")
def test_pick_line_color_in_create_mode_applies_to_new_line(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "line-color.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    _enter_shape_mode(qtbot, window, ShapeType.LINE)

    # 色選択ダイアログを赤で確定したことにする
    monkeypatch.setattr(
        page_edit_window_module.QColorDialog,
        "getColor",
        staticmethod(lambda *a, **k: QColor(255, 0, 0)),
    )
    qtbot.mouseClick(window._zoom_annotation_border_color_btn, Qt.MouseButton.LeftButton)
    assert window._zoom_annotation_border_color == pytest.approx((1.0, 0.0, 0.0), abs=0.01)

    window._zoom_annotation_border_width_spin.setValue(3)

    _drag_on_zoom_label(qtbot, window, (60, 80), (200, 160))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 1)

    shape = list_shape_annots(str(pdf_path), 0)[0]
    assert shape.shape_type == ShapeType.LINE
    assert shape.stroke_color == pytest.approx((1.0, 0.0, 0.0), abs=0.02)
    assert shape.stroke_width == pytest.approx(3.0, abs=0.01)


@pytest.mark.usefixtures("qtbot")
def test_rectangle_create_mode_uses_default_border_and_fill(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "rect-color.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    _enter_shape_mode(qtbot, window, ShapeType.RECTANGLE)
    # 矩形では背景色も編集対象として有効化される
    assert window._zoom_annotation_fill_color_btn.isEnabled() is True

    colors = iter([QColor(0, 0, 255), QColor(0, 255, 0)])
    monkeypatch.setattr(
        page_edit_window_module.QColorDialog,
        "getColor",
        staticmethod(lambda *a, **k: next(colors)),
    )
    # 線色=青、背景色=緑
    qtbot.mouseClick(window._zoom_annotation_border_color_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window._zoom_annotation_fill_color_btn, Qt.MouseButton.LeftButton)

    _drag_on_zoom_label(qtbot, window, (60, 80), (200, 180))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 1)

    shape = list_shape_annots(str(pdf_path), 0)[0]
    assert shape.shape_type == ShapeType.RECTANGLE
    assert shape.stroke_color == pytest.approx((0.0, 0.0, 1.0), abs=0.02)
    assert shape.fill_color == pytest.approx((0.0, 1.0, 0.0), abs=0.02)


@pytest.mark.usefixtures("qtbot")
def test_triangle_apex_default_applies_to_new_triangle(qtbot, tmp_path):
    pdf_path = tmp_path / "triangle-apex.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    _enter_shape_mode(qtbot, window, ShapeType.TRIANGLE)
    assert window._zoom_shape_triangle_options.isVisible() is True
    # デフォルトは中央 (50%)
    assert window._zoom_shape_triangle_apex_x_spin.value() == 50
    window._zoom_shape_triangle_apex_x_spin.setValue(20)

    _drag_on_zoom_label(qtbot, window, (60, 80), (200, 180))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 1)

    shape = list_shape_annots(str(pdf_path), 0)[0]
    assert shape.shape_type == ShapeType.TRIANGLE
    assert shape.triangle_apex[0] == pytest.approx(0.2, abs=0.01)


@pytest.mark.usefixtures("qtbot")
def test_freetext_new_mode_enables_text_color_without_selection(qtbot, tmp_path):
    pdf_path = tmp_path / "ft-defaults.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    assert window._zoom_annotation_text_color_btn.isEnabled() is False

    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    assert window._zoom_create_mode == "freetext"
    # FreeText 新規作成モードでは文字色・背景色・線色が編集可能
    assert window._zoom_annotation_text_color_btn.isEnabled() is True
    assert window._zoom_annotation_fill_color_btn.isEnabled() is True
    assert window._zoom_annotation_border_color_btn.isEnabled() is True
    # 図形専用オプションは表示されない
    assert window._zoom_shape_arrow_options.isVisible() is False
    assert window._zoom_annotation_fontsize_spin.isVisible() is True

    # 新規ボタンを解除すると無効に戻る
    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    assert window._zoom_create_mode is None
    assert window._zoom_annotation_text_color_btn.isEnabled() is False


@pytest.mark.usefixtures("qtbot")
def test_switching_from_freetext_to_shape_mode_updates_panel(qtbot, tmp_path):
    pdf_path = tmp_path / "switch-mode.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    assert window._zoom_create_mode == "freetext"
    assert window._zoom_annotation_fontsize_spin.isVisible() is True

    # 図形ボタンに切り替えると FreeText 新規は解除され、図形向け表示になる
    _enter_shape_mode(qtbot, window, ShapeType.BRACKET)
    assert window._zoom_create_mode == ShapeType.BRACKET
    assert window._zoom_annotation_new_btn.isChecked() is False
    assert window._zoom_shape_bracket_options.isVisible() is True
    assert window._zoom_annotation_fontsize_spin.isVisible() is False
    assert window._zoom_annotation_border_color_btn.isEnabled() is True


@pytest.mark.usefixtures("qtbot")
def test_shape_button_while_annotation_selected_enters_create_mode(qtbot, tmp_path):
    """線を選択（アクティベイト）中に□を押したら、その線の編集ではなく
    □の新規配置モードへ切り替わること（選択が解除され作成デフォルトを表示）。"""
    pdf_path = tmp_path / "select-then-shape.pdf"
    _make_pdf(pdf_path)

    window = _create_window(qtbot, pdf_path)
    _open_zoom(window, qtbot)

    # 線を1本描く → 作成直後はその線が選択（アクティベイト）状態になる
    _enter_shape_mode(qtbot, window, ShapeType.LINE)
    _drag_on_zoom_label(qtbot, window, (60, 80), (200, 160))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 1)
    qtbot.waitUntil(
        lambda: window._selected_zoom_annotation is not None
        and window._selected_zoom_annotation.shape_type == ShapeType.LINE
    )
    assert window._zoom_create_mode is None

    # 線を選択中の状態で □ を押す
    _enter_shape_mode(qtbot, window, ShapeType.RECTANGLE)

    # □ の新規配置モードに入り、選択していた線は解除される
    assert window._zoom_create_mode == ShapeType.RECTANGLE
    assert window._selected_zoom_annotation is None
    assert window._zoom_label._annotation_create_mode is True
    assert window._zoom_label._annotation_create_shape_type == ShapeType.RECTANGLE
    assert window._zoom_label._selected_annotation_xref is None
    # パネルは矩形作成向け表示（背景色が有効・文字サイズ非表示）
    assert window._zoom_annotation_fill_color_btn.isEnabled() is True
    assert window._zoom_annotation_fontsize_spin.isVisible() is False

    # ドラッグで矩形が1つ追加される（線はそのまま残る）
    _drag_on_zoom_label(qtbot, window, (60, 200), (200, 300))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 2)
    types = {s.shape_type for s in list_shape_annots(str(pdf_path), 0)}
    assert types == {ShapeType.LINE, ShapeType.RECTANGLE}
