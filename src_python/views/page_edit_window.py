"""Page edit window for editing PDF pages."""
import os
import logging
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QScrollArea, QGridLayout,
    QInputDialog, QLabel, QFrame, QApplication, QRubberBand,
    QToolButton, QSizePolicy
)
from PyQt6.QtCore import Qt, QMimeData, pyqtSignal, QPoint, QPointF, QRect, QRectF, QUrl
from PyQt6.QtGui import QAction, QKeySequence, QDrag, QPainter, QColor, QPen, QDesktopServices

from src.utils.pdf_utils import (
    get_page_thumbnail, get_page_pixmap, get_page_words, get_page_links,
    get_page_count, rotate_pages, remove_pages, reorder_pages, extract_pages,
    insert_pages
)
from src.models.undo_manager import UndoManager, UndoAction

PAGETHUMBNAIL_MIME_TYPE = "application/x-pdfas-page"
PDFCARD_MIME_TYPE = "application/x-pdfas-card"

logger = logging.getLogger(__name__)


class PageThumbnail(QFrame):
    """Widget representing a single PDF page."""

    clicked = pyqtSignal(object)
    THUMBNAIL_SIZE = 120

    def __init__(self, pdf_path: str, page_num: int, display_num: int = None, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_num = page_num
        self._display_num = display_num if display_num is not None else page_num
        self._is_selected = False
        self._explicitly_hidden = False
        self._drag_start_pos = None
        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the thumbnail UI."""
        self.setFixedSize(self.THUMBNAIL_SIZE + 10, self.THUMBNAIL_SIZE + 30)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #f0f0f0;")
        layout.addWidget(self._image_label)

        self._number_label = QLabel(str(self._display_num + 1))
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
        return self._page_num

    @property
    def is_selected(self) -> bool:
        return self._is_selected

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        self._update_style()

    def refresh(self) -> None:
        self._load_thumbnail()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            parent_window = self.window()
            if hasattr(parent_window, "_open_zoom_view"):
                parent_window._open_zoom_view(self._page_num)
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        parent_window = self.window()
        page_nums_list = [self._page_num]
        if hasattr(parent_window, '_selected_thumbnails'):
            selected_thumbs = parent_window._selected_thumbnails
            if self in selected_thumbs and len(selected_thumbs) > 1:
                page_nums_list = [thumb.page_num for thumb in selected_thumbs]
                page_nums_str = ','.join(str(n) for n in page_nums_list)
            else:
                page_nums_str = str(self._page_num)
        else:
            page_nums_str = str(self._page_num)

        logger.debug(f"Starting drag: pdf_path={self._pdf_path}, page_nums={page_nums_list}")

        drag = QDrag(self)
        mime_data = QMimeData()
        data = f"{self._pdf_path}|{page_nums_str}".encode('utf-8')
        mime_data.setData(PAGETHUMBNAIL_MIME_TYPE, data)
        drag.setMimeData(mime_data)

        pixmap = self.grab()
        pixmap = pixmap.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio)
        
        # Add badge for multiple selection
        if len(page_nums_list) > 1:
            from PyQt6.QtGui import QPainter, QColor, QFont
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.GlobalColor.white)
            painter.setBrush(QColor(0, 120, 215))
            badge_size = 20
            painter.drawEllipse(pixmap.width() - badge_size, 0, badge_size, badge_size)
            font = QFont()
            font.setBold(True)
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(pixmap.width() - badge_size, 0, badge_size, badge_size,
                           Qt.AlignmentFlag.AlignCenter, str(len(page_nums_list)))
            painter.end()
        
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        result = drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
        logger.debug(f"Drag completed with result: {result}")


class ZoomPageWidget(QWidget):
    wheel_zoom = pyqtSignal(int)
    link_clicked = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._zoom_factor = 1.0
        self._words: list[tuple] = []
        self._word_rects: list[QRectF] = []
        self._links: list[dict] = []
        self._link_rects: list[QRectF | None] = []
        self._selected_word_indices: list[int] = []
        self._selection_origin: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._selection_active = False
        self._pressed_link: dict | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def sizeHint(self):
        if self._pixmap and not self._pixmap.isNull():
            return self._pixmap.size()
        return super().sizeHint()

    def set_page(self, pixmap, words, links, zoom_factor: float) -> None:
        self._pixmap = pixmap
        self._zoom_factor = zoom_factor or 1.0
        self._words = words or []
        self._links = links or []

        scale = self._zoom_factor
        self._word_rects = []
        for word in self._words:
            if len(word) < 4:
                continue
            x0, y0, x1, y1 = word[0], word[1], word[2], word[3]
            self._word_rects.append(QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale))

        self._link_rects = []
        for link in self._links:
            rect = link.get("from")
            if rect is None:
                self._link_rects.append(None)
                continue
            x0, y0, x1, y1 = rect
            self._link_rects.append(QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale))

        self._selected_word_indices = []
        self._selection_origin = None
        self._selection_rect = None
        self._selection_active = False
        self._pressed_link = None
        if self._pixmap and not self._pixmap.isNull():
            self.setMinimumSize(self._pixmap.size())
        else:
            self.setMinimumSize(0, 0)
        self.updateGeometry()
        self.update()

    def _pixmap_offset(self) -> QPoint:
        if not self._pixmap or self._pixmap.isNull():
            return QPoint(0, 0)
        x = max(0, (self.width() - self._pixmap.width()) // 2)
        y = max(0, (self.height() - self._pixmap.height()) // 2)
        return QPoint(x, y)

    def _point_in_pixmap(self, pos: QPoint) -> QPointF | None:
        if not self._pixmap or self._pixmap.isNull():
            return None
        offset = self._pixmap_offset()
        x = pos.x() - offset.x()
        y = pos.y() - offset.y()
        if x < 0 or y < 0 or x > self._pixmap.width() or y > self._pixmap.height():
            return None
        return QPointF(x, y)

    def _link_at(self, pos: QPoint) -> dict | None:
        pix_pos = self._point_in_pixmap(pos)
        if pix_pos is None:
            return None
        for i, rect in enumerate(self._link_rects):
            if rect and rect.contains(pix_pos):
                return self._links[i]
        return None

    def _word_index_at(self, pos: QPoint) -> int | None:
        pix_pos = self._point_in_pixmap(pos)
        if pix_pos is None:
            return None
        for i, rect in enumerate(self._word_rects):
            if rect.contains(pix_pos):
                return i
        return None

    def _update_selection(self) -> None:
        if not self._pixmap or self._pixmap.isNull() or self._selection_rect is None:
            self._selected_word_indices = []
            return
        offset = self._pixmap_offset()
        sel = QRectF(self._selection_rect.normalized())
        sel.translate(-offset.x(), -offset.y())
        pix_bounds = QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
        sel = sel.intersected(pix_bounds)
        if sel.isEmpty():
            self._selected_word_indices = []
            return
        selected = []
        for i, rect in enumerate(self._word_rects):
            if rect.intersects(sel):
                selected.append(i)
        self._selected_word_indices = selected

    def _selected_text(self) -> str:
        if not self._selected_word_indices:
            return ""
        selected_words = [self._words[i] for i in self._selected_word_indices if i < len(self._words)]
        selected_words.sort(key=lambda w: (w[5], w[6], w[7]) if len(w) > 7 else (0, 0, 0))
        lines: list[str] = []
        current_line: list[str] = []
        current_key = None
        for word in selected_words:
            if len(word) < 5:
                continue
            key = (word[5], word[6]) if len(word) > 6 else (0, 0)
            if current_key is None:
                current_key = key
            if key != current_key:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word[4]]
                current_key = key
            else:
                current_line.append(word[4])
        if current_line:
            lines.append(" ".join(current_line))
        return "\n".join(lines)

    def _update_cursor(self, pos: QPoint) -> None:
        if self._link_at(pos):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        if self._word_index_at(pos) is not None:
            self.setCursor(Qt.CursorShape.IBeamCursor)
            return
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().brush(self.backgroundRole()))
        if self._pixmap and not self._pixmap.isNull():
            offset = self._pixmap_offset()
            painter.drawPixmap(offset, self._pixmap)

            if self._selected_word_indices:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 120, 215, 80))
                for idx in self._selected_word_indices:
                    if idx < len(self._word_rects):
                        rect = self._word_rects[idx].translated(QPointF(offset))
                        painter.drawRect(rect)

        if self._selection_rect is not None:
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._selection_rect.normalized())

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+ホイールでズーム
            delta = event.angleDelta().y()
            if delta == 0:
                return
            step = 5 if delta > 0 else -5
            self.wheel_zoom.emit(step)
            event.accept()
        else:
            # 通常スクロールは親に伝播
            event.ignore()

    def mousePressEvent(self, event) -> None:
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self.setFocus()
                self._selection_origin = event.pos()
                self._selection_rect = QRect(self._selection_origin, self._selection_origin)
                self._pressed_link = self._link_at(event.pos())
                self._selection_active = self._pressed_link is None
                if self._selection_active:
                    self._update_selection()
                self.update()
        except Exception as e:
            logger.exception("Error in ZoomPageWidget.mousePressEvent")
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        try:
            if event.buttons() & Qt.MouseButton.LeftButton and self._selection_origin is not None:
                if not self._selection_active:
                    if (event.pos() - self._selection_origin).manhattanLength() >= QApplication.startDragDistance():
                        self._selection_active = True
                        self._pressed_link = None
                if self._selection_active:
                    self._selection_rect = QRect(self._selection_origin, event.pos()).normalized()
                    self._update_selection()
                    self.update()
            else:
                self._update_cursor(event.pos())
        except Exception as e:
            logger.exception("Error in ZoomPageWidget.mouseMoveEvent")
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._selection_active:
                    if self._selection_rect is not None:
                        rect = self._selection_rect.normalized()
                        if rect.width() < 3 and rect.height() < 3:
                            idx = self._word_index_at(event.pos())
                            self._selected_word_indices = [idx] if idx is not None else []
                    self._selection_rect = None
                    self._selection_active = False
                else:
                    if self._pressed_link is not None:
                        self.link_clicked.emit(self._pressed_link)
                self._selection_origin = None
                self._pressed_link = None
                self.update()
        except Exception as e:
            logger.exception("Error in ZoomPageWidget.mouseReleaseEvent")
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            text = self._selected_text()
            if text:
                QApplication.clipboard().setText(text)
                event.accept()
                return
        super().keyPressEvent(event)


class PageEditWindow(QMainWindow):
    """Window for editing pages within a PDF."""

    ZOOM_MIN = 25
    ZOOM_MAX = 400
    ZOOM_STEP = 5

    def __init__(self, pdf_path: str, undo_manager: UndoManager, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._undo_manager = undo_manager
        self._thumbnails: list[PageThumbnail] = []
        self._selected_thumbnails: list[PageThumbnail] = []
        self._grid_scroll = None
        self._zoom_view = None
        self._zoom_scroll = None
        self._zoom_label = None
        self._zoom_percent_label = None
        self._zoom_prev_btn = None
        self._zoom_next_btn = None
        self._zoom_page_num = None
        self._zoom_factor = 1.0
        self._zoom_text_cache: dict[int, tuple[list[tuple], list[dict]]] = {}

        # Drop indicator
        self._drop_indicator = None
        self._drop_indicator_index = -1

        # Rubber band selection
        self._rubber_band = None
        self._rubber_band_origin = None

        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()
        self._undo_manager.add_listener(self._on_undo_manager_changed)
        self._load_pages()

    def _setup_ui(self) -> None:
        self.setWindowTitle(f"JusticePDF - Edit: {os.path.basename(self._pdf_path)}")
        self.resize(800, 600)
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        layout.addWidget(self._grid_scroll)

        self._container = QWidget()
        self._container.setAcceptDrops(True)
        self._grid_scroll.setWidget(self._container)

        self._grid_layout = QGridLayout(self._container)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Drop indicator line
        self._drop_indicator = QFrame(self._container)
        self._drop_indicator.setFrameShape(QFrame.Shape.VLine)
        self._drop_indicator.setStyleSheet("background-color: #007bff;")
        self._drop_indicator.setFixedWidth(3)
        self._drop_indicator.hide()

        # Rubber band for selection
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self._container)

        self._zoom_view = QWidget()
        zoom_layout = QVBoxLayout(self._zoom_view)
        zoom_layout.setContentsMargins(0, 0, 0, 0)

        zoom_controls = QWidget()
        controls_layout = QHBoxLayout(zoom_controls)
        controls_layout.setContentsMargins(10, 10, 10, 10)

        self._zoom_back_btn = QPushButton("Back")
        self._zoom_back_btn.clicked.connect(self._exit_zoom_view)
        controls_layout.addWidget(self._zoom_back_btn)

        self._zoom_out_btn = QPushButton("-")
        self._zoom_out_btn.clicked.connect(self._on_zoom_out)
        controls_layout.addWidget(self._zoom_out_btn)

        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.clicked.connect(self._on_zoom_in)
        controls_layout.addWidget(self._zoom_in_btn)

        self._zoom_reset_btn = QPushButton("100%")
        self._zoom_reset_btn.clicked.connect(self._on_zoom_reset)
        controls_layout.addWidget(self._zoom_reset_btn)

        self._zoom_percent_label = QLabel("100%")
        controls_layout.addWidget(self._zoom_percent_label)

        zoom_layout.addWidget(zoom_controls)

        self._zoom_scroll = QScrollArea()
        self._zoom_scroll.setWidgetResizable(True)
        self._zoom_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_label = ZoomPageWidget()
        self._zoom_label.wheel_zoom.connect(self._on_zoom_wheel)
        self._zoom_label.link_clicked.connect(self._on_zoom_link_clicked)
        self._zoom_scroll.setWidget(self._zoom_label)

        nav_button_style = (
            "QToolButton { background-color: #f2f2f2; border: 2px solid #4c4c4c; "
            "border-radius: 8px; padding: 4px; }"
            "QToolButton:hover { background-color: #dbe8ff; }"
            "QToolButton:pressed { background-color: #bcd4ff; }"
            "QToolButton:disabled { background-color: #e0e0e0; border-color: #aaaaaa; }"
            "QToolButton::arrow { width: 18px; height: 18px; }"
        )

        self._zoom_prev_btn = QToolButton()
        self._zoom_prev_btn.setArrowType(Qt.ArrowType.LeftArrow)
        self._zoom_prev_btn.setToolTip("Previous page")
        self._zoom_prev_btn.setAutoRaise(False)
        self._zoom_prev_btn.setFixedWidth(56)
        self._zoom_prev_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._zoom_prev_btn.setStyleSheet(nav_button_style)
        self._zoom_prev_btn.clicked.connect(self._on_zoom_prev_page)

        self._zoom_next_btn = QToolButton()
        self._zoom_next_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._zoom_next_btn.setToolTip("Next page")
        self._zoom_next_btn.setAutoRaise(False)
        self._zoom_next_btn.setFixedWidth(56)
        self._zoom_next_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._zoom_next_btn.setStyleSheet(nav_button_style)
        self._zoom_next_btn.clicked.connect(self._on_zoom_next_page)

        nav_container = QWidget()
        nav_layout = QHBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)
        nav_layout.addWidget(self._zoom_prev_btn)
        nav_layout.addWidget(self._zoom_scroll, 1)
        nav_layout.addWidget(self._zoom_next_btn)
        zoom_layout.addWidget(nav_container)

        layout.addWidget(self._zoom_view)
        self._zoom_view.hide()

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("Redo")
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addSeparator()

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename)
        toolbar.addWidget(self._rename_btn)

        toolbar.addSeparator()

        self._rotate_btn = QPushButton("Rotate")
        self._rotate_btn.clicked.connect(self._on_rotate)
        toolbar.addWidget(self._rotate_btn)

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self._select_all_btn)

        self._update_button_states()

    def _setup_shortcuts(self) -> None:
        undo_action = QAction(self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._on_undo)
        self.addAction(undo_action)

        redo_action = QAction(self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._on_redo)
        self.addAction(redo_action)

        delete_action = QAction(self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self._on_delete)
        self.addAction(delete_action)

        rename_action = QAction(self)
        rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        rename_action.triggered.connect(self._on_rename)
        self.addAction(rename_action)

        select_all_action = QAction(self)
        select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_action.triggered.connect(self._on_select_all)
        self.addAction(select_all_action)

    def _update_button_states(self) -> None:
        has_selection = len(self._selected_thumbnails) > 0
        self._delete_btn.setEnabled(has_selection)
        self._rotate_btn.setEnabled(has_selection)
        self._undo_btn.setEnabled(self._undo_manager.can_undo())
        self._redo_btn.setEnabled(self._undo_manager.can_redo())

    def _debug_undo_state(self, reason: str) -> None:
        if not self._undo_btn or not self._redo_btn:
            return
        undo_color = "black" if self._undo_btn.isEnabled() else "gray"
        redo_color = "black" if self._redo_btn.isEnabled() else "gray"
        logger.debug(
            "[UndoState][PageEditWindow] %s | undo=%s redo=%s undo_count=%s redo_count=%s",
            reason,
            undo_color,
            redo_color,
            self._undo_manager.undo_count(),
            self._undo_manager.redo_count(),
        )

    def _on_undo_manager_changed(self, reason: str) -> None:
        self._update_button_states()
        self._debug_undo_state(reason)

    def _load_pages(self) -> None:
        # 既存のサムネイルをグリッドから先に取り除く
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            # setParent(None)は呼ばない（deleteLater()で処理される）

        for thumb in self._thumbnails:
            thumb.deleteLater()
        self._thumbnails.clear()
        self._selected_thumbnails.clear()
        self._zoom_text_cache.clear()

        # ファイル存在チェック
        if not os.path.exists(self._pdf_path):
            return

        page_count = get_page_count(self._pdf_path)
        if page_count == 0:
            return

        # ズームビューのページ番号を調整
        if self._zoom_page_num is not None and self._zoom_page_num >= page_count:
            self._zoom_page_num = max(0, page_count - 1)

        for i in range(page_count):
            thumb = PageThumbnail(self._pdf_path, i)
            thumb.clicked.connect(self._on_thumbnail_clicked)
            self._thumbnails.append(thumb)

        self._refresh_grid()
        if self._zoom_view and self._zoom_view.isVisible():
            self._render_zoom_page()

    def _refresh_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            # 削除予定のウィジェットには触らない
            widget = item.widget()
            if widget and widget in self._thumbnails:
                widget.setParent(None)

        # Use window width if container width is not yet set
        available_width = self._container.width()
        if available_width < 100:  # Not yet properly sized
            available_width = self.width() - 40  # Account for margins and scrollbar
        cols = max(1, available_width // (PageThumbnail.THUMBNAIL_SIZE + 20))

        visible_thumbs = [t for t in self._thumbnails if not t._explicitly_hidden]
        for i, thumb in enumerate(visible_thumbs):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(thumb, row, col)
            thumb.setVisible(True)

    def _remove_page_thumbnails(self, page_indices: list[int]) -> None:
        """指定されたページのサムネイルを削除（差分更新）"""
        # グリッドから全サムネイルを一旦取り除く
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            widget = item.widget()
            if widget and widget in self._thumbnails:
                widget.setParent(None)

        # 指定されたインデックスのサムネイルを削除（逆順で処理）
        for idx in sorted(page_indices, reverse=True):
            if 0 <= idx < len(self._thumbnails):
                thumb = self._thumbnails.pop(idx)
                if thumb in self._selected_thumbnails:
                    self._selected_thumbnails.remove(thumb)
                thumb.deleteLater()

        # ページ番号を再割り当て
        for i, thumb in enumerate(self._thumbnails):
            thumb._page_num = i
            thumb._display_num = i
            thumb._number_label.setText(str(i + 1))

        # ズームビューのページ番号を調整
        if self._zoom_page_num is not None:
            page_count = len(self._thumbnails)
            if page_count == 0:
                self._zoom_page_num = None
                if self._zoom_view and self._zoom_view.isVisible():
                    self._exit_zoom_view()
            elif self._zoom_page_num >= page_count:
                self._zoom_page_num = max(0, page_count - 1)
                if self._zoom_view and self._zoom_view.isVisible():
                    self._render_zoom_page()

        # ズームテキストキャッシュをクリア（ページ番号が変わるため）
        self._zoom_text_cache.clear()

        self._refresh_grid()

    def _clear_selection(self) -> None:
        for thumb in self._selected_thumbnails:
            thumb.set_selected(False)
        self._selected_thumbnails.clear()
        self._update_button_states()

    def hide_page(self, page_num: int) -> None:
        for thumb in self._thumbnails:
            if thumb.page_num == page_num:
                thumb._explicitly_hidden = True
                thumb.setVisible(False)
                if thumb in self._selected_thumbnails:
                    self._selected_thumbnails.remove(thumb)
                break

    def _on_thumbnail_clicked(self, thumb: PageThumbnail) -> None:
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
            if thumb not in self._selected_thumbnails:
                self._clear_selection()
                thumb.set_selected(True)
                self._selected_thumbnails.append(thumb)

        self._update_button_states()

    def _open_zoom_view(self, page_num: int) -> None:
        self._zoom_page_num = page_num
        self._set_zoom_percent(100)
        if self._grid_scroll:
            self._grid_scroll.hide()
        if self._zoom_view:
            self._zoom_view.show()

    def _exit_zoom_view(self) -> None:
        if self._zoom_view:
            self._zoom_view.hide()
        if self._grid_scroll:
            self._grid_scroll.show()

    def _set_zoom_percent(self, value: int) -> None:
        value = max(self.ZOOM_MIN, min(self.ZOOM_MAX, value))
        self._zoom_factor = value / 100.0
        if self._zoom_percent_label:
            self._zoom_percent_label.setText(f"{value}%")
        self._render_zoom_page()

    def _on_zoom_wheel(self, step: int) -> None:
        current = int(self._zoom_factor * 100)
        self._set_zoom_percent(current + step)

    def _on_zoom_in(self) -> None:
        current = int(self._zoom_factor * 100)
        self._set_zoom_percent(current + self.ZOOM_STEP)

    def _on_zoom_out(self) -> None:
        current = int(self._zoom_factor * 100)
        self._set_zoom_percent(current - self.ZOOM_STEP)

    def _on_zoom_reset(self) -> None:
        self._set_zoom_percent(100)

    def _on_zoom_prev_page(self) -> None:
        if self._zoom_page_num is None:
            return
        if self._zoom_page_num <= 0:
            self._update_zoom_nav_buttons()
            return
        self._zoom_page_num -= 1
        self._render_zoom_page()

    def _on_zoom_next_page(self) -> None:
        if self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if self._zoom_page_num >= page_count - 1:
            self._update_zoom_nav_buttons(page_count)
            return
        self._zoom_page_num += 1
        self._render_zoom_page()

    def _update_zoom_nav_buttons(self, page_count: int | None = None) -> None:
        if not self._zoom_prev_btn or not self._zoom_next_btn:
            return
        if self._zoom_page_num is None:
            self._zoom_prev_btn.setEnabled(False)
            self._zoom_next_btn.setEnabled(False)
            return
        if page_count is None:
            page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            self._zoom_prev_btn.setEnabled(False)
            self._zoom_next_btn.setEnabled(False)
            return
        self._zoom_prev_btn.setEnabled(self._zoom_page_num > 0)
        self._zoom_next_btn.setEnabled(self._zoom_page_num < page_count - 1)

    def _render_zoom_page(self) -> None:
        if self._zoom_page_num is None or not self._zoom_label:
            return
        page_count = get_page_count(self._pdf_path)
        self._update_zoom_nav_buttons(page_count)
        if self._zoom_page_num >= page_count:
            self._exit_zoom_view()
            return
        pixmap = get_page_pixmap(self._pdf_path, self._zoom_page_num, self._zoom_factor)
        words = []
        links = []
        if self._zoom_page_num in self._zoom_text_cache:
            words, links = self._zoom_text_cache[self._zoom_page_num]
        else:
            words = get_page_words(self._pdf_path, self._zoom_page_num)
            links = get_page_links(self._pdf_path, self._zoom_page_num)
            self._zoom_text_cache[self._zoom_page_num] = (words, links)
        self._zoom_label.set_page(pixmap, words, links, self._zoom_factor)

    def _on_zoom_link_clicked(self, link: dict) -> None:
        uri = link.get("uri")
        if uri:
            QDesktopServices.openUrl(QUrl(uri))
            return
        file_path = link.get("file")
        if file_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))
            return
        target_page = link.get("page")
        if isinstance(target_page, int):
            self._zoom_page_num = target_page
            self._render_zoom_page()

    def _on_undo(self) -> None:
        self._undo_manager.undo()
        self._load_pages()
        self._update_button_states()

    def _on_redo(self) -> None:
        self._undo_manager.redo()
        self._load_pages()
        self._update_button_states()

    def _on_delete(self) -> None:
        if not self._selected_thumbnails:
            return

        import tempfile

        indices = sorted([t.page_num for t in self._selected_thumbnails], reverse=True)
        pdf_path = self._pdf_path

        backup_fd, backup_path = tempfile.mkstemp(suffix=".pdf")
        os.close(backup_fd)

        sorted_indices = sorted(indices)
        extract_pages(pdf_path, backup_path, sorted_indices)

        def do_delete():
            remove_pages(pdf_path, indices)
            self._load_pages()

        def undo_delete():
            insert_pages(pdf_path, backup_path, sorted_indices)
            self._load_pages()

        do_delete()

        self._undo_manager.add_action(UndoAction(
            description=f"Delete {len(indices)} page(s)",
            undo_func=undo_delete,
            redo_func=do_delete
        ))

    def _on_rename(self) -> None:
        old_path = self._pdf_path
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=old_name
        )

        if ok and new_name and new_name != old_name:
            if not new_name.lower().endswith(".pdf"):
                new_name += ".pdf"
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            if os.path.abspath(old_path) == os.path.abspath(new_path):
                return

            def _get_main_window():
                from PyQt6.QtWidgets import QApplication
                from src.views.main_window import MainWindow

                for widget in QApplication.topLevelWidgets():
                    if isinstance(widget, MainWindow):
                        return widget
                return None

            def do_rename() -> None:
                main_window = _get_main_window()
                if main_window:
                    main_window._perform_rename(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    self._pdf_path = new_path
                    self.setWindowTitle(f"JusticePDF - Edit: {new_name}")

            def undo_rename() -> None:
                main_window = _get_main_window()
                if main_window:
                    main_window._perform_rename(new_path, old_path)
                else:
                    os.rename(new_path, old_path)
                    self._pdf_path = old_path
                    self.setWindowTitle(f"JusticePDF - Edit: {old_name}")

            do_rename()
            self._undo_manager.add_action(UndoAction(
                description="Rename PDF",
                undo_func=undo_rename,
                redo_func=do_rename
            ))

    def _on_rotate(self) -> None:
        if not self._selected_thumbnails:
            return

        indices = [t.page_num for t in self._selected_thumbnails]
        pdf_path = self._pdf_path
        selected_thumbs = list(self._selected_thumbnails)

        def do_rotate():
            rotate_pages(pdf_path, indices, 90)
            for thumb in selected_thumbs:
                thumb.refresh()

        def undo_rotate():
            rotate_pages(pdf_path, indices, 270)
            for thumb in selected_thumbs:
                thumb.refresh()

        do_rotate()

        self._undo_manager.add_action(UndoAction(
            description=f"Rotate {len(indices)} page(s)",
            undo_func=undo_rotate,
            redo_func=do_rotate
        ))

    def _on_select_all(self) -> None:
        self._clear_selection()
        for thumb in self._thumbnails:
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)
        self._update_button_states()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_grid()

    def mousePressEvent(self, event) -> None:
        """Handle mouse press - start rubber band selection on empty area."""
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            while child is not None:
                if isinstance(child, PageThumbnail):
                    super().mousePressEvent(event)
                    return
                child = child.parent()
            # Start rubber band selection on empty area
            container_pos = self._container.mapFrom(self, event.pos())
            self._rubber_band_origin = container_pos
            self._rubber_band.setGeometry(container_pos.x(), container_pos.y(), 0, 0)
            self._rubber_band.show()
            self._clear_selection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for rubber band selection."""
        if self._rubber_band_origin is not None:
            container_pos = self._container.mapFrom(self, event.pos())
            from PyQt6.QtCore import QRect
            rect = QRect(self._rubber_band_origin, container_pos).normalized()
            self._rubber_band.setGeometry(rect)
            # Select thumbnails intersecting with rubber band
            self._clear_selection()
            for thumb in self._thumbnails:
                if thumb.isVisible() and rect.intersects(thumb.geometry()):
                    thumb.set_selected(True)
                    self._selected_thumbnails.append(thumb)
            self._update_button_states()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Handle mouse release to end rubber band selection."""
        if event.button() == Qt.MouseButton.LeftButton and self._rubber_band_origin is not None:
            self._rubber_band.hide()
            self._rubber_band_origin = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            if source_path != self._pdf_path:
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        """Handle drag move event - show drop indicator."""
        if event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            self._show_drop_indicator(drop_pos)
        elif event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            if source_path != self._pdf_path:
                event.acceptProposedAction()
                drop_pos = self._container.mapFrom(self, event.position().toPoint())
                self._show_drop_indicator(drop_pos)

    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave event - hide drop indicator."""
        self._hide_drop_indicator()
        super().dragLeaveEvent(event)

    def _show_drop_indicator(self, pos) -> None:
        """Show drop indicator at the appropriate position."""
        idx = self._get_drop_page_index(pos)
        if idx == self._drop_indicator_index:
            return

        self._drop_indicator_index = idx

        if not self._thumbnails:
            self._drop_indicator.hide()
            return

        # Calculate indicator position
        visible_thumbs = [t for t in self._thumbnails if t.isVisible()]
        if not visible_thumbs:
            self._drop_indicator.hide()
            return

        if idx == 0:
            ref_thumb = visible_thumbs[0]
            x = ref_thumb.geometry().left() - 5
        elif idx >= len(visible_thumbs):
            ref_thumb = visible_thumbs[-1]
            x = ref_thumb.geometry().right() + 2
        else:
            ref_thumb = visible_thumbs[min(idx, len(visible_thumbs) - 1)]
            x = ref_thumb.geometry().left() - 5

        thumb_rect = visible_thumbs[0].geometry() if visible_thumbs else None
        if thumb_rect:
            self._drop_indicator.setFixedHeight(thumb_rect.height())
            self._drop_indicator.move(x, ref_thumb.geometry().top())
            self._drop_indicator.raise_()
            self._drop_indicator.show()

    def _hide_drop_indicator(self) -> None:
        """Hide the drop indicator."""
        self._drop_indicator.hide()
        self._drop_indicator_index = -1

    def dropEvent(self, event) -> None:
        """Handle drop event."""
        logger.debug(f"PageEditWindow.dropEvent called, mimeData formats: {event.mimeData().formats()}")
        self._hide_drop_indicator()

        if event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            pdf_path, page_nums_str = data.split('|')
            page_nums = [int(n) for n in page_nums_str.split(',') if n]
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            logger.debug(f"PAGETHUMBNAIL drop: pdf_path={pdf_path}, page_nums={page_nums}, drop_pos={drop_pos}")

            if pdf_path == self._pdf_path:
                logger.debug("Same file, calling _handle_page_reorder")
                self._handle_page_reorder(page_nums, drop_pos)
            else:
                logger.debug("Different file, calling _handle_page_insert")
                self._handle_page_insert(pdf_path, page_nums, drop_pos)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            logger.debug(f"PDFCARD drop: source_path={source_path}")
            if source_path != self._pdf_path:
                drop_pos = self._container.mapFrom(self, event.position().toPoint())
                page_count = get_page_count(source_path)
                logger.debug(f"Inserting all {page_count} pages from {source_path}")
                if page_count > 0:
                    all_pages = list(range(page_count))
                    self._handle_page_insert(source_path, all_pages, drop_pos)
            event.acceptProposedAction()
        else:
            logger.debug("Unknown drop format, ignoring")

    def _handle_page_reorder(self, source_pages: list[int], drop_pos) -> None:
        target_page = self._get_drop_page_index(drop_pos)

        source_pages = sorted(set(source_pages))
        if not source_pages or target_page == -1:
            return

        page_count = get_page_count(self._pdf_path)
        remaining = [i for i in range(page_count) if i not in source_pages]
        removed_before = sum(1 for p in source_pages if p < target_page)
        insert_index = max(0, min(target_page - removed_before, len(remaining)))
        new_order = remaining[:insert_index] + source_pages + remaining[insert_index:]
        if new_order == list(range(page_count)):
            return

        pdf_path = self._pdf_path
        moved_count = len(source_pages)
        final_insert_index = insert_index

        def do_reorder():
            reorder_pages(pdf_path, new_order)
            self._load_pages()
            # Select moved pages
            self._clear_selection()
            for i in range(final_insert_index, final_insert_index + moved_count):
                if i < len(self._thumbnails):
                    self._thumbnails[i].set_selected(True)
                    self._selected_thumbnails.append(self._thumbnails[i])
            self._update_button_states()

        def undo_reorder():
            inverse = [0] * len(new_order)
            for i, pos in enumerate(new_order):
                inverse[pos] = i
            reorder_pages(pdf_path, inverse)
            self._load_pages()

        do_reorder()

        self._undo_manager.add_action(UndoAction(
            description="Reorder page",
            undo_func=undo_reorder,
            redo_func=do_reorder
        ))

    def _handle_page_insert(self, source_pdf_path: str, source_pages: list[int], drop_pos) -> None:
        import tempfile
        from send2trash import send2trash

        logger.debug(f"_handle_page_insert called: source={source_pdf_path}, pages={source_pages}, drop_pos={drop_pos}")
        
        source_pages = sorted(set(source_pages))
        if not source_pages:
            logger.debug("No source pages, returning")
            return

        insert_at = self._get_drop_page_index(drop_pos)
        logger.debug(f"insert_at={insert_at}")
        if insert_at == -1:
            logger.debug("insert_at is -1, returning")
            return

        modifiers = QApplication.keyboardModifiers()
        is_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        logger.debug(f"is_copy={is_copy}")

        tmp_path = None
        inserted_count = len(source_pages)
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            logger.debug(f"Extracting pages to tmp_path={tmp_path}")
            if not extract_pages(source_pdf_path, tmp_path, source_pages):
                logger.debug("extract_pages failed, returning")
                return

            page_count = get_page_count(self._pdf_path)
            insert_at = max(0, min(insert_at, page_count))
            logger.debug(f"Inserting pages at index {insert_at} into {self._pdf_path}")
            insert_pages(self._pdf_path, tmp_path, [insert_at] * len(source_pages))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                logger.debug(f"Cleaning up tmp_path={tmp_path}")
                os.unlink(tmp_path)

        logger.debug("Reloading pages in target window")
        self._load_pages()
        
        # Select inserted pages
        self._clear_selection()
        for i in range(insert_at, insert_at + inserted_count):
            if i < len(self._thumbnails):
                self._thumbnails[i].set_selected(True)
                self._selected_thumbnails.append(self._thumbnails[i])
        self._update_button_states()

        if not is_copy:
            logger.debug(f"Removing pages from source: {source_pdf_path}")
            file_deleted = remove_pages(source_pdf_path, source_pages)
            logger.debug(f"file_deleted={file_deleted}")
            
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, PageEditWindow) and widget._pdf_path == source_pdf_path:
                    if file_deleted:
                        logger.debug(f"File deleted, closing PageEditWindow for {source_pdf_path}")
                        widget.close()
                    else:
                        logger.debug(f"Reloading pages in source PageEditWindow for {source_pdf_path}")
                        widget._load_pages()
                    break
            if file_deleted:
                logger.debug(f"Removing card for {source_pdf_path} from MainWindow")
                from src.views.main_window import MainWindow
                for widget in QApplication.topLevelWidgets():
                    if isinstance(widget, MainWindow):
                        widget._remove_card(source_pdf_path)
                        widget._refresh_grid()
                        break
        
        logger.debug("_handle_page_insert completed")

    def _get_drop_page_index(self, pos) -> int:
        pad = max(0, self._grid_layout.spacing() // 2)
        for i, thumb in enumerate(self._thumbnails):
            thumb_rect = thumb.geometry()
            expanded_rect = thumb_rect.adjusted(-pad, -pad, pad, pad)
            if expanded_rect.contains(pos):
                center_x = thumb_rect.center().x()
                if pos.x() < center_x:
                    return i
                return i + 1
        if self._thumbnails:
            return len(self._thumbnails)
        return 0

    def closeEvent(self, event) -> None:
        """Handle window close - unlock the card in main window."""
        from PyQt6.QtWidgets import QApplication
        from src.views.main_window import MainWindow
        
        logger.debug(f"PageEditWindow closing for {self._pdf_path}")

        self._undo_manager.remove_listener(self._on_undo_manager_changed)
        
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.unlock_card(self._pdf_path)
                break
        super().closeEvent(event)
