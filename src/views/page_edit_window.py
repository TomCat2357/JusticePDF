"""Page edit window for editing PDF pages."""
import os
import shutil
import logging
from collections import deque
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QScrollArea, QGridLayout,
    QInputDialog, QLabel, QFrame, QApplication, QRubberBand,
    QToolButton, QSizePolicy, QPlainTextEdit, QFormLayout,
    QSpinBox, QColorDialog, QSlider
)
from PyQt6.QtCore import (
    Qt, QMimeData, pyqtSignal, QPoint, QPointF, QRect, QRectF, QUrl,
    QTimer, QEvent, QSignalBlocker
)
from PyQt6.QtGui import (
    QKeySequence, QDrag, QPainter, QColor, QPen, QDesktopServices, QPixmap,
    QBrush, QCursor
)

from src.utils.pdf_utils import (
    get_page_thumbnail, get_page_pixmap, get_page_words, get_page_links,
    get_page_count, rotate_pages, remove_pages, reorder_pages, extract_pages,
    insert_pages, render_page_thumbnails_batch, FreeTextAnnotData,
    list_freetext_annots, create_freetext_annot, replace_freetext_annot,
    delete_freetext_annot
)
from src.models.undo_manager import UndoManager, UndoAction
from src.views.view_helpers import (
    clear_selection,
    log_undo_state,
    register_shortcuts,
    viewport_width_or_fallback,
)

PAGETHUMBNAIL_MIME_TYPE = "application/x-pdfas-page"
PDFCARD_MIME_TYPE = "application/x-pdfas-card"

logger = logging.getLogger(__name__)


class PageThumbnail(QFrame):
    """Widget representing a single PDF page."""

    clicked = pyqtSignal(object)
    THUMBNAIL_SIZE = 120

    def __init__(self, pdf_path: str, page_num: int, display_num: int = None, parent=None, *, thumb_size: int | None = None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_num = page_num
        self._display_num = display_num if display_num is not None else page_num
        self._is_selected = False
        self._explicitly_hidden = False
        self._drag_start_pos = None
        self._thumb_size = int(thumb_size) if thumb_size is not None else self.THUMBNAIL_SIZE
        self._thumbnail_loaded = False
        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the thumbnail UI."""
        self.setFixedSize(self._thumb_size + 10, self._thumb_size + 30)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #f0f0f0;")
        layout.addWidget(self._image_label)

        self._number_label = QLabel(str(self._display_num + 1))
        self._number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._number_label)

        self.invalidate_thumbnail()
        self._update_style()

    @property
    def thumbnail_loaded(self) -> bool:
        return self._thumbnail_loaded

    def invalidate_thumbnail(self) -> None:
        self._thumbnail_loaded = False
        self._image_label.setPixmap(QPixmap())
        self._image_label.setText("PDF")

    def load_thumbnail(self) -> bool:
        pixmap = get_page_thumbnail(self._pdf_path, self._page_num, self._thumb_size)
        if pixmap.isNull():
            self._image_label.setPixmap(QPixmap())
            self._image_label.setText("...")
            self._thumbnail_loaded = False
            return False
        self._image_label.setPixmap(pixmap)
        self._image_label.setText("")
        self._thumbnail_loaded = True
        return True

    def set_pixmap_direct(self, pixmap: QPixmap) -> None:
        """Set a pre-rendered pixmap directly (for batch rendering)."""
        if pixmap.isNull():
            self._image_label.setPixmap(QPixmap())
            self._image_label.setText("...")
            self._thumbnail_loaded = False
        else:
            self._image_label.setPixmap(pixmap)
            self._image_label.setText("")
            self._thumbnail_loaded = True

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
        self.invalidate_thumbnail()
        self.load_thumbnail()

    def set_thumbnail_size(self, size: int) -> None:
        self._thumb_size = int(size)
        self.setFixedSize(self._thumb_size + 10, self._thumb_size + 30)
        self._image_label.setFixedSize(self._thumb_size, self._thumb_size)
        self.invalidate_thumbnail()
        self.updateGeometry()

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


class AnnotationTextEdit(QPlainTextEdit):
    commit_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._finished = False
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _emit_commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.commit_requested.emit(self.toPlainText())

    def _emit_cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.cancel_requested.emit()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._emit_commit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._emit_cancel()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Delete
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self.delete_requested.emit()
            event.accept()
            return
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._emit_commit()
            event.accept()
            return
        super().keyPressEvent(event)


class ZoomPageWidget(QWidget):
    wheel_zoom = pyqtSignal(int)
    link_clicked = pyqtSignal(dict)
    annotation_selected = pyqtSignal(object)
    annotation_geometry_changed = pyqtSignal(object, object, str)
    annotation_create_requested = pyqtSignal(object)
    annotation_edit_requested = pyqtSignal(object)
    annotation_text_committed = pyqtSignal(object, str)
    annotation_text_edit_cancelled = pyqtSignal()
    annotation_delete_requested = pyqtSignal()
    annotation_copy_requested = pyqtSignal(object)
    annotation_paste_requested = pyqtSignal()
    annotation_paste_placement_requested = pyqtSignal(object)

    HANDLE_SIZE = 10
    MIN_ANNOT_SIZE = 24.0

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
        self._annotations: list[FreeTextAnnotData] = []
        self._annotation_rects: list[QRectF] = []
        self._selected_annotation_xref: int | None = None
        self._hover_annotation_xref: int | None = None
        self._annotation_create_mode = False
        self._annotation_create_origin_page: QPointF | None = None
        self._annotation_create_preview_rect: QRectF | None = None
        self._drag_annotation_xref: int | None = None
        self._drag_mode: str | None = None
        self._drag_origin_page: QPointF | None = None
        self._drag_base_rect: QRectF | None = None
        self._pending_annotation_rect: QRectF | None = None
        self._drag_moved = False
        self._inline_editor: AnnotationTextEdit | None = None
        self._editing_annotation_xref: int | None = None
        self._editing_annotation_original_text = ""
        self._annotation_paste_available = False
        self._paste_annotation: FreeTextAnnotData | None = None
        self._paste_preview_rect: QRectF | None = None
        self._paste_drag_active = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def sizeHint(self):
        if self._pixmap and not self._pixmap.isNull():
            return self._pixmap.deviceIndependentSize().toSize()
        return super().sizeHint()

    def page_size_points(self) -> tuple[float, float]:
        if not self._pixmap or self._pixmap.isNull() or self._zoom_factor <= 0:
            return (0.0, 0.0)
        logical_size = self._pixmap.deviceIndependentSize()
        return (logical_size.width() / self._zoom_factor, logical_size.height() / self._zoom_factor)

    def _clear_annotation_create_drag(self) -> None:
        self._annotation_create_origin_page = None
        self._annotation_create_preview_rect = None

    def set_annotation_create_mode(self, enabled: bool) -> None:
        self._annotation_create_mode = bool(enabled)
        self._clear_annotation_create_drag()
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def set_selected_annotation_xref(self, xref: int | None) -> None:
        self._selected_annotation_xref = xref
        self.update()

    def has_active_text_editor(self) -> bool:
        return self._inline_editor is not None and self._inline_editor.isVisible()

    def set_annotation_paste_available(self, available: bool) -> None:
        self._annotation_paste_available = bool(available)

    def has_annotation_paste_mode(self) -> bool:
        return self._paste_annotation is not None

    def begin_annotation_paste_mode(self, annotation: FreeTextAnnotData) -> None:
        self._paste_annotation = annotation
        self._paste_preview_rect = None
        self._paste_drag_active = False
        self._selection_origin = None
        self._selection_rect = None
        self._selection_active = False
        self._pressed_link = None
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def cancel_annotation_paste_mode(self) -> None:
        if self._paste_annotation is None and self._paste_preview_rect is None and not self._paste_drag_active:
            return
        self._paste_annotation = None
        self._paste_preview_rect = None
        self._paste_drag_active = False
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def begin_annotation_text_edit(self, annotation: FreeTextAnnotData) -> None:
        self.cancel_annotation_text_edit()
        editor = AnnotationTextEdit(self)
        editor.setPlainText(annotation.content)
        editor.commit_requested.connect(lambda text, annot=annotation: self._commit_inline_editor(annot, text))
        editor.cancel_requested.connect(self.cancel_annotation_text_edit)
        editor.delete_requested.connect(self.annotation_delete_requested)
        editor.setStyleSheet(self._inline_editor_stylesheet(annotation))
        editor.setFrameStyle(QFrame.Shape.NoFrame)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setViewportMargins(0, 0, 0, 0)
        editor.document().setDocumentMargin(0)
        font = editor.font()
        font.setPixelSize(max(10, round(annotation.fontsize * self._zoom_factor)))
        editor.setFont(font)
        self._inline_editor = editor
        self._editing_annotation_xref = annotation.xref
        self._editing_annotation_original_text = annotation.content
        self._selected_annotation_xref = annotation.xref
        self._layout_inline_editor()
        editor.show()
        editor.setFocus()
        editor.selectAll()
        self.update()

    def commit_annotation_text_edit(self) -> None:
        if self._inline_editor is not None:
            self._inline_editor._emit_commit()

    def cancel_annotation_text_edit(self) -> None:
        if self._inline_editor is None:
            return
        editor = self._inline_editor
        self._inline_editor = None
        self._editing_annotation_xref = None
        self._editing_annotation_original_text = ""
        editor.hide()
        editor.deleteLater()
        self.annotation_text_edit_cancelled.emit()
        self.update()

    def _commit_inline_editor(self, annotation: FreeTextAnnotData, text: str) -> None:
        original = self._editing_annotation_original_text
        xref = self._editing_annotation_xref
        editor = self._inline_editor
        self._inline_editor = None
        self._editing_annotation_xref = None
        self._editing_annotation_original_text = ""
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        self.update()
        if xref == annotation.xref and text != original:
            self.annotation_text_committed.emit(annotation, text)
        else:
            self.annotation_text_edit_cancelled.emit()

    def _inline_editor_stylesheet(self, annotation: FreeTextAnnotData) -> str:
        fill = annotation.fill_color or (1.0, 1.0, 1.0)
        opacity = max(0.0, min(1.0, annotation.opacity))
        fill_rgba = f"rgba({round(fill[0] * 255)}, {round(fill[1] * 255)}, {round(fill[2] * 255)}, {round(opacity * 255)})"
        text_rgb = f"rgb({round(annotation.text_color[0] * 255)}, {round(annotation.text_color[1] * 255)}, {round(annotation.text_color[2] * 255)})"
        return (
            "QPlainTextEdit {"
            f"background-color: {fill_rgba};"
            f"color: {text_rgb};"
            "border: 0px solid transparent;"
            "padding: 0px;"
            "margin: 0px;"
            "}"
        )

    def _layout_inline_editor(self) -> None:
        if self._inline_editor is None or self._editing_annotation_xref is None:
            return
        annotation = self._annotation_by_xref(self._editing_annotation_xref)
        if annotation is None:
            return
        rect = self._annotation_widget_rect(annotation)
        self._inline_editor.setGeometry(rect.toRect())

    def set_page(self, pixmap, words, links, annotations, zoom_factor: float, selected_annotation_xref: int | None = None) -> None:
        self._pixmap = pixmap
        self._zoom_factor = zoom_factor or 1.0
        self._words = words or []
        self._links = links or []
        self._annotations = annotations or []

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

        self._annotation_rects = []
        for annot in self._annotations:
            x0, y0, x1, y1 = annot.rect
            self._annotation_rects.append(QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale))

        self._selected_word_indices = []
        self._selection_origin = None
        self._selection_rect = None
        self._selection_active = False
        self._pressed_link = None
        self._selected_annotation_xref = selected_annotation_xref
        self._hover_annotation_xref = None
        self._drag_annotation_xref = None
        self._drag_mode = None
        self._drag_origin_page = None
        self._drag_base_rect = None
        self._pending_annotation_rect = None
        self._drag_moved = False
        self._clear_annotation_create_drag()
        self._paste_preview_rect = None
        self._paste_drag_active = False
        if self._pixmap and not self._pixmap.isNull():
            self.setMinimumSize(self._pixmap.deviceIndependentSize().toSize())
        else:
            self.setMinimumSize(0, 0)
        self._layout_inline_editor()
        self.updateGeometry()
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def _rect_tuple_to_qrectf(self, rect: tuple[float, float, float, float]) -> QRectF:
        return QRectF(QPointF(rect[0], rect[1]), QPointF(rect[2], rect[3]))

    def _qrectf_to_rect_tuple(self, rect: QRectF) -> tuple[float, float, float, float]:
        normalized = rect.normalized()
        return (normalized.left(), normalized.top(), normalized.right(), normalized.bottom())

    def _page_rect_to_widget_rect(self, rect: QRectF) -> QRectF:
        offset = self._pixmap_offset()
        return QRectF(
            offset.x() + rect.left() * self._zoom_factor,
            offset.y() + rect.top() * self._zoom_factor,
            rect.width() * self._zoom_factor,
            rect.height() * self._zoom_factor,
        )

    def _pixmap_offset(self) -> QPoint:
        if not self._pixmap or self._pixmap.isNull():
            return QPoint(0, 0)
        logical_size = self._pixmap.deviceIndependentSize()
        x = max(0, int((self.width() - logical_size.width()) / 2))
        y = max(0, int((self.height() - logical_size.height()) / 2))
        return QPoint(x, y)

    def _point_in_pixmap(self, pos: QPoint) -> QPointF | None:
        if not self._pixmap or self._pixmap.isNull():
            return None
        offset = self._pixmap_offset()
        x = pos.x() - offset.x()
        y = pos.y() - offset.y()
        logical_size = self._pixmap.deviceIndependentSize()
        if x < 0 or y < 0 or x > logical_size.width() or y > logical_size.height():
            return None
        return QPointF(x, y)

    def _page_point_from_widget_pos(self, pos: QPoint, *, clamp: bool = False) -> QPointF | None:
        pix_pos = self._point_in_pixmap(pos)
        if pix_pos is None:
            if not clamp or not self._pixmap or self._pixmap.isNull():
                return None
            offset = self._pixmap_offset()
            logical_size = self._pixmap.deviceIndependentSize()
            clamped_x = min(max(pos.x() - offset.x(), 0.0), logical_size.width())
            clamped_y = min(max(pos.y() - offset.y(), 0.0), logical_size.height())
            pix_pos = QPointF(clamped_x, clamped_y)
        return QPointF(pix_pos.x() / self._zoom_factor, pix_pos.y() / self._zoom_factor)

    def page_point_from_global_pos(self, global_pos: QPoint) -> QPointF | None:
        return self._page_point_from_widget_pos(self.mapFromGlobal(global_pos))

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

    def _annotation_by_xref(self, xref: int | None) -> FreeTextAnnotData | None:
        if xref is None:
            return None
        for annot in self._annotations:
            if annot.xref == xref:
                return annot
        return None

    def _annotation_widget_rect(self, annot: FreeTextAnnotData, rect_override: QRectF | None = None) -> QRectF:
        rect = rect_override if rect_override is not None else self._rect_tuple_to_qrectf(annot.rect)
        return self._page_rect_to_widget_rect(rect)

    def _annotation_color(
        self,
        color: tuple[float, float, float] | None,
        *,
        opacity: float = 1.0,
    ) -> QColor | None:
        if color is None:
            return None
        return QColor(
            round(color[0] * 255),
            round(color[1] * 255),
            round(color[2] * 255),
            round(max(0.0, min(1.0, opacity)) * 255),
        )

    def _paint_annotation(self, painter: QPainter, annot: FreeTextAnnotData, rect: QRectF) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        fill_color = self._annotation_color(annot.fill_color, opacity=annot.opacity)
        if fill_color is not None:
            painter.fillRect(rect, fill_color)

        pen_width = max(0.0, float(annot.border_width) * self._zoom_factor)
        border_color = self._annotation_color(annot.border_color)
        if border_color is not None and pen_width > 0:
            border_pen = QPen(border_color)
            border_pen.setWidthF(pen_width)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = pen_width / 2.0
            border_rect = rect
            if rect.width() > pen_width and rect.height() > pen_width:
                border_rect = rect.adjusted(inset, inset, -inset, -inset)
            painter.drawRect(border_rect)

        if annot.content:
            font = painter.font()
            font.setPixelSize(max(10, round(annot.fontsize * self._zoom_factor)))
            painter.setFont(font)
            text_color = self._annotation_color(annot.text_color)
            if text_color is not None:
                painter.setPen(text_color)
            painter.drawText(
                rect,
                int(
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignTop
                    | Qt.TextFlag.TextWordWrap
                ),
                annot.content,
            )

        painter.restore()

    def _handle_rects(self, rect: QRectF) -> dict[str, QRectF]:
        size = float(self.HANDLE_SIZE)
        half = size / 2.0
        cx = rect.center().x()
        cy = rect.center().y()
        positions = {
            "nw": QPointF(rect.left(), rect.top()),
            "n": QPointF(cx, rect.top()),
            "ne": QPointF(rect.right(), rect.top()),
            "e": QPointF(rect.right(), cy),
            "se": QPointF(rect.right(), rect.bottom()),
            "s": QPointF(cx, rect.bottom()),
            "sw": QPointF(rect.left(), rect.bottom()),
            "w": QPointF(rect.left(), cy),
        }
        return {
            name: QRectF(point.x() - half, point.y() - half, size, size)
            for name, point in positions.items()
        }

    def _annotation_hit_test(self, pos: QPoint) -> tuple[FreeTextAnnotData | None, str | None]:
        selected = self._annotation_by_xref(self._selected_annotation_xref)
        if selected is not None:
            selected_rect = self._annotation_widget_rect(selected)
            for handle, handle_rect in self._handle_rects(selected_rect).items():
                if handle_rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                    return selected, handle
        for annot in reversed(self._annotations):
            if self._annotation_widget_rect(annot).contains(QPointF(pos)):
                return annot, "move"
        return None, None

    def _cursor_for_handle(self, handle: str | None) -> Qt.CursorShape:
        mapping = {
            "move": Qt.CursorShape.SizeAllCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
        }
        return mapping.get(handle, Qt.CursorShape.ArrowCursor)

    def _annotation_rect_close(self, left: QRectF | None, right: QRectF | None) -> bool:
        if left is None or right is None:
            return left is right
        return (
            abs(left.left() - right.left()) < 0.01
            and abs(left.top() - right.top()) < 0.01
            and abs(left.width() - right.width()) < 0.01
            and abs(left.height() - right.height()) < 0.01
        )

    def annotation_rect_for_page_point(
        self,
        annotation: FreeTextAnnotData,
        page_point: QPointF,
    ) -> QRectF | None:
        page_w, page_h = self.page_size_points()
        if page_w <= 0 or page_h <= 0:
            return None
        src_x0, src_y0, src_x1, src_y1 = annotation.rect
        width = min(max(1.0, float(src_x1 - src_x0)), page_w)
        height = min(max(1.0, float(src_y1 - src_y0)), page_h)
        left = min(max(0.0, page_point.x()), max(0.0, page_w - width))
        top = min(max(0.0, page_point.y()), max(0.0, page_h - height))
        return QRectF(left, top, width, height)

    def _paste_rect_for_page_point(self, page_point: QPointF) -> QRectF | None:
        if self._paste_annotation is None:
            return None
        return self.annotation_rect_for_page_point(self._paste_annotation, page_point)

    def _drag_updated_rect(self, current_page: QPointF) -> QRectF | None:
        if self._drag_mode is None or self._drag_origin_page is None or self._drag_base_rect is None:
            return None

        page_w, page_h = self.page_size_points()
        min_size = self.MIN_ANNOT_SIZE
        base = self._drag_base_rect

        if self._drag_mode == "move":
            width = base.width()
            height = base.height()
            left = base.left() + (current_page.x() - self._drag_origin_page.x())
            top = base.top() + (current_page.y() - self._drag_origin_page.y())
            left = min(max(0.0, left), max(0.0, page_w - width))
            top = min(max(0.0, top), max(0.0, page_h - height))
            return QRectF(left, top, width, height)

        left = base.left()
        top = base.top()
        right = base.right()
        bottom = base.bottom()

        if "w" in self._drag_mode:
            left = min(max(0.0, current_page.x()), right - min_size)
        if "e" in self._drag_mode:
            right = max(min(page_w, current_page.x()), left + min_size)
        if "n" in self._drag_mode:
            top = min(max(0.0, current_page.y()), bottom - min_size)
        if "s" in self._drag_mode:
            bottom = max(min(page_h, current_page.y()), top + min_size)

        return QRectF(QPointF(left, top), QPointF(right, bottom))

    def _update_selection(self) -> None:
        if not self._pixmap or self._pixmap.isNull() or self._selection_rect is None:
            self._selected_word_indices = []
            return
        offset = self._pixmap_offset()
        sel = QRectF(self._selection_rect.normalized())
        sel.translate(-offset.x(), -offset.y())
        logical_size = self._pixmap.deviceIndependentSize()
        pix_bounds = QRectF(0, 0, logical_size.width(), logical_size.height())
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
        if self.has_annotation_paste_mode():
            if self._point_in_pixmap(pos) is not None:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._annotation_create_mode:
            if self._point_in_pixmap(pos) is not None:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        annot, handle = self._annotation_hit_test(pos)
        if annot is not None:
            self._hover_annotation_xref = annot.xref
            self.setCursor(self._cursor_for_handle(handle))
            return
        self._hover_annotation_xref = None
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

            for annot in self._annotations:
                preview_rect: QRectF | None = None
                if annot.xref == self._drag_annotation_xref and self._pending_annotation_rect is not None:
                    preview_rect = self._annotation_widget_rect(annot, self._pending_annotation_rect)
                annot_rect = preview_rect or self._annotation_widget_rect(annot)
                is_being_edited = (
                    annot.xref == self._editing_annotation_xref
                    and self._inline_editor is not None
                    and self._inline_editor.isVisible()
                )
                if not is_being_edited:
                    self._paint_annotation(painter, annot, annot_rect)
                if annot.xref == self._selected_annotation_xref:
                    painter.setPen(QPen(QColor(0, 120, 215), 1))
                    painter.setBrush(QBrush(QColor(255, 255, 255)))
                    for handle_rect in self._handle_rects(annot_rect).values():
                        painter.drawRect(handle_rect)

        if self._selection_rect is not None:
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._selection_rect.normalized())
        if self._annotation_create_preview_rect is not None:
            preview_rect = self._page_rect_to_widget_rect(self._annotation_create_preview_rect)
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(preview_rect)
        if self._paste_annotation is not None and self._paste_preview_rect is not None:
            preview_rect = self._annotation_widget_rect(self._paste_annotation, self._paste_preview_rect)
            self._paint_annotation(painter, self._paste_annotation, preview_rect)
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(preview_rect)

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
                page_point = self._page_point_from_widget_pos(event.pos())
                if self.has_annotation_paste_mode():
                    if page_point is None:
                        self.cancel_annotation_paste_mode()
                        event.accept()
                        return
                    if self._selected_annotation_xref is not None:
                        self._selected_annotation_xref = None
                        self.annotation_selected.emit(None)
                    self._selection_origin = None
                    self._selection_rect = None
                    self._selection_active = False
                    self._pressed_link = None
                    self._paste_drag_active = True
                    self._paste_preview_rect = self._paste_rect_for_page_point(page_point)
                    self.update()
                    event.accept()
                    return
                if self._annotation_create_mode:
                    if page_point is None:
                        event.accept()
                        return
                    if self._selected_annotation_xref is not None:
                        self._selected_annotation_xref = None
                        self.annotation_selected.emit(None)
                    self._selected_word_indices = []
                    self._selection_origin = None
                    self._selection_rect = None
                    self._selection_active = False
                    self._pressed_link = None
                    self._annotation_create_origin_page = page_point
                    self._annotation_create_preview_rect = QRectF(page_point, page_point)
                    self.update()
                    event.accept()
                    return

                annot, handle = self._annotation_hit_test(event.pos())
                if annot is not None and handle is not None:
                    self._selected_annotation_xref = annot.xref
                    self.annotation_selected.emit(annot)
                    self._drag_annotation_xref = annot.xref
                    self._drag_mode = handle
                    self._drag_origin_page = self._page_point_from_widget_pos(event.pos(), clamp=True)
                    self._drag_base_rect = self._rect_tuple_to_qrectf(annot.rect)
                    self._pending_annotation_rect = QRectF(self._drag_base_rect)
                    self._drag_moved = False
                    self.update()
                    event.accept()
                    return

                if self._selected_annotation_xref is not None:
                    self._selected_annotation_xref = None
                    self.annotation_selected.emit(None)

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

    def mouseDoubleClickEvent(self, event) -> None:
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                annot, handle = self._annotation_hit_test(event.pos())
                if annot is not None and handle == "move":
                    self.annotation_edit_requested.emit(annot)
                    event.accept()
                    return
        except Exception:
            logger.exception("Error in ZoomPageWidget.mouseDoubleClickEvent")
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        try:
            if self.has_annotation_paste_mode() and self._paste_drag_active:
                current_page = self._page_point_from_widget_pos(event.pos(), clamp=True)
                if current_page is not None:
                    self._paste_preview_rect = self._paste_rect_for_page_point(current_page)
                    self.update()
                event.accept()
                return
            if (
                self._annotation_create_mode
                and self._annotation_create_origin_page is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                current_page = self._page_point_from_widget_pos(event.pos(), clamp=True)
                if current_page is not None:
                    self._annotation_create_preview_rect = QRectF(
                        self._annotation_create_origin_page,
                        current_page,
                    ).normalized()
                    self.update()
                event.accept()
                return
            if event.buttons() & Qt.MouseButton.LeftButton and self._drag_mode is not None:
                current_page = self._page_point_from_widget_pos(event.pos(), clamp=True)
                if current_page is not None:
                    updated = self._drag_updated_rect(current_page)
                    if updated is not None:
                        self._pending_annotation_rect = updated
                        self._drag_moved = not self._annotation_rect_close(updated, self._drag_base_rect)
                        self.update()
                event.accept()
                return
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
                if self.has_annotation_paste_mode():
                    final_rect = self._paste_preview_rect
                    self.cancel_annotation_paste_mode()
                    if final_rect is not None:
                        self.annotation_paste_placement_requested.emit(
                            self._qrectf_to_rect_tuple(final_rect)
                        )
                    event.accept()
                    return
                if self._annotation_create_mode and self._annotation_create_origin_page is not None:
                    current_page = self._page_point_from_widget_pos(event.pos(), clamp=True)
                    final_rect = self._annotation_create_preview_rect
                    if current_page is not None:
                        final_rect = QRectF(self._annotation_create_origin_page, current_page).normalized()
                    self._clear_annotation_create_drag()
                    self.update()
                    if (
                        final_rect is not None
                        and final_rect.width() > 0
                        and final_rect.height() > 0
                    ):
                        self.annotation_create_requested.emit(
                            self._qrectf_to_rect_tuple(final_rect)
                        )
                    event.accept()
                    return
                if self._drag_mode is not None:
                    annot = self._annotation_by_xref(self._drag_annotation_xref)
                    final_rect = self._pending_annotation_rect or self._drag_base_rect
                    if annot is not None and final_rect is not None and self._drag_moved:
                        self.annotation_geometry_changed.emit(
                            annot,
                            self._qrectf_to_rect_tuple(final_rect),
                            self._drag_mode,
                        )
                    self._drag_annotation_xref = None
                    self._drag_mode = None
                    self._drag_origin_page = None
                    self._drag_base_rect = None
                    self._pending_annotation_rect = None
                    self._drag_moved = False
                    self.update()
                    event.accept()
                    return
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
        if event.key() == Qt.Key.Key_Escape and self.has_annotation_paste_mode():
            self.cancel_annotation_paste_mode()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            annot = self._annotation_by_xref(self._selected_annotation_xref)
            if annot is not None:
                self.annotation_copy_requested.emit(annot)
                event.accept()
                return
            text = self._selected_text()
            if text:
                QApplication.clipboard().setText(text)
                event.accept()
                return
        if event.matches(QKeySequence.StandardKey.Paste) and self._annotation_paste_available:
            self.annotation_paste_requested.emit()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Delete
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self._selected_annotation_xref is not None
        ):
            self.annotation_delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_inline_editor()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self.has_annotation_paste_mode():
            self.cancel_annotation_paste_mode()


class PageEditWindow(QMainWindow):
    """Window for editing pages within a PDF."""

    ZOOM_MIN = 25
    ZOOM_MAX = 400
    ZOOM_STEP = 5
    PREVIEW_THUMB_MIN = 80
    PREVIEW_THUMB_MAX = 400
    PREVIEW_THUMB_STEP = 20

    def __init__(self, pdf_path: str, undo_manager: UndoManager, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._undo_manager = undo_manager
        self._did_initial_grid_layout = False
        self._thumbnails: list[PageThumbnail] = []
        self._selected_thumbnails: list[PageThumbnail] = []
        self._grid_scroll = None
        self._zoom_view = None
        self._zoom_scroll = None
        self._zoom_label = None
        self._zoom_percent_label = None
        self._zoom_page_label = None
        self._zoom_prev_btn = None
        self._zoom_next_btn = None
        self._zoom_page_num = None
        self._zoom_factor = 1.0
        self._zoom_text_cache: dict[int, tuple[list[tuple], list[dict]]] = {}
        self._zoom_annotations: list[FreeTextAnnotData] = []
        self._selected_zoom_annotation: FreeTextAnnotData | None = None
        self._copied_zoom_annotation: FreeTextAnnotData | None = None
        self._zoom_annotation_drawer = None
        self._zoom_annotation_panel = None
        self._zoom_annotation_toggle_btn = None
        self._zoom_annotation_open = False
        self._zoom_annotation_form_sync = False
        self._zoom_annotation_text_commit_in_progress = False
        self._zoom_annotation_new_btn = None
        self._zoom_annotation_delete_btn = None
        self._zoom_annotation_width_spin = None
        self._zoom_annotation_height_spin = None
        self._zoom_annotation_fontsize_spin = None
        self._zoom_annotation_opacity_slider = None
        self._zoom_annotation_opacity_label = None
        self._zoom_annotation_border_width_spin = None
        self._zoom_annotation_text_color_btn = None
        self._zoom_annotation_fill_color_btn = None
        self._zoom_annotation_border_color_btn = None
        self._zoom_annotation_text_color = (0.0, 0.0, 0.0)
        self._zoom_annotation_fill_color = (1.0, 1.0, 0.6)
        self._zoom_annotation_border_color = (0.0, 0.0, 0.0)
        self._thumb_size = PageThumbnail.THUMBNAIL_SIZE
        self._thumb_render_queue: deque[int] = deque()
        self._thumb_render_queue_set: set[int] = set()
        self._thumb_render_timer = QTimer(self)
        self._thumb_render_timer.setSingleShot(True)
        self._thumb_render_timer.timeout.connect(self._process_thumbnail_render_queue)
        self._scroll_debounce_timer = QTimer(self)
        self._scroll_debounce_timer.setSingleShot(True)
        self._scroll_debounce_timer.setInterval(150)
        self._scroll_debounce_timer.timeout.connect(self._enqueue_visible_thumbnail_renders)

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
        QTimer.singleShot(0, self._load_pages)

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
        self._grid_scroll.viewport().installEventFilter(self)
        self._grid_scroll.verticalScrollBar().valueChanged.connect(self._on_grid_viewport_changed)
        self._grid_scroll.horizontalScrollBar().valueChanged.connect(self._on_grid_viewport_changed)
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

        controls_layout.addStretch()

        self._zoom_page_label = QLabel("")
        controls_layout.addWidget(self._zoom_page_label)

        zoom_layout.addWidget(zoom_controls)

        self._zoom_scroll = QScrollArea()
        self._zoom_scroll.setWidgetResizable(True)
        self._zoom_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_label = ZoomPageWidget()
        self._zoom_label.wheel_zoom.connect(self._on_zoom_wheel)
        self._zoom_label.link_clicked.connect(self._on_zoom_link_clicked)
        self._zoom_label.annotation_selected.connect(self._on_zoom_annotation_selected)
        self._zoom_label.annotation_geometry_changed.connect(self._on_zoom_annotation_geometry_changed)
        self._zoom_label.annotation_create_requested.connect(self._on_zoom_annotation_create_requested)
        self._zoom_label.annotation_edit_requested.connect(self._on_zoom_annotation_edit_requested)
        self._zoom_label.annotation_text_committed.connect(self._on_zoom_annotation_text_committed)
        self._zoom_label.annotation_text_edit_cancelled.connect(self._on_zoom_annotation_text_edit_cancelled)
        self._zoom_label.annotation_delete_requested.connect(self._delete_selected_zoom_annotation)
        self._zoom_label.annotation_copy_requested.connect(self._on_zoom_annotation_copy_requested)
        self._zoom_label.annotation_paste_requested.connect(self._on_zoom_annotation_paste_requested)
        self._zoom_label.annotation_paste_placement_requested.connect(self._on_zoom_annotation_paste_placement_requested)
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

        zoom_content = QWidget()
        zoom_content_layout = QHBoxLayout(zoom_content)
        zoom_content_layout.setContentsMargins(0, 0, 0, 0)
        zoom_content_layout.setSpacing(0)
        zoom_content_layout.addWidget(nav_container, 1)

        self._zoom_annotation_drawer = QFrame()
        self._zoom_annotation_drawer.setFrameShape(QFrame.Shape.StyledPanel)
        self._zoom_annotation_drawer.setStyleSheet("QFrame { background-color: #f5f5f5; }")
        drawer_layout = QHBoxLayout(self._zoom_annotation_drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(0)

        self._zoom_annotation_toggle_btn = QToolButton()
        self._zoom_annotation_toggle_btn.setText("◀")
        self._zoom_annotation_toggle_btn.setToolTip("付箋編集")
        self._zoom_annotation_toggle_btn.clicked.connect(self._toggle_zoom_annotation_drawer)
        self._zoom_annotation_toggle_btn.setFixedWidth(32)
        drawer_layout.addWidget(self._zoom_annotation_toggle_btn)

        self._zoom_annotation_panel = QWidget()
        panel_layout = QVBoxLayout(self._zoom_annotation_panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        title = QLabel("FreeText")
        panel_layout.addWidget(title)

        action_row = QHBoxLayout()
        self._zoom_annotation_new_btn = QPushButton("新規")
        self._zoom_annotation_new_btn.setCheckable(True)
        self._zoom_annotation_new_btn.clicked.connect(self._on_zoom_annotation_new_clicked)
        action_row.addWidget(self._zoom_annotation_new_btn)

        self._zoom_annotation_delete_btn = QPushButton("削除")
        self._zoom_annotation_delete_btn.clicked.connect(self._delete_selected_zoom_annotation)
        action_row.addWidget(self._zoom_annotation_delete_btn)
        panel_layout.addLayout(action_row)

        form = QFormLayout()
        self._zoom_annotation_width_spin = QSpinBox()
        self._zoom_annotation_width_spin.setRange(20, 5000)
        self._zoom_annotation_width_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        form.addRow("幅", self._zoom_annotation_width_spin)

        self._zoom_annotation_height_spin = QSpinBox()
        self._zoom_annotation_height_spin.setRange(20, 5000)
        self._zoom_annotation_height_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        form.addRow("高さ", self._zoom_annotation_height_spin)

        self._zoom_annotation_fontsize_spin = QSpinBox()
        self._zoom_annotation_fontsize_spin.setRange(6, 400)
        self._zoom_annotation_fontsize_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        form.addRow("文字サイズ", self._zoom_annotation_fontsize_spin)

        self._zoom_annotation_border_width_spin = QSpinBox()
        self._zoom_annotation_border_width_spin.setRange(0, 100)
        self._zoom_annotation_border_width_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        form.addRow("線幅", self._zoom_annotation_border_width_spin)
        panel_layout.addLayout(form)

        opacity_row = QWidget()
        opacity_layout = QHBoxLayout(opacity_row)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        opacity_layout.addWidget(QLabel("透明度"))
        self._zoom_annotation_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_annotation_opacity_slider.setRange(0, 100)
        self._zoom_annotation_opacity_slider.valueChanged.connect(self._on_zoom_annotation_opacity_changed)
        opacity_layout.addWidget(self._zoom_annotation_opacity_slider, 1)
        self._zoom_annotation_opacity_label = QLabel("0%")
        opacity_layout.addWidget(self._zoom_annotation_opacity_label)
        panel_layout.addWidget(opacity_row)

        self._zoom_annotation_text_color_btn = QPushButton()
        self._zoom_annotation_text_color_btn.clicked.connect(lambda: self._pick_zoom_annotation_color("text"))
        panel_layout.addWidget(self._build_labeled_color_row("文字色", self._zoom_annotation_text_color_btn))

        self._zoom_annotation_fill_color_btn = QPushButton()
        self._zoom_annotation_fill_color_btn.clicked.connect(lambda: self._pick_zoom_annotation_color("fill"))
        panel_layout.addWidget(self._build_labeled_color_row("背景色", self._zoom_annotation_fill_color_btn))

        self._zoom_annotation_border_color_btn = QPushButton()
        self._zoom_annotation_border_color_btn.clicked.connect(lambda: self._pick_zoom_annotation_color("border"))
        panel_layout.addWidget(self._build_labeled_color_row("線色", self._zoom_annotation_border_color_btn))

        panel_layout.addStretch()
        drawer_layout.addWidget(self._zoom_annotation_panel)
        zoom_content_layout.addWidget(self._zoom_annotation_drawer)
        zoom_layout.addWidget(zoom_content, 1)
        self._set_zoom_annotation_drawer_open(False)
        self._set_selected_zoom_annotation(None)

        layout.addWidget(self._zoom_view)
        self._zoom_view.hide()

    def _build_labeled_color_row(self, label_text: str, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label_text))
        button.setMinimumWidth(120)
        layout.addWidget(button, 1)
        return row

    def _set_color_button_preview(
        self,
        button: QPushButton | None,
        color: tuple[float, float, float] | None,
        *,
        allow_none: bool = False,
    ) -> None:
        if button is None:
            return
        if color is None and allow_none:
            button.setText("透明")
            button.setStyleSheet("")
            return
        qcolor = self._rgb_tuple_to_qcolor(color or (0.0, 0.0, 0.0))
        button.setText(qcolor.name())
        text_color = "#ffffff" if qcolor.lightnessF() < 0.5 else "#000000"
        button.setStyleSheet(f"background-color: {qcolor.name()}; color: {text_color};")

    def _rgb_tuple_to_qcolor(self, color: tuple[float, float, float]) -> QColor:
        return QColor(
            max(0, min(255, round(color[0] * 255))),
            max(0, min(255, round(color[1] * 255))),
            max(0, min(255, round(color[2] * 255))),
        )

    def _qcolor_to_rgb_tuple(self, color: QColor) -> tuple[float, float, float]:
        return (color.redF(), color.greenF(), color.blueF())

    def _set_zoom_annotation_drawer_open(self, is_open: bool) -> None:
        self._zoom_annotation_open = bool(is_open)
        if self._zoom_annotation_panel:
            self._zoom_annotation_panel.setVisible(self._zoom_annotation_open)
        if self._zoom_annotation_drawer:
            self._zoom_annotation_drawer.setFixedWidth(320 if self._zoom_annotation_open else 32)
        if self._zoom_annotation_toggle_btn:
            self._zoom_annotation_toggle_btn.setText("▶" if self._zoom_annotation_open else "◀")

    def _toggle_zoom_annotation_drawer(self) -> None:
        self._set_zoom_annotation_drawer_open(not self._zoom_annotation_open)

    def _set_zoom_annotation_create_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._zoom_label:
            if enabled:
                self._zoom_label.cancel_annotation_paste_mode()
            self._zoom_label.set_annotation_create_mode(enabled)
        if self._zoom_annotation_new_btn:
            with QSignalBlocker(self._zoom_annotation_new_btn):
                self._zoom_annotation_new_btn.setChecked(enabled)
            self._zoom_annotation_new_btn.setText("配置待ち" if enabled else "新規")

    def _set_selected_zoom_annotation(
        self,
        annotation: FreeTextAnnotData | None,
        *,
        open_drawer: bool = False,
    ) -> None:
        self._commit_inline_annotation_editor()
        self._selected_zoom_annotation = annotation
        if self._zoom_label:
            self._zoom_label.set_selected_annotation_xref(annotation.xref if annotation else None)
        if open_drawer and annotation is not None:
            self._set_zoom_annotation_drawer_open(True)
        self._zoom_annotation_form_sync = True
        try:
            widgets = [
                self._zoom_annotation_width_spin,
                self._zoom_annotation_height_spin,
                self._zoom_annotation_fontsize_spin,
                self._zoom_annotation_opacity_slider,
                self._zoom_annotation_border_width_spin,
                self._zoom_annotation_text_color_btn,
                self._zoom_annotation_fill_color_btn,
                self._zoom_annotation_border_color_btn,
            ]
            has_selection = annotation is not None
            for widget in widgets:
                if widget is not None:
                    widget.setEnabled(has_selection)
            if self._zoom_annotation_delete_btn:
                self._zoom_annotation_delete_btn.setEnabled(has_selection)

            if annotation is None:
                for spin in (
                    self._zoom_annotation_width_spin,
                    self._zoom_annotation_height_spin,
                    self._zoom_annotation_fontsize_spin,
                    self._zoom_annotation_border_width_spin,
                ):
                    if spin is not None:
                        spin.clear()
                if self._zoom_annotation_opacity_slider is not None:
                    self._zoom_annotation_opacity_slider.setValue(0)
                if self._zoom_annotation_opacity_label is not None:
                    self._zoom_annotation_opacity_label.setText("0%")
                self._zoom_annotation_text_color = (0.0, 0.0, 0.0)
                self._zoom_annotation_fill_color = (1.0, 1.0, 0.6)
                self._zoom_annotation_border_color = (0.0, 0.0, 0.0)
            else:
                x0, y0, x1, y1 = annotation.rect
                if self._zoom_annotation_width_spin:
                    self._zoom_annotation_width_spin.setValue(max(20, round(x1 - x0)))
                if self._zoom_annotation_height_spin:
                    self._zoom_annotation_height_spin.setValue(max(20, round(y1 - y0)))
                if self._zoom_annotation_fontsize_spin:
                    self._zoom_annotation_fontsize_spin.setValue(max(6, round(annotation.fontsize)))
                if self._zoom_annotation_opacity_slider:
                    self._zoom_annotation_opacity_slider.setValue(round(annotation.opacity * 100))
                if self._zoom_annotation_opacity_label:
                    self._zoom_annotation_opacity_label.setText(f"{round(annotation.opacity * 100)}%")
                if self._zoom_annotation_border_width_spin:
                    self._zoom_annotation_border_width_spin.setValue(round(annotation.border_width))
                self._zoom_annotation_text_color = annotation.text_color
                self._zoom_annotation_fill_color = annotation.fill_color or (1.0, 1.0, 0.6)
                self._zoom_annotation_border_color = annotation.border_color or (0.0, 0.0, 0.0)

            self._set_color_button_preview(self._zoom_annotation_text_color_btn, self._zoom_annotation_text_color)
            self._set_color_button_preview(self._zoom_annotation_fill_color_btn, self._zoom_annotation_fill_color)
            self._set_color_button_preview(
                self._zoom_annotation_border_color_btn,
                None if (annotation is not None and annotation.border_width <= 0) else self._zoom_annotation_border_color,
                allow_none=True,
            )
        finally:
            self._zoom_annotation_form_sync = False

    def _pick_zoom_annotation_color(self, kind: str) -> None:
        if self._selected_zoom_annotation is None:
            return
        if kind == "text":
            current = self._zoom_annotation_text_color
        elif kind == "fill":
            current = self._zoom_annotation_fill_color
        else:
            current = self._zoom_annotation_border_color
        color = QColorDialog.getColor(self._rgb_tuple_to_qcolor(current), self, "色を選択")
        if not color.isValid():
            return
        rgb = self._qcolor_to_rgb_tuple(color)
        if kind == "text":
            self._zoom_annotation_text_color = rgb
            self._set_color_button_preview(self._zoom_annotation_text_color_btn, rgb)
        elif kind == "fill":
            self._zoom_annotation_fill_color = rgb
            self._set_color_button_preview(self._zoom_annotation_fill_color_btn, rgb)
        else:
            self._zoom_annotation_border_color = rgb
            self._set_color_button_preview(self._zoom_annotation_border_color_btn, rgb, allow_none=True)
        self._apply_zoom_annotation_form()

    def _current_zoom_annotation_page_size(self) -> tuple[float, float]:
        if self._zoom_label:
            return self._zoom_label.page_size_points()
        return (0.0, 0.0)

    def _annotation_data_from_form(
        self,
        base: FreeTextAnnotData,
        *,
        rect: tuple[float, float, float, float] | None = None,
    ) -> FreeTextAnnotData:
        x0, y0, x1, y1 = rect or base.rect
        if rect is None and self._zoom_annotation_width_spin and self._zoom_annotation_height_spin:
            width = float(self._zoom_annotation_width_spin.value())
            height = float(self._zoom_annotation_height_spin.value())
            x1 = x0 + width
            y1 = y0 + height
        border_width = float(self._zoom_annotation_border_width_spin.value()) if self._zoom_annotation_border_width_spin else base.border_width
        border_color = None if border_width <= 0 else self._zoom_annotation_border_color
        return FreeTextAnnotData(
            page_num=base.page_num,
            xref=base.xref,
            rect=(x0, y0, x1, y1),
            content=base.content,
            fontsize=float(self._zoom_annotation_fontsize_spin.value()) if self._zoom_annotation_fontsize_spin else base.fontsize,
            text_color=self._zoom_annotation_text_color,
            fill_color=self._zoom_annotation_fill_color,
            border_color=border_color,
            border_width=border_width,
            opacity=(float(self._zoom_annotation_opacity_slider.value()) / 100.0) if self._zoom_annotation_opacity_slider else base.opacity,
            fontname=base.fontname,
            annotation_id=base.annotation_id,
            subject=base.subject,
        )

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
        register_shortcuts(
            self,
            (
                (QKeySequence.StandardKey.Undo, self._on_undo),
                (QKeySequence.StandardKey.Redo, self._on_redo),
                (QKeySequence.StandardKey.Delete, self._on_delete),
                (QKeySequence(Qt.Key.Key_F2), self._on_rename),
                (QKeySequence.StandardKey.SelectAll, self._on_select_all),
                (QKeySequence(Qt.Key.Key_R), self._on_rotate),
            ),
        )

    def _find_zoom_annotation(self, xref: int | None) -> FreeTextAnnotData | None:
        if xref is None:
            return None
        for annot in self._zoom_annotations:
            if annot.xref == xref:
                return annot
        return None

    def _set_copied_zoom_annotation(self, annotation: FreeTextAnnotData | None) -> None:
        self._copied_zoom_annotation = annotation
        if self._zoom_label:
            self._zoom_label.set_annotation_paste_available(annotation is not None)

    def _copy_zoom_annotation_data(self, annotation: FreeTextAnnotData) -> FreeTextAnnotData:
        return FreeTextAnnotData(
            page_num=annotation.page_num,
            xref=annotation.xref,
            rect=annotation.rect,
            content=annotation.content,
            fontsize=annotation.fontsize,
            text_color=annotation.text_color,
            fill_color=annotation.fill_color,
            border_color=annotation.border_color,
            border_width=annotation.border_width,
            opacity=annotation.opacity,
            fontname=annotation.fontname,
            annotation_id=annotation.annotation_id,
            subject=annotation.subject,
        )

    def _commit_inline_annotation_editor(self) -> None:
        if (
            self._zoom_label
            and self._zoom_label.has_active_text_editor()
            and not self._zoom_annotation_text_commit_in_progress
        ):
            self._zoom_label.commit_annotation_text_edit()

    def _refresh_current_zoom_page(self, *, open_drawer: bool = False) -> None:
        self._commit_inline_annotation_editor()
        if self._zoom_page_num is not None and self._zoom_page_num < len(self._thumbnails):
            self._request_thumbnail_refresh(self._zoom_page_num)
        if self._zoom_view and self._zoom_view.isVisible():
            self._render_zoom_page()
            if open_drawer and self._selected_zoom_annotation is not None:
                self._set_zoom_annotation_drawer_open(True)

    def _on_zoom_annotation_selected(self, annotation: object) -> None:
        self._set_zoom_annotation_create_mode(False)
        if isinstance(annotation, FreeTextAnnotData):
            current = self._find_zoom_annotation(annotation.xref) or annotation
            self._set_selected_zoom_annotation(current, open_drawer=True)
        else:
            self._set_selected_zoom_annotation(None)

    def _on_zoom_annotation_edit_requested(self, annotation: object) -> None:
        if not isinstance(annotation, FreeTextAnnotData) or not self._zoom_label:
            return
        current = self._find_zoom_annotation(annotation.xref) or annotation
        self._set_selected_zoom_annotation(current, open_drawer=True)
        self._zoom_label.begin_annotation_text_edit(current)

    def _on_zoom_annotation_text_committed(self, annotation: object, text: str) -> None:
        if not isinstance(annotation, FreeTextAnnotData):
            return
        current = self._find_zoom_annotation(annotation.xref) or annotation
        if text == current.content:
            return
        new_annotation = FreeTextAnnotData(
            page_num=current.page_num,
            xref=current.xref,
            rect=current.rect,
            content=text,
            fontsize=current.fontsize,
            text_color=current.text_color,
            fill_color=current.fill_color,
            border_color=current.border_color,
            border_width=current.border_width,
            opacity=current.opacity,
            fontname=current.fontname,
            annotation_id=current.annotation_id,
            subject=current.subject,
        )
        self._zoom_annotation_text_commit_in_progress = True
        try:
            self._run_zoom_annotation_replace(current, new_annotation, "Edit FreeText text")
        finally:
            self._zoom_annotation_text_commit_in_progress = False

    def _on_zoom_annotation_text_edit_cancelled(self) -> None:
        pass

    def _on_zoom_annotation_copy_requested(self, annotation: object) -> None:
        if not isinstance(annotation, FreeTextAnnotData):
            return
        current = self._find_zoom_annotation(annotation.xref) or annotation
        self._set_copied_zoom_annotation(self._copy_zoom_annotation_data(current))

    def _on_zoom_annotation_paste_requested(self) -> None:
        if self._copied_zoom_annotation is None or self._zoom_label is None or self._zoom_page_num is None:
            return
        self._set_zoom_annotation_create_mode(False)
        page_point = self._zoom_label.page_point_from_global_pos(QCursor.pos())
        if page_point is None:
            return
        rect = self._zoom_label.annotation_rect_for_page_point(self._copied_zoom_annotation, page_point)
        if rect is None:
            return
        self._on_zoom_annotation_paste_placement_requested(
            self._zoom_label._qrectf_to_rect_tuple(rect)
        )

    def _on_zoom_annotation_paste_placement_requested(self, rect: object) -> None:
        if (
            self._copied_zoom_annotation is None
            or self._zoom_page_num is None
            or not isinstance(rect, tuple)
            or len(rect) != 4
        ):
            return
        paste_data = FreeTextAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            rect=(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
            content=self._copied_zoom_annotation.content,
            fontsize=self._copied_zoom_annotation.fontsize,
            text_color=self._copied_zoom_annotation.text_color,
            fill_color=self._copied_zoom_annotation.fill_color,
            border_color=self._copied_zoom_annotation.border_color,
            border_width=self._copied_zoom_annotation.border_width,
            opacity=self._copied_zoom_annotation.opacity,
            fontname=self._copied_zoom_annotation.fontname,
            annotation_id="",
            subject=self._copied_zoom_annotation.subject,
        )
        state: dict[str, FreeTextAnnotData | None] = {"created": None}

        def do_paste() -> None:
            state["created"] = create_freetext_annot(self._pdf_path, paste_data)
            self._selected_zoom_annotation = state["created"]
            self._refresh_current_zoom_page(open_drawer=True)

        def undo_paste() -> None:
            created = state["created"]
            if created is not None:
                delete_freetext_annot(self._pdf_path, created.page_num, created.xref)
            self._selected_zoom_annotation = None
            self._refresh_current_zoom_page()

        do_paste()
        self._undo_manager.add_action(UndoAction(
            description="Paste FreeText",
            undo_func=undo_paste,
            redo_func=do_paste,
        ))

    def _on_zoom_annotation_form_value_changed(self, _value: int) -> None:
        self._apply_zoom_annotation_form()

    def _on_zoom_annotation_opacity_changed(self, value: int) -> None:
        if self._zoom_annotation_opacity_label:
            self._zoom_annotation_opacity_label.setText(f"{int(value)}%")
        self._apply_zoom_annotation_form()

    def _on_zoom_annotation_new_clicked(self, checked: bool) -> None:
        if not self._zoom_view or not self._zoom_view.isVisible() or self._zoom_page_num is None:
            self._set_zoom_annotation_create_mode(False)
            return
        self._set_zoom_annotation_drawer_open(True)
        self._set_zoom_annotation_create_mode(checked)

    def _on_zoom_annotation_create_requested(self, rect: object) -> None:
        if (
            self._zoom_page_num is None
            or not isinstance(rect, tuple)
            or len(rect) != 4
        ):
            return
        rect_tuple = tuple(float(value) for value in rect)
        if rect_tuple[2] <= rect_tuple[0] or rect_tuple[3] <= rect_tuple[1]:
            return
        self._set_zoom_annotation_create_mode(False)
        template = FreeTextAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            rect=rect_tuple,
            content="",
            fontsize=14.0,
            text_color=self._zoom_annotation_text_color,
            fill_color=self._zoom_annotation_fill_color,
            border_color=self._zoom_annotation_border_color,
            border_width=1.0,
            opacity=1.0,
        )
        state: dict[str, FreeTextAnnotData | None] = {"created": None}

        def do_create() -> None:
            state["created"] = create_freetext_annot(self._pdf_path, template)
            self._selected_zoom_annotation = state["created"]
            self._refresh_current_zoom_page(open_drawer=True)
            if self._zoom_label and self._selected_zoom_annotation is not None:
                current = self._find_zoom_annotation(self._selected_zoom_annotation.xref) or self._selected_zoom_annotation
                self._zoom_label.begin_annotation_text_edit(current)

        def undo_create() -> None:
            created = state["created"]
            if created is not None:
                delete_freetext_annot(self._pdf_path, created.page_num, created.xref)
            self._selected_zoom_annotation = None
            self._refresh_current_zoom_page()

        do_create()
        self._undo_manager.add_action(UndoAction(
            description="Create FreeText",
            undo_func=undo_create,
            redo_func=do_create,
        ))

    def _run_zoom_annotation_replace(
        self,
        old_annotation: FreeTextAnnotData,
        new_annotation: FreeTextAnnotData,
        description: str,
    ) -> None:
        if not self._zoom_annotation_text_commit_in_progress:
            self._commit_inline_annotation_editor()
        state = {"old": old_annotation, "new": None}

        def do_replace() -> None:
            state["new"] = replace_freetext_annot(
                self._pdf_path,
                old_annotation.page_num,
                state["old"].xref,
                new_annotation,
            )
            self._selected_zoom_annotation = state["new"]
            self._refresh_current_zoom_page(open_drawer=True)

        def undo_replace() -> None:
            state["old"] = replace_freetext_annot(
                self._pdf_path,
                old_annotation.page_num,
                state["new"].xref,
                state["old"],
            )
            self._selected_zoom_annotation = state["old"]
            self._refresh_current_zoom_page(open_drawer=True)

        do_replace()
        self._undo_manager.add_action(UndoAction(
            description=description,
            undo_func=undo_replace,
            redo_func=do_replace,
        ))

    def _apply_zoom_annotation_form(self) -> None:
        if self._zoom_annotation_form_sync or self._selected_zoom_annotation is None:
            return
        old_annotation = self._selected_zoom_annotation
        new_annotation = self._annotation_data_from_form(old_annotation)
        if (
            old_annotation.rect == new_annotation.rect
            and old_annotation.content == new_annotation.content
            and abs(old_annotation.fontsize - new_annotation.fontsize) < 0.01
            and old_annotation.text_color == new_annotation.text_color
            and old_annotation.fill_color == new_annotation.fill_color
            and old_annotation.border_color == new_annotation.border_color
            and abs(old_annotation.border_width - new_annotation.border_width) < 0.01
            and abs(old_annotation.opacity - new_annotation.opacity) < 0.01
        ):
            return
        self._run_zoom_annotation_replace(old_annotation, new_annotation, "Update FreeText")

    def _delete_selected_zoom_annotation(self) -> None:
        if self._selected_zoom_annotation is None:
            return
        self._commit_inline_annotation_editor()
        state = {"old": self._selected_zoom_annotation}

        def do_delete() -> None:
            delete_freetext_annot(self._pdf_path, state["old"].page_num, state["old"].xref)
            self._selected_zoom_annotation = None
            self._refresh_current_zoom_page()

        def undo_delete() -> None:
            state["old"] = create_freetext_annot(self._pdf_path, state["old"])
            self._selected_zoom_annotation = state["old"]
            self._refresh_current_zoom_page(open_drawer=True)

        do_delete()
        self._undo_manager.add_action(UndoAction(
            description="Delete FreeText",
            undo_func=undo_delete,
            redo_func=do_delete,
        ))

    def _on_zoom_annotation_geometry_changed(self, annotation: object, rect: object, mode: str) -> None:
        if not isinstance(annotation, FreeTextAnnotData):
            return
        if not isinstance(rect, tuple) or len(rect) != 4:
            return
        old_annotation = self._find_zoom_annotation(annotation.xref) or annotation
        new_annotation = FreeTextAnnotData(
            page_num=old_annotation.page_num,
            xref=old_annotation.xref,
            rect=(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
            content=old_annotation.content,
            fontsize=old_annotation.fontsize,
            text_color=old_annotation.text_color,
            fill_color=old_annotation.fill_color,
            border_color=old_annotation.border_color,
            border_width=old_annotation.border_width,
            opacity=old_annotation.opacity,
            fontname=old_annotation.fontname,
            annotation_id=old_annotation.annotation_id,
            subject=old_annotation.subject,
        )
        if old_annotation.rect == new_annotation.rect:
            return
        description = "Move FreeText" if mode == "move" else "Resize FreeText"
        self._run_zoom_annotation_replace(old_annotation, new_annotation, description)

    def _update_button_states(self) -> None:
        has_selection = len(self._selected_thumbnails) > 0
        zoom_active = bool(
            self._zoom_view
            and self._zoom_view.isVisible()
            and self._zoom_page_num is not None
        )
        can_edit_pages = has_selection or zoom_active
        self._delete_btn.setEnabled(can_edit_pages)
        self._rotate_btn.setEnabled(can_edit_pages)
        self._undo_btn.setEnabled(self._undo_manager.can_undo())
        self._redo_btn.setEnabled(self._undo_manager.can_redo())

    def _debug_undo_state(self, reason: str) -> None:
        log_undo_state(
            logger=logger,
            context_name="PageEditWindow",
            reason=reason,
            undo_button=self._undo_btn,
            redo_button=self._redo_btn,
            undo_manager=self._undo_manager,
        )

    def _on_undo_manager_changed(self, reason: str) -> None:
        self._update_button_states()
        self._debug_undo_state(reason)

    def _reset_thumbnail_render_queue(self) -> None:
        self._thumb_render_timer.stop()
        self._scroll_debounce_timer.stop()
        self._thumb_render_queue.clear()
        self._thumb_render_queue_set.clear()

    def _schedule_thumbnail_render(self) -> None:
        if self._thumb_render_queue and not self._thumb_render_timer.isActive():
            self._thumb_render_timer.start(0)

    def _enqueue_thumbnail_render(self, page_num: int, *, priority: bool = False) -> None:
        if page_num < 0 or page_num >= len(self._thumbnails):
            return
        thumb = self._thumbnails[page_num]
        if thumb._explicitly_hidden or thumb.thumbnail_loaded:
            return
        if page_num in self._thumb_render_queue_set:
            return
        if priority:
            self._thumb_render_queue.appendleft(page_num)
        else:
            self._thumb_render_queue.append(page_num)
        self._thumb_render_queue_set.add(page_num)

    def _visible_rect_in_container(self) -> QRect:
        if not self._grid_scroll:
            return QRect()
        viewport = self._grid_scroll.viewport()
        top_left = self._container.mapFrom(viewport, QPoint(0, 0))
        bottom_right = self._container.mapFrom(
            viewport,
            QPoint(max(0, viewport.width() - 1), max(0, viewport.height() - 1)),
        )
        return QRect(top_left, bottom_right).normalized()

    def _enqueue_visible_thumbnail_renders(self) -> None:
        if not self._thumbnails or not self._grid_scroll:
            return
        visible_rect = self._visible_rect_in_container()
        visible_pages = []
        for page_num, thumb in enumerate(self._thumbnails):
            if thumb._explicitly_hidden or not thumb.isVisible():
                continue
            if thumb.geometry().intersects(visible_rect):
                if not thumb.thumbnail_loaded:
                    visible_pages.append(page_num)
        if not visible_pages:
            self._schedule_thumbnail_render()
            return
        visible_set = set(visible_pages)
        # キュー再構築: 表示中ページを先頭、残りをその後ろ
        remaining = deque()
        for pn in self._thumb_render_queue:
            if pn not in visible_set:
                remaining.append(pn)
        new_queue = deque(visible_pages)
        new_queue.extend(remaining)
        self._thumb_render_queue = new_queue
        self._thumb_render_queue_set = set(new_queue)
        self._schedule_thumbnail_render()

    def _enqueue_all_thumbnail_renders(self) -> None:
        for page_num, thumb in enumerate(self._thumbnails):
            if thumb._explicitly_hidden:
                continue
            self._enqueue_thumbnail_render(page_num)
        # 表示ページの優先化後にスケジュール開始（次のイベントループで）
        QTimer.singleShot(0, self._enqueue_visible_thumbnail_renders)

    def _request_thumbnail_refresh(self, page_num: int) -> None:
        if page_num < 0 or page_num >= len(self._thumbnails):
            return
        thumb = self._thumbnails[page_num]
        thumb.invalidate_thumbnail()
        self._enqueue_thumbnail_render(page_num, priority=True)
        self._schedule_thumbnail_render()

    def _process_thumbnail_render_queue(self) -> None:
        batch: list[int] = []
        while self._thumb_render_queue and len(batch) < 5:
            page_num = self._thumb_render_queue.popleft()
            self._thumb_render_queue_set.discard(page_num)
            if page_num < 0 or page_num >= len(self._thumbnails):
                continue
            thumb = self._thumbnails[page_num]
            if thumb._explicitly_hidden or thumb.thumbnail_loaded:
                continue
            batch.append(page_num)
        if batch:
            pixmaps = render_page_thumbnails_batch(self._pdf_path, batch, self._thumb_size)
            for pn in batch:
                if pn < len(self._thumbnails):
                    self._thumbnails[pn].set_pixmap_direct(pixmaps.get(pn, QPixmap()))
        self._schedule_thumbnail_render()

    def _on_grid_viewport_changed(self, _value: int) -> None:
        self._scroll_debounce_timer.start()  # デバウンス（スクロール停止150ms後に優先化）

    def _load_pages(self) -> None:
        self._reset_thumbnail_render_queue()
        # 既存のサムネイルをグリッドから先に取り除く
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            # setParent(None)は呼ばない（deleteLater()で処理される）

        for thumb in self._thumbnails:
            thumb.deleteLater()
        self._thumbnails.clear()
        self._selected_thumbnails.clear()
        self._zoom_text_cache.clear()
        self._zoom_annotations = []

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
            thumb = PageThumbnail(self._pdf_path, i, thumb_size=self._thumb_size)
            thumb.clicked.connect(self._on_thumbnail_clicked)
            self._thumbnails.append(thumb)

        self._refresh_grid()
        self._enqueue_all_thumbnail_renders()
        if self._zoom_view and self._zoom_view.isVisible():
            self._render_zoom_page()

    def _on_external_pdf_rotation(self) -> None:
        """外部（MainWindow等）で回転されたPDFを即時反映する。"""
        if not os.path.exists(self._pdf_path):
            return

        page_count = get_page_count(self._pdf_path)
        if page_count != len(self._thumbnails):
            self._load_pages()
            return

        self._zoom_text_cache.clear()
        self._zoom_annotations = []
        self._reset_thumbnail_render_queue()
        for thumb in self._thumbnails:
            thumb.invalidate_thumbnail()
        self._enqueue_all_thumbnail_renders()

        if self._zoom_view and self._zoom_view.isVisible():
            self._render_zoom_page()

    def _grid_available_width(self) -> int:
        """Width source for column calculation (always consistent)."""
        return viewport_width_or_fallback(self._grid_scroll, self.width())

    def _refresh_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            # 削除予定のウィジェットには触らない
            widget = item.widget()
            if widget and widget in self._thumbnails:
                widget.setParent(None)

        available_width = self._grid_available_width()
        spacing = self._grid_layout.horizontalSpacing()
        if spacing < 0:
            spacing = self._grid_layout.spacing()
        spacing = int(spacing)
        m = self._grid_layout.contentsMargins()
        usable = max(1, int(available_width) - int(m.left() + m.right()))

        w = int(self._thumb_size)
        cols = max(1, int((usable + spacing) // (w + spacing)))

        visible_thumbs = [t for t in self._thumbnails if not t._explicitly_hidden]
        for i, thumb in enumerate(visible_thumbs):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(thumb, row, col)
            thumb.setVisible(True)
        self._enqueue_visible_thumbnail_renders()

    def _remove_page_thumbnails(self, page_indices: list[int]) -> None:
        """指定されたページのサムネイルを削除（差分更新）"""
        self._reset_thumbnail_render_queue()
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
        self._zoom_annotations = []

        self._refresh_grid()
        self._enqueue_all_thumbnail_renders()

    def _clear_selection(self) -> None:
        clear_selection(self._selected_thumbnails)
        self._update_button_states()

    def _set_thumbnail_size(self, size: int) -> None:
        size = max(self.PREVIEW_THUMB_MIN, min(self.PREVIEW_THUMB_MAX, int(size)))
        if size == self._thumb_size:
            return
        self._reset_thumbnail_render_queue()
        self._thumb_size = size
        for thumb in self._thumbnails:
            thumb.set_thumbnail_size(self._thumb_size)
        self._refresh_grid()
        self._enqueue_all_thumbnail_renders()

    def eventFilter(self, obj, event) -> bool:
        grid_scroll = getattr(self, "_grid_scroll", None)
        if grid_scroll and obj is grid_scroll.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if self._zoom_view and self._zoom_view.isVisible():
                    return False
                delta = event.angleDelta().y()
                if delta != 0:
                    step = self.PREVIEW_THUMB_STEP if delta > 0 else -self.PREVIEW_THUMB_STEP
                    self._set_thumbnail_size(self._thumb_size + step)
                event.accept()
                return True
        return super().eventFilter(obj, event)

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
        self._commit_inline_annotation_editor()
        self._zoom_page_num = page_num
        self._selected_zoom_annotation = None
        self._set_zoom_annotation_create_mode(False)
        self._set_zoom_percent(100)
        if self._grid_scroll:
            self._grid_scroll.hide()
        if self._zoom_view:
            self._zoom_view.show()
        self._update_button_states()

    def _exit_zoom_view(self) -> None:
        self._commit_inline_annotation_editor()
        last_page = self._zoom_page_num
        self._set_zoom_annotation_create_mode(False)
        self._set_selected_zoom_annotation(None)
        if self._zoom_view:
            self._zoom_view.hide()
        if self._grid_scroll:
            self._grid_scroll.show()
        # 最後に表示していたページを選択状態にする
        if last_page is not None and 0 <= last_page < len(self._thumbnails):
            self._clear_selection()
            thumb = self._thumbnails[last_page]
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)
            # サムネイルが見える位置にスクロール
            if self._grid_scroll:
                self._grid_scroll.ensureWidgetVisible(thumb)
        self._update_button_states()

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
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
        self._zoom_page_num -= 1
        self._render_zoom_page()

    def _on_zoom_next_page(self) -> None:
        if self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if self._zoom_page_num >= page_count - 1:
            self._update_zoom_nav_buttons(page_count)
            return
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
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
        if not self._zoom_annotation_text_commit_in_progress:
            self._commit_inline_annotation_editor()
        if self._zoom_page_num is None or not self._zoom_label:
            return
        page_count = get_page_count(self._pdf_path)
        self._update_zoom_nav_buttons(page_count)
        if self._zoom_page_label:
            self._zoom_page_label.setText(f"{self._zoom_page_num + 1} / {page_count}")
        if self._zoom_page_num >= page_count:
            self._exit_zoom_view()
            return
        dpr = self._zoom_label.devicePixelRatioF()
        pixmap = get_page_pixmap(
            self._pdf_path,
            self._zoom_page_num,
            self._zoom_factor * dpr,
            annots=False,
        )
        pixmap.setDevicePixelRatio(dpr)
        words = []
        links = []
        if self._zoom_page_num in self._zoom_text_cache:
            words, links = self._zoom_text_cache[self._zoom_page_num]
        else:
            words = get_page_words(self._pdf_path, self._zoom_page_num)
            links = get_page_links(self._pdf_path, self._zoom_page_num)
            self._zoom_text_cache[self._zoom_page_num] = (words, links)
        self._zoom_annotations = list_freetext_annots(self._pdf_path, self._zoom_page_num)
        selected_xref = self._selected_zoom_annotation.xref if self._selected_zoom_annotation else None
        current_selection = self._find_zoom_annotation(selected_xref)
        self._zoom_label.set_page(
            pixmap,
            words,
            links,
            self._zoom_annotations,
            self._zoom_factor,
            current_selection.xref if current_selection else None,
        )
        self._set_selected_zoom_annotation(current_selection)

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
        self._commit_inline_annotation_editor()
        self._undo_manager.undo()
        self._load_pages()
        self._update_button_states()

    def _on_redo(self) -> None:
        self._commit_inline_annotation_editor()
        self._undo_manager.redo()
        self._load_pages()
        self._update_button_states()

    def _on_delete(self) -> None:
        # ズームビュー表示中の場合
        if self._zoom_view and self._zoom_view.isVisible():
            if self._selected_zoom_annotation is not None:
                self._delete_selected_zoom_annotation()
                return
            self._delete_zoom_page()
            return

        if not self._selected_thumbnails:
            return

        import tempfile

        indices = sorted([t.page_num for t in self._selected_thumbnails], reverse=True)
        pdf_path = self._pdf_path

        # 全ページ削除かチェック
        page_count = get_page_count(pdf_path)
        if len(indices) >= page_count:
            # 全ページ削除 → ファイル削除＋UNDO対応
            backup_fd, backup_path = tempfile.mkstemp(suffix=".pdf")
            os.close(backup_fd)
            shutil.copy2(pdf_path, backup_path)
            self._delete_all_pages(backup_path)
            return

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
        # ズームビュー表示中の場合
        if self._zoom_view and self._zoom_view.isVisible():
            self._rotate_zoom_page()
            return

        if not self._selected_thumbnails:
            return

        indices = [t.page_num for t in self._selected_thumbnails]
        pdf_path = self._pdf_path
        selected_thumbs = list(self._selected_thumbnails)

        def do_rotate():
            rotate_pages(pdf_path, indices, 90)
            for thumb in selected_thumbs:
                self._request_thumbnail_refresh(thumb.page_num)

        def undo_rotate():
            rotate_pages(pdf_path, indices, 270)
            for thumb in selected_thumbs:
                self._request_thumbnail_refresh(thumb.page_num)

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

    def _delete_zoom_page(self) -> None:
        """ズームビュー表示中のページを削除"""
        import tempfile

        if self._zoom_page_num is None:
            return

        pdf_path = self._pdf_path
        page_count = get_page_count(pdf_path)
        current_page = self._zoom_page_num

        # 全ページ削除かチェック
        if page_count <= 1:
            backup_fd, backup_path = tempfile.mkstemp(suffix=".pdf")
            os.close(backup_fd)
            shutil.copy2(pdf_path, backup_path)
            self._delete_all_pages_from_zoom(backup_path)
            return

        # 削除後に表示するページを計算
        if current_page >= page_count - 1:
            # 最後のページを削除 → 一つ前のページを表示
            next_page = current_page - 1
        else:
            # それ以外 → 同じインデックス（次のページが繰り上がる）
            next_page = current_page

        # バックアップ作成
        backup_fd, backup_path = tempfile.mkstemp(suffix=".pdf")
        os.close(backup_fd)
        extract_pages(pdf_path, backup_path, [current_page])

        deleted_page = current_page

        def do_delete():
            remove_pages(pdf_path, [deleted_page])
            self._remove_page_thumbnails([deleted_page])
            self._zoom_text_cache.clear()
            if self._zoom_view and self._zoom_view.isVisible():
                new_page_count = get_page_count(pdf_path)
                if new_page_count > 0:
                    self._zoom_page_num = min(next_page, new_page_count - 1)
                    self._render_zoom_page()

        def undo_delete():
            insert_pages(pdf_path, backup_path, [deleted_page])
            self._load_pages()
            self._zoom_text_cache.clear()
            if self._zoom_view and self._zoom_view.isVisible():
                self._zoom_page_num = deleted_page
                self._render_zoom_page()

        do_delete()

        self._undo_manager.add_action(UndoAction(
            description="Delete page from zoom view",
            undo_func=undo_delete,
            redo_func=do_delete
        ))

    def _rotate_zoom_page(self) -> None:
        """ズームビュー表示中のページを回転"""
        if self._zoom_page_num is None:
            return

        pdf_path = self._pdf_path
        page_num = self._zoom_page_num

        def do_rotate():
            rotate_pages(pdf_path, [page_num], 90)
            # ズームテキストキャッシュをクリアして再描画
            self._zoom_text_cache.pop(page_num, None)
            if self._zoom_view and self._zoom_view.isVisible():
                self._render_zoom_page()
            # サムネイルも更新
            if page_num < len(self._thumbnails):
                self._request_thumbnail_refresh(page_num)

        def undo_rotate():
            rotate_pages(pdf_path, [page_num], 270)
            self._zoom_text_cache.pop(page_num, None)
            if self._zoom_view and self._zoom_view.isVisible():
                self._render_zoom_page()
            if page_num < len(self._thumbnails):
                self._request_thumbnail_refresh(page_num)

        do_rotate()

        self._undo_manager.add_action(UndoAction(
            description="Rotate page from zoom view",
            undo_func=undo_rotate,
            redo_func=do_rotate
        ))

    def _delete_all_pages(self, backup_path: str) -> None:
        """全ページ削除（ファイルをゴミ箱へ移動し、UNDO対応）"""
        from send2trash import send2trash
        from src.views.main_window import MainWindow

        pdf_path = self._pdf_path

        def _get_main_window():
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, MainWindow):
                    return widget
            return None

        def _close_edit_windows() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, PageEditWindow) and widget._pdf_path == pdf_path:
                    widget.close()

        def do_delete():
            main_window = _get_main_window()
            if main_window:
                main_window._register_internal_remove([pdf_path])
                main_window._remove_card(pdf_path)
                main_window._refresh_grid()
            # ファイルをゴミ箱へ
            if os.path.exists(pdf_path):
                send2trash(pdf_path)
            # このPDFのPageEditWindowをすべて閉じる
            _close_edit_windows()

        def undo_delete():
            main_window = _get_main_window()
            if main_window:
                main_window._register_internal_add([pdf_path])
            # バックアップからファイル復元
            shutil.copy2(backup_path, pdf_path)
            if main_window:
                restored_card = main_window._get_card_by_path(pdf_path)
                if restored_card is None:
                    restored_card = main_window._add_card(pdf_path)
                    main_window._refresh_grid()
                    main_window._internal_adds.discard(main_window._normalize_path(pdf_path))
                # 復元後は編集画面を再度開く
                main_window._on_card_double_clicked(restored_card)

        do_delete()

        self._undo_manager.add_action(UndoAction(
            description="Delete all pages (file to trash)",
            undo_func=undo_delete,
            redo_func=do_delete
        ))

    def _delete_all_pages_from_zoom(self, backup_path: str) -> None:
        """ズームビューから最後の1ページ削除時の処理"""
        self._delete_all_pages(backup_path)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_grid()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Run one post-show reflow so initial column count uses stable viewport width.
        if not self._did_initial_grid_layout:
            self._did_initial_grid_layout = True
            QTimer.singleShot(0, self._refresh_grid)

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

        self._reset_thumbnail_render_queue()
        self._undo_manager.remove_listener(self._on_undo_manager_changed)
        
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.unlock_card(self._pdf_path)
                break
        super().closeEvent(event)
