"""個人情報(PII)検出ドロワーのロジック(PageEditWindowのmixin)。

``ZoomAnnotationMixin``(付箋)/``BookmarksPanel``(しおり)と同じ、
「ドロワーは表示専用、PDFの読み書き・Undo登録はmixin側が持つ」という
役割分担に従う。検出そのもの(SudachiPy形態素解析)は重いため
``src.workers.pii_detect_worker.PiiDetectWorker`` でバックグラウンド実行する。

塗りつぶし対象は2種類ある:
    - 塗りつぶし候補(``TextMarkupAnnotData`` + ``pii_entity``): 検出結果、または
      手動で「テキスト候補」ツールで選択したテキスト。
    - 塗りつぶし用図形(``ShapeAnnotData`` + ``pii_entity``): 「塗り四角」/「塗り丸」
      ツールで作成した矩形/楕円(写真・印影・手書き等、文字の無い領域用)。
どちらも通常のマーカー/図形とは ``pii_entity`` の有無で区別され、混同しない。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from src.pii.detection_service import PiiDetection, expand_quads_for_ocr, run_detection
from src.pii.entity_types import (
    ENTITY_TYPES,
    ENTITY_TYPES_WITH_MANUAL,
    MANUAL_ENTITY_TYPE,
    get_entity_type_name_ja,
)
from src.pii.engines import ENGINE_KEYS
from src.pii.pdf_text_map import text_under_ellipse, text_under_rect
from src.pii.settings import PiiSettings
from src.pii.text_normalize import normalize_1to1
from src.views.page_edit_annotations import CreateMode, _AnnotRef
from src.utils.pdf_utils import (
    MarkupType,
    PdfWritePermissionError,
    ShapeAnnotData,
    ShapeType,
    TextMarkupAnnotData,
    create_markup_annot,
    create_markup_annots,
    create_shape_annot,
    delete_markup_annot,
    delete_markup_annots,
    delete_shape_annot,
    export_pages_as_images,
    export_pdf_compressed,
    get_page_chars,
    get_page_count,
    list_pii_mask_shapes,
    list_pii_markup_annots,
    rasterize_pdf,
    redact_pdf_remove_text,
    restyle_pii_annots,
    set_pii_annot_style,
)
from src.views.export_dialog import ExportOptionsDialog
from src.views.pii_panel import PiiPanel, PiiResultRow, ScopeChoiceDialog
from src.views.pii_settings_dialog import PiiSettingsDialog
from src.workers.pii_detect_worker import PiiDetectWorker

logger = logging.getLogger(__name__)

# PIIハイライトの既定の透明度。手動マーカーより薄くして、下の文字を
# 読みながら検出範囲を確認しやすくする。ズームビュー上の実際の見た目(設定の
# 色・透明度)は page_edit_widgets.ZoomPageWidget._paint_markup_annotation/
# _paint_shape_annotation が pii_entity の有無を見て描き分けるため、この不透明度は
# 主にPDF側の見た目(Acrobat等の外部ビューア)に効く。
PII_HIGHLIGHT_OPACITY = 0.35
# 手動で追加する塗りつぶし対象(テキスト候補/図形)の種別。常に「手動」固定。
MANUAL_MASK_ENTITY = MANUAL_ENTITY_TYPE
MASK_SHAPE_STROKE_WIDTH = 1.2
MASK_SHAPE_OPACITY = 0.35


class PiiDrawerMixin:
    """PageEditWindow に混ぜ込む個人情報検出ドロワー機能。"""

    # ------------------------------------------------------------------
    # ドロワーの組み立て・開閉
    # ------------------------------------------------------------------
    def _build_pii_drawer(self) -> "PiiPanel":
        """個人情報検出ドロワーを組み立てる。"""
        self._pii_panel = PiiPanel()
        self._pii_worker = None  # 実行中の検出ワーカー(無ければ None)
        self._pii_panel.detect_current_page_requested.connect(self._on_pii_detect_current_page)
        self._pii_panel.detect_all_pages_requested.connect(self._on_pii_detect_all_pages)
        self._pii_panel.keep_existing_toggled.connect(self._on_pii_keep_existing_toggled)
        self._pii_panel.mask_markup_tool_toggled.connect(self._on_pii_mask_markup_toggled)
        self._pii_panel.mask_rect_tool_toggled.connect(self._on_pii_mask_rect_toggled)
        self._pii_panel.mask_ellipse_tool_toggled.connect(self._on_pii_mask_ellipse_toggled)
        self._pii_panel.remove_selected_requested.connect(self._on_pii_remove_selected)
        self._pii_panel.add_exclude_word_requested.connect(self._on_pii_add_exclude_word)
        self._pii_panel.add_detect_word_requested.connect(self._on_pii_add_detect_word)
        self._pii_panel.settings_requested.connect(self._on_pii_settings_requested)
        self._pii_panel.export_requested.connect(self._on_pii_export_requested)
        self._pii_panel.result_activated.connect(self._on_pii_result_activated)
        self._pii_panel.open_changed.connect(self._on_pii_drawer_open_changed)
        self._pii_panel.entity_visibility_changed.connect(self._on_pii_entity_visibility_changed)
        self._pii_panel.mask_color_changed.connect(self._on_pii_mask_color_changed)
        self._pii_panel.mask_transparency_changed.connect(self._on_pii_mask_transparency_changed)
        # 開閉トグルはツールバーの「個人情報検出」ボタンへ移設するため内蔵トグルを隠す
        self._pii_panel.use_external_toggle()
        self._pii_panel.set_keep_existing_checked(self._pii_settings().keep_existing_on_detect)
        self._sync_pii_panel_from_settings()
        return self._pii_panel

    def _on_pii_keep_existing_toggled(self, checked: bool) -> None:
        settings = self._pii_settings().copy()
        settings.keep_existing_on_detect = checked
        settings.save()
        self._pii_settings_cache = settings

    def _sync_pii_panel_from_settings(self) -> None:
        """設定(種別の表示/検出・色・透明度)をパネルとズームビューへ反映する。"""
        settings = self._pii_settings()
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_entity_visibility(
                {et: settings.is_entity_visible(et) for et in ENTITY_TYPES_WITH_MANUAL}
            )
            panel.set_mask_style(settings.mask_color, settings.mask_transparency)
            panel.set_manual_tools_enabled(settings.manual_visible)
        self._apply_pii_visual_settings()

    def _apply_pii_visual_settings(self) -> None:
        """塗りつぶし候補の色・透明度と、非表示にする種別をズームビューへ反映する。"""
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        zoom_label = getattr(self, "_zoom_label", None)
        if zoom_label is not None:
            zoom_label.set_pii_mask_style(settings.mask_color, settings.mask_opacity)
            zoom_label.set_pii_hidden_entities(settings.hidden_entities())

    def _sync_pii_annot_style_registry(self) -> None:
        """PDFへ書くPII注釈の色・不透明度・非表示種別(他のPDFソフト向け)を、現在の設定に合わせる。

        ユーティリティ層(``pdf_utils.annotations``)の注釈作成関数がこの値を参照する
        (Undo/Redoによる再作成も同じ経路のため、常に最新の設定で書かれる)。
        """
        settings = self._pii_settings()
        set_pii_annot_style(
            settings.mask_color, settings.mask_opacity, settings.hidden_entities()
        )

    def _restyle_pii_annots_in_pdf(self) -> None:
        """ファイル内のPII注釈を、現在の色・透明度・チェック状態に揃える(Undo対象外)。

        設定は全ファイル共通、注釈はファイルごとに持つため、設定の確定時と
        個人情報検出ドロワーを開いたときに呼ぶ。他のPDFソフトで開いたとき、
        チェック中の種別は選んだ色・透明度の注釈として表示され、チェックを外した
        種別は見えなくなる。書き込めないPDFでは何もせずヒントだけ表示する。
        """
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        try:
            changed = restyle_pii_annots(
                self._pdf_path,
                settings.mask_color,
                settings.mask_opacity,
                settings.hidden_entities(),
            )
        except PdfWritePermissionError:
            logger.warning("PII注釈の色・表示状態を更新できませんでした(書き込み不可): %s", self._pdf_path)
            self._flash_zoom_hint("PDFに書き込めないため、他のPDFソフト向けの注釈の色・表示状態は更新されません")
            return
        except Exception:  # noqa: BLE001 - 見た目の同期失敗で操作を止めない
            logger.warning("PII注釈の色・表示状態の更新に失敗しました", exc_info=True)
            return
        if changed:
            # 画面上のデータ(注釈の色・不透明度)もファイルに合わせて読み直す。
            self._refresh_current_zoom_page()

    def _on_pii_entity_visibility_changed(self, entity: str, checked: bool) -> None:
        """種別チェックボックスの操作。設定へ即保存し、一覧・ページ上・サムネイルを更新する。"""
        settings = self._pii_settings().copy()
        if entity == MANUAL_ENTITY_TYPE:
            settings.manual_visible = bool(checked)
        elif entity in ENTITY_TYPES:
            settings.enabled_entities[entity] = bool(checked)
        else:
            return
        settings.save()
        self._pii_settings_cache = settings
        self._apply_pii_visual_settings()
        self._restyle_pii_annots_in_pdf()
        selected = self._selected_zoom_annotation
        if not checked and selected is not None and getattr(selected, "pii_entity", "") == entity:
            # 非表示にした種別の注釈が選択中のままだと、見えないのに Delete で消せてしまう。
            self._set_selected_zoom_annotation(None)
        if entity == MANUAL_ENTITY_TYPE:
            panel = getattr(self, "_pii_panel", None)
            if panel is not None:
                panel.set_manual_tools_enabled(bool(checked))
            if not checked and self._create_mode in (CreateMode.MASK_MARKUP, CreateMode.MASK_SHAPE):
                # 手動が非表示の間は追加しても見えないため、装着中のツールは解除する。
                self._activate_create_mode(CreateMode.NONE)
        self._reload_pii_results()
        self._invalidate_and_requeue_thumbnails()

    def _on_pii_mask_color_changed(self, color: object) -> None:
        settings = self._pii_settings().copy()
        settings.mask_color = (float(color[0]), float(color[1]), float(color[2]))
        settings.save()
        self._pii_settings_cache = settings
        self._apply_pii_visual_settings()
        self._restyle_pii_annots_in_pdf()
        self._invalidate_and_requeue_thumbnails()

    def _on_pii_mask_transparency_changed(self, value: int, commit: bool) -> None:
        """透明度スライダ。ドラッグ中は画面へ即時反映するだけで、保存は確定時に行う。"""
        settings = self._pii_settings().copy()
        settings.mask_transparency = int(value)
        if commit:
            settings.save()
        self._pii_settings_cache = settings
        self._apply_pii_visual_settings()
        if commit:
            self._restyle_pii_annots_in_pdf()
            self._invalidate_and_requeue_thumbnails()

    def _toggle_pii_drawer(self) -> None:
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_open(not panel.is_open)

    def _on_pii_drawer_open_changed(self, is_open: bool) -> None:
        if getattr(self, "_zoom_pii_btn", None) is not None:
            self._zoom_pii_btn.setChecked(is_open)
        self._sync_zoom_panel_button()
        if is_open:
            # 横幅を確保するため、他のドロワーは閉じる(付箋/しおりと排他)。
            if self._zoom_annotation_open:
                self._set_zoom_annotation_drawer_open(False)
            bookmarks_panel = getattr(self, "_bookmarks_panel", None)
            if bookmarks_panel is not None and bookmarks_panel.is_open:
                bookmarks_panel.set_open(False)
            ocr_panel = getattr(self, "_ocr_panel", None)
            if ocr_panel is not None and ocr_panel.is_open:
                ocr_panel.set_open(False)
            # 設定は全ファイル共通・注釈はファイルごとなので、開いたファイルの
            # PII注釈を現在の設定(色・透明度・チェック)に揃える。
            self._restyle_pii_annots_in_pdf()
            self._reload_pii_results()
        else:
            # ドロワーを閉じたら手動ツール(テキスト候補/塗り四角/塗り丸)も解除する。
            if self._create_mode in (CreateMode.MASK_MARKUP, CreateMode.MASK_SHAPE):
                self._activate_create_mode(CreateMode.NONE)

    def _pii_settings(self) -> PiiSettings:
        """設定はウィンドウ内でキャッシュし、設定ダイアログ確定時にのみ更新する。"""
        settings = getattr(self, "_pii_settings_cache", None)
        if settings is None:
            settings = PiiSettings.load()
            self._pii_settings_cache = settings
        return settings

    # ------------------------------------------------------------------
    # 検出実行
    # ------------------------------------------------------------------
    def _on_pii_detect_current_page(self) -> None:
        if self._zoom_page_num is None:
            return
        self._run_pii_detection([self._zoom_page_num])

    def _on_pii_detect_all_pages(self) -> None:
        page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            return
        self._run_pii_detection(list(range(page_count)))

    def _run_pii_detection(self, page_indices: list[int]) -> None:
        if getattr(self, "_pii_worker", None) is not None or self._ocr_busy():
            return  # 実行中は多重起動しない
        # 設定「テキストレイヤの無いページはOCRしてから検出する」: 先にOCR(バックグラウンド)
        # → 結果をメインスレッドで埋め込み → 検出、の順に進める。
        if self._pii_settings().ocr_enabled and self._start_ocr_before_detect(
            page_indices, lambda: self._start_pii_detection_worker(page_indices)
        ):
            return
        self._start_pii_detection_worker(page_indices)

    def _start_pii_detection_worker(self, page_indices: list[int]) -> None:
        if getattr(self, "_pii_worker", None) is not None:
            return
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_busy(True, "検出を開始しています...")
        worker = PiiDetectWorker(self._pdf_path, page_indices, self._pii_settings(), parent=self)
        worker.progress.connect(self._on_pii_detect_progress)
        worker.finished.connect(
            lambda results: self._on_pii_detect_finished(page_indices, results)
        )
        worker.error.connect(self._on_pii_detect_error)
        self._pii_worker = worker
        worker.start()

    def _on_pii_detect_progress(self, done: int, total: int) -> None:
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_progress(done, total)

    def _on_pii_detect_error(self, error: Exception) -> None:
        self._pii_worker = None
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_busy(False)
        logger.warning("個人情報検出に失敗しました: %s", error)
        QMessageBox.warning(self, "個人情報検出", f"検出に失敗しました。\n\n{error}")

    def _on_pii_detect_finished(
        self, page_indices: list[int], detections: list[PiiDetection]
    ) -> None:
        self._pii_worker = None
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_busy(False)

        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        new_items = [
            TextMarkupAnnotData(
                page_num=d.page_num,
                xref=0,
                quads=d.quads,
                markup_type=MarkupType.HIGHLIGHT,
                color=settings.mask_color,
                opacity=PII_HIGHLIGHT_OPACITY,
                pii_entity=d.entity_type,
                pii_text=d.text,
            )
            for d in detections
        ]

        if panel is not None and panel.keep_existing_checked():
            # 「既存の結果を残して追加検出」がオンの場合は既存の塗りつぶし候補
            # (このページ含む全ページ)を一切消さず、重複しない新規分だけを追加する。
            deduped_new_items = self._filter_new_markup_duplicates(new_items)
            if not deduped_new_items:
                panel.set_busy(False, "新しい個人情報は検出されませんでした")
                return
            self._append_new_pii_markups(
                deduped_new_items, f"個人情報検出で追加 ({len(deduped_new_items)}件)"
            )
            return

        # 従来通りの動作: 再検出したページ上の既存PIIハイライトは置き換える
        # (同じ範囲が何度も重ねて追加されるのを防ぐ)。
        # ただし、パネルでチェックが外れている種別(検出対象外)は触らない。
        target_pages = set(page_indices)
        old_annots = [
            a
            for a in list_pii_markup_annots(self._pdf_path)
            if a.page_num in target_pages and settings.is_entity_visible(a.pii_entity)
        ]

        if not old_annots and not new_items:
            if panel is not None:
                panel.set_busy(False, "個人情報は検出されませんでした")
            return

        # 他のマーカー編集(_apply_eraser_to_selection等)と同じく、削除/再作成で
        # xrefが回っても後続のUndo/Redoが常に生きているxrefを参照できるよう、
        # _AnnotRef ハンドルを経由する。
        old_refs = [self._annot_ref_for(a.page_num, a.xref) for a in old_annots]
        new_refs: list[_AnnotRef | None] = [None] * len(new_items)

        def do_apply() -> None:
            if old_refs:
                delete_markup_annots(
                    self._pdf_path, [(ref.page_num, ref.xref) for ref in old_refs]
                )
                for ref in old_refs:
                    self._release_annot_ref(ref)
            created = create_markup_annots(self._pdf_path, new_items) if new_items else []
            for i, saved in enumerate(created):
                if new_refs[i] is None:
                    new_refs[i] = self._register_annot_ref(saved.page_num, saved.xref)
                else:
                    self._rebind_annot_ref(new_refs[i], saved.page_num, saved.xref)
            self._reload_pii_results()
            self._refresh_current_zoom_page()

        def undo_apply() -> None:
            live_new_refs = [ref for ref in new_refs if ref is not None]
            if live_new_refs:
                delete_markup_annots(
                    self._pdf_path, [(ref.page_num, ref.xref) for ref in live_new_refs]
                )
                for ref in live_new_refs:
                    self._release_annot_ref(ref)
            if old_refs:
                recreated = create_markup_annots(self._pdf_path, old_annots)
                for ref, saved in zip(old_refs, recreated):
                    self._rebind_annot_ref(ref, saved.page_num, saved.xref)
            self._reload_pii_results()
            self._refresh_current_zoom_page()

        # _push_undoable は PdfWritePermissionError を内部で捕捉し、警告表示まで
        # 行う(呼び出し側での再捕捉は不要。_apply_eraser_to_selection 等と同じ)。
        self._push_undoable(f"個人情報検出 ({len(new_items)}件)", do_apply, undo_apply)

    # ------------------------------------------------------------------
    # 既存結果を残した追加(「既存の結果を残して追加検出」/追加パターンでの
    # 部分再検出/「追加パターンに登録」)で共用するヘルパー
    # ------------------------------------------------------------------
    @staticmethod
    def _quad_key(quads) -> tuple:
        """quad群を比較用のキーに変換する(浮動小数の誤差を丸めて吸収する)。"""
        return tuple(tuple(round(float(v), 1) for v in q) for q in quads)

    def _existing_markup_keys(self) -> set[tuple[int, tuple, str]]:
        """既存の塗りつぶし候補マーカーの(ページ, quad, 語句)キー集合。"""
        return {
            (a.page_num, self._quad_key(a.quads), a.pii_text)
            for a in list_pii_markup_annots(self._pdf_path)
        }

    def _filter_new_markup_duplicates(
        self, items: list[TextMarkupAnnotData]
    ) -> list[TextMarkupAnnotData]:
        """既存マーカー、および ``items`` 内部の重複を除いた新規分だけを返す。

        「同じページ・同じ位置・同じ語句」を重複とみなす(要望:既存の結果を
        残して追加検出/追加パターンでの部分再検出のいずれからも使う)。
        """
        seen = self._existing_markup_keys()
        result: list[TextMarkupAnnotData] = []
        for item in items:
            key = (item.page_num, self._quad_key(item.quads), item.pii_text)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _append_new_pii_markups(
        self, new_items: list[TextMarkupAnnotData], description: str
    ) -> None:
        """既存の塗りつぶし候補は変更せず、新規マーカーだけを追加する(Undo対応)。"""
        if not new_items:
            return
        self._sync_pii_annot_style_registry()
        new_refs: list[_AnnotRef | None] = [None] * len(new_items)

        def do_apply() -> None:
            created = create_markup_annots(self._pdf_path, new_items)
            for i, saved in enumerate(created):
                if new_refs[i] is None:
                    new_refs[i] = self._register_annot_ref(saved.page_num, saved.xref)
                else:
                    self._rebind_annot_ref(new_refs[i], saved.page_num, saved.xref)
            self._reload_pii_results()
            self._refresh_current_zoom_page()

        def undo_apply() -> None:
            live_refs = [ref for ref in new_refs if ref is not None]
            if live_refs:
                delete_markup_annots(
                    self._pdf_path, [(ref.page_num, ref.xref) for ref in live_refs]
                )
                for ref in live_refs:
                    self._release_annot_ref(ref)
            self._reload_pii_results()
            self._refresh_current_zoom_page()

        self._push_undoable(description, do_apply, undo_apply)

    # ------------------------------------------------------------------
    # 手動追加: テキスト候補 / 塗り四角 / 塗り丸
    # ------------------------------------------------------------------
    def _on_pii_mask_markup_toggled(self, checked: bool) -> None:
        if checked and self._zoom_label is not None and self._zoom_label._selected_char_indices:
            # 先にページ上でテキストを選択してから「テキスト候補」を押した場合は、
            # その場で候補を作って連続モードには入らない(ボタンは押下前の状態へ戻す)。
            # _activate_create_mode は選択を消してしまうため、必ずその前に読む。
            self._create_mask_candidate_from_selection()
            self._zoom_label.clear_text_selection()
            panel = getattr(self, "_pii_panel", None)
            if panel is not None:
                panel.set_mask_markup_tool_active(False)
            return
        self._activate_create_mode(CreateMode.MASK_MARKUP if checked else CreateMode.NONE)

    def _on_pii_mask_rect_toggled(self, checked: bool) -> None:
        if checked:
            self._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.RECTANGLE)
        else:
            self._activate_create_mode(CreateMode.NONE)

    def _on_pii_mask_ellipse_toggled(self, checked: bool) -> None:
        if checked:
            self._activate_create_mode(CreateMode.MASK_SHAPE, ShapeType.ELLIPSE)
        else:
            self._activate_create_mode(CreateMode.NONE)

    def _create_mask_candidate_from_selection(self) -> None:
        """「テキスト候補」ツール: 選択中のテキストを塗りつぶし候補にする。

        ``page_edit_annotations.ZoomAnnotationMixin._on_zoom_text_selection_released``
        から ``CreateMode.MASK_MARKUP`` のときだけ呼ばれる(cross-mixin呼び出し。
        PiiDrawerMixin が既に ``_annot_ref_for`` 等を同様に呼んでいるのと同じ規約)。
        """
        if self._zoom_page_num is None or self._zoom_label is None:
            return
        quads = self._zoom_label.selected_markup_quads()
        if not quads:
            self._flash_zoom_hint("塗りつぶし候補にするテキストを選択してください")
            return
        # OCRで埋め込んだ文字を選択した場合は、自動検出と同じくOCRの位置誤差を見込んだ
        # 余白ぶん広げる(通常のテキストの quad はそのまま)。
        quads = expand_quads_for_ocr(self._pdf_path, self._zoom_page_num, list(quads))
        matched_text = self._zoom_label.selected_text()
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        template = TextMarkupAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            quads=tuple(quads),
            markup_type=MarkupType.HIGHLIGHT,
            color=settings.mask_color,
            opacity=PII_HIGHLIGHT_OPACITY,
            pii_entity=MANUAL_MASK_ENTITY,
            pii_text=matched_text,
        )
        self._run_zoom_create(
            "塗りつぶし候補を追加",
            lambda: create_markup_annot(self._pdf_path, template),
            lambda *a: delete_markup_annot(*a),
            select_created=False,
        )

    def _create_mask_shape(
        self, shape_type: ShapeType, rect_tuple: tuple[float, float, float, float]
    ) -> None:
        """「塗り四角」/「塗り丸」ツール: ドラッグ確定した矩形/楕円を塗りつぶし用図形にする。

        ``page_edit_annotations.ZoomAnnotationMixin._on_zoom_shape_create_requested``
        から ``CreateMode.MASK_SHAPE`` のときだけ呼ばれる。
        """
        if self._zoom_page_num is None:
            return
        chars = get_page_chars(self._pdf_path, self._zoom_page_num)
        if shape_type == ShapeType.ELLIPSE:
            matched_text = text_under_ellipse(chars, rect_tuple)
        else:
            matched_text = text_under_rect(chars, rect_tuple)
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        template = ShapeAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            rect=rect_tuple,
            shape_type=shape_type,
            # PDF上の注釈の色は作成時点の設定色(全種別共通)にする。ズームビュー上の
            # 実際の見た目は ZoomPageWidget._paint_shape_annotation が常に現在の
            # 設定色・透明度で描くため、後から色を変えても既存の図形が追従する。
            stroke_color=settings.mask_color,
            fill_color=settings.mask_color,
            stroke_width=MASK_SHAPE_STROKE_WIDTH,
            opacity=MASK_SHAPE_OPACITY,
            pii_entity=MANUAL_MASK_ENTITY,
            pii_text=matched_text,
        )
        shape_label = "楕円" if shape_type == ShapeType.ELLIPSE else "四角"
        self._run_zoom_create(
            f"塗りつぶし用の{shape_label}を追加",
            lambda: create_shape_annot(self._pdf_path, template),
            lambda *a: delete_shape_annot(*a),
            select_created=False,
        )

    # ------------------------------------------------------------------
    # 結果一覧
    # ------------------------------------------------------------------
    def _build_pii_result_rows(self) -> list[PiiResultRow]:
        """検出結果一覧に表示する行(塗りつぶし候補+塗りつぶし用図形)を組み立てる。

        ``pii_text`` が空(旧バージョンが作成したハイライト、または座標だけ持つ
        塗りつぶし用図形)の場合は、その場でページ文字から抽出してフォールバックする。
        """
        rows: list[PiiResultRow] = []
        chars_cache: dict[int, list[dict]] = {}
        settings = self._pii_settings()

        def chars_for(page_num: int) -> list[dict]:
            cached = chars_cache.get(page_num)
            if cached is None:
                cached = get_page_chars(self._pdf_path, page_num)
                chars_cache[page_num] = cached
            return cached

        for annot in list_pii_markup_annots(self._pdf_path):
            if not settings.is_entity_visible(annot.pii_entity):
                continue  # パネルでチェックが外れた種別は一覧に出さない
            text = annot.pii_text
            if not text:
                text = text_under_rect(chars_for(annot.page_num), annot.rect)
            rows.append(
                PiiResultRow(
                    annot=annot,
                    page_num=annot.page_num,
                    entity=annot.pii_entity,
                    text=text,
                    kind="markup",
                )
            )

        for shape in list_pii_mask_shapes(self._pdf_path):
            if not settings.is_entity_visible(shape.pii_entity):
                continue
            # 図形は移動/リサイズ(dataclasses.replaceでrectだけ変わる)できるため、
            # 作成時にキャッシュした shape.pii_text は移動後は古くなり得る。
            # 図形は文字数も少なく再抽出が軽いため、常にその場で計算し直す
            # (キャッシュを信用しない)。マーカー(検出結果)側は逆に検出時の
            # テキストの方が正確なため pii_text をそのまま使う。
            if shape.shape_type == ShapeType.ELLIPSE:
                text = text_under_ellipse(chars_for(shape.page_num), shape.rect, shape.rotation)
            elif shape.shape_type == ShapeType.RECTANGLE:
                text = text_under_rect(chars_for(shape.page_num), shape.rect, shape.rotation)
            else:
                text = shape.pii_text
            rows.append(
                PiiResultRow(
                    annot=shape,
                    page_num=shape.page_num,
                    entity=shape.pii_entity,
                    text=text,
                    kind="shape",
                )
            )

        rows.sort(key=lambda r: (r.page_num, r.kind, r.text))
        return rows

    def _reload_pii_results(self) -> None:
        panel = getattr(self, "_pii_panel", None)
        if panel is None or not panel.is_open:
            return
        panel.set_results(self._build_pii_result_rows())

    def _on_pii_result_activated(self, annot: object) -> None:
        if not isinstance(annot, (TextMarkupAnnotData, ShapeAnnotData)):
            return
        self._jump_zoom_to_page(annot.page_num + 1)
        current = self._find_zoom_annotation(annot.xref) or annot
        self._set_selected_zoom_annotation(current, open_drawer=False)

    def _reveal_pii_result(self, annot: "TextMarkupAnnotData | ShapeAnnotData") -> None:
        """ページ上でクリックした塗りつぶし対象を、結果一覧の該当行として見せる。

        個人情報検出ドロワーが閉じていれば開き(付箋ドロワー等は排他で閉じる)、
        ページ上の選択を維持したまま一覧の該当行をアクティブにしてスクロールする。
        """
        panel = getattr(self, "_pii_panel", None)
        if panel is None:
            return
        if not panel.is_open:
            panel.set_open(True)
        current = self._find_zoom_annotation(annot.xref) or annot
        self._set_selected_zoom_annotation(current, open_drawer=False)
        panel.select_result(current)

    # ------------------------------------------------------------------
    # 削除
    # ------------------------------------------------------------------
    def _on_pii_remove_selected(self) -> None:
        panel = getattr(self, "_pii_panel", None)
        if panel is None:
            return
        selected = [
            a for a in panel.selected_results() if isinstance(a, (TextMarkupAnnotData, ShapeAnnotData))
        ]
        if selected:
            self._remove_mask_targets(selected, "塗りつぶし対象を削除")

    def _remove_mask_targets(
        self, targets: list["TextMarkupAnnotData | ShapeAnnotData"], description: str
    ) -> None:
        """塗りつぶし候補(マーカー)/塗りつぶし用図形の混在リストを一括削除する。

        マーカーは ``create_markup_annots``/``delete_markup_annots`` の一括APIを、
        図形は種別ごとの単発APIを使う(塗りつぶし用図形は通常数が少ないため)。
        """
        snapshot = list(targets)
        refs = [self._annot_ref_for(a.page_num, a.xref) for a in snapshot]
        markup_pairs = [
            (ref, a) for ref, a in zip(refs, snapshot) if isinstance(a, TextMarkupAnnotData)
        ]
        shape_pairs = [
            (ref, a) for ref, a in zip(refs, snapshot) if isinstance(a, ShapeAnnotData)
        ]

        def do_delete() -> None:
            if markup_pairs:
                delete_markup_annots(
                    self._pdf_path, [(ref.page_num, ref.xref) for ref, _ in markup_pairs]
                )
            for ref, _ in shape_pairs:
                delete_shape_annot(self._pdf_path, ref.page_num, ref.xref)
            for ref in refs:
                self._release_annot_ref(ref)
            self._reload_pii_results()
            self._refresh_current_zoom_page()

        def undo_delete() -> None:
            if markup_pairs:
                recreated = create_markup_annots(self._pdf_path, [a for _, a in markup_pairs])
                for (ref, _), saved in zip(markup_pairs, recreated):
                    self._rebind_annot_ref(ref, saved.page_num, saved.xref)
            for ref, a in shape_pairs:
                saved = create_shape_annot(self._pdf_path, a)
                self._rebind_annot_ref(ref, saved.page_num, saved.xref)
            self._reload_pii_results()
            self._refresh_current_zoom_page()

        self._push_undoable(description, do_delete, undo_delete)

    # ------------------------------------------------------------------
    # 結果一覧の右クリックメニュー: 除外語に追加 / 検出語に追加
    # ------------------------------------------------------------------
    def _ask_pii_exclude_scope(self, text: str) -> str:
        """「除外語に追加」の後の範囲選択ダイアログ。``ScopeChoiceDialog.SCOPE_*`` を返す。

        テストからはこのメソッドを差し替えてモーダル表示を避ける。
        """
        return ScopeChoiceDialog.ask(
            "除外語に追加",
            "除外語に追加しました(今後の検出から除外されます)。\n"
            "すでに検出済みのこの語句の結果を削除しますか?(手動で追加した分は削除しません)",
            text,
            ("全ページの検出済みを削除", "このページだけ削除", "削除しない"),
            self,
        )

    def _ask_pii_detect_scope(self, text: str, entity: str) -> str:
        """「検出語に追加」の後の範囲選択ダイアログ。``ScopeChoiceDialog.SCOPE_*`` を返す。

        テストからはこのメソッドを差し替えてモーダル表示を避ける。
        """
        return ScopeChoiceDialog.ask(
            "検出語に追加",
            f"検出語({get_entity_type_name_ja(entity)})に追加しました。\n"
            "この語句を今すぐ検出して塗りつぶし候補に追加しますか?",
            text,
            ("全ページで検出", "このページだけ検出", "検出しない"),
            self,
        )

    @staticmethod
    def _drop_exclusions_for_text(settings: PiiSettings, text: str) -> bool:
        """``text`` を検出語へ追加するとき、同じ語句の除外設定をすべて取り除く。

        除外語(``excluded_words``)・種別別の除外語(``entity_exclusions``。空に
        なったリストは項目ごと消す)・除外パターン(``text_exclusions_regex`` の
        ``re.escape(text)`` および ``^re.escape(text)$``)から外す。除外が追加より
        優先されるため、残っていると追加した語句が検出されなくなってしまう。
        何か取り除いたら True。
        """
        removed = False
        target = text.strip()
        # 検出は全角→半角へ1文字ずつ揃えた文字列に対して行われるため、元の表記と
        # 正規化後の表記のどちらで登録されていても同じ語句として扱う。
        variants = {text, target, normalize_1to1(text), normalize_1to1(target)}
        kept_words = [
            w for w in settings.excluded_words if w.strip() not in variants
        ]
        if len(kept_words) != len(settings.excluded_words):
            settings.excluded_words = kept_words
            removed = True
        for key in list(settings.entity_exclusions):
            words = settings.entity_exclusions[key]
            if any(w in variants for w in words):
                words = [w for w in words if w not in variants]
                removed = True
            if words:
                settings.entity_exclusions[key] = words
            else:
                del settings.entity_exclusions[key]
        drop: set[str] = set()
        for variant in variants:
            escaped = re.escape(variant)
            drop.update({escaped, f"^{escaped}$"})
        kept_regex = [rx for rx in settings.text_exclusions_regex if rx not in drop]
        if len(kept_regex) != len(settings.text_exclusions_regex):
            settings.text_exclusions_regex = kept_regex
            removed = True
        return removed

    def _on_pii_add_exclude_word(self, text: str) -> None:
        """右クリック「除外語に追加」。

        語句を除外語に登録し(追加パターン=検出語には触れない)、範囲を選ばせて、
        すでに検出済みの同じ語句の結果(手動追加分を除く)をまとめて削除する
        (Undo は1回分)。
        """
        if not text or not text.strip():
            return
        settings = self._pii_settings().copy()
        if text.strip() not in {w.strip() for w in settings.excluded_words}:
            settings.excluded_words.append(text.strip())
            settings.save()
            self._pii_settings_cache = settings

        scope = self._ask_pii_exclude_scope(text)
        if scope == ScopeChoiceDialog.SCOPE_NONE:
            return
        page_filter: int | None = None
        if scope == ScopeChoiceDialog.SCOPE_PAGE:
            if self._zoom_page_num is None:
                return
            page_filter = self._zoom_page_num
        targets = [
            r.annot
            for r in self._build_pii_result_rows()
            if r.entity != MANUAL_ENTITY_TYPE
            and r.text.strip() == text.strip()
            and (page_filter is None or r.page_num == page_filter)
        ]
        if targets:
            self._remove_mask_targets(targets, f"除外語「{text.strip()}」の検出済みを削除")

    def _on_pii_add_detect_word(self, entity: str, text: str) -> None:
        """右クリック「検出語に追加」(種別はサブメニューで選択済み)。

        選んだ語句をそのまま(``re.escape`` した)追加検出パターンとして登録し、
        同じ語句の除外設定を取り除く。続けて範囲(全ページ/このページだけ/検出しない)
        を選ばせ、選ばれたら、そのパターンだけで部分再検出する(既存の検出結果は
        そのまま、重複しない新規分だけを追加する)。
        """
        if not text or not text.strip() or entity not in ENTITY_TYPES:
            return
        # パターンは、全角→半角へ1文字ずつ揃えた検出用テキストに対して使われるため、
        # 語句も同じ正規化をかけてから(記号をエスケープして)登録する。
        pattern = re.escape(normalize_1to1(text))
        settings = self._pii_settings().copy()
        entry = (entity, pattern)
        added = entry not in settings.additional_patterns
        if added:
            settings.additional_patterns.append(entry)
        removed_exclusion = self._drop_exclusions_for_text(settings, text)
        if added or removed_exclusion:
            settings.save()
            self._pii_settings_cache = settings

        scope = self._ask_pii_detect_scope(text, entity)
        if scope == ScopeChoiceDialog.SCOPE_PAGE:
            if self._zoom_page_num is None:
                return
            page_indices = [self._zoom_page_num]
        elif scope == ScopeChoiceDialog.SCOPE_ALL:
            page_count = get_page_count(self._pdf_path)
            if page_count <= 0:
                return
            page_indices = list(range(page_count))
        else:
            return
        self._run_pattern_only_detection(entity, pattern, page_indices)

    def _run_pattern_only_detection(
        self, entity: str, pattern: str, page_indices: list[int]
    ) -> None:
        """追加パターン1件だけを使って対象ページを部分再検出し、結果に追加する。

        他の検出エンジン(正規表現/形態素解析/日時)は無効化し、
        他の種別の検出も行わない(この操作は「登録したパターンで追加検出する」
        ためのものであり、通常の全種別検出をやり直すものではない)。
        除外設定は現在の設定をそのまま引き継ぐ(除外が追加より優先されるため、
        まだ除外に入っている語句は検出されない)。
        件数が少なく軽量な処理のため、バックグラウンドワーカーは使わず同期実行する。
        """
        current = self._pii_settings()
        enabled_entities = {et: (et == entity) for et in ENTITY_TYPES}
        pattern_settings = PiiSettings(
            enabled_entities=enabled_entities,
            additional_patterns=[(entity, pattern)],
            text_exclusions_regex=list(current.text_exclusions_regex),
            entity_exclusions={k: list(v) for k, v in current.entity_exclusions.items()},
            excluded_words=list(current.excluded_words),
            enabled_engines={key: False for key in ENGINE_KEYS},
            dedupe_enabled=False,
        )
        try:
            detections = run_detection(self._pdf_path, page_indices, pattern_settings)
        except Exception as error:  # noqa: BLE001 - ダイアログで詳細を提示する
            logger.warning("検出語での検出に失敗しました: %s", error, exc_info=True)
            QMessageBox.warning(self, "個人情報検出", f"追加検出に失敗しました。\n\n{error}")
            return

        settings = self._pii_settings()
        new_items = [
            TextMarkupAnnotData(
                page_num=d.page_num,
                xref=0,
                quads=d.quads,
                markup_type=MarkupType.HIGHLIGHT,
                color=settings.mask_color,
                opacity=PII_HIGHLIGHT_OPACITY,
                pii_entity=d.entity_type,
                pii_text=d.text,
            )
            for d in detections
        ]
        new_items = self._filter_new_markup_duplicates(new_items)
        if not new_items:
            QMessageBox.information(self, "個人情報検出", "新しく検出された箇所はありませんでした。")
            return
        self._append_new_pii_markups(new_items, f"検出語で検出 ({len(new_items)}件)")

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------
    def _on_pii_settings_requested(self) -> None:
        dialog = PiiSettingsDialog(self._pii_settings(), self)
        if dialog.exec():
            new_settings = dialog.result_settings()
            new_settings.save()
            self._pii_settings_cache = new_settings
            self._sync_pii_panel_from_settings()

    # ------------------------------------------------------------------
    # エクスポート(黒塗り+文字削除 → 形式・解像度・圧縮を選んで書き出し)
    # ------------------------------------------------------------------
    def _collect_mask_targets(
        self,
    ) -> tuple[
        list[TextMarkupAnnotData],
        list[ShapeAnnotData],
        list["TextMarkupAnnotData | ShapeAnnotData"],
    ]:
        """エクスポート対象の塗りつぶし候補/図形と、対象外にした注釈の一覧を返す。

        パネルでチェックが外れている種別は対象外(黒塗り・文字削除は行わない)。
        ただし対象外の注釈も含め、PII注釈は出力からすべて取り除かれる
        (注釈のSubjectに検出した語句が入っているため、出力に残さない)。
        """
        settings = self._pii_settings()
        markups = list_pii_markup_annots(self._pdf_path)
        shapes = list_pii_mask_shapes(self._pdf_path)
        markup_targets = [a for a in markups if settings.is_entity_visible(a.pii_entity)]
        shape_targets = [a for a in shapes if settings.is_entity_visible(a.pii_entity)]
        skipped = [
            a for a in (*markups, *shapes) if not settings.is_entity_visible(a.pii_entity)
        ]
        return markup_targets, shape_targets, skipped

    @staticmethod
    def _skipped_note(skipped_annots: list) -> str:
        """完了メッセージに添える「非表示の種別は対象外」の注記(0件なら空文字)。"""
        count = len(skipped_annots)
        if count <= 0:
            return ""
        return f"\n\n非表示の種別 {count} 件は対象外です。"

    def _ask_pii_export_options(self) -> "dict | None":
        """エクスポート設定ダイアログ(通常のエクスポートと同じもの)を開き、選択を返す。

        キャンセル時は None。テストからはこのメソッドを差し替えてモーダル表示を避ける。
        """
        dialog = ExportOptionsDialog(
            self, note="チェック中の種別は黒塗りし、下の文字を削除します。"
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.get_options()

    def _on_pii_export_requested(self) -> None:
        """「エクスポート...」。

        チェック中の種別の塗りつぶし対象は常に黒で塗りつぶし、下の文字を削除する
        (パネルの色・透明度は関係しない)。その結果に対し、通常のエクスポートと
        同じ設定(画像化・解像度・圧縮・PNG/JPEG等)を適用して書き出す。
        """
        markup_targets, shape_targets, skipped = self._collect_mask_targets()
        if not markup_targets and not shape_targets:
            QMessageBox.information(
                self,
                "個人情報検出",
                "黒塗りする塗りつぶし対象がありません。" + self._skipped_note(skipped),
            )
            return

        options = self._ask_pii_export_options()
        if options is None:
            return

        src_path = Path(self._pdf_path)
        fmt = options["format"]
        output_path = ""
        out_dir = ""
        if fmt == "pdf":
            default_path = str(src_path.parent / f"{src_path.stem}_黒塗り.pdf")
            output_path, _ = QFileDialog.getSaveFileName(
                self, "黒塗りしてエクスポート", default_path, "PDF Files (*.pdf)"
            )
            if not output_path:
                return
        else:
            out_dir = QFileDialog.getExistingDirectory(self, "エクスポート先フォルダを選択")
            if not out_dir:
                return

        # 黒塗り+文字削除の対象領域。quadごとに(複数行にまたがる検出は外接矩形だと
        # 行間の無関係な文字まで覆ってしまうため)、図形は rotation を末尾に付けて渡す。
        remove_xrefs: dict[int, list[int]] = {}
        redact_rects: dict[int, list[tuple[float, ...]]] = {}
        redact_ellipses: dict[int, list[tuple[float, ...]]] = {}
        padding = 1.0
        # 対象外の種別を含め、PII注釈は出力からすべて取り除く(塗りつぶしは対象のみ)。
        for annot in (*markup_targets, *shape_targets, *skipped):
            remove_xrefs.setdefault(annot.page_num, []).append(annot.xref)
        for annot in markup_targets:
            for x0, y0, x1, y1 in annot.quads:
                redact_rects.setdefault(annot.page_num, []).append(
                    (x0 - padding, y0 - padding, x1 + padding, y1 + padding)
                )
        for shape in shape_targets:
            region = (*shape.rect, shape.rotation)
            if shape.shape_type == ShapeType.ELLIPSE:
                redact_ellipses.setdefault(shape.page_num, []).append(region)
            else:
                redact_rects.setdefault(shape.page_num, []).append(region)

        try:
            # 画像名(<元名>_黒塗り_pN.png 等)が正しくなるよう、一時フォルダ内に
            # 最終名と同じ stem で黒塗り済みPDFを作り、そこから書き出す。
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_pdf = os.path.join(tmp_dir, f"{src_path.stem}_黒塗り.pdf")
                redact_pdf_remove_text(
                    self._pdf_path,
                    tmp_pdf,
                    remove_xrefs=remove_xrefs,
                    redact_rects=redact_rects,
                    redact_ellipses=redact_ellipses,
                    fill_color=(0.0, 0.0, 0.0),
                )
                created = self._write_pii_export(tmp_pdf, options, output_path, out_dir)
        except Exception as error:  # noqa: BLE001 - ダイアログで詳細を提示する
            logger.warning("PIIエクスポートに失敗しました: %s", error, exc_info=True)
            QMessageBox.warning(
                self, "個人情報検出", f"エクスポートに失敗しました。\n\n{error}"
            )
            return

        if fmt == "pdf":
            message = (
                "黒塗り済みのPDFを書き出しました(元のファイルは変更されていません)。"
                f"\n\n{output_path}"
            )
        else:
            message = (
                f"黒塗り済みの画像を {len(created)} ページ分書き出しました"
                f"(元のファイルは変更されていません)。\n\n{out_dir}"
            )
        QMessageBox.information(
            self, "個人情報検出", message + self._skipped_note(skipped)
        )

    @staticmethod
    def _write_pii_export(
        tmp_pdf: str, options: dict, output_path: str, out_dir: str
    ) -> list[str]:
        """黒塗り済みの一時PDFへ、通常のエクスポートと同じ形式・圧縮設定を適用して書き出す。"""
        fmt = options["format"]
        if fmt != "pdf":
            return export_pages_as_images(
                tmp_pdf,
                out_dir,
                fmt=fmt,
                dpi=options["dpi"],
                quality=options["jpeg_quality"],
            )
        if options["rasterize"]:
            rasterize_pdf(
                tmp_pdf,
                output_path,
                dpi=options["pdf_image_dpi"],
                image_format=options["rasterize_format"],
                jpeg_quality=options["pdf_image_quality"],
            )
        elif options["pdf_optimize_level"] > 0:
            export_pdf_compressed(
                tmp_pdf,
                output_path,
                optimize_level=options["pdf_optimize_level"],
                image_dpi=options["pdf_image_dpi"],
                image_quality=options["pdf_image_quality"],
            )
        else:
            shutil.copy2(tmp_pdf, output_path)
        return [output_path]
