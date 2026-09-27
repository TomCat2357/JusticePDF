"""正規表現ベースの認識器。

Ported from PresidioPDF src/analysis/recognizers/regex_recognizers.py（無改修）。
旧 Presidio ``PatternRecognizer``（INDIVIDUAL_NUMBER / YEAR / PERSON(敬称付き) /
PHONE_NUMBER）を NLP 非依存の素の ``re`` へ移植したもの。正規表現は移行前と
完全同一にし、回帰差分が出ないようにしている。返却は素の dict。
"""

from __future__ import annotations

import re
from typing import Dict, List

# (entity_type, compiled pattern)。score は旧 Presidio 互換の概念だが、
# 本パイプラインの重複抑制はスパンベースのため保持しない。
_RECOGNIZERS = [
    ("INDIVIDUAL_NUMBER", re.compile(r"(?<!\d)(?:\d{4}-?\d{4}-?\d{4})(?!\d)")),
    ("YEAR", re.compile(r"([1-9][0-9]{3}年|(令和|平成|昭和|大正|明治)([1-9][0-9]?)年)")),
    ("PERSON", re.compile(r"([一-鿿]+)(?:くん|さん|君|ちゃん|様)")),
    ("PHONE_NUMBER", re.compile(r"(?<!\d)(?:0\d{1,4}[-]?\d{1,4}[-]?\d{4})(?!\d)")),
]


def detect_regex_entities(text: str, entities: List[str]) -> List[Dict]:
    """正規表現で検出可能なエンティティを抽出する。"""
    results: List[Dict] = []
    for entity_type, pattern in _RECOGNIZERS:
        if entity_type not in entities:
            continue
        for m in pattern.finditer(text):
            s, e = m.start(), m.end()
            if s == e:
                continue
            results.append(
                {
                    "start": s,
                    "end": e,
                    "entity_type": entity_type,
                    "text": text[s:e],
                }
            )
    return results
