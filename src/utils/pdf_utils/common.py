"""pdf_utils共通基盤: 書き込み権限エラー・保存プリミティブ・ピクスマップキャッシュ。"""
import logging
import os
import shutil
import tempfile
from collections import OrderedDict

import fitz
from PyQt6.QtGui import QPixmap

from src.utils import app_settings
from src.utils.constants import PIXMAP_CACHE_MAX_ENTRIES



logger = logging.getLogger(__name__)


def is_heavy_pdf(pdf_path: str, page_count: int | None = None) -> bool:
    """ページ数またはファイルサイズが閾値を超える「重量文書」かどうか判定する。

    しきい値は設定ダイアログでユーザーが変更でき(``app_settings``)、
    未設定なら ``src.utils.constants`` の既定値にフォールバックする。
    ``page_count`` が既に分かっていれば再取得せずに使う(呼び出し側で
    ``get_page_count`` 済みのことが多いため、二重に PDF を開かない)。
    """
    if page_count is not None and page_count > app_settings.heavy_pdf_page_count_threshold():
        return True
    try:
        if os.path.getsize(pdf_path) > app_settings.heavy_pdf_file_size_bytes():
            return True
    except OSError:
        pass
    return False


class PdfWritePermissionError(PermissionError):
    """Raised when a PDF cannot be overwritten because another app is using it."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        super().__init__(13, f"Permission denied: '{pdf_path}'", pdf_path)




class _PixmapCache:
    def __init__(self, maxsize: int = 256):
        self._maxsize = max(1, int(maxsize))
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

    def set_maxsize(self, maxsize: int) -> None:
        """上限件数を変更する(設定ダイアログからの即時反映用)。

        縮小した場合は古いエントリから追い出して、すぐに新しい上限へ従う。
        """
        self._maxsize = max(1, int(maxsize))
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)


# 起動時の実際の上限値は、QApplication 生成後に main.py が
# set_pixmap_cache_max_entries(app_settings.pixmap_cache_max_entries()) を
# 呼んで反映する(QSettings は QApplication の組織名/アプリ名設定に依存する
# ため、モジュール読み込み時点ではまだ呼び出せない)。ここでは素の既定値で
# 初期化しておく。
_pixmap_cache = _PixmapCache(maxsize=PIXMAP_CACHE_MAX_ENTRIES)


def clear_pixmap_cache_for_path(pdf_path: str) -> None:
    _pixmap_cache.clear_for_path(pdf_path)


def set_pixmap_cache_max_entries(maxsize: int) -> None:
    """設定で変更されたキャッシュ上限件数を、実行中のキャッシュへ即時反映する。"""
    _pixmap_cache.set_maxsize(maxsize)


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

