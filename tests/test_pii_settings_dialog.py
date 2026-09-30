"""PiiSettingsDialog の基本操作テスト。"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QTabWidget

from src.pii.settings import PiiSettings
from src.views.pii_settings_dialog import PiiSettingsDialog

pytestmark = pytest.mark.usefixtures("qapp")


def test_entities_tab_removed_and_entity_settings_preserved(qtbot):
    """「検出対象・色」タブは廃止(種別チェックボックスと色はパネルへ移動)。

    ダイアログを確定しても、パネル側で設定した検出対象・色・透明度は変わらない。
    """
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    settings.manual_visible = False
    settings.mask_color = (0.1, 0.2, 0.3)
    settings.mask_transparency = 15
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    tabs = dialog.findChild(QTabWidget)
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert "検出対象・色" not in titles
    assert titles == ["検出エンジン", "除外・検出パターン", "重複除去", "OCR"]
    assert not hasattr(dialog, "_entity_checks")
    assert not hasattr(dialog, "_entity_color_btns")

    result = dialog.result_settings()
    assert result.enabled_entities["PERSON"] is False
    assert result.enabled_entities["LOCATION"] is True
    assert result.manual_visible is False
    assert result.mask_color == (0.1, 0.2, 0.3)
    assert result.mask_transparency == 15


def test_add_and_remove_additional_pattern(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    dialog._pattern_regex_edit.setText(r"社員番号[0-9]{4}")
    dialog._on_add_pattern()
    result = dialog.result_settings()
    assert (dialog._pattern_entity_combo.currentData(), r"社員番号[0-9]{4}") in result.additional_patterns

    dialog._pattern_table.selectRow(0)
    dialog._on_remove_pattern()
    result_after_remove = dialog.result_settings()
    assert result_after_remove.additional_patterns == []


def test_engines_tab_lists_always_available_engines_checked_by_default(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._engine_checks["regex"].isChecked() is True
    assert dialog._engine_checks["regex"].isEnabled() is True
    assert dialog._engine_checks["datetime"].isChecked() is True


def test_engines_tab_select_all_only_affects_available_engines(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    dialog._set_all_engine_checks(False)
    dialog._set_all_engine_checks(True)
    for key, checkbox in dialog._engine_checks.items():
        assert checkbox.isChecked() == checkbox.isEnabled(), key

    result = dialog.result_settings()
    for key, checkbox in dialog._engine_checks.items():
        assert result.enabled_engines[key] == checkbox.isChecked()


def test_toggling_engine_checkbox_is_reflected_in_result(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    dialog._engine_checks["regex"].setChecked(False)
    result = dialog.result_settings()
    assert result.enabled_engines["regex"] is False
    # 元のオブジェクトは変更されない(ダイアログはコピー上で編集する)。
    assert settings.enabled_engines["regex"] is True


def test_dedupe_options_round_trip(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    dialog._dedupe_enabled_check.setChecked(False)
    idx = dialog._dedupe_overlap_combo.findData("exact")
    dialog._dedupe_overlap_combo.setCurrentIndex(idx)

    result = dialog.result_settings()
    assert result.dedupe_enabled is False
    assert result.dedupe_overlap == "exact"


def _texts(table, col):
    return [table.item(r, col).text() for r in range(table.rowCount())]


def _select(table, rows):
    from PyQt6.QtCore import QItemSelectionModel

    table.clearSelection()
    model = table.selectionModel()
    for r in rows:
        model.select(
            table.model().index(r, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def test_excluded_words_section_removed(qtbot):
    from PyQt6.QtWidgets import QLabel

    dialog = PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog)
    assert not hasattr(dialog, "_excluded_words_list")
    labels = " ".join(lb.text() for lb in dialog.findChildren(QLabel))
    assert "除外語" not in labels
    assert "検出パターン" in labels and "追加検出パターン" not in labels


def test_exclusion_table_columns_add_and_persist_timestamp(qtbot):
    settings = PiiSettings()
    settings.text_exclusions_regex = ["旧パターン"]  # 追加日時なし(旧形式)
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    assert [table.horizontalHeaderItem(i).text() for i in range(2)] == ["パターン", "追加日時"]
    assert _texts(table, 1) == [""]  # 日時なしは空欄

    dialog._exclusion_edit.setText("新パターン")
    dialog._on_add_exclusion()
    assert _texts(table, 0) == ["旧パターン", "新パターン"]
    assert len(_texts(table, 1)[1]) == len("2026-09-30 11:23")
    result = dialog.result_settings()
    assert result.text_exclusions_regex == ["旧パターン", "新パターン"]
    assert "新パターン" in result.text_exclusions_added_at
    assert "旧パターン" not in result.text_exclusions_added_at
    # 元のオブジェクトは変更されない
    assert settings.text_exclusions_regex == ["旧パターン"]


def test_pattern_table_columns_and_timestamp(qtbot):
    from src.pii.settings import pattern_key

    settings = PiiSettings()
    settings.additional_patterns = [("PERSON", "山田")]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._pattern_table
    assert [table.horizontalHeaderItem(i).text() for i in range(3)] == ["種類", "パターン", "追加日時"]
    assert _texts(table, 1) == ["山田"] and _texts(table, 2) == [""]
    dialog._pattern_regex_edit.setText("鈴木")
    dialog._on_add_pattern()
    assert _texts(table, 1) == ["山田", "鈴木"]
    assert _texts(table, 2)[1] != ""
    result = dialog.result_settings()
    key = pattern_key(dialog._pattern_entity_combo.currentData(), "鈴木")
    assert key in result.additional_patterns_added_at


def test_tables_are_read_only_row_multi_select(qtbot):
    from PyQt6.QtWidgets import QAbstractItemView

    dialog = PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog)
    for table in (dialog._exclusion_table, dialog._pattern_table):
        assert table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
        assert table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert table.isSortingEnabled()


def test_exclusion_table_multi_delete_button_and_delete_key(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QPushButton

    settings = PiiSettings()
    settings.text_exclusions_regex = ["a", "b", "c", "d", "e"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    _select(table, [0, 2, 4])
    remove_btn = [b for b in dialog.findChildren(QPushButton) if b.text() == "削除"][0]
    remove_btn.click()
    assert _texts(table, 0) == ["b", "d"]
    assert dialog.result_settings().text_exclusions_regex == ["b", "d"]

    _select(table, [0, 1])
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    assert table.rowCount() == 0
    assert dialog.result_settings().text_exclusions_regex == []


def test_pattern_table_multi_delete_button_and_delete_key(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QPushButton

    settings = PiiSettings()
    settings.additional_patterns = [
        ("PERSON", "a"), ("LOCATION", "b"), ("PERSON", "c"), ("PERSON", "d"),
    ]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._pattern_table
    _select(table, [0, 1, 3])
    remove_btn = [b for b in dialog.findChildren(QPushButton) if b.text() == "削除"][1]
    remove_btn.click()
    assert _texts(table, 1) == ["c"]
    assert dialog.result_settings().additional_patterns == [("PERSON", "c")]
    _select(table, [0])
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    assert dialog.result_settings().additional_patterns == []


def test_ctrl_a_selects_all_rows(qtbot):
    from PyQt6.QtCore import Qt

    settings = PiiSettings()
    settings.text_exclusions_regex = ["a", "b", "c"]
    settings.additional_patterns = [("PERSON", "x"), ("PERSON", "y")]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    for table, n in ((dialog._exclusion_table, 3), (dialog._pattern_table, 2)):
        table.clearSelection()
        table.setFocus()
        qtbot.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert len(table.selectionModel().selectedRows()) == n


def test_sort_then_delete_removes_the_right_entry(qtbot):
    from PyQt6.QtCore import Qt

    settings = PiiSettings()
    settings.text_exclusions_regex = ["b", "c", "a"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert _texts(table, 0) == ["a", "b", "c"]
    table.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert _texts(table, 0) == ["c", "b", "a"]
    _select(table, [0])  # 並べ替え後の先頭 = "c"
    dialog._on_remove_exclusion()
    assert dialog.result_settings().text_exclusions_regex == ["b", "a"]


def test_sort_by_added_at_uses_datetime_not_display_string(qtbot):
    from PyQt6.QtCore import Qt

    settings = PiiSettings()
    settings.text_exclusions_regex = ["late", "none", "early", "mid"]
    settings.text_exclusions_added_at = {
        "late": "2026-09-30T11:23:45",
        "early": "2025-01-02T03:04:05",
        "mid": "2026-09-30T09:00:00",
    }
    settings.additional_patterns = [("PERSON", "p1"), ("PERSON", "p2"), ("PERSON", "p3")]
    settings.additional_patterns_added_at = {
        "PERSON\tp1": "2026-02-01T00:00:00",
        "PERSON\tp3": "2026-01-01T00:00:00",
    }
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert _texts(table, 0) == ["none", "early", "mid", "late"]  # 日時なしは最古扱い
    table.sortItems(1, Qt.SortOrder.DescendingOrder)
    assert _texts(table, 0) == ["late", "mid", "early", "none"]
    ptable = dialog._pattern_table
    ptable.sortItems(2, Qt.SortOrder.AscendingOrder)
    assert _texts(ptable, 1) == ["p2", "p3", "p1"]


def test_header_click_toggles_sort_order(qtbot):
    from PyQt6.QtCore import QPoint, Qt

    settings = PiiSettings()
    settings.text_exclusions_regex = ["b", "a", "c"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.show()
    table = dialog._exclusion_table
    header = table.horizontalHeader()
    pos = QPoint(10, header.height() // 2)
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    first = _texts(table, 0)
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    second = _texts(table, 0)
    assert first == ["a", "b", "c"]
    assert second == ["c", "b", "a"]


def test_exclusion_select_fills_edit_and_update_replaces_in_place(qtbot):
    settings = PiiSettings()
    settings.text_exclusions_regex = ["a", "b", "c"]
    settings.text_exclusions_added_at = {"b": "2026-01-01T00:00:00"}
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    assert not dialog._update_exclusion_btn.isEnabled()

    _select(table, [1])
    assert dialog._exclusion_edit.text() == "b"
    assert dialog._update_exclusion_btn.isEnabled()

    dialog._exclusion_edit.setText("B2")
    dialog._update_exclusion_btn.click()
    assert _texts(table, 0) == ["a", "B2", "c"]  # 位置を維持・重複追加なし
    assert dialog._exclusion_edit.text() == ""
    assert not dialog._update_exclusion_btn.isEnabled()
    result = dialog.result_settings()
    assert result.text_exclusions_regex == ["a", "B2", "c"]
    assert "b" not in result.text_exclusions_added_at
    assert result.text_exclusions_added_at["B2"] > "2026-01-01T00:00:00"
    assert settings.text_exclusions_regex == ["a", "b", "c"]  # 元は不変


def test_exclusion_update_disabled_for_multi_or_none_and_add_stays_new(qtbot):
    settings = PiiSettings()
    settings.text_exclusions_regex = ["a", "b"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    _select(table, [0, 1])
    assert not dialog._update_exclusion_btn.isEnabled()
    table.clearSelection()
    assert not dialog._update_exclusion_btn.isEnabled()

    _select(table, [0])
    dialog._exclusion_edit.setText("new")
    dialog._on_add_exclusion()  # 選択中でも「追加」は新規追加
    assert dialog.result_settings().text_exclusions_regex == ["a", "b", "new"]


def test_exclusion_update_duplicate_or_empty_is_ignored(qtbot):
    settings = PiiSettings()
    settings.text_exclusions_regex = ["a", "b"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    _select(table, [0])
    dialog._exclusion_edit.setText("b")  # 他の項目と重複
    dialog._on_update_exclusion()
    dialog._exclusion_edit.setText("  ")
    dialog._on_update_exclusion()
    assert _texts(table, 0) == ["a", "b"]
    assert dialog.result_settings().text_exclusions_regex == ["a", "b"]


def test_exclusion_update_after_sort_replaces_the_right_entry(qtbot):
    from PyQt6.QtCore import Qt

    settings = PiiSettings()
    settings.text_exclusions_regex = ["b", "c", "a"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._exclusion_table
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    _select(table, [0])  # "a"
    assert dialog._exclusion_edit.text() == "a"
    dialog._exclusion_edit.setText("a2")
    dialog._on_update_exclusion()
    assert dialog.result_settings().text_exclusions_regex == ["b", "c", "a2"]
    assert sorted(_texts(table, 0)) == ["a2", "b", "c"]


def test_pattern_select_fills_type_and_regex_and_update_replaces(qtbot):
    from src.pii.settings import pattern_key

    settings = PiiSettings()
    settings.additional_patterns = [("PERSON", "x"), ("LOCATION", "y"), ("PERSON", "z")]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._pattern_table
    _select(table, [1])
    assert dialog._pattern_entity_combo.currentData() == "LOCATION"
    assert dialog._pattern_regex_edit.text() == "y"
    assert dialog._update_pattern_btn.isEnabled()

    dialog._pattern_entity_combo.setCurrentIndex(dialog._pattern_entity_combo.findData("PHONE_NUMBER"))
    dialog._pattern_regex_edit.setText("y2")
    dialog._update_pattern_btn.click()
    result = dialog.result_settings()
    assert result.additional_patterns == [("PERSON", "x"), ("PHONE_NUMBER", "y2"), ("PERSON", "z")]
    assert _texts(table, 1) == ["x", "y2", "z"]
    assert pattern_key("LOCATION", "y") not in result.additional_patterns_added_at
    assert pattern_key("PHONE_NUMBER", "y2") in result.additional_patterns_added_at
    assert not dialog._update_pattern_btn.isEnabled()


def test_pattern_update_type_only_change_and_duplicate(qtbot):
    settings = PiiSettings()
    settings.additional_patterns = [("PERSON", "x"), ("LOCATION", "x")]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    table = dialog._pattern_table
    _select(table, [0])
    # 種別だけ変えて他項目と同じ (LOCATION, x) になる -> 重複なので無視
    dialog._pattern_entity_combo.setCurrentIndex(dialog._pattern_entity_combo.findData("LOCATION"))
    dialog._on_update_pattern()
    assert dialog.result_settings().additional_patterns == [("PERSON", "x"), ("LOCATION", "x")]
    # 種別だけの変更は更新できる
    dialog._pattern_entity_combo.setCurrentIndex(dialog._pattern_entity_combo.findData("PHONE_NUMBER"))
    dialog._on_update_pattern()
    assert dialog.result_settings().additional_patterns == [("PHONE_NUMBER", "x"), ("LOCATION", "x")]


def test_settings_replace_methods_keep_position():
    s = PiiSettings()
    s.add_exclusion("a", "2026-01-01T00:00:00")
    s.add_exclusion("b")
    assert s.replace_exclusion("a", "c", "2026-02-02T00:00:00")
    assert s.text_exclusions_regex == ["c", "b"]
    assert s.text_exclusions_added_at["c"] == "2026-02-02T00:00:00"
    assert "a" not in s.text_exclusions_added_at
    assert not s.replace_exclusion("c", "b") and not s.replace_exclusion("c", "c")
    assert not s.replace_exclusion("zzz", "q") and not s.replace_exclusion("c", "")

    s.add_additional_pattern("PERSON", "x")
    s.add_additional_pattern("PERSON", "y")
    assert s.replace_additional_pattern(("PERSON", "x"), ("LOCATION", "x2"))
    assert s.additional_patterns == [("LOCATION", "x2"), ("PERSON", "y")]
    assert not s.replace_additional_pattern(("LOCATION", "x2"), ("PERSON", "y"))
    assert not s.replace_additional_pattern(("NOPE", "q"), ("PERSON", "r"))
