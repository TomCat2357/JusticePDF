from __future__ import annotations

import pytest

from src.utils.pdf_utils import TocEntry
from src.views.bookmarks_panel import BookmarksPanel


pytestmark = pytest.mark.usefixtures("qapp")


def _entries():
    return [
        TocEntry(1, "Chapter 1", 1),
        TocEntry(2, "Section 1.1", 2),
        TocEntry(2, "Section 1.2", 3),
        TocEntry(3, "Sub 1.2.1", 4),
        TocEntry(1, "Chapter 2", 5),
    ]


def test_build_tree_roundtrip():
    panel = BookmarksPanel()
    panel.load_entries(_entries())
    assert panel._tree_to_entries() == _entries()


def test_demote_makes_child_of_prev_sibling():
    panel = BookmarksPanel()
    panel.load_entries(_entries())
    # Select "Chapter 2" (top-level index 1) and demote -> becomes child of Chapter 1
    ch2 = panel._tree.topLevelItem(1)
    panel._tree.setCurrentItem(ch2)

    captured = []
    panel.bookmarks_changed.connect(lambda entries, desc: captured.append((entries, desc)))
    panel._on_demote()

    assert panel._tree.topLevelItemCount() == 1
    entries, desc = captured[-1]
    assert desc == "しおり降格"
    # Chapter 2 now level 2, last entry
    assert entries[-1] == TocEntry(2, "Chapter 2", 5)


def test_demote_blocked_for_first_sibling():
    panel = BookmarksPanel()
    panel.load_entries(_entries())
    first = panel._tree.topLevelItem(0)
    panel._tree.setCurrentItem(first)

    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append(d))
    panel._on_demote()

    assert captured == []  # nothing changed
    assert panel._tree.topLevelItemCount() == 2


def test_promote_raises_level():
    panel = BookmarksPanel()
    panel.load_entries(_entries())
    # "Sub 1.2.1" is a grandchild; promote -> becomes child of Chapter1 (level 2),
    # placed after its old parent "Section 1.2"
    ch1 = panel._tree.topLevelItem(0)
    sec12 = ch1.child(1)  # Section 1.2
    sub = sec12.child(0)  # Sub 1.2.1
    panel._tree.setCurrentItem(sub)

    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append((e, d)))
    panel._on_promote()

    entries, desc = captured[-1]
    assert desc == "しおり昇格"
    # Sub 1.2.1 should now be level 2
    titles_levels = [(e.title, e.level) for e in entries]
    assert ("Sub 1.2.1", 2) in titles_levels


def test_move_down_within_siblings():
    panel = BookmarksPanel()
    panel.load_entries(_entries())
    ch1 = panel._tree.topLevelItem(0)
    sec11 = ch1.child(0)  # Section 1.1
    panel._tree.setCurrentItem(sec11)

    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append((e, d)))
    panel._move_within_siblings(1)

    entries, desc = captured[-1]
    assert desc == "しおり移動"
    # Order of the two sections under Chapter 1 swapped
    titles = [e.title for e in entries]
    assert titles.index("Section 1.2") < titles.index("Section 1.1")


def test_add_current_page_uses_provider():
    panel = BookmarksPanel()
    panel.load_entries([])
    panel.set_current_page_provider(lambda: 7)

    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append((e, d)))
    panel._on_add_current_page()

    entries, desc = captured[-1]
    assert desc == "しおり追加"
    assert entries[0].page == 7
    assert entries[0].level == 1


def test_jump_requested_on_click():
    panel = BookmarksPanel()
    panel.load_entries(_entries())
    jumps = []
    panel.jump_requested.connect(jumps.append)

    sec = panel._tree.topLevelItem(0).child(0)  # Section 1.1, page 2
    panel._on_item_clicked(sec, 0)
    assert jumps == [2]
