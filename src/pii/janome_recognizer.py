"""Janome形態素解析による補助的な固有名詞検出(任意エンジン)。

``src.pii.engines`` の "janome" エンジンに対応する。``src.pii.pos_ne_recognizer``
(SudachiPy版)と同じ「固有名詞のサブ品詞でエンティティを分類する」方式を、
Janome(IPADic)の品詞体系に合わせて実装したもの。SudachiPyが使えない環境での
代替、または両方を有効にして検出漏れを減らす補完用途を想定する。

Janomeは任意依存のため、実際の import は初回検出時まで遅延し、失敗時は
例外を送出せず「検出0件」として扱う。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_tokenizer = None
_load_attempted = False


def _load_tokenizer():
    global _tokenizer, _load_attempted
    if _load_attempted:
        return _tokenizer
    _load_attempted = True
    try:
        from janome.tokenizer import Tokenizer

        _tokenizer = Tokenizer()
    except Exception:
        logger.warning(
            "Janomeの読み込みに失敗したため、このエンジンは無効として扱います",
            exc_info=True,
        )
        _tokenizer = None
    return _tokenizer


def is_ready() -> bool:
    return _load_tokenizer() is not None


def _classify(sub_pos: str) -> Optional[str]:
    """IPADicの固有名詞サブ品詞をエンティティ種別へ分類する。

    対応表(SudachiPy版 ``pos_ne_recognizer._classify`` に準拠):
        人名        -> PERSON
        地域        -> LOCATION
        組織/一般   -> PROPER_NOUN
    """
    if sub_pos == "人名":
        return "PERSON"
    if sub_pos in ("地域", "地名"):
        return "LOCATION"
    return "PROPER_NOUN"


def detect_janome_entities(text: str, entities: List[str]) -> List[Dict]:
    """Janomeの品詞情報から固有名詞を抽出し PERSON/LOCATION/PROPER_NOUN に分類する。"""
    if not text:
        return []
    tokenizer = _load_tokenizer()
    if tokenizer is None:
        return []

    results: List[Dict] = []
    search_from = 0
    try:
        tokens = list(tokenizer.tokenize(text))
    except Exception:
        logger.warning("Janomeでの解析に失敗しました", exc_info=True)
        return []

    for tok in tokens:
        surface = tok.surface
        if not surface:
            continue
        # JanomeはSudachiPyと違いオフセットを直接提供しないため、直前の一致
        # 位置から前方検索して座標を復元する(同じ表層形が繰り返し出現しても
        # 探索開始位置を前進させることで正しい出現位置を追える)。
        idx = text.find(surface, search_from)
        if idx < 0:
            idx = text.find(surface)
            if idx < 0:
                continue
        start, end = idx, idx + len(surface)
        search_from = end

        pos_parts = tok.part_of_speech.split(",")
        if len(pos_parts) < 3 or pos_parts[0] != "名詞" or pos_parts[1] != "固有名詞":
            continue
        entity_type = _classify(pos_parts[2])
        if entity_type not in entities:
            continue
        results.append(
            {"start": start, "end": end, "entity_type": entity_type, "text": surface}
        )
    return results
