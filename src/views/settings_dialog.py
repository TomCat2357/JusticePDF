"""アプリ設定ダイアログ。

PDFs ライブラリの場所と、ドラッグ＆ドロップで重ねたときのしおり生成を設定する。
設定値は :mod:`src.utils.app_settings`(QSettings ラッパ)に保存する。
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.utils import app_settings


class SettingsDialog(QDialog):
    """PDFs フォルダの場所と重ね時のしおり生成を設定するダイアログ。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setMinimumWidth(520)

        # OK 受理後に呼び出し側へ渡す「解決済みの新しいフォルダ」。未変更なら None。
        self.resolved_pdfs_dir: Path | None = None

        layout = QVBoxLayout(self)

        # --- PDFs フォルダの場所 ---
        layout.addWidget(QLabel("PDFsフォルダの場所"))
        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setText(app_settings.get_pdfs_dir_raw())
        self._path_edit.setPlaceholderText("例: C:\\Users\\you\\Documents\\PDFs または PDFs")
        self._path_edit.setToolTip(
            "PDFを保存・管理するフォルダ。\n"
            "相対パスはホームフォルダ基準で解決します(例:「PDFs」→ ~/PDFs)。\n"
            "存在しないフォルダは自動で作成します。"
        )
        path_row.addWidget(self._path_edit)
        browse_btn = QPushButton("参照…")
        browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        hint = QLabel("相対パスはホームフォルダ基準。存在しない場合は自動作成します。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        # --- 重ね時のしおり生成 ---
        self._bookmark_cb = QCheckBox(
            "ドラッグ＆ドロップで重ねたときにファイル名でしおりを作る"
            "（ファイル内のしおりもぶら下げる）"
        )
        self._bookmark_cb.setChecked(app_settings.get_merge_add_bookmarks())
        self._bookmark_cb.setToolTip(
            "オフのときは重ねてもしおりを作りません(既定)。\n"
            "オンにすると、重ねた各ファイルの先頭にファイル名のしおりを付け、\n"
            "そのファイルが持つしおりはその下にぶら下げます。"
        )
        layout.addWidget(self._bookmark_cb)

        layout.addStretch(1)

        # --- OK / キャンセル ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        current = app_settings.resolve_pdfs_dir(self._path_edit.text().strip() or ".")
        start = str(current) if current.exists() else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "PDFsフォルダを選択", start)
        if chosen:
            self._path_edit.setText(chosen)

    def _on_accept(self) -> None:
        raw = self._path_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "設定", "PDFsフォルダの場所を入力してください。")
            return

        resolved = app_settings.resolve_pdfs_dir(raw)
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "設定",
                f"フォルダを作成できませんでした:\n{resolved}\n\n{exc}",
            )
            return
        if not resolved.is_dir():
            QMessageBox.warning(
                self,
                "設定",
                f"指定の場所はフォルダではありません:\n{resolved}",
            )
            return

        previous = app_settings.get_pdfs_dir()
        app_settings.set_pdfs_dir(raw)
        app_settings.set_merge_add_bookmarks(self._bookmark_cb.isChecked())

        # フォルダが変わったときだけ呼び出し側へ知らせる(即時切り替え用)。
        if resolved.resolve() != previous.resolve():
            self.resolved_pdfs_dir = resolved

        self.accept()
