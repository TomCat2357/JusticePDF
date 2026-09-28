"""PiiPanel(個人情報検出ドロワー)の単体テスト。

架空のダミーデータのみを使用する。
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMenu, QVBoxLayout, QWidget

from src.pii.entity_types import MANUAL_ENTITY_TYPE
from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData
from src.views.pii_panel import PiiPanel, PiiResultRow

pytestmark = pytest.mark.usefixtures("qapp")


def _row(page_num: int, entity: str, text: str) -> PiiResultRow:
    annot = TextMarkupAnnotData(
        page_num=page_num,
        xref=0,
        quads=((0.0, 0.0, 10.0, 10.0),),
        markup_type=MarkupType.HIGHLIGHT,
        color=(1.0, 0.0, 0.0),
        pii_entity=entity,
        pii_text=text,
    )
    return PiiResultRow(annot=annot, page_num=page_num, entity=entity, text=text, kind="markup")


def test_manual_entity_combo_defaults_to_manual(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert panel.selected_manual_entity() == MANUAL_ENTITY_TYPE


def test_manual_entity_combo_can_be_changed(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    idx = panel._manual_entity_combo.findData("PERSON")
    assert idx >= 0
    panel._manual_entity_combo.setCurrentIndex(idx)
    assert panel.selected_manual_entity() == "PERSON"


def test_keep_existing_checkbox_round_trip(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert panel.keep_existing_checked() is False
    panel.set_keep_existing_checked(True)
    assert panel.keep_existing_checked() is True


def test_result_tree_removed_entity_filter_group():
    """要望4: 「表示するエンティティ種別」グループを削除したこと(関連APIも撤去)。"""
    assert not hasattr(PiiPanel, "enabled_entity_filter")


def _texts(panel, column: int = 0) -> list[str]:
    return [
        panel._result_tree.topLevelItem(i).text(column)
        for i in range(panel._result_tree.topLevelItemCount())
    ]


def test_sort_row_removed():
    """並び替えコンボ/昇降順ボタンは撤去し、列ヘッダクリックに一本化した。"""
    panel = PiiPanel()
    assert not hasattr(panel, "_sort_combo")
    assert not hasattr(panel, "_sort_order_btn")


def test_sort_by_text_orders_results_alphabetically_by_display_text(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [_row(0, "PERSON", "山田太郎"), _row(0, "LOCATION", "青森県"), _row(0, "PERSON", "鈴木一郎")]
    panel.set_results(rows)

    panel.set_sort("text")

    texts = _texts(panel)
    assert texts == sorted(texts)


def test_header_click_on_same_column_reverses_result_order(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [_row(0, "PERSON", "A"), _row(1, "PERSON", "B"), _row(2, "PERSON", "C")]
    panel.set_results(rows)

    assert _texts(panel, 2) == ["p.1", "p.2", "p.3"]  # 既定はページ昇順
    header = panel._result_tree.header()
    assert header.sortIndicatorSection() == 2
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    panel._on_result_header_clicked(2)  # 同じ列 → 降順
    assert _texts(panel, 2) == ["p.3", "p.2", "p.1"]
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder


def test_header_click_switches_sort_field(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [_row(0, "PERSON", "B"), _row(0, "PERSON", "A")]
    panel.set_results(rows)

    panel._on_result_header_clicked(0)  # 語句列
    assert panel._sort_field == "text"
    assert _texts(panel) == ["A", "B"]
    assert panel._result_tree.header().sortIndicatorSection() == 0


def test_ctrl_a_selects_all_results_even_with_window_shortcut(qtbot):
    """結果一覧にフォーカスがあるとき、Ctrl+A はウィンドウのショートカットより優先して全選択する。"""
    window = QWidget()
    qtbot.addWidget(window)
    fired = []
    action = QAction(window)
    action.setShortcut(QKeySequence.StandardKey.SelectAll)
    action.triggered.connect(lambda: fired.append(True))
    window.addAction(action)
    layout = QVBoxLayout(window)
    panel = PiiPanel()
    panel.set_open(True)
    layout.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "A"), _row(1, "PERSON", "B"), _row(2, "PERSON", "C")])
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    panel._result_tree.setFocus()
    qtbot.waitUntil(lambda: panel._result_tree.hasFocus())

    qtbot.keyClick(panel._result_tree, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)

    assert len(panel.selected_results()) == 3
    assert fired == []


def test_delete_key_in_result_tree_requests_remove_selected(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "A")])
    panel._result_tree.topLevelItem(0).setSelected(True)
    captured = []
    panel.remove_selected_requested.connect(lambda: captured.append(True))

    qtbot.keyClick(panel._result_tree, Qt.Key.Key_Delete)

    assert captured == [True]


def test_display_mode_combo_round_trip_and_signal(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert panel.display_mode() == "mark"
    captured = []
    panel.display_mode_changed.connect(captured.append)

    panel.set_display_mode("black")  # プログラムからの設定ではシグナルを出さない
    assert panel.display_mode() == "black"
    assert captured == []

    idx = panel._display_mode_combo.findData("hidden")
    panel._display_mode_combo.setCurrentIndex(idx)
    assert captured == ["hidden"]

    panel.set_display_mode("unknown")
    assert panel.display_mode() == "mark"


def test_context_menu_emits_add_pattern_requested_for_auto_entity(qtbot, monkeypatch):
    """要望B: 自動検出の種別(ENTITY_TYPES)の行では「追加パターンに登録」を発火できる。"""
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(3, "PERSON", "山田太郎")])

    captured = {}
    panel.add_pattern_requested.connect(lambda entity, text: captured.update(entity=entity, text=text))

    chosen_action = {}

    def fake_exec(self, *_args, **_kwargs):
        for action in self.actions():
            if action.text() == "追加パターンに登録":
                chosen_action["action"] = action
                return action
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)

    item = panel._result_tree.topLevelItem(0)
    rect = panel._result_tree.visualItemRect(item)
    panel._on_result_context_menu(rect.center())

    assert captured == {"entity": "PERSON", "text": "山田太郎"}


def test_context_menu_disables_add_pattern_for_manual_entity(qtbot, monkeypatch):
    """手動追加分(種別=MANUAL)には「追加パターンに登録」を出さない(無効化)。"""
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, MANUAL_ENTITY_TYPE, "テスト")])

    seen = {}

    def fake_exec(self, *_args, **_kwargs):
        for action in self.actions():
            if action.text() == "追加パターンに登録":
                seen["enabled"] = action.isEnabled()
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)

    item = panel._result_tree.topLevelItem(0)
    rect = panel._result_tree.visualItemRect(item)
    panel._on_result_context_menu(rect.center())

    assert seen.get("enabled") is False
