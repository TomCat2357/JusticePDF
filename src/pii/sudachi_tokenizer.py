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

    ``core``/``full``/``small`` はいずれも必須依存(``pyproject.toml``)だが、
    ``full`` はPyPIにwheel配布が無く sdist のビルド時に外部URL
    (辞書データ配布用のCDN)へ直接アクセスする特殊なパッケージのため、
    ネットワーク環境によっては ``pip install``/``uv sync`` 自体が失敗し、
    導入できないことがある(詳細は ``pyproject.toml`` のコメント参照)。
    そうした壊れた/一部だけ導入された環境でもアプリがクラッシュしないよう、
    未導入でも辞書の選択自体はでき、実際の検出時に分かりやすいエラーで
    案内するようにしている(保険的な扱い)。
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
                f"`pip install sudachidict-{dict_name}`"
                f"(uv環境では `uv add sudachidict-{dict_name}`)で追加導入してください。"
                + (
                    " full辞書はネットワーク環境によってはダウンロードに"
                    "失敗することがあります(pyproject.tomlのコメント参照)。"
                    if dict_name == "full"
                    else ""
                )
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
