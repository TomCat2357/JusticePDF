# src/utils/settings_store.py
"""設定の保存先(アプリルート直下 settings フォルダの INI)を決める。

``QSettings()``(引数なし)の呼び出しが各所に散在しているため、呼び出し側は
変更せず、プロセス既定の形式/パスを INI へ向けることで全箇所を揃える。

``QSettings.setPath(IniFormat, UserScope, dir)`` の保存先は
``<dir>/<組織名>/<アプリ名>.ini`` になる。``<アプリルート>/settings/JusticePDF.ini``
にするため、``dir`` にアプリルート、組織名に ``settings`` を指定する
(``configure()`` が QApplication の組織名をそう設定する)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSettings

logger = logging.getLogger(__name__)

SETTINGS_DIR_NAME = "settings"
APP_NAME = "JusticePDF"
# 旧レジストリ(HKCU\Software\JusticePDF\JusticePDF)の組織名/アプリ名。
LEGACY_ORG_NAME = "JusticePDF"
LEGACY_APP_NAME = "JusticePDF"

# src/ の親ディレクトリ(配布版でも JusticePDF フォルダ直下)をアプリルートとする。
APP_ROOT = Path(__file__).resolve().parents[2]


def settings_dir(app_root: Path | None = None) -> Path:
    return (app_root or APP_ROOT) / SETTINGS_DIR_NAME


def settings_file(app_root: Path | None = None) -> Path:
    return settings_dir(app_root) / f"{APP_NAME}.ini"


def migrate_from_registry(ini_path: Path) -> int:
    """INI が未存在のときだけ、旧レジストリの全キーを INI へコピーする。

    レジストリ側は変更・削除しない。コピーしたキー数を返す(未実施は 0)。
    """
    if ini_path.exists():
        return 0
    # 既定形式が INI になっているため、レジストリは Native を明示して読む。
    legacy = QSettings(
        QSettings.Format.NativeFormat,
        QSettings.Scope.UserScope,
        LEGACY_ORG_NAME,
        LEGACY_APP_NAME,
    )
    keys = legacy.allKeys()
    if not keys:
        return 0
    dest = QSettings(str(ini_path), QSettings.Format.IniFormat)
    for key in keys:
        dest.setValue(key, legacy.value(key))
    dest.sync()
    logger.info("旧レジストリの設定 %d 件を %s へ移行しました", len(keys), ini_path)
    return len(keys)


def configure(app_root: Path | None = None) -> bool:
    """QApplication 生成後、他の QSettings 使用前に呼ぶ。

    成功すると組織名/アプリ名と既定の形式/パスを設定して True を返す。
    フォルダを作れない・書き込めない場合は警告を出し、組織名/アプリ名を従来値
    (レジストリ既定)にして False を返す(起動は継続)。
    """
    from PyQt6.QtCore import QCoreApplication

    root = app_root or APP_ROOT
    folder = settings_dir(root)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        logger.warning("設定フォルダを利用できません(%s)。レジストリに保存します: %s", folder, exc)
        QCoreApplication.setOrganizationName(LEGACY_ORG_NAME)
        QCoreApplication.setApplicationName(LEGACY_APP_NAME)
        return False

    QCoreApplication.setOrganizationName(SETTINGS_DIR_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root))
    try:
        migrate_from_registry(settings_file(root))
    except Exception:  # 移行失敗で起動を止めない
        logger.warning("レジストリ設定の移行に失敗しました", exc_info=True)
    return True
