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

from PyQt6.QtCore import QEvent, Qt, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QKeySequence
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

# 塗りつぶし候補の表示モード(ズームビュー上の見た目のみ。PDFには影響しない)。
PII_DISPLAY_MODE_MARK = "mark"
PII_DISPLAY_MODE_BLACK = "black"
PII_DISPLAY_MODE_HIDDEN = "hidden"
PII_DISPLAY_MODES: tuple[tuple[str, str], ...] = (
    (PII_DISPLAY_MODE_MARK, "マーキング"),
    (PII_DISPLAY_MODE_BLACK, "黒塗り"),
    (PII_DISPLAY_MODE_HIDDEN, "非表示"),
)

_COLUMN_TO_SORT_FIELD = {0: "text", 1: "entity", 2: "page"}
_SORT_FIELD_TO_COLUMN = {v: k for k, v in _COLUMN_TO_SORT_FIELD.items()}


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
        「このページだけ検出」ボタン押下時。
    detect_all_pages_requested()
        「全ページを検出」ボタン押下時。
    mask_markup_tool_toggled(bool)
        「テキスト候補」ツール(手動でテキストを選択→塗りつぶし候補化)のON/OFF。
    mask_rect_tool_toggled(bool)
        「塗り四角」ツールのON/OFF。
    mask_ellipse_tool_toggled(bool)
        「塗り丸」ツールのON/OFF。
    remove_selected_requested()
        「選択を削除」ボタン押下時、または結果一覧でDeleteキー押下時。
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
    display_mode_changed(str)
        「表示」コンボで塗りつぶし候補の見た目を切り替えたとき
        ("mark" | "black" | "hidden")。
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
    display_mode_changed = pyqtSignal(str)

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
        # 検出範囲は左から「全ページ」→「このページだけ」の順に並べる。追加検出
        # パターン登録後の3択ダイアログ(全ページ/このページだけ/登録のみ)と
        # 表記を揃え、範囲の選び方を一貫させる。
        detect_row = QHBoxLayout()
        self._detect_all_btn = QPushButton("全ページを検出")
        self._detect_all_btn.setToolTip("すべてのページを個人情報検出します。")
        self._detect_all_btn.clicked.connect(self.detect_all_pages_requested.emit)
        detect_row.addWidget(self._detect_all_btn)
        self._detect_current_btn = QPushButton("このページだけ検出")
        self._detect_current_btn.setToolTip("表示中のページだけを個人情報検出します。")
        self._detect_current_btn.clicked.connect(self.detect_current_page_requested.emit)
        detect_row.addWidget(self._detect_current_btn)
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
        # 元の「検出結果・塗りつぶし対象」は何を指しているか分かりにくいとの
        # 要望を受け、一覧の実際の役割(自動検出+手動追加分をまとめた、
        # 「エクスポート時に塗りつぶされる対象そのもの」の一覧であり、
        # チェックボックスでの取捨選択は無い)が伝わる表記に変更した。
        result_label = QLabel("塗りつぶし対象一覧")
        result_label.setToolTip(
            "自動検出された箇所と手動で追加した候補/図形の一覧です。\n"
            "ここに表示されている項目はすべて塗りつぶし(黒塗り/文字削除)の対象になります。\n"
            "不要な項目は選択して「選択を削除」で一覧から外してください。"
        )
        panel_layout.addWidget(result_label)
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("表示:"))
        self._display_mode_combo = QComboBox()
        for mode, label in PII_DISPLAY_MODES:
            self._display_mode_combo.addItem(label, mode)
        self._display_mode_combo.setToolTip(
            "ページ上の塗りつぶし候補の見た目を切り替えます。\n"
            "マーキング: 種別色の薄い塗り+枠線(下の文字が読める)\n"
            "黒塗り: 黒で塗りつぶした仕上がりイメージ\n"
            "非表示: 候補を描かずに元のページを確認する\n"
            "(表示上の切り替えのみで、PDFやエクスポート結果は変わりません)"
        )
        self._display_mode_combo.currentIndexChanged.connect(self._on_display_mode_changed)
        display_row.addWidget(self._display_mode_combo, 1)
        panel_layout.addLayout(display_row)

        # 並び替えは列ヘッダのクリックで行う(同じ列の再クリックで昇順/降順を反転)。
        self._sort_field = "page"
        self._sort_ascending = True
        self._result_tree = _ResultTree()
        self._result_tree.setObjectName("piiResultTree")
        self._result_tree.setColumnCount(3)
        self._result_tree.setHeaderLabels(["語句", "種別", "ページ"])
        self._result_tree.setRootIsDecorated(False)
        self._result_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._result_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._result_tree.itemClicked.connect(self._on_result_item_clicked)
        self._result_tree.itemSelectionChanged.connect(self._update_button_states)
        self._result_tree.customContextMenuRequested.connect(self._on_result_context_menu)
        self._result_tree.delete_pressed.connect(self._on_result_delete_pressed)
        header = self._result_tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_result_header_clicked)
        self._update_sort_indicator()
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

    def display_mode(self) -> str:
        """塗りつぶし候補の表示モード("mark" | "black" | "hidden")を返す。"""
        data = self._display_mode_combo.currentData()
        return str(data) if data else PII_DISPLAY_MODE_MARK

    def set_display_mode(self, mode: str) -> None:
        """表示モードを設定する(シグナルは発火しない)。未知の値は「マーキング」扱い。"""
        idx = self._display_mode_combo.findData(mode)
        with self._signal_blockers(self._display_mode_combo):
            self._display_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)

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

    def set_sort(self, field: str, ascending: bool = True) -> None:
        """並び替えキー("page" | "text" | "entity")と方向を設定して再描画する。"""
        self._sort_field = field if field in _SORT_FIELD_TO_COLUMN else "page"
        self._sort_ascending = bool(ascending)
        self._update_sort_indicator()
        self._rebuild_result_tree()

    def _on_result_header_clicked(self, column: int) -> None:
        """列ヘッダクリックで並び替える。

        同じ列を続けてクリックしたときは昇順/降順を反転し、別の列をクリック
        したときはその列の昇順に切り替える。
        """
        field = _COLUMN_TO_SORT_FIELD.get(column, "page")
        if field == self._sort_field:
            self.set_sort(field, not self._sort_ascending)
        else:
            self.set_sort(field, True)

    def _update_sort_indicator(self) -> None:
        order = Qt.SortOrder.AscendingOrder if self._sort_ascending else Qt.SortOrder.DescendingOrder
        self._result_tree.header().setSortIndicator(_SORT_FIELD_TO_COLUMN[self._sort_field], order)

    def _on_display_mode_changed(self, _index: int) -> None:
        self.display_mode_changed.emit(self.display_mode())

    def _on_result_delete_pressed(self) -> None:
        if self._result_tree.selectedItems():
            self.remove_selected_requested.emit()

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


class _ResultTree(QTreeWidget):
    """結果一覧。Ctrl+A / Delete をウィンドウのショートカットより優先して受け取る。

    ページ編集ウィンドウには Ctrl+A(全ページ選択)・Delete(ページ削除)の
    ウィンドウショートカットがあり、そのままでは一覧にフォーカスがあっても
    そちらが発火してしまう。ShortcutOverride を受理して一覧側で処理する。
    """

    delete_pressed = pyqtSignal()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and (
            event.matches(QKeySequence.StandardKey.SelectAll)
            or event.matches(QKeySequence.StandardKey.Delete)
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Delete):
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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
