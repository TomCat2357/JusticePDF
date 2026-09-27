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

from src.pii.entity_types import ENTITY_TYPES, get_entity_type_name_ja
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
    settings_requested()
        「設定...」ボタン押下時。
    export_rasterize_requested()
        「黒塗りして画像のみエクスポート」メニュー項目選択時。
    export_redact_requested()
        「文字を削除してテキストPDFとしてエクスポート」メニュー項目選択時。
    result_activated(object)
        結果一覧の項目がクリックされたとき、対応する注釈データを伴って発火
        (``TextMarkupAnnotData`` または ``ShapeAnnotData``)。
    filter_changed()
        エンティティ種別フィルタのチェック状態が変わったとき発火。
    """

    open_changed = pyqtSignal(bool)
    detect_current_page_requested = pyqtSignal()
    detect_all_pages_requested = pyqtSignal()
    mask_markup_tool_toggled = pyqtSignal(bool)
    mask_rect_tool_toggled = pyqtSignal(bool)
    mask_ellipse_tool_toggled = pyqtSignal(bool)
    remove_selected_requested = pyqtSignal()
    remove_all_requested = pyqtSignal()
    delete_same_text_requested = pyqtSignal(str)
    add_exclusion_requested = pyqtSignal(str, str)
    settings_requested = pyqtSignal()
    export_rasterize_requested = pyqtSignal()
    export_redact_requested = pyqtSignal()
    result_activated = pyqtSignal(object)
    filter_changed = pyqtSignal()

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

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        panel_layout.addWidget(self._progress_bar)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: palette(dark);")
        panel_layout.addWidget(self._status_label)

        # --- 手動で塗りつぶし候補を追加 ---
        manual_group = QGroupBox("手動で塗りつぶし候補を追加")
        manual_layout = QHBoxLayout(manual_group)
        self._mask_markup_btn = QToolButton()
        self._mask_markup_btn.setText("テキスト候補")
        self._mask_markup_btn.setToolTip(
            "ページ上のテキストをドラッグ選択して塗りつぶし候補にします。"
        )
        self._mask_markup_btn.setCheckable(True)
        self._mask_markup_btn.toggled.connect(self.mask_markup_tool_toggled.emit)
        manual_layout.addWidget(self._mask_markup_btn)
        self._mask_rect_btn = QToolButton()
        self._mask_rect_btn.setText("塗り四角")
        self._mask_rect_btn.setToolTip(
            "ドラッグで塗りつぶし用の四角を描きます(文字の無い写真等にも使えます)。"
        )
        self._mask_rect_btn.setCheckable(True)
        self._mask_rect_btn.toggled.connect(self.mask_rect_tool_toggled.emit)
        manual_layout.addWidget(self._mask_rect_btn)
        self._mask_ellipse_btn = QToolButton()
        self._mask_ellipse_btn.setText("塗り丸")
        self._mask_ellipse_btn.setToolTip(
            "ドラッグで塗りつぶし用の楕円を描きます(印影等にも使えます)。"
        )
        self._mask_ellipse_btn.setCheckable(True)
        self._mask_ellipse_btn.toggled.connect(self.mask_ellipse_tool_toggled.emit)
        manual_layout.addWidget(self._mask_ellipse_btn)
        panel_layout.addWidget(manual_group)

        # --- エンティティ種別フィルタ ---
        filter_group = QGroupBox("表示するエンティティ種別")
        filter_layout = QVBoxLayout(filter_group)
        self._filter_checks: dict[str, QCheckBox] = {}
        for entity_type in ENTITY_TYPES:
            checkbox = QCheckBox(get_entity_type_name_ja(entity_type))
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._on_filter_toggled)
            filter_layout.addWidget(checkbox)
            self._filter_checks[entity_type] = checkbox
        panel_layout.addWidget(filter_group)

        # --- 結果一覧 ---
        panel_layout.addWidget(QLabel("検出結果・塗りつぶし対象"))
        self._result_tree = QTreeWidget()
        self._result_tree.setColumnCount(3)
        self._result_tree.setHeaderLabels(["語句", "種別", "ページ"])
        self._result_tree.setRootIsDecorated(False)
        self._result_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._result_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._result_tree.itemClicked.connect(self._on_result_item_clicked)
        self._result_tree.itemSelectionChanged.connect(self._update_button_states)
        self._result_tree.customContextMenuRequested.connect(self._on_result_context_menu)
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

    def enabled_entity_filter(self) -> set[str]:
        """フィルタで表示対象になっているエンティティ種別の集合を返す。"""
        return {et for et, box in self._filter_checks.items() if box.isChecked()}

    def set_mask_markup_tool_active(self, enabled: bool) -> None:
        """呼び出し側(mixin)の実際の作成モードにボタンのチェック状態を同期する。"""
        with self._signal_blockers(self._mask_markup_btn):
            self._mask_markup_btn.setChecked(bool(enabled))

    def set_mask_shape_tool_active(self, shape_type: "ShapeType | None") -> None:
        with self._signal_blockers(self._mask_rect_btn, self._mask_ellipse_btn):
            self._mask_rect_btn.setChecked(shape_type == ShapeType.RECTANGLE)
            self._mask_ellipse_btn.setChecked(shape_type == ShapeType.ELLIPSE)

    def set_results(self, rows: list[PiiResultRow]) -> None:
        """検出結果・塗りつぶし用図形の一覧を表示中のフィルタに従って再描画する。"""
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

    def _on_filter_toggled(self, _checked: bool) -> None:
        self._rebuild_result_tree()
        self.filter_changed.emit()

    def _rebuild_result_tree(self) -> None:
        self._result_tree.clear()
        visible_entities = self.enabled_entity_filter()
        for row in self._rows:
            if row.entity and row.entity not in visible_entities:
                continue
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
        chosen = menu.exec(self._result_tree.viewport().mapToGlobal(pos))
        if chosen is delete_same_action:
            self.delete_same_text_requested.emit(row.text)
        elif chosen is exclude_action:
            self.add_exclusion_requested.emit(row.entity, row.text)

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
