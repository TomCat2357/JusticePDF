"""PiiPanel(個人情報検出ドロワー)の単体テスト。

架空のダミーデータのみを使用する。
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMenu

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


def test_sort_by_text_orders_results_alphabetically_by_display_text(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [_row(0, "PERSON", "山田太郎"), _row(0, "LOCATION", "青森県"), _row(0, "PERSON", "鈴木一郎")]
    panel.set_results(rows)

    idx = panel._sort_combo.findData("text")
    panel._sort_combo.setCurrentIndex(idx)

    texts = [
        panel._result_tree.topLevelItem(i).text(0)
        for i in range(panel._result_tree.topLevelItemCount())
    ]
    assert texts == sorted(texts)


def test_sort_order_toggle_reverses_result_order(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [_row(0, "PERSON", "A"), _row(1, "PERSON", "B"), _row(2, "PERSON", "C")]
    panel.set_results(rows)

    idx = panel._sort_combo.findData("page")
    panel._sort_combo.setCurrentIndex(idx)
    ascending_pages = [
        panel._result_tree.topLevelItem(i).text(2)
        for i in range(panel._result_tree.topLevelItemCount())
    ]
    assert ascending_pages == ["p.1", "p.2", "p.3"]

    panel._sort_order_btn.setChecked(True)
    descending_pages = [
        panel._result_tree.topLevelItem(i).text(2)
        for i in range(panel._result_tree.topLevelItemCount())
    ]
    assert descending_pages == list(reversed(ascending_pages))


def test_header_click_switches_sort_field(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [_row(0, "PERSON", "B"), _row(0, "PERSON", "A")]
    panel.set_results(rows)

    panel._on_result_header_clicked(0)  # 語句列
    assert panel._sort_field == "text"
    texts = [
        panel._result_tree.topLevelItem(i).text(0)
        for i in range(panel._result_tree.topLevelItemCount())
    ]
    assert texts == ["A", "B"]


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
