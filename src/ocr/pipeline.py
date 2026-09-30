"""OCR実行パイプライン(GUI非依存)。

Ported from PresidioPDF ``PipelineService.run_ocr`` の認識部分。PresidioPDF 版は
認識→PDFへの埋め込み→保存までを1関数で行っていたが、JusticePDF はPDFへの
書き込みを常にメインスレッド(``PageEditWindow``)へ集約する方針のため、
ここでは「認識だけ」を行い ``{page_num: [OCRResult]}`` を返す
(バックグラウンドスレッドから呼ぶ)。埋め込みは ``src.ocr.embedder`` が担う。
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Sequence

import fitz

from src.ocr import get_ocr_service
from src.ocr.base import OCRResult, _OCRServiceBase
from src.ocr.embedder import remove_ocr_annots

logger = logging.getLogger(__name__)

DEFAULT_OCR_DPI = 300


def page_has_text(pdf_path: str, page_num: int) -> bool:
    """ページに(埋め込み済みのOCRを含む)テキストレイヤがあるか。"""
    try:
        with fitz.open(pdf_path) as doc:
            if page_num < 0 or page_num >= len(doc):
                return False
            return bool(doc[page_num].get_text().strip())
    except Exception:  # noqa: BLE001
        logger.debug("page_has_text failed: %s p%s", pdf_path, page_num, exc_info=True)
        return False


def run_ocr_pages(
    pdf_path: str,
    page_indices: Sequence[int],
    *,
    dpi: int = DEFAULT_OCR_DPI,
    tier: str = "light",
    only_textless: bool = False,
    service=None,
    progress_callback: "Optional[Callable[[int, int], None]]" = None,
) -> Dict[int, List[OCRResult]]:
    """指定ページをOCRし、``{ページ番号: [OCRResult(行単位・表示座標)]}`` を返す。

    - ページを ``dpi``(既定300)で画像化(注釈は描かない)し、既にあるテキスト
      (span矩形)を白塗りしてから認識する(同じ文字の二重認識を避ける)。
    - 以前の実行で埋め込んだOCR注釈は、メモリ上のコピーから取り除いた状態で
      白塗り矩形を計算する(古いOCRが新しい認識を白塗りしてしまわないように)。
      ファイルは変更しない。
    - ``only_textless=True`` なら、既にテキスト(OCR済み含む)があるページは
      スキップして結果に含めない(検出前の自動OCR用)。
    - 座標は 72/dpi 倍して pt に換算した、回転適用後の表示座標。
    ``service`` を省略すると ``get_ocr_service``(RapidOCR)を使う(テストでは差し替える)。
    """
    try:
        dpi_value = int(dpi)
    except (TypeError, ValueError) as exc:
        raise ValueError("dpiは整数で指定してください") from exc
    if dpi_value <= 0:
        raise ValueError("dpiは1以上で指定してください")

    pages = [int(p) for p in page_indices]
    if service is None:
        service = get_ocr_service({"tier": tier})
    scale = 72.0 / float(dpi_value)
    results: Dict[int, List[OCRResult]] = {}

    with fitz.open(pdf_path) as doc:
        total = len(pages)
        for done, page_num in enumerate(pages, start=1):
            if 0 <= page_num < len(doc):
                if only_textless and doc[page_num].get_text().strip():
                    pass  # 既にテキストがある
                else:
                    remove_ocr_annots(doc, [page_num])  # メモリ上のみ(保存しない)
                    page = doc[page_num]
                    pixmap = page.get_pixmap(dpi=dpi_value, alpha=False, annots=False)
                    existing_rects = _OCRServiceBase.extract_existing_text_rects(
                        page, dpi_value
                    )
                    results[page_num] = service.run_ocr_on_page(
                        pixmap,
                        existing_rects,
                        page_num=page_num,
                        scale_x=scale,
                        scale_y=scale,
                    )
            if progress_callback is not None:
                progress_callback(done, total)
    return results
