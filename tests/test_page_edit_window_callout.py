"""ズームビューの校正コールアウト UI のテスト。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from src.utils.pdf_utils import (
    ShapeAnnotData,
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


def _make_one_callout(window, qtbot, pdf_path):
    """吹き出しを 1 個作り、開いたインライン編集を閉じて返す。"""
    window._on_callout_btn_clicked(True)
    window._on_callout_create_requested((200.0, 300.0))
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)
    # 作成直後に開く本文インライン編集を閉じ、以降の Undo を決定的にする。
    window._zoom_label.cancel_annotation_text_edit()
    return list_freetext_annots(str(pdf_path), 0)[0]


@pytest.mark.usefixtures("qtbot")
def test_callout_paste_move_undo_twice_does_not_crash(qtbot, tmp_path):
    """コピー&貼り付け → 本文ボックス移動 → Undo×2 で xref エラーにならない。

    回帰: 貼り付けの Undo が作成時 xref を握ったままで、移動の delete+recreate により
    その xref が消えていたため「xref ... is not an annot of this page」で落ちていた。
    """
    pdf_path = tmp_path / "callout-paste-move-undo.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    original = _make_one_callout(window, qtbot, pdf_path)

    # コピーして、少しずらした位置に貼り付ける（吹き出しが 2 個になる）。
    window._copied_zoom_annotation = original
    r = original.rect
    paste_rect = (r[0] + 40.0, r[1] + 40.0, r[2] + 40.0, r[3] + 40.0)
    window._on_zoom_annotation_paste_placement_requested(paste_rect)
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)
    pasted = next(a for a in list_freetext_annots(str(pdf_path), 0) if a.xref != original.xref)

    # 貼り付けた吹き出しの本文ボックスを移動する。
    pr = pasted.rect
    move_rect = (pr[0] + 30.0, pr[1] + 20.0, pr[2] + 30.0, pr[3] + 20.0)
    window._on_zoom_annotation_geometry_changed(pasted, move_rect, "move")
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)

    # Undo×2: 移動 → 貼り付け を取り消す。例外を投げず、元の 1 個に戻る。
    window._undo_manager.undo()  # 移動の取り消し
    window._undo_manager.undo()  # 貼り付けの取り消し
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    remaining = list_freetext_annots(str(pdf_path), 0)[0]
    assert remaining.callout_target == pytest.approx(original.callout_target, abs=0.5)


@pytest.mark.usefixtures("qtbot")
def test_callout_paste_move_undo_then_redo_no_duplicate(qtbot, tmp_path):
    """Undo×2 後の Redo×2 で吹き出しが二重生成されず 2 個に戻る。"""
    pdf_path = tmp_path / "callout-paste-move-redo.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    original = _make_one_callout(window, qtbot, pdf_path)

    window._copied_zoom_annotation = original
    r = original.rect
    window._on_zoom_annotation_paste_placement_requested(
        (r[0] + 40.0, r[1] + 40.0, r[2] + 40.0, r[3] + 40.0)
    )
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)
    pasted = next(a for a in list_freetext_annots(str(pdf_path), 0) if a.xref != original.xref)

    pr = pasted.rect
    window._on_zoom_annotation_geometry_changed(
        pasted, (pr[0] + 30.0, pr[1] + 20.0, pr[2] + 30.0, pr[3] + 20.0), "move"
    )
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)

    window._undo_manager.undo()
    window._undo_manager.undo()
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 1)

    # Redo×2: 貼り付け → 移動 をやり直す。重複せず 2 個。
    window._undo_manager.redo()  # 貼り付けのやり直し
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)
    window._undo_manager.redo()  # 移動のやり直し
    qtbot.waitUntil(lambda: len(list_freetext_annots(str(pdf_path), 0)) == 2)
    assert len(list_freetext_annots(str(pdf_path), 0)) == 2


@pytest.mark.usefixtures("qtbot")
def test_shape_paste_move_undo_twice_does_not_crash(qtbot, tmp_path):
    """図形でも 貼り付け → 移動 → Undo×2 が落ちない（同じ xref 取り残しバグ）。"""
    pdf_path = tmp_path / "shape-paste-move-undo.pdf"
    make_pdf(pdf_path, width=400, height=500)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    clip = ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(60.0, 60.0, 160.0, 120.0),
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(0.0, 0.0, 0.0),
        fill_color=None,
        stroke_width=1.0,
        opacity=1.0,
    )
    window._copied_zoom_annotation = clip
    window._on_zoom_annotation_paste_placement_requested((80.0, 80.0, 180.0, 140.0))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 1)
    pasted = list_shape_annots(str(pdf_path), 0)[0]

    pr = pasted.rect
    window._on_zoom_annotation_geometry_changed(
        pasted, (pr[0] + 25.0, pr[1] + 15.0, pr[2] + 25.0, pr[3] + 15.0), "move"
    )
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 1)

    window._undo_manager.undo()  # 移動の取り消し
    window._undo_manager.undo()  # 貼り付けの取り消し
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf_path), 0)) == 0)
    assert len(list_shape_annots(str(pdf_path), 0)) == 0
