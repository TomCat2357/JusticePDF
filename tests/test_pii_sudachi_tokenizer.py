"""src.pii.sudachi_tokenizer の辞書種別/分割モード切替えのテスト。

架空のダミーデータではなく一般的な地名・普通名詞のみを使用する
(個人情報を含まない)。
"""
from __future__ import annotations

import pytest

from src.pii.sudachi_tokenizer import (
    SUDACHI_DICT_TYPES,
    SUDACHI_SPLIT_MODES,
    SudachiTokenizer,
    sudachi_dict_available,
)


def test_sudachi_dict_types_and_split_modes_constants():
    assert SUDACHI_DICT_TYPES == ("core", "full", "small")
    assert SUDACHI_SPLIT_MODES == ("A", "B", "C")


def test_core_dict_is_available():
    # core は必須依存(pyproject.toml)なので常に導入済みのはず。
    assert sudachi_dict_available("core") is True


def test_small_dict_is_available():
    # small も要望により必須依存にした。
    assert sudachi_dict_available("small") is True


def test_unknown_dict_type_is_not_available():
    assert sudachi_dict_available("no-such-dict") is False


def test_full_dict_availability_check_never_raises():
    """full辞書は必須依存(pyproject.toml)だが、sdist配布のみでビルド時に外部
    URLから辞書データをダウンロードする特殊なパッケージのため、ネットワーク
    環境によっては導入に失敗しうる。可用性チェック自体は例外を出さず
    bool値を返すことだけを確認する(この作業環境ではネットワーク制限により
    未導入であることが多い)。
    """
    assert isinstance(sudachi_dict_available("full"), bool)


@pytest.mark.skipif(
    not sudachi_dict_available("full"),
    reason="sudachidict-full が実際には導入されていない環境",
)
def test_full_dict_tokenizes_when_available():
    """導入済み環境限定: full辞書でも実際にトークナイズできること。"""
    text = "国立国会図書館デジタルコレクションを利用した。"
    full = SudachiTokenizer(dict_type="full", split_mode="C")
    surfaces_full = [tok.surface for tok in full.tokenize(text)]
    assert "図書館" in surfaces_full


def test_split_mode_changes_tokenization_result():
    """要望: 分割モードの切替えで実際に分割結果が変わること。"""
    text = "国立国会図書館に行った。"
    mode_a = SudachiTokenizer(dict_type="core", split_mode="A")
    mode_c = SudachiTokenizer(dict_type="core", split_mode="C")

    surfaces_a = [tok.surface for tok in mode_a.tokenize(text)]
    surfaces_c = [tok.surface for tok in mode_c.tokenize(text)]

    assert surfaces_a != surfaces_c
    # Aモード(短単位)は「図書」「館」に分割される。
    assert "図書" in surfaces_a and "館" in surfaces_a
    # Cモード(長単位)は「図書館」がひとまとまりになる。
    assert "図書館" in surfaces_c


def test_dict_type_changes_tokenization_result():
    """要望: 辞書種別の切替えで実際に分割結果が変わること(core versus small)。"""
    text = "国立国会図書館デジタルコレクションを利用した。"
    core = SudachiTokenizer(dict_type="core", split_mode="C")
    small = SudachiTokenizer(dict_type="small", split_mode="C")

    surfaces_core = [tok.surface for tok in core.tokenize(text)]
    surfaces_small = [tok.surface for tok in small.tokenize(text)]

    assert surfaces_core != surfaces_small
    assert "図書館" in surfaces_core
    assert "図書" in surfaces_small and "館" in surfaces_small


def test_uninstalled_dict_raises_clear_error(monkeypatch):
    """未導入の辞書(full)を選ぶと分かりやすいエラーになること(要望5関連)。"""
    monkeypatch.setattr(
        "src.pii.sudachi_tokenizer.sudachi_dict_available", lambda _dt: False
    )
    with pytest.raises(ModuleNotFoundError, match="full"):
        SudachiTokenizer(dict_type="full", split_mode="C")
