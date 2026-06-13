"""ズームビューの校正コールアウト UI のテスト。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from src.utils.pdf_utils import (
    FreeTextAnnotData,
    ShapeType,
    list_freetext_annots,
    list_shape_annots,
)
from tests.helpers import create_page_edit_window, make_pdf, open_zoom


@pytest.mark.usefixtures("qtbot")
def test_callout_button_enters_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "callout-mode.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    qtbot.mouseClick(window._zoom_callout_btn, Qt.MouseButton.LeftButton)
    assert window._callout_create_mode is True
    assert window._zoom_label._callout_create_mode is True

    qtbot.mouseClick(window._zoom_callout_btn, Qt.MouseButton.LeftButton)
    assert window._callout_create_mode is False


@pytest.mark.usefixtures("qtbot")
def test_callout_create_builds_grouped_annotations(qtbot, tmp_path):
    pdf_path = tmp_path / "callout-create.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._on_callout_btn_clicked(True)
    window._on_callout_create_requested((200.0, 300.0))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 2)

    fts = list_freetext_annots(str(pdf_path), 0)
    shapes = list_shape_annots(str(pdf_path), 0)
    assert len(fts) == 1
    gid = fts[0].group_id
    assert gid
    # 横向き括弧 + 引き出し線、どちらも同じ group。
    assert all(s.group_id == gid for s in shapes)
    assert any(s.shape_type == ShapeType.BRACKET and s.bracket_orientation == "horizontal" for s in shapes)
    assert any(s.shape_type == ShapeType.LINE for s in shapes)
    # 配置後はモードを抜ける。
    assert window._callout_create_mode is False


@pytest.mark.usefixtures("qtbot")
def test_callout_group_delete_and_undo(qtbot, tmp_path):
    pdf_path = tmp_path / "callout-del.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._on_callout_btn_clicked(True)
    window._on_callout_create_requested((200.0, 300.0))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 2)

    # 一部（本文ボックス）を選択して削除 → グループ全体が消える。
    fts = list_freetext_annots(str(pdf_path), 0)
    window._set_selected_zoom_annotation(window._find_zoom_annotation(fts[0].xref))
    window._delete_selected_zoom_annotation()
    qtbot.waitUntil(
        lambda: not list_freetext_annots(str(pdf_path), 0)
        and not list_shape_annots(str(pdf_path), 0)
    )

    # Undo でグループが復活。
    window._undo_manager.undo()
    qtbot.waitUntil(
        lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1
        and len(list_shape_annots(str(pdf_path), 0)) == 2
    )
