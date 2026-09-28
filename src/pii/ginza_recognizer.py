"""GiNZA(spaCy日本語モデル)による補助的な固有表現認識(任意エンジン)。

``src.pii.engines`` の "ginza" エンジンに対応する。spaCy/GiNZAは重い任意依存
のため、実際の import・モデル読込みは初回検出時まで遅延し、失敗時は例外を
送出せず「検出0件」として扱う(未導入環境でも他の検出エンジンに影響しない)。
一度読み込んだモデルはプロセス内で使い回す。
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

_nlp = None
_load_attempted = False

# spaCy/GiNZAの固有表現ラベル -> JusticePDFのエンティティ種別。
# ラベル体系はモデルにより多少揺れがあるため、代表的なものを網羅する。
_LABEL_MAP: Dict[str, str] = {
    "PERSON": "PERSON",
    "PSN": "PERSON",
    "LOC": "LOCATION",
    "GPE": "LOCATION",
    "Province": "LOCATION",
    "City": "LOCATION",
    "Country": "LOCATION",
    "Spa": "LOCATION",
}


def _load_nlp():
    global _nlp, _load_attempted
    if _load_attempted:
        return _nlp
    _load_attempted = True
    try:
        import spacy

        for model_name in ("ja_ginza_electra", "ja_ginza"):
            try:
                _nlp = spacy.load(model_name)
                break
            except OSError:
                continue
        if _nlp is None:
            raise OSError(
                "GiNZAの日本語モデル(ja_ginza / ja_ginza_electra)が見つかりません"
            )
    except Exception:
        logger.warning(
            "GiNZA/spaCyの読み込みに失敗したため、このエンジンは無効として扱います",
            exc_info=True,
        )
        _nlp = None
    return _nlp


def is_ready() -> bool:
    """実際にモデルまで読み込めるかどうか(判定にも読み込みが必要な点に注意)。"""
    return _load_nlp() is not None


def detect_ginza_entities(text: str, entities: List[str]) -> List[Dict]:
    """GiNZAの統計的NERでPERSON/LOCATIONを検出する。未導入時は空リストを返す。"""
    if not text:
        return []
    nlp = _load_nlp()
    if nlp is None:
        return []
    results: List[Dict] = []
    try:
        doc = nlp(text)
    except Exception:
        logger.warning("GiNZAでの解析に失敗しました", exc_info=True)
        return []
    for ent in doc.ents:
        entity_type = _LABEL_MAP.get(ent.label_)
        if entity_type is None or entity_type not in entities:
            continue
        results.append(
            {
                "start": ent.start_char,
                "end": ent.end_char,
                "entity_type": entity_type,
                "text": ent.text,
            }
        )
    return results
