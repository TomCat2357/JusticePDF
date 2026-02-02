# PDFas Enhancement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Note:** Update the Progress section below as tasks are completed.

**Goal:** メインウィンドウとページ編集ウィンドウのUX改善（空スロット機能、選択解除、Undo/Redo拡張）

**Architecture:**
- メインウィンドウに「空スロット」(Placeholder)の概念を導入し、PDFの挿入位置を明示化
- Split操作をUndo/Redo対応に拡張
- ページ編集ウィンドウの複数選択動作を改善

**Tech Stack:** PyQt6, PyMuPDF (fitz), send2trash

---

## 進捗 / Progress
task終了時に速やかに[x]を記載すること
- [x] Task 1: PlaceholderCard基盤実装 (commit c830404)
- [x] Task 2: MainWindowにPlaceholder管理機能を追加 (commit 5f39130)
- [x] Task 3: 何もない場所をクリックしたら選択解除 (commit 73f3110)
- [x] Task 4: SplitをUndo/Redo対応に (commit 98006c1)
- [x] Task 5: D&Dでカード配置変更時にPlaceholderを残す (commit 02855c3)
- [x] Task 6: Placeholderを消すボタンの追加 (commit ef8fac7)
- [x] Task 7: D&Dでの間への挿入サポート (commit 3423354)
- [x] Task 8: PageEditWindow複数選択の改善 (commit 2c9685e)
- [x] Task 9: ページ移動時に元ウィンドウで非表示化
- [x] Task 10: ページ番号を閉じるまで維持
- [x] Task 11: D&DでPlaceholderのMIMEタイプを処理
- [x] Task 12: PDFカードをマージ時にPlaceholderを残す (commit 773241a)
- [x] Task 13: 末尾Placeholderへのマージ時の縮退処理 (commit 843fca6)
- [x] Task 14: 統合テスト

---

## Task 1: メインウィンドウの空スロット(Placeholder)基盤実装

**Files:**
- Create: `src/views/placeholder_card.py`
- Modify: `src/views/main_window.py:20-30` (importとカードリスト管理)

**Step 1: PlaceholderCardクラスの作成**

```python
# src/views/placeholder_card.py
"""Placeholder card widget for empty slots in the main window."""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui import QDrag

PLACEHOLDER_MIME_TYPE = "application/x-pdfas-placeholder"


class PlaceholderCard(QFrame):
    """Widget representing an empty slot.

    Can receive PDFs via drag-and-drop and be reordered.
    """

    clicked = pyqtSignal(object)
    dropped_on = pyqtSignal(object, str)  # (self, source_path)

    CARD_WIDTH = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_selected = False
        self._drag_start_pos = None
        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the placeholder UI."""
        self.setFixedWidth(self.CARD_WIDTH)
        self.setFixedHeight(180)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self._label = QLabel("(empty)")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #888; font-size: 14px;")
        layout.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._update_style()

    def _update_style(self) -> None:
        """Update style based on selection."""
        if self._is_selected:
            self.setStyleSheet("PlaceholderCard { background-color: #e6f3ff; border: 2px dashed #007bff; }")
        else:
            self.setStyleSheet("PlaceholderCard { background-color: #fafafa; border: 2px dashed #ccc; }")

    @property
    def is_selected(self) -> bool:
        return self._is_selected

    @property
    def is_placeholder(self) -> bool:
        """Identify as placeholder."""
        return True

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        self._update_style()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for drag."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return

        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(PLACEHOLDER_MIME_TYPE, b"placeholder")
        drag.setMimeData(mime_data)

        pixmap = self.grab()
        pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:
        """Accept PDF card drops."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            self.setStyleSheet("PlaceholderCard { background-color: #90EE90; border: 2px dashed #228B22; }")
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._update_style()

    def dropEvent(self, event) -> None:
        """Handle PDF card drop."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            self.dropped_on.emit(self, source_path)
            event.acceptProposedAction()
        self._update_style()
```

**Step 2: ファイルを作成したことを確認**

Run: `python -c "from src.views.placeholder_card import PlaceholderCard; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add src/views/placeholder_card.py
git commit -m "feat: add PlaceholderCard class for empty slots

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 2: MainWindowにPlaceholder管理機能を追加

**Files:**
- Modify: `src/views/main_window.py`

**Step 1: インポートとカード管理の変更**

main_window.pyの先頭インポートに追加:
```python
from src.views.placeholder_card import PlaceholderCard, PLACEHOLDER_MIME_TYPE
```

**Step 2: カードリストをUnion型に変更**

`_cards`の型を変更:
```python
self._cards: list[PDFCard | PlaceholderCard] = []
self._selected_cards: list[PDFCard | PlaceholderCard] = []
```

**Step 3: 末尾Placeholder管理メソッドの追加**

```python
def _ensure_trailing_placeholder(self) -> None:
    """Ensure exactly one placeholder at the end if there are PDFs."""
    # PDFがない場合はPlaceholderを作らない
    pdf_cards = [c for c in self._cards if isinstance(c, PDFCard)]
    if not pdf_cards:
        # 全てのPlaceholderを削除
        for card in self._cards[:]:
            if isinstance(card, PlaceholderCard):
                if card in self._selected_cards:
                    self._selected_cards.remove(card)
                self._cards.remove(card)
                card.deleteLater()
        return

    # 末尾のPlaceholderを確認
    trailing_placeholders = 0
    for card in reversed(self._cards):
        if isinstance(card, PlaceholderCard):
            trailing_placeholders += 1
        else:
            break

    # 末尾Placeholderが0個なら1個追加
    if trailing_placeholders == 0:
        placeholder = self._create_placeholder()
        self._cards.append(placeholder)
    # 末尾Placeholderが2個以上なら1個に縮退
    elif trailing_placeholders > 1:
        removed = 0
        for card in reversed(self._cards[:]):
            if isinstance(card, PlaceholderCard):
                if removed < trailing_placeholders - 1:
                    if card in self._selected_cards:
                        self._selected_cards.remove(card)
                    self._cards.remove(card)
                    card.deleteLater()
                    removed += 1
                else:
                    break
            else:
                break

def _create_placeholder(self) -> PlaceholderCard:
    """Create a new placeholder card."""
    placeholder = PlaceholderCard()
    placeholder.clicked.connect(self._on_card_clicked)
    placeholder.dropped_on.connect(self._on_placeholder_drop)
    return placeholder

def _on_placeholder_drop(self, placeholder: PlaceholderCard, source_path: str) -> None:
    """Handle PDF dropped on placeholder - replace placeholder with PDF."""
    # Find the placeholder index
    try:
        idx = self._cards.index(placeholder)
    except ValueError:
        return

    # Find source card
    source_card = None
    source_idx = -1
    for i, card in enumerate(self._cards):
        if isinstance(card, PDFCard) and card.pdf_path == source_path:
            source_card = card
            source_idx = i
            break

    if source_card is None:
        return

    # Remove source from old position and add placeholder there
    self._cards.pop(source_idx)
    new_placeholder = self._create_placeholder()
    self._cards.insert(source_idx, new_placeholder)

    # Replace target placeholder with source card
    if source_idx < idx:
        idx -= 1
    self._cards.pop(idx)
    placeholder.deleteLater()
    self._cards.insert(idx, source_card)

    self._ensure_trailing_placeholder()
    self._refresh_grid()
```

**Step 4: _load_existing_filesを更新**

```python
def _load_existing_files(self) -> None:
    """Load existing PDF files from the work directory."""
    for pdf_path in self._watcher.get_pdf_files():
        self._add_card(pdf_path)
    self._ensure_trailing_placeholder()
    self._refresh_grid()
```

**Step 5: テスト実行**

Run: `python -m pytest tests/ -v -k "main" --tb=short`

**Step 6: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: add placeholder management to MainWindow

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 3: 何もない場所をクリックしたら選択解除

**Files:**
- Modify: `src/views/main_window.py`

**Step 1: mousePressEventをオーバーライド**

MainWindowクラスに追加:
```python
def mousePressEvent(self, event) -> None:
    """Handle mouse press - clear selection when clicking empty area."""
    # Check if click is on a card
    child = self.childAt(event.pos())

    # Traverse up to find if we clicked on a card
    while child is not None:
        if isinstance(child, (PDFCard, PlaceholderCard)):
            # Clicked on a card, let normal handling proceed
            super().mousePressEvent(event)
            return
        child = child.parent()

    # Clicked on empty area - clear selection
    self._clear_selection()
    super().mousePressEvent(event)
```

**Step 2: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 3: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: clear selection when clicking empty area in MainWindow

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 4: SplitをUndo/Redo対応に

**Files:**
- Modify: `src/views/main_window.py:488-512` (_on_split メソッド)

**Step 1: _on_splitメソッドの書き換え**

```python
def _on_split(self) -> None:
    """Handle split action - split PDFs into individual pages."""
    from src.utils.pdf_utils import extract_pages
    import tempfile
    import shutil

    if not self._selected_cards:
        return

    # Only process actual PDFCards
    pdf_cards = [c for c in self._selected_cards if isinstance(c, PDFCard)]
    if not pdf_cards:
        return

    split_info = []  # [(original_path, backup_path, new_paths)]

    for card in pdf_cards:
        page_count = get_page_count(card.pdf_path)
        if page_count <= 1:
            continue

        # Backup original
        backup_fd, backup_path = tempfile.mkstemp(suffix=".pdf")
        os.close(backup_fd)
        shutil.copy2(card.pdf_path, backup_path)

        original_path = card.pdf_path
        base_name = os.path.splitext(card.filename)[0]
        new_paths = []

        # Extract each page
        for i in range(page_count):
            j = 1
            while True:
                new_name = f"{base_name}_{j}.pdf"
                new_path = self._work_dir / new_name
                if not new_path.exists():
                    break
                j += 1

            extract_pages(card.pdf_path, str(new_path), [i])
            new_paths.append(str(new_path))

        split_info.append((original_path, backup_path, new_paths))

        # Delete original
        send2trash(original_path)

    if not split_info:
        return

    work_dir = self._work_dir

    def undo_split():
        for original_path, backup_path, new_paths in split_info:
            # Restore original
            shutil.copy2(backup_path, original_path)
            # Delete split files
            for new_path in new_paths:
                if os.path.exists(new_path):
                    send2trash(new_path)

    def redo_split():
        for original_path, backup_path, new_paths in split_info:
            # Re-split
            base_name = os.path.splitext(os.path.basename(original_path))[0]
            page_count = get_page_count(original_path)
            for i in range(page_count):
                if i < len(new_paths):
                    extract_pages(original_path, new_paths[i], [i])
            send2trash(original_path)

    self._undo_manager.add_action(UndoAction(
        description=f"Split {len(split_info)} PDF(s)",
        undo_func=undo_split,
        redo_func=redo_split
    ))
    self._clear_selection()
```

**Step 2: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 3: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: add Undo/Redo support for Split operation

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 5: D&Dでカード配置変更時にPlaceholderを残す

**Files:**
- Modify: `src/views/main_window.py`

**Step 1: _handle_card_dropを更新**

```python
def _handle_card_drop(self, source_path: str, drop_pos) -> None:
    """Handle internal card drop for reordering."""
    # Find source card
    source_card = None
    source_idx = -1
    for i, card in enumerate(self._cards):
        if isinstance(card, PDFCard) and card.pdf_path == source_path:
            source_card = card
            source_idx = i
            break

    if source_card is None:
        return

    # Find target position
    target_idx = self._get_drop_index(drop_pos)
    if target_idx == -1 or target_idx == source_idx:
        return

    old_cards = self._cards[:]  # Undo用のコピー

    # Remove source and insert placeholder at source position
    self._cards.pop(source_idx)
    placeholder = self._create_placeholder()
    self._cards.insert(source_idx, placeholder)

    # Adjust target index if needed
    if target_idx > source_idx:
        # target_idx is now pointing to correct position
        pass

    # Insert source card at target position
    self._cards.insert(target_idx, source_card)

    self._ensure_trailing_placeholder()
    self._refresh_grid()

    new_cards = self._cards[:]  # Redo用のコピー

    def undo_reorder():
        self._cards.clear()
        self._cards.extend(old_cards)
        self._ensure_trailing_placeholder()
        self._refresh_grid()

    def redo_reorder():
        self._cards.clear()
        self._cards.extend(new_cards)
        self._ensure_trailing_placeholder()
        self._refresh_grid()

    self._undo_manager.add_action(UndoAction(
        description=f"Move card",
        undo_func=undo_reorder,
        redo_func=redo_reorder
    ))
```

**Step 2: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 3: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: leave placeholder when moving card via D&D

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Placeholderを消すボタンの追加

**Files:**
- Modify: `src/views/main_window.py:84-153` (_setup_toolbar)

**Step 1: ツールバーにボタン追加**

`_setup_toolbar`の`Sort by date`ボタンの後に追加:
```python
toolbar.addSeparator()

# Remove placeholders
self._remove_placeholders_btn = QPushButton("Remove Empty")
self._remove_placeholders_btn.clicked.connect(self._on_remove_placeholders)
toolbar.addWidget(self._remove_placeholders_btn)
```

**Step 2: ハンドラ追加**

```python
def _on_remove_placeholders(self) -> None:
    """Remove all placeholders except the trailing one."""
    # Count placeholders (except trailing)
    placeholders_to_remove = []

    # Find placeholders that are not at the end
    pdf_found_after = False
    for card in reversed(self._cards):
        if isinstance(card, PDFCard):
            pdf_found_after = True
        elif isinstance(card, PlaceholderCard) and pdf_found_after:
            placeholders_to_remove.append(card)

    if not placeholders_to_remove:
        return

    old_cards = self._cards[:]

    for placeholder in placeholders_to_remove:
        if placeholder in self._selected_cards:
            self._selected_cards.remove(placeholder)
        self._cards.remove(placeholder)
        placeholder.deleteLater()

    self._ensure_trailing_placeholder()
    self._refresh_grid()

    new_cards = self._cards[:]

    def undo_remove():
        self._cards.clear()
        self._cards.extend(old_cards)
        self._refresh_grid()

    def redo_remove():
        self._cards.clear()
        self._cards.extend(new_cards)
        self._refresh_grid()

    self._undo_manager.add_action(UndoAction(
        description=f"Remove {len(placeholders_to_remove)} placeholder(s)",
        undo_func=undo_remove,
        redo_func=redo_remove
    ))
```

**Step 3: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 4: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: add Remove Empty button to clear intermediate placeholders

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 7: D&Dでの間への挿入サポート

**Files:**
- Modify: `src/views/main_window.py`

**Step 1: _get_drop_indexを更新して挿入位置を明確化**

```python
def _get_drop_index(self, pos) -> int:
    """Get the index where the drop should occur.

    Returns the index where the card should be inserted.
    If dropped on the left half of a card, insert before it.
    If dropped on the right half, insert after it.
    """
    for i, card in enumerate(self._cards):
        card_rect = card.geometry()
        if card_rect.contains(pos):
            # Determine left or right half
            center_x = card_rect.center().x()
            if pos.x() < center_x:
                return i  # Insert before
            else:
                return i + 1  # Insert after

    # If dropped after last card, return last index + 1
    if self._cards:
        return len(self._cards)
    return 0
```

**Step 2: dragMoveEventでドロップ位置のプレビュー表示**

後回し - UIの改善は後で

**Step 3: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: improve drop position detection for between-card insertion

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 8: PageEditWindow複数選択の改善

**Files:**
- Modify: `src/views/page_edit_window.py:289-319` (_on_thumbnail_clicked)

**Step 1: _on_thumbnail_clickedを修正**

問題: 通常クリックで複数選択が解除される

修正方針: 既に選択済みのサムネイルをクリックした場合は解除しない

```python
def _on_thumbnail_clicked(self, thumb: PageThumbnail) -> None:
    """Handle thumbnail click."""
    from PyQt6.QtWidgets import QApplication
    modifiers = QApplication.keyboardModifiers()

    if modifiers & Qt.KeyboardModifier.ControlModifier:
        if thumb in self._selected_thumbnails:
            thumb.set_selected(False)
            self._selected_thumbnails.remove(thumb)
        else:
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)
    elif modifiers & Qt.KeyboardModifier.ShiftModifier:
        if self._selected_thumbnails:
            start_idx = self._thumbnails.index(self._selected_thumbnails[-1])
            end_idx = self._thumbnails.index(thumb)
            if start_idx > end_idx:
                start_idx, end_idx = end_idx, start_idx
            for i in range(start_idx, end_idx + 1):
                if self._thumbnails[i] not in self._selected_thumbnails:
                    self._thumbnails[i].set_selected(True)
                    self._selected_thumbnails.append(self._thumbnails[i])
        else:
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)
    else:
        # Normal click
        if thumb in self._selected_thumbnails:
            # Clicked on already selected thumbnail - do nothing
            # This preserves multi-selection
            pass
        else:
            # Clicked on unselected thumbnail - clear others and select this
            self._clear_selection()
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)

    self._update_button_states()
```

**Step 2: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 3: Commit**

```bash
git add src/views/page_edit_window.py
git commit -m "fix: preserve multi-selection when clicking selected thumbnail

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 9: ページ移動時に元ウィンドウで非表示化

**Files:**
- Modify: `src/views/page_edit_window.py`
- Modify: `src/views/main_window.py`

**Step 1: PageEditWindowにページ非表示機能を追加**

PageEditWindowに追加:
```python
def hide_page(self, page_num: int) -> None:
    """Hide a page thumbnail (after moving to main window)."""
    for thumb in self._thumbnails:
        if thumb.page_num == page_num:
            thumb.setVisible(False)
            if thumb in self._selected_thumbnails:
                self._selected_thumbnails.remove(thumb)
            break
```

**Step 2: MainWindowの_handle_page_extractionを更新**

```python
def _handle_page_extraction(self, data: str) -> None:
    """Handle page extraction from page edit window."""
    from src.utils.pdf_utils import extract_pages, remove_pages

    pdf_path, page_num_str = data.split('|')
    page_num = int(page_num_str)

    # Generate unique filename
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    i = 1
    while True:
        new_name = f"{base_name}_page{page_num + 1}_{i}.pdf"
        new_path = self._work_dir / new_name
        if not new_path.exists():
            break
        i += 1

    # Extract page to new file
    extract_pages(pdf_path, str(new_path), [page_num])

    # Remove page from source
    remove_pages(pdf_path, [page_num])

    # Hide page in source window
    from src.views.page_edit_window import PageEditWindow
    for window in self.findChildren(PageEditWindow):
        if window._pdf_path == pdf_path:
            window.hide_page(page_num)
            break
```

**Step 3: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 4: Commit**

```bash
git add src/views/main_window.py src/views/page_edit_window.py
git commit -m "feat: hide page in source window when moved to main window

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 10: ページ番号を閉じるまで維持

**Files:**
- Modify: `src/views/page_edit_window.py`

**Step 1: PageThumbnailに元のページ番号を保持**

PageThumbnailクラスを修正:
```python
def __init__(self, pdf_path: str, page_num: int, display_num: int = None, parent=None):
    super().__init__(parent)
    self._pdf_path = pdf_path
    self._page_num = page_num  # 現在のPDF内でのインデックス
    self._display_num = display_num if display_num is not None else page_num  # 表示用番号（変更しない）
    self._is_selected = False
    self._drag_start_pos = None
    self.setAcceptDrops(True)

    self._setup_ui()
```

ラベル表示を修正:
```python
# Page number - use display_num which doesn't change
self._number_label = QLabel(str(self._display_num + 1))
```

**Step 2: _handle_page_reorderでpage_numのみ更新**

```python
def _handle_page_reorder(self, source_page: int, drop_pos) -> None:
    """Handle page reorder within the same PDF."""
    # Find target position
    target_page = self._get_drop_page_index(drop_pos)

    if target_page == -1 or target_page == source_page:
        return

    # Build new order
    page_count = get_page_count(self._pdf_path)
    new_order = list(range(page_count))
    new_order.remove(source_page)

    if target_page > source_page:
        target_page -= 1
    new_order.insert(target_page, source_page)

    pdf_path = self._pdf_path
    old_thumbs_order = self._thumbnails[:]

    def do_reorder():
        reorder_pages(pdf_path, new_order)
        # Reorder thumbnails in UI without reloading
        source_thumb = None
        source_idx = -1
        for i, t in enumerate(self._thumbnails):
            if t.page_num == source_page:
                source_thumb = t
                source_idx = i
                break
        if source_thumb:
            self._thumbnails.pop(source_idx)
            insert_idx = target_page
            if source_idx < target_page:
                insert_idx = target_page
            self._thumbnails.insert(insert_idx, source_thumb)
            # Update page_num for all thumbnails to match new PDF order
            for i, t in enumerate(self._thumbnails):
                t._page_num = i
        self._refresh_grid()

    def undo_reorder():
        # Calculate inverse
        inverse = [0] * len(new_order)
        for i, pos in enumerate(new_order):
            inverse[pos] = i
        reorder_pages(pdf_path, inverse)
        self._thumbnails.clear()
        self._thumbnails.extend(old_thumbs_order)
        for i, t in enumerate(self._thumbnails):
            t._page_num = i
        self._refresh_grid()

    do_reorder()

    self._undo_manager.add_action(UndoAction(
        description=f"Reorder page",
        undo_func=undo_reorder,
        redo_func=do_reorder
    ))
```

**Step 3: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 4: Commit**

```bash
git add src/views/page_edit_window.py
git commit -m "feat: preserve original page numbers until window reopened

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 11: D&DでPlaceholderのMIMEタイプを処理

**Files:**
- Modify: `src/views/main_window.py`

**Step 1: dragEnterEventを更新**

```python
def dragEnterEvent(self, event) -> None:
    """Handle drag enter event."""
    from src.views.pdf_card import PDFCARD_MIME_TYPE
    from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

    if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
        event.acceptProposedAction()
    elif event.mimeData().hasFormat(PLACEHOLDER_MIME_TYPE):
        event.acceptProposedAction()
    elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
        event.acceptProposedAction()
    elif event.mimeData().hasUrls():
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith('.pdf'):
                event.acceptProposedAction()
                return
```

**Step 2: dropEventを更新**

```python
def dropEvent(self, event) -> None:
    """Handle drop event."""
    from src.views.pdf_card import PDFCARD_MIME_TYPE
    from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

    if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
        source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
        drop_pos = event.position().toPoint()
        self._handle_card_drop(source_path, drop_pos)
        event.acceptProposedAction()
    elif event.mimeData().hasFormat(PLACEHOLDER_MIME_TYPE):
        drop_pos = event.position().toPoint()
        self._handle_placeholder_drop(drop_pos)
        event.acceptProposedAction()
    elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
        data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
        self._handle_page_extraction(data)
        event.acceptProposedAction()
    elif event.mimeData().hasUrls():
        self._handle_external_file_drop(event.mimeData().urls())
        event.acceptProposedAction()
```

**Step 3: Placeholder移動ハンドラ追加**

```python
def _handle_placeholder_drop(self, drop_pos) -> None:
    """Handle placeholder reordering."""
    # Find dragged placeholder (the one being dragged)
    # Since placeholder doesn't carry unique ID, find selected one
    source_placeholder = None
    source_idx = -1
    for i, card in enumerate(self._cards):
        if isinstance(card, PlaceholderCard) and card.is_selected:
            source_placeholder = card
            source_idx = i
            break

    if source_placeholder is None:
        return

    target_idx = self._get_drop_index(drop_pos)
    if target_idx == -1 or target_idx == source_idx:
        return

    old_cards = self._cards[:]

    self._cards.pop(source_idx)
    if target_idx > source_idx:
        target_idx -= 1
    self._cards.insert(target_idx, source_placeholder)

    self._ensure_trailing_placeholder()
    self._refresh_grid()

    new_cards = self._cards[:]

    def undo():
        self._cards.clear()
        self._cards.extend(old_cards)
        self._refresh_grid()

    def redo():
        self._cards.clear()
        self._cards.extend(new_cards)
        self._refresh_grid()

    self._undo_manager.add_action(UndoAction(
        description="Move placeholder",
        undo_func=undo,
        redo_func=redo
    ))
```

**Step 4: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 5: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: support placeholder drag and drop reordering

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 12: PDFカードをマージ時にPlaceholderを残す

**Files:**
- Modify: `src/views/main_window.py`

**Step 1: _on_card_mergeを更新**

```python
def _on_card_merge(self, target_card: PDFCard, source_path: str) -> None:
    """Handle card merge (drop card on another card)."""
    from src.utils.pdf_utils import merge_pdfs
    import tempfile
    import shutil

    target_path = target_card.pdf_path

    # Find source card index
    source_idx = -1
    source_card = None
    for i, card in enumerate(self._cards):
        if isinstance(card, PDFCard) and card.pdf_path == source_path:
            source_idx = i
            source_card = card
            break

    if source_card is None:
        return

    # Create merged PDF in temp location
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        merge_pdfs(tmp_path, [target_path, source_path])

        # Replace target with merged
        shutil.move(tmp_path, target_path)

        # Remove source card and insert placeholder
        self._cards.pop(source_idx)
        source_card.deleteLater()
        placeholder = self._create_placeholder()
        self._cards.insert(source_idx, placeholder)

        # Delete source file
        send2trash(source_path)

        self._ensure_trailing_placeholder()
        self._refresh_grid()
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
```

**Step 2: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 3: Commit**

```bash
git add src/views/main_window.py
git commit -m "feat: leave placeholder when merging PDFs

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 13: 末尾Placeholderへのマージ時の縮退処理

**Files:**
- Modify: `src/views/placeholder_card.py`
- Modify: `src/views/main_window.py`

**Step 1: PlaceholderCardにplaceholder→placeholderドロップ対応**

PlaceholderCardのdragEnterEventとdropEventを更新:
```python
def dragEnterEvent(self, event) -> None:
    """Accept PDF card and placeholder drops."""
    from src.views.pdf_card import PDFCARD_MIME_TYPE
    if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
        self.setStyleSheet("PlaceholderCard { background-color: #90EE90; border: 2px dashed #228B22; }")
        event.acceptProposedAction()
    elif event.mimeData().hasFormat(PLACEHOLDER_MIME_TYPE):
        self.setStyleSheet("PlaceholderCard { background-color: #FFE4B5; border: 2px dashed #FFA500; }")
        event.acceptProposedAction()
    else:
        event.ignore()

def dropEvent(self, event) -> None:
    """Handle PDF card or placeholder drop."""
    from src.views.pdf_card import PDFCARD_MIME_TYPE
    if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
        source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
        self.dropped_on.emit(self, source_path)
        event.acceptProposedAction()
    elif event.mimeData().hasFormat(PLACEHOLDER_MIME_TYPE):
        # Placeholder dropped on placeholder - handled by MainWindow
        self.dropped_on.emit(self, "")  # Empty string signals placeholder
        event.acceptProposedAction()
    self._update_style()
```

**Step 2: MainWindowで処理**

_on_placeholder_dropを更新:
```python
def _on_placeholder_drop(self, target: PlaceholderCard, source_path: str) -> None:
    """Handle drop on placeholder."""
    if source_path == "":
        # Placeholder dropped on placeholder - merge (remove source)
        # Find selected placeholder
        source_placeholder = None
        source_idx = -1
        for i, card in enumerate(self._cards):
            if isinstance(card, PlaceholderCard) and card.is_selected and card != target:
                source_placeholder = card
                source_idx = i
                break

        if source_placeholder is None:
            return

        old_cards = self._cards[:]

        # Remove source placeholder
        self._cards.remove(source_placeholder)
        if source_placeholder in self._selected_cards:
            self._selected_cards.remove(source_placeholder)
        source_placeholder.deleteLater()

        self._ensure_trailing_placeholder()
        self._refresh_grid()

        new_cards = self._cards[:]

        def undo():
            self._cards.clear()
            self._cards.extend(old_cards)
            self._refresh_grid()

        def redo():
            self._cards.clear()
            self._cards.extend(new_cards)
            self._refresh_grid()

        self._undo_manager.add_action(UndoAction(
            description="Merge placeholders",
            undo_func=undo,
            redo_func=redo
        ))
    else:
        # PDF dropped on placeholder - replace
        # (既存の_on_placeholder_dropロジック)
        # ...
```

**Step 3: テスト実行**

Run: `python -m pytest tests/ -v --tb=short`

**Step 4: Commit**

```bash
git add src/views/placeholder_card.py src/views/main_window.py
git commit -m "feat: merge placeholders when dropping placeholder on placeholder

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 14: 統合テスト

**Step 1: アプリケーション起動テスト**

Run: `python -c "from src.views.main_window import MainWindow; from PyQt6.QtWidgets import QApplication; import sys; app = QApplication(sys.argv); w = MainWindow(); print('OK')"`

**Step 2: 全テスト実行**

Run: `python -m pytest tests/ -v`

**Step 3: 手動テスト**

以下をテスト:
1. 空の場所クリックで選択解除
2. SplitのUndo/Redo
3. PDFが1つ以上ある時、末尾に(empty)が表示される
4. D&Dで配置変更時にPlaceholderが残る
5. Remove Emptyボタンで中間Placeholderが削除される
6. ページ編集ウィンドウで複数選択後のクリックで解除されない
7. ページ移動時に元ウィンドウでページが非表示になる

**Step 4: 最終Commit**

```bash
git add -A
git commit -m "feat: complete PDFas enhancement implementation

- Add placeholder (empty slot) support
- Clear selection on empty area click
- Add Undo/Redo for Split operation
- Leave placeholder when moving cards
- Add Remove Empty button
- Fix multi-selection in PageEditWindow
- Hide pages when moved to main window
- Preserve page numbers until window reopened

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

**Plan complete and saved to `docs/plans/2025-01-25-pdfas-enhancement.md`. Two execution options:**

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
