"""注釈操作の高速化(増分保存・フォーム確定1回・部分再スキャン・Undoでの全体再読込回避)のテスト。"""
from __future__ import annotations

import os

import fitz
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent

from src.models.undo_manager import UndoAction, UndoManager
from src.utils.pdf_utils import (
    MarkupType,
    ShapeAnnotData,
    ShapeType,
    TextMarkupAnnotData,
    create_shape_annot,
    delete_shape_annot,
    edit_markup_annots,
    list_markup_annots,
    list_pii_targets_by_page,
    list_shape_annots,
    load_zoom_page_annotations,
    pages_changed_since,
    replace_shape_annot,
    set_annot_xref_order,
    create_markup_annot,
)
from src.utils.pdf_utils import common as pdf_common
from src.views import page_edit_window as page_edit_window_module
from tests.helpers import create_page_edit_window, make_pdf, open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


def _shape(page_num: int = 0, *, pii: bool = False, rect=(40.0, 50.0, 140.0, 110.0)) -> ShapeAnnotData:
    return ShapeAnnotData(
        page_num=page_num,
        xref=0,
        rect=rect,
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(1.0, 0.0, 0.0),
        fill_color=None,
        stroke_width=2.0,
        opacity=1.0,
        pii_entity="PERSON" if pii else "",
        pii_text="x" if pii else "",
    )


def _text_pdf(path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=320, height=420)
        page.insert_text((40, 60), "Hello markup world", fontsize=18)
    doc.save(str(path))
    doc.close()


# --- A: 増分保存 ------------------------------------------------------------


def test_annotation_crud_is_appended_incrementally_and_roundtrips(tmp_path):
    pdf = tmp_path / "incr.pdf"
    make_pdf(pdf, pages=2)
    original = pdf.read_bytes()

    created = create_shape_annot(str(pdf), _shape())
    after_create = pdf.read_bytes()
    # 増分保存は元のバイト列の末尾に追記するだけ(先頭は書き換わらない)。
    assert after_create.startswith(original)
    assert len(after_create) > len(original)

    replaced = replace_shape_annot(
        str(pdf), 0, created.xref, _shape(rect=(60.0, 70.0, 160.0, 130.0))
    )
    assert pdf.read_bytes().startswith(after_create)
    assert [a.rect for a in list_shape_annots(str(pdf), 0)] == [(60.0, 70.0, 160.0, 130.0)]

    # 削除→再作成(Undo/Redo と同じ往復)を繰り返しても、一覧と xref が整合する。
    for _ in range(3):
        assert delete_shape_annot(str(pdf), 0, replaced.xref) is True
        assert list_shape_annots(str(pdf), 0) == []
        recreated = create_shape_annot(str(pdf), _shape())
        listed = list_shape_annots(str(pdf), 0)
        assert [a.xref for a in listed] == [recreated.xref]
        replaced = recreated
    with fitz.open(str(pdf)) as doc:
        assert doc.page_count == 2
        assert len(list(doc[0].annots())) == 1


def test_set_annot_xref_order_is_incremental(tmp_path):
    pdf = tmp_path / "order.pdf"
    make_pdf(pdf)
    a = create_shape_annot(str(pdf), _shape())
    b = create_shape_annot(str(pdf), _shape(rect=(10.0, 10.0, 60.0, 60.0)))
    before = pdf.read_bytes()
    assert set_annot_xref_order(str(pdf), 0, [b.xref, a.xref]) is True
    assert pdf.read_bytes().startswith(before)


def test_edit_markup_annots_batches_into_one_save(tmp_path, monkeypatch):
    pdf = tmp_path / "batch.pdf"
    _text_pdf(pdf)
    with fitz.open(str(pdf)) as doc:
        rects = [doc[0].search_for(w)[0] for w in ("Hello", "markup", "world")]
    items = [
        create_markup_annot(
            str(pdf),
            TextMarkupAnnotData(
                page_num=0, xref=0, quads=((r.x0, r.y0, r.x1, r.y1),),
                markup_type=MarkupType.HIGHLIGHT, color=(1.0, 1.0, 0.0), opacity=0.4,
            ),
        )
        for r in rects
    ]
    saves: list[bool] = []
    original = pdf_common._save_document_in_place
    from src.utils.pdf_utils import annotations as annotations_module

    def counting(doc, path, **kwargs):
        saves.append(kwargs.get("incremental", False))
        return original(doc, path, **kwargs)

    monkeypatch.setattr(annotations_module, "_save_document_in_place", counting)
    results = edit_markup_annots(
        str(pdf),
        [
            ("delete", 0, items[0].xref),
            ("replace", 0, items[1].xref, TextMarkupAnnotData(
                page_num=0, xref=0, quads=items[1].quads,
                markup_type=MarkupType.UNDERLINE, color=(1.0, 0.0, 0.0), opacity=1.0,
            )),
            ("create", items[0]),
        ],
    )
    assert saves == [True]  # 3件の書き換えで保存は1回(増分)
    assert results[0] is None and results[1] is not None and results[2] is not None
    kinds = sorted(a.markup_type.value for a in list_markup_annots(str(pdf), 0))
    assert kinds == ["highlight", "highlight", "underline"]


def test_load_zoom_page_annotations_opens_once(tmp_path, monkeypatch):
    pdf = tmp_path / "snapshot.pdf"
    make_pdf(pdf, pages=2)
    shape = create_shape_annot(str(pdf), _shape(1))
    opens: list[str] = []
    real_open = fitz.open

    def counting_open(*args, **kwargs):
        opens.append(str(args[0]) if args else "")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(fitz, "open", counting_open)
    data = load_zoom_page_annotations(str(pdf), 1, include_ink=True)
    assert len(opens) == 1
    assert data["page_count"] == 2
    assert [a.xref for a in data["shape"]] == [shape.xref]
    assert data["xref_order"] == [shape.xref]
    assert load_zoom_page_annotations(str(pdf), 5)["shape"] == []


# --- C: 部分再スキャン ------------------------------------------------------


def test_pages_changed_since_tracks_own_writes_only(tmp_path):
    pdf = tmp_path / "journal.pdf"
    make_pdf(pdf, pages=4)
    token = pdf_common._get_file_cache_token(str(pdf))
    assert pages_changed_since(str(pdf), token) == frozenset()

    first = create_shape_annot(str(pdf), _shape(1))
    create_shape_annot(str(pdf), _shape(3))
    assert pages_changed_since(str(pdf), token) == frozenset({1, 3})

    # 記録されない書き込み(外部変更・ページ構成変更)が挟まれば追跡不能(None)。
    mid = pdf_common._get_file_cache_token(str(pdf))
    with fitz.open(str(pdf)) as doc:
        doc.set_metadata({"title": "external"})
        doc.saveIncr()
    assert pages_changed_since(str(pdf), mid) is None
    assert pages_changed_since(str(pdf), token) is None
    assert first.xref  # silence lint


def test_list_pii_targets_by_page_accepts_page_subset(tmp_path):
    pdf = tmp_path / "pii-pages.pdf"
    make_pdf(pdf, pages=3)
    for pn in (0, 2):
        create_shape_annot(str(pdf), _shape(pn, pii=True))
    assert sorted(list_pii_targets_by_page(str(pdf))) == [0, 2]
    assert sorted(list_pii_targets_by_page(str(pdf), [2, 99])) == [2]
    assert list_pii_targets_by_page(str(pdf), [1]) == {}


def test_window_rescans_only_changed_pages_for_pii_targets(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "pii-partial.pdf"
    make_pdf(pdf, pages=3)
    create_shape_annot(str(pdf), _shape(0, pii=True))
    create_shape_annot(str(pdf), _shape(2, pii=True))
    window = create_page_edit_window(qtbot, pdf)

    calls: list = []
    real = page_edit_window_module.list_pii_targets_by_page

    def spy(path, pages=None):
        calls.append(None if pages is None else sorted(pages))
        return real(path, pages)

    monkeypatch.setattr(page_edit_window_module, "list_pii_targets_by_page", spy)
    window._pii_targets_by_page_cache = None
    assert sorted(window._get_pii_targets_by_page()) == [0, 2]
    assert calls == [None]  # 初回は全ページ

    # 自分の書き込み(ページ1に追加)の後は、そのページだけ再スキャン。
    create_shape_annot(str(pdf), _shape(1, pii=True))
    calls.clear()
    assert sorted(window._get_pii_targets_by_page()) == [0, 1, 2]
    assert calls == [[1]]

    # 削除も同様(ページ0だけ)。
    victim = list_shape_annots(str(pdf), 0)[0]
    delete_shape_annot(str(pdf), 0, victim.xref)
    calls.clear()
    assert sorted(window._get_pii_targets_by_page()) == [1, 2]
    assert calls == [[0]]

    # 外部変更が挟まったら全ページ再スキャン。
    with fitz.open(str(pdf)) as doc:
        doc.set_metadata({"title": "external"})
        doc.saveIncr()
    calls.clear()
    window._get_pii_targets_by_page()
    assert calls == [None]


def test_window_pii_result_rows_rebuilt_per_changed_page(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "pii-rows.pdf"
    make_pdf(pdf, pages=3)
    create_shape_annot(str(pdf), _shape(0, pii=True))
    create_shape_annot(str(pdf), _shape(2, pii=True))
    window = create_page_edit_window(qtbot, pdf)
    assert sorted({r.page_num for r in window._build_pii_result_rows()}) == [0, 2]

    seen: list = []
    real = window._scan_pii_rows_by_page

    def spy(pages):
        seen.append(None if pages is None else sorted(pages))
        return real(pages)

    monkeypatch.setattr(window, "_scan_pii_rows_by_page", spy)
    create_shape_annot(str(pdf), _shape(1, pii=True))
    rows = window._build_pii_result_rows()
    assert sorted({r.page_num for r in rows}) == [0, 1, 2]
    assert seen == [[1]]
    seen.clear()
    window._build_pii_result_rows()  # 変更なしなら再走査しない
    assert seen == []


# --- D: Undo/Redo の全体再読込 ----------------------------------------------


def test_undo_manager_peek_exposes_affects_pages():
    manager = UndoManager()
    manager.add_action(UndoAction("page", lambda: None, lambda: None))
    manager.add_action(UndoAction("annot", lambda: None, lambda: None, affects_pages=False))
    assert manager.peek_undo().affects_pages is False
    manager.undo()
    assert manager.peek_redo().description == "annot"
    assert manager.peek_undo().affects_pages is True  # 既定は安全側(全体再読込)


def test_annotation_undo_redo_does_not_reload_pages_but_page_ops_do(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "undo-load.pdf"
    make_pdf(pdf, pages=2)
    window = create_page_edit_window(qtbot, pdf)
    open_zoom(window, qtbot)

    loads: list[int] = []
    real_load = window._load_pages
    monkeypatch.setattr(window, "_load_pages", lambda: (loads.append(1), real_load())[1])

    # 注釈の作成 → Undo/Redo では _load_pages を呼ばない。
    window._on_zoom_shape_create_requested(ShapeType.RECTANGLE, (40.0, 40.0), (140.0, 100.0))
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf), 0)) == 1)
    assert window._undo_manager.peek_undo().affects_pages is False
    loads.clear()
    window._on_undo()
    assert list_shape_annots(str(pdf), 0) == []
    window._on_redo()
    assert len(list_shape_annots(str(pdf), 0)) == 1
    assert loads == []
    # 画面(ズーム表示の注釈)は更新されている。
    assert len(window._zoom_annotations) == 1
    window._on_undo()
    assert window._zoom_annotations == []

    # ページ操作(回転)の Undo/Redo は従来どおり _load_pages を呼ぶ。
    window._rotate_zoom_page()
    assert window._undo_manager.peek_undo().affects_pages is True
    loads.clear()
    window._on_undo()
    assert loads == [1]
    loads.clear()
    window._on_redo()
    assert loads == [1]


# --- B: フォームの確定は1回 -------------------------------------------------


def _open_with_selected_shape(qtbot, tmp_path):
    pdf = tmp_path / "form.pdf"
    make_pdf(pdf)
    created = create_shape_annot(str(pdf), _shape())
    window = create_page_edit_window(qtbot, pdf)
    open_zoom(window, qtbot)
    qtbot.wait(300)  # 起動直後の遅延再描画(_load_pages 経由)が落ち着くのを待つ
    window._set_selected_zoom_annotation(window._find_zoom_annotation(created.xref))
    assert window._selected_zoom_annotation is not None
    return pdf, window


def _count_replace(monkeypatch):
    from src.views import page_edit_annotations as annotations_view

    calls: list = []
    real = annotations_view.replace_shape_annot

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(annotations_view, "replace_shape_annot", counting)
    return calls


def test_slider_drag_saves_once_on_release(qtbot, tmp_path, monkeypatch):
    pdf, window = _open_with_selected_shape(qtbot, tmp_path)
    calls = _count_replace(monkeypatch)
    undo_before = window._undo_manager.undo_count()
    slider = window._zoom_annotation_opacity_slider

    slider.setSliderDown(True)
    for value in (10, 30, 50, 70, 80):  # 透明度%(右ほど透明)
        slider.setValue(value)
    qtbot.wait(window.ZOOM_FORM_COMMIT_DEBOUNCE_MS + 200)
    assert calls == []  # ドラッグ中は(デバウンス時間が過ぎても)保存しない
    # 画面上のプレビューだけは即時に変わる。
    preview = [a for a in window._zoom_label._annotations if a.xref == window._selected_zoom_annotation.xref]
    assert abs(preview[0].opacity - 0.2) < 0.01
    assert abs(list_shape_annots(str(pdf), 0)[0].opacity - 1.0) < 0.01

    slider.setSliderDown(False)  # sliderReleased
    assert len(calls) == 1
    assert abs(list_shape_annots(str(pdf), 0)[0].opacity - 0.2) < 0.02
    assert window._undo_manager.undo_count() == undo_before + 1


def test_non_drag_changes_are_debounced_into_one_save_and_one_undo(qtbot, tmp_path, monkeypatch):
    pdf, window = _open_with_selected_shape(qtbot, tmp_path)
    calls = _count_replace(monkeypatch)
    undo_before = window._undo_manager.undo_count()

    for value in (5, 10, 15, 20, 25):  # キー/ホイール相当の連続変更
        window._zoom_annotation_opacity_slider.setValue(value)
    for value in (3, 4, 5):  # スピン矢印クリック/ホイール連打相当
        window._zoom_annotation_border_width_spin.setValue(value)
    assert calls == []
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)
    qtbot.wait(window.ZOOM_FORM_COMMIT_DEBOUNCE_MS + 200)
    assert len(calls) == 1
    saved = list_shape_annots(str(pdf), 0)[0]
    assert abs(saved.opacity - 0.75) < 0.02
    assert saved.stroke_width == 5.0
    assert window._undo_manager.undo_count() == undo_before + 1

    # Undo 1回で、確定前(変更前)の状態にまとめて戻る。
    window._on_undo()
    reverted = list_shape_annots(str(pdf), 0)[0]
    assert abs(reverted.opacity - 1.0) < 0.01
    assert reverted.stroke_width == 2.0


def test_spin_editing_finished_commits_immediately(qtbot, tmp_path, monkeypatch):
    pdf, window = _open_with_selected_shape(qtbot, tmp_path)
    calls = _count_replace(monkeypatch)
    window._zoom_annotation_width_spin.setValue(200)
    assert calls == []
    window._zoom_annotation_width_spin.editingFinished.emit()
    assert len(calls) == 1
    assert list_shape_annots(str(pdf), 0)[0].rect[2] - list_shape_annots(str(pdf), 0)[0].rect[0] == 200.0


def test_pending_form_edit_is_committed_before_undo_and_close(qtbot, tmp_path, monkeypatch):
    pdf, window = _open_with_selected_shape(qtbot, tmp_path)
    calls = _count_replace(monkeypatch)
    window._zoom_annotation_opacity_slider.setValue(60)  # 透明度60% = 不透明度0.4
    assert calls == []
    window.closeEvent(QCloseEvent())  # 閉じる前に未確定の編集を確定する
    assert len(calls) == 1
    assert abs(list_shape_annots(str(pdf), 0)[0].opacity - 0.4) < 0.02


# --- B: 消しゴムは1回の保存 -------------------------------------------------


def test_eraser_multiple_markups_use_single_batch_and_single_undo(qtbot, tmp_path, monkeypatch):
    from src.views import page_edit_annotations as annotations_view

    pdf = tmp_path / "eraser-batch.pdf"
    _text_pdf(pdf)
    window = create_page_edit_window(qtbot, pdf)
    open_zoom(window, qtbot)
    label = window._zoom_label
    # 2つのマーカー(全文)を作る
    label._selected_char_indices = list(range(len(label._chars)))
    window._create_markup_from_selection(MarkupType.HIGHLIGHT)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf), 0)) == 1)
    label._selected_char_indices = list(range(len(label._chars)))
    window._create_markup_from_selection(MarkupType.UNDERLINE)
    qtbot.waitUntil(lambda: len(list_markup_annots(str(pdf), 0)) == 2)

    calls: list = []
    real = annotations_view.edit_markup_annots

    def counting(path, ops):
        calls.append(len(ops))
        return real(path, ops)

    monkeypatch.setattr(annotations_view, "edit_markup_annots", counting)
    undo_before = window._undo_manager.undo_count()
    label._selected_char_indices = list(range(len(label._chars)))
    window._apply_eraser_to_selection()
    qtbot.waitUntil(lambda: list_markup_annots(str(pdf), 0) == [])
    assert calls == [2]  # 2件の削除が1回の open/保存
    assert window._undo_manager.undo_count() == undo_before + 1

    window._on_undo()
    assert len(list_markup_annots(str(pdf), 0)) == 2
    assert calls == [2, 2]
    window._on_redo()
    assert list_markup_annots(str(pdf), 0) == []


# --- A: 閉じるときの整理 ----------------------------------------------------


def test_close_compacts_only_when_file_bloated(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "compact.pdf"
    make_pdf(pdf)
    window = create_page_edit_window(qtbot, pdf)
    compacted: list[str] = []
    monkeypatch.setattr(page_edit_window_module, "compact_pdf_in_place", compacted.append)
    size = os.path.getsize(pdf)

    # 閾値未満(+5%): 何もしない
    window._initial_file_size = int(size / 1.05)
    assert window._pdf_bloated_since_open() is False
    window._compact_pdf_if_bloated()
    assert compacted == []

    # 比率超過(+10%超)
    window._initial_file_size = int(size / 1.2)
    assert window._pdf_bloated_since_open() is True
    # バイト数超過(+20MB超): 比率が小さくても整理する
    monkeypatch.setattr(page_edit_window_module, "INCREMENTAL_SAVE_COMPACT_RATIO", 1000.0)
    monkeypatch.setattr(page_edit_window_module, "INCREMENTAL_SAVE_COMPACT_BYTES", 100)
    window._initial_file_size = size - 101
    assert window._pdf_bloated_since_open() is True
    window._initial_file_size = size - 99
    assert window._pdf_bloated_since_open() is False
    monkeypatch.undo()
    monkeypatch.setattr(page_edit_window_module, "compact_pdf_in_place", compacted.append)

    window._initial_file_size = int(size / 1.2)
    window.closeEvent(QCloseEvent())
    assert compacted == [str(pdf)]


def test_compact_pdf_in_place_shrinks_incremental_bloat(tmp_path):
    pdf = tmp_path / "bloat.pdf"
    make_pdf(pdf)
    created = create_shape_annot(str(pdf), _shape())
    xref = created.xref
    for i in range(30):
        moved = replace_shape_annot(
            str(pdf), 0, xref, _shape(rect=(40.0 + i, 50.0, 140.0 + i, 110.0))
        )
        xref = moved.xref
    bloated = os.path.getsize(pdf)
    pdf_common.compact_pdf_in_place(str(pdf))
    assert os.path.getsize(pdf) < bloated
    assert [a.rect[0] for a in list_shape_annots(str(pdf), 0)] == [69.0]
