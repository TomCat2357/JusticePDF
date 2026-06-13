"""Acrobat-style print dialog with a live preview.

The left column exposes printer, copies, page range, fit mode, orientation,
paper size, duplex, color and N-up controls plus a button that opens the OS
print dialog for full driver-specific settings. The right column shows a live
preview of the current sheet that reflects orientation, fit mode and N-up.

Page selection is expressed over the *concatenation* of all pages across the
printed PDFs (1-based), so a multi-PDF print is treated as one running
document. The produced :class:`PrintSettings` / :class:`QPrinter` are consumed
by :func:`src.utils.pdf_utils.print_pdfs`.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QRectF, QSettings
from PyQt6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPixmap
from PyQt6.QtPrintSupport import QPrinter, QPrinterInfo, QPrintDialog
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.utils.pdf_utils import (
    PrintSettings,
    build_flat_index,
    get_page_size_points,
    get_page_thumbnail,
    nup_grid,
    parse_page_range,
)


class PrintDialog(QDialog):
    """Dialog that gathers print options and offers a live preview."""

    _FIT_MODES = [
        ("用紙に合わせる", "fit"),
        ("実際のサイズ (100%)", "actual"),
        ("特大ページを縮小", "shrink"),
        ("カスタム倍率", "custom"),
    ]
    _ORIENTATIONS = [("自動", "auto"), ("縦", "portrait"), ("横", "landscape")]
    _PAGE_SIZES = [
        ("A4", QPageSize.PageSizeId.A4),
        ("A3", QPageSize.PageSizeId.A3),
        ("A5", QPageSize.PageSizeId.A5),
        ("B4 (JIS)", QPageSize.PageSizeId.JisB4),
        ("B5 (JIS)", QPageSize.PageSizeId.JisB5),
        ("Letter", QPageSize.PageSizeId.Letter),
        ("Legal", QPageSize.PageSizeId.Legal),
    ]
    _DUPLEX = [
        ("片面", "none"),
        ("両面（長辺綴じ）", "long"),
        ("両面（短辺綴じ）", "short"),
    ]
    _COLORS = [("カラー", True), ("モノクロ", False)]
    _NUP = [1, 2, 4, 6, 9, 16]

    _DUPLEX_TO_QT = {
        "none": QPrinter.DuplexMode.DuplexNone,
        "long": QPrinter.DuplexMode.DuplexLongSide,
        "short": QPrinter.DuplexMode.DuplexShortSide,
    }
    _QT_TO_DUPLEX = {v: k for k, v in _DUPLEX_TO_QT.items()}

    def __init__(self, pdf_paths, parent: QWidget | None = None, *, current_index: int | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("印刷")
        self.setMinimumWidth(760)

        self._pdf_paths = list(pdf_paths)
        self._flat = build_flat_index(self._pdf_paths)
        self._total = len(self._flat)
        self._current_index = current_index  # 0-based running index, or None
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        self._printers = QPrinterInfo.availablePrinters()
        self._preview_sheet = 0
        self._size_cache: dict[int, tuple[float, float]] = {}

        outer = QVBoxLayout(self)
        cols = QHBoxLayout()
        outer.addLayout(cols)

        cols.addLayout(self._build_form(), 0)
        cols.addLayout(self._build_preview(), 1)

        # Bottom buttons
        btn_box = QDialogButtonBox()
        self._ok_btn = btn_box.addButton("印刷", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)

        self._load_settings()
        self._connect_signals()
        self._sync_printer_name()

        if not self._printers:
            self._ok_btn.setEnabled(False)
            self._os_btn.setEnabled(False)
            self._range_info.setText("利用可能なプリンタがありません。")

        self._update_controls()

    # ------------------------------------------------------------------ build
    def _build_form(self) -> QVBoxLayout:
        left = QVBoxLayout()
        form = QFormLayout()
        left.addLayout(form)

        # Printer
        self._printer_combo = QComboBox()
        for pi in self._printers:
            self._printer_combo.addItem(pi.printerName())
        default_name = QPrinterInfo.defaultPrinter().printerName()
        di = self._printer_combo.findText(default_name)
        if di >= 0:
            self._printer_combo.setCurrentIndex(di)
        form.addRow("プリンタ:", self._printer_combo)

        # Copies + collate
        copies_widget = QWidget()
        cl = QHBoxLayout(copies_widget)
        cl.setContentsMargins(0, 0, 0, 0)
        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 999)
        self._copies_spin.setValue(1)
        self._collate_cb = QCheckBox("部単位で印刷")
        self._collate_cb.setChecked(True)
        cl.addWidget(self._copies_spin)
        cl.addWidget(self._collate_cb)
        cl.addStretch()
        form.addRow("部数:", copies_widget)

        # Page range
        range_widget = QWidget()
        rl = QVBoxLayout(range_widget)
        rl.setContentsMargins(0, 0, 0, 0)
        self._range_all = QRadioButton("すべて")
        self._range_current = QRadioButton("現在のページ")
        self._range_custom = QRadioButton("ページ指定")
        self._range_all.setChecked(True)
        self._range_group = QButtonGroup(self)
        for rb in (self._range_all, self._range_current, self._range_custom):
            self._range_group.addButton(rb)
            rl.addWidget(rb)
        self._range_edit = QLineEdit()
        self._range_edit.setPlaceholderText("例: 1-5, 8, 11-13")
        rl.addWidget(self._range_edit)
        self._range_info = QLabel("")
        rl.addWidget(self._range_info)
        if self._current_index is None:
            self._range_current.setEnabled(False)
        form.addRow("印刷範囲:", range_widget)

        # Fit mode + custom scale
        self._fit_combo = QComboBox()
        self._fit_combo.addItems([lbl for lbl, _ in self._FIT_MODES])
        form.addRow("ページサイズ処理:", self._fit_combo)
        self._scale_label = QLabel("倍率:")
        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(10, 1000)
        self._scale_spin.setValue(100)
        self._scale_spin.setSuffix(" %")
        form.addRow(self._scale_label, self._scale_spin)

        # Orientation
        self._orient_combo = QComboBox()
        self._orient_combo.addItems([lbl for lbl, _ in self._ORIENTATIONS])
        form.addRow("ページの向き:", self._orient_combo)

        # Paper size
        self._paper_combo = QComboBox()
        self._paper_combo.addItems([lbl for lbl, _ in self._PAGE_SIZES])
        form.addRow("用紙サイズ:", self._paper_combo)

        # Duplex
        self._duplex_combo = QComboBox()
        self._duplex_combo.addItems([lbl for lbl, _ in self._DUPLEX])
        form.addRow("両面印刷:", self._duplex_combo)

        # Color
        self._color_combo = QComboBox()
        self._color_combo.addItems([lbl for lbl, _ in self._COLORS])
        form.addRow("カラー:", self._color_combo)

        # N-up
        self._nup_combo = QComboBox()
        for n in self._NUP:
            self._nup_combo.addItem("1ページ/枚" if n == 1 else f"{n}ページ/枚")
        form.addRow("1枚あたり:", self._nup_combo)

        # OS driver settings
        self._os_btn = QPushButton("プリンタの詳細設定（OS）…")
        self._os_btn.clicked.connect(self._on_os_settings)
        left.addWidget(self._os_btn)
        left.addStretch()
        return left

    def _build_preview(self) -> QVBoxLayout:
        right = QVBoxLayout()
        self._preview_label = QLabel()
        self._preview_label.setMinimumSize(300, 380)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet(
            "background:#f4f4f2; border:1px solid #d4d4d2;"
        )
        right.addWidget(self._preview_label, 1)

        nav = QHBoxLayout()
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(44)
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(44)
        self._nav_label = QLabel("")
        self._nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._prev_btn.clicked.connect(lambda: self._step_preview(-1))
        self._next_btn.clicked.connect(lambda: self._step_preview(1))
        nav.addWidget(self._prev_btn)
        nav.addWidget(self._nav_label, 1)
        nav.addWidget(self._next_btn)
        right.addLayout(nav)
        return right

    def _connect_signals(self) -> None:
        self._printer_combo.currentIndexChanged.connect(self._on_printer_changed)
        for rb in (self._range_all, self._range_current, self._range_custom):
            rb.toggled.connect(self._on_setting_changed)
        self._range_edit.textChanged.connect(self._on_setting_changed)
        self._fit_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._scale_spin.valueChanged.connect(self._on_setting_changed)
        self._orient_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._paper_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._color_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._nup_combo.currentIndexChanged.connect(self._on_setting_changed)

    # --------------------------------------------------------------- handlers
    def _on_setting_changed(self, *_args) -> None:
        self._update_controls()
        self._update_preview()

    def _on_printer_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._printers):
            self._printer.setPrinterName(self._printers[idx].printerName())

    def _sync_printer_name(self) -> None:
        idx = self._printer_combo.currentIndex()
        if 0 <= idx < len(self._printers):
            self._printer.setPrinterName(self._printers[idx].printerName())

    def _update_controls(self) -> None:
        is_custom = self._FIT_MODES[self._fit_combo.currentIndex()][1] == "custom"
        self._scale_label.setVisible(is_custom)
        self._scale_spin.setVisible(is_custom)
        self._range_edit.setEnabled(self._range_custom.isChecked())
        self._update_range_info()

    def _update_range_info(self) -> None:
        if not self._printers:
            return
        if self._range_custom.isChecked():
            nums = parse_page_range(self._range_edit.text(), self._total)
            self._range_info.setText(
                f"{len(nums)} ページを印刷します" if nums else "（有効なページがありません）"
            )
        else:
            self._range_info.setText("")

    def _on_os_settings(self) -> None:
        """Open the native print dialog so the user can reach the driver's full
        properties page (paper source, quality, special media, ...)."""
        self.build_printer()  # push current selections onto the printer first
        dlg = QPrintDialog(self._printer, self)
        dlg.setWindowTitle("プリンタの詳細設定")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._read_back_from_printer()

    def _read_back_from_printer(self) -> None:
        p = self._printer
        idx = self._printer_combo.findText(p.printerName())
        if idx >= 0:
            self._printer_combo.setCurrentIndex(idx)
        layout = p.pageLayout()
        self._set_combo_value(
            self._orient_combo, self._ORIENTATIONS,
            "landscape" if layout.orientation() == QPageLayout.Orientation.Landscape else "portrait",
        )
        sid = layout.pageSize().id()
        self._set_combo_value(self._paper_combo, self._PAGE_SIZES, sid)
        self._set_combo_value(self._color_combo, self._COLORS, p.colorMode() == QPrinter.ColorMode.Color)
        self._set_combo_value(self._duplex_combo, self._DUPLEX, self._QT_TO_DUPLEX.get(p.duplex(), "none"))
        self._copies_spin.setValue(max(p.copyCount(), 1))
        self._update_preview()

    def _step_preview(self, delta: int) -> None:
        self._preview_sheet += delta
        self._update_preview()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _set_combo_value(combo: QComboBox, options, value) -> None:
        for i, (_, v) in enumerate(options):
            if v == value:
                combo.setCurrentIndex(i)
                return

    @staticmethod
    def _set_index(combo: QComboBox, idx: int) -> None:
        if 0 <= idx < combo.count():
            combo.setCurrentIndex(idx)

    def _page_pt(self, flat_index: int) -> tuple[float, float]:
        if flat_index not in self._size_cache:
            path, idx = self._flat[flat_index]
            self._size_cache[flat_index] = get_page_size_points(path, idx)
        return self._size_cache[flat_index]

    def _resolve_targets(self) -> list[int]:
        """Return the selected pages as 0-based running indices into ``_flat``."""
        if self._range_current.isChecked() and self._current_index is not None:
            return [self._current_index]
        if self._range_custom.isChecked():
            return [n - 1 for n in parse_page_range(self._range_edit.text(), self._total)]
        return list(range(self._total))

    def _is_landscape(self) -> bool:
        mode = self._ORIENTATIONS[self._orient_combo.currentIndex()][1]
        if mode == "landscape":
            return True
        if mode == "portrait":
            return False
        targets = self._resolve_targets()  # auto
        if not targets:
            return False
        w, h = self._page_pt(targets[0])
        return w > h

    # ------------------------------------------------------------ public API
    def build_printer(self) -> QPrinter:
        """Apply the current selections onto (and return) the held QPrinter.

        Returns the same instance every call so driver settings chosen via the
        OS dialog are preserved. ``"auto"`` orientation is left untouched here
        and resolved per-job by ``print_pdfs``.
        """
        p = self._printer
        name = self._printer_combo.currentText()
        if name:
            p.setPrinterName(name)
        p.setPageSize(QPageSize(self._PAGE_SIZES[self._paper_combo.currentIndex()][1]))
        orient = self._ORIENTATIONS[self._orient_combo.currentIndex()][1]
        if orient == "landscape":
            p.setPageOrientation(QPageLayout.Orientation.Landscape)
        elif orient == "portrait":
            p.setPageOrientation(QPageLayout.Orientation.Portrait)
        p.setColorMode(
            QPrinter.ColorMode.Color
            if self._COLORS[self._color_combo.currentIndex()][1]
            else QPrinter.ColorMode.GrayScale
        )
        p.setDuplex(self._DUPLEX_TO_QT[self._DUPLEX[self._duplex_combo.currentIndex()][1]])
        return p

    def get_settings(self) -> PrintSettings:
        # "すべて" → empty list (print_pdfs treats empty as all pages).
        if self._range_all.isChecked():
            page_numbers: list[int] = []
        else:
            page_numbers = [t + 1 for t in self._resolve_targets()]
        return PrintSettings(
            printer_name=self._printer_combo.currentText(),
            copies=self._copies_spin.value(),
            collate=self._collate_cb.isChecked(),
            page_numbers=page_numbers,
            fit_mode=self._FIT_MODES[self._fit_combo.currentIndex()][1],
            custom_scale_pct=self._scale_spin.value(),
            orientation=self._ORIENTATIONS[self._orient_combo.currentIndex()][1],
            page_size_id=self._PAGE_SIZES[self._paper_combo.currentIndex()][1],
            duplex=self._DUPLEX[self._duplex_combo.currentIndex()][1],
            color=self._COLORS[self._color_combo.currentIndex()][1],
            nup=self._NUP[self._nup_combo.currentIndex()],
        )

    def accept(self) -> None:  # noqa: D102 - Qt override
        if not self._printers:
            return
        if self._range_custom.isChecked():
            if not parse_page_range(self._range_edit.text(), self._total):
                QMessageBox.warning(self, "印刷", "有効なページ範囲を入力してください。")
                return
        self._save_settings()
        super().accept()

    # ----------------------------------------------------------- persistence
    def _save_settings(self) -> None:
        s = QSettings()
        s.setValue("print/printer", self._printer_combo.currentText())
        s.setValue("print/copies", self._copies_spin.value())
        s.setValue("print/collate", self._collate_cb.isChecked())
        s.setValue("print/fit", self._fit_combo.currentIndex())
        s.setValue("print/scale", self._scale_spin.value())
        s.setValue("print/orientation", self._orient_combo.currentIndex())
        s.setValue("print/paper", self._paper_combo.currentIndex())
        s.setValue("print/duplex", self._duplex_combo.currentIndex())
        s.setValue("print/color", self._color_combo.currentIndex())
        s.setValue("print/nup", self._nup_combo.currentIndex())

    def _load_settings(self) -> None:
        s = QSettings()
        name = s.value("print/printer", "", type=str)
        if name:
            i = self._printer_combo.findText(name)
            if i >= 0:
                self._printer_combo.setCurrentIndex(i)
        self._copies_spin.setValue(int(s.value("print/copies", 1, type=int)))
        self._collate_cb.setChecked(bool(s.value("print/collate", True, type=bool)))
        self._set_index(self._fit_combo, int(s.value("print/fit", 0, type=int)))
        self._scale_spin.setValue(int(s.value("print/scale", 100, type=int)))
        self._set_index(self._orient_combo, int(s.value("print/orientation", 0, type=int)))
        self._set_index(self._paper_combo, int(s.value("print/paper", 0, type=int)))
        self._set_index(self._duplex_combo, int(s.value("print/duplex", 0, type=int)))
        self._set_index(self._color_combo, int(s.value("print/color", 0, type=int)))
        self._set_index(self._nup_combo, int(s.value("print/nup", 0, type=int)))
        # Page range always starts at "すべて" (document-specific, not persisted).
        self._range_all.setChecked(True)

    # -------------------------------------------------------------- preview
    def showEvent(self, event) -> None:  # noqa: D102 - Qt override
        super().showEvent(event)
        self._update_preview()

    def resizeEvent(self, event) -> None:  # noqa: D102 - Qt override
        super().resizeEvent(event)
        self._update_preview()

    def _update_preview(self) -> None:
        targets = self._resolve_targets()
        nup = self._NUP[self._nup_combo.currentIndex()]
        total_sheets = math.ceil(len(targets) / nup) if targets else 0

        if total_sheets == 0:
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("印刷するページがありません")
            self._nav_label.setText("0 / 0")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            return

        self._preview_label.setText("")
        self._preview_sheet = max(0, min(self._preview_sheet, total_sheets - 1))
        self._nav_label.setText(f"{self._preview_sheet + 1} / {total_sheets}")
        self._prev_btn.setEnabled(self._preview_sheet > 0)
        self._next_btn.setEnabled(self._preview_sheet < total_sheets - 1)

        fit_mode = self._FIT_MODES[self._fit_combo.currentIndex()][1]
        custom_pct = self._scale_spin.value()
        color = self._COLORS[self._color_combo.currentIndex()][1]
        landscape = self._is_landscape()

        lw = max(self._preview_label.width(), 50)
        lh = max(self._preview_label.height(), 50)
        canvas = QPixmap(lw, lh)
        canvas.fill(Qt.GlobalColor.transparent)

        size_id = self._PAGE_SIZES[self._paper_combo.currentIndex()][1]
        pts = QPageSize(size_id).sizePoints()
        paper_w_pt, paper_h_pt = pts.width(), pts.height()
        if landscape:
            paper_w_pt, paper_h_pt = paper_h_pt, paper_w_pt
        if paper_w_pt <= 0 or paper_h_pt <= 0:
            return

        margin = 12
        k = min((lw - 2 * margin) / paper_w_pt, (lh - 2 * margin) / paper_h_pt)
        paper_w, paper_h = paper_w_pt * k, paper_h_pt * k
        px0, py0 = (lw - paper_w) / 2, (lh - paper_h) / 2
        paper_rect = QRectF(px0, py0, paper_w, paper_h)

        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.fillRect(paper_rect, Qt.GlobalColor.white)
            painter.setPen(QColor("#bdbdbd"))
            painter.drawRect(paper_rect)

            rows, cols = nup_grid(nup, landscape)
            cell_w, cell_h = paper_w / cols, paper_h / rows
            start = self._preview_sheet * nup
            for pos in range(nup):
                ti = start + pos
                if ti >= len(targets):
                    break
                r, c = pos // cols, pos % cols
                cell = QRectF(px0 + c * cell_w, py0 + r * cell_h, cell_w, cell_h)
                self._draw_preview_page(
                    painter, targets[ti], cell, fit_mode, custom_pct, k, color
                )
        finally:
            painter.end()
        self._preview_label.setPixmap(canvas)

    def _draw_preview_page(self, painter, flat_index, cell, fit_mode, custom_pct, k, color) -> None:
        pw_pt, ph_pt = self._page_pt(flat_index)
        if pw_pt <= 0 or ph_pt <= 0:
            return

        if fit_mode == "actual":
            place_w, place_h = pw_pt * k, ph_pt * k
        elif fit_mode == "custom":
            f = max(custom_pct, 1) / 100.0
            place_w, place_h = pw_pt * k * f, ph_pt * k * f
        elif fit_mode == "shrink":
            place_w, place_h = pw_pt * k, ph_pt * k
            if place_w > cell.width() or place_h > cell.height():
                s = min(cell.width() / place_w, cell.height() / place_h)
                place_w, place_h = place_w * s, place_h * s
        else:  # fit
            s = min(cell.width() / pw_pt, cell.height() / ph_pt)
            place_w, place_h = pw_pt * s, ph_pt * s
        if place_w <= 0 or place_h <= 0:
            return

        path, page_index = self._flat[flat_index]
        thumb_size = int(min(max(place_w, place_h, 32), 1000))
        thumb = get_page_thumbnail(path, page_index, size=thumb_size)
        if thumb.isNull():
            return
        if not color:
            thumb = QPixmap.fromImage(
                thumb.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            )
        scaled = thumb.scaled(
            int(round(place_w)),
            int(round(place_h)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        tx = cell.x() + (cell.width() - scaled.width()) / 2
        ty = cell.y() + (cell.height() - scaled.height()) / 2
        painter.save()
        try:
            painter.setClipRect(cell)
            painter.drawPixmap(int(round(tx)), int(round(ty)), scaled)
        finally:
            painter.restore()
