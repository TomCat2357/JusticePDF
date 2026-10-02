from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLineEdit

from src.utils.pdf_utils import NoteAnnotData, create_note_annot, get_pdf_toc
from src.views.bookmarks_panel import _NODE_KIND_ROLE, _PAGE_ROLE
from tests.helpers import create_page_edit_window, make_pdf


def _add_bookmark(qtbot, panel, title=None):
    """追加 → (任意でタイトル入力) → Enter で確定する。"""
    # 起動直後の遅延再読込(しおりツリー再構築)が編集中に走って未確定項目を
    # 取り消さないよう、先にイベントを流しておく。
    QApplication.processEvents()
    panel._on_add_current_page()
    editor = panel._tree.findChild(QLineEdit)
    assert editor is not None
    if title is not None:
        editor.setText(title)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()  # デリゲートの確定は queued


def test_add_bookmark_persists_and_undo_redo(qtbot, tmp_path):
    pdf_path = tmp_path / "bm.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)

    window._open_zoom_view(2)  # 0-based page index 2 -> page 3
    window._bookmarks_panel.set_open(True)
    _add_bookmark(qtbot, window._bookmarks_panel)

    toc = get_pdf_toc(str(pdf_path))
    assert len(toc) == 1
    assert toc[0].page == 3  # 1-based
    assert toc[0].level == 1

    # Undo removes the bookmark from disk
    window._on_undo()
    assert get_pdf_toc(str(pdf_path)) == []

    # Redo re-adds it
    window._on_redo()
    redone = get_pdf_toc(str(pdf_path))
    assert len(redone) == 1
    assert redone[0].page == 3


def test_jump_changes_zoom_page(qtbot, tmp_path):
    pdf_path = tmp_path / "jump.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)

    window._open_zoom_view(0)
    window._bookmarks_panel.set_open(True)

    window._jump_zoom_to_page(4)  # page 4 -> index 3
    assert window._zoom_page_num == 3

    # Out-of-range pages are clamped to the last page
    window._jump_zoom_to_page(99)
    assert window._zoom_page_num == 3


def test_hierarchy_persists_to_disk(qtbot, tmp_path):
    pdf_path = tmp_path / "hier.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)

    window._open_zoom_view(0)
    panel = window._bookmarks_panel
    panel.set_open(True)

    # Add two top-level bookmarks (page 1), then demote the second under the first.
    _add_bookmark(qtbot, panel)
    window._zoom_page_num = 1
    _add_bookmark(qtbot, panel)

    second = panel._tree.topLevelItem(1)
    panel._tree.setCurrentItem(second)
    panel._on_demote()

    toc = get_pdf_toc(str(pdf_path))
    assert [e.level for e in toc] == [1, 2]


def test_drawer_mutually_exclusive_with_annotation_drawer(qtbot, tmp_path):
    pdf_path = tmp_path / "excl.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(0)

    window._bookmarks_panel.set_open(True)
    window._set_zoom_annotation_drawer_open(True)
    assert window._bookmarks_panel.is_open is False

    window._bookmarks_panel.set_open(True)
    assert window._zoom_annotation_open is False


def _find_note_item(panel):
    def walk(item):
        if item.data(0, _NODE_KIND_ROLE) == "note":
            return item
        for i in range(item.childCount()):
            found = walk(item.child(i))
            if found is not None:
                return found
        return None

    for i in range(panel._tree.topLevelItemCount()):
        found = walk(panel._tree.topLevelItem(i))
        if found is not None:
            return found
    return None


def test_new_bookmark_immediately_merges_notes(qtbot, tmp_path):
    """新規しおり作成直後に、付箋がそのしおり配下へ即マージされる(再ナビ不要)。"""
    pdf_path = tmp_path / "merge.pdf"
    make_pdf(pdf_path, pages=4)
    # 3ページ目(index 2)に付箋を1つ作成。
    create_note_annot(
        str(pdf_path),
        NoteAnnotData(page_num=2, xref=0, point=(40, 40), content="note p3", color=(1, 1, 0)),
    )

    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(2)  # page index 2 -> 1-based page 3
    panel = window._bookmarks_panel
    panel.set_open(True)

    # しおりがまだ無いので、付箋はトップレベルのグループにぶら下がっている。
    note_item = _find_note_item(panel)
    assert note_item is not None
    assert note_item.parent().parent() is None  # note -> note_group(top-level)

    # 3ページ目にしおりを追加すると、付箋がそのしおり配下へ即マージされる。
    _add_bookmark(qtbot, panel)

    note_item = _find_note_item(panel)
    assert note_item is not None
    group = note_item.parent()
    bookmark = group.parent()
    assert bookmark is not None  # もはやトップレベルではない
    assert bookmark.data(0, _NODE_KIND_ROLE) is None  # しおり(付箋ノードではない)
    assert bookmark.data(0, _PAGE_ROLE) == 3


def test_add_with_name_is_single_undo_entry(qtbot, tmp_path):
    pdf_path = tmp_path / "one.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(1)
    panel = window._bookmarks_panel
    panel.set_open(True)

    _add_bookmark(qtbot, panel, "第一章")
    toc = get_pdf_toc(str(pdf_path))
    assert [(e.title, e.page) for e in toc] == [("第一章", 2)]

    # 追加+命名で Undo は1件だけ: 1回の Undo でしおりごと消える。
    window._on_undo()
    assert get_pdf_toc(str(pdf_path)) == []

    window._on_redo()
    assert [(e.title, e.page) for e in get_pdf_toc(str(pdf_path))] == [("第一章", 2)]


def test_add_escape_leaves_toc_untouched(qtbot, tmp_path):
    pdf_path = tmp_path / "esc.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(0)
    panel = window._bookmarks_panel
    panel.set_open(True)

    panel._on_add_current_page()
    qtbot.keyClick(panel._tree.findChild(QLineEdit), Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert get_pdf_toc(str(pdf_path)) == []
    assert panel._tree.topLevelItemCount() == 0


def test_reload_during_add_is_deferred_until_editor_closes(qtbot, tmp_path):
    pdf_path = tmp_path / "defer.pdf"
    make_pdf(pdf_path, pages=4)
    window = create_page_edit_window(qtbot, pdf_path)
    window._open_zoom_view(1)
    panel = window._bookmarks_panel
    panel.set_open(True)
    QApplication.processEvents()

    panel._on_add_current_page()
    # 編集中に外部要因の再読込が来ても、追加中の項目は取り消されない。
    window._reload_bookmarks_tree()
    assert panel._pending_item is not None
    editor = panel._tree.findChild(QLineEdit)
    editor.setText("遅延")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()

    assert [(e.title, e.page) for e in get_pdf_toc(str(pdf_path))] == [("遅延", 2)]
    assert panel._tree.topLevelItemCount() == 1
