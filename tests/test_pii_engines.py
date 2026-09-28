"""src.pii.engines / ginza_recognizer / janome_recognizer のテスト。

GiNZA・Janomeはこのプロジェクトの必須依存ではないため、未導入環境でも
エラーにならず「検出0件」を返すことを確認する(導入されていれば、その分は
別途手動確認が必要)。
"""
from __future__ import annotations

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
