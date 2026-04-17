"""Export options dialog for configuring format, DPI, quality, and compression."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ExportOptionsDialog(QDialog):
    """Dialog for selecting export format and related options."""

    _FORMATS = ["PDF (*.pdf)", "PNG (*.png)", "JPEG (*.jpg)"]

    # Preset DPI and quality for each optimization level that includes image recompression.
    # {optimize_index: (dpi, quality%)}
    _PRESETS: dict[int, tuple[int, int]] = {
        2: (200, 85),
        3: (150, 50),
        4: (72, 10),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("エクスポート設定")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        layout.addLayout(form)

        # Format combo
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(self._FORMATS)
        form.addRow("出力形式:", self._fmt_combo)

        # DPI (images only)
        self._dpi_spin = QSpinBox()
        self._dpi_spin.setRange(72, 600)
        self._dpi_spin.setValue(150)
        self._dpi_spin.setSuffix(" dpi")
        self._dpi_label = QLabel("DPI:")
        form.addRow(self._dpi_label, self._dpi_spin)

        # JPEG quality (JPEG only)
        self._quality_label = QLabel("品質:")
        quality_widget = QWidget()
        q_layout = QHBoxLayout(quality_widget)
        q_layout.setContentsMargins(0, 0, 0, 0)
        self._quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(85)
        self._quality_value = QLabel("85%")
        self._quality_value.setFixedWidth(40)
        q_layout.addWidget(self._quality_slider)
        q_layout.addWidget(self._quality_value)
        form.addRow(self._quality_label, quality_widget)
        self._quality_widget = quality_widget

        self._quality_slider.valueChanged.connect(
            lambda v: self._quality_value.setText(f"{v}%")
        )

        # PDF optimization level (PDF only)
        self._optimize_combo = QComboBox()
        self._optimize_combo.addItems([
            "なし（そのまま保存）",
            "軽量（不要データ除去のみ）",
            "標準（画像再圧縮 - 高画質）",
            "高圧縮（画像再圧縮）",
            "最大圧縮（画像再圧縮 - 低画質）",
            "カスタム（画像再圧縮 - 任意設定）",
        ])
        self._optimize_combo.setCurrentIndex(0)
        self._optimize_label = QLabel("最適化:")
        form.addRow(self._optimize_label, self._optimize_combo)

        # Image DPI for optimization (PDF only, levels 2+)
        self._img_dpi_combo = QComboBox()
        self._IMG_DPI_OPTIONS = [
            ("72 dpi（最小・低画質）", 72),
            ("100 dpi（小）", 100),
            ("150 dpi（標準）", 150),
            ("200 dpi（やや高画質）", 200),
            ("300 dpi（高画質）", 300),
            ("600 dpi（最高画質）", 600),
        ]
        for label, _ in self._IMG_DPI_OPTIONS:
            self._img_dpi_combo.addItem(label)
        self._img_dpi_combo.setCurrentIndex(2)  # default: 150 dpi
        self._img_dpi_label = QLabel("画像DPI:")
        form.addRow(self._img_dpi_label, self._img_dpi_combo)

        # Image quality for optimization (PDF only, levels 2+)
        self._img_quality_label = QLabel("画像品質:")
        img_q_widget = QWidget()
        img_q_layout = QHBoxLayout(img_q_widget)
        img_q_layout.setContentsMargins(0, 0, 0, 0)
        self._img_quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._img_quality_slider.setRange(10, 100)
        self._img_quality_slider.setValue(75)
        self._img_quality_value = QLabel("75%")
        self._img_quality_value.setFixedWidth(40)
        img_q_layout.addWidget(self._img_quality_slider)
        img_q_layout.addWidget(self._img_quality_value)
        form.addRow(self._img_quality_label, img_q_widget)
        self._img_quality_widget = img_q_widget

        self._img_quality_slider.valueChanged.connect(
            lambda v: self._img_quality_value.setText(f"{v}%")
        )

        self._optimize_combo.currentIndexChanged.connect(self._on_optimize_changed)
        self._on_optimize_changed(0)

        # Text deletion checkbox (PDF only) — rasterizes to image-only PDF
        self._rasterize_cb = QCheckBox("テキストデータを削除（画像のみ）")
        layout.addWidget(self._rasterize_cb)

        # Buttons
        btn_box = QDialogButtonBox()
        self._ok_btn = btn_box.addButton("エクスポート", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # Wire format change
        self._fmt_combo.currentIndexChanged.connect(self._on_format_changed)
        self._on_format_changed(0)

    def _on_optimize_changed(self, index: int) -> None:
        show_img = index >= 2
        self._img_dpi_label.setVisible(show_img)
        self._img_dpi_combo.setVisible(show_img)
        self._img_quality_label.setVisible(show_img)
        self._img_quality_widget.setVisible(show_img)

        if index in self._PRESETS:
            dpi, quality = self._PRESETS[index]
            # Set preset values and disable editing
            dpi_idx = next(
                (i for i, (_, d) in enumerate(self._IMG_DPI_OPTIONS) if d == dpi),
                2,
            )
            self._img_dpi_combo.setCurrentIndex(dpi_idx)
            self._img_quality_slider.setValue(quality)
            self._img_dpi_combo.setEnabled(False)
            self._img_quality_slider.setEnabled(False)
        elif index == 5:  # Custom
            self._img_dpi_combo.setEnabled(True)
            self._img_quality_slider.setEnabled(True)

    def _on_format_changed(self, index: int) -> None:
        fmt_text = self._fmt_combo.currentText()
        is_pdf = "*.pdf" in fmt_text
        is_jpeg = "*.jpg" in fmt_text
        is_image = not is_pdf

        self._dpi_label.setVisible(is_image)
        self._dpi_spin.setVisible(is_image)

        self._quality_label.setVisible(is_jpeg)
        self._quality_widget.setVisible(is_jpeg)

        self._optimize_label.setVisible(is_pdf)
        self._optimize_combo.setVisible(is_pdf)
        opt_idx = self._optimize_combo.currentIndex()
        show_img = is_pdf and opt_idx >= 2
        self._img_dpi_label.setVisible(show_img)
        self._img_dpi_combo.setVisible(show_img)
        self._img_quality_label.setVisible(show_img)
        self._img_quality_widget.setVisible(show_img)

        self._rasterize_cb.setVisible(is_pdf)

    def get_options(self) -> dict:
        fmt_text = self._fmt_combo.currentText()
        if "*.png" in fmt_text:
            fmt = "png"
        elif "*.jpg" in fmt_text:
            fmt = "jpeg"
        else:
            fmt = "pdf"

        return {
            "format": fmt,
            "dpi": self._dpi_spin.value(),
            "jpeg_quality": self._quality_slider.value(),
            "pdf_optimize_level": self._optimize_combo.currentIndex(),
            "pdf_image_dpi": self._IMG_DPI_OPTIONS[self._img_dpi_combo.currentIndex()][1],
            "pdf_image_quality": self._img_quality_slider.value(),
            "rasterize": self._rasterize_cb.isChecked(),
        }
