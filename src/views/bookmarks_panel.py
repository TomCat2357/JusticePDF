"""しおり(アウトライン/ブックマーク)編集パネル。

ページ編集画面のズームビュー右側にドロワーとして組み込む独立部品。
QTreeWidget で階層構造を編集し、変更があるたびに ``bookmarks_changed`` を
``list[TocEntry]`` 付きで発火する。永続化(PDFへの保存)や Undo 登録は
呼び出し側(page_edit_window)が担い、本パネルはそれらを一切知らない。
"""
from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.utils.pdf_utils import TocEntry

logger = logging.getLogger(__name__)

_PAGE_ROLE = Qt.ItemDataRole.UserRole
# ノード種別: None=しおり, "note_group"=付箋グループ, "note"=付箋
_NODE_KIND_ROLE = Qt.ItemDataRole.UserRole + 1
_XREF_ROLE = Qt.ItemDataRole.UserRole + 2

_MIN_ROW_HEIGHT = 24
_DEFAULT_MAX_PAGE = 99999
# グローバルQSS(QLineEdit の padding 6px 8px 等)でインライン編集欄が潰れないよう、
# エディタ個別に上書きする。
_EDITOR_STYLE = (
    "{sel} {{ padding: 0 2px; margin: 0; border: 1px solid #4f46e5;"
    " border-radius: 0; background-color: #ffffff; }}"
)


class _BookmarkDelegate(QStyledItemDelegate):
    """しおりツリー用デリゲート。行高の確保と、列ごとのインライン編集欄を提供する。

    列0=タイトル(QLineEdit)、列1=ページ(QSpinBox, 1..ページ数)。
    """

    def __init__(self, panel: "BookmarksPanel") -> None:
        super().__init__(panel._tree)
        self._panel = panel

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), _MIN_ROW_HEIGHT))

    def createEditor(self, parent, option, index):
        if index.column() == 1:
            spin = QSpinBox(parent)
            spin.setRange(1, self._panel._max_page())
            spin.setStyleSheet(_EDITOR_STYLE.format(sel="QSpinBox"))
            spin.setMinimumHeight(_MIN_ROW_HEIGHT)
            return spin
        edit = QLineEdit(parent)
        edit.setStyleSheet(_EDITOR_STYLE.format(sel="QLineEdit"))
        edit.setMinimumHeight(_MIN_ROW_HEIGHT)
        return edit

    def setEditorData(self, editor, index) -> None:
        if isinstance(editor, QSpinBox):
            try:
                editor.setValue(int(index.data(Qt.ItemDataRole.DisplayRole)))
            except (TypeError, ValueError):
                editor.setValue(1)
            editor.selectAll()
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:
        if isinstance(editor, QSpinBox):
            editor.interpretText()
            model.setData(index, str(editor.value()), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class BookmarksPanel(QFrame):
    """しおり編集ドロワー。

    Signals
    -------
    bookmarks_changed(list, str)
        ツリーが編集されるたびに ``(list[TocEntry], 操作の説明)`` を発火。
    jump_requested(int)
        しおりがクリックされたとき、ジャンプ先ページ(1始まり)を発火。
    open_changed(bool)
        ドロワーの開閉が切り替わったとき発火。
    """

    bookmarks_changed = pyqtSignal(list, str)
    jump_requested = pyqtSignal(int)
    note_jump_requested = pyqtSignal(int, int)  # (page 1始まり, 付箋 xref)
    open_changed = pyqtSignal(bool)
    # タイトル編集(追加/改名)が閉じたとき発火。編集中に見送った再読込のきっかけに使う。
    editing_finished = pyqtSignal()

    DRAWER_WIDTH = 320

    def __init__(
        self,
        parent: QWidget | None = None,
        page_count_provider: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("bookmarksDrawer")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._is_open = False
        self._collapsed_width = 32
        self._loading = False
        self._suppress_item_changed = False
        # 閲覧専用(見開き表示)モード。True の間は編集系ボタンを全て無効化し、
        # ジャンプ/閲覧のみ可能にする。
        self._read_only = False
        self._current_page_provider: Callable[[], int | None] | None = None
        self._page_count_provider = page_count_provider
        # 現在ページが存在するか(無ければ追加ボタンを無効化)。read_only が優先。
        self._current_page_available = True
        # 付箋一覧（page 1始まり, xref, 冒頭テキスト）。set_annotation_notes で更新。
        self._notes: list[tuple[int, int, str]] = []
        # 「追加」直後でタイトル編集中の未確定項目。確定(Enter/フォーカス喪失)で
        # 1回だけ「しおり追加」を発火し、Esc なら取り除いて何も発火しない。
        self._pending_item: QTreeWidgetItem | None = None
        # 追加前に選択されていた項目(Esc で取り消すときに選択を戻す)。
        self._pending_prev: QTreeWidgetItem | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("◀")
        self._toggle_btn.setToolTip("しおり編集")
        self._toggle_btn.setFixedWidth(32)
        self._toggle_btn.clicked.connect(self.toggle)
        layout.addWidget(self._toggle_btn)

        self._panel = QWidget()
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        panel_layout.addWidget(QLabel("しおり"))

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["タイトル", "ページ"])
        self._tree.setColumnWidth(0, 200)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemSelectionChanged.connect(self._update_button_states)
        self._delegate = _BookmarkDelegate(self)
        self._tree.setItemDelegate(self._delegate)
        # setModelData(→itemChanged)は closeEditor より先に走る。確定/取消の判定はここで行う。
        self._delegate.closeEditor.connect(self._on_close_editor)
        self._tree.installEventFilter(self)
        panel_layout.addWidget(self._tree, 1)

        hint = QLabel("ダブルクリックまたは F2 で名前・ページを編集")
        hint.setObjectName("bookmarksHint")
        hint.setWordWrap(True)
        panel_layout.addWidget(hint)

        # 追加/削除
        add_row = QHBoxLayout()
        self._add_btn = QPushButton("追加")
        self._add_btn.setToolTip("現在のページにしおりを追加し、名前を入力します")
        self._add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self._add_btn)
        self._delete_btn = QPushButton("削除")
        self._delete_btn.setToolTip("選択中のしおりを削除します(Delete キー)")
        self._delete_btn.clicked.connect(self._on_delete)
        add_row.addWidget(self._delete_btn)
        panel_layout.addLayout(add_row)
        # 旧名の互換エイリアス(「現在ページに追加」ボタンは「追加」に統合)
        self._add_current_btn = self._add_btn

        # 階層
        move_row = QHBoxLayout()
        self._promote_btn = QPushButton("← 昇格")
        self._promote_btn.setToolTip("階層を一つ上げます")
        self._promote_btn.clicked.connect(self._on_promote)
        move_row.addWidget(self._promote_btn)
        self._demote_btn = QPushButton("降格 →")
        self._demote_btn.setToolTip("直前のしおりの子にします")
        self._demote_btn.clicked.connect(self._on_demote)
        move_row.addWidget(self._demote_btn)
        panel_layout.addLayout(move_row)

        # 並べ替え
        order_row = QHBoxLayout()
        self._up_btn = QPushButton("↑ 上へ")
        self._up_btn.setToolTip("同じ階層で一つ上へ移動します")
        self._up_btn.clicked.connect(lambda: self._move_within_siblings(-1))
        order_row.addWidget(self._up_btn)
        self._down_btn = QPushButton("↓ 下へ")
        self._down_btn.setToolTip("同じ階層で一つ下へ移動します")
        self._down_btn.clicked.connect(lambda: self._move_within_siblings(1))
        order_row.addWidget(self._down_btn)
        panel_layout.addLayout(order_row)

        layout.addWidget(self._panel)

        self.set_open(False)
        self._update_button_states()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    def set_current_page_provider(self, provider: Callable[[], int | None]) -> None:
        """現在表示中のページ(1始まり)を返す callable を登録する。None なら追加は何もしない。"""
        self._current_page_provider = provider

    def set_page_count_provider(self, provider: Callable[[], int] | None) -> None:
        """総ページ数を返す callable を登録する(ページ列エディタの上限)。"""
        self._page_count_provider = provider

    def set_current_page_available(self, available: bool) -> None:
        """現在ページが存在するか。False の間は追加ボタンを無効化する(閲覧専用が優先)。"""
        self._current_page_available = bool(available)
        self._update_button_states()

    def _max_page(self) -> int:
        if self._page_count_provider is not None:
            try:
                return max(1, int(self._page_count_provider()))
            except (TypeError, ValueError):
                pass
        return _DEFAULT_MAX_PAGE

    def set_read_only(self, read_only: bool) -> None:
        """閲覧専用(見開き表示)時に編集系ボタンを全て無効化する。

        ツリーのクリックによるジャンプ/閲覧は引き続き可能。
        """
        self._read_only = bool(read_only)
        if self._read_only:
            self._cancel_pending()
        self._update_button_states()

    def is_editing(self) -> bool:
        """タイトル/ページのインライン編集中か(追加直後の未確定項目を含む)。

        編集中に load_entries すると編集欄と入力中の内容が失われるため、呼び出し側は
        True の間は再読込を見送り、``editing_finished`` で読み直すこと。
        """
        return self._tree.state() == QAbstractItemView.State.EditingState

    def commit_pending(self) -> None:
        """編集中の追加(未確定項目)があれば確定する。"""
        self._commit_pending_editor()

    def load_entries(self, entries: list[TocEntry]) -> None:
        """しおり一覧でツリーを再構築する(オンディスクの真値で同期)。"""
        self._loading = True
        try:
            self._build_tree(entries)
            self._rebuild_note_nodes()
        finally:
            self._loading = False
        self._update_button_states()

    def set_annotation_notes(self, notes: list[tuple[int, int, str]]) -> None:
        """付箋一覧（page 1始まり, xref, 冒頭テキスト）を反映する。

        付箋があるページのみ、最寄りのしおりの下に「付箋」グループ（既定で畳む）を
        作り、各付箋を子ノードとしてぶら下げる。
        """
        self._notes = list(notes)
        self._loading = True
        try:
            self._rebuild_note_nodes()
        finally:
            self._loading = False

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
        """開閉トグルを外部ボタンに委譲する。

        内蔵のトグルボタンを隠し、折りたたみ時の幅を0にする。開閉自体は
        呼び出し側が ``set_open`` / ``toggle`` で制御する。
        """
        self._collapsed_width = 0
        self._toggle_btn.hide()
        if not self._is_open:
            self.setFixedWidth(0)

    # ------------------------------------------------------------------
    # ツリー <-> list[TocEntry] 変換
    # ------------------------------------------------------------------
    def _build_tree(self, entries: list[TocEntry]) -> None:
        # 再構築で未確定項目が宙に浮かないよう、先に取り消す(何も発火しない)。
        self._cancel_pending(restore_selection=False)
        self._tree.clear()
        stack: list[tuple[int, QTreeWidgetItem]] = []  # (level, item)
        for entry in entries:
            item = self._make_item(entry.title, entry.page)
            while stack and stack[-1][0] >= entry.level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self._tree.addTopLevelItem(item)
            stack.append((entry.level, item))
        self._tree.expandAll()

    def _tree_to_entries(self) -> list[TocEntry]:
        out: list[TocEntry] = []

        def walk(item: QTreeWidgetItem, level: int) -> None:
            # 付箋ノード（グループ・付箋）はしおりではないので TOC に含めない。
            if item.data(0, _NODE_KIND_ROLE) is not None:
                return
            title = item.text(0).strip() or "(無題)"
            page = item.data(0, _PAGE_ROLE)
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = 1
            out.append(TocEntry(level=level, title=title, page=page))
            for i in range(item.childCount()):
                walk(item.child(i), level + 1)

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i), 1)
        return out

    # ------------------------------------------------------------------
    # 付箋ノード（しおりにぶら下げる）
    # ------------------------------------------------------------------
    def _is_note_node(self, item: QTreeWidgetItem) -> bool:
        return item.data(0, _NODE_KIND_ROLE) in ("note", "note_group")

    def _iter_bookmark_items(self) -> list[QTreeWidgetItem]:
        """しおり項目を表示順（プレオーダー）で返す。付箋ノードは除く。"""
        result: list[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            if self._is_note_node(item):
                return
            result.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))
        return result

    def _clear_note_nodes(self) -> None:
        self._suppress_item_changed = True
        try:
            to_remove: list[QTreeWidgetItem] = []

            def collect(item: QTreeWidgetItem) -> None:
                for i in range(item.childCount()):
                    child = item.child(i)
                    if self._is_note_node(child):
                        to_remove.append(child)
                    else:
                        collect(child)

            for i in range(self._tree.topLevelItemCount()):
                top = self._tree.topLevelItem(i)
                if self._is_note_node(top):
                    to_remove.append(top)
                else:
                    collect(top)

            for item in to_remove:
                parent = item.parent()
                if parent is None:
                    idx = self._tree.indexOfTopLevelItem(item)
                    if idx >= 0:
                        self._tree.takeTopLevelItem(idx)
                else:
                    parent.removeChild(item)
        finally:
            self._suppress_item_changed = False

    def _nearest_bookmark_for_page(self, page: int) -> QTreeWidgetItem | None:
        """page 以下で最も近い（表示順で最後の）しおり項目を返す。"""
        target: QTreeWidgetItem | None = None
        for item in self._iter_bookmark_items():
            item_page = item.data(0, _PAGE_ROLE)
            try:
                item_page = int(item_page)
            except (TypeError, ValueError):
                continue
            if item_page <= page:
                target = item
            else:
                break
        return target

    def _make_note_group_item(self, count: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"📝 付箋 ({count})", ""])
        item.setData(0, _NODE_KIND_ROLE, "note_group")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _make_note_item(self, page: int, xref: int, preview: str) -> QTreeWidgetItem:
        text = preview.strip().replace("\n", " ") or "（空のコメント）"
        if len(text) > 30:
            text = text[:30] + "…"
        item = QTreeWidgetItem([f"p.{page}: {text}", str(page)])
        item.setData(0, _PAGE_ROLE, int(page))
        item.setData(0, _NODE_KIND_ROLE, "note")
        item.setData(0, _XREF_ROLE, int(xref))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _rebuild_note_nodes(self) -> None:
        self._clear_note_nodes()
        if not self._notes:
            return
        self._suppress_item_changed = True
        try:
            # ターゲットしおり（または None=トップレベル）ごとに付箋をまとめる。
            grouped: dict[int, list[tuple[int, int, str]]] = {}
            order: list[QTreeWidgetItem | None] = []
            buckets: dict[int, QTreeWidgetItem | None] = {}
            for page, xref, preview in sorted(self._notes, key=lambda n: (n[0], n[1])):
                target = self._nearest_bookmark_for_page(page)
                key = id(target) if target is not None else 0
                if key not in grouped:
                    grouped[key] = []
                    buckets[key] = target
                    order.append(target)
                grouped[key].append((page, xref, preview))

            for target in order:
                key = id(target) if target is not None else 0
                items = grouped[key]
                group = self._make_note_group_item(len(items))
                for page, xref, preview in items:
                    group.addChild(self._make_note_item(page, xref, preview))
                if target is None:
                    self._tree.addTopLevelItem(group)
                else:
                    target.addChild(group)
                group.setExpanded(False)  # 既定で畳む
        finally:
            self._suppress_item_changed = False

    def _make_item(self, title: str, page: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title, str(page)])
        item.setData(0, _PAGE_ROLE, int(page))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        return item

    # ------------------------------------------------------------------
    # 変更の発火
    # ------------------------------------------------------------------
    def _emit_changed(self, description: str) -> None:
        if self._loading:
            return
        self.bookmarks_changed.emit(self._tree_to_entries(), description)

    # ------------------------------------------------------------------
    # ツリーイベント
    # ------------------------------------------------------------------
    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, _NODE_KIND_ROLE)
        if kind == "note_group":
            item.setExpanded(not item.isExpanded())
            return
        if kind == "note":
            page = item.data(0, _PAGE_ROLE)
            xref = item.data(0, _XREF_ROLE)
            try:
                self.note_jump_requested.emit(int(page), int(xref))
            except (TypeError, ValueError):
                pass
            return
        page = item.data(0, _PAGE_ROLE)
        try:
            self.jump_requested.emit(int(page))
        except (TypeError, ValueError):
            pass

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        # 閲覧専用中はダブルクリックでの改名/ページ編集を抑止(クリックのジャンプは可)。
        if self._read_only:
            return
        if self._is_note_node(item):
            return
        if column in (0, 1):
            self._tree.editItem(item, column)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        # インライン編集の確定時に発火。プログラム的な更新は抑制する。
        if self._loading or self._suppress_item_changed:
            return
        if self._is_note_node(item):
            return
        if item is self._pending_item:
            return  # 追加中の名前入力は closeEditor 側で「しおり追加」として1回だけ発火する
        if column == 0:
            self._emit_changed("しおり名変更")
        elif column == 1:
            old = item.data(0, _PAGE_ROLE)
            try:
                page = max(1, min(int(item.text(1).strip()), self._max_page()))
            except ValueError:
                page = old
            self._suppress_item_changed = True
            try:
                item.setText(1, str(page))
                item.setData(0, _PAGE_ROLE, page)
            finally:
                self._suppress_item_changed = False
            if page != old:
                self._emit_changed("しおりページ変更")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # F2=タイトル編集、Delete=削除(ツリーにフォーカスがある間のみ)。
        if obj is self._tree and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_F2:
                self._start_title_edit(self._tree.currentItem())
                return True
            if key == Qt.Key.Key_Delete:
                if not self._read_only:
                    self._on_delete()
                return True
        return super().eventFilter(obj, event)

    def _start_title_edit(self, item: QTreeWidgetItem | None) -> None:
        if item is None or self._read_only or self._is_note_node(item):
            return
        if not (item.flags() & Qt.ItemFlag.ItemIsEditable):
            return
        self._tree.setCurrentItem(item)
        self._tree.editItem(item, 0)

    # ------------------------------------------------------------------
    # 追加 / 削除 / 編集
    # ------------------------------------------------------------------
    def _current_page(self) -> int | None:
        """現在ページ(1始まり)。provider が None を返したら None。未登録なら 1。"""
        if self._current_page_provider is None:
            return 1
        try:
            value = self._current_page_provider()
            return None if value is None else max(1, int(value))
        except (TypeError, ValueError):
            return None

    def _insert_sibling(self, item: QTreeWidgetItem) -> None:
        """選択項目の直後・同階層に挿入。未選択ならトップレベル末尾。"""
        selected = self._tree.currentItem()
        if selected is not None and self._is_note_node(selected):
            selected = None
        if selected is None:
            self._tree.addTopLevelItem(item)
            return
        parent = selected.parent()
        if parent is None:
            index = self._tree.indexOfTopLevelItem(selected)
            self._tree.insertTopLevelItem(index + 1, item)
        else:
            index = parent.indexOfChild(selected)
            parent.insertChild(index + 1, item)
            parent.setExpanded(True)

    def _alive(self, item: QTreeWidgetItem | None) -> bool:
        if item is None:
            return False
        try:
            return item.treeWidget() is self._tree
        except RuntimeError:
            return False

    def _commit_pending_editor(self) -> None:
        """追加中のタイトル編集があれば先に確定する。

        削除/移動/昇格/降格/再追加など、ツリー構造を変える操作の冒頭で呼ぶ。
        未確定項目を放置したままツリーを組み替えると項目が宙に浮くため、
        「無視」ではなく「先に確定してから続行」に統一している。
        """
        if self._pending_item is None:
            return
        editor = self._tree.findChild(QLineEdit)
        if editor is not None:
            self._delegate.commitData.emit(editor)
            self._delegate.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
        if self._pending_item is not None:  # エディタが無かった場合の保険
            self._finish_pending(commit=True)

    def _on_close_editor(self, _editor, hint) -> None:
        if self._pending_item is None:
            # 改名/ページ編集の終了(追加中でない)。
            self.editing_finished.emit()
            return
        revert = hint == QAbstractItemDelegate.EndEditHint.RevertModelCache
        self._finish_pending(commit=not revert)
        self.editing_finished.emit()

    def _finish_pending(self, commit: bool) -> None:
        """未確定項目を確定(commit=True: 1回だけ発火)または取り消し(発火なし)する。"""
        item = self._pending_item
        if item is None:
            return  # 二重発火防止
        self._pending_item = None
        if not self._alive(item):
            self._pending_prev = None
            return
        if commit:
            self._pending_prev = None
            if not item.text(0).strip():
                self._suppress_item_changed = True
                try:
                    item.setText(0, "(無題)")
                finally:
                    self._suppress_item_changed = False
            self._emit_changed("しおり追加")
            return
        self._remove_pending_item(item, restore_selection=True)

    def _remove_pending_item(self, item: QTreeWidgetItem, restore_selection: bool) -> None:
        prev, self._pending_prev = self._pending_prev, None
        self._suppress_item_changed = True
        try:
            self._take_item(item)
        finally:
            self._suppress_item_changed = False
        if restore_selection:
            if self._alive(prev):
                self._tree.setCurrentItem(prev)
            else:
                self._tree.setCurrentItem(None)
        self._update_button_states()

    def _cancel_pending(self, restore_selection: bool = True) -> None:
        """未確定項目を何も発火せず取り除く(再構築・閲覧専用化の直前に使う)。"""
        item = self._pending_item
        if item is None:
            return
        self._pending_item = None
        if self._alive(item):
            self._remove_pending_item(item, restore_selection)
        else:
            self._pending_prev = None

    def _on_add(self) -> None:
        """現在ページに「(無題)」を挿入してタイトルをインライン編集する。

        挿入時点では発火しない。編集確定で「しおり追加」を1回だけ発火(Undo 1件)、
        Esc で取り消せば項目ごと消えて何も発火しない。
        """
        if self._read_only or not self._current_page_available:
            return
        self._commit_pending_editor()
        page = self._current_page()
        if page is None:
            return
        prev = self._tree.currentItem()
        item = self._make_item("(無題)", min(page, self._max_page()))
        self._insert_sibling(item)
        self._pending_item = item
        self._pending_prev = prev
        self._start_title_edit(item)
        if self._pending_item is item and self._tree.state() != QAbstractItemView.State.EditingState:
            # エディタが開けなかった場合は即確定して項目を宙に浮かせない。
            self._finish_pending(commit=True)

    # 旧名の互換エイリアス
    _on_add_current_page = _on_add

    def _on_delete(self) -> None:
        self._commit_pending_editor()
        item = self._tree.currentItem()
        if item is None:
            return
        if item.childCount() > 0:
            reply = QMessageBox.question(
                self,
                "しおりを削除",
                "このしおりには子しおりがあります。子も含めて削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._take_item(item)
        self._emit_changed("しおり削除")

    # ------------------------------------------------------------------
    # 階層 / 並べ替え
    # ------------------------------------------------------------------
    def _take_item(self, item: QTreeWidgetItem) -> QTreeWidgetItem:
        """ツリーから item を切り離して返す(子はぶら下げたまま)。"""
        parent = item.parent()
        if parent is None:
            index = self._tree.indexOfTopLevelItem(item)
            return self._tree.takeTopLevelItem(index)
        index = parent.indexOfChild(item)
        return parent.takeChild(index)

    def _on_demote(self) -> None:
        """選択項目を直前の兄弟の子にする。"""
        self._commit_pending_editor()
        item = self._tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            index = self._tree.indexOfTopLevelItem(item)
            prev = self._tree.topLevelItem(index - 1) if index > 0 else None
        else:
            index = parent.indexOfChild(item)
            prev = parent.child(index - 1) if index > 0 else None
        if prev is None:
            return  # 直前の兄弟が無ければ降格不可
        taken = self._take_item(item)
        prev.addChild(taken)
        prev.setExpanded(True)
        self._tree.setCurrentItem(taken)
        self._emit_changed("しおり降格")

    def _on_promote(self) -> None:
        """選択項目を親の次の兄弟に引き上げる。"""
        self._commit_pending_editor()
        item = self._tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            return  # トップレベルは昇格不可
        grandparent = parent.parent()
        taken = self._take_item(item)
        if grandparent is None:
            parent_index = self._tree.indexOfTopLevelItem(parent)
            self._tree.insertTopLevelItem(parent_index + 1, taken)
        else:
            parent_index = grandparent.indexOfChild(parent)
            grandparent.insertChild(parent_index + 1, taken)
        self._tree.setCurrentItem(taken)
        self._emit_changed("しおり昇格")

    def _move_within_siblings(self, delta: int) -> None:
        self._commit_pending_editor()
        item = self._tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            count = self._tree.topLevelItemCount()
            index = self._tree.indexOfTopLevelItem(item)
            new_index = index + delta
            if not (0 <= new_index < count):
                return
            taken = self._tree.takeTopLevelItem(index)
            self._tree.insertTopLevelItem(new_index, taken)
        else:
            count = parent.childCount()
            index = parent.indexOfChild(item)
            new_index = index + delta
            if not (0 <= new_index < count):
                return
            taken = parent.takeChild(index)
            parent.insertChild(new_index, taken)
        self._tree.setCurrentItem(item)
        self._tree.expandItem(item)
        self._emit_changed("しおり移動")

    # ------------------------------------------------------------------
    # ボタン状態
    # ------------------------------------------------------------------
    def _update_button_states(self) -> None:
        # 閲覧専用(見開き表示)中は、作成系も含めた全編集ボタンを無効化する。
        if self._read_only:
            for btn in (
                self._add_btn, self._delete_btn, self._promote_btn,
                self._demote_btn, self._up_btn, self._down_btn,
            ):
                btn.setEnabled(False)
            return
        # 通常モードでは作成ボタンは常時有効(選択非依存)。閲覧専用からの復帰を保証する。
        self._add_btn.setEnabled(self._current_page_available)
        item = self._tree.currentItem()
        # 付箋ノードはしおり編集の対象外。
        if item is not None and self._is_note_node(item):
            for btn in (
                self._delete_btn, self._promote_btn,
                self._demote_btn, self._up_btn, self._down_btn,
            ):
                btn.setEnabled(False)
            return
        has_selection = item is not None
        self._delete_btn.setEnabled(has_selection)

        can_promote = has_selection and item.parent() is not None
        self._promote_btn.setEnabled(bool(can_promote))

        can_demote = False
        can_up = False
        can_down = False
        if has_selection:
            parent = item.parent()
            if parent is None:
                index = self._tree.indexOfTopLevelItem(item)
                count = self._tree.topLevelItemCount()
            else:
                index = parent.indexOfChild(item)
                count = parent.childCount()
            can_demote = index > 0
            can_up = index > 0
            can_down = index < count - 1
        self._demote_btn.setEnabled(can_demote)
        self._up_btn.setEnabled(can_up)
        self._down_btn.setEnabled(can_down)
