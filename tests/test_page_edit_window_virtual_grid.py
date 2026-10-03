"""ページ一覧の仮想化(ウィジェットプール・ページ番号キーの状態)のテスト。"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QDragLeaveEvent, QMouseEvent
from PyQt6.QtTest import QTest

from src.models.undo_manager import UndoManager
from src.views.page_edit_window import PageEditWindow

from src.utils import app_settings
from src.utils.constants import HEAVY_PDF_PAGE_COUNT_THRESHOLD, PAGETHUMBNAIL_MIME_TYPE
from src.views.page_edit_widgets import build_page_drag_mime, page_drag_pages
from tests.helpers import create_page_edit_window, make_pdf

PAGES = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5  # 305 ページ(重量文書)


@pytest.fixture
def big_window(qtbot, tmp_path):
    pdf_path = tmp_path / "big.pdf"
    make_pdf(pdf_path, pages=PAGES, width=80, height=80)
    return create_page_edit_window(qtbot, pdf_path)


def _drain_render_queue(window) -> None:
    guard = 0
    while window._thumb_render_queue and guard < 5000:
        window._process_thumbnail_render_queue()
        guard += 1


def _mouse_event(window, kind, pos: QPoint, buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    return QMouseEvent(
        kind,
        QPointF(pos),
        QPointF(window.mapToGlobal(pos)),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


# --- ウィジェットプール -----------------------------------------------


def test_loading_a_big_document_creates_a_pool_not_one_widget_per_page(big_window):
    window = big_window
    assert window._page_count == PAGES
    assert window._grid.metrics.count == PAGES
    # 表示範囲(と前後の数行)ぶんだけで、305個は作らない。
    assert 0 < window._grid.pool_size < 100
    assert len(window._grid.bound_widgets()) == window._grid.pool_size
    # 「読み込み中」の進捗はタイトルへ出さない。
    assert window.windowTitle() == window._page_edit_window_title()
    assert "読み込み中" not in window.windowTitle()
    assert window._grid.widget_for_page(0) is not None
    assert window._grid.widget_for_page(PAGES - 1) is None


def test_pool_is_reused_and_never_shrinks_while_scrolling(big_window):
    window = big_window
    size_after_load = window._grid.pool_size
    seen: set[int] = set()
    for page in (50, 150, 250, 300, 10, 0):
        window.scroll_to_page(page)
        seen.update(id(w) for w in window._grid.bound_widgets())
        assert window._grid.widget_for_page(page) is not None
    # 新しく作られるのは、最初の割り当てより少し多くなるときだけ(ページ数に比例しない)。
    assert window._grid.pool_size <= size_after_load + 2 * window._grid.metrics.cols
    assert len(seen) == window._grid.pool_size


def test_scroll_to_page_binds_a_visible_widget(big_window):
    window = big_window
    window.scroll_to_page(200)
    widget = window.widget_for_page(200)
    assert widget is not None
    assert widget.isVisible()
    assert widget.page_num == 200
    viewport = window._grid_scroll.viewport()
    top_left = window._container.mapTo(viewport, widget.geometry().topLeft())
    assert 0 <= top_left.y() <= viewport.height() - widget.height()
    # 割り当て済みのウィジェットは、その位置のセルに置かれている。
    assert widget.pos() == window._grid.metrics.cell_rect(200).topLeft()
    # ページ番号バッジも付け替わっている。
    assert widget._number_label.text() == "201"


def test_out_of_view_pages_are_unbound_and_hidden(big_window):
    window = big_window
    first = window.widget_for_page(0)
    window.scroll_to_page(250)
    assert window.widget_for_page(0) is None
    assert window.widget_for_page(250) is not None
    assert first.page_num != 0  # 別のページへ再利用されたか、解放済み
    # 外れたウィジェットは再利用待ち(隠れていて、ページに結び付かない)。
    for w in window._grid._free:
        assert w.isHidden()
        assert w.page_num == -1


def test_selection_survives_scrolling_away_and_back(big_window):
    window = big_window
    window._select_pages([3, 4])
    assert window.widget_for_page(3).is_selected
    window.scroll_to_page(260)
    assert window.widget_for_page(3) is None
    assert list(window._selected_pages) == [3, 4]
    # 別ページへ割り当て直されたウィジェットに、選択の見た目が残っていない。
    assert not any(w.is_selected for w in window._grid.bound_widgets())
    window.scroll_to_page(0)
    assert window.widget_for_page(3).is_selected
    assert window.widget_for_page(4).is_selected
    assert not window.widget_for_page(5).is_selected


def test_rendered_cache_stays_within_cap_after_scrolling_through(big_window, monkeypatch):
    window = big_window
    monkeypatch.setattr(app_settings, "pixmap_cache_max_entries", lambda: 32)
    cap = window._rendered_cap()
    assert cap < PAGES
    for page in range(0, PAGES, 40):
        window.scroll_to_page(page)
        window._enqueue_visible_thumbnail_renders()
        _drain_render_queue(window)
        # 上限は表示セル数(プールの大きさ)に応じて動くので、都度求め直す。
        assert len(window._rendered) <= window._rendered_cap()
    window.scroll_to_page(PAGES - 1)
    window._enqueue_visible_thumbnail_renders()
    _drain_render_queue(window)
    assert len(window._rendered) <= window._rendered_cap()
    # 表示中のページは描画済みで、ウィジェットにも画像が入っている。
    assert window.is_page_rendered(PAGES - 1)
    assert window.widget_for_page(PAGES - 1).thumbnail_loaded


def test_rendered_pixmap_is_applied_when_page_scrolls_back_into_view(big_window):
    window = big_window
    window._enqueue_visible_thumbnail_renders()
    _drain_render_queue(window)
    assert window.is_page_rendered(0)
    window.scroll_to_page(150)
    window.scroll_to_page(0)
    widget = window.widget_for_page(0)
    assert widget.thumbnail_loaded
    assert widget._image_label.pixmap() is not None and not widget._image_label.pixmap().isNull()


def test_widget_state_updates_skip_repolish_when_unchanged(big_window, monkeypatch):
    widget = big_window.widget_for_page(0)
    calls: list[int] = []
    original = widget._update_style
    monkeypatch.setattr(widget, "_update_style", lambda: (calls.append(1), original())[1])
    widget.set_state(False, False, False)
    assert calls == []
    widget.set_state(True, False, False)
    assert calls == [1]
    widget.set_state(True, False, False)
    assert calls == [1]
    big_window._grid.update_page(0)  # 状態は選択無しなので 1 回戻る
    assert calls == [1, 1]


# --- 検索ハイライト ---------------------------------------------------


def test_search_hits_follow_pages_not_widgets(big_window):
    window = big_window
    window._search_hit_pages = [3, 200]
    window._apply_search_highlights()
    assert window.widget_for_page(3).is_search_hit
    assert not window.widget_for_page(4).is_search_hit
    window.scroll_to_page(200)
    assert window.widget_for_page(200).is_search_hit
    assert sum(1 for w in window._grid.bound_widgets() if w.is_search_hit) == 1
    window._clear_search_highlights()
    assert not any(w.is_search_hit for w in window._grid.bound_widgets())
    assert window._search_hit_set == set()


# --- ドロップ位置 -----------------------------------------------------


def test_drop_index_at_row_end_is_end_of_that_row(qtbot, tmp_path):
    pdf_path = tmp_path / "drop.pdf"
    make_pdf(pdf_path, pages=12, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    m = window._grid.metrics
    cols = m.cols
    assert 1 < cols < 12
    y = m.cell_rect(0).center().y()
    # 1行目の最後のセルより右。文書末尾(12)ではなく、その行の末尾(= cols)。
    pos = QPoint(m.cell_rect(cols - 1).right() + 200, y)
    assert window._get_drop_page_index(pos) == cols
    # 最終行より下は文書末尾。
    assert window._get_drop_page_index(QPoint(10, m.content_height + 500)) == 12
    # 先頭の手前。
    assert window._get_drop_page_index(QPoint(0, 0)) == 0


def test_drop_indicator_and_targets_are_computed_without_thumbnail_geometry(qtbot, tmp_path):
    pdf_path = tmp_path / "indicator.pdf"
    make_pdf(pdf_path, pages=12, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    m = window._grid.metrics
    cols = m.cols
    y = m.cell_rect(0).center().y()

    window._show_drop_indicator(QPoint(m.cell_rect(cols - 1).right() + 200, y))
    assert window._grid.drop_indicator_visible
    assert window._drop_indicator_index == cols
    assert window._drop_target_pages == (cols - 1, cols)
    assert window.widget_for_page(cols - 1).is_drop_target
    assert window.widget_for_page(cols).is_drop_target
    # 行末の挿入は、前の行の最後のセルの右側に縦線を出す。
    rect = m.indicator_rect(cols, row_end=True)
    assert window._grid._drop_indicator.geometry() == rect

    window._show_drop_indicator(QPoint(m.cell_rect(0).left() + 1, y))
    assert window._drop_target_pages == (0,)
    assert window.widget_for_page(0).is_drop_target
    assert not window.widget_for_page(cols).is_drop_target

    window._hide_drop_indicator()
    assert not window._grid.drop_indicator_visible
    assert window._drop_target_pages == ()
    assert not any(w.is_drop_target for w in window._grid.bound_widgets())


# --- ラバーバンド -----------------------------------------------------


def test_rubber_band_selects_pages_by_geometry_math_and_diffs(qtbot, tmp_path):
    pdf_path = tmp_path / "rubber.pdf"
    make_pdf(pdf_path, pages=12, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    grid = window._grid
    m = grid.metrics
    assert m.cols >= 3

    start_in_grid = QPoint(2, 2)  # 余白(どのセルでもない)
    press_pos = grid.mapTo(window, start_in_grid)
    window.mousePressEvent(_mouse_event(window, QEvent.Type.MouseButtonPress, press_pos))
    assert window._rubber_band_origin is not None

    end = m.cell_rect(m.cols + 1).center()  # 2行目の2列目の中央
    window.mouseMoveEvent(
        _mouse_event(window, QEvent.Type.MouseMove, grid.mapTo(window, end))
    )
    expected = [0, 1, m.cols, m.cols + 1]
    assert list(window._selected_pages) == expected
    assert [p for p in range(12) if window.widget_for_page(p).is_selected] == expected

    # 範囲を縮めると、外れたページだけ選択が外れる。
    small_end = m.cell_rect(1).center()
    window.mouseMoveEvent(
        _mouse_event(window, QEvent.Type.MouseMove, grid.mapTo(window, small_end))
    )
    assert list(window._selected_pages) == [0, 1]
    assert not window.widget_for_page(m.cols).is_selected

    window.mouseReleaseEvent(
        _mouse_event(window, QEvent.Type.MouseButtonRelease, grid.mapTo(window, small_end), Qt.MouseButton.NoButton)
    )
    assert window._rubber_band_origin is None
    assert not grid.rubber_band.isVisible()


# --- ドラッグ ---------------------------------------------------------


def test_drag_pages_follow_selection_order():
    selected = {5: None, 2: None, 7: None}
    assert page_drag_pages(2, selected) == [5, 2, 7]  # 選択順(辞書順)のまま
    assert page_drag_pages(3, selected) == [3]  # 選択外のページを掴んだらそのページだけ
    assert page_drag_pages(4, {4: None}) == [4]
    assert page_drag_pages(0, {}) == [0]


def test_build_page_drag_mime_format():
    mime = build_page_drag_mime("C:/x/y.pdf", [5, 2, 7])
    data = mime.data(PAGETHUMBNAIL_MIME_TYPE).data().decode("utf-8")
    assert data == "C:/x/y.pdf|5,2,7"


def test_grid_drag_provider_uses_window_selection(qtbot, tmp_path):
    pdf_path = tmp_path / "drag.pdf"
    make_pdf(pdf_path, pages=10, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._select_pages([6, 1, 3])
    assert window._grid._drag_pages_provider(1) == [6, 1, 3]
    assert window._grid._drag_pages_provider(8) == [8]


# --- ページの削除(差分更新) -------------------------------------------


def test_remove_page_thumbnails_keeps_rendered_pixmaps_with_remapped_keys(qtbot, tmp_path):
    pdf_path = tmp_path / "remove.pdf"
    make_pdf(pdf_path, pages=12, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._enqueue_visible_thumbnail_renders()
    _drain_render_queue(window)
    assert all(window.is_page_rendered(p) for p in range(12))
    old = {p: window._rendered[p] for p in range(12)}
    window._select_pages([1, 2, 6])
    window._search_hit_pages = [4]
    window._apply_search_highlights()

    window._remove_page_thumbnails([2, 5])

    assert window._page_count == 10
    assert window._grid.metrics.count == 10
    assert sorted(window._rendered) == list(range(10))
    survivors = [0, 1, 3, 4, 6, 7, 8, 9, 10, 11]
    for new_index, old_index in enumerate(survivors):
        assert window._rendered[new_index] is old[old_index]
    # 選択は削除分だけ詰まり、削除されたページは外れる。
    assert list(window._selected_pages) == [1, 4]
    assert window.widget_for_page(1).is_selected and window.widget_for_page(4).is_selected
    # 検索ヒットは無効になる。
    assert window._search_hit_pages == [] and window._search_hit_set == set()
    # 割り当て直されたウィジェットに画像が入っている(再描画待ちではない)。
    assert all(window.widget_for_page(p).thumbnail_loaded for p in range(10))
    assert window.widget_for_page(10) is None


def test_remove_page_thumbnails_is_safe_while_grid_is_hidden(qtbot, tmp_path):
    pdf_path = tmp_path / "remove-hidden.pdf"
    make_pdf(pdf_path, pages=40, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(0)
    assert window._grid_scroll.isHidden()

    window._remove_page_thumbnails(list(range(10, 30)))

    assert window._page_count == 20
    assert window._grid.metrics.count == 20
    assert window._grid.minimumHeight() == window._grid.metrics.content_height
    # 一覧へ戻ると、20ページ分が割り当てられる。
    window._exit_zoom_view()
    assert window._grid.widget_for_page(0) is not None


# --- 読み込み直し・拡大表示からの復帰 -----------------------------------


def test_load_pages_preserves_scroll_position(big_window):
    window = big_window
    window.scroll_to_page(150)
    value = window._grid_scroll.verticalScrollBar().value()
    assert value > 0
    window._load_pages()
    assert window._grid_scroll.verticalScrollBar().value() == value
    assert window.widget_for_page(150) is not None


def test_exit_zoom_view_binds_the_last_viewed_page(qtbot, tmp_path):
    pdf_path = tmp_path / "zoom-200.pdf"
    make_pdf(pdf_path, pages=300, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    assert window.widget_for_page(200) is None

    window._open_zoom_view(200)
    assert window._grid_scroll.isHidden()
    window._exit_zoom_view()

    widget = window.widget_for_page(200)
    assert widget is not None
    assert widget.isVisible()
    assert widget.is_selected
    viewport = window._grid_scroll.viewport()
    top_left = window._container.mapTo(viewport, widget.geometry().topLeft())
    assert 0 <= top_left.y() <= viewport.height() - widget.height()


# --- 塗りつぶし(PII)の見た目 -------------------------------------------


def test_pii_mask_style_is_applied_to_recycled_widgets(big_window):
    window = big_window
    settings = window._pii_settings().copy()
    settings.mask_color = (1.0, 0.0, 0.0)
    settings.mask_transparency = 0
    window._pii_settings_cache = settings
    window._apply_pii_visual_settings()

    expected = window._current_pii_mask_style()
    assert expected[0] == (1.0, 0.0, 0.0)
    # 割り当て済みのウィジェットへ即時に反映される。
    assert all(w._image_label._pii_style == expected for w in window._grid.bound_widgets())
    # スクロールで割り当てられた(再利用された)ウィジェットにも、割り当て時に流し込まれる。
    window.scroll_to_page(260)
    widget = window.widget_for_page(260)
    assert widget._image_label._pii_style == expected
    assert all(w._image_label._pii_style == expected for w in window._grid.bound_widgets())


def test_recycled_widget_does_not_keep_previous_pages_overlay(big_window):
    window = big_window
    widget = window.widget_for_page(0)
    widget.set_pii_overlay([object()], (100.0, 100.0))
    window.scroll_to_page(260)
    for w in window._grid.bound_widgets():
        assert w._image_label._pii_targets == []


# --- 初回表示・リサイズ・マウス操作 ---------------------------------------


def test_initial_load_after_show_binds_visible_widgets(qtbot, tmp_path):
    """ヘルパーを使わず、show() → 遅延の _load_pages() という実際の起動順でも割り当てられる。"""
    pdf_path = tmp_path / "startup.pdf"
    make_pdf(pdf_path, pages=PAGES, width=80, height=80)
    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._page_count == PAGES)
    qtbot.waitUntil(lambda: window.widget_for_page(0) is not None and window.widget_for_page(0).isVisible())
    assert window._grid.pool_size < 100
    # 描画が表示範囲から進む。
    qtbot.waitUntil(lambda: window.is_page_rendered(0))
    assert len(window._rendered) < PAGES


def test_growing_the_window_binds_more_rows(qtbot, tmp_path):
    pdf_path = tmp_path / "grow.pdf"
    make_pdf(pdf_path, pages=PAGES, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window.resize(800, 300)
    qtbot.wait(30)
    window._on_grid_resize_settled()
    small = len(window._grid.bound_widgets())
    window.resize(800, 1000)
    qtbot.wait(30)
    window._on_grid_resize_settled()
    assert len(window._grid.bound_widgets()) > small
    last_visible = window._grid.visible_page_range(0)[1] - 1
    assert window.widget_for_page(last_visible) is not None


def test_clicking_and_double_clicking_a_thumbnail_widget(qtbot, tmp_path):
    pdf_path = tmp_path / "click.pdf"
    make_pdf(pdf_path, pages=PAGES, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window.scroll_to_page(120)
    widget = window.widget_for_page(120)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    assert list(window._selected_pages) == [120]
    assert widget.is_selected

    QTest.mouseDClick(widget, Qt.MouseButton.LeftButton)
    assert window._zoom_view.isVisible()
    assert window._zoom_page_num == 120


def test_drag_request_is_emitted_by_the_widget_and_handled_by_the_grid(qtbot, tmp_path, monkeypatch):
    import src.views.page_grid_view as grid_module

    drags: list[dict] = []

    class FakeDrag:
        def __init__(self, source):
            self.source = source
            self.mime = None

        def setMimeData(self, mime):
            self.mime = mime

        def setPixmap(self, _pixmap):
            pass

        def setHotSpot(self, _point):
            pass

        def exec(self, _actions):
            drags.append(
                {
                    "source": self.source,
                    "data": self.mime.data(PAGETHUMBNAIL_MIME_TYPE).data().decode("utf-8"),
                }
            )
            return Qt.DropAction.IgnoreAction

    # 実際の QDrag.exec(モーダルなドラッグ操作)は走らせない。
    monkeypatch.setattr(grid_module, "QDrag", FakeDrag)

    pdf_path = tmp_path / "drag-signal.pdf"
    make_pdf(pdf_path, pages=10, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._select_pages([6, 3, 1])
    widget = window.widget_for_page(3)

    start = QPoint(10, 10)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(start + QPoint(60, 60)),
        QPointF(widget.mapToGlobal(start + QPoint(60, 60))),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(move)
    # 押したページが複数選択に含まれるので、選択全体が選択順で渡る。
    assert len(drags) == 1
    assert drags[0]["source"] is widget
    assert drags[0]["data"] == f"{window._pdf_path}|6,3,1"
    # 1回のドラッグ操作で複数回は始まらない。
    widget.mouseMoveEvent(move)
    assert len(drags) == 1


def test_ctrl_wheel_size_change_clears_rendered_and_resizes_pool(big_window):
    window = big_window
    window._enqueue_visible_thumbnail_renders()
    _drain_render_queue(window)
    assert window._rendered
    old_item_w = window._grid.metrics.item_w

    window._set_thumbnail_size(window._preferred_thumb_size + window.PREVIEW_THUMB_STEP)

    m = window._grid.metrics
    assert m.item_w == old_item_w + window.PREVIEW_THUMB_STEP
    assert window._thumb_size == m.item_w - 30
    # 描画済みは捨てられ(大きさが合わない)、割り当て済み・プール内の全ウィジェットが新サイズになる。
    assert not window._rendered
    assert all(w.width() == m.item_w for w in window._grid._pool)
    for page in window._grid.bound_pages():
        widget = window.widget_for_page(page)
        assert widget.pos() == m.cell_rect(page).topLeft()
        assert widget.width() == m.item_w
    # 新しい大きさでの描画が積まれる。
    assert window._thumb_render_queue


# --- ドラッグ中の自動スクロール -------------------------------------------


def _drag_move(window, grid_viewport_y: int):
    """一覧のビューポート内の指定 y(x は中央付近)でドラッグ移動イベントを送る。"""
    from PyQt6.QtCore import QMimeData
    from PyQt6.QtGui import QDragMoveEvent

    viewport = window._grid_scroll.viewport()
    pos = window.mapFromGlobal(viewport.mapToGlobal(QPoint(viewport.width() // 2, grid_viewport_y)))
    mime = build_page_drag_mime(window._pdf_path, [0])
    assert isinstance(mime, QMimeData)
    event = QDragMoveEvent(
        pos,
        Qt.DropAction.MoveAction | Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dragMoveEvent(event)
    return pos, mime


def test_drag_near_bottom_edge_autoscrolls_and_updates_drop_index(big_window):
    window = big_window
    vbar = window._grid_scroll.verticalScrollBar()
    height = window._grid_scroll.viewport().height()
    assert vbar.value() == 0

    pos, _mime = _drag_move(window, height - 5)
    assert window._drag_autoscroll_timer.isActive()
    assert window._drag_autoscroll_timer.interval() == 50
    index_before = window._get_drop_page_index(window._grid.mapFrom(window, pos))

    window._on_drag_autoscroll_tick()
    assert vbar.value() >= 20
    for _ in range(15):
        window._on_drag_autoscroll_tick()
    # 同じカーソル位置でも、スクロールした分だけ後ろのページが挿入先になる。
    index_after = window._get_drop_page_index(window._grid.mapFrom(window, pos))
    assert index_after > index_before
    assert window._drop_indicator_index == index_after
    assert window._grid.drop_indicator_visible

    window.dragLeaveEvent(QDragLeaveEvent())
    assert not window._drag_autoscroll_timer.isActive()
    assert not window._grid.drop_indicator_visible


def test_drag_near_top_edge_autoscrolls_up_and_leaving_the_edge_stops(big_window):
    window = big_window
    vbar = window._grid_scroll.verticalScrollBar()
    height = window._grid_scroll.viewport().height()
    window.scroll_to_page(150)
    start = vbar.value()
    assert start > 100

    _drag_move(window, 5)
    assert window._drag_autoscroll_timer.isActive()
    window._on_drag_autoscroll_tick()
    assert vbar.value() < start

    # 端の帯から出たら止まる。
    _drag_move(window, height // 2)
    assert not window._drag_autoscroll_timer.isActive()
    value = vbar.value()
    window._on_drag_autoscroll_tick()
    assert vbar.value() == value


def test_autoscroll_speeds_up_near_the_edge_and_stops_on_drop(big_window):
    window = big_window
    height = window._grid_scroll.viewport().height()
    _drag_move(window, height - 29)
    slow = window._drag_autoscroll_step
    _drag_move(window, height - 1)
    fast = window._drag_autoscroll_step
    assert 20 <= slow < fast

    class _DropEvent:
        def __init__(self, mime, pos):
            self._mime, self._pos = mime, pos

        def mimeData(self):
            return self._mime

        def position(self):
            class _P:
                def __init__(s, p):
                    s._p = p

                def toPoint(s):
                    return s._p

            return _P(self._pos)

        def modifiers(self):
            return Qt.KeyboardModifier.NoModifier

        def acceptProposedAction(self):
            pass

    pos, mime = _drag_move(window, height - 5)
    assert window._drag_autoscroll_timer.isActive()
    window._handle_page_reorder = lambda *a, **k: None  # 並べ替え自体は別テストで確認済み
    window.dropEvent(_DropEvent(mime, pos))
    assert not window._drag_autoscroll_timer.isActive()


# --- スクロールバーのつまみ操作中は描画しない -----------------------------------


def test_render_queue_is_paused_while_slider_is_held_and_resumes_on_release(big_window):
    window = big_window
    vbar = window._grid_scroll.verticalScrollBar()
    window._reset_thumbnail_render_queue()
    window._rendered.clear()

    vbar.setSliderDown(True)
    window.scroll_to_page(200)
    window._enqueue_visible_thumbnail_renders()
    queued = list(window._thumb_render_queue)
    assert queued
    window._process_thumbnail_render_queue()
    # つまみを掴んでいる間は 1 ページも描かれず、タイマーも回らない。
    assert list(window._thumb_render_queue) == queued
    assert not window._thumb_render_timer.isActive()
    assert not any(window.is_page_rendered(p) for p in queued)

    vbar.setSliderDown(False)  # sliderReleased を発行する
    visible_start, visible_stop = window._grid.visible_page_range(0)
    assert window._thumb_render_timer.isActive() or window._thumb_render_queue
    _drain_render_queue(window)
    assert all(window.is_page_rendered(p) for p in range(visible_start, visible_stop))


def test_stale_queue_entries_outside_prefetch_range_are_not_rendered(big_window):
    window = big_window
    window._reset_thumbnail_render_queue()
    window._rendered.clear()
    window.scroll_to_page(250)
    p0, p1 = window._grid.prefetch_page_range()
    assert p0 > 10
    window._enqueue_thumbnail_render(0)  # 画面から遠く離れた古い予約
    window._enqueue_thumbnail_render(p0)
    _drain_render_queue(window)
    assert not window.is_page_rendered(0)
    assert window.is_page_rendered(p0)


# --- 列数・サムネイルサイズの変更でも見ていたページを保つ -------------------------


def test_thumbnail_size_change_keeps_first_visible_page_in_view(big_window):
    window = big_window
    grid = window._grid
    window.scroll_to_page(150)
    window._grid_scroll.verticalScrollBar().setValue(
        grid.metrics.cell_rect(150).top() - grid.metrics.margin
    )
    anchor = grid.first_visible_page()
    assert anchor is not None and anchor > 50
    old_metrics = grid.metrics

    window._set_thumbnail_size(window._preferred_thumb_size + 3 * window.PREVIEW_THUMB_STEP)

    new_metrics = grid.metrics
    assert (new_metrics.cols, new_metrics.item_w) != (old_metrics.cols, old_metrics.item_w)
    new_first = grid.first_visible_page()
    # 最上行は、元の最上行のページを含む(行頭 <= anchor < 行頭 + 列数)。
    assert new_first <= anchor < new_first + new_metrics.cols
    assert window.widget_for_page(anchor) is not None


def test_column_count_change_by_resize_keeps_first_visible_page(big_window):
    window = big_window
    grid = window._grid
    window.scroll_to_page(200)
    window._grid_scroll.verticalScrollBar().setValue(
        grid.metrics.cell_rect(200).top() - grid.metrics.margin
    )
    anchor = grid.first_visible_page()
    old_cols = grid.metrics.cols

    window.resize(window.width() + 3 * (grid.metrics.item_w + grid.metrics.spacing), window.height())
    window._relayout_grid()

    assert grid.metrics.cols != old_cols
    new_first = grid.first_visible_page()
    assert new_first <= anchor < new_first + grid.metrics.cols


def test_relayout_without_metric_change_does_not_move_scroll_position(big_window):
    window = big_window
    window.scroll_to_page(120)
    vbar = window._grid_scroll.verticalScrollBar()
    vbar.setValue(vbar.value() + 7)  # 行の途中
    value = vbar.value()
    window._relayout_grid()
    assert vbar.value() == value


def test_relayout_does_not_anchor_while_grid_is_hidden(big_window):
    window = big_window
    window.scroll_to_page(150)
    vbar = window._grid_scroll.verticalScrollBar()
    value = vbar.value()
    window._open_zoom_view(0)
    assert window._grid_scroll.isHidden()
    window._preferred_thumb_size += 2 * window.PREVIEW_THUMB_STEP
    window._relayout_grid()
    # 非表示の間はスクロール位置を触らない(復帰時に拡大表示側が位置を決める)。
    assert vbar.value() == value


# --- 文書の使い回し(開き直しを減らす) ---------------------------------------


def test_load_pages_keeps_the_doc_open_only_for_heavy_documents(qtbot, tmp_path):
    from src.utils.pdf_utils import common

    heavy = tmp_path / "heavy-hold.pdf"
    make_pdf(heavy, pages=PAGES, width=80, height=80)
    window = create_page_edit_window(qtbot, heavy)
    window._release_held_doc()
    window._load_pages()
    assert any(key[1] == str(heavy) for key in common._held_docs)
    assert window._held_doc_idle_timer.isActive()
    window._release_held_doc()
    assert not any(key[1] == str(heavy) for key in common._held_docs)

    light = tmp_path / "light-hold.pdf"
    make_pdf(light, pages=5, width=80, height=80)
    window2 = create_page_edit_window(qtbot, light)
    assert not any(key[1] == str(light) for key in common._held_docs)
