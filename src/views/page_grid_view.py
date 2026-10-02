"""仮想化したページ一覧グリッド。

ページ数に比例してウィジェットを作ると、数千ページの文書で生成・配置・メモリのコストが
ページ数に比例して膨らむ。ここでは QScrollArea の中身となる 1 枚のウィジェットが
「見えているセル(と前後数行)」にだけ ``PageThumbnail`` を割り当て、スクロールに応じて
使い回す(ウィジェットプール)。セルの位置は ``page_grid_geometry`` の算術で求め、
レイアウトは使わない。

選択・ドロップ先・検索ヒットなどのページごとの状態は持ち主(ウィンドウ)が保持し、
グリッドはコールバックでそれを取得して表示に反映するだけ(状態の持ち主は常にウィンドウ)。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import QFrame, QRubberBand, QScrollArea, QWidget

from src.views.page_edit_widgets import PageThumbnail, build_page_drag_mime
from src.views.page_grid_geometry import GridMetrics
from src.views.view_helpers import apply_drag_pixmap

logger = logging.getLogger(__name__)

GRID_MARGIN = 10
GRID_SPACING = 10
# 表示範囲の上下に余分にウィジェットを割り当てる行数(スクロール直後の空白を減らす)。
OVERSCAN_ROWS = 2


class VirtualPageGrid(QWidget):
    """ページ一覧の中身。見えている範囲のページにだけウィジェットを割り当てる。"""

    page_clicked = pyqtSignal(int)
    page_double_clicked = pyqtSignal(int)

    def __init__(
        self,
        scroll_area: QScrollArea,
        *,
        pdf_path_provider: Callable[[], str],
        state_provider: Callable[[int], tuple[bool, bool, bool]],
        bind_hook: Callable[[PageThumbnail, int], None],
        drag_pages_provider: Callable[[int], list[int]],
        thumb_size: int = PageThumbnail.THUMBNAIL_SIZE,
    ) -> None:
        super().__init__()
        self._scroll = scroll_area
        self._pdf_path_provider = pdf_path_provider
        self._state_provider = state_provider
        self._bind_hook = bind_hook
        self._drag_pages_provider = drag_pages_provider
        self._thumb_size = int(thumb_size)
        self._metrics = GridMetrics(
            cols=1,
            item_w=self._thumb_size + PageThumbnail.CARD_PADDING,
            item_h=self._thumb_size + PageThumbnail.CARD_PADDING,
            margin=GRID_MARGIN,
            spacing=GRID_SPACING,
            count=0,
        )
        # プールは縮めず、ウィンドウの寿命の間 deleteLater もしない
        # (ドラッグ元ウィジェットが drag.exec 中に破棄される事故を避けるため)。
        self._pool: list[PageThumbnail] = []
        self._free: list[PageThumbnail] = []
        self._bound: dict[int, PageThumbnail] = {}
        self.setAcceptDrops(True)

        # ドロップ位置の縦線と、ラバーバンド(どちらも描画専用の子ウィジェット)。
        self._drop_indicator = QFrame(self)
        self._drop_indicator.setFrameShape(QFrame.Shape.VLine)
        self._drop_indicator.setStyleSheet("background-color: #4f46e5;")
        self._drop_indicator.setFixedWidth(3)
        self._drop_indicator.hide()
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)

        scroll_area.verticalScrollBar().valueChanged.connect(self._on_vscroll)

    # ------------------------------------------------------------------
    # 基本情報
    # ------------------------------------------------------------------
    @property
    def metrics(self) -> GridMetrics:
        return self._metrics

    @property
    def rubber_band(self) -> QRubberBand:
        return self._rubber_band

    @property
    def thumb_size(self) -> int:
        return self._thumb_size

    @property
    def page_count(self) -> int:
        return self._metrics.count

    @property
    def pool_size(self) -> int:
        return len(self._pool)

    def widget_for_page(self, page: int) -> PageThumbnail | None:
        return self._bound.get(page)

    def is_page_bound(self, page: int) -> bool:
        return page in self._bound

    def bound_widgets(self) -> list[PageThumbnail]:
        return list(self._bound.values())

    def bound_pages(self) -> list[int]:
        return sorted(self._bound)

    # ------------------------------------------------------------------
    # 寸法
    # ------------------------------------------------------------------
    def set_metrics(self, cols: int, thumb_size: int, available_width: int = 0) -> None:
        """列数とサムネイルサイズを設定する(ウィジェットの再割り当ては ``relayout()`` で行う)。"""
        thumb_size = max(1, int(thumb_size))
        if thumb_size != self._thumb_size:
            self._thumb_size = thumb_size
            for widget in self._pool:
                widget.set_thumbnail_size(thumb_size)
        item = thumb_size + PageThumbnail.CARD_PADDING
        self._metrics = GridMetrics(
            cols=cols,
            item_w=item,
            item_h=item,
            margin=GRID_MARGIN,
            spacing=GRID_SPACING,
            count=self._metrics.count,
        )
        self._apply_size(available_width)

    def set_page_count(self, count: int) -> None:
        """ページ数を設定する。ページ番号の対応が変わるので、割り当てを全て解いて割り当て直す。"""
        self.unbind_all()
        m = self._metrics
        self._metrics = GridMetrics(
            cols=m.cols,
            item_w=m.item_w,
            item_h=m.item_h,
            margin=m.margin,
            spacing=m.spacing,
            count=count,
        )
        self._apply_size(self.minimumWidth())
        self.relayout()

    def _apply_size(self, available_width: int = 0) -> None:
        """高さ(縦スクロール範囲)と最小幅を現在の寸法に合わせる。非表示でも更新する。"""
        m = self._metrics
        min_w = max(1, int(available_width), m.content_width)
        min_h = max(1, m.content_height)
        if self.minimumWidth() != min_w:
            self.setMinimumWidth(min_w)
        if self.minimumHeight() != min_h:
            self.setMinimumHeight(min_h)
        # QScrollArea のスクロール範囲はウィジェットのリサイズで同期的に更新される。
        # 最小サイズの変更だけだとレイアウト要求の処理待ち(次のイベントループ)になり、
        # 直後の scroll_to_page がクランプされてしまうので、ここで実サイズも合わせる。
        viewport = self._scroll.viewport()
        size = QSize(max(min_w, viewport.width()), max(min_h, viewport.height()))
        if self.size() != size:
            self.resize(size)

    # ------------------------------------------------------------------
    # 割り当て
    # ------------------------------------------------------------------
    def _viewport_y_range(self) -> tuple[int, int, int]:
        height = self._scroll.viewport().height()
        y0 = self._scroll.verticalScrollBar().value()
        return y0, y0 + height - 1, height

    def visible_page_range(self, overscan_rows: int = 0) -> tuple[int, int]:
        """表示中の行にかかるページ範囲 [start, stop)。"""
        y0, y1, height = self._viewport_y_range()
        if height <= 0:
            return (0, 0)
        return self._metrics.visible_range(y0, y1, overscan_rows)

    def prefetch_page_range(self) -> tuple[int, int]:
        """表示範囲の上下に 1 画面ぶん足したページ範囲 [start, stop)(先読み用)。"""
        y0, y1, height = self._viewport_y_range()
        if height <= 0:
            return (0, 0)
        return self._metrics.visible_range(y0 - height, y1 + height, 0)

    def first_visible_page(self) -> int | None:
        start, stop = self.visible_page_range(0)
        return start if stop > start else None

    def relayout(self) -> None:
        """表示範囲(+前後の行)のページへウィジェットを割り当て、範囲外は解放する。

        非表示のとき(拡大表示中など)は何もしない。表示されたとき(showEvent)に追従する。
        """
        if not self.isVisible():
            return
        m = self._metrics
        start, stop = self.visible_page_range(OVERSCAN_ROWS) if m.count else (0, 0)
        for page in [p for p in self._bound if not (start <= p < stop)]:
            self._release(page)
        for page in range(start, stop):
            rect = m.cell_rect(page)
            widget = self._bound.get(page)
            if widget is None:
                self._assign(page, rect)
            elif widget.pos() != rect.topLeft():
                widget.move(rect.topLeft())
        self._raise_overlays()

    def unbind_all(self) -> None:
        for page in list(self._bound):
            self._release(page)

    def _acquire(self) -> PageThumbnail:
        if self._free:
            return self._free.pop()
        widget = PageThumbnail(
            self._pdf_path_provider(), -1, parent=self, thumb_size=self._thumb_size
        )
        widget.clicked.connect(self.page_clicked)
        widget.double_clicked.connect(self.page_double_clicked)
        widget.drag_requested.connect(self._on_drag_requested)
        widget.hide()
        self._pool.append(widget)
        return widget

    def _assign(self, page: int, rect: QRect) -> None:
        widget = self._acquire()
        widget.bind(page, pdf_path=self._pdf_path_provider())
        widget.set_state(*self._state_provider(page))
        self._bind_hook(widget, page)
        widget.move(rect.topLeft())
        widget.show()
        self._bound[page] = widget

    def _release(self, page: int) -> None:
        widget = self._bound.pop(page, None)
        if widget is None:
            return
        widget.hide()
        widget.unbind()
        self._free.append(widget)

    def _raise_overlays(self) -> None:
        if self._drop_indicator.isVisible():
            self._drop_indicator.raise_()
        if self._rubber_band.isVisible():
            self._rubber_band.raise_()

    def update_page(self, page: int) -> None:
        """ページの状態(選択・ドロップ先・検索ヒット)をウィジェットへ反映する(割り当て中のみ)。"""
        widget = self._bound.get(page)
        if widget is not None:
            widget.set_state(*self._state_provider(page))

    # ------------------------------------------------------------------
    # スクロール
    # ------------------------------------------------------------------
    def scroll_to_page(self, page: int) -> None:
        """そのページのセルが見えるようにスクロールし、ウィジェットを割り当てる。"""
        m = self._metrics
        if not 0 <= page < m.count:
            return
        rect = m.cell_rect(page)
        vbar = self._scroll.verticalScrollBar()
        height = self._scroll.viewport().height()
        if height > 0:
            value = vbar.value()
            if rect.top() - m.margin < value:
                vbar.setValue(max(0, rect.top() - m.margin))
            elif rect.bottom() + m.margin > value + height - 1:
                vbar.setValue(rect.bottom() + m.margin - height + 1)
        hbar = self._scroll.horizontalScrollBar()
        width = self._scroll.viewport().width()
        if width > 0:
            hvalue = hbar.value()
            if rect.left() - m.margin < hvalue:
                hbar.setValue(max(0, rect.left() - m.margin))
            elif rect.right() + m.margin > hvalue + width - 1:
                hbar.setValue(rect.right() + m.margin - width + 1)
        self.relayout()

    def _on_vscroll(self, _value: int) -> None:
        self.relayout()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.relayout()

    # ------------------------------------------------------------------
    # ドロップ位置・ラバーバンド
    # ------------------------------------------------------------------
    def show_drop_indicator(self, rect: QRect | None) -> None:
        if rect is None:
            self._drop_indicator.hide()
            return
        self._drop_indicator.setGeometry(rect)
        self._drop_indicator.raise_()
        self._drop_indicator.show()

    def hide_drop_indicator(self) -> None:
        self._drop_indicator.hide()

    @property
    def drop_indicator_visible(self) -> bool:
        return self._drop_indicator.isVisible()

    # ------------------------------------------------------------------
    # ドラッグ開始
    # ------------------------------------------------------------------
    def _on_drag_requested(self, page: int) -> None:
        source = self._bound.get(page)
        if source is None:
            return
        pages = self._drag_pages_provider(page)
        logger.debug("Starting drag: pages=%s", pages)
        drag = QDrag(source)
        drag.setMimeData(build_page_drag_mime(self._pdf_path_provider(), pages))
        apply_drag_pixmap(
            drag, source, max_size=80, count=len(pages), badge_size=20, badge_font_size=9
        )
        result = drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
        logger.debug("Drag completed with result: %s", result)
