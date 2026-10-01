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
    iter_restyle_pii_annots,
    list_pii_targets_by_page,
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


def _ap_streams(pdf_path) -> list[tuple[str, bytes, str]]:
    """各注釈の (種別, AP/N のコンテンツストリーム, AP/N オブジェクトの文字列) を返す。"""
    result = []
    with fitz.open(str(pdf_path)) as doc:
        for annot in doc[0].annots() or []:
            _, ref = doc.xref_get_key(annot.xref, "AP/N")
            ap_xref = int(ref.split()[0])
            result.append(
                (
                    annot.type[1],
                    doc.xref_stream(ap_xref),
                    doc.xref_object(ap_xref, compressed=False),
                )
            )
    return result


def _make_styled_pdf(tmp_path, name: str):
    pdf_path = tmp_path / name
    _make_pdf(pdf_path)
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "PERSON"))
    create_shape_annot(str(pdf_path), _shape("PERSON"))
    return pdf_path


def _make_multipage_pdf(path, pages: int) -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=200)
        page.insert_text((40, 100), "SECRET", fontsize=18)
    doc.save(str(path))
    doc.close()


def test_restyle_regenerates_appearance_streams_with_new_color_and_opacity(tmp_path):
    pdf_path = _make_styled_pdf(tmp_path, "style-ap.pdf")

    assert restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, ()) == 2

    highlight, square = _ap_streams(pdf_path)
    # ハイライト: 新色の rg、Multiply と新しい不透明度(CA/ca)が AP に焼かれている。
    assert b"0 .5 1 rg" in highlight[1]
    assert "/BM /Multiply" in highlight[2]
    assert "/CA .75" in highlight[2] and "/ca .75" in highlight[2]
    # 図形: 枠 RG と塗り rg の両方が新色。
    assert b"0 .5 1 RG" in square[1] and b"0 .5 1 rg" in square[1]
    assert "/CA .75" in square[2] and "/ca .75" in square[2]


def test_restyle_falls_back_to_annot_update_without_native_mupdf_functions(
    monkeypatch, tmp_path
):
    """fitz.mupdf のネイティブ再生成関数が無い環境でも、annot.update() で同じ結果になる。"""
    monkeypatch.delattr(fitz.mupdf, "pdf_annot_request_resynthesis")
    pdf_path = _make_styled_pdf(tmp_path, "style-fallback.pdf")

    assert restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, ()) == 2

    states = _pdf_state(pdf_path)
    assert all(s["stroke"] == (0.0, 0.5, 1.0) and s["ca"] == 0.75 for s in states)
    highlight, square = _ap_streams(pdf_path)
    assert b"0 .5 1 rg" in highlight[1] and b"0 .5 1 RG" in square[1]


def test_restyle_saves_incrementally_and_keeps_xrefs(tmp_path):
    pdf_path = _make_styled_pdf(tmp_path, "style-incr.pdf")

    def xrefs():
        return [m.xref for m in list_pii_markup_annots(str(pdf_path))] + [
            s.xref for s in list_pii_mask_shapes(str(pdf_path))
        ]

    xrefs_before = xrefs()
    before = pdf_path.read_bytes()

    assert restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, {"PERSON"}) == 2

    after = pdf_path.read_bytes()
    # 増分保存: 既存のバイト列は変わらず、末尾に追記されるだけ。
    assert after.startswith(before) and len(after) > len(before)
    assert xrefs() == xrefs_before
    # 同じスタイルでの2回目はバイト列不変(保存しない)。
    assert restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, {"PERSON"}) == 0
    assert pdf_path.read_bytes() == after


def _add_markup_on_every_page(pdf_path, pages: int) -> None:
    for page_num in range(pages):
        data = _markup(pdf_path, "SECRET", "PERSON")
        data.page_num = page_num
        create_markup_annot(str(pdf_path), data)


def test_iter_restyle_yields_per_page_and_returns_changed_count(tmp_path):
    pdf_path = tmp_path / "style-iter.pdf"
    _make_multipage_pdf(pdf_path, 3)
    _add_markup_on_every_page(pdf_path, 3)

    gen = iter_restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, ())
    yields = 0
    try:
        while True:
            next(gen)
            yields += 1
    except StopIteration as stop:
        changed = stop.value
    assert yields == 3 and changed == 3
    assert all(a.color == (0.0, 0.5, 1.0) for a in list_pii_markup_annots(str(pdf_path)))


def test_iter_restyle_close_midway_does_not_save_and_releases_file(tmp_path):
    import os

    pdf_path = tmp_path / "style-iter-abort.pdf"
    _make_multipage_pdf(pdf_path, 2)
    _add_markup_on_every_page(pdf_path, 2)
    before = pdf_path.read_bytes()

    gen = iter_restyle_pii_annots(str(pdf_path), (0.0, 0.5, 1.0), 0.75, ())
    next(gen)  # 1ページ目を処理した時点で中断
    gen.close()

    assert pdf_path.read_bytes() == before
    # ファイルは開いたままになっておらず、全体保存(置き換え)もできる。
    with fitz.open(str(pdf_path)) as reopened:
        reopened.save(str(pdf_path) + ".tmp")
    os.replace(str(pdf_path) + ".tmp", str(pdf_path))


def test_list_pii_targets_by_page_matches_per_kind_listing_and_opens_once(
    monkeypatch, tmp_path
):
    pdf_path = tmp_path / "style-targets.pdf"
    _make_multipage_pdf(pdf_path, 4)
    for page_num in (0, 2, 3):
        data = _markup(pdf_path, "SECRET", "PERSON")
        data.page_num = page_num
        create_markup_annot(str(pdf_path), data)
        shape = _shape("LOCATION")
        shape.page_num = page_num
        create_shape_annot(str(pdf_path), shape)
    # 通常のマーカーは対象外。
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", ""))

    from src.utils.pdf_utils import annotations as annotations_module

    opens = []
    real_open = fitz.open
    monkeypatch.setattr(
        annotations_module.fitz, "open", lambda *a, **k: opens.append(a) or real_open(*a, **k)
    )
    by_page = list_pii_targets_by_page(str(pdf_path))
    monkeypatch.undo()

    assert len(opens) == 1  # PIIのあるページ数(3)に依存しない
    assert sorted(by_page) == [0, 2, 3]
    expected = [*list_pii_markup_annots(str(pdf_path)), *list_pii_mask_shapes(str(pdf_path))]
    assert sorted(t.xref for _, targets in by_page.values() for t in targets) == sorted(
        t.xref for t in expected
    )
    for pn, (size, targets) in by_page.items():
        assert size == (400.0, 200.0)
        assert [type(t).__name__ for t in targets] == ["TextMarkupAnnotData", "ShapeAnnotData"]
        assert all(t.page_num == pn for t in targets)


def test_get_page_sizes_points_opens_once_and_handles_out_of_range(monkeypatch, tmp_path):
    from src.utils.pdf_utils import get_page_sizes_points, rendering

    pdf_path = tmp_path / "sizes.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=100)
    doc.new_page(width=200, height=400)
    doc.save(str(pdf_path))
    doc.close()
    opens = []
    real_open = fitz.open
    monkeypatch.setattr(rendering.fitz, "open", lambda *a, **k: opens.append(a) or real_open(*a, **k))

    sizes = get_page_sizes_points(str(pdf_path), [0, 1, 9])

    assert len(opens) == 1
    assert sizes == {0: (300.0, 100.0), 1: (200.0, 400.0), 9: (0.0, 0.0)}


# ---------------------------------------------------------------------------
# ウィンドウ統合
# ---------------------------------------------------------------------------


def _open(qtbot, pdf_path):
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    return window


def _wait_restyle(qtbot, window) -> None:
    """予約中・実行中の PII restyle(デバウンス+時間分割ジョブ)が終わるまで待つ。"""

    def idle() -> bool:
        timer = window._pii_restyle_timer
        tick = window._pii_restyle_tick_timer
        return (
            window._pii_restyle_job is None
            and not (timer is not None and timer.isActive())
            and not (tick is not None and tick.isActive())
        )

    qtbot.waitUntil(idle, timeout=10000)


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
    _wait_restyle(qtbot, window)

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
    _wait_restyle(qtbot, window)
    assert all(s["stroke"] == (0.0, 0.0, 1.0) for s in _pdf_state(pdf_path))

    # 透明度: ドラッグ中はPDFを書かず、離したときに反映。
    slider = panel._transparency_slider
    slider.setSliderDown(True)
    slider.setValue(20)
    assert all(s["ca"] == 0.3 for s in _pdf_state(pdf_path))  # まだ既定(透明度70)のまま
    slider.setSliderDown(False)
    _wait_restyle(qtbot, window)
    assert all(s["ca"] == 0.8 for s in _pdf_state(pdf_path))

    # 種別チェックの切り替えで Hidden が付く/外れる。
    panel._entity_checks["PERSON"].setChecked(False)
    _wait_restyle(qtbot, window)
    assert [s["hidden"] for s in _pdf_state(pdf_path)] == [True, False]
    panel._entity_checks["PERSON"].setChecked(True)
    _wait_restyle(qtbot, window)
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
        # 実物と同じくジェネレータ。書き込み不可は next() の中(保存時)で起きる。
        raise PdfWritePermissionError(str(pdf_path))
        yield  # pragma: no cover

    monkeypatch.setattr(page_edit_pii_module, "iter_restyle_pii_annots", deny)

    window._toggle_pii_drawer()  # 例外を投げずにドロワーが開く
    window._pii_panel._entity_checks["PERSON"].setChecked(False)
    _wait_restyle(qtbot, window)

    assert window._pii_restyle_job is None
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


def _make_window_with_pii(qtbot, tmp_path, name: str):
    pdf_path = tmp_path / name
    _make_pdf(pdf_path)
    create_markup_annot(str(pdf_path), _markup(pdf_path, "SECRET", "PERSON"))
    create_markup_annot(str(pdf_path), _markup(pdf_path, "KEEPME", "MANUAL"))
    window = _open(qtbot, pdf_path)
    window._toggle_pii_drawer()
    _wait_restyle(qtbot, window)
    return pdf_path, window


def test_consecutive_slider_commits_collapse_into_one_restyle(qtbot, monkeypatch, tmp_path):
    pdf_path, window = _make_window_with_pii(qtbot, tmp_path, "style-debounce.pdf")
    starts = []
    real_iter = page_edit_pii_module.iter_restyle_pii_annots
    monkeypatch.setattr(
        page_edit_pii_module,
        "iter_restyle_pii_annots",
        lambda *a, **k: starts.append(a) or real_iter(*a, **k),
    )
    slider = window._pii_panel._transparency_slider

    for value in (10, 20, 30, 40, 50):
        slider.setValue(value)
    _wait_restyle(qtbot, window)

    assert len(starts) == 1
    assert starts[0][2] == pytest.approx(0.5)  # 最後の値(透明度50 -> 不透明度0.5)
    assert all(s["ca"] == 0.5 for s in _pdf_state(pdf_path))


def test_write_during_restyle_job_aborts_without_saving_and_reschedules(qtbot, tmp_path):
    pdf_path, window = _make_window_with_pii(qtbot, tmp_path, "style-abort.pdf")
    window._pii_panel._transparency_slider.setValue(20)  # 不透明度0.8へ
    window._start_pii_restyle_job()  # デバウンスを待たず開始
    job = window._pii_restyle_job
    assert job is not None
    before = pdf_path.read_bytes()
    next(job)  # ジョブがPDFを開いたまま途中にいる状態

    pushed = window._push_undoable("テスト", lambda: None, lambda: None)

    assert pushed is True
    assert window._pii_restyle_job is None  # 中断された(PDFは閉じられた)
    assert pdf_path.read_bytes() == before  # 途中結果は保存されていない
    assert window._pii_restyle_timer.isActive()  # 走り直しが予約された
    _wait_restyle(qtbot, window)
    assert all(s["ca"] == 0.8 for s in _pdf_state(pdf_path))


def test_close_flushes_pending_restyle_synchronously(qtbot, tmp_path):
    pdf_path, window = _make_window_with_pii(qtbot, tmp_path, "style-close.pdf")
    window._pii_panel._transparency_slider.setValue(20)
    assert window._pii_restyle_timer.isActive()  # まだ書かれていない
    assert all(s["ca"] == 0.3 for s in _pdf_state(pdf_path))

    from PyQt6.QtCore import Qt

    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)  # 後始末は qtbot に任せる
    window.close()

    assert all(s["ca"] == 0.8 for s in _pdf_state(pdf_path))
    assert window._pii_restyle_job is None
    assert not window._pii_restyle_timer.isActive()


def test_restyle_completion_keeps_targets_cache_and_updates_memory(qtbot, tmp_path):
    from src.utils.pdf_utils import _get_file_cache_token

    pdf_path, window = _make_window_with_pii(qtbot, tmp_path, "style-cache.pdf")
    by_page = window._get_pii_targets_by_page()  # キャッシュを温める
    assert window._pii_targets_by_page_cache[0] == _get_file_cache_token(str(pdf_path))
    window._on_pii_mask_color_changed((0.0, 0.0, 1.0))
    _wait_restyle(qtbot, window)

    # ファイルは書き換わったがキャッシュは再スキャンされず、新しいトークンへ付け替わる。
    cache = window._pii_targets_by_page_cache
    assert cache[1] is by_page
    assert cache[0] == _get_file_cache_token(str(pdf_path))
    assert window._get_pii_targets_by_page() is by_page
    # メモリ上の注釈の色も更新されている。
    assert all(
        a.color == (0.0, 0.0, 1.0)
        for a in window._zoom_annotations
        if getattr(a, "pii_entity", "")
    )
