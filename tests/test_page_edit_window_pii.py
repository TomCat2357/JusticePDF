"""個人情報検出ドロワー(PageEditWindow統合)のテスト。

架空のダミーテキスト(山田太郎、090-1234-5678)を埋め込んだPDFを使う。
"""
from __future__ import annotations

import re

import fitz
import pytest

from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import list_pii_markup_annots
from src.views import page_edit_pii as page_edit_pii_module
from src.views.page_edit_window import PageEditWindow
from tests.helpers import open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


def _make_pii_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text(
        (40, 100), "山田太郎の電話番号は0901234567890", fontname="japan", fontsize=14
    )
    doc.save(str(path))
    doc.close()


def _make_job_title_pdf(path) -> None:
    """職業欄が直後の日付列と区切り文字無しで連結される表組みを模したPDF。

    「公務員」はどの自動検出エンティティにも該当しない(要望B/不具合Cの再現用)。
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "丸尾幸男公務員昭和52年11月23日", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def _create_window(qtbot, pdf_path) -> PageEditWindow:
    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()
    return window


def _make_choice_message_box(choice_label: str):
    """QMessageBox の確認ダイアログ(全ページ/このページだけ/登録のみ)を、

    実際のモーダル表示無しに指定ラベルのボタンが押されたことにして進める
    スタブ。``QMessageBox.information``/``warning`` は他の経路からも
    呼ばれるため、無害な no-op として提供する。
    """
    from PyQt6.QtWidgets import QMessageBox as RealQMessageBox

    class _FakeChoiceBox:
        ButtonRole = RealQMessageBox.ButtonRole

        def __init__(self, parent=None):
            self._buttons: list[tuple[str, object]] = []

        def setWindowTitle(self, *_a):
            pass

        def setText(self, *_a):
            pass

        def addButton(self, label, _role):
            token = object()
            self._buttons.append((label, token))
            return token

        def setDefaultButton(self, *_a):
            pass

        def exec(self):
            return 0

        def clickedButton(self):
            for label, token in self._buttons:
                if label == choice_label:
                    return token
            return None

        @staticmethod
        def information(*_a, **_k):
            return None

        @staticmethod
        def warning(*_a, **_k):
            return None

    return _FakeChoiceBox


def test_pii_drawer_toggle_is_exclusive_with_annotation_drawer(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._toggle_zoom_annotation_drawer()
    assert window._zoom_annotation_open is True

    window._toggle_pii_drawer()
    assert window._pii_panel.is_open is True
    assert window._zoom_annotation_open is False


def test_pii_detection_creates_highlight_and_undo_removes_it(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    assert window._pii_worker is not None

    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)

    annots = list_pii_markup_annots(str(pdf_path))
    assert len(annots) >= 1
    assert {a.pii_entity for a in annots} & {"PERSON", "PHONE_NUMBER"}

    assert window._undo_manager.can_undo() is True
    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == len(annots)


def test_pii_detection_undo_redo_multiple_cycles(qtbot, tmp_path):
    """検出→取消→やり直し→取消 を繰り返しても件数が正しいこと(_AnnotRef回帰確認)。"""
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    count = len(list_pii_markup_annots(str(pdf_path)))
    assert count >= 1

    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count

    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count


def test_pii_remove_selected_undo_redo_multiple_cycles(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    count = len(list_pii_markup_annots(str(pdf_path)))
    assert count >= 1

    window._on_pii_remove_all()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count

    window._undo_manager.redo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count


def test_pii_remove_all_clears_highlights(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    assert list_pii_markup_annots(str(pdf_path))

    window._on_pii_remove_all()
    assert list_pii_markup_annots(str(pdf_path)) == []


# ---------------------------------------------------------------------------
# 要望D: 「既存の結果を残して追加検出」
# ---------------------------------------------------------------------------


def test_keep_existing_checkbox_persists_to_settings(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-keep-persist.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    # set_keep_existing_checked() は「設定値をチェックボックスへ反映する」
    # 一方向の同期用API(シグナルを止めて書き換える)なので、ユーザー操作を
    # 模すにはチェックボックス自体を操作して toggled シグナルを発火させる。
    window._pii_panel._keep_existing_check.setChecked(True)
    assert window._pii_settings().keep_existing_on_detect is True


def test_keep_existing_detect_does_not_duplicate_or_remove_prior_results(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-keep.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    first_count = len(list_pii_markup_annots(str(pdf_path)))
    assert first_count >= 1

    window._pii_panel._keep_existing_check.setChecked(True)
    undo_depth_before = window._undo_manager.undo_count()
    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    # 同じページを検出し直しただけ(検出結果は同一)なので、既存分は消えず、
    # 重複した新規分も追加されない。新規追加が0件なので、Undoスタックにも
    # 積まれない(押しても直前の検出まで戻ってしまわないこと)。
    assert len(list_pii_markup_annots(str(pdf_path))) == first_count
    assert window._undo_manager.undo_count() == undo_depth_before


# ---------------------------------------------------------------------------
# 要望B / 不具合C: 結果一覧「追加パターンに登録」からの部分再検出
# ---------------------------------------------------------------------------


def test_add_pattern_requested_registers_pattern_without_detecting(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-pattern-register-only.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    monkeypatch.setattr(
        page_edit_pii_module, "QMessageBox", _make_choice_message_box("追加検出しない(登録のみ)")
    )

    window._on_pii_add_pattern_requested("PERSON", "公務員")

    settings = window._pii_settings()
    assert ("PERSON", "公務員") in settings.additional_patterns
    assert list_pii_markup_annots(str(pdf_path)) == []


def test_add_pattern_requested_does_not_register_duplicate_pattern(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-pattern-dup.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    monkeypatch.setattr(
        page_edit_pii_module, "QMessageBox", _make_choice_message_box("追加検出しない(登録のみ)")
    )
    window._on_pii_add_pattern_requested("PERSON", "公務員")
    window._on_pii_add_pattern_requested("PERSON", "公務員")

    settings = window._pii_settings()
    assert settings.additional_patterns.count(("PERSON", "公務員")) == 1


def test_add_pattern_and_detect_current_page_adds_new_markup(qtbot, monkeypatch, tmp_path):
    """不具合Cの再現条件(隣接する日付列と区切り文字無しで連結)でも検出できること。"""
    pdf_path = tmp_path / "pii-pattern-detect.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    monkeypatch.setattr(
        page_edit_pii_module, "QMessageBox", _make_choice_message_box("このページだけ追加検出")
    )

    window._on_pii_add_pattern_requested("PERSON", "公務員")

    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_text == "公務員" and a.pii_entity == "PERSON" for a in annots)

    # 同じパターンでもう一度部分再検出しても重複追加されないこと。
    count_after_first = len(annots)
    window._run_pattern_only_detection("PERSON", re.escape("公務員"), [0])
    assert len(list_pii_markup_annots(str(pdf_path))) == count_after_first


def test_add_pattern_and_detect_all_pages(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-pattern-detect-all.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    monkeypatch.setattr(
        page_edit_pii_module, "QMessageBox", _make_choice_message_box("全ページで追加検出")
    )

    window._on_pii_add_pattern_requested("PERSON", "公務員")

    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_text == "公務員" and a.pii_entity == "PERSON" for a in annots)


def test_pii_mask_overlay_paints_markup_and_ellipse_only_inside():
    """ページ一覧サムネイル用の重ね描き: マーカーは矩形、塗り丸は楕円の内側だけを塗る。"""
    from PyQt6.QtGui import QColor, QPixmap

    from src.utils.pdf_utils import MarkupType, ShapeAnnotData, ShapeType, TextMarkupAnnotData
    from src.views.page_edit_widgets import paint_pii_mask_overlay

    base = QPixmap(100, 50)
    base.fill(QColor("white"))
    markup = TextMarkupAnnotData(
        page_num=0,
        xref=1,
        quads=((20.0, 20.0, 60.0, 40.0),),
        markup_type=MarkupType.HIGHLIGHT,
        color=(1.0, 0.0, 0.0),
        opacity=0.35,
        pii_entity="PERSON",
    )
    ellipse = ShapeAnnotData(
        page_num=0,
        xref=2,
        rect=(100.0, 0.0, 200.0, 100.0),
        shape_type=ShapeType.ELLIPSE,
        stroke_color=(0.0, 0.0, 1.0),
        fill_color=(0.0, 0.0, 0.0),
        stroke_width=1.2,
        opacity=0.35,
        pii_entity="LOCATION",
    )
    image = paint_pii_mask_overlay(base, [markup, ellipse], (200.0, 100.0)).toImage()

    marker_px = image.pixelColor(20, 15)  # quad の内側(縮尺0.5)
    assert marker_px.red() > 200 and marker_px.green() < 200
    center_px = image.pixelColor(75, 25)  # 楕円の中心
    assert center_px.blue() > 200 and center_px.red() < 200
    corner_px = image.pixelColor(51, 1)  # 外接矩形の角(楕円の外側)は塗らない
    assert (corner_px.red(), corner_px.green(), corner_px.blue()) == (255, 255, 255)


def test_page_list_thumbnail_shows_pii_mask_targets(qtbot, tmp_path):
    """ページ一覧のサムネイルにも塗りつぶし対象が種別色で重ね描きされる。"""
    from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData, create_markup_annot

    pdf_path = tmp_path / "pii_thumb.pdf"
    _make_pii_pdf(pdf_path)
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((0.0, 0.0, 400.0, 200.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity="PERSON",
            pii_text="山田太郎",
        ),
    )
    window = _create_window(qtbot, pdf_path)
    window._reset_thumbnail_render_queue()
    window._thumbnails[0].invalidate_thumbnail()
    window._thumb_render_queue.append(0)
    window._thumb_render_queue_set.add(0)
    window._process_thumbnail_render_queue()
    thumb = window._thumbnails[0]
    assert thumb.thumbnail_loaded

    image = thumb._image_label.pixmap().toImage()
    px = image.pixelColor(image.width() // 2, 2)
    # 実PDF注釈(種別色の35%・不透明度0.35=暗い灰色)ではなく、赤系の重ね描きになる。
    assert px.red() > 200 and px.green() < 200 and px.blue() < 200
