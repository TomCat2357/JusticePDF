"""PresidioPDFから移植した検出エンジン(src.pii.analyzer.Analyzer)の基本検出テスト。

架空のダミーデータ(山田太郎、090-1234-5678等)のみを使用する。
"""
from __future__ import annotations

from src.pii.analyzer import Analyzer
from src.pii.config_manager import ConfigManager
from src.pii.settings import PiiSettings


def _analyze(text: str, settings: PiiSettings | None = None):
    settings = settings or PiiSettings()
    analyzer = Analyzer(ConfigManager(settings.to_config_overrides()))
    return analyzer.analyze_text(text, settings.enabled_entity_list())


def test_detects_person_name_with_honorific():
    results = _analyze("山田太郎さんに連絡してください。")
    person_hits = [r for r in results if r["entity_type"] == "PERSON"]
    assert person_hits, results
    assert any("山田" in r["text"] for r in person_hits)


def test_detects_valid_phone_number():
    # 090-0000-0000 は libphonenumber の JP 妥当性検証で無効と判定されるため、
    # 実在しない体裁だが検証を通る番号(090-1234-5678)を使う。
    results = _analyze("電話番号は090-1234-5678です。")
    phone_hits = [r for r in results if r["entity_type"] == "PHONE_NUMBER"]
    assert len(phone_hits) == 1
    assert phone_hits[0]["text"] == "090-1234-5678"


def test_rejects_obviously_fake_phone_number():
    """全桁同一の番号のように明らかに無効な番号は検出しない(妥当性検証の確認)。"""
    results = _analyze("090-0000-0000に電話して。")
    phone_hits = [r for r in results if r["entity_type"] == "PHONE_NUMBER"]
    assert phone_hits == []


def test_detects_date_time_and_year():
    results = _analyze("令和6年4月1日に会議を行いました。")
    entity_types = {r["entity_type"] for r in results}
    assert "DATE_TIME" in entity_types


def test_detects_location():
    results = _analyze("東京都で開催されました。")
    location_hits = [r for r in results if r["entity_type"] == "LOCATION"]
    assert location_hits, results


def test_entity_filter_excludes_disabled_types():
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    results = _analyze("山田太郎さんの電話番号は090-1234-5678です。", settings)
    entity_types = {r["entity_type"] for r in results}
    assert "PERSON" not in entity_types
    assert "PHONE_NUMBER" in entity_types


def test_text_exclusion_removes_matching_word():
    settings = PiiSettings()
    settings.text_exclusions = ["090-1234-5678"]
    results = _analyze("電話番号は090-1234-5678です。", settings)
    phone_hits = [r for r in results if r["entity_type"] == "PHONE_NUMBER"]
    assert phone_hits == []


def test_additional_pattern_detects_custom_entity():
    settings = PiiSettings()
    settings.additional_patterns = [("OTHER", r"社員番号[0-9]{4}")]
    settings.enabled_entities["OTHER"] = True
    results = _analyze("社員番号1234を確認してください。", settings)
    other_hits = [r for r in results if r["entity_type"] == "OTHER"]
    assert other_hits
    assert other_hits[0]["text"] == "社員番号1234"
