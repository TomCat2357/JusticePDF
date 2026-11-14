"""Folder card widget for displaying subfolders in the main window grid."""
import os
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QUrl, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QDrag, QPixmap

from src.views import icons
from src.views.card_widgets import ElidedNameLabel, card_width_for
from src.views import theme
from src.views.theme import COLORS
from src.views.view_helpers import apply_drag_pixmap
from src.utils.file_kinds import is_library_file
from src.utils.constants import (
    PDFCARD_MIME_TYPE,
    FOLDERCARD_MIME_TYPE,
    PAGETHUMBNAIL_MIME_TYPE,
)


class FolderCard(QFrame):
    """Widget representing a subfolder as a card.

    Displays a folder icon, item count, and folder name.
    Supports selection, drag-and-drop, and double-click to open.
    """

    clicked = pyqtSignal(object)
    double_clicked = pyqtSignal(object, bool)  # self, alt_pressed
    dropped_on = pyqtSignal(object, object)  # self, payloads dict {mime_type: payload_str}
    context_menu_requested = pyqtSignal(object, object)

    THUMBNAIL_SIZE = 136
    CARD_WIDTH = card_width_for(THUMBNAIL_SIZE)

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
        self._refresh_debounce.setInterval(80)
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
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 淡黄の正方形の中に Folder アイコン(橙)と「N 件」
        self._thumbnail_container = QFrame()
        self._thumbnail_container.setObjectName("folderThumb")
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)
        inner = QVBoxLayout(self._thumbnail_container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(2)
        inner.addStretch(1)

        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._count_label = QLabel("0 件")
        self._count_label.setObjectName("folderCount")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._count_label, alignment=Qt.AlignmentFlag.AlignCenter)
        inner.addStretch(1)
        self._render_folder_icon()

        layout.addWidget(self._thumbnail_container, alignment=Qt.AlignmentFlag.AlignCenter)

        self._name_label = ElidedNameLabel()
        self._name_label.setObjectName("cardName")
        layout.addWidget(self._name_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._update_style()
        theme.bind(self, lambda w: w._render_folder_icon(), call_now=False)

    def _render_folder_icon(self) -> None:
        size = max(16, int(round(self._thumb_size * 0.4)))
        self._icon_label.setFixedSize(size, size)
        self._icon_label.setPixmap(icons.pixmap("Folder", COLORS["folder-icon"], size))

    def _load_folder_info(self) -> None:
        count = 0
        try:
            for name in os.listdir(self._folder_path):
                full = os.path.join(self._folder_path, name)
                if os.path.isdir(full):
                    count += 1
                elif is_library_file(full):
                    count += 1
        except OSError:
            count = 0
        self._item_count = count
        self._count_label.setText(f"{count} 件")
        self._name_label.set_full_text(
            os.path.basename(self._folder_path) or self._folder_path, self._thumb_size
        )

    def _update_style(self) -> None:
        self.setProperty("state", "selected" if self._is_selected else "normal")
        self.style().unpolish(self)
        self.style().polish(self)

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
        self._render_folder_icon()
        self._name_label.set_full_text(self.filename, self._thumb_size)
        self.updateGeometry()

    def set_preview_size_fast(self, card_width: int, thumb_size: int) -> None:
        if self.width() == int(card_width) and self._thumbnail_container.width() == int(thumb_size):
            return
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

        parent_window = self.window()
        folder_paths = [self._folder_path]
        pdf_paths: list[str] = []
        selected_folders = getattr(parent_window, "_selected_folder_cards", None)
        if selected_folders and self in selected_folders and len(selected_folders) > 1:
            folder_paths = [fc.folder_path for fc in selected_folders]
        if selected_folders and self in selected_folders:
            selected_cards = getattr(parent_window, "_selected_cards", None)
            if selected_cards:
                pdf_paths = [c.pdf_path for c in selected_cards if not c.is_locked]

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(FOLDERCARD_MIME_TYPE, "|".join(folder_paths).encode("utf-8"))
        if pdf_paths:
            mime_data.setData(PDFCARD_MIME_TYPE, "|".join(pdf_paths).encode("utf-8"))
        urls = [QUrl.fromLocalFile(p) for p in folder_paths + pdf_paths if os.path.exists(p)]
        if urls:
            mime_data.setUrls(urls)
        drag.setMimeData(mime_data)

        apply_drag_pixmap(drag, self)

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
            target_norm = os.path.normpath(self._folder_path)
            sources = [p for p in src.split("|") if p]
            if any(os.path.normpath(s) != target_norm for s in sources):
                event.acceptProposedAction()
                return
        if md.hasUrls():
            for url in md.urls():
                if url.toLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.setProperty("state", "droptarget")
        self.style().unpolish(self)
        self.style().polish(self)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._update_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        self._update_style()
        payloads: dict[str, str] = {}
        for mime in (PDFCARD_MIME_TYPE, PAGETHUMBNAIL_MIME_TYPE, FOLDERCARD_MIME_TYPE):
            if md.hasFormat(mime):
                payloads[mime] = bytes(md.data(mime)).decode("utf-8", errors="replace")
        if not payloads and md.hasUrls():
            urls = [u.toLocalFile() for u in md.urls() if u.toLocalFile()]
            if urls:
                payloads["text/uri-list"] = "|".join(urls)
        if payloads:
            self.dropped_on.emit(self, payloads)
            event.acceptProposedAction()
            return
        event.ignore()
