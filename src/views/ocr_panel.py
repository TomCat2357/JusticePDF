"""OCRドロワー。

ページ編集画面のズームビュー右側にドロワーとして組み込む独立部品。
``PiiPanel`` / ``BookmarksPanel`` と同じ「表示専用・状態は呼び出し側(page_edit_window)
が持つ」方針に従う。本パネルはPDFの読み書きもOCR実行も行わず、ボタン操作を
シグナルで通知するだけ。実際のOCR実行・埋め込み・削除・Undo登録は
``src.views.page_edit_ocr.OcrDrawerMixin`` が担う。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ocr import OCR_INSTALL_HINT


class OcrPanel(QFrame):
    """OCRドロワー。

    Signals
    -------
    open_changed(bool)
        ドロワーの開閉が切り替わったとき発火。
    ocr_all_requested()
        「全ページをOCR」ボタン押下時。
    ocr_page_requested()
        「このページだけOCR」ボタン押下時。
    clear_all_requested()
        「OCRテキストを削除(全ページ)」ボタン押下時。
    clear_page_requested()
        「OCRテキストを削除(このページ)」ボタン押下時。
    text_style_changed(bool, object, int, bool)
        認識文字の表示設定(表示するか, 色(RGB 0.0-1.0のタプル), 透明度0-100, 確定か)。
        透明度スライダのドラッグ中は確定=False(画面へ即時反映するだけで保存しない)。
    """

    open_changed = pyqtSignal(bool)
    ocr_all_requested = pyqtSignal()
    ocr_page_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    clear_page_requested = pyqtSignal()
    text_style_changed = pyqtSignal(bool, object, int, bool)

    DRAWER_WIDTH = 340

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ocrDrawer")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._is_open = False
        self._collapsed_width = 32
        self._available = True
        self._busy = False
        self._text_color: tuple[float, float, float] = (1.0, 0.0, 0.0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("◀")
        self._toggle_btn.setToolTip("OCR")
        self._toggle_btn.setFixedWidth(32)
        self._toggle_btn.clicked.connect(self.toggle)
        layout.addWidget(self._toggle_btn)

        self._panel = QWidget()
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        panel_layout.addWidget(QLabel("OCR"))

        description = QLabel(
            "スキャン画像など、文字として選択できないページを文字認識(OCR)し、"
            "見えないテキストとして埋め込みます。埋め込んだ文字は、検索・選択・"
            "個人情報検出の対象になります。"
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: palette(dark);")
        panel_layout.addWidget(description)

        # 依存(rapidocr/onnxruntime)が未導入のときの案内。導入済みなら非表示。
        self._hint_label = QLabel(OCR_INSTALL_HINT)
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: #b45309;")
        panel_layout.addWidget(self._hint_label)

        run_group = QGroupBox("文字認識を実行")
        run_layout = QHBoxLayout(run_group)
        self._ocr_all_btn = QPushButton("全ページをOCR")
        self._ocr_all_btn.setToolTip("すべてのページを文字認識して、見えないテキストとして埋め込みます。")
        self._ocr_all_btn.clicked.connect(self.ocr_all_requested.emit)
        run_layout.addWidget(self._ocr_all_btn)
        self._ocr_page_btn = QPushButton("このページだけOCR")
        self._ocr_page_btn.setToolTip("表示中のページだけを文字認識します。")
        self._ocr_page_btn.clicked.connect(self.ocr_page_requested.emit)
        run_layout.addWidget(self._ocr_page_btn)
        panel_layout.addWidget(run_group)

        clear_group = QGroupBox("OCRテキストを削除")
        clear_layout = QHBoxLayout(clear_group)
        self._clear_all_btn = QPushButton("全ページ")
        self._clear_all_btn.setToolTip("すべてのページの、埋め込んだOCRテキストを削除します。")
        self._clear_all_btn.clicked.connect(self.clear_all_requested.emit)
        clear_layout.addWidget(self._clear_all_btn)
        self._clear_page_btn = QPushButton("このページ")
        self._clear_page_btn.setToolTip("表示中のページの、埋め込んだOCRテキストを削除します。")
        self._clear_page_btn.clicked.connect(self.clear_page_requested.emit)
        clear_layout.addWidget(self._clear_page_btn)
        panel_layout.addWidget(clear_group)

        # 認識した文字を画面に重ねて表示する(表示専用。PDFの埋め込みは変えない)。
        # 色・透明度は、OCR済みの文字にも再OCRなしでいつでも反映される。
        text_group = QGroupBox("認識した文字の表示")
        text_layout = QVBoxLayout(text_group)
        self._text_visible_check = QCheckBox("認識した文字を画面に重ねて表示")
        self._text_visible_check.setToolTip(
            "埋め込んだOCRテキストを、認識した行の位置に重ねて表示します。\n"
            "表示だけの設定で、PDFの内容は変わりません。"
        )
        self._text_visible_check.toggled.connect(self._emit_text_style)
        text_layout.addWidget(self._text_visible_check)
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("色:"))
        self._text_color_btn = QPushButton()
        self._text_color_btn.setFixedWidth(48)
        self._text_color_btn.setToolTip("認識した文字の色を選びます。")
        self._text_color_btn.clicked.connect(self._on_text_color_clicked)
        style_row.addWidget(self._text_color_btn)
        style_row.addWidget(QLabel("透明度:"))
        self._text_transparency_slider = QSlider(Qt.Orientation.Horizontal)
        self._text_transparency_slider.setRange(0, 100)
        self._text_transparency_slider.setValue(50)
        self._text_transparency_slider.setToolTip("0%で不透明、100%で完全に透明です。")
        self._text_transparency_slider.valueChanged.connect(self._on_text_transparency_value)
        self._text_transparency_slider.sliderReleased.connect(
            lambda: self._emit_text_style(commit=True)
        )
        style_row.addWidget(self._text_transparency_slider, 1)
        self._text_transparency_label = QLabel("50%")
        self._text_transparency_label.setMinimumWidth(36)
        style_row.addWidget(self._text_transparency_label)
        text_layout.addLayout(style_row)
        panel_layout.addWidget(text_group)
        self._refresh_text_color_button()

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        panel_layout.addWidget(self._progress_bar)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: palette(dark);")
        panel_layout.addWidget(self._status_label)

        panel_layout.addStretch()
        layout.addWidget(self._panel)

        self.set_open(False)
        self.set_available(True)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._is_open

    def set_open(self, is_open: bool) -> None:
        is_open = bool(is_open)
        changed = is_open != self._is_open
        self._is_open = is_open
        self._panel.setVisible(is_open)
        self.setFixedWidth(self.DRAWER_WIDTH if is_open else self._collapsed_width)
        self._toggle_btn.setText("▶" if is_open else "◀")
        if changed:
            self.open_changed.emit(is_open)

    def toggle(self) -> None:
        self.set_open(not self._is_open)

    def use_external_toggle(self) -> None:
        """開閉トグルを外部ボタン(パネルのドロップダウン)に委譲する(他のドロワーと同じ規約)。"""
        self._collapsed_width = 0
        self._toggle_btn.hide()
        if not self._is_open:
            self.setFixedWidth(0)

    # 認識文字の表示設定 ---------------------------------------------------
    def set_text_style(
        self, visible: bool, color: "tuple[float, float, float]", transparency: int
    ) -> None:
        """設定値をUIへ反映する(シグナルは出さない)。"""
        self._text_color = (float(color[0]), float(color[1]), float(color[2]))
        widgets = (self._text_visible_check, self._text_transparency_slider)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._text_visible_check.setChecked(bool(visible))
            self._text_transparency_slider.setValue(max(0, min(100, int(transparency))))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._text_transparency_label.setText(f"{self._text_transparency_slider.value()}%")
        self._refresh_text_color_button()

    def text_style(self) -> "tuple[bool, tuple[float, float, float], int]":
        return (
            self._text_visible_check.isChecked(),
            self._text_color,
            int(self._text_transparency_slider.value()),
        )

    def _refresh_text_color_button(self) -> None:
        r, g, b = (max(0, min(255, round(c * 255))) for c in self._text_color)
        self._text_color_btn.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b}); border: 1px solid palette(mid);"
        )

    def _emit_text_style(self, *_args, commit: bool = True) -> None:
        visible, color, transparency = self.text_style()
        self.text_style_changed.emit(visible, color, transparency, bool(commit))

    def _on_text_color_clicked(self) -> None:
        r, g, b = (max(0, min(255, round(c * 255))) for c in self._text_color)
        chosen = QColorDialog.getColor(QColor(r, g, b), self, "認識した文字の色")
        if not chosen.isValid():
            return
        self._text_color = (chosen.redF(), chosen.greenF(), chosen.blueF())
        self._refresh_text_color_button()
        self._emit_text_style()

    def _on_text_transparency_value(self, value: int) -> None:
        self._text_transparency_label.setText(f"{value}%")
        # ドラッグ中は画面へ即時反映のみ。キーボード/クリックでの変更は即確定。
        self._emit_text_style(commit=not self._text_transparency_slider.isSliderDown())

    def set_available(self, available: bool) -> None:
        """OCRの依存が導入済みか。未導入なら実行ボタンを無効にして案内を出す。"""
        self._available = bool(available)
        self._hint_label.setVisible(not self._available)
        self._update_button_states()

    def is_available(self) -> bool:
        return self._available

    def set_busy(self, busy: bool, status: str = "") -> None:
        """OCR実行中はボタンを無効化し、進捗表示を出す。"""
        self._busy = bool(busy)
        self._progress_bar.setVisible(self._busy)
        if self._busy:
            self._progress_bar.setRange(0, 0)  # ページ数が分かるまでは不定進捗
        self._status_label.setText(status)
        self._update_button_states()

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(done)
        self._status_label.setText(f"OCR中... ({done}/{total})")

    def set_status(self, status: str) -> None:
        self._status_label.setText(status)

    def status_text(self) -> str:
        return self._status_label.text()

    def _update_button_states(self) -> None:
        run_enabled = self._available and not self._busy
        self._ocr_all_btn.setEnabled(run_enabled)
        self._ocr_page_btn.setEnabled(run_enabled)
        # 削除は認識エンジン無しでも行える(実行中だけ無効)。
        self._clear_all_btn.setEnabled(not self._busy)
        self._clear_page_btn.setEnabled(not self._busy)
