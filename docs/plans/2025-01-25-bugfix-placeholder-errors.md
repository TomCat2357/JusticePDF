# PlaceholderCard AttributeError and D&D Issues Fix

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
>
> **IMPORTANT:** タスク完了直後に、そのタスクのチェックボックスを[ ]から[x]に更新してください。

**Goal:** Fix AttributeError when accessing pdf_path on PlaceholderCard objects and resolve placeholder duplication issues during D&D operations.

**Architecture:** Add type guards using isinstance() before accessing PDFCard-specific attributes. Review and fix placeholder management logic in drag-and-drop operations. Add comprehensive logging with DEBUG mode.

**Tech Stack:** PyQt6, Python 3.11+, logging module

---

### Task 1: [x] Fix AttributeError in _on_file_added and _on_file_removed

**Files:**
- Modify: `src/views/main_window.py:378-399`

**Step 1: Add type guard in _on_file_added**

In `_on_file_added` method (line 378), add type check before accessing `pdf_path`:

```python
def _on_file_added(self, path: str) -> None:
    """Handle new file added to folder."""
    # Check if card already exists
    for card in self._cards:
        if isinstance(card, PDFCard) and card.pdf_path == path:
            return
    self._add_card(path)
    self._refresh_grid()
```

**Step 2: Add type guard in _on_file_removed**

In `_on_file_removed` method (line 387), add type check before accessing `pdf_path`:

```python
def _on_file_removed(self, path: str) -> None:
    """Handle file removed from folder."""
    self._remove_card(path)
    self._refresh_grid()
    self._update_button_states()
```

**Step 3: Add type guard in _remove_card**

In `_remove_card` method (line 221), add type check:

```python
def _remove_card(self, pdf_path: str) -> None:
    """Remove a card for a PDF file."""
    for card in self._cards[:]:
        if isinstance(card, PDFCard) and card.pdf_path == pdf_path:
            if card in self._selected_cards:
                self._selected_cards.remove(card)
            self._cards.remove(card)
            card.deleteLater()
            break
```

**Step 4: Add type guard in _on_file_modified**

In `_on_file_modified` method (line 393), add type check:

```python
def _on_file_modified(self, path: str) -> None:
    """Handle file modified."""
    for card in self._cards:
        if isinstance(card, PDFCard) and card.pdf_path == path:
            card.refresh()
            break
```

**Step 5: Manual testing**

Test scenarios:
1. Import a PDF → Stack it on another PDF → Verify no AttributeError
2. Drag pages from PageEditWindow to MainWindow → Verify no AttributeError
3. Check console output for any remaining AttributeError traces

---

### Task 2: [x] Fix placeholder duplication during D&D operations

**Files:**
- Modify: `src/views/main_window.py:738-841`
- Modify: `src/views/main_window.py:914-955`

**Step 1: Review _handle_card_drop logic**

Analyze the current implementation (lines 738-793) to identify where extra placeholders are created.

Current flow:
1. Remove source card from position
2. Insert placeholder at source position
3. Insert source card at target position
4. Call `_ensure_trailing_placeholder()`

**Issue:** When dropping between cards, this might create extra placeholders.

**Step 2: Fix _handle_card_drop to prevent duplication**

Replace the implementation with clearer logic:

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

    # Check if we're dropping on a PlaceholderCard
    target_card = None
    if target_idx < len(self._cards):
        target_card = self._cards[target_idx]

    old_cards = self._cards[:]  # Undo用のコピー

    # Remove source card
    self._cards.pop(source_idx)

    # Adjust target index if removing source affected it
    adjusted_target_idx = target_idx
    if target_idx > source_idx:
        adjusted_target_idx -= 1

    # If dropping on a PlaceholderCard, replace it; otherwise insert
    if isinstance(target_card, PlaceholderCard):
        # Replace the placeholder with the source card
        self._cards.pop(adjusted_target_idx)
        target_card.deleteLater()
        self._cards.insert(adjusted_target_idx, source_card)
        # Add placeholder at source position
        placeholder = self._create_placeholder()
        self._cards.insert(source_idx, placeholder)
    else:
        # Insert at target position and add placeholder at source
        placeholder = self._create_placeholder()
        self._cards.insert(source_idx, placeholder)
        self._cards.insert(adjusted_target_idx, source_card)

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

**Step 3: Review _ensure_trailing_placeholder logic**

Check the implementation (lines 914-955) for issues:

Current logic:
- Counts trailing placeholders
- Removes extras if > 1
- Adds one if == 0

**Potential issue:** The logic might not handle mid-list placeholders correctly.

**Step 4: Enhance _ensure_trailing_placeholder with better cleanup**

Add logging and defensive checks:

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
    trailing_placeholder_indices = []
    for i in range(len(self._cards) - 1, -1, -1):
        card = self._cards[i]
        if isinstance(card, PlaceholderCard):
            trailing_placeholders += 1
            trailing_placeholder_indices.append(i)
        else:
            break

    # 末尾Placeholderが0個なら1個追加
    if trailing_placeholders == 0:
        placeholder = self._create_placeholder()
        self._cards.append(placeholder)
    # 末尾Placeholderが2個以上なら1個に縮退
    elif trailing_placeholders > 1:
        # Keep only the last one, remove others
        indices_to_remove = trailing_placeholder_indices[1:]  # Skip the last (first in reversed list)
        for idx in sorted(indices_to_remove, reverse=True):
            card = self._cards[idx]
            if card in self._selected_cards:
                self._selected_cards.remove(card)
            self._cards.pop(idx)
            card.deleteLater()
```

**Step 5: Manual testing of D&D scenarios**

Test the following scenarios:
1. Drag PDF to the end (empty placeholder)
2. Drag PDF between two PDFs
3. Drag PDF to a middle placeholder
4. Drag placeholder to another placeholder
5. Move multiple PDFs and check placeholder count after each operation

Expected: Exactly one trailing placeholder after each operation (if PDFs exist).

---

### Task 3: [x] Add defensive pdf_path property to PlaceholderCard

**Files:**
- Modify: `src/views/placeholder_card.py:50-58`

**Step 1: Add pdf_path property that returns None**

Add this property after the `is_placeholder` property:

```python
@property
def is_placeholder(self) -> bool:
    """Identify as placeholder."""
    return True

@property
def pdf_path(self) -> None:
    """PlaceholderCard doesn't have a pdf_path. Returns None for type compatibility."""
    return None
```

**Rationale:** This provides a safe fallback if any code accidentally accesses `pdf_path` on a PlaceholderCard without type checking.

**Step 2: Add filename property**

Add this for consistency with PDFCard interface:

```python
@property
def filename(self) -> str:
    """PlaceholderCard doesn't have a filename."""
    return "(empty)"
```

**Step 3: Manual verification**

Run the application and verify:
- PlaceholderCard.pdf_path returns None
- PlaceholderCard.filename returns "(empty)"
- No AttributeError is raised

---

### Task 4: [x] Add comprehensive logging for debugging

**Files:**
- Modify: `src/views/main_window.py:914-955`

**Step 1: Add debug logging to _ensure_trailing_placeholder**

```python
def _ensure_trailing_placeholder(self) -> None:
    """Ensure exactly one placeholder at the end if there are PDFs."""
    # Debug: Log current state
    pdf_count = len([c for c in self._cards if isinstance(c, PDFCard)])
    placeholder_count = len([c for c in self._cards if isinstance(c, PlaceholderCard)])

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
    trailing_placeholder_indices = []
    for i in range(len(self._cards) - 1, -1, -1):
        card = self._cards[i]
        if isinstance(card, PlaceholderCard):
            trailing_placeholders += 1
            trailing_placeholder_indices.append(i)
        else:
            break

    # Debug: Log placeholder state
    # Uncomment for debugging:
    # print(f"[_ensure_trailing_placeholder] PDFs: {pdf_count}, Placeholders: {placeholder_count}, Trailing: {trailing_placeholders}")

    # 末尾Placeholderが0個なら1個追加
    if trailing_placeholders == 0:
        placeholder = self._create_placeholder()
        self._cards.append(placeholder)
    # 末尾Placeholderが2個以上なら1個に縮退
    elif trailing_placeholders > 1:
        # Keep only the last one, remove others
        indices_to_remove = trailing_placeholder_indices[1:]  # Skip the last (first in reversed list)
        for idx in sorted(indices_to_remove, reverse=True):
            card = self._cards[idx]
            if card in self._selected_cards:
                self._selected_cards.remove(card)
            self._cards.pop(idx)
            card.deleteLater()
```

**Step 2: Manual testing with logging enabled**

Uncomment the debug print statements and test:
1. All D&D operations from memo2.md
2. Verify placeholder counts in console output
3. Comment out logging after verification

---

### Task 5: [ ] Integration testing

**Files:**
- Test manually

**Step 1: Test all reported scenarios from memo2.md**

1. **Stack PDF on another PDF:**
   - Import two PDFs
   - Drag one onto the other
   - Expected: Merge completes, no AttributeError

2. **Move PDF between PDFs:**
   - Have 3+ PDFs
   - Drag middle PDF to different position
   - Expected: Reorder works, exactly one trailing placeholder

3. **Drag PDF to placeholder:**
   - Create some placeholders
   - Drag PDF onto middle placeholder
   - Expected: Placeholder replaced, source position gets placeholder

4. **Drag page from PageEditWindow to MainWindow:**
   - Open a PDF in PageEditWindow
   - Drag a page thumbnail to MainWindow
   - Expected: Page extracted to new PDF, no AttributeError

**Step 2: Verify placeholder count**

After each operation:
- Count placeholders in grid
- Expected: Maximum one trailing placeholder (or none if no PDFs)

**Step 3: Document any remaining issues**

If issues persist:
- Note exact reproduction steps
- Capture error traces
- Update plan with additional tasks

---

### Task 6: [x] Code review and cleanup

**Files:**
- Review: `src/views/main_window.py`

**Step 1: Review all isinstance checks**

Search for all locations accessing card properties:
```bash
# Use Grep to find all card.pdf_path accesses
```

Verify each has proper type guard.

**Step 2: Review all _cards modifications**

Find all locations that:
- Add to `self._cards`
- Remove from `self._cards`
- Modify `self._cards` order

Verify each calls `_ensure_trailing_placeholder()` afterward.

**Step 3: Add type hints where missing**

Ensure all methods have proper type hints, especially:
- Methods returning cards
- Methods accepting cards as parameters

**Step 4: Final manual test**

Run complete test suite:
1. Import PDFs
2. Reorder via D&D
3. Stack PDFs
4. Split PDFs
5. Delete PDFs
6. Undo/Redo operations
7. Extract pages from PageEditWindow

Expected: No AttributeErrors, stable placeholder count.

---

### Task 7: [x] Implement DEBUG mode with logging.debug

**Files:**
- Modify: `src/views/main_window.py:1-30`
- Modify: `src/views/main_window.py` (add logging to key methods)
- Create: `src/utils/debug_logger.py` (optional helper module)

**Goal:** Add comprehensive debug logging to track all card operations and state changes, controllable via environment variable.

**Step 1: Setup logging configuration**

Add logging import and configuration at the top of `main_window.py`:

```python
# src/views/main_window.py
"""Main window for PDFas application."""
import os
import logging
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QScrollArea, QGridLayout,
    QFileDialog, QInputDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QKeySequence
from send2trash import send2trash

from src.views.pdf_card import PDFCard
from src.views.placeholder_card import PlaceholderCard, PLACEHOLDER_MIME_TYPE
from src.controllers.folder_watcher import FolderWatcher
from src.models.undo_manager import UndoManager, UndoAction
from src.utils.pdf_utils import create_empty_pdf, rotate_pages, get_page_count

# Setup logging
logger = logging.getLogger(__name__)

# Configure logging level from environment variable
# Set PDFAS_DEBUG=1 to enable debug logging
if os.environ.get('PDFAS_DEBUG', '0') == '1':
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s',
        datefmt='%H:%M:%S'
    )
else:
    logging.basicConfig(level=logging.INFO)
```

**Step 2: Add state logging helper method**

Add a helper method in `MainWindow` class to log current state:

```python
def _log_state(self, operation: str) -> None:
    """Log current card state for debugging."""
    pdf_count = len([c for c in self._cards if isinstance(c, PDFCard)])
    placeholder_count = len([c for c in self._cards if isinstance(c, PlaceholderCard)])
    selected_count = len(self._selected_cards)

    # Build card layout string
    card_layout = []
    for i, card in enumerate(self._cards):
        if isinstance(card, PDFCard):
            card_layout.append(f"PDF({card.filename})")
        else:
            card_layout.append("Empty")

    logger.debug(
        f"[{operation}] Cards: {len(self._cards)}, "
        f"PDFs: {pdf_count}, Placeholders: {placeholder_count}, "
        f"Selected: {selected_count} | Layout: [{', '.join(card_layout)}]"
    )
```

**Step 3: Add logging to card management methods**

Add logging calls to key methods:

1. **_add_card:**
```python
def _add_card(self, pdf_path: str) -> PDFCard:
    """Add a new card for a PDF file."""
    logger.debug(f"Adding card for: {os.path.basename(pdf_path)}")
    card = PDFCard(pdf_path)
    card.clicked.connect(self._on_card_clicked)
    card.double_clicked.connect(self._on_card_double_clicked)
    card.dropped_on.connect(self._on_card_merge)
    self._cards.append(card)
    self._log_state("add_card")
    return card
```

2. **_remove_card:**
```python
def _remove_card(self, pdf_path: str) -> None:
    """Remove a card for a PDF file."""
    logger.debug(f"Removing card for: {os.path.basename(pdf_path)}")
    for card in self._cards[:]:
        if isinstance(card, PDFCard) and card.pdf_path == pdf_path:
            if card in self._selected_cards:
                self._selected_cards.remove(card)
            self._cards.remove(card)
            card.deleteLater()
            break
    self._log_state("remove_card")
```

3. **_ensure_trailing_placeholder:**
```python
def _ensure_trailing_placeholder(self) -> None:
    """Ensure exactly one placeholder at the end if there are PDFs."""
    pdf_cards = [c for c in self._cards if isinstance(c, PDFCard)]
    placeholder_count = len([c for c in self._cards if isinstance(c, PlaceholderCard)])

    logger.debug(f"ensure_trailing_placeholder: PDFs={len(pdf_cards)}, Placeholders={placeholder_count}")

    if not pdf_cards:
        # 全てのPlaceholderを削除
        for card in self._cards[:]:
            if isinstance(card, PlaceholderCard):
                if card in self._selected_cards:
                    self._selected_cards.remove(card)
                self._cards.remove(card)
                card.deleteLater()
        logger.debug("Removed all placeholders (no PDFs)")
        return

    # 末尾のPlaceholderを確認
    trailing_placeholders = 0
    trailing_placeholder_indices = []
    for i in range(len(self._cards) - 1, -1, -1):
        card = self._cards[i]
        if isinstance(card, PlaceholderCard):
            trailing_placeholders += 1
            trailing_placeholder_indices.append(i)
        else:
            break

    logger.debug(f"Trailing placeholders: {trailing_placeholders}")

    # 末尾Placeholderが0個なら1個追加
    if trailing_placeholders == 0:
        placeholder = self._create_placeholder()
        self._cards.append(placeholder)
        logger.debug("Added trailing placeholder")
    # 末尾Placeholderが2個以上なら1個に縮退
    elif trailing_placeholders > 1:
        # Keep only the last one, remove others
        indices_to_remove = trailing_placeholder_indices[1:]
        logger.debug(f"Removing {len(indices_to_remove)} excess trailing placeholders")
        for idx in sorted(indices_to_remove, reverse=True):
            card = self._cards[idx]
            if card in self._selected_cards:
                self._selected_cards.remove(card)
            self._cards.pop(idx)
            card.deleteLater()

    self._log_state("ensure_trailing_placeholder")
```

**Step 4: Add logging to D&D operations**

1. **_handle_card_drop:**
```python
def _handle_card_drop(self, source_path: str, drop_pos) -> None:
    """Handle internal card drop for reordering."""
    logger.debug(f"Card drop: source={os.path.basename(source_path)}, pos={drop_pos}")

    # Find source card
    source_card = None
    source_idx = -1
    for i, card in enumerate(self._cards):
        if isinstance(card, PDFCard) and card.pdf_path == source_path:
            source_card = card
            source_idx = i
            break

    if source_card is None:
        logger.warning(f"Source card not found: {source_path}")
        return

    # Find target position
    target_idx = self._get_drop_index(drop_pos)
    logger.debug(f"Drop indices: source={source_idx}, target={target_idx}")

    if target_idx == -1 or target_idx == source_idx:
        logger.debug("Drop cancelled: invalid target or same position")
        return

    # ... rest of implementation ...

    self._log_state("handle_card_drop")
```

2. **_handle_placeholder_drop:**
```python
def _handle_placeholder_drop(self, drop_pos) -> None:
    """Handle placeholder reordering."""
    logger.debug(f"Placeholder drop at pos={drop_pos}")

    # ... implementation ...

    self._log_state("handle_placeholder_drop")
```

3. **_on_placeholder_drop:**
```python
def _on_placeholder_drop(self, placeholder: PlaceholderCard, source_path: str) -> None:
    """Handle drop on placeholder."""
    logger.debug(f"Drop on placeholder: source_path={source_path if source_path else 'placeholder'}")

    # ... implementation ...

    self._log_state("on_placeholder_drop")
```

**Step 5: Add logging to selection operations**

```python
def _on_card_clicked(self, card: PDFCard) -> None:
    """Handle card click."""
    from PyQt6.QtWidgets import QApplication
    modifiers = QApplication.keyboardModifiers()

    card_name = card.filename if isinstance(card, PDFCard) else "(empty)"
    logger.debug(f"Card clicked: {card_name}, modifiers={modifiers}")

    # ... rest of implementation ...

    self._log_state("card_clicked")
```

**Step 6: Document DEBUG mode usage**

Add to README or create a DEBUG.md file:

```markdown
## DEBUG Mode

To enable detailed debug logging:

**Windows:**
```cmd
set PDFAS_DEBUG=1
python -m src.main
```

**Linux/Mac:**
```bash
PDFAS_DEBUG=1 python -m src.main
```

Debug logs will show:
- Card additions/removals with file names
- D&D operations with source/target positions
- Placeholder management operations
- Current state after each operation (PDF count, placeholder count, layout)
- Selection changes

Logs are printed to console with timestamp and line number for easy tracking.
```

**Step 7: Manual testing with DEBUG mode**

1. Enable DEBUG mode: `set PDFAS_DEBUG=1`
2. Run application
3. Perform operations from memo2.md
4. Verify console output shows detailed operation traces
5. Confirm state logging helps identify issues

Expected log output example:
```
14:23:15 [DEBUG] main_window:212 - Adding card for: document1.pdf
14:23:15 [DEBUG] main_window:150 - [add_card] Cards: 1, PDFs: 1, Placeholders: 0, Selected: 0 | Layout: [PDF(document1.pdf)]
14:23:15 [DEBUG] main_window:920 - ensure_trailing_placeholder: PDFs=1, Placeholders=0
14:23:15 [DEBUG] main_window:945 - Added trailing placeholder
14:23:15 [DEBUG] main_window:150 - [ensure_trailing_placeholder] Cards: 2, PDFs: 1, Placeholders: 1, Selected: 0 | Layout: [PDF(document1.pdf), Empty]
```

---

## Testing Checklist

- [ ] No AttributeError when stacking PDFs
- [ ] No AttributeError when dragging pages from PageEditWindow
- [ ] Exactly one trailing placeholder after reorder operations
- [ ] Placeholders don't duplicate when moving PDFs
- [ ] Dropping on placeholder works correctly
- [ ] Undo/Redo maintains correct placeholder count
- [ ] All isinstance checks in place
- [ ] Type hints added where needed
- [ ] DEBUG mode works with PDFAS_DEBUG=1
- [ ] Debug logs show operation details and state changes
- [ ] Debug logs help identify issues during testing

## Completion Criteria

1. All AttributeError traces from memo2.md are resolved
2. Placeholder count remains stable (max 1 trailing) during all D&D operations
3. Manual testing confirms no regressions
4. Code review shows proper type guards throughout
5. DEBUG mode implemented and functional with comprehensive logging
6. Debug logs provide clear visibility into operation flow and state changes
