"""Application settings dialog (default library folder)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.utils import app_settings
from src.views.view_helpers import build_accept_cancel_box


class SettingsDialog(QDialog):
    """アプリ全体の設定（デフォルトで開くフォルダ）を編集する小型ダイアログ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        layout.addLayout(form)

        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self._folder_edit = QLineEdit(str(app_settings.library_dir()))
        folder_layout.addWidget(self._folder_edit, 1)
        browse_btn = QPushButton("参照…")
        browse_btn.clicked.connect(self._on_browse)
        folder_layout.addWidget(browse_btn)
        form.addRow("デフォルトで開くフォルダ:", folder_row)

        btn_box, self._ok_btn = build_accept_cancel_box(self, "OK")
        layout.addWidget(btn_box)

    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "デフォルトで開くフォルダを選択", self._folder_edit.text()
        )
        if folder:
            self._folder_edit.setText(folder)

    def accept(self) -> None:  # noqa: D102 - Qt override
        if not self._folder_edit.text().strip():
            QMessageBox.warning(self, "設定", "フォルダを入力してください。")
            return
        super().accept()

    def selected_folder(self) -> str:
        """入力値を絶対パスへ正規化して返す（~展開・相対パスの絶対化を含む）。"""
        path = Path(self._folder_edit.text().strip()).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return str(path)
