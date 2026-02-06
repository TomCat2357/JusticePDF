"""PDF card widget for displaying PDF files."""
import os
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QApplication, QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint, QUrl
from PyQt6.QtGui import QDrag, QPixmap
from src.utils.pdf_utils import get_pdf_card_info

PDFCARD_MIME_TYPE = "application/x-pdfas-card"


class PDFCard(QFrame):
    """Widget representing a PDF file as a card.

    Displays a thumbnail of the first page, page count, and filename.
    Supports selection and drag-and-drop operations.
    """

    clicked = pyqtSignal(object)  # Emits self when clicked
    double_clicked = pyqtSignal(object)  # Emits self when double-clicked
    dropped_on = pyqtSignal(object, str)  # Emits (self, source_paths_str) when another card is dropped on this card

    CARD_WIDTH = 150
    THUMBNAIL_SIZE = 120

    def __init__(self, pdf_path: str, parent=None, *, card_width: int | None = None, thumb_size: int | None = None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._is_selected = False
        self._is_locked = False
        self._page_count = 0
        self._drag_start_pos = None
        self._card_width = int(card_width) if card_width is not None else self.CARD_WIDTH
        self._thumb_size = int(thumb_size) if thumb_size is not None else self.THUMBNAIL_SIZE
        self.setAcceptDrops(True)

        self._setup_ui()
        self._load_pdf_info()

    def _setup_ui(self) -> None:
        """Set up the card UI."""
        self.setFixedWidth(self._card_width)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Thumbnail container with page count overlay
        self._thumbnail_container = QWidget()
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)
        
        # Thumbnail
        self._thumbnail_label = QLabel(self._thumbnail_container)
        self._thumbnail_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ccc;")
        self._thumbnail_label.move(0, 0)

        # Page count overlay (top-right of thumbnail)
        self._page_count_label = QLabel("0p", self._thumbnail_container)
        self._page_count_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.7); color: white; padding: 2px 5px; border-radius: 3px; font-size: 11px;"
        )
        self._page_count_label.adjustSize()
        self._page_count_label.move(self._thumb_size - self._page_count_label.width() - 3, 3)
        self._page_count_label.raise_()

        layout.addWidget(self._thumbnail_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # Filename only (no page count below)
        self._filename_label = QLabel()
        self._filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._filename_label.setWordWrap(True)
        layout.addWidget(self._filename_label)

        self._update_style()

    def _load_pdf_info(self) -> None:
        """Load PDF information and thumbnail."""
        thumbnail, page_count = get_pdf_card_info(self._pdf_path, self._thumb_size)

        # Thumbnail
        if not thumbnail.isNull():
            self._thumbnail_label.setPixmap(thumbnail)
            self._thumbnail_label.setText("")
        else:
            self._thumbnail_label.setPixmap(QPixmap())
            self._thumbnail_label.setText("(empty)")

        # Page count (avoid opening PDF twice)
        self._page_count = page_count
        self._page_count_label.setText(f"{self._page_count}p")
        self._page_count_label.adjustSize()
        # Reposition to top-right after text change
        self._page_count_label.move(self._thumb_size - self._page_count_label.width() - 3, 3)

        # Filename
        self._filename_label.setText(os.path.basename(self._pdf_path))

    def _update_style(self) -> None:
        """Update the card style based on selection and locked state."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        
        if self._is_locked:
            # Locked state: grayed out
            self.setStyleSheet("PDFCard { background-color: #d0d0d0; border: 1px solid #999; }")
            effect = QGraphicsOpacityEffect()
            effect.setOpacity(0.5)
            self.setGraphicsEffect(effect)
        else:
            # Remove opacity effect
            self.setGraphicsEffect(None)
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

    @property
    def is_locked(self) -> bool:
        """Check if the card is locked (being edited in PageEditWindow)."""
        return self._is_locked

    def set_locked(self, locked: bool) -> None:
        """Set the locked state."""
        self._is_locked = locked
        self._update_style()

    def set_preview_size(self, card_width: int, thumb_size: int) -> None:
        """Update card and thumbnail sizes (for Ctrl+wheel preview zoom)."""
        self._card_width = int(card_width)
        self._thumb_size = int(thumb_size)
        self.setFixedWidth(self._card_width)
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumbnail_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._load_pdf_info()
        self.updateGeometry()

    def refresh(self) -> None:
        """Refresh the card display."""
        self._load_pdf_info()

    def mousePressEvent(self, event) -> None:
        """Handle mouse press events."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move events for drag initiation."""
        # Locked cards cannot be dragged
        if self._is_locked:
            return
            
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return

        # Check if drag threshold exceeded
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        # Get selected cards from parent window
        parent_window = self.window()
        selected_paths = [self._pdf_path]
        if hasattr(parent_window, '_selected_cards'):
            selected = parent_window._selected_cards
            if self in selected and len(selected) > 1:
                # Filter out locked cards and use remaining selected cards
                available_selected = [c for c in selected if not c.is_locked]
                if self in available_selected and len(available_selected) > 1:
                    selected_paths = [c.pdf_path for c in parent_window._cards if c in available_selected]

        # Start drag
        drag = QDrag(self)
        mime_data = QMimeData()
        data = '|'.join(selected_paths).encode('utf-8')
        mime_data.setData(PDFCARD_MIME_TYPE, data)
        urls = [QUrl.fromLocalFile(p) for p in selected_paths if os.path.exists(p)]
        if urls:
            mime_data.setUrls(urls)
        drag.setMimeData(mime_data)

        # Create drag pixmap
        pixmap = self.grab()
        pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
        
        # Add badge for multiple selection
        if len(selected_paths) > 1:
            from PyQt6.QtGui import QPainter, QColor, QFont
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.GlobalColor.white)
            painter.setBrush(QColor(0, 120, 215))
            badge_size = 24
            painter.drawEllipse(pixmap.width() - badge_size, 0, badge_size, badge_size)
            font = QFont()
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(pixmap.width() - badge_size, 0, badge_size, badge_size,
                           Qt.AlignmentFlag.AlignCenter, str(len(selected_paths)))
            painter.end()
        
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        # Export drag should always be copy (external drop).
        drag.exec(Qt.DropAction.CopyAction)

    def mouseDoubleClickEvent(self, event) -> None:
        """Handle double-click events."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self)
        super().mouseDoubleClickEvent(event)

    def dropEvent(self, event) -> None:
        """Handle drop event."""
        # Locked cards cannot accept drops
        if self._is_locked:
            event.ignore()
            return
            
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            data = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            source_paths = [p for p in data.split('|') if p != self._pdf_path]
            if source_paths:
                self.dropped_on.emit(self, '|'.join(source_paths))
                event.acceptProposedAction()
        self._update_style()
