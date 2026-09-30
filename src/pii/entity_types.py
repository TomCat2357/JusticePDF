"""個人情報エンティティタイプ定義の一元管理。

Ported from PresidioPDF src/core/entity_types.py（無改修）。
プロジェクト全体で使用されるエンティティタイプ・日本語名・色・
エイリアスをここで一括管理し、DRY原則を維持する。
"""

from typing import Dict, List, Tuple


# --- エンティティタイプ一覧（正式名、順序固定） ---
ENTITY_TYPES: List[str] = [
    "PERSON",
    "LOCATION",
    "DATE_TIME",
    "PHONE_NUMBER",
    "INDIVIDUAL_NUMBER",
    "YEAR",
    "PROPER_NOUN",
    "OTHER",
]

# 手動で追加した塗りつぶし候補(テキスト候補/塗り四角/塗り丸)の既定エンティティ種別。
# 自動検出エンジンの対象にはしない(``ENTITY_TYPES`` には含めない)ため、
# 検出対象(``PiiSettings.enabled_entities``)には混ぜず、表示の有無だけを
# ``PiiSettings.manual_visible`` で扱う。
MANUAL_ENTITY_TYPE: str = "MANUAL"

# 個人情報検出ドロワーの「表示・検出する種別」チェックボックス等、UI側で
# 「通常のエンティティ種別 + 手動」をまとめて扱いたい箇所向けの一覧(手動を末尾に置く)。
ENTITY_TYPES_WITH_MANUAL: List[str] = ENTITY_TYPES + [MANUAL_ENTITY_TYPE]

# --- CLI小文字エイリアス → 正式名 ---
ENTITY_ALIASES: Dict[str, str] = {
    "person": "PERSON",
    "location": "LOCATION",
    "date_time": "DATE_TIME",
    "phone_number": "PHONE_NUMBER",
    "individual_number": "INDIVIDUAL_NUMBER",
    "year": "YEAR",
    "proper_noun": "PROPER_NOUN",
    "other": "OTHER",
    # 追加エイリアス
    "address": "LOCATION",
}

# --- 日本語表示名 ---
ENTITY_TYPE_NAMES_JA: Dict[str, str] = {
    "PERSON": "人名",
    "LOCATION": "場所",
    "DATE_TIME": "日時",
    "PHONE_NUMBER": "電話番号",
    "INDIVIDUAL_NUMBER": "マイナンバー",
    "YEAR": "年号",
    "PROPER_NOUN": "固有名詞",
    "OTHER": "その他",
    "MANUAL": "手動",
}

# --- ハイライト（マーカー注釈）用色 RGB [0.0-1.0] ---
# JusticePDF のマーカー注釈はハイライト系の色をそのまま使うため、
# PresidioPDF の HIGHLIGHT_COLORS をそのまま採用する。
HIGHLIGHT_COLORS: Dict[str, List[float]] = {
    "PERSON": [1.0, 0.8, 0.8],
    "LOCATION": [0.8, 1.0, 0.8],
    "DATE_TIME": [0.8, 0.8, 1.0],
    "PHONE_NUMBER": [1.0, 1.0, 0.8],
    "INDIVIDUAL_NUMBER": [1.0, 0.8, 1.0],
    "YEAR": [0.9, 0.8, 1.0],
    "PROPER_NOUN": [1.0, 0.9, 0.8],
    # 手動追加(テキスト候補/塗り四角/塗り丸)の既定色。他のどの種別の既定色とも
    # 被らない薄い水色にして、視覚的に「自動検出ではない」ことが分かるようにする。
    "MANUAL": [0.75, 0.92, 0.95],
}
HIGHLIGHT_COLOR_DEFAULT: List[float] = [0.9, 0.9, 0.9]


def get_highlight_color(entity_type: str) -> Tuple[float, float, float]:
    """エンティティタイプに対応するマーカー色を返す(既定色つき)。"""
    color = HIGHLIGHT_COLORS.get(entity_type, HIGHLIGHT_COLOR_DEFAULT)
    return (float(color[0]), float(color[1]), float(color[2]))


def get_entity_type_name_ja(entity_type: str) -> str:
    """エンティティタイプの日本語表示名を返す。"""
    return ENTITY_TYPE_NAMES_JA.get(entity_type, entity_type)


def normalize_entity_key(name: str) -> str:
    """エンティティ名を正式名（大文字）に正規化する。

    小文字エイリアス、大文字そのまま、addressエイリアスに対応。
    見つからない場合は大文字化した値を返す（空入力なら空文字列）。
    """
    n = str(name or "").strip()
    if not n:
        return ""
    low = n.lower()
    if low in ENTITY_ALIASES:
        return ENTITY_ALIASES[low]
    return n.upper()
