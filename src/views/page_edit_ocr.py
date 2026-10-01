"""OCRドロワーのロジック(PageEditWindowのmixin)。

``PiiDrawerMixin``(個人情報検出)と同じ、「ドロワーは表示専用、PDFの読み書き・
Undo登録はmixin側が持つ」という役割分担に従う。認識(RapidOCR、重い処理)は
``src.workers.ocr_worker.OcrWorker`` でバックグラウンド実行し、結果を受け取った
このmixin(メインスレッド)が ``src.ocr.embedder`` でPDFへ埋め込む
(PDFへの書き込みは常にメインスレッドの1か所に集約する)。

埋め込み・削除・それらの取り消しは、すべて「対象ページのOCR注釈を、ある結果で
置き換える」操作(``replace_ocr_in_file``)で表せるため、実行前の内容を控えておき
Undo では元の内容へ置き換える(Undo/Redo に対応)。

テスト用の差し替え点: ``_create_ocr_worker``(ワーカー生成。偽のOCRエンジンを渡せる)、
モジュール属性 ``is_ocr_available``。
"""
from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtWidgets import QMessageBox

from src.ocr import OCR_INSTALL_HINT, is_ocr_available
from src.ocr.base import OCRResult
from src.ocr.embedder import count_ocr_annots, replace_ocr_in_file, snapshot_ocr_results
from src.ocr.pipeline import DEFAULT_OCR_DPI
from src.pii.settings import DEFAULT_OCR_TEXT_COLOR, normalize_ocr_model_tier
from src.utils.pdf_utils import get_page_count
from src.views.ocr_panel import OcrPanel
from src.workers.ocr_worker import OcrWorker

logger = logging.getLogger(__name__)


class OcrDrawerMixin:
    """PageEditWindow に混ぜ込むOCRドロワー機能。"""

    # ------------------------------------------------------------------
    # ドロワーの組み立て・開閉
    # ------------------------------------------------------------------
    def _build_ocr_drawer(self) -> "OcrPanel":
        """OCRドロワーを組み立てる。"""
        self._ocr_panel = OcrPanel()
        self._ocr_worker: "OcrWorker | None" = None
        self._ocr_before_detect_active = False
        self._ocr_panel.ocr_all_requested.connect(self._on_ocr_all_pages)
        self._ocr_panel.ocr_page_requested.connect(self._on_ocr_current_page)
        self._ocr_panel.clear_all_requested.connect(self._on_ocr_clear_all_pages)
        self._ocr_panel.clear_page_requested.connect(self._on_ocr_clear_current_page)
        self._ocr_panel.open_changed.connect(self._on_ocr_drawer_open_changed)
        self._ocr_panel.text_style_changed.connect(self._on_ocr_text_style_changed)
        settings = self._ocr_settings()
        self._ocr_panel.set_text_style(
            settings.ocr_text_visible, settings.ocr_text_color, settings.ocr_text_transparency
        )
        # 開閉トグルはツールバーの「パネル」ドロップダウンへ移設するため内蔵トグルを隠す
        self._ocr_panel.use_external_toggle()
        self._ocr_panel.set_available(is_ocr_available())
        return self._ocr_panel

    def _toggle_ocr_drawer(self) -> None:
        panel = getattr(self, "_ocr_panel", None)
        if panel is not None:
            panel.set_open(not panel.is_open)

    def _on_ocr_drawer_open_changed(self, is_open: bool) -> None:
        if getattr(self, "_zoom_ocr_action", None) is not None:
            self._zoom_ocr_action.setChecked(is_open)
        self._sync_zoom_panel_button()
        if not is_open:
            return
        # 横幅を確保するため、他のドロワーは閉じる(付箋/しおり/個人情報検出と排他)。
        if self._zoom_annotation_open:
            self._set_zoom_annotation_drawer_open(False)
        bookmarks_panel = getattr(self, "_bookmarks_panel", None)
        if bookmarks_panel is not None and bookmarks_panel.is_open:
            bookmarks_panel.set_open(False)
        pii_panel = getattr(self, "_pii_panel", None)
        if pii_panel is not None and pii_panel.is_open:
            pii_panel.set_open(False)
        # 依存の導入状況は起動後に変わり得る(`uv sync --extra ocr` 直後など)ので開くたびに確認する。
        self._ocr_panel.set_available(is_ocr_available())

    # ------------------------------------------------------------------
    # 認識した文字の重ね表示(色・透明度。表示専用でPDFは変えない)
    # ------------------------------------------------------------------
    def _on_ocr_text_style_changed(
        self, visible: bool, color: object, transparency: int, commit: bool
    ) -> None:
        """表示の切替・色・透明度。OCR済みの文字へも再OCRなしで即時に反映する。

        透明度スライダのドラッグ中(commit=False)は画面へ反映するだけで保存しない。
        """
        settings = self._pii_settings().copy()
        settings.ocr_text_visible = bool(visible)
        settings.ocr_text_color = (float(color[0]), float(color[1]), float(color[2]))
        settings.ocr_text_transparency = int(transparency)
        if commit:
            settings.save()
        self._pii_settings_cache = settings
        self._sync_ocr_overlay()

    def _sync_ocr_overlay(self) -> None:
        """現在表示中のページのOCR行を、設定の色・透明度でズームビューへ重ねる。

        表示オフ・複数ページ表示・OCR未実行のときは空にする(何も描かない)。
        """
        zoom_label = getattr(self, "_zoom_label", None)
        if zoom_label is None or not self._zoom_view_shown():
            return  # ページ一覧では重ね表示しない(拡大表示を開くとき描き直す)
        settings = self._ocr_settings()
        lines: list = []
        color = getattr(settings, "ocr_text_color", DEFAULT_OCR_TEXT_COLOR)
        if (
            settings.ocr_text_visible
            and self._zoom_page_num is not None
            and not self._zoom_page_layout_is_multi()
        ):
            try:
                lines = [
                    (r.text, (r.x, r.y, r.x + r.width, r.y + r.height))
                    for r in snapshot_ocr_results(self._pdf_path, [self._zoom_page_num])
                ]
            except Exception:  # noqa: BLE001 - 表示専用。読めなくても画面操作は止めない
                logger.debug("OCR行の読み取りに失敗", exc_info=True)
        zoom_label.set_ocr_overlay(lines, color, settings.ocr_text_opacity)

    # ------------------------------------------------------------------
    # OCR実行
    # ------------------------------------------------------------------
    def _ocr_settings(self):
        """OCR関連の設定(``PiiSettings``: ocr_dpi 等)。"""
        return self._pii_settings()

    def _ocr_dpi(self) -> int:
        try:
            return int(self._ocr_settings().ocr_dpi) or DEFAULT_OCR_DPI
        except (TypeError, ValueError):
            return DEFAULT_OCR_DPI

    def _ocr_tier(self) -> str:
        """設定のOCRモデル(light=軽量 / heavy=高精度)。"""
        return normalize_ocr_model_tier(getattr(self._ocr_settings(), "ocr_model_tier", "light"))

    def _create_ocr_worker(self, page_indices: list[int], *, only_textless: bool) -> "OcrWorker":
        """OCRワーカーを生成する(テストから差し替えて偽のエンジンを渡せる)。"""
        return OcrWorker(
            self._pdf_path,
            page_indices,
            dpi=self._ocr_dpi(),
            tier=self._ocr_tier(),
            only_textless=only_textless,
            parent=self,
        )

    def _ocr_busy(self) -> bool:
        return getattr(self, "_ocr_worker", None) is not None

    def _on_ocr_all_pages(self) -> None:
        page_count = get_page_count(self._pdf_path)
        if page_count > 0:
            self._run_ocr(list(range(page_count)))

    def _on_ocr_current_page(self) -> None:
        if self._zoom_page_num is not None and self._canvas_available():
            self._run_ocr([self._zoom_page_num])

    def _run_ocr(
        self,
        page_indices: list[int],
        *,
        only_textless: bool = False,
        on_done: "Callable[[], None] | None" = None,
        show_errors: bool = True,
    ) -> bool:
        """指定ページをバックグラウンドでOCRし、終わったらPDFへ埋め込む。

        ``on_done`` は埋め込み後(またはエラー/対象なしで中止したとき)に呼ぶ
        コールバック(検出前の自動OCRから、続けて検出を始めるのに使う)。
        OCRを開始できたら True。
        """
        panel = getattr(self, "_ocr_panel", None)
        if self._ocr_busy():
            return False
        if not is_ocr_available():
            if panel is not None:
                panel.set_status(f"OCRを利用できません。{OCR_INSTALL_HINT}")
            return False
        # OCRワーカーはファイルを読んで認識するので、手動保存モードでは未保存の編集を先に保存する。
        if not self._ensure_saved("OCR"):
            return False
        if panel is not None:
            panel.set_busy(True, "OCRを開始しています...(初回はモデルの読み込みに時間がかかります)")
        pii_panel = getattr(self, "_pii_panel", None)
        self._ocr_before_detect_active = on_done is not None
        if on_done is not None and pii_panel is not None:
            pii_panel.set_busy(True, "OCRを開始しています...")

        worker = self._create_ocr_worker(page_indices, only_textless=only_textless)
        worker.progress.connect(self._on_ocr_progress)
        worker.finished.connect(
            lambda results: self._on_ocr_finished(page_indices, results, on_done)
        )
        worker.error.connect(lambda error: self._on_ocr_error(error, on_done, show_errors))
        self._ocr_worker = worker
        worker.start()
        return True

    def _on_ocr_progress(self, done: int, total: int) -> None:
        panel = getattr(self, "_ocr_panel", None)
        if panel is not None:
            panel.set_progress(done, total)
        pii_panel = getattr(self, "_pii_panel", None)
        if pii_panel is not None and self._ocr_before_detect_active:
            pii_panel.set_status(f"OCR中... ({done}/{total})")

    def _finish_ocr_worker(self) -> None:
        worker = getattr(self, "_ocr_worker", None)
        self._ocr_worker = None
        self._ocr_before_detect_active = False
        if worker is not None:
            worker.deleteLater()
        panel = getattr(self, "_ocr_panel", None)
        if panel is not None:
            panel.set_busy(False)
        self._update_save_button()  # ワーカー実行中は無効にしていた保存ボタンを戻す

    def _on_ocr_error(
        self, error: Exception, on_done: "Callable[[], None] | None", show_errors: bool
    ) -> None:
        self._finish_ocr_worker()
        logger.warning("OCRに失敗しました: %s", error)
        panel = getattr(self, "_ocr_panel", None)
        if panel is not None:
            panel.set_status("OCRに失敗しました")
        if show_errors:
            QMessageBox.warning(self, "OCR", f"OCRに失敗しました。\n\n{error}")
        if on_done is not None:
            on_done()

    def _on_ocr_finished(
        self,
        page_indices: list[int],
        results: "dict[int, list[OCRResult]]",
        on_done: "Callable[[], None] | None",
    ) -> None:
        self._finish_ocr_worker()
        panel = getattr(self, "_ocr_panel", None)
        # 文字が1行も認識されなかったページは、既存のOCRを消さず何もしない
        # (再実行で「何も認識できなかった」ときに、以前の結果まで失わないため)。
        pages = sorted(page_num for page_num, page_lines in results.items() if page_lines)
        lines = [line for page_num in pages for line in results[page_num]]
        if not results:
            if panel is not None:
                panel.set_status("OCR対象のページはありませんでした")
        elif not pages:
            if panel is not None:
                panel.set_status("文字を認識できませんでした")
        else:
            applied = self._apply_ocr_lines(pages, lines, f"OCR ({len(lines)}行)")
            if panel is not None and applied:
                panel.set_status(f"OCR完了: {len(pages)}ページ・{len(lines)}行を埋め込みました")
        if on_done is not None:
            on_done()

    # ------------------------------------------------------------------
    # 埋め込み・削除(Undo対応)
    # ------------------------------------------------------------------
    def _apply_ocr_lines(
        self, pages: list[int], lines: "list[OCRResult]", description: str
    ) -> bool:
        """対象ページのOCR注釈を ``lines`` で置き換える(Undo対応)。成功したら True。

        実行前の内容を控えておき、Undo では元の内容へ置き換える。
        """
        before = snapshot_ocr_results(self._pdf_path, pages)

        def do_apply() -> None:
            replace_ocr_in_file(self._pdf_path, pages, lines)
            self._after_ocr_change(pages)

        def undo_apply() -> None:
            replace_ocr_in_file(self._pdf_path, pages, before)
            self._after_ocr_change(pages)

        return self._push_undoable(description, do_apply, undo_apply)

    def _after_ocr_change(self, pages: list[int]) -> None:
        """OCR注釈の増減後に、抽出テキストのキャッシュ・検索結果・画面を更新する。"""
        for page_num in pages:
            self._zoom_text_cache.pop(page_num, None)
        invalidate_search = getattr(self, "_invalidate_search_results", None)
        if invalidate_search is not None:
            invalidate_search()
        self._refresh_current_zoom_page()

    def _clear_ocr(self, pages: list[int], description: str) -> None:
        if not pages:
            return
        if count_ocr_annots(self._pdf_path, pages) == 0:
            panel = getattr(self, "_ocr_panel", None)
            if panel is not None:
                panel.set_status("削除するOCRテキストはありません")
            return
        if self._apply_ocr_lines(pages, [], description):
            panel = getattr(self, "_ocr_panel", None)
            if panel is not None:
                panel.set_status("OCRテキストを削除しました")

    def _on_ocr_clear_all_pages(self) -> None:
        page_count = get_page_count(self._pdf_path)
        if page_count > 0 and not self._ocr_busy():
            self._clear_ocr(list(range(page_count)), "OCRテキストを削除(全ページ)")

    def _on_ocr_clear_current_page(self) -> None:
        if self._zoom_page_num is not None and self._canvas_available() and not self._ocr_busy():
            self._clear_ocr([self._zoom_page_num], "OCRテキストを削除(このページ)")

    # ------------------------------------------------------------------
    # 個人情報検出の前段(設定「テキストレイヤの無いページはOCRしてから検出する」)
    # ------------------------------------------------------------------
    def _start_ocr_before_detect(
        self, page_indices: list[int], on_done: "Callable[[], None]"
    ) -> bool:
        """テキストレイヤの無いページだけをOCRし、埋め込み後に ``on_done``(検出開始)を呼ぶ。

        OCRを開始できたら True(``on_done`` は完了時に呼ばれる)。開始できなければ
        False(呼び出し側がそのまま検出を始める)。エラー時もOCRなしで検出を続ける。
        """
        return self._run_ocr(
            page_indices, only_textless=True, on_done=on_done, show_errors=False
        )
