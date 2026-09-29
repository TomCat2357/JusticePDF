"""塗りつぶし候補(手動テキスト選択)/塗りつぶし用図形(四角・丸)のUI統合テスト。

架空のダミーテキスト(SECRET/KEEPME/山田太郎)を使う。
"""
from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import Qt

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


# ---------------------------------------------------------------------------
# 「テキスト候補」ボタン: 先に選択済みならその場で追加 / 未選択なら連続モード
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("qtbot")
def test_mask_markup_button_with_preselected_text_creates_candidate_without_arming(
    qtbot, tmp_path
):
    pdf_path = tmp_path / "mask-markup-preselected.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    btn = window._pii_panel._mask_markup_btn

    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    btn.click()

    candidates = list_pii_markup_annots(str(pdf_path), 0)
    assert len(candidates) == 1
    assert candidates[0].pii_entity == "MANUAL"
    assert candidates[0].pii_text == "SECRET"
    # 連続モードには入らず、ボタンは押下前(未チェック)へ戻り、選択も解除される。
    assert window._create_mode is CreateMode.NONE
    assert btn.isChecked() is False
    assert not window._zoom_label._selected_char_indices

    # 1回分のUndoで元に戻る。
    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path), 0) == []
    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 1


@pytest.mark.usefixtures("qtbot")
def test_mask_markup_button_survives_real_drag_selection_then_click(qtbot, tmp_path):
    """実際にページ上でドラッグ選択してからボタンをクリックしても、選択が残っていて追加される
    (ボタンのクリックで選択が消える/フォーカスを奪われることが無い)。"""
    pdf_path = tmp_path / "mask-markup-real-drag.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    _drag_on_zoom_label(qtbot, window, (30, 95), (200, 95))
    assert window._zoom_label._selected_char_indices

    qtbot.mouseClick(window._pii_panel._mask_markup_btn, Qt.MouseButton.LeftButton)

    candidates = list_pii_markup_annots(str(pdf_path), 0)
    assert len(candidates) == 1
    assert candidates[0].pii_text.strip()
    assert window._create_mode is CreateMode.NONE
    assert window._pii_panel._mask_markup_btn.isChecked() is False


@pytest.mark.usefixtures("qtbot")
def test_mask_markup_button_without_selection_arms_continuous_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-markup-arm.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    btn = window._pii_panel._mask_markup_btn

    btn.click()
    assert window._create_mode is CreateMode.MASK_MARKUP
    assert btn.isChecked() is True
    assert list_pii_markup_annots(str(pdf_path), 0) == []

    # 連続モード中は選択するたびに追加される。
    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._on_zoom_text_selection_released()
    assert len(list_pii_markup_annots(str(pdf_path), 0)) == 1
    assert window._create_mode is CreateMode.MASK_MARKUP

    # 再クリックで解除。
    btn.click()
    assert window._create_mode is CreateMode.NONE
    assert btn.isChecked() is False


@pytest.mark.usefixtures("qtbot")
def test_mask_shape_buttons_always_arm_even_with_text_selected(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-shape-arm.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._pii_panel._mask_rect_btn.click()
    assert window._create_mode is CreateMode.MASK_SHAPE
    assert window._pii_panel._mask_rect_btn.isChecked() is True
    assert list_pii_markup_annots(str(pdf_path), 0) == []

    window._pii_panel._mask_ellipse_btn.click()
    assert window._mask_shape_pending_type == ShapeType.ELLIPSE
    assert window._pii_panel._mask_rect_btn.isChecked() is False


@pytest.mark.usefixtures("qtbot")
def test_manual_candidates_use_global_mask_color_and_manual_entity(qtbot, tmp_path):
    pdf_path = tmp_path / "mask-color.pdf"
    _make_text_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    window._on_pii_mask_color_changed((0.2, 0.4, 0.6))

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    _select_chars(window, _char_indices_for_substring(window, "SECRET"))
    window._on_zoom_text_selection_released()
    window._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
    _drag_on_zoom_label(qtbot, window, (250, 80), (330, 115))

    (markup,) = list_pii_markup_annots(str(pdf_path), 0)
    (shape,) = list_pii_mask_shapes(str(pdf_path), 0)
    assert markup.pii_entity == "MANUAL" and shape.pii_entity == "MANUAL"
    assert markup.color == pytest.approx((0.2, 0.4, 0.6), abs=0.01)
    assert shape.stroke_color == pytest.approx((0.2, 0.4, 0.6), abs=0.01)


# ---------------------------------------------------------------------------
# エクスポート(黒塗り+文字削除 → 形式・解像度・圧縮を選んで書き出し)
# ---------------------------------------------------------------------------

_DEFAULT_EXPORT_OPTIONS = {
    "format": "pdf",
    "dpi": 150,
    "jpeg_quality": 85,
    "pdf_optimize_level": 0,
    "pdf_image_dpi": 150,
    "pdf_image_quality": 75,
    "rasterize": False,
    "rasterize_format": "png",
}


def _word_center(pdf_path, word: str) -> tuple[float, float]:
    with fitz.open(str(pdf_path)) as doc:
        rect = doc[0].search_for(word)[0]
    return ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def _add_pii_markup(pdf_path, word: str, entity: str) -> None:
    with fitz.open(str(pdf_path)) as doc:
        rect = doc[0].search_for(word)[0]
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((rect.x0, rect.y0, rect.x1, rect.y1),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(0.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity=entity,
            pii_text=word,
        ),
    )


def _patch_export(window, monkeypatch, *, out_path=None, out_dir=None, **option_overrides):
    """エクスポートのダイアログ類を差し替える。戻り値は (完了メッセージ一覧, 呼び出し記録)。"""
    options = {**_DEFAULT_EXPORT_OPTIONS, **option_overrides}
    calls = {"options": 0, "save": 0, "dir": 0}
    shown: list[str] = []

    def ask_options():
        calls["options"] += 1
        return options

    def get_save(*a, **k):
        calls["save"] += 1
        return (str(out_path), "")

    def get_dir(*a, **k):
        calls["dir"] += 1
        return str(out_dir)

    window._ask_pii_export_options = ask_options
    monkeypatch.setattr(
        page_edit_pii_module.QFileDialog, "getSaveFileName", staticmethod(get_save)
    )
    monkeypatch.setattr(
        page_edit_pii_module.QFileDialog, "getExistingDirectory", staticmethod(get_dir)
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox,
        "information",
        staticmethod(lambda *a, **k: shown.append(str(a[2]))),
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append("WARN:" + str(a[2])))
    )
    return shown, calls


def _open_export_window(qtbot, pdf_path):
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    return window


def _all_object_text(pdf_path) -> str:
    """出力PDFの全オブジェクトを文字列化する(注釈のSubject等の取り残しを探すため)。"""
    parts = []
    with fitz.open(str(pdf_path)) as doc:
        for xref in range(1, doc.xref_length()):
            try:
                parts.append(doc.xref_object(xref, compressed=False))
            except Exception:  # noqa: BLE001
                continue
    return "\n".join(parts)


@pytest.mark.usefixtures("qtbot")
def test_export_redact_keeps_text_layer_deletes_target_and_leaves_no_pii_annots(
    qtbot, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "export-redact.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")
    # 通常のマーカー(塗りつぶし対象ではない)は残る。
    with fitz.open(str(pdf_path)) as doc:
        keep_rect = doc[0].search_for("KEEPME")[0]
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((keep_rect.x0, keep_rect.y0, keep_rect.x1, keep_rect.y1),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0),
            opacity=0.4,
        ),
    )
    secret_center = _word_center(pdf_path, "SECRET")
    original_bytes = pdf_path.read_bytes()

    window = _open_export_window(qtbot, pdf_path)
    out_path = tmp_path / "out_redact.pdf"
    shown, _calls = _patch_export(window, monkeypatch, out_path=out_path)

    window._on_pii_export_requested()

    assert out_path.exists()
    with fitz.open(str(out_path)) as out_doc:
        text = out_doc[0].get_text()
        assert "SECRET" not in text
        assert "KEEPME" in text
        # PII注釈はすべて取り除かれ、通常のマーカー1件だけが残る。
        annots = list(out_doc[0].annots() or [])
        assert len(annots) == 1
        assert "pii_" not in annots[0].info.get("subject", "")
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(int(secret_center[0] * 2), int(secret_center[1] * 2))[:3] == (0, 0, 0)
    everything = _all_object_text(out_path)
    assert "pii_entity" not in everything and "pii_text" not in everything
    assert any("黒塗り済みのPDF" in message for message in shown)
    # 元ファイルは変更されない。
    assert pdf_path.read_bytes() == original_bytes


@pytest.mark.usefixtures("qtbot")
def test_export_is_always_black_regardless_of_mask_color_and_transparency(
    qtbot, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "export-black.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")
    secret_center = _word_center(pdf_path, "SECRET")

    window = _open_export_window(qtbot, pdf_path)
    window._on_pii_mask_color_changed((1.0, 0.0, 0.0))
    window._pii_panel._transparency_slider.setValue(100)  # 完全に透明
    out_path = tmp_path / "out_black.pdf"
    _patch_export(window, monkeypatch, out_path=out_path)

    window._on_pii_export_requested()

    with fitz.open(str(out_path)) as out_doc:
        assert "SECRET" not in out_doc[0].get_text()
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(int(secret_center[0] * 2), int(secret_center[1] * 2))[:3] == (0, 0, 0)


@pytest.mark.usefixtures("qtbot")
def test_export_rasterize_option_gives_image_only_pdf(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "export-raster.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")
    secret_center = _word_center(pdf_path, "SECRET")

    window = _open_export_window(qtbot, pdf_path)
    out_path = tmp_path / "out_raster.pdf"
    _patch_export(
        window,
        monkeypatch,
        out_path=out_path,
        rasterize=True,
        rasterize_format="png",
        pdf_image_dpi=100,
    )

    window._on_pii_export_requested()

    with fitz.open(str(out_path)) as out_doc:
        assert out_doc[0].get_text().strip() == ""  # 画像のみ
        assert list(out_doc[0].annots() or []) == []
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(int(secret_center[0] * 2), int(secret_center[1] * 2))[:3] == (0, 0, 0)


@pytest.mark.usefixtures("qtbot")
def test_export_compress_option_keeps_text_and_black_fill(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "export-compress.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")
    secret_center = _word_center(pdf_path, "SECRET")

    window = _open_export_window(qtbot, pdf_path)
    out_path = tmp_path / "out_compress.pdf"
    _patch_export(window, monkeypatch, out_path=out_path, pdf_optimize_level=1)

    window._on_pii_export_requested()

    with fitz.open(str(out_path)) as out_doc:
        text = out_doc[0].get_text()
        assert "SECRET" not in text and "KEEPME" in text
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(int(secret_center[0] * 2), int(secret_center[1] * 2))[:3] == (0, 0, 0)
    assert "pii_entity" not in _all_object_text(out_path)


@pytest.mark.usefixtures("qtbot")
def test_export_image_format_writes_named_black_images(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtGui import QImage

    pdf_path = tmp_path / "export-images.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")
    secret_center = _word_center(pdf_path, "SECRET")

    window = _open_export_window(qtbot, pdf_path)
    out_dir = tmp_path / "images"
    out_dir.mkdir()
    shown, calls = _patch_export(
        window, monkeypatch, out_dir=out_dir, format="png", dpi=100
    )

    window._on_pii_export_requested()

    assert calls["dir"] == 1 and calls["save"] == 0
    image_path = out_dir / "export-images_黒塗り_p1.png"
    assert image_path.exists()
    image = QImage(str(image_path))
    scale = 100 / 72
    pixel = image.pixelColor(int(secret_center[0] * scale), int(secret_center[1] * scale))
    assert (pixel.red(), pixel.green(), pixel.blue()) == (0, 0, 0)
    assert any("1 ページ" in message for message in shown)


@pytest.mark.usefixtures("qtbot")
def test_export_skips_unchecked_entities_removes_their_annots_and_reports_count(
    qtbot, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "export-skip.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")
    _add_pii_markup(pdf_path, "KEEPME", "PERSON")
    keep_center = _word_center(pdf_path, "KEEPME")

    window = _open_export_window(qtbot, pdf_path)
    window._pii_panel._entity_checks["PERSON"].setChecked(False)
    out_path = tmp_path / "out_skip.pdf"
    shown, _calls = _patch_export(window, monkeypatch, out_path=out_path)

    window._on_pii_export_requested()

    with fitz.open(str(out_path)) as out_doc:
        text = out_doc[0].get_text()
        assert "SECRET" not in text
        assert "KEEPME" in text  # チェックが外れた種別は黒塗り・文字削除しない
        assert list(out_doc[0].annots() or []) == []  # 注釈は出力から取り除く
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(int(keep_center[0] * 2), int(keep_center[1] * 2))[:3] != (0, 0, 0)
        # 対象外の語句の上端の余白に、注釈の塗りが残っていない。
        with fitz.open(str(pdf_path)) as src_doc:
            keep_rect = src_doc[0].search_for("KEEPME")[0]
        strip = fitz.Rect(keep_rect.x0 + 1, keep_rect.y0 + 0.5, keep_rect.x1, keep_rect.y0 + 2.5)
        assert min(out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), clip=strip).samples) >= 250
    everything = _all_object_text(out_path)
    assert "KEEPME" not in "".join(
        line for line in everything.splitlines() if "Subj" in line
    )
    assert "pii_entity" not in everything
    assert any("非表示の種別 1 件は対象外" in message for message in shown)


@pytest.mark.usefixtures("qtbot")
def test_export_with_no_targets_shows_message_and_asks_nothing(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "export-none.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "KEEPME", "PERSON")

    window = _open_export_window(qtbot, pdf_path)
    window._pii_panel._entity_checks["PERSON"].setChecked(False)
    out_path = tmp_path / "out_none.pdf"
    shown, calls = _patch_export(window, monkeypatch, out_path=out_path)

    window._on_pii_export_requested()

    assert not out_path.exists()
    assert calls == {"options": 0, "save": 0, "dir": 0}
    assert any("黒塗りする塗りつぶし対象がありません" in message for message in shown)
    assert any("非表示の種別 1 件は対象外" in message for message in shown)


@pytest.mark.usefixtures("qtbot")
def test_export_cancelled_options_writes_nothing(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "export-cancel.pdf"
    _make_text_pdf(pdf_path, "SECRET KEEPME")
    _add_pii_markup(pdf_path, "SECRET", "MANUAL")

    window = _open_export_window(qtbot, pdf_path)
    out_path = tmp_path / "out_cancel.pdf"
    _shown, calls = _patch_export(window, monkeypatch, out_path=out_path)
    window._ask_pii_export_options = lambda: None  # ダイアログをキャンセル

    window._on_pii_export_requested()

    assert calls["save"] == 0 and not out_path.exists()


def test_export_options_dialog_shows_note_and_panel_has_single_export_button(qtbot):
    from src.views.export_dialog import ExportOptionsDialog
    from src.views.pii_panel import PiiPanel

    dialog = ExportOptionsDialog(note="チェック中の種別は黒塗りし、下の文字を削除します。")
    qtbot.addWidget(dialog)
    assert dialog._note_label is not None
    assert "黒塗り" in dialog._note_label.text()
    assert ExportOptionsDialog()._note_label is None

    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert panel._export_btn.text() == "エクスポート..."
    assert panel._export_btn.menu() is None
    assert not hasattr(panel, "export_rasterize_requested")
    assert not hasattr(panel, "export_redact_requested")
    fired = []
    panel.export_requested.connect(lambda: fired.append(True))
    panel.set_results([])
    panel._export_btn.setEnabled(True)
    panel._export_btn.click()
    assert fired == [True]
