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
