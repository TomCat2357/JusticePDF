"""個人情報(PII)検出をバックグラウンドスレッドで実行するワーカー。

SudachiPy の形態素解析はページ数が多いと数秒かかることがあるため、
``src.workers.file_worker.FileOperationWorker`` と同様に UI スレッドを
ブロックしないよう QThread 上で実行する。進捗表示のため、
汎用の FileOperationWorker とは別に専用クラスとして定義し、ページ処理
1件ごとに ``progress`` シグナルを発火する。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from src.pii.detection_service import PiiDetection, run_detection
from src.pii.settings import PiiSettings

logger = logging.getLogger(__name__)


class PiiDetectWorker(QThread):
    """指定ページ群の個人情報検出をバックグラウンドで実行する。

    Signals:
        progress(done, total): ページ処理が1件完了するたびに発火。
        warnings_raised(list): ユーザーに見せるべき注意(``list[str]``。辞書のフォールバック等)。
            ``finished`` の直前に、注意があるときだけ発火。
        finished(list): 検出結果(``list[PiiDetection]``)を伴って完了時に発火。
        error(Exception): 検出処理全体が失敗した場合に発火。
    """

    progress = pyqtSignal(int, int)
    warnings_raised = pyqtSignal(list)
    finished = pyqtSignal(list)
    error = pyqtSignal(Exception)

    def __init__(
        self,
        pdf_path: str,
        page_indices: list[int],
        settings: PiiSettings,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_indices = list(page_indices)
        self._settings = settings

    def run(self) -> None:
        try:
            warnings: list[str] = []
            results: list[PiiDetection] = run_detection(
                self._pdf_path,
                self._page_indices,
                self._settings,
                progress_callback=lambda done, total: self.progress.emit(done, total),
                warnings=warnings,
            )
            if warnings:
                self.warnings_raised.emit(list(warnings))
            self.finished.emit(results)
        except Exception as exc:
            logger.debug("PiiDetectWorker error", exc_info=True)
            self.error.emit(exc)
