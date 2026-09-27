"""日時（DATE_TIME）認識器。

Ported from PresidioPDF src/analysis/recognizers/datetime_recognizer.py（無改修）。
旧構成では DATE_TIME は spaCy 統計 NER が唯一の検出源だった。本認識器は
正規表現でこれを代替する（和暦/西暦の年月日、月日、スラッシュ・ハイフン日付）。
マスキング用途は過剰マスク方向が望ましいため既定 ON で運用する。
相対表現（来月・翌日など）は対象外（既知の recall ギャップ）。
"""

from __future__ import annotations

import re
from typing import Dict, List

_PATTERNS = [
    # 和暦年月日: 令和6年4月1日 / 令和6年4月 / 令和6年 / 令和元年
    re.compile(
        r"(令和|平成|昭和|大正|明治)\s*[0-9０-９元]+年(?:\s*[0-9０-９]+月)?(?:\s*[0-9０-９]+日)?"
    ),
    # 西暦年月日: 2026年4月1日 / 2026年4月 / 2026年
    re.compile(r"[0-9０-９]{4}年(?:\s*[0-9０-９]+月)?(?:\s*[0-9０-９]+日)?"),
    # 月日のみ: 4月1日
    re.compile(r"[0-9０-９]{1,2}月[0-9０-９]{1,2}日"),
    # スラッシュ/ハイフン日付: 2026/4/1, 2026-04-01
    re.compile(r"(?<!\d)[0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2}(?!\d)"),
]


def detect_datetime(text: str, entities: List[str]) -> List[Dict]:
    """正規表現で日時表現を抽出する。"""
    if "DATE_TIME" not in entities:
        return []
    results: List[Dict] = []
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            s, e = m.start(), m.end()
            if s == e:
                continue
            results.append(
                {
                    "start": s,
                    "end": e,
                    "entity_type": "DATE_TIME",
                    "text": text[s:e],
                }
            )
    return results
