"""個人情報検出の設定ダイアログ。

設定項目の構成は PresidioPDF の設定ダイアログ
(``PresidioPDF/src/gui_pyqt/views/config_dialog.py``: 検出エンジン・
除外/検出パターン・重複除去・OCR)に倣うが、実装はJusticePDFのUI規約
(``src.views.settings_dialog.SettingsDialog`` と同じ ``build_accept_cancel_box``
パターン)に合わせて新規に書いている。

個人情報検出ドロワー(``src.views.pii_panel.PiiPanel``)の「設定...」ボタンから
開く独立ダイアログとして提供する(JusticePDFのメイン設定ダイアログは
「デフォルトで開くフォルダ」1項目だけの小型ダイアログのため、タブ化した
このダイアログを別立てにする方が自然)。

以前の「検出対象・色」タブは、種別ごとの検出対象チェックボックスを
個人情報検出ドロワー(``PiiPanel`` の「表示・検出する種別」)へ、色を
ドロワーの「色」「透明度」(全種別共通)へ移したため廃止した。
"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.ocr import is_ocr_available
from src.pii.engines import ENGINES, is_engine_available
from src.pii.entity_types import ENTITY_TYPES, get_entity_type_name_ja
from src.pii.settings import (
    RESULT_ORDER_MODES,
    WHITESPACE_MODES,
    PiiSettings,
    format_added_at,
    normalize_ocr_model_tier,
    normalize_result_order_mode,
    parse_added_at,
    pattern_key,
)
from src.pii.sudachi_tokenizer import SUDACHI_DICT_LABELS, sudachi_dict_available
from src.views.view_helpers import build_accept_cancel_box

_SORT_ROLE = Qt.ItemDataRole.UserRole + 1
_PAYLOAD_ROLE = Qt.ItemDataRole.UserRole


class _SortItem(QTableWidgetItem):
    """並べ替えキー(UserRole+1)で比べるセル。日時は表示文字列ではなく datetime で並べる。"""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self.data(_SORT_ROLE)
        theirs = other.data(_SORT_ROLE)
        if mine is None or theirs is None:
            return super().__lt__(other)
        return mine < theirs


class _PatternTable(QTableWidget):
    """パターン一覧の表(行選択・複数選択・見出しクリックで並べ替え・読み取り専用)。

    最後の列が「追加日時」。各行の先頭セルの UserRole に元のデータ(除外パターンの
    文字列/検出パターンの (種別, 正規表現))を持たせ、並べ替えで行が入れ替わっても
    削除時に元データを取り違えないようにする。追加日時が未記録の項目は空欄で、
    昇順では最も古い扱い(先頭)に並ぶ。
    """

    delete_requested = pyqtSignal()

    def __init__(self, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(len(headers) - 2, QHeaderView.ResizeMode.Stretch)
        header.setSortIndicatorShown(True)
        # 見出しクリックで昇順/降順を切り替える(初期は保存順のまま)。
        self.setSortingEnabled(True)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.setMinimumHeight(110)

    def append_entry(self, payload, cells: list[str], added_at: str | None) -> None:
        """1行追加する。``cells`` は日時列より前の表示文字列。"""
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)  # 挿入中に行が入れ替わらないようにする
        row = self.rowCount()
        self.insertRow(row)
        self._fill_row(row, payload, cells, added_at)
        self.setSortingEnabled(sorting)

    def update_entry(self, row: int, payload, cells: list[str], added_at: str | None) -> None:
        """``row`` 行の内容を置き換える(行の位置はそのまま)。"""
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)  # 書き換え中に行が入れ替わらないようにする
        self._fill_row(row, payload, cells, added_at)
        self.setSortingEnabled(sorting)

    def _fill_row(self, row: int, payload, cells: list[str], added_at: str | None) -> None:
        for col, text in enumerate(cells):
            item = _SortItem(text)
            item.setData(_SORT_ROLE, text)
            self.setItem(row, col, item)
        parsed = parse_added_at(added_at)
        date_item = _SortItem(format_added_at(added_at))
        date_item.setData(_SORT_ROLE, parsed if parsed is not None else datetime.min)
        self.setItem(row, len(cells), date_item)
        self.item(row, 0).setData(_PAYLOAD_ROLE, payload)

    def selected_payload(self):
        """ちょうど1行だけ選択されているときその元データを返す。それ以外は None。"""
        rows = self.selected_rows()
        if len(rows) != 1:
            return None
        return self.item(rows[0], 0).data(_PAYLOAD_ROLE)

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.selectionModel().selectedRows()})

    def take_selected_payloads(self) -> list:
        """選択中の行をすべて表から取り除き、元データのリストを返す。"""
        rows = self.selected_rows()
        payloads = [self.item(row, 0).data(_PAYLOAD_ROLE) for row in rows]
        for row in reversed(rows):  # 大きい行番号から消して番号のずれを防ぐ
            self.removeRow(row)
        return payloads

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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

        tabs.addTab(self._build_engines_tab(), "検出エンジン")
        tabs.addTab(self._build_exclusions_tab(), "除外・検出パターン")
        tabs.addTab(self._build_dedupe_tab(), "重複除去")
        tabs.addTab(self._build_ocr_tab(), "OCR")

        btn_box, self._ok_btn = build_accept_cancel_box(self, "OK")
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # タブ: 検出エンジン
    # ------------------------------------------------------------------
    def _build_engines_tab(self) -> QWidget:
        """検出エンジン(認識器)ごとのON/OFF。

        以前のPresidioPDFでは複数のエンジン/認識器を選択できたが、JusticePDFへの
        移植時に単一パイプラインへ統合され選べなくなっていたため、ここで
        ``src.pii.engines.ENGINES`` を選択式に戻す。未導入のエンジン(SudachiPy
        が入っていない環境など)はチェックボックスをグレーアウトする。
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel("チェックしたエンジンだけを検出に使用します。未導入のエンジンは選択できません。")
        )

        select_row = QHBoxLayout()
        select_all_btn = QPushButton("すべて選択")
        select_all_btn.clicked.connect(lambda: self._set_all_engine_checks(True))
        select_row.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("すべて解除")
        deselect_all_btn.clicked.connect(lambda: self._set_all_engine_checks(False))
        select_row.addWidget(deselect_all_btn)
        select_row.addStretch()
        layout.addLayout(select_row)

        self._engine_checks: dict[str, QCheckBox] = {}
        for engine in ENGINES:
            available = is_engine_available(engine.key)
            label = engine.name_ja if available else f"{engine.name_ja}(未インストール)"
            checkbox = QCheckBox(label)
            checkbox.setToolTip(engine.description_ja)
            checkbox.setEnabled(available)
            checkbox.setChecked(
                available and self._settings.enabled_engines.get(engine.key, engine.default_enabled)
            )
            layout.addWidget(checkbox)
            self._engine_checks[engine.key] = checkbox

        layout.addWidget(self._build_sudachi_dict_group())

        # ページ末尾と次ページ先頭で分かれた語(氏名など)も検出する。検出対象のうち
        # ページ番号が連続する範囲ごとに本文を連結して解析する。
        self._cross_page_check = QCheckBox("ページをまたぐ語も検出する")
        self._cross_page_check.setToolTip(
            "ページ末尾と次のページ先頭で分かれた氏名などを検出します。"
            "検出対象のうち、ページ番号が連続している範囲ごとに本文をつなげて解析します。"
            "結果は、ページごとに分かれた通常のマーカーとして保存されます。"
        )
        self._cross_page_check.setChecked(self._settings.cross_page_detection)
        layout.addWidget(self._cross_page_check)

        layout.addStretch()
        return widget

    def _build_sudachi_dict_group(self) -> QGroupBox:
        """形態素解析(SudachiPy)の辞書の選択。未導入の辞書は選べない。"""
        group = QGroupBox("形態素解析の辞書")
        group_layout = QVBoxLayout(group)
        row = QHBoxLayout()
        row.addWidget(QLabel("辞書:"))
        self._sudachi_dict_combo = QComboBox()
        for dict_type, label in SUDACHI_DICT_LABELS.items():
            available = sudachi_dict_available(dict_type)
            self._sudachi_dict_combo.addItem(
                label if available else f"{label}(未インストール)", dict_type
            )
            item = self._sudachi_dict_combo.model().item(self._sudachi_dict_combo.count() - 1)
            if not available:
                item.setEnabled(False)
                item.setToolTip("`uv sync`(または `pip install -e \".[all]\"`)で導入できます。")
        current_dict = str(self._settings.sudachi_dict_type or "core").lower()
        self._set_combo_value(
            self._sudachi_dict_combo,
            current_dict if current_dict in SUDACHI_DICT_LABELS else "core",
        )
        self._sudachi_dict_combo.setEnabled(is_engine_available("sudachi"))
        row.addWidget(self._sudachi_dict_combo)
        row.addStretch()
        group_layout.addLayout(row)
        hint = QLabel(
            "大きい辞書ほど固有名詞の検出精度が上がりますが、読み込みに時間とメモリを使います。"
            "未導入の辞書は `uv sync`(または `pip install -e \".[all]\"`)で導入します"
            "(fullは数百MB)。導入していない辞書が設定に残っている場合は、標準(core)で検出して警告を表示します。"
        )
        hint.setWordWrap(True)
        group_layout.addWidget(hint)
        return group

    def _set_all_engine_checks(self, checked: bool) -> None:
        for checkbox in self._engine_checks.values():
            if checkbox.isEnabled():
                checkbox.setChecked(checked)

    # ------------------------------------------------------------------
    # タブ: 除外・検出パターン
    # ------------------------------------------------------------------
    def _build_exclusions_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(
            QLabel(
                "除外パターン(正規表現・部分一致で検出結果から除外。記号は \\ でエスケープ。"
                "完全一致なら ^語句$ の形。検出パターンや人名リストの検出結果よりも優先されます)"
            )
        )
        self._exclusion_table = _PatternTable(["パターン", "追加日時"])
        for regex in self._settings.text_exclusions_regex:
            self._exclusion_table.append_entry(
                regex, [regex], self._settings.text_exclusions_added_at.get(regex)
            )
        self._exclusion_table.delete_requested.connect(self._on_remove_exclusion)
        self._exclusion_table.itemSelectionChanged.connect(self._on_exclusion_selection_changed)
        layout.addWidget(self._exclusion_table)

        exclusion_row = QHBoxLayout()
        self._exclusion_edit = QLineEdit()
        self._exclusion_edit.setPlaceholderText("正規表現を入力")
        exclusion_row.addWidget(self._exclusion_edit, 1)
        add_exclusion_btn = QPushButton("追加")
        add_exclusion_btn.clicked.connect(self._on_add_exclusion)
        exclusion_row.addWidget(add_exclusion_btn)
        # 一覧で1件だけ選ぶと入力欄にその内容が入り、編集して「更新」で置き換える。
        self._update_exclusion_btn = QPushButton("更新")
        self._update_exclusion_btn.setToolTip("一覧で選んだ項目を、入力欄の内容で置き換えます")
        self._update_exclusion_btn.setEnabled(False)
        self._update_exclusion_btn.clicked.connect(self._on_update_exclusion)
        exclusion_row.addWidget(self._update_exclusion_btn)
        remove_exclusion_btn = QPushButton("削除")
        remove_exclusion_btn.clicked.connect(self._on_remove_exclusion)
        exclusion_row.addWidget(remove_exclusion_btn)
        layout.addLayout(exclusion_row)

        layout.addWidget(
            QLabel(
                "検出パターン(エンティティ種別 + 正規表現。"
                "除外パターンに一致する結果は除外されます)"
            )
        )
        self._pattern_table = _PatternTable(["種類", "パターン", "追加日時"])
        for entity_type, regex in self._settings.additional_patterns:
            self._pattern_table.append_entry(
                (entity_type, regex),
                [get_entity_type_name_ja(entity_type), regex],
                self._settings.additional_patterns_added_at.get(
                    pattern_key(entity_type, regex)
                ),
            )
        self._pattern_table.delete_requested.connect(self._on_remove_pattern)
        self._pattern_table.itemSelectionChanged.connect(self._on_pattern_selection_changed)
        layout.addWidget(self._pattern_table)

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
        self._update_pattern_btn = QPushButton("更新")
        self._update_pattern_btn.setToolTip("一覧で選んだ項目を、入力欄の内容で置き換えます")
        self._update_pattern_btn.setEnabled(False)
        self._update_pattern_btn.clicked.connect(self._on_update_pattern)
        pattern_row.addWidget(self._update_pattern_btn)
        remove_pattern_btn = QPushButton("削除")
        remove_pattern_btn.clicked.connect(self._on_remove_pattern)
        pattern_row.addWidget(remove_pattern_btn)
        layout.addLayout(pattern_row)

        whitespace_row = QHBoxLayout()
        whitespace_row.addWidget(QLabel("登録時の空白の扱い:"))
        self._whitespace_mode_combo = QComboBox()
        for key, label in WHITESPACE_MODES:
            self._whitespace_mode_combo.addItem(label, key)
        self._set_combo_value(
            self._whitespace_mode_combo, self._settings.pattern_whitespace_mode
        )
        self._whitespace_mode_combo.setToolTip(
            "右クリックメニューから語句を検出パターン・除外パターンに追加するとき、\n"
            "語句の中の空白(半角・全角・改行)をどう扱うパターンにするかを選びます。\n"
            "この一覧から手入力で追加するパターンには影響しません。"
        )
        whitespace_row.addWidget(self._whitespace_mode_combo, 1)
        layout.addLayout(whitespace_row)

        result_order_row = QHBoxLayout()
        result_order_row.addWidget(QLabel("結果一覧の並び順(ページ内):"))
        self._result_order_combo = QComboBox()
        for key, label in RESULT_ORDER_MODES:
            self._result_order_combo.addItem(label, key)
        self._set_combo_value(self._result_order_combo, self._settings.result_order_mode)
        self._result_order_combo.setToolTip(
            "個人情報検出パネルの結果一覧で、同じページ内の項目を並べる順序です。\n"
            "横書き: 上から下へ、同じ行は左から右へ。\n"
            "縦書き: 右の列から左の列へ、同じ列は上から下へ。"
        )
        result_order_row.addWidget(self._result_order_combo, 1)
        layout.addLayout(result_order_row)

        return widget

    def _on_add_exclusion(self) -> None:
        text = self._exclusion_edit.text().strip()
        if not text:
            return
        if self._settings.add_exclusion(text):
            self._exclusion_table.append_entry(
                text, [text], self._settings.text_exclusions_added_at.get(text)
            )
        self._exclusion_edit.clear()

    def _on_exclusion_selection_changed(self) -> None:
        """1件だけ選ばれたら入力欄へ反映し「更新」を有効にする(複数・未選択は無効)。"""
        regex = self._exclusion_table.selected_payload()
        self._update_exclusion_btn.setEnabled(regex is not None)
        if regex is not None:
            self._exclusion_edit.setText(regex)

    def _on_update_exclusion(self) -> None:
        old = self._exclusion_table.selected_payload()
        new = self._exclusion_edit.text().strip()
        if old is None or not new:
            return
        if self._settings.replace_exclusion(old, new):
            row = self._exclusion_table.selected_rows()[0]
            self._exclusion_table.update_entry(
                row, new, [new], self._settings.text_exclusions_added_at.get(new)
            )
            self._exclusion_table.clearSelection()
            self._exclusion_edit.clear()

    def _on_remove_exclusion(self) -> None:
        for regex in self._exclusion_table.take_selected_payloads():
            if regex in self._settings.text_exclusions_regex:
                self._settings.text_exclusions_regex.remove(regex)
        self._settings.prune_added_at()

    def _on_add_pattern(self) -> None:
        regex = self._pattern_regex_edit.text().strip()
        if not regex:
            return
        entity_type = self._pattern_entity_combo.currentData()
        if self._settings.add_additional_pattern(entity_type, regex):
            self._pattern_table.append_entry(
                (entity_type, regex),
                [get_entity_type_name_ja(entity_type), regex],
                self._settings.additional_patterns_added_at.get(
                    pattern_key(entity_type, regex)
                ),
            )
        self._pattern_regex_edit.clear()

    def _on_pattern_selection_changed(self) -> None:
        """1件だけ選ばれたら種別・正規表現を入力欄へ反映し「更新」を有効にする。"""
        entry = self._pattern_table.selected_payload()
        self._update_pattern_btn.setEnabled(entry is not None)
        if entry is not None:
            entity_type, regex = entry
            self._set_combo_value(self._pattern_entity_combo, entity_type)
            self._pattern_regex_edit.setText(regex)

    def _on_update_pattern(self) -> None:
        old = self._pattern_table.selected_payload()
        regex = self._pattern_regex_edit.text().strip()
        if old is None or not regex:
            return
        new = (self._pattern_entity_combo.currentData(), regex)
        if self._settings.replace_additional_pattern(old, new):
            row = self._pattern_table.selected_rows()[0]
            self._pattern_table.update_entry(
                row,
                new,
                [get_entity_type_name_ja(new[0]), regex],
                self._settings.additional_patterns_added_at.get(pattern_key(*new)),
            )
            self._pattern_table.clearSelection()
            self._pattern_regex_edit.clear()

    def _on_remove_pattern(self) -> None:
        for entry in self._pattern_table.take_selected_payloads():
            if entry in self._settings.additional_patterns:
                self._settings.additional_patterns.remove(entry)
        self._settings.prune_added_at()

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

        available = is_ocr_available()
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

        # 有効にすると「検出」の前に、テキストレイヤの無いページだけを自動でOCRして
        # (見えないテキストとして埋め込み)から検出する。手動のOCRはOCRパネルから行う。
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

        # モデルの種別。heavy(server)は同梱されておらず、初回のOCR実行時に
        # RapidOCRがモデルファイルをダウンロードする(light/mobileはパッケージに同梱)。
        tier_row = QHBoxLayout()
        tier_row.addWidget(QLabel("OCRモデル:"))
        self._ocr_tier_combo = QComboBox()
        self._ocr_tier_combo.addItem("軽量(高速・同梱モデル)", "light")
        self._ocr_tier_combo.addItem("高精度(低速・初回にモデルをダウンロード)", "heavy")
        self._ocr_tier_combo.setToolTip(
            "高精度モデルはパッケージに同梱されていないため、初めて使うときに"
            "RapidOCRがモデルファイルをインターネットからダウンロードします。"
            "軽量モデルは同梱されており、追加のダウンロードは要りません。"
        )
        self._set_combo_value(
            self._ocr_tier_combo, normalize_ocr_model_tier(self._settings.ocr_model_tier)
        )
        self._ocr_tier_combo.setEnabled(available)
        tier_row.addWidget(self._ocr_tier_combo)
        tier_row.addStretch()
        group_layout.addLayout(tier_row)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    # ------------------------------------------------------------------
    # 確定
    # ------------------------------------------------------------------
    def result_settings(self) -> PiiSettings:
        """ダイアログで編集した内容を反映した ``PiiSettings`` を返す。"""
        for engine_key, checkbox in self._engine_checks.items():
            # 未導入(disabled)のチェックボックスは常に未チェックなので、そのまま
            # 保存してもis_engine_enabled()側の既定値解決には影響しない
            # (導入され次第、チェックすれば有効になる)。
            self._settings.enabled_engines[engine_key] = checkbox.isChecked()
        self._settings.dedupe_enabled = self._dedupe_enabled_check.isChecked()
        self._settings.dedupe_overlap = self._dedupe_overlap_combo.currentData()
        self._settings.dedupe_keep = self._dedupe_keep_combo.currentData()
        self._settings.pattern_whitespace_mode = self._whitespace_mode_combo.currentData()
        self._settings.result_order_mode = normalize_result_order_mode(
            self._result_order_combo.currentData()
        )
        self._settings.ocr_enabled = self._ocr_enabled_check.isChecked()
        self._settings.ocr_dpi = self._ocr_dpi_spin.value()
        self._settings.ocr_model_tier = normalize_ocr_model_tier(
            self._ocr_tier_combo.currentData()
        )
        self._settings.sudachi_dict_type = str(
            self._sudachi_dict_combo.currentData() or self._settings.sudachi_dict_type
        )
        self._settings.cross_page_detection = self._cross_page_check.isChecked()
        self._settings.prune_added_at()
        return self._settings
