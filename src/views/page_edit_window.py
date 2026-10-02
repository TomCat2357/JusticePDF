"""Page edit window for editing PDF pages."""
import itertools
import os
import shutil
import logging
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import replace as dataclass_replace
from enum import Enum, auto
from PyQt6 import sip
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
    QActionGroup,
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
    list_ink_annot_xrefs,
    list_ink_annot_xrefs_by_page,
    list_pii_targets_by_page,
    load_zoom_page_annotations,
    _get_file_cache_token,
    compact_pdf_in_place,
    pages_changed_since,
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
    is_heavy_pdf,
)
from src.utils import app_settings
from src.utils.pdf_utils.common import (
    PdfSession,
    PdfSessionConflictError,
    hold_doc,
    open_session,
    release_held_docs,
    release_session_for_path,
)
from src.utils.constants import (
    INCREMENTAL_SAVE_COMPACT_BYTES,
    INCREMENTAL_SAVE_COMPACT_RATIO,
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
    responsive_grid_metrics,
    viewport_width_or_fallback,
)
from send2trash import send2trash
from src.utils.trash_utils import build_trash_failure_message
from src.views.page_edit_annotations import (
    CreateMode,
    ZoomAnnotationMixin,
    _AnnotRef,
)
from src.views.page_edit_ocr import OcrDrawerMixin
from src.views.page_edit_pii import PiiDrawerMixin


logger = logging.getLogger(__name__)


class ZoomPageLayout(Enum):
    """レイアウトごとの1画面あたりのページ配置。"""

    SINGLE = ("single", "1枚", 1, 1)
    HORIZONTAL = ("horizontal", "横2枚", 2, 1)
    VERTICAL = ("vertical", "縦2枚", 1, 2)
    GRID = ("grid", "4枚", 2, 2)

    def __init__(self, key: str, label: str, columns: int, rows: int) -> None:
        self.key = key
        self.label = label
        self.columns = columns
        self.rows = rows

    @property
    def page_capacity(self) -> int:
        return self.columns * self.rows


# 括弧図形コンボボックスの並び(インデックス⇔値の唯一の対応表)


class PageEditWindow(QMainWindow, ZoomAnnotationMixin, PiiDrawerMixin, OcrDrawerMixin):
    """Window for editing pages within a PDF."""

    ZOOM_MIN = 25
    ZOOM_MAX = 400
    ZOOM_STEP = 5
    # 「100%」ボタンのドロップダウンに並べる倍率プリセット（25/100/400% は必須）。
    ZOOM_PRESETS = (25, 50, 75, 100, 150, 200, 300, 400)
    # 右側ドロワー(アノテーション/個人情報検出/OCR)ドロップダウンの、パネルが開いていないときの表示。
    ZOOM_PANEL_BUTTON_DEFAULT_TEXT = "パネル"
    PREVIEW_THUMB_MIN = 80
    PREVIEW_THUMB_MAX = 400
    PREVIEW_THUMB_STEP = 20

    def __init__(self, pdf_path: str, undo_manager: UndoManager, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        # 増分保存の累積による肥大を、閉じるときに整理するか判断するための基準サイズ。
        self._initial_file_size = self._current_file_size()
        self._undo_manager = undo_manager
        # 手動保存モード(設定「編集内容の保存方法」)。開くときに1回だけ読み、開いている間は
        # 切り替えない。手動のときは編集をメモリ上のドキュメント(セッション)へ行い、
        # 保存ボタンで初めてファイルへ書く。自動のときは _session が None のままで従来どおり。
        self._undo_owner = object()  # 共有 UndoManager に積んだ未保存の操作を識別するトークン
        self._session: PdfSession | None = None
        self._save_action: QAction | None = None
        self._save_btn: QPushButton | None = None
        if app_settings.is_manual_save_mode():
            self._open_manual_session()
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
        # 拡大表示のページレイアウト。SINGLE は編集可能、それ以外は閲覧専用。
        self._zoom_page_layout = ZoomPageLayout.SINGLE
        self._zoom_layout_actions: dict[ZoomPageLayout, QAction] = {}
        # Acrobat 等で書かれた手書き(Ink)注釈の表示/非表示。既定は表示。
        # アノテーション/しおりドロワーの切替や複数ページ同時表示への切替では
        # リセットされず、ウィンドウ内でこの状態を保持する(永続化はしない)。
        self._show_ink_annots: bool = True
        # ページ番号ごとの Ink 注釈 xref のキャッシュ(サムネイル用)。
        # ページ数が多い文書でサムネイル1枚ごとに fitz.open するのを避けるため、
        # 初回アクセス時に文書全体を一度だけ開いて集計する。ページ構成が変わる
        # 操作(読み込み直し・削除等)の際に None へ戻して再集計させる。
        self._ink_xrefs_by_page_cache: dict[int, list[int]] | None = None
        # 重量文書では表示対象ページ分だけを集計するため、走査済みページを覚える
        # (None=全ページ走査済み。キャッシュが None のときは参照されず作り直される)。
        self._ink_xrefs_scanned_pages: set[int] | None = None
        # 描画済みサムネイルごとの「塗りつぶし対象の xref」(描き直しの要否判定用)。
        self._grid_pii_xrefs: dict[int, tuple[int, ...]] = {}
        # ページ一覧サムネイルに重ねる塗りつぶし対象(個人情報検出)のページ別
        # 集計。塗りつぶし対象の追加/削除はファイル保存を伴うので、ファイルの
        # キャッシュトークン(mtime/size)が変われば自動的に再集計する。
        self._pii_targets_by_page_cache: (
            "tuple[tuple[int, int, int], dict[int, tuple[tuple[float, float], list]]] | None"
        ) = None
        # 上記の走査済みページ(重量文書の部分集計用。None=全ページ走査済み)。
        self._pii_targets_scanned_pages: set[int] | None = None
        # 個人情報検出の結果一覧用の行キャッシュ(ページ別・ファイルトークン付き)。
        # 注釈書き込みで変わったページ分だけ組み直す(_build_pii_result_rows 参照)。
        self._pii_result_rows_cache: (
            "tuple[tuple[int, int, int], dict[int, list]] | None"
        ) = None
        self._zoom_annotation_form_sync = False
        # フォームのスライダー/スピン操作の未確定(保存待ち)フラグ。確定は page_edit_annotations 参照。
        self._zoom_form_commit_pending = False
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
        self._zoom_annotation_border_color_btn = None
        self._zoom_annotation_text_color = (0.0, 0.0, 0.0)
        self._zoom_annotation_fill_color: tuple[float, float, float] | None = (1.0, 1.0, 0.6)
        self._zoom_annotation_border_color: tuple[float, float, float] | None = (0.0, 0.0, 0.0)
        self._zoom_markup_color: tuple[float, float, float] = (0.85, 0.0, 0.0)
        self._zoom_markup_color_btn: QPushButton | None = None
        self._markup_buttons: dict[MarkupType, QToolButton] = {}
        # マーカー/下線/取り消し線の連続モード中に選択中の種類（未使用時は None）。
        self._markup_sticky_type: MarkupType | None = None
        self._eraser_btn: QToolButton | None = None
        # 「連続」トグル(既定OFF)。ONの間だけマーカー/U/S/消しゴムのクリックが
        # sticky ツール切り替えとして扱われ、図形・ノートも作成後に解除されない
        # (校正・テキストボックスは対象外)。OFF中は各ボタンは常に単発動作。
        self._markup_continuous_mode: bool = False
        self._markup_continuous_btn: QToolButton | None = None
        self._zoom_note_color: tuple[float, float, float] = (1.0, 0.92, 0.23)
        self._zoom_note_color_btn: QPushButton | None = None
        self._zoom_note_btn: QToolButton | None = None
        self._zoom_note_editor: NoteContentEdit | None = None
        self._zoom_note_list: QListWidget | None = None
        self._zoom_callout_btn: QToolButton | None = None
        self._create_mode = CreateMode.NONE
        # 個人情報検出ドロワー: 「塗りつぶし用の四角/楕円」配置待ち中の図形種別。
        self._mask_shape_pending_type = None
        # 本文編集中の付箋 xref と、確定前の元本文（差分判定用）。
        self._editing_note_xref: int | None = None
        self._editing_note_original = ""
        # Preferred size is controlled by Ctrl+wheel.  _thumb_size stays at
        # that size while the window is resized; a narrow viewport scrolls
        # horizontally instead of shrinking the thumbnails.
        self._preferred_thumb_size = PageThumbnail.THUMBNAIL_SIZE
        self._thumb_size = self._preferred_thumb_size
        self._thumb_render_queue: deque[int] = deque()
        self._thumb_render_queue_set: set[int] = set()
        # ページ数/ファイルサイズが閾値を超える「重量文書」かどうか
        # (_load_pages() で判定して更新)。真の間は、サムネイルウィジェット
        # 生成をチャンク分割し、描画も表示範囲のみを逐次処理する。
        self._is_heavy_document: bool = False
        # 重量文書でのウィジェット生成をチャンクへ分割するための残りページ
        # イテレータ(通常文書では None のまま)。
        self._pending_widget_pages: "Iterator[int] | None" = None
        # 現在グリッドへ並べ終えている列数(None=未確定/不整合)。リサイズで列数が
        # 変わらないときの並べ直しスキップと、読み込み中に抑止したリサイズの判定に使う。
        self._grid_laid_out_cols: int | None = None
        # 重量文書の読み込み中に届いたリサイズ(読み込み完了時に1回だけ反映する)。
        self._grid_resize_deferred = False
        # 進捗表示用(_pending_widget_pages が None でない間だけ意味を持つ)。
        self._pending_widget_total: int = 0
        # _load_pages() 呼び出しごとに増える世代番号。チャンク処理中に再度
        # _load_pages() が呼ばれた場合、古い世代のタイマーコールバックが
        # クリア済みの self._thumbnails へ追記してしまうのを防ぐ。
        self._widget_build_generation: int = 0
        self._thumb_render_timer = QTimer(self)
        self._thumb_render_timer.setSingleShot(True)
        self._thumb_render_timer.timeout.connect(self._process_thumbnail_render_queue)
        # 重量文書の描画バッチ間で保持している文書(hold_doc(linger=True))を、一定時間
        # 描画が無ければ閉じる。保持するのはファイルのメモリ上の写しでハンドルは掴まない。
        self._held_doc_idle_timer = QTimer(self)
        self._held_doc_idle_timer.setSingleShot(True)
        self._held_doc_idle_timer.setInterval(3000)
        self._held_doc_idle_timer.timeout.connect(self._release_held_doc)
        _held_path = self._pdf_path
        self.destroyed.connect(lambda *_a, _p=_held_path: release_held_docs(_p))
        self._scroll_debounce_timer = QTimer(self)
        self._scroll_debounce_timer.setSingleShot(True)
        self._scroll_debounce_timer.setInterval(60)
        self._scroll_debounce_timer.timeout.connect(self._enqueue_visible_thumbnail_renders)
        self._grid_resize_timer = QTimer(self)
        self._grid_resize_timer.setSingleShot(True)
        self._grid_resize_timer.timeout.connect(self._on_grid_resize_settled)

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
        self.setWindowTitle(self._page_edit_window_title())
        self.resize(800, 600)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        central = QWidget()
        self.setCentralWidget(central)

        # 左: ページ一覧/拡大表示(どちらか一方だけ表示)、右: ドロワー列。
        # ドロワーは両モードで共有するため、表示モードを切り替えても付け替えない。
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        main_area = QWidget()
        layout = QVBoxLayout(main_area)
        layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(main_area, 1)

        self._build_page_grid(layout)
        self._build_zoom_view(layout)

        root_layout.addWidget(self._build_annotation_drawer())
        root_layout.addWidget(self._build_bookmarks_panel())
        root_layout.addWidget(self._build_pii_drawer())
        root_layout.addWidget(self._build_ocr_drawer())
        self._set_zoom_annotation_drawer_open(False)
        self._set_selected_zoom_annotation(None)

    def _build_page_grid(self, layout: QVBoxLayout) -> None:
        """ページサムネイル一覧(グリッド)と選択・ドロップ表示部品を組み立てる。"""
        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
        """拡大表示ビュー(操作バー+キャンバス)を組み立てる。ドロワーは _setup_ui が右列に置く。"""
        self._zoom_view = QWidget()
        zoom_layout = QVBoxLayout(self._zoom_view)
        zoom_layout.setContentsMargins(0, 0, 0, 0)

        zoom_layout.addWidget(self._build_zoom_controls())
        zoom_layout.addWidget(self._build_zoom_canvas(), 1)

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

        # 右側ドロワー(アノテーション/個人情報検出/OCR/しおり)を1つのドロップダウンに
        # まとめる。ボタン表示は開いているパネル名(無ければ「パネル」)。項目は
        # 同時に1つだけ選べ(全て外すことも可)、選択済みの項目を選ぶと閉じる。
        # ドロワー同士の排他はそれぞれの開閉ハンドラが担う。
        self._zoom_panel_btn = QPushButton(self.ZOOM_PANEL_BUTTON_DEFAULT_TEXT)
        self._zoom_panel_btn.setCheckable(True)
        self._zoom_panel_btn.setToolTip("右側のパネル(アノテーション/個人情報検出/OCR/しおり)を選択")
        self._zoom_panel_menu = QMenu(self._zoom_panel_btn)
        self._zoom_panel_menu.setToolTipsVisible(True)
        self._zoom_panel_group = QActionGroup(self._zoom_panel_menu)
        self._zoom_panel_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        # 属性名は従来のボタンのまま(setEnabled/setChecked/trigger は QAction にもある)。
        self._zoom_object_btn = self._zoom_panel_menu.addAction("アノテーション")
        self._zoom_object_btn.setToolTip("付箋編集")
        self._zoom_pii_btn = self._zoom_panel_menu.addAction("個人情報検出")
        self._zoom_pii_btn.setToolTip("個人情報(PII)の検出・ハイライト・黒塗りエクスポート")
        self._zoom_ocr_action = self._zoom_panel_menu.addAction("OCR")
        self._zoom_ocr_action.setToolTip("文字認識(OCR)して、検索・個人情報検出で使えるテキストを埋め込む")
        self._zoom_bookmark_action = self._zoom_panel_menu.addAction("しおり")
        self._zoom_bookmark_action.setToolTip("しおり編集")
        # 旧しおりボタンの属性名も QAction へ向ける(text/isChecked/setChecked/trigger は共通)。
        self._zoom_bookmark_btn = self._zoom_bookmark_action
        for action in (
            self._zoom_object_btn,
            self._zoom_pii_btn,
            self._zoom_ocr_action,
            self._zoom_bookmark_action,
        ):
            action.setCheckable(True)
            self._zoom_panel_group.addAction(action)
        self._zoom_object_btn.triggered.connect(self._on_zoom_panel_annotation_triggered)
        self._zoom_pii_btn.triggered.connect(self._on_zoom_panel_pii_triggered)
        self._zoom_ocr_action.triggered.connect(self._on_zoom_panel_ocr_triggered)
        self._zoom_bookmark_action.triggered.connect(self._on_zoom_panel_bookmarks_triggered)
        # ボタンのクリックでメニューを開いた後にチェック状態がずれないよう、
        # メニューが閉じたら実際のドロワーの開閉状態に合わせ直す。
        self._zoom_panel_menu.aboutToHide.connect(
            lambda: QTimer.singleShot(0, self._sync_zoom_panel_button)
        )
        self._zoom_panel_btn.setMenu(self._zoom_panel_menu)
        controls_layout.addWidget(self._zoom_panel_btn)

        # ページ表示レイアウト。クリックで選択肢を開き、1枚を選ぶと閲覧専用を解除する。
        self._zoom_spread_btn = QPushButton("ページ表示")
        self._zoom_spread_btn.setToolTip("ページ表示方法を選択")
        self._zoom_layout_menu = QMenu(self._zoom_spread_btn)
        for layout in ZoomPageLayout:
            action = self._zoom_layout_menu.addAction(layout.label)
            action.setCheckable(True)
            action.setToolTip(
                "通常表示・編集可" if layout is ZoomPageLayout.SINGLE else "閲覧専用"
            )
            action.triggered.connect(
                lambda _checked=False, selected=layout: self._set_zoom_page_layout(selected)
            )
            self._zoom_layout_actions[layout] = action
        self._zoom_spread_btn.setMenu(self._zoom_layout_menu)
        controls_layout.addWidget(self._zoom_spread_btn)

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
        self._zoom_label.interaction_started.connect(self._flush_zoom_annotation_form_commit)
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
        self._zoom_label.text_selection_released.connect(self._on_zoom_text_selection_released)
        self._zoom_label.text_select_only_escape_requested.connect(
            lambda: self._activate_create_mode(CreateMode.NONE)
        )
        self._zoom_label.create_tool_escape_requested.connect(self._on_create_tool_escape_requested)
        self._zoom_label.scroll_requested.connect(self._on_zoom_scroll_requested)
        self._zoom_label.zoom_region_requested.connect(self._on_zoom_region_requested)
        self._zoom_scroll.setWidget(self._zoom_label)
        return self._zoom_scroll


    def _build_bookmarks_panel(self) -> "BookmarksPanel":
        """しおり(アウトライン)編集ドロワーを組み立てる。"""
        self._bookmarks_panel = BookmarksPanel()
        self._bookmarks_panel.set_current_page_provider(self._bookmark_current_page)
        if hasattr(self._bookmarks_panel, "set_page_count_provider"):
            self._bookmarks_panel.set_page_count_provider(
                lambda: get_page_count(self._pdf_path)
            )
        self._bookmarks_panel.bookmarks_changed.connect(self._run_toc_update)
        # しおり編集中に見送った再読込(下の _reload_bookmarks_tree 参照)を編集終了後に1回だけ行う。
        self._bookmarks_reload_deferred = False
        self._bookmarks_panel.editing_finished.connect(self._on_bookmarks_editing_finished)
        self._bookmarks_panel.jump_requested.connect(self._jump_zoom_to_page)
        self._bookmarks_panel.note_jump_requested.connect(self._on_bookmark_note_jump)
        self._bookmarks_panel.open_changed.connect(self._on_bookmarks_drawer_open_changed)
        # 開閉トグルは「パネル」ドロップダウンへ移設したため内蔵トグルを隠す
        self._bookmarks_panel.use_external_toggle()
        return self._bookmarks_panel


    def _on_zoom_panel_annotation_triggered(self, checked: bool) -> None:
        """パネルメニューの「アノテーション」。チェックされたら開き、外されたら閉じる。"""
        self._set_zoom_annotation_drawer_open(bool(checked))

    def _on_zoom_panel_pii_triggered(self, checked: bool) -> None:
        """パネルメニューの「個人情報検出」。チェックされたら開き、外されたら閉じる。"""
        panel = getattr(self, "_pii_panel", None)
        if panel is not None:
            panel.set_open(bool(checked))
        self._sync_zoom_panel_button()

    def _on_zoom_panel_ocr_triggered(self, checked: bool) -> None:
        """パネルメニューの「OCR」。チェックされたら開き、外されたら閉じる。"""
        panel = getattr(self, "_ocr_panel", None)
        if panel is not None:
            panel.set_open(bool(checked))
        self._sync_zoom_panel_button()

    def _on_zoom_panel_bookmarks_triggered(self, checked: bool) -> None:
        """パネルメニューの「しおり」。チェックされたら開き、外されたら閉じる。"""
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_open(bool(checked))
        self._sync_zoom_panel_button()

    def _panel_buttons(self) -> list:
        """パネルのドロップダウンボタン(拡大表示バーとページ一覧ツールバーの両方)。"""
        return [
            b
            for b in (
                getattr(self, "_zoom_panel_btn", None),
                getattr(self, "_grid_panel_btn", None),
            )
            if b is not None
        ]

    def _sync_zoom_panel_button(self) -> None:
        """パネルボタンの表示(開いているパネル名/「パネル」)とチェック状態を実状態に合わせる。"""
        buttons = self._panel_buttons()
        if not buttons:
            return
        pii_panel = getattr(self, "_pii_panel", None)
        ocr_panel = getattr(self, "_ocr_panel", None)
        bookmarks_panel = getattr(self, "_bookmarks_panel", None)
        if self._zoom_annotation_open:
            name = self._zoom_object_btn.text()
        elif pii_panel is not None and pii_panel.is_open:
            name = self._zoom_pii_btn.text()
        elif ocr_panel is not None and ocr_panel.is_open:
            name = self._zoom_ocr_action.text()
        elif bookmarks_panel is not None and bookmarks_panel.is_open:
            name = self._zoom_bookmark_action.text()
        else:
            name = None
        for button in buttons:
            button.setText(name or self.ZOOM_PANEL_BUTTON_DEFAULT_TEXT)
            button.setChecked(name is not None)
        # メニュー側のチェックも実状態へ(プログラムから開閉された場合の同期)。
        self._zoom_object_btn.setChecked(bool(self._zoom_annotation_open))
        self._zoom_pii_btn.setChecked(bool(pii_panel is not None and pii_panel.is_open))
        self._zoom_ocr_action.setChecked(bool(ocr_panel is not None and ocr_panel.is_open))
        self._zoom_bookmark_action.setChecked(
            bool(bookmarks_panel is not None and bookmarks_panel.is_open)
        )

    def _zoom_view_shown(self) -> bool:
        """拡大表示(ビュー)に切り替わっているか(ウィンドウ未表示でも切替状態で判定する)。"""
        view = getattr(self, "_zoom_view", None)
        return view is not None and not view.isHidden()

    def _canvas_available(self) -> bool:
        """ページ画面(単ページの拡大表示)が使えるか。ページ一覧・複数ページ表示では False。"""
        return self._zoom_view_shown() and not self._zoom_page_layout_is_multi()

    def _apply_panel_context(self) -> None:
        """表示モード(ページ一覧/拡大)に合わせて、各パネルの画面依存の操作を有効/無効にする。"""
        canvas = self._canvas_available()
        self._set_annotation_canvas_available(canvas)
        # 拡大表示バーにも同じ「パネル」があるので、拡大表示中はツールバー側を隠す。
        grid_action = getattr(self, "_grid_panel_action", None)
        if grid_action is not None:
            grid_action.setVisible(not self._zoom_view_shown())
        pii_panel = getattr(self, "_pii_panel", None)
        if pii_panel is not None:
            pii_panel.set_canvas_available(canvas)
        ocr_panel = getattr(self, "_ocr_panel", None)
        if ocr_panel is not None:
            ocr_panel.set_canvas_available(canvas)
        if not canvas and self._create_mode is not CreateMode.NONE:
            # ページ一覧へ戻る/複数ページ表示へ移るとき、作成ツールの装着を解除する。
            self._activate_create_mode(CreateMode.NONE)
        self._sync_bookmark_page_availability()

    def _bookmark_current_page(self) -> "int | None":
        """しおり追加の対象ページ(1始まり)。ページ一覧では選択中の先頭ページ、無ければ None。"""
        if self._zoom_view_shown():
            return (self._zoom_page_num or 0) + 1
        selected = [t.page_num for t in self._selected_thumbnails]
        return min(selected) + 1 if selected else None

    def _sync_bookmark_page_availability(self) -> None:
        panel = getattr(self, "_bookmarks_panel", None)
        setter = getattr(panel, "set_current_page_available", None)
        if setter is not None:
            setter(self._bookmark_current_page() is not None)

    def _toggle_bookmarks_drawer(self) -> None:
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_open(not panel.is_open)

    def _zoom_page_layout_is_multi(self) -> bool:
        return self._zoom_page_layout is not ZoomPageLayout.SINGLE

    def _zoom_page_capacity(self) -> int:
        return self._zoom_page_layout.page_capacity

    def _last_zoom_group_start(self, page_count: int) -> int:
        if page_count <= 0:
            return 0
        last_index = page_count - 1
        capacity = self._zoom_page_capacity()
        return last_index - (last_index % capacity)

    def _sync_zoom_page_layout_controls(self) -> None:
        selected = getattr(self, "_zoom_page_layout", ZoomPageLayout.SINGLE)
        for layout, action in getattr(self, "_zoom_layout_actions", {}).items():
            action.setChecked(layout is selected)

    def _set_zoom_page_layout(self, layout: ZoomPageLayout) -> None:
        """拡大表示のページレイアウトを切り替える。"""
        if not isinstance(layout, ZoomPageLayout):
            layout = ZoomPageLayout.SINGLE
        self._commit_inline_annotation_editor()
        is_multi = layout is not ZoomPageLayout.SINGLE
        if is_multi:
            # 閲覧専用モードへ入る前に、編集系の状態を全て終了させる。
            self._set_zoom_annotation_create_mode(False)
            self._set_selected_zoom_annotation(None)
            if self._zoom_annotation_open:
                self._set_zoom_annotation_drawer_open(False)
            pii_panel = getattr(self, "_pii_panel", None)
            if pii_panel is not None and pii_panel.is_open:
                pii_panel.set_open(False)
            ocr_panel = getattr(self, "_ocr_panel", None)
            if ocr_panel is not None and ocr_panel.is_open:
                ocr_panel.set_open(False)
            # ページ送りの単位に合わせて先頭ページへ正規化する。
            if self._zoom_page_num is not None:
                capacity = layout.page_capacity
                self._zoom_page_num -= self._zoom_page_num % capacity

        self._zoom_page_layout = layout
        if self._zoom_label:
            self._zoom_label.set_view_only(is_multi)
        # 複数ページ表示中はアノテーション(付箋)/個人情報検出ドロワーを無効化する。
        self._set_zoom_panel_actions_enabled(not is_multi)
        self._apply_panel_context()
        self._sync_zoom_page_layout_controls()
        # 複数ページ表示中はしおりを閲覧/ジャンプ専用にし、回転・削除ボタンを無効化する。
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_read_only(is_multi)
        # Undo/Redo も閲覧専用中は操作させない。
        self._undo_btn.setEnabled(not is_multi and self._undo_manager.can_undo())
        self._redo_btn.setEnabled(not is_multi and self._undo_manager.can_redo())
        self._update_button_states()
        self._render_zoom()

    def _set_zoom_panel_actions_enabled(self, enabled: bool) -> None:
        """パネルメニューのアノテーション/個人情報検出/OCRの有効/無効を切り替える(しおりは対象外)。"""
        if getattr(self, "_zoom_object_btn", None) is not None:
            self._zoom_object_btn.setEnabled(enabled)
        if getattr(self, "_zoom_pii_btn", None) is not None:
            self._zoom_pii_btn.setEnabled(enabled)
        if getattr(self, "_zoom_ocr_action", None) is not None:
            self._zoom_ocr_action.setEnabled(enabled)
        # しおりは複数ページ表示でも閲覧/ジャンプ専用で使えるので、パネルボタンと
        # 「しおり」項目は有効のまま(上の3項目だけをグレーアウトする)。

    def _toggle_zoom_spread_view(self) -> None:
        """旧来の見開き切替呼び出しを横2枚の選択へ互換接続する。"""
        layout = (
            ZoomPageLayout.SINGLE
            if self._zoom_page_layout_is_multi()
            else ZoomPageLayout.HORIZONTAL
        )
        self._set_zoom_page_layout(layout)

    def _on_bookmarks_drawer_open_changed(self, is_open: bool) -> None:
        self._sync_zoom_panel_button()
        if is_open:
            # 付箋/個人情報検出ドロワーと排他にする
            if self._zoom_annotation_open:
                self._set_zoom_annotation_drawer_open(False)
            pii_panel = getattr(self, "_pii_panel", None)
            if pii_panel is not None and pii_panel.is_open:
                pii_panel.set_open(False)
            ocr_panel = getattr(self, "_ocr_panel", None)
            if ocr_panel is not None and ocr_panel.is_open:
                ocr_panel.set_open(False)
            self._reload_bookmarks_tree()

    def _reload_bookmarks_tree(self) -> None:
        """ディスク上のしおりでツリーを再構築する。ドロワーが閉じていれば何もしない。"""
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is None or not panel.is_open:
            return
        if panel.is_editing():
            # 編集中に再構築すると入力欄と追加中の項目が失われる。編集が閉じてから1回だけ読み直す。
            self._bookmarks_reload_deferred = True
            return
        self._bookmarks_reload_deferred = False
        panel.load_entries(get_pdf_toc(self._pdf_path))
        self._reload_bookmark_notes()

    def _on_bookmarks_editing_finished(self) -> None:
        if getattr(self, "_bookmarks_reload_deferred", False):
            self._reload_bookmarks_tree()

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
        """しおりパネルの付箋ノードクリックで、該当ページへ移動し付箋を選択する。

        付箋の選択はページ画面が要るので、ページ一覧からは該当ページの拡大表示へ切り替える。
        """
        if not self._zoom_view_shown():
            page_count = get_page_count(self._pdf_path)
            if page_count <= 0:
                return
            self._open_zoom_view(max(0, min(page_one_based - 1, page_count - 1)))
        self._jump_zoom_to_page(page_one_based)
        note = self._find_zoom_annotation(xref)
        if isinstance(note, NoteAnnotData):
            self._set_selected_zoom_annotation(note, open_drawer=False)

    def _jump_zoom_to_page(self, page_one_based: int) -> None:
        """しおりクリック時に該当ページ(1始まり)へ移動する。ページ一覧ではそのサムネイルを選択する。"""
        zoom_shown = self._zoom_view_shown()
        if zoom_shown and self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            return
        target = max(0, min(page_one_based - 1, page_count - 1))
        if not zoom_shown:
            self._select_page_thumbnail(target)
            return
        self._commit_inline_annotation_editor()
        self._release_create_mode_for_page_move()
        self._selected_zoom_annotation = None
        self._zoom_page_num = target
        self._render_zoom()

    def _run_toc_update(self, new_entries: list[TocEntry], description: str) -> None:
        """しおり変更を PDF に保存し、Undo/Redo に登録する(TOC全体スナップショット方式)。"""
        if not self._ensure_saved("しおりの変更"):
            # パネルは編集後の表示になっているので、ディスク上の真値に戻す
            self._reload_bookmarks_tree()
            return
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
        self._add_undo_action(UndoAction(
            description=description,
            undo_func=undo_update,
            redo_func=lambda: do_update(True),
            writes_file=True,
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

        # 手動保存モードのときだけ表示する「保存」ボタン(未保存の変更があるときだけ有効)。
        self._save_btn = QPushButton("保存")
        self._save_btn.setToolTip("編集内容をファイルへ保存 (Ctrl+S)")
        self._save_btn.clicked.connect(self._on_save)
        self._save_action = toolbar.addWidget(self._save_btn)
        self._save_action.setVisible(self._session is not None)

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

        # 拡大表示バーの「パネル」と同じ QAction を共有する(チェック状態が連動する)。
        # 画面(ページ)が要る機能はページ一覧ではパネル内でグレーアウトされる。
        self._grid_panel_btn = QPushButton(self.ZOOM_PANEL_BUTTON_DEFAULT_TEXT)
        self._grid_panel_btn.setCheckable(True)
        self._grid_panel_btn.setToolTip(self._zoom_panel_btn.toolTip())
        self._grid_panel_menu = QMenu(self._grid_panel_btn)
        self._grid_panel_menu.setToolTipsVisible(True)
        for action in self._zoom_panel_group.actions():
            self._grid_panel_menu.addAction(action)
        self._grid_panel_menu.aboutToHide.connect(
            lambda: QTimer.singleShot(0, self._sync_zoom_panel_button)
        )
        self._grid_panel_btn.setMenu(self._grid_panel_menu)
        # QToolBar に載せたウィジェットの表示切替は、返された QAction で行う。
        self._grid_panel_action = toolbar.addWidget(self._grid_panel_btn)

        self._apply_panel_context()
        self._sync_zoom_panel_button()
        self._update_button_states()

    def _setup_shortcuts(self) -> None:
        register_shortcuts(
            self,
            (
                (QKeySequence.StandardKey.Undo, self._on_undo),
                (QKeySequence.StandardKey.Redo, self._on_redo),
                (QKeySequence.StandardKey.Save, self._on_save_shortcut),
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
        if isinstance(error, PdfSessionConflictError):
            return  # 保存の確認をキャンセルされた(ほかのアプリが使用中なのではない)ので、案内は出さない
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
        affects_pages: bool = True,
        writes_file: bool = False,
    ) -> bool:
        """do_func を実行し、成功時のみ Undo/Redo 履歴に登録する。

        ``affects_pages=False`` は注釈の編集だけの操作(ページ構成は変わらない)。Undo/Redo
        後にページ一覧を作り直さず表示中のページだけ更新する。do/undo の各関数が
        ``_refresh_current_zoom_page`` 等で自分の画面更新を行うこと。

        ``writes_file=True`` はファイルを直接書き換える操作(ページ構成など。手動保存モードでは
        メモリ上で編集できない)。Undo/Redo の前に保存を求める。

        PdfWritePermissionError 時は警告ダイアログを表示して False を返す
        (履歴には積まない)。redo には do_func をそのまま使う。
        """
        # PII注釈の restyle ジョブはPDFを開いたままなので、書き込み前に止める
        # (Windows では全体保存の置き換えに失敗する)。終わったら走り直す。
        # PDFへ書き込む新しい経路を足すときも同じガードが必要。
        with self._pii_restyle_paused():
            try:
                do_func()
            except PdfWritePermissionError as error:
                self._handle_pdf_write_permission_denied(
                    error, selected_annotation=selected_annotation_on_error
                )
                return False
            except Exception:
                if self._session is None:
                    raise
                # 手動保存モード: 途中までの変更がメモリ上のドキュメントに残っている(pdf_utils 側で
                # 未保存扱いにしてある)。画面を実際の内容に合わせ直し、履歴には積まない。
                logger.exception("手動保存モードの編集に失敗しました: %s", description)
                self._refresh_current_zoom_page()
                self._flash_zoom_hint("操作に失敗しました。画面を再表示しました")
                return False
        self._add_undo_action(UndoAction(
            description=description,
            undo_func=undo_func,
            redo_func=do_func,
            affects_pages=affects_pages,
            writes_file=writes_file,
        ))
        return True

    def _add_undo_action(self, action: UndoAction) -> None:
        """共有 UndoManager へ操作を積む。手動保存モードでは未保存分としてこのウィンドウを記録する。"""
        if self._session is not None and not action.writes_file:
            action.owner = self._undo_owner
        self._undo_manager.add_action(action)

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
        # 複数ページ表示(閲覧専用)中はページ編集(回転・削除)を不可にする。
        spread = self._zoom_page_layout_is_multi()
        can_edit_pages = (has_selection or zoom_active) and not spread
        self._delete_btn.setEnabled(can_edit_pages)
        self._rename_btn.setEnabled(True)
        self._rotate_btn.setEnabled(can_edit_pages)
        self._undo_btn.setEnabled(not spread and self._undo_manager.can_undo())
        self._redo_btn.setEnabled(not spread and self._undo_manager.can_redo())
        self._update_save_button()
        self._sync_bookmark_page_availability()

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

    # --- 手動保存モード(メモリ上で編集し、保存ボタンでファイルへ書く) ----------------
    # 手動保存では pdf_utils の注釈・描画系の関数が、このウィンドウのセッションが保持する
    # ドキュメントを使う(common.PdfSession)。ファイルを別経路で書き換える操作(ページ構成・
    # しおり・タイトル・ファイル名・OCR/検出のワーカー・エクスポート・印刷など)は、
    # 先に _ensure_saved() で保存してから行う。保存済みなら pdf_utils 側の書き込みが
    # 保持ハンドルを自動で閉じる(Windows では開いたままだと置き換えできない)。

    def _open_manual_session(self) -> None:
        try:
            session = open_session(self._pdf_path)
        except Exception:  # noqa: BLE001 - 開けなければ自動保存で続ける
            logger.warning(
                "手動保存モードを開始できないため、自動保存で開きます: %s",
                self._pdf_path,
                exc_info=True,
            )
            return
        session.add_dirty_listener(self._on_session_dirty_changed)
        session.before_external_write = self._on_external_write_requested
        self._session = session

    def _session_dirty(self) -> bool:
        return self._session is not None and self._session.dirty()

    def _on_session_dirty_changed(self) -> None:
        """未保存の状態が変わったとき(未保存になった・保存した)の画面更新。"""
        if self._pending_widget_pages is None:
            self.setWindowTitle(self._page_edit_window_title())
        self._update_save_button()

    def _save_blocked_by_worker(self) -> bool:
        """OCR・個人情報検出のワーカー実行中は保存しない(ワーカーはファイルを読んでいるため)。"""
        return self._ocr_busy() or getattr(self, "_pii_worker", None) is not None

    def _update_save_button(self) -> None:
        if self._save_btn is None:
            return
        self._save_btn.setEnabled(self._session_dirty() and not self._save_blocked_by_worker())

    def _on_save_shortcut(self) -> None:
        # 自動保存モードでは何もしない(Ctrl+S は手動保存モード専用)。
        if self._session is not None:
            self._on_save()

    def _on_save(self) -> bool:
        """未保存の変更をファイルへ書く。保存できた(または保存不要だった)ら True。"""
        session = self._session
        if session is None:
            return True
        # 編集中のテキスト・未確定のフォーム操作を先に確定する(メモリ上のドキュメントへ反映)。
        self._commit_inline_annotation_editor()
        if not session.dirty():
            return True
        if self._save_blocked_by_worker():
            self._flash_zoom_hint("OCR・個人情報検出の実行中は保存できません")
            return False
        self._flash_zoom_hint("保存中...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        error: PdfWritePermissionError | None = None
        try:
            QApplication.processEvents()
            # restyle ジョブはPDFを開いたままなので、書き込みの間だけ止める。
            with self._pii_restyle_paused():
                session.save()
        except PdfWritePermissionError as exc:
            error = exc
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            self._handle_pdf_write_permission_denied(error)
            return False
        # 保存した操作は、以後このウィンドウの「未保存分」として破棄の対象にしない。
        self._undo_manager.release_owner(self._undo_owner)
        self._flash_zoom_hint("保存しました")
        self._update_button_states()
        return True

    def _ensure_saved(self, reason: str, *, silent: bool = False, release: bool = False) -> bool:
        """ファイルを直接読み書きする操作の前に呼ぶ。未保存なら保存を確認し、保存できたら True。

        *silent* は確認なしで保存する(すでにユーザーが保存を了承した流れの続き用)。
        *release* は、続けてファイル名変更・ゴミ箱送りなど保持ハンドルがあると失敗する
        操作をするとき、保存後にハンドルを閉じる(次に読まれたときにディスクから開き直す)。
        自動保存モード(セッション無し)では常に True を返して何もしない。
        """
        session = self._session
        if session is None:
            return True
        self._commit_inline_annotation_editor()
        if session.dirty():
            if not silent:
                answer = QMessageBox.question(
                    self,
                    "保存",
                    f"この操作({reason})の前に保存が必要です。\n保存しますか?",
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save,
                )
                if answer != QMessageBox.StandardButton.Save:
                    return False
            if not self._on_save():
                return False
        if release:
            self._release_session_handle()
        return True

    def _release_session_handle(self) -> None:
        """保持ハンドルを閉じる。実行中の restyle ジョブは閉じたドキュメントを掴んだままになるので、先に止めて走り直す。"""
        session = self._session
        if session is None:
            return
        was_dirty = session.dirty()
        was_active = self._abort_pii_restyle_job()
        try:
            # 中断した restyle が残した途中経過だけが未保存の内容なら、捨ててよい(再実行で収束する)。
            session.release(force=not was_dirty)
        finally:
            if was_active:
                self._schedule_pii_restyle()

    def _on_external_write_requested(self) -> bool:
        """未保存のままほかの経路でファイルへ書かれそうなとき(pdf_utils から)の保存確認。"""
        return self._ensure_saved("ほかの書き込み操作")

    def _on_pdf_path_changed(self, new_path: str) -> None:
        """ファイル名変更の後に呼ぶ。パスを更新し、手動保存ではセッションを新しいパスで開き直す。"""
        self._pdf_path = new_path
        session = self._session
        if session is not None:
            # 改名できた時点で保持ハンドルは無く(あれば OS が拒否する)、変更も保存済み。
            session.close(discard=True)
            self._session = None
            self._open_manual_session()
        self.setWindowTitle(self._page_edit_window_title())

    def _finish_manual_session_on_close(self) -> bool:
        """ウィンドウを閉じる前の保存確認とセッションの後始末。閉じてよければ True(キャンセルは False)。

        未保存なら 保存 / 破棄 / キャンセル。保存 → restyle を反映 → 保存 → セッションを閉じる。
        破棄 → restyle は反映せずセッションを閉じ、共有 UndoManager から未保存分の操作を取り除く。
        """
        session = self._session
        if session is None:
            return True
        self._commit_inline_annotation_editor()
        # 実行中の restyle を止める(途中までの変更は未保存として残る)。予約だけのものも止まる。
        was_dirty = session.dirty()
        restyle_pending = self._abort_pii_restyle_job()
        discard = False
        if not was_dirty and restyle_pending:
            # ユーザーの編集は無く、PII注釈の色・表示状態の反映だけが残っている: 自動保存モードと同じく
            # 確認なしで反映して保存する(書けなければ諦めて閉じる。次に開いたとき再度反映される)。
            self._run_pii_restyle_now()
            try:
                session.save()
            except Exception:  # noqa: BLE001 - 見た目の同期失敗で閉じる操作を止めない
                logger.warning("PII注釈の反映を保存できませんでした: %s", self._pdf_path, exc_info=True)
                discard = True
        elif session.dirty():
            Button = QMessageBox.StandardButton
            answer = QMessageBox.question(
                self,
                "保存",
                "保存していない変更があります。\n保存しますか?",
                Button.Save | Button.Discard | Button.Cancel,
                Button.Save,
            )
            if answer == Button.Save:
                if restyle_pending:
                    self._run_pii_restyle_now()
                if not self._on_save():
                    return False
            elif answer == Button.Discard:
                discard = True
            else:
                if restyle_pending:
                    self._schedule_pii_restyle()
                return False
        self._session = None
        try:
            session.close(discard=discard)
        finally:
            if discard:
                self._undo_manager.purge_owner(self._undo_owner)
            else:
                self._undo_manager.release_owner(self._undo_owner)
        return True

    def _prepare_session_for_reload(self) -> bool:
        """ディスクから読み直す前の準備。続けてよければ True。

        保存済みなら保持ハンドルを閉じるだけ(次に読まれたときにディスクから開き直す)。
        未保存の変更があるときは、外部で変更された場合に失われることを確認する。
        """
        session = self._session
        if session is None:
            return True
        if session.dirty():
            if not session.disk_changed_externally():
                # ファイルは自分が最後に読み書きしたままなので、読み直す意味が無い(F5 や
                # 自分の書き込みの余波)。未保存の編集を捨てる確認はせず、そのまま残す。
                return False
            answer = QMessageBox.question(
                self,
                "再読み込み",
                "ファイルが外部で変更されました。\n再読み込みすると、保存していない変更は失われます。"
                "\n再読み込みしますか?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self._abort_pii_restyle_job()
            session.close(discard=True)
            self._session = None
            self._undo_manager.purge_owner(self._undo_owner)
            self._open_manual_session()
            self._update_button_states()
            self.setWindowTitle(self._page_edit_window_title())
            return True
        self._release_session_handle()
        return True

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
        if self._is_heavy_document:
            # 重量文書は全ページを一括でキューへ積まない。表示範囲のページだけ
            # 描画し、残りはスクロールに応じて逐次描画する(オンデマンド方式)。
            # そうしないと数千ページ分の描画ジョブが一度に走り、CPU を長時間
            # 占有した上、256 件しかない固定サイズキャッシュを次々追い出して
            # 表示中のサムネイルまで再描画させてしまう。
            self._enqueue_visible_thumbnail_renders()
            return
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

    def _get_ink_xrefs_by_page(
        self, pages: "list[int] | None" = None
    ) -> dict[int, list[int]]:
        """Ink 注釈 xref をページ番号ごとに取得する(結果はキャッシュ)。

        通常文書は文書全体を一度だけ集計する。重量文書で ``pages`` を渡したときは、
        まだ走査していないページ分だけ集計して足す(全ページ走査でフリーズするのを避ける)。
        ``pages`` 以外のページ分はキャッシュに無いことがあるので、呼び出し側は渡した
        ページだけを参照すること。
        """
        if self._ink_xrefs_by_page_cache is None:
            self._ink_xrefs_scanned_pages = set()
        if self._is_heavy_document and pages is not None:
            if self._ink_xrefs_by_page_cache is None:
                self._ink_xrefs_by_page_cache = {}
            scanned = self._ink_xrefs_scanned_pages
            if scanned is not None:
                missing = [pn for pn in pages if pn not in scanned]
                if missing:
                    self._ink_xrefs_by_page_cache.update(
                        list_ink_annot_xrefs_by_page(self._pdf_path, missing)
                    )
                    scanned.update(missing)
            return self._ink_xrefs_by_page_cache
        if self._ink_xrefs_by_page_cache is None or self._ink_xrefs_scanned_pages is not None:
            self._ink_xrefs_by_page_cache = list_ink_annot_xrefs_by_page(self._pdf_path)
            self._ink_xrefs_scanned_pages = None  # 全ページ走査済み
        return self._ink_xrefs_by_page_cache

    def _get_pii_targets_by_page(
        self, pages: "list[int] | None" = None
    ) -> "dict[int, tuple[tuple[float, float], list]]":
        """ページ番号 -> (表示座標系のページサイズ, 塗りつぶし対象の一覧)。

        重量文書で ``pages`` を渡したときは、そのページのうち未走査の分だけ集計する
        (全ページ走査でフリーズするのを避ける)。その場合 ``pages`` 以外のページ分は
        含まれないことがあるので、呼び出し側は渡したページだけを参照すること。
        """
        partial = self._is_heavy_document and pages is not None
        token = _get_file_cache_token(self._pdf_path)
        cached = self._pii_targets_by_page_cache
        if cached is None:
            self._pii_targets_scanned_pages = set()
        scanned = self._pii_targets_scanned_pages  # None = 全ページ走査済み
        if cached is not None and cached[0] != token:
            # 自分の注釈書き込みだけでトークンが変わったなら、変更したページだけ再スキャンする。
            # 外部変更・ページ構成変更が挟まって追跡できない(None)ときは全ページ再スキャン。
            changed = pages_changed_since(self._pdf_path, cached[0])
            if changed is not None:
                by_page = dict(cached[1])
                for pn in changed:
                    by_page.pop(pn, None)
                if scanned is None:
                    by_page.update(list_pii_targets_by_page(self._pdf_path, changed))
                else:
                    # 部分キャッシュ: 変えたページは未走査に戻し、必要になったとき再集計する。
                    scanned.difference_update(changed)
                cached = (token, by_page)
                self._pii_targets_by_page_cache = cached
            else:
                cached = None
                self._pii_targets_scanned_pages = scanned = set()
        if partial:
            if cached is None:
                cached = (token, {})
                self._pii_targets_by_page_cache = cached
            if scanned is not None:
                missing = [pn for pn in pages if pn not in scanned]
                if missing:
                    cached[1].update(list_pii_targets_by_page(self._pdf_path, missing))
                    scanned.update(missing)
            return cached[1]
        if cached is not None and scanned is None:
            return cached[1]
        # ドキュメントを1回だけ開く(PIIのあるページ数に依存しない)。
        by_page = list_pii_targets_by_page(self._pdf_path)
        self._pii_targets_by_page_cache = (token, by_page)
        self._pii_targets_scanned_pages = None  # 全ページ走査済み
        return by_page

    def _sync_grid_pii_thumbnails(self) -> None:
        """塗りつぶし対象が変わったページのサムネイルを描き直す(ページ一覧へ戻ったとき・一覧での操作後)。

        サムネイルは描画時に対象の注釈をページ画像から隠して重ね描きするため、
        対象の増減は画像ごと作り直さないと二重に見える。
        """
        if not self._thumbnails or not self._grid_pii_xrefs:
            return
        # 重量文書は描画済み(=集計済み)のページ分だけ見る(全ページ走査を避ける)。
        loaded = [pn for pn, t in enumerate(self._thumbnails) if t.thumbnail_loaded]
        targets = self._get_pii_targets_by_page(loaded if self._is_heavy_document else None)
        for pn, thumb in enumerate(self._thumbnails):
            if not thumb.thumbnail_loaded:
                continue
            current = tuple(t.xref for t in targets.get(pn, (None, ()))[1])
            if self._grid_pii_xrefs.get(pn, ()) != current:
                self._request_thumbnail_refresh(pn)

    def _invalidate_and_requeue_thumbnails(self) -> None:
        """全サムネイルを未読込状態に戻し、再描画キューへ積み直す。"""
        self._reset_thumbnail_render_queue()
        for thumb in self._thumbnails:
            thumb.invalidate_thumbnail()
        self._enqueue_all_thumbnail_renders()

    def _process_thumbnail_render_queue(self) -> None:
        batch: list[int] = []
        batch_limit = (
            max(1, app_settings.heavy_pdf_render_batch_size()) if self._is_heavy_document else 5
        )
        while self._thumb_render_queue and len(batch) < batch_limit:
            page_num = self._thumb_render_queue.popleft()
            self._thumb_render_queue_set.discard(page_num)
            if page_num < 0 or page_num >= len(self._thumbnails):
                continue
            thumb = self._thumbnails[page_num]
            if thumb._explicitly_hidden or thumb.thumbnail_loaded:
                continue
            batch.append(page_num)
        if batch:
            # 手書き(Ink)注釈を非表示にする設定なら、対象ページ分だけ xref を隠す。
            # 塗りつぶし対象(個人情報検出)も実PDF注釈の暗く薄い見た目では縮小時に
            # 判別できないため、ページ画像からは隠して後から見やすく重ね描きする。
            # 重量文書は描画バッチのページ分だけ集計する(全ページ走査でフリーズするため)。
            scan_pages = batch if self._is_heavy_document else None
            hide_xrefs_by_page: dict[int, set[int]] = {}
            # 走査と描画で同じドキュメントを使い回す(重量文書は開き直すたびに
            # 最初の load_page でページツリー解析が走り、バッチごとに約1秒ブロックするため)。
            # 重量文書はバッチをまたいで保持する(アイドル・閉じる・ファイル更新時に閉じる)。
            with hold_doc(self._pdf_path, linger=self._is_heavy_document):
                pii_targets_by_page = self._get_pii_targets_by_page(scan_pages)
                ink_xrefs_by_page = (
                    None if self._show_ink_annots else self._get_ink_xrefs_by_page(scan_pages)
                )
                for pn in batch:
                    xrefs: set[int] = set()
                    if ink_xrefs_by_page:
                        xrefs.update(ink_xrefs_by_page.get(pn, ()))
                    if pn in pii_targets_by_page:
                        xrefs.update(t.xref for t in pii_targets_by_page[pn][1])
                    if xrefs:
                        hide_xrefs_by_page[pn] = xrefs
                pixmaps = render_page_thumbnails_batch(
                    self._pdf_path, batch, self._thumb_size, hide_xrefs=hide_xrefs_by_page or None
                )
            pii_settings = self._pii_settings()
            pii_color = pii_settings.mask_color
            pii_opacity = pii_settings.mask_opacity
            pii_hidden = pii_settings.hidden_entities()
            for pn in batch:
                if pn < len(self._thumbnails):
                    pixmap = pixmaps.get(pn, QPixmap())
                    thumb = self._thumbnails[pn]
                    # 塗りつぶし対象はピクセルへ焼き込まず、サムネイルの paint 時に重ねる
                    # (色・透明度・種別の変更で画像を再レンダリングしなくて済む)。
                    if pn in pii_targets_by_page:
                        page_size, targets = pii_targets_by_page[pn]
                        thumb.set_pii_overlay(targets, page_size)
                    else:
                        thumb.set_pii_overlay([], (0.0, 0.0))
                    thumb.set_pii_mask_style(pii_color, pii_opacity, pii_hidden)
                    self._grid_pii_xrefs[pn] = tuple(
                        t.xref for t in pii_targets_by_page.get(pn, (None, ()))[1]
                    )
                    thumb.set_pixmap_direct(pixmap)
            if self._is_heavy_document:
                self._held_doc_idle_timer.start()
        self._schedule_thumbnail_render()

    def _release_held_doc(self) -> None:
        """バッチ間で保持している文書を閉じる(アイドル・閉じる・再読込・ファイル更新の前)。"""
        self._held_doc_idle_timer.stop()
        release_held_docs(self._pdf_path)

    def _on_grid_viewport_changed(self, _value: int) -> None:
        self._scroll_debounce_timer.start()  # デバウンス（スクロール停止150ms後に優先化）

    def _load_pages(self) -> None:
        self._release_held_doc()
        # ページ構成が変わるので、塗りつぶし対象のページ別集計は破棄して全ページ再集計させる。
        self._pii_targets_by_page_cache = None
        self._pii_result_rows_cache = None
        self._grid_pii_xrefs.clear()
        # ページ構成の変更(書き込み)の直後に呼ばれるので、PDFを開いたままの
        # PII restyle ジョブを止め、走り直しを予約する(デバウンスなので本処理の後に動く)。
        if self._abort_pii_restyle_job():
            self._schedule_pii_restyle()
        self._reset_thumbnail_render_queue()
        # ページ構成が変わるため、ページ別 Ink xref キャッシュも破棄する。
        self._ink_xrefs_by_page_cache = None
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

        self._refresh_page_bound_views()

        # ズームビューのページ番号を調整
        if self._zoom_page_num is not None and self._zoom_page_num >= page_count:
            self._zoom_page_num = max(0, page_count - 1)

        self._widget_build_generation += 1
        self._grid_laid_out_cols = None
        self._grid_resize_deferred = False
        self._is_heavy_document = is_heavy_pdf(self._pdf_path, page_count)

        if self._is_heavy_document:
            # 重量文書: サムネイルウィジェットを一度に生成すると数千個の QWidget
            # 生成が UI スレッドを長時間ブロックしフリーズしたように見えるため、
            # チャンクに分けてイベントループへ制御を返しながら生成する。
            # グリッドへの反映(_refresh_grid())は全ウィジェット生成後に1回だけ
            # 行う。チャンクごとに呼ぶと、その時点までの全ウィジェットを毎回
            # 並べ直すことになり O(ページ数^2) になってしまい、後半のチャンクほど
            # 処理が重くなって結局フリーズしたように見えてしまうため。
            self._pending_widget_pages = iter(range(page_count))
            self._pending_widget_total = page_count
            self._build_thumbnail_widgets_chunk(self._widget_build_generation)
        else:
            for i in range(page_count):
                thumb = PageThumbnail(self._pdf_path, i, thumb_size=self._thumb_size)
                thumb.clicked.connect(self._on_thumbnail_clicked)
                self._thumbnails.append(thumb)

            self._refresh_grid()
            self._enqueue_all_thumbnail_renders()
            if self._zoom_view and self._zoom_view.isVisible():
                self._render_zoom()

    def _refresh_page_bound_views(self) -> None:
        """ページ構成が変わった(並べ替え・削除・挿入・Undo/Redo)後に、ページ番号を持つ表示を追従させる。

        注釈・OCR・しおりの実体は PDF 側にありページと一緒に動くが、個人情報検出の結果一覧
        (行が ``page_num`` を持つ)としおりツリーは画面側のコピーなので作り直す。
        """
        self._reload_pii_results()
        self._reload_bookmarks_tree()

    def _page_edit_window_title(self) -> str:
        # 手動保存モードで未保存の変更があるときは末尾に " *" を付ける。
        mark = " *" if self._session is not None and self._session.dirty() else ""
        return f"JusticePDF - 編集:{os.path.basename(self._pdf_path)}{mark}"

    def _build_chunk_if_alive(self, generation: int) -> None:
        """ウィンドウ破棄後に singleShot が発火しても例外にならないようにする。"""
        if sip.isdeleted(self):
            return
        self._build_thumbnail_widgets_chunk(generation)

    def _build_thumbnail_widgets_chunk(self, generation: int) -> None:
        """重量文書向け: サムネイルウィジェットを少しずつ生成し、都度グリッドへ足す。

        1 チャンク生成するたびに次のイベントループへ ``QTimer.singleShot(0, ...)``
        で処理を譲り、UI スレッドを長く占有しないようにする。新規ウィジェットは
        その場でグリッドへ ``addWidget()`` する(位置は現在の列数から算出)。
        ``_refresh_grid()`` のように「一旦全部外してから全件を並べ直す」方式だと
        件数が増えるほど1回あたりのコストが線形に伸び、チャンクを重ねると
        全体では件数の2乗のコストになってしまうため、ここでは増分追加のみ行う
        (列数が変わるリサイズ等は次に呼ばれる ``_refresh_grid()`` で解消される)。
        表示範囲のサムネイル描画予約(``_enqueue_visible_thumbnail_renders()``)は
        最初と最後のチャンクでのみ行う(これも全件走査のため、毎チャンク呼ぶと
        同様に重くなる)。読み込み中はタイトルバーに進捗を表示し、フリーズと
        誤認されないようにする。
        """
        if generation != self._widget_build_generation or self._pending_widget_pages is None:
            return  # 途中で _load_pages() がやり直された(古い世代は破棄)

        cols = max(1, self._apply_grid_metrics())
        chunk_size = max(1, app_settings.heavy_pdf_widget_chunk_size())
        is_first_chunk = not self._thumbnails
        if is_first_chunk:
            self._grid_laid_out_cols = cols
        elif cols != self._grid_laid_out_cols:
            # 読み込み中に列数が変わった: 既配置分と食い違うので完了時に並べ直す。
            self._grid_laid_out_cols = None
        chunk = list(itertools.islice(self._pending_widget_pages, chunk_size))
        # 表示中のコンテナへ1件ずつ追加すると setVisible のたびに再レイアウト/再描画が
        # 走り件数に応じて遅くなるため、チャンク中は更新を止める。
        # 表示中のコンテナへ子を show するたびに親レイアウトが即時に activate され
        # (全件の再計算で O(件数))、1件数ミリ秒まで遅くなる。チャンク中はレイアウトを
        # 無効化し更新も止め、終わってから1回だけ反映する。
        # 注意: ``self._container.setUpdatesEnabled(True)`` は全子孫ウィジェットへ再帰して
        # update() を発行するため、数千件で約1秒かかる(計測値)。ここでは使わない。
        self._grid_layout.setEnabled(False)
        try:
            for i in chunk:
                thumb = PageThumbnail(self._pdf_path, i, thumb_size=self._thumb_size)
                thumb.clicked.connect(self._on_thumbnail_clicked)
                self._thumbnails.append(thumb)
                row, col = divmod(len(self._thumbnails) - 1, cols)
                self._grid_layout.addWidget(thumb, row, col)
                thumb.setVisible(True)
        finally:
            self._grid_layout.setEnabled(True)

        if len(chunk) == chunk_size:
            if is_first_chunk:
                # 最初の可視範囲だけは早く描画し、起動直後にフリーズと誤認
                # されないようにする。
                self._enqueue_visible_thumbnail_renders()
            # まだ続きがある: 進捗をタイトルに表示しつつ次のイベントループへ
            self.setWindowTitle(
                f"{self._page_edit_window_title()} - 読み込み中 "
                f"({len(self._thumbnails)}/{self._pending_widget_total})"
            )
            QTimer.singleShot(0, lambda: self._build_chunk_if_alive(generation))
        else:
            # 全ページ分のウィジェット生成が完了。
            self._pending_widget_pages = None
            self.setWindowTitle(self._page_edit_window_title())
            if self._grid_resize_deferred or self._grid_laid_out_cols != cols:
                # 読み込み中に抑止したリサイズ(列数変更など)をここで1回だけ反映する。
                self._grid_resize_deferred = False
                self._refresh_grid()
            else:
                self._enqueue_visible_thumbnail_renders()
            if self._zoom_view and self._zoom_view.isVisible():
                self._render_zoom()

    def refresh_from_disk(self) -> None:
        """Reload the current PDF from disk without closing the edit window."""
        if not os.path.exists(self._pdf_path):
            return

        self._commit_inline_annotation_editor()
        self._release_held_doc()
        # 手動保存モード: 保存済みなら保持ハンドルを閉じて読み直す。未保存なら破棄してよいか確認する。
        if not self._prepare_session_for_reload():
            return
        clear_pixmap_cache_for_path(self._pdf_path)
        page_count = get_page_count(self._pdf_path)
        if page_count != len(self._thumbnails):
            self._load_pages()
            return

        self._zoom_text_cache.clear()
        self._zoom_annotations = []
        # ページ内容(Ink 注釈を含む)が変わった可能性があるためキャッシュを破棄する。
        self._ink_xrefs_by_page_cache = None
        self._invalidate_and_requeue_thumbnails()

        if self._zoom_view and self._zoom_view.isVisible():
            self._render_zoom()

    def _on_external_pdf_rotation(self) -> None:
        """外部（MainWindow等）で回転されたPDFを即時反映する。"""
        self.refresh_from_disk()

    def _grid_available_width(self) -> int:
        """Width source for column calculation (always consistent)."""
        return viewport_width_or_fallback(
            self._grid_scroll,
            self.width(),
            reserve_vertical_scrollbar=True,
        )

    def _apply_grid_metrics(self) -> int:
        """列数を計算し、コンテナ幅・サムネイルサイズへ反映して列数を返す。

        ウィジェットを実際にグリッドへ追加(addWidget)するのは呼び出し側の
        責務(``_refresh_grid()`` は全件を一括で、重量文書の読み込み中は
        ``_build_thumbnail_widgets_chunk()`` がチャンクごとに増分で行う)。
        """
        available_width = self._grid_available_width()
        spacing = self._grid_layout.horizontalSpacing()
        if spacing < 0:
            spacing = self._grid_layout.spacing()
        spacing = int(spacing)
        m = self._grid_layout.contentsMargins()
        preferred_item_width = self._preferred_thumb_size + PageThumbnail.CARD_PADDING
        cols, item_width = responsive_grid_metrics(
            available_width,
            preferred_item_width,
            spacing,
            m.left() + m.right(),
        )

        content_width = (
            m.left()
            + m.right()
            + cols * item_width
            + max(0, cols - 1) * spacing
        )
        self._container.setMinimumWidth(max(1, int(available_width), content_width))
        thumb_size = max(1, item_width - PageThumbnail.CARD_PADDING)
        if thumb_size != self._thumb_size:
            self._reset_thumbnail_render_queue()
            self._thumb_size = thumb_size
            for thumb in self._thumbnails:
                thumb.set_thumbnail_size(self._thumb_size)
        return cols

    def _on_grid_resize_settled(self) -> None:
        """ビューポートのリサイズ後の再配置。

        重量文書の読み込み中は抑止し(チャンク生成と並べ直しが交互に走って
        O(ページ数^2) になるため)、完了時に1回だけ反映する。列数もサムネイルサイズも
        変わらないなら(パネル開閉など)並べ直しを省く。
        """
        if self._pending_widget_pages is not None:
            self._grid_resize_deferred = True
            return
        previous_size = self._thumb_size
        cols = self._apply_grid_metrics()
        if cols == self._grid_laid_out_cols and self._thumb_size == previous_size:
            return
        self._refresh_grid()

    def _detach_all_from_grid_layout(self) -> None:
        """レイアウトからウィジェットを全て外す(親は変えない)。

        以前は外すたびに ``setParent(None)`` していたが、これは1件ごとにフォーカスチェーン等を
        辿るため件数の2乗で遅くなる(計測: 8000件で約12秒、14592件で数十秒)。
        親はコンテナのままにし、再配置しないもの(非表示ページ)は呼び出し側で隠す。
        """
        while self._grid_layout.count():
            widget = self._grid_layout.takeAt(0).widget()
            if widget is not None:
                # WA_LaidOut が残っていると、再度 addWidget するとき Qt が「元のレイアウトから
                # 外す」ために既配置の全項目を走査する(件数の2乗: 14000件で約4秒)。
                # 既に外してあるので落としてよい。
                widget.setAttribute(Qt.WidgetAttribute.WA_LaidOut, False)

    def _refresh_grid(self) -> None:
        self._detach_all_from_grid_layout()

        cols = self._apply_grid_metrics()
        self._grid_laid_out_cols = cols

        # コンテナの setUpdatesEnabled(True) は全子孫へ再帰して update() を発行し、
        # 数千件で約1秒かかる(計測値)ため使わず、レイアウトの無効化だけで抑える。
        self._grid_layout.setEnabled(False)
        try:
            row_col = 0
            for thumb in self._thumbnails:
                if thumb._explicitly_hidden:
                    thumb.setVisible(False)
                    continue
                row, col = divmod(row_col, cols)
                row_col += 1
                self._grid_layout.addWidget(thumb, row, col)
                if thumb.isHidden():
                    thumb.setVisible(True)
        finally:
            self._grid_layout.setEnabled(True)
        self._enqueue_visible_thumbnail_renders()

    def _remove_page_thumbnails(self, page_indices: list[int]) -> None:
        """指定されたページのサムネイルを削除（差分更新）"""
        self._reset_thumbnail_render_queue()
        # ページ番号がずれるため、ページ別 Ink xref キャッシュも破棄する。
        self._ink_xrefs_by_page_cache = None
        # グリッドから全サムネイルを一旦取り除く
        self._detach_all_from_grid_layout()

        # 指定されたインデックスのサムネイルを削除（逆順で処理）
        for idx in sorted(page_indices, reverse=True):
            if 0 <= idx < len(self._thumbnails):
                thumb = self._thumbnails.pop(idx)
                if thumb in self._selected_thumbnails:
                    self._selected_thumbnails.remove(thumb)
                thumb.hide()  # 親から外さないので、削除されるまで残像が出ないよう隠す
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
        if size == self._preferred_thumb_size:
            return
        self._preferred_thumb_size = size
        self._refresh_grid()
        self._enqueue_all_thumbnail_renders()

    def eventFilter(self, obj, event) -> bool:
        grid_scroll = getattr(self, "_grid_scroll", None)
        if grid_scroll and obj is grid_scroll.viewport():
            if event.type() == QEvent.Type.Resize:
                # A vertical scrollbar can appear after the grid is laid out,
                # reducing the viewport width by its own width.
                self._grid_resize_timer.start()
                return False
            if event.type() != QEvent.Type.Wheel:
                return super().eventFilter(obj, event)
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if self._zoom_view and self._zoom_view.isVisible():
                    return False
                delta = event.angleDelta().y()
                if delta != 0:
                    step = self.PREVIEW_THUMB_STEP if delta > 0 else -self.PREVIEW_THUMB_STEP
                    self._set_thumbnail_size(self._preferred_thumb_size + step)
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

    def _reset_zoom_page_layout(self) -> None:
        """ページレイアウトを通常(単ページ・編集可)へ戻す。再描画はしない。"""
        self._zoom_page_layout = ZoomPageLayout.SINGLE
        if self._zoom_label:
            self._zoom_label.set_view_only(False)
        self._sync_zoom_page_layout_controls()
        self._set_zoom_panel_actions_enabled(True)
        panel = getattr(self, "_bookmarks_panel", None)
        if panel is not None:
            panel.set_read_only(False)

    def _open_zoom_view(self, page_num: int) -> None:
        self._commit_inline_annotation_editor()
        # 拡大ビューは常に単ページ表示で開始する。
        self._reset_zoom_page_layout()
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
        self._apply_panel_context()
        self._sync_ocr_overlay()  # 表示前の描画では重ね表示を省くため、開いた直後に反映する
        self._reload_bookmarks_tree()
        self._update_button_states()

    def _exit_zoom_view(self) -> None:
        self._commit_inline_annotation_editor()
        # 複数ページ表示はここで解除し、次回の拡大表示は単ページで開始させる。
        self._reset_zoom_page_layout()
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
        if last_page is not None:
            self._select_page_thumbnail(last_page)
        self._apply_panel_context()
        self._sync_grid_pii_thumbnails()
        self._update_button_states()

    def _select_page_thumbnail(self, page_num: int) -> None:
        """ページ一覧で、そのページのサムネイルだけを選択して見える位置へスクロールする。"""
        if not 0 <= page_num < len(self._thumbnails):
            return
        self._clear_selection()
        thumb = self._thumbnails[page_num]
        thumb.set_selected(True)
        self._selected_thumbnails.append(thumb)
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
        self._release_create_mode_for_page_move()
        self._selected_zoom_annotation = None
        if self._zoom_page_layout_is_multi():
            # 複数ページ表示はレイアウトの収容枚数単位で戻る。
            capacity = self._zoom_page_capacity()
            new = self._zoom_page_num - capacity
            new -= new % capacity
            self._zoom_page_num = max(0, new)
        else:
            self._zoom_page_num -= 1
        self._render_zoom()

    def _on_zoom_next_page(self) -> None:
        if self._zoom_page_num is None:
            return
        page_count = get_page_count(self._pdf_path)
        if self._zoom_page_layout_is_multi():
            # 複数ページ表示は最後のグループ先頭で停止する。
            last_start = self._last_zoom_group_start(page_count)
            if self._zoom_page_num >= last_start:
                self._update_zoom_nav_buttons(page_count)
                return
            self._commit_inline_annotation_editor()
            self._release_create_mode_for_page_move()
            self._selected_zoom_annotation = None
            capacity = self._zoom_page_capacity()
            new = self._zoom_page_num + capacity
            new -= new % capacity
            self._zoom_page_num = min(last_start, new)
            self._render_zoom()
            return
        if self._zoom_page_num >= page_count - 1:
            self._update_zoom_nav_buttons(page_count)
            return
        self._commit_inline_annotation_editor()
        self._release_create_mode_for_page_move()
        self._selected_zoom_annotation = None
        self._zoom_page_num += 1
        self._render_zoom()

    def _on_zoom_first_page(self) -> None:
        if self._zoom_page_num is None:
            return
        if self._zoom_page_num == 0:
            return
        self._commit_inline_annotation_editor()
        self._release_create_mode_for_page_move()
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
        # 複数ページ表示時は最後のグループ先頭へ移動する。
        target = (
            self._last_zoom_group_start(page_count)
            if self._zoom_page_layout_is_multi()
            else last_index
        )
        if self._zoom_page_num == target:
            return
        self._commit_inline_annotation_editor()
        self._release_create_mode_for_page_move()
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
        if self._zoom_page_layout_is_multi():
            # 複数ページ表示: 最後のグループ先頭で「次」を無効化する。
            last_start = self._last_zoom_group_start(page_count)
            self._zoom_prev_btn.setEnabled(self._zoom_page_num > 0)
            self._zoom_next_btn.setEnabled(self._zoom_page_num < last_start)
            return
        self._zoom_prev_btn.setEnabled(self._zoom_page_num > 0)
        self._zoom_next_btn.setEnabled(self._zoom_page_num < page_count - 1)

    def _render_zoom_page(self) -> None:
        if not self._zoom_annotation_text_commit_in_progress:
            self._commit_inline_annotation_editor()
        if self._zoom_page_num is None or not self._zoom_label:
            return
        # ページ数・注釈4種・描画順・Ink xref を1回の open でまとめて取得する
        # (以前は7回 open していた。大きいPDFで各20〜25ms)。
        page_data = load_zoom_page_annotations(
            self._pdf_path, self._zoom_page_num, include_ink=not self._show_ink_annots
        )
        page_count = page_data["page_count"]
        self._update_zoom_nav_buttons(page_count)
        if self._zoom_page_label:
            self._zoom_page_label.setText(f"{self._zoom_page_num + 1} / {page_count}")
        if self._zoom_page_num >= page_count:
            self._exit_zoom_view()
            return
        dpr = self._zoom_label.devicePixelRatioF()
        merged = (
            page_data["freetext"] + page_data["shape"] + page_data["markup"] + page_data["note"]
        )
        # オーバーレイで描く注釈(フリーテキスト・図形・マークアップ・ノート)は
        # 二重描画を避けるためページ画像側では隠す。それ以外(Ink など本アプリが
        # 編集対象としない注釈)はページ画像にそのまま焼き込んで表示する。
        hide_xrefs = {a.xref for a in merged}
        if not self._show_ink_annots:
            # 手書き(Ink)注釈を非表示にする設定の場合、この xref もページ画像側で隠す。
            hide_xrefs |= set(page_data["ink_xrefs"])
        pixmap = get_page_pixmap(
            self._pdf_path,
            self._zoom_page_num,
            self._zoom_factor * dpr,
            annots=True,
            hide_xrefs=hide_xrefs,
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
        xref_order = page_data["xref_order"]
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
        self._sync_ocr_overlay()
        self._set_selected_zoom_annotation(current_selection)
        self._update_note_list_widget()

    def _render_zoom(self) -> None:
        """現在のレイアウトに応じてズーム表示を更新する。"""
        if self._zoom_page_layout_is_multi():
            self._render_zoom_pages()
        else:
            self._render_zoom_page()

    # 複数ページ表示のページ間ゲター(論理px)。
    SPREAD_GUTTER = 12

    def _format_zoom_page_label(
        self, start: int, displayed_count: int, page_count: int
    ) -> str:
        first = start + 1
        if displayed_count <= 1:
            return f"{first} / {page_count}"
        return f"{first}-{start + displayed_count} / {page_count}"

    def _compose_page_pixmap(
        self, pixmaps: list[QPixmap], columns: int, dpr: float
    ) -> QPixmap:
        """ページ画像を行優先のグリッドに合成して返す。"""
        if not pixmaps:
            return QPixmap(1, 1)
        columns = max(1, columns)
        rows = (len(pixmaps) + columns - 1) // columns
        gutter_dev = round(self.SPREAD_GUTTER * dpr)
        column_widths = [0] * columns
        row_heights = [0] * rows
        for index, pixmap in enumerate(pixmaps):
            column = index % columns
            row = index // columns
            column_widths[column] = max(column_widths[column], pixmap.width())
            row_heights[row] = max(row_heights[row], pixmap.height())
        total_w = sum(column_widths) + gutter_dev * (columns - 1)
        total_h = sum(row_heights) + gutter_dev * (rows - 1)
        canvas = QPixmap(max(1, total_w), max(1, total_h))
        canvas.fill(Qt.GlobalColor.white)
        painter = QPainter(canvas)
        y = 0
        for row in range(rows):
            x = 0
            for column in range(columns):
                index = row * columns + column
                if index < len(pixmaps):
                    painter.drawPixmap(x, y, pixmaps[index])
                x += column_widths[column]
                if column < columns - 1:
                    x += gutter_dev
            y += row_heights[row]
            if row < rows - 1:
                y += gutter_dev
        painter.end()
        return canvas

    def _compose_spread_pixmap(self, left_pix: QPixmap, right_pix: QPixmap | None,
                               dpr: float) -> QPixmap:
        """既存の横2枚合成APIを互換維持する。"""
        pixmaps = [left_pix]
        if right_pix is not None and not right_pix.isNull():
            pixmaps.append(right_pix)
        return self._compose_page_pixmap(pixmaps, 2, dpr)

    def _render_zoom_spread(self) -> None:
        """旧来の見開き描画APIを複数ページ描画へ互換接続する。"""
        self._render_zoom_pages()

    def _render_zoom_pages(self) -> None:
        """選択されたレイアウトで複数ページを合成して表示する。閲覧専用。"""
        if not self._zoom_annotation_text_commit_in_progress:
            self._commit_inline_annotation_editor()
        if self._zoom_page_num is None or not self._zoom_label:
            return
        page_count = get_page_count(self._pdf_path)
        if page_count <= 0:
            self._exit_zoom_view()
            return
        layout = self._zoom_page_layout
        capacity = layout.page_capacity
        start = self._zoom_page_num
        if start >= page_count:
            start = self._last_zoom_group_start(page_count)
        start -= start % capacity
        start = max(0, start)
        self._zoom_page_num = start
        page_indices = list(range(start, min(start + capacity, page_count)))

        self._update_zoom_nav_buttons(page_count)
        if self._zoom_page_label:
            self._zoom_page_label.setText(
                self._format_zoom_page_label(start, len(page_indices), page_count)
            )

        dpr = self._zoom_label.devicePixelRatioF()
        scale = self._zoom_factor * dpr
        # 注釈はページ画像に焼き込んで見えるようにする(annots=True)。
        # 手書き(Ink)注釈を非表示にする設定なら、各ページの Ink の xref を隠す。
        pixmaps = [
            get_page_pixmap(
                self._pdf_path,
                page_index,
                scale,
                annots=True,
                hide_xrefs=(
                    None
                    if self._show_ink_annots
                    else set(list_ink_annot_xrefs(self._pdf_path, page_index))
                ),
            )
            for page_index in page_indices
        ]
        combined = self._compose_page_pixmap(pixmaps, layout.columns, dpr)
        combined.setDevicePixelRatio(dpr)
        # words/links/annots/chars を空で渡し、選択・編集のヒット対象を無くす。
        self._zoom_label.set_page(
            combined, [], [], [], self._zoom_factor, None, chars=[]
        )
        # 合成画像にはページ座標系が無いため検索ハイライトは出さない。
        self._zoom_label.set_search_hit_rects([])
        self._sync_ocr_overlay()  # 複数ページ表示では空になる(重ね表示は単ページのみ)
        # 複数ページ表示中は付箋編集UI(B一覧)を対象外にするため注釈状態をクリアする。
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
        if self._zoom_page_layout_is_multi():
            return
        self._commit_inline_annotation_editor()
        pending = self._undo_manager.peek_undo()
        affects_pages = pending.affects_pages if pending is not None else True
        if pending is not None and pending.writes_file and not self._ensure_saved("元に戻す", release=True):
            return
        with self._pii_restyle_paused():  # PDFへ書き込むので restyle ジョブを止める
            try:
                self._undo_manager.undo()
            except PdfWritePermissionError as error:
                self._handle_pdf_write_permission_denied(error, selected_annotation=self._selected_zoom_annotation)
                return
        self._claim_undo_redo_action(pending)
        self._after_undo_redo(affects_pages)

    def _on_redo(self) -> None:
        if self._zoom_page_layout_is_multi():
            return
        self._commit_inline_annotation_editor()
        pending = self._undo_manager.peek_redo()
        affects_pages = pending.affects_pages if pending is not None else True
        if pending is not None and pending.writes_file and not self._ensure_saved("やり直し", release=True):
            return
        with self._pii_restyle_paused():  # PDFへ書き込むので restyle ジョブを止める
            try:
                self._undo_manager.redo()
            except PdfWritePermissionError as error:
                self._handle_pdf_write_permission_denied(error, selected_annotation=self._selected_zoom_annotation)
                return
        self._claim_undo_redo_action(pending)
        self._after_undo_redo(affects_pages)

    def _claim_undo_redo_action(self, action: "UndoAction | None") -> None:
        """Undo/Redo した操作の効果がメモリ上の未保存の変更として残ったら、このウィンドウの分として記録する。

        (保存点を越えて Undo すると、保存済みだった操作の取り消しが未保存になる。
        破棄したときに、その操作を共有 UndoManager に残さないため)
        """
        if (
            self._session is not None
            and action is not None
            and action.owner is None
            and not action.writes_file
            and self._session.dirty()
        ):
            action.owner = self._undo_owner

    def _after_undo_redo(self, affects_pages: bool) -> None:
        """Undo/Redo 実行後の画面更新。

        ページ構成を変える操作は従来どおり全体を読み込み直す。注釈だけの操作は、
        do/undo の各関数が表示中ページ(ズーム画面・サムネイル・一覧)を更新済みなので、
        ここでは Ink xref キャッシュを捨てるだけにして、317枚規模のサムネイル再構築や
        PII結果・しおりの再構築を避ける。
        """
        if affects_pages:
            self._load_pages()
        else:
            self._ink_xrefs_by_page_cache = None
        self._update_button_states()

    def _on_delete(self) -> None:
        # 複数ページ表示(閲覧専用)中は削除不可(Delete キーのショートカット対策)。
        if self._zoom_page_layout_is_multi():
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
        if not self._ensure_saved("ページの削除"):
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

        # 削除したページを指すしおりは remove_pages が取り除くので、Undo 用に元の TOC を控える。
        old_toc = get_pdf_toc(pdf_path)

        def do_delete():
            remove_pages(pdf_path, indices)
            self._load_pages()

        def undo_delete():
            insert_pages(pdf_path, backup_path, sorted_indices)
            if old_toc:
                update_pdf_toc(pdf_path, old_toc)
            self._load_pages()

        self._push_undoable(f"Delete {len(indices)} page(s)", do_delete, undo_delete, writes_file=True)

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
            # 改名は保持中のハンドルがあると Windows で失敗するので、保存してハンドルを閉じる。
            if not self._ensure_saved("名前変更", release=True):
                return

            def _get_main_window():
                from src.views.main_window import MainWindow

                owner = self.parent()
                return owner if isinstance(owner, MainWindow) else None

            def do_rename() -> None:
                # 共有 UndoManager 経由(メイン画面の Redo など)でも、保持ハンドルがあると改名できない。
                # 未保存なら保存の確認、保存済みならハンドルを閉じる。
                release_session_for_path(old_path)
                main_window = _get_main_window()
                if main_window:
                    main_window._perform_rename(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    self._on_pdf_path_changed(new_path)

            def undo_rename() -> None:
                release_session_for_path(new_path)
                main_window = _get_main_window()
                if main_window:
                    main_window._perform_rename(new_path, old_path)
                else:
                    os.rename(new_path, old_path)
                    self._on_pdf_path_changed(old_path)

            try:
                do_rename()
            except OSError as error:
                self._handle_file_operation_error(error, old_path, "名前変更")
                return
            self._add_undo_action(UndoAction(
                description="Rename PDF",
                undo_func=undo_rename,
                redo_func=do_rename,
                writes_file=True,
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
        if not self._ensure_saved("PDF名の変更"):
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
        self._add_undo_action(UndoAction(
            description="Rename PDF Name",
            undo_func=undo_rename_pdf_title,
            redo_func=do_rename_pdf_title,
            writes_file=True,
        ))

    def _on_print(self) -> None:
        """Print the current PDF."""
        from src.views.print_dialog import PrintDialog
        current = self._zoom_page_num if (self._zoom_view and self._zoom_view.isVisible()) else None
        dialog = PrintDialog([self._pdf_path], self, current_index=current)
        if dialog.exec() != PrintDialog.DialogCode.Accepted:
            return
        # 印刷はファイルを読んで行うので、未保存の編集が含まれるよう先に保存する。
        if not self._ensure_saved("印刷"):
            return
        print_pdfs([self._pdf_path], self, settings=dialog.get_settings(), printer=dialog.build_printer())

    def _on_rotate(self) -> None:
        # 複数ページ表示(閲覧専用)中は回転不可(R キーのショートカット対策)。
        if self._zoom_page_layout_is_multi():
            return
        # ズームビュー表示中の場合
        if self._zoom_view and self._zoom_view.isVisible():
            self._rotate_zoom_page()
            return

        if not self._selected_thumbnails:
            return
        if not self._ensure_saved("ページの回転"):
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

        self._push_undoable(f"Rotate {len(indices)} page(s)", do_rotate, undo_rotate, writes_file=True)

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
        if not self._ensure_saved("ページの削除"):
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
        old_toc = get_pdf_toc(pdf_path)

        def do_delete():
            remove_pages(pdf_path, [deleted_page])
            self._remove_page_thumbnails([deleted_page])
            self._reload_bookmarks_tree()
            self._zoom_text_cache.clear()
            if self._zoom_view and self._zoom_view.isVisible():
                new_page_count = get_page_count(pdf_path)
                if new_page_count > 0:
                    self._zoom_page_num = min(next_page, new_page_count - 1)
                    self._render_zoom()

        def undo_delete():
            insert_pages(pdf_path, backup_path, [deleted_page])
            if old_toc:
                update_pdf_toc(pdf_path, old_toc)
            self._load_pages()
            self._zoom_text_cache.clear()
            if self._zoom_view and self._zoom_view.isVisible():
                self._zoom_page_num = deleted_page
                self._render_zoom()

        self._push_undoable("Delete page from zoom view", do_delete, undo_delete, writes_file=True)

    def _rotate_zoom_page(self) -> None:
        """ズームビュー表示中のページを回転"""
        if self._zoom_page_num is None:
            return
        if not self._ensure_saved("ページの回転"):
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

        self._push_undoable("Rotate page from zoom view", do_rotate, undo_rotate, writes_file=True)

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
                    # 手動保存セッションが保持しているハンドルがあるとゴミ箱へ送れない(Windows)。
                    release_session_for_path(pdf_path)
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

        self._add_undo_action(UndoAction(
            description="Delete all pages (file to trash)",
            undo_func=undo_delete,
            redo_func=do_delete,
            writes_file=True,
        ))

    def _delete_all_pages_from_zoom(self, backup_path: str) -> None:
        """ズームビューから最後の1ページ削除時の処理"""
        self._delete_all_pages(backup_path)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 並べ直しは直接呼ばず、デバウンスして _on_grid_resize_settled に任せる
        # (読み込み中は抑止され、列数・サムネイルサイズが変わらなければ省かれる)。
        self._grid_resize_timer.start()

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
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            source_pdf_path = data.split('|')[0]
            if source_pdf_path == self._pdf_path and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                event.setDropAction(Qt.DropAction.CopyAction)
            else:
                event.setDropAction(Qt.DropAction.MoveAction)
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
                is_copy = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if is_copy:
                    logger.debug("Same file with Ctrl held, calling _handle_page_copy")
                    self._handle_page_copy(page_nums, drop_pos)
                else:
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
        if not self._ensure_saved("ページの並べ替え"):
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

        self._push_undoable("Reorder page", do_reorder, undo_reorder, writes_file=True)

    def _handle_page_copy(self, source_pages: list[int], drop_pos) -> None:
        """Ctrl+ドラッグで同一PDF内のページを複製挿入する。"""
        import tempfile

        target_page = self._get_drop_page_index(drop_pos)
        source_pages = sorted(set(source_pages))
        if not source_pages or target_page == -1:
            return
        if not self._ensure_saved("ページのコピー"):
            return

        pdf_path = self._pdf_path
        page_count = get_page_count(pdf_path)
        insert_at = max(0, min(target_page, page_count))
        copied_count = len(source_pages)

        def do_copy():
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                if not extract_pages(pdf_path, tmp_path, source_pages):
                    return
                insert_pages(pdf_path, tmp_path, [insert_at] * copied_count)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            self._load_pages()
            self._clear_selection()
            for i in range(insert_at, insert_at + copied_count):
                if i < len(self._thumbnails):
                    self._thumbnails[i].set_selected(True)
                    self._selected_thumbnails.append(self._thumbnails[i])
            self._update_button_states()

        def undo_copy():
            remove_pages(pdf_path, list(range(insert_at, insert_at + copied_count)))
            self._load_pages()

        self._push_undoable("Copy page", do_copy, undo_copy, writes_file=True)

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

        # 両方のファイルを直接読み書きするので、(手動保存モードの)未保存の編集を先に保存する。
        if not self._ensure_saved("ページの挿入"):
            return
        if not ensure_path_saved(source_pdf_path, "ページの移動・コピー"):
            return

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

    def _current_file_size(self) -> int:
        try:
            return os.path.getsize(self._pdf_path)
        except OSError:
            return 0

    def _pdf_bloated_since_open(self) -> bool:
        """ウィンドウを開いた時点から、増分保存でファイルが閾値を超えて膨らんだか。"""
        initial = self._initial_file_size
        if initial <= 0:
            return False
        growth = self._current_file_size() - initial
        return growth > INCREMENTAL_SAVE_COMPACT_BYTES or growth > initial * INCREMENTAL_SAVE_COMPACT_RATIO

    def _compact_pdf_if_bloated(self) -> None:
        """増分保存の累積で肥大していたら、閉じるときに1回だけ全体保存で整理する。

        閾値未満なら何もしない(閉じる操作を遅くしない)。失敗しても閉じる操作は止めない。
        """
        if not self._pdf_bloated_since_open():
            return
        try:
            compact_pdf_in_place(self._pdf_path)
        except Exception:  # noqa: BLE001 - 整理の失敗でウィンドウを閉じられなくしない
            logger.warning("PDFの整理(全体保存)に失敗しました: %s", self._pdf_path, exc_info=True)

    def closeEvent(self, event) -> None:
        """Handle window close - unlock the card in main window."""
        from src.views.main_window import MainWindow

        logger.debug(f"PageEditWindow closing for {self._pdf_path}")

        # 手動保存モード: 未保存なら 保存/破棄/キャンセル を確認し、セッションを閉じる。
        if not self._finish_manual_session_on_close():
            event.ignore()
            return

        self._reset_thumbnail_render_queue()
        self._release_held_doc()
        # 未確定のフォーム編集(スライダー/スピン)があれば先に確定する。
        self._flush_zoom_annotation_form_commit()
        # 予約中・実行中の PII 注釈スタイル反映は、閉じる前に同期で済ませる。
        self._flush_pii_restyle(sync=True)
        # restyle の保存が済んだ後で、増分保存で肥大していたら1回だけ整理する。
        self._compact_pdf_if_bloated()
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
            self._release_create_mode_for_page_move()
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


def ensure_path_saved(pdf_path: str, reason: str) -> bool:
    """*pdf_path* を開いている編集ウィンドウの未保存の編集を保存する(手動保存モード)。

    別のウィンドウ・メイン画面がそのファイルを直接読み書きする操作(ページのドラッグ移動など)の
    前に呼ぶ。保存をキャンセルされたら False。該当ウィンドウが無い・自動保存なら True。
    """
    key = os.path.normcase(os.path.abspath(pdf_path))
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, PageEditWindow) and os.path.normcase(os.path.abspath(widget._pdf_path)) == key:
            if not widget._ensure_saved(reason):
                return False
    return True
