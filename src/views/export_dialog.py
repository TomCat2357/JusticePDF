"""Export options dialog for configuring format, DPI, quality, and compression."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
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
    # The low-compression side is intentionally sparse (only "なし"/"軽量"); the
    # useful high-compression side is finely graded so the 150→60 dpi range is
    # smoothly selectable instead of jumping straight to the strongest preset.
    _PRESETS: dict[int, tuple[int, int]] = {
        2: (150, 55),
        3: (130, 42),
        4: (110, 32),
        5: (95, 24),
        6: (80, 16),
        7: (60, 10),
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
            "高圧縮（画像再圧縮）",
            "強圧縮（画像再圧縮）",
            "中強圧縮（画像再圧縮）",
            "より強い圧縮（画像再圧縮）",
            "かなり強い圧縮（画像再圧縮）",
            "最大圧縮（画像再圧縮 - 低画質）",
            "カスタム（画像再圧縮 - 任意設定）",
        ])
        self._optimize_combo.setCurrentIndex(0)
        self._optimize_label = QLabel("最適化:")
        form.addRow(self._optimize_label, self._optimize_combo)

        # Image DPI for optimization (PDF only, levels 2+)
        self._img_dpi_combo = QComboBox()
        # Includes every DPI used by _PRESETS (60/80/95/110/130/150) so preset
        # selection can seed the exact value, plus rounder values for Custom.
        self._IMG_DPI_OPTIONS = [
            ("60 dpi（最小・低画質）", 60),
            ("72 dpi", 72),
            ("80 dpi", 80),
            ("95 dpi", 95),
            ("100 dpi", 100),
            ("110 dpi", 110),
            ("130 dpi", 130),
            ("150 dpi（標準）", 150),
            ("200 dpi（高画質）", 200),
            ("300 dpi（高画質）", 300),
            ("600 dpi（最高画質）", 600),
        ]
        for label, _ in self._IMG_DPI_OPTIONS:
            self._img_dpi_combo.addItem(label)
        self._img_dpi_combo.setCurrentIndex(self._dpi_option_index(150))  # default: 150 dpi
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

        # Text deletion checkbox (PDF only) — rasterizes to image-only PDF
        self._rasterize_cb = QCheckBox("テキストデータを削除（画像のみ）")
        layout.addWidget(self._rasterize_cb)

        # Rasterize image format (shown only when the checkbox is on)
        self._raster_fmt_widget = QWidget()
        raster_fmt_layout = QHBoxLayout(self._raster_fmt_widget)
        raster_fmt_layout.setContentsMargins(20, 0, 0, 0)
        self._raster_jpeg_radio = QRadioButton("JPEG（高圧縮）")
        self._raster_png_radio = QRadioButton("PNG（可逆・文字くっきり）")
        self._raster_jpeg_radio.setChecked(True)
        self._raster_fmt_group = QButtonGroup(self)
        self._raster_fmt_group.addButton(self._raster_jpeg_radio)
        self._raster_fmt_group.addButton(self._raster_png_radio)
        raster_fmt_layout.addWidget(self._raster_jpeg_radio)
        raster_fmt_layout.addWidget(self._raster_png_radio)
        raster_fmt_layout.addStretch()
        layout.addWidget(self._raster_fmt_widget)

        self._rasterize_cb.toggled.connect(self._update_controls)
        self._raster_jpeg_radio.toggled.connect(self._update_controls)

        # Buttons
        btn_box = QDialogButtonBox()
        self._ok_btn = btn_box.addButton("エクスポート", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # Wire format change
        self._fmt_combo.currentIndexChanged.connect(self._update_controls)

        # Initial sync once every widget exists
        self._update_controls()

    def _update_controls(self, *_args) -> None:
        """Show/hide and enable/disable controls based on format, optimize
        level, and the rasterize (text-deletion) toggle."""
        fmt_text = self._fmt_combo.currentText()
        is_pdf = "*.pdf" in fmt_text
        is_jpeg = "*.jpg" in fmt_text
        is_image = not is_pdf

        # Image-output (PNG/JPEG) controls
        self._dpi_label.setVisible(is_image)
        self._dpi_spin.setVisible(is_image)
        self._quality_label.setVisible(is_jpeg)
        self._quality_widget.setVisible(is_jpeg)

        # PDF-only top-level controls
        self._optimize_label.setVisible(is_pdf)
        self._optimize_combo.setVisible(is_pdf)
        self._rasterize_cb.setVisible(is_pdf)

        rasterize = is_pdf and self._rasterize_cb.isChecked()
        opt_idx = self._optimize_combo.currentIndex()

        # Optimization stays selectable for PDF. Even while rasterizing the
        # level acts as a starting preset for image DPI/quality.
        self._optimize_combo.setEnabled(is_pdf)

        # JPEG/PNG radio appears only while rasterizing.
        self._raster_fmt_widget.setVisible(rasterize)
        raster_jpeg = self._raster_jpeg_radio.isChecked()

        # Image DPI applies to rasterize OR to optimize levels >= 2.
        show_dpi = is_pdf and (rasterize or opt_idx >= 2)
        self._img_dpi_label.setVisible(show_dpi)
        self._img_dpi_combo.setVisible(show_dpi)

        # Image quality applies to (rasterize & JPEG) or optimize levels >= 2.
        show_quality = is_pdf and (
            (rasterize and raster_jpeg) or (not rasterize and opt_idx >= 2)
        )
        self._img_quality_label.setVisible(show_quality)
        self._img_quality_widget.setVisible(show_quality)

        # Presets lock DPI/quality only in the non-rasterize path. While
        # rasterizing the user can always fine-tune them (Custom likewise).
        preset_locked = (not rasterize) and (opt_idx in self._PRESETS)
        self._img_dpi_combo.setEnabled(not preset_locked)
        self._img_quality_slider.setEnabled(not preset_locked)

    def _dpi_option_index(self, dpi: int) -> int:
        """Return the _IMG_DPI_OPTIONS index whose DPI equals *dpi*, falling
        back to the 150 dpi entry (or 0) when not present."""
        for i, (_, d) in enumerate(self._IMG_DPI_OPTIONS):
            if d == dpi:
                return i
        for i, (_, d) in enumerate(self._IMG_DPI_OPTIONS):
            if d == 150:
                return i
        return 0

    def _on_optimize_changed(self, *_args) -> None:
        """Apply the preset DPI/quality for the chosen optimize level, then
        refresh visibility/enabled state. Selecting a preset seeds the image
        DPI/quality values; while rasterizing they remain editable afterward."""
        opt_idx = self._optimize_combo.currentIndex()
        if opt_idx in self._PRESETS:
            dpi, quality = self._PRESETS[opt_idx]
            self._img_dpi_combo.setCurrentIndex(self._dpi_option_index(dpi))
            self._img_quality_slider.setValue(quality)
        self._update_controls()

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
            "rasterize_format": "jpeg" if self._raster_jpeg_radio.isChecked() else "png",
        }
