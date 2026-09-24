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
