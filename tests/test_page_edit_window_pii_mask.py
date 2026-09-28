"""塗りつぶし候補(手動テキスト選択)/塗りつぶし用図形(四角・丸)のUI統合テスト。

架空のダミーテキスト(SECRET/KEEPME/山田太郎)を使う。
"""
from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import Qt

from src.pii.settings import PiiSettings
from src.utils.pdf_utils import (
    MarkupType,
    ShapeType,
    TextMarkupAnnotData,
    create_markup_annot,
    list_markup_annots,
    list_pii_markup_annots,
    list_pii_mask_shapes,
    list_shape_annots,
)
from src.views import page_edit_pii as page_edit_pii_module
from src.views.page_edit_annotations import CreateMode
from tests.helpers import create_page_edit_window, open_zoom, page_click_pos

pytestmark = pytest.mark.usefixtures("qapp")


def _make_text_pdf(path, text: str = "SECRET KEEPME", *, width=400, height=200) -> None:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(fitz.Rect(0, 0, width, height), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((40, 100), text, fontsize=18)
    doc.save(str(path))
    doc.close()


def _select_chars(window, indices: list[int]) -> None:
    window._zoom_label._selected_char_indices = list(indices)


def _char_indices_for_substring(window, substring: str) -> list[int]:
    label = window._zoom_label
    text = "".join(ch["c"] for ch in label._chars)
    start = text.index(substring)
    return list(range(start, start + len(substring)))


def _drag_on_zoom_label(qtbot, window, start, end) -> None:
    qtbot.mousePress(window._zoom_label, Qt.MouseButton.LeftButton, pos=page_click_pos(window, *start))
    qtbot.mouseMove(window._zoom_label, page_click_pos(window, *end))
    qtbot.mouseRelease(window._zoom_label, Qt.MouseButton.LeftButton, pos=page_click_pos(window, *end))


# ---------------------------------------------------------------------------
# 手動: テキスト候補ツール
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_mask_markup_tool_creates_candidate_from_selection_and_undo(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-markup.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    assert window._create_mode is CreateMode.MASK_MARKUP

    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._on_zoom_text_selection_released()

    candidates = list_pii_markup_annots(str(pdf_path), 0)
    assert len(candidates) == 1
    assert candidates[0].pii_entity == "MANUAL"
    assert candidates[0].pii_text == "SECRET"
    # 通常のマーカーとしても一覧に出る(list_markup_annotsのスーパーセット)。
    assert len(list_markup_annots(str(pdf_path), 0)) == 1

    assert window._undo_manager.can_undo() is True
    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path), 0) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 1


@pytest.mark.usefixtures("qtbot")
def test_mask_markup_tool_stays_armed_after_creating_one_candidate(qtbot, tmp_path):
    """連続モード回帰テスト: 1件作成しても「テキスト候補」ツールが解除されないこと。

    塗りつぶし候補の作成は付箋ドロワーの排他制御(個人情報ドロワーと同時に
    開けない)を経由するため、素朴な実装だと作成直後にドロワーが競合して
    ツールが自動解除されてしまう(_run_zoom_create の open_drawer=True 経由)。
    """
    pdf_path = tmp_path / "mask-markup-sticky.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._on_zoom_text_selection_released()

    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 1
    assert window._create_mode is CreateMode.MASK_MARKUP
    assert window._pii_panel._mask_markup_btn.isChecked() is True
    assert window._pii_panel.is_open is True

    # ツールが解除されていないので、もう一度選択→確定で2件目も作れる。
    _select_chars(window, _char_indices_for_substring(window, "KEEPME"))
    window._on_zoom_text_selection_released()
    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 2


@pytest.mark.usefixtures("qtbot")
def test_mask_markup_tool_without_selection_shows_hint_and_creates_nothing(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-markup-empty.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    window._zoom_label._selected_char_indices = []
    window._on_zoom_text_selection_released()

    assert list_pii_markup_annots(str(pdf_path), 0) == []


# ---------------------------------------------------------------------------
# 手動: 塗り四角/塗り丸ツール
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_mask_rect_tool_creates_shape_and_undo(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-rect.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
    assert window._create_mode is CreateMode.MASK_SHAPE

    _drag_on_zoom_label(qtbot, window, (30, 80), (120, 115))

    shapes = list_pii_mask_shapes(str(pdf_path), 0)
    assert len(shapes) == 1
    assert shapes[0].shape_type == ShapeType.RECTANGLE
    assert shapes[0].pii_entity == "MANUAL"
    # 矩形の下にある文字("SECRET")が抽出されてキャッシュされていること。
    assert "SECRET" in shapes[0].pii_text
    # 通常の図形一覧のスーパーセットとしても見える。
    assert len(list_shape_annots(str(pdf_path), 0)) == 1

    assert window._undo_manager.can_undo() is True
    window._undo_manager.undo()
    assert list_pii_mask_shapes(str(pdf_path), 0) == []
    window._undo_manager.redo()
    assert len(list_pii_mask_shapes(str(pdf_path), 0)) == 1


@pytest.mark.usefixtures("qtbot")
def test_mask_rect_tool_stays_armed_after_creating_one_shape(qtbot, tmp_path):
    """連続モード回帰テスト: 1件作成しても「塗り四角」ツールが解除されないこと。"""
    pdf_path = tmp_path / "mask-rect-sticky.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
    _drag_on_zoom_label(qtbot, window, (30, 80), (80, 115))

    assert len(list_pii_mask_shapes(str(pdf_path), 0)) == 1
    assert window._create_mode is CreateMode.MASK_SHAPE
    assert window._pii_panel._mask_rect_btn.isChecked() is True
    assert window._pii_panel.is_open is True

    _drag_on_zoom_label(qtbot, window, (200, 80), (260, 115))
    assert len(list_pii_mask_shapes(str(pdf_path), 0)) == 2


@pytest.mark.usefixtures("qtbot")
def test_mask_ellipse_tool_creates_shape(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-ellipse.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.ELLIPSE)
    _drag_on_zoom_label(qtbot, window, (30, 80), (120, 115))

    shapes = list_pii_mask_shapes(str(pdf_path), 0)
    assert len(shapes) == 1
    assert shapes[0].shape_type == ShapeType.ELLIPSE
    assert shapes[0].pii_entity == "MANUAL"


@pytest.mark.usefixtures("qtbot")
def test_mask_shape_result_row_recomputes_text_after_move(qtbot, tmp_path):
    """回帰テスト: 図形を空白領域へ移動したら、結果一覧の語句も追従して
    空になること(作成時のpii_textキャッシュを一覧側が信用しない)。
    """
    pdf_path = tmp_path / "mask-rect-move.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
    _drag_on_zoom_label(qtbot, window, (30, 80), (120, 115))

    shapes = list_pii_mask_shapes(str(pdf_path), 0)
    assert len(shapes) == 1
    assert "SECRET" in shapes[0].pii_text

    rows = window._build_pii_result_rows()
    assert rows[0].text and "SECRET" in rows[0].text

    from dataclasses import replace as dataclass_replace

    from src.utils.pdf_utils import replace_shape_annot

    moved = dataclass_replace(shapes[0], rect=(300.0, 150.0, 390.0, 195.0))
    replace_shape_annot(str(pdf_path), shapes[0].page_num, shapes[0].xref, moved)

    rows_after_move = window._build_pii_result_rows()
    assert len(rows_after_move) == 1
    assert rows_after_move[0].text == ""
    assert rows_after_move[0].display_text == "[図形]"


@pytest.mark.usefixtures("qtbot")
def test_mask_shape_tool_does_not_create_normal_shape(qtbot, tmp_path):
    """塗り四角ツールで作った図形は list_shape_annots には出るが、通常図形としては
    区別され list_pii_mask_shapes 経由でのみ塗りつぶし対象扱いされること。"""
    pdf_path = tmp_path / "mask-vs-normal.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
    _drag_on_zoom_label(qtbot, window, (30, 80), (120, 115))

    all_shapes = list_shape_annots(str(pdf_path), 0)
    assert len(all_shapes) == 1
    assert all_shapes[0].pii_entity == "MANUAL"


# ---------------------------------------------------------------------------
# 結果一覧の一括操作
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_delete_same_text_removes_all_matching_candidates(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-bulk.pdf"
    _make_text_pdf(pdf_path, "SECRET SECRET")  # 同じ語句が2箇所

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    label = window._zoom_label
    text = "".join(ch["c"] for ch in label._chars)
    first = text.index("SECRET")
    second = text.index("SECRET", first + 1)

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    _select_chars(window, list(range(first, first + len("SECRET"))))
    window._on_zoom_text_selection_released()
    window._activate_create_mode(CreateMode.MASK_MARKUP)
    _select_chars(window, list(range(second, second + len("SECRET"))))
    window._on_zoom_text_selection_released()

    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 2

    window._on_pii_delete_same_text("SECRET")
    assert list_pii_markup_annots(str(pdf_path), 0) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 2


@pytest.mark.usefixtures("qtbot")
def test_add_exclusion_registers_text_in_pii_settings(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "mask-exclusion.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    window._on_pii_add_exclusion("PERSON", "架空太郎")

    reloaded = PiiSettings.load()
    assert "架空太郎" in reloaded.entity_exclusions.get("PERSON", [])
    assert "架空太郎" in window._pii_settings().entity_exclusions.get("PERSON", [])


# ---------------------------------------------------------------------------
# エクスポート(黒塗り画像 / 文字削除テキストPDF)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_export_rasterize_hides_candidates_and_fills_mask_shapes(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "mask-export-raster.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._on_zoom_text_selection_released()

    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
    _drag_on_zoom_label(qtbot, window, (250, 80), (330, 115))

    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 1
    assert len(list_pii_mask_shapes(str(pdf_path), 0)) == 1

    out_path = tmp_path / "out_raster.pdf"
    monkeypatch.setattr(
        page_edit_pii_module.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "")),
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
    )

    window._on_pii_export_rasterize_requested()

    assert out_path.exists()
    with fitz.open(str(out_path)) as out_doc:
        page = out_doc[0]
        assert page.get_text().strip() == ""  # ラスタライズ済み
        assert (page.annots() or []) == [] or all(
            a.info.get("subject", "") == "" for a in (page.annots() or [])
        )


@pytest.mark.usefixtures("qtbot")
def test_export_redact_removes_masked_text_keeps_other_text_and_normal_annots(
    qtbot, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "mask-export-redact.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    # 通常のマーカー(塗りつぶし対象ではない)を1件作っておく。
    normal_annot = TextMarkupAnnotData(
        page_num=0,
        xref=0,
        quads=((250.0, 80.0, 330.0, 115.0),),
        markup_type=MarkupType.HIGHLIGHT,
        color=(1.0, 1.0, 0.0),
        opacity=0.4,
    )
    create_markup_annot(str(pdf_path), normal_annot)

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._on_zoom_text_selection_released()

    out_path = tmp_path / "out_redact.pdf"
    monkeypatch.setattr(
        page_edit_pii_module.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "")),
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
    )

    window._on_pii_export_redact_requested()

    assert out_path.exists()
    with fitz.open(str(out_path)) as out_doc:
        remaining_text = out_doc[0].get_text()
        assert "SECRET" not in remaining_text
        assert "KEEPME" in remaining_text
        remaining_annots = list(out_doc[0].annots() or [])
        # 塗りつぶし候補(SECRETのハイライト)は消え、通常マーカーは残る。
        assert len(remaining_annots) == 1

    # 元ファイルは変更されていない。
    with fitz.open(str(pdf_path)) as src_doc:
        assert "SECRET" in src_doc[0].get_text()
