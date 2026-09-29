"""JusticePDF 向け個人情報検出サービス。

``src.pii.analyzer.Analyzer``（PresidioPDF から移植した検出エンジン）と
``src.pii.pdf_text_map``（JusticePDF の ``get_page_chars`` を使った座標変換）を
つなぎ、ページ単位で検出結果(quad付き)を返す。PresidioPDF の
``gui_pyqt/services/pipeline_service.py`` に相当するが、JusticePDF は
ページ単位でしかテキストを扱わない(グローバルなページ/ブロック/オフセット
アドレッシングが不要)ため、大幅に単純化した専用実装になっている。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import fitz

from src.pii.analyzer import Analyzer
from src.pii.config_manager import ConfigManager
from src.pii.dedupe import dedupe_detections
from src.pii.pdf_text_map import get_page_text_and_chars, offset_span_to_quads
from src.pii.settings import PiiSettings
from src.pii.text_normalize import normalize_1to1, original_span

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PiiDetection:
    """1件の検出結果（表示座標系のquad付き）。"""

    page_num: int
    entity_type: str
    text: str
    quads: tuple[tuple[float, float, float, float], ...]


# Analyzer は SudachiPy 辞書のロードが重いため、辞書種別/分割モードが同じ間は
# 再利用する。exclusions/追加パターン等は呼び出しごとに ConfigManager を
# 作り直して差し替えるので、設定変更は次回の検出から即座に反映される。
_analyzer_cache: dict[tuple[str, str], Analyzer] = {}


def _get_analyzer(settings: PiiSettings) -> Analyzer:
    cache_key = (settings.sudachi_dict_type, settings.sudachi_split_mode)
    analyzer = _analyzer_cache.get(cache_key)
    if analyzer is None:
        analyzer = Analyzer(ConfigManager(settings.to_config_overrides()))
        _analyzer_cache[cache_key] = analyzer
    else:
        analyzer.config_manager = ConfigManager(settings.to_config_overrides())
    return analyzer


def clear_analyzer_cache() -> None:
    """テスト・辞書切替時などにキャッシュ済みAnalyzerを破棄する。"""
    _analyzer_cache.clear()


def detect_pii_in_text(
    text: str, chars: list[dict], settings: PiiSettings
) -> list[tuple[str, str, list[tuple[float, float, float, float]]]]:
    """テキスト+文字座標から (entity_type, text, quads) のリストを返す(1ページ分)。

    解析は、全角の数字・英字などを1文字ずつ半角へ揃えた文字列(``normalize_1to1``。
    文字数が変わらないので、オフセットと文字座標は1対1のまま)に対して行う。
    返す語句(text)は、ページ上の元の文字列(全角のまま)。
    """
    if not text.strip():
        return []

    analyzer = _get_analyzer(settings)
    entities = settings.enabled_entity_list()
    normalized = normalize_1to1(text)
    model_results = analyzer.analyze_text(normalized, entities)

    plain: list[dict] = []
    for r in model_results:
        quads = offset_span_to_quads(chars, int(r["start"]), int(r["end"]))
        if not quads:
            continue
        plain.append(
            {
                "start": r["start"],
                "end": r["end"],
                "entity": r["entity_type"],
                "text": original_span(
                    text, normalized, int(r["start"]), int(r["end"]), str(r["text"])
                ),
                "_quads": quads,
            }
        )

    if settings.dedupe_enabled and plain:
        deduped = dedupe_detections(
            {"plain": plain},
            overlap=settings.dedupe_overlap,
            keep=settings.dedupe_keep,
            entity_priority=settings.entity_priority_order,
        )
        plain = deduped.get("plain", [])

    return [(str(d["entity"]), str(d["text"]), d["_quads"]) for d in plain]


# OCR注釈の行から得た文字の位置は、画像上の実際の文字位置から2〜3pt程度ずれることがある
# (OCRの行矩形の誤差。実測)。エクスポートの黒塗りは quad に1ptだけ余白を足すため、
# OCR由来の quad は作成時に行の高さに比例する余白で広げておく(画面上の表示も同じ範囲になる)。
OCR_QUAD_MARGIN_RATIO = 0.25
OCR_QUAD_MARGIN_MIN_PT = 2.0


def _ocr_regions(
    pdf_path: str, page_num: int
) -> tuple[list[tuple[float, float, float, float]], tuple[float, float, float, float] | None]:
    """ページ内のOCR注釈の矩形(``get_page_chars`` と同じ座標系)と、ページの範囲を返す。"""
    from src.ocr.embedder import is_ocr_annot

    try:
        with fitz.open(pdf_path) as doc:
            if page_num < 0 or page_num >= len(doc):
                return [], None
            page = doc[page_num]
            rects = [
                (float(a.rect.x0), float(a.rect.y0), float(a.rect.x1), float(a.rect.y1))
                for a in (page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]) or [])
                if is_ocr_annot(a)
            ]
            if not rects:
                return [], None
            bounds = fitz.Rect(page.rect) * page.derotation_matrix
            bounds.normalize()
            return rects, (bounds.x0, bounds.y0, bounds.x1, bounds.y1)
    except Exception:  # noqa: BLE001
        logger.debug("OCR領域の取得に失敗: %s p%s", pdf_path, page_num, exc_info=True)
        return [], None


def expand_ocr_quads(
    quads: "list[tuple[float, float, float, float]]",
    ocr_rects: "list[tuple[float, float, float, float]]",
    bounds: "tuple[float, float, float, float] | None",
) -> list[tuple[float, float, float, float]]:
    """OCR注釈の上にある quad を、行の高さの ``OCR_QUAD_MARGIN_RATIO`` 倍(最小
    ``OCR_QUAD_MARGIN_MIN_PT``)だけ上下左右へ広げる(ページの範囲内に収める)。
    OCR注釈の外(通常のテキスト)の quad はそのまま。"""
    expanded: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1 in quads:
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        on_ocr = any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in ocr_rects)
        if not on_ocr:
            expanded.append((x0, y0, x1, y1))
            continue
        margin = max(OCR_QUAD_MARGIN_MIN_PT, OCR_QUAD_MARGIN_RATIO * (y1 - y0))
        nx0, ny0, nx1, ny1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
        if bounds is not None:
            nx0, ny0 = max(nx0, bounds[0]), max(ny0, bounds[1])
            nx1, ny1 = min(nx1, bounds[2]), min(ny1, bounds[3])
        expanded.append((nx0, ny0, nx1, ny1))
    return expanded


def expand_quads_for_ocr(
    pdf_path: str,
    page_num: int,
    quads: "list[tuple[float, float, float, float]]",
) -> list[tuple[float, float, float, float]]:
    """ページのOCR注釈の上にある quad だけを、OCR用の余白で広げて返す(OCR注釈が無ければそのまま)。

    自動検出(``run_detection``)と、手動の「テキスト候補」(選択した文字のquad)が共用する。
    """
    ocr_rects, bounds = _ocr_regions(pdf_path, page_num)
    if not ocr_rects:
        return list(quads)
    return expand_ocr_quads(list(quads), ocr_rects, bounds)


def run_detection(
    pdf_path: str,
    page_indices: list[int],
    settings: PiiSettings,
    *,
    progress_callback: "Callable[[int, int], None] | None" = None,
) -> list[PiiDetection]:
    """指定ページ群の個人情報を検出する(バックグラウンドスレッドから呼ぶ想定)。"""
    results: list[PiiDetection] = []
    total = len(page_indices)
    for done, page_num in enumerate(page_indices, start=1):
        try:
            text, chars = get_page_text_and_chars(pdf_path, page_num)
            ocr_rects, bounds = _ocr_regions(pdf_path, page_num)
            for entity_type, entity_text, quads in detect_pii_in_text(text, chars, settings):
                if ocr_rects:
                    quads = expand_ocr_quads(quads, ocr_rects, bounds)
                results.append(
                    PiiDetection(
                        page_num=page_num,
                        entity_type=entity_type,
                        text=entity_text,
                        quads=tuple(quads),
                    )
                )
        except Exception:
            logger.exception("PII検出に失敗しました: page=%s", page_num)
        if progress_callback is not None:
            progress_callback(done, total)
    return results
