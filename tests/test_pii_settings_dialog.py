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
    assert titles == ["検出エンジン", "除外・追加パターン", "重複除去", "OCR"]
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

    dialog._pattern_list.setCurrentRow(0)
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


def test_excluded_words_editor_add_and_remove(qtbot):
    settings = PiiSettings()
    settings.excluded_words = ["既存語"]
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert [dialog._excluded_words_list.item(i).text() for i in range(dialog._excluded_words_list.count())] == ["既存語"]

    row = dialog._excluded_words_list.parentWidget().layout()
    # 追加/削除行(最初の QLineEdit と、その隣の「追加」「削除」ボタン)を探す。
    from PyQt6.QtWidgets import QLineEdit, QPushButton

    edits = [w for w in dialog.findChildren(QLineEdit) if w.placeholderText() == "除外語を入力"]
    assert len(edits) == 1
    edits[0].setText("追加語")
    add_btn = [b for b in dialog.findChildren(QPushButton) if b.text() == "追加"][0]
    add_btn.click()  # 最初の「追加」ボタン=除外語の行
    result = dialog.result_settings()
    assert result.excluded_words == ["既存語", "追加語"]
    # 元のオブジェクトは変更されない(ダイアログはコピー上で編集する)。
    assert settings.excluded_words == ["既存語"]

    dialog._excluded_words_list.setCurrentRow(0)
    remove_btn = [b for b in dialog.findChildren(QPushButton) if b.text() == "削除"][0]
    remove_btn.click()
    assert dialog.result_settings().excluded_words == ["追加語"]
