from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QRectF, Qt

from src.utils.pdf_utils import FreeTextAnnotData, create_freetext_annot, list_freetext_annots
from src.views.page_edit_window import PageEditWindow
from tests.helpers import create_page_edit_window, make_pdf, open_zoom, page_click_pos


def _make_freetext(pdf_path) -> int:
    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="move",
            fontsize=14,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.6),
            border_color=(0.0, 0.0, 0.0),
            border_width=2,
            opacity=1.0,
        ),
    )
    return created.xref


def _select_annotation(window: PageEditWindow, qtbot, xref: int) -> None:
    annot = next(a for a in window._zoom_annotations if a.xref == xref)
    rect = window._zoom_label._annotation_widget_rect(annot)
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(
        lambda: window._selected_zoom_annotation is not None
        and window._selected_zoom_annotation.xref == xref
    )


@pytest.mark.usefixtures("qtbot")
def test_ctrl_arrow_moves_selected_annotation_by_coarse_step(qtbot, tmp_path):
    pdf_path = tmp_path / "ctrl-move.pdf"
    make_pdf(pdf_path)
    xref = _make_freetext(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _select_annotation(window, qtbot, xref)

    coarse = window._zoom_label.ANNOTATION_MOVE_STEP_COARSE
    assert coarse > window._zoom_label.ANNOTATION_MOVE_STEP

    qtbot.keyClick(window._zoom_label, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].rect[0] != 40.0)
    moved = list_freetext_annots(str(pdf_path), 0)[0].rect
    assert moved[0] == pytest.approx(40.0 + coarse, abs=0.5)
    assert moved[1] == pytest.approx(50.0, abs=0.5)


@pytest.mark.usefixtures("qtbot")
def test_arrow_scrolls_view_when_no_annotation_selected(qtbot, tmp_path):
    pdf_path = tmp_path / "scroll.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._zoom_label.setFocus()

    step = window._zoom_label.ZOOM_SCROLL_STEP
    with qtbot.waitSignal(window._zoom_label.scroll_requested, timeout=1000) as blocker:
        qtbot.keyClick(window._zoom_label, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    assert blocker.args == [0, step]


@pytest.mark.usefixtures("qtbot")
def test_ctrl_arrow_scrolls_view_faster_when_no_annotation_selected(qtbot, tmp_path):
    pdf_path = tmp_path / "ctrl-scroll.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._zoom_label.setFocus()

    fast = window._zoom_label.ZOOM_SCROLL_STEP_FAST
    assert fast > window._zoom_label.ZOOM_SCROLL_STEP
    with qtbot.waitSignal(window._zoom_label.scroll_requested, timeout=1000) as blocker:
        qtbot.keyClick(window._zoom_label, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    assert blocker.args == [0, fast]


@pytest.mark.usefixtures("qtbot")
def test_middle_drag_pans_view(qtbot, tmp_path):
    pdf_path = tmp_path / "pan.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    start = page_click_pos(window, 60, 60)
    end = QPoint(start.x() + 40, start.y() + 30)
    with qtbot.waitSignal(window._zoom_label.scroll_requested, timeout=1000):
        qtbot.mousePress(window._zoom_label, Qt.MouseButton.MiddleButton, pos=start)
        qtbot.mouseMove(window._zoom_label, end)
    qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.MiddleButton, pos=end)
    assert window._zoom_label._pan_active is False


@pytest.mark.usefixtures("qtbot")
def test_right_drag_marquee_zooms_into_region(qtbot, tmp_path):
    pdf_path = tmp_path / "marquee.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    assert window._zoom_factor == pytest.approx(1.0)

    start = page_click_pos(window, 40, 40)
    end = page_click_pos(window, 130, 150)
    with qtbot.waitSignal(window._zoom_label.zoom_region_requested, timeout=1000) as blocker:
        qtbot.mousePress(window._zoom_label, Qt.MouseButton.RightButton, pos=start)
        qtbot.mouseMove(window._zoom_label, end)
        qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.RightButton, pos=end)

    page_rect = blocker.args[0]
    assert isinstance(page_rect, QRectF)
    assert page_rect.width() > 0 and page_rect.height() > 0

    # 範囲がビューポートより小さいので拡大されているはず（クランプ上限まで）。
    viewport = window._zoom_scroll.viewport()
    fit = min(viewport.width() / page_rect.width(), viewport.height() / page_rect.height())
    expected = max(window.ZOOM_MIN, min(window.ZOOM_MAX, int(fit * 100)))
    assert int(round(window._zoom_factor * 100)) == expected
    assert window._zoom_reset_btn.text() == f"{expected}%"


@pytest.mark.usefixtures("qtbot")
def test_tiny_right_drag_does_not_zoom(qtbot, tmp_path):
    pdf_path = tmp_path / "marquee-tiny.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    start = page_click_pos(window, 60, 60)
    end = QPoint(start.x() + 2, start.y() + 2)
    with qtbot.assertNotEmitted(window._zoom_label.zoom_region_requested):
        qtbot.mousePress(window._zoom_label, Qt.MouseButton.RightButton, pos=start)
        qtbot.mouseMove(window._zoom_label, end)
        qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.RightButton, pos=end)
    assert window._zoom_factor == pytest.approx(1.0)


@pytest.mark.usefixtures("qtbot")
def test_zoom_preset_dropdown_sets_percent_and_button_text(qtbot, tmp_path):
    pdf_path = tmp_path / "preset.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    # 25% / 100% / 400% は必須。
    assert {25, 100, 400}.issubset(set(window.ZOOM_PRESETS))

    menu = window._zoom_reset_btn.menu()
    assert menu is not None
    labels = [action.text() for action in menu.actions()]
    assert labels == [f"{p}%" for p in window.ZOOM_PRESETS]

    # メニュー項目を発火させると倍率とボタン表示が更新される。
    action_400 = next(a for a in menu.actions() if a.text() == "400%")
    action_400.trigger()
    assert window._zoom_factor == pytest.approx(4.0)
    assert window._zoom_reset_btn.text() == "400%"

    action_25 = next(a for a in menu.actions() if a.text() == "25%")
    action_25.trigger()
    assert window._zoom_factor == pytest.approx(0.25)
    assert window._zoom_reset_btn.text() == "25%"
