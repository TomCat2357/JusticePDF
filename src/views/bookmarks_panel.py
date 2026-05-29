"""しおり(アウトライン/ブックマーク)編集パネル。

ページ編集画面のズームビュー右側にドロワーとして組み込む独立部品。
QTreeWidget で階層構造を編集し、変更があるたびに ``bookmarks_changed`` を
``list[TocEntry]`` 付きで発火する。永続化(PDFへの保存)や Undo 登録は
呼び出し側(page_edit_window)が担い、本パネルはそれらを一切知らない。
"""
from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.utils.pdf_utils import TocEntry

logger = logging.getLogger(__name__)

_PAGE_ROLE = Qt.ItemDataRole.UserRole


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
    open_changed = pyqtSignal(bool)

    DRAWER_WIDTH = 320

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bookmarksDrawer")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._is_open = False
        self._loading = False
        self._suppress_item_changed = False
        self._current_page_provider: Callable[[], int] | None = None

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
        panel_layout.addWidget(self._tree, 1)

        # 追加/削除
        add_row = QHBoxLayout()
        self._add_current_btn = QPushButton("現在ページに追加")
        self._add_current_btn.setToolTip("表示中のページへのしおりを追加")
        self._add_current_btn.clicked.connect(self._on_add_current_page)
        add_row.addWidget(self._add_current_btn)
        panel_layout.addLayout(add_row)

        edit_row = QHBoxLayout()
        self._add_btn = QPushButton("追加...")
        self._add_btn.clicked.connect(self._on_add_dialog)
        edit_row.addWidget(self._add_btn)
        self._edit_btn = QPushButton("編集...")
        self._edit_btn.clicked.connect(self._on_edit_dialog)
        edit_row.addWidget(self._edit_btn)
        self._delete_btn = QPushButton("削除")
        self._delete_btn.clicked.connect(self._on_delete)
        edit_row.addWidget(self._delete_btn)
        panel_layout.addLayout(edit_row)

        # 階層/並べ替え
        move_row = QHBoxLayout()
        self._promote_btn = QPushButton("← 昇格")
        self._promote_btn.setToolTip("階層を一つ上げる")
        self._promote_btn.clicked.connect(self._on_promote)
        move_row.addWidget(self._promote_btn)
        self._demote_btn = QPushButton("降格 →")
        self._demote_btn.setToolTip("直前の項目の子にする")
        self._demote_btn.clicked.connect(self._on_demote)
        move_row.addWidget(self._demote_btn)
        panel_layout.addLayout(move_row)

        order_row = QHBoxLayout()
        self._up_btn = QPushButton("↑ 上へ")
        self._up_btn.clicked.connect(lambda: self._move_within_siblings(-1))
        order_row.addWidget(self._up_btn)
        self._down_btn = QPushButton("↓ 下へ")
        self._down_btn.clicked.connect(lambda: self._move_within_siblings(1))
        order_row.addWidget(self._down_btn)
        panel_layout.addLayout(order_row)

        layout.addWidget(self._panel)

        self.set_open(False)
        self._update_button_states()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    def set_current_page_provider(self, provider: Callable[[], int]) -> None:
        """現在表示中のページ(1始まり)を返す callable を登録する。"""
        self._current_page_provider = provider

    def load_entries(self, entries: list[TocEntry]) -> None:
        """しおり一覧でツリーを再構築する(オンディスクの真値で同期)。"""
        self._loading = True
        try:
            self._build_tree(entries)
        finally:
            self._loading = False
        self._update_button_states()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def set_open(self, is_open: bool) -> None:
        is_open = bool(is_open)
        changed = is_open != self._is_open
        self._is_open = is_open
        self._panel.setVisible(is_open)
        self.setFixedWidth(self.DRAWER_WIDTH if is_open else 32)
        self._toggle_btn.setText("▶" if is_open else "◀")
        if changed:
            self.open_changed.emit(is_open)

    def toggle(self) -> None:
        self.set_open(not self._is_open)

    # ------------------------------------------------------------------
    # ツリー <-> list[TocEntry] 変換
    # ------------------------------------------------------------------
    def _build_tree(self, entries: list[TocEntry]) -> None:
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
        page = item.data(0, _PAGE_ROLE)
        try:
            self.jump_requested.emit(int(page))
        except (TypeError, ValueError):
            pass

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self._tree.editItem(item, 0)
        elif column == 1:
            self._edit_page_via_dialog(item)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        # インライン編集(タイトル列)の確定時に発火。プログラム的な更新は抑制する。
        if self._loading or self._suppress_item_changed:
            return
        if column == 0:
            self._emit_changed("しおり名変更")

    # ------------------------------------------------------------------
    # 追加 / 削除 / 編集
    # ------------------------------------------------------------------
    def _current_page(self) -> int:
        if self._current_page_provider is None:
            return 1
        try:
            return max(1, int(self._current_page_provider()))
        except (TypeError, ValueError):
            return 1

    def _insert_sibling(self, item: QTreeWidgetItem) -> None:
        """選択項目の直後・同階層に挿入。未選択ならトップレベル末尾。"""
        selected = self._tree.currentItem()
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

    def _on_add_current_page(self) -> None:
        page = self._current_page()
        item = self._make_item("(無題)", page)
        self._insert_sibling(item)
        self._tree.setCurrentItem(item)
        self._emit_changed("しおり追加")

    def _on_add_dialog(self) -> None:
        default_page = self._current_page()
        title, ok = QInputDialog.getText(self, "しおりを追加", "タイトル:")
        if not ok:
            return
        title = title.strip() or "(無題)"
        page, ok = QInputDialog.getInt(
            self, "しおりを追加", "ページ:", default_page, 1, 1_000_000
        )
        if not ok:
            return
        item = self._make_item(title, page)
        self._insert_sibling(item)
        self._tree.setCurrentItem(item)
        self._emit_changed("しおり追加")

    def _on_edit_dialog(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            return
        title, ok = QInputDialog.getText(
            self, "しおりを編集", "タイトル:", text=item.text(0)
        )
        if not ok:
            return
        current_page = item.data(0, _PAGE_ROLE) or 1
        page, ok = QInputDialog.getInt(
            self, "しおりを編集", "ページ:", int(current_page), 1, 1_000_000
        )
        if not ok:
            return
        self._suppress_item_changed = True
        try:
            item.setText(0, title.strip() or "(無題)")
            item.setText(1, str(page))
            item.setData(0, _PAGE_ROLE, int(page))
        finally:
            self._suppress_item_changed = False
        self._emit_changed("しおり編集")

    def _edit_page_via_dialog(self, item: QTreeWidgetItem) -> None:
        current_page = item.data(0, _PAGE_ROLE) or 1
        page, ok = QInputDialog.getInt(
            self, "ページを変更", "ページ:", int(current_page), 1, 1_000_000
        )
        if not ok:
            return
        self._suppress_item_changed = True
        try:
            item.setText(1, str(page))
            item.setData(0, _PAGE_ROLE, int(page))
        finally:
            self._suppress_item_changed = False
        self._emit_changed("しおりページ変更")

    def _on_delete(self) -> None:
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
        item = self._tree.currentItem()
        has_selection = item is not None
        for btn in (self._edit_btn, self._delete_btn):
            btn.setEnabled(has_selection)

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
