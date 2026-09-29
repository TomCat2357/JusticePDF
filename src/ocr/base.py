"""OCRバックエンドの共通基盤。

Ported from PresidioPDF src/ocr/base.py(文字色の自動検出など JusticePDF では
使わない部分を除いた版)。

OCR エンジン非依存の出力データ構造(``OCRResult``)と、各バックエンドが共有する
画像前処理(既存テキストの白塗り)・結果整形・座標正規化のヘルパ
(``_OCRServiceBase``)を提供する。具体的な OCR エンジンは ``RapidOCRService``
がこの基盤を実装する。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import fitz

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """OCRの行矩形(PDFの表示座標系。回転適用後のページ左上が原点、単位はpt)。"""

    text: str
    x: float
    y: float
    width: float
    height: float
    page_num: int
    confidence: float = 0.0

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


class _OCRServiceBase:
    """OCRバックエンドの共通実装(画像前処理・結果整形・座標正規化)。

    サブクラスは ``run_ocr_on_page`` と ``is_available`` を実装する。
    """

    # ------------------------------------------------------------------ #
    # 画像前処理・結果整形(サブクラスの run_ocr_on_page から利用)
    # ------------------------------------------------------------------ #
    def _prepare_image(
        self,
        page_pixmap: "fitz.Pixmap",
        existing_text_rects: Optional[Sequence[Sequence[float]]],
    ) -> Any:
        """Pixmapを画像化し、既存テキスト矩形を白塗りして返す。

        既にテキストレイヤがある部分(画像化した時にも文字として見える)を
        白で潰してから OCR にかけることで、同じ文字を二重に認識しない。
        """
        if not isinstance(page_pixmap, fitz.Pixmap):
            raise TypeError("page_pixmapはfitz.Pixmapである必要があります")

        image = self._pixmap_to_image(page_pixmap)
        _, image_draw_module = self._get_pillow_modules()
        draw = image_draw_module.Draw(image)
        for rect in existing_text_rects or []:
            normalized = self._normalize_rect(rect)
            if not normalized:
                continue
            draw.rectangle(normalized, fill=(255, 255, 255))
        return image

    def _finalize_results(
        self,
        raw_result: Any,
        *,
        page_num: int,
        scale_x: float,
        scale_y: float,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> List[OCRResult]:
        """OCRエンジンの生結果(``(text, quad, score)`` の列)を ``OCRResult`` へ正規化する。

        画像ピクセル座標の quad を外接矩形にし、``scale``(72/dpi)でPDFの pt へ換算する。
        """
        results: List[OCRResult] = []
        for item in self._iter_result_items(raw_result):
            parsed = self._parse_raw_result_item(item)
            if parsed is None:
                continue
            text, (x, y, w, h), confidence = parsed
            if not text or w <= 0.0 or h <= 0.0:
                continue
            results.append(
                OCRResult(
                    text=text,
                    x=(float(x) * float(scale_x)) + float(offset_x),
                    y=(float(y) * float(scale_y)) + float(offset_y),
                    width=float(w) * float(scale_x),
                    height=float(h) * float(scale_y),
                    page_num=int(page_num),
                    confidence=float(confidence),
                )
            )
        return results

    # ------------------------------------------------------------------ #
    # 座標・結果パース(バックエンド非依存の純ユーティリティ)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _get_pillow_modules():
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:
            raise ImportError(
                "OCR機能にはPillowが必要です。`uv sync --extra ocr` を確認してください。"
            ) from exc
        return Image, ImageDraw

    @staticmethod
    def _pixmap_to_image(pixmap: "fitz.Pixmap") -> Any:
        image_module, _ = _OCRServiceBase._get_pillow_modules()
        mode = "RGBA" if pixmap.alpha else "RGB"
        image = image_module.frombytes(
            mode, (pixmap.width, pixmap.height), pixmap.samples
        )
        if mode == "RGBA":
            image = image.convert("RGB")
        return image

    @staticmethod
    def extract_existing_text_rects(
        page: "fitz.Page",
        dpi: int,
    ) -> List[Tuple[float, float, float, float]]:
        """ページ上の既存テキスト(span)の矩形を、``dpi`` で画像化した座標へ換算して返す。"""
        text_dict = page.get_text("dict") or {}
        blocks = text_dict.get("blocks", []) if isinstance(text_dict, dict) else []
        scale = float(dpi) / 72.0
        # get_text の座標はページの回転前(内部座標)。画像化(get_pixmap)は回転後の
        # 表示向きなので、白塗りする矩形も表示座標へ直してから画像座標へ換算する。
        rotation_matrix = page.rotation_matrix if page.rotation else None
        rects: List[Tuple[float, float, float, float]] = []
        for block in blocks:
            if not isinstance(block, dict) or int(block.get("type", -1)) != 0:
                continue
            for line in block.get("lines", []) or []:
                if not isinstance(line, dict):
                    continue
                for span in line.get("spans", []) or []:
                    if not isinstance(span, dict):
                        continue
                    normalized = _OCRServiceBase._normalize_rect(span.get("bbox"))
                    if not normalized:
                        continue
                    if rotation_matrix is not None:
                        display = fitz.Rect(normalized) * rotation_matrix
                        display.normalize()
                        normalized = (display.x0, display.y0, display.x1, display.y1)
                    x0, y0, x1, y1 = normalized
                    rects.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale))
        return rects

    @staticmethod
    def _normalize_rect(raw_rect: Any) -> Optional[Tuple[float, float, float, float]]:
        """矩形/四隅点(quad)/8要素配列/辞書のいずれも (x0, y0, x1, y1) の外接矩形へ揃える。"""
        if isinstance(raw_rect, fitz.Rect):
            x0, y0, x1, y1 = raw_rect
        elif isinstance(raw_rect, dict):
            if {"x", "y", "width", "height"} <= set(raw_rect.keys()):
                try:
                    x0 = float(raw_rect.get("x"))
                    y0 = float(raw_rect.get("y"))
                    x1 = x0 + float(raw_rect.get("width"))
                    y1 = y0 + float(raw_rect.get("height"))
                except (TypeError, ValueError):
                    return None
            elif {"left", "top", "right", "bottom"} <= set(raw_rect.keys()):
                try:
                    x0 = float(raw_rect.get("left"))
                    y0 = float(raw_rect.get("top"))
                    x1 = float(raw_rect.get("right"))
                    y1 = float(raw_rect.get("bottom"))
                except (TypeError, ValueError):
                    return None
            else:
                return None
        elif isinstance(raw_rect, (list, tuple)):
            if raw_rect and all(
                isinstance(point, (list, tuple)) and len(point) >= 2
                for point in raw_rect
            ):
                points: List[Tuple[float, float]] = []
                for point in raw_rect:
                    try:
                        points.append((float(point[0]), float(point[1])))
                    except (TypeError, ValueError):
                        return None
                x0 = min(point[0] for point in points)
                y0 = min(point[1] for point in points)
                x1 = max(point[0] for point in points)
                y1 = max(point[1] for point in points)
            elif len(raw_rect) >= 8:
                try:
                    numbers = [float(value) for value in raw_rect[:8]]
                except (TypeError, ValueError):
                    return None
                points = list(zip(numbers[0::2], numbers[1::2]))
                x0 = min(point[0] for point in points)
                y0 = min(point[1] for point in points)
                x1 = max(point[0] for point in points)
                y1 = max(point[1] for point in points)
            elif len(raw_rect) >= 4:
                try:
                    x0 = float(raw_rect[0])
                    y0 = float(raw_rect[1])
                    x1 = float(raw_rect[2])
                    y1 = float(raw_rect[3])
                except (TypeError, ValueError):
                    return None
            else:
                return None
        else:
            return None

        if x1 <= x0 or y1 <= y0:
            return None
        return x0, y0, x1, y1

    @staticmethod
    def _iter_result_items(raw_result: Any) -> Iterable[Any]:
        if isinstance(raw_result, list):
            return raw_result
        if isinstance(raw_result, tuple):
            return list(raw_result)
        if isinstance(raw_result, dict):
            for key in ("results", "result", "lines", "ocr_results", "items", "data"):
                value = raw_result.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, tuple):
                    return list(value)
            if {"text", "bbox"} <= set(raw_result.keys()) or {"text", "box"} <= set(
                raw_result.keys()
            ):
                return [raw_result]
        return []

    @staticmethod
    def _parse_raw_result_item(
        item: Any,
    ) -> Optional[Tuple[str, Tuple[float, float, float, float], float]]:
        if isinstance(item, dict):
            text = str(item.get("text", "") or "").strip()
            raw_box = item.get("bbox")
            if raw_box is None:
                raw_box = item.get("box")
            if raw_box is None:
                raw_box = item.get("bounds")
            if raw_box is None:
                raw_box = item.get("points")
            if raw_box is None:
                raw_box = item.get("polygon")
            if raw_box is None:
                raw_box = item.get("boundingBox")
            confidence = item.get(
                "confidence", item.get("score", item.get("prob", 0.0))
            )
        elif isinstance(item, (list, tuple)):
            if len(item) < 2:
                return None
            text = str(item[0] or "").strip()
            raw_box = item[1]
            confidence = item[2] if len(item) >= 3 else 0.0
        else:
            return None

        normalized = _OCRServiceBase._normalize_rect(raw_box)
        if not normalized:
            return None
        x0, y0, x1, y1 = normalized

        try:
            score = float(confidence)
        except (TypeError, ValueError):
            score = 0.0

        return text, (x0, y0, x1 - x0, y1 - y0), score
