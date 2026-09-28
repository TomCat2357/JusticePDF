"""個人情報(PII)検出ドロワー。

ページ編集画面のズームビュー右側にドロワーとして組み込む独立部品。
``BookmarksPanel`` と同じ「表示専用・状態は呼び出し側(page_edit_window)が持つ」
方針に従う。本パネルはPDFの読み書きを一切行わず、ボタン操作をシグナルで
通知するだけ。実際の検出実行・注釈の作成/削除・Undo登録は
``src.views.page_edit_pii.PiiDrawerMixin`` が担う。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.pii.entity_types import (
    ENTITY_TYPES,
    ENTITY_TYPES_WITH_MANUAL,
    MANUAL_ENTITY_TYPE,
    get_entity_type_name_ja,
)
from src.utils.pdf_utils import ShapeAnnotData, ShapeType, TextMarkupAnnotData

logger = logging.getLogger(__name__)

_ANNOT_ROLE = Qt.ItemDataRole.UserRole


@dataclass(slots=True)
class PiiResultRow:
    """結果一覧1行分の表示用データ(マーカー/図形どちらも同じ形で扱う)。"""

    annot: "TextMarkupAnnotData | ShapeAnnotData"
    page_num: int
    entity: str
    text: str  # マッチした文字列(無ければ空文字)
    kind: str  # "markup" | "shape"

    @property
    def display_text(self) -> str:
        if self.text:
            return self.text
        return "[図形]" if self.kind == "shape" else "(テキストなし)"


class PiiPanel(QFrame):
    """個人情報検出ドロワー。

    Signals
    -------
    open_changed(bool)
        ドロワーの開閉が切り替わったとき発火。
    detect_current_page_requested()
        「現在のページを検出」ボタン押下時。
    detect_all_pages_requested()
        「すべてのページを検出」ボタン押下時。
    mask_markup_tool_toggled(bool)
        「テキスト候補」ツール(手動でテキストを選択→塗りつぶし候補化)のON/OFF。
    mask_rect_tool_toggled(bool)
        「塗り四角」ツールのON/OFF。
    mask_ellipse_tool_toggled(bool)
        「塗り丸」ツールのON/OFF。
    remove_selected_requested()
        「選択を削除」ボタン押下時。
    remove_all_requested()
        「すべて削除」ボタン押下時。
    delete_same_text_requested(str)
        結果一覧の右クリックメニュー「同じ語句をすべて削除」。
    add_exclusion_requested(str, str)
        結果一覧の右クリックメニュー「除外語句に登録」。(entity_type, text) を伴う。
    add_pattern_requested(str, str)
        結果一覧の右クリックメニュー「追加パターンに登録」。(entity_type, text) を伴う。
    settings_requested()
        「設定...」ボタン押下時。
    export_rasterize_requested()
        「黒塗りして画像のみエクスポート」メニュー項目選択時。
    export_redact_requested()
        「文字を削除してテキストPDFとしてエクスポート」メニュー項目選択時。
    result_activated(object)
        結果一覧の項目がクリックされたとき、対応する注釈データを伴って発火
        (``TextMarkupAnnotData`` または ``ShapeAnnotData``)。
    """

    open_changed = pyqtSignal(bool)
    detect_current_page_requested = pyqtSignal()
    detect_all_pages_requested = pyqtSignal()
    keep_existing_toggled = pyqtSignal(bool)
    mask_markup_tool_toggled = pyqtSignal(bool)
    mask_rect_tool_toggled = pyqtSignal(bool)
    mask_ellipse_tool_toggled = pyqtSignal(bool)
    remove_selected_requested = pyqtSignal()
    remove_all_requested = pyqtSignal()
    delete_same_text_requested = pyqtSignal(str)
    add_exclusion_requested = pyqtSignal(str, str)
    add_pattern_requested = pyqtSignal(str, str)
    settings_requested = pyqtSignal()
    export_rasterize_requested = pyqtSignal()
    export_redact_requested = pyqtSignal()
    result_activated = pyqtSignal(object)

    DRAWER_WIDTH = 340

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("piiDrawer")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._is_open = False
        self._collapsed_width = 32
        self._rows: list[PiiResultRow] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("◀")
        self._toggle_btn.setToolTip("個人情報検出")
        self._toggle_btn.setFixedWidth(32)
        self._toggle_btn.clicked.connect(self.toggle)
        layout.addWidget(self._toggle_btn)

        self._panel = QWidget()
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        panel_layout.addWidget(QLabel("個人情報検出"))

        # --- 検出実行 ---
        detect_row = QHBoxLayout()
        self._detect_current_btn = QPushButton("現在のページを検出")
        self._detect_current_btn.clicked.connect(self.detect_current_page_requested.emit)
        detect_row.addWidget(self._detect_current_btn)
        self._detect_all_btn = QPushButton("すべてのページを検出")
        self._detect_all_btn.clicked.connect(self.detect_all_pages_requested.emit)
        detect_row.addWidget(self._detect_all_btn)
        panel_layout.addLayout(detect_row)

        self._keep_existing_check = QCheckBox("既存の結果を残して追加検出")
        self._keep_existing_check.setToolTip(
            "オンにすると、検出済みの塗りつぶし候補・手動追加分は消さずに、"
            "新しく見つかったものだけを追加します"
            "(同じページ・同じ位置・同じ語句のものは重複して追加しません)。\n"
            "オフの場合は従来通り、検出し直したページの結果を置き換えます。"
        )
        self._keep_existing_check.toggled.connect(self.keep_existing_toggled.emit)
        panel_layout.addWidget(self._keep_existing_check)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        panel_layout.addWidget(self._progress_bar)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: palette(dark);")
        panel_layout.addWidget(self._status_label)

        # --- 手動で塗りつぶし候補を追加 ---
        manual_group = QGroupBox("手動で塗りつぶし候補を追加")
        manual_layout = QVBoxLayout(manual_group)
        entity_row = QHBoxLayout()
        entity_row.addWidget(QLabel("種別:"))
        self._manual_entity_combo = QComboBox()
        for entity_type in ENTITY_TYPES_WITH_MANUAL:
            self._manual_entity_combo.addItem(get_entity_type_name_ja(entity_type), entity_type)
        # 既定は「手動」(自動検出のどの種別にも属さないことを明示する)。
        default_idx = self._manual_entity_combo.findData(MANUAL_ENTITY_TYPE)
        if default_idx >= 0:
            self._manual_entity_combo.setCurrentIndex(default_idx)
        self._manual_entity_combo.setToolTip(
            "「テキスト候補」「塗り四角」「塗り丸」で追加する候補の種別(色)を選びます。"
        )
        entity_row.addWidget(self._manual_entity_combo, 1)
        manual_layout.addLayout(entity_row)

        tools_row = QHBoxLayout()
        self._mask_markup_btn = QToolButton()
        self._mask_markup_btn.setText("テキスト候補")
        self._mask_markup_btn.setToolTip(
            "ページ上のテキストをドラッグ選択して塗りつぶし候補にします。"
        )
        self._mask_markup_btn.setCheckable(True)
        self._mask_markup_btn.toggled.connect(self.mask_markup_tool_toggled.emit)
        tools_row.addWidget(self._mask_markup_btn)
        self._mask_rect_btn = QToolButton()
        self._mask_rect_btn.setText("塗り四角")
        self._mask_rect_btn.setToolTip(
            "ドラッグで塗りつぶし用の四角を描きます(文字の無い写真等にも使えます)。"
        )
        self._mask_rect_btn.setCheckable(True)
        self._mask_rect_btn.toggled.connect(self.mask_rect_tool_toggled.emit)
        tools_row.addWidget(self._mask_rect_btn)
        self._mask_ellipse_btn = QToolButton()
        self._mask_ellipse_btn.setText("塗り丸")
        self._mask_ellipse_btn.setToolTip(
            "ドラッグで塗りつぶし用の楕円を描きます(印影等にも使えます)。"
        )
        self._mask_ellipse_btn.setCheckable(True)
        self._mask_ellipse_btn.toggled.connect(self.mask_ellipse_tool_toggled.emit)
        tools_row.addWidget(self._mask_ellipse_btn)
        manual_layout.addLayout(tools_row)
        panel_layout.addWidget(manual_group)

        # --- 結果一覧 ---
        panel_layout.addWidget(QLabel("検出結果・塗りつぶし対象"))
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("並び替え:"))
        self._sort_field = "page"
        self._sort_ascending = True
        self._sort_combo = QComboBox()
        self._sort_combo.addItem("ページ順", "page")
        self._sort_combo.addItem("語句順", "text")
        self._sort_combo.addItem("種別順", "entity")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_field_changed)
        sort_row.addWidget(self._sort_combo, 1)
        self._sort_order_btn = QToolButton()
        self._sort_order_btn.setCheckable(True)
        self._sort_order_btn.setText("昇順 ▲")
        self._sort_order_btn.setToolTip("並び順(昇順/降順)を切り替えます。")
        self._sort_order_btn.toggled.connect(self._on_sort_order_toggled)
        sort_row.addWidget(self._sort_order_btn)
        panel_layout.addLayout(sort_row)

        self._result_tree = QTreeWidget()
        self._result_tree.setColumnCount(3)
        self._result_tree.setHeaderLabels(["語句", "種別", "ページ"])
        self._result_tree.setRootIsDecorated(False)
        self._result_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._result_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._result_tree.itemClicked.connect(self._on_result_item_clicked)
        self._result_tree.itemSelectionChanged.connect(self._update_button_states)
        self._result_tree.customContextMenuRequested.connect(self._on_result_context_menu)
        # 列ヘッダクリックでも並び替えできるようにする(明示的なコンボ/ボタンと併用)。
        header = self._result_tree.header()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_result_header_clicked)
        panel_layout.addWidget(self._result_tree, 1)

        # --- 操作ボタン ---
        action_row = QHBoxLayout()
        self._remove_selected_btn = QPushButton("選択を削除")
        self._remove_selected_btn.clicked.connect(self.remove_selected_requested.emit)
        action_row.addWidget(self._remove_selected_btn)
        self._remove_all_btn = QPushButton("すべて削除")
        self._remove_all_btn.clicked.connect(self.remove_all_requested.emit)
        action_row.addWidget(self._remove_all_btn)
        panel_layout.addLayout(action_row)

        settings_row = QHBoxLayout()
        self._settings_btn = QPushButton("設定...")
        self._settings_btn.clicked.connect(self.settings_requested.emit)
        settings_row.addWidget(self._settings_btn)
        panel_layout.addLayout(settings_row)

        self._export_btn = QPushButton("エクスポート ▾")
        self._export_btn.setToolTip("塗りつぶし候補・塗りつぶし用図形を反映してPDFを書き出します。")
        export_menu = QMenu(self._export_btn)
        rasterize_action = export_menu.addAction("黒塗りして画像のみエクスポート")
        rasterize_action.triggered.connect(self.export_rasterize_requested.emit)
        redact_action = export_menu.addAction("文字を削除してテキストPDFとしてエクスポート")
        redact_action.triggered.connect(self.export_redact_requested.emit)
        self._export_btn.setMenu(export_menu)
        panel_layout.addWidget(self._export_btn)

        panel_layout.addStretch()
        layout.addWidget(self._panel)

        self.set_open(False)
        self._update_button_states()

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
        """開閉トグルを外部ボタンに委譲する(BookmarksPanelと同じ規約)。"""
        self._collapsed_width = 0
        self._toggle_btn.hide()
        if not self._is_open:
            self.setFixedWidth(0)

    def set_busy(self, busy: bool, status: str = "") -> None:
        """検出実行中はボタンを無効化し、進捗表示を出す。"""
        self._detect_current_btn.setEnabled(not busy)
        self._detect_all_btn.setEnabled(not busy)
        self._progress_bar.setVisible(busy)
        if busy:
            self._progress_bar.setRange(0, 0)  # 総ページ数が分かるまでは不定進捗
        self._status_label.setText(status)

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(done)
        self._status_label.setText(f"検出中... ({done}/{total})")

    def keep_existing_checked(self) -> bool:
        """「既存の結果を残して追加検出」チェックボックスの状態を返す。"""
        return self._keep_existing_check.isChecked()

    def set_keep_existing_checked(self, checked: bool) -> None:
        with self._signal_blockers(self._keep_existing_check):
            self._keep_existing_check.setChecked(bool(checked))

    def selected_manual_entity(self) -> str:
        """手動追加ツール(テキスト候補/塗り四角/塗り丸)用に選択中のエンティティ種別。"""
        data = self._manual_entity_combo.currentData()
        return str(data) if data else MANUAL_ENTITY_TYPE

    def set_mask_markup_tool_active(self, enabled: bool) -> None:
        """呼び出し側(mixin)の実際の作成モードにボタンのチェック状態を同期する。"""
        with self._signal_blockers(self._mask_markup_btn):
            self._mask_markup_btn.setChecked(bool(enabled))

    def set_mask_shape_tool_active(self, shape_type: "ShapeType | None") -> None:
        with self._signal_blockers(self._mask_rect_btn, self._mask_ellipse_btn):
            self._mask_rect_btn.setChecked(shape_type == ShapeType.RECTANGLE)
            self._mask_ellipse_btn.setChecked(shape_type == ShapeType.ELLIPSE)

    def set_results(self, rows: list[PiiResultRow]) -> None:
        """検出結果・塗りつぶし用図形の一覧を、現在の並び順設定で再描画する。"""
        self._rows = list(rows)
        self._rebuild_result_tree()
        self._update_button_states()

    def selected_results(self) -> list["TextMarkupAnnotData | ShapeAnnotData"]:
        items = self._result_tree.selectedItems()
        return [item.data(0, _ANNOT_ROLE).annot for item in items if item.data(0, _ANNOT_ROLE) is not None]

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _signal_blockers(self, *widgets):
        return _MultiSignalBlocker(widgets)

    def _on_sort_field_changed(self, _index: int) -> None:
        data = self._sort_combo.currentData()
        self._sort_field = str(data) if data else "page"
        self._rebuild_result_tree()

    def _on_sort_order_toggled(self, checked: bool) -> None:
        self._sort_order_btn.setText("降順 ▼" if checked else "昇順 ▲")
        self._sort_ascending = not checked
        self._rebuild_result_tree()

    def _on_result_header_clicked(self, column: int) -> None:
        """列ヘッダクリックでもコンボ/昇降順ボタンと同じ並び替えを行えるようにする。

        同じ列を続けてクリックしたときは昇順/降順を反転し、別の列をクリック
        したときはその列の並び替えキーへ切り替える(方向は維持)。
        """
        field = {0: "text", 1: "entity", 2: "page"}.get(column, "page")
        if field == self._sort_field:
            self._sort_order_btn.setChecked(not self._sort_order_btn.isChecked())
            return
        idx = self._sort_combo.findData(field)
        if idx >= 0:
            self._sort_combo.setCurrentIndex(idx)  # _on_sort_field_changed 経由で再描画される

    def _sort_key(self, row: "PiiResultRow"):
        entity_ja = get_entity_type_name_ja(row.entity or "OTHER")
        if self._sort_field == "text":
            return (row.display_text, row.page_num, entity_ja)
        if self._sort_field == "entity":
            return (entity_ja, row.page_num, row.display_text)
        return (row.page_num, row.display_text, entity_ja)  # "page"(既定)

    def _rebuild_result_tree(self) -> None:
        self._result_tree.clear()
        rows = sorted(self._rows, key=self._sort_key, reverse=not self._sort_ascending)
        for row in rows:
            entity_ja = get_entity_type_name_ja(row.entity or "OTHER")
            item = QTreeWidgetItem([row.display_text, entity_ja, f"p.{row.page_num + 1}"])
            item.setData(0, _ANNOT_ROLE, row)
            self._result_tree.addTopLevelItem(item)
        for col in range(3):
            self._result_tree.resizeColumnToContents(col)

    def _on_result_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        row: PiiResultRow | None = item.data(0, _ANNOT_ROLE)
        if row is not None:
            self.result_activated.emit(row.annot)

    def _on_result_context_menu(self, pos) -> None:
        item = self._result_tree.itemAt(pos)
        if item is None:
            return
        row: PiiResultRow | None = item.data(0, _ANNOT_ROLE)
        if row is None:
            return
        menu = QMenu(self._result_tree)
        delete_same_action = menu.addAction("同じ語句をすべて削除")
        delete_same_action.setEnabled(bool(row.text))
        exclude_action = menu.addAction("除外語句に登録")
        exclude_action.setEnabled(bool(row.text and row.entity))
        # 追加パターンは自動検出の種別(ENTITY_TYPES)向けの機能のため、
        # 手動追加分(種別="MANUAL")には出さない。
        add_pattern_action = menu.addAction("追加パターンに登録")
        add_pattern_action.setEnabled(bool(row.text and row.entity in ENTITY_TYPES))
        chosen = menu.exec(self._result_tree.viewport().mapToGlobal(pos))
        if chosen is delete_same_action:
            self.delete_same_text_requested.emit(row.text)
        elif chosen is exclude_action:
            self.add_exclusion_requested.emit(row.entity, row.text)
        elif chosen is add_pattern_action:
            self.add_pattern_requested.emit(row.entity, row.text)

    def _update_button_states(self) -> None:
        has_results = self._result_tree.topLevelItemCount() > 0
        self._remove_all_btn.setEnabled(has_results)
        self._export_btn.setEnabled(has_results)
        self._remove_selected_btn.setEnabled(bool(self._result_tree.selectedItems()))


class _MultiSignalBlocker:
    """with構文で複数ウィジェットのシグナルを一括ブロックする(QSignalBlockerの複数版)。"""

    def __init__(self, widgets) -> None:
        self._widgets = list(widgets)
        self._blockers: list = []

    def __enter__(self) -> None:
        self._blockers = [QSignalBlocker(w) for w in self._widgets]
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        self._blockers = []
