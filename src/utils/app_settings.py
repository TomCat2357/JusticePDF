"""アプリ全体の設定(QSettings ラッパ)。

PDFs ライブラリの場所や、ドラッグ＆ドロップで重ねたときのしおり生成方針など、
ウィンドウをまたいで共有する設定を一元管理する。``QSettings`` は ``main.py`` で
``setOrganizationName``/``setApplicationName`` 済みなので、引数なしで生成できる。
"""
from pathlib import Path

from PyQt6.QtCore import QSettings

# QSettings のキー
_KEY_PDFS_DIR = "library/pdfs_dir"
_KEY_MERGE_ADD_BOOKMARKS = "merge/add_file_bookmarks"


def default_pdfs_dir() -> Path:
    """既定の PDFs ライブラリ(``~/Documents/PDFs``)。"""
    return Path.home() / "Documents" / "PDFs"


def resolve_pdfs_dir(value: str) -> Path:
    """設定文字列を実際のパスへ解決する。

    - ``~`` を展開する。
    - 絶対パスはそのまま。
    - 相対パスは **ホームディレクトリ基準** で解決する
      (例: ``"PDFs"`` -> ``~/PDFs``、``"work/案件"`` -> ``~/work/案件``)。
    """
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = Path.home() / p
    return p


def get_pdfs_dir_raw() -> str:
    """保存されている生の設定文字列を返す(未設定なら既定パスの文字列)。"""
    s = QSettings()
    value = s.value(_KEY_PDFS_DIR, "", type=str)
    return value if value else str(default_pdfs_dir())


def get_pdfs_dir() -> Path:
    """PDFs ライブラリの実パスを返す(未設定なら既定)。"""
    s = QSettings()
    value = s.value(_KEY_PDFS_DIR, "", type=str)
    if value:
        return resolve_pdfs_dir(value)
    return default_pdfs_dir()


def set_pdfs_dir(value: str) -> None:
    """PDFs ライブラリの場所を保存する(生文字列のまま=相対指定の可搬性を保つ)。"""
    s = QSettings()
    s.setValue(_KEY_PDFS_DIR, str(value))


def get_merge_add_bookmarks() -> bool:
    """ドラッグ＆ドロップで重ねたときにファイル名のしおりを作るか(既定 False)。

    True のとき、重ねた各ファイルの先頭にファイル名のしおりを付け、そのファイルが
    元々持つしおりは子としてぶら下げる(``merge_pdfs_in_place`` の挙動)。
    """
    s = QSettings()
    return bool(s.value(_KEY_MERGE_ADD_BOOKMARKS, False, type=bool))


def set_merge_add_bookmarks(value: bool) -> None:
    s = QSettings()
    s.setValue(_KEY_MERGE_ADD_BOOKMARKS, bool(value))
