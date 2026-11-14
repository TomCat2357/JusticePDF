"""Markdown（.md / .markdown）のプレビュー・編集ウィンドウ。

ライブラリのカードをダブルクリックすると開く。既定は整形表示（プレビュー）で、「編集」で
生のテキストを直せる。［保存］(Ctrl+S) は元の文字コード・改行で書き戻し、［PDFに変換］は
メインウィンドウの変換（``convert_requested``）に任せる（出力は同じフォルダの ``名前.pdf``、
重なれば連番。進捗・取り消しもライブラリ側と同じ）。

プレビューは外部ネットワークへ出ない: 画像はローカルファイルと ``data:`` のみ読み
（``markdown_to_pdf.load_image_resource``）、``http(s)`` の画像は取得しない。リンクは
アプリ内で遷移せず、``http(s)`` / ``mailto`` / ローカルファイルを OS の既定のアプリ
（ブラウザなど）で開く。

保存の文字コード:
  * 読み込み時に判定した文字コード（UTF-8 / UTF-8(BOM付) / CP932）と改行（CRLF / LF / CR）で書く。
    改行が混在していたファイルは、CRLF が 1 つでもあれば CRLF に揃う。
  * CP932 のファイルに CP932 で表せない文字（絵文字など）を書き足したときは確認し、
    「はい」なら UTF-8 で保存してこのウィンドウの文字コードも UTF-8 にする（「いいえ」は保存しない）。
  * どの文字コードでも読めず置換付きで開いた場合は、保存すると文字が失われることを確認してから UTF-8 で保存する。
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QPalette, QTextDocument
from PyQt6.QtWidgets import (
    QMainWindow, QMessageBox, QPlainTextEdit, QStackedWidget, QTextBrowser, QToolBar,
)

from src.utils.markdown_to_pdf import MONO_FAMILIES, base_url_for, jp_font, load_image_resource
from src.utils.text_file import TextFile, encode_text, normalize_newlines, read_text_file, write_bytes_atomic
from src.views import theme
from src.views.ops import make_action
from src.views.theme import COLORS

logger = logging.getLogger(__name__)

_OPEN_SCHEMES = ("http", "https", "mailto")
_ENC_LABEL = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 (BOM付)", "cp932": "Shift_JIS (CP932)"}
_NL_LABEL = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}


def open_link(url: QUrl, base_url: QUrl, browser: QTextBrowser | None = None) -> bool:
    """リンクを OS で開く。ページ内（#見出し）はスクロール。開いたら True。"""
    if not url.scheme() and not url.path() and url.hasFragment():
        if browser is not None:
            browser.scrollToAnchor(url.fragment())
        return True
    resolved = url if url.scheme() else base_url.resolved(url)
    if resolved.scheme().lower() in _OPEN_SCHEMES or resolved.isLocalFile():
        return QDesktopServices.openUrl(resolved)
    return False  # javascript: や未知のスキームは開かない


class SafeMarkdownBrowser(QTextBrowser):
    """リンクをアプリ内で辿らず、画像はローカルと data: だけ読む QTextBrowser。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._on_anchor)

    def loadResource(self, type_, name):  # noqa: N802 (Qt の名前)
        if int(type_) == int(QTextDocument.ResourceType.ImageResource.value):
            return load_image_resource(self.document().baseUrl(), name)
        return None

    def set_markdown_source(self, text: str, base_url: QUrl) -> None:
        self.document().setBaseUrl(base_url)
        self.setSearchPaths([base_url.toLocalFile()])
        bar = self.verticalScrollBar()
        pos = bar.value()
        self.setMarkdown(text)
        bar.setValue(pos)

    def _on_anchor(self, url: QUrl) -> None:
        open_link(url, self.document().baseUrl(), self)


class MarkdownWindow(QMainWindow):
    """Markdown のプレビュー / 編集ウィンドウ。"""

    convert_requested = pyqtSignal(str)  # PDF に変換したいファイルのパス
    closed = pyqtSignal(object)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = os.path.abspath(path)
        self._file = TextFile("", "utf-8", "\n")
        self._preview_stale = True
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(900, 720)

        self._browser = SafeMarkdownBrowser()
        self._editor = QPlainTextEdit()
        self._editor.setTabStopDistance(32)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._browser)
        self._stack.addWidget(self._editor)
        self.setCentralWidget(self._stack)

        bar = QToolBar("Markdown", self)
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        self._edit_action = make_action(self, "edit", self._on_toggle_edit, checkable=True)
        self._save_action = make_action(self, "save", self.save, shortcut="Ctrl+S")
        self._convert_action = make_action(self, "convertPdf", self._on_convert)
        self._app_action = make_action(self, "openApp", self._on_open_app)
        for a in (self._edit_action, self._save_action, self._convert_action, self._app_action):
            bar.addAction(a)

        self._editor.document().modificationChanged.connect(self._on_modified)
        self._editor.textChanged.connect(self._on_text_changed)
        theme.bind(self, MarkdownWindow._apply_theme)
        self.reload_from_disk()

    # ---- 状態 -----------------------------------------------------------
    @property
    def path(self) -> str:
        return self._path

    @property
    def is_dirty(self) -> bool:
        return self._editor.document().isModified()

    @property
    def encoding(self) -> str:
        return self._file.encoding

    def current_text(self) -> str:
        return normalize_newlines(self._editor.toPlainText())

    def is_editing(self) -> bool:
        return self._stack.currentWidget() is self._editor

    def _apply_theme(self) -> None:
        for w in (self._browser, self._editor):
            pal = w.palette()
            # 地と文字の色はアプリの QSS（テーマ）が決める。リンク色だけテーマの色にする
            pal.setColor(QPalette.ColorRole.Link, QColor(COLORS["mode-pages"]))
            w.setPalette(pal)
        self._browser.document().setDefaultFont(jp_font(11.0))
        mono = QFont()
        mono.setFamilies(MONO_FAMILIES + ["monospace"])
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(10.5)
        self._editor.setFont(mono)
        self._preview_stale = True
        if not self.is_editing():
            self._refresh_preview()

    def _update_title(self) -> None:
        mark = "● " if self.is_dirty else ""
        self.setWindowTitle(f"{mark}{os.path.basename(self._path)} - Markdown")
        self._save_action.setEnabled(self.is_dirty)

    def _on_modified(self, _modified: bool) -> None:
        self._update_title()

    def _on_text_changed(self) -> None:
        self._preview_stale = True

    # ---- 読み書き ---------------------------------------------------------
    def reload_from_disk(self) -> None:
        try:
            self._file = read_text_file(self._path)
        except OSError as e:
            QMessageBox.warning(self, "Markdown", f"ファイルを開けませんでした。\n{e}")
            self._file = TextFile("", "utf-8", "\n")
        self._editor.setPlainText(self._file.text)
        self._editor.document().setModified(False)
        self._preview_stale = True
        self._refresh_preview()
        self._show_format()
        self._update_title()

    def _show_format(self) -> None:
        label = _ENC_LABEL.get(self._file.encoding, self._file.encoding)
        self.statusBar().showMessage(f"{label} / {_NL_LABEL[self._file.newline]}")

    def _refresh_preview(self) -> None:
        if not self._preview_stale:
            return
        self._browser.set_markdown_source(self.current_text(), base_url_for(self._path))
        self._preview_stale = False

    def _confirm_encoding(self, text: str) -> str | None:
        """保存に使う文字コードを決める。キャンセル（保存しない）なら None。"""
        enc = self._file.encoding
        Yes, No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        if self._file.lossy:
            ans = QMessageBox.question(
                self, "保存",
                "このファイルは文字コードを判別できず、一部の文字が置き換わって開かれています。\n"
                "このまま UTF-8 で保存すると元の文字は戻りません。保存しますか?",
                Yes | No, No)
            return "utf-8" if ans == Yes else None
        try:
            encode_text(text, enc, self._file.newline)
            return enc
        except UnicodeEncodeError:
            ans = QMessageBox.question(
                self, "保存",
                f"{_ENC_LABEL.get(enc, enc)} で表せない文字が含まれています。\n"
                "UTF-8 で保存しますか?（「いいえ」で保存を中止します）",
                Yes | No, Yes)
            return "utf-8" if ans == Yes else None

    def save(self) -> bool:
        """保存する。保存した（または変更なし）なら True、中止・失敗なら False。"""
        if not self.is_dirty:
            return True
        text = self.current_text()
        enc = self._confirm_encoding(text)
        if enc is None:
            return False
        try:
            write_bytes_atomic(self._path, encode_text(text, enc, self._file.newline))
        except OSError as e:
            QMessageBox.warning(self, "保存", f"保存できませんでした。\n{e}")
            return False
        self._file = TextFile(text, enc, self._file.newline, False)
        self._editor.document().setModified(False)
        self._show_format()
        return True

    # ---- 操作 -----------------------------------------------------------
    def set_edit_mode(self, editing: bool) -> None:
        self._edit_action.setChecked(editing)
        if editing:
            self._stack.setCurrentWidget(self._editor)
            self._editor.setFocus()
        else:
            self._refresh_preview()
            self._stack.setCurrentWidget(self._browser)

    def _on_toggle_edit(self, checked: bool) -> None:
        self.set_edit_mode(bool(checked))

    def _on_convert(self) -> None:
        if self.is_dirty and not self.save():
            return
        self.convert_requested.emit(self._path)

    def _on_open_app(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.is_dirty:
            Save = QMessageBox.StandardButton.Save
            Discard = QMessageBox.StandardButton.Discard
            Cancel = QMessageBox.StandardButton.Cancel
            ans = QMessageBox.question(
                self, "Markdown", "変更が保存されていません。保存しますか?",
                Save | Discard | Cancel, Save)
            if ans == Cancel or (ans == Save and not self.save()):
                event.ignore()
                return
        self.closed.emit(self)
        super().closeEvent(event)
