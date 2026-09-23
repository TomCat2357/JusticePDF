"""拡大表示ドロワーの色ボタン(色見本バー)のテスト。

09-23: 色ボタンをバー化し、中に色の「意味」(文字色/背景色/線色/装飾色/付箋色)を
表示するようにした。あわせて「透明」の選択はカラーダイアログ内のボタンへ移動し、
別立ての「透明」ボタンは廃止した。ここではそのUI仕様を確認する。
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QDialogButtonBox, QPushButton

from src.utils.pdf_utils import FreeTextAnnotData, create_freetext_annot
from src.views import page_edit_annotations as page_edit_annotations_module
from tests.helpers import create_page_edit_window, make_pdf, open_zoom


@pytest.mark.usefixtures("qtbot")
def test_color_button_text_shows_meaning_not_hex(qtbot, tmp_path):
    pdf_path = tmp_path / "swatch-text.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    assert window._zoom_markup_color_btn.text() == "装飾色"
    assert window._zoom_note_color_btn.text() == "付箋色"
    assert window._zoom_annotation_text_color_btn.text() == "文字色"
    assert window._zoom_annotation_fill_color_btn.text() == "背景色"
    assert window._zoom_annotation_border_color_btn.text() == "線色"


@pytest.mark.usefixtures("qtbot")
def test_color_button_tooltip_contains_hex(qtbot, tmp_path):
    pdf_path = tmp_path / "swatch-tooltip.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_annotation_fill_color = (1.0, 0.6, 0.6)
    window._set_color_button_preview(
        window._zoom_annotation_fill_color_btn,
        window._zoom_annotation_fill_color,
        allow_none=True,
    )
    tooltip = window._zoom_annotation_fill_color_btn.toolTip()
    assert tooltip.startswith("背景色: #")

    assert window._zoom_markup_color_btn.toolTip().startswith(
        "マーカー・下線・取り消し線の色: #"
    )
    assert window._zoom_note_color_btn.toolTip().startswith("付箋色: #")


@pytest.mark.usefixtures("qtbot")
def test_transparent_color_sets_property_and_tooltip(qtbot, tmp_path):
    pdf_path = tmp_path / "swatch-transparent.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._set_color_button_preview(
        window._zoom_annotation_fill_color_btn, (1.0, 1.0, 1.0), allow_none=True
    )
    assert window._zoom_annotation_fill_color_btn.property("transparent") is False

    window._set_color_button_preview(
        window._zoom_annotation_fill_color_btn, None, allow_none=True
    )
    assert window._zoom_annotation_fill_color_btn.property("transparent") is True
    assert window._zoom_annotation_fill_color_btn.text() == "背景色"
    assert window._zoom_annotation_fill_color_btn.toolTip() == "背景色: 透明"


@pytest.mark.usefixtures("qtbot")
def test_no_separate_transparent_buttons_remain(qtbot, tmp_path):
    pdf_path = tmp_path / "swatch-no-clear-btn.pdf"
    make_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    assert not hasattr(window, "_zoom_annotation_fill_color_clear_btn")
    assert not hasattr(window, "_zoom_annotation_border_color_clear_btn")
    drawer = window._zoom_annotation_panel
    assert all(
        btn.text() != "透明" for btn in drawer.findChildren(QPushButton)
    )


@pytest.mark.usefixtures("qtbot")
def test_pick_color_dialog_has_transparent_button_when_allowed(qtbot):
    """allow_none=True のとき、ダイアログのボタン列に「透明」ボタンが追加され、
    押すと (True, None) が返ることを実際にダイアログを構築して確認する
    (exec() は呼ばずボタンの click() だけで完結させる)。
    """
    from PyQt6.QtCore import QTimer

    captured: dict[str, object] = {}

    def _intercept_exec(self) -> int:
        button_box = self.findChild(QDialogButtonBox)
        assert button_box is not None
        none_btns = [b for b in button_box.buttons() if b.text() == "透明"]
        assert len(none_btns) == 1
        none_btns[0].click()
        captured["result"] = self.result()
        return self.result()

    from PyQt6.QtWidgets import QColorDialog

    qtbot_dialog_cls = QColorDialog
    original_exec = qtbot_dialog_cls.exec
    qtbot_dialog_cls.exec = _intercept_exec
    try:
        accepted, color = page_edit_annotations_module._pick_color(
            None, QColor(255, 255, 255), "テスト", allow_none=True
        )
    finally:
        qtbot_dialog_cls.exec = original_exec

    assert accepted is True
    assert color is None


@pytest.mark.usefixtures("qtbot")
def test_pick_color_dialog_without_allow_none_has_no_transparent_button(qtbot, monkeypatch):
    monkeypatch.setattr(
        page_edit_annotations_module.QColorDialog,
        "getColor",
        staticmethod(lambda *a, **k: QColor(10, 20, 30)),
    )
    accepted, color = page_edit_annotations_module._pick_color(
        None, QColor(255, 255, 255), "テスト", allow_none=False
    )
    assert accepted is True
    assert color == QColor(10, 20, 30)


@pytest.mark.usefixtures("qtbot")
def test_pick_transparent_fill_color_via_dialog_helper_is_undoable(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "transparent-undo.pdf"
    make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 170, 120),
            content="hello",
            fontsize=16,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.6),
            border_color=(0.0, 0.0, 0.0),
            border_width=1,
            opacity=1.0,
        ),
    )

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    from src.utils.pdf_utils import list_freetext_annots

    annot = list_freetext_annots(str(pdf_path), 0)[0]
    rect = window._zoom_label._annotation_widget_rect(annot)
    from PyQt6.QtCore import Qt

    qtbot.mouseClick(window._zoom_label, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.waitUntil(
        lambda: window._selected_zoom_annotation is not None
        and window._selected_zoom_annotation.xref == created.xref
    )

    monkeypatch.setattr(page_edit_annotations_module, "_pick_color", lambda *a, **k: (True, None))
    qtbot.mouseClick(window._zoom_annotation_fill_color_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].fill_color is None)

    window._on_undo()
    qtbot.waitUntil(lambda: list_freetext_annots(str(pdf_path), 0)[0].fill_color is not None)
