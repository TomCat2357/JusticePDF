"""ページ一覧での検索の使い勝手(現在のヒット表示・選択を触らない・基準位置・中央寄せ・状態表示)のテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.views.page_edit_widgets import PageThumbnail
from src.views.page_edit_window import PageEditWindow
from tests.helpers import create_page_edit_window, open_zoom


def _make_pdf(path, pages, counts):
    """counts: {ページ番号: needle の個数}。"""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 30), f"page {i}", fontname="helv", fontsize=12)
        for k in range(counts.get(i, 0)):
            page.insert_text((20, 60 + 20 * k), "needle", fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()


HITS = {3: 1, 13: 1, 23: 1, 33: 1}


def _window(qtbot, tmp_path, pages=40, counts=None):
    path = tmp_path / "ux.pdf"
    _make_pdf(path, pages, HITS if counts is None else counts)
    window = create_page_edit_window(qtbot, path)
    qtbot.wait(50)
    assert window._page_count == pages
    window._on_open_search()
    return window


@pytest.fixture
def chunked(monkeypatch):
    monkeypatch.setattr(PageEditWindow, "SEARCH_SYNC_MAX_PAGES", 5)
    monkeypatch.setattr(PageEditWindow, "SEARCH_TICK_BUDGET_S", 0.0)


def _status(window) -> str:
    return window._search_dialog._status_label.text()


# --- サムネイルの見た目の組み合わせ -------------------------------------


@pytest.mark.parametrize(
    ("flags", "state"),
    [
        ((False, False, False, False), "normal"),
        ((False, False, True, False), "search_hit"),
        ((False, False, True, True), "current_hit"),
        ((True, False, False, False), "selected"),
        ((True, False, True, False), "selected_hit"),
        ((True, False, True, True), "selected_current"),
        ((False, True, True, True), "droptarget"),
        ((True, True, True, True), "droptarget"),
        ((False, True, False, False), "droptarget"),
    ],
)
def test_thumbnail_state_combinations(qtbot, tmp_path, flags, state):
    widget = PageThumbnail(str(tmp_path / "x.pdf"), 0)
    qtbot.addWidget(widget)
    widget.set_state(*flags)
    assert widget.property("state") == state
    assert widget.is_current_hit == flags[3]


def test_thumbnail_state_skips_repolish_when_unchanged(qtbot, tmp_path, monkeypatch):
    widget = PageThumbnail(str(tmp_path / "x.pdf"), 0)
    qtbot.addWidget(widget)
    calls = []
    original = widget._update_style
    monkeypatch.setattr(widget, "_update_style", lambda: (calls.append(1), original())[1])
    widget.set_state(False, False, True, True)
    widget.set_state(False, False, True, True)
    assert calls == [1]
    widget.set_state(False, False, True, False)
    assert calls == [1, 1]
    widget.unbind()
    assert widget.property("state") == "normal" and not widget.is_current_hit


def test_current_hit_follows_pages_and_is_cleared(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    assert window._search_current == 3
    assert window.widget_for_page(3).property("state") == "current_hit"
    assert window.widget_for_page(13).property("state") == "search_hit"
    window._on_search_next()
    assert window._search_current == 13
    assert window.widget_for_page(3).property("state") == "search_hit"
    assert window.widget_for_page(13).property("state") == "current_hit"
    # 手動で選ぶと選択 + 現在のヒット
    window._select_pages([13])
    assert window.widget_for_page(13).property("state") == "selected_current"
    window._select_pages([23])
    assert window.widget_for_page(23).property("state") == "selected_hit"
    window._clear_search_highlights()
    assert window._search_current is None
    assert window.widget_for_page(13).property("state") == "normal"
    assert window.widget_for_page(23).property("state") == "selected"


# --- 選択を触らない -----------------------------------------------------


def test_search_and_next_prev_keep_selection_and_buttons(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    assert not window._delete_btn.isEnabled() and not window._rotate_btn.isEnabled()
    window._on_search_execute("needle")
    for _ in range(6):
        window._on_search_next()
    for _ in range(3):
        window._on_search_prev()
    assert list(window._selected_pages) == []
    assert not window._delete_btn.isEnabled() and not window._rotate_btn.isEnabled()


def test_user_selection_survives_search(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._select_pages([30, 31])
    delete_enabled = window._delete_btn.isEnabled()
    assert delete_enabled
    window._on_search_execute("needle")
    for _ in range(5):
        window._on_search_next()
    assert list(window._selected_pages) == [30, 31]
    assert window._delete_btn.isEnabled() == delete_enabled


# --- 次へ/前へはユーザーの位置に従う -------------------------------------


def test_next_prev_start_from_clicked_page(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    assert window._search_current == 3
    window._on_page_clicked(14)
    window._on_search_next()
    assert window._search_current == 23
    window._on_page_clicked(14)
    window._on_search_prev()
    assert window._search_current == 13
    # ジャンプした後は、現在のヒットから進む
    window._on_search_prev()
    assert window._search_current == 3


def test_next_prev_wrap_around(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    window._on_page_clicked(35)
    window._on_search_next()
    assert window._search_current == 3
    window._on_page_clicked(1)
    window._on_search_prev()
    assert window._search_current == 33
    # クリックしたページがヒットなら、その次/前
    window._on_page_clicked(13)
    window._on_search_next()
    assert window._search_current == 23
    window._on_page_clicked(13)
    window._on_search_prev()
    assert window._search_current == 3
    # 全ヒットを順に回る
    seen = [window._search_current]
    for _ in range(4):
        window._on_search_next()
        seen.append(window._search_current)
    assert seen == [3, 13, 23, 33, 3]


def test_rubber_band_selection_sets_anchor(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    window._set_rubber_band_selection([14, 15])
    window._on_search_next()
    assert window._search_current == 23


# --- 検索開始の基準位置 -------------------------------------------------


@pytest.mark.parametrize(("selected", "expected"), [(0, 3), (5, 13), (13, 13), (25, 33), (35, 3)])
def test_search_starts_from_selected_page(qtbot, tmp_path, selected, expected):
    window = _window(qtbot, tmp_path)
    window._select_pages([selected, 39])
    window._on_search_execute("needle")
    assert window._search_current == expected
    assert list(window._selected_pages) == [selected, 39]


def test_search_starts_from_first_visible_page(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, pages=300, counts={5: 1, 100: 1, 200: 1})
    window._grid._scroll.verticalScrollBar().setValue(
        window._grid.metrics.cell_rect(110).top()
    )
    first = window._grid.first_visible_page()
    assert first is not None and 100 < first <= 200
    window._on_search_execute("needle")
    assert window._search_current == 200
    assert list(window._selected_pages) == []


def test_search_in_zoom_starts_from_zoom_page(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    open_zoom(window, qtbot)
    window._zoom_page_num = 20
    window._select_pages([1])
    window._on_search_execute("needle")
    assert window._search_current == 23
    assert window._zoom_page_num == 23
    window._on_search_next()
    assert window._zoom_page_num == 33
    window._on_search_next()
    assert window._zoom_page_num == 3  # 末尾から先頭へ回る
    window._on_search_prev()
    assert window._zoom_page_num == 33
    assert list(window._selected_pages) == [1]


@pytest.mark.parametrize(("selected", "expected"), [(25, 33), (35, 3)])
def test_chunked_search_jumps_once_from_reference(qtbot, tmp_path, chunked, monkeypatch, selected, expected):
    window = _window(qtbot, tmp_path, pages=60)
    window._select_pages([selected])
    jumps = []
    orig = window._jump_to_search_page
    monkeypatch.setattr(window, "_jump_to_search_page", lambda p: (jumps.append(p), orig(p))[1])
    window._on_search_execute("needle")
    assert window._search_scan is not None
    qtbot.waitUntil(lambda: window._search_scan is None, timeout=15000)
    assert jumps == [expected]
    assert window._search_current == expected
    assert list(window._selected_pages) == [selected]


def test_chunked_search_cancel_without_hit_after_reference_wraps(qtbot, tmp_path, chunked):
    window = _window(qtbot, tmp_path, pages=60)
    window._select_pages([35])
    window._on_search_execute("needle")
    window._search_timer.stop()
    for _ in range(10):  # 先頭から少しだけ走査する: 基準(35)より後のヒットはまだ無い
        window._search_step()
        if window._search_hit_pages:
            break
    assert window._search_scan is not None and window._search_hit_pages
    assert window._search_current is None
    window._on_search_cancel()
    assert window._search_current == window._search_hit_pages[0]


# --- 中央寄せ -----------------------------------------------------------


def _cell_center_in_viewport(window, page):
    grid = window._grid
    rect = grid.metrics.cell_rect(page)
    top = rect.top() - grid._scroll.verticalScrollBar().value()
    return top + rect.height() / 2, grid._scroll.viewport().height()


def test_search_jump_centers_target_in_long_document(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, pages=400, counts={5: 1, 200: 1, 390: 1})
    window._on_search_execute("needle")
    qtbot.waitUntil(lambda: window._search_scan is None, timeout=15000)
    assert window._search_current == 5
    window._on_search_next()
    assert window._search_current == 200
    y, height = _cell_center_in_viewport(window, 200)
    assert height / 3 <= y <= height * 2 / 3
    assert window._grid.widget_for_page(200) is not None
    assert list(window._selected_pages) == []


def test_scroll_page_to_center_does_not_move_when_comfortably_visible(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, pages=400, counts={})
    grid = window._grid
    vbar = grid._scroll.verticalScrollBar()
    grid.scroll_page_to_center(200)
    value = vbar.value()
    # 中央付近の別の行(同じ行・隣の行)は動かさない
    cols = grid.metrics.cols
    for page in (200, 200 + cols if grid.metrics.cell_rect(200 + cols).bottom() - value < grid._scroll.viewport().height() * 0.85 else 200):
        grid.scroll_page_to_center(page)
        assert vbar.value() == value
    # 見えていないページは動かす
    grid.scroll_page_to_center(10)
    assert vbar.value() != value


def test_scroll_page_to_center_clamps_at_ends(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, pages=400, counts={})
    grid = window._grid
    vbar = grid._scroll.verticalScrollBar()
    grid.scroll_page_to_center(399)
    assert vbar.value() == vbar.maximum()
    grid.scroll_page_to_center(0)
    assert vbar.value() == 0


# --- 状態表示 -----------------------------------------------------------


def test_status_counts_pages_and_occurrences(qtbot, tmp_path):
    counts = dict(enumerate([3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 1, 1]))
    assert sum(counts.values()) == 25
    window = _window(qtbot, tmp_path, pages=20, counts=counts)
    window._on_search_execute("needle")
    assert len(window._search_hit_pages) == 12
    assert _status(window) == "ページ 1 / 12"
    window._on_search_next()
    assert _status(window) == "ページ 2 / 12"
    window._on_search_prev()
    window._on_search_prev()
    assert _status(window) == "ページ 12 / 12"
    window._on_search_execute("zzz-none")
    assert _status(window) == "見つかりません"


def test_status_label_strings_and_stable_width(qtbot):
    from src.views.search_dialog import SearchDialog

    dialog = SearchDialog()
    qtbot.addWidget(dialog)
    label = dialog._status_label
    min_width = label.minimumWidth()
    assert min_width > 0
    dialog.set_progress(50, 300, 3)
    assert label.text() == "検索中… 50 / 300 ページ（ヒット 3 ページ）"
    dialog.set_progress(50, 300, 3, 2)
    assert label.text() == "ページ 2 / 3 ・検索中… 50 / 300 ページ"
    dialog.set_status(2, 3)
    assert label.text() == "ページ 2 / 3"
    dialog.set_status(0, 0)
    assert label.text() == "見つかりません"
    assert label.minimumWidth() == min_width
    for longest in (
        "ページ 999 / 999 ・検索中… 99,999 / 99,999 ページ",
        "検索中… 99,999 / 99,999 ページ（ヒット 999 ページ）",
    ):
        assert label.fontMetrics().horizontalAdvance(longest) <= min_width


def test_status_during_chunked_scan(qtbot, tmp_path, chunked):
    window = _window(qtbot, tmp_path, pages=60)
    window._on_search_execute("needle")
    assert window._search_scan is not None
    assert "検索中… " in _status(window)
    qtbot.waitUntil(lambda: window._search_scan is None, timeout=15000)
    assert _status(window) == "ページ 1 / 4"
