"""SudachiPy の薄いラッパー。

Ported from PresidioPDF src/analysis/backends/sudachi_tokenizer.py（無改修）。
分かち書き＋品詞（サブ品詞含む）と文字オフセットを提供する。
``sudachipy`` は重い依存のため遅延 import し、辞書/トークナイザは一度だけ生成して保持する。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import List, Tuple

# UI(設定ダイアログ)で選べる辞書種別/分割モード。
# Ported from PresidioPDF DetectConfigService.SUDACHI_DICT_TYPES/SPLIT_MODES。
SUDACHI_DICT_TYPES: Tuple[str, ...] = ("core", "full", "small")
SUDACHI_SPLIT_MODES: Tuple[str, ...] = ("A", "B", "C")


def sudachi_dict_available(dict_type: str) -> bool:
    """指定辞書種別のパッケージ(``sudachidict_<type>``)が導入済みかを返す。

    ``core``/``small`` は必須依存だが、``full`` は辞書データが数百MBあり、
    かつPyPIにwheel配布が無く sdist のビルド時に外部URLへ直接アクセスする
    特殊なパッケージのため、必須依存にはしていない
    (詳細は ``pyproject.toml`` のコメント参照)。未導入でも選択自体はでき、
    実際の検出時に分かりやすいエラーで案内する。
    """
    module_name = f"sudachidict_{str(dict_type or '').strip().lower()}"
    try:
        return importlib.util.find_spec(module_name) is not None
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
        if not sudachi_dict_available(dict_name):
            raise ModuleNotFoundError(
                f"Sudachi辞書「{dict_name}」が導入されていません"
                f"(sudachidict_{dict_name} が見つかりません)。"
                f"`uv add sudachidict-{dict_name}` 等で追加導入してください。"
            )
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
