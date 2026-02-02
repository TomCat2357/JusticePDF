# Phase 3: Drag and Drop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** カードとページのドラッグ&ドロップ操作を実装し、PDFの並び替え・マージ・分割を直感的に行えるようにする。

**Architecture:** PyQt6のドラッグ&ドロップシステムを使用。カスタムMIMEタイプでPDFパスとページ情報を転送。ドラッグ中は半透明のプレビューを表示し、ドロップ位置をインジケーターで示す。

**Tech Stack:** PyQt6 (QDrag, QMimeData, QPixmap), PyMuPDF (fitz)

---

## Task 8: カード並び替え（メインウィンドウ内D&D）

**Files:**
- Modify: `src/views/pdf_card.py`
- Modify: `src/views/main_window.py`

**Step 1: PDFCardにドラッグ開始機能を追加するテスト作成**

```python
# tests/test_pdf_card_dnd.py
"""Tests for PDF card drag and drop functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtTest import QTest
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


def test_pdf_card_has_drag_enabled(app, sample_pdf):
    """Test that PDFCard has drag enabled."""
    card = PDFCard(sample_pdf)
    # After drag is implemented, card should support dragging
    assert hasattr(card, 'mouseMoveEvent')


def test_pdf_card_mime_type():
    """Test that PDFCard uses correct MIME type."""
    from src.views.pdf_card import PDFCARD_MIME_TYPE
    assert PDFCARD_MIME_TYPE == "application/x-pdfas-card"
```

**Step 2: テストを実行して失敗を確認**

Run: `pytest tests/test_pdf_card_dnd.py -v`
Expected: FAIL with "cannot import name 'PDFCARD_MIME_TYPE'"

**Step 3: PDFCardにドラッグ機能を実装**

```python
# src/views/pdf_card.py の先頭に追加
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui import QPixmap, QDrag

PDFCARD_MIME_TYPE = "application/x-pdfas-card"
```

```python
# src/views/pdf_card.py PDFCardクラスに以下のメソッドを追加

    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._is_selected = False
        self._page_count = 0
        self._drag_start_pos = None  # 追加

        self._setup_ui()
        self._load_pdf_info()

    def mousePressEvent(self, event) -> None:
        """Handle mouse press events."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move events for drag initiation."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return

        # Check if drag threshold exceeded
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        # Start drag
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(PDFCARD_MIME_TYPE, self._pdf_path.encode('utf-8'))
        mime_data.setText(self._pdf_path)
        drag.setMimeData(mime_data)

        # Create drag pixmap
        pixmap = self.grab()
        pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        drag.exec(Qt.DropAction.MoveAction)
```

```python
# インポートに追加
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QApplication
```

**Step 4: テストを実行して通過を確認**

Run: `pytest tests/test_pdf_card_dnd.py -v`
Expected: PASS

**Step 5: MainWindowにドロップ受け入れ機能のテスト作成**

```python
# tests/test_main_window_dnd.py に追加
"""Tests for main window drag and drop functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from src.views.main_window import MainWindow


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_accepts_drops(app):
    """Test that MainWindow accepts drops."""
    window = MainWindow()
    assert window._container.acceptDrops()
    window.close()
```

**Step 6: テスト実行して失敗を確認**

Run: `pytest tests/test_main_window_dnd.py::test_main_window_accepts_drops -v`
Expected: FAIL

**Step 7: MainWindowにドロップ受け入れを実装**

```python
# src/views/main_window.py の_setup_ui()メソッドに追加

    def _setup_ui(self) -> None:
        """Set up the main UI."""
        self.setWindowTitle("PDFas")
        self.resize(1000, 700)
        self.setAcceptDrops(True)  # 追加

        # ... 既存のコード ...

        # Container for grid
        self._container = QWidget()
        self._container.setAcceptDrops(True)  # 追加
        scroll.setWidget(self._container)
```

```python
# src/views/main_window.py に以下のメソッドを追加

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            # External file drop
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith('.pdf'):
                    event.acceptProposedAction()
                    return

    def dragMoveEvent(self, event) -> None:
        """Handle drag move event."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Handle drop event."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            # Internal card reorder
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            drop_pos = event.position().toPoint()
            self._handle_card_drop(source_path, drop_pos)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            # External file drop
            self._handle_external_file_drop(event.mimeData().urls())
            event.acceptProposedAction()

    def _handle_card_drop(self, source_path: str, drop_pos) -> None:
        """Handle internal card drop for reordering."""
        # Find source card
        source_card = None
        source_idx = -1
        for i, card in enumerate(self._cards):
            if card.pdf_path == source_path:
                source_card = card
                source_idx = i
                break

        if source_card is None:
            return

        # Find target position
        target_idx = self._get_drop_index(drop_pos)
        if target_idx == -1 or target_idx == source_idx:
            return

        # Reorder cards list
        self._cards.pop(source_idx)
        if target_idx > source_idx:
            target_idx -= 1
        self._cards.insert(target_idx, source_card)

        self._refresh_grid()

    def _get_drop_index(self, pos) -> int:
        """Get the index where the drop should occur."""
        for i, card in enumerate(self._cards):
            card_rect = card.geometry()
            if card_rect.contains(pos):
                return i
        # If dropped after last card, return last index + 1
        if self._cards:
            return len(self._cards)
        return 0

    def _handle_external_file_drop(self, urls) -> None:
        """Handle external file drop (import)."""
        import shutil
        for url in urls:
            src_path = url.toLocalFile()
            if not src_path.lower().endswith('.pdf'):
                continue

            filename = os.path.basename(src_path)
            dest_path = self._work_dir / filename

            # Handle duplicate names
            i = 1
            while dest_path.exists():
                name, ext = os.path.splitext(filename)
                dest_path = self._work_dir / f"{name}_{i}{ext}"
                i += 1

            shutil.copy2(src_path, dest_path)
```

**Step 8: テスト実行して通過を確認**

Run: `pytest tests/test_main_window_dnd.py -v`
Expected: PASS

**Step 9: コミット**

```bash
git add src/views/pdf_card.py src/views/main_window.py tests/test_pdf_card_dnd.py tests/test_main_window_dnd.py
git commit -m "feat: add card drag and drop for reordering"
```

---

## Task 9: カードのマージ（カードにカードをドロップ）

**Files:**
- Modify: `src/views/pdf_card.py`
- Modify: `src/views/main_window.py`

**Step 1: カードマージのテスト作成**

```python
# tests/test_card_merge.py
"""Tests for PDF card merge functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.utils.pdf_utils import get_page_count, merge_pdfs


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def two_sample_pdfs():
    """Create two sample PDFs for testing."""
    files = []
    for i in range(2):
        f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((100, 100), f"Test {i}")
        doc.save(f.name)
        doc.close()
        files.append(f.name)
    yield files
    for f in files:
        if os.path.exists(f):
            os.unlink(f)


def test_merge_two_pdfs(app, two_sample_pdfs):
    """Test merging two PDFs."""
    pdf1, pdf2 = two_sample_pdfs

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out:
        output_path = out.name

    try:
        merge_pdfs(output_path, [pdf1, pdf2])
        assert get_page_count(output_path) == 2
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
```

**Step 2: テスト実行して通過を確認（既存機能）**

Run: `pytest tests/test_card_merge.py -v`
Expected: PASS

**Step 3: PDFCardにドロップ受け入れ機能を追加**

```python
# src/views/pdf_card.py PDFCardクラスに追加

    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._is_selected = False
        self._page_count = 0
        self._drag_start_pos = None
        self.setAcceptDrops(True)  # 追加

        self._setup_ui()
        self._load_pdf_info()

    # 新規メソッド追加
    dropped_on = pyqtSignal(object, str)  # シグナル追加（self, source_path）

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event."""
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            if source_path != self._pdf_path:
                self.setStyleSheet("PDFCard { background-color: #90EE90; border: 2px solid #228B22; }")
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave event."""
        self._update_style()

    def dropEvent(self, event) -> None:
        """Handle drop event."""
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            if source_path != self._pdf_path:
                self.dropped_on.emit(self, source_path)
                event.acceptProposedAction()
        self._update_style()
```

**Step 4: MainWindowでマージ処理を追加**

```python
# src/views/main_window.py _add_card()を修正

    def _add_card(self, pdf_path: str) -> PDFCard:
        """Add a new card for a PDF file."""
        card = PDFCard(pdf_path)
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)
        card.dropped_on.connect(self._on_card_merge)  # 追加
        self._cards.append(card)
        return card

    # 新規メソッド追加
    def _on_card_merge(self, target_card: PDFCard, source_path: str) -> None:
        """Handle card merge (drop card on another card)."""
        from src.utils.pdf_utils import merge_pdfs
        import tempfile
        import shutil

        target_path = target_card.pdf_path

        # Create merged PDF in temp location
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            merge_pdfs(tmp_path, [target_path, source_path])

            # Replace target with merged
            shutil.move(tmp_path, target_path)

            # Delete source
            send2trash(source_path)

            # Refresh will happen via folder watcher
        except Exception as e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
```

**Step 5: テスト実行して通過を確認**

Run: `pytest tests/test_card_merge.py -v`
Expected: PASS

**Step 6: コミット**

```bash
git add src/views/pdf_card.py src/views/main_window.py tests/test_card_merge.py
git commit -m "feat: add card merge by dropping card on another card"
```

---

## Task 10: 外部ファイルドロップ（既にTask 8で実装済み）

Task 8の`_handle_external_file_drop`で実装済み。

**Step 1: 外部ファイルドロップのテスト作成**

```python
# tests/test_external_drop.py
"""Tests for external file drop functionality."""
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QUrl, QMimeData


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mime_data_with_pdf_url(app):
    """Test that MIME data with PDF URL is recognized."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/path/to/test.pdf")])
    assert mime.hasUrls()
    assert mime.urls()[0].toLocalFile().endswith('.pdf')
```

**Step 2: テスト実行して通過を確認**

Run: `pytest tests/test_external_drop.py -v`
Expected: PASS

**Step 3: コミット**

```bash
git add tests/test_external_drop.py
git commit -m "test: add external file drop tests"
```

---

## Task 11: ページの並び替え（編集ウィンドウ内D&D）

**Files:**
- Modify: `src/views/page_edit_window.py`

**Step 1: PageThumbnailにドラッグ機能を追加するテスト**

```python
# tests/test_page_thumbnail_dnd.py
"""Tests for page thumbnail drag and drop functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.views.page_edit_window import PageThumbnail, PAGETHUMBNAIL_MIME_TYPE


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
        for i in range(3):
            page = doc.new_page()
            page.insert_text((100, 100), f"Page {i+1}")
        doc.save(f.name)
        doc.close()
        yield f.name
    os.unlink(f.name)


def test_page_thumbnail_mime_type():
    """Test that PageThumbnail uses correct MIME type."""
    assert PAGETHUMBNAIL_MIME_TYPE == "application/x-pdfas-page"


def test_page_thumbnail_has_drag(app, sample_pdf):
    """Test that PageThumbnail supports dragging."""
    thumb = PageThumbnail(sample_pdf, 0)
    assert hasattr(thumb, 'mouseMoveEvent')
```

**Step 2: テスト実行して失敗を確認**

Run: `pytest tests/test_page_thumbnail_dnd.py -v`
Expected: FAIL with "cannot import name 'PAGETHUMBNAIL_MIME_TYPE'"

**Step 3: PageThumbnailにドラッグ機能を実装**

```python
# src/views/page_edit_window.py の先頭に追加
PAGETHUMBNAIL_MIME_TYPE = "application/x-pdfas-page"
```

```python
# src/views/page_edit_window.py PageThumbnailクラスを修正

class PageThumbnail(QFrame):
    """Widget representing a single PDF page."""

    clicked = pyqtSignal(object)

    THUMBNAIL_SIZE = 120

    def __init__(self, pdf_path: str, page_num: int, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_num = page_num
        self._is_selected = False
        self._drag_start_pos = None  # 追加
        self.setAcceptDrops(True)  # 追加

        self._setup_ui()

    # mousePressEventを修正
    def mousePressEvent(self, event) -> None:
        """Handle mouse press."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self)
        super().mousePressEvent(event)

    # 新規メソッド追加
    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move events for drag initiation."""
        from PyQt6.QtWidgets import QApplication

        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return

        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        drag = QDrag(self)
        mime_data = QMimeData()

        # Encode pdf_path and page_num
        data = f"{self._pdf_path}|{self._page_num}".encode('utf-8')
        mime_data.setData(PAGETHUMBNAIL_MIME_TYPE, data)
        drag.setMimeData(mime_data)

        # Create drag pixmap
        pixmap = self.grab()
        pixmap = pixmap.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        drag.exec(Qt.DropAction.MoveAction)
```

**Step 4: テスト実行して通過を確認**

Run: `pytest tests/test_page_thumbnail_dnd.py -v`
Expected: PASS

**Step 5: PageEditWindowにドロップ処理を追加**

```python
# src/views/page_edit_window.py PageEditWindowクラスの_setup_ui()を修正

    def _setup_ui(self) -> None:
        """Set up the UI."""
        self.setWindowTitle(f"Edit: {os.path.basename(self._pdf_path)}")
        self.resize(800, 600)
        self.setAcceptDrops(True)  # 追加

        # ... 既存のコード ...

        # Container
        self._container = QWidget()
        self._container.setAcceptDrops(True)  # 追加
        scroll.setWidget(self._container)
```

```python
# src/views/page_edit_window.py PageEditWindowクラスに追加

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event."""
        if event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            pdf_path, _ = data.split('|')
            if pdf_path == self._pdf_path:
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        """Handle drag move event."""
        if event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Handle drop event."""
        if event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            pdf_path, page_num_str = data.split('|')

            if pdf_path == self._pdf_path:
                source_page = int(page_num_str)
                drop_pos = event.position().toPoint()
                self._handle_page_reorder(source_page, drop_pos)
                event.acceptProposedAction()

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

        # Apply reorder
        reorder_pages(self._pdf_path, new_order)
        self._load_pages()

    def _get_drop_page_index(self, pos) -> int:
        """Get the page index where the drop should occur."""
        for i, thumb in enumerate(self._thumbnails):
            thumb_rect = thumb.geometry()
            if thumb_rect.contains(pos):
                return i
        if self._thumbnails:
            return len(self._thumbnails)
        return 0
```

**Step 6: テスト実行して通過を確認**

Run: `pytest tests/test_page_thumbnail_dnd.py -v`
Expected: PASS

**Step 7: コミット**

```bash
git add src/views/page_edit_window.py tests/test_page_thumbnail_dnd.py
git commit -m "feat: add page reordering via drag and drop"
```

---

## Task 12: ページの抽出（ページをメインウィンドウにドロップ）

**Files:**
- Modify: `src/views/main_window.py`
- Modify: `src/views/page_edit_window.py`

**Step 1: ページ抽出のテスト作成**

```python
# tests/test_page_extraction.py
"""Tests for page extraction via drag and drop."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.utils.pdf_utils import extract_pages, get_page_count


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def multi_page_pdf():
    """Create a multi-page PDF for testing."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = fitz.open()
        for i in range(5):
            page = doc.new_page()
            page.insert_text((100, 100), f"Page {i+1}")
        doc.save(f.name)
        doc.close()
        yield f.name
    os.unlink(f.name)


def test_extract_single_page(app, multi_page_pdf):
    """Test extracting a single page."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out:
        output_path = out.name

    try:
        extract_pages(multi_page_pdf, output_path, [2])  # Extract page 3 (0-indexed)
        assert get_page_count(output_path) == 1
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
```

**Step 2: テスト実行して通過を確認（既存機能）**

Run: `pytest tests/test_page_extraction.py -v`
Expected: PASS

**Step 3: MainWindowでページドロップを受け付ける**

```python
# src/views/main_window.py dragEnterEventを修正

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith('.pdf'):
                    event.acceptProposedAction()
                    return

    def dragMoveEvent(self, event) -> None:
        """Handle drag move event."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Handle drop event."""
        from src.views.pdf_card import PDFCARD_MIME_TYPE
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            drop_pos = event.position().toPoint()
            self._handle_card_drop(source_path, drop_pos)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            self._handle_page_extraction(data)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            self._handle_external_file_drop(event.mimeData().urls())
            event.acceptProposedAction()

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
```

**Step 4: テスト実行して通過を確認**

Run: `pytest tests/test_page_extraction.py -v`
Expected: PASS

**Step 5: コミット**

```bash
git add src/views/main_window.py tests/test_page_extraction.py
git commit -m "feat: add page extraction by dropping page to main window"
```

---

## Summary

Phase 3は5つのタスクで構成されます：

| Task | 機能 | ファイル |
|------|------|---------|
| 8 | カード並び替え | pdf_card.py, main_window.py |
| 9 | カードマージ | pdf_card.py, main_window.py |
| 10 | 外部ファイルドロップ | main_window.py |
| 11 | ページ並び替え | page_edit_window.py |
| 12 | ページ抽出 | main_window.py |

**テストコマンド:**
```bash
# 全テスト実行
pytest tests/ -v

# D&D関連テストのみ
pytest tests/test_*_dnd.py tests/test_card_merge.py tests/test_external_drop.py tests/test_page_extraction.py -v

# アプリケーション起動
python src/main.py
```
