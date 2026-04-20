"""Folder card widget for displaying subfolders in the main window grid."""
import os
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QApplication, QWidget, QStyle,
)
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint, QUrl, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QDrag, QPixmap

from src.views.pdf_card import PDFCARD_MIME_TYPE

FOLDERCARD_MIME_TYPE = "application/x-pdfas-folder"
PAGETHUMBNAIL_MIME_TYPE = "application/x-pdfas-page"


class FolderCard(QFrame):
    """Widget representing a subfolder as a card.

    Displays a folder icon, item count, and folder name.
    Supports selection, drag-and-drop, and double-click to open.
    """

    clicked = pyqtSignal(object)
    double_clicked = pyqtSignal(object, bool)  # self, alt_pressed
    dropped_on = pyqtSignal(object, str, object)  # self, mime_type, payload_bytes
    context_menu_requested = pyqtSignal(object, object)

    CARD_WIDTH = 150
    THUMBNAIL_SIZE = 120

    def __init__(
        self,
        folder_path: str,
        parent=None,
        *,
        card_width: int | None = None,
        thumb_size: int | None = None,
    ):
        super().__init__(parent)
        self._folder_path = folder_path
        self._is_selected = False
        self._drag_start_pos = None
        self._card_width = int(card_width) if card_width is not None else self.CARD_WIDTH
        self._thumb_size = int(thumb_size) if thumb_size is not None else self.THUMBNAIL_SIZE
        self._item_count = 0
        self.setAcceptDrops(True)

        self._setup_ui()
        self._load_folder_info()

        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(120)
        self._refresh_debounce.timeout.connect(self._load_folder_info)

        self._fs_watcher = QFileSystemWatcher(self)
        if os.path.isdir(self._folder_path):
            self._fs_watcher.addPath(self._folder_path)
        self._fs_watcher.directoryChanged.connect(
            lambda _path: self._refresh_debounce.start()
        )

    def _setup_ui(self) -> None:
        self.setFixedWidth(self._card_width)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self._thumbnail_container = QWidget()
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)

        self._icon_label = QLabel(self._thumbnail_container)
        self._icon_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setStyleSheet("background-color: #fff8dc; border: 1px solid #d4a017;")
        self._icon_label.move(0, 0)
        self._render_folder_icon()

        self._count_label = QLabel("0", self._thumbnail_container)
        self._count_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.7); color: white; padding: 2px 5px; border-radius: 3px; font-size: 11px;"
        )
        self._count_label.adjustSize()
        self._count_label.move(self._thumb_size - self._count_label.width() - 3, 3)
        self._count_label.raise_()

        layout.addWidget(self._thumbnail_container, alignment=Qt.AlignmentFlag.AlignCenter)

        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(True)
        layout.addWidget(self._name_label)

        self._update_style()

    def _render_folder_icon(self) -> None:
        style = self.style()
        icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        inner = max(16, self._thumb_size - 24)
        pix = icon.pixmap(inner, inner)
        if not pix.isNull():
            self._icon_label.setPixmap(pix)
            self._icon_label.setText("")
        else:
            font = self._icon_label.font()
            font.setPointSize(max(24, self._thumb_size // 3))
            self._icon_label.setFont(font)
            self._icon_label.setText("📁")

    def _load_folder_info(self) -> None:
        count = 0
        try:
            for name in os.listdir(self._folder_path):
                full = os.path.join(self._folder_path, name)
                if os.path.isdir(full):
                    count += 1
                elif name.lower().endswith(".pdf"):
                    count += 1
        except OSError:
            count = 0
        self._item_count = count
        self._count_label.setText(str(count))
        self._count_label.adjustSize()
        self._count_label.move(self._thumb_size - self._count_label.width() - 3, 3)
        self._name_label.setText(os.path.basename(self._folder_path) or self._folder_path)

    def _update_style(self) -> None:
        if self._is_selected:
            self.setStyleSheet("FolderCard { background-color: #cce5ff; border: 2px solid #007bff; }")
        else:
            self.setStyleSheet("FolderCard { background-color: white; border: 1px solid #ccc; }")

    @property
    def folder_path(self) -> str:
        return self._folder_path

    @property
    def filename(self) -> str:
        return os.path.basename(self._folder_path) or self._folder_path

    @property
    def is_selected(self) -> bool:
        return self._is_selected

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        self._update_style()

    @property
    def is_locked(self) -> bool:
        return False

    def set_preview_size(self, card_width: int, thumb_size: int) -> None:
        self._card_width = int(card_width)
        self._thumb_size = int(thumb_size)
        self.setFixedWidth(self._card_width)
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)
        self._icon_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._render_folder_icon()
        self._count_label.adjustSize()
        self._count_label.move(self._thumb_size - self._count_label.width() - 3, 3)
        self.updateGeometry()

    def set_preview_size_fast(self, card_width: int, thumb_size: int) -> None:
        self.set_preview_size(card_width, thumb_size)

    def render_high_quality(self) -> None:
        pass

    def refresh(self) -> None:
        self._load_folder_info()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(FOLDERCARD_MIME_TYPE, self._folder_path.encode("utf-8"))
        if os.path.isdir(self._folder_path):
            mime_data.setUrls([QUrl.fromLocalFile(self._folder_path)])
        drag.setMimeData(mime_data)

        pixmap = self.grab()
        pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            alt = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
            self.double_clicked.emit(self, alt)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_menu_requested.emit(self, event.globalPos())
        event.accept()

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
            return
        if md.hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
            return
        if md.hasFormat(FOLDERCARD_MIME_TYPE):
            src = bytes(md.data(FOLDERCARD_MIME_TYPE)).decode("utf-8", errors="replace")
            if src and os.path.normpath(src) != os.path.normpath(self._folder_path):
                event.acceptProposedAction()
                return
        if md.hasUrls():
            for url in md.urls():
                if url.toLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.setStyleSheet(
            "FolderCard { background-color: #d4f8d4; border: 2px solid #228B22; }"
        )
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._update_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        self._update_style()
        for mime in (PDFCARD_MIME_TYPE, PAGETHUMBNAIL_MIME_TYPE, FOLDERCARD_MIME_TYPE):
            if md.hasFormat(mime):
                payload = bytes(md.data(mime)).decode("utf-8", errors="replace")
                self.dropped_on.emit(self, mime, payload)
                event.acceptProposedAction()
                return
        if md.hasUrls():
            urls = [u.toLocalFile() for u in md.urls() if u.toLocalFile()]
            if urls:
                self.dropped_on.emit(self, "text/uri-list", "|".join(urls))
                event.acceptProposedAction()
                return
        event.ignore()
