"""Page edit window for editing PDF pages."""
import os
import shutil
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import replace as dataclass_replace
from enum import Enum, auto
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QToolBar,
    QPushButton,
    QScrollArea,
    QGridLayout,
    QInputDialog,
    QLabel,
    QFrame,
    QApplication,
    QRubberBand,
    QMessageBox,
    QToolButton,
    QFormLayout,
    QSpinBox,
    QColorDialog,
    QSlider,
    QCheckBox,
    QComboBox,
    QMenu,
    QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import (
    Qt,
    QSize,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QUrl,
    QTimer,
    QEvent,
    QSignalBlocker,
)
from PyQt6.QtGui import (
    QKeySequence,
    QPainter,
    QColor,
    QDesktopServices,
    QPixmap,
    QCursor,
    QAction,
)

from src.utils.pdf_utils import (
    get_page_pixmap,
    get_page_words,
    get_page_chars,
    get_page_links,
    get_page_count,
    rotate_pages,
    remove_pages,
    reorder_pages,
    extract_pages,
    insert_pages,
    render_page_thumbnails_batch,
    FreeTextAnnotData,
    list_freetext_annots,
    create_freetext_annot,
    replace_freetext_annot,
    delete_freetext_annot,
    get_pdf_metadata_title,
    update_pdf_metadata_title,
    PdfWritePermissionError,
    clear_pixmap_cache_for_path,
    print_pdfs,
    ShapeType,
    ShapeAnnotData,
    AnyAnnotData,
    list_shape_annots,
    create_shape_annot,
    replace_shape_annot,
    delete_shape_annot,
    create_bracket_pair,
    create_callout,
    delete_annot_group,
    _callout_box_attach,
    MarkupType,
    TextMarkupAnnotData,
    list_markup_annots,
    create_markup_annot,
    delete_markup_annot,
    replace_markup_annot,
    NoteAnnotData,
    list_note_annots,
    create_note_annot,
    delete_note_annot,
    replace_note_annot,
    reorder_annot_on_page,
    get_annot_xref_order,
    set_annot_xref_order,
    search_text_in_pdf,
    TocEntry,
    get_pdf_toc,
    update_pdf_toc,
)
from src.utils.constants import (
    PAGETHUMBNAIL_MIME_TYPE,
    PDFCARD_MIME_TYPE,
)


from src.views.page_edit_widgets import (
    AnnotationTextEdit,
    NoteContentEdit,
    PageThumbnail,
    ZoomPageWidget,
    _apply_block_line_height,
    _build_freetext_document,
    _pixel_size_to_pointf,
)


def _line_endpoints_from_shape(
    shape: "ShapeAnnotData",
    rect_override: tuple[float, float, float, float] | None = None,
    vertices_override: tuple[tuple[float, float], ...] | None = None,
) -> tuple[tuple[float, float], tuple[float, float]]:
    # LINE 注釈の rect (bbox) と正規化頂点から、ページ座標の (start, end) を再構築する。
    rect = rect_override if rect_override is not None else shape.rect
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    verts = vertices_override if vertices_override is not None else shape.vertices
    if verts and len(verts) >= 2:
        rx1, ry1 = verts[0]
        rx2, ry2 = verts[1]
    else:
        rx1, ry1 = (0.0, 0.5)
        rx2, ry2 = (1.0, 0.5)
    return (
        (rect[0] + rx1 * width, rect[1] + ry1 * height),
        (rect[0] + rx2 * width, rect[1] + ry2 * height),
    )


from src.models.undo_manager import UndoManager, UndoAction
from src.views.bookmarks_panel import BookmarksPanel
from src.views.view_helpers import (
    clear_selection,
    log_undo_state,
    register_shortcuts,
    viewport_width_or_fallback,
)
from send2trash import send2trash
from src.utils.trash_utils import build_trash_failure_message


logger = logging.getLogger(__name__)


class CreateMode(Enum):
    """拡大表示での新規作成モード。常にいずれか1つだけが有効(相互排他)。"""

    NONE = auto()
    FREETEXT = auto()  # テキストボックス(付箋)の配置待ち
    SHAPE = auto()  # 図形の配置待ち(種別は _zoom_create_mode が保持)
    NOTE = auto()  # コメント付箋の配置待ち
    CALLOUT = auto()  # 校正コールアウトの配置待ち


# 括弧図形コンボボックスの並び(インデックス⇔値の唯一の対応表)
_BRACKET_STYLES = ("square", "round", "curly")
_BRACKET_SIZES = ("small", "medium", "large")


class _AnnotRef:
    """論理的に同じ注釈の「現在の xref」を共有保持する可変ハンドル。

    注釈の移動・リサイズ・テキスト編集は PDF 上で「古い注釈を削除して
    新規に作り直す」ため xref が変わる。貼り付け/作成/複製の Undo は
    作成時点の xref を固定で握っているとここで取り残され、後から
    「xref ... is not an annot of this page」で失敗する。

    そこで論理的に同じ注釈を指す全ての Undo/Redo アクションがこの 1 個の
    ハンドルを共有し、置換のたびにここだけ更新する。こうすると Undo は
    常に「いま生きている xref」を対象にできる。
    """

    __slots__ = ("page_num", "xref")

    def __init__(self, page_num: int, xref: int):
        self.page_num = page_num
        self.xref = xref


class PageEditWindow(QMainWindow):
    """Window for editing pages within a PDF."""

    ZOOM_MIN = 25
    ZOOM_MAX = 400
    ZOOM_STEP = 5
    # 「100%」ボタンのドロップダウンに並べる倍率プリセット（25/100/400% は必須）。
    ZOOM_PRESETS = (25, 50, 75, 100, 150, 200, 300, 400)
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
        self._zoom_reset_btn = None
        self._zoom_page_label = None
        self._zoom_prev_btn = None
        self._zoom_next_btn = None
        self._zoom_page_num = None
        self._zoom_factor = 1.0
        self._zoom_text_cache: dict[int, tuple[list[tuple], list[dict], list[dict]]] = {}
        self._zoom_annotations: list[FreeTextAnnotData] = []
        self._selected_zoom_annotation: FreeTextAnnotData | None = None
        # 注釈未選択でも新規作成のデフォルト値を編集できるよう、現在の作成モードを保持する。
        # None=非作成 / "freetext"=FreeText新規 / ShapeType=図形新規
        self._zoom_create_mode: ShapeType | str | None = None
        self._copied_zoom_annotation: AnyAnnotData | None = None
        # 論理注釈 → 現在の xref を共有するハンドルのレジストリ。キーは「現在の xref」。
        # 置換(move/resize/edit)で xref が回るたびに更新し、貼り付け/作成/複製/削除の
        # Undo が常に生きている xref を対象にできるようにする。詳細は _AnnotRef 参照。
        self._annot_refs: dict[int, _AnnotRef] = {}
        self._zoom_annotation_drawer = None
        self._zoom_annotation_panel = None
        self._zoom_annotation_open = False
        # 見開き表示(2ページ並べ・閲覧専用)モードのON/OFF。
        self._zoom_spread_mode = False
        self._zoom_annotation_form_sync = False
        self._zoom_annotation_text_commit_in_progress = False
        self._zoom_annotation_new_btn = None
        self._zoom_annotation_delete_btn = None
        self._zoom_annotation_order_front_btn = None
        self._zoom_annotation_order_forward_btn = None
        self._zoom_annotation_order_backward_btn = None
        self._zoom_annotation_order_back_btn = None
        self._zoom_annotation_width_spin = None
        self._zoom_annotation_height_spin = None
        self._zoom_annotation_fontsize_spin = None
        self._zoom_annotation_opacity_slider = None
        self._zoom_annotation_opacity_label = None
        self._zoom_annotation_border_width_spin = None
        self._zoom_annotation_text_color_btn = None
        self._zoom_annotation_fill_color_btn = None
        self._zoom_annotation_fill_color_clear_btn = None
        self._zoom_annotation_border_color_btn = None
        self._zoom_annotation_border_color_clear_btn = None
        self._zoom_annotation_text_color = (0.0, 0.0, 0.0)
        self._zoom_annotation_fill_color: tuple[float, float, float] | None = (1.0, 1.0, 0.6)
        self._zoom_annotation_border_color: tuple[float, float, float] | None = (0.0, 0.0, 0.0)
        self._zoom_markup_color: tuple[float, float, float] = (1.0, 0.92, 0.23)
        self._zoom_markup_color_btn: QPushButton | None = None
        self._markup_buttons: dict[MarkupType, QToolButton] = {}
        self._zoom_note_color: tuple[float, float, float] = (1.0, 0.92, 0.23)
        self._zoom_note_color_btn: QPushButton | None = None
        self._zoom_note_btn: QToolButton | None = None
        self._zoom_note_editor: NoteContentEdit | None = None
        self._zoom_note_list: QListWidget | None = None
        self._zoom_callout_btn: QToolButton | None = None
        self._create_mode = CreateMode.NONE
        # 本文編集中の付箋 xref と、確定前の元本文（差分判定用）。
        self._editing_note_xref: int | None = None
        self._editing_note_original = ""
        self._thumb_size = PageThumbnail.THUMBNAIL_SIZE
        self._thumb_render_queue: deque[int] = deque()
        self._thumb_render_queue_set: set[int] = set()
        self._thumb_render_timer = QTimer(self)
        self._thumb_render_timer.setSingleShot(True)
        self._thumb_render_timer.timeout.connect(self._process_thumbnail_render_queue)
        self._scroll_debounce_timer = QTimer(self)
        self._scroll_debounce_timer.setSingleShot(True)
        self._scroll_debounce_timer.setInterval(60)
        self._scroll_debounce_timer.timeout.connect(self._enqueue_visible_thumbnail_renders)

        # Drop indicator
        self._drop_indicator = None
        self._drop_indicator_index = -1

        # Rubber band selection
        self._rubber_band = None
        self._rubber_band_origin = None

        # Text search (Ctrl+F)
        self._search_dialog = None
        self._search_hits: dict[int, list] = {}
        self._search_hit_pages: list[int] = []
        self._search_cursor: int = -1

        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()
        self._undo_manager.add_listener(self._on_undo_manager_changed)
        QTimer.singleShot(0, self._load_pages)

    def _setup_ui(self) -> None:
        self.setWindowTitle(f"JusticePDF - 編集:{os.path.basename(self._pdf_path)}")
        self.resize(800, 600)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self._build_page_grid(layout)
        self._build_zoom_view(layout)

    def _build_page_grid(self, layout: QVBoxLayout) -> None:
        """ページサムネイル一覧(グリッド)と選択・ドロップ表示部品を組み立てる。"""
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
        self._drop_indicator.setStyleSheet("background-color: #4f46e5;")
        self._drop_indicator.setFixedWidth(3)
        self._drop_indicator.hide()

        # Rubber band for selection
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self._container)

    def _build_zoom_view(self, layout: QVBoxLayout) -> None:
        """拡大表示ビュー(操作バー+キャンバス+付箋/しおりドロワー)を組み立てる。"""
        self._zoom_view = QWidget()
        zoom_layout = QVBoxLayout(self._zoom_view)
        zoom_layout.setContentsMargins(0, 0, 0, 0)

        zoom_layout.addWidget(self._build_zoom_controls())

        zoom_content = QWidget()
        zoom_content_layout = QHBoxLayout(zoom_content)
        zoom_content_layout.setContentsMargins(0, 0, 0, 0)
        zoom_content_layout.setSpacing(0)
        zoom_content_layout.addWidget(self._build_zoom_canvas(), 1)
        zoom_content_layout.addWidget(self._build_annotation_drawer())
        zoom_content_layout.addWidget(self._build_bookmarks_panel())

        zoom_layout.addWidget(zoom_content, 1)
        self._set_zoom_annotation_drawer_open(False)
        self._set_selected_zoom_annotation(None)

        layout.addWidget(self._zoom_view)
        self._zoom_view.hide()

    def _build_zoom_controls(self) -> QWidget:
        """拡大表示上部の操作バー(戻る/倍率/ページ移動/各ドロワー/検索)を組み立てる。"""
        zoom_controls = QWidget()
        controls_layout = QHBoxLayout(zoom_controls)
        controls_layout.setContentsMargins(10, 10, 10, 10)

        self._zoom_back_btn = QPushButton("戻る")
        self._zoom_back_btn.clicked.connect(self._exit_zoom_view)
        controls_layout.addWidget(self._zoom_back_btn)

        self._zoom_out_btn = QPushButton("-")
        self._zoom_out_btn.clicked.connect(self._on_zoom_out)
        controls_layout.addWidget(self._zoom_out_btn)

        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.clicked.connect(self._on_zoom_in)
        controls_layout.addWidget(self._zoom_in_btn)

        # 「100%」ボタン。クリックすると倍率プリセットのドロップダウンが開く。
        # ボタン表示は現在の倍率を反映する。
        self._zoom_reset_btn = QToolButton()
        self._zoom_reset_btn.setObjectName("zoomPreset")
        self._zoom_reset_btn.setText("100%")
        self._zoom_reset_btn.setToolTip("倍率を選択")
        self._zoom_reset_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        zoom_preset_menu = QMenu(self._zoom_reset_btn)
        for preset in self.ZOOM_PRESETS:
            action = zoom_preset_menu.addAction(f"{preset}%")
            action.triggered.connect(
                lambda _checked=False, p=preset: self._set_zoom_percent(p)
            )
        self._zoom_reset_btn.setMenu(zoom_preset_menu)
        controls_layout.addWidget(self._zoom_reset_btn)

        # ページ移動(◁ ▷)
        self._zoom_prev_btn = QToolButton()
        self._zoom_prev_btn.setObjectName("zoomNav")
        self._zoom_prev_btn.setArrowType(Qt.ArrowType.LeftArrow)
        self._zoom_prev_btn.setToolTip("前のページ")
        self._zoom_prev_btn.setFixedWidth(40)
        self._zoom_prev_btn.clicked.connect(self._on_zoom_prev_page)
        controls_layout.addWidget(self._zoom_prev_btn)

        self._zoom_next_btn = QToolButton()
        self._zoom_next_btn.setObjectName("zoomNav")
        self._zoom_next_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._zoom_next_btn.setToolTip("次のページ")
        self._zoom_next_btn.setFixedWidth(40)
        self._zoom_next_btn.clicked.connect(self._on_zoom_next_page)
        controls_layout.addWidget(self._zoom_next_btn)

        # アノテーション(付箋)/しおり ドロワー開閉(右端の内蔵トグルから移設)
        self._zoom_object_btn = QPushButton("アノテーション")
        self._zoom_object_btn.setCheckable(True)
        self._zoom_object_btn.setToolTip("付箋編集")
        self._zoom_object_btn.clicked.connect(self._toggle_zoom_annotation_drawer)
        controls_layout.addWidget(self._zoom_object_btn)

        self._zoom_bookmark_btn = QPushButton("しおり")
        self._zoom_bookmark_btn.setCheckable(True)
        self._zoom_bookmark_btn.setToolTip("しおり編集")
        self._zoom_bookmark_btn.clicked.connect(self._toggle_bookmarks_drawer)
        controls_layout.addWidget(self._zoom_bookmark_btn)

        # 見開き表示(2ページを左右に並べて閲覧)。閲覧専用で編集・文字選択は不可。
        self._zoom_spread_btn = QPushButton("見開き表示")
        self._zoom_spread_btn.setCheckable(True)
        self._zoom_spread_btn.setToolTip("見開き表示（閲覧のみ）")
        self._zoom_spread_btn.clicked.connect(self._toggle_zoom_spread_view)
        controls_layout.addWidget(self._zoom_spread_btn)

        # PDF 内テキスト検索 (Ctrl+F と同じ)
        self._zoom_search_btn = QPushButton("検索")
        self._zoom_search_btn.setToolTip("PDF 内のテキストを検索 (Ctrl+F)")
        self._zoom_search_btn.clicked.connect(self._on_open_search)
        controls_layout.addWidget(self._zoom_search_btn)

        controls_layout.addStretch()

        self._zoom_page_label = QLabel("")
        controls_layout.addWidget(self._zoom_page_label)

        return zoom_controls

    def _build_zoom_canvas(self) -> QScrollArea:
        """拡大表示キャンバス(ZoomPageWidget)とそのシグナル配線を組み立てる。"""
        self._zoom_scroll = QScrollArea()
        self._zoom_scroll.setWidgetResizable(True)
        self._zoom_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_label = ZoomPageWidget()
        self._zoom_label.wheel_zoom.connect(self._on_zoom_wheel)
        self._zoom_label.link_clicked.connect(self._on_zoom_link_clicked)
        self._zoom_label.annotation_selected.connect(self._on_zoom_annotation_selected)
        self._zoom_label.annotation_geometry_changed.connect(self._on_zoom_annotation_geometry_changed)
        self._zoom_label.shape_geometry_changed_with_vertices.connect(
            self._on_zoom_shape_geometry_changed_with_vertices
        )
        self._zoom_label.callout_target_changed.connect(self._on_zoom_callout_target_changed)
        self._zoom_label.annotation_create_requested.connect(self._on_zoom_annotation_create_requested)
        self._zoom_label.shape_create_requested.connect(self._on_zoom_shape_create_requested)
        self._zoom_label.note_create_requested.connect(self._on_note_create_requested)
        self._zoom_label.callout_create_requested.connect(self._on_callout_create_requested)
        self._zoom_label.annotation_edit_requested.connect(self._on_zoom_annotation_edit_requested)
        self._zoom_label.annotation_text_committed.connect(self._on_zoom_annotation_text_committed)
        self._zoom_label.annotation_text_edit_cancelled.connect(self._on_zoom_annotation_text_edit_cancelled)
        self._zoom_label.annotation_delete_requested.connect(self._delete_selected_zoom_annotation)
        self._zoom_label.annotation_copy_requested.connect(self._on_zoom_annotation_copy_requested)
        self._zoom_label.annotation_paste_requested.connect(self._on_zoom_annotation_paste_requested)
        self._zoom_label.annotation_paste_placement_requested.connect(self._on_zoom_annotation_paste_placement_requested)
        self._zoom_label.annotation_duplicate_requested.connect(self._on_zoom_annotation_duplicate_requested)
        self._zoom_label.scroll_requested.connect(self._on_zoom_scroll_requested)
        self._zoom_label.zoom_region_requested.connect(self._on_zoom_region_requested)
        self._zoom_scroll.setWidget(self._zoom_label)
        return self._zoom_scroll

    def _build_annotation_drawer(self) -> QFrame:
        """付箋編集ドロワー(作成ツール群+プロパティフォーム)を組み立てる。"""
        self._zoom_annotation_drawer = QFrame()
        self._zoom_annotation_drawer.setObjectName("annotationDrawer")
        self._zoom_annotation_drawer.setFrameShape(QFrame.Shape.StyledPanel)
        drawer_layout = QHBoxLayout(self._zoom_annotation_drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(0)

        self._zoom_annotation_panel = QWidget()
        panel_layout = QVBoxLayout(self._zoom_annotation_panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        title = QLabel("付箋")
        panel_layout.addWidget(title)

        self._build_annotation_actions(panel_layout)
        self._build_shape_tools(panel_layout)
        self._build_markup_tools(panel_layout)
        self._build_note_tools(panel_layout)
        self._build_annotation_form(panel_layout)
        self._build_shape_option_rows(panel_layout)

        # FreeText-only widgets container references for visibility toggling
        self._zoom_freetext_only_widgets: list[QWidget] = []

        # 現在ページの付箋一覧（B）。クリックで該当付箋を選択。
        self._zoom_note_list_label = QLabel("このページの付箋")
        panel_layout.addWidget(self._zoom_note_list_label)
        self._zoom_note_list = QListWidget()
        self._zoom_note_list.setMaximumHeight(140)
        self._zoom_note_list.itemClicked.connect(self._on_note_list_item_clicked)
        panel_layout.addWidget(self._zoom_note_list)

        panel_layout.addStretch()
        drawer_layout.addWidget(self._zoom_annotation_panel)
        return self._zoom_annotation_drawer

    def _build_annotation_actions(self, panel_layout: QVBoxLayout) -> None:
        """付箋の新規/削除と重なり順操作のボタン列を組み立てる。"""
        action_row = QHBoxLayout()
        self._zoom_annotation_new_btn = QPushButton("新規")
        self._zoom_annotation_new_btn.setCheckable(True)
        self._zoom_annotation_new_btn.clicked.connect(self._on_zoom_annotation_new_clicked)
        action_row.addWidget(self._zoom_annotation_new_btn)

        self._zoom_annotation_delete_btn = QPushButton("削除")
        self._zoom_annotation_delete_btn.clicked.connect(self._delete_selected_zoom_annotation)
        action_row.addWidget(self._zoom_annotation_delete_btn)
        panel_layout.addLayout(action_row)

        order_row = QHBoxLayout()
        self._zoom_annotation_order_back_btn = QPushButton("最背面")
        self._zoom_annotation_order_back_btn.setToolTip("最背面へ移動")
        self._zoom_annotation_order_back_btn.setEnabled(False)
        self._zoom_annotation_order_back_btn.clicked.connect(
            lambda: self._reorder_selected_zoom_annotation("back")
        )
        order_row.addWidget(self._zoom_annotation_order_back_btn)

        self._zoom_annotation_order_backward_btn = QPushButton("背面へ")
        self._zoom_annotation_order_backward_btn.setToolTip("1つ背面へ移動")
        self._zoom_annotation_order_backward_btn.setEnabled(False)
        self._zoom_annotation_order_backward_btn.clicked.connect(
            lambda: self._reorder_selected_zoom_annotation("backward")
        )
        order_row.addWidget(self._zoom_annotation_order_backward_btn)

        self._zoom_annotation_order_forward_btn = QPushButton("前面へ")
        self._zoom_annotation_order_forward_btn.setToolTip("1つ前面へ移動")
        self._zoom_annotation_order_forward_btn.setEnabled(False)
        self._zoom_annotation_order_forward_btn.clicked.connect(
            lambda: self._reorder_selected_zoom_annotation("forward")
        )
        order_row.addWidget(self._zoom_annotation_order_forward_btn)

        self._zoom_annotation_order_front_btn = QPushButton("最前面")
        self._zoom_annotation_order_front_btn.setToolTip("最前面へ移動")
        self._zoom_annotation_order_front_btn.setEnabled(False)
        self._zoom_annotation_order_front_btn.clicked.connect(
            lambda: self._reorder_selected_zoom_annotation("front")
        )
        order_row.addWidget(self._zoom_annotation_order_front_btn)
        panel_layout.addLayout(order_row)

    def _build_shape_tools(self, panel_layout: QVBoxLayout) -> None:
        """図形作成ボタン列を組み立てる。"""
        shape_label = QLabel("図形")
        panel_layout.addWidget(shape_label)
        shape_row = QHBoxLayout()
        self._shape_buttons: dict[ShapeType, QToolButton] = {}
        shape_defs = [
            ("―", ShapeType.LINE, "線"),
            ("△", ShapeType.TRIANGLE, "三角形"),
            ("○", ShapeType.ELLIPSE, "楕円"),
            ("□", ShapeType.RECTANGLE, "四角形"),
            ("[ ]", ShapeType.BRACKET, "括弧"),
        ]
        for text, shape_type, tooltip in shape_defs:
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setCheckable(True)
            btn.setMinimumWidth(40)
            btn.clicked.connect(lambda checked, st=shape_type: self._on_shape_btn_clicked(st, checked))
            shape_row.addWidget(btn)
            self._shape_buttons[shape_type] = btn
        panel_layout.addLayout(shape_row)

    def _build_markup_tools(self, panel_layout: QVBoxLayout) -> None:
        """文字装飾(ハイライト/下線/取り消し線)ボタン列を組み立てる。"""
        markup_label = QLabel("文字装飾")
        panel_layout.addWidget(markup_label)
        markup_row = QHBoxLayout()
        self._markup_buttons: dict[MarkupType, QToolButton] = {}
        markup_defs = [
            ("ﾏｰｶｰ", MarkupType.HIGHLIGHT, "ハイライト（テキスト選択後にクリック）"),
            ("U", MarkupType.UNDERLINE, "下線（テキスト選択後にクリック）"),
            ("S", MarkupType.STRIKEOUT, "取り消し線（テキスト選択後にクリック）"),
        ]
        for text, markup_type, tooltip in markup_defs:
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setMinimumWidth(48)
            btn.clicked.connect(lambda _checked=False, mt=markup_type: self._on_markup_btn_clicked(mt))
            markup_row.addWidget(btn)
            self._markup_buttons[markup_type] = btn
        self._zoom_markup_color_btn = QPushButton()
        self._zoom_markup_color_btn.setToolTip("マークアップの色")
        self._zoom_markup_color_btn.clicked.connect(self._pick_markup_color)
        markup_row.addWidget(self._zoom_markup_color_btn)
        panel_layout.addLayout(markup_row)
        self._set_color_button_preview(self._zoom_markup_color_btn, self._zoom_markup_color)

    def _build_note_tools(self, panel_layout: QVBoxLayout) -> None:
        """付箋(コメント)/校正コールアウトのツール列と本文エディタを組み立てる。"""
        note_label = QLabel("付箋（コメント）")
        panel_layout.addWidget(note_label)
        note_row = QHBoxLayout()
        self._zoom_note_btn = QToolButton()
        self._zoom_note_btn.setText("ノート")
        self._zoom_note_btn.setCheckable(True)
        self._zoom_note_btn.setToolTip("付箋を追加（クリックで配置）")
        self._zoom_note_btn.clicked.connect(self._on_note_btn_clicked)
        note_row.addWidget(self._zoom_note_btn)
        self._zoom_note_color_btn = QPushButton()
        self._zoom_note_color_btn.setToolTip("付箋の色")
        self._zoom_note_color_btn.clicked.connect(self._pick_note_color)
        note_row.addWidget(self._zoom_note_color_btn)
        self._zoom_callout_btn = QToolButton()
        self._zoom_callout_btn.setText("校正")
        self._zoom_callout_btn.setCheckable(True)
        self._zoom_callout_btn.setToolTip("校正コールアウトを追加（挿入位置をクリック）")
        self._zoom_callout_btn.clicked.connect(self._on_callout_btn_clicked)
        note_row.addWidget(self._zoom_callout_btn)
        note_row.addStretch(1)
        panel_layout.addLayout(note_row)
        self._set_color_button_preview(self._zoom_note_color_btn, self._zoom_note_color)

        # 付箋本文エディタ（付箋選択時のみ表示）
        self._zoom_note_editor = NoteContentEdit()
        self._zoom_note_editor.setPlaceholderText("コメントを入力")
        self._zoom_note_editor.setFixedHeight(80)
        self._zoom_note_editor.commit_requested.connect(self._commit_note_editor_if_dirty)
        panel_layout.addWidget(self._zoom_note_editor)
        self._zoom_note_editor.hide()

    def _build_annotation_form(self, panel_layout: QVBoxLayout) -> None:
        """サイズ/文字サイズ/線幅/透明度/色のプロパティフォームを組み立てる。"""
        form = QFormLayout()
        self._zoom_annotation_width_spin = QSpinBox()
        self._zoom_annotation_width_spin.setRange(1, 5000)
        self._zoom_annotation_width_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        self._zoom_annotation_width_label = QLabel("幅")
        form.addRow(self._zoom_annotation_width_label, self._zoom_annotation_width_spin)

        self._zoom_annotation_height_spin = QSpinBox()
        self._zoom_annotation_height_spin.setRange(1, 5000)
        self._zoom_annotation_height_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        self._zoom_annotation_height_label = QLabel("高さ")
        form.addRow(self._zoom_annotation_height_label, self._zoom_annotation_height_spin)

        self._zoom_annotation_fontsize_spin = QSpinBox()
        self._zoom_annotation_fontsize_spin.setRange(6, 400)
        self._zoom_annotation_fontsize_spin.valueChanged.connect(self._on_zoom_annotation_form_value_changed)
        self._zoom_annotation_fontsize_label = QLabel("文字サイズ")
        form.addRow(self._zoom_annotation_fontsize_label, self._zoom_annotation_fontsize_spin)

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
        self._zoom_annotation_opacity_slider.setValue(100)
        self._zoom_annotation_opacity_slider.valueChanged.connect(self._on_zoom_annotation_opacity_changed)
        opacity_layout.addWidget(self._zoom_annotation_opacity_slider, 1)
        self._zoom_annotation_opacity_label = QLabel("100%")
        opacity_layout.addWidget(self._zoom_annotation_opacity_label)
        panel_layout.addWidget(opacity_row)

        self._zoom_annotation_text_color_btn = QPushButton()
        self._zoom_annotation_text_color_btn.clicked.connect(lambda: self._pick_zoom_annotation_color("text"))
        self._zoom_annotation_text_color_row = self._build_labeled_color_row("文字色", self._zoom_annotation_text_color_btn)
        panel_layout.addWidget(self._zoom_annotation_text_color_row)

        self._zoom_annotation_fill_color_btn = QPushButton()
        self._zoom_annotation_fill_color_btn.clicked.connect(lambda: self._pick_zoom_annotation_color("fill"))
        self._zoom_annotation_fill_color_clear_btn = QPushButton("透明")
        self._zoom_annotation_fill_color_clear_btn.clicked.connect(
            lambda: self._clear_zoom_annotation_color("fill")
        )
        panel_layout.addWidget(
            self._build_labeled_color_row(
                "背景色",
                self._zoom_annotation_fill_color_btn,
                clear_button=self._zoom_annotation_fill_color_clear_btn,
            )
        )

        self._zoom_annotation_border_color_btn = QPushButton()
        self._zoom_annotation_border_color_btn.clicked.connect(lambda: self._pick_zoom_annotation_color("border"))
        self._zoom_annotation_border_color_clear_btn = QPushButton("透明")
        self._zoom_annotation_border_color_clear_btn.clicked.connect(
            lambda: self._clear_zoom_annotation_color("border")
        )
        panel_layout.addWidget(
            self._build_labeled_color_row(
                "線色",
                self._zoom_annotation_border_color_btn,
                clear_button=self._zoom_annotation_border_color_clear_btn,
            )
        )

    def _build_shape_option_rows(self, panel_layout: QVBoxLayout) -> None:
        """図形種別ごとの追加オプション行(回転/線/括弧/三角形)を組み立てる。"""
        # Rotation
        self._zoom_shape_rotation_row = QWidget()
        rot_layout = QHBoxLayout(self._zoom_shape_rotation_row)
        rot_layout.setContentsMargins(0, 0, 0, 0)
        rot_layout.addWidget(QLabel("回転"))
        self._zoom_shape_rotation_spin = QSpinBox()
        self._zoom_shape_rotation_spin.setRange(0, 359)
        self._zoom_shape_rotation_spin.setWrapping(True)
        self._zoom_shape_rotation_spin.setSuffix("°")
        self._zoom_shape_rotation_spin.valueChanged.connect(self._on_zoom_shape_rotation_changed)
        rot_layout.addWidget(self._zoom_shape_rotation_spin)
        self._zoom_shape_rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_shape_rotation_slider.setRange(0, 359)
        self._zoom_shape_rotation_slider.valueChanged.connect(self._on_zoom_shape_rotation_slider_changed)
        rot_layout.addWidget(self._zoom_shape_rotation_slider, 1)
        panel_layout.addWidget(self._zoom_shape_rotation_row)
        self._zoom_shape_rotation_row.hide()

        # Line options: 矢印は選択時・作成時の両方で表示。始点/終点座標は既存図形の編集時のみ。
        self._zoom_shape_arrow_options = QWidget()
        arrow_layout = QHBoxLayout(self._zoom_shape_arrow_options)
        arrow_layout.setContentsMargins(0, 0, 0, 0)
        self._zoom_shape_arrow_start_cb = QCheckBox("始点矢印")
        self._zoom_shape_arrow_start_cb.stateChanged.connect(self._on_zoom_shape_option_changed)
        arrow_layout.addWidget(self._zoom_shape_arrow_start_cb)
        self._zoom_shape_arrow_end_cb = QCheckBox("終点矢印")
        self._zoom_shape_arrow_end_cb.stateChanged.connect(self._on_zoom_shape_option_changed)
        arrow_layout.addWidget(self._zoom_shape_arrow_end_cb)
        panel_layout.addWidget(self._zoom_shape_arrow_options)
        self._zoom_shape_arrow_options.hide()

        self._zoom_shape_line_endpoint_options = QWidget()
        endpoint_form = QFormLayout(self._zoom_shape_line_endpoint_options)
        endpoint_form.setContentsMargins(0, 0, 0, 0)

        self._zoom_shape_line_start_x_spin = QSpinBox()
        self._zoom_shape_line_start_x_spin.setRange(0, 100000)
        self._zoom_shape_line_start_x_spin.valueChanged.connect(self._on_zoom_shape_option_changed)
        self._zoom_shape_line_start_y_spin = QSpinBox()
        self._zoom_shape_line_start_y_spin.setRange(0, 100000)
        self._zoom_shape_line_start_y_spin.valueChanged.connect(self._on_zoom_shape_option_changed)
        start_row = QWidget()
        start_layout = QHBoxLayout(start_row)
        start_layout.setContentsMargins(0, 0, 0, 0)
        start_layout.addWidget(QLabel("X"))
        start_layout.addWidget(self._zoom_shape_line_start_x_spin, 1)
        start_layout.addWidget(QLabel("Y"))
        start_layout.addWidget(self._zoom_shape_line_start_y_spin, 1)
        endpoint_form.addRow("始点", start_row)

        self._zoom_shape_line_end_x_spin = QSpinBox()
        self._zoom_shape_line_end_x_spin.setRange(0, 100000)
        self._zoom_shape_line_end_x_spin.valueChanged.connect(self._on_zoom_shape_option_changed)
        self._zoom_shape_line_end_y_spin = QSpinBox()
        self._zoom_shape_line_end_y_spin.setRange(0, 100000)
        self._zoom_shape_line_end_y_spin.valueChanged.connect(self._on_zoom_shape_option_changed)
        end_row = QWidget()
        end_layout = QHBoxLayout(end_row)
        end_layout.setContentsMargins(0, 0, 0, 0)
        end_layout.addWidget(QLabel("X"))
        end_layout.addWidget(self._zoom_shape_line_end_x_spin, 1)
        end_layout.addWidget(QLabel("Y"))
        end_layout.addWidget(self._zoom_shape_line_end_y_spin, 1)
        endpoint_form.addRow("終点", end_row)

        panel_layout.addWidget(self._zoom_shape_line_endpoint_options)
        self._zoom_shape_line_endpoint_options.hide()

        # Bracket options
        self._zoom_shape_bracket_options = QWidget()
        bracket_opt_layout = QFormLayout(self._zoom_shape_bracket_options)
        bracket_opt_layout.setContentsMargins(0, 0, 0, 0)
        self._zoom_shape_bracket_style_combo = QComboBox()
        self._zoom_shape_bracket_style_combo.addItems(["角括弧 [ ]", "丸括弧 ( )", "波括弧 { }"])
        self._zoom_shape_bracket_style_combo.currentIndexChanged.connect(self._on_zoom_shape_option_changed)
        bracket_opt_layout.addRow("スタイル", self._zoom_shape_bracket_style_combo)
        self._zoom_shape_bracket_size_combo = QComboBox()
        self._zoom_shape_bracket_size_combo.addItems(["S", "M", "L"])
        self._zoom_shape_bracket_size_combo.setCurrentIndex(1)
        self._zoom_shape_bracket_size_combo.currentIndexChanged.connect(self._on_zoom_shape_option_changed)
        bracket_opt_layout.addRow("サイズ", self._zoom_shape_bracket_size_combo)
        self._zoom_shape_bracket_both_cb = QCheckBox("両側")
        self._zoom_shape_bracket_both_cb.stateChanged.connect(self._on_zoom_shape_option_changed)
        bracket_opt_layout.addRow("", self._zoom_shape_bracket_both_cb)
        panel_layout.addWidget(self._zoom_shape_bracket_options)
        self._zoom_shape_bracket_options.hide()

        # Triangle options: apex X position (relative 0-100% within bbox)
        self._zoom_shape_triangle_options = QWidget()
        triangle_opt_layout = QFormLayout(self._zoom_shape_triangle_options)
        triangle_opt_layout.setContentsMargins(0, 0, 0, 0)

        apex_x_row = QWidget()
        apex_x_layout = QHBoxLayout(apex_x_row)
        apex_x_layout.setContentsMargins(0, 0, 0, 0)
        self._zoom_shape_triangle_apex_x_spin = QSpinBox()
        self._zoom_shape_triangle_apex_x_spin.setRange(0, 100)
        self._zoom_shape_triangle_apex_x_spin.setSuffix("%")
        self._zoom_shape_triangle_apex_x_spin.valueChanged.connect(self._on_zoom_shape_triangle_apex_x_changed)
        apex_x_layout.addWidget(self._zoom_shape_triangle_apex_x_spin)
        self._zoom_shape_triangle_apex_x_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_shape_triangle_apex_x_slider.setRange(0, 100)
        self._zoom_shape_triangle_apex_x_slider.valueChanged.connect(self._on_zoom_shape_triangle_apex_x_slider_changed)
        apex_x_layout.addWidget(self._zoom_shape_triangle_apex_x_slider, 1)
        # 新規三角形のデフォルト頂点位置（中央）に合わせる
        with QSignalBlocker(self._zoom_shape_triangle_apex_x_spin):
            self._zoom_shape_triangle_apex_x_spin.setValue(50)
        with QSignalBlocker(self._zoom_shape_triangle_apex_x_slider):
            self._zoom_shape_triangle_apex_x_slider.setValue(50)
        triangle_opt_layout.addRow("上頂点 X", apex_x_row)

        panel_layout.addWidget(self._zoom_shape_triangle_options)
        self._zoom_shape_triangle_options.hide()

    def _build_bookmarks_panel(self) -> "BookmarksPanel":
        """しおり(アウトライン)編集ドロワーを組み立てる。"""
        self._bookmarks_panel = BookmarksPanel()
        self._bookmarks_panel.set_current_page_provider(
            lambda: (self._zoom_page_num or 0) + 1
        )
        self._bookmarks_panel.bookmarks_changed.connect(self._run_toc_update)
        self._bookmarks_panel.jump_requested.connect(self._jump_zoom_to_page)
        self._bookmarks_panel.note_jump_requested.connect(self._on_bookmark_note_jump)
        self._bookmarks_panel.open_changed.connect(self._on_bookmarks_drawer_open_changed)
        # 開閉トグルはツールバーの「しおり」ボタンへ移設したため内蔵トグルを隠す
        self._bookmarks_panel.use_external_toggle()
        return self._bookmarks_panel

    def _build_labeled_color_row(
        self,
        label_text: str,
        button: QPushButton,
        *,
        clear_button: QPushButton | None = None,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label_text))
        button.setMinimumWidth(120)
        layout.addWidget(button, 1)
        if clear_button is not None:
            clear_button.setFixedWidth(56)
            layout.addWidget(clear_button)
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
            button.setStyleSheet("background-color: transparent; color: #000000;")
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
            self._zoom_annotation_drawer.setFixedWidth(320 if self._zoom_annotation_open else 0)
        if getattr(self, "_zoom_object_btn", None) is not None:
            self._zoom_object_btn.setChecked(self._zoom_annotation_open)
        # 横幅を確保するため、付箋ドロワーを開いたらしおりドロワーを閉じる
        if self._zoom_annotation_open and getattr(self, "_bookmarks_panel", None):
            if self._bookmarks_panel.is_open:
                self._bookmarks_panel.set_open(False)

    def _toggle_zoom_annotation_drawer(self) -> None:
        self._set_zoom_annotation_drawer_open(not self._zoom_annotation_open)

    def _toggle_bookmarks_drawer(self) -> None:
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_open(not panel.is_open)

    def _toggle_zoom_spread_view(self) -> None:
        """見開き表示(閲覧専用)の ON/OFF を切り替える。"""
        enable = not self._zoom_spread_mode
        self._commit_inline_annotation_editor()
        if enable:
            # 閲覧専用モードへ入る前に、編集系の状態を全て終了させる。
            self._set_zoom_annotation_create_mode(False)
            self._set_selected_zoom_annotation(None)
            if self._zoom_annotation_open:
                self._set_zoom_annotation_drawer_open(False)
            # 左ページを偶数 index に正規化(LTR ペアリング: (0,1),(2,3),...)。
            if self._zoom_page_num is not None:
                self._zoom_page_num -= self._zoom_page_num % 2
        self._zoom_spread_mode = enable
        if self._zoom_label:
            self._zoom_label.set_view_only(enable)
        # 見開き中はアノテーション(付箋)ドロワーを無効化する。
        if getattr(self, "_zoom_object_btn", None) is not None:
            self._zoom_object_btn.setEnabled(not enable)
        if getattr(self, "_zoom_spread_btn", None) is not None:
            self._zoom_spread_btn.setChecked(enable)
        # 見開き中はしおりを閲覧/ジャンプ専用にし、回転・削除ボタンを無効化する。
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_read_only(enable)
        self._update_button_states()
        self._render_zoom()

    def _on_bookmarks_drawer_open_changed(self, is_open: bool) -> None:
        if getattr(self, "_zoom_bookmark_btn", None) is not None:
            self._zoom_bookmark_btn.setChecked(is_open)
        if is_open:
            # 付箋ドロワーと排他にする
            if self._zoom_annotation_open:
                self._set_zoom_annotation_drawer_open(False)
            self._reload_bookmarks_tree()

    def _reload_bookmarks_tree(self) -> None:
        """ディスク上のしおりでツリーを再構築する。ドロワーが閉じていれば何もしない。"""
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is None or not panel.is_open:
            return
        panel.load_entries(get_pdf_toc(self._pdf_path))
        self._reload_bookmark_notes()

    def _reload_bookmark_notes(self) -> None:
        """しおりパネルに文書全体の付箋一覧を反映する。"""
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is None or not panel.is_open:
            return
        notes = list_note_annots(self._pdf_path)
        entries = [
            (note.page_num + 1, note.xref, note.content or "")
            for note in notes
        ]
        panel.set_annotation_notes(entries)

    def _on_bookmark_note_jump(self, page_one_based: int, xref: int) -> None:
        """しおりパネルの付箋ノードクリックで、該当ページへ移動し付箋を選択する。"""
        self._jump_zoom_to_page(page_one_based)
        note = self._find_zoom_annotation(xref)
        if isinstance(note, NoteAnnotData):
            self._set_selected_zoom_annotation(note, open_drawer=False)

    def _jump_zoom_to_page(self, page_one_based: int) -> None:
        """しおりクリック時に該当ページ(1始まり)へズーム移動する。"""
        if self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            return
        target = max(0, min(page_one_based - 1, page_count - 1))
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
        self._zoom_page_num = target
        self._render_zoom()

    def _run_toc_update(self, new_entries: list[TocEntry], description: str) -> None:
        """しおり変更を PDF に保存し、Undo/Redo に登録する(TOC全体スナップショット方式)。"""
        old_entries = get_pdf_toc(self._pdf_path)
        state: dict[str, list[TocEntry]] = {"old": old_entries, "new": list(new_entries)}

        def do_update(reload_tree: bool = True) -> None:
            update_pdf_toc(self._pdf_path, state["new"])
            if reload_tree:
                self._reload_bookmarks_tree()

        def undo_update() -> None:
            update_pdf_toc(self._pdf_path, state["old"])
            self._reload_bookmarks_tree()

        try:
            # パネルは既に編集後の状態を表示しているため、ここでは再構築しない
            do_update(reload_tree=False)
            # ツリーは再構築しないが、付箋は最新のしおり配下へ即マージし直す
            # (新規しおり作成直後でも付箋が混ざった状態を反映するため)
            self._reload_bookmark_notes()
        except PdfWritePermissionError as error:
            # 保存できなかったので、表示をディスク上の真値に戻す
            self._reload_bookmarks_tree()
            self._handle_pdf_write_permission_denied(error)
            return
        self._undo_manager.add_action(UndoAction(
            description=description,
            undo_func=undo_update,
            redo_func=lambda: do_update(True),
        ))
        self._update_button_states()

    @property
    def _note_create_mode(self) -> bool:
        """テスト互換用の読み取り専用ミラー(状態は _create_mode が唯一の情報源)。"""
        return self._create_mode is CreateMode.NOTE

    @property
    def _callout_create_mode(self) -> bool:
        """テスト互換用の読み取り専用ミラー(状態は _create_mode が唯一の情報源)。"""
        return self._create_mode is CreateMode.CALLOUT

    def _activate_create_mode(
        self, mode: CreateMode, shape_type: ShapeType | None = None
    ) -> None:
        """新規作成モードを切り替える単一の状態機械。

        有効化するモード以外を必ず解除してから対象モードを有効化する。
        各ボタンハンドラにコピペされていた相互排他の前処理を一元化したもの。
        """
        if mode is not CreateMode.FREETEXT:
            self._set_zoom_annotation_create_mode(False)
        if mode is not CreateMode.SHAPE:
            self._set_shape_create_mode(None)
        if mode is not CreateMode.NOTE:
            self._set_note_create_mode(False)
        if mode is not CreateMode.CALLOUT:
            self._set_callout_create_mode(False)
        if mode is CreateMode.FREETEXT:
            self._set_zoom_annotation_create_mode(True)
        elif mode is CreateMode.SHAPE:
            self._set_shape_create_mode(shape_type)
        elif mode in (CreateMode.NOTE, CreateMode.CALLOUT):
            if self._selected_zoom_annotation is not None:
                self._set_selected_zoom_annotation(None)
            if mode is CreateMode.NOTE:
                self._set_note_create_mode(True)
            else:
                self._set_callout_create_mode(True)

    def _set_zoom_annotation_create_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled:
            self._create_mode = CreateMode.FREETEXT
        elif self._create_mode is CreateMode.FREETEXT:
            self._create_mode = CreateMode.NONE
        if self._zoom_label:
            if enabled:
                self._zoom_label.cancel_annotation_paste_mode()
            self._zoom_label.set_annotation_create_mode(enabled)
        if self._zoom_annotation_new_btn:
            with QSignalBlocker(self._zoom_annotation_new_btn):
                self._zoom_annotation_new_btn.setChecked(enabled)
            self._zoom_annotation_new_btn.setText("配置待ち" if enabled else "新規")
        self._set_zoom_create_mode("freetext" if enabled else None)

    def _set_zoom_create_mode(self, mode: ShapeType | str | None) -> None:
        """作成モードを更新し、変化した場合のみ右パネルへ反映する。

        作成モードへ入るとき（mode が None 以外）は、選択中の注釈があっても解除して
        新規作成のデフォルト値（線色など）を右パネルへ表示する。これにより「線を選択中に
        □を押す」と、その注釈の編集ではなく□の新規配置モードへ切り替わる。
        作成モードを抜けるとき（mode=None）に注釈が選択されていればその編集表示を優先し触らない。
        変化が無いのに再構築すると、選択時に余分なレイアウト更新が走るため差分時のみ実行する。
        """
        if self._zoom_create_mode == mode:
            return
        self._zoom_create_mode = mode
        if mode is not None or self._selected_zoom_annotation is None:
            self._set_selected_zoom_annotation(None)

    def _set_selected_zoom_annotation(
        self,
        annotation: AnyAnnotData | None,
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
            is_shape = isinstance(annotation, ShapeAnnotData)
            is_freetext = isinstance(annotation, FreeTextAnnotData)
            is_markup = isinstance(annotation, TextMarkupAnnotData)
            is_note = isinstance(annotation, NoteAnnotData)
            has_selection = annotation is not None

            # 注釈未選択でも作成モード中は新規作成のデフォルト値を編集できるようにする。
            # 注釈選択中はその注釈の編集を優先するため、作成モードは無視する。
            create_mode = self._zoom_create_mode if not has_selection else None
            create_shape_type = create_mode if isinstance(create_mode, ShapeType) else None
            # マークアップ・付箋選択時は専用の行で編集するため、
            # 汎用フォーム（幅/高さ/文字サイズ/各色など）は無効化する。
            controls_active = (has_selection and not is_markup and not is_note) or create_mode is not None

            # 表示・有効化の判定は「選択中の注釈」または「作成対象」に基づく
            panel_shape_type = annotation.shape_type if is_shape else create_shape_type
            panel_is_shape = is_shape or create_shape_type is not None
            is_line_shape = panel_shape_type == ShapeType.LINE

            # Toggle FreeText-only vs shape-only controls
            hide_text_only = panel_is_shape or is_markup or is_note
            self._zoom_annotation_fontsize_spin.setVisible(not hide_text_only)
            self._zoom_annotation_fontsize_label.setVisible(not hide_text_only)
            self._zoom_annotation_text_color_row.setVisible(not hide_text_only)
            # マークアップ用ツール行（色・タイプ）の選択状態を反映
            self._update_markup_controls(annotation if is_markup else None)
            # 付箋本文エディタの表示・内容
            self._update_note_editor(annotation if is_note else None)
            # 回転は既存図形の編集時のみ（新規は描画後に調整する）
            self._zoom_shape_rotation_row.setVisible(is_shape)
            # 矢印は LINE の選択時・作成時の両方で表示
            self._zoom_shape_arrow_options.setVisible(is_line_shape)
            # 始点/終点座標は既存 LINE の編集時のみ（作成前は未確定）
            self._zoom_shape_line_endpoint_options.setVisible(is_line_shape and is_shape)
            self._zoom_shape_bracket_options.setVisible(panel_shape_type == ShapeType.BRACKET)
            self._zoom_shape_triangle_options.setVisible(panel_shape_type == ShapeType.TRIANGLE)
            # 幅/高さは描画で確定するため作成モードでは隠す。LINE は始点/終点で編集するので隠す。
            # それ以外（選択あり／完全未選択）は従来通り表示する（未選択時は無効化のみ）。
            hide_size = create_mode is not None or (is_shape and annotation.shape_type == ShapeType.LINE)
            show_size = not hide_size and not is_markup and not is_note
            for sz_widget in (
                self._zoom_annotation_width_spin,
                self._zoom_annotation_width_label,
                self._zoom_annotation_height_spin,
                self._zoom_annotation_height_label,
            ):
                if sz_widget is not None:
                    sz_widget.setVisible(show_size)

            widgets = [
                self._zoom_annotation_width_spin,
                self._zoom_annotation_height_spin,
                self._zoom_annotation_opacity_slider,
                self._zoom_annotation_border_width_spin,
                self._zoom_annotation_fill_color_btn,
                self._zoom_annotation_fill_color_clear_btn,
                self._zoom_annotation_border_color_btn,
                self._zoom_annotation_border_color_clear_btn,
            ]
            if not panel_is_shape:
                widgets.extend([
                    self._zoom_annotation_fontsize_spin,
                    self._zoom_annotation_text_color_btn,
                ])
            for widget in widgets:
                if widget is not None:
                    widget.setEnabled(controls_active)
            # 削除・重ね順は対象注釈が必要なので選択時のみ有効化する
            if self._zoom_annotation_delete_btn:
                self._zoom_annotation_delete_btn.setEnabled(has_selection)
            for order_btn in (
                self._zoom_annotation_order_back_btn,
                self._zoom_annotation_order_backward_btn,
                self._zoom_annotation_order_forward_btn,
                self._zoom_annotation_order_front_btn,
            ):
                if order_btn is not None:
                    order_btn.setEnabled(has_selection)

            # 非選択（作成モード含む）時は最後の設定値（インスタンス状態）を保持し、
            # 次回の新規作成に引き継ぐ。スピン・スライダー・色は再設定しない。
            if is_shape:
                x0, y0, x1, y1 = annotation.rect
                is_line = annotation.shape_type == ShapeType.LINE
                size_min = 0 if is_line else 1
                if self._zoom_annotation_width_spin:
                    self._zoom_annotation_width_spin.setRange(size_min, 5000)
                    self._zoom_annotation_width_spin.setValue(max(size_min, round(x1 - x0)))
                if self._zoom_annotation_height_spin:
                    self._zoom_annotation_height_spin.setRange(size_min, 5000)
                    self._zoom_annotation_height_spin.setValue(max(size_min, round(y1 - y0)))
                if self._zoom_annotation_opacity_slider:
                    self._zoom_annotation_opacity_slider.setValue(round(annotation.opacity * 100))
                if self._zoom_annotation_opacity_label:
                    self._zoom_annotation_opacity_label.setText(f"{round(annotation.opacity * 100)}%")
                if self._zoom_annotation_border_width_spin:
                    self._zoom_annotation_border_width_spin.setValue(round(annotation.stroke_width))
                self._zoom_annotation_fill_color = annotation.fill_color
                self._zoom_annotation_border_color = annotation.stroke_color
                # Rotation
                with QSignalBlocker(self._zoom_shape_rotation_spin):
                    self._zoom_shape_rotation_spin.setValue(round(annotation.rotation))
                with QSignalBlocker(self._zoom_shape_rotation_slider):
                    self._zoom_shape_rotation_slider.setValue(round(annotation.rotation))
                # Line arrows + endpoints
                if annotation.shape_type == ShapeType.LINE:
                    with QSignalBlocker(self._zoom_shape_arrow_start_cb):
                        self._zoom_shape_arrow_start_cb.setChecked(annotation.arrow_start)
                    with QSignalBlocker(self._zoom_shape_arrow_end_cb):
                        self._zoom_shape_arrow_end_cb.setChecked(annotation.arrow_end)
                    (sx_pt, sy_pt), (ex_pt, ey_pt) = _line_endpoints_from_shape(annotation)
                    page_w, page_h = self._current_zoom_annotation_page_size()
                    max_x = max(1, int(round(max(page_w, sx_pt, ex_pt)))) or 100000
                    max_y = max(1, int(round(max(page_h, sy_pt, ey_pt)))) or 100000
                    for spin, value, max_val in (
                        (self._zoom_shape_line_start_x_spin, sx_pt, max_x),
                        (self._zoom_shape_line_start_y_spin, sy_pt, max_y),
                        (self._zoom_shape_line_end_x_spin, ex_pt, max_x),
                        (self._zoom_shape_line_end_y_spin, ey_pt, max_y),
                    ):
                        with QSignalBlocker(spin):
                            spin.setRange(0, max_val)
                            spin.setValue(int(round(value)))
                # Bracket options
                if annotation.shape_type == ShapeType.BRACKET:
                    style_idx = _BRACKET_STYLES.index(annotation.bracket_style) if annotation.bracket_style in _BRACKET_STYLES else 0
                    size_idx = _BRACKET_SIZES.index(annotation.bracket_size) if annotation.bracket_size in _BRACKET_SIZES else 1
                    with QSignalBlocker(self._zoom_shape_bracket_style_combo):
                        self._zoom_shape_bracket_style_combo.setCurrentIndex(style_idx)
                    with QSignalBlocker(self._zoom_shape_bracket_size_combo):
                        self._zoom_shape_bracket_size_combo.setCurrentIndex(size_idx)
                    with QSignalBlocker(self._zoom_shape_bracket_both_cb):
                        self._zoom_shape_bracket_both_cb.setChecked(annotation.bracket_both_sides)
                # Triangle apex
                if annotation.shape_type == ShapeType.TRIANGLE:
                    ax_pct = round(annotation.triangle_apex[0] * 100)
                    with QSignalBlocker(self._zoom_shape_triangle_apex_x_spin):
                        self._zoom_shape_triangle_apex_x_spin.setValue(ax_pct)
                    with QSignalBlocker(self._zoom_shape_triangle_apex_x_slider):
                        self._zoom_shape_triangle_apex_x_slider.setValue(ax_pct)
            elif is_freetext:
                # FreeText
                x0, y0, x1, y1 = annotation.rect
                if self._zoom_annotation_width_spin:
                    self._zoom_annotation_width_spin.setRange(1, 5000)
                    self._zoom_annotation_width_spin.setValue(max(1, round(x1 - x0)))
                if self._zoom_annotation_height_spin:
                    self._zoom_annotation_height_spin.setRange(1, 5000)
                    self._zoom_annotation_height_spin.setValue(max(1, round(y1 - y0)))
                if self._zoom_annotation_fontsize_spin:
                    self._zoom_annotation_fontsize_spin.setValue(max(6, round(annotation.fontsize)))
                if self._zoom_annotation_opacity_slider:
                    self._zoom_annotation_opacity_slider.setValue(round(annotation.opacity * 100))
                if self._zoom_annotation_opacity_label:
                    self._zoom_annotation_opacity_label.setText(f"{round(annotation.opacity * 100)}%")
                if self._zoom_annotation_border_width_spin:
                    self._zoom_annotation_border_width_spin.setValue(round(annotation.border_width))
                self._zoom_annotation_text_color = annotation.text_color
                self._zoom_annotation_fill_color = annotation.fill_color
                self._zoom_annotation_border_color = (
                    annotation.border_color if annotation.border_width > 0 else None
                )

            self._set_color_button_preview(self._zoom_annotation_text_color_btn, self._zoom_annotation_text_color)
            self._set_color_button_preview(
                self._zoom_annotation_fill_color_btn,
                self._zoom_annotation_fill_color,
                allow_none=True,
            )
            if is_shape and annotation is not None:
                border_preview = annotation.stroke_color
            elif is_freetext and annotation is not None and annotation.border_width <= 0:
                border_preview = None
            else:
                border_preview = self._zoom_annotation_border_color
            self._set_color_button_preview(
                self._zoom_annotation_border_color_btn,
                border_preview,
                allow_none=True,
            )
        finally:
            self._zoom_annotation_form_sync = False

    def _pick_color_via_dialog(
        self, current: tuple[float, float, float]
    ) -> tuple[float, float, float] | None:
        """共通のカラー選択ダイアログ。キャンセル時は None を返す。"""
        color = QColorDialog.getColor(self._rgb_tuple_to_qcolor(current), self, "色を選択")
        if not color.isValid():
            return None
        return self._qcolor_to_rgb_tuple(color)

    def _pick_zoom_annotation_color(self, kind: str) -> None:
        # 注釈選択中はその注釈へ適用、作成モード中は新規作成のデフォルト色を更新する
        if self._selected_zoom_annotation is None and self._zoom_create_mode is None:
            return
        if kind == "text":
            current = self._zoom_annotation_text_color
        elif kind == "fill":
            current = self._zoom_annotation_fill_color or (1.0, 1.0, 0.6)
        else:
            current = self._zoom_annotation_border_color or (0.0, 0.0, 0.0)
        rgb = self._pick_color_via_dialog(current)
        if rgb is None:
            return
        if kind == "text":
            self._zoom_annotation_text_color = rgb
            self._set_color_button_preview(self._zoom_annotation_text_color_btn, rgb)
        elif kind == "fill":
            self._zoom_annotation_fill_color = rgb
            self._set_color_button_preview(self._zoom_annotation_fill_color_btn, rgb, allow_none=True)
        else:
            self._zoom_annotation_border_color = rgb
            self._set_color_button_preview(self._zoom_annotation_border_color_btn, rgb, allow_none=True)
        self._apply_zoom_annotation_form()

    def _clear_zoom_annotation_color(self, kind: str) -> None:
        if self._selected_zoom_annotation is None and self._zoom_create_mode is None:
            return
        if kind == "fill":
            self._zoom_annotation_fill_color = None
            self._set_color_button_preview(self._zoom_annotation_fill_color_btn, None, allow_none=True)
        elif kind == "border":
            self._zoom_annotation_border_color = None
            self._set_color_button_preview(self._zoom_annotation_border_color_btn, None, allow_none=True)
        else:
            return
        self._apply_zoom_annotation_form()

    # --- Text markup (highlight / underline / strikeout) -----------------

    def _markup_default_opacity(self, markup_type: MarkupType) -> float:
        # ハイライトは半透明で文字が透ける。下線/取り消し線は不透明。
        return 0.4 if markup_type == MarkupType.HIGHLIGHT else 1.0

    def _update_markup_controls(self, selected: object) -> None:
        """マークアップツール行の色見本を選択中の注釈（あれば）に合わせる。"""
        if self._zoom_markup_color_btn is None:
            return
        if isinstance(selected, TextMarkupAnnotData):
            self._set_color_button_preview(self._zoom_markup_color_btn, selected.color)
        else:
            self._set_color_button_preview(self._zoom_markup_color_btn, self._zoom_markup_color)

    def _update_note_editor(self, selected: object) -> None:
        """付箋本文エディタの表示・内容・色見本を選択状態に合わせる。"""
        if self._zoom_note_editor is None:
            return
        if isinstance(selected, NoteAnnotData):
            self._editing_note_xref = selected.xref
            self._editing_note_original = selected.content
            with QSignalBlocker(self._zoom_note_editor):
                if self._zoom_note_editor.toPlainText() != selected.content:
                    self._zoom_note_editor.setPlainText(selected.content)
            self._zoom_note_editor.show()
            if self._zoom_note_color_btn is not None:
                self._set_color_button_preview(self._zoom_note_color_btn, selected.color)
        else:
            self._editing_note_xref = None
            self._editing_note_original = ""
            self._zoom_note_editor.hide()
            if self._zoom_note_color_btn is not None:
                self._set_color_button_preview(self._zoom_note_color_btn, self._zoom_note_color)

    def _on_markup_btn_clicked(self, markup_type: MarkupType) -> None:
        selected = self._selected_zoom_annotation
        if isinstance(selected, TextMarkupAnnotData):
            # 選択中のマークアップの種類を変更する。
            if selected.markup_type == markup_type:
                return
            new_annotation = dataclass_replace(
                selected,
                markup_type=markup_type,
                opacity=self._markup_default_opacity(markup_type),
                annotation_id="",
                subject="",  # 空にして種類変更後のメタデータを再生成させる
            )
            self._run_zoom_markup_replace(selected, new_annotation, f"Change to {markup_type.value}")
            return
        self._create_markup_from_selection(markup_type)

    def _create_markup_from_selection(self, markup_type: MarkupType) -> None:
        if self._zoom_page_num is None or self._zoom_label is None:
            return
        quads = self._zoom_label.selected_markup_quads()
        if not quads:
            self._flash_zoom_hint("マークアップするテキストを選択してください")
            return
        template = TextMarkupAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            quads=tuple(quads),
            markup_type=markup_type,
            color=self._zoom_markup_color,
            opacity=self._markup_default_opacity(markup_type),
        )
        # set_page() が再描画とテキスト選択の解除を行う。
        self._run_zoom_create(
            f"Create {markup_type.value}",
            lambda: create_markup_annot(self._pdf_path, template),
            lambda *a: delete_markup_annot(*a),
        )

    def _pick_markup_color(self) -> None:
        selected = self._selected_zoom_annotation
        is_markup = isinstance(selected, TextMarkupAnnotData)
        current = selected.color if is_markup else self._zoom_markup_color
        rgb = self._pick_color_via_dialog(current)
        if rgb is None:
            return
        self._zoom_markup_color = rgb
        if self._zoom_markup_color_btn is not None:
            self._set_color_button_preview(self._zoom_markup_color_btn, rgb)
        if is_markup:
            new_annotation = TextMarkupAnnotData(
                page_num=selected.page_num,
                xref=selected.xref,
                quads=selected.quads,
                markup_type=selected.markup_type,
                color=rgb,
                opacity=selected.opacity,
            )
            self._run_zoom_markup_replace(selected, new_annotation, "Update markup color")

    def _run_zoom_create(
        self,
        description: str,
        create_fn: Callable[[], "AnyAnnotData"],
        delete_fn: Callable[[str, int, int], bool],
        *,
        after_create: Callable[["AnyAnnotData"], None] | None = None,
    ) -> None:
        """注釈の新規作成をundo可能な操作として実行する(全注釈種共通の骨格)。

        redo時は同じ _AnnotRef ハンドルを作り直した新xrefへ張り替えることで、
        後続のundo操作が常に生きているxrefを参照できるようにする。
        create_fn/delete_fn はモジュールグローバルを遅延参照する lambda を渡すこと
        (テストの monkeypatch を効かせるため)。
        """
        ref: _AnnotRef | None = None

        def do_create() -> None:
            nonlocal ref
            created = create_fn()
            if ref is None:
                ref = self._register_annot_ref(created.page_num, created.xref)
            else:
                self._rebind_annot_ref(ref, created.page_num, created.xref)
            self._selected_zoom_annotation = created
            self._refresh_current_zoom_page(open_drawer=True)
            if after_create is not None:
                after_create(created)

        def undo_create() -> None:
            if ref is not None:
                delete_fn(self._pdf_path, ref.page_num, ref.xref)
                self._release_annot_ref(ref)
            self._selected_zoom_annotation = None
            self._refresh_current_zoom_page()

        self._push_undoable(description, do_create, undo_create)

    def _run_zoom_replace(
        self,
        old_annotation: "AnyAnnotData",
        new_annotation: "AnyAnnotData",
        description: str,
        replace_fn: Callable,
        *,
        commit_inline: bool = False,
        select_old_on_error: bool = False,
    ) -> None:
        """注釈の置換をundo可能な操作として実行する(全注釈種共通の骨格)。

        replace_fn はモジュールグローバルを遅延参照する lambda を渡すこと
        (テストの monkeypatch を効かせるため)。
        """
        if commit_inline and not self._zoom_annotation_text_commit_in_progress:
            self._commit_inline_annotation_editor()
        state: dict[str, AnyAnnotData | None] = {"old": old_annotation, "new": None}
        ref: _AnnotRef | None = None

        def do_replace() -> None:
            nonlocal ref
            if ref is None:
                ref = self._annot_ref_for(old_annotation.page_num, old_annotation.xref)
            saved = replace_fn(self._pdf_path, ref.page_num, ref.xref, new_annotation)
            self._rebind_annot_ref(ref, saved.page_num, saved.xref)
            state["new"] = saved
            self._selected_zoom_annotation = saved
            self._refresh_current_zoom_page(open_drawer=True)

        def undo_replace() -> None:
            saved = replace_fn(self._pdf_path, ref.page_num, ref.xref, state["old"])
            self._rebind_annot_ref(ref, saved.page_num, saved.xref)
            state["old"] = saved
            self._selected_zoom_annotation = saved
            self._refresh_current_zoom_page(open_drawer=True)

        kwargs = {}
        if select_old_on_error:
            kwargs["selected_annotation_on_error"] = state["old"]
        self._push_undoable(description, do_replace, undo_replace, **kwargs)

    def _run_zoom_markup_replace(
        self,
        old_annotation: TextMarkupAnnotData,
        new_annotation: TextMarkupAnnotData,
        description: str,
    ) -> None:
        self._run_zoom_replace(
            old_annotation, new_annotation, description,
            lambda *a: replace_markup_annot(*a),
        )

    def _flash_zoom_hint(self, message: str) -> None:
        """簡易ヒント表示（ステータスバー or ツールチップ）。"""
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(message, 3000)

    # --- Sticky note (comment) -------------------------------------------

    def _set_note_create_mode(self, enabled: bool) -> None:
        if enabled:
            self._create_mode = CreateMode.NOTE
        elif self._create_mode is CreateMode.NOTE:
            self._create_mode = CreateMode.NONE
        if self._zoom_note_btn is not None:
            with QSignalBlocker(self._zoom_note_btn):
                self._zoom_note_btn.setChecked(enabled)
        if self._zoom_label is not None:
            self._zoom_label.set_note_create_mode(enabled)

    def _on_note_btn_clicked(self, checked: bool) -> None:
        # 他の作成モード・選択を解除して付箋配置モードへ。
        self._activate_create_mode(CreateMode.NOTE if checked else CreateMode.NONE)

    def _on_note_create_requested(self, point: object) -> None:
        if self._zoom_page_num is None or not isinstance(point, tuple) or len(point) != 2:
            return
        template = NoteAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            point=(float(point[0]), float(point[1])),
            content="",
            color=self._zoom_note_color,
        )
        def focus_note_editor(_created: NoteAnnotData) -> None:
            # 配置後すぐ本文入力できるようフォーカス。
            if self._zoom_note_editor is not None:
                self._zoom_note_editor.setFocus()

        # 連続配置はせず、配置したら作成モードを抜ける。
        self._set_note_create_mode(False)
        self._run_zoom_create(
            "Create note",
            lambda: create_note_annot(self._pdf_path, template),
            lambda *a: delete_note_annot(*a),
            after_create=focus_note_editor,
        )

    def _pick_note_color(self) -> None:
        selected = self._selected_zoom_annotation
        is_note = isinstance(selected, NoteAnnotData)
        current = selected.color if is_note else self._zoom_note_color
        rgb = self._pick_color_via_dialog(current)
        if rgb is None:
            return
        self._zoom_note_color = rgb
        if self._zoom_note_color_btn is not None:
            self._set_color_button_preview(self._zoom_note_color_btn, rgb)
        if is_note:
            new_annotation = dataclass_replace(
                selected, color=rgb, annotation_id="", subject="",
            )
            self._run_zoom_note_replace(selected, new_annotation, "Update note color")

    def _commit_note_editor_if_dirty(self) -> None:
        if self._zoom_note_editor is None or self._editing_note_xref is None:
            return
        selected = self._selected_zoom_annotation
        if not isinstance(selected, NoteAnnotData) or selected.xref != self._editing_note_xref:
            return
        text = self._zoom_note_editor.toPlainText()
        if text == self._editing_note_original:
            return
        new_annotation = dataclass_replace(
            selected, content=text, annotation_id="", subject="",
        )
        self._editing_note_original = text
        self._run_zoom_note_replace(selected, new_annotation, "Edit note text")

    def _run_zoom_note_replace(
        self,
        old_annotation: NoteAnnotData,
        new_annotation: NoteAnnotData,
        description: str,
    ) -> None:
        self._run_zoom_replace(
            old_annotation, new_annotation, description,
            lambda *a: replace_note_annot(*a),
        )

    def _update_note_list_widget(self) -> None:
        """現在ページの付箋一覧（B）を更新する。"""
        if self._zoom_note_list is None:
            return
        notes = [a for a in self._zoom_annotations if isinstance(a, NoteAnnotData)]
        with QSignalBlocker(self._zoom_note_list):
            self._zoom_note_list.clear()
            for note in notes:
                text = (note.content or "").strip().replace("\n", " ") or "（空のコメント）"
                preview = text[:40] + ("…" if len(text) > 40 else "")
                item = QListWidgetItem(preview)
                item.setData(Qt.ItemDataRole.UserRole, note.xref)
                self._zoom_note_list.addItem(item)
                if (
                    self._selected_zoom_annotation is not None
                    and self._selected_zoom_annotation.xref == note.xref
                ):
                    self._zoom_note_list.setCurrentItem(item)
        has_notes = bool(notes)
        if self._zoom_note_list_label is not None:
            self._zoom_note_list_label.setVisible(has_notes)
        self._zoom_note_list.setVisible(has_notes)

    def _on_note_list_item_clicked(self, item: object) -> None:
        if item is None:
            return
        xref = item.data(Qt.ItemDataRole.UserRole)
        note = self._find_zoom_annotation(xref)
        if isinstance(note, NoteAnnotData):
            self._set_selected_zoom_annotation(note, open_drawer=True)

    # --- Proofreading callout --------------------------------------------

    def _set_callout_create_mode(self, enabled: bool) -> None:
        if enabled:
            self._create_mode = CreateMode.CALLOUT
        elif self._create_mode is CreateMode.CALLOUT:
            self._create_mode = CreateMode.NONE
        if self._zoom_callout_btn is not None:
            with QSignalBlocker(self._zoom_callout_btn):
                self._zoom_callout_btn.setChecked(enabled)
        if self._zoom_label is not None:
            self._zoom_label.set_callout_create_mode(enabled)

    def _on_callout_btn_clicked(self, checked: bool) -> None:
        self._activate_create_mode(CreateMode.CALLOUT if checked else CreateMode.NONE)

    def _on_callout_create_requested(self, target: object) -> None:
        if self._zoom_page_num is None or not isinstance(target, tuple) or len(target) != 2:
            return
        tx, ty = float(target[0]), float(target[1])
        page_w, page_h = self._current_zoom_annotation_page_size()
        # 本文ボックスは挿入位置の上に既定サイズで配置（はみ出しは内側へ寄せる）。
        box_w, box_h = 160.0, 46.0
        gap = 28.0
        bx0 = tx - box_w / 2.0
        by1 = ty - gap
        by0 = by1 - box_h
        if page_w > 0:
            bx0 = max(2.0, min(bx0, page_w - box_w - 2.0))
        if by0 < 2.0:
            # 上に置けない場合は挿入位置の下へ。
            by0 = ty + gap
            by1 = by0 + box_h
        text_rect = (bx0, by0, bx0 + box_w, by1)

        def begin_text_edit(created: FreeTextAnnotData) -> None:
            # 本文をすぐ入力できるよう FreeText インライン編集を開始。
            current = self._find_zoom_annotation(created.xref)
            if isinstance(current, FreeTextAnnotData) and self._zoom_label is not None:
                self._zoom_label.begin_annotation_text_edit(current)

        self._set_callout_create_mode(False)
        self._run_zoom_create(
            "Create callout",
            lambda: create_callout(
                self._pdf_path,
                self._zoom_page_num,
                text_rect=text_rect,
                target_point=(tx, ty),
                text="",
            ),
            lambda *a: delete_freetext_annot(*a),
            after_create=begin_text_edit,
        )

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
        # 校正コールアウトはターゲット（挿入位置）を固定し、本文ボックスの新枠に
        # 合わせて引き出し線の接続点を再計算する。
        callout_line: tuple[tuple[float, float], ...] = ()
        callout_target = base.callout_target
        if base.callout_line and callout_target is not None:
            box_attach = _callout_box_attach((x0, y0, x1, y1), callout_target)
            callout_line = (callout_target, box_attach)
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
            subject="",
            callout_line=callout_line,
            callout_target=callout_target,
        )

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._undo_btn = QPushButton("元に戻す")
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("やり直し")
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addSeparator()

        self._delete_btn = QPushButton("削除")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        # 名前変更(ファイル名 / PDF名)を 1 つのボタンに統合し、クリックで
        # ドロップダウンメニューを表示する(ファイル一覧モードと見た目・挙動を統一)。
        self._rename_btn = QPushButton("名前変更")
        self._rename_menu = QMenu(self._rename_btn)
        self._rename_file_action = self._rename_menu.addAction("ファイル名")
        self._rename_file_action.triggered.connect(self._on_rename)
        self._rename_title_action = self._rename_menu.addAction("PDF名")
        self._rename_title_action.triggered.connect(self._on_rename_pdf_title)
        self._rename_btn.setMenu(self._rename_menu)
        toolbar.addWidget(self._rename_btn)

        self._print_btn = QPushButton("印刷")
        self._print_btn.clicked.connect(self._on_print)
        toolbar.addWidget(self._print_btn)

        toolbar.addSeparator()

        self._rotate_btn = QPushButton("回転")
        self._rotate_btn.clicked.connect(self._on_rotate)
        toolbar.addWidget(self._rotate_btn)

        self._select_all_btn = QPushButton("すべて選択")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self._select_all_btn)

        toolbar.addSeparator()

        self._search_btn = QPushButton("検索")
        self._search_btn.setToolTip("PDF 内のテキストを検索 (Ctrl+F)")
        self._search_btn.clicked.connect(self._on_open_search)
        toolbar.addWidget(self._search_btn)

        self._update_button_states()

    def _setup_shortcuts(self) -> None:
        register_shortcuts(
            self,
            (
                (QKeySequence.StandardKey.Undo, self._on_undo),
                (QKeySequence.StandardKey.Redo, self._on_redo),
                (QKeySequence.StandardKey.Delete, self._on_delete),
                (QKeySequence(Qt.Key.Key_F2), self._on_rename),
                (QKeySequence("Shift+F2"), self._on_rename_pdf_title),
                (QKeySequence.StandardKey.SelectAll, self._on_select_all),
                (QKeySequence(Qt.Key.Key_R), self._on_rotate),
                (QKeySequence.StandardKey.Print, self._on_print),
                (QKeySequence.StandardKey.Find, self._on_open_search),
            ),
        )
        # 拡大モード中だけ有効化するページ送りショートカット。
        # グリッドモードでは無効化し、PageUp/PageDown/Home/End を
        # QScrollArea のデフォルトスクロールに譲る。
        self._zoom_nav_actions: list[QAction] = []
        for key, handler in (
            (Qt.Key.Key_PageUp, self._on_zoom_prev_page),
            (Qt.Key.Key_PageDown, self._on_zoom_next_page),
            (Qt.Key.Key_Home, self._on_zoom_first_page),
            (Qt.Key.Key_End, self._on_zoom_last_page),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(handler)
            action.setEnabled(False)
            self.addAction(action)
            self._zoom_nav_actions.append(action)

    def _find_zoom_annotation(self, xref: int | None) -> AnyAnnotData | None:
        if xref is None:
            return None
        for annot in self._zoom_annotations:
            if annot.xref == xref:
                return annot
        return None

    def _set_copied_zoom_annotation(self, annotation: AnyAnnotData | None) -> None:
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
            callout_line=annotation.callout_line,
            callout_target=annotation.callout_target,
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
            self._render_zoom()
            if open_drawer and self._selected_zoom_annotation is not None:
                self._set_zoom_annotation_drawer_open(True)
        # しおりドロワーが開いていれば付箋一覧も更新する。
        self._reload_bookmark_notes()

    def _on_zoom_annotation_selected(self, annotation: object) -> None:
        self._set_zoom_annotation_create_mode(False)
        self._set_shape_create_mode(None)
        self._set_note_create_mode(False)
        self._set_callout_create_mode(False)
        if isinstance(annotation, (FreeTextAnnotData, ShapeAnnotData, TextMarkupAnnotData, NoteAnnotData)):
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
        new_annotation = dataclass_replace(
            current,
            content=text,
            subject="",  # 空にして本文変更後のメタデータを再生成させる
            text_rotation=0,
            group_id="",
        )
        self._zoom_annotation_text_commit_in_progress = True
        try:
            self._run_zoom_annotation_replace(current, new_annotation, "Edit FreeText text")
        finally:
            self._zoom_annotation_text_commit_in_progress = False

    def _on_zoom_annotation_text_edit_cancelled(self) -> None:
        pass

    def _handle_pdf_write_permission_denied(
        self,
        error: PdfWritePermissionError,
        *,
        selected_annotation: FreeTextAnnotData | None = None,
    ) -> None:
        logger.warning("PDF write blocked while editing %s", error.pdf_path)
        logger.debug("PDF write blocked while editing %s", error.pdf_path, exc_info=True)
        if selected_annotation is not None:
            self._selected_zoom_annotation = selected_annotation
        self._refresh_current_zoom_page(
            open_drawer=selected_annotation is not None and self._zoom_annotation_open
        )
        pdf_name = os.path.basename(error.pdf_path or self._pdf_path)
        QMessageBox.warning(
            self,
            "PDFを編集できません",
            (
                "このPDFは他のアプリで使用中のため保存できません。\n\n"
                f"{pdf_name}\n\n"
                "Acrobat などで閉じてから、もう一度お試しください。"
            ),
        )

    def _push_undoable(
        self,
        description: str,
        do_func: Callable[[], None],
        undo_func: Callable[[], None],
        *,
        selected_annotation_on_error: FreeTextAnnotData | None = None,
    ) -> bool:
        """do_func を実行し、成功時のみ Undo/Redo 履歴に登録する。

        PdfWritePermissionError 時は警告ダイアログを表示して False を返す
        (履歴には積まない)。redo には do_func をそのまま使う。
        """
        try:
            do_func()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(
                error, selected_annotation=selected_annotation_on_error
            )
            return False
        self._undo_manager.add_action(UndoAction(
            description=description,
            undo_func=undo_func,
            redo_func=do_func,
        ))
        return True

    # --- Annotation xref handles ------------------------------------------
    # 注釈の移動・編集は delete+recreate で xref を回す。論理的に同じ注釈を指す
    # Undo/Redo クロージャは下の 3 ヘルパーで 1 個の _AnnotRef を共有し、置換のたびに
    # ハンドルだけを張り替えることで、貼り付け/作成/複製/削除の Undo が常に
    # 「いま生きている xref」を対象にできるようにする。

    def _register_annot_ref(self, page_num: int, xref: int) -> _AnnotRef:
        """新規作成した注釈のハンドルを登録して返す。"""
        ref = _AnnotRef(page_num, xref)
        self._annot_refs[xref] = ref
        return ref

    def _annot_ref_for(self, page_num: int, xref: int) -> _AnnotRef:
        """xref に対応する既存ハンドルを返す。無ければ新規登録して返す。

        既に他アクション(貼り付け等)が作ったハンドルがあれば同じオブジェクトを
        共有し、無ければ(ディスク読込の既存注釈など)その場で登録する。
        """
        return self._annot_refs.get(xref) or self._register_annot_ref(page_num, xref)

    def _rebind_annot_ref(self, ref: _AnnotRef, page_num: int, xref: int) -> None:
        """置換で xref が変わったとき、同じハンドルを新しい xref に張り替える。

        同一オブジェクトを更新するため、このハンドルを共有する全アクションへ
        新しい xref が伝播する。
        """
        self._annot_refs.pop(ref.xref, None)
        ref.page_num = page_num
        ref.xref = xref
        self._annot_refs[xref] = ref

    def _release_annot_ref(self, ref: _AnnotRef | None) -> None:
        """注釈が削除されたとき、ハンドルをレジストリから外す。

        ハンドルオブジェクト自体は他アクションがまだ握っている可能性があるため
        破棄せず、Redo/再作成時に _rebind_annot_ref で復帰できるようにしておく。
        """
        if ref is not None:
            self._annot_refs.pop(ref.xref, None)

    def _handle_file_operation_error(self, error: Exception, pdf_path: str, action: str) -> None:
        logger.warning("%s failed for %s", action, pdf_path)
        logger.debug("%s failed for %s", action, pdf_path, exc_info=True)
        pdf_name = os.path.basename(pdf_path)
        QMessageBox.warning(
            self,
            f"{action}できません",
            f"{action}に失敗しました。\n\n{pdf_name}\n\n{error}",
        )

    def _on_zoom_annotation_copy_requested(self, annotation: object) -> None:
        if isinstance(annotation, ShapeAnnotData):
            current = self._find_zoom_annotation(annotation.xref) or annotation
            self._set_copied_zoom_annotation(current)
            return
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

    def _copy_shape_for_placement(
        self,
        src: ShapeAnnotData,
        new_rect: tuple[float, float, float, float],
        vertices: tuple[tuple[float, float], ...] | None = None,
    ) -> ShapeAnnotData:
        """コピー/複製した図形を新しい枠位置に置くためのデータを作る。

        新規注釈として識別子(annotation_id/subject/group_id)は振り直すため空にし、
        括弧ペアの片割れとしての属性(both_sides/orientation)もリセットする。
        """
        return dataclass_replace(
            src,
            page_num=self._zoom_page_num,
            xref=0,
            rect=new_rect,
            bracket_both_sides=False,
            bracket_orientation="vertical",
            group_id="",
            annotation_id="",
            subject="",
            vertices=src.vertices if vertices is None else vertices,
        )

    def _copy_freetext_for_placement(
        self,
        src: FreeTextAnnotData,
        new_rect: tuple[float, float, float, float],
    ) -> FreeTextAnnotData:
        """コピー/複製した FreeText を新しい枠位置に置くためのデータを作る。

        校正コールアウトはボックスの移動量だけ挿入位置もずらして全体を平行移動する。
        新規注釈として識別子(annotation_id/subject/group_id)は振り直すため空にする。
        """
        callout_line: tuple[tuple[float, float], ...] = ()
        callout_target = None
        if src.callout_line and src.callout_target is not None:
            dx = new_rect[0] - src.rect[0]
            dy = new_rect[1] - src.rect[1]
            callout_target = (src.callout_target[0] + dx, src.callout_target[1] + dy)
            callout_line = (callout_target, _callout_box_attach(new_rect, callout_target))
        return dataclass_replace(
            src,
            page_num=self._zoom_page_num,
            xref=0,
            rect=new_rect,
            annotation_id="",
            subject="",
            text_rotation=0,
            group_id="",
            callout_line=callout_line,
            callout_target=callout_target,
        )

    def _on_zoom_annotation_paste_placement_requested(self, rect: object) -> None:
        if (
            self._copied_zoom_annotation is None
            or self._zoom_page_num is None
            or not isinstance(rect, tuple)
            or len(rect) != 4
        ):
            return
        new_rect = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))

        if isinstance(self._copied_zoom_annotation, ShapeAnnotData):
            paste_data = self._copy_shape_for_placement(self._copied_zoom_annotation, new_rect)
            self._run_zoom_create(
                f"Paste {paste_data.shape_type.value}",
                lambda: create_shape_annot(self._pdf_path, paste_data),
                lambda *a: delete_shape_annot(*a),
            )
            return

        paste_data_ft = self._copy_freetext_for_placement(self._copied_zoom_annotation, new_rect)
        self._run_zoom_create(
            "Paste FreeText",
            lambda: create_freetext_annot(self._pdf_path, paste_data_ft),
            lambda *a: delete_freetext_annot(*a),
        )

    def _on_zoom_annotation_duplicate_requested(
        self, annotation: object, rect: object, vertices: object
    ) -> None:
        if (
            self._zoom_page_num is None
            or not isinstance(rect, tuple)
            or len(rect) != 4
        ):
            return
        new_rect = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))

        if isinstance(annotation, ShapeAnnotData):
            new_vertices = annotation.vertices
            if isinstance(vertices, tuple):
                new_vertices = tuple(tuple(v) for v in vertices)
            dup_data = self._copy_shape_for_placement(annotation, new_rect, new_vertices)
            self._run_zoom_create(
                f"Duplicate {dup_data.shape_type.value}",
                lambda: create_shape_annot(self._pdf_path, dup_data),
                lambda *a: delete_shape_annot(*a),
            )
            return

        if not isinstance(annotation, FreeTextAnnotData):
            return
        dup_ft = self._copy_freetext_for_placement(annotation, new_rect)
        self._run_zoom_create(
            "Duplicate FreeText",
            lambda: create_freetext_annot(self._pdf_path, dup_ft),
            lambda *a: delete_freetext_annot(*a),
        )

    def _on_zoom_annotation_form_value_changed(self, _value: int) -> None:
        self._apply_zoom_annotation_form()

    def _on_zoom_annotation_opacity_changed(self, value: int) -> None:
        if self._zoom_annotation_opacity_label:
            self._zoom_annotation_opacity_label.setText(f"{int(value)}%")
        self._apply_zoom_annotation_form()

    # --- Shape methods ---

    def _on_shape_btn_clicked(self, shape_type: ShapeType, checked: bool) -> None:
        if not self._zoom_view or not self._zoom_view.isVisible() or self._zoom_page_num is None:
            self._set_shape_create_mode(None)
            return
        if checked:
            self._activate_create_mode(CreateMode.SHAPE, shape_type)
            self._set_zoom_annotation_drawer_open(True)
        else:
            self._set_shape_create_mode(None)

    def _set_shape_create_mode(self, shape_type: ShapeType | None) -> None:
        if shape_type is not None:
            self._create_mode = CreateMode.SHAPE
        elif self._create_mode is CreateMode.SHAPE:
            self._create_mode = CreateMode.NONE
        for st, btn in self._shape_buttons.items():
            with QSignalBlocker(btn):
                btn.setChecked(st == shape_type)
        if self._zoom_label:
            if shape_type is not None:
                self._zoom_label.cancel_annotation_paste_mode()
                self._zoom_label.set_annotation_create_mode(True, shape_type=shape_type)
            else:
                if self._zoom_label._annotation_create_shape_type is not None:
                    self._zoom_label.set_annotation_create_mode(False)
        self._set_zoom_create_mode(shape_type)

    def _on_zoom_shape_create_requested(self, shape_type: object, start: object, end: object) -> None:
        if (
            self._zoom_page_num is None
            or not isinstance(shape_type, ShapeType)
            or not isinstance(start, tuple)
            or len(start) != 2
            or not isinstance(end, tuple)
            or len(end) != 2
        ):
            return
        sx, sy = float(start[0]), float(start[1])
        ex, ey = float(end[0]), float(end[1])

        vertices: tuple[tuple[float, float], ...] = ()
        if shape_type == ShapeType.LINE:
            x0, x1 = (sx, ex) if sx <= ex else (ex, sx)
            y0, y1 = (sy, ey) if sy <= ey else (ey, sy)
            if max(x1 - x0, y1 - y0) < 1.0:
                return
            width = x1 - x0
            height = y1 - y0
            rx1 = (sx - x0) / width if width > 0 else 0.0
            ry1 = (sy - y0) / height if height > 0 else 0.5
            rx2 = (ex - x0) / width if width > 0 else 1.0
            ry2 = (ey - y0) / height if height > 0 else 0.5
            vertices = ((rx1, ry1), (rx2, ry2))
            rect_tuple = (x0, y0, x1, y1)
        else:
            x0, x1 = (sx, ex) if sx <= ex else (ex, sx)
            y0, y1 = (sy, ey) if sy <= ey else (ey, sy)
            if x1 - x0 < 1.0 or y1 - y0 < 1.0:
                return
            rect_tuple = (x0, y0, x1, y1)

        self._set_shape_create_mode(None)

        # For bracket with "both sides" checked, create a pair
        if shape_type == ShapeType.BRACKET and self._zoom_shape_bracket_both_cb.isChecked():
            self._create_bracket_pair(rect_tuple)
            return

        bracket_style = _BRACKET_STYLES[self._zoom_shape_bracket_style_combo.currentIndex()]
        bracket_size = _BRACKET_SIZES[self._zoom_shape_bracket_size_combo.currentIndex()]

        if shape_type == ShapeType.LINE:
            arrow_start = bool(self._zoom_shape_arrow_start_cb.isChecked()) if self._zoom_shape_arrow_start_cb else False
            arrow_end = bool(self._zoom_shape_arrow_end_cb.isChecked()) if self._zoom_shape_arrow_end_cb else True
        else:
            arrow_start = False
            arrow_end = False
        if shape_type == ShapeType.TRIANGLE and self._zoom_shape_triangle_apex_x_spin:
            triangle_apex = (float(self._zoom_shape_triangle_apex_x_spin.value()) / 100.0, 0.0)
        else:
            triangle_apex = (0.5, 0.0)
        template = ShapeAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            rect=rect_tuple,
            shape_type=shape_type,
            stroke_color=self._zoom_annotation_border_color or (0.0, 0.0, 0.0),
            fill_color=self._zoom_annotation_fill_color if shape_type not in (ShapeType.LINE, ShapeType.BRACKET) else None,
            stroke_width=max(1.0, float(self._zoom_annotation_border_width_spin.value())) if self._zoom_annotation_border_width_spin else 1.0,
            opacity=(float(self._zoom_annotation_opacity_slider.value()) / 100.0) if self._zoom_annotation_opacity_slider else 1.0,
            rotation=0.0,
            arrow_start=arrow_start,
            arrow_end=arrow_end,
            bracket_style=bracket_style,
            bracket_size=bracket_size,
            bracket_side="left",
            vertices=vertices,
            triangle_apex=triangle_apex,
        )
        self._run_zoom_create(
            f"Create {shape_type.value}",
            lambda: create_shape_annot(self._pdf_path, template),
            lambda *a: delete_shape_annot(*a),
        )

    def _create_bracket_pair(self, rect_tuple: tuple[float, float, float, float]) -> None:
        bracket_style = _BRACKET_STYLES[self._zoom_shape_bracket_style_combo.currentIndex()]
        bracket_size = _BRACKET_SIZES[self._zoom_shape_bracket_size_combo.currentIndex()]
        stroke_color = self._zoom_annotation_border_color or (0.0, 0.0, 0.0)
        stroke_width = max(1.0, float(self._zoom_annotation_border_width_spin.value())) if self._zoom_annotation_border_width_spin else 1.0
        opacity = (float(self._zoom_annotation_opacity_slider.value()) / 100.0) if self._zoom_annotation_opacity_slider else 1.0

        ref0: _AnnotRef | None = None
        ref1: _AnnotRef | None = None

        def do_create() -> None:
            nonlocal ref0, ref1
            pair = create_bracket_pair(
                self._pdf_path,
                rect_tuple,
                self._zoom_page_num,
                bracket_style=bracket_style,
                bracket_size=bracket_size,
                stroke_color=stroke_color,
                stroke_width=stroke_width,
                opacity=opacity,
            )
            if ref0 is None:
                ref0 = self._register_annot_ref(pair[0].page_num, pair[0].xref)
                ref1 = self._register_annot_ref(pair[1].page_num, pair[1].xref)
            else:
                self._rebind_annot_ref(ref0, pair[0].page_num, pair[0].xref)
                self._rebind_annot_ref(ref1, pair[1].page_num, pair[1].xref)
            self._selected_zoom_annotation = pair[0]
            self._refresh_current_zoom_page(open_drawer=True)

        def undo_create() -> None:
            for ref in (ref0, ref1):
                if ref is not None:
                    delete_shape_annot(self._pdf_path, ref.page_num, ref.xref)
                    self._release_annot_ref(ref)
            self._selected_zoom_annotation = None
            self._refresh_current_zoom_page()

        self._push_undoable("Create bracket pair", do_create, undo_create)

    def _run_zoom_shape_replace(
        self,
        old_annotation: ShapeAnnotData,
        new_annotation: ShapeAnnotData,
        description: str,
    ) -> None:
        self._run_zoom_replace(
            old_annotation, new_annotation, description,
            lambda *a: replace_shape_annot(*a),
            commit_inline=True,
        )

    def _shape_data_from_form(
        self,
        base: ShapeAnnotData,
        *,
        rect: tuple[float, float, float, float] | None = None,
    ) -> ShapeAnnotData:
        new_vertices = base.vertices
        if base.shape_type == ShapeType.LINE and rect is None:
            # LINE はフォームの始点・終点スピナーから幾何を再構築する
            sx = float(self._zoom_shape_line_start_x_spin.value())
            sy = float(self._zoom_shape_line_start_y_spin.value())
            ex = float(self._zoom_shape_line_end_x_spin.value())
            ey = float(self._zoom_shape_line_end_y_spin.value())
            x0 = min(sx, ex)
            x1 = max(sx, ex)
            y0 = min(sy, ey)
            y1 = max(sy, ey)
            if max(x1 - x0, y1 - y0) < 1.0:
                # 退化したら base のジオメトリを保持
                x0, y0, x1, y1 = base.rect
                new_vertices = base.vertices
            else:
                width = x1 - x0
                height = y1 - y0
                rx1 = (sx - x0) / width if width > 0 else 0.0
                ry1 = (sy - y0) / height if height > 0 else 0.5
                rx2 = (ex - x0) / width if width > 0 else 1.0
                ry2 = (ey - y0) / height if height > 0 else 0.5
                new_vertices = ((rx1, ry1), (rx2, ry2))
        else:
            x0, y0, x1, y1 = rect or base.rect
            if rect is None and self._zoom_annotation_width_spin and self._zoom_annotation_height_spin:
                width = float(self._zoom_annotation_width_spin.value())
                height = float(self._zoom_annotation_height_spin.value())
                if base.shape_type == ShapeType.LINE and width <= 0 and height <= 0:
                    # Both-zero line is disallowed — keep previous rect size
                    width = float(base.rect[2] - base.rect[0])
                    height = float(base.rect[3] - base.rect[1])
                x1 = x0 + width
                y1 = y0 + height
        stroke_width = float(self._zoom_annotation_border_width_spin.value()) if self._zoom_annotation_border_width_spin else base.stroke_width
        stroke_color = self._zoom_annotation_border_color if stroke_width > 0 else base.stroke_color
        rotation = float(self._zoom_shape_rotation_spin.value())
        bracket_style = _BRACKET_STYLES[self._zoom_shape_bracket_style_combo.currentIndex()]
        bracket_size = _BRACKET_SIZES[self._zoom_shape_bracket_size_combo.currentIndex()]
        if base.shape_type == ShapeType.TRIANGLE:
            apex_x = float(self._zoom_shape_triangle_apex_x_spin.value()) / 100.0
            triangle_apex = (apex_x, 0.0)
        else:
            triangle_apex = base.triangle_apex

        return ShapeAnnotData(
            page_num=base.page_num,
            xref=base.xref,
            rect=(x0, y0, x1, y1),
            shape_type=base.shape_type,
            stroke_color=stroke_color,
            fill_color=self._zoom_annotation_fill_color,
            stroke_width=stroke_width,
            opacity=(float(self._zoom_annotation_opacity_slider.value()) / 100.0) if self._zoom_annotation_opacity_slider else base.opacity,
            rotation=rotation,
            arrow_start=self._zoom_shape_arrow_start_cb.isChecked(),
            arrow_end=self._zoom_shape_arrow_end_cb.isChecked(),
            bracket_style=bracket_style,
            bracket_size=bracket_size,
            bracket_both_sides=base.bracket_both_sides,
            bracket_side=base.bracket_side,
            group_id=base.group_id,
            vertices=new_vertices,
            triangle_apex=triangle_apex,
            annotation_id=base.annotation_id,
            subject="",
        )

    def _on_zoom_shape_rotation_changed(self, value: int) -> None:
        with QSignalBlocker(self._zoom_shape_rotation_slider):
            self._zoom_shape_rotation_slider.setValue(value)
        self._apply_zoom_annotation_form()

    def _on_zoom_shape_rotation_slider_changed(self, value: int) -> None:
        with QSignalBlocker(self._zoom_shape_rotation_spin):
            self._zoom_shape_rotation_spin.setValue(value)
        self._apply_zoom_annotation_form()

    def _on_zoom_shape_triangle_apex_x_changed(self, value: int) -> None:
        with QSignalBlocker(self._zoom_shape_triangle_apex_x_slider):
            self._zoom_shape_triangle_apex_x_slider.setValue(value)
        self._apply_zoom_annotation_form()

    def _on_zoom_shape_triangle_apex_x_slider_changed(self, value: int) -> None:
        with QSignalBlocker(self._zoom_shape_triangle_apex_x_spin):
            self._zoom_shape_triangle_apex_x_spin.setValue(value)
        self._apply_zoom_annotation_form()

    def _on_zoom_shape_option_changed(self, _value=None) -> None:
        self._apply_zoom_annotation_form()

    def _on_zoom_annotation_new_clicked(self, checked: bool) -> None:
        if not self._zoom_view or not self._zoom_view.isVisible() or self._zoom_page_num is None:
            self._set_zoom_annotation_create_mode(False)
            return
        self._set_zoom_annotation_drawer_open(True)
        self._activate_create_mode(CreateMode.FREETEXT if checked else CreateMode.NONE)

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
        fontsize = (
            float(self._zoom_annotation_fontsize_spin.value())
            if self._zoom_annotation_fontsize_spin and self._zoom_annotation_fontsize_spin.value() > 0
            else 14.0
        )
        border_width = (
            float(self._zoom_annotation_border_width_spin.value())
            if self._zoom_annotation_border_width_spin and self._zoom_annotation_border_width_spin.value() > 0
            else 1.0
        )
        opacity = (
            float(self._zoom_annotation_opacity_slider.value()) / 100.0
            if self._zoom_annotation_opacity_slider
            else 1.0
        )
        template = FreeTextAnnotData(
            page_num=self._zoom_page_num,
            xref=0,
            rect=rect_tuple,
            content="",
            fontsize=fontsize,
            text_color=self._zoom_annotation_text_color,
            fill_color=self._zoom_annotation_fill_color,
            border_color=self._zoom_annotation_border_color,
            border_width=border_width,
            opacity=opacity,
        )
        def begin_text_edit(created: FreeTextAnnotData) -> None:
            if self._zoom_label:
                current = self._find_zoom_annotation(created.xref) or created
                self._zoom_label.begin_annotation_text_edit(current)

        self._run_zoom_create(
            "Create FreeText",
            lambda: create_freetext_annot(self._pdf_path, template),
            lambda *a: delete_freetext_annot(*a),
            after_create=begin_text_edit,
        )

    def _run_zoom_annotation_replace(
        self,
        old_annotation: FreeTextAnnotData,
        new_annotation: FreeTextAnnotData,
        description: str,
    ) -> None:
        self._run_zoom_replace(
            old_annotation, new_annotation, description,
            lambda *a: replace_freetext_annot(*a),
            commit_inline=True,
            select_old_on_error=True,
        )

    def _apply_zoom_annotation_form(self) -> None:
        if self._zoom_annotation_form_sync or self._selected_zoom_annotation is None:
            return
        old_annotation = self._selected_zoom_annotation

        if isinstance(old_annotation, (TextMarkupAnnotData, NoteAnnotData)):
            # マークアップ・付箋は専用の行でのみ編集する。
            return

        if isinstance(old_annotation, ShapeAnnotData):
            new_annotation = self._shape_data_from_form(old_annotation)
            old_verts = tuple(tuple(v) for v in old_annotation.vertices) if old_annotation.vertices else ()
            new_verts = tuple(tuple(v) for v in new_annotation.vertices) if new_annotation.vertices else ()
            if (
                old_annotation.rect == new_annotation.rect
                and old_annotation.stroke_color == new_annotation.stroke_color
                and old_annotation.fill_color == new_annotation.fill_color
                and abs(old_annotation.stroke_width - new_annotation.stroke_width) < 0.01
                and abs(old_annotation.opacity - new_annotation.opacity) < 0.01
                and abs(old_annotation.rotation - new_annotation.rotation) < 0.01
                and old_annotation.arrow_start == new_annotation.arrow_start
                and old_annotation.arrow_end == new_annotation.arrow_end
                and old_annotation.bracket_style == new_annotation.bracket_style
                and old_annotation.bracket_size == new_annotation.bracket_size
                and abs(old_annotation.triangle_apex[0] - new_annotation.triangle_apex[0]) < 1e-4
                and abs(old_annotation.triangle_apex[1] - new_annotation.triangle_apex[1]) < 1e-4
                and old_verts == new_verts
            ):
                return
            self._run_zoom_shape_replace(old_annotation, new_annotation, f"Update {old_annotation.shape_type.value}")
            return

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
        annot = self._selected_zoom_annotation

        # グループ（校正コールアウト等）はまとめて削除する。
        group_id = getattr(annot, "group_id", "")
        if group_id:
            page_num = annot.page_num
            members_ft = [a for a in list_freetext_annots(self._pdf_path, page_num) if a.group_id == group_id]
            members_sh = [a for a in list_shape_annots(self._pdf_path, page_num) if a.group_id == group_id]
            grp = {"ft": members_ft, "sh": members_sh}
            grp_refs: dict[str, list] = {"ft": [], "sh": []}

            def do_delete_group() -> None:
                # 削除前に各メンバーの共有ハンドルを控え、削除後にレジストリから外す。
                grp_refs["ft"] = [self._annot_refs.get(m.xref) for m in grp["ft"]]
                grp_refs["sh"] = [self._annot_refs.get(m.xref) for m in grp["sh"]]
                delete_annot_group(self._pdf_path, page_num, group_id)
                for ref_list in grp_refs.values():
                    for ref in ref_list:
                        self._release_annot_ref(ref)
                self._selected_zoom_annotation = None
                self._refresh_current_zoom_page()

            def undo_delete_group() -> None:
                restored = None
                new_ft = []
                for data, ref in zip(grp["ft"], grp_refs["ft"]):
                    created = create_freetext_annot(self._pdf_path, data)
                    if ref is not None:
                        self._rebind_annot_ref(ref, created.page_num, created.xref)
                    new_ft.append(created)
                    restored = created
                new_sh = []
                for data, ref in zip(grp["sh"], grp_refs["sh"]):
                    s = create_shape_annot(self._pdf_path, data)
                    if ref is not None:
                        self._rebind_annot_ref(ref, s.page_num, s.xref)
                    new_sh.append(s)
                    restored = restored or s
                # 再作成後の xref を保持し、Redo 時のハンドル控えを正しく取れるようにする。
                grp["ft"], grp["sh"] = new_ft, new_sh
                self._selected_zoom_annotation = restored
                self._refresh_current_zoom_page(open_drawer=True)

            self._push_undoable("Delete callout", do_delete_group, undo_delete_group)
            return

        # 型別の削除/復元操作テーブル(遅延バインドlambdaでモジュールグローバルを
        # 参照し、テストのmonkeypatchを効かせる)。該当なしはFreeText扱い。
        if isinstance(annot, TextMarkupAnnotData):
            self._run_zoom_delete(
                annot,
                f"Delete {annot.markup_type.value}",
                lambda *a: delete_markup_annot(*a),
                lambda *a: create_markup_annot(*a),
            )
        elif isinstance(annot, NoteAnnotData):
            def clear_note_editing() -> None:
                self._editing_note_xref = None

            self._run_zoom_delete(
                annot,
                "Delete note",
                lambda *a: delete_note_annot(*a),
                lambda *a: create_note_annot(*a),
                on_delete=clear_note_editing,
            )
        elif isinstance(annot, ShapeAnnotData):
            self._run_zoom_delete(
                annot,
                f"Delete {annot.shape_type.value}",
                lambda *a: delete_shape_annot(*a),
                lambda *a: create_shape_annot(*a),
            )
        else:
            self._run_zoom_delete(
                annot,
                "Delete FreeText",
                lambda *a: delete_freetext_annot(*a),
                lambda *a: create_freetext_annot(*a),
                select_old_on_error=True,
            )

    def _run_zoom_delete(
        self,
        annot: "AnyAnnotData",
        description: str,
        delete_fn: Callable[[str, int, int], bool],
        create_fn: Callable[[str, "AnyAnnotData"], "AnyAnnotData"],
        *,
        on_delete: Callable[[], None] | None = None,
        select_old_on_error: bool = False,
    ) -> None:
        """注釈1件の削除をundo可能な操作として実行する(全注釈種共通の骨格)。"""
        state = {"old": annot}
        ref: _AnnotRef | None = None

        def do_delete() -> None:
            nonlocal ref
            if ref is None:
                ref = self._annot_ref_for(state["old"].page_num, state["old"].xref)
            delete_fn(self._pdf_path, ref.page_num, ref.xref)
            self._release_annot_ref(ref)
            self._selected_zoom_annotation = None
            if on_delete is not None:
                on_delete()
            self._refresh_current_zoom_page()

        def undo_delete() -> None:
            recreated = create_fn(self._pdf_path, state["old"])
            self._rebind_annot_ref(ref, recreated.page_num, recreated.xref)
            state["old"] = recreated
            self._selected_zoom_annotation = recreated
            self._refresh_current_zoom_page(open_drawer=True)

        kwargs = {}
        if select_old_on_error:
            kwargs["selected_annotation_on_error"] = state["old"]
        self._push_undoable(description, do_delete, undo_delete, **kwargs)

    def _reorder_selected_zoom_annotation(self, mode: str) -> None:
        if self._selected_zoom_annotation is None or self._zoom_page_num is None:
            return
        self._commit_inline_annotation_editor()
        annot = self._selected_zoom_annotation
        page_num = annot.page_num
        xref = annot.xref
        before = get_annot_xref_order(self._pdf_path, page_num)
        if xref not in before:
            return

        def do_reorder() -> None:
            reorder_annot_on_page(self._pdf_path, page_num, xref, mode)
            self._selected_zoom_annotation = self._find_zoom_annotation(xref) or annot
            self._refresh_current_zoom_page(open_drawer=True)

        def undo_reorder() -> None:
            set_annot_xref_order(self._pdf_path, page_num, before)
            self._selected_zoom_annotation = self._find_zoom_annotation(xref) or annot
            self._refresh_current_zoom_page(open_drawer=True)

        try:
            do_reorder()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return
        after = get_annot_xref_order(self._pdf_path, page_num)
        if after == before:
            return
        self._undo_manager.add_action(UndoAction(
            description=f"Reorder annotation ({mode})",
            undo_func=undo_reorder,
            redo_func=do_reorder,
        ))

    def _on_zoom_annotation_geometry_changed(self, annotation: object, rect: object, mode: str) -> None:
        if not isinstance(rect, tuple) or len(rect) != 4:
            return
        new_rect = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))

        if isinstance(annotation, ShapeAnnotData):
            old_annotation = self._find_zoom_annotation(annotation.xref) or annotation
            if old_annotation.rect == new_rect:
                return
            new_annotation = dataclass_replace(
                old_annotation,
                rect=new_rect,
                bracket_orientation="vertical",
                # リサイズ時はsubjectを空にしてメタデータ(original_rect等)を再生成させる
                subject=old_annotation.subject if mode == "move" else "",
            )
            description = f"Move {old_annotation.shape_type.value}" if mode == "move" else f"Resize {old_annotation.shape_type.value}"
            self._run_zoom_shape_replace(old_annotation, new_annotation, description)
            return

        if not isinstance(annotation, FreeTextAnnotData):
            return
        old_annotation = self._find_zoom_annotation(annotation.xref) or annotation
        # 校正コールアウト: 挿入位置（target）は固定し、本文ボックスの新枠に
        # 合わせて引き出し線の接続点を再計算する（移動でも target を指したまま追従）。
        callout_line: tuple[tuple[float, float], ...] = ()
        callout_target = old_annotation.callout_target
        if old_annotation.callout_line and callout_target is not None:
            box_attach = _callout_box_attach(new_rect, callout_target)
            callout_line = (callout_target, box_attach)
        new_annotation = dataclass_replace(
            old_annotation,
            rect=new_rect,
            # リサイズ時はsubjectを空にしてメタデータを再生成させる
            subject=old_annotation.subject if mode == "move" else "",
            text_rotation=0,
            group_id="",
            callout_line=callout_line,
            callout_target=callout_target,
        )
        if old_annotation.rect == new_annotation.rect:
            return
        description = "Move FreeText" if mode == "move" else "Resize FreeText"
        self._run_zoom_annotation_replace(old_annotation, new_annotation, description)

    def _on_zoom_callout_target_changed(self, annotation: object, target: object) -> None:
        """校正コールアウトのさきっぽ（挿入位置）だけを移動する。本文ボックスは不変。"""
        if not isinstance(annotation, FreeTextAnnotData):
            return
        if not isinstance(target, (tuple, list)) or len(target) != 2:
            return
        try:
            new_target = (float(target[0]), float(target[1]))
        except (TypeError, ValueError):
            return
        old_annotation = self._find_zoom_annotation(annotation.xref) or annotation
        if not old_annotation.callout_line:
            return
        # 本文ボックスは固定し、新しい先端から接続点だけ再計算する。
        new_rect = old_annotation.rect
        box_attach = _callout_box_attach(new_rect, new_target)
        callout_line = (new_target, box_attach)
        old_target = old_annotation.callout_target
        if old_target is not None and (
            abs(new_target[0] - old_target[0]) < 0.01
            and abs(new_target[1] - old_target[1]) < 0.01
        ):
            return
        new_annotation = dataclass_replace(
            old_annotation, callout_line=callout_line, callout_target=new_target,
        )
        self._run_zoom_annotation_replace(old_annotation, new_annotation, "Move callout pointer")

    def _on_zoom_shape_geometry_changed_with_vertices(
        self, annotation: object, rect: object, vertices: object, mode: str
    ) -> None:
        if not isinstance(annotation, ShapeAnnotData):
            return
        if not isinstance(rect, tuple) or len(rect) != 4:
            return
        if not isinstance(vertices, tuple) or len(vertices) != 2:
            return
        try:
            new_rect = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            new_vertices = (
                (float(vertices[0][0]), float(vertices[0][1])),
                (float(vertices[1][0]), float(vertices[1][1])),
            )
        except (TypeError, ValueError, IndexError):
            return
        old_annotation = self._find_zoom_annotation(annotation.xref) or annotation
        old_verts = tuple(tuple(v) for v in old_annotation.vertices) if old_annotation.vertices else ()
        if old_annotation.rect == new_rect and old_verts == new_vertices:
            return
        new_annotation = dataclass_replace(
            old_annotation,
            rect=new_rect,
            vertices=new_vertices,
            bracket_orientation="vertical",
            subject="",  # 空にして端点変更後のメタデータを再生成させる
        )
        description = f"Move {old_annotation.shape_type.value} endpoint"
        self._run_zoom_shape_replace(old_annotation, new_annotation, description)

    def _update_button_states(self) -> None:
        has_selection = len(self._selected_thumbnails) > 0
        zoom_active = bool(
            self._zoom_view
            and self._zoom_view.isVisible()
            and self._zoom_page_num is not None
        )
        # 見開き表示(閲覧専用)中はページ編集(回転・削除)を不可にする。
        spread = getattr(self, "_zoom_spread_mode", False)
        can_edit_pages = (has_selection or zoom_active) and not spread
        self._delete_btn.setEnabled(can_edit_pages)
        self._rename_btn.setEnabled(True)
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
        # ページ構成が変わったので検索結果は破棄する
        self._invalidate_search_results()
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
            self._render_zoom()

    def refresh_from_disk(self) -> None:
        """Reload the current PDF from disk without closing the edit window."""
        if not os.path.exists(self._pdf_path):
            return

        self._commit_inline_annotation_editor()
        clear_pixmap_cache_for_path(self._pdf_path)
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
            self._render_zoom()

    def _on_external_pdf_rotation(self) -> None:
        """外部（MainWindow等）で回転されたPDFを即時反映する。"""
        self.refresh_from_disk()

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
            thumb._reposition_number_badge()

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
                    self._render_zoom()

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

    def _reset_zoom_spread_mode(self) -> None:
        """見開きモードを解除し、通常(単ページ・編集可)の状態へ戻す。再描画はしない。"""
        self._zoom_spread_mode = False
        if self._zoom_label:
            self._zoom_label.set_view_only(False)
        if getattr(self, "_zoom_spread_btn", None) is not None:
            self._zoom_spread_btn.setChecked(False)
        if getattr(self, "_zoom_object_btn", None) is not None:
            self._zoom_object_btn.setEnabled(True)
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_read_only(False)

    def _open_zoom_view(self, page_num: int) -> None:
        self._commit_inline_annotation_editor()
        # 拡大ビューは常に単ページ表示で開始する。
        self._reset_zoom_spread_mode()
        self._zoom_page_num = page_num
        self._selected_zoom_annotation = None
        self._set_zoom_annotation_create_mode(False)
        self._set_zoom_percent(100)
        if self._grid_scroll:
            self._grid_scroll.hide()
        if self._zoom_view:
            self._zoom_view.show()
        for action in getattr(self, "_zoom_nav_actions", ()):
            action.setEnabled(True)
        self._reload_bookmarks_tree()
        self._update_button_states()

    def _exit_zoom_view(self) -> None:
        self._commit_inline_annotation_editor()
        # 見開きモードはここで解除し、次回の拡大表示は単ページで開始させる。
        self._reset_zoom_spread_mode()
        last_page = self._zoom_page_num
        self._set_zoom_annotation_create_mode(False)
        self._set_selected_zoom_annotation(None)
        for action in getattr(self, "_zoom_nav_actions", ()):
            action.setEnabled(False)
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
        if self._zoom_reset_btn:
            self._zoom_reset_btn.setText(f"{value}%")
        self._render_zoom()

    def _on_zoom_scroll_requested(self, dx: int, dy: int) -> None:
        # 中ボタンドラッグ / Ctrl+矢印からのビュースクロール要求を処理する。
        if not self._zoom_scroll:
            return
        hbar = self._zoom_scroll.horizontalScrollBar()
        vbar = self._zoom_scroll.verticalScrollBar()
        hbar.setValue(hbar.value() + int(dx))
        vbar.setValue(vbar.value() + int(dy))

    def _on_zoom_region_requested(self, page_rect: object) -> None:
        # 右ドラッグで指定された範囲がビューポートに収まる倍率へ拡大し、中央に表示する。
        if not self._zoom_scroll or not self._zoom_label:
            return
        if (
            not isinstance(page_rect, QRectF)
            or page_rect.width() <= 0
            or page_rect.height() <= 0
        ):
            return
        viewport = self._zoom_scroll.viewport()
        avail_w = max(1, viewport.width())
        avail_h = max(1, viewport.height())
        fit = min(avail_w / page_rect.width(), avail_h / page_rect.height())
        percent = max(self.ZOOM_MIN, min(self.ZOOM_MAX, int(fit * 100)))
        self._set_zoom_percent(percent)
        center = page_rect.center()
        # 倍率変更後のレイアウト確定を待ってから中央へスクロールする。
        QTimer.singleShot(0, lambda: self._center_zoom_on_page_point(center))

    def _center_zoom_on_page_point(self, page_pt: QPointF) -> None:
        if not self._zoom_scroll or not self._zoom_label:
            return
        wp = self._zoom_label.widget_point_from_page_point(page_pt)
        viewport = self._zoom_scroll.viewport()
        hbar = self._zoom_scroll.horizontalScrollBar()
        vbar = self._zoom_scroll.verticalScrollBar()
        hbar.setValue(int(wp.x() - viewport.width() / 2))
        vbar.setValue(int(wp.y() - viewport.height() / 2))

    def _on_zoom_wheel(self, step: int) -> None:
        current = int(self._zoom_factor * 100)
        self._set_zoom_percent(current + step)

    def _on_zoom_in(self) -> None:
        current = int(self._zoom_factor * 100)
        self._set_zoom_percent(current + self.ZOOM_STEP)

    def _on_zoom_out(self) -> None:
        current = int(self._zoom_factor * 100)
        self._set_zoom_percent(current - self.ZOOM_STEP)

    def _on_zoom_prev_page(self) -> None:
        if self._zoom_page_num is None:
            return
        if self._zoom_page_num <= 0:
            self._update_zoom_nav_buttons()
            return
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
        if self._zoom_spread_mode:
            # 見開きは2ページ単位で戻り、左ページを偶数 index に保つ。
            new = self._zoom_page_num - 2
            new -= new % 2
            self._zoom_page_num = max(0, new)
        else:
            self._zoom_page_num -= 1
        self._render_zoom()

    def _on_zoom_next_page(self) -> None:
        if self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if self._zoom_spread_mode:
            # 見開きは2ページ単位で進む。最後の見開き(left==last_left)で停止。
            last_left = (page_count - 1) - ((page_count - 1) % 2)
            if self._zoom_page_num >= last_left:
                self._update_zoom_nav_buttons(page_count)
                return
            self._commit_inline_annotation_editor()
            self._set_zoom_annotation_create_mode(False)
            self._selected_zoom_annotation = None
            new = self._zoom_page_num + 2
            new -= new % 2
            self._zoom_page_num = min(last_left, new)
            self._render_zoom()
            return
        if self._zoom_page_num >= page_count - 1:
            self._update_zoom_nav_buttons(page_count)
            return
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
        self._zoom_page_num += 1
        self._render_zoom()

    def _on_zoom_first_page(self) -> None:
        if self._zoom_page_num is None:
            return
        if self._zoom_page_num == 0:
            return
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
        self._zoom_page_num = 0
        self._render_zoom()

    def _on_zoom_last_page(self) -> None:
        if self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            return
        last_index = page_count - 1
        # 見開き時は最後の見開きの左ページ(偶数 index)へ移動する。
        target = last_index - (last_index % 2) if self._zoom_spread_mode else last_index
        if self._zoom_page_num == target:
            return
        self._commit_inline_annotation_editor()
        self._set_zoom_annotation_create_mode(False)
        self._selected_zoom_annotation = None
        self._zoom_page_num = target
        self._render_zoom()

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
        if self._zoom_spread_mode:
            # 見開き: 最後の見開き(left==last_left)で「次」を無効化する。
            last_left = (page_count - 1) - ((page_count - 1) % 2)
            self._zoom_prev_btn.setEnabled(self._zoom_page_num > 0)
            self._zoom_next_btn.setEnabled(self._zoom_page_num < last_left)
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
        chars = []
        if self._zoom_page_num in self._zoom_text_cache:
            words, links, chars = self._zoom_text_cache[self._zoom_page_num]
        else:
            words = get_page_words(self._pdf_path, self._zoom_page_num)
            links = get_page_links(self._pdf_path, self._zoom_page_num)
            chars = get_page_chars(self._pdf_path, self._zoom_page_num)
            self._zoom_text_cache[self._zoom_page_num] = (words, links, chars)
        freetext_annots = list_freetext_annots(self._pdf_path, self._zoom_page_num)
        shape_annots = list_shape_annots(self._pdf_path, self._zoom_page_num)
        markup_annots = list_markup_annots(self._pdf_path, self._zoom_page_num)
        note_annots = list_note_annots(self._pdf_path, self._zoom_page_num)
        merged = freetext_annots + shape_annots + markup_annots + note_annots
        xref_order = get_annot_xref_order(self._pdf_path, self._zoom_page_num)
        order_index = {x: i for i, x in enumerate(xref_order)}
        # PDF の /Annots 配列順（描画順）に並べ替え。未登録 xref は末尾に置く。
        merged.sort(key=lambda a: order_index.get(a.xref, len(order_index)))
        self._zoom_annotations = merged
        selected_xref = self._selected_zoom_annotation.xref if self._selected_zoom_annotation else None
        current_selection = self._find_zoom_annotation(selected_xref)
        self._zoom_label.set_page(
            pixmap,
            words,
            links,
            self._zoom_annotations,
            self._zoom_factor,
            current_selection.xref if current_selection else None,
            chars=chars,
        )
        hit_rects = self._search_hits.get(self._zoom_page_num, [])
        self._zoom_label.set_search_hit_rects(hit_rects)
        self._set_selected_zoom_annotation(current_selection)
        self._update_note_list_widget()

    def _render_zoom(self) -> None:
        """現在のモードに応じてズーム表示を更新する(単ページ / 見開き)。"""
        if self._zoom_spread_mode:
            self._render_zoom_spread()
        else:
            self._render_zoom_page()

    # 見開き表示のページ間ゲター(論理px)。
    SPREAD_GUTTER = 12

    def _compose_spread_pixmap(self, left_pix: QPixmap, right_pix: QPixmap | None,
                               dpr: float) -> QPixmap:
        """左右ページの pixmap をデバイスピクセルのまま1枚に合成して返す。

        ソース pixmap は DPR=1.0 のまま(=デバイスピクセル等倍)で受け取り、
        合成後の pixmap にのみ呼び出し側で setDevicePixelRatio(dpr) を適用する。
        ここで個別に DPR を掛けると drawPixmap が 1/dpr 縮小してボケるため。
        """
        gutter_dev = round(self.SPREAD_GUTTER * dpr)
        lw, lh = left_pix.width(), left_pix.height()
        has_right = right_pix is not None and not right_pix.isNull()
        if has_right:
            rw, rh = right_pix.width(), right_pix.height()
            total_w = lw + gutter_dev + rw
            total_h = max(lh, rh)
        else:
            # 右ページが無い(最終奇数ページ等)場合は左単独。
            total_w = lw
            total_h = lh
        canvas = QPixmap(max(1, total_w), max(1, total_h))
        canvas.fill(Qt.GlobalColor.white)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, left_pix)  # 上揃え(top-aligned)
        if has_right:
            painter.drawPixmap(lw + gutter_dev, 0, right_pix)
        painter.end()
        return canvas

    def _render_zoom_spread(self) -> None:
        """見開き(左→右で2ページ並べ)を合成して表示する。閲覧専用。"""
        if not self._zoom_annotation_text_commit_in_progress:
            self._commit_inline_annotation_editor()
        if self._zoom_page_num is None or not self._zoom_label:
            return
        page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            self._exit_zoom_view()
            return
        # 左ページ index を有効範囲・偶数へ正規化する。
        left = self._zoom_page_num
        if left >= page_count:
            left = (page_count - 1) - ((page_count - 1) % 2)
        left -= left % 2
        left = max(0, left)
        self._zoom_page_num = left
        right = left + 1
        has_right = right < page_count

        self._update_zoom_nav_buttons(page_count)
        if self._zoom_page_label:
            if has_right:
                self._zoom_page_label.setText(f"{left + 1}-{right + 1} / {page_count}")
            else:
                self._zoom_page_label.setText(f"{left + 1} / {page_count}")

        dpr = self._zoom_label.devicePixelRatioF()
        scale = self._zoom_factor * dpr
        # 注釈はページ画像に焼き込んで見えるようにする(annots=True)。
        left_pix = get_page_pixmap(self._pdf_path, left, scale, annots=True)
        right_pix = (
            get_page_pixmap(self._pdf_path, right, scale, annots=True)
            if has_right else None
        )
        combined = self._compose_spread_pixmap(left_pix, right_pix, dpr)
        combined.setDevicePixelRatio(dpr)
        # words/links/annots/chars を空で渡し、選択・編集のヒット対象を無くす。
        self._zoom_label.set_page(
            combined, [], [], [], self._zoom_factor, None, chars=[]
        )
        # 合成画像にはページ座標系が無いため検索ハイライトは出さない。
        self._zoom_label.set_search_hit_rects([])
        # 見開き中は付箋編集UI(B一覧)を対象外にするため注釈状態をクリアする。
        self._zoom_annotations = []
        self._update_note_list_widget()

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
            self._render_zoom()

    def _on_undo(self) -> None:
        self._commit_inline_annotation_editor()
        try:
            self._undo_manager.undo()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error, selected_annotation=self._selected_zoom_annotation)
            return
        self._load_pages()
        self._update_button_states()

    def _on_redo(self) -> None:
        self._commit_inline_annotation_editor()
        try:
            self._undo_manager.redo()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error, selected_annotation=self._selected_zoom_annotation)
            return
        self._load_pages()
        self._update_button_states()

    def _on_delete(self) -> None:
        # 見開き表示(閲覧専用)中は削除不可(Delete キーのショートカット対策)。
        if getattr(self, "_zoom_spread_mode", False):
            return
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

        self._push_undoable(f"Delete {len(indices)} page(s)", do_delete, undo_delete)

    def _on_rename(self) -> None:
        old_path = self._pdf_path
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old_name
        )

        if ok and new_name and new_name != old_name:
            if not new_name.lower().endswith(".pdf"):
                new_name += ".pdf"
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            if os.path.abspath(old_path) == os.path.abspath(new_path):
                return

            def _get_main_window():
                from src.views.main_window import MainWindow

                owner = self.parent()
                return owner if isinstance(owner, MainWindow) else None

            def do_rename() -> None:
                main_window = _get_main_window()
                if main_window:
                    main_window._perform_rename(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    self._pdf_path = new_path
                    self.setWindowTitle(f"JusticePDF - 編集:{new_name}")

            def undo_rename() -> None:
                main_window = _get_main_window()
                if main_window:
                    main_window._perform_rename(new_path, old_path)
                else:
                    os.rename(new_path, old_path)
                    self._pdf_path = old_path
                    self.setWindowTitle(f"JusticePDF - 編集:{old_name}")

            try:
                do_rename()
            except OSError as error:
                self._handle_file_operation_error(error, old_path, "名前変更")
                return
            self._undo_manager.add_action(UndoAction(
                description="Rename PDF",
                undo_func=undo_rename,
                redo_func=do_rename
            ))

    def _on_rename_pdf_title(self) -> None:
        old_path = self._pdf_path
        old_name = os.path.basename(old_path)
        old_title = get_pdf_metadata_title(old_path) or os.path.splitext(old_name)[0]
        new_title, ok = QInputDialog.getText(
            self, "PDFタイトルの変更", "新しいPDFタイトル:", text=old_title
        )

        if not ok or not new_title or new_title == old_title:
            return

        def do_rename_pdf_title() -> None:
            update_pdf_metadata_title(old_path, new_title)
            self.refresh_from_disk()

        def undo_rename_pdf_title() -> None:
            update_pdf_metadata_title(old_path, old_title)
            self.refresh_from_disk()

        try:
            do_rename_pdf_title()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return
        except Exception as error:
            self._handle_file_operation_error(error, old_path, "PDFタイトル変更")
            return
        self._undo_manager.add_action(UndoAction(
            description="Rename PDF Name",
            undo_func=undo_rename_pdf_title,
            redo_func=do_rename_pdf_title
        ))

    def _on_print(self) -> None:
        """Print the current PDF."""
        from src.views.print_dialog import PrintDialog
        current = self._zoom_page_num if (self._zoom_view and self._zoom_view.isVisible()) else None
        dialog = PrintDialog([self._pdf_path], self, current_index=current)
        if dialog.exec() != PrintDialog.DialogCode.Accepted:
            return
        print_pdfs([self._pdf_path], self, settings=dialog.get_settings(), printer=dialog.build_printer())

    def _on_rotate(self) -> None:
        # 見開き表示(閲覧専用)中は回転不可(R キーのショートカット対策)。
        if getattr(self, "_zoom_spread_mode", False):
            return
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

        self._push_undoable(f"Rotate {len(indices)} page(s)", do_rotate, undo_rotate)

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
                    self._render_zoom()

        def undo_delete():
            insert_pages(pdf_path, backup_path, [deleted_page])
            self._load_pages()
            self._zoom_text_cache.clear()
            if self._zoom_view and self._zoom_view.isVisible():
                self._zoom_page_num = deleted_page
                self._render_zoom()

        self._push_undoable("Delete page from zoom view", do_delete, undo_delete)

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
                self._render_zoom()
            # サムネイルも更新
            if page_num < len(self._thumbnails):
                self._request_thumbnail_refresh(page_num)

        def undo_rotate():
            rotate_pages(pdf_path, [page_num], 270)
            self._zoom_text_cache.pop(page_num, None)
            if self._zoom_view and self._zoom_view.isVisible():
                self._render_zoom()
            if page_num < len(self._thumbnails):
                self._request_thumbnail_refresh(page_num)

        self._push_undoable("Rotate page from zoom view", do_rotate, undo_rotate)

    def _delete_all_pages(self, backup_path: str) -> None:
        """全ページ削除（ファイルをゴミ箱へ移動し、UNDO対応）"""
        from src.views.main_window import MainWindow

        pdf_path = self._pdf_path

        def _get_main_window():
            owner = self.parent()
            return owner if isinstance(owner, MainWindow) else None

        def _close_edit_windows() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, PageEditWindow) and widget._pdf_path == pdf_path:
                    widget.close()

        def do_delete():
            main_window = _get_main_window()
            removed_card = False
            if main_window:
                main_window._register_internal_remove([pdf_path])
                main_window._remove_card(pdf_path)
                main_window._refresh_grid()
                removed_card = True
            # ファイルをゴミ箱へ
            try:
                if os.path.exists(pdf_path):
                    send2trash(pdf_path)
            except OSError:
                if main_window:
                    main_window._internal_removes.discard(main_window._normalize_path(pdf_path))
                    if removed_card and main_window._get_card_by_path(pdf_path) is None:
                        main_window._add_card(pdf_path)
                        main_window._refresh_grid()
                raise
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

        try:
            do_delete()
        except OSError as error:
            QMessageBox.warning(
                self,
                "削除できません",
                build_trash_failure_message(pdf_path, error),
            )
            return

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

        targets = []
        if 0 <= idx - 1 < len(self._thumbnails):
            left = self._thumbnails[idx - 1]
            if left.isVisible():
                targets.append(left)
        if 0 <= idx < len(self._thumbnails):
            right = self._thumbnails[idx]
            if right.isVisible():
                targets.append(right)
        self._clear_all_drop_targets(except_thumbs=targets)
        for t in targets:
            t.set_drop_target(True)

    def _clear_all_drop_targets(self, except_thumbs=()) -> None:
        """Turn off droptarget highlight on every thumbnail (optionally skipping some)."""
        skip = set(except_thumbs)
        for thumb in self._thumbnails:
            if thumb in skip:
                continue
            if thumb.is_drop_target:
                thumb.set_drop_target(False)

    def _hide_drop_indicator(self) -> None:
        """Hide the drop indicator."""
        self._drop_indicator.hide()
        self._drop_indicator_index = -1
        self._clear_all_drop_targets()

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

        self._push_undoable("Reorder page", do_reorder, undo_reorder)

    def _handle_page_insert(self, source_pdf_path: str, source_pages: list[int], drop_pos) -> None:
        import tempfile

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
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return
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

        try:
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
                    owner = self.parent()
                    if isinstance(owner, MainWindow):
                        owner._remove_card(source_pdf_path)
                        owner._refresh_grid()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return

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
        from src.views.main_window import MainWindow

        logger.debug(f"PageEditWindow closing for {self._pdf_path}")

        self._reset_thumbnail_render_queue()
        self._undo_manager.remove_listener(self._on_undo_manager_changed)

        if self._search_dialog is not None:
            self._search_dialog.close()
            self._search_dialog = None

        owner = self.parent()
        if isinstance(owner, MainWindow):
            owner.unlock_card(self._pdf_path)
        super().closeEvent(event)

    # --- Text search (Ctrl+F) ---

    def _on_open_search(self) -> None:
        """Open or focus the modeless search dialog."""
        from src.views.search_dialog import SearchDialog

        if self._search_dialog is None:
            self._search_dialog = SearchDialog(self)
            self._search_dialog.search_requested.connect(self._on_search_execute)
            self._search_dialog.next_requested.connect(self._on_search_next)
            self._search_dialog.prev_requested.connect(self._on_search_prev)
            self._search_dialog.finished.connect(self._on_search_dialog_finished)
        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.activateWindow()
        self._search_dialog.focus_input()

    def _on_search_dialog_finished(self, _result: int) -> None:
        self._clear_search_highlights()

    def _on_search_execute(self, query: str) -> None:
        query = (query or "").strip()
        # まず以前のハイライトをクリア
        self._clear_search_highlights()
        if not query:
            if self._search_dialog is not None:
                self._search_dialog.set_status(0, 0)
            return
        self._search_hits = search_text_in_pdf(self._pdf_path, query)
        self._search_hit_pages = sorted(self._search_hits.keys())
        self._apply_search_highlights()
        if not self._search_hit_pages:
            self._search_cursor = -1
            if self._search_dialog is not None:
                self._search_dialog.set_status(0, 0)
            return
        self._search_cursor = 0
        self._jump_to_search_page(self._search_hit_pages[0])
        if self._search_dialog is not None:
            self._search_dialog.set_status(1, len(self._search_hit_pages))

    def _on_search_next(self) -> None:
        if not self._search_hit_pages:
            return
        self._search_cursor = (self._search_cursor + 1) % len(self._search_hit_pages)
        self._jump_to_search_page(self._search_hit_pages[self._search_cursor])
        if self._search_dialog is not None:
            self._search_dialog.set_status(self._search_cursor + 1, len(self._search_hit_pages))

    def _on_search_prev(self) -> None:
        if not self._search_hit_pages:
            return
        self._search_cursor = (self._search_cursor - 1) % len(self._search_hit_pages)
        self._jump_to_search_page(self._search_hit_pages[self._search_cursor])
        if self._search_dialog is not None:
            self._search_dialog.set_status(self._search_cursor + 1, len(self._search_hit_pages))

    def _jump_to_search_page(self, page_num: int) -> None:
        if page_num < 0 or page_num >= len(self._thumbnails):
            return
        zoom_visible = bool(self._zoom_view and self._zoom_view.isVisible())
        if zoom_visible:
            # ズーム表示中は対応ページに切り替えてヒット矩形を表示
            self._commit_inline_annotation_editor()
            self._set_zoom_annotation_create_mode(False)
            self._selected_zoom_annotation = None
            self._zoom_page_num = page_num
            self._render_zoom()
        else:
            # サムネイルグリッド表示中は選択 + スクロール
            self._clear_selection()
            thumb = self._thumbnails[page_num]
            thumb.set_selected(True)
            self._selected_thumbnails.append(thumb)
            if self._grid_scroll:
                self._grid_scroll.ensureWidgetVisible(thumb)
            self._update_button_states()

    def _apply_search_highlights(self) -> None:
        hit_set = set(self._search_hit_pages)
        for thumb in self._thumbnails:
            thumb.set_search_hit(thumb.page_num in hit_set)

    def _clear_search_highlights(self) -> None:
        for thumb in self._thumbnails:
            thumb.set_search_hit(False)
        self._search_hits = {}
        self._search_hit_pages = []
        self._search_cursor = -1
        if self._zoom_label is not None:
            self._zoom_label.set_search_hit_rects([])

    def _invalidate_search_results(self) -> None:
        """ページ構成が変わったときに呼び、検索状態とダイアログ表示を初期化する。"""
        self._search_hits = {}
        self._search_hit_pages = []
        self._search_cursor = -1
        if self._zoom_label is not None:
            self._zoom_label.set_search_hit_rects([])
        if self._search_dialog is not None:
            self._search_dialog.clear_status()
