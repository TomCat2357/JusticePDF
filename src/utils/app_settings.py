# src/utils/app_settings.py
"""アプリ全体の設定（QSettings ベース）。

order_store.py はフォルダ単位の並び順専用ストアなのでここでは使わず、
既存の QSettings 系（print_dialog.py, page_edit_annotations.py）に倣い
"<カテゴリ>/<項目>" というキー命名を踏襲する。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSettings

from src.utils.constants import (
    HEAVY_PDF_FILE_SIZE_MB,
    HEAVY_PDF_FILE_SIZE_MB_RANGE,
    HEAVY_PDF_PAGE_COUNT_THRESHOLD,
    HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE,
    HEAVY_PDF_RENDER_BATCH_SIZE,
    HEAVY_PDF_RENDER_BATCH_SIZE_RANGE,
    HEAVY_PDF_WIDGET_CHUNK_SIZE,
    HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE,
    PIXMAP_CACHE_MAX_ENTRIES,
    PIXMAP_CACHE_MAX_ENTRIES_RANGE,
)

logger = logging.getLogger(__name__)

DEFAULT_FOLDER_KEY = "general/default_folder"

# ---------------------------------------------------------------------------
# 大容量PDFの逐次処理(重量文書モード)に関する設定
# ---------------------------------------------------------------------------
HEAVY_PDF_PAGE_COUNT_THRESHOLD_KEY = "heavy_pdf/page_count_threshold"
HEAVY_PDF_FILE_SIZE_MB_KEY = "heavy_pdf/file_size_mb"
HEAVY_PDF_WIDGET_CHUNK_SIZE_KEY = "heavy_pdf/widget_chunk_size"
HEAVY_PDF_RENDER_BATCH_SIZE_KEY = "heavy_pdf/render_batch_size"
PIXMAP_CACHE_MAX_ENTRIES_KEY = "heavy_pdf/pixmap_cache_max_entries"


def _clamp(value: int, value_range: tuple[int, int]) -> int:
    lo, hi = value_range
    return max(lo, min(hi, value))


def _get_int(key: str, default: int, value_range: tuple[int, int]) -> int:
    """QSettings から整数値を取得し、許容範囲へクランプして返す。

    保存値が壊れている(数値に変換できない)場合も既定値にフォールバックする。
    """
    raw = QSettings().value(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("設定値が不正なため既定値を使用します: %s=%r", key, raw)
        value = default
    return _clamp(value, value_range)


def _set_int(key: str, value: int, value_range: tuple[int, int]) -> None:
    QSettings().setValue(key, _clamp(int(value), value_range))


def heavy_pdf_page_count_threshold() -> int:
    """「重量文書」とみなすページ数のしきい値(これを超えたら重量文書)。"""
    return _get_int(
        HEAVY_PDF_PAGE_COUNT_THRESHOLD_KEY,
        HEAVY_PDF_PAGE_COUNT_THRESHOLD,
        HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE,
    )


def set_heavy_pdf_page_count_threshold(value: int) -> None:
    _set_int(HEAVY_PDF_PAGE_COUNT_THRESHOLD_KEY, value, HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE)


def heavy_pdf_file_size_mb() -> int:
    """「重量文書」とみなすファイルサイズのしきい値(MB単位)。"""
    return _get_int(
        HEAVY_PDF_FILE_SIZE_MB_KEY, HEAVY_PDF_FILE_SIZE_MB, HEAVY_PDF_FILE_SIZE_MB_RANGE
    )


def set_heavy_pdf_file_size_mb(value: int) -> None:
    _set_int(HEAVY_PDF_FILE_SIZE_MB_KEY, value, HEAVY_PDF_FILE_SIZE_MB_RANGE)


def heavy_pdf_file_size_bytes() -> int:
    """heavy_pdf_file_size_mb() をバイト単位に換算した値。"""
    return heavy_pdf_file_size_mb() * 1024 * 1024


def heavy_pdf_widget_chunk_size() -> int:
    """重量文書のサムネイルウィジェットを1回のイベントループで生成する件数。"""
    return _get_int(
        HEAVY_PDF_WIDGET_CHUNK_SIZE_KEY,
        HEAVY_PDF_WIDGET_CHUNK_SIZE,
        HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE,
    )


def set_heavy_pdf_widget_chunk_size(value: int) -> None:
    _set_int(HEAVY_PDF_WIDGET_CHUNK_SIZE_KEY, value, HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE)


def heavy_pdf_render_batch_size() -> int:
    """重量文書のサムネイルを1回のタイマー発火で描画する件数。"""
    return _get_int(
        HEAVY_PDF_RENDER_BATCH_SIZE_KEY,
        HEAVY_PDF_RENDER_BATCH_SIZE,
        HEAVY_PDF_RENDER_BATCH_SIZE_RANGE,
    )


def set_heavy_pdf_render_batch_size(value: int) -> None:
    _set_int(HEAVY_PDF_RENDER_BATCH_SIZE_KEY, value, HEAVY_PDF_RENDER_BATCH_SIZE_RANGE)


def pixmap_cache_max_entries() -> int:
    """ページ画像/サムネイルキャッシュ(_PixmapCache)の最大保持件数。"""
    return _get_int(
        PIXMAP_CACHE_MAX_ENTRIES_KEY, PIXMAP_CACHE_MAX_ENTRIES, PIXMAP_CACHE_MAX_ENTRIES_RANGE
    )


def set_pixmap_cache_max_entries(value: int) -> None:
    _set_int(PIXMAP_CACHE_MAX_ENTRIES_KEY, value, PIXMAP_CACHE_MAX_ENTRIES_RANGE)


def default_library_dir() -> Path:
    """設定が無いときの既定ライブラリフォルダ（~/Documents/PDFs）。"""
    return Path.home() / "Documents" / "PDFs"


def library_dir() -> Path:
    """設定されたライブラリフォルダを返す（副作用の無い純粋な getter）。

    未設定、または空文字が保存されている場合は既定値へフォールバックする。
    """
    value = QSettings().value(DEFAULT_FOLDER_KEY, "", type=str)
    return Path(value) if value else default_library_dir()


def ensure_library_dir() -> Path:
    """library_dir() を mkdir して返す。

    設定保存後にリムーバブルドライブが切断された・ネットワーク共有が消えた
    等で作成に失敗した場合は、既定値へフォールバックして mkdir する。
    """
    path = library_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        logger.warning("設定されたフォルダを作成できません: %s。既定値を使用します。", path)
        fallback = default_library_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def set_library_dir(path: str | Path) -> None:
    """ライブラリフォルダを設定へ保存する。"""
    QSettings().setValue(DEFAULT_FOLDER_KEY, str(path))
