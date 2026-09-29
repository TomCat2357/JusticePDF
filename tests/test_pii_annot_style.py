"""PII注釈(塗りつぶし候補/図形)の、他のPDFソフト向けの見た目(色・不透明度・Hidden)のテスト。

架空のダミーテキスト(SECRET/KEEPME)のみを使う。
"""
from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    MarkupType,
    PdfWritePermissionError,
    ShapeAnnotData,
    ShapeType,
    TextMarkupAnnotData,
    create_markup_annot,
    create_markup_annots,
    create_shape_annot,
    delete_markup_annots,
    get_pii_annot_style,
    list_pii_markup_annots,
    list_pii_mask_shapes,
    replace_markup_annot,
    reset_pii_annot_style,
    restyle_pii_annots,
    set_pii_annot_style,
)
from src.views import page_edit_pii as page_edit_pii_module
from tests.helpers import create_page_edit_window, open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


def _make_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.save(str(path))
    doc.close()


def _markup(pdf_path, word: str, entity: str, *, color=(0.9, 0.2, 0.2), opacity=0.35):
    with fitz.open(str(pdf_path)) as doc:
        rect = doc[0].search_for(word)[0]
    return TextMarkupAnnotData(
        page_num=0,
        xref=0,
        quads=((rect.x0, rect.y0, rect.x1, rect.y1),),
        markup_type=MarkupType.HIGHLIGHT,
        color=color,
        opacity=opacity,
        pii_entity=entity,
        pii_text=word,
    )


def _shape(entity: str) -> ShapeAnnotData:
    return ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(200.0, 20.0, 260.0, 60.0),
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(0.9, 0.2, 0.2),
        fill_color=(0.9, 0.2, 0.2),
        stroke_width=1.2,
        opacity=0.35,
        pii_entity=entity,
        pii_text="",
    )


def _pdf_state(pdf_path) -> list[dict]:
    """PDF上の注釈の (種別, ストロークの色, 塗り, CA, hiddenか) を返す。"""
    states = []
    with fitz.open(str(pdf_path)) as doc:
        for annot in doc[0].annots() or []:
            _, ca = doc.xref_get_key(annot.xref, "CA")
            states.append(
                {
                    "type": annot.type[1],
                    "stroke": tuple(round(c, 3) for c in (annot.colors.get("stroke") or ())),
                    "fill": tuple(round(c, 3) for c in (annot.colors.get("fill") or ())),
                    "ca": None if ca == "null" else round(float(ca), 3),
                    "hidden": bool(annot.flags & fitz.PDF_ANNOT_IS_HIDDEN),
                }
            )
    return states


def test_style_registry_defaults_and_setter():
    assert get_pii_annot_style() == ((0.0, 0.0, 0.0), 0.3, frozenset())
    set_pii_annot_style((1.0, 0.5, 0.0), 0.6, ["PERSON"])
    assert get_pii_annot_style() == ((1.0, 0.5, 0.0), 0.6, frozenset({"PERSON"}))
    reset_pii_annot_style()
    assert get_pii_annot_style()[1] == 0.3


def test_new_pii_annots_are_written_with_style_color_opacity_and_hidden_flag(tmp_path):
    pdf_path = tmp_path / "style-new.pdf"
    _make_pdf(pdf_path)
    set_pii_annot_style((1.0, 0.0, 0.0), 0.4, {"PERSON"})

    visible = create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "MANUAL"))
    hidden = create_markup_annot(str(pdf_path), _markup(pdf_path, "KEEPME", "PERSON"))
    shape = create_shape_annot(str(pdf_path), _shape("LOCATION"))
    hidden_shape = create_shape_annot(str(pdf_path), _shape("PERSON"))

    states = {s["type"] + str(i): s for i, s in enumerate(_pdf_state(pdf_path))}
    highlight_visible, highlight_hidden, square_visible, square_hidden = states.values()
    # 暗くしない(2種目以降のタプルは全て指定色そのもの)・不透明度は 1-透明度。
    for state in (highlight_visible, highlight_hidden):
        assert state["stroke"] == (1.0, 0.0, 0.0)
        assert state["ca"] == 0.4
    for state in (square_visible, square_hidden):
        assert state["stroke"] == (1.0, 0.0, 0.0)
        assert state["fill"] == (1.0, 0.0, 0.0)
        assert state["ca"] == 0.4
    assert (highlight_visible["hidden"], highlight_hidden["hidden"]) == (False, True)
    assert (square_visible["hidden"], square_hidden["hidden"]) == (False, True)

    # アプリ側が読み戻すデータ(Subjectメタデータ+CA)も新スタイルで一貫している。
    assert visible.color == (1.0, 0.0, 0.0) and visible.opacity == pytest.approx(0.4)
    assert hidden.pii_entity == "PERSON"
    assert shape.stroke_color == (1.0, 0.0, 0.0) and shape.fill_color == (1.0, 0.0, 0.0)
    assert hidden_shape.opacity == pytest.approx(0.4)


def test_normal_markups_and_shapes_are_not_touched_by_style(tmp_path):
    pdf_path = tmp_path / "style-normal.pdf"
    _make_pdf(pdf_path)
    set_pii_annot_style((1.0, 0.0, 0.0), 0.4, {"PERSON"})
    normal = _markup(pdf_path, "SECRET", "", color=(0.0, 1.0, 0.0), opacity=0.7)

    create_markup_annot(str(pdf_path), normal)

    (state,) = _pdf_state(pdf_path)
    assert state["stroke"] == (0.0, 1.0, 0.0)
    assert state["ca"] == 0.7
    assert state["hidden"] is False
    # restyle も通常のマーカーには触れない。
    assert restyle_pii_annots(str(pdf_path), (1.0, 0.0, 0.0), 0.4, {"PERSON"}) == 0


def test_recreate_after_delete_uses_current_style_even_with_stale_data(tmp_path):
    """Undo/Redo は古いデータで再作成するが、書き込みは常に現在のスタイルになる。"""
    pdf_path = tmp_path / "style-recreate.pdf"
    _make_pdf(pdf_path)
    (created,) = create_markup_annots(str(pdf_path), [_markup(pdf_path, "SECRET", "PERSON")])
    stale = created  # 作成時点(既定スタイル)のデータ
    delete_markup_annots(str(pdf_path), [(created.page_num, created.xref)])

    set_pii_annot_style((0.0, 0.0, 1.0), 0.8, {"PERSON"})
    create_markup_annots(str(pdf_path), [stale])

    (state,) = _pdf_state(pdf_path)
    assert state["stroke"] == (0.0, 0.0, 1.0) and state["ca"] == 0.8 and state["hidden"] is True
    (read_back,) = list_pii_markup_annots(str(pdf_path))
    assert read_back.color == (0.0, 0.0, 1.0)
    assert read_back.opacity == pytest.approx(0.8)

    # replace(移動・再作成)でも同様。
    replaced = replace_markup_annot(str(pdf_path), 0, read_back.xref, read_back)
    assert replaced.color == (0.0, 0.0, 1.0)


def test_restyle_updates_existing_annots_and_saves_only_when_needed(tmp_path):
    pdf_path = tmp_path / "style-restyle.pdf"
    _make_pdf(pdf_path)
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "PERSON"))
    create_markup_annot(str(pdf_path), _markup(pdf_path, "KEEPME", "MANUAL"))
    create_shape_annot(str(pdf_path), _shape("PERSON"))

    changed = restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, {"PERSON"})

    assert changed == 3
    states = _pdf_state(pdf_path)
    assert [s["hidden"] for s in states] == [True, False, True]
    assert all(s["stroke"] == (0.0, 0.5, 1.0) and s["ca"] == 0.75 for s in states)
    assert states[2]["fill"] == (0.0, 0.5, 1.0)
    # アプリが読み戻す色も更新されている(Undo/Redoで古い値が復活しない)。
    assert all(a.color == (0.0, 0.5, 1.0) for a in list_pii_markup_annots(str(pdf_path)))
    assert list_pii_mask_shapes(str(pdf_path))[0].stroke_color == (0.0, 0.5, 1.0)

    # 同じスタイルでもう一度: 何も変わらない=保存もしない(ファイルは不変)。
    before = pdf_path.read_bytes()
    assert restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, {"PERSON"}) == 0
    assert pdf_path.read_bytes() == before

    # 非表示だった種別を戻すと Hidden が解除される。
    assert restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, ()) == 2
    assert [s["hidden"] for s in _pdf_state(pdf_path)] == [False, False, False]


def test_restyle_migrates_legacy_dimmed_annots(tmp_path):
    """旧バージョンが書いた(色を暗くした)注釈も、開いたときに指定色へ揃う。"""
    pdf_path = tmp_path / "style-legacy.pdf"
    _make_pdf(pdf_path)
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "PERSON"))
    with fitz.open(str(pdf_path)) as doc:
        page = doc[0]
        annot = next(iter(page.annots()))
        annot.set_colors(stroke=[c * 0.35 for c in (0.9, 0.2, 0.2)])
        annot.update()
        doc.saveIncr()

    assert restyle_pii_annots(str(pdf_path), (0.0, 0.0, 0.0), 0.3, ()) == 1
    assert _pdf_state(pdf_path)[0]["stroke"] == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# ウィンドウ統合
# ---------------------------------------------------------------------------


def _open(qtbot, pdf_path):
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    return window


def test_drawer_open_restyles_stale_file_to_current_settings(qtbot, tmp_path):
    pdf_path = tmp_path / "style-open.pdf"
    _make_pdf(pdf_path)
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "PERSON"))  # 既定スタイルで作成
    window = _open(qtbot, pdf_path)
    settings = window._pii_settings().copy()
    settings.mask_color = (1.0, 0.0, 0.0)
    settings.mask_transparency = 50
    settings.enabled_entities["PERSON"] = False
    window._pii_settings_cache = settings

    window._toggle_pii_drawer()

    (state,) = _pdf_state(pdf_path)
    assert state["stroke"] == (1.0, 0.0, 0.0) and state["ca"] == 0.5 and state["hidden"] is True


def test_panel_operations_restyle_annots_in_pdf(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog

    pdf_path = tmp_path / "style-panel.pdf"
    _make_pdf(pdf_path)
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "PERSON"))
    create_markup_annot(str(pdf_path), _markup(pdf_path, "KEEPME", "MANUAL"))
    window = _open(qtbot, pdf_path)
    window._toggle_pii_drawer()
    panel = window._pii_panel

    # 色の確定
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor(0, 0, 255)))
    panel._color_btn.click()
    assert all(s["stroke"] == (0.0, 0.0, 1.0) for s in _pdf_state(pdf_path))

    # 透明度: ドラッグ中はPDFを書かず、離したときに反映。
    slider = panel._transparency_slider
    slider.setSliderDown(True)
    slider.setValue(20)
    assert all(s["ca"] == 0.3 for s in _pdf_state(pdf_path))  # まだ既定(透明度70)のまま
    slider.setSliderDown(False)
    assert all(s["ca"] == 0.8 for s in _pdf_state(pdf_path))

    # 種別チェックの切り替えで Hidden が付く/外れる。
    panel._entity_checks["PERSON"].setChecked(False)
    assert [s["hidden"] for s in _pdf_state(pdf_path)] == [True, False]
    panel._entity_checks["PERSON"].setChecked(True)
    assert [s["hidden"] for s in _pdf_state(pdf_path)] == [False, False]

    # 画面上のデータもファイルに合わせて読み直されている。
    assert all(
        a.color == (0.0, 0.0, 1.0) for a in window._zoom_label._annotations if getattr(a, "pii_entity", "")
    )


def test_undo_redo_of_detection_keeps_current_style_and_hidden_flag(qtbot, tmp_path):
    pdf_path = tmp_path / "style-undo.pdf"
    _make_pdf(pdf_path)
    window = _open(qtbot, pdf_path)
    window._toggle_pii_drawer()
    window._pii_panel._transparency_slider.setValue(40)
    window._pii_panel._entity_checks["MANUAL"].setChecked(False)

    from src.views.page_edit_annotations import CreateMode

    # 手動が非表示だとツールは使えないので、テキスト候補をPERSONとして直接追加する。
    window._append_new_pii_markups(
        [_markup(pdf_path, "SECRET", "PERSON"), _markup(pdf_path, "KEEPME", "MANUAL")], "テスト追加"
    )
    assert [s["hidden"] for s in _pdf_state(pdf_path)] == [False, True]
    assert all(s["ca"] == 0.6 for s in _pdf_state(pdf_path))
    assert window._create_mode is CreateMode.NONE

    window._undo_manager.undo()
    assert _pdf_state(pdf_path) == []
    window._undo_manager.redo()
    states = _pdf_state(pdf_path)
    assert [s["hidden"] for s in states] == [False, True]
    assert all(s["ca"] == 0.6 and s["stroke"] == (0.0, 0.0, 0.0) for s in states)


def test_restyle_permission_error_is_handled_gracefully(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "style-readonly.pdf"
    _make_pdf(pdf_path)
    window = _open(qtbot, pdf_path)

    def deny(*_a, **_k):
        raise PdfWritePermissionError(str(pdf_path))

    monkeypatch.setattr(page_edit_pii_module, "restyle_pii_annots", deny)

    window._toggle_pii_drawer()  # 例外を投げずにドロワーが開く
    window._pii_panel._entity_checks["PERSON"].setChecked(False)

    assert window._pii_panel.is_open is True
    assert window._pii_settings().enabled_entities["PERSON"] is False


def test_app_renderers_still_hide_pii_annots_so_no_double_drawing(qtbot, tmp_path):
    """自前描画(オーバーレイ)する塗りつぶし候補は、ページ画像から隠したまま(二重描画しない)。"""
    from src.utils.pdf_utils import get_page_pixmap

    pdf_path = tmp_path / "style-render.pdf"
    _make_pdf(pdf_path)
    (created,) = create_markup_annots(str(pdf_path), [_markup(pdf_path, "SECRET", "PERSON")])
    set_pii_annot_style((0.0, 0.0, 0.0), 1.0, ())  # 不透明の黒(見えれば一目で分かる)
    restyle_pii_annots(str(pdf_path), (0.0, 0.0, 0.0), 1.0, ())

    with fitz.open(str(pdf_path)) as doc:
        rect = doc[0].search_for("SECRET")[0]
    scale = 2.0
    cx, cy = int((rect.x0 + rect.x1) / 2 * scale), int(rect.y0 * scale) + 2

    baked = get_page_pixmap(str(pdf_path), 0, zoom=scale).toImage().pixelColor(cx, cy)
    hidden = get_page_pixmap(
        str(pdf_path), 0, zoom=scale, hide_xrefs={created.xref}
    ).toImage().pixelColor(cx, cy)
    assert (baked.red(), baked.green(), baked.blue()) == (0, 0, 0)  # 焼き込み経路では見える
    assert (hidden.red(), hidden.green(), hidden.blue()) == (255, 255, 255)  # hide_xrefs で隠せる

    # 種別を非表示にした注釈は、焼き込み経路でも(Hiddenフラグで)描かれない。
    restyle_pii_annots(str(pdf_path), (0.0, 0.0, 0.0), 1.0, {"PERSON"})
    baked_hidden = get_page_pixmap(str(pdf_path), 0, zoom=scale).toImage().pixelColor(cx, cy)
    assert (baked_hidden.red(), baked_hidden.green(), baked_hidden.blue()) == (255, 255, 255)
