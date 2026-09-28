"""OCR（RapidOCR）によるテキストレイヤ無しページの検出補助。

Ported from PresidioPDF src/ocr/rapidocr_service.py / src/ocr/base.py の
アイデアを踏襲するが、PresidioPDF 版は「OCR結果をPDFへ不可視テキストとして
埋め込む」ためのものだった。JusticePDF はPDFへの書き込みを行わず、
個人情報検出用のテキスト＋座標(quad)だけをその場で得られればよいため、
必要な部分（画像化→RapidOCR実行→矩形の取得）だけを抜き出して書き直した。
そのため PresidioPDF の「埋め込みテキスト色」「透明度」「Xオフセット/Y
オフセット」「テキスト色を画像から自動検出」といった設定はJusticePDFでは
意味を持たない(埋め込み機能自体が無いため)。移植したのは、両者に共通する
「モデル規模(tier: 軽量mobile/高精度server)」の選択のみ。

``rapidocr``（および推論バックエンドの ``onnxruntime``）は必須依存
(``pyproject.toml``)だが、実行環境によっては導入に失敗している場合もある
ため、本モジュールは import 時に失敗しないよう遅延 import する。

``src.pii.detection_service.run_detection`` は、抽出したテキストが空
(=テキストレイヤの無いページ)で ``PiiSettings.ocr_enabled`` が有効かつ
本モジュールが利用可能な場合にのみ、ここでOCRへフォールバックする
(通常のテキストがあるページではOCRを使わない)。
"""
from __future__ import annotations

import importlib.util
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# RapidOCR エンジンは初期化が重い(モデル読み込み)ため、tier(モデル規模)ごとに
# 遅延生成してキャッシュする。tier を切り替えるたびに作り直す必要があるため、
# 単一のグローバル変数ではなく tier をキーにした辞書で持つ。
_engines: dict[str, object] = {}


def is_ocr_available() -> bool:
    """rapidocr / onnxruntime が導入済みかどうかを返す（実際には import しない）。"""
    try:
        return (
            importlib.util.find_spec("rapidocr") is not None
            and importlib.util.find_spec("onnxruntime") is not None
        )
    except Exception:
        return False


def _get_engine(tier: str = "light"):
    """tier("light"=軽量/mobile、"heavy"=高精度/server)に応じたRapidOCRエンジン。

    Ported from PresidioPDF ``src/ocr/rapidocr_service.py`` のモデル選択方針
    (検出はPP-OCRv5のmobile/server、日本語認識はPP-OCRv4+``LangRec.JAPAN``)。
    """
    tier_key = "heavy" if str(tier or "").lower() == "heavy" else "light"
    engine = _engines.get(tier_key)
    if engine is None:
        from rapidocr import (  # noqa: PLC0415 - 任意依存の遅延import
            EngineType,
            LangRec,
            ModelType,
            OCRVersion,
            RapidOCR,
        )

        model_type = ModelType.SERVER if tier_key == "heavy" else ModelType.MOBILE
        lang_rec = getattr(LangRec, "JAPAN", "japan")
        params = {
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.model_type": model_type,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": lang_rec,
            "Rec.ocr_version": OCRVersion.PPOCRV4,
            "Rec.model_type": model_type,
        }
        try:
            engine = RapidOCR(params=params)
        except Exception:
            # PP-OCRv4 日本語recにSERVER版が無い等の場合、recのみmobileへ
            # フォールバックする(PresidioPDFの同様のフォールバックを踏襲)。
            logger.warning(
                "RapidOCR(%s)のモデル構成に失敗。recをmobileにフォールバックします",
                tier_key,
                exc_info=True,
            )
            params["Rec.model_type"] = ModelType.MOBILE
            engine = RapidOCR(params=params)
        _engines[tier_key] = engine
    return engine


def ocr_page_text_and_chars(
    pdf_path: str, page_num: int, *, dpi: int = 300, tier: str = "light"
) -> tuple[str, list[dict]]:
    """OCR でページのテキストと文字相当の座標情報を取得する。

    テキストレイヤの無いページ（スキャン画像等）を対象に、RapidOCR が返す
    行単位の四隅点(quad)を文字幅で等分し、
    ``src.utils.pdf_utils.rendering.get_page_chars`` と互換の
    ``{"c", "bbox", "line_id"}`` 形式へ変換する。文字単位の実座標ではなく
    行矩形からの近似だが、検出結果をハイライトする用途には十分な精度である。

    ``rapidocr`` が未導入の場合は ``ImportError`` を送出する
    (呼び出し側は :func:`is_ocr_available` で事前にガードすること)。
    """
    import fitz

    engine = _get_engine(tier)
    chars: list[dict] = []
    text_parts: list[str] = []

    with fitz.open(pdf_path) as doc:
        if page_num < 0 or page_num >= len(doc):
            return "", []
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        scale = 72.0 / float(dpi)
        with tempfile.TemporaryDirectory(prefix="justicepdf-ocr-") as tmp_dir:
            image_path = Path(tmp_dir) / "page.png"
            pix.save(str(image_path))
            output = engine(str(image_path))

    boxes = getattr(output, "boxes", None) or []
    txts = list(getattr(output, "txts", None) or [])
    line_id = 0
    for i, box in enumerate(boxes):
        text = str(txts[i] if i < len(txts) else "").strip()
        if not text:
            continue
        points = box.tolist() if hasattr(box, "tolist") else box
        try:
            xs = [float(p[0]) * scale for p in points]
            ys = [float(p[1]) * scale for p in points]
        except (TypeError, IndexError, ValueError):
            continue
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        width = x1 - x0
        if width <= 0 or not text:
            continue
        n = len(text)
        for idx, ch in enumerate(text):
            cx0 = x0 + width * idx / n
            cx1 = x0 + width * (idx + 1) / n
            chars.append({"c": ch, "bbox": (cx0, y0, cx1, y1), "line_id": line_id})
            text_parts.append(ch)
        line_id += 1

    return "".join(text_parts), chars
