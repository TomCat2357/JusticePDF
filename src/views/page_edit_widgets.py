"""ページ編集画面のウィジェット群(サムネイル/インライン編集/拡大キャンバス)。

page_edit_window.py から機械的に移動したもの。描画・マウス処理は
テスト未カバーのため意図的に再構成していない。
"""
import math
import logging
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QFrame,
    QApplication,
    QPlainTextEdit,
)
from PyQt6.QtCore import (
    Qt,
    QMimeData,
    pyqtSignal,
    QPoint,
    QPointF,
    QRect,
    QRectF,
)
from PyQt6.QtGui import (
    QFont,
    QKeySequence,
    QDrag,
    QPainter,
    QColor,
    QPen,
    QPixmap,
    QBrush,
    QPolygonF,
    QGuiApplication,
    QTextDocument,
    QTextOption,
    QTextCursor,
    QTextBlockFormat,
    QAbstractTextDocumentLayout,
    QPalette,
)

from src.utils.pdf_utils import (
    get_page_thumbnail,
    FreeTextAnnotData,
    ShapeType,
    ShapeAnnotData,
    AnyAnnotData,
    MarkupType,
    TextMarkupAnnotData,
    NoteAnnotData,
)
from src.utils.constants import (
    FREETEXT_LINE_HEIGHT,
    FREETEXT_TEXT_INSET_PT,
    PAGETHUMBNAIL_MIME_TYPE,
    freetext_canvas_font_families,
)


from src.views.view_helpers import apply_drag_pixmap

logger = logging.getLogger(__name__)


def _apply_block_line_height(document: "QTextDocument") -> None:
    """文書の全ブロックに共有の行間係数(FREETEXT_LINE_HEIGHT)を適用する。

    キャンバスのプレビュー描画とインラインエディタ、保存 PDF の /DS line-height を
    そろえるための共通処理。"""
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    block_format = QTextBlockFormat()
    block_format.setLineHeight(
        FREETEXT_LINE_HEIGHT * 100.0,
        QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
    )
    cursor.mergeBlockFormat(block_format)


def _build_freetext_document(text: str, font: "QFont", text_width: float) -> "QTextDocument":
    """テキストボックス本文を描画するための QTextDocument を組み立てる。

    インラインエディタ(QPlainTextEdit)と同じ折り返し・行間で描画することで、
    編集中と非編集時の見た目を一致させる。"""
    document = QTextDocument()
    document.setDocumentMargin(0)
    document.setDefaultFont(font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    option.setAlignment(Qt.AlignmentFlag.AlignLeft)
    document.setDefaultTextOption(option)
    document.setPlainText(text)
    document.setTextWidth(max(0.0, text_width))
    _apply_block_line_height(document)
    return document


def _freetext_pixel_size(fontsize: float, zoom: float) -> int:
    """FreeText の画面描画ピクセルサイズ。保存 PDF と比例させるため下限は 1px のみ。"""
    return max(1, round(fontsize * zoom))


def _pixel_size_to_pointf(pixel_size: int) -> float:
    # Convert pixel size to a point size so QFont.pointSize() stays positive.
    # setPixelSize() invalidates pointSize (-1), which triggers a Qt warning
    # if anything downstream re-applies setPointSize on the font.
    dpi = 96.0
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        dpi = screen.logicalDotsPerInch() or 96.0
    return max(1.0, pixel_size * 72.0 / dpi)



class PageThumbnail(QFrame):
    """Widget representing a single PDF page."""

    clicked = pyqtSignal(object)
    THUMBNAIL_SIZE = 120
    CARD_PADDING = 30  # total horizontal/vertical padding around the thumbnail

    def __init__(self, pdf_path: str, page_num: int, display_num: int = None, parent=None, *, thumb_size: int | None = None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_num = page_num
        self._display_num = display_num if display_num is not None else page_num
        self._is_selected = False
        self._is_drop_target = False
        self._is_search_hit = False
        self._explicitly_hidden = False
        self._drag_start_pos = None
        self._thumb_size = int(thumb_size) if thumb_size is not None else self.THUMBNAIL_SIZE
        self._thumbnail_loaded = False
        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the thumbnail UI in the same style as PDFCard."""
        self.setFixedSize(self._thumb_size + self.CARD_PADDING, self._thumb_size + self.CARD_PADDING)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self._thumbnail_container = QWidget()
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)

        self._image_label = QLabel(self._thumbnail_container)
        self._image_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ccc;")
        self._image_label.move(0, 0)

        # Page-number badge in the top-right, matching PDFCard's page-count badge.
        self._number_label = QLabel(str(self._display_num + 1), self._thumbnail_container)
        self._number_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.7); color: white; padding: 2px 5px; border-radius: 3px; font-size: 11px;"
        )
        self._number_label.adjustSize()
        self._number_label.move(self._thumb_size - self._number_label.width() - 3, 3)
        self._number_label.raise_()

        layout.addWidget(self._thumbnail_container, alignment=Qt.AlignmentFlag.AlignCenter)

        self.invalidate_thumbnail()
        self._update_style()

    def _reposition_number_badge(self) -> None:
        self._number_label.adjustSize()
        self._number_label.move(self._thumb_size - self._number_label.width() - 3, 3)

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
        """Update style based on selection and drop-target state."""
        if self._is_drop_target:
            state = "droptarget"
        elif self._is_selected:
            state = "selected"
        elif self._is_search_hit:
            state = "search_hit"
        else:
            state = "normal"
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def is_search_hit(self) -> bool:
        return self._is_search_hit

    def set_search_hit(self, on: bool) -> None:
        on = bool(on)
        if self._is_search_hit == on:
            return
        self._is_search_hit = on
        self._update_style()

    @property
    def is_drop_target(self) -> bool:
        return self._is_drop_target

    def set_drop_target(self, on: bool) -> None:
        on = bool(on)
        if self._is_drop_target == on:
            return
        self._is_drop_target = on
        self._update_style()

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
        size = int(size)
        if size == self._thumb_size:
            return
        self._thumb_size = size
        self.setFixedSize(self._thumb_size + self.CARD_PADDING, self._thumb_size + self.CARD_PADDING)
        self._thumbnail_container.setFixedSize(self._thumb_size, self._thumb_size)
        self._image_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._reposition_number_badge()
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

        apply_drag_pixmap(drag, self, max_size=80, count=len(page_nums_list),
                          badge_size=20, badge_font_size=9)

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
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._emit_commit()
            event.accept()
            return
        super().keyPressEvent(event)


class NoteContentEdit(QPlainTextEdit):
    """付箋本文の編集欄。フォーカスを失ったとき、または Ctrl+Enter で確定する。"""

    commit_requested = pyqtSignal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.commit_requested.emit()

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.commit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ZoomPageWidget(QWidget):
    wheel_zoom = pyqtSignal(int)
    link_clicked = pyqtSignal(dict)
    annotation_selected = pyqtSignal(object)
    annotation_geometry_changed = pyqtSignal(object, object, str)
    # LINE 端点ドラッグ専用：(annotation, new_rect_tuple, new_vertices, mode)
    shape_geometry_changed_with_vertices = pyqtSignal(object, object, object, str)
    # 校正コールアウトのさきっぽ（挿入位置）ドラッグ専用：(annotation, new_target_tuple)
    callout_target_changed = pyqtSignal(object, object)
    annotation_create_requested = pyqtSignal(object)
    shape_create_requested = pyqtSignal(object, object, object)  # (ShapeType, start_pt_tuple, end_pt_tuple)
    note_create_requested = pyqtSignal(object)  # placement point tuple (page coords)
    callout_create_requested = pyqtSignal(object)  # insertion target point tuple (page coords)
    annotation_edit_requested = pyqtSignal(object)
    annotation_text_committed = pyqtSignal(object, str)
    annotation_text_edit_cancelled = pyqtSignal()
    annotation_delete_requested = pyqtSignal()
    annotation_copy_requested = pyqtSignal(object)
    annotation_paste_requested = pyqtSignal()
    annotation_paste_placement_requested = pyqtSignal(object)
    # Ctrl+ドラッグで複製: (annotation, new_rect_tuple, new_vertices_or_None)
    annotation_duplicate_requested = pyqtSignal(object, object, object)
    # ビューのスクロール要求 (dx, dy: ピクセル)。中ボタンドラッグ / Ctrl+矢印で発火。
    scroll_requested = pyqtSignal(int, int)
    # 右ドラッグで指定した範囲（ページ座標 QRectF）への拡大要求。
    zoom_region_requested = pyqtSignal(object)

    HANDLE_SIZE = 10
    # 付箋アイコンの画面上の固定サイズ（px）。ズームに依らず一定。
    NOTE_ICON_PX = 22
    # 矢印キーによる注釈移動量（PDF ポイント）。細かい / 通常 / 粗い移動。
    # 押下回数によらず一定ステップ（加速なし）。
    # Alt/Shift=細かく、無修飾=通常、Ctrl=粗く。
    ANNOTATION_MOVE_STEP = 10.0
    ANNOTATION_MOVE_STEP_FINE = 1.0
    ANNOTATION_MOVE_STEP_COARSE = 50.0
    # 矢印キーで注釈未選択時にビューをスクロールする 1 ステップ（ピクセル）。
    # 無修飾=通常スクロール、Ctrl=高速スクロール。
    ZOOM_SCROLL_STEP = 80
    ZOOM_SCROLL_STEP_FAST = 320
    # 矢印キー -> (dx, dy) の単位ベクトル（ページ座標は下方向が +y）。
    _ARROW_DELTAS = {
        Qt.Key.Key_Left: (-1.0, 0.0),
        Qt.Key.Key_Right: (1.0, 0.0),
        Qt.Key.Key_Up: (0.0, -1.0),
        Qt.Key.Key_Down: (0.0, 1.0),
    }

    @staticmethod
    def _min_dims_for_annotation(annot) -> tuple[float, float, float]:
        # (min_w, min_h, min_span). min_span enforces max(w, h) for lines.
        if isinstance(annot, ShapeAnnotData) and annot.shape_type == ShapeType.LINE:
            return (0.0, 0.0, 1.0)
        return (1.0, 1.0, 0.0)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._zoom_factor = 1.0
        self._words: list[tuple] = []
        self._word_rects: list[QRectF] = []
        self._links: list[dict] = []
        self._link_rects: list[QRectF | None] = []
        self._selected_word_indices: list[int] = []
        # Character-level text-flow selection (anchor -> head in reading order).
        self._chars: list[dict] = []
        self._char_rects: list[QRectF] = []
        self._char_line_ids: list[int] = []
        # Per line_id: (first_char_idx, last_char_idx, x0, y0, x1, y1) in pixmap coords.
        self._line_bounds: list[tuple[int, int, float, float, float, float]] = []
        self._sel_anchor_char: int | None = None
        self._sel_head_char: int | None = None
        self._selected_char_indices: list[int] = []
        self._selection_origin: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._selection_active = False
        self._pressed_link: dict | None = None
        self._annotations: list[FreeTextAnnotData] = []
        self._annotation_rects: list[QRectF] = []
        self._selected_annotation_xref: int | None = None
        self._hover_annotation_xref: int | None = None
        self._annotation_create_mode = False
        self._annotation_create_shape_type: ShapeType | None = None
        self._annotation_create_origin_page: QPointF | None = None
        self._annotation_create_current_page: QPointF | None = None
        self._annotation_create_preview_rect: QRectF | None = None
        self._drag_annotation_xref: int | None = None
        self._drag_mode: str | None = None
        self._drag_origin_page: QPointF | None = None
        self._drag_base_rect: QRectF | None = None
        self._pending_annotation_rect: QRectF | None = None
        self._drag_base_vertices: tuple[tuple[float, float], ...] | None = None
        self._pending_line_vertices: tuple[tuple[float, float], ...] | None = None
        # 校正コールアウトのさきっぽドラッグ中の暫定先端位置（ページ座標）
        self._pending_callout_target: tuple[float, float] | None = None
        self._drag_moved = False
        self._drag_copy_mode = False
        self._inline_editor: AnnotationTextEdit | None = None
        self._editing_annotation_xref: int | None = None
        self._editing_annotation_original_text = ""
        self._annotation_paste_available = False
        self._paste_annotation: FreeTextAnnotData | None = None
        self._paste_preview_rect: QRectF | None = None
        self._paste_drag_active = False
        self._search_hits_pdf: list[tuple[float, float, float, float]] = []
        self._search_hit_rects: list[QRectF] = []
        # 中ボタンドラッグによるパン（つかんで移動）状態。
        self._pan_active = False
        self._pan_last_global: QPoint | None = None
        # 右ドラッグによる範囲指定拡大（マーキーズーム）状態。
        self._marquee_active = False
        self._marquee_origin: QPoint | None = None
        self._marquee_rect: QRect | None = None
        # 付箋（ノート）配置モードとホバーポップアップ状態。
        self._note_create_mode = False
        self._hover_note_xref: int | None = None
        self._note_popup: QFrame | None = None
        # 校正コールアウト配置モード（クリックで挿入位置を指定）。
        self._callout_create_mode = False
        # 閲覧専用モード（見開き表示）。文字選択・注釈編集・リンク操作を抑止する。
        self._view_only = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        # 右ドラッグを範囲指定に使うため、既定のコンテキストメニューを抑止する。
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

    def sizeHint(self):
        if self._pixmap and not self._pixmap.isNull():
            return self._pixmap.deviceIndependentSize().toSize()
        return super().sizeHint()

    def page_size_points(self) -> tuple[float, float]:
        if not self._pixmap or self._pixmap.isNull() or self._zoom_factor <= 0:
            return (0.0, 0.0)
        logical_size = self._pixmap.deviceIndependentSize()
        return (logical_size.width() / self._zoom_factor, logical_size.height() / self._zoom_factor)

    def set_view_only(self, enabled: bool) -> None:
        """閲覧専用モードの切り替え。ON で選択・注釈編集・リンク操作を抑止する。

        ホイールズーム・中ボタンのパン・右ドラッグの範囲指定ズームは
        閲覧操作として有効なまま残す。
        """
        self._view_only = bool(enabled)
        if self._view_only:
            self.cancel_annotation_text_edit()
            self.cancel_annotation_paste_mode()
            self._pan_active = False
            self._marquee_active = False
            self._selection_active = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _clear_annotation_create_drag(self) -> None:
        self._annotation_create_origin_page = None
        self._annotation_create_current_page = None
        self._annotation_create_preview_rect = None

    def set_annotation_create_mode(self, enabled: bool, shape_type: ShapeType | None = None) -> None:
        self._annotation_create_mode = bool(enabled)
        self._annotation_create_shape_type = shape_type if enabled else None
        self._clear_annotation_create_drag()
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def set_note_create_mode(self, enabled: bool) -> None:
        self._note_create_mode = bool(enabled)
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def set_callout_create_mode(self, enabled: bool) -> None:
        self._callout_create_mode = bool(enabled)
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def _note_widget_rect(self, note: NoteAnnotData) -> QRectF:
        """Fixed-pixel icon rect (does not scale with zoom)."""
        top_left = self._page_point_to_widget_point(QPointF(note.point[0], note.point[1]))
        size = float(self.NOTE_ICON_PX)
        return QRectF(top_left.x(), top_left.y(), size, size)

    def set_selected_annotation_xref(self, xref: int | None) -> None:
        self._selected_annotation_xref = xref
        self.update()

    def has_active_text_editor(self) -> bool:
        return self._inline_editor is not None and self._inline_editor.isVisible()

    def set_annotation_paste_available(self, available: bool) -> None:
        self._annotation_paste_available = bool(available)

    def has_annotation_paste_mode(self) -> bool:
        return self._paste_annotation is not None

    def begin_annotation_paste_mode(self, annotation: AnyAnnotData) -> None:
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
        pixel_size = _freetext_pixel_size(annotation.fontsize, self._zoom_factor)
        editor.setStyleSheet(self._inline_editor_stylesheet(annotation, pixel_size))
        editor.setFrameStyle(QFrame.Shape.NoFrame)
        editor.setContentsMargins(0, 0, 0, 0)
        effective_border_width = annotation.border_width if annotation.border_color is not None else 0.0
        # キャンバス描画(_paint_annotation)・保存 PDF(/RD)と同じ内側余白を四辺に適用。
        text_inset_px = max(0, round((FREETEXT_TEXT_INSET_PT + effective_border_width) * self._zoom_factor))
        editor.setViewportMargins(text_inset_px, text_inset_px, text_inset_px, text_inset_px)
        editor.document().setDocumentMargin(0)
        font = editor.font()
        font.setPointSizeF(_pixel_size_to_pointf(pixel_size))
        font.setFamilies(list(freetext_canvas_font_families(annotation.fontname)))
        editor.setFont(font)
        _apply_block_line_height(editor.document())
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

    def _inline_editor_stylesheet(self, annotation: FreeTextAnnotData, pixel_size: int) -> str:
        opacity = max(0.0, min(1.0, annotation.opacity))
        if annotation.fill_color is None:
            fill_css = "transparent"
        else:
            fill_css = (
                f"rgba({round(annotation.fill_color[0] * 255)}, "
                f"{round(annotation.fill_color[1] * 255)}, "
                f"{round(annotation.fill_color[2] * 255)}, "
                f"{round(opacity * 255)})"
            )
        text_css = (
            f"rgba({round(annotation.text_color[0] * 255)}, "
            f"{round(annotation.text_color[1] * 255)}, "
            f"{round(annotation.text_color[2] * 255)}, "
            f"{round(opacity * 255)})"
        )
        # スタイルシートがフォントを既定値にリセットしても、キャンバスと同じ
        # フォントで編集できるよう font-family も明示する(行間はブロック書式で適用)。
        family_css = ", ".join(f'"{fam}"' for fam in freetext_canvas_font_families(annotation.fontname))
        return (
            "QPlainTextEdit {"
            f"background-color: {fill_css};"
            f"color: {text_css};"
            f"font-family: {family_css};"
            f"font-size: {max(1, int(pixel_size))}px;"
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

    def set_page(self, pixmap, words, links, annotations, zoom_factor: float, selected_annotation_xref: int | None = None, chars=None) -> None:
        self._pixmap = pixmap
        self._zoom_factor = zoom_factor or 1.0
        self._words = words or []
        self._chars = chars or []
        self._links = links or []
        self._annotations = annotations or []

        scale = self._zoom_factor
        self._word_rects = []
        for word in self._words:
            if len(word) < 4:
                continue
            x0, y0, x1, y1 = word[0], word[1], word[2], word[3]
            self._word_rects.append(QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale))

        self._char_rects = []
        self._char_line_ids = []
        self._line_bounds = []
        line_acc: dict[int, list[float]] = {}
        for i, ch in enumerate(self._chars):
            x0, y0, x1, y1 = ch["bbox"]
            self._char_rects.append(
                QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)
            )
            line_id = ch["line_id"]
            self._char_line_ids.append(line_id)
            # Accumulate per-line extents in pixmap coords: [first, last, x0, y0, x1, y1].
            acc = line_acc.get(line_id)
            if acc is None:
                line_acc[line_id] = [i, i, x0 * scale, y0 * scale, x1 * scale, y1 * scale]
            else:
                acc[1] = i
                acc[2] = min(acc[2], x0 * scale)
                acc[3] = min(acc[3], y0 * scale)
                acc[4] = max(acc[4], x1 * scale)
                acc[5] = max(acc[5], y1 * scale)
        self._line_bounds = [
            (a[0], a[1], a[2], a[3], a[4], a[5])
            for _lid, a in sorted(line_acc.items())
        ]

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

        self._search_hit_rects = [
            QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)
            for (x0, y0, x1, y1) in self._search_hits_pdf
        ]

        self._selected_word_indices = []
        self._selected_char_indices = []
        self._sel_anchor_char = None
        self._sel_head_char = None
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
        self._drag_base_vertices = None
        self._pending_line_vertices = None
        self._pending_callout_target = None
        self._drag_moved = False
        self._clear_annotation_create_drag()
        self._paste_preview_rect = None
        self._paste_drag_active = False
        self._hover_note_xref = None
        self._hide_note_popup()
        if self._pixmap and not self._pixmap.isNull():
            self.setMinimumSize(self._pixmap.deviceIndependentSize().toSize())
        else:
            self.setMinimumSize(0, 0)
        self._layout_inline_editor()
        self.updateGeometry()
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def set_search_hit_rects(self, pdf_rects) -> None:
        """Provide PDF-coordinate rects for the current page's search hits.

        Rects are scaled by the current zoom factor for paintEvent.
        Pass an empty list to clear.
        """
        self._search_hits_pdf = [
            (float(r.x0), float(r.y0), float(r.x1), float(r.y1)) for r in pdf_rects
        ]
        scale = self._zoom_factor
        self._search_hit_rects = [
            QRectF(x0 * scale, y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)
            for (x0, y0, x1, y1) in self._search_hits_pdf
        ]
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

    def _page_point_to_widget_point(self, pt: QPointF) -> QPointF:
        offset = self._pixmap_offset()
        return QPointF(
            offset.x() + pt.x() * self._zoom_factor,
            offset.y() + pt.y() * self._zoom_factor,
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

    def widget_point_from_page_point(self, pt: QPointF) -> QPointF:
        """Map a page-space point to this widget's pixel coordinates."""
        return self._page_point_to_widget_point(pt)

    def _apply_shape_shift_constraint(
        self,
        shape_type: "ShapeType | None",
        origin: QPointF | None,
        end: QPointF,
    ) -> QPointF:
        # Word-style: Shift 押下中は線を 0/45/90/135° にスナップ、他図形は bbox を正方形化。
        if origin is None or shape_type is None:
            return end
        dx = end.x() - origin.x()
        dy = end.y() - origin.y()
        if shape_type == ShapeType.LINE:
            length = math.hypot(dx, dy)
            if length <= 0.0:
                return end
            angle = math.atan2(dy, dx)
            step = math.pi / 4.0
            snapped = round(angle / step) * step
            return QPointF(
                origin.x() + math.cos(snapped) * length,
                origin.y() + math.sin(snapped) * length,
            )
        if shape_type in (ShapeType.RECTANGLE, ShapeType.ELLIPSE, ShapeType.TRIANGLE):
            side = min(abs(dx), abs(dy))
            sx = 1.0 if dx >= 0 else -1.0
            sy = 1.0 if dy >= 0 else -1.0
            return QPointF(origin.x() + sx * side, origin.y() + sy * side)
        return end

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

    def _char_index_at(self, pos: QPoint, *, nearest: bool = False) -> int | None:
        """Map a widget point to a character index in reading order.

        With ``nearest=False`` only returns the char whose box contains the
        point (or ``None``). With ``nearest=True`` always resolves to the
        closest character on the nearest line (clamping to line end / page
        edge), so drag selection follows text flow even past line ends.
        """
        if not self._char_rects:
            return None
        pix_pos = self._point_in_pixmap(pos)
        if pix_pos is None:
            if not nearest or not self._pixmap or self._pixmap.isNull():
                return None
            offset = self._pixmap_offset()
            logical_size = self._pixmap.deviceIndependentSize()
            px = min(max(pos.x() - offset.x(), 0.0), logical_size.width())
            py = min(max(pos.y() - offset.y(), 0.0), logical_size.height())
        else:
            px, py = pix_pos.x(), pix_pos.y()

        if not nearest:
            for i, rect in enumerate(self._char_rects):
                if rect.contains(QPointF(px, py)):
                    return i
            return None

        if not self._line_bounds:
            return None
        # Pick the nearest line. Use the writing mode of the first line's first
        # char to decide which axis runs along the line.
        first_idx = self._line_bounds[0][0]
        wmode = self._chars[first_idx].get("wmode", 0) if first_idx < len(self._chars) else 0
        best_line = None
        best_dist = None
        for lb in self._line_bounds:
            _f, _l, lx0, ly0, lx1, ly1 = lb
            if wmode == 1:  # vertical: lines stack left/right, pick by x band
                if lx0 <= px <= lx1:
                    dist = 0.0
                else:
                    dist = lx0 - px if px < lx0 else px - lx1
            else:  # horizontal: lines stack top/bottom, pick by y band
                if ly0 <= py <= ly1:
                    dist = 0.0
                else:
                    dist = ly0 - py if py < ly0 else py - ly1
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_line = lb
        if best_line is None:
            return None
        f, l, lx0, ly0, lx1, ly1 = best_line
        # Within the chosen line pick the char whose center is closest along the
        # reading axis; clamp to the line's ends.
        best_idx = f
        best_cdist = None
        for i in range(f, l + 1):
            rect = self._char_rects[i]
            if wmode == 1:
                center = rect.center().y()
                cdist = abs(center - py)
            else:
                center = rect.center().x()
                cdist = abs(center - px)
            if best_cdist is None or cdist < best_cdist:
                best_cdist = cdist
                best_idx = i
        return best_idx

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

    def _paint_callout_leader(
        self, painter: QPainter, annot: FreeTextAnnotData, rect: QRectF
    ) -> None:
        """校正吹き出しの引き出し線（しっぽ）＋矢印を画面キャンバスに描く。

        保存 PDF 側 (pdf_utils._add_freetext_annot_to_page) は callout=点列 +
        line_end=OPEN_ARROW で引き出し線を描くが、編集キャンバスでは描かれて
        いなかった。ここで本文ボックスから挿入位置 (callout_target) へ向かう線と
        先端の矢印を描き、保存 PDF と見た目をそろえる。

        引き出し線はテキスト回転の影響を受けないウィジェット絶対座標で描く必要が
        あるため、_paint_annotation の回転変換より前に呼ぶこと。接続点 (box_attach)
        は渡された rect から再計算するので、ドラッグゴースト/貼り付けプレビューでも
        箱に追従する。
        """
        if not annot.callout_line or annot.callout_target is None:
            return
        # さきっぽドラッグ中はこの注釈だけ暫定先端位置で引き出し線を描く。
        target_pt = annot.callout_target
        if (
            self._drag_mode == "callout_tip"
            and annot.xref == self._drag_annotation_xref
            and self._pending_callout_target is not None
        ):
            target_pt = self._pending_callout_target
        target = self._page_point_to_widget_point(QPointF(target_pt[0], target_pt[1]))
        # _callout_box_attach と同じ規則: ターゲットがボックスより下なら下辺、
        # そうでなければ上辺の中央に接続する。
        if target.y() >= rect.bottom():
            attach = QPointF(rect.center().x(), rect.bottom())
        else:
            attach = QPointF(rect.center().x(), rect.top())
        line_color = self._annotation_color(annot.border_color, opacity=annot.opacity)
        if line_color is None:
            line_color = self._annotation_color(annot.text_color, opacity=annot.opacity)
        if line_color is None:
            return
        pen_width = max(1.0, float(annot.border_width) * self._zoom_factor)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(line_color)
        pen.setWidthF(pen_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(attach, target)
        self._draw_arrow_heads(painter, attach, target, pen_width, False, True)
        painter.restore()

    def _paint_annotation(self, painter: QPainter, annot: FreeTextAnnotData, rect: QRectF) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # 引き出し線（しっぽ）は回転変換より前にウィジェット絶対座標で描く。
        self._paint_callout_leader(painter, annot, rect)

        text_rot = annot.text_rotation % 360
        if text_rot:
            center = rect.center()
            painter.translate(center)
            painter.rotate(text_rot)
            # Use swapped dimensions for 90/270 so text wrapping matches native rendering
            if text_rot in (90, 270):
                w, h = rect.height(), rect.width()
            else:
                w, h = rect.width(), rect.height()
            paint_rect = QRectF(-w / 2, -h / 2, w, h)
        else:
            paint_rect = rect

        fill_color = self._annotation_color(annot.fill_color, opacity=annot.opacity)
        if fill_color is not None:
            painter.fillRect(paint_rect, fill_color)

        pen_width = max(0.0, float(annot.border_width) * self._zoom_factor)
        border_color = self._annotation_color(annot.border_color, opacity=annot.opacity)
        if border_color is not None and pen_width > 0:
            border_pen = QPen(border_color)
            border_pen.setWidthF(pen_width)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = pen_width / 2.0
            border_rect = paint_rect
            if paint_rect.width() > pen_width and paint_rect.height() > pen_width:
                border_rect = paint_rect.adjusted(inset, inset, -inset, -inset)
            painter.drawRect(border_rect)

        if annot.content:
            font = painter.font()
            pixel_size = _freetext_pixel_size(annot.fontsize, self._zoom_factor)
            font.setPointSizeF(_pixel_size_to_pointf(pixel_size))
            # フォントファミリ・内側余白・行間を保存時(Acrobat 表示)と共有して
            # 折り返し位置と位置をそろえる。本文は QPlainTextEdit と同じ
            # QTextDocument で描画し、編集中と非編集時の見た目も一致させる。
            font.setFamilies(list(freetext_canvas_font_families(annot.fontname)))
            text_color = self._annotation_color(annot.text_color, opacity=annot.opacity)
            effective_border_width = annot.border_width if annot.border_color is not None else 0.0
            text_inset = (FREETEXT_TEXT_INSET_PT + effective_border_width) * self._zoom_factor
            text_rect = paint_rect.adjusted(text_inset, text_inset, -text_inset, -text_inset)
            if text_rect.width() > 0 and text_rect.height() > 0:
                document = _build_freetext_document(annot.content, font, text_rect.width())
                painter.save()
                painter.translate(text_rect.topLeft())
                painter.setClipRect(QRectF(0, 0, text_rect.width(), text_rect.height()))
                ctx = QAbstractTextDocumentLayout.PaintContext()
                if text_color is not None:
                    ctx.palette.setColor(QPalette.ColorRole.Text, text_color)
                document.documentLayout().draw(painter, ctx)
                painter.restore()

        painter.restore()

    def _paint_markup_annotation(self, painter: QPainter, annot: TextMarkupAnnotData) -> None:
        """Render highlight / underline / strikeout from quads.

        ズームビューのページ画像は注釈なし (annots=False) で描画されるため、
        マークアップの見た目はここで描く。保存済み PDF を PyMuPDF が
        描画する際は実際のマークアップ注釈として再現される。
        """
        base = self._annotation_color(annot.color, opacity=1.0) or QColor(255, 255, 0)
        opacity = max(0.0, min(1.0, float(annot.opacity)))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if annot.markup_type == MarkupType.HIGHLIGHT:
            fill = QColor(base)
            fill.setAlphaF(opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            for quad in annot.quads:
                rect = self._page_rect_to_widget_rect(self._rect_tuple_to_qrectf(quad))
                painter.drawRect(rect)
        else:
            line_color = QColor(base)
            line_color.setAlphaF(opacity)
            for quad in annot.quads:
                rect = self._page_rect_to_widget_rect(self._rect_tuple_to_qrectf(quad))
                line_width = max(1.0, rect.height() * 0.06)
                pen = QPen(line_color)
                pen.setWidthF(line_width)
                painter.setPen(pen)
                if annot.markup_type == MarkupType.UNDERLINE:
                    y = rect.bottom() - line_width
                else:  # STRIKEOUT
                    y = rect.center().y()
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.restore()

    def selected_markup_quads(self) -> list[tuple[float, float, float, float]]:
        """Return PDF-space quads (x0, y0, x1, y1) for the current text selection.

        One quad per line-run: consecutive selected chars on the same line are
        merged into a single horizontal (or vertical) bar.
        """
        quads: list[tuple[float, float, float, float]] = []
        for run in self._selected_line_runs():
            x0 = y0 = x1 = y1 = None
            for idx in run:
                if idx >= len(self._chars):
                    continue
                bx0, by0, bx1, by1 = self._chars[idx]["bbox"]
                if x0 is None:
                    x0, y0, x1, y1 = bx0, by0, bx1, by1
                else:
                    x0 = min(x0, bx0)
                    y0 = min(y0, by0)
                    x1 = max(x1, bx1)
                    y1 = max(y1, by1)
            if x0 is not None:
                quads.append((float(x0), float(y0), float(x1), float(y1)))
        return quads

    def _paint_note_annotation(self, painter: QPainter, annot: NoteAnnotData) -> None:
        """Draw a fixed-size sticky-note icon at the note's anchor point."""
        rect = self._note_widget_rect(annot)
        fill = self._annotation_color(annot.color, opacity=max(0.3, annot.opacity)) or QColor(255, 235, 59)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = rect.adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(90, 80, 0), 1))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(body, 3, 3)
        # 折り返し（ドッグイヤー）。
        fold = min(body.width(), body.height()) * 0.34
        p_tr = body.topRight()
        tri = [
            QPointF(p_tr.x() - fold, p_tr.y()),
            QPointF(p_tr.x(), p_tr.y() + fold),
            QPointF(p_tr.x() - fold, p_tr.y() + fold),
        ]
        painter.setBrush(QBrush(QColor(0, 0, 0, 40)))
        painter.drawPolygon(*tri)
        # 本文がある印として横線を数本描く。
        painter.setPen(QPen(QColor(70, 60, 0, 160), 1))
        n_lines = 3 if annot.content.strip() else 1
        for i in range(n_lines):
            y = body.top() + body.height() * (0.4 + 0.2 * i)
            painter.drawLine(QPointF(body.left() + 3, y), QPointF(body.right() - 3, y))
        painter.restore()

    def _paint_shape_annotation(
        self,
        painter: QPainter,
        shape: ShapeAnnotData,
        rect: QRectF,
        *,
        vertices_override: tuple[tuple[float, float], ...] | None = None,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        opacity = max(0.0, min(1.0, shape.opacity))
        stroke_color = self._annotation_color(shape.stroke_color, opacity=opacity)
        fill_color = self._annotation_color(shape.fill_color, opacity=opacity)

        pen_width = max(0.0, float(shape.stroke_width) * self._zoom_factor)
        if stroke_color is not None and pen_width > 0:
            pen = QPen(stroke_color)
            pen.setWidthF(pen_width)
            painter.setPen(pen)
        else:
            painter.setPen(Qt.PenStyle.NoPen)

        if fill_color is not None:
            painter.setBrush(QBrush(fill_color))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)

        center = rect.center()
        rotation = shape.rotation
        if rotation != 0.0:
            painter.translate(center)
            painter.rotate(rotation)
            paint_rect = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
        else:
            paint_rect = rect

        if shape.shape_type == ShapeType.LINE:
            verts = vertices_override if vertices_override is not None else shape.vertices
            if verts and len(verts) >= 2:
                rx1, ry1 = verts[0]
                rx2, ry2 = verts[1]
            else:
                rx1, ry1 = (0.0, 0.5)
                rx2, ry2 = (1.0, 0.5)
            p1 = QPointF(
                paint_rect.left() + rx1 * paint_rect.width(),
                paint_rect.top() + ry1 * paint_rect.height(),
            )
            p2 = QPointF(
                paint_rect.left() + rx2 * paint_rect.width(),
                paint_rect.top() + ry2 * paint_rect.height(),
            )
            painter.drawLine(p1, p2)
            if shape.arrow_start or shape.arrow_end:
                self._draw_arrow_heads(painter, p1, p2, pen_width, shape.arrow_start, shape.arrow_end)

        elif shape.shape_type == ShapeType.RECTANGLE:
            inset = pen_width / 2.0 if pen_width > 0 else 0.0
            draw_rect = paint_rect
            if draw_rect.width() > pen_width and draw_rect.height() > pen_width:
                draw_rect = paint_rect.adjusted(inset, inset, -inset, -inset)
            painter.drawRect(draw_rect)

        elif shape.shape_type == ShapeType.ELLIPSE:
            inset = pen_width / 2.0 if pen_width > 0 else 0.0
            draw_rect = paint_rect
            if draw_rect.width() > pen_width and draw_rect.height() > pen_width:
                draw_rect = paint_rect.adjusted(inset, inset, -inset, -inset)
            painter.drawEllipse(draw_rect)

        elif shape.shape_type == ShapeType.TRIANGLE:
            ax, ay = shape.triangle_apex
            ax = min(1.0, max(0.0, float(ax)))
            ay = min(1.0, max(0.0, float(ay)))
            apex_x = paint_rect.left() + ax * paint_rect.width()
            apex_y = paint_rect.top() + ay * paint_rect.height()
            poly = QPolygonF([
                QPointF(apex_x, apex_y),
                QPointF(paint_rect.right(), paint_rect.bottom()),
                QPointF(paint_rect.left(), paint_rect.bottom()),
            ])
            painter.drawPolygon(poly)

        elif shape.shape_type == ShapeType.BRACKET:
            from src.utils.pdf_utils import _compute_bracket_vertices
            # Compute bracket vertices in the local (unrotated) rect coordinate space
            local_rect = (paint_rect.left(), paint_rect.top(), paint_rect.right(), paint_rect.bottom())
            verts = _compute_bracket_vertices(
                local_rect, shape.bracket_style, shape.bracket_side, shape.bracket_orientation
            )
            poly = QPolygonF([QPointF(v[0], v[1]) for v in verts])
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(poly)

        painter.restore()

    def _draw_arrow_heads(
        self,
        painter: QPainter,
        p1: QPointF,
        p2: QPointF,
        pen_width: float,
        draw_start: bool,
        draw_end: bool,
    ) -> None:
        import math
        arrow_size = max(8.0, pen_width * 4)
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux

        if draw_end:
            tip = p2
            base1 = QPointF(tip.x() - arrow_size * ux + arrow_size * 0.4 * px,
                            tip.y() - arrow_size * uy + arrow_size * 0.4 * py)
            base2 = QPointF(tip.x() - arrow_size * ux - arrow_size * 0.4 * px,
                            tip.y() - arrow_size * uy - arrow_size * 0.4 * py)
            painter.drawLine(tip, base1)
            painter.drawLine(tip, base2)

        if draw_start:
            tip = p1
            base1 = QPointF(tip.x() + arrow_size * ux + arrow_size * 0.4 * px,
                            tip.y() + arrow_size * uy + arrow_size * 0.4 * py)
            base2 = QPointF(tip.x() + arrow_size * ux - arrow_size * 0.4 * px,
                            tip.y() + arrow_size * uy - arrow_size * 0.4 * py)
            painter.drawLine(tip, base1)
            painter.drawLine(tip, base2)

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

    def _callout_tip_handle_rect(
        self,
        annot: FreeTextAnnotData,
        target_override: tuple[float, float] | None = None,
    ) -> QRectF | None:
        """校正コールアウトのさきっぽ（挿入位置）のハンドル矩形を返す。

        callout を持たない FreeText では None。target_override を渡すとドラッグ中の
        暫定先端位置でハンドルを描ける。
        """
        if not isinstance(annot, FreeTextAnnotData) or not annot.callout_line:
            return None
        target = target_override if target_override is not None else annot.callout_target
        if target is None:
            return None
        center = self._page_point_to_widget_point(QPointF(target[0], target[1]))
        size = float(self.HANDLE_SIZE)
        half = size / 2.0
        return QRectF(center.x() - half, center.y() - half, size, size)

    def _line_endpoints_in_widget(
        self,
        shape: ShapeAnnotData,
        rect_override: QRectF | None = None,
        vertices_override: tuple[tuple[float, float], ...] | None = None,
    ) -> tuple[QPointF, QPointF]:
        widget_rect = self._annotation_widget_rect(shape, rect_override)
        verts = vertices_override if vertices_override is not None else shape.vertices
        if verts and len(verts) >= 2:
            rx1, ry1 = verts[0]
            rx2, ry2 = verts[1]
        else:
            rx1, ry1 = (0.0, 0.5)
            rx2, ry2 = (1.0, 0.5)
        p1 = QPointF(
            widget_rect.left() + rx1 * widget_rect.width(),
            widget_rect.top() + ry1 * widget_rect.height(),
        )
        p2 = QPointF(
            widget_rect.left() + rx2 * widget_rect.width(),
            widget_rect.top() + ry2 * widget_rect.height(),
        )
        rotation = float(shape.rotation or 0.0)
        if rotation:
            cx = widget_rect.center().x()
            cy = widget_rect.center().y()
            rad = math.radians(rotation)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)

            def rot(p: QPointF) -> QPointF:
                dx = p.x() - cx
                dy = p.y() - cy
                return QPointF(cx + dx * cos_r - dy * sin_r, cy + dx * sin_r + dy * cos_r)

            p1 = rot(p1)
            p2 = rot(p2)
        return p1, p2

    def _line_handle_rects(
        self,
        shape: ShapeAnnotData,
        rect_override: QRectF | None = None,
        vertices_override: tuple[tuple[float, float], ...] | None = None,
    ) -> dict[str, QRectF]:
        p1, p2 = self._line_endpoints_in_widget(shape, rect_override, vertices_override)
        size = float(self.HANDLE_SIZE)
        half = size / 2.0
        return {
            "ep_start": QRectF(p1.x() - half, p1.y() - half, size, size),
            "ep_end": QRectF(p2.x() - half, p2.y() - half, size, size),
        }

    @staticmethod
    def _point_to_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
        dx = b.x() - a.x()
        dy = b.y() - a.y()
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-9:
            return math.hypot(p.x() - a.x(), p.y() - a.y())
        t = ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        cx = a.x() + t * dx
        cy = a.y() + t * dy
        return math.hypot(p.x() - cx, p.y() - cy)

    def _line_hit_test(self, shape: ShapeAnnotData, pos: QPoint) -> bool:
        p1, p2 = self._line_endpoints_in_widget(shape)
        pen_width = max(0.0, float(shape.stroke_width) * self._zoom_factor)
        tolerance = max(6.0, pen_width / 2.0 + 4.0)
        return self._point_to_segment_distance(QPointF(pos), p1, p2) <= tolerance

    def _annotation_hit_test(self, pos: QPoint) -> tuple[FreeTextAnnotData | None, str | None]:
        selected = self._annotation_by_xref(self._selected_annotation_xref)
        if selected is not None:
            if isinstance(selected, ShapeAnnotData) and selected.shape_type == ShapeType.LINE:
                for handle, handle_rect in self._line_handle_rects(selected).items():
                    if handle_rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                        return selected, handle
            elif isinstance(selected, FreeTextAnnotData) and selected.callout_line:
                # 校正コールアウト: さきっぽハンドルを本文ボックスのハンドルより優先判定。
                tip_rect = self._callout_tip_handle_rect(selected)
                if tip_rect is not None and tip_rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                    return selected, "callout_tip"
                selected_rect = self._annotation_widget_rect(selected)
                for handle, handle_rect in self._handle_rects(selected_rect).items():
                    if handle_rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                        return selected, handle
            elif isinstance(selected, (FreeTextAnnotData, ShapeAnnotData)):
                # マークアップ（quad ベース）はリサイズ・移動ハンドルを持たない。
                selected_rect = self._annotation_widget_rect(selected)
                for handle, handle_rect in self._handle_rects(selected_rect).items():
                    if handle_rect.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                        return selected, handle
        for annot in reversed(self._annotations):
            if isinstance(annot, ShapeAnnotData) and annot.shape_type == ShapeType.LINE:
                if self._line_hit_test(annot, pos):
                    return annot, "move"
            elif isinstance(annot, TextMarkupAnnotData):
                # マークアップは選択のみ（移動・リサイズ不可）。
                if self._annotation_widget_rect(annot).contains(QPointF(pos)):
                    return annot, "select"
            elif isinstance(annot, NoteAnnotData):
                # 付箋は固定サイズアイコンのヒット領域で選択のみ。
                if self._note_widget_rect(annot).contains(QPointF(pos)):
                    return annot, "select"
            elif self._annotation_widget_rect(annot).contains(QPointF(pos)):
                return annot, "move"
        return None, None

    def _cursor_for_handle(self, handle: str | None) -> Qt.CursorShape:
        mapping = {
            "move": Qt.CursorShape.SizeAllCursor,
            "callout_tip": Qt.CursorShape.SizeAllCursor,
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

    def _drag_updated_line(
        self, current_page: QPointF
    ) -> tuple[QRectF, tuple[tuple[float, float], tuple[float, float]]] | None:
        # LINE の端点ハンドル (ep_start / ep_end) ドラッグ時に、新しい bbox と正規化頂点を返す。
        if (
            self._drag_mode not in ("ep_start", "ep_end")
            or self._drag_base_rect is None
            or self._drag_base_vertices is None
        ):
            return None
        base = self._drag_base_rect
        verts = self._drag_base_vertices
        if verts and len(verts) >= 2:
            rx1, ry1 = verts[0]
            rx2, ry2 = verts[1]
        else:
            rx1, ry1 = (0.0, 0.5)
            rx2, ry2 = (1.0, 0.5)
        bw = base.width()
        bh = base.height()
        sp_orig = QPointF(base.left() + rx1 * bw, base.top() + ry1 * bh)
        ep_orig = QPointF(base.left() + rx2 * bw, base.top() + ry2 * bh)

        page_w, page_h = self.page_size_points()
        clamped = QPointF(
            min(max(0.0, current_page.x()), page_w if page_w > 0 else current_page.x()),
            min(max(0.0, current_page.y()), page_h if page_h > 0 else current_page.y()),
        )
        if self._drag_mode == "ep_start":
            new_sp, new_ep = clamped, ep_orig
        else:
            new_sp, new_ep = sp_orig, clamped

        x0 = min(new_sp.x(), new_ep.x())
        x1 = max(new_sp.x(), new_ep.x())
        y0 = min(new_sp.y(), new_ep.y())
        y1 = max(new_sp.y(), new_ep.y())
        if max(x1 - x0, y1 - y0) < 1.0:
            return None
        width = x1 - x0
        height = y1 - y0
        nrx1 = (new_sp.x() - x0) / width if width > 0 else 0.0
        nry1 = (new_sp.y() - y0) / height if height > 0 else 0.5
        nrx2 = (new_ep.x() - x0) / width if width > 0 else 1.0
        nry2 = (new_ep.y() - y0) / height if height > 0 else 0.5
        return QRectF(QPointF(x0, y0), QPointF(x1, y1)), ((nrx1, nry1), (nrx2, nry2))

    def _drag_updated_rect(self, current_page: QPointF) -> QRectF | None:
        if self._drag_mode is None or self._drag_origin_page is None or self._drag_base_rect is None:
            return None

        page_w, page_h = self.page_size_points()
        base = self._drag_base_rect

        if self._drag_mode == "move":
            width = base.width()
            height = base.height()
            left = base.left() + (current_page.x() - self._drag_origin_page.x())
            top = base.top() + (current_page.y() - self._drag_origin_page.y())
            left = min(max(0.0, left), max(0.0, page_w - width))
            top = min(max(0.0, top), max(0.0, page_h - height))
            return QRectF(left, top, width, height)

        annot = self._annotation_by_xref(self._drag_annotation_xref)
        min_w, min_h, min_span = self._min_dims_for_annotation(annot)

        left = base.left()
        top = base.top()
        right = base.right()
        bottom = base.bottom()

        if "w" in self._drag_mode:
            left = min(max(0.0, current_page.x()), right - min_w)
        if "e" in self._drag_mode:
            right = max(min(page_w, current_page.x()), left + min_w)
        if "n" in self._drag_mode:
            top = min(max(0.0, current_page.y()), bottom - min_h)
        if "s" in self._drag_mode:
            bottom = max(min(page_h, current_page.y()), top + min_h)

        if min_span > 0.0 and max(right - left, bottom - top) < min_span:
            return None

        return QRectF(QPointF(left, top), QPointF(right, bottom))

    def _update_char_selection(self) -> None:
        """Set selected chars to the reading-order run between anchor and head."""
        anchor = self._sel_anchor_char
        head = self._sel_head_char
        if anchor is None or head is None or not self._chars:
            self._selected_char_indices = []
            return
        lo, hi = (anchor, head) if anchor <= head else (head, anchor)
        lo = max(0, lo)
        hi = min(len(self._chars) - 1, hi)
        self._selected_char_indices = list(range(lo, hi + 1))

    def _select_word_at_char(self, char_idx: int) -> None:
        """Promote a single char to its enclosing word's char run (click-select)."""
        if char_idx is None or char_idx >= len(self._char_rects):
            self._selected_char_indices = [char_idx] if char_idx is not None else []
            return
        center = self._char_rects[char_idx].center()
        word_rect = None
        for wr in self._word_rects:
            if wr.contains(center):
                word_rect = wr
                break
        if word_rect is None:
            self._selected_char_indices = [char_idx]
            return
        run = [i for i, cr in enumerate(self._char_rects) if word_rect.contains(cr.center())]
        self._selected_char_indices = run or [char_idx]

    def _selected_line_runs(self) -> list[list[int]]:
        """Group selected char indices into per-line runs, preserving order."""
        runs: list[list[int]] = []
        current: list[int] = []
        current_line = None
        for idx in self._selected_char_indices:
            if idx >= len(self._char_line_ids):
                continue
            line_id = self._char_line_ids[idx]
            if current_line is None or line_id == current_line:
                current.append(idx)
                current_line = line_id
            else:
                runs.append(current)
                current = [idx]
                current_line = line_id
        if current:
            runs.append(current)
        return runs

    def _selected_text(self) -> str:
        if not self._selected_char_indices:
            return ""
        parts: list[str] = []
        prev_line = None
        for idx in self._selected_char_indices:
            if idx >= len(self._chars):
                continue
            ch = self._chars[idx]
            line_id = ch["line_id"]
            if prev_line is not None and line_id != prev_line:
                parts.append("\n")
            parts.append(ch["c"])
            prev_line = line_id
        return "".join(parts)

    def _update_cursor(self, pos: QPoint) -> None:
        if self._view_only:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.has_annotation_paste_mode() or self._note_create_mode or self._callout_create_mode:
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

            if self._search_hit_rects:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 235, 59, 130))
                offset_pt = QPointF(offset)
                for rect in self._search_hit_rects:
                    painter.drawRect(rect.translated(offset_pt))

            if self._selected_char_indices:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 120, 215, 80))
                offset_pt = QPointF(offset)
                for run in self._selected_line_runs():
                    merged: QRectF | None = None
                    for idx in run:
                        if idx >= len(self._char_rects):
                            continue
                        cr = self._char_rects[idx]
                        merged = cr if merged is None else merged.united(cr)
                    if merged is not None:
                        painter.drawRect(merged.translated(offset_pt))

            for annot in self._annotations:
                preview_rect: QRectF | None = None
                dragging_this = annot.xref == self._drag_annotation_xref
                if (
                    dragging_this
                    and self._pending_annotation_rect is not None
                    and not self._drag_copy_mode
                ):
                    preview_rect = self._annotation_widget_rect(annot, self._pending_annotation_rect)
                annot_rect = preview_rect or self._annotation_widget_rect(annot)
                is_line_shape = (
                    isinstance(annot, ShapeAnnotData)
                    and annot.shape_type == ShapeType.LINE
                )
                line_vertices_override: tuple[tuple[float, float], ...] | None = None
                if (
                    is_line_shape
                    and dragging_this
                    and self._pending_line_vertices is not None
                    and not self._drag_copy_mode
                ):
                    line_vertices_override = self._pending_line_vertices
                is_being_edited = (
                    annot.xref == self._editing_annotation_xref
                    and self._inline_editor is not None
                    and self._inline_editor.isVisible()
                )
                is_markup = isinstance(annot, TextMarkupAnnotData)
                is_note = isinstance(annot, NoteAnnotData)
                if not is_being_edited:
                    if isinstance(annot, ShapeAnnotData):
                        self._paint_shape_annotation(
                            painter,
                            annot,
                            annot_rect,
                            vertices_override=line_vertices_override,
                        )
                    elif is_markup:
                        self._paint_markup_annotation(painter, annot)
                    elif is_note:
                        self._paint_note_annotation(painter, annot)
                    else:
                        self._paint_annotation(painter, annot, annot_rect)
                if annot.xref == self._selected_annotation_xref:
                    if is_markup or is_note:
                        # マークアップ・付箋は選択枠（破線）のみ。ハンドルなし。
                        outline = self._note_widget_rect(annot) if is_note else annot_rect
                        painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRect(outline.adjusted(-2, -2, 2, 2))
                        continue
                    painter.setPen(QPen(QColor(0, 120, 215), 1))
                    painter.setBrush(QBrush(QColor(255, 255, 255)))
                    if is_line_shape:
                        handle_rect_override = (
                            self._pending_annotation_rect if preview_rect is not None else None
                        )
                        handle_iter = self._line_handle_rects(
                            annot,
                            handle_rect_override,
                            line_vertices_override,
                        ).values()
                    else:
                        handle_iter = self._handle_rects(annot_rect).values()
                    for handle_rect in handle_iter:
                        painter.drawRect(handle_rect)
                    # 校正コールアウト: さきっぽ（挿入位置）のハンドルを丸で描く。
                    if isinstance(annot, FreeTextAnnotData) and annot.callout_line:
                        tip_override = (
                            self._pending_callout_target
                            if (
                                self._drag_mode == "callout_tip"
                                and annot.xref == self._drag_annotation_xref
                            )
                            else None
                        )
                        tip_handle = self._callout_tip_handle_rect(annot, tip_override)
                        if tip_handle is not None:
                            painter.drawEllipse(tip_handle)

            if (
                self._drag_copy_mode
                and self._pending_annotation_rect is not None
                and self._drag_annotation_xref is not None
            ):
                drag_annot = self._annotation_by_xref(self._drag_annotation_xref)
                if drag_annot is not None:
                    ghost_rect = self._annotation_widget_rect(drag_annot, self._pending_annotation_rect)
                    painter.save()
                    painter.setOpacity(0.6)
                    if isinstance(drag_annot, ShapeAnnotData):
                        self._paint_shape_annotation(painter, drag_annot, ghost_rect)
                    else:
                        self._paint_annotation(painter, drag_annot, ghost_rect)
                    painter.restore()

        if self._annotation_create_preview_rect is not None:
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if (
                self._annotation_create_shape_type == ShapeType.LINE
                and self._annotation_create_origin_page is not None
                and self._annotation_create_current_page is not None
            ):
                p1 = self._page_point_to_widget_point(self._annotation_create_origin_page)
                p2 = self._page_point_to_widget_point(self._annotation_create_current_page)
                painter.drawLine(p1, p2)
            else:
                preview_rect = self._page_rect_to_widget_rect(self._annotation_create_preview_rect)
                painter.drawRect(preview_rect)
        if self._paste_annotation is not None and self._paste_preview_rect is not None:
            preview_rect = self._annotation_widget_rect(self._paste_annotation, self._paste_preview_rect)
            if isinstance(self._paste_annotation, ShapeAnnotData):
                self._paint_shape_annotation(painter, self._paste_annotation, preview_rect)
            else:
                self._paint_annotation(painter, self._paste_annotation, preview_rect)
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(preview_rect)
        if self._marquee_rect is not None:
            # 右ドラッグの範囲指定拡大プレビュー。
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 120, 215, 40))
            painter.drawRect(self._marquee_rect.normalized())

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
            if event.button() == Qt.MouseButton.MiddleButton:
                # 中ボタンドラッグでつかんで移動（パン）。
                self.setFocus()
                self._pan_active = True
                self._pan_last_global = event.globalPosition().toPoint()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                # 右ドラッグで範囲を指定し、その範囲を拡大表示。
                # 注釈作成中・貼り付け中は通常操作を優先して無効化する。
                if self._annotation_create_mode or self.has_annotation_paste_mode():
                    event.accept()
                    return
                self.setFocus()
                self._marquee_active = True
                self._marquee_origin = event.pos()
                self._marquee_rect = QRect(event.pos(), event.pos())
                self.update()
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if self._view_only:
                    # 閲覧専用: 選択・作成・ドラッグ・リンク操作を行わない。
                    self.setFocus()
                    event.accept()
                    return
                self.setFocus()
                page_point = self._page_point_from_widget_pos(event.pos())
                if self._note_create_mode:
                    if page_point is not None:
                        self.note_create_requested.emit((page_point.x(), page_point.y()))
                    event.accept()
                    return
                if self._callout_create_mode:
                    if page_point is not None:
                        self.callout_create_requested.emit((page_point.x(), page_point.y()))
                    event.accept()
                    return
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
                    self._selected_char_indices = []
                    self._sel_anchor_char = None
                    self._sel_head_char = None
                    self._selection_origin = None
                    self._selection_rect = None
                    self._selection_active = False
                    self._pressed_link = None
                    self._annotation_create_origin_page = page_point
                    self._annotation_create_current_page = page_point
                    self._annotation_create_preview_rect = QRectF(page_point, page_point)
                    self.update()
                    event.accept()
                    return

                annot, handle = self._annotation_hit_test(event.pos())
                if annot is not None and handle == "select":
                    # マークアップ: 選択のみ。ドラッグによる移動・リサイズはしない。
                    self._selected_annotation_xref = annot.xref
                    self.annotation_selected.emit(annot)
                    self._selection_origin = None
                    self._selection_rect = None
                    self._selection_active = False
                    self._pressed_link = None
                    self.update()
                    event.accept()
                    return
                if annot is not None and handle is not None:
                    ctrl_copy = (
                        handle == "move"
                        and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                    )
                    self._selected_annotation_xref = annot.xref
                    self.annotation_selected.emit(annot)
                    self._drag_annotation_xref = annot.xref
                    self._drag_mode = handle
                    self._drag_origin_page = self._page_point_from_widget_pos(event.pos(), clamp=True)
                    self._drag_base_rect = self._rect_tuple_to_qrectf(annot.rect)
                    self._pending_annotation_rect = QRectF(self._drag_base_rect)
                    if handle == "callout_tip" and isinstance(annot, FreeTextAnnotData):
                        # さきっぽドラッグ: 本文ボックスは不変。先端位置だけを更新する。
                        self._drag_base_vertices = None
                        self._pending_line_vertices = None
                        self._pending_callout_target = annot.callout_target
                    elif isinstance(annot, ShapeAnnotData) and annot.shape_type == ShapeType.LINE:
                        base_verts = tuple(tuple(v) for v in annot.vertices) if annot.vertices else ()
                        self._drag_base_vertices = base_verts
                        self._pending_line_vertices = base_verts
                        self._pending_callout_target = None
                    else:
                        self._drag_base_vertices = None
                        self._pending_line_vertices = None
                        self._pending_callout_target = None
                    self._drag_moved = False
                    self._drag_copy_mode = ctrl_copy
                    self.update()
                    event.accept()
                    return

                if self._selected_annotation_xref is not None:
                    self._selected_annotation_xref = None
                    self.annotation_selected.emit(None)

                self._selection_origin = event.pos()
                self._selection_rect = None
                self._pressed_link = self._link_at(event.pos())
                self._selection_active = self._pressed_link is None
                if self._selection_active:
                    # Anchor the caret now; the visible run is built once the
                    # drag starts (or promoted to a word on a no-drag release).
                    anchor = self._char_index_at(event.pos(), nearest=True)
                    self._sel_anchor_char = anchor
                    self._sel_head_char = anchor
                    self._selected_char_indices = []
                self.update()
        except Exception:
            logger.exception("Error in ZoomPageWidget.mousePressEvent")
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if self._view_only:
            super().mouseDoubleClickEvent(event)
            return
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                annot, handle = self._annotation_hit_test(event.pos())
                if annot is not None and handle == "move" and not isinstance(annot, ShapeAnnotData):
                    self.annotation_edit_requested.emit(annot)
                    event.accept()
                    return
        except Exception:
            logger.exception("Error in ZoomPageWidget.mouseDoubleClickEvent")
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        try:
            if self._pan_active and (event.buttons() & Qt.MouseButton.MiddleButton):
                cur = event.globalPosition().toPoint()
                if self._pan_last_global is not None:
                    # マウスの移動と逆方向にスクロールして紙をつかんで動かす感覚にする。
                    self.scroll_requested.emit(
                        self._pan_last_global.x() - cur.x(),
                        self._pan_last_global.y() - cur.y(),
                    )
                self._pan_last_global = cur
                event.accept()
                return
            if self._marquee_active and (event.buttons() & Qt.MouseButton.RightButton):
                if self._marquee_origin is not None:
                    self._marquee_rect = QRect(self._marquee_origin, event.pos()).normalized()
                    self.update()
                event.accept()
                return
            if self._view_only:
                # 閲覧専用: ホバー・選択・ドラッグ等は行わず、カーソルのみ更新。
                self._update_cursor(event.pos())
                event.accept()
                return
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
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        current_page = self._apply_shape_shift_constraint(
                            self._annotation_create_shape_type,
                            self._annotation_create_origin_page,
                            current_page,
                        )
                    self._annotation_create_current_page = current_page
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
                    if self._drag_mode == "callout_tip":
                        new_target = (current_page.x(), current_page.y())
                        self._pending_callout_target = new_target
                        base = self._annotation_by_xref(self._drag_annotation_xref)
                        base_target = base.callout_target if base is not None else None
                        if base_target is None:
                            self._drag_moved = True
                        else:
                            self._drag_moved = (
                                abs(new_target[0] - base_target[0]) > 0.01
                                or abs(new_target[1] - base_target[1]) > 0.01
                            )
                        self.update()
                    elif self._drag_mode in ("ep_start", "ep_end"):
                        line_updated = self._drag_updated_line(current_page)
                        if line_updated is not None:
                            new_rect, new_vertices = line_updated
                            self._pending_annotation_rect = new_rect
                            self._pending_line_vertices = new_vertices
                            rect_changed = not self._annotation_rect_close(new_rect, self._drag_base_rect)
                            verts_changed = new_vertices != self._drag_base_vertices
                            self._drag_moved = rect_changed or verts_changed
                            self.update()
                    else:
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
                        if self._sel_anchor_char is None:
                            self._sel_anchor_char = self._char_index_at(
                                self._selection_origin, nearest=True
                            )
                if self._selection_active:
                    self._sel_head_char = self._char_index_at(event.pos(), nearest=True)
                    self._update_char_selection()
                    self.update()
            else:
                self._update_cursor(event.pos())
                self._update_note_hover(event.pos())
        except Exception:
            logger.exception("Error in ZoomPageWidget.mouseMoveEvent")
        event.accept()

    def _note_at(self, pos: QPoint) -> NoteAnnotData | None:
        for annot in reversed(self._annotations):
            if isinstance(annot, NoteAnnotData) and self._note_widget_rect(annot).contains(QPointF(pos)):
                return annot
        return None

    def _update_note_hover(self, pos: QPoint) -> None:
        """Show a small popup preview when hovering a sticky-note icon (A)."""
        note = self._note_at(pos)
        new_xref = note.xref if note is not None else None
        if new_xref == self._hover_note_xref:
            return
        self._hover_note_xref = new_xref
        if note is None:
            self._hide_note_popup()
            return
        self._show_note_popup(note)

    def _hide_note_popup(self) -> None:
        if self._note_popup is not None:
            self._note_popup.hide()
            self._note_popup.deleteLater()
            self._note_popup = None

    def _show_note_popup(self, note: NoteAnnotData) -> None:
        self._hide_note_popup()
        text = (note.content or "").strip() or "（空のコメント）"
        preview = text[:80] + ("…" if len(text) > 80 else "")
        popup = QFrame(self)
        popup.setObjectName("notePopup")
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.setStyleSheet(
            "#notePopup { background-color: #fffbe6; border: 1px solid #b8a700; border-radius: 4px; }"
            " QLabel { color: #333; padding: 4px 6px; }"
        )
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(preview, popup)
        label.setWordWrap(True)
        label.setMaximumWidth(220)
        layout.addWidget(label)
        popup.adjustSize()
        icon_rect = self._note_widget_rect(note)
        x = int(icon_rect.right() + 6)
        y = int(icon_rect.top())
        if x + popup.width() > self.width():
            x = int(icon_rect.left() - popup.width() - 6)
        y = max(0, min(y, self.height() - popup.height()))
        popup.move(max(0, x), y)
        popup.show()
        popup.raise_()
        self._note_popup = popup

    def leaveEvent(self, event) -> None:
        self._hover_note_xref = None
        self._hide_note_popup()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        try:
            if event.button() == Qt.MouseButton.MiddleButton and self._pan_active:
                self._pan_active = False
                self._pan_last_global = None
                self._update_cursor(event.pos())
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton and self._marquee_active:
                self._marquee_active = False
                self._marquee_origin = None
                rect = self._marquee_rect
                self._marquee_rect = None
                self.update()
                # 十分な大きさの範囲が指定されたときだけ拡大する。
                if rect is not None and rect.width() >= 5 and rect.height() >= 5:
                    p0 = self._page_point_from_widget_pos(rect.topLeft(), clamp=True)
                    p1 = self._page_point_from_widget_pos(rect.bottomRight(), clamp=True)
                    if p0 is not None and p1 is not None:
                        page_rect = QRectF(p0, p1).normalized()
                        if page_rect.width() > 0 and page_rect.height() > 0:
                            self.zoom_region_requested.emit(page_rect)
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if self._view_only:
                    event.accept()
                    return
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
                    origin = self._annotation_create_origin_page
                    end_point = current_page if current_page is not None else self._annotation_create_current_page
                    shape_type = self._annotation_create_shape_type
                    if (
                        end_point is not None
                        and origin is not None
                        and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    ):
                        end_point = self._apply_shape_shift_constraint(shape_type, origin, end_point)
                    self._clear_annotation_create_drag()
                    self.update()
                    if origin is not None and end_point is not None:
                        start_t = (origin.x(), origin.y())
                        end_t = (end_point.x(), end_point.y())
                        if shape_type is not None:
                            self.shape_create_requested.emit(shape_type, start_t, end_t)
                        else:
                            final_rect = QRectF(origin, end_point).normalized()
                            if final_rect.width() > 0 and final_rect.height() > 0:
                                self.annotation_create_requested.emit(
                                    self._qrectf_to_rect_tuple(final_rect)
                                )
                    event.accept()
                    return
                if self._drag_mode is not None:
                    annot = self._annotation_by_xref(self._drag_annotation_xref)
                    final_rect = self._pending_annotation_rect or self._drag_base_rect
                    final_vertices = self._pending_line_vertices
                    final_callout_target = self._pending_callout_target
                    drag_mode = self._drag_mode
                    copy_mode = self._drag_copy_mode
                    if (
                        annot is not None
                        and drag_mode == "callout_tip"
                        and final_callout_target is not None
                        and self._drag_moved
                    ):
                        self.callout_target_changed.emit(annot, final_callout_target)
                    elif annot is not None and final_rect is not None and self._drag_moved:
                        if copy_mode and drag_mode == "move":
                            self.annotation_duplicate_requested.emit(
                                annot,
                                self._qrectf_to_rect_tuple(final_rect),
                                None,
                            )
                        elif (
                            isinstance(annot, ShapeAnnotData)
                            and annot.shape_type == ShapeType.LINE
                            and drag_mode in ("ep_start", "ep_end")
                            and final_vertices is not None
                        ):
                            self.shape_geometry_changed_with_vertices.emit(
                                annot,
                                self._qrectf_to_rect_tuple(final_rect),
                                final_vertices,
                                drag_mode,
                            )
                        else:
                            self.annotation_geometry_changed.emit(
                                annot,
                                self._qrectf_to_rect_tuple(final_rect),
                                drag_mode,
                            )
                    self._drag_annotation_xref = None
                    self._drag_mode = None
                    self._drag_origin_page = None
                    self._drag_base_rect = None
                    self._pending_annotation_rect = None
                    self._drag_base_vertices = None
                    self._pending_line_vertices = None
                    self._pending_callout_target = None
                    self._drag_moved = False
                    self._drag_copy_mode = False
                    self.update()
                    event.accept()
                    return
                if self._selection_active:
                    no_drag = (
                        self._selection_origin is not None
                        and (event.pos() - self._selection_origin).manhattanLength()
                        < QApplication.startDragDistance()
                    )
                    if no_drag:
                        # Pure click: select the whole word at this point.
                        idx = self._char_index_at(event.pos())
                        if idx is None:
                            idx = self._sel_anchor_char
                        if idx is not None:
                            self._select_word_at_char(idx)
                        else:
                            self._selected_char_indices = []
                    self._selection_rect = None
                    self._selection_active = False
                else:
                    if self._pressed_link is not None:
                        self.link_clicked.emit(self._pressed_link)
                self._selection_origin = None
                self._pressed_link = None
                self.update()
        except Exception:
            logger.exception("Error in ZoomPageWidget.mouseReleaseEvent")
        event.accept()

    def keyPressEvent(self, event) -> None:
        if self._view_only:
            # 閲覧専用: 矢印キーでのビュースクロールのみ許可（Ctrl で高速）。
            if event.key() in self._ARROW_DELTAS:
                ux, uy = self._ARROW_DELTAS[event.key()]
                step = (
                    self.ZOOM_SCROLL_STEP_FAST
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    else self.ZOOM_SCROLL_STEP
                )
                self.scroll_requested.emit(int(ux * step), int(uy * step))
                event.accept()
                return
            super().keyPressEvent(event)
            return
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
        if event.key() in self._ARROW_DELTAS and self._inline_editor is None:
            ux, uy = self._ARROW_DELTAS[event.key()]
            mods = event.modifiers()
            if self._selected_annotation_xref is not None:
                # 注釈を移動。Alt/Shift=細かく、Ctrl=粗く、無修飾=通常ステップ。
                if mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier):
                    step = self.ANNOTATION_MOVE_STEP_FINE
                elif mods & Qt.KeyboardModifier.ControlModifier:
                    step = self.ANNOTATION_MOVE_STEP_COARSE
                else:
                    step = self.ANNOTATION_MOVE_STEP
                if self._move_selected_annotation(ux * step, uy * step):
                    event.accept()
                    return
            else:
                # 注釈未選択時は矢印でビューをスクロール。Ctrl で高速スクロール。
                # QScrollArea 任せにせず自前で出し分け、Ctrl の加速を確実にする。
                step = (
                    self.ZOOM_SCROLL_STEP_FAST
                    if mods & Qt.KeyboardModifier.ControlModifier
                    else self.ZOOM_SCROLL_STEP
                )
                self.scroll_requested.emit(int(ux * step), int(uy * step))
                event.accept()
                return
        super().keyPressEvent(event)

    def _move_selected_annotation(self, dx: float, dy: float) -> bool:
        annot = self._annotation_by_xref(self._selected_annotation_xref)
        if annot is None:
            return False
        base = self._rect_tuple_to_qrectf(annot.rect).normalized()
        width = base.width()
        height = base.height()
        page_w, page_h = self.page_size_points()
        left = base.left() + dx
        top = base.top() + dy
        if page_w > 0:
            left = min(max(0.0, left), max(0.0, page_w - width))
        if page_h > 0:
            top = min(max(0.0, top), max(0.0, page_h - height))
        new_rect = QRectF(left, top, width, height)
        if self._annotation_rect_close(new_rect, base):
            return False
        self.annotation_geometry_changed.emit(
            annot, self._qrectf_to_rect_tuple(new_rect), "move"
        )
        return True

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_inline_editor()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self.has_annotation_paste_mode():
            self.cancel_annotation_paste_mode()

