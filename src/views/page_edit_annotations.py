"""拡大表示のアノテーション編集ロジック(PageEditWindowのmixin)。

状態はすべて self(= PageEditWindow インスタンス)上に持つ。
page_edit_window.py から機械的に移動したもの。
"""
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


class ZoomAnnotationMixin:
    """PageEditWindow に混ぜ込む付箋(アノテーション)編集機能。"""

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
