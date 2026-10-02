"""拡大ビューの「連続」トグルが、図形・ノートの作成にも効くことのテスト。

「連続」ONの間、図形(線/三角形/楕円/四角形/括弧)とノートは作成後に配置待ちを
解除せず、作成物も選択しない。連続OFFのときは従来どおり単発(作成後に解除・
作成物を選択してドロワーを開く)。校正・テキストボックスは連続ONでも単発のまま。
実際のマウスイベント(press/move/release)を ZoomPageWidget へ送って検証する。
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.utils.pdf_utils import (
    NoteAnnotData,
    ShapeAnnotData,
    ShapeType,
    list_freetext_annots,
    list_note_annots,
    list_pii_mask_shapes,
    list_shape_annots,
)
from src.views.page_edit_annotations import CreateMode
from tests.helpers import create_page_edit_window, make_pdf, open_zoom, page_click_pos

_SHAPE_TYPES = [
    ShapeType.LINE,
    ShapeType.TRIANGLE,
    ShapeType.ELLIPSE,
    ShapeType.RECTANGLE,
    ShapeType.BRACKET,
]
# 2回のドラッグ(ページ座標)。互いに重ならない位置にして、2個の作成を区別できるようにする。
_DRAG_1 = ((40, 200), (140, 260))
_DRAG_2 = ((180, 300), (280, 380))


def _open_window(qtbot, tmp_path, *, pages: int = 1):
    pdf_path = tmp_path / "continuous-create.pdf"
    make_pdf(pdf_path, pages=pages)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    return window, pdf_path


def _drag(qtbot, window, drag) -> None:
    (x0, y0), (x1, y1) = drag
    label = window._zoom_label
    qtbot.mousePress(label, Qt.MouseButton.LeftButton, pos=page_click_pos(window, x0, y0))
    qtbot.mouseMove(label, page_click_pos(window, (x0 + x1) // 2, (y0 + y1) // 2))
    qtbot.mouseMove(label, page_click_pos(window, x1, y1))
    qtbot.mouseRelease(label, Qt.MouseButton.LeftButton, pos=page_click_pos(window, x1, y1))


def _click(qtbot, window, x: int, y: int) -> None:
    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=page_click_pos(window, x, y))


def _click_continuous(qtbot, window) -> None:
    qtbot.mouseClick(window._markup_continuous_btn, Qt.MouseButton.LeftButton)


def _shape_count(pdf_path, page: int = 0) -> int:
    return len(list_shape_annots(str(pdf_path), page))


def _note_count(pdf_path, page: int = 0) -> int:
    return len(list_note_annots(str(pdf_path), page))


def _activate(qtbot, window) -> None:
    """ウィンドウをアクティブにする(フォーカスの所在を検証するテスト用)。"""
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)


def _press_escape(qtbot, window) -> None:
    window._zoom_label.setFocus()
    qtbot.keyClick(window._zoom_label, Qt.Key.Key_Escape)


def _assert_nothing_selected(window) -> None:
    assert window._selected_zoom_annotation is None
    assert window._zoom_label._selected_annotation_xref is None


def _assert_no_create_tool(window) -> None:
    """作成ツールの配置待ちが window・ボタン・widget のどこにも残っていない。"""
    assert window._create_mode is CreateMode.NONE
    assert not any(btn.isChecked() for btn in window._shape_buttons.values())
    assert window._zoom_note_btn.isChecked() is False
    assert window._zoom_label._annotation_create_mode is False
    assert window._zoom_label._note_create_mode is False
    assert window._zoom_create_mode is None


# ---------------------------------------------------------------------------
# 連続ON: 図形
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape_type", _SHAPE_TYPES, ids=lambda st: st.name)
def test_continuous_on_shape_stays_armed_and_creates_repeatedly(qtbot, tmp_path, shape_type):
    window, pdf_path = _open_window(qtbot, tmp_path)
    btn = window._shape_buttons[shape_type]

    _click_continuous(qtbot, window)
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.SHAPE

    # ツールボタンは1回しか押さずに、2回ドラッグして2個作る。
    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 1)
    assert window._create_mode is CreateMode.SHAPE
    _assert_nothing_selected(window)
    _drag(qtbot, window, _DRAG_2)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 2)

    # 配置待ちは window・ボタン・widget・作成パネルのすべてで維持され、作成物は選択されない。
    assert window._create_mode is CreateMode.SHAPE
    assert btn.isChecked() is True
    assert window._zoom_label._annotation_create_mode is True
    assert window._zoom_label._annotation_create_shape_type == shape_type
    assert window._zoom_create_mode == shape_type
    assert window._markup_continuous_btn.isChecked() is True
    _assert_nothing_selected(window)
    assert {a.shape_type for a in list_shape_annots(str(pdf_path), 0)} == {shape_type}


def test_continuous_on_bracket_both_sides_stays_armed(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    btn = window._shape_buttons[ShapeType.BRACKET]

    _click_continuous(qtbot, window)
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    window._zoom_shape_bracket_both_cb.setChecked(True)

    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 2)  # 括弧ペアは2個
    assert window._create_mode is CreateMode.SHAPE
    _assert_nothing_selected(window)
    _drag(qtbot, window, _DRAG_2)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 4)

    assert window._create_mode is CreateMode.SHAPE
    assert btn.isChecked() is True
    assert window._zoom_label._annotation_create_mode is True
    _assert_nothing_selected(window)


def test_continuous_on_shape_undo_keeps_armed(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._shape_buttons[ShapeType.RECTANGLE], Qt.MouseButton.LeftButton)
    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 1)

    window._on_undo()
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 0)
    assert window._create_mode is CreateMode.SHAPE
    assert window._shape_buttons[ShapeType.RECTANGLE].isChecked() is True

    # Undo のあとも、そのまま続けて作れる。
    _drag(qtbot, window, _DRAG_2)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 1)


# ---------------------------------------------------------------------------
# 連続ON: ノート
# ---------------------------------------------------------------------------


def test_continuous_on_note_stays_armed_and_keeps_focus_on_page(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _activate(qtbot, window)

    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.NOTE

    _click(qtbot, window, 200, 200)
    qtbot.waitUntil(lambda: _note_count(pdf_path) == 1)
    assert window._create_mode is CreateMode.NOTE
    _assert_nothing_selected(window)
    _click(qtbot, window, 100, 300)
    qtbot.waitUntil(lambda: _note_count(pdf_path) == 2)

    assert window._create_mode is CreateMode.NOTE
    assert window._zoom_note_btn.isChecked() is True
    assert window._zoom_label._note_create_mode is True
    assert window._markup_continuous_btn.isChecked() is True
    _assert_nothing_selected(window)
    # 本文欄は(選択が無いので)表示されず、フォーカスも移らない。
    assert window._zoom_note_editor.isVisible() is False
    assert window._zoom_note_editor.hasFocus() is False
    # フォーカスはページ(widget)に残るので、Esc がそのまま届く。
    assert QApplication.focusWidget() is window._zoom_label


# ---------------------------------------------------------------------------
# 連続OFF: 従来どおり単発(作成後に解除・作成物を選択してドロワーを開く)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape_type", _SHAPE_TYPES, ids=lambda st: st.name)
def test_continuous_off_shape_is_one_shot_and_selects_created(qtbot, tmp_path, shape_type):
    window, pdf_path = _open_window(qtbot, tmp_path)
    assert window._markup_continuous_btn.isChecked() is False

    qtbot.mouseClick(window._shape_buttons[shape_type], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.SHAPE
    # ドロワーは図形ボタンで開くので、いったん閉じて作成で開き直ることを確かめる。
    window._set_zoom_annotation_drawer_open(False)

    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 1)

    _assert_no_create_tool(window)
    assert isinstance(window._selected_zoom_annotation, ShapeAnnotData)
    assert window._zoom_label._selected_annotation_xref == window._selected_zoom_annotation.xref
    assert window._zoom_annotation_open is True
    assert window._zoom_annotation_panel.isVisible() is True

    # 配置待ちを抜けているので、続けてドラッグしても2個目は作られない。
    _drag(qtbot, window, _DRAG_2)
    qtbot.wait(100)
    assert _shape_count(pdf_path) == 1


def test_continuous_off_bracket_both_sides_is_one_shot(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    qtbot.mouseClick(window._shape_buttons[ShapeType.BRACKET], Qt.MouseButton.LeftButton)
    window._zoom_shape_bracket_both_cb.setChecked(True)
    window._set_zoom_annotation_drawer_open(False)

    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 2)

    _assert_no_create_tool(window)
    assert isinstance(window._selected_zoom_annotation, ShapeAnnotData)
    assert window._zoom_annotation_open is True
    assert window._zoom_annotation_panel.isVisible() is True


def test_continuous_off_note_is_one_shot_and_focuses_editor(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _activate(qtbot, window)

    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.NOTE
    window._set_zoom_annotation_drawer_open(False)

    _click(qtbot, window, 200, 200)
    qtbot.waitUntil(lambda: _note_count(pdf_path) == 1)

    _assert_no_create_tool(window)
    assert isinstance(window._selected_zoom_annotation, NoteAnnotData)
    assert window._zoom_label._selected_annotation_xref == window._selected_zoom_annotation.xref
    assert window._zoom_annotation_open is True
    assert window._zoom_note_editor.isVisible() is True
    # 本文をすぐ入力できるよう、フォーカスは本文欄へ移る(従来どおり)。
    qtbot.waitUntil(lambda: window._zoom_note_editor.hasFocus())

    # 配置待ちを抜けているので、続けてクリックしても2個目は作られない。
    _click(qtbot, window, 100, 300)
    qtbot.wait(100)
    assert _note_count(pdf_path) == 1


# ---------------------------------------------------------------------------
# Esc / トグルOFF でツールを解除
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape_type", [ShapeType.RECTANGLE, ShapeType.BRACKET], ids=lambda st: st.name)
def test_continuous_on_escape_releases_shape_tool_but_keeps_toggle(qtbot, tmp_path, shape_type):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._shape_buttons[shape_type], Qt.MouseButton.LeftButton)
    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 1)
    assert window._create_mode is CreateMode.SHAPE

    _press_escape(qtbot, window)

    _assert_no_create_tool(window)
    assert window._markup_continuous_btn.isChecked() is True
    assert window._markup_continuous_mode is True
    # 解除後は図形が作られない。
    _drag(qtbot, window, _DRAG_2)
    qtbot.wait(100)
    assert _shape_count(pdf_path) == 1


def test_continuous_on_escape_releases_note_tool_even_after_placing(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _activate(qtbot, window)
    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    _click(qtbot, window, 200, 200)
    qtbot.waitUntil(lambda: _note_count(pdf_path) == 1)
    assert window._create_mode is CreateMode.NOTE

    # 配置直後でも Esc が widget に届いて、ツールだけ解除される(キーはフォーカス中の
    # ウィジェットへ送るので、フォーカスが本文欄などへ移っていれば解除されない)。
    focus = QApplication.focusWidget()
    assert focus is window._zoom_label
    qtbot.keyClick(focus, Qt.Key.Key_Escape)

    _assert_no_create_tool(window)
    assert window._markup_continuous_btn.isChecked() is True
    _click(qtbot, window, 100, 300)
    qtbot.wait(100)
    assert _note_count(pdf_path) == 1


def test_continuous_off_escape_does_not_release_shape_or_note(qtbot, tmp_path):
    """連続OFFの単発の配置待ちは、従来どおり Esc では解除されない。"""
    window, _pdf_path = _open_window(qtbot, tmp_path)

    qtbot.mouseClick(window._shape_buttons[ShapeType.RECTANGLE], Qt.MouseButton.LeftButton)
    _press_escape(qtbot, window)
    assert window._create_mode is CreateMode.SHAPE
    assert window._shape_buttons[ShapeType.RECTANGLE].isChecked() is True

    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
    _press_escape(qtbot, window)
    assert window._create_mode is CreateMode.NOTE
    assert window._zoom_note_btn.isChecked() is True


@pytest.mark.parametrize("tool", ["shape", "note"])
def test_toggling_continuous_off_releases_shape_and_note_tool(qtbot, tmp_path, tool):
    window, _pdf_path = _open_window(qtbot, tmp_path)
    _click_continuous(qtbot, window)
    if tool == "shape":
        qtbot.mouseClick(window._shape_buttons[ShapeType.ELLIPSE], Qt.MouseButton.LeftButton)
        assert window._create_mode is CreateMode.SHAPE
    else:
        qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)
        assert window._create_mode is CreateMode.NOTE

    _click_continuous(qtbot, window)  # OFF

    assert window._markup_continuous_mode is False
    _assert_no_create_tool(window)


# ---------------------------------------------------------------------------
# 校正・テキストボックスは連続ONでも単発
# ---------------------------------------------------------------------------


def test_continuous_on_callout_is_still_one_shot(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._zoom_callout_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.CALLOUT

    _click(qtbot, window, 200, 200)
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    assert window._create_mode is CreateMode.NONE
    assert window._zoom_callout_btn.isChecked() is False
    assert window._zoom_label._callout_create_mode is False
    assert window._markup_continuous_btn.isChecked() is True  # トグル自体は維持


def test_continuous_on_freetext_is_still_one_shot(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path)
    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._zoom_annotation_new_btn, Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.FREETEXT

    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    assert window._create_mode is CreateMode.NONE
    assert window._zoom_annotation_new_btn.isChecked() is False
    assert window._zoom_label._annotation_create_mode is False
    assert window._markup_continuous_btn.isChecked() is True  # トグル自体は維持


def test_mask_shape_tool_is_independent_of_continuous_toggle_and_escape(qtbot, tmp_path):
    """個人情報検出の塗りつぶし図形ツールは別系統: 「連続」トグルとEscでは解除されない。"""
    window, pdf_path = _open_window(qtbot, tmp_path)
    window._toggle_pii_drawer()
    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)

    _click_continuous(qtbot, window)  # ON
    _click_continuous(qtbot, window)  # OFF
    assert window._create_mode is CreateMode.MASK_SHAPE
    _press_escape(qtbot, window)
    assert window._create_mode is CreateMode.MASK_SHAPE
    assert window._zoom_label._annotation_create_mode is True

    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: len(list_pii_mask_shapes(str(pdf_path), 0)) == 1)
    assert window._create_mode is CreateMode.MASK_SHAPE  # 従来どおり連続して描ける


# ---------------------------------------------------------------------------
# ページ移動: window・ボタン・widget の状態が食い違わない
# ---------------------------------------------------------------------------


def _wait_for_zoom_page(qtbot, window, page: int) -> None:
    qtbot.waitUntil(
        lambda: window._zoom_page_num == page
        and window._zoom_label is not None
        and window._zoom_label._pixmap is not None
        and not window._zoom_label._pixmap.isNull()
    )


def test_continuous_on_shape_survives_page_move_consistently(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path, pages=3)
    btn = window._shape_buttons[ShapeType.RECTANGLE]
    _click_continuous(qtbot, window)
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path, 0) == 1)

    # 次ページ・最終ページ・先頭ページ・前ページ・しおり/検索ジャンプのどれでも維持される。
    moves = [
        (1, window._on_zoom_next_page),
        (2, window._on_zoom_last_page),
        (0, window._on_zoom_first_page),
        (1, lambda: window._jump_zoom_to_page(2)),
        (0, window._on_zoom_prev_page),
        (2, lambda: window._jump_to_search_page(2)),
    ]
    for page, move in moves:
        move()
        _wait_for_zoom_page(qtbot, window, page)
        assert window._create_mode is CreateMode.SHAPE
        assert btn.isChecked() is True
        assert window._zoom_label._annotation_create_mode is True
        assert window._zoom_label._annotation_create_shape_type == ShapeType.RECTANGLE
        assert window._zoom_create_mode == ShapeType.RECTANGLE
        assert window._markup_continuous_btn.isChecked() is True

    # 移動先のページでも、ボタンを押し直さずに作れる。
    _drag(qtbot, window, _DRAG_2)
    qtbot.waitUntil(lambda: _shape_count(pdf_path, 2) == 1)
    assert window._create_mode is CreateMode.SHAPE
    _assert_nothing_selected(window)


def test_continuous_on_note_survives_page_move(qtbot, tmp_path):
    window, pdf_path = _open_window(qtbot, tmp_path, pages=2)
    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._zoom_note_btn, Qt.MouseButton.LeftButton)

    window._on_zoom_next_page()
    _wait_for_zoom_page(qtbot, window, 1)

    assert window._create_mode is CreateMode.NOTE
    assert window._zoom_note_btn.isChecked() is True
    assert window._zoom_label._note_create_mode is True
    _click(qtbot, window, 200, 200)
    qtbot.waitUntil(lambda: _note_count(pdf_path, 1) == 1)
    assert window._create_mode is CreateMode.NOTE


def test_continuous_off_shape_tool_is_released_consistently_on_page_move(qtbot, tmp_path):
    """連続OFFの単発の図形ツールは、ページ移動で解除される(ボタンも一緒に戻る)。

    従来は widget 側だけが解除され、ボタンの押下表示と window の状態が残って
    食い違っていた。
    """
    window, pdf_path = _open_window(qtbot, tmp_path, pages=2)
    qtbot.mouseClick(window._shape_buttons[ShapeType.RECTANGLE], Qt.MouseButton.LeftButton)

    window._on_zoom_next_page()
    _wait_for_zoom_page(qtbot, window, 1)

    _assert_no_create_tool(window)
    _drag(qtbot, window, _DRAG_1)
    qtbot.wait(100)
    assert _shape_count(pdf_path, 1) == 0


def test_mask_shape_tool_survives_page_move_consistently(qtbot, tmp_path):
    """個人情報検出の塗りつぶし図形ツールも、ページ移動後に window・ボタン・widget が揃って維持される。

    従来は widget 側だけが解除され、ボタンが点灯したまま描けない状態になっていた。
    """
    window, pdf_path = _open_window(qtbot, tmp_path, pages=2)
    window._toggle_pii_drawer()
    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)

    window._on_zoom_next_page()
    _wait_for_zoom_page(qtbot, window, 1)

    assert window._create_mode is CreateMode.MASK_SHAPE
    assert window._pii_panel._mask_rect_btn.isChecked() is True
    assert window._zoom_label._annotation_create_mode is True
    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: len(list_pii_mask_shapes(str(pdf_path), 1)) == 1)
    assert window._create_mode is CreateMode.MASK_SHAPE


def test_paste_releases_shape_tool_consistently_even_when_continuous(qtbot, tmp_path):
    """貼り付けは貼った注釈を選択するため、図形ツールは連続ONでも解除する(状態は食い違わない)。"""
    window, pdf_path = _open_window(qtbot, tmp_path)
    qtbot.mouseClick(window._shape_buttons[ShapeType.RECTANGLE], Qt.MouseButton.LeftButton)
    _drag(qtbot, window, _DRAG_1)
    qtbot.waitUntil(lambda: _shape_count(pdf_path) == 1)
    window._on_zoom_annotation_copy_requested(window._selected_zoom_annotation)
    assert window._copied_zoom_annotation is not None

    _click_continuous(qtbot, window)
    qtbot.mouseClick(window._shape_buttons[ShapeType.ELLIPSE], Qt.MouseButton.LeftButton)
    assert window._create_mode is CreateMode.SHAPE

    window._on_zoom_annotation_paste_requested()

    _assert_no_create_tool(window)
