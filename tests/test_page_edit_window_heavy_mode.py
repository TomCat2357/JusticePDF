"""重量文書(ページ数/ファイルサイズが閾値超)の逐次処理切り替えのテスト。"""
from __future__ import annotations

import pytest

from src.utils.constants import HEAVY_PDF_PAGE_COUNT_THRESHOLD
from src.utils.pdf_utils import is_heavy_pdf
from tests.helpers import create_page_edit_window, make_pdf


pytestmark = pytest.mark.usefixtures("qtbot")


# ---------------------------------------------------------------------------
# is_heavy_pdf() 単体テスト
# ---------------------------------------------------------------------------

def test_is_heavy_pdf_by_page_count(tmp_path):
    pdf_path = tmp_path / "small.pdf"
    make_pdf(pdf_path, pages=1, width=80, height=80)

    assert is_heavy_pdf(str(pdf_path), page_count=HEAVY_PDF_PAGE_COUNT_THRESHOLD) is False
    assert is_heavy_pdf(str(pdf_path), page_count=HEAVY_PDF_PAGE_COUNT_THRESHOLD + 1) is True


def test_is_heavy_pdf_by_file_size(tmp_path, monkeypatch):
    pdf_path = tmp_path / "big-file.pdf"
    make_pdf(pdf_path, pages=1, width=80, height=80)

    monkeypatch.setattr(
        "src.utils.pdf_utils.common.os.path.getsize",
        lambda _p: 999_999_999,
    )
    assert is_heavy_pdf(str(pdf_path), page_count=1) is True


def test_is_heavy_pdf_missing_file_does_not_raise(tmp_path):
    missing = tmp_path / "does-not-exist.pdf"
    assert is_heavy_pdf(str(missing), page_count=1) is False


# ---------------------------------------------------------------------------
# PageEditWindow: モード切り替え
# ---------------------------------------------------------------------------

def test_small_document_loads_synchronously_in_normal_mode(qtbot, tmp_path):
    pdf_path = tmp_path / "small.pdf"
    make_pdf(pdf_path, pages=5, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    assert window._is_heavy_document is False
    # 全文書で _load_pages() 呼び出し内にページ数が確定する(タイマー待ちが不要)。
    assert window._page_count == 5


def test_heavy_document_sets_page_count_immediately_without_loading_title(qtbot, tmp_path):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-pages.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    assert window._is_heavy_document is True
    # ウィジェットを分割生成しないので、_load_pages() から戻った時点で全ページ分のページ数が揃い、
    # タイトルバーに「読み込み中」の進捗も出ない。
    assert window._page_count == page_count
    assert "読み込み中" not in window.windowTitle()
    assert window.windowTitle() == window._page_edit_window_title()


def test_heavy_document_does_not_prequeue_every_page(qtbot, tmp_path):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-queue.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    window._rendered.clear()
    window._enqueue_visible_thumbnail_renders()

    # 全ページを一括でキューへ積まず、表示範囲(と先読み)のページだけを積む。
    assert 0 < len(window._thumb_render_queue) < page_count
    # 表示中のページが先頭、先読み分がその後ろ。
    first_visible, _stop = window._grid.visible_page_range(0)
    assert window._thumb_render_queue[0] == first_visible


def test_normal_document_enqueues_visible_pages(qtbot, tmp_path):
    page_count = 8
    pdf_path = tmp_path / "normal-queue.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    window._reset_thumbnail_render_queue()
    window._rendered.clear()
    window._enqueue_visible_thumbnail_renders()

    # 8ページは全部が画面内なので、全て積まれる。
    assert sorted(window._thumb_render_queue) == list(range(page_count))


def test_enqueue_skips_rendered_pages_and_pages_far_from_view(qtbot, tmp_path):
    page_count = 400
    pdf_path = tmp_path / "far-queue.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    window._reset_thumbnail_render_queue()
    window._rendered.clear()
    window._enqueue_visible_thumbnail_renders()
    queued = set(window._thumb_render_queue)
    assert 0 in queued
    assert page_count - 1 not in queued
    assert len(queued) < 80

    # 描画済みのページは積み直さない。
    window._process_thumbnail_render_queue()
    done = [p for p in queued if window.is_page_rendered(p)]
    assert done
    window._enqueue_visible_thumbnail_renders()
    assert not (set(window._thumb_render_queue) & set(done))


def test_heavy_document_render_batch_is_single_page(qtbot, tmp_path):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 10
    pdf_path = tmp_path / "heavy-batch.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    # 文書末尾のページを手動でキューに積み、1回のタイマー発火でどこまで描画されるかを検証する
    # (表示範囲から外れた予約は描かずに捨てるので、先にそこまでスクロールしておく)。
    window.scroll_to_page(page_count - 1)
    target_pages = list(range(page_count - 10, page_count))
    window._reset_thumbnail_render_queue()
    window._rendered.clear()
    for pn in target_pages:
        window._thumb_render_queue.append(pn)
        window._thumb_render_queue_set.add(pn)

    window._process_thumbnail_render_queue()

    loaded = sum(1 for pn in target_pages if window.is_page_rendered(pn))
    assert loaded == 1
    assert len(window._thumb_render_queue) == len(target_pages) - 1


def test_normal_document_render_batch_up_to_five(qtbot, tmp_path):
    page_count = 8
    pdf_path = tmp_path / "normal-batch.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    window._reset_thumbnail_render_queue()
    window._rendered.clear()
    for pn in range(page_count):
        window._thumb_render_queue.append(pn)
        window._thumb_render_queue_set.add(pn)

    window._process_thumbnail_render_queue()

    loaded = sum(1 for pn in range(page_count) if window.is_page_rendered(pn))
    assert loaded == 5


# ---------------------------------------------------------------------------
# リサイズ / PII・Ink の走査範囲限定
# ---------------------------------------------------------------------------

def test_resize_with_same_columns_keeps_rendered_thumbnails_and_positions(qtbot, tmp_path):
    pdf_path = tmp_path / "normal-resize.pdf"
    make_pdf(pdf_path, pages=4, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._process_thumbnail_render_queue()
    qtbot.waitUntil(lambda: all(window.is_page_rendered(p) for p in range(4)))
    positions = {p: window.widget_for_page(p).pos() for p in range(4)}
    pixmaps = {p: window._rendered[p].pixmap for p in range(4)}
    bound = {p: window.widget_for_page(p) for p in range(4)}

    window._on_grid_resize_settled()

    # 列数・サムネイルサイズが同じなら、割り当ても位置も描画済み画像も変わらない。
    for p in range(4):
        assert window.widget_for_page(p) is bound[p]
        assert window.widget_for_page(p).pos() == positions[p]
        assert window._rendered[p].pixmap is pixmaps[p]


def test_resize_changing_columns_repositions_cells(qtbot, tmp_path):
    pdf_path = tmp_path / "cols-resize.pdf"
    make_pdf(pdf_path, pages=12, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window.resize(1100, 700)
    qtbot.wait(30)
    window._on_grid_resize_settled()
    cols_wide = window._grid.metrics.cols

    window.resize(400, 700)
    qtbot.wait(30)
    window._on_grid_resize_settled()
    cols_narrow = window._grid.metrics.cols

    assert cols_narrow < cols_wide
    m = window._grid.metrics
    for p in window._grid.bound_pages():
        assert window.widget_for_page(p).pos() == m.cell_rect(p).topLeft()


def test_heavy_document_scans_pii_and_ink_only_for_batch_pages(qtbot, tmp_path, monkeypatch):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-scan.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    pii_scans: list = []
    ink_scans: list = []
    import src.views.page_edit_window as mod

    monkeypatch.setattr(
        mod, "list_pii_targets_by_page",
        lambda _p, pages=None: (pii_scans.append(pages), {})[1],
    )
    monkeypatch.setattr(
        mod, "list_ink_annot_xrefs_by_page",
        lambda _p, pages=None: (ink_scans.append(pages), {})[1],
    )
    window._pii_targets_by_page_cache = None
    window._ink_xrefs_by_page_cache = None

    window._get_pii_targets_by_page([1, 2])
    window._get_ink_xrefs_by_page([1, 2])
    assert pii_scans == [[1, 2]] and ink_scans == [[1, 2]]

    # 走査済みページは再走査せず、未走査ページだけ追加で走査する。
    window._get_pii_targets_by_page([2, 3])
    window._get_ink_xrefs_by_page([2, 3])
    assert pii_scans[-1] == [3] and ink_scans[-1] == [3]
    window._get_pii_targets_by_page([1, 2, 3])
    assert len(pii_scans) == 2

    # 全ページが必要になったら全走査して整合させる。
    window._get_pii_targets_by_page()
    window._get_ink_xrefs_by_page()
    assert pii_scans[-1] is None and ink_scans[-1] is None
    window._get_pii_targets_by_page([5])
    assert len(pii_scans) == 3


def test_resize_event_does_not_relayout_directly(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtGui import QResizeEvent
    from PyQt6.QtCore import QSize

    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-resize-event.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    calls: list[int] = []
    monkeypatch.setattr(window, "_relayout_grid", lambda: calls.append(1))
    window._grid_resize_timer.stop()
    window.resizeEvent(QResizeEvent(QSize(700, 500), QSize(600, 400)))
    # リサイズ通知は並べ直しを直接呼ばず、デバウンス用のタイマーを起動するだけ。
    assert calls == []
    assert window._grid_resize_timer.isActive()

    window._grid_resize_timer.stop()
    window._on_grid_resize_settled()
    assert calls == [1]


def test_render_batch_reuses_single_document_open(qtbot, tmp_path, monkeypatch):
    import fitz

    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-reuse.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._reset_thumbnail_render_queue()
    window._rendered.clear()
    window._pii_targets_by_page_cache = None
    window._ink_xrefs_by_page_cache = None
    window._show_ink_annots = False
    window._enqueue_thumbnail_render(3)

    opens: list[str] = []
    real_open = fitz.open

    def counting_open(*args, **kwargs):
        opens.append(str(args[0]) if args else "")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(fitz, "open", counting_open)
    window._process_thumbnail_render_queue()
    assert len([p for p in opens if p == str(window._pdf_path)]) <= 1
    assert window.is_page_rendered(3)


def test_hold_doc_closes_document_on_exit(tmp_path):
    from src.utils.pdf_utils import _open_doc
    from src.utils.pdf_utils.common import _held_docs, hold_doc

    pdf_path = tmp_path / "hold.pdf"
    make_pdf(pdf_path, pages=2, width=80, height=80)
    with hold_doc(str(pdf_path)):
        with _open_doc(str(pdf_path)) as a:
            pass
        with _open_doc(str(pdf_path)) as b:
            assert a is b
            assert not b.is_closed
    assert b.is_closed
    assert not _held_docs


@pytest.fixture(autouse=True)
def _clear_held_docs():
    from src.utils.pdf_utils.common import release_held_docs

    yield
    release_held_docs()


def test_hold_doc_linger_reuses_document_and_releases(tmp_path):
    from src.utils.pdf_utils import _open_doc
    from src.utils.pdf_utils.common import _held_docs, hold_doc, release_held_docs

    pdf_path = str(tmp_path / "linger.pdf")
    make_pdf(pdf_path, pages=2, width=80, height=80)
    with hold_doc(pdf_path, linger=True):
        with _open_doc(pdf_path) as a:
            pass
    assert not a.is_closed  # スコープを抜けても保持される
    with hold_doc(pdf_path, linger=True):
        with _open_doc(pdf_path) as b:
            pass
    assert a is b
    # スコープ外では保持ドキュメントを使わない(別に開く)
    with _open_doc(pdf_path) as c:
        assert c is not a
    assert not a.is_closed
    release_held_docs(pdf_path)
    assert a.is_closed
    assert not _held_docs


def test_hold_doc_linger_does_not_lock_file_for_replace(tmp_path):
    """保持中でも os.replace / 削除が成功する(ファイルハンドルを掴まない)。"""
    import os

    from src.utils.pdf_utils.common import hold_doc

    pdf_path = tmp_path / "lock.pdf"
    other = tmp_path / "other.pdf"
    make_pdf(pdf_path, pages=2, width=80, height=80)
    make_pdf(other, pages=3, width=80, height=80)
    with hold_doc(str(pdf_path), linger=True):
        pass
    os.replace(other, pdf_path)
    assert pdf_path.exists()


def test_hold_doc_linger_discarded_when_file_changes(tmp_path):
    import os

    import fitz

    from src.utils.pdf_utils import _open_doc
    from src.utils.pdf_utils.common import hold_doc

    pdf_path = tmp_path / "chg.pdf"
    make_pdf(pdf_path, pages=2, width=80, height=80)
    with hold_doc(str(pdf_path), linger=True):
        with _open_doc(str(pdf_path)) as a:
            assert len(a) == 2
    make_pdf(pdf_path, pages=4, width=80, height=80)
    st = os.stat(pdf_path)
    os.utime(pdf_path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    with hold_doc(str(pdf_path), linger=True):
        with _open_doc(str(pdf_path)) as b:
            assert len(b) == 4
    assert b is not a
    assert isinstance(b, fitz.Document)


def test_hold_doc_released_before_disk_write(tmp_path):
    from src.utils.pdf_utils.common import _held_docs, _open_doc_for_write, hold_doc

    pdf_path = str(tmp_path / "w.pdf")
    make_pdf(pdf_path, pages=2, width=80, height=80)
    with hold_doc(pdf_path, linger=True):
        pass
    assert _held_docs
    with _open_doc_for_write(pdf_path):
        pass
    assert not _held_docs


def test_heavy_render_batches_share_held_doc_and_idle_timer_releases(qtbot, tmp_path, monkeypatch):
    from src.utils.pdf_utils.common import _held_docs

    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-linger.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._reset_thumbnail_render_queue()
    window._enqueue_thumbnail_render(3)
    window._process_thumbnail_render_queue()
    assert _held_docs  # バッチ後も保持される
    assert window._held_doc_idle_timer.isActive()
    held = next(iter(_held_docs.values())).doc
    window._enqueue_thumbnail_render(4)
    window._process_thumbnail_render_queue()
    assert next(iter(_held_docs.values())).doc is held  # 次のバッチで使い回す
    window._held_doc_idle_timer.timeout.emit()
    assert not _held_docs
    assert held.is_closed


def test_heavy_window_close_releases_held_doc(qtbot, tmp_path):
    from src.utils.pdf_utils.common import _held_docs

    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-close.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._enqueue_thumbnail_render(2)
    window._process_thumbnail_render_queue()
    assert _held_docs
    from PyQt6.QtCore import Qt

    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window.close()
    assert not _held_docs
