"""pdf_utils共通基盤: 書き込み権限エラー・保存プリミティブ・ピクスマップキャッシュ。"""
import logging
import os
import shutil
import tempfile
from collections import OrderedDict

import fitz
from PyQt6.QtGui import QPixmap



logger = logging.getLogger(__name__)


class PdfWritePermissionError(PermissionError):
    """Raised when a PDF cannot be overwritten because another app is using it."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        super().__init__(13, f"Permission denied: '{pdf_path}'", pdf_path)




class _PixmapCache:
    def __init__(self, maxsize: int = 256):
        self._maxsize = maxsize
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()

    def get(self, key: tuple) -> QPixmap | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: tuple, pixmap: QPixmap) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = pixmap
            return
        if len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = pixmap

    def clear(self) -> None:
        self._cache.clear()

    def clear_for_path(self, pdf_path: str) -> None:
        keys_to_remove = [k for k in self._cache if k[0] == pdf_path]
        for k in keys_to_remove:
            del self._cache[k]


_pixmap_cache = _PixmapCache(maxsize=256)


def clear_pixmap_cache_for_path(pdf_path: str) -> None:
    _pixmap_cache.clear_for_path(pdf_path)


def clear_pixmap_cache() -> None:
    _pixmap_cache.clear()


def _is_permission_denied_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return True
        if isinstance(current, OSError) and getattr(current, "errno", None) == 13:
            return True
        message = str(current).lower()
        if "permission denied" in message or "access is denied" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _save_document_in_place(
    doc: fitz.Document, pdf_path: str, *, incremental: bool = False
) -> None:
    """Persist a modified document.

    When *incremental* is True, tries ``saveIncr()`` first for speed
    (append-only, no rewrite).  Falls back to full save on failure.
    When False (default), uses full save with garbage collection to
    prevent file growth from repeated annotation edits.
    """
    if incremental:
        try:
            doc.saveIncr()
            _pixmap_cache.clear_for_path(pdf_path)
            return
        except Exception as error:
            if _is_permission_denied_error(error):
                raise PdfWritePermissionError(pdf_path) from error
            # Fall through to full save

    tmp_path: str | None = None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        doc.save(tmp_path, garbage=1, deflate=True)
    except Exception as error:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if _is_permission_denied_error(error):
            raise PdfWritePermissionError(pdf_path) from error
        raise
    try:
        shutil.move(tmp_path, pdf_path)
    except Exception as move_error:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if _is_permission_denied_error(move_error):
            raise PdfWritePermissionError(pdf_path) from move_error
        raise
    _pixmap_cache.clear_for_path(pdf_path)



def _get_file_cache_token(pdf_path: str) -> tuple[int, int, int]:
    """Return a filesystem-based token that changes when the file instance changes."""
    try:
        stat_result = os.stat(pdf_path)
    except OSError:
        return (0, 0, 0)
    return (
        int(getattr(stat_result, "st_mtime_ns", 0)),
        int(stat_result.st_size),
        int(getattr(stat_result, "st_ctime_ns", 0)),
    )

