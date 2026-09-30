"""正規表現ベースの認識器。

Ported from PresidioPDF src/analysis/recognizers/regex_recognizers.py（無改修）。
旧 Presidio ``PatternRecognizer``（INDIVIDUAL_NUMBER / YEAR / PERSON(敬称付き) /
PHONE_NUMBER）を NLP 非依存の素の ``re`` へ移植したもの。正規表現は移行前と
完全同一にし、回帰差分が出ないようにしている。返却は素の dict。

ただし PERSON(敬称付き)だけは、敬称の前の漢字列が人名ではない一般語(皆さん・
お客様(→客様)・奥様 など)の場合に結果から外す後段フィルタ
(``_NON_NAME_HONORIFIC_STEMS``)をかけている。1文字の姓(林さん・森さん)が
あるため文字数では絞れず、正規表現自体は変えずに一般語の集合で除外する。
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

# 敬称(くん/さん/君/ちゃん/様)の前に来ても人名ではない一般語(PERSON の後段フィルタ用)。
# 「お客様」は正規表現上「客様」として拾われるため、語幹の「客」で判定する。
_NON_NAME_HONORIFIC_STEMS = frozenset(
    {
        "皆", "皆々", "客", "奥", "神", "仏", "殿", "姫", "坊",
        "各位", "先方", "貴方", "相手", "旦那",
    }
)


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
            if entity_type == "PERSON" and m.group(1) in _NON_NAME_HONORIFIC_STEMS:
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
