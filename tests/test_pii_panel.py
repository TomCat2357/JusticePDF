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


def test_manual_entity_combo_removed(qtbot):
    """手動追加の種別は「手動」固定。種別コンボは撤去した。"""
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "_manual_entity_combo")
    assert not hasattr(panel, "selected_manual_entity")


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


def test_display_mode_combo_removed(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "_display_mode_combo")
    assert not hasattr(panel, "display_mode_changed")


def test_remove_all_button_removed_and_delete_button_renamed(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "_remove_all_btn")
    assert not hasattr(panel, "remove_all_requested")
    assert panel._remove_selected_btn.text() == "削除"


def test_entity_checkboxes_are_three_column_grid_of_nine(qtbot):
    from src.pii.entity_types import ENTITY_TYPES, get_entity_type_name_ja

    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert list(panel._entity_checks) == [*ENTITY_TYPES, "MANUAL"]
    assert len(panel._entity_checks) == 9
    for entity, check in panel._entity_checks.items():
        assert check.text() == get_entity_type_name_ja(entity)
        assert check.isChecked() is True
    grid = panel._entity_checks["PERSON"].parentWidget().layout()
    columns = {grid.getItemPosition(i)[1] for i in range(grid.count())}
    assert columns == {0, 1, 2}


def test_entity_checkbox_emits_signal_and_set_visibility_is_silent(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    captured = []
    panel.entity_visibility_changed.connect(lambda e, c: captured.append((e, c)))

    panel._entity_checks["LOCATION"].setChecked(False)
    assert captured == [("LOCATION", False)]

    captured.clear()
    panel.set_entity_visibility({"PERSON": False, "MANUAL": False})  # プログラムからはシグナル無し
    assert captured == []
    assert panel.entity_visibility()["PERSON"] is False
    assert panel.entity_visibility()["MANUAL"] is False
    assert panel.entity_visibility()["LOCATION"] is True  # 未指定の種別はオン


def test_manual_tools_disabled_helper(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_manual_tools_enabled(False)
    assert not panel._mask_markup_btn.isEnabled()
    assert not panel._mask_rect_btn.isEnabled()
    assert not panel._mask_ellipse_btn.isEnabled()
    panel.set_manual_tools_enabled(True)
    assert panel._mask_rect_btn.isEnabled()


def test_manual_tool_buttons_do_not_take_focus_and_have_no_hint_label(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    for btn in (panel._mask_markup_btn, panel._mask_rect_btn, panel._mask_ellipse_btn):
        assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus
    # 操作の説明文はボタンのツールチップに集約し、パネル上の常時表示の説明は置かない。
    assert not hasattr(panel, "_manual_hint_label")
    assert "連続モード" in panel._mask_markup_btn.toolTip()


def test_delete_settings_export_buttons_share_one_row(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_open(True)
    panel.resize(PiiPanel.DRAWER_WIDTH, 600)
    panel.show()
    qtbot.waitExposed(panel)
    buttons = (panel._remove_selected_btn, panel._settings_btn, panel._export_btn)
    tops = {btn.mapTo(panel, btn.rect().topLeft()).y() for btn in buttons}
    assert len(tops) == 1  # 同じ行(縦位置が同じ)
    xs = [btn.mapTo(panel, btn.rect().topLeft()).x() for btn in buttons]
    assert xs == sorted(xs) and len(set(xs)) == 3  # 左から 削除 / 設定 / エクスポート


def test_select_result_activates_matching_row_and_scrolls(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.resize(340, 300)
    panel.set_open(True)
    panel.show()
    qtbot.waitExposed(panel)
    rows = [_row(i, "PERSON", f"人物{i}") for i in range(60)]
    panel.set_results(rows)
    clicked = []
    panel.result_activated.connect(clicked.append)

    target = rows[45].annot
    assert panel.select_result(target) is True

    tree = panel._result_tree
    current = tree.currentItem()
    assert current.data(0, Qt.ItemDataRole.UserRole).annot is target
    assert tree.selectedItems() == [current]
    assert tree.visualItemRect(current).intersects(tree.viewport().rect())  # スクロールで見える
    assert clicked == []  # 一覧側からのシグナルは出さない(往復防止)

    # 一覧に無い注釈は選択を変えない。
    other = _row(999, "PERSON", "無い").annot
    assert panel.select_result(other) is False
    assert tree.selectedItems() == [current]


def test_mask_style_round_trip_and_signals(qtbot, monkeypatch):
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog

    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert panel.mask_transparency() == 70
    assert panel.mask_color() == (0.0, 0.0, 0.0)

    colors, transparencies = [], []
    panel.mask_color_changed.connect(colors.append)
    panel.mask_transparency_changed.connect(lambda v, c: transparencies.append((v, c)))

    panel.set_mask_style((1.0, 0.0, 0.0), 30)  # プログラムからの設定ではシグナルを出さない
    assert colors == [] and transparencies == []
    assert panel.mask_transparency() == 30
    assert panel._transparency_label.text() == "30%"
    assert "rgb(255, 0, 0)" in panel._color_btn.styleSheet()

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor(0, 0, 255)))
    panel._color_btn.click()
    assert colors == [(0.0, 0.0, 1.0)]
    assert panel.mask_color() == (0.0, 0.0, 1.0)
    assert "rgb(0, 0, 255)" in panel._color_btn.styleSheet()

    # ドラッグ中は確定=False、離したときに確定=True。
    slider = panel._transparency_slider
    slider.setSliderDown(True)
    slider.setValue(10)
    assert transparencies[-1] == (10, False)
    slider.setSliderDown(False)
    assert transparencies[-1] == (10, True)
    # キー操作などドラッグでない変更はその場で確定。
    transparencies.clear()
    slider.setValue(50)
    assert transparencies == [(50, True)]


def test_result_context_menu_has_copy_first(qtbot, monkeypatch):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "山田太郎")])

    seen = {}

    def fake_exec(self, *_args, **_kwargs):
        seen["texts"] = [a.text() for a in self.actions() if a.text()]
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    item = panel._result_tree.topLevelItem(0)
    panel._on_result_context_menu(panel._result_tree.visualItemRect(item).center())

    assert seen["texts"][0] == "コピー"
    assert seen["texts"][1:] == [
        "検出語に追加",
        "手動扱いで検出(検出語に登録しない)",
        "除外パターンに追加",
        "検出結果から削除(除外に登録しない)",
    ]
    assert "同じ語句をすべて削除" not in seen["texts"]
    # 旧メニュー項目は撤去済み。
    assert "追加パターンに登録" not in seen["texts"]
    assert "除外語句に登録" not in seen["texts"]


def _clipboard_text() -> str:
    from PyQt6.QtGui import QGuiApplication

    return QGuiApplication.clipboard().text()


def test_ctrl_c_copies_selected_rows_as_tsv(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results(
        [_row(0, "PERSON", "山田太郎"), _row(1, "LOCATION", "青森県"), _row(2, "PERSON", "鈴木一郎")]
    )
    panel._result_tree.topLevelItem(0).setSelected(True)
    panel._result_tree.topLevelItem(2).setSelected(True)

    qtbot.keyClick(panel._result_tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert _clipboard_text() == "語句\t種別\tページ\n山田太郎\t人名\t1\n鈴木一郎\t人名\t3"


def test_ctrl_c_without_selection_copies_all_rows_in_visual_order(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(2, "PERSON", "C"), _row(0, "LOCATION", "A"), _row(1, "PERSON", "B")])
    panel.set_sort("page", ascending=False)  # 画面上の並び(ページ降順)でコピーされる

    qtbot.keyClick(panel._result_tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    lines = _clipboard_text().split("\n")
    assert lines[0] == "語句\t種別\tページ"
    assert lines[1:] == ["C\t人名\t3", "B\t人名\t2", "A\t場所\t1"]


def test_copy_replaces_tabs_and_newlines_in_text(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "山田\t太郎\r\n次郎\n三郎")])

    panel.copy_results_to_clipboard()

    lines = _clipboard_text().split("\n")
    assert len(lines) == 2  # 見出し+1行(改行がセル内に残らない)
    assert lines[1] == "山田 太郎  次郎 三郎\t人名\t1"


def test_ctrl_c_wins_over_window_shortcut(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    fired = []
    action = QAction(window)
    action.setShortcut(QKeySequence.StandardKey.Copy)
    action.triggered.connect(lambda: fired.append(True))
    window.addAction(action)
    layout = QVBoxLayout(window)
    panel = PiiPanel()
    panel.set_open(True)
    layout.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "A")])
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    panel._result_tree.setFocus()
    qtbot.waitUntil(lambda: panel._result_tree.hasFocus())

    qtbot.keyClick(panel._result_tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert fired == []
    assert _clipboard_text().split("\n")[1] == "A\t人名\t1"


def _find_action(menu, text):
    """メニュー(サブメニュー含む)から表示名の一致する QAction を探す。"""
    for action in menu.actions():
        if action.text() == text:
            return action
        if action.menu() is not None:
            found = _find_action(action.menu(), text)
            if found is not None:
                return found
    return None


def _open_context_menu(panel, monkeypatch, pick: str | None):
    """右クリックメニューを開き、表示名 ``pick`` の項目が選ばれたことにする。

    戻り値は {"menu": QMenu} (開いたメニュー。項目の有効/無効の確認用)。
    """
    opened = {}

    def fake_exec(self, *_args, **_kwargs):
        opened["menu"] = self
        if pick is None:
            return None
        return _find_action(self, pick)

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    item = panel._result_tree.topLevelItem(0)
    panel._on_result_context_menu(panel._result_tree.visualItemRect(item).center())
    return opened


def test_context_menu_detect_word_submenu_lists_eight_entities_and_emits(qtbot, monkeypatch):
    from src.pii.entity_types import ENTITY_TYPES, get_entity_type_name_ja

    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(3, "PERSON", "山田太郎")])
    captured = []
    panel.add_detect_word_requested.connect(lambda entity, text: captured.append((entity, text)))

    opened = _open_context_menu(panel, monkeypatch, "場所")

    detect_action = _find_action(opened["menu"], "検出語に追加")
    assert detect_action.isEnabled() is True
    submenu_texts = [a.text() for a in detect_action.menu().actions()]
    assert submenu_texts == [get_entity_type_name_ja(e) for e in ENTITY_TYPES]
    assert len(submenu_texts) == 8 and "手動" not in submenu_texts
    assert captured == [("LOCATION", "山田太郎")]


def test_context_menu_detect_word_is_enabled_for_manual_rows(qtbot, monkeypatch):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, MANUAL_ENTITY_TYPE, "テスト")])
    captured = []
    panel.add_detect_word_requested.connect(lambda entity, text: captured.append((entity, text)))

    opened = _open_context_menu(panel, monkeypatch, "人名")

    assert _find_action(opened["menu"], "検出語に追加").isEnabled() is True
    assert captured == [("PERSON", "テスト")]


def test_context_menu_exclude_word_emits_text(qtbot, monkeypatch):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "山田太郎")])
    captured = []
    panel.add_exclude_word_requested.connect(captured.append)

    _open_context_menu(panel, monkeypatch, "除外パターンに追加")

    assert captured == ["山田太郎"]


def test_context_menu_word_actions_disabled_when_no_text(qtbot, monkeypatch):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "")])

    opened = _open_context_menu(panel, monkeypatch, None)

    assert _find_action(opened["menu"], "検出語に追加").isEnabled() is False
    assert _find_action(opened["menu"], "除外パターンに追加").isEnabled() is False


def test_scope_choice_dialog_labels_and_scopes(qtbot):
    from src.views.pii_panel import ScopeChoiceDialog

    labels = ("全ページで検出", "このページだけ検出", "検出しない")
    dialog = ScopeChoiceDialog("検出語に追加", "説明文", "山田太郎", labels)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "検出語に追加"
    assert dialog._text_edit.isReadOnly() is True and dialog._text_edit.text() == "山田太郎"
    assert dialog._message_label.text() == "説明文"
    assert [dialog._all_btn.text(), dialog._page_btn.text(), dialog._none_btn.text()] == list(labels)
    # 何も押さずに閉じた場合は「しない」扱い。
    assert dialog.scope() == ScopeChoiceDialog.SCOPE_NONE

    dialog._page_btn.click()
    assert dialog.scope() == ScopeChoiceDialog.SCOPE_PAGE
    dialog2 = ScopeChoiceDialog("t", "m", "w", labels)
    qtbot.addWidget(dialog2)
    dialog2._all_btn.click()
    assert dialog2.scope() == ScopeChoiceDialog.SCOPE_ALL


def test_old_context_menu_signals_removed(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "add_pattern_requested")
    assert not hasattr(panel, "add_exclusion_requested")


def test_result_row_display_text_is_always_a_single_line():
    from src.views.pii_panel import DISPLAY_TEXT_MAX_CHARS, PiiResultRow

    def row(text):
        return PiiResultRow(annot=None, page_num=0, entity="MANUAL", text=text, kind="markup")

    assert row("申）\n ").display_text == "申）"
    assert row("（答申\n）　").display_text == "（答申 ）"
    assert row("a\r\n\t b").display_text == "a b"
    assert row("\n 　").display_text == "(テキストなし)"
    long = row("あ" * 200).display_text
    assert len(long) == DISPLAY_TEXT_MAX_CHARS and long.endswith("…")
    assert "\n" not in row("x\ny").display_text
    # 元のデータ(右クリックの除外/検出登録に使う text)は変えない。
    assert row("申）\n").text == "申）\n"
