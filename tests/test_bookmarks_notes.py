"""しおりパネルへの付箋集約（ページ単位・該当ページのみ・畳む）のテスト。"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from src.utils.pdf_utils import TocEntry
from src.views.bookmarks_panel import (
    BookmarksPanel,
    _NODE_KIND_ROLE,
    _XREF_ROLE,
)


pytestmark = pytest.mark.usefixtures("qapp")


def _flatten(panel):
    rows = []

    def walk(item):
        rows.append(item)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(panel._tree.topLevelItemCount()):
        walk(panel._tree.topLevelItem(i))
    return rows


def _kinds(panel):
    return [item.data(0, _NODE_KIND_ROLE) for item in _flatten(panel)]


def test_notes_nest_under_nearest_bookmark_and_collapse(qtbot):
    panel = BookmarksPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.load_entries([
        TocEntry(level=1, title="Chapter 1", page=1),
        TocEntry(level=1, title="Chapter 2", page=3),
    ])
    panel.set_annotation_notes([
        (2, 11, "note on page 2"),
        (3, 22, "note on page 3"),
    ])

    rows = _flatten(panel)
    labels = [r.text(0) for r in rows]
    kinds = [r.data(0, _NODE_KIND_ROLE) for r in rows]

    assert "Chapter 1" in labels and "Chapter 2" in labels
    # 付箋グループが2つ（各章に1つ）。
    assert kinds.count("note_group") == 2
    assert kinds.count("note") == 2

    # グループは既定で畳まれている。
    for item in rows:
        if item.data(0, _NODE_KIND_ROLE) == "note_group":
            assert item.isExpanded() is False

    # 付箋ノードは正しい xref / page を保持。
    note_items = [r for r in rows if r.data(0, _NODE_KIND_ROLE) == "note"]
    by_xref = {r.data(0, _XREF_ROLE): r for r in note_items}
    assert set(by_xref.keys()) == {11, 22}


def test_note_nodes_excluded_from_toc(qtbot):
    panel = BookmarksPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.load_entries([TocEntry(level=1, title="Only", page=1)])
    panel.set_annotation_notes([(1, 5, "a note")])

    entries = panel._tree_to_entries()
    assert [(e.title, e.page) for e in entries] == [("Only", 1)]


def test_empty_notes_clears_note_nodes(qtbot):
    panel = BookmarksPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.load_entries([TocEntry(level=1, title="Ch", page=1)])
    panel.set_annotation_notes([(1, 5, "a note")])
    assert "note_group" in _kinds(panel)

    panel.set_annotation_notes([])
    assert "note_group" not in _kinds(panel)
    assert "note" not in _kinds(panel)


def test_note_click_emits_note_jump(qtbot):
    panel = BookmarksPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.load_entries([TocEntry(level=1, title="Ch", page=1)])
    panel.set_annotation_notes([(2, 77, "jump me")])

    note_item = next(
        r for r in _flatten(panel) if r.data(0, _NODE_KIND_ROLE) == "note"
    )

    received = []
    panel.note_jump_requested.connect(lambda page, xref: received.append((page, xref)))
    panel._on_item_clicked(note_item, 0)
    assert received == [(2, 77)]


def test_notes_without_bookmarks_go_top_level(qtbot):
    panel = BookmarksPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.load_entries([])
    panel.set_annotation_notes([(1, 1, "orphan note")])

    top_kinds = [
        panel._tree.topLevelItem(i).data(0, _NODE_KIND_ROLE)
        for i in range(panel._tree.topLevelItemCount())
    ]
    assert "note_group" in top_kinds
