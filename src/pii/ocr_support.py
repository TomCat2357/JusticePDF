"""OCR（RapidOCR）によるテキストレイヤ無しページの検出補助（任意機能）。

Ported from PresidioPDF src/ocr/rapidocr_service.py / src/ocr/base.py の
アイデアを踏襲するが、PresidioPDF 版は「OCR結果をPDFへ不可視テキストとして
埋め込む」ためのものだった。JusticePDF はPDFへの書き込みを行わず、
個人情報検出用のテキスト＋座標(quad)だけをその場で得られればよいため、
必要な部分（画像化→RapidOCR実行→矩形の取得）だけを抜き出して書き直した。

``rapidocr``（および推論バックエンドの ``onnxruntime``）は
``pyproject.toml`` の ``ocr`` extra でのみ導入されるオプション機能なので、
本モジュールは import 時に失敗しないよう遅延 import する。
"""
from __future__ import annotations

import importlib.util
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_engine = None  # RapidOCR エンジンは初期化が重いため遅延生成してキャッシュする


def is_ocr_available() -> bool:
    """rapidocr / onnxruntime が導入済みかどうかを返す（実際には import しない）。"""
    try:
        return (
            importlib.util.find_spec("rapidocr") is not None
            and importlib.util.find_spec("onnxruntime") is not None
        )
    except Exception:
        return False


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR  # noqa: PLC0415 - 任意依存の遅延import

        _engine = RapidOCR()
    return _engine


def ocr_page_text_and_chars(
    pdf_path: str, page_num: int, *, dpi: int = 300
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

    engine = _get_engine()
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
