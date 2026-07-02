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
    _freetext_pixel_size,
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
from src.views.page_edit_annotations import (
    CreateMode,
    ZoomAnnotationMixin,
    _AnnotRef,
)


logger = logging.getLogger(__name__)


# 括弧図形コンボボックスの並び(インデックス⇔値の唯一の対応表)


class PageEditWindow(QMainWindow, ZoomAnnotationMixin):
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


    # --- Text markup (highlight / underline / strikeout) -----------------


    # --- Sticky note (comment) -------------------------------------------


    # --- Proofreading callout --------------------------------------------


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


    def _handle_file_operation_error(self, error: Exception, pdf_path: str, action: str) -> None:
        logger.warning("%s failed for %s", action, pdf_path)
        logger.debug("%s failed for %s", action, pdf_path, exc_info=True)
        pdf_name = os.path.basename(pdf_path)
        QMessageBox.warning(
            self,
            f"{action}できません",
            f"{action}に失敗しました。\n\n{pdf_name}\n\n{error}",
        )


    # --- Shape methods ---


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
