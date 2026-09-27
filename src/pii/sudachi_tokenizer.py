"""SudachiPy の薄いラッパー。

Ported from PresidioPDF src/analysis/backends/sudachi_tokenizer.py（無改修）。
分かち書き＋品詞（サブ品詞含む）と文字オフセットを提供する。
``sudachipy`` は重い依存のため遅延 import し、辞書/トークナイザは一度だけ生成して保持する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


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
        # sudachipy 0.6.x の新 API は `dict=`、旧 API は `dict_type=`。両対応。
        try:
            self._tokenizer = Dictionary(dict=dict_name).create()
        except TypeError:
            self._tokenizer = Dictionary(dict_type=dict_name).create()

    def tokenize(self, text: str) -> List[Token]:
        """テキストを形態素に分割する。"""
        if not text:
            return []
        return [
            Token(m.surface(), m.begin(), m.end(), tuple(m.part_of_speech()))
            for m in self._tokenizer.tokenize(text, self._mode)
        ]
