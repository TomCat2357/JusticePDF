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
import re
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from src.pii.detection_service import PiiDetection, run_detection
from src.pii.entity_types import ENTITY_TYPES, MANUAL_ENTITY_TYPE, get_entity_type_name_ja
from src.pii.engines import ENGINE_KEYS
from src.pii.pdf_text_map import text_under_ellipse, text_under_rect
from src.pii.settings import PiiSettings
from src.views.page_edit_annotations import CreateMode, _AnnotRef
from src.utils.pdf_utils import (
    MarkupType,
    ShapeAnnotData,
    ShapeType,
    TextMarkupAnnotData,
    create_markup_annot,
    create_markup_annots,
    create_shape_annot,
    delete_markup_annot,
    delete_markup_annots,
    delete_shape_annot,
    get_page_chars,
    get_page_count,
    list_pii_mask_shapes,
    list_pii_markup_annots,
    rasterize_pdf,
    redact_pdf_remove_text,
)
from src.views.pii_panel import PiiPanel, PiiResultRow
from src.views.pii_settings_dialog import PiiSettingsDialog
from src.workers.pii_detect_worker import PiiDetectWorker

logger = logging.getLogger(__name__)

# PIIハイライトの既定の透明度。手動マーカーより薄くして、下の文字を
# 読みながら検出範囲を確認しやすくする。実際の見た目(種別色の薄い塗り+濃い枠)は
# page_edit_widgets.ZoomPageWidget._paint_markup_annotation/_paint_shape_annotation
# が pii_entity の有無を見て描き分けるため、この不透明度は主にPDF側の見た目
# (Acrobat等の外部ビューア)に効く。
PII_HIGHLIGHT_OPACITY = 0.35
# 手動で追加する塗りつぶし対象(テキスト候補/図形)の既定エンティティ種別
# (ドロワーのコンボボックスで他の種別に変更できる。既定は「手動」)。
MANUAL_MASK_ENTITY = MANUAL_ENTITY_TYPE
MASK_SHAPE_FILL_COLOR = (0.0, 0.0, 0.0)
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
        self._pii_panel.detect_current_page_requested.connect(self._on_pii_detect_current_page)
        self._pii_panel.detect_all_pages_requested.connect(self._on_pii_detect_all_pages)
        self._pii_panel.keep_existing_toggled.connect(self._on_pii_keep_existing_toggled)
        self._pii_panel.mask_markup_tool_toggled.connect(self._on_pii_mask_markup_toggled)
        self._pii_panel.mask_rect_tool_toggled.connect(self._on_pii_mask_rect_toggled)
        self._pii_panel.mask_ellipse_tool_toggled.connect(self._on_pii_mask_ellipse_toggled)
        self._pii_panel.remove_selected_requested.connect(self._on_pii_remove_selected)
        self._pii_panel.remove_all_requested.connect(self._on_pii_remove_all)
        self._pii_panel.delete_same_text_requested.connect(self._on_pii_delete_same_text)
        self._pii_panel.add_exclusion_requested.connect(self._on_pii_add_exclusion)
        self._pii_panel.add_pattern_requested.connect(self._on_pii_add_pattern_requested)
        self._pii_panel.settings_requested.connect(self._on_pii_settings_requested)
        self._pii_panel.export_rasterize_requested.connect(self._on_pii_export_rasterize_requested)
        self._pii_panel.export_redact_requested.connect(self._on_pii_export_redact_requested)
        self._pii_panel.result_activated.connect(self._on_pii_result_activated)
        self._pii_panel.open_changed.connect(self._on_pii_drawer_open_changed)
        # 開閉トグルはツールバーの「個人情報検出」ボタンへ移設するため内蔵トグルを隠す
        self._pii_panel.use_external_toggle()
        self._pii_panel.set_keep_existing_checked(self._pii_settings().keep_existing_on_detect)
        return self._pii_panel

    def _on_pii_keep_existing_toggled(self, checked: bool) -> None:
        settings = self._pii_settings().copy()
        settings.keep_existing_on_detect = checked
        settings.save()
        self._pii_settings_cache = settings

    def _manual_mask_entity(self) -> str:
        """手動追加ツール(テキスト候補/塗り四角/塗り丸)向けに選択中の種別。"""
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            return panel.selected_manual_entity()
        return MANUAL_MASK_ENTITY

    def _toggle_pii_drawer(self) -> None:
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_open(not panel.is_open)

    def _on_pii_drawer_open_changed(self, is_open: bool) -> None:
        if getattr(self, "_zoom_pii_btn", None) is not None:
            self._zoom_pii_btn.setChecked(is_open)
        if is_open:
            # 横幅を確保するため、他のドロワーは閉じる(付箋/しおりと排他)。
            if self._zoom_annotation_open:
                self._set_zoom_annotation_drawer_open(False)
            bookmarks_panel = getattr(self, "_bookmarks_panel", None)
            if bookmarks_panel is not None and bookmarks_panel.is_open:
                bookmarks_panel.set_open(False)
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
        if getattr(self, "_pii_worker", None) is not None:
            return  # 実行中は多重起動しない
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
        new_items = [
            TextMarkupAnnotData(
                page_num=d.page_num,
                xref=0,
                quads=d.quads,
                markup_type=MarkupType.HIGHLIGHT,
                color=settings.color_for(d.entity_type),
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
        target_pages = set(page_indices)
        old_annots = [
            a for a in list_pii_markup_annots(self._pdf_path) if a.page_num in target_pages
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
        matched_text = self._zoom_label.selected_text()
        settings = self._pii_settings()
        entity_type = self._manual_mask_entity()
        template = TextMarkupAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            quads=tuple(quads),
            markup_type=MarkupType.HIGHLIGHT,
            color=settings.color_for(entity_type),
            opacity=PII_HIGHLIGHT_OPACITY,
            pii_entity=entity_type,
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
        entity_type = self._manual_mask_entity()
        settings = self._pii_settings()
        template = ShapeAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            rect=rect_tuple,
            shape_type=shape_type,
            # 枠線色は選んだ種別の設定色を使う(実際の画面上の見た目は
            # ZoomPageWidget._paint_shape_annotation がこの色を元に「薄い塗り+
            # 濃い枠」へ描き分ける)。塗り色は実PDF注釈用の既定(黒)のまま。
            stroke_color=settings.color_for(entity_type),
            fill_color=MASK_SHAPE_FILL_COLOR,
            stroke_width=MASK_SHAPE_STROKE_WIDTH,
            opacity=MASK_SHAPE_OPACITY,
            pii_entity=entity_type,
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

        def chars_for(page_num: int) -> list[dict]:
            cached = chars_cache.get(page_num)
            if cached is None:
                cached = get_page_chars(self._pdf_path, page_num)
                chars_cache[page_num] = cached
            return cached

        for annot in list_pii_markup_annots(self._pdf_path):
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

    def _on_pii_remove_all(self) -> None:
        all_targets: list[TextMarkupAnnotData | ShapeAnnotData] = [
            *list_pii_markup_annots(self._pdf_path),
            *list_pii_mask_shapes(self._pdf_path),
        ]
        if all_targets:
            self._remove_mask_targets(all_targets, "塗りつぶし対象をすべて削除")

    def _on_pii_delete_same_text(self, text: str) -> None:
        """結果一覧の右クリックメニュー「同じ語句をすべて削除」。"""
        if not text:
            return
        rows = self._build_pii_result_rows()
        targets = [r.annot for r in rows if r.text == text]
        if targets:
            self._remove_mask_targets(targets, f"「{text}」を一括削除")

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
    # 除外語句への登録
    # ------------------------------------------------------------------
    def _on_pii_add_exclusion(self, entity: str, text: str) -> None:
        if not entity or not text:
            return
        settings = self._pii_settings().copy()
        existing = settings.entity_exclusions.setdefault(entity, [])
        if text in existing:
            QMessageBox.information(
                self, "個人情報検出", f"「{text}」は既に除外語句に登録済みです。"
            )
            return
        existing.append(text)
        settings.save()
        self._pii_settings_cache = settings
        QMessageBox.information(
            self,
            "個人情報検出",
            f"「{text}」を除外語句({get_entity_type_name_ja(entity)})に登録しました。\n"
            "次回以降の検出から反映されます。",
        )

    # ------------------------------------------------------------------
    # 追加パターンへの登録(結果一覧の右クリックメニュー)
    # ------------------------------------------------------------------
    def _on_pii_add_pattern_requested(self, entity: str, text: str) -> None:
        """結果一覧の右クリックメニュー「追加パターンに登録」。

        選んだ語句をそのまま(``re.escape`` した)追加検出パターンとして設定に
        登録し、続けて「全ページ/このページだけ/登録のみ」を尋ねて必要なら
        そのパターンだけで部分再検出する(既存の検出結果はそのまま、重複しない
        新規分だけを追加する)。
        """
        if not entity or not text or entity not in ENTITY_TYPES:
            return
        pattern = re.escape(text)
        settings = self._pii_settings().copy()
        entry = (entity, pattern)
        if entry in settings.additional_patterns:
            QMessageBox.information(
                self, "個人情報検出", f"「{text}」は既に追加パターンに登録済みです。"
            )
            return
        settings.additional_patterns.append(entry)
        settings.save()
        self._pii_settings_cache = settings

        box = QMessageBox(self)
        box.setWindowTitle("個人情報検出")
        box.setText(
            f"「{text}」を追加検出パターン({get_entity_type_name_ja(entity)})に"
            "登録しました。\nこのパターンで追加検出しますか?"
        )
        all_btn = box.addButton("全ページで追加検出", QMessageBox.ButtonRole.AcceptRole)
        page_btn = box.addButton("このページだけ追加検出", QMessageBox.ButtonRole.AcceptRole)
        none_btn = box.addButton("追加検出しない(登録のみ)", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(all_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is none_btn or clicked is None:
            return

        if clicked is page_btn:
            if self._zoom_page_num is None:
                return
            page_indices = [self._zoom_page_num]
        else:
            page_count = get_page_count(self._pdf_path)
            if page_count <= 0:
                return
            page_indices = list(range(page_count))
        self._run_pattern_only_detection(entity, pattern, page_indices)

    def _run_pattern_only_detection(
        self, entity: str, pattern: str, page_indices: list[int]
    ) -> None:
        """追加パターン1件だけを使って対象ページを部分再検出し、結果に追加する。

        他の検出エンジン(正規表現/形態素解析/日時/GiNZA/Janome)は無効化し、
        他の種別の検出も行わない(この操作は「登録したパターンで追加検出する」
        ためのものであり、通常の全種別検出をやり直すものではない)。
        件数が少なく軽量な処理のため、バックグラウンドワーカーは使わず同期実行する。
        """
        enabled_entities = {et: (et == entity) for et in ENTITY_TYPES}
        pattern_settings = PiiSettings(
            enabled_entities=enabled_entities,
            additional_patterns=[(entity, pattern)],
            enabled_engines={key: False for key in ENGINE_KEYS},
            dedupe_enabled=False,
        )
        try:
            detections = run_detection(self._pdf_path, page_indices, pattern_settings)
        except Exception as error:  # noqa: BLE001 - ダイアログで詳細を提示する
            logger.warning("追加パターンでの検出に失敗しました: %s", error, exc_info=True)
            QMessageBox.warning(self, "個人情報検出", f"追加検出に失敗しました。\n\n{error}")
            return

        settings = self._pii_settings()
        new_items = [
            TextMarkupAnnotData(
                page_num=d.page_num,
                xref=0,
                quads=d.quads,
                markup_type=MarkupType.HIGHLIGHT,
                color=settings.color_for(d.entity_type),
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
        self._append_new_pii_markups(new_items, f"追加パターンで検出 ({len(new_items)}件)")

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------
    def _on_pii_settings_requested(self) -> None:
        dialog = PiiSettingsDialog(self._pii_settings(), self)
        if dialog.exec():
            new_settings = dialog.result_settings()
            new_settings.save()
            self._pii_settings_cache = new_settings

    # ------------------------------------------------------------------
    # エクスポート: 黒塗りして画像のみ
    # ------------------------------------------------------------------
    def _collect_mask_targets(
        self,
    ) -> tuple[list[TextMarkupAnnotData], list[ShapeAnnotData]]:
        return list_pii_markup_annots(self._pdf_path), list_pii_mask_shapes(self._pdf_path)

    def _on_pii_export_rasterize_requested(self) -> None:
        markup_targets, shape_targets = self._collect_mask_targets()
        if not markup_targets and not shape_targets:
            QMessageBox.information(
                self, "個人情報検出", "黒塗りする塗りつぶし対象がありません。"
            )
            return

        src_path = Path(self._pdf_path)
        default_path = str(src_path.parent / f"{src_path.stem}_黒塗り.pdf")
        output_path, _ = QFileDialog.getSaveFileName(
            self, "黒塗りして画像のみエクスポート", default_path, "PDF Files (*.pdf)"
        )
        if not output_path:
            return

        # 塗りつぶし対象の注釈自体は非表示にし(hide_xrefs)、その位置へ実際の
        # 黒塗り(add_redact_annot は使わない)を描画してからラスタライズする。
        # 塗りつぶし用の楕円は外接矩形ではなく実際の楕円として塗る。
        hide_xrefs: dict[int, list[int]] = {}
        black_fill_regions: dict[int, list[tuple[float, float, float, float]]] = {}
        black_fill_ellipses: dict[int, list[tuple[float, float, float, float]]] = {}
        padding = 1.0
        for annot in markup_targets:
            hide_xrefs.setdefault(annot.page_num, []).append(annot.xref)
            # annot.rect(全quadの外接矩形)ではなく、quadごとに黒塗りする。
            # 複数行にまたがる検出は外接矩形が行間の無関係な文字まで覆ってしまうため。
            for x0, y0, x1, y1 in annot.quads:
                black_fill_regions.setdefault(annot.page_num, []).append(
                    (x0 - padding, y0 - padding, x1 + padding, y1 + padding)
                )
        for shape in shape_targets:
            hide_xrefs.setdefault(shape.page_num, []).append(shape.xref)
            # rotation を末尾に付けて渡す(回転していない図形は0.0で従来通り)。
            # 図形が回転していても外接矩形ではなく実際の輪郭が黒塗りされるよう
            # rasterize_pdf 側で解釈される(shape.rect は回転前の矩形のため)。
            region = (*shape.rect, shape.rotation)
            if shape.shape_type == ShapeType.ELLIPSE:
                black_fill_ellipses.setdefault(shape.page_num, []).append(region)
            else:
                black_fill_regions.setdefault(shape.page_num, []).append(region)

        try:
            rasterize_pdf(
                self._pdf_path,
                output_path,
                dpi=150,
                hide_xrefs=hide_xrefs,
                black_fill_regions=black_fill_regions,
                black_fill_ellipses=black_fill_ellipses,
            )
        except Exception as error:  # noqa: BLE001 - ダイアログで詳細を提示する
            logger.warning("PII黒塗りエクスポートに失敗しました: %s", error, exc_info=True)
            QMessageBox.warning(
                self, "個人情報検出", f"エクスポートに失敗しました。\n\n{error}"
            )
            return

        QMessageBox.information(
            self,
            "個人情報検出",
            f"黒塗り済み・画像のみのPDFを書き出しました。\n\n{output_path}",
        )

    # ------------------------------------------------------------------
    # エクスポート: 文字を削除してテキストPDFとして
    # ------------------------------------------------------------------
    def _on_pii_export_redact_requested(self) -> None:
        markup_targets, shape_targets = self._collect_mask_targets()
        if not markup_targets and not shape_targets:
            QMessageBox.information(
                self, "個人情報検出", "削除する塗りつぶし対象がありません。"
            )
            return

        src_path = Path(self._pdf_path)
        default_path = str(src_path.parent / f"{src_path.stem}_文字削除.pdf")
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "文字を削除してテキストPDFとしてエクスポート",
            default_path,
            "PDF Files (*.pdf)",
        )
        if not output_path:
            return

        remove_xrefs: dict[int, list[int]] = {}
        redact_rects: dict[int, list[tuple[float, float, float, float]]] = {}
        redact_ellipses: dict[int, list[tuple[float, float, float, float]]] = {}
        padding = 1.0
        for annot in markup_targets:
            remove_xrefs.setdefault(annot.page_num, []).append(annot.xref)
            for x0, y0, x1, y1 in annot.quads:
                redact_rects.setdefault(annot.page_num, []).append(
                    (x0 - padding, y0 - padding, x1 + padding, y1 + padding)
                )
        for shape in shape_targets:
            remove_xrefs.setdefault(shape.page_num, []).append(shape.xref)
            # rotation を末尾に付けて渡す(回転していない図形は0.0で従来通り)。
            # 図形が回転していても外接矩形ではなく実際の輪郭がredact/黒塗り
            # されるよう redact_pdf_remove_text 側で解釈される。
            region = (*shape.rect, shape.rotation)
            if shape.shape_type == ShapeType.ELLIPSE:
                redact_ellipses.setdefault(shape.page_num, []).append(region)
            else:
                redact_rects.setdefault(shape.page_num, []).append(region)

        try:
            redact_pdf_remove_text(
                self._pdf_path,
                output_path,
                remove_xrefs=remove_xrefs,
                redact_rects=redact_rects,
                redact_ellipses=redact_ellipses,
            )
        except Exception as error:  # noqa: BLE001 - ダイアログで詳細を提示する
            logger.warning("PII文字削除エクスポートに失敗しました: %s", error, exc_info=True)
            QMessageBox.warning(
                self, "個人情報検出", f"エクスポートに失敗しました。\n\n{error}"
            )
            return

        QMessageBox.information(
            self,
            "個人情報検出",
            f"文字を削除したPDFを書き出しました(元のファイルは変更されていません)。\n\n{output_path}",
        )
