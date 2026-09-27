"""個人情報検出の設定ダイアログ。

設定項目の構成は PresidioPDF の設定ダイアログ
(``PresidioPDF/src/gui_pyqt/views/config_dialog.py``: 検出対象エンティティ・
色・除外/追加パターン・重複除去・OCR)に倣うが、実装はJusticePDFのUI規約
(``src.views.settings_dialog.SettingsDialog`` と同じ ``build_accept_cancel_box``
パターン)に合わせて新規に書いている。

個人情報検出ドロワー(``src.views.pii_panel.PiiPanel``)の「設定...」ボタンから
開く独立ダイアログとして提供する(JusticePDFのメイン設定ダイアログは
「デフォルトで開くフォルダ」1項目だけの小型ダイアログのため、タブ化した
このダイアログを別立てにする方が自然)。
"""
from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.pii import ocr_support
from src.pii.entity_types import ENTITY_TYPES, get_entity_type_name_ja
from src.pii.settings import PiiSettings
from src.views.view_helpers import build_accept_cancel_box


def _color_to_qcolor(rgb: tuple[float, float, float]) -> QColor:
    return QColor(
        max(0, min(255, round(rgb[0] * 255))),
        max(0, min(255, round(rgb[1] * 255))),
        max(0, min(255, round(rgb[2] * 255))),
    )


def _qcolor_to_color(color: QColor) -> tuple[float, float, float]:
    return (color.redF(), color.greenF(), color.blueF())


class PiiSettingsDialog(QDialog):
    """個人情報検出の設定を編集するダイアログ。"""

    def __init__(self, settings: PiiSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("個人情報検出の設定")
        self.setMinimumSize(480, 480)
        # 呼び出し側のインスタンスは直接変更せず、コピー上で編集して
        # accept() 時にだけ確定させる(キャンセルで元の設定が壊れないように)。
        self._settings = settings.copy()

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        tabs.addTab(self._build_entities_tab(), "検出対象・色")
        tabs.addTab(self._build_exclusions_tab(), "除外・追加パターン")
        tabs.addTab(self._build_dedupe_tab(), "重複除去")
        tabs.addTab(self._build_ocr_tab(), "OCR")

        btn_box, self._ok_btn = build_accept_cancel_box(self, "OK")
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # タブ: 検出対象エンティティ・色
    # ------------------------------------------------------------------
    def _build_entities_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("チェックした種別のみ検出します。色はマーカーの色になります。"))

        self._entity_checks: dict[str, QCheckBox] = {}
        self._entity_color_btns: dict[str, QPushButton] = {}
        for entity_type in ENTITY_TYPES:
            row = QHBoxLayout()
            checkbox = QCheckBox(get_entity_type_name_ja(entity_type))
            checkbox.setChecked(self._settings.is_entity_enabled(entity_type))
            row.addWidget(checkbox, 1)

            color_btn = QPushButton("色...")
            color_btn.clicked.connect(
                lambda _checked=False, et=entity_type: self._on_pick_color(et)
            )
            self._apply_color_preview(color_btn, self._settings.color_for(entity_type))
            row.addWidget(color_btn)

            layout.addLayout(row)
            self._entity_checks[entity_type] = checkbox
            self._entity_color_btns[entity_type] = color_btn

        layout.addStretch()
        return widget

    def _apply_color_preview(self, button: QPushButton, rgb: tuple[float, float, float]) -> None:
        qcolor = _color_to_qcolor(rgb)
        button.setStyleSheet(f"background-color: {qcolor.name()};")
        button.setToolTip(qcolor.name())

    def _on_pick_color(self, entity_type: str) -> None:
        from PyQt6.QtWidgets import QColorDialog

        current = _color_to_qcolor(self._settings.color_for(entity_type))
        color = QColorDialog.getColor(current, self, f"{get_entity_type_name_ja(entity_type)}の色")
        if not color.isValid():
            return
        self._settings.colors[entity_type] = _qcolor_to_color(color)
        self._apply_color_preview(self._entity_color_btns[entity_type], self._settings.colors[entity_type])

    # ------------------------------------------------------------------
    # タブ: 除外・追加パターン
    # ------------------------------------------------------------------
    def _build_exclusions_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel("除外ワード(部分一致で検出結果から除外)"))
        self._exclusion_list = QListWidget()
        self._exclusion_list.addItems(self._settings.text_exclusions)
        layout.addWidget(self._exclusion_list)
        layout.addLayout(
            self._build_add_remove_row(
                self._exclusion_list, self._settings.text_exclusions, "除外ワードを入力"
            )
        )

        layout.addWidget(QLabel("除外パターン(正規表現)"))
        self._exclusion_regex_list = QListWidget()
        self._exclusion_regex_list.addItems(self._settings.text_exclusions_regex)
        layout.addWidget(self._exclusion_regex_list)
        layout.addLayout(
            self._build_add_remove_row(
                self._exclusion_regex_list,
                self._settings.text_exclusions_regex,
                "正規表現を入力",
            )
        )

        layout.addWidget(QLabel("追加検出パターン(エンティティ種別 + 正規表現)"))
        self._pattern_list = QListWidget()
        for entity_type, regex in self._settings.additional_patterns:
            self._pattern_list.addItem(f"{get_entity_type_name_ja(entity_type)}: {regex}")
        layout.addWidget(self._pattern_list)

        pattern_row = QHBoxLayout()
        self._pattern_entity_combo = QComboBox()
        for entity_type in ENTITY_TYPES:
            self._pattern_entity_combo.addItem(get_entity_type_name_ja(entity_type), entity_type)
        pattern_row.addWidget(self._pattern_entity_combo)
        self._pattern_regex_edit = QLineEdit()
        self._pattern_regex_edit.setPlaceholderText("正規表現")
        pattern_row.addWidget(self._pattern_regex_edit, 1)
        add_pattern_btn = QPushButton("追加")
        add_pattern_btn.clicked.connect(self._on_add_pattern)
        pattern_row.addWidget(add_pattern_btn)
        remove_pattern_btn = QPushButton("削除")
        remove_pattern_btn.clicked.connect(self._on_remove_pattern)
        pattern_row.addWidget(remove_pattern_btn)
        layout.addLayout(pattern_row)

        return widget

    def _build_add_remove_row(
        self, list_widget: QListWidget, backing_list: list[str], placeholder: str
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit, 1)

        def on_add() -> None:
            text = edit.text().strip()
            if not text:
                return
            backing_list.append(text)
            list_widget.addItem(text)
            edit.clear()

        def on_remove() -> None:
            for item in list_widget.selectedItems():
                idx = list_widget.row(item)
                list_widget.takeItem(idx)
                if 0 <= idx < len(backing_list):
                    backing_list.pop(idx)

        add_btn = QPushButton("追加")
        add_btn.clicked.connect(on_add)
        row.addWidget(add_btn)
        remove_btn = QPushButton("削除")
        remove_btn.clicked.connect(on_remove)
        row.addWidget(remove_btn)
        return row

    def _on_add_pattern(self) -> None:
        regex = self._pattern_regex_edit.text().strip()
        if not regex:
            return
        entity_type = self._pattern_entity_combo.currentData()
        self._settings.additional_patterns.append((entity_type, regex))
        self._pattern_list.addItem(f"{get_entity_type_name_ja(entity_type)}: {regex}")
        self._pattern_regex_edit.clear()

    def _on_remove_pattern(self) -> None:
        for item in self._pattern_list.selectedItems():
            idx = self._pattern_list.row(item)
            self._pattern_list.takeItem(idx)
            if 0 <= idx < len(self._settings.additional_patterns):
                self._settings.additional_patterns.pop(idx)

    # ------------------------------------------------------------------
    # タブ: 重複除去
    # ------------------------------------------------------------------
    def _build_dedupe_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel(
                "検出器同士(正規表現・形態素解析・日時)は重なりを除去しないため、"
                "同じ範囲に複数のハイライトが重なることがあります。"
                "重複除去を有効にすると、重なるもの同士から1件だけ残します。"
            )
        )

        self._dedupe_enabled_check = QCheckBox("重複除去を有効にする")
        self._dedupe_enabled_check.setChecked(self._settings.dedupe_enabled)
        layout.addWidget(self._dedupe_enabled_check)

        form = QFormLayout()
        self._dedupe_overlap_combo = QComboBox()
        self._dedupe_overlap_combo.addItem("一部でも重なる", "overlap")
        self._dedupe_overlap_combo.addItem("完全に一致", "exact")
        self._dedupe_overlap_combo.addItem("内包関係", "contain")
        self._set_combo_value(self._dedupe_overlap_combo, self._settings.dedupe_overlap)
        form.addRow("重複の判定方法:", self._dedupe_overlap_combo)

        self._dedupe_keep_combo = QComboBox()
        self._dedupe_keep_combo.addItem("最も広い範囲を残す", "widest")
        self._dedupe_keep_combo.addItem("先に見つかった方を残す", "first")
        self._dedupe_keep_combo.addItem("後に見つかった方を残す", "last")
        self._dedupe_keep_combo.addItem("エンティティ優先順で残す", "entity-order")
        self._set_combo_value(self._dedupe_keep_combo, self._settings.dedupe_keep)
        form.addRow("残す方の選び方:", self._dedupe_keep_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("エンティティ優先順(上ほど優先。「エンティティ優先順で残す」選択時のみ使用)"))
        self._priority_list = QListWidget()
        self._priority_list.addItems(
            [get_entity_type_name_ja(et) for et in self._settings.entity_priority_order]
        )
        layout.addWidget(self._priority_list)

        layout.addStretch()
        return widget

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    # ------------------------------------------------------------------
    # タブ: OCR
    # ------------------------------------------------------------------
    def _build_ocr_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        available = ocr_support.is_ocr_available()
        group = QGroupBox("OCR (RapidOCR) - 任意機能")
        group_layout = QVBoxLayout(group)
        if available:
            status = QLabel("RapidOCR が利用可能です。")
        else:
            status = QLabel(
                "RapidOCR が導入されていません。テキストレイヤの無いページ"
                "(スキャン画像等)でOCRを使うには、"
                "`uv sync --extra ocr` を実行してください。"
            )
            status.setWordWrap(True)
        group_layout.addWidget(status)

        self._ocr_enabled_check = QCheckBox("テキストレイヤの無いページはOCRしてから検出する")
        self._ocr_enabled_check.setChecked(self._settings.ocr_enabled)
        self._ocr_enabled_check.setEnabled(available)
        if not available:
            self._ocr_enabled_check.setToolTip("RapidOCRが導入されていないため使用できません。")
        group_layout.addWidget(self._ocr_enabled_check)

        dpi_row = QHBoxLayout()
        dpi_row.addWidget(QLabel("OCR解像度(DPI):"))
        self._ocr_dpi_spin = QSpinBox()
        self._ocr_dpi_spin.setRange(72, 600)
        self._ocr_dpi_spin.setValue(self._settings.ocr_dpi)
        self._ocr_dpi_spin.setEnabled(available)
        dpi_row.addWidget(self._ocr_dpi_spin)
        dpi_row.addStretch()
        group_layout.addLayout(dpi_row)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    # ------------------------------------------------------------------
    # 確定
    # ------------------------------------------------------------------
    def result_settings(self) -> PiiSettings:
        """ダイアログで編集した内容を反映した ``PiiSettings`` を返す。"""
        for entity_type, checkbox in self._entity_checks.items():
            self._settings.enabled_entities[entity_type] = checkbox.isChecked()
        self._settings.dedupe_enabled = self._dedupe_enabled_check.isChecked()
        self._settings.dedupe_overlap = self._dedupe_overlap_combo.currentData()
        self._settings.dedupe_keep = self._dedupe_keep_combo.currentData()
        self._settings.ocr_enabled = self._ocr_enabled_check.isChecked()
        self._settings.ocr_dpi = self._ocr_dpi_spin.value()
        return self._settings
