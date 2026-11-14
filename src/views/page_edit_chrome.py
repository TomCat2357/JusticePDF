"""個別画面（ページ一覧・拡大ビュー）の枠組み（PageEditWindow の mixin）。

Justice Desk（ONLINE 版）の個別画面に合わせた部分をここに集める:

- 窓の上端のモード帯（``ModeBar``）と、ページ一覧 / 拡大ビューそれぞれのツールバー
- 検索バー（``SearchBar``）との連携
- 右クリックメニュー（ページ・余白・拡大ビューの文字 / 注釈 / 何もない所）
- 選択モード（T / V / H）、サムネイルの大きさ、倍率メニュー
- 白紙挿入・抽出・エクスポートなどの操作

状態はすべて ``self``（PageEditWindow）に持つ。操作の名前・アイコン・キー表記は ``ops.OPS`` を
使い、ツールバーと右クリックで同じ QAction を共有する。
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QPoint, QSignalBlocker, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QIntValidator, QKeySequence, QPainter
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolBar,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from src.utils.path_utils import ensure_unique_path
from src.utils.pdf_utils import (
    MarkupType,
    extract_pages,
    get_page_count,
    get_pdf_metadata_title,
    remove_pages,
)
from src.utils.pdf_utils.pages import insert_blank_pages
from src.views import icons
from src.views.context_menu import MENU_ICON_COLOR, MENU_ICON_SIZE, build_context_menu
from src.views.mode_bar import ModeBar
from src.views.ops import OPS, make_action, make_tool_button, tooltip_text

logger = logging.getLogger(__name__)

# サムネイルの大きさ（Justice Desk と同じ 4 段階）。
THUMB_PRESETS: tuple[tuple[str, int], ...] = (("小", 110), ("中", 170), ("大", 250), ("特大", 360))
THUMB_DEFAULT = 170
# 白紙挿入（表示名, 用紙, 横向きか）
BLANK_CHOICES: tuple[tuple[str, str, bool], ...] = (
    ("A4 縦", "A4", False),
    ("A4 横", "A4", True),
    ("A3 縦", "A3", False),
    ("A3 横", "A3", True),
)
DOC_NAME_MAX_WIDTH = 224  # 14rem
ZOOM_PAGE_EDIT_WIDTH = 48


def format_page_ranges(pages_one_based) -> str:
    """ページ番号（1 始まり）を ``1-3,5`` の形にまとめる（抽出のファイル名用）。"""
    nums = sorted(set(int(p) for p in pages_one_based))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(parts)


class _MenuToolButton(QToolButton):
    """アイコン + ▼ の横長アイコンを持つボタン。

    QToolButton は親が QToolBar のとき、自分の iconSize ではなくツールバーの iconSize で
    アイコンを描く（横長アイコンが縮んでしまう）。ここでは自分の iconSize を使う。
    """

    def initStyleOption(self, option) -> None:  # noqa: N802 - Qt override
        super().initStyleOption(option)
        option.iconSize = self.iconSize()


class _TextMenuButton(QToolButton):
    """文字 + 小さな ▼ のボタン（倍率の「100%」）。"""

    CHEVRON = 12

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        color = "#334155" if self.isEnabled() else "#94a3b8"
        pm = icons.pixmap("ChevronDown", color, self.CHEVRON, self.devicePixelRatioF())
        painter = QPainter(self)
        try:
            painter.drawPixmap(self.width() - self.CHEVRON - 8, (self.height() - self.CHEVRON) // 2, pm)
        finally:
            painter.end()


def _menu_icon(name: str):
    return icons.icon(name, MENU_ICON_COLOR, size=MENU_ICON_SIZE)


class PageEditChromeMixin:
    """PageEditWindow の画面まわり（モード帯・ツールバー・メニュー・選択モード・各種操作）。"""

    # ------------------------------------------------------------------
    # 組み立て
    # ------------------------------------------------------------------
    def _build_panel_actions(self) -> None:
        """パネル（注釈 / しおり / 個人情報 / OCR）の QAction。ページ一覧と拡大ビューのメニューで共有する。"""
        self._zoom_panel_group = QActionGroup(self)
        self._zoom_panel_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        rows = (
            ("_zoom_object_btn", "注釈", "付箋・図形・文字などの注釈", "Alt+1", self._on_zoom_panel_annotation_triggered),
            ("_zoom_bookmark_action", "しおり", "しおり編集", "Alt+2", self._on_zoom_panel_bookmarks_triggered),
            ("_zoom_pii_btn", "墨消し", "個人情報(PII)の検出・ハイライト・黒塗りエクスポート", "Alt+3", self._on_zoom_panel_pii_triggered),
            ("_zoom_ocr_action", "OCR", "文字認識(OCR)して、検索・個人情報検出で使えるテキストを埋め込む", "Alt+4", self._on_zoom_panel_ocr_triggered),
        )
        for attr, text, tip, keys, handler in rows:
            action = QAction(text, self)
            action.setToolTip(tip)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(handler)
            self._zoom_panel_group.addAction(action)
            self.addAction(action)  # ウィンドウのどこからでも Alt+1〜4 が効く
            setattr(self, attr, action)
        # 旧しおりボタンの属性名も QAction へ向ける。
        self._zoom_bookmark_btn = self._zoom_bookmark_action
        self._panel_toggle_action = QAction(self)
        self._panel_toggle_action.setShortcut(QKeySequence("Alt+P"))
        self._panel_toggle_action.triggered.connect(lambda *_: self._toggle_panels())
        self.addAction(self._panel_toggle_action)
        self._last_panel_action = self._zoom_object_btn

    def _toggle_panels(self) -> None:
        """Alt+P: 開いているパネルを閉じる。全部閉じていれば最後に開いていたパネルを開く。"""
        actions = list(self._zoom_panel_group.actions())
        opened = [a for a in actions if a.isChecked()]
        if opened:
            self._last_panel_action = opened[0]
            opened[0].setChecked(False)
            self._apply_panel_action(opened[0], False)
            return
        target = self._last_panel_action
        if not target.isEnabled():
            return
        target.setChecked(True)
        self._apply_panel_action(target, True)

    def _apply_panel_action(self, action: QAction, checked: bool) -> None:
        handler = {
            self._zoom_object_btn: self._on_zoom_panel_annotation_triggered,
            self._zoom_bookmark_action: self._on_zoom_panel_bookmarks_triggered,
            self._zoom_pii_btn: self._on_zoom_panel_pii_triggered,
            self._zoom_ocr_action: self._on_zoom_panel_ocr_triggered,
        }[action]
        handler(checked)

    def _build_chrome_actions(self) -> None:
        """ツールバー・右クリックで共有する QAction を作る。"""
        S = lambda f: (lambda *_: f())  # noqa: E731 - triggered の checked 引数を捨てる
        self._act_to_list = make_action(self, "toList", S(self._exit_zoom_view))
        self._act_back = make_action(self, "back", S(self._on_back_to_library), label="戻る")
        self._act_back.setToolTip("戻る（フォルダの一覧へ）")
        # 保存。手動保存モードのときだけ見せる（有効条件は _update_save_button）。
        self._save_btn = make_action(self, "save", S(self._on_save))
        self._save_btn.setToolTip("編集内容をファイルへ保存 (Ctrl+S)")
        self._save_action = self._save_btn
        self._save_widget_actions: list[QAction] = []
        self._select_all_btn = make_action(self, "selectall", S(self._on_select_all))
        self._select_all_btn.setToolTip("すべて選択 (Ctrl+A)")
        self._delete_btn = make_action(self, "delete", S(self._on_delete))
        self._delete_btn.setToolTip("選択したページを削除 (Delete)")
        # 拡大ビューの削除は「表示中の 1 ページ」。Delete キーは注釈の削除に使うのでキー表記を出さない。
        self._zoom_delete_action = make_action(self, "delete", S(self._on_delete))
        self._zoom_delete_action.setToolTip("表示中のページを削除")
        self._extract_action = make_action(self, "extract", S(self._on_extract))
        self._extract_action.setToolTip("抽出（選択したページから新しい PDF を作る）")
        self._export_action = make_action(self, "export", S(self._on_export_file))
        self._export_action.setToolTip("エクスポート (Ctrl+E)")
        self._print_btn = make_action(self, "print", S(self._on_print))
        self._print_btn.setToolTip("印刷 (Ctrl+P)")
        self._search_btn = make_action(self, "find", None, label="検索", checkable=True)
        self._search_btn.setToolTip("PDF 内のテキストを検索 (Ctrl+F)")
        self._search_btn.triggered.connect(lambda checked=False: self._on_search_action(checked))
        self._pii_action = make_action(self, "pii", S(self._on_open_pii_panel))
        self._pii_action.setToolTip("個人情報をチェック")
        # 元に戻す・やり直し: ツールバーには出さない（余白の右クリックにだけ出す）。
        # ショートカットと有効条件は従来どおり（_setup_shortcuts / _update_button_states）。
        self._undo_btn = make_action(self, "undo", S(self._on_undo))
        self._redo_btn = make_action(self, "redo", S(self._on_redo))
        self._deselect_action = make_action(self, "deselect", S(self._clear_selection))
        # 回転
        self._rotate_actions = {
            90: make_action(self, "rotateRight", S(lambda: self._rotate_pages_by(90))),
            270: make_action(self, "rotateLeft", S(lambda: self._rotate_pages_by(270))),
            180: make_action(self, "rotate180", S(lambda: self._rotate_pages_by(180))),
        }
        # 白紙挿入
        self._blank_actions: list[QAction] = []
        for label, size, landscape in BLANK_CHOICES:
            a = QAction(icons.icon("FilePlus", MENU_ICON_COLOR, size=MENU_ICON_SIZE), label, self)
            a.triggered.connect(lambda *_, s=size, l=landscape: self._insert_blank(s, l))
            self._blank_actions.append(a)
        # 名前変更
        self._rename_file_action = QAction("ファイル名\tF2", self)
        self._rename_file_action.triggered.connect(S(self._on_rename))
        self._rename_title_action = QAction("PDF名\tShift+F2", self)
        self._rename_title_action.triggered.connect(S(self._on_rename_pdf_title))
        self._menu_buttons: dict[str, list[QToolButton]] = {"rotate": [], "blank": [], "rename": []}

    # ---- 部品 --------------------------------------------------------
    def _add_button(self, toolbar: QToolBar, action: QAction, variant: str) -> QToolButton:
        btn = make_tool_button(action, variant=variant)
        wa = toolbar.addWidget(btn)
        if action is self._save_btn:
            self._save_widget_actions.append(wa)
            wa.setVisible(self._session is not None)
        return btn

    @staticmethod
    def _menu_button(icon: str, tip: str, menu: QMenu, variant: str) -> QToolButton:
        """アイコン + ▼ のメニューボタン（ツールバーに直接置く）。"""
        btn = _MenuToolButton()
        btn.setProperty("variant", variant)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoRaise(True)
        px = 16 if variant == "box" else 17
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setIconSize(QSize(*icons.chevron_icon_size(px)))
        btn.setIcon(icons.icon_with_chevron(icon, size=px))
        btn.setToolTip(tip)
        return btn

    def _add_menu_button(self, toolbar: QToolBar, key: str, icon: str, tip: str, menu: QMenu, variant: str) -> QToolButton:
        btn = self._menu_button(icon, tip, menu, variant)
        toolbar.addWidget(btn)
        self._menu_buttons.setdefault(key, []).append(btn)
        return btn

    def _rotate_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        for angle in (90, 270, 180):
            menu.addAction(self._rotate_actions[angle])
        return menu

    def _blank_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        for a in self._blank_actions:
            menu.addAction(a)
        return menu

    def _rename_menu_new(self, parent) -> QMenu:
        menu = QMenu(parent)
        menu.addAction(self._rename_file_action)
        menu.addAction(self._rename_title_action)
        return menu

    def _panel_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        menu.setToolTipsVisible(True)
        for action in (
            self._zoom_object_btn,
            self._zoom_bookmark_action,
            self._zoom_pii_btn,
            self._zoom_ocr_action,
        ):
            menu.addAction(action)
        menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self._sync_zoom_panel_button))
        return menu

    def _make_spacer(self) -> QWidget:
        spacer = QWidget()
        spacer.setObjectName("toolbarSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return spacer

    # ------------------------------------------------------------------
    # モード帯・ツールバー
    # ------------------------------------------------------------------
    def _setup_toolbar(self) -> None:
        self._build_chrome_actions()
        self._setup_mode_bar()
        self._setup_grid_toolbar()
        self._setup_zoom_toolbar()
        self._apply_chrome_mode()
        self._apply_panel_context()
        self._sync_zoom_panel_button()
        self._refresh_toolbar_info()
        self._set_save_action_visible(self._session is not None)
        self._update_button_states()

    def _setup_mode_bar(self) -> None:
        bar = ModeBar(self)
        # 戻る・進む・上へは帯では出さない（ツールバーの「戻る」を使う）。
        for btn in (bar._back, bar._forward, bar._up):
            btn.hide()
        bar.reload_requested.connect(self.refresh_from_disk)
        bar.crumb_requested.connect(self._on_crumb_requested)
        self._mode_bar = bar
        self.setMenuWidget(bar)
        self._update_crumbs()

    def _update_crumbs(self) -> None:
        folder = os.path.dirname(os.path.abspath(self._pdf_path))
        folder_name = os.path.basename(folder) or folder
        self._mode_bar.set_crumbs([(folder_name, "folder"), (os.path.basename(self._pdf_path), None)])

    def _on_crumb_requested(self, payload) -> None:
        if payload == "folder":
            self._on_back_to_library()

    def _on_back_to_library(self) -> None:
        """窓を閉じてメイン窓を前に出す。未保存の確認は closeEvent に任せる。"""
        owner = self.parent()
        if not self.close():
            return
        if owner is not None:
            owner.show()
            owner.raise_()
            owner.activateWindow()

    def _setup_grid_toolbar(self) -> None:
        tb = QToolBar()
        tb.setObjectName("pageEditToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(17, 17))
        self.addToolBar(tb)
        self._grid_toolbar = tb
        v = "flat"

        self._add_button(tb, self._act_back, v)
        self._add_button(tb, self._save_btn, v)
        tb.addSeparator()
        self._add_button(tb, self._select_all_btn, v)
        self._add_button(tb, self._delete_btn, v)
        self._rotate_btn = self._add_menu_button(tb, "rotate", "RotateCw", "回転 (R)", self._rotate_menu(tb), v)
        self._add_menu_button(tb, "blank", "FilePlus", "白紙挿入（選択ページの後ろ、未選択なら末尾）", self._blank_menu(tb), v)
        self._add_button(tb, self._extract_action, v)
        self._add_button(tb, self._export_action, v)
        self._add_button(tb, self._print_btn, v)
        self._rename_btn = self._add_menu_button(tb, "rename", "Pencil", "名前変更 (F2)", self._rename_menu_new(tb), v)
        tb.addSeparator()
        self._add_button(tb, self._search_btn, v)
        self._add_button(tb, self._pii_action, v)
        self._grid_panel_menu = self._panel_menu(tb)
        self._grid_panel_btn = self._menu_button("PanelRight", "パネル (Alt+P)", self._grid_panel_menu, v)
        self._grid_panel_action = tb.addWidget(self._grid_panel_btn)

        # 右寄せ: サムネイルの大きさ・ファイル名・ページ数
        tb.addWidget(self._make_spacer())
        self._thumb_menu = QMenu(tb)
        self._thumb_actions: dict[int, QAction] = {}
        for label, size in THUMB_PRESETS:
            a = QAction(f"{label}（{size}px）", self._thumb_menu)
            a.setCheckable(True)
            a.triggered.connect(lambda *_, s=size: self._set_thumbnail_size(s))
            self._thumb_menu.addAction(a)
            self._thumb_actions[size] = a
        self._thumb_menu.aboutToShow.connect(self._sync_thumb_menu)
        self._thumb_btn = self._menu_button(
            "MoveDiagonal", "サムネイルの大きさ (Ctrl+ホイール)", self._thumb_menu, v
        )
        tb.addWidget(self._thumb_btn)
        self._doc_name_label = QLabel()
        self._doc_name_label.setObjectName("pageEditDocName")
        tb.addWidget(self._doc_name_label)
        self._page_count_label = QLabel()
        self._page_count_label.setObjectName("pageEditPageCount")
        tb.addWidget(self._page_count_label)

    def _sync_thumb_menu(self) -> None:
        for size, action in self._thumb_actions.items():
            action.setChecked(size == self._preferred_thumb_size)

    def _setup_zoom_toolbar(self) -> None:
        tb = QToolBar()
        tb.setObjectName("pageEditZoomToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        self._zoom_toolbar = tb
        v = "box"

        self._add_button(tb, self._act_to_list, v)
        self._add_button(tb, self._save_btn, v)
        tb.addSeparator()

        # ページ移動: ◁ [ 3 ] / N ▷
        self._zoom_prev_btn = make_tool_button(
            icon="ChevronLeft", tooltip=tooltip_text(OPS["pagePrev"].label, OPS["pagePrev"].keys), variant=v
        )
        self._zoom_prev_btn.clicked.connect(lambda *_: self._on_zoom_prev_page())
        tb.addWidget(self._zoom_prev_btn)
        self._zoom_page_edit = QLineEdit()
        self._zoom_page_edit.setObjectName("zoomPageEdit")
        self._zoom_page_edit.setFixedWidth(ZOOM_PAGE_EDIT_WIDTH)
        self._zoom_page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_page_edit.setValidator(QIntValidator(1, 999999, self))
        self._zoom_page_edit.setToolTip("ページ番号（Enter で移動）")
        self._zoom_page_edit.editingFinished.connect(self._on_zoom_page_edited)
        tb.addWidget(self._zoom_page_edit)
        self._zoom_page_label = QLabel("")
        self._zoom_page_label.setObjectName("zoomPageTotal")
        tb.addWidget(self._zoom_page_label)
        self._zoom_next_btn = make_tool_button(
            icon="ChevronRight", tooltip=tooltip_text(OPS["pageNext"].label, OPS["pageNext"].keys), variant=v
        )
        self._zoom_next_btn.clicked.connect(lambda *_: self._on_zoom_next_page())
        tb.addWidget(self._zoom_next_btn)
        tb.addSeparator()

        # 倍率「100%」▼
        self._zoom_reset_btn = _TextMenuButton()
        self._zoom_reset_btn.setObjectName("zoomPercent")
        self._zoom_reset_btn.setProperty("variant", "box")
        self._zoom_reset_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._zoom_reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._zoom_reset_btn.setAutoRaise(True)
        self._zoom_reset_btn.setText("100%")
        self._zoom_reset_btn.setToolTip("倍率")
        self._zoom_reset_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._zoom_reset_btn.setMenu(self._build_zoom_percent_menu(self._zoom_reset_btn))
        tb.addWidget(self._zoom_reset_btn)

        # 表示（1枚 / 横2枚 / 縦2枚 / 4枚）
        self._zoom_layout_menu = QMenu(tb)
        for layout in type(self._zoom_page_layout):
            action = self._zoom_layout_menu.addAction(layout.label)
            action.setCheckable(True)
            action.setToolTip("通常表示・編集可" if layout.page_capacity == 1 else "閲覧専用")
            action.triggered.connect(lambda *_, selected=layout: self._set_zoom_page_layout(selected))
            self._zoom_layout_actions[layout] = action
        self._zoom_layout_menu.setToolTipsVisible(True)
        self._zoom_spread_btn = self._menu_button("LayoutGrid", "表示（ページの並べ方）", self._zoom_layout_menu, v)
        tb.addWidget(self._zoom_spread_btn)
        tb.addSeparator()

        # 選択モードの 3 連ボタン
        holder = QWidget()
        holder.setObjectName("selectGroup")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        self._select_buttons: dict[str, QToolButton] = {}
        segs = (("text", "selText", "first"), ("obj", "selObj", "mid"), ("hand", "selHand", "last"))
        for mode, op_id, seg in segs:
            op = OPS[op_id]
            b = make_tool_button(icon=op.icon, tooltip=tooltip_text(op.label, op.keys), variant=v)
            b.setCheckable(True)
            b.setProperty("seg", seg)
            b.clicked.connect(lambda checked=False, m=mode: self._set_select_mode(m if checked else None))
            hl.addWidget(b)
            self._select_buttons[mode] = b
        tb.addWidget(holder)
        tb.addSeparator()

        self._add_button(tb, self._zoom_delete_action, v)
        self._add_menu_button(tb, "rotate", "RotateCw", "回転 (R)", self._rotate_menu(tb), v)
        self._add_button(tb, self._extract_action, v)
        self._add_button(tb, self._export_action, v)
        self._add_button(tb, self._print_btn, v)
        self._add_menu_button(tb, "rename", "Pencil", "名前変更 (F2)", self._rename_menu_new(tb), v)
        tb.addSeparator()
        self._add_button(tb, self._search_btn, v)
        self._add_button(tb, self._pii_action, v)
        self._zoom_panel_menu = self._panel_menu(tb)
        self._zoom_panel_btn = self._menu_button("PanelRight", "パネル (Alt+P)", self._zoom_panel_menu, v)
        tb.addWidget(self._zoom_panel_btn)

    def _build_zoom_percent_menu(self, parent) -> QMenu:
        """倍率メニュー: 倍率の入力欄 / 縮小・拡大 / 25〜400% / 幅・高さに合わせる。"""
        menu = QMenu(parent)

        def item(op_id: str, slot) -> QAction:
            op = OPS[op_id]
            a = QAction(_menu_icon(op.icon), f"{op.label}\t{op.keys}" if op.keys else op.label, menu)
            a.triggered.connect(lambda *_: slot())
            menu.addAction(a)
            return a

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 4, 12, 4)
        rl.addWidget(QLabel("倍率"))
        self._zoom_percent_spin = QSpinBox()
        self._zoom_percent_spin.setRange(self.ZOOM_MIN, self.ZOOM_MAX)
        self._zoom_percent_spin.setSuffix(" %")
        self._zoom_percent_spin.setKeyboardTracking(False)
        self._zoom_percent_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._zoom_percent_spin.setFixedWidth(72)
        self._zoom_percent_spin.editingFinished.connect(self._on_zoom_percent_entered)
        rl.addWidget(self._zoom_percent_spin)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(row)
        menu.addAction(wa)
        item("zoomOut", self._on_zoom_out)
        item("zoomIn", self._on_zoom_in)
        menu.addSeparator()
        self._zoom_preset_actions: dict[int, QAction] = {}
        for preset in self.ZOOM_PRESETS:
            a = menu.addAction(f"{preset}%")
            a.setCheckable(True)
            a.triggered.connect(lambda *_, p=preset: self._set_zoom_percent(p))
            self._zoom_preset_actions[preset] = a
        menu.addSeparator()
        a = QAction("幅に合わせる\tCtrl+\\", menu)
        a.triggered.connect(lambda *_: self._fit_zoom("width"))
        menu.addAction(a)
        a = QAction("高さに合わせる", menu)
        a.triggered.connect(lambda *_: self._fit_zoom("height"))
        menu.addAction(a)
        menu.aboutToShow.connect(self._sync_zoom_percent_menu)
        return menu

    def _sync_zoom_percent_menu(self) -> None:
        percent = round(self._zoom_factor * 100)
        with QSignalBlocker(self._zoom_percent_spin):
            self._zoom_percent_spin.setValue(percent)
        for preset, action in self._zoom_preset_actions.items():
            action.setChecked(preset == percent)

    def _on_zoom_percent_entered(self) -> None:
        self._set_zoom_percent(self._zoom_percent_spin.value())
        self._zoom_reset_btn.menu().close()
        self._focus_main_surface()

    # ---- 表示の切り替え ----------------------------------------------
    def _apply_chrome_mode(self) -> None:
        """ページ一覧 / 拡大ビューに合わせて、モード帯とツールバーを切り替える。"""
        zoom = self._zoom_view_shown()
        self._mode_bar.set_mode("zoom" if zoom else "pages")
        for btn in (self._mode_bar._back, self._mode_bar._forward, self._mode_bar._up):
            btn.hide()
        self._grid_toolbar.setVisible(not zoom)
        self._zoom_toolbar.setVisible(zoom)
        for action in getattr(self, "_zoom_key_actions", ()):
            action.setEnabled(zoom)

    def _focus_main_surface(self) -> None:
        if self._zoom_view_shown() and self._zoom_label is not None:
            self._zoom_label.setFocus()
        elif self._grid_scroll is not None:
            self._grid_scroll.setFocus()

    def _refresh_toolbar_info(self, reload_title: bool = False) -> None:
        """ファイル名（PDF 名が無ければファイル名。未保存なら ` *`）と「N ページ」。"""
        if reload_title or not hasattr(self, "_doc_title_cache"):
            title = ""
            # 重量文書は PDF 名を読むためだけに開かない（ファイル名を出す）。
            if reload_title and not self._is_heavy_document:
                try:
                    title = get_pdf_metadata_title(self._pdf_path) or ""
                except Exception:  # noqa: BLE001 - 表示用なので失敗しても続ける
                    logger.debug("PDF名を読めませんでした: %s", self._pdf_path, exc_info=True)
            self._doc_title_cache = title.strip() or os.path.basename(self._pdf_path)
        text = self._doc_title_cache + (" *" if self._session_dirty() else "")
        fm = self._doc_name_label.fontMetrics()
        self._doc_name_label.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, DOC_NAME_MAX_WIDTH))
        self._doc_name_label.setToolTip(text)
        self._page_count_label.setText(f"{self._page_count} ページ")

    def _set_save_action_visible(self, visible: bool) -> None:
        self._save_btn.setVisible(visible)
        for wa in self._save_widget_actions:
            wa.setVisible(visible)

    # ------------------------------------------------------------------
    # ページ番号・倍率
    # ------------------------------------------------------------------
    def _set_zoom_page_display(self, start: int, displayed: int, total: int) -> None:
        """拡大ビューのページ番号の欄と「/ N」（複数ページ表示は「-終わり / N」）。"""
        edit = getattr(self, "_zoom_page_edit", None)
        label = getattr(self, "_zoom_page_label", None)
        if edit is None or label is None:
            return
        with QSignalBlocker(edit):
            edit.setText(str(start + 1))
        if displayed <= 1:
            label.setText(f"/ {total}")
        else:
            label.setText(f"-{start + displayed} / {total}")

    def _on_zoom_page_edited(self) -> None:
        edit = self._zoom_page_edit
        try:
            target = int(edit.text())
        except ValueError:
            target = None
        current = (self._zoom_page_num or 0) + 1
        if target is not None and target != current:
            self._jump_zoom_to_page(target)
        elif self._zoom_page_num is not None:
            with QSignalBlocker(edit):
                edit.setText(str(current))
        if edit.hasFocus():
            self._focus_main_surface()  # Enter の後は T / V / H などのキーをキャンバスへ戻す

    def _fit_zoom(self, mode: str) -> None:
        """幅に合わせる / 高さに合わせる。"""
        if self._zoom_label is None or self._zoom_scroll is None:
            return
        page_w, page_h = self._zoom_label.page_size_points()
        if page_w <= 0 or page_h <= 0:
            return
        viewport = self._zoom_scroll.viewport()
        margin = 8
        avail_w = max(1, viewport.width() - margin)
        avail_h = max(1, viewport.height() - margin)
        scale = avail_w / page_w if mode == "width" else avail_h / page_h
        self._fit_mode_last = mode
        self._set_zoom_percent(int(scale * 100))

    def _toggle_fit_zoom(self) -> None:
        """Ctrl+\\: 幅に合わせる → 高さに合わせる を交互に。"""
        nxt = "height" if getattr(self, "_fit_mode_last", None) == "width" else "width"
        self._fit_zoom(nxt)

    def _reset_zoom_100(self) -> None:
        self._set_zoom_percent(100)

    # ------------------------------------------------------------------
    # サムネイルの大きさ
    # ------------------------------------------------------------------
    def _step_thumb_preset(self, direction: int) -> None:
        """Ctrl+ホイール: 小 / 中 / 大 / 特大 を 1 段ずつ。"""
        sizes = [s for _label, s in THUMB_PRESETS]
        cur = self._preferred_thumb_size
        if direction > 0:
            bigger = [s for s in sizes if s > cur]
            target = bigger[0] if bigger else sizes[-1]
        else:
            smaller = [s for s in sizes if s < cur]
            target = smaller[-1] if smaller else sizes[0]
        self._set_thumbnail_size(target)

    # ------------------------------------------------------------------
    # 選択モード（T / V / H）
    # ------------------------------------------------------------------
    def _set_select_mode(self, mode: str | None) -> None:
        mode = mode if mode in ("text", "obj", "hand") else None
        self._select_mode = mode
        for m, btn in getattr(self, "_select_buttons", {}).items():
            with QSignalBlocker(btn):
                btn.setChecked(m == mode)
        if self._zoom_label is not None:
            self._zoom_label.set_select_mode(mode)

    def _toggle_select_mode(self, mode: str) -> None:
        """T / V / H: もう一度押すと解除。"""
        self._set_select_mode(None if getattr(self, "_select_mode", None) == mode else mode)

    # ------------------------------------------------------------------
    # キー
    # ------------------------------------------------------------------
    @staticmethod
    def _text_input_has_focus() -> bool:
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)):
            return True
        return isinstance(w, QComboBox) and w.isEditable()

    def _unless_typing(self, handler):
        """入力欄に文字を打っている間は単キー（R・Delete・T・V・H…）を発火させない。"""
        def run() -> None:
            if not self._text_input_has_focus():
                handler()
        return run

    def _setup_chrome_shortcuts(self) -> None:
        """拡大ビューのときだけ有効にするキー（倍率・選択モード）。"""
        typing = self._unless_typing
        self._zoom_key_actions: list[QAction] = []
        rows = (
            (("Ctrl+0",), self._reset_zoom_100),
            (("Ctrl++", "Ctrl+=", "Ctrl+Shift+;"), self._on_zoom_in),
            (("Ctrl+-",), self._on_zoom_out),
            (("Ctrl+\\", "Ctrl+¥"), self._toggle_fit_zoom),
            (("T",), typing(lambda: self._toggle_select_mode("text"))),
            (("V",), typing(lambda: self._toggle_select_mode("obj"))),
            (("H",), typing(lambda: self._toggle_select_mode("hand"))),
            (("A",), typing(lambda: self._set_select_mode(None))),
        )
        for keys, handler in rows:
            action = QAction(self)
            action.setShortcuts([QKeySequence(k) for k in keys])
            action.triggered.connect(lambda *_, h=handler: h())
            action.setEnabled(False)
            self.addAction(action)
            self._zoom_key_actions.append(action)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _selected_or_current_pages(self) -> list[int]:
        """操作の対象ページ（0 始まり・昇順）。拡大ビューは表示中の 1 ページ。"""
        if self._zoom_view_shown():
            return [self._zoom_page_num] if self._zoom_page_num is not None else []
        return sorted(self._selected_pages)

    def _insert_blank(self, size: str, landscape: bool) -> None:
        """選択ページの後ろ（未選択なら末尾）に白紙を挿入する。"""
        if self._zoom_page_layout_is_multi():
            return
        if not self._ensure_saved("白紙の挿入"):
            return
        pdf_path = self._pdf_path
        count = get_page_count(pdf_path)
        selected = self._selected_or_current_pages()
        at = (max(selected) + 1) if selected else count
        at = min(at, count)

        def do_insert() -> None:
            insert_blank_pages(pdf_path, at, size, landscape)
            self._load_pages()

        def undo_insert() -> None:
            remove_pages(pdf_path, [at])
            self._load_pages()

        if self._push_undoable("Insert blank page", do_insert, undo_insert, writes_file=True):
            self._select_pages([at])
            if self._grid is not None:
                self._grid.scroll_to_page(at)

    def _on_extract(self) -> None:
        """選択ページから、同じフォルダに新しい PDF を作る（``<元>_pages_<範囲>.pdf``）。"""
        if self._zoom_page_layout_is_multi():
            return
        pages = self._selected_or_current_pages()
        if not pages:
            return
        if not self._ensure_saved("ページの抽出"):
            return
        stem = os.path.splitext(os.path.basename(self._pdf_path))[0]
        name = f"{stem}_pages_{format_page_ranges(p + 1 for p in pages)}.pdf"
        out = ensure_unique_path(os.path.dirname(os.path.abspath(self._pdf_path)), name)
        try:
            ok = extract_pages(self._pdf_path, str(out), pages)
        except Exception as error:  # noqa: BLE001
            self._handle_file_operation_error(error, self._pdf_path, "抽出")
            return
        if ok:
            self._flash_zoom_hint(f"抽出しました: {out.name}")

    def _on_export_file(self) -> None:
        """この 1 ファイルを既存のエクスポートダイアログでエクスポートする（薄いラッパ）。"""
        from src.views.export_dialog import ExportOptionsDialog

        if not self._ensure_saved("エクスポート"):
            return
        if not os.path.exists(self._pdf_path):
            return
        dialog = ExportOptionsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.get_options()
        dst_dir = QFileDialog.getExistingDirectory(self, "エクスポート先フォルダを選択")
        if not dst_dir:
            return
        jobs = [(self._pdf_path, os.path.basename(self._pdf_path))]
        if options["format"] == "pdf":
            self._export_as_pdf(
                jobs, dst_dir,
                optimize_level=options["pdf_optimize_level"],
                image_dpi=options["pdf_image_dpi"],
                image_quality=options["pdf_image_quality"],
                rasterize=options["rasterize"],
                rasterize_format=options["rasterize_format"],
            )
        else:
            self._export_as_images(
                jobs, dst_dir, options["format"],
                dpi=options["dpi"], quality=options["jpeg_quality"],
            )

    def _on_open_pii_panel(self) -> None:
        """ツールバーの「個人情報」: 個人情報パネルを開く（複数ページ表示中は閲覧専用なので開かない）。"""
        if self._zoom_page_layout_is_multi():
            return
        self._zoom_pii_btn.setChecked(True)
        self._on_zoom_panel_pii_triggered(True)

    def _add_bookmark_for_page(self, page: int | None = None) -> None:
        """しおりを追加する（しおりパネルを開き、既存のしおり追加の入力を始める）。

        page（0 始まり）を指定すると、ページ一覧ではそのページだけを選んでから追加する。
        """
        if page is not None and not self._zoom_view_shown():
            self._select_pages([page])
        panel = self._bookmarks_panel
        if not panel.is_open:
            self._zoom_bookmark_action.setChecked(True)
            self._on_zoom_panel_bookmarks_triggered(True)  # ディスク上のしおりを読んでから追加する
        panel._on_add()

    # ------------------------------------------------------------------
    # 検索バー
    # ------------------------------------------------------------------
    def _on_search_action(self, checked: bool) -> None:
        if checked:
            self._open_search_bar()
        elif self._search_bar is not None and self._search_bar.isVisible():
            self._search_bar.close_bar()

    def _open_search_bar(self, text: str | None = None) -> None:
        bar = self._search_bar
        bar.show()
        with QSignalBlocker(self._search_btn):
            self._search_btn.setChecked(True)
        if text:
            bar.set_text(text)
            bar.submit()
        bar.focus_input()

    def _on_search_bar_closed(self) -> None:
        with QSignalBlocker(self._search_btn):
            self._search_btn.setChecked(False)
        self._clear_search_highlights()
        self._focus_main_surface()

    # ------------------------------------------------------------------
    # 右クリックメニュー
    # ------------------------------------------------------------------
    @staticmethod
    def _ctx(op_id: str, slot, enabled: bool = True, label: str | None = None, keys="__op__") -> tuple:
        """OPS の定義から build_context_menu 用の項目を作る。"""
        op = OPS[op_id]
        return (op.group, op.icon, label if label is not None else op.label, slot,
                op.keys if keys == "__op__" else keys, enabled)

    def _show_menu(self, menu: QMenu, global_pos: QPoint) -> None:
        self._open_menu = menu  # 参照を保つ（テスト・確認用）
        menu.popup(global_pos)

    def _page_context_menu(self, page: int) -> QMenu:
        """ページ一覧のページの右クリック。未選択のページなら先にそれだけを選ぶ。"""
        if page not in self._selected_pages:
            self._select_pages([page])
        n = len(self._selected_pages)
        header = f"{page + 1} ページ目" if n == 1 else f"{n} ページ"
        spread = self._zoom_page_layout_is_multi()
        S = lambda f: (lambda *_: f())  # noqa: E731
        entries = [
            self._ctx("openZoom", S(lambda: self._open_zoom_view(page)), enabled=(n == 1), keys=None),
            self._ctx("rotateRight", S(lambda: self._rotate_pages_by(90))),
            self._ctx("rotateLeft", S(lambda: self._rotate_pages_by(270))),
            self._ctx("rotate180", S(lambda: self._rotate_pages_by(180))),
            self._ctx("bookmarkAdd", S(self._add_bookmark_for_page), enabled=(n == 1)),
            self._ctx("extract", S(self._on_extract), enabled=not spread),
            self._ctx("delete", S(self._on_delete)),
        ]
        return build_context_menu(self, header, entries)

    def _blank_context_menu(self) -> QMenu:
        """ページ一覧の余白の右クリック（見出しはファイル名）。"""
        S = lambda f: (lambda *_: f())  # noqa: E731
        entries = [
            self._ctx("selectall", S(self._on_select_all)),
            self._ctx("undo", S(self._on_undo), enabled=self._undo_btn.isEnabled()),
            self._ctx("redo", S(self._on_redo), enabled=self._redo_btn.isEnabled()),
            self._ctx("export", S(self._on_export_file)),
            self._ctx("print", S(self._on_print)),
            self._ctx("find", S(self._open_search_bar)),
        ]
        if self._selected_pages:
            entries.insert(1, self._ctx("deselect", S(self._clear_selection)))
        return build_context_menu(self, os.path.basename(self._pdf_path), entries)

    def _on_grid_context_requested(self, page: int, global_pos: QPoint) -> None:
        """グリッドの右クリック。page < 0 は余白。"""
        menu = self._page_context_menu(page) if page >= 0 else self._blank_context_menu()
        self._show_menu(menu, global_pos)

    def _zoom_context_menu(self, pos: QPoint) -> QMenu:
        """拡大ビューの右クリック（動かさずに離したとき）。選んだ文字 / 注釈 / 何もない所で出し分ける。"""
        label = self._zoom_label
        S = lambda f: (lambda *_: f())  # noqa: E731
        multi = self._zoom_page_layout_is_multi()
        page_no = (self._zoom_page_num or 0) + 1
        if label.text_selection_contains(pos):
            text = label.selected_glyph_text() or label.selected_text()
            markup = lambda t: S(lambda: self._create_markup_from_selection(t))  # noqa: E731
            entries = [
                self._ctx("copy", S(lambda: QApplication.clipboard().setText(text))),
                self._ctx("highlight", markup(MarkupType.HIGHLIGHT)),
                self._ctx("underline", markup(MarkupType.UNDERLINE)),
                self._ctx("strikeout", markup(MarkupType.STRIKEOUT)),
                self._ctx("bookmarkAdd", S(self._add_bookmark_for_page)),
                self._ctx("find", S(lambda: self._open_search_bar(text.strip()))),
            ]
            return build_context_menu(self, "選んだ文字", entries)
        annot = label.annotation_at(pos)
        if annot is not None:
            current = self._find_zoom_annotation(annot.xref) or annot
            self._set_selected_zoom_annotation(current)
            is_pii = bool(getattr(current, "pii_entity", None))
            copyable = type(current).__name__ in ("FreeTextAnnotData", "ShapeAnnotData") and not is_pii
            entries = [
                self._ctx(
                    "openPanel",
                    S(lambda: self._reveal_pii_result(current) if is_pii
                      else self._set_zoom_annotation_drawer_open(True)),
                    label="個人情報のパネルで開く" if is_pii else None,
                ),
                self._ctx("copy", S(lambda: self._on_zoom_annotation_copy_requested(current)),
                          enabled=copyable),
                self._ctx("duplicate", S(lambda: self._duplicate_zoom_annotation(current)), enabled=copyable),
                self._ctx("delete", S(self._delete_selected_zoom_annotation)),
            ]
            if not is_pii:
                entries.append(self._ctx("toFront", S(lambda: self._reorder_selected_zoom_annotation("front"))))
                entries.append(self._ctx("toBack", S(lambda: self._reorder_selected_zoom_annotation("back"))))
            return build_context_menu(self, "注釈", entries)
        entries = [
            self._ctx("undo", S(self._on_undo), enabled=self._undo_btn.isEnabled()),
            self._ctx("redo", S(self._on_redo), enabled=self._redo_btn.isEnabled()),
            self._ctx("rotateRight", S(lambda: self._rotate_pages_by(90)), enabled=not multi),
            self._ctx("rotateLeft", S(lambda: self._rotate_pages_by(270)), enabled=not multi),
            self._ctx("rotate180", S(lambda: self._rotate_pages_by(180)), enabled=not multi),
            self._ctx("bookmarkAdd", S(self._add_bookmark_for_page), enabled=not multi),
            self._ctx("extract", S(self._on_extract), enabled=not multi),
            self._ctx("export", S(self._on_export_file)),
            self._ctx("print", S(self._on_print)),
            self._ctx("find", S(self._open_search_bar)),
        ]
        return build_context_menu(self, f"{page_no} ページ目", entries)

    def _on_zoom_context_requested(self, pos: QPoint) -> None:
        self._show_menu(self._zoom_context_menu(pos), self._zoom_label.mapToGlobal(pos))

    def _duplicate_zoom_annotation(self, annot) -> None:
        """注釈を少しずらして複製する。"""
        dx = dy = 10.0
        x0, y0, x1, y1 = annot.rect
        vertices = getattr(annot, "vertices", None)
        new_vertices = None
        if vertices:
            new_vertices = tuple(tuple(v) for v in vertices)  # 頂点は矩形に対する割合なのでそのまま
        self._on_zoom_annotation_duplicate_requested(
            annot, (x0 + dx, y0 + dy, x1 + dx, y1 + dy), new_vertices
        )
