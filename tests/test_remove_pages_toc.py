"""ページ削除でしおりが取り残されない(削除ページのしおりは消え、子は繰り上がる)ことのテスト。"""
from __future__ import annotations

import pytest

from src.utils.pdf_utils import (
    TocEntry,
    filter_toc_for_removed_pages,
    get_pdf_toc,
    remove_pages,
)
from tests.helpers import create_page_edit_window, make_pdf


def _t(*rows):
    return [TocEntry(*r) for r in rows]


def _rows(entries):
    return [(e.level, e.title, e.page) for e in entries]


def test_filter_promotes_children_and_renumbers():
    entries = _t((1, "A", 1), (2, "A1", 2), (3, "A1a", 3), (2, "A2", 4), (1, "B", 5))
    # 2, 4 ページ目(0始まり 1, 3)を削除
    result = filter_toc_for_removed_pages(entries, [1, 3])
    assert _rows(result) == [(1, "A", 1), (2, "A1a", 2), (1, "B", 3)]


def test_filter_removed_parent_promotes_all_children():
    entries = _t((1, "A", 1), (2, "A1", 2), (2, "A2", 3), (1, "B", 4))
    assert _rows(filter_toc_for_removed_pages(entries, [0])) == [
        (1, "A1", 1),
        (1, "A2", 2),
        (1, "B", 3),
    ]


def test_filter_consecutive_removed_parents():
    entries = _t((1, "A", 1), (2, "A1", 1), (3, "A1a", 2), (4, "A1a1", 3), (3, "A1b", 4))
    # 1 ページ目(A と A1 の親子)を削除 → A1a は level 1、A1a1 は 2、A1b は 1
    assert _rows(filter_toc_for_removed_pages(entries, [0])) == [
        (1, "A1a", 1),
        (2, "A1a1", 2),
        (1, "A1b", 3),
    ]


def test_filter_all_removed_and_nothing_removed():
    entries = _t((1, "A", 1), (2, "B", 2))
    assert filter_toc_for_removed_pages(entries, [0, 1]) == []
    assert _rows(filter_toc_for_removed_pages(entries, [])) == [(1, "A", 1), (2, "B", 2)]


def test_remove_pages_drops_dangling_bookmarks_on_disk(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_pdf(
        pdf,
        pages=5,
        toc=[[1, "A", 1], [2, "A1", 2], [3, "A1a", 3], [2, "A2", 4], [1, "B", 5]],
    )
    assert remove_pages(str(pdf), [1, 3]) is False
    assert _rows(get_pdf_toc(str(pdf))) == [(1, "A", 1), (2, "A1a", 2), (1, "B", 3)]


def _select_and_delete(window, qtbot, index):
    window._on_thumbnail_clicked(window._thumbnails[index])
    count = len(window._thumbnails)
    window._on_delete()
    qtbot.waitUntil(lambda: len(window._thumbnails) == count - 1)


@pytest.mark.usefixtures("qapp")
def test_delete_page_in_window_updates_toc_tree_and_undo_redo(qtbot, tmp_path):
    pdf = tmp_path / "w.pdf"
    original = [[1, "A", 1], [2, "A1", 2], [1, "B", 3], [2, "B1", 4]]
    make_pdf(pdf, pages=4, toc=original)
    window = create_page_edit_window(qtbot, pdf)
    window._bookmarks_panel.set_open(True)

    _select_and_delete(window, qtbot, 0)  # A のページ

    expected = [(1, "A1", 1), (1, "B", 2), (2, "B1", 3)]
    assert _rows(get_pdf_toc(str(pdf))) == expected
    tree = [(e.level, e.title, e.page) for e in window._bookmarks_panel._tree_to_entries()]
    assert tree == expected

    window._on_undo()
    qtbot.waitUntil(lambda: len(window._thumbnails) == 4)
    assert [[e.level, e.title, e.page] for e in get_pdf_toc(str(pdf))] == original
    tree = [[e.level, e.title, e.page] for e in window._bookmarks_panel._tree_to_entries()]
    assert tree == original

    window._on_redo()
    qtbot.waitUntil(lambda: len(window._thumbnails) == 3)
    assert _rows(get_pdf_toc(str(pdf))) == expected
