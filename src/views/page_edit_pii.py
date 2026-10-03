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
import time
from contextlib import contextmanager
from dataclasses import replace as dataclass_replace
from pathlib import Path

from PyQt6.QtCore import QTimer
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
from src.pii.settings import (
    WHITESPACE_MODES,
    PiiSettings,
    exact_match_pattern,
    literal_to_pattern,
)
from src.pii.text_normalize import normalize_1to1, normalize_pattern
from src.views.page_edit_annotations import CreateMode, _AnnotRef
from src.utils.pdf_utils import (
    MarkupType,
    PdfWritePermissionError,
    ShapeAnnotData,
    ShapeType,
    TextMarkupAnnotData,
    _get_file_cache_token,
    pages_changed_since,
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
    iter_restyle_pii_annots,
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
# 色・透明度などの変更からPDFへの書き込み(restyle)を始めるまでのデバウンス時間。
# ホイール/キー操作の連続commitは、最後の1回分の書き込みに集約される。
PII_RESTYLE_DEBOUNCE_MS = 500
# restyle ジョブ1ティックあたりのUIスレッド占有の上限(ミリ秒)。
PII_RESTYLE_TICK_BUDGET_MS = 25


class PiiDrawerMixin:
    """PageEditWindow に混ぜ込む個人情報検出ドロワー機能。"""

    # PII注釈の色・透明度・表示状態をPDFへ反映する restyle ジョブの状態。
    # 大きいPDFでUIを固めないよう、UIスレッド上でジェネレータを時間分割して進める。
    _pii_restyle_job = None
    _pii_restyle_timer = None  # デバウンス用(single-shot)
    _pii_restyle_tick_timer = None  # ジョブを進めるタイマー(0ms)
    _pii_restyle_old_token = None

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
        self._pii_panel.detect_manual_requested.connect(self._on_pii_detect_manual)
        self._pii_panel.remove_detected_requested.connect(self._on_pii_remove_detected)
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
            panel.set_result_order_mode(settings.result_order_mode)
            panel.set_result_text_display(
                settings.result_text_display_mode, settings.result_text_max_lines
            )
        self._apply_pii_visual_settings()

    def _apply_pii_visual_settings(self) -> None:
        """塗りつぶし候補の色・透明度と、非表示にする種別をズームビューへ反映する。"""
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        zoom_label = getattr(self, "_zoom_label", None)
        if zoom_label is not None:
            zoom_label.set_pii_mask_style(settings.mask_color, settings.mask_opacity)
            zoom_label.set_pii_hidden_entities(settings.hidden_entities())
        # ページ一覧のサムネイルは paint 時に重ね描きするので、画像の再レンダリングは不要。
        # 見た目はウィンドウに保持し(割り当て時にウィジェットへ流し込む)、今割り当て済みの
        # (=表示範囲の)ウィジェットだけ更新する。
        self._pii_mask_style = self._read_pii_mask_style()
        grid = getattr(self, "_grid", None)
        if grid is not None:
            for thumb in grid.bound_widgets():
                thumb.set_pii_mask_style(*self._pii_mask_style)

    def _sync_pii_annot_style_registry(self) -> None:
        """PDFへ書くPII注釈の色・不透明度・非表示種別(他のPDFソフト向け)を、現在の設定に合わせる。

        ユーティリティ層(``pdf_utils.annotations``)の注釈作成関数がこの値を参照する
        (Undo/Redoによる再作成も同じ経路のため、常に最新の設定で書かれる)。
        """
        settings = self._pii_settings()
        set_pii_annot_style(
            settings.mask_color, settings.mask_opacity, settings.hidden_entities()
        )

    # ------------------------------------------------------------------
    # PII注釈スタイルのPDFへの書き込み(デバウンス+時間分割ジョブ)
    # ------------------------------------------------------------------
    # 設定は全ファイル共通、注釈はファイルごとに持つため、設定の確定時と
    # 個人情報検出ドロワーを開いたときにファイル内のPII注釈を現在の色・透明度・
    # チェック状態へ揃える(Undo対象外)。他のPDFソフトで開いたとき、チェック中の
    # 種別は選んだ色・透明度の注釈として表示され、チェックを外した種別は見えなくなる。
    # 書き込めないPDFでは何もせずヒントだけ表示する。
    #
    # 大きいPDFでは数秒かかるため、同期実行せず次の流れで進める:
    #   _schedule_pii_restyle(): 500ms デバウンス(連続操作は最後の1回に集約)
    #   -> _start_pii_restyle_job(): iter_restyle_pii_annots を開く
    #   -> _on_pii_restyle_tick(): 1ティック約25ms の予算で next() を繰り返す
    #   -> StopIteration で増分保存が済み、_on_pii_restyle_finished()。
    # ジョブは実行中ずっとPDFを開いたままなので、**このウィンドウがPDFへ書き込む
    # 操作の前では必ず _abort_pii_restyle_job() / _pii_restyle_paused() を通すこと**
    # (Windows では開いたまま全体保存の置き換えに失敗する)。新しい書き込み経路を
    # 追加するときも同様。中断したジョブは再スケジュールで最新設定から走り直す。

    def _ensure_pii_restyle_timers(self) -> None:
        if self._pii_restyle_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(PII_RESTYLE_DEBOUNCE_MS)
            timer.timeout.connect(self._start_pii_restyle_job)
            self._pii_restyle_timer = timer
        if self._pii_restyle_tick_timer is None:
            tick = QTimer(self)
            tick.setInterval(0)
            tick.timeout.connect(self._on_pii_restyle_tick)
            self._pii_restyle_tick_timer = tick

    def _schedule_pii_restyle(self) -> None:
        """PII注釈のスタイル反映を予約する(実行中なら中断して、最新設定で走り直す)。"""
        self._sync_pii_annot_style_registry()
        self._abort_pii_restyle_job()
        self._ensure_pii_restyle_timers()
        self._pii_restyle_timer.start()

    def _abort_pii_restyle_job(self) -> bool:
        """予約・実行中の restyle を止める(PDFは保存しない)。何かを止めたら True。"""
        active = False
        for timer in (self._pii_restyle_timer, self._pii_restyle_tick_timer):
            if timer is not None and timer.isActive():
                timer.stop()
                active = True
        job = self._pii_restyle_job
        if job is not None:
            self._pii_restyle_job = None
            active = True
            try:
                job.close()  # GeneratorExit: 保存せずPDFを閉じる
            except Exception:  # noqa: BLE001
                logger.debug("PII restyle ジョブの中断に失敗しました", exc_info=True)
        return active

    @contextmanager
    def _pii_restyle_paused(self):
        """PDFへ書き込む処理を囲む。実行中の restyle を止め、終わったら走り直しを予約する。"""
        was_active = self._abort_pii_restyle_job()
        try:
            yield
        finally:
            if was_active:
                self._schedule_pii_restyle()

    def _flush_pii_restyle(self, sync: bool = True) -> None:
        """予約・実行中の restyle があれば同期で最後まで反映する(ウィンドウを閉じる前など)。"""
        if not self._abort_pii_restyle_job() or not sync:
            return
        self._run_pii_restyle_now()

    def _run_pii_restyle_now(self) -> None:
        """PII注釈のスタイル反映を、いま同期で最後まで行う(予約・実行中のジョブは止めてから)。"""
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        try:
            restyle_pii_annots(
                self._pdf_path,
                settings.mask_color,
                settings.mask_opacity,
                settings.hidden_entities(),
            )
        except PdfWritePermissionError:
            logger.warning("PII注釈の色・表示状態を更新できませんでした(書き込み不可): %s", self._pdf_path)
        except Exception:  # noqa: BLE001 - 見た目の同期失敗で閉じる操作を止めない
            logger.warning("PII注釈の色・表示状態の更新に失敗しました", exc_info=True)

    def _start_pii_restyle_job(self) -> None:
        self._abort_pii_restyle_job()
        settings = self._pii_settings()
        self._sync_pii_annot_style_registry()
        self._ensure_pii_restyle_timers()
        self._pii_restyle_old_token = _get_file_cache_token(self._pdf_path)
        self._pii_restyle_job = iter_restyle_pii_annots(
            self._pdf_path,
            settings.mask_color,
            settings.mask_opacity,
            settings.hidden_entities(),
        )
        self._pii_restyle_tick_timer.start()

    def _on_pii_restyle_tick(self) -> None:
        job = self._pii_restyle_job
        if job is None:
            if self._pii_restyle_tick_timer is not None:
                self._pii_restyle_tick_timer.stop()
            return
        deadline = time.monotonic() + PII_RESTYLE_TICK_BUDGET_MS / 1000.0
        changed = 0
        try:
            while True:
                next(job)
                if time.monotonic() >= deadline:
                    return  # 次のティックで続ける(その間にUIイベントを処理する)
        except StopIteration as stop:
            changed = int(stop.value or 0)
        except PdfWritePermissionError:
            logger.warning("PII注釈の色・表示状態を更新できませんでした(書き込み不可): %s", self._pdf_path)
            self._flash_zoom_hint("PDFに書き込めないため、他のPDFソフト向けの注釈の色・表示状態は更新されません")
        except Exception:  # noqa: BLE001 - 見た目の同期失敗で操作を止めない
            logger.warning("PII注釈の色・表示状態の更新に失敗しました", exc_info=True)
        self._pii_restyle_job = None
        self._pii_restyle_tick_timer.stop()
        if changed:
            self._on_pii_restyle_finished(changed)

    def _on_pii_restyle_finished(self, changed: int) -> None:
        """restyle 完了後の後処理。結果一覧の行は変わらないので再スキャンはしない。"""
        # サムネイル用の塗りつぶし対象キャッシュは、保存でファイルのトークンだけが
        # 変わった(中身=位置・種別は同じ)ので、開く前のトークンと一致していれば付け替える。
        cache = getattr(self, "_pii_targets_by_page_cache", None)
        if cache is not None and cache[0] == self._pii_restyle_old_token:
            self._pii_targets_by_page_cache = (_get_file_cache_token(self._pdf_path), cache[1])
        rows_cache = getattr(self, "_pii_result_rows_cache", None)
        if rows_cache is not None and rows_cache[0] == self._pii_restyle_old_token:
            self._pii_result_rows_cache = (_get_file_cache_token(self._pdf_path), rows_cache[1])
        # 画面上のデータ(注釈の色・不透明度)もファイルに合わせてメモリ内で更新する。
        settings = self._pii_settings()
        color = tuple(float(c) for c in settings.mask_color)
        opacity = settings.mask_opacity

        def restyled(annot):
            if isinstance(annot, TextMarkupAnnotData) and annot.pii_entity:
                return dataclass_replace(annot, color=color, opacity=opacity)
            if isinstance(annot, ShapeAnnotData) and annot.pii_entity:
                return dataclass_replace(
                    annot, stroke_color=color, fill_color=color, opacity=opacity
                )
            return annot

        annotations = getattr(self, "_zoom_annotations", None)
        if annotations:
            annotations[:] = [restyled(a) for a in annotations]
        zoom_label = getattr(self, "_zoom_label", None)
        label_annotations = getattr(zoom_label, "_annotations", None)
        if label_annotations and label_annotations is not annotations:
            label_annotations[:] = [restyled(a) for a in label_annotations]
        selected = getattr(self, "_selected_zoom_annotation", None)
        if selected is not None:
            self._selected_zoom_annotation = restyled(selected)
        if zoom_label is not None:
            zoom_label.update()

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
        self._schedule_pii_restyle()
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

    def _on_pii_mask_color_changed(self, color: object) -> None:
        settings = self._pii_settings().copy()
        settings.mask_color = (float(color[0]), float(color[1]), float(color[2]))
        settings.save()
        self._pii_settings_cache = settings
        self._apply_pii_visual_settings()
        self._schedule_pii_restyle()

    def _on_pii_mask_transparency_changed(self, value: int, commit: bool) -> None:
        """透明度スライダ。ドラッグ中は画面へ即時反映するだけで、保存は確定時に行う。"""
        settings = self._pii_settings().copy()
        settings.mask_transparency = int(value)
        if commit:
            settings.save()
        self._pii_settings_cache = settings
        self._apply_pii_visual_settings()
        if commit:
            self._schedule_pii_restyle()

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
            self._schedule_pii_restyle()
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
        if self._zoom_page_num is None or not self._canvas_available():
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
        # 検出ワーカーはファイルを読むので、手動保存モードでは未保存の編集を先に保存する。
        if not self._ensure_saved("個人情報検出"):
            return
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
        # 検出前のOCR結果はメモリ上のドキュメントに埋め込まれているので、確認なしで保存してから読ませる
        # (OCRの前に保存を了承済みの流れの続き)。保存できなければ検出は始めない。
        if not self._ensure_saved("個人情報検出", silent=True):
            if panel is not None:
                panel.set_busy(False)
            return
        if panel is not None:
            panel.set_busy(True, "検出を開始しています...")
        worker = PiiDetectWorker(self._pdf_path, page_indices, self._pii_settings(), parent=self)
        worker.progress.connect(self._on_pii_detect_progress)
        worker.warnings_raised.connect(self._on_pii_detect_warnings)
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

    def _on_pii_detect_warnings(self, messages: list) -> None:
        """検出中に出た注意(辞書のフォールバック等)を、検出完了後にダイアログで知らせる。"""
        text = "\n".join(str(m) for m in messages)
        logger.warning("個人情報検出の注意: %s", text)
        # 結果の反映(finished)より後に表示する(モーダルで結果の反映を止めないため)。
        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "個人情報検出", text))

    def _on_pii_detect_error(self, error: Exception) -> None:
        self._pii_worker = None
        self._update_save_button()
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_busy(False)
        logger.warning("個人情報検出に失敗しました: %s", error)
        QMessageBox.warning(self, "個人情報検出", f"検出に失敗しました。\n\n{error}")

    def _on_pii_detect_finished(
        self, page_indices: list[int], detections: list[PiiDetection]
    ) -> None:
        self._pii_worker = None
        self._update_save_button()
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
        # 選択範囲の行頭・行末の空白/改行(全角スペース含む)は候補に含めない。
        # 含めると語句に改行が混ざって結果一覧の行が縦に伸び、塗りつぶしも空白部分に及ぶ。
        quads = self._zoom_label.selected_glyph_quads()
        if not quads:
            self._flash_zoom_hint("塗りつぶし候補にするテキストを選択してください")
            return
        # OCRで埋め込んだ文字を選択した場合は、自動検出と同じくOCRの位置誤差を見込んだ
        # 余白ぶん広げる(通常のテキストの quad はそのまま)。
        quads = expand_quads_for_ocr(self._pdf_path, self._zoom_page_num, list(quads))
        matched_text = self._zoom_label.selected_glyph_text()
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
            after_create=self._select_created_pii_result,
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
            after_create=self._select_created_pii_result,
        )

    # ------------------------------------------------------------------
    # 結果一覧
    # ------------------------------------------------------------------
    # ページ数がこれを超える差分更新は、ページごとに開き直すより全体走査の方が速い。
    _PII_ROWS_PARTIAL_SCAN_MAX_PAGES = 4

    def _build_pii_result_rows(self) -> list[PiiResultRow]:
        """検出結果一覧に表示する行(塗りつぶし候補+塗りつぶし用図形)を組み立てる。

        ページ別の行をファイルトークン付きでキャッシュし、自分の注釈書き込みで変わった
        ページだけ組み直す(全ページ走査は大きいPDFで0.7秒以上かかるため)。ページ構成の
        変更・外部変更で追跡できないときは全体を走査し直す。表示種別の絞り込みは
        キャッシュ後に行うので、設定変更ではキャッシュを捨てなくてよい。
        """
        settings = self._pii_settings()
        token = _get_file_cache_token(self._pdf_path)
        cache = getattr(self, "_pii_result_rows_cache", None)
        by_page: dict[int, list[PiiResultRow]] | None = None
        if cache is not None:
            if cache[0] == token:
                by_page = cache[1]
            else:
                changed = pages_changed_since(self._pdf_path, cache[0])
                if changed is not None and len(changed) <= self._PII_ROWS_PARTIAL_SCAN_MAX_PAGES:
                    by_page = dict(cache[1])
                    for pn in changed:
                        by_page.pop(pn, None)
                    by_page.update(self._scan_pii_rows_by_page(changed))
        if by_page is None:
            by_page = self._scan_pii_rows_by_page(None)
        self._pii_result_rows_cache = (token, by_page)
        rows = [
            row
            for page_rows in by_page.values()
            for row in page_rows
            if settings.is_entity_visible(row.entity)  # パネルでチェックが外れた種別は一覧に出さない
        ]
        rows.sort(key=lambda r: (r.page_num, r.kind, r.text))
        return rows

    def _scan_pii_rows_by_page(
        self, pages: "frozenset[int] | set[int] | None"
    ) -> dict[int, list[PiiResultRow]]:
        """行(塗りつぶし候補+塗りつぶし用図形)をページ番号ごとに組み立てる(表示種別の絞り込み前)。

        ``pages`` が None なら文書全体、指定があればそのページだけを走査する。
        ``pii_text`` が空(旧バージョンが作成したハイライト、または座標だけ持つ
        塗りつぶし用図形)の場合は、その場でページ文字から抽出してフォールバックする。
        """
        rows: list[PiiResultRow] = []
        chars_cache: dict[int, list[dict]] = {}
        markups: list[TextMarkupAnnotData] = []
        shapes: list[ShapeAnnotData] = []
        if pages is None:
            markups = list_pii_markup_annots(self._pdf_path)
            shapes = list_pii_mask_shapes(self._pdf_path)
        else:
            for pn in sorted(pages):
                markups += list_pii_markup_annots(self._pdf_path, pn)
                shapes += list_pii_mask_shapes(self._pdf_path, pn)

        def chars_for(page_num: int) -> list[dict]:
            cached = chars_cache.get(page_num)
            if cached is None:
                cached = get_page_chars(self._pdf_path, page_num)
                chars_cache[page_num] = cached
            return cached

        for annot in markups:
            text = annot.pii_text
            if not text:
                text = text_under_rect(chars_for(annot.page_num), annot.rect)
            # 並び順用の位置は先頭のquad(quadsは読み順)。無ければ全体の外接矩形。
            bbox = annot.quads[0] if annot.quads else annot.rect
            rows.append(
                PiiResultRow(
                    annot=annot,
                    page_num=annot.page_num,
                    entity=annot.pii_entity,
                    text=text,
                    kind="markup",
                    bbox=tuple(float(v) for v in bbox),
                )
            )

        for shape in shapes:
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
                    bbox=tuple(float(v) for v in shape.rect),
                )
            )

        by_page: dict[int, list[PiiResultRow]] = {}
        for row in rows:
            by_page.setdefault(row.page_num, []).append(row)
        return by_page

    def _reload_pii_results(self) -> None:
        panel = getattr(self, "_pii_panel", None)
        if panel is None or not panel.is_open:
            return
        panel.set_results(self._build_pii_result_rows())

    def _on_pii_result_activated(self, annot: object) -> None:
        if not isinstance(annot, (TextMarkupAnnotData, ShapeAnnotData)):
            return
        self._jump_zoom_to_page(annot.page_num + 1)
        if not self._zoom_view_shown():
            return  # ページ一覧では該当ページのサムネイルを選ぶだけ(注釈の選択はページ画面が要る)
        current = self._find_zoom_annotation(annot.xref) or annot
        self._set_selected_zoom_annotation(current, open_drawer=False)

    def _select_created_pii_result(self, created: object) -> None:
        """手動追加した塗りつぶし対象を、結果一覧でアクティブにしてスクロールする。

        ``_run_zoom_create`` の ``after_create`` から呼ばれる(一覧は作成後の
        ``_refresh_current_zoom_page`` で再構築済み)。``select_result`` は
        ``result_activated`` を発火しないため、ページ側への往復は起きない。
        """
        panel = getattr(self, "_pii_panel", None)
        if panel is None or not panel.is_open:
            return
        panel.select_result(created)

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
        if not self._zoom_view_shown():
            return
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
    # 結果一覧の右クリックメニュー: 除外パターンに追加 / 検出語に追加
    # ------------------------------------------------------------------
    def _ask_pii_exclude_scope(self, text: str) -> str:
        """「除外パターンに追加」の後の範囲選択ダイアログ。``ScopeChoiceDialog.SCOPE_*`` を返す。

        テストからはこのメソッドを差し替えてモーダル表示を避ける。
        """
        return ScopeChoiceDialog.ask(
            "除外パターンに追加",
            "除外パターンに追加しました(今後の検出から除外されます)。\n"
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

    def _ask_pii_manual_detect_scope(self, text: str) -> str:
        """「手動扱いで検出」の範囲選択ダイアログ。``ScopeChoiceDialog.SCOPE_*`` を返す。

        テストからはこのメソッドを差し替えてモーダル表示を避ける。
        """
        return ScopeChoiceDialog.ask(
            "手動扱いで検出",
            "この語句を検出して、手動扱いの塗りつぶし候補に追加しますか?\n"
            "(検出語には登録しません。除外パターンに入っている語句でも検出します)",
            text,
            ("全ページで検出", "このページだけ検出", "検出しない"),
            self,
        )

    def _ask_pii_remove_detected_scope(self, text: str) -> str:
        """「検出結果から削除」の範囲選択ダイアログ。``ScopeChoiceDialog.SCOPE_*`` を返す。

        テストからはこのメソッドを差し替えてモーダル表示を避ける。
        """
        return ScopeChoiceDialog.ask(
            "検出結果から削除",
            "検出済みのこの語句の結果を削除しますか?\n"
            "(除外パターンには登録しません。手動で追加した分は削除しません)",
            text,
            ("全ページの同じ語句を削除", "このページだけ削除", "削除しない"),
            self,
        )

    def _scope_page_num(self) -> "int | None":
        """「このページだけ」の対象ページ。拡大表示では表示中のページ、ページ一覧では選択中の先頭ページ。"""
        if self._zoom_view_shown():
            return self._zoom_page_num
        selected = list(self._selected_pages)
        return min(selected) if selected else None

    def _pii_page_indices_for_scope(self, scope: str) -> list[int] | None:
        """範囲(``ScopeChoiceDialog.SCOPE_*``)を対象ページ番号のリストにする。

        「しない」、対象のページが無い場合は None(何もしない)。
        """
        if scope == ScopeChoiceDialog.SCOPE_PAGE:
            page_num = self._scope_page_num()
            return None if page_num is None else [page_num]
        if scope == ScopeChoiceDialog.SCOPE_ALL:
            page_count = get_page_count(self._pdf_path)
            if page_count <= 0:
                return None
            return list(range(page_count))
        return None

    def _remove_detected_same_text(
        self, text: str, pattern: str, scope: str, description: str
    ) -> None:
        """範囲内で、``pattern``(``^...$`` の完全一致)に合う検出済みの結果をまとめて削除する。

        手動追加分は削除しない。Undo は1回分。``scope`` が「しない」なら何もしない。
        「除外パターンに追加」と「検出結果から削除」で共用する。
        """
        if scope == ScopeChoiceDialog.SCOPE_NONE:
            return
        page_filter: int | None = None
        if scope == ScopeChoiceDialog.SCOPE_PAGE:
            page_filter = self._scope_page_num()
            if page_filter is None:
                return
        # 検出は normalize_1to1 済みテキストに対して行われるため、パターン
        # (空白の扱いによっては ``\\s*`` などを含む)も同じ正規化をかけて完全一致で比べる。
        compiled = re.compile(normalize_pattern(pattern[1:-1]))
        targets = [
            r.annot
            for r in self._build_pii_result_rows()
            if r.entity != MANUAL_ENTITY_TYPE
            and compiled.fullmatch(normalize_1to1(r.text.strip()))
            and (page_filter is None or r.page_num == page_filter)
        ]
        if targets:
            self._remove_mask_targets(targets, description)

    @staticmethod
    def _drop_exclusions_for_text(settings: PiiSettings, text: str) -> bool:
        """``text`` を検出語へ追加するとき、同じ語句の除外設定をすべて取り除く。

        種別別の除外語(``entity_exclusions``。空になったリストは項目ごと消す)・
        除外パターン(``text_exclusions_regex`` の ``re.escape(text)`` および
        ``^re.escape(text)$``)から外す。除外パターンが検出パターンより優先される
        ため、残っていると追加した語句が検出されなくなってしまう。
        何か取り除いたら True。
        """
        removed = False
        target = text.strip()
        # 検出は全角→半角へ1文字ずつ揃えた文字列に対して行われるため、元の表記と
        # 正規化後の表記のどちらで登録されていても同じ語句として扱う。
        variants = {text, target, normalize_1to1(text), normalize_1to1(target)}
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
            # 空白の扱い(4通り)で登録された除外パターンも、同じ語句として外す。
            for mode, _label in WHITESPACE_MODES:
                body = literal_to_pattern(variant, mode)
                drop.update({body, f"^{body}$"})
        kept_regex = [rx for rx in settings.text_exclusions_regex if rx not in drop]
        if len(kept_regex) != len(settings.text_exclusions_regex):
            settings.text_exclusions_regex = kept_regex
            removed = True
        return removed

    def _on_pii_add_exclude_word(self, text: str) -> None:
        """右クリック「除外パターンに追加」。

        語句を完全一致の除外パターン(``^語句$``、記号はエスケープ)に登録し
        (検出パターン=検出語には触れない)、範囲を選ばせて、
        すでに検出済みの同じ語句の結果(手動追加分を除く)をまとめて削除する
        (Undo は1回分)。
        """
        if not text or not text.strip():
            return
        settings = self._pii_settings().copy()
        pattern = exact_match_pattern(text, settings.pattern_whitespace_mode)
        if settings.add_exclusion(pattern):
            settings.save()
            self._pii_settings_cache = settings

        scope = self._ask_pii_exclude_scope(text)
        self._remove_detected_same_text(
            text, pattern, scope, f"除外パターン「{text.strip()}」の検出済みを削除"
        )

    def _on_pii_remove_detected(self, text: str) -> None:
        """右クリック「検出結果から削除(除外に登録しない)」。

        設定(除外パターン)は変えず、範囲を選ばせて、すでに検出済みの同じ語句の結果
        (手動追加分を除く)をまとめて削除する(Undo は1回分)。
        """
        if not text or not text.strip():
            return
        pattern = exact_match_pattern(text, self._pii_settings().pattern_whitespace_mode)
        scope = self._ask_pii_remove_detected_scope(text)
        self._remove_detected_same_text(
            text, pattern, scope, f"検出結果「{text.strip()}」を削除"
        )

    def _on_pii_detect_manual(self, text: str) -> None:
        """右クリック「手動扱いで検出(検出語に登録しない)」。

        設定(検出語・除外パターン)は変えず、範囲を選ばせて、その語句を検出し、
        結果を種別「手動」の塗りつぶし候補として追加する(重複しない新規分だけ)。
        除外設定は無視する(除外に入っている語句でも検出できる)。
        """
        if not text or not text.strip():
            return
        scope = self._ask_pii_manual_detect_scope(text)
        page_indices = self._pii_page_indices_for_scope(scope)
        if page_indices is None:
            return
        pattern = literal_to_pattern(
            normalize_1to1(text), self._pii_settings().pattern_whitespace_mode
        )
        # 手動は自動検出の対象種別ではないため、実在の種別(その他)で検出して手動へ付け替える。
        self._run_pattern_only_detection(
            "OTHER",
            pattern,
            page_indices,
            result_entity=MANUAL_ENTITY_TYPE,
            ignore_exclusions=True,
            description="手動扱いで検出",
        )

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
        settings = self._pii_settings().copy()
        pattern = literal_to_pattern(normalize_1to1(text), settings.pattern_whitespace_mode)
        entry = (entity, pattern)
        added = settings.add_additional_pattern(*entry)
        removed_exclusion = self._drop_exclusions_for_text(settings, text)
        if added or removed_exclusion:
            settings.save()
            self._pii_settings_cache = settings

        scope = self._ask_pii_detect_scope(text, entity)
        page_indices = self._pii_page_indices_for_scope(scope)
        if page_indices is None:
            return
        self._run_pattern_only_detection(entity, pattern, page_indices)

    def _run_pattern_only_detection(
        self,
        entity: str,
        pattern: str,
        page_indices: list[int],
        result_entity: str | None = None,
        ignore_exclusions: bool = False,
        description: str = "検出語で検出",
    ) -> None:
        """追加パターン1件だけを使って対象ページを部分再検出し、結果に追加する。

        他の検出エンジン(正規表現/形態素解析/日時)は無効化し、
        他の種別の検出も行わない(この操作は「登録したパターンで追加検出する」
        ためのものであり、通常の全種別検出をやり直すものではない)。
        除外設定は現在の設定をそのまま引き継ぐ(除外が追加より優先されるため、
        まだ除外に入っている語句は検出されない)。
        ``result_entity`` を指定すると、生成する塗りつぶし候補の種別をそれに差し替える
        (検出そのものは ``entity`` で行う。「手動」は検出対象の種別ではないため)。
        ``ignore_exclusions`` が True なら除外設定を空にして検出する。
        件数が少なく軽量な処理のため、バックグラウンドワーカーは使わず同期実行する。
        """
        current = self._pii_settings()
        enabled_entities = {et: (et == entity) for et in ENTITY_TYPES}
        pattern_settings = PiiSettings(
            enabled_entities=enabled_entities,
            additional_patterns=[(entity, pattern)],
            text_exclusions_regex=[] if ignore_exclusions else list(current.text_exclusions_regex),
            entity_exclusions=(
                {}
                if ignore_exclusions
                else {k: list(v) for k, v in current.entity_exclusions.items()}
            ),
            enabled_engines={key: False for key in ENGINE_KEYS},
            dedupe_enabled=False,
            cross_page_detection=current.cross_page_detection,
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
                pii_entity=result_entity or d.entity_type,
                pii_text=d.text,
            )
            for d in detections
        ]
        new_items = self._filter_new_markup_duplicates(new_items)
        if not new_items:
            QMessageBox.information(self, "個人情報検出", "新しく検出された箇所はありませんでした。")
            return
        self._append_new_pii_markups(new_items, f"{description} ({len(new_items)}件)")

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

        # 黒塗りはファイルを読んで行うので、手動保存モードでは未保存の編集を先に保存する。
        if not self._ensure_saved("エクスポート"):
            return

        # 黒塗り用の読み込み中に restyle ジョブがPDFを開いたままにならないよう止め、
        # 終わったら(止めていたなら)走り直す。
        with self._pii_restyle_paused():
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
