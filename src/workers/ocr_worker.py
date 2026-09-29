"""OCR(RapidOCR)をバックグラウンドスレッドで実行するワーカー。

認識は重い(モデルの初回読み込み・1ページ数秒)ため、``PiiDetectWorker`` と同様に
QThread 上で実行し、ページごとに進捗を通知する。ワーカーは認識だけを行い
``{page_num: [OCRResult]}`` を返す。PDFへの埋め込み(書き込み)は結果を受け取った
メインスレッド(``PageEditWindow``)が行う(書き込みを1か所に集約するため)。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from src.ocr.pipeline import DEFAULT_OCR_DPI, run_ocr_pages

logger = logging.getLogger(__name__)


class OcrWorker(QThread):
    """指定ページ群のOCRをバックグラウンドで実行する。

    Signals:
        progress(done, total): ページ処理が1件完了するたびに発火。
        finished(dict): ``{page_num: [OCRResult]}`` を伴って完了時に発火。
        error(Exception): OCR処理全体が失敗した場合に発火。
    """

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(dict)
    error = pyqtSignal(Exception)

    def __init__(
        self,
        pdf_path: str,
        page_indices: list[int],
        *,
        dpi: int = DEFAULT_OCR_DPI,
        tier: str = "light",
        only_textless: bool = False,
        service=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_indices = list(page_indices)
        self._dpi = dpi
        self._tier = tier
        self._only_textless = only_textless
        self._service = service

    def run(self) -> None:
        try:
            results = run_ocr_pages(
                self._pdf_path,
                self._page_indices,
                dpi=self._dpi,
                tier=self._tier,
                only_textless=self._only_textless,
                service=self._service,
                progress_callback=lambda done, total: self.progress.emit(done, total),
            )
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001
            logger.debug("OcrWorker error", exc_info=True)
            self.error.emit(exc)
