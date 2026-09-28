"""src.pii.engines のテスト。"""
from __future__ import annotations

from src.pii import engines


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
    assert set(defaults) == {"regex", "sudachi", "datetime"}

