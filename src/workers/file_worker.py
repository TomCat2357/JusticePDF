"""Background worker for file I/O operations."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class FileOperationWorker(QThread):
    """Run a blocking callable on a background thread.

    Signals:
        finished(result): Emitted with the callable's return value on success.
        error(exception): Emitted with the exception on failure.
    """

    finished = pyqtSignal(object)
    error = pyqtSignal(Exception)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        parent: QThread | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as exc:
            logger.debug("FileOperationWorker error", exc_info=True)
            self.error.emit(exc)
