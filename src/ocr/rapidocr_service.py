"""RapidOCR v3 ラッパー。

Ported from PresidioPDF src/ocr/rapidocr_service.py。

軽(mobile)/重(server)の 2 モデルを ``tier`` で選択する。日本語認識は
PP-OCRv5 に日本語 rec モデルが無いため PP-OCRv4 + ``LangRec.JAPAN`` を使用する。
検出(Det)は言語非依存で PP-OCRv5 の mobile/server を使う。
出力(``RapidOCROutput`` の ``.boxes`` = 四隅点 quad / ``.txts`` / ``.scores``)を
基盤の整形ヘルパで ``OCRResult`` へ変換する。

``rapidocr``/``onnxruntime`` は ``pyproject.toml`` の ``ocr`` extra でのみ導入される
任意依存なので、import は遅延させ、未導入でもこのモジュール自体は import できる。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Sequence

import fitz

from src.ocr.base import OCRResult, _OCRServiceBase

logger = logging.getLogger(__name__)


class RapidOCRService(_OCRServiceBase):
    """RapidOCR v3 バックエンド。"""

    _AVAILABILITY_CACHE: Optional[bool] = None

    def __init__(self, tier: str = "light"):
        self._tier = "heavy" if str(tier or "").lower() == "heavy" else "light"
        self._engine: Any = None

    @classmethod
    def is_available(cls) -> bool:
        """rapidocr と onnxruntime が import 可能か(導入済みか)を判定する。"""
        if cls._AVAILABILITY_CACHE is not None:
            return cls._AVAILABILITY_CACHE
        try:
            import importlib.util

            cls._AVAILABILITY_CACHE = (
                importlib.util.find_spec("rapidocr") is not None
                and importlib.util.find_spec("onnxruntime") is not None
            )
        except Exception:
            cls._AVAILABILITY_CACHE = False
        return cls._AVAILABILITY_CACHE

    # ------------------------------------------------------------------ #
    # エンジン構築(tier に応じた mobile/server モデル選択)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_japanese_langrec(lang_rec_enum: Any) -> Any:
        """LangRec から日本語に相当する値を解決する。"""
        for name in ("JAPAN", "JAPANESE", "JA"):
            if hasattr(lang_rec_enum, name):
                return getattr(lang_rec_enum, name)
        # 最終手段: 文字列(RapidOCR は OmegaConf 経由で文字列も受理しうる)
        return "japan"

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine

        try:
            from rapidocr import (
                RapidOCR,
                EngineType,
                ModelType,
                OCRVersion,
                LangRec,
            )
        except Exception as exc:  # pragma: no cover - import 環境依存
            raise ImportError(
                "RapidOCR が利用できません。`uv sync --extra ocr` を実行してください。"
            ) from exc

        model_type = ModelType.SERVER if self._tier == "heavy" else ModelType.MOBILE
        lang_rec = self._resolve_japanese_langrec(LangRec)

        params = {
            # 検出: 言語非依存。tier で mobile/server を切替(PP-OCRv5)
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.model_type": model_type,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            # 認識: 日本語は PP-OCRv4 を使用
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": lang_rec,
            "Rec.ocr_version": OCRVersion.PPOCRV4,
            "Rec.model_type": model_type,
        }

        try:
            self._engine = RapidOCR(params=params)
        except Exception as exc:
            # PP-OCRv4 日本語 rec に SERVER 版が無い等の場合、rec のみ mobile へフォールバック
            logger.warning(
                "RapidOCR(%s) のモデル構成に失敗。rec を mobile にフォールバックします: %s",
                self._tier,
                exc,
            )
            params["Rec.model_type"] = ModelType.MOBILE
            self._engine = RapidOCR(params=params)

        return self._engine

    # ------------------------------------------------------------------ #
    # 1ページOCR
    # ------------------------------------------------------------------ #
    def run_ocr_on_page(
        self,
        page_pixmap: "fitz.Pixmap",
        existing_text_rects: Optional[Sequence[Sequence[float]]] = None,
        *,
        page_num: int = 0,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> List[OCRResult]:
        """1ページ分のOCRを実行して座標付き(行単位)の結果を返す。

        ``existing_text_rects`` は画像ピクセル座標の既存テキスト矩形で、白塗りしてから
        認識する。``scale_x``/``scale_y`` は画像ピクセル→pt の倍率(72/dpi)。
        """
        image = self._prepare_image(page_pixmap, existing_text_rects)

        engine = self._get_engine()
        with tempfile.TemporaryDirectory(prefix="justicepdf-rapidocr-") as temp_dir:
            image_path = Path(temp_dir) / "page.png"
            image.save(image_path, format="PNG")
            output = engine(str(image_path))

        raw_items = self._output_to_items(output)
        return self._finalize_results(
            raw_items,
            page_num=page_num,
            scale_x=scale_x,
            scale_y=scale_y,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    @staticmethod
    def _output_to_items(output: Any) -> List[tuple]:
        """RapidOCR の出力を ``(text, quad, score)`` タプル列に正規化する。

        基盤の ``_parse_raw_result_item`` は list/tuple item を
        ``(text, box, score)`` 順で解釈し、quad(四隅点リスト)は
        ``_normalize_rect`` の points 経路で受理される。
        """
        if output is None:
            return []

        items: List[tuple] = []

        boxes = getattr(output, "boxes", None)
        if boxes is not None:
            txts = list(getattr(output, "txts", None) or [])
            scores = list(getattr(output, "scores", None) or [])
            for i, box in enumerate(boxes):
                text = txts[i] if i < len(txts) else ""
                score = scores[i] if i < len(scores) else 0.0
                quad = box.tolist() if hasattr(box, "tolist") else box
                items.append((text, quad, score))
            return items

        # フォールバック: [[box, text, score], ...] 形式(旧 API 互換)
        try:
            for entry in output or []:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                box = entry[0]
                text = entry[1] if len(entry) > 1 else ""
                score = entry[2] if len(entry) > 2 else 0.0
                quad = box.tolist() if hasattr(box, "tolist") else box
                items.append((text, quad, score))
        except TypeError:
            return []
        return items
