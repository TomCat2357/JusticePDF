"""PDF card widget for displaying PDF files."""
import os
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QApplication, QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QUrl
from PyQt6.QtGui import QColor, QDrag, QPainter, QPixmap

from src.views import icons
from src.views.card_widgets import ElidedNameLabel, card_width_for, paper_pixmap
from src.views import theme
from src.views.theme import COLORS
from src.views.view_helpers import apply_drag_pixmap
from src.utils.pdf_utils import get_pdf_card_info
from src.utils import file_kinds
from src.utils.constants import PDFCARD_MIME_TYPE, FOLDERCARD_MIME_TYPE


class PDFCard(QFrame):
    """Widget representing a PDF file as a card.

    Displays a thumbnail of the first page, page count, and filename.
    Supports selection and drag-and-drop operations.

    PDF 以外のファイル（Word・Excel・画像など）も同じカードで表す。その場合は
    fitz で開かず、種類のアイコン・拡張子バッジ・ファイルサイズを描く
    （:attr:`is_pdf` が False、``page_count`` は 0）。属性名 ``pdf_path`` は
    PDF 以外でも「ファイルのパス」の意味で使う。
    """

    clicked = pyqtSignal(object)  # Emits self when clicked
    double_clicked = pyqtSignal(object, bool)  # Emits (self, alt_pressed) when double-clicked
    dropped_on = pyqtSignal(object, str)  # Emits (self, source_paths_str) when another card is dropped on this card
    context_menu_requested = pyqtSignal(object, object)  # Emits (self, global_pos) when context menu is requested

    # サムネイル枠（正方形）の既定の一辺。カード幅は枠 + 余白。
    THUMBNAIL_SIZE = 136
    CARD_WIDTH = card_width_for(THUMBNAIL_SIZE)

    def __init__(self, pdf_path: str, parent=None, *, card_width: int | None = None, thumb_size: int | None = None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._is_selected = False
        self._is_locked = False
        self._is_drop_target = False
        self._page_count = 0
        self._kind = file_kinds.kind_of(pdf_path)
        self._drag_start_pos = None
        self._card_width = int(card_width) if card_width is not None else self.CARD_WIDTH
        self._thumb_size = int(thumb_size) if thumb_size is not None else self.THUMBNAIL_SIZE
        self._original_pixmap: QPixmap | None = None
        self.setAcceptDrops(True)

        self._setup_ui()
        self._load_pdf_info()

    def _setup_ui(self) -> None:
        """Set up the card UI."""
        self.setFixedWidth(self._card_width)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 正方形のサムネイル枠（右上にバッジ、左上に編集中の錠前）
        self._thumbnail_container = QWidget()
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)

        self._thumbnail_label = QLabel(self._thumbnail_container)
        self._thumbnail_label.setObjectName("cardThumb")
        self._thumbnail_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail_label.move(0, 0)

        # PDF 以外のとき左下に出すファイルサイズ
        self._size_label = QLabel("", self._thumbnail_container)
        self._size_label.setObjectName("cardBadge")
        self._size_label.hide()

        # 種別バッジ「PDF 3p」（右上）
        self._page_count_label = QLabel("PDF", self._thumbnail_container)
        self._page_count_label.setObjectName("cardBadge")
        self._page_count_label.adjustSize()
        self._place_badge()
        self._page_count_label.raise_()

        # 編集中（ロック）の錠前（左上）
        self._lock_label = QLabel(self._thumbnail_container)
        self._lock_label.setObjectName("cardLock")
        theme.bind(self._lock_label, lambda w: w.setPixmap(icons.pixmap("Lock", COLORS["star"], 16)))
        self._lock_label.setFixedSize(16, 16)
        self._lock_label.move(4, 4)
        self._lock_label.hide()

        layout.addWidget(self._thumbnail_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # 名前（中央揃え・2 行まで）
        self._filename_label = ElidedNameLabel()
        self._filename_label.setObjectName("cardName")
        layout.addWidget(self._filename_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._update_style()

    def _place_badge(self) -> None:
        self._page_count_label.adjustSize()
        self._page_count_label.move(self._thumb_size - self._page_count_label.width() - 4, 4)
        self._size_label.adjustSize()
        self._size_label.move(4, self._thumb_size - self._size_label.height() - 4)

    def _set_thumbnail_pixmap(self, pix: QPixmap | None) -> None:
        if pix is not None and not pix.isNull():
            self._thumbnail_label.setPixmap(paper_pixmap(pix))
            self._thumbnail_label.setText("")
        else:
            self._thumbnail_label.setPixmap(QPixmap())
            self._thumbnail_label.setText("(空)")

    def _other_thumbnail(self) -> QPixmap:
        """PDF 以外: 種類のアイコンを中央に描いた正方形（色は描画時の COLORS から引く）。"""
        size = self._thumb_size
        dpr = icons._default_dpr()
        pix = QPixmap(round(size * dpr), round(size * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(QColor(COLORS["slate-50"]))
        icon_name, color_key = file_kinds.kind_icon(self._kind)
        icon_size = max(16, int(round(size * 0.4)))
        icon_pix = icons.pixmap(icon_name, COLORS[color_key], icon_size, dpr)
        painter = QPainter(pix)
        off = (size - icon_size) / 2
        painter.drawPixmap(int(round(off)), int(round(off)), icon_pix)
        painter.end()
        return pix

    def _load_other_info(self) -> None:
        """PDF 以外のファイルの表示（fitz は使わない）。"""
        self._original_pixmap = None
        self._page_count = 0
        self._thumbnail_label.setText("")
        self._thumbnail_label.setPixmap(paper_pixmap(self._other_thumbnail()))
        ext = file_kinds.extension(self._pdf_path)
        self._page_count_label.setText(ext.upper() if ext else file_kinds.kind_label(self._kind))
        try:
            self._size_label.setText(file_kinds.format_size(os.path.getsize(self._pdf_path)))
        except OSError:
            self._size_label.setText("")
        self._size_label.setVisible(bool(self._size_label.text()))
        self._place_badge()
        self._filename_label.set_full_text(os.path.basename(self._pdf_path), self._thumb_size)

    def _load_pdf_info(self) -> None:
        """Load PDF information and thumbnail."""
        self._kind = file_kinds.kind_of(self._pdf_path)
        if self._kind != "pdf":
            self._load_other_info()
            return
        self._size_label.hide()
        thumbnail, page_count = get_pdf_card_info(self._pdf_path, self._thumb_size)

        # Thumbnail
        self._original_pixmap = thumbnail if not thumbnail.isNull() else None
        self._set_thumbnail_pixmap(self._original_pixmap)

        # Page count (avoid opening PDF twice)
        self._page_count = page_count
        self._page_count_label.setText(f"PDF {self._page_count}p" if self._page_count > 0 else "PDF")
        self._place_badge()

        # Filename
        self._filename_label.set_full_text(os.path.basename(self._pdf_path), self._thumb_size)

    def _update_style(self) -> None:
        """Update the card style based on selection, locked, and drop-target state."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        if self._is_locked:
            state = "locked"
            effect = QGraphicsOpacityEffect()
            effect.setOpacity(0.5)
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)
            if self._is_drop_target:
                state = "droptarget"
            elif self._is_selected:
                state = "selected"
            else:
                state = "normal"
        self._lock_label.setVisible(self._is_locked)
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def pdf_path(self) -> str:
        """Get the PDF file path."""
        return self._pdf_path

    @property
    def kind(self) -> str:
        """ファイルの種類キー（``pdf`` / ``excel`` / …。file_kinds.FILE_KINDS）。"""
        return self._kind

    @property
    def is_pdf(self) -> bool:
        return self._kind == "pdf"

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

    @property
    def is_drop_target(self) -> bool:
        return self._is_drop_target

    def set_drop_target(self, on: bool) -> None:
        """Toggle the drop-target highlight (merge candidate)."""
        on = bool(on)
        if self._is_drop_target == on:
            return
        self._is_drop_target = on
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

    def set_preview_size_fast(self, card_width: int, thumb_size: int) -> None:
        """Update sizes with fast Qt-side scaling (no fitz re-render)."""
        self._card_width = int(card_width)
        self._thumb_size = int(thumb_size)
        if self.width() == self._card_width and self._thumbnail_label.width() == self._thumb_size:
            return
        self.setFixedWidth(self._card_width)
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumbnail_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._place_badge()
        self._filename_label.set_full_text(os.path.basename(self._pdf_path), self._thumb_size)
        if not self.is_pdf:
            self._thumbnail_label.setPixmap(paper_pixmap(self._other_thumbnail()))
            self.updateGeometry()
            return
        if self._original_pixmap and not self._original_pixmap.isNull():
            scaled = self._original_pixmap.scaled(
                self._thumb_size, self._thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumbnail_label.setPixmap(paper_pixmap(scaled))
        self.updateGeometry()

    def render_high_quality(self) -> None:
        """Re-render the thumbnail at full quality for current size."""
        self._load_pdf_info()

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
        selected_folder_paths: list[str] = []
        if hasattr(parent_window, '_selected_cards'):
            selected = parent_window._selected_cards
            if self in selected and len(selected) > 1:
                # Filter out locked cards and use remaining selected cards
                available_selected = [c for c in selected if not c.is_locked]
                if self in available_selected and len(available_selected) > 1:
                    selected_paths = [c.pdf_path for c in parent_window._cards if c in available_selected]
            if self in selected:
                selected_folders = getattr(parent_window, '_selected_folder_cards', None)
                if selected_folders:
                    selected_folder_paths = [fc.folder_path for fc in selected_folders]

        # Start drag
        drag = QDrag(self)
        mime_data = QMimeData()
        data = '|'.join(selected_paths).encode('utf-8')
        mime_data.setData(PDFCARD_MIME_TYPE, data)
        if selected_folder_paths:
            mime_data.setData(FOLDERCARD_MIME_TYPE, '|'.join(selected_folder_paths).encode('utf-8'))
        urls = [QUrl.fromLocalFile(p) for p in selected_paths + selected_folder_paths if os.path.exists(p)]
        if urls:
            mime_data.setUrls(urls)
        drag.setMimeData(mime_data)

        apply_drag_pixmap(drag, self, count=len(selected_paths))

        # Allow both move (internal/cross-window) and copy (external/Ctrl).
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)

    def mouseDoubleClickEvent(self, event) -> None:
        """Handle double-click events.

        When Alt is held, the receiver should open the page edit window
        directly in enlarged (zoom) mode.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            alt = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
            self.double_clicked.emit(self, alt)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Emit the native context-menu request with global coordinates."""
        self.context_menu_requested.emit(self, event.globalPos())
        event.accept()

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
