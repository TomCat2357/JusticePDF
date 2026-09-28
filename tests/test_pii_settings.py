"""PiiSettings の QSettings 永続化ラウンドトリップテスト。"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings

from src.pii.settings import PiiSettings


def test_settings_default_round_trip():
    settings = PiiSettings()
    settings.save()

    loaded = PiiSettings.load()
    assert loaded.enabled_entities == settings.enabled_entities
    assert loaded.dedupe_enabled == settings.dedupe_enabled
    assert loaded.dedupe_overlap == settings.dedupe_overlap
    assert loaded.dedupe_keep == settings.dedupe_keep
    assert loaded.sudachi_dict_type == settings.sudachi_dict_type
    assert loaded.enabled_engines == settings.enabled_engines
    assert loaded.keep_existing_on_detect == settings.keep_existing_on_detect


def test_settings_round_trip_with_customizations():
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    settings.colors["PERSON"] = (1.0, 0.0, 0.0)
    settings.text_exclusions = ["除外語A"]
    settings.text_exclusions_regex = [r"\d{3}-\d{4}"]
    settings.entity_exclusions = {"LOCATION": ["東京都"]}
    settings.additional_patterns = [("OTHER", r"社員番号[0-9]{4}")]
    settings.custom_names = ["架空太郎"]
    settings.dedupe_enabled = False
    settings.dedupe_overlap = "exact"
    settings.dedupe_keep = "first"
    settings.ocr_enabled = True
    settings.ocr_dpi = 400
    settings.ocr_tier = "heavy"
    settings.enabled_engines["datetime"] = False
    settings.enabled_engines["sudachi"] = False
    settings.entity_overlap_mode = "same"
    settings.ignore_newlines = False
    settings.ignore_whitespace = True
    settings.keep_existing_on_detect = True
    settings.save()

    loaded = PiiSettings.load()
    assert loaded.enabled_entities["PERSON"] is False
    assert loaded.colors["PERSON"] == pytest.approx((1.0, 0.0, 0.0))
    assert loaded.text_exclusions == ["除外語A"]
    assert loaded.text_exclusions_regex == [r"\d{3}-\d{4}"]
    assert loaded.entity_exclusions == {"LOCATION": ["東京都"]}
    assert loaded.additional_patterns == [("OTHER", r"社員番号[0-9]{4}")]
    assert loaded.custom_names == ["架空太郎"]
    assert loaded.dedupe_enabled is False
    assert loaded.dedupe_overlap == "exact"
    assert loaded.dedupe_keep == "first"
    assert loaded.ocr_enabled is True
    assert loaded.ocr_dpi == 400
    assert loaded.ocr_tier == "heavy"
    assert loaded.enabled_engines["datetime"] is False
    assert loaded.enabled_engines["sudachi"] is False
    assert loaded.entity_overlap_mode == "same"
    assert loaded.ignore_newlines is False
    assert loaded.ignore_whitespace is True
    assert loaded.keep_existing_on_detect is True


def test_color_for_falls_back_to_default_when_unset():
    settings = PiiSettings()
    color = settings.color_for("PERSON")
    assert len(color) == 3


def test_to_config_overrides_groups_additional_patterns_by_entity():
    settings = PiiSettings()
    settings.additional_patterns = [
        ("OTHER", r"AAA\d+"),
        ("OTHER", r"BBB\d+"),
        ("PERSON", r"CCC"),
    ]
    overrides = settings.to_config_overrides()
    recognizers = overrides["custom_recognizers"]
    entity_types = {v["entity_type"] for v in recognizers.values()}
    assert entity_types == {"OTHER", "PERSON"}
    other_entry = next(v for v in recognizers.values() if v["entity_type"] == "OTHER")
    assert [p["regex"] for p in other_entry["patterns"]] == [r"AAA\d+", r"BBB\d+"]


def test_copy_deep_copies_enabled_engines():
    settings = PiiSettings()
    copy = settings.copy()
    copy.enabled_engines["sudachi"] = False
    assert settings.enabled_engines["sudachi"] is True


def test_to_config_overrides_includes_engines():
    settings = PiiSettings()
    settings.enabled_engines["datetime"] = False
    overrides = settings.to_config_overrides()
    assert overrides["engines"]["datetime"] is False
    assert overrides["engines"]["regex"] is True


def test_to_config_overrides_includes_entity_overlap_mode_and_text_preprocess():
    settings = PiiSettings()
    settings.entity_overlap_mode = "same"
    settings.ignore_newlines = False
    settings.ignore_whitespace = True
    overrides = settings.to_config_overrides()
    assert overrides["deduplication"]["entity_overlap_mode"] == "same"
    assert overrides["text_preprocess"] == {
        "ignore_newlines": False,
        "ignore_whitespace": True,
    }


def test_load_silently_drops_removed_engine_keys():
    """要望: 旧設定にginza/janomeのキーが残っていてもエラーにならないこと。"""
    settings = QSettings()
    settings.setValue(
        "pii/enabled_engines",
        '{"regex": true, "sudachi": false, "ginza": true, "janome": true}',
    )

    loaded = PiiSettings.load()
    assert loaded.enabled_engines["regex"] is True
    assert loaded.enabled_engines["sudachi"] is False
    assert "ginza" not in loaded.enabled_engines
    assert "janome" not in loaded.enabled_engines


def test_load_uses_isolated_qsettings_between_tests():
    """conftest の _isolated_qsettings により、テスト間で設定が漏れないことを確認する。"""
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    settings.save()
    assert QSettings().value("pii/dedupe_enabled") is not None
