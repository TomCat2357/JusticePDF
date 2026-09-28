"""PiiSettingsDialog の基本操作テスト。"""
from __future__ import annotations

import pytest

from src.pii.settings import PiiSettings
from src.views.pii_settings_dialog import PiiSettingsDialog

pytestmark = pytest.mark.usefixtures("qapp")


def test_dialog_reflects_initial_settings(qtbot):
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._entity_checks["PERSON"].isChecked() is False
    assert dialog._entity_checks["LOCATION"].isChecked() is True


def test_toggling_entity_checkbox_is_reflected_in_result(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    dialog._entity_checks["PERSON"].setChecked(False)
    result = dialog.result_settings()
    assert result.enabled_entities["PERSON"] is False
    # 元のオブジェクトは変更されない(ダイアログはコピー上で編集する)
    assert settings.enabled_entities["PERSON"] is True


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


def test_entities_tab_select_all_and_deselect_all(qtbot):
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    dialog._set_all_entity_checks(True)
    assert all(box.isChecked() for box in dialog._entity_checks.values())

    dialog._set_all_entity_checks(False)
    assert all(not box.isChecked() for box in dialog._entity_checks.values())


def test_manual_entity_has_color_button_but_no_checkbox(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert "MANUAL" not in dialog._entity_checks
    assert "MANUAL" in dialog._entity_color_btns


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


def test_sudachi_dict_and_split_mode_round_trip(qtbot):
    settings = PiiSettings()
    settings.sudachi_dict_type = "small"
    settings.sudachi_split_mode = "A"
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._sudachi_dict_combo.currentData() == "small"
    assert dialog._sudachi_split_combo.currentData() == "A"

    idx = dialog._sudachi_dict_combo.findData("core")
    dialog._sudachi_dict_combo.setCurrentIndex(idx)
    idx = dialog._sudachi_split_combo.findData("C")
    dialog._sudachi_split_combo.setCurrentIndex(idx)

    result = dialog.result_settings()
    assert result.sudachi_dict_type == "core"
    assert result.sudachi_split_mode == "C"


def test_sudachi_full_dict_marked_uninstalled_when_unavailable(qtbot, monkeypatch):
    import src.views.pii_settings_dialog as dialog_module

    monkeypatch.setattr(
        dialog_module, "sudachi_dict_available", lambda dt: dt != "full"
    )
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    idx = dialog._sudachi_dict_combo.findData("full")
    assert "未インストール" in dialog._sudachi_dict_combo.itemText(idx)


def test_entity_overlap_mode_round_trip(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._entity_overlap_any_radio.isChecked() is True
    dialog._entity_overlap_same_radio.setChecked(True)

    result = dialog.result_settings()
    assert result.entity_overlap_mode == "same"


def test_text_preprocess_settings_round_trip(qtbot):
    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._ignore_newlines_check.isChecked() is True
    assert dialog._ignore_whitespace_check.isChecked() is False

    dialog._ignore_newlines_check.setChecked(False)
    dialog._ignore_whitespace_check.setChecked(True)

    result = dialog.result_settings()
    assert result.ignore_newlines is False
    assert result.ignore_whitespace is True


def test_ocr_tier_round_trip(qtbot):
    settings = PiiSettings()
    settings.ocr_tier = "heavy"
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._ocr_tier_combo.currentData() == "heavy"

    idx = dialog._ocr_tier_combo.findData("light")
    dialog._ocr_tier_combo.setCurrentIndex(idx)
    result = dialog.result_settings()
    assert result.ocr_tier == "light"
