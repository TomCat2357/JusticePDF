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
from PyQt6.QtGui import QColor, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QSlider,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.pii.entity_types import (
    ENTITY_TYPES,
    ENTITY_TYPES_WITH_MANUAL,
    get_entity_type_name_ja,
)
from src.utils.pdf_utils import ShapeAnnotData, ShapeType, TextMarkupAnnotData

logger = logging.getLogger(__name__)

_ANNOT_ROLE = Qt.ItemDataRole.UserRole

_COLUMN_TO_SORT_FIELD = {0: "text", 1: "entity", 2: "page"}
_SORT_FIELD_TO_COLUMN = {v: k for k, v in _COLUMN_TO_SORT_FIELD.items()}

# 結果一覧をExcel等へコピーするときの見出し行(タブ区切り)。
_TSV_HEADER = "語句\t種別\tページ"


# 結果一覧の「語句」列に出す最大文字数(超えた分は「…」で省略)。
DISPLAY_TEXT_MAX_CHARS = 60


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
        """一覧に出す1行の語句(改行・連続空白は空白1つにまとめ、長ければ省略する)。

        保存済みのデータに改行が含まれていても、行が縦に伸びないようにする。
        """
        if self.text:
            single = " ".join(self.text.split())
            if single:
                if len(single) > DISPLAY_TEXT_MAX_CHARS:
                    return single[: DISPLAY_TEXT_MAX_CHARS - 1] + "…"
                return single
        return "[図形]" if self.kind == "shape" else "(テキストなし)"


def _tsv_cell(text: str) -> str:
    """TSVの1セル分に整形する(タブ・改行はセル/行を壊すため空白に置き換える)。"""
    return str(text).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def rows_to_tsv(rows: "list[PiiResultRow]") -> str:
    """結果行をExcel貼り付け用のTSV文字列にする(見出し行つき、ページは数字のみ)。"""
    lines = [_TSV_HEADER]
    for row in rows:
        lines.append(
            "\t".join(
                (
                    _tsv_cell(row.text or row.display_text),  # 省略せず全文
                    _tsv_cell(get_entity_type_name_ja(row.entity or "OTHER")),
                    str(row.page_num + 1),
                )
            )
        )
    return "\n".join(lines)


class ScopeChoiceDialog(QDialog):
    """「検出語に追加」「除外パターンに追加」の後に開く、範囲選択の小さなダイアログ。

    語句(読み取り専用)と説明文を示し、「全ページ / このページだけ / しない」の
    3択(ボタン文言は呼び出し側が指定)から選ばせる。ダイアログを閉じた場合は
    「しない」扱い。
    """

    SCOPE_ALL = "all"
    SCOPE_PAGE = "page"
    SCOPE_NONE = "none"

    def __init__(
        self,
        title: str,
        message: str,
        word: str,
        labels: "tuple[str, str, str]",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._scope: str = self.SCOPE_NONE

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._text_edit = QLineEdit(word)
        self._text_edit.setReadOnly(True)
        form.addRow("語句:", self._text_edit)
        layout.addLayout(form)

        self._message_label = QLabel(message)
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        buttons = QHBoxLayout()
        self._all_btn = QPushButton(labels[0])
        self._page_btn = QPushButton(labels[1])
        self._none_btn = QPushButton(labels[2])
        for btn, scope in (
            (self._all_btn, self.SCOPE_ALL),
            (self._page_btn, self.SCOPE_PAGE),
            (self._none_btn, self.SCOPE_NONE),
        ):
            btn.clicked.connect(lambda _checked=False, sc=scope: self._choose(sc))
            buttons.addWidget(btn)
        self._all_btn.setDefault(True)
        layout.addLayout(buttons)

    def _choose(self, scope: str) -> None:
        self._scope = scope
        self.accept()

    def scope(self) -> str:
        return self._scope

    @staticmethod
    def ask(
        title: str,
        message: str,
        word: str,
        labels: "tuple[str, str, str]",
        parent: QWidget | None = None,
    ) -> str:
        """ダイアログを表示し、選ばれた範囲(``SCOPE_*``)を返す。閉じた場合は SCOPE_NONE。"""
        dialog = ScopeChoiceDialog(title, message, word, labels, parent)
        dialog.exec()
        return dialog.scope()


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
        「削除」ボタン押下時、または結果一覧でDeleteキー押下時。
    add_detect_word_requested(str, str)
        結果一覧の右クリックメニュー「検出語に追加」の種別サブメニュー選択時。
        (entity_type, text) を伴う。
    add_exclude_word_requested(str)
        結果一覧の右クリックメニュー「除外パターンに追加」。text を伴う。
    settings_requested()
        「設定...」ボタン押下時。
    export_requested()
        「エクスポート...」ボタン押下時(黒塗りしたPDF/画像の書き出し)。
    result_activated(object)
        結果一覧の項目がクリックされたとき、対応する注釈データを伴って発火
        (``TextMarkupAnnotData`` または ``ShapeAnnotData``)。
    entity_visibility_changed(str, bool)
        「表示・検出する種別」のチェックボックスが操作されたとき (entity_type, checked)。
    mask_color_changed(object)
        「色」ボタンでダイアログから色が選ばれたとき。RGB(0.0-1.0)の3要素タプル。
    mask_transparency_changed(int, bool)
        「透明度」スライダが動いたとき (値, 確定したか)。ドラッグ中は確定=False
        (画面の即時反映だけ行い、保存はドラッグ終了時にする)。
    """

    open_changed = pyqtSignal(bool)
    detect_current_page_requested = pyqtSignal()
    detect_all_pages_requested = pyqtSignal()
    keep_existing_toggled = pyqtSignal(bool)
    mask_markup_tool_toggled = pyqtSignal(bool)
    mask_rect_tool_toggled = pyqtSignal(bool)
    mask_ellipse_tool_toggled = pyqtSignal(bool)
    remove_selected_requested = pyqtSignal()
    add_detect_word_requested = pyqtSignal(str, str)
    add_exclude_word_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()
    export_requested = pyqtSignal()
    result_activated = pyqtSignal(object)
    entity_visibility_changed = pyqtSignal(str, bool)
    mask_color_changed = pyqtSignal(object)
    mask_transparency_changed = pyqtSignal(int, bool)

    DRAWER_WIDTH = 340

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("piiDrawer")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._is_open = False
        self._collapsed_width = 32
        self._rows: list[PiiResultRow] = []
        self._mask_color: tuple[float, float, float] = (0.0, 0.0, 0.0)

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
        # パターン登録ダイアログの3択(全ページ/このページだけ/登録のみ)と
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
        # 手動追加分の種別は常に「手動」(種別は選ばせない)。
        manual_group = QGroupBox("手動で塗りつぶし候補を追加")
        manual_layout = QVBoxLayout(manual_group)

        tools_row = QHBoxLayout()
        self._mask_markup_btn = QToolButton()
        self._mask_markup_btn.setText("テキスト候補")
        self._mask_markup_btn.setToolTip(
            "ページ上のテキストをドラッグ選択して塗りつぶし候補にします。\n"
            "先にテキストを選択してから押すと、その場で追加します。\n"
            "先に押すと連続モードになり、選択するたびに追加します(Esc で終了)。"
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
        # ボタンを押してもフォーカスを奪わない(ページ上で選択済みのテキスト選択や
        # キー操作(Esc)の受け取り先を変えないため)。
        for btn in (self._mask_markup_btn, self._mask_rect_btn, self._mask_ellipse_btn):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        manual_layout.addLayout(tools_row)
        panel_layout.addWidget(manual_group)

        # --- 結果一覧 ---
        # 元の「検出結果・塗りつぶし対象」は何を指しているか分かりにくいとの
        # 要望を受け、一覧の実際の役割(自動検出+手動追加分をまとめた、
        # 「エクスポート時に塗りつぶされる対象そのもの」の一覧)が伝わる表記に
        # 変更した。表示する種別は下の「表示・検出する種別」で絞り込める。
        result_label = QLabel("塗りつぶし対象一覧")
        result_label.setToolTip(
            "自動検出された箇所と手動で追加した候補/図形の一覧です。\n"
            "ここに表示されている項目はすべて塗りつぶし(黒塗り/文字削除)の対象になります。\n"
            "不要な項目は選択して「削除」で一覧から外してください。"
        )
        panel_layout.addWidget(result_label)

        # 種別ごとの「表示・検出する」チェックボックス(3列)。自動検出の8種別は
        # 検出対象の設定そのもの、「手動」は手動追加分の表示設定。
        entity_group = QGroupBox("表示・検出する種別")
        entity_group.setToolTip(
            "チェックした種別だけを、一覧・ページ上・エクスポートの対象にし、検出も行います。\n"
            "チェックを外した種別は一覧・ページ上に出ず、クリックもできず、"
            "検出・エクスポートの対象外になります(PDF上の注釈は消えません)。"
        )
        entity_grid = QGridLayout(entity_group)
        entity_grid.setContentsMargins(8, 4, 8, 4)
        self._entity_checks: dict[str, QCheckBox] = {}
        for i, entity_type in enumerate(ENTITY_TYPES_WITH_MANUAL):
            check = QCheckBox(get_entity_type_name_ja(entity_type))
            check.setChecked(True)
            check.toggled.connect(
                lambda checked, et=entity_type: self.entity_visibility_changed.emit(et, checked)
            )
            self._entity_checks[entity_type] = check
            entity_grid.addWidget(check, i // 3, i % 3)
        panel_layout.addWidget(entity_group)

        # 塗りつぶしの色(全種別共通)と透明度。
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("色:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(40, 22)
        self._color_btn.setToolTip("塗りつぶしの色を選びます(全種別共通・エクスポートにも使われます)。")
        self._color_btn.clicked.connect(self._on_color_button_clicked)
        style_row.addWidget(self._color_btn)
        style_row.addSpacing(8)
        style_row.addWidget(QLabel("透明度:"))
        self._transparency_slider = QSlider(Qt.Orientation.Horizontal)
        self._transparency_slider.setRange(0, 100)
        self._transparency_slider.setValue(70)
        self._transparency_slider.setToolTip(
            "0%: 不透明(黒塗りエクスポートと同じベタ塗り)/ 100%: 完全に透明\n"
            "(ページ上の見た目のみ。エクスポートは常に不透明で塗りつぶします)"
        )
        self._transparency_slider.valueChanged.connect(self._on_transparency_value_changed)
        self._transparency_slider.sliderReleased.connect(self._on_transparency_released)
        style_row.addWidget(self._transparency_slider, 1)
        self._transparency_label = QLabel("70%")
        self._transparency_label.setMinimumWidth(36)
        style_row.addWidget(self._transparency_label)
        panel_layout.addLayout(style_row)
        self._apply_color_swatch()

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
        self._result_tree.copy_pressed.connect(self.copy_results_to_clipboard)
        header = self._result_tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_result_header_clicked)
        self._update_sort_indicator()
        panel_layout.addWidget(self._result_tree, 1)

        # --- 操作ボタン ---
        # 全件削除は結果一覧で Ctrl+A → 「削除」で行える。
        # 削除・設定・エクスポートは1行に並べる(縦に3段積むと結果一覧が狭くなるため)。
        action_row = QHBoxLayout()
        self._remove_selected_btn = QPushButton("削除")
        self._remove_selected_btn.setToolTip("結果一覧で選択した項目を塗りつぶし対象から削除します。")
        self._remove_selected_btn.clicked.connect(self.remove_selected_requested.emit)
        action_row.addWidget(self._remove_selected_btn)

        self._settings_btn = QPushButton("設定...")
        self._settings_btn.clicked.connect(self.settings_requested.emit)
        action_row.addWidget(self._settings_btn)

        self._export_btn = QPushButton("エクスポート...")
        self._export_btn.setToolTip(
            "チェック中の種別の塗りつぶし対象を黒塗りし(下の文字も削除)、"
            "PDF・画像として書き出します。\n"
            "出力形式・解像度・圧縮などは、開くダイアログで選べます。"
        )
        self._export_btn.clicked.connect(self.export_requested.emit)
        action_row.addWidget(self._export_btn)
        panel_layout.addLayout(action_row)

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

    def set_status(self, status: str) -> None:
        """進捗表示を変えずにステータス行だけを書き換える。"""
        self._status_label.setText(status)

    def keep_existing_checked(self) -> bool:
        """「既存の結果を残して追加検出」チェックボックスの状態を返す。"""
        return self._keep_existing_check.isChecked()

    def set_keep_existing_checked(self, checked: bool) -> None:
        with self._signal_blockers(self._keep_existing_check):
            self._keep_existing_check.setChecked(bool(checked))

    # --- 種別チェックボックス ---
    def entity_visibility(self) -> dict[str, bool]:
        """種別 -> チェック状態(自動検出の8種別+手動)。"""
        return {et: check.isChecked() for et, check in self._entity_checks.items()}

    def set_entity_visibility(self, states: "dict[str, bool]") -> None:
        """設定値をチェックボックスへ反映する(シグナルは発火しない)。未指定の種別はオン。"""
        for entity_type, check in self._entity_checks.items():
            with self._signal_blockers(check):
                check.setChecked(bool(states.get(entity_type, True)))

    # --- 色・透明度 ---
    def mask_color(self) -> tuple[float, float, float]:
        return self._mask_color

    def mask_transparency(self) -> int:
        return int(self._transparency_slider.value())

    def set_mask_style(self, color: "tuple[float, float, float]", transparency: int) -> None:
        """色(RGB 0.0-1.0)と透明度(0-100)を反映する(シグナルは発火しない)。"""
        self._mask_color = (float(color[0]), float(color[1]), float(color[2]))
        self._apply_color_swatch()
        with self._signal_blockers(self._transparency_slider):
            self._transparency_slider.setValue(max(0, min(100, int(transparency))))
        self._transparency_label.setText(f"{self._transparency_slider.value()}%")

    # --- 手動ツール ---
    def set_manual_tools_enabled(self, enabled: bool) -> None:
        """手動追加ツール(テキスト候補/塗り四角/塗り丸)の有効/無効を切り替える。

        種別「手動」のチェックが外れている間は、追加しても見えないため無効にする。
        """
        enabled = bool(enabled)
        for btn in (self._mask_markup_btn, self._mask_rect_btn, self._mask_ellipse_btn):
            btn.setEnabled(enabled)

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

    def select_result(self, annot: object) -> bool:
        """``annot`` に対応する行だけを選択(アクティブ)にして、見える位置へスクロールする。

        ページ上で塗りつぶし候補をクリックしたときに、一覧の該当行へ追従させるために使う。
        一致は (種類, ページ, xref) で判定する。行が無ければ選択は変えず False を返す。
        ``result_activated`` は発火しない(ページ側の選択との往復を避ける)。
        """
        target = (type(annot), getattr(annot, "page_num", None), getattr(annot, "xref", None))
        tree = self._result_tree
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            row = item.data(0, _ANNOT_ROLE)
            if row is None:
                continue
            if (type(row.annot), row.annot.page_num, row.annot.xref) != target:
                continue
            tree.clearSelection()
            tree.setCurrentItem(item)  # 現在行+選択の両方を更新する
            tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            return True
        return False

    def copy_results_to_clipboard(self) -> None:
        """結果一覧をTSVとしてクリップボードへコピーする(Excelへそのまま貼り付け可)。

        選択行があれば選択行(画面上の並び順)、無ければ全行をコピーする。
        """
        rows = self._rows_for_copy()
        QGuiApplication.clipboard().setText(rows_to_tsv(rows))

    def _rows_for_copy(self) -> list[PiiResultRow]:
        tree = self._result_tree
        all_items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        selected = [item for item in all_items if item.isSelected()]
        items = selected or all_items
        rows: list[PiiResultRow] = []
        for item in items:
            row = item.data(0, _ANNOT_ROLE)
            if row is not None:
                rows.append(row)
        return rows

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

    def _apply_color_swatch(self) -> None:
        r, g, b = (max(0, min(255, round(c * 255))) for c in self._mask_color)
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r}, {g}, {b}); border: 1px solid palette(mid); }}"
        )

    def _on_color_button_clicked(self) -> None:
        r, g, b = (max(0, min(255, round(c * 255))) for c in self._mask_color)
        chosen = QColorDialog.getColor(QColor(r, g, b), self, "塗りつぶしの色")
        if not chosen.isValid():
            return
        self._mask_color = (chosen.redF(), chosen.greenF(), chosen.blueF())
        self._apply_color_swatch()
        self.mask_color_changed.emit(self._mask_color)

    def _on_transparency_value_changed(self, value: int) -> None:
        self._transparency_label.setText(f"{value}%")
        # ドラッグ中は画面の即時反映だけ(確定=False)。キー操作/クリックでの
        # 変更のようにドラッグでない場合はその場で確定として通知する。
        self.mask_transparency_changed.emit(int(value), not self._transparency_slider.isSliderDown())

    def _on_transparency_released(self) -> None:
        self.mask_transparency_changed.emit(int(self._transparency_slider.value()), True)

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
            if row.text and row.display_text != row.text:
                item.setToolTip(0, row.text)  # 省略・整形前の全文
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
        copy_action = menu.addAction("コピー")
        menu.addSeparator()
        # 検出語: 種別のサブメニューから選ぶ(手動追加分の行でも、語句があれば登録できる)。
        detect_menu = menu.addMenu("検出語に追加")
        detect_menu.setEnabled(bool(row.text))
        detect_actions: dict = {}
        for entity_type in ENTITY_TYPES:
            action = detect_menu.addAction(get_entity_type_name_ja(entity_type))
            detect_actions[action] = entity_type
        exclude_action = menu.addAction("除外パターンに追加")
        exclude_action.setEnabled(bool(row.text))
        chosen = menu.exec(self._result_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is copy_action:
            self.copy_results_to_clipboard()
        elif chosen is exclude_action:
            self.add_exclude_word_requested.emit(row.text)
        elif chosen in detect_actions:
            self.add_detect_word_requested.emit(detect_actions[chosen], row.text)

    def _update_button_states(self) -> None:
        has_results = self._result_tree.topLevelItemCount() > 0
        self._export_btn.setEnabled(has_results)
        self._remove_selected_btn.setEnabled(bool(self._result_tree.selectedItems()))


class _ResultTree(QTreeWidget):
    """結果一覧。Ctrl+A / Ctrl+C / Delete をウィンドウのショートカットより優先して受け取る。

    ページ編集ウィンドウには Ctrl+A(全ページ選択)・Delete(ページ削除)などの
    ウィンドウショートカットがあり、そのままでは一覧にフォーカスがあっても
    そちらが発火してしまう。ShortcutOverride を受理して一覧側で処理する。
    """

    delete_pressed = pyqtSignal()
    copy_pressed = pyqtSignal()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and (
            event.matches(QKeySequence.StandardKey.SelectAll)
            or event.matches(QKeySequence.StandardKey.Delete)
            or event.matches(QKeySequence.StandardKey.Copy)
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
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_pressed.emit()
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
