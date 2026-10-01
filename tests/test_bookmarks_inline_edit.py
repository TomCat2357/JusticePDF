from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLineEdit, QSpinBox, QStyle, QStyleOptionFrame

from src.utils.pdf_utils import TocEntry
from src.views.bookmarks_panel import BookmarksPanel

QSS_PATH = Path(__file__).resolve().parent.parent / "src" / "views" / "style.qss"


def _entries():
    return [TocEntry(1, "Chapter 1", 1), TocEntry(1, "Chapter 2", 5)]


@pytest.fixture
def real_qss(qapp):
    qapp.setStyleSheet(QSS_PATH.read_text(encoding="utf-8"))
    yield
    qapp.setStyleSheet("")


def _shown_panel(qtbot):
    panel = BookmarksPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.load_entries(_entries())
    panel.show()
    qtbot.waitExposed(panel)
    return panel


def test_title_editor_not_squashed_with_global_qss(qtbot, real_qss):
    panel = _shown_panel(qtbot)
    item = panel._tree.topLevelItem(0)
    panel._tree.editItem(item, 0)
    editor = panel._tree.findChild(QLineEdit)
    assert editor is not None
    # QSS の padding/border を差し引いた実テキスト領域の高さで判定する。
    opt = QStyleOptionFrame()
    editor.initStyleOption(opt)
    text_rect = editor.style().subElementRect(
        QStyle.SubElement.SE_LineEditContents, opt, editor
    )
    assert text_rect.height() >= editor.fontMetrics().height()


def test_page_editor_is_bounded_spinbox_and_commits(qtbot):
    panel = _shown_panel(qtbot)
    panel.set_page_count_provider(lambda: 10)
    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append((e, d)))
    item = panel._tree.topLevelItem(0)
    panel._on_item_double_clicked(item, 1)
    spin = panel._tree.findChild(QSpinBox)
    assert spin is not None
    assert (spin.minimum(), spin.maximum()) == (1, 10)
    spin.setValue(7)
    panel._tree.commitData(spin)
    assert captured[-1][1] == "しおりページ変更"
    assert captured[-1][0][0].page == 7
    assert item.text(1) == "7"


def test_page_text_clamped_and_invalid_restored(qtbot):
    panel = _shown_panel(qtbot)
    panel.set_page_count_provider(lambda: 10)
    item = panel._tree.topLevelItem(0)
    item.setText(1, "99")
    assert panel._tree_to_entries()[0].page == 10
    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append(d))
    item.setText(1, "abc")
    assert item.text(1) == "10"
    assert captured == []


def test_add_starts_inline_edit(qtbot):
    panel = _shown_panel(qtbot)
    panel.set_current_page_provider(lambda: 3)
    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append(d))
    panel._add_btn.click()
    assert captured == ["しおり追加"]
    assert panel._tree.findChild(QLineEdit) is not None
    assert panel._tree.currentItem().text(0) == "(無題)"


def test_add_noop_when_provider_returns_none_or_unavailable(qtbot):
    panel = _shown_panel(qtbot)
    panel.set_current_page_provider(lambda: None)
    panel._on_add()
    assert panel._tree.topLevelItemCount() == 2
    panel.set_current_page_provider(lambda: 2)
    panel.set_current_page_available(False)
    assert not panel._add_btn.isEnabled()
    panel.set_read_only(True)
    panel.set_current_page_available(True)
    assert not panel._add_btn.isEnabled()  # 閲覧専用が優先
    panel.set_read_only(False)
    assert panel._add_btn.isEnabled()


def test_f2_starts_title_edit(qtbot):
    panel = _shown_panel(qtbot)
    tree = panel._tree
    tree.setFocus()
    tree.setCurrentItem(tree.topLevelItem(1))
    QTest.keyClick(tree, Qt.Key.Key_F2)
    assert tree.findChild(QLineEdit) is not None


def test_f2_ignored_when_read_only(qtbot):
    panel = _shown_panel(qtbot)
    panel.set_read_only(True)
    tree = panel._tree
    tree.setCurrentItem(tree.topLevelItem(0))
    QTest.keyClick(tree, Qt.Key.Key_F2)
    assert tree.findChild(QLineEdit) is None


def test_delete_key_deletes_current(qtbot):
    panel = _shown_panel(qtbot)
    tree = panel._tree
    captured = []
    panel.bookmarks_changed.connect(lambda e, d: captured.append(d))
    tree.setCurrentItem(tree.topLevelItem(1))
    QTest.keyClick(tree, Qt.Key.Key_Delete)
    assert captured == ["しおり削除"]
    assert tree.topLevelItemCount() == 1
