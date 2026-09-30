"""SudachiPy の薄いラッパー。

Ported from PresidioPDF src/analysis/backends/sudachi_tokenizer.py（無改修）。
分かち書き＋品詞（サブ品詞含む）と文字オフセットを提供する。
``sudachipy`` は重い依存のため遅延 import し、辞書/トークナイザは一度だけ生成して保持する。
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# 選択できる辞書種別 → 日本語ラベル。pyproject の extra(sudachi-small/-full、`all` に含まれる)で導入する。
SUDACHI_DICT_LABELS = {
    "small": "小(small)",
    "core": "標準(core)",
    "full": "大(full)",
}
FALLBACK_DICT_TYPE = "core"


def sudachi_dict_available(dict_type: str) -> bool:
    """辞書パッケージ(``sudachidict_<type>``)が導入済みか(実際にはimportしない)。"""
    try:
        return importlib.util.find_spec(f"sudachidict_{str(dict_type).lower()}") is not None
    except (ImportError, ValueError):
        return False


@dataclass
class Token:
    """1形態素。"""

    surface: str
    begin: int          # 入力文字列内のコードポイントオフセット（先頭）
    end: int            # 同（排他終端）
    pos: Tuple[str, ...]  # part_of_speech() の6要素タプル


class SudachiTokenizer:
    """SudachiPy トークナイザのラッパ。"""

    def __init__(self, dict_type: str = "core", split_mode: str = "C"):
        from sudachipy import Dictionary, SplitMode

        self._mode = {
            "A": SplitMode.A,
            "B": SplitMode.B,
            "C": SplitMode.C,
        }.get(str(split_mode or "C").upper(), SplitMode.C)

        dict_name = str(dict_type or "core").lower()
        # 辞書が未導入などで読み込めなかったとき、coreへ切り替えた旨のメッセージ。
        self.fallback_message: Optional[str] = None
        try:
            self._tokenizer = self._create(Dictionary, dict_name)
        except Exception as exc:  # noqa: BLE001 - 未導入辞書(ModuleNotFoundError等)
            if dict_name == FALLBACK_DICT_TYPE:
                raise
            self.fallback_message = (
                f"Sudachi辞書「{dict_name}」を読み込めなかったため、"
                f"「{FALLBACK_DICT_TYPE}」辞書で検出しました"
                f"(`uv sync`(または `pip install -e \".[all]\"`)で導入できます)。"
            )
            logger.warning("%s 原因: %s", self.fallback_message, exc)
            self._tokenizer = self._create(Dictionary, FALLBACK_DICT_TYPE)

    @staticmethod
    def _create(dictionary_cls, dict_name: str):
        # sudachipy 0.6.x の新 API は `dict=`、旧 API は `dict_type=`。両対応。
        try:
            return dictionary_cls(dict=dict_name).create()
        except TypeError:
            return dictionary_cls(dict_type=dict_name).create()

    def tokenize(self, text: str) -> List[Token]:
        """テキストを形態素に分割する。"""
        if not text:
            return []
        return [
            Token(m.surface(), m.begin(), m.end(), tuple(m.part_of_speech()))
            for m in self._tokenizer.tokenize(text, self._mode)
        ]
