"""OCR(RapidOCR)機能。

Ported from PresidioPDF src/ocr/(``OCRBackend`` の抽象化、``RapidOCRService``、
``PDFTextEmbedder``)。JusticePDF では、

- ``rapidocr_service.RapidOCRService``: 1ページ分を画像化→(既存テキストを白塗り)→
  RapidOCR で行単位に認識
- ``embedder``: 認識した行を見えない FreeText 注釈として PDF に埋め込む/削除する
- ``src.workers.ocr_worker.OcrWorker``: 上記の認識(重い処理)をバックグラウンドで実行

という分担で使う。``rapidocr``/``onnxruntime`` は ``pyproject.toml`` の ``ocr`` extra
でのみ導入される任意依存なので、未導入でもこのパッケージは import できる。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.ocr.base import OCRResult
from src.ocr.rapidocr_service import RapidOCRService

__all__ = [
    "OCRResult",
    "RapidOCRService",
    "get_ocr_service",
    "is_ocr_available",
]

OCR_INSTALL_HINT = "`uv sync --extra ocr` でOCRを有効化できます"


def is_ocr_available() -> bool:
    """rapidocr / onnxruntime が導入済みか(実際にはimportしない)。"""
    return RapidOCRService.is_available()


_service_cache: Dict[str, RapidOCRService] = {}


def get_ocr_service(ocr_settings: Optional[Dict[str, Any]] = None) -> RapidOCRService:
    """OCR設定(``{"tier": "light"|"heavy"}``)からバックエンドを返す(エンジンは使い回す)。

    Raises:
        RuntimeError: 依存(rapidocr/onnxruntime)が未導入の場合。
    """
    tier = str((ocr_settings or {}).get("tier", "light") or "light")
    if not RapidOCRService.is_available():
        raise RuntimeError(f"RapidOCR が利用できません。{OCR_INSTALL_HINT}")
    service = _service_cache.get(tier)
    if service is None:
        service = RapidOCRService(tier=tier)
        _service_cache[tier] = service
    return service
