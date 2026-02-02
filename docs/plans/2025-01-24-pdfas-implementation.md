# PDFas Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a desktop app to visually merge/split PDFs using drag-and-drop operations.

**Architecture:** PyQt6 GUI with PyMuPDF for PDF operations. Main window shows PDF cards in a grid, page edit windows allow page-level manipulation. All operations auto-save to `$HOME/Documents/PDFas/` folder with full Undo/Redo support.

**Tech Stack:** Python 3.x, PyQt6, PyMuPDF (fitz), watchdog (folder monitoring), send2trash (Windows recycle bin)

---

## Phase 1: Project Setup & Core Infrastructure

### Task 1: Project Setup

**Files:**
- Create: `pyproject.toml`
- Create: `src/main.py`
- Create: `src/__init__.py`
- Create: `src/models/__init__.py`
- Create: `src/views/__init__.py`
- Create: `src/controllers/__init__.py`
- Create: `src/utils/__init__.py`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "chestpdf"
version = "0.1.0"
description = "PDFas - Visual PDF merge/split application."
requires-python = ">=3.10"
dependencies = [
    "PyQt6>=6.6.0",
    "PyMuPDF>=1.23.0",
    "watchdog>=3.0.0",
    "send2trash>=1.8.0",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
```

**Step 2: Create directory structure**

```bash
mkdir -p src/models src/views src/controllers src/utils tests
```

**Step 3: Create src/__init__.py**

```python
"""PDFas - Visual PDF merge/split application."""
```

**Step 4: Create empty __init__.py files**

```python
# src/models/__init__.py, src/views/__init__.py, etc.
"""Module init."""
```

**Step 5: Create minimal main.py**

```python
"""PDFas application entry point."""
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel


def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("PDFas")
    window.setCentralWidget(QLabel("PDFas - Loading..."))
    window.resize(800, 600)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

**Step 6: Install dependencies and verify**

```bash
uv venv .venv
uv sync
python src/main.py
```

Expected: Window opens with "PDFas - Loading..." text.

**Step 7: Commit**

```bash
git add .
git commit -m "feat: initial project setup with PyQt6"
```

---

### Task 2: PDF Utilities

**Files:**
- Create: `src/utils/pdf_utils.py`
- Create: `tests/test_pdf_utils.py`

**Step 1: Write failing test for thumbnail generation**

```python
# tests/test_pdf_utils.py
"""Tests for PDF utilities."""
import os
import tempfile
import fitz
import pytest
from src.utils.pdf_utils import get_thumbnail, get_page_count, create_empty_pdf


@pytest.fixture
def sample_pdf():
    """Create a sample PDF for testing."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 100), "Test Page 1")
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 100), "Test Page 2")
        doc.save(f.name)
        doc.close()
        yield f.name
    os.unlink(f.name)


def test_get_thumbnail_returns_qpixmap(sample_pdf):
    """Test that get_thumbnail returns a QPixmap."""
    from PyQt6.QtGui import QPixmap
    thumbnail = get_thumbnail(sample_pdf, size=128)
    assert isinstance(thumbnail, QPixmap)
    assert not thumbnail.isNull()


def test_get_page_count(sample_pdf):
    """Test that get_page_count returns correct count."""
    count = get_page_count(sample_pdf)
    assert count == 2


def test_create_empty_pdf():
    """Test that create_empty_pdf creates a 0-page PDF."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "empty.pdf")
        create_empty_pdf(path)
        assert os.path.exists(path)
        assert get_page_count(path) == 0
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_pdf_utils.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement pdf_utils.py**

```python
# src/utils/pdf_utils.py
"""PDF utility functions using PyMuPDF."""
import fitz
from PyQt6.QtGui import QPixmap, QImage


def get_thumbnail(pdf_path: str, size: int = 128) -> QPixmap:
    """Generate a thumbnail of the first page of a PDF.

    Args:
        pdf_path: Path to the PDF file.
        size: Maximum dimension (width or height) of the thumbnail.

    Returns:
        QPixmap of the first page, or empty QPixmap if PDF has no pages.
    """
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return QPixmap()

        page = doc[0]
        # Calculate zoom to fit within size
        zoom = size / max(page.rect.width, page.rect.height)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        doc.close()

        # Convert to QPixmap
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)
    except Exception:
        return QPixmap()


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Number of pages, or 0 if file cannot be read.
    """
    try:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


def create_empty_pdf(pdf_path: str) -> None:
    """Create an empty PDF with 0 pages.

    Args:
        pdf_path: Path where the PDF will be created.
    """
    doc = fitz.open()
    doc.save(pdf_path)
    doc.close()


def get_page_thumbnail(pdf_path: str, page_num: int, size: int = 128) -> QPixmap:
    """Generate a thumbnail of a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page_num: 0-indexed page number.
        size: Maximum dimension of the thumbnail.

    Returns:
        QPixmap of the page, or empty QPixmap on error.
    """
    try:
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            doc.close()
            return QPixmap()

        page = doc[page_num]
        zoom = size / max(page.rect.width, page.rect.height)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        doc.close()

        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)
    except Exception:
        return QPixmap()


def merge_pdfs(output_path: str, pdf_paths: list[str]) -> None:
    """Merge multiple PDFs into one.

    Args:
        output_path: Path for the merged PDF.
        pdf_paths: List of PDF paths to merge in order.
    """
    output_doc = fitz.open()
    for path in pdf_paths:
        src_doc = fitz.open(path)
        output_doc.insert_pdf(src_doc)
        src_doc.close()
    output_doc.save(output_path)
    output_doc.close()


def extract_pages(src_path: str, output_path: str, page_indices: list[int]) -> None:
    """Extract specific pages from a PDF to a new file.

    Args:
        src_path: Source PDF path.
        output_path: Output PDF path.
        page_indices: 0-indexed page numbers to extract.
    """
    src_doc = fitz.open(src_path)
    output_doc = fitz.open()
    for idx in page_indices:
        if 0 <= idx < len(src_doc):
            output_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)
    output_doc.save(output_path)
    output_doc.close()
    src_doc.close()


def remove_pages(pdf_path: str, page_indices: list[int]) -> None:
    """Remove specific pages from a PDF (in place).

    Args:
        pdf_path: Path to the PDF.
        page_indices: 0-indexed page numbers to remove.
    """
    doc = fitz.open(pdf_path)
    # Remove in reverse order to maintain indices
    for idx in sorted(page_indices, reverse=True):
        if 0 <= idx < len(doc):
            doc.delete_page(idx)
    doc.saveIncr()
    doc.close()


def rotate_pages(pdf_path: str, page_indices: list[int], angle: int = 90) -> None:
    """Rotate specific pages in a PDF (in place).

    Args:
        pdf_path: Path to the PDF.
        page_indices: 0-indexed page numbers to rotate.
        angle: Rotation angle (90, 180, 270).
    """
    doc = fitz.open(pdf_path)
    for idx in page_indices:
        if 0 <= idx < len(doc):
            page = doc[idx]
            page.set_rotation((page.rotation + angle) % 360)
    doc.saveIncr()
    doc.close()


def reorder_pages(pdf_path: str, new_order: list[int]) -> None:
    """Reorder pages in a PDF (in place).

    Args:
        pdf_path: Path to the PDF.
        new_order: List of current page indices in new order.
    """
    doc = fitz.open(pdf_path)
    doc.select(new_order)
    doc.saveIncr()
    doc.close()
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pdf_utils.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/utils/pdf_utils.py tests/test_pdf_utils.py
git commit -m "feat: add PDF utility functions"
```

---

### Task 3: Undo Manager

**Files:**
- Create: `src/models/undo_manager.py`
- Create: `tests/test_undo_manager.py`

**Step 1: Write failing test**

```python
# tests/test_undo_manager.py
"""Tests for Undo/Redo manager."""
import pytest
from src.models.undo_manager import UndoManager, UndoAction


def test_undo_manager_can_undo_after_action():
    """Test that undo is available after adding an action."""
    manager = UndoManager()
    action = UndoAction(
        description="Test action",
        undo_func=lambda: None,
        redo_func=lambda: None
    )
    manager.add_action(action)
    assert manager.can_undo()
    assert not manager.can_redo()


def test_undo_manager_executes_undo():
    """Test that undo executes the undo function."""
    result = []
    manager = UndoManager()
    action = UndoAction(
        description="Add item",
        undo_func=lambda: result.append("undone"),
        redo_func=lambda: result.append("redone")
    )
    manager.add_action(action)
    manager.undo()
    assert result == ["undone"]
    assert manager.can_redo()


def test_undo_manager_executes_redo():
    """Test that redo executes the redo function."""
    result = []
    manager = UndoManager()
    action = UndoAction(
        description="Add item",
        undo_func=lambda: result.append("undone"),
        redo_func=lambda: result.append("redone")
    )
    manager.add_action(action)
    manager.undo()
    manager.redo()
    assert result == ["undone", "redone"]


def test_undo_manager_clears_redo_on_new_action():
    """Test that adding a new action clears redo stack."""
    manager = UndoManager()
    action1 = UndoAction("Action 1", lambda: None, lambda: None)
    action2 = UndoAction("Action 2", lambda: None, lambda: None)
    manager.add_action(action1)
    manager.undo()
    manager.add_action(action2)
    assert not manager.can_redo()


def test_undo_manager_respects_max_size():
    """Test that undo stack respects maximum size."""
    manager = UndoManager(max_size=3)
    for i in range(5):
        manager.add_action(UndoAction(f"Action {i}", lambda: None, lambda: None))

    # Should only be able to undo 3 times
    count = 0
    while manager.can_undo():
        manager.undo()
        count += 1
    assert count == 3
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_undo_manager.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement undo_manager.py**

```python
# src/models/undo_manager.py
"""Undo/Redo manager for PDFas operations."""
from dataclasses import dataclass
from typing import Callable
from collections import deque


@dataclass
class UndoAction:
    """Represents a single undoable action."""
    description: str
    undo_func: Callable[[], None]
    redo_func: Callable[[], None]


class UndoManager:
    """Manages undo/redo operations.

    Thread-safe undo/redo stack with configurable maximum size.
    """

    def __init__(self, max_size: int = 100):
        """Initialize the undo manager.

        Args:
            max_size: Maximum number of actions to keep in undo history.
        """
        self._undo_stack: deque[UndoAction] = deque(maxlen=max_size)
        self._redo_stack: list[UndoAction] = []
        self._max_size = max_size

    def add_action(self, action: UndoAction) -> None:
        """Add a new action to the undo stack.

        Clears the redo stack when a new action is added.

        Args:
            action: The action to add.
        """
        self._undo_stack.append(action)
        self._redo_stack.clear()

    def can_undo(self) -> bool:
        """Check if there are actions to undo."""
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        """Check if there are actions to redo."""
        return len(self._redo_stack) > 0

    def undo(self) -> str | None:
        """Undo the last action.

        Returns:
            Description of the undone action, or None if nothing to undo.
        """
        if not self.can_undo():
            return None

        action = self._undo_stack.pop()
        action.undo_func()
        self._redo_stack.append(action)
        return action.description

    def redo(self) -> str | None:
        """Redo the last undone action.

        Returns:
            Description of the redone action, or None if nothing to redo.
        """
        if not self.can_redo():
            return None

        action = self._redo_stack.pop()
        action.redo_func()
        self._undo_stack.append(action)
        return action.description

    def clear(self) -> None:
        """Clear all undo/redo history."""
        self._undo_stack.clear()
        self._redo_stack.clear()

    def get_undo_description(self) -> str | None:
        """Get the description of the next action to undo."""
        if self._undo_stack:
            return self._undo_stack[-1].description
        return None

    def get_redo_description(self) -> str | None:
        """Get the description of the next action to redo."""
        if self._redo_stack:
            return self._redo_stack[-1].description
        return None
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_undo_manager.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/models/undo_manager.py tests/test_undo_manager.py
git commit -m "feat: add Undo/Redo manager"
```

---

### Task 4: Folder Watcher

**Files:**
- Create: `src/controllers/folder_watcher.py`
- Create: `tests/test_folder_watcher.py`

**Step 1: Write failing test**

```python
# tests/test_folder_watcher.py
"""Tests for folder watcher."""
import os
import tempfile
import time
import pytest
from PyQt6.QtCore import QCoreApplication
from src.controllers.folder_watcher import FolderWatcher


@pytest.fixture
def app():
    """Create QApplication for signal testing."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


@pytest.fixture
def temp_folder():
    """Create a temporary folder for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_folder_watcher_emits_file_added(app, temp_folder):
    """Test that file_added signal is emitted when a file is added."""
    watcher = FolderWatcher(temp_folder)
    watcher.start()

    added_files = []
    watcher.file_added.connect(lambda path: added_files.append(path))

    # Create a PDF file
    pdf_path = os.path.join(temp_folder, "test.pdf")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4")

    # Process events
    time.sleep(0.5)
    app.processEvents()

    watcher.stop()

    assert len(added_files) == 1
    assert added_files[0] == pdf_path


def test_folder_watcher_ignores_non_pdf(app, temp_folder):
    """Test that non-PDF files are ignored."""
    watcher = FolderWatcher(temp_folder)
    watcher.start()

    added_files = []
    watcher.file_added.connect(lambda path: added_files.append(path))

    # Create a non-PDF file
    txt_path = os.path.join(temp_folder, "test.txt")
    with open(txt_path, "w") as f:
        f.write("test")

    time.sleep(0.5)
    app.processEvents()

    watcher.stop()

    assert len(added_files) == 0
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_folder_watcher.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement folder_watcher.py**

```python
# src/controllers/folder_watcher.py
"""Folder watcher for monitoring PDF changes."""
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from PyQt6.QtCore import QObject, pyqtSignal


class PDFEventHandler(FileSystemEventHandler):
    """Handle file system events for PDF files."""

    def __init__(self, watcher: "FolderWatcher"):
        super().__init__()
        self._watcher = watcher

    def _is_pdf(self, path: str) -> bool:
        """Check if the path is a PDF file."""
        return path.lower().endswith(".pdf")

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_added.emit(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_removed.emit(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_modified.emit(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            if self._is_pdf(event.src_path):
                self._watcher.file_removed.emit(event.src_path)
            if self._is_pdf(event.dest_path):
                self._watcher.file_added.emit(event.dest_path)


class FolderWatcher(QObject):
    """Watch a folder for PDF file changes.

    Emits Qt signals when PDF files are added, removed, or modified.
    """

    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)

    def __init__(self, folder_path: str, parent=None):
        super().__init__(parent)
        self._folder_path = folder_path
        self._observer = Observer()
        self._handler = PDFEventHandler(self)

    @property
    def folder_path(self) -> str:
        """Get the watched folder path."""
        return self._folder_path

    def start(self) -> None:
        """Start watching the folder."""
        self._observer.schedule(self._handler, self._folder_path, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        """Stop watching the folder."""
        self._observer.stop()
        self._observer.join(timeout=1)

    def get_pdf_files(self) -> list[str]:
        """Get all PDF files currently in the folder.

        Returns:
            List of absolute paths to PDF files.
        """
        files = []
        for filename in os.listdir(self._folder_path):
            if filename.lower().endswith(".pdf"):
                files.append(os.path.join(self._folder_path, filename))
        return files
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_folder_watcher.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/controllers/folder_watcher.py tests/test_folder_watcher.py
git commit -m "feat: add folder watcher for PDF monitoring"
```

---

## Phase 2: Main Window Basic UI

### Task 5: PDF Card Widget

**Files:**
- Create: `src/views/pdf_card.py`
- Create: `tests/test_pdf_card.py`

**Step 1: Write failing test**

```python
# tests/test_pdf_card.py
"""Tests for PDF card widget."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.views.pdf_card import PDFCard


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sample_pdf():
    """Create a sample PDF for testing."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((100, 100), "Test")
        doc.save(f.name)
        doc.close()
        yield f.name
    os.unlink(f.name)


def test_pdf_card_displays_filename(app, sample_pdf):
    """Test that PDF card displays the filename."""
    card = PDFCard(sample_pdf)
    assert os.path.basename(sample_pdf) in card.filename


def test_pdf_card_displays_page_count(app, sample_pdf):
    """Test that PDF card displays page count."""
    card = PDFCard(sample_pdf)
    assert card.page_count == 1


def test_pdf_card_is_selectable(app, sample_pdf):
    """Test that PDF card can be selected."""
    card = PDFCard(sample_pdf)
    assert not card.is_selected
    card.set_selected(True)
    assert card.is_selected
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_pdf_card.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement pdf_card.py**

```python
# src/views/pdf_card.py
"""PDF card widget for displaying PDF files."""
import os
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from src.utils.pdf_utils import get_thumbnail, get_page_count


class PDFCard(QFrame):
    """Widget representing a PDF file as a card.

    Displays a thumbnail of the first page, page count, and filename.
    Supports selection and drag-and-drop operations.
    """

    clicked = pyqtSignal(object)  # Emits self when clicked
    double_clicked = pyqtSignal(object)  # Emits self when double-clicked

    CARD_WIDTH = 150
    THUMBNAIL_SIZE = 120

    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._is_selected = False
        self._page_count = 0

        self._setup_ui()
        self._load_pdf_info()

    def _setup_ui(self) -> None:
        """Set up the card UI."""
        self.setFixedWidth(self.CARD_WIDTH)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Thumbnail
        self._thumbnail_label = QLabel()
        self._thumbnail_label.setFixedSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE)
        self._thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ccc;")
        layout.addWidget(self._thumbnail_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Page count
        self._page_count_label = QLabel("0 pages")
        self._page_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._page_count_label)

        # Filename
        self._filename_label = QLabel()
        self._filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._filename_label.setWordWrap(True)
        layout.addWidget(self._filename_label)

        self._update_style()

    def _load_pdf_info(self) -> None:
        """Load PDF information and thumbnail."""
        # Thumbnail
        thumbnail = get_thumbnail(self._pdf_path, self.THUMBNAIL_SIZE)
        if not thumbnail.isNull():
            self._thumbnail_label.setPixmap(thumbnail)
        else:
            self._thumbnail_label.setText("(empty)")

        # Page count
        self._page_count = get_page_count(self._pdf_path)
        page_text = f"{self._page_count} page" if self._page_count == 1 else f"{self._page_count} pages"
        self._page_count_label.setText(page_text)

        # Filename
        self._filename_label.setText(os.path.basename(self._pdf_path))

    def _update_style(self) -> None:
        """Update the card style based on selection state."""
        if self._is_selected:
            self.setStyleSheet("PDFCard { background-color: #cce5ff; border: 2px solid #007bff; }")
        else:
            self.setStyleSheet("PDFCard { background-color: white; border: 1px solid #ccc; }")

    @property
    def pdf_path(self) -> str:
        """Get the PDF file path."""
        return self._pdf_path

    @property
    def filename(self) -> str:
        """Get the PDF filename."""
        return os.path.basename(self._pdf_path)

    @property
    def page_count(self) -> int:
        """Get the page count."""
        return self._page_count

    @property
    def is_selected(self) -> bool:
        """Check if the card is selected."""
        return self._is_selected

    def set_selected(self, selected: bool) -> None:
        """Set the selection state."""
        self._is_selected = selected
        self._update_style()

    def refresh(self) -> None:
        """Refresh the card display."""
        self._load_pdf_info()

    def mousePressEvent(self, event) -> None:
        """Handle mouse press events."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Handle double-click events."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self)
        super().mouseDoubleClickEvent(event)
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pdf_card.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/views/pdf_card.py tests/test_pdf_card.py
git commit -m "feat: add PDF card widget"
```

---

### Task 6: Main Window Basic Layout

**Files:**
- Create: `src/views/main_window.py`
- Modify: `src/main.py`

**Step 1: Implement main_window.py**

```python
# src/views/main_window.py
"""Main window for PDFas application."""
import os
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
from src.controllers.folder_watcher import FolderWatcher
from src.models.undo_manager import UndoManager
from src.utils.pdf_utils import create_empty_pdf, rotate_pages, get_page_count


class MainWindow(QMainWindow):
    """Main application window.

    Displays PDF files as cards in a grid layout.
    """

    def __init__(self):
        super().__init__()
        self._cards: list[PDFCard] = []
        self._selected_cards: list[PDFCard] = []
        self._sort_order = "name"  # "name" or "date"
        self._sort_ascending = True

        # Setup working directory
        self._work_dir = Path.home() / "Documents" / "PDFas"
        self._work_dir.mkdir(parents=True, exist_ok=True)

        # Undo manager
        self._undo_manager = UndoManager()

        # Setup UI
        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()

        # Setup folder watcher
        self._watcher = FolderWatcher(str(self._work_dir))
        self._watcher.file_added.connect(self._on_file_added)
        self._watcher.file_removed.connect(self._on_file_removed)
        self._watcher.file_modified.connect(self._on_file_modified)
        self._watcher.start()

        # Load existing files
        self._load_existing_files()

    def _setup_ui(self) -> None:
        """Set up the main UI."""
        self.setWindowTitle("PDFas")
        self.resize(1000, 700)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll)

        # Container for grid
        self._container = QWidget()
        scroll.setWidget(self._container)

        self._grid_layout = QGridLayout(self._container)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def _setup_toolbar(self) -> None:
        """Set up the toolbar."""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Undo/Redo
        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("Redo")
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addSeparator()

        # Delete
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        # Rename
        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename)
        toolbar.addWidget(self._rename_btn)

        toolbar.addSeparator()

        # New empty PDF
        self._new_btn = QPushButton("New PDF")
        self._new_btn.clicked.connect(self._on_new_pdf)
        toolbar.addWidget(self._new_btn)

        # Import
        self._import_btn = QPushButton("Import")
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        toolbar.addSeparator()

        # Rotate
        self._rotate_btn = QPushButton("Rotate")
        self._rotate_btn.clicked.connect(self._on_rotate)
        toolbar.addWidget(self._rotate_btn)

        # Select all
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self._select_all_btn)

        # Split
        self._split_btn = QPushButton("Split")
        self._split_btn.clicked.connect(self._on_split)
        toolbar.addWidget(self._split_btn)

        toolbar.addSeparator()

        # Sort by name
        self._sort_name_btn = QPushButton("Name")
        self._sort_name_btn.clicked.connect(self._on_sort_by_name)
        toolbar.addWidget(self._sort_name_btn)

        # Sort by date
        self._sort_date_btn = QPushButton("Date")
        self._sort_date_btn.clicked.connect(self._on_sort_by_date)
        toolbar.addWidget(self._sort_date_btn)

        self._update_button_states()

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Ctrl+Z - Undo
        undo_action = QAction(self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._on_undo)
        self.addAction(undo_action)

        # Ctrl+Y - Redo
        redo_action = QAction(self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._on_redo)
        self.addAction(redo_action)

        # Delete
        delete_action = QAction(self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._on_delete)
        self.addAction(delete_action)

        # F2 - Rename
        rename_action = QAction(self)
        rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        rename_action.triggered.connect(self._on_rename)
        self.addAction(rename_action)

        # Ctrl+A - Select all
        select_all_action = QAction(self)
        select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_action.triggered.connect(self._on_select_all)
        self.addAction(select_all_action)

    def _update_button_states(self) -> None:
        """Update toolbar button enabled states."""
        has_selection = len(self._selected_cards) > 0
        self._delete_btn.setEnabled(has_selection)
        self._rename_btn.setEnabled(len(self._selected_cards) == 1)
        self._rotate_btn.setEnabled(has_selection)
        self._split_btn.setEnabled(has_selection)
        self._undo_btn.setEnabled(self._undo_manager.can_undo())
        self._redo_btn.setEnabled(self._undo_manager.can_redo())

    def _load_existing_files(self) -> None:
        """Load existing PDF files from the work directory."""
        for pdf_path in self._watcher.get_pdf_files():
            self._add_card(pdf_path)
        self._refresh_grid()

    def _add_card(self, pdf_path: str) -> PDFCard:
        """Add a new card for a PDF file."""
        card = PDFCard(pdf_path)
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)
        self._cards.append(card)
        return card

    def _remove_card(self, pdf_path: str) -> None:
        """Remove a card for a PDF file."""
        for card in self._cards[:]:
            if card.pdf_path == pdf_path:
                if card in self._selected_cards:
                    self._selected_cards.remove(card)
                self._cards.remove(card)
                card.deleteLater()
                break

    def _refresh_grid(self) -> None:
        """Refresh the grid layout."""
        # Clear grid
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        # Sort cards
        self._sort_cards()

        # Add cards to grid
        cols = max(1, self._container.width() // (PDFCard.CARD_WIDTH + 20))
        for i, card in enumerate(self._cards):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(card, row, col)

    def _sort_cards(self) -> None:
        """Sort cards based on current sort order."""
        if self._sort_order == "name":
            self._cards.sort(key=lambda c: c.filename.lower(), reverse=not self._sort_ascending)
        else:  # date
            self._cards.sort(
                key=lambda c: os.path.getmtime(c.pdf_path),
                reverse=not self._sort_ascending
            )

    def _clear_selection(self) -> None:
        """Clear all selections."""
        for card in self._selected_cards:
            card.set_selected(False)
        self._selected_cards.clear()
        self._update_button_states()

    def _on_card_clicked(self, card: PDFCard) -> None:
        """Handle card click."""
        modifiers = QApplication.keyboardModifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+click: toggle selection
            if card in self._selected_cards:
                card.set_selected(False)
                self._selected_cards.remove(card)
            else:
                card.set_selected(True)
                self._selected_cards.append(card)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Shift+click: range select
            if self._selected_cards:
                start_idx = self._cards.index(self._selected_cards[-1])
                end_idx = self._cards.index(card)
                if start_idx > end_idx:
                    start_idx, end_idx = end_idx, start_idx
                for i in range(start_idx, end_idx + 1):
                    if self._cards[i] not in self._selected_cards:
                        self._cards[i].set_selected(True)
                        self._selected_cards.append(self._cards[i])
            else:
                card.set_selected(True)
                self._selected_cards.append(card)
        else:
            # Normal click: single select
            self._clear_selection()
            card.set_selected(True)
            self._selected_cards.append(card)

        self._update_button_states()

    def _on_card_double_clicked(self, card: PDFCard) -> None:
        """Handle card double-click - open page edit window."""
        from src.views.page_edit_window import PageEditWindow
        window = PageEditWindow(card.pdf_path, self._undo_manager, self)
        window.show()

    def _on_file_added(self, path: str) -> None:
        """Handle new file added to folder."""
        # Check if card already exists
        for card in self._cards:
            if card.pdf_path == path:
                return
        self._add_card(path)
        self._refresh_grid()

    def _on_file_removed(self, path: str) -> None:
        """Handle file removed from folder."""
        self._remove_card(path)
        self._refresh_grid()
        self._update_button_states()

    def _on_file_modified(self, path: str) -> None:
        """Handle file modified."""
        for card in self._cards:
            if card.pdf_path == path:
                card.refresh()
                break

    def _on_undo(self) -> None:
        """Handle undo action."""
        self._undo_manager.undo()
        self._update_button_states()

    def _on_redo(self) -> None:
        """Handle redo action."""
        self._undo_manager.redo()
        self._update_button_states()

    def _on_delete(self) -> None:
        """Handle delete action."""
        if not self._selected_cards:
            return

        paths = [card.pdf_path for card in self._selected_cards]

        # Delete files
        for path in paths:
            send2trash(path)

        self._clear_selection()

    def _on_rename(self) -> None:
        """Handle rename action."""
        if len(self._selected_cards) != 1:
            return

        card = self._selected_cards[0]
        old_name = card.filename
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=old_name
        )

        if ok and new_name and new_name != old_name:
            if not new_name.lower().endswith(".pdf"):
                new_name += ".pdf"
            old_path = card.pdf_path
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            os.rename(old_path, new_path)

    def _on_new_pdf(self) -> None:
        """Handle new empty PDF action."""
        # Find unique name
        i = 1
        while True:
            name = f"New_{i}.pdf"
            path = self._work_dir / name
            if not path.exists():
                break
            i += 1

        create_empty_pdf(str(path))

    def _on_import(self) -> None:
        """Handle import action."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import PDF", "", "PDF Files (*.pdf)"
        )

        for src_path in paths:
            filename = os.path.basename(src_path)
            dest_path = self._work_dir / filename

            # Handle duplicate names
            i = 1
            while dest_path.exists():
                name, ext = os.path.splitext(filename)
                dest_path = self._work_dir / f"{name}_{i}{ext}"
                i += 1

            import shutil
            shutil.copy2(src_path, dest_path)

    def _on_rotate(self) -> None:
        """Handle rotate action."""
        for card in self._selected_cards:
            page_count = get_page_count(card.pdf_path)
            if page_count > 0:
                rotate_pages(card.pdf_path, list(range(page_count)), 90)
                card.refresh()

    def _on_select_all(self) -> None:
        """Handle select all action."""
        self._clear_selection()
        for card in self._cards:
            card.set_selected(True)
            self._selected_cards.append(card)
        self._update_button_states()

    def _on_split(self) -> None:
        """Handle split action - split PDFs into individual pages."""
        from src.utils.pdf_utils import extract_pages

        for card in self._selected_cards:
            page_count = get_page_count(card.pdf_path)
            if page_count <= 1:
                continue

            base_name = os.path.splitext(card.filename)[0]

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

            # Delete original
            send2trash(card.pdf_path)

    def _on_sort_by_name(self) -> None:
        """Handle sort by name."""
        if self._sort_order == "name":
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_order = "name"
            self._sort_ascending = True
        self._refresh_grid()

    def _on_sort_by_date(self) -> None:
        """Handle sort by date."""
        if self._sort_order == "date":
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_order = "date"
            self._sort_ascending = False  # Newest first by default
        self._refresh_grid()

    def resizeEvent(self, event) -> None:
        """Handle window resize."""
        super().resizeEvent(event)
        self._refresh_grid()

    def closeEvent(self, event) -> None:
        """Handle window close."""
        self._watcher.stop()
        super().closeEvent(event)


# Import here to avoid circular import
from PyQt6.QtWidgets import QApplication
```

**Step 2: Update main.py**

```python
# src/main.py
"""PDFas application entry point."""
import sys
from PyQt6.QtWidgets import QApplication
from src.views.main_window import MainWindow


def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("PDFas")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

**Step 3: Run and verify basic functionality**

```bash
python src/main.py
```

Expected: Window opens with toolbar, empty grid (or shows PDFs if any exist in Documents/PDFas).

**Step 4: Commit**

```bash
git add src/views/main_window.py src/main.py
git commit -m "feat: add main window with toolbar and PDF grid"
```

---

### Task 7: Page Edit Window

**Files:**
- Create: `src/views/page_edit_window.py`

**Step 1: Implement page_edit_window.py**

```python
# src/views/page_edit_window.py
"""Page edit window for editing PDF pages."""
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QScrollArea, QGridLayout,
    QInputDialog, QLabel, QFrame
)
from PyQt6.QtCore import Qt, QSize, QMimeData, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QDrag, QPixmap

from src.utils.pdf_utils import (
    get_page_thumbnail, get_page_count, rotate_pages,
    remove_pages, reorder_pages, extract_pages
)
from src.models.undo_manager import UndoManager


class PageThumbnail(QFrame):
    """Widget representing a single PDF page."""

    clicked = pyqtSignal(object)

    THUMBNAIL_SIZE = 120

    def __init__(self, pdf_path: str, page_num: int, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_num = page_num
        self._is_selected = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the thumbnail UI."""
        self.setFixedSize(self.THUMBNAIL_SIZE + 10, self.THUMBNAIL_SIZE + 30)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        # Thumbnail image
        self._image_label = QLabel()
        self._image_label.setFixedSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #f0f0f0;")
        layout.addWidget(self._image_label)

        # Page number
        self._number_label = QLabel(str(self._page_num + 1))
        self._number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._number_label)

        self._load_thumbnail()
        self._update_style()

    def _load_thumbnail(self) -> None:
        """Load the page thumbnail."""
        pixmap = get_page_thumbnail(self._pdf_path, self._page_num, self.THUMBNAIL_SIZE)
        if not pixmap.isNull():
            self._image_label.setPixmap(pixmap)

    def _update_style(self) -> None:
        """Update style based on selection."""
        if self._is_selected:
            self.setStyleSheet("PageThumbnail { background-color: #cce5ff; border: 2px solid #007bff; }")
        else:
            self.setStyleSheet("PageThumbnail { background-color: white; border: 1px solid #ccc; }")

    @property
    def page_num(self) -> int:
        """Get the page number (0-indexed)."""
        return self._page_num

    @property
    def is_selected(self) -> bool:
        """Check if selected."""
        return self._is_selected

    def set_selected(self, selected: bool) -> None:
        """Set selection state."""
        self._is_selected = selected
        self._update_style()

    def refresh(self) -> None:
        """Refresh the thumbnail."""
        self._load_thumbnail()

    def mousePressEvent(self, event) -> None:
        """Handle mouse press."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
        super().mousePressEvent(event)


class PageEditWindow(QMainWindow):
    """Window for editing pages within a PDF."""

    def __init__(self, pdf_path: str, undo_manager: UndoManager, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._undo_manager = undo_manager
        self._thumbnails: list[PageThumbnail] = []
        self._selected_thumbnails: list[PageThumbnail] = []

        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()
        self._load_pages()

    def _setup_ui(self) -> None:
        """Set up the UI."""
        self.setWindowTitle(f"Edit: {os.path.basename(self._pdf_path)}")
        self.resize(800, 600)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        # Container
        self._container = QWidget()
        scroll.setWidget(self._container)

        self._grid_layout = QGridLayout(self._container)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def _setup_toolbar(self) -> None:
        """Set up the toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Undo/Redo
        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("Redo")
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addSeparator()

        # Delete
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        # Rename (PDF)
        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename)
        toolbar.addWidget(self._rename_btn)

        toolbar.addSeparator()

        # Rotate
        self._rotate_btn = QPushButton("Rotate")
        self._rotate_btn.clicked.connect(self._on_rotate)
        toolbar.addWidget(self._rotate_btn)

        # Select all
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self._select_all_btn)

        self._update_button_states()

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Ctrl+Z
        undo_action = QAction(self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._on_undo)
        self.addAction(undo_action)

        # Ctrl+Y
        redo_action = QAction(self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._on_redo)
        self.addAction(redo_action)

        # Delete
        delete_action = QAction(self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._on_delete)
        self.addAction(delete_action)

        # F2
        rename_action = QAction(self)
        rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        rename_action.triggered.connect(self._on_rename)
        self.addAction(rename_action)

        # Ctrl+A
        select_all_action = QAction(self)
        select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_action.triggered.connect(self._on_select_all)
        self.addAction(select_all_action)

    def _update_button_states(self) -> None:
        """Update button states."""
        has_selection = len(self._selected_thumbnails) > 0
        self._delete_btn.setEnabled(has_selection)
        self._rotate_btn.setEnabled(has_selection)
        self._undo_btn.setEnabled(self._undo_manager.can_undo())
        self._redo_btn.setEnabled(self._undo_manager.can_redo())

    def _load_pages(self) -> None:
        """Load all pages from the PDF."""
        # Clear existing
        for thumb in self._thumbnails:
            thumb.deleteLater()
        self._thumbnails.clear()
        self._selected_thumbnails.clear()

        # Load pages
        page_count = get_page_count(self._pdf_path)
        for i in range(page_count):
            thumb = PageThumbnail(self._pdf_path, i)
            thumb.clicked.connect(self._on_thumbnail_clicked)
            self._thumbnails.append(thumb)

        self._refresh_grid()

    def _refresh_grid(self) -> None:
        """Refresh the grid layout."""
        # Clear grid
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        # Add thumbnails
        cols = max(1, self._container.width() // (PageThumbnail.THUMBNAIL_SIZE + 20))
        for i, thumb in enumerate(self._thumbnails):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(thumb, row, col)

    def _clear_selection(self) -> None:
        """Clear selection."""
        for thumb in self._selected_thumbnails:
            thumb.set_selected(False)
        self._selected_thumbnails.clear()
        self._update_button_states()

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
            self._clear_selection()
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)

        self._update_button_states()

    def _on_undo(self) -> None:
        """Handle undo."""
        self._undo_manager.undo()
        self._load_pages()
        self._update_button_states()

    def _on_redo(self) -> None:
        """Handle redo."""
        self._undo_manager.redo()
        self._load_pages()
        self._update_button_states()

    def _on_delete(self) -> None:
        """Handle delete pages."""
        if not self._selected_thumbnails:
            return

        indices = sorted([t.page_num for t in self._selected_thumbnails], reverse=True)
        remove_pages(self._pdf_path, indices)
        self._load_pages()

    def _on_rename(self) -> None:
        """Handle rename PDF."""
        old_name = os.path.basename(self._pdf_path)
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=old_name
        )

        if ok and new_name and new_name != old_name:
            if not new_name.lower().endswith(".pdf"):
                new_name += ".pdf"
            new_path = os.path.join(os.path.dirname(self._pdf_path), new_name)
            os.rename(self._pdf_path, new_path)
            self._pdf_path = new_path
            self.setWindowTitle(f"Edit: {new_name}")

    def _on_rotate(self) -> None:
        """Handle rotate pages."""
        if not self._selected_thumbnails:
            return

        indices = [t.page_num for t in self._selected_thumbnails]
        rotate_pages(self._pdf_path, indices, 90)

        for thumb in self._selected_thumbnails:
            thumb.refresh()

    def _on_select_all(self) -> None:
        """Handle select all."""
        self._clear_selection()
        for thumb in self._thumbnails:
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)
        self._update_button_states()

    def resizeEvent(self, event) -> None:
        """Handle resize."""
        super().resizeEvent(event)
        self._refresh_grid()
```

**Step 2: Run and verify**

```bash
python src/main.py
```

Expected: Can double-click a PDF card to open page edit window.

**Step 3: Commit**

```bash
git add src/views/page_edit_window.py
git commit -m "feat: add page edit window"
```

---

## Phase 3: Drag and Drop Operations (Tasks 8-12)

### Task 8-12: Implement D&D

These tasks involve complex drag-and-drop functionality:
- Card reordering in main window
- Card merging (drop on card)
- External file drop (import)
- Page reordering in edit window
- Page D&D between windows
- Page extraction to main window

Each requires careful implementation of:
- `mouseMoveEvent` for drag initiation
- `dragEnterEvent`, `dragMoveEvent`, `dropEvent` for drop handling
- Custom MIME types for internal data transfer
- Visual feedback during drag

**Implementation approach:** Add D&D to PDFCard and PageThumbnail classes, then handle drops in respective windows.

---

## Summary

This plan covers the core implementation in 7 main tasks:
1. Project setup
2. PDF utilities
3. Undo manager
4. Folder watcher
5. PDF card widget
6. Main window
7. Page edit window

Additional tasks for D&D will be implemented incrementally after the basic UI is working.

---

**Testing commands:**
```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_pdf_utils.py -v

# Run application
python src/main.py
```
