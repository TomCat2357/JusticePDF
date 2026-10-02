"""重量文書(ページ数/ファイルサイズが閾値超)の逐次処理切り替えのテスト。"""
from __future__ import annotations

import pytest

from src.utils.constants import (
    HEAVY_PDF_PAGE_COUNT_THRESHOLD,
    HEAVY_PDF_WIDGET_CHUNK_SIZE,
)
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
    # 通常文書は _load_pages() 呼び出し内で全ウィジェットが同期的に揃う
    # (チャンク分割やタイマー待ちが不要=既存の挙動を維持)。
    assert len(window._thumbnails) == 5


def test_heavy_document_builds_widgets_incrementally(qtbot, tmp_path):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-pages.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    assert window._is_heavy_document is True
    # _load_pages() から戻った直後は最初のチャンク分しか生成されていない
    # (全ページ分を同期生成する場合、ここで既に page_count と一致してしまう)。
    assert 0 < len(window._thumbnails) <= HEAVY_PDF_WIDGET_CHUNK_SIZE

    qtbot.waitUntil(lambda: len(window._thumbnails) == page_count, timeout=10000)
    assert len(window._thumbnails) == page_count


def test_heavy_document_does_not_prequeue_every_page(qtbot, tmp_path):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-queue.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    qtbot.waitUntil(lambda: len(window._thumbnails) == page_count, timeout=10000)

    window._enqueue_all_thumbnail_renders()

    # 通常文書は全ページをキューへ積むが、重量文書は表示範囲のページだけを
    # 逐次(オンデマンドで)積む。
    assert len(window._thumb_render_queue) < page_count


def test_normal_document_prequeues_every_page(qtbot, tmp_path):
    page_count = 8
    pdf_path = tmp_path / "normal-queue.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    window._reset_thumbnail_render_queue()
    for thumb in window._thumbnails:
        thumb.invalidate_thumbnail()
    window._enqueue_all_thumbnail_renders()

    assert len(window._thumb_render_queue) == page_count


def test_heavy_document_render_batch_is_single_page(qtbot, tmp_path):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 10
    pdf_path = tmp_path / "heavy-batch.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    qtbot.waitUntil(lambda: len(window._thumbnails) == page_count, timeout=10000)

    # ウィンドウ下端付近(通常は表示範囲外)のページを手動でキューに積み、
    # 1回のタイマー発火でどこまで描画されるかを検証する。
    target_pages = list(range(page_count - 10, page_count))
    window._reset_thumbnail_render_queue()
    for pn in target_pages:
        window._thumb_render_queue.append(pn)
        window._thumb_render_queue_set.add(pn)

    window._process_thumbnail_render_queue()

    loaded = sum(1 for pn in target_pages if window._thumbnails[pn].thumbnail_loaded)
    assert loaded == 1
    assert len(window._thumb_render_queue) == len(target_pages) - 1


def test_normal_document_render_batch_up_to_five(qtbot, tmp_path):
    page_count = 8
    pdf_path = tmp_path / "normal-batch.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)

    window._reset_thumbnail_render_queue()
    for pn in range(page_count):
        window._thumbnails[pn].invalidate_thumbnail()
        window._thumb_render_queue.append(pn)
        window._thumb_render_queue_set.add(pn)

    window._process_thumbnail_render_queue()

    loaded = sum(1 for t in window._thumbnails if t.thumbnail_loaded)
    assert loaded == 5


# ---------------------------------------------------------------------------
# 読み込み中のリサイズ抑止 / PII・Ink の走査範囲限定
# ---------------------------------------------------------------------------

def test_heavy_document_defers_resize_refresh_until_load_done(qtbot, tmp_path, monkeypatch):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-resize.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    assert window._pending_widget_pages is not None

    calls: list[int] = []
    original = window._refresh_grid
    monkeypatch.setattr(window, "_refresh_grid", lambda: (calls.append(1), original())[1])

    # 読み込み中のリサイズ通知では並べ直さず、完了時に1回だけ反映する。
    window._on_grid_resize_settled()
    assert calls == []
    qtbot.waitUntil(lambda: window._pending_widget_pages is None, timeout=10000)
    assert calls == [1]


def test_resize_with_same_columns_skips_relayout(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "normal-resize.pdf"
    make_pdf(pdf_path, pages=4, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window._refresh_grid()

    calls: list[int] = []
    monkeypatch.setattr(window, "_refresh_grid", lambda: calls.append(1))
    window._on_grid_resize_settled()
    assert calls == []

    # 列数が変わるなら並べ直す。
    window._grid_laid_out_cols = (window._grid_laid_out_cols or 1) + 1
    window._on_grid_resize_settled()
    assert calls == [1]


def test_heavy_document_scans_pii_and_ink_only_for_batch_pages(qtbot, tmp_path, monkeypatch):
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-scan.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    qtbot.waitUntil(lambda: window._pending_widget_pages is None, timeout=10000)

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


def test_resize_event_does_not_call_refresh_grid_directly(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtGui import QResizeEvent
    from PyQt6.QtCore import QSize

    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-resize-event.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    assert window._pending_widget_pages is not None

    calls: list[int] = []
    monkeypatch.setattr(window, "_refresh_grid", lambda: calls.append(1))
    window.resizeEvent(QResizeEvent(QSize(700, 500), QSize(600, 400)))
    assert calls == []
    assert window._grid_resize_timer.isActive()

    # 読み込み中にタイマーが満了しても並べ直さず、完了待ちの印だけ付ける。
    window._grid_resize_timer.stop()
    window._on_grid_resize_settled()
    assert calls == []
    assert window._grid_resize_deferred is True


def test_render_batch_reuses_single_document_open(qtbot, tmp_path, monkeypatch):
    import fitz

    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-reuse.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    qtbot.waitUntil(lambda: window._pending_widget_pages is None, timeout=10000)
    window._reset_thumbnail_render_queue()
    for thumb in window._thumbnails:
        thumb.invalidate_thumbnail()
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
    assert window._thumbnails[3].thumbnail_loaded


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
    qtbot.waitUntil(lambda: window._pending_widget_pages is None, timeout=10000)
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
    qtbot.waitUntil(lambda: window._pending_widget_pages is None, timeout=10000)
    window._enqueue_thumbnail_render(2)
    window._process_thumbnail_render_queue()
    assert _held_docs
    from PyQt6.QtCore import Qt

    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window.close()
    assert not _held_docs


def test_refresh_grid_keeps_thumbnails_parented_and_respects_hidden(qtbot, tmp_path):
    """再配置は親を外さない(setParent(None) は件数の2乗で遅い)。非表示ページは並べない。"""
    pdf_path = tmp_path / "grid.pdf"
    make_pdf(pdf_path, pages=6, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    window.hide_page(2)
    window._refresh_grid()
    assert all(t.parentWidget() is window._container for t in window._thumbnails)
    laid_out = [
        window._grid_layout.itemAt(i).widget() for i in range(window._grid_layout.count())
    ]
    assert window._thumbnails[2] not in laid_out
    assert len(laid_out) == 5
    assert window._thumbnails[2].isHidden()


def test_heavy_chunk_does_not_toggle_container_updates(qtbot, tmp_path, monkeypatch):
    """container.setUpdatesEnabled(True) は全子孫へ再帰し数千件で約1秒かかるため、チャンクでは呼ばない。"""
    page_count = HEAVY_PDF_PAGE_COUNT_THRESHOLD + 5
    pdf_path = tmp_path / "heavy-upd.pdf"
    make_pdf(pdf_path, pages=page_count, width=80, height=80)
    window = create_page_edit_window(qtbot, pdf_path)
    calls: list[bool] = []
    real = window._container.setUpdatesEnabled
    monkeypatch.setattr(
        window._container, "setUpdatesEnabled", lambda v: (calls.append(v), real(v))[1]
    )
    qtbot.waitUntil(lambda: window._pending_widget_pages is None, timeout=10000)
    assert calls == []
