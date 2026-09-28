"""src.pii.engines / ginza_recognizer / janome_recognizer のテスト。

GiNZA・Janomeは(要望によりpyproject.tomlでは必須依存だが)実行環境によっては
実際には読み込めないことがあるため、未導入/読み込み失敗時でもエラーになら
ず「検出0件」を返すことを確認する。さらに、実際に読み込める環境では
架空のダミーデータで検出・ラベル対応の妥当性も確認する
(``src.pii.engines.is_engine_available`` で導入済みかどうかを判定してから
skipif する。CI/開発環境の状況に応じてどちらの経路もテストされる)。
"""
from __future__ import annotations

import pytest

from src.pii import engines
from src.pii.ginza_recognizer import detect_ginza_entities
from src.pii.janome_recognizer import detect_janome_entities


def test_regex_and_datetime_engines_are_always_available():
    assert engines.is_engine_available("regex") is True
    assert engines.is_engine_available("datetime") is True


def test_unknown_engine_key_is_not_available():
    assert engines.is_engine_available("no-such-engine") is False


def test_default_enabled_engines_matches_engine_defaults():
    defaults = engines.default_enabled_engines()
    assert defaults["regex"] is True
    assert defaults["sudachi"] is True
    assert defaults["datetime"] is True
    assert defaults["ginza"] is False
    assert defaults["janome"] is False


def test_ginza_recognizer_never_raises_when_unavailable():
    # 未導入なら空リスト、導入済みならモデルなりの結果を返す(型だけ確認)。
    result = detect_ginza_entities("山田太郎さんに連絡してください。", ["PERSON"])
    assert isinstance(result, list)


def test_janome_recognizer_never_raises_when_unavailable():
    result = detect_janome_entities("山田太郎さんに連絡してください。", ["PERSON"])
    assert isinstance(result, list)


@pytest.mark.skipif(
    not engines.is_engine_available("janome"), reason="janome が実際には読み込めない環境"
)
def test_janome_recognizer_detects_person_and_location_with_correct_offsets():
    """導入済み環境限定: Janomeのラベル対応(人名→PERSON/地域→LOCATION)の妥当性確認。"""
    text = "山田太郎さんは東京都に住んでいます。"
    results = detect_janome_entities(text, ["PERSON", "LOCATION", "PROPER_NOUN"])
    by_text = {r["text"]: r for r in results}
    assert "PERSON" in {r["entity_type"] for r in results}
    assert "LOCATION" in {r["entity_type"] for r in results}
    # オフセットが実際の出現位置と一致すること(Janomeは自前でオフセットを
    # 提供しないため、src.pii.janome_recognizer が text.find で復元している)。
    for r in results:
        assert text[r["start"] : r["end"]] == r["text"]


@pytest.mark.skipif(
    not engines.is_engine_available("ginza"), reason="spaCy/GiNZAが実際には読み込めない環境"
)
def test_ginza_recognizer_detects_person_with_correct_offsets():
    """導入済み環境限定: GiNZAのラベル対応(PERSON等)の妥当性確認。"""
    text = "山田太郎さんは東京都に住んでいます。"
    results = detect_ginza_entities(text, ["PERSON", "LOCATION"])
    assert results, "GiNZAが利用可能なのに検出結果が0件だった"
    for r in results:
        assert text[r["start"] : r["end"]] == r["text"]
        assert r["entity_type"] in {"PERSON", "LOCATION"}
