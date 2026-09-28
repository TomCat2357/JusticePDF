"""個人情報(PII)検出機能。

PresidioPDF の GUI 非依存な検出エンジンを JusticePDF に統合するための
移植パッケージ。各モジュールの先頭コメントに移植元(Ported from ...)を
明記している。

公開API:
    - ``entity_types``: エンティティ種別・日本語名・既定ハイライト色
    - ``settings.PiiSettings``: 検出設定(QSettingsへの永続化を含む)
    - ``detection_service.run_detection`` / ``PiiDetection``: 検出実行
    - ``pdf_text_map``: ページテキスト⇔quad座標変換
    - ``ocr_support``: 任意のOCR(RapidOCR)によるテキストレイヤ補完
"""
from src.pii.entity_types import (
    ENTITY_TYPES,
    ENTITY_TYPE_NAMES_JA,
    MANUAL_ENTITY_TYPE,
    get_entity_type_name_ja,
    get_highlight_color,
)
from src.pii.engines import ENGINES, ENGINE_KEYS, is_engine_available
from src.pii.settings import PiiSettings
from src.pii.detection_service import PiiDetection, run_detection

__all__ = [
    "ENTITY_TYPES",
    "ENTITY_TYPE_NAMES_JA",
    "MANUAL_ENTITY_TYPE",
    "get_entity_type_name_ja",
    "get_highlight_color",
    "ENGINES",
    "ENGINE_KEYS",
    "is_engine_available",
    "PiiSettings",
    "PiiDetection",
    "run_detection",
]
