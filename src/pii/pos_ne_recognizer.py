"""形態素 NE 認識器。

Ported from PresidioPDF src/analysis/recognizers/pos_ne_recognizer.py
（import 先を ``src.pii.*`` へ書き換え。ロジックは無改修）。

旧 spaCy の統計 NER（PERSON/LOCATION）と PROPN 判定（PROPER_NOUN）の両方を、
SudachiPy の固有名詞サブ品詞による 1 回の走査で代替する。

対応表:
    名詞-固有名詞-人名-*        -> PERSON
    名詞-固有名詞-地名/地域-*   -> LOCATION
    名詞-固有名詞-(その他)      -> PROPER_NOUN
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.pii.sudachi_tokenizer import SudachiTokenizer


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
    for tok in tokenizer.tokenize(text):
        entity_type = _classify(tok.pos)
        if entity_type is None or entity_type not in entities:
            continue
        results.append(
            {
                "start": tok.begin,
                "end": tok.end,
                "entity_type": entity_type,
                "text": tok.surface,
            }
        )
    return results
