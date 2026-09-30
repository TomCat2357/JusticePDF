"""形態素 NE 認識器。

Ported from PresidioPDF src/analysis/recognizers/pos_ne_recognizer.py
（import 先を ``src.pii.*`` へ書き換え。ロジックは無改修）。

旧 spaCy の統計 NER（PERSON/LOCATION）と PROPN 判定（PROPER_NOUN）の両方を、
SudachiPy の固有名詞サブ品詞による 1 回の走査で代替する。

対応表:
    名詞-固有名詞-人名-*        -> PERSON
    名詞-固有名詞-地名/地域-*   -> LOCATION
    名詞-固有名詞-(その他)      -> PROPER_NOUN

PDF から抽出した氏名は「姓　名」のように間へ空白が入ることがある。Sudachi は空白を
独立したトークンにするため、前後の品詞がずれて名側が検出されない(または誤分類される)。
そこで Sudachi へ渡す入力だけ、漢字・かな・カタカナに両側を挟まれた空白を除いて解析し、
結果のオフセットは対応表で元のテキスト上へ戻す(他の認識器や元テキストは変えない)。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.pii.sudachi_tokenizer import SudachiTokenizer


# 除去対象の空白(半角・全角)。両側が _CJK_CHAR のときだけ除く。英字・数字に隣接する
# 空白は語の区切りとして意味があるため残す。
# ひらがな・カタカナ(中黒「・」は除く。長音符「ー」は含む)・漢字(拡張A含む)・々〆〇。
_CJK_CHAR = r"\u3041-\u309F\u30A1-\u30FA\u30FC-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\u3005-\u3007"
_CJK_GAP_RE = re.compile(rf"(?<=[{_CJK_CHAR}])[ \u3000]+(?=[{_CJK_CHAR}])")


def strip_cjk_gaps(text: str) -> Tuple[str, List[int]]:
    """CJK 文字に挟まれた空白を除いた文字列と、除去後→元の添字対応表を返す。

    対応表 ``orig_idx[i]`` は、除去後の ``i`` 文字目が元テキストの何文字目かを表す。
    """
    kept: List[str] = []
    orig_idx: List[int] = []
    cursor = 0
    for m in _CJK_GAP_RE.finditer(text):
        kept.append(text[cursor : m.start()])
        orig_idx.extend(range(cursor, m.start()))
        cursor = m.end()
    kept.append(text[cursor:])
    orig_idx.extend(range(cursor, len(text)))
    return "".join(kept), orig_idx


def map_span_to_original(begin: int, end: int, orig_idx: List[int]) -> Tuple[int, int]:
    """除去後の [begin, end) を元テキスト上の範囲へ戻す(空でない範囲が前提)。

    終端は ``orig_idx[end]`` ではなく最終文字の次を使う(``end`` 側は直後の空白の
    向こうを指してしまうため)。空白をまたぐ範囲は空白を含む範囲になる。
    """
    return orig_idx[begin], orig_idx[end - 1] + 1


def _classify(pos: Tuple[str, ...]) -> Optional[str]:
    """品詞タプルを固有名詞サブ品詞でエンティティへ分類する。"""
    if len(pos) < 3:
        return None
    if pos[0] != "名詞" or pos[1] != "固有名詞":
        return None
    sub = pos[2]
    if sub == "人名":
        return "PERSON"
    if sub in ("地名", "地域"):
        return "LOCATION"
    # 組織・一般・その他の固有名詞
    return "PROPER_NOUN"


def detect_pos_entities(
    tokenizer: SudachiTokenizer, text: str, entities: List[str]
) -> List[Dict]:
    """形態素解析で固有名詞を抽出し PERSON/LOCATION/PROPER_NOUN に分類する。"""
    results: List[Dict] = []
    stripped, orig_idx = strip_cjk_gaps(text)
    for tok in tokenizer.tokenize(stripped):
        entity_type = _classify(tok.pos)
        if entity_type is None or entity_type not in entities:
            continue
        start, end = map_span_to_original(tok.begin, tok.end, orig_idx)
        results.append(
            {
                "start": start,
                "end": end,
                "entity_type": entity_type,
                "text": text[start:end],
            }
        )
    return results
