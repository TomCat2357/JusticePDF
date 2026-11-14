"""「ヘルプ」（操作の一覧）と「このアプリについて」のダイアログ。"""
from __future__ import annotations

import html
from importlib import metadata
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

APP_NAME = "JusticePDF"
LUCIDE_VERSION = "0.294.0"
LUCIDE_LICENSE_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "icons" / "lucide" / "LICENSE"
)

# ローカル版で実際に効く操作だけ（キー, 操作）
KEYBOARD_HELP: list[tuple[str, str]] = [
    ("Ctrl + A", "すべて選択"),
    ("Esc", "名前の絞り込みを閉じる / 選択を解除"),
    ("Delete", "選択した項目をごみ箱へ"),
    ("F2", "ファイル名を変更"),
    ("Shift + F2", "PDF名（タイトル）を変更"),
    ("R", "選んだ PDF を右に90°回転"),
    ("Ctrl + Z / Ctrl + Y", "元に戻す / やり直す"),
    ("Ctrl + E", "エクスポート"),
    ("Ctrl + P", "印刷"),
    ("Ctrl + F", "名前で絞り込み"),
    ("F5", "再読み込み"),
    ("Alt + ←  /  Alt + →", "フォルダの移動を戻る / 進む"),
    ("Alt + ↑", "ひとつ上のフォルダへ"),
    ("Ctrl + ホイール", "カードの大きさを変える"),
]
MOUSE_HELP: list[tuple[str, str]] = [
    ("クリック", "選択"),
    ("Ctrl + クリック", "選択に加える / 外す"),
    ("Shift + クリック", "範囲を選択"),
    ("空白のドラッグ", "範囲選択"),
    ("ダブルクリック", "PDF は個別画面で開く / フォルダはこの画面で開く"),
    ("Ctrl + ダブルクリック", "フォルダを新しいウィンドウで開く"),
    ("マウスのサイドボタン", "フォルダの移動を戻る / 進む"),
    ("上のパンくず", "その場所のフォルダへ移動"),
    ("Alt + ダブルクリック", "PDF を拡大表示で開く"),
    ("カードのドラッグ", "並べ替え"),
    ("カードの中央へドロップ", "結合"),
    ("フォルダのカードへドロップ", "そのフォルダへ移動"),
    ("Ctrl + ドロップ", "移動の代わりにコピー"),
    ("右クリック", "操作のメニュー"),
    ("Shift + 右クリック", "エクスプローラのメニュー"),
]

# (表示名, pip の配布名 or None, ライセンス, URL)
LIBRARIES: list[tuple[str, str | None, str, str]] = [
    ("PyQt6", "PyQt6", "GPL v3", "https://www.riverbankcomputing.com/software/pyqt/"),
    ("PyMuPDF", "PyMuPDF", "AGPL-3.0", "https://github.com/pymupdf/PyMuPDF"),
    ("watchdog", "watchdog", "Apache-2.0", "https://github.com/gorakhargosh/watchdog"),
    ("Send2Trash", "Send2Trash", "BSD-3-Clause", "https://github.com/arsenetar/send2trash"),
    ("pywin32", "pywin32", "PSF", "https://github.com/mhammond/pywin32"),
    ("SudachiPy", "sudachipy", "Apache-2.0", "https://github.com/WorksApplications/SudachiPy"),
    ("SudachiDict", "sudachidict-core", "Apache-2.0", "https://github.com/WorksApplications/SudachiDict"),
    ("phonenumbers", "phonenumbers", "Apache-2.0", "https://github.com/daviddrysdale/python-phonenumbers"),
    ("lxml", "lxml", "BSD-3-Clause", "https://lxml.de/"),
    ("python-pptx", "python-pptx", "MIT", "https://github.com/scanny/python-pptx"),
    ("RapidOCR（任意）", "rapidocr", "Apache-2.0", "https://github.com/RapidAI/RapidOCR"),
    ("lucide（アイコン）", None, "ISC", "https://github.com/lucide-icons/lucide"),
]


def app_version() -> str:
    """アプリの版（インストール情報、無ければ pyproject.toml から）。"""
    try:
        return metadata.version("justicepdf")
    except metadata.PackageNotFoundError:
        pass
    try:
        import tomllib

        root = Path(__file__).resolve().parent.parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except Exception:
        return "不明"


def library_version(dist: str | None) -> str:
    if dist is None:
        return LUCIDE_VERSION
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "-"


def lucide_license_text() -> str:
    try:
        return LUCIDE_LICENSE_PATH.read_text(encoding="utf-8")
    except OSError:
        return "（ライセンス文を読み込めませんでした）"


def _table_html(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<tr><td style='padding:2px 14px 2px 0;white-space:nowrap'><b>{html.escape(k)}</b></td>"
        f"<td style='padding:2px 0'>{html.escape(v)}</td></tr>"
        for k, v in rows
    )
    return f"<h4>{html.escape(title)}</h4><table>{body}</table>"


class HelpDialog(QDialog):
    """キーボードとマウス操作の一覧。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ヘルプ")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        self._browser = QTextBrowser(self)
        self._browser.setHtml(
            _table_html("キーボード", KEYBOARD_HELP) + _table_html("マウス", MOUSE_HELP)
        )
        lay.addWidget(self._browser, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        box.rejected.connect(self.reject)
        box.clicked.connect(lambda _b: self.close())
        lay.addWidget(box)


class AboutDialog(QDialog):
    """アプリ名・版・使っているライブラリとライセンス。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("このアプリについて")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        head = QLabel(f"<h2>{APP_NAME}</h2>バージョン {html.escape(app_version())}", self)
        lay.addWidget(head)

        lay.addWidget(QLabel("使っているライブラリ", self))
        self._table = QTableWidget(len(LIBRARIES), 4, self)
        self._table.setHorizontalHeaderLabels(["名前", "版", "ライセンス", "URL"])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for r, (name, dist, lic, url) in enumerate(LIBRARIES):
            for c, text in enumerate((name, library_version(dist), lic, url)):
                self._table.setItem(r, c, QTableWidgetItem(text))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self._table, 2)

        lay.addWidget(QLabel("lucide のライセンス（ISC）全文", self))
        self._license = QPlainTextEdit(self)
        self._license.setReadOnly(True)
        self._license.setPlainText(lucide_license_text())
        lay.addWidget(self._license, 1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        box.rejected.connect(self.reject)
        box.clicked.connect(lambda _b: self.close())
        lay.addWidget(box)
