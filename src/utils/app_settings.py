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

logger = logging.getLogger(__name__)

DEFAULT_FOLDER_KEY = "general/default_folder"


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
