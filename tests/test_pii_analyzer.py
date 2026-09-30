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


def test_disabling_sudachi_engine_stops_pos_based_detection():
    """要望3: 検出エンジンを個別にON/OFFできること(形態素解析エンジンの無効化)。"""
    settings = PiiSettings()
    settings.enabled_engines["sudachi"] = False
    results = _analyze("東京都で開催されました。", settings)
    assert results == []


def test_disabling_regex_engine_stops_regex_based_detection():
    settings = PiiSettings()
    settings.enabled_engines["regex"] = False
    results = _analyze("電話番号は090-1234-5678です。", settings)
    phone_hits = [r for r in results if r["entity_type"] == "PHONE_NUMBER"]
    assert phone_hits == []


def test_disabling_datetime_engine_stops_datetime_detection():
    settings = PiiSettings()
    settings.enabled_engines["datetime"] = False
    results = _analyze("令和6年4月1日に会議を行いました。", settings)
    assert "DATE_TIME" not in {r["entity_type"] for r in results}


def test_additional_pattern_still_applies_even_with_all_engines_disabled():
    """追加パターンは「エンジン」の枠外で常に適用される(部分再検出機能の前提)。"""
    settings = PiiSettings()
    for key in settings.enabled_engines:
        settings.enabled_engines[key] = False
    settings.additional_patterns = [("OTHER", r"社員番号[0-9]{4}")]
    results = _analyze("社員番号1234を確認してください。", settings)
    assert [r["text"] for r in results] == ["社員番号1234"]


def test_entity_filter_excludes_disabled_types():
    settings = PiiSettings()
    settings.enabled_entities["PERSON"] = False
    results = _analyze("山田太郎さんの電話番号は090-1234-5678です。", settings)
    entity_types = {r["entity_type"] for r in results}
    assert "PERSON" not in entity_types
    assert "PHONE_NUMBER" in entity_types


def test_text_exclusion_regex_removes_partially_matching_word():
    settings = PiiSettings()
    # re.search による部分一致: 検出語(電話番号全体)の一部にマッチすれば除外。
    settings.text_exclusions_regex = ["1234-5678"]
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


def test_additional_pattern_detected_even_when_immediately_adjacent_to_date():
    """回帰テスト: 追加パターンの直後に区切り文字無しで日付が続いても検出されること。

    表組みのPDFでは列同士がテキスト抽出時に区切り文字無しで連結されることが
    あり(例:「公務員」の直後に区切り無しで「昭和52年11月23日」が続く)、
    以前は重複除去(src.pii.dedupe)の境界判定の不具合により、隣接するだけで
    重ならない検出同士が誤って「重複」扱いされ、短い方(追加パターンの検出)が
    消えてしまっていた。
    """
    settings = PiiSettings()
    settings.additional_patterns = [("PERSON", "公務員")]
    results = _analyze("丸尾幸男公務員昭和52年11月23日", settings)
    entity_types_by_text = {r["text"]: r["entity_type"] for r in results}
    assert entity_types_by_text.get("公務員") == "PERSON"
    assert "DATE_TIME" in {r["entity_type"] for r in results}


# ---------------------------------------------------------------------------
# 除外が最優先(追加パターン/カスタム人名の結果にも除外が効く)
# ---------------------------------------------------------------------------


def _no_engines(settings: PiiSettings) -> PiiSettings:
    for key in settings.enabled_engines:
        settings.enabled_engines[key] = False
    return settings


def test_exact_exclusion_pattern_beats_additional_pattern():
    settings = _no_engines(PiiSettings())
    settings.additional_patterns = [("OTHER", "公務員")]
    assert [r["text"] for r in _analyze("丸尾幸男は公務員です。", settings)] == ["公務員"]

    settings.text_exclusions_regex = ["^公務員$"]
    assert _analyze("丸尾幸男は公務員です。", settings) == []


def test_entity_exclusion_beats_additional_pattern_for_that_entity_only():
    settings = _no_engines(PiiSettings())
    settings.additional_patterns = [("PERSON", "公務員")]
    settings.entity_exclusions = {"PERSON": ["公務員"]}
    assert _analyze("丸尾幸男は公務員です。", settings) == []

    # 別の種別の除外は、この種別の追加パターンには効かない(従来の種別別除外の意味を保つ)。
    settings.entity_exclusions = {"LOCATION": ["公務員"]}
    assert [r["text"] for r in _analyze("丸尾幸男は公務員です。", settings)] == ["公務員"]


def test_exclusion_regex_beats_additional_pattern():
    settings = _no_engines(PiiSettings())
    settings.additional_patterns = [("OTHER", r"社員番号[0-9]{4}")]
    settings.text_exclusions_regex = ["1234"]
    assert _analyze("社員番号1234と社員番号5678", settings)[0]["text"] == "社員番号5678"
    assert len(_analyze("社員番号1234と社員番号5678", settings)) == 1


def test_exact_exclusion_pattern_beats_custom_names():
    settings = _no_engines(PiiSettings())
    settings.custom_names = ["架空太郎"]
    assert [r["text"] for r in _analyze("架空太郎さんが来た。", settings)] == ["架空太郎"]

    settings.text_exclusions_regex = ["^架空太郎$"]
    assert _analyze("架空太郎さんが来た。", settings) == []


def test_anchored_exclusion_pattern_is_exact_match_and_applies_to_model_results():
    settings = PiiSettings()
    settings.text_exclusions_regex = ["^090-1234-5678$"]
    results = _analyze("電話番号は090-1234-5678です。", settings)
    assert [r for r in results if r["entity_type"] == "PHONE_NUMBER"] == []

    # 完全一致なので、部分的に一致するだけの語では除外されない。
    settings.text_exclusions_regex = ["^1234-5678$"]
    results = _analyze("電話番号は090-1234-5678です。", settings)
    assert [r["text"] for r in results if r["entity_type"] == "PHONE_NUMBER"] == ["090-1234-5678"]
