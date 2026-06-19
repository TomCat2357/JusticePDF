"""ズームビューの校正コールアウト UI のテスト。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from src.utils.pdf_utils import (
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
def test_callout_create_builds_single_freetext(qtbot, tmp_path):
    pdf_path = tmp_path / "callout-create.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._on_callout_btn_clicked(True)
    window._on_callout_create_requested((200.0, 300.0))
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    fts = list_freetext_annots(str(pdf_path), 0)
    shapes = list_shape_annots(str(pdf_path), 0)
    # 校正コールアウトは単一の FreeText オブジェクト。図形は作られない。
    assert len(fts) == 1
    assert len(shapes) == 0
    assert len(fts[0].callout_line) == 2
    assert fts[0].callout_target == (200.0, 300.0)
    # 配置後はモードを抜ける。
    assert window._callout_create_mode is False


@pytest.mark.usefixtures("qtbot")
def test_callout_delete_and_undo(qtbot, tmp_path):
    pdf_path = tmp_path / "callout-del.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._on_callout_btn_clicked(True)
    window._on_callout_create_requested((200.0, 300.0))
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    # 1 個のオブジェクトとして選択・削除できる。
    fts = list_freetext_annots(str(pdf_path), 0)
    window._set_selected_zoom_annotation(window._find_zoom_annotation(fts[0].xref))
    window._delete_selected_zoom_annotation()
    qtbot.waitUntil(lambda: not list_freetext_annots(str(pdf_path), 0))

    # Undo で復活し、コールアウト情報も保持される。
    window._undo_manager.undo()
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)
    restored = list_freetext_annots(str(pdf_path), 0)[0]
    assert len(restored.callout_line) == 2


@pytest.mark.usefixtures("qtbot")
def test_callout_move_keeps_target_fixed(qtbot, tmp_path):
    pdf_path = tmp_path / "callout-move.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._on_callout_btn_clicked(True)
    window._on_callout_create_requested((200.0, 300.0))
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    annot = list_freetext_annots(str(pdf_path), 0)[0]
    target_before = annot.callout_target
    old_rect = annot.rect
    # 本文ボックスだけを移動する。
    new_rect = (old_rect[0] + 30.0, old_rect[1] + 20.0, old_rect[2] + 30.0, old_rect[3] + 20.0)
    window._on_zoom_annotation_geometry_changed(annot, new_rect, "move")
    qtbot.waitUntil(
        lambda: list_freetext_annots(str(pdf_path), 0)[0].rect[0] == pytest.approx(new_rect[0], abs=0.5)
    )

    moved = list_freetext_annots(str(pdf_path), 0)[0]
    # 挿入位置（target）は固定されたまま、コールアウトは 1 個のまま。
    assert moved.callout_target == pytest.approx(target_before, abs=0.5)
    assert len(list_freetext_annots(str(pdf_path), 0)) == 1
    assert len(list_shape_annots(str(pdf_path), 0)) == 0
