"""Application settings dialog (default library folder)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.utils import app_settings
from src.utils.constants import (
    HEAVY_PDF_FILE_SIZE_MB_RANGE,
    HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE,
    HEAVY_PDF_RENDER_BATCH_SIZE_RANGE,
    HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE,
    PIXMAP_CACHE_MAX_ENTRIES_RANGE,
)
from src.utils.pdf_utils import set_pixmap_cache_max_entries
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

        # ---------------------------------------------------------------
        # 大容量PDF(重量文書)の逐次処理に関する設定
        # ---------------------------------------------------------------
        heavy_note = QLabel(
            "ページ数またはファイルサイズが下記を超えるPDFは、フリーズを防ぐため\n"
            "表示範囲だけを逐次読み込む方式に自動的に切り替わります。"
        )
        heavy_note.setWordWrap(True)
        form.addRow(heavy_note)

        self._page_threshold_spin = QSpinBox()
        self._page_threshold_spin.setRange(*HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE)
        self._page_threshold_spin.setSuffix(" ページ")
        self._page_threshold_spin.setValue(app_settings.heavy_pdf_page_count_threshold())
        form.addRow("重量文書とみなすページ数:", self._page_threshold_spin)

        self._file_size_spin = QSpinBox()
        self._file_size_spin.setRange(*HEAVY_PDF_FILE_SIZE_MB_RANGE)
        self._file_size_spin.setSuffix(" MB")
        self._file_size_spin.setValue(app_settings.heavy_pdf_file_size_mb())
        form.addRow("重量文書とみなすファイルサイズ:", self._file_size_spin)

        self._widget_chunk_spin = QSpinBox()
        self._widget_chunk_spin.setRange(*HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE)
        self._widget_chunk_spin.setSuffix(" ページ/回")
        self._widget_chunk_spin.setValue(app_settings.heavy_pdf_widget_chunk_size())
        form.addRow("サムネイル生成の分割サイズ:", self._widget_chunk_spin)

        self._render_batch_spin = QSpinBox()
        self._render_batch_spin.setRange(*HEAVY_PDF_RENDER_BATCH_SIZE_RANGE)
        self._render_batch_spin.setSuffix(" ページ/回")
        self._render_batch_spin.setValue(app_settings.heavy_pdf_render_batch_size())
        form.addRow("重量文書の描画バッチサイズ:", self._render_batch_spin)

        self._cache_entries_spin = QSpinBox()
        self._cache_entries_spin.setRange(*PIXMAP_CACHE_MAX_ENTRIES_RANGE)
        self._cache_entries_spin.setSuffix(" 件")
        self._cache_entries_spin.setValue(app_settings.pixmap_cache_max_entries())
        form.addRow("ページ画像キャッシュの上限件数:", self._cache_entries_spin)

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

    def save_heavy_pdf_settings(self) -> None:
        """重量文書関連の設定を保存する。

        QSpinBox 自体が range でクランプ済みだが、app_settings 側でも
        二重にクランプする(設定ファイルを直接編集された場合の防御)。
        ページ数/ファイルサイズのしきい値は次に開く文書から、キャッシュ
        上限件数はここで即座に反映される。
        """
        app_settings.set_heavy_pdf_page_count_threshold(self._page_threshold_spin.value())
        app_settings.set_heavy_pdf_file_size_mb(self._file_size_spin.value())
        app_settings.set_heavy_pdf_widget_chunk_size(self._widget_chunk_spin.value())
        app_settings.set_heavy_pdf_render_batch_size(self._render_batch_spin.value())
        app_settings.set_pixmap_cache_max_entries(self._cache_entries_spin.value())
        # キャッシュ上限は実行中のキャッシュへ即時反映する。
        set_pixmap_cache_max_entries(app_settings.pixmap_cache_max_entries())
