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
    settings.text_exclusions_regex = [r"\d{3}-\d{4}"]
    settings.entity_exclusions = {"LOCATION": ["東京都"]}
    settings.additional_patterns = [("OTHER", r"社員番号[0-9]{4}")]
    settings.custom_names = ["架空太郎"]
    settings.dedupe_enabled = False
    settings.dedupe_overlap = "exact"
    settings.dedupe_keep = "first"
    settings.ocr_enabled = True
    settings.ocr_dpi = 400
    settings.enabled_engines["datetime"] = False
    settings.enabled_engines["sudachi"] = False
    settings.keep_existing_on_detect = True
    settings.save()

    loaded = PiiSettings.load()
    assert loaded.enabled_entities["PERSON"] is False
    assert loaded.colors["PERSON"] == pytest.approx((1.0, 0.0, 0.0))
    assert loaded.text_exclusions_regex == [r"\d{3}-\d{4}"]
    assert loaded.entity_exclusions == {"LOCATION": ["東京都"]}
    assert loaded.additional_patterns == [("OTHER", r"社員番号[0-9]{4}")]
    assert loaded.custom_names == ["架空太郎"]
    assert loaded.dedupe_enabled is False
    assert loaded.dedupe_overlap == "exact"
    assert loaded.dedupe_keep == "first"
    assert loaded.ocr_enabled is True
    assert loaded.ocr_dpi == 400
    assert loaded.enabled_engines["datetime"] is False
    assert loaded.enabled_engines["sudachi"] is False
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
    copy.enabled_engines["regex"] = False
    assert settings.enabled_engines["regex"] is True


def test_to_config_overrides_includes_engines():
    settings = PiiSettings()
    settings.enabled_engines["sudachi"] = False
    overrides = settings.to_config_overrides()
    assert overrides["engines"]["sudachi"] is False
    assert overrides["engines"]["regex"] is True


def test_load_uses_isolated_qsettings_between_tests():
    """conftest の _isolated_qsettings により、テスト間で設定が漏れないことを確認する。"""
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    settings.save()
    assert QSettings().value("pii/dedupe_enabled") is not None


def test_legacy_text_exclusions_migrate_to_escaped_regex():
    """旧「除外ワード」は記号をエスケープして除外パターンへ移行される。"""
    s = QSettings()
    PiiSettings(text_exclusions_regex=["既存"]).save(s)
    s.setValue("pii/text_exclusions", '["(株)A.B", "既存"]')

    loaded = PiiSettings.load(s)
    assert loaded.text_exclusions_regex == ["既存", r"\(株\)A\.B"]

    # 保存し直すと旧キーは消え、削除した除外パターンが再移行で復活しない。
    loaded.text_exclusions_regex = []
    loaded.save(s)
    assert PiiSettings.load(s).text_exclusions_regex == []


def test_removed_engine_keys_are_ignored_on_load():
    s = QSettings()
    s.setValue("pii/enabled_engines", '{"regex": true, "ginza": true, "janome": true}')
    loaded = PiiSettings.load(s)
    assert "ginza" not in loaded.enabled_engines
    assert "janome" not in loaded.enabled_engines


def test_mask_style_defaults():
    settings = PiiSettings()
    assert settings.mask_color == (0.0, 0.0, 0.0)
    assert settings.mask_transparency == 70
    assert settings.manual_visible is True
    assert settings.mask_opacity == pytest.approx(0.3)

    loaded = PiiSettings.load()
    assert loaded.mask_transparency == 70
    assert loaded.mask_color == (0.0, 0.0, 0.0)


def test_mask_style_round_trip():
    settings = PiiSettings()
    settings.mask_color = (0.2, 0.4, 0.6)
    settings.mask_transparency = 25
    settings.manual_visible = False
    settings.save()

    loaded = PiiSettings.load()
    assert loaded.mask_color == pytest.approx((0.2, 0.4, 0.6))
    assert loaded.mask_transparency == 25
    assert loaded.manual_visible is False
    assert loaded.mask_opacity == pytest.approx(0.75)


def test_legacy_black_display_mode_migrates_to_transparency_zero():
    s = QSettings()
    s.setValue("pii/display_mode", "black")

    assert PiiSettings.load().mask_transparency == 0


def test_legacy_other_display_modes_use_default_transparency():
    s = QSettings()
    s.setValue("pii/display_mode", "hidden")

    assert PiiSettings.load().mask_transparency == 70


def test_new_transparency_key_wins_over_legacy_display_mode():
    s = QSettings()
    s.setValue("pii/display_mode", "black")
    s.setValue("pii/mask_transparency", 40)

    assert PiiSettings.load().mask_transparency == 40


def test_hidden_entities_include_manual_and_disabled_entities():
    settings = PiiSettings()
    assert settings.hidden_entities() == set()

    settings.enabled_entities["PERSON"] = False
    settings.manual_visible = False

    assert settings.hidden_entities() == {"PERSON", "MANUAL"}
    assert settings.is_entity_visible("LOCATION") is True
    assert settings.is_entity_visible("MANUAL") is False
    # 手動は enabled_entities に混ぜない(検出対象一覧は8種別のまま)。
    assert "MANUAL" not in settings.enabled_entity_list()


def test_excluded_words_round_trip_copy_and_overrides():
    settings = PiiSettings()
    assert settings.excluded_words == []
    settings.excluded_words = ["架空太郎", "テスト語"]
    settings.save()

    loaded = PiiSettings.load()
    assert loaded.excluded_words == ["架空太郎", "テスト語"]
    assert loaded.to_config_overrides()["exclusions"]["excluded_words"] == ["架空太郎", "テスト語"]

    clone = loaded.copy()
    clone.excluded_words.append("別語")
    assert loaded.excluded_words == ["架空太郎", "テスト語"]  # コピーは独立
