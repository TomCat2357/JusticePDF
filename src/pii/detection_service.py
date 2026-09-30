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
        # 辞書種別/分割モードが変わったら古いAnalyzerは捨てる(大きな辞書が常駐し続けないように)。
        _analyzer_cache.clear()
        analyzer = Analyzer(ConfigManager(settings.to_config_overrides()))
        _analyzer_cache[cache_key] = analyzer
    else:
        analyzer.config_manager = ConfigManager(settings.to_config_overrides())
    return analyzer


def clear_analyzer_cache() -> None:
    """テスト・辞書切替時などにキャッシュ済みAnalyzerを破棄する。"""
    _analyzer_cache.clear()


def _detect_plain(
    text: str,
    settings: PiiSettings,
    quads_for_span: Callable[[int, int], object],
) -> list[dict]:
    """テキストを解析し、``quads_for_span(start, end)`` が真値を返した結果だけを
    (重複除去まで済ませて)dict のリストで返す。各dictに ``_quads`` として
    ``quads_for_span`` の戻り値を持たせる。"""
    if not text.strip():
        return []

    analyzer = _get_analyzer(settings)
    entities = settings.enabled_entity_list()
    normalized = normalize_1to1(text)
    model_results = analyzer.analyze_text(normalized, entities)

    plain: list[dict] = []
    for r in model_results:
        quads = quads_for_span(int(r["start"]), int(r["end"]))
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
    return plain


def detect_pii_in_text(
    text: str, chars: list[dict], settings: PiiSettings
) -> list[tuple[str, str, list[tuple[float, float, float, float]]]]:
    """テキスト+文字座標から (entity_type, text, quads) のリストを返す(1ページ分)。

    解析は、全角の数字・英字などを1文字ずつ半角へ揃えた文字列(``normalize_1to1``。
    文字数が変わらないので、オフセットと文字座標は1対1のまま)に対して行う。
    返す語句(text)は、ページ上の元の文字列(全角のまま)。
    """
    plain = _detect_plain(
        text, settings, lambda start, end: offset_span_to_quads(chars, start, end)
    )
    return [(str(d["entity"]), str(d["text"]), d["_quads"]) for d in plain]


def detect_pii_across_pages(
    pages: "list[tuple[int, str, list[dict]]]",
    settings: PiiSettings,
    *,
    defer_last_page_starts: bool = False,
    skip_starts_before: int = 0,
    carry_out: "list[int] | None" = None,
) -> "list[tuple[int, str, str, list[tuple[float, float, float, float]]]]":
    """連続するページ群のテキストを連結して解析し、(page_num, entity_type, text, quads)
    をページごとの断片として返す。

    ページ内の行と行は区切り文字なしで連結されているので、ページ間も同じく区切りなしで
    つなぐ(ページ末尾「山」+次ページ先頭「田太郎」の氏名も検出できる)。ページをまたぐ
    結果は、断片ごとに自分のページの結果(quadはそのページ分だけ)になるが、``text`` は
    どの断片もマッチ全体の文字列になる。

    ``defer_last_page_starts=True`` のときは、最後のページから始まる結果を返さない
    (次の窓の先頭ページとして検出し直すため。``run_detection`` の窓処理用)。
    ``skip_starts_before`` は、先頭ページ内のオフセットがこの値より前から始まる結果を
    捨てる(前の窓が既に採用した、重なりページにまたがる結果の重複を避けるため)。
    ``carry_out`` を渡すと、採用した結果のうち最後のページに食い込んだものの最大終了位置
    (最後のページ先頭からのオフセット。無ければ0)を ``carry_out[0]`` に入れる
    (次の窓の ``skip_starts_before`` に渡す)。
    """
    if not pages:
        return []
    text = "".join(t for _, t, _ in pages)
    chars: list[dict] = []
    bounds: list[tuple[int, int, int]] = []  # (page_num, 開始オフセット, 終了オフセット)
    for page_num, _page_text, page_chars in pages:
        bounds.append((page_num, len(chars), len(chars) + len(page_chars)))
        chars.extend(page_chars)

    def page_quads(start: int, end: int) -> "dict[int, list]":
        result: dict[int, list] = {}
        for page_num, b0, b1 in bounds:
            lo, hi = max(start, b0), min(end, b1)
            if lo >= hi:
                continue
            quads = offset_span_to_quads(chars, lo, hi)
            if quads:
                result[page_num] = quads
        return result

    plain = _detect_plain(text, settings, page_quads)
    last_start = bounds[-1][1]
    out: list[tuple[int, str, str, list]] = []
    carry = 0
    for d in plain:
        if int(d["start"]) < skip_starts_before:
            continue
        if defer_last_page_starts and len(bounds) > 1 and int(d["start"]) >= last_start:
            continue
        carry = max(carry, int(d["end"]) - last_start)
        for page_num, quads in d["_quads"].items():
            out.append((page_num, str(d["entity"]), str(d["text"]), quads))
    if carry_out is not None:
        carry_out[:] = [max(carry, 0)]
    return out


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


# ページをまたぐ検出で、連続ページの連結を1回の解析にまとめる最大ページ数。
# 長い連続範囲でも進捗を出せるよう、この枚数ごとの窓に区切って解析する
# (窓は1ページ重ねる。重なりのページから始まる結果は、次の窓の先頭として検出する)。
CROSS_PAGE_WINDOW = 10


def _consecutive_runs(page_indices: list[int]) -> list[list[int]]:
    """ページ番号(PDFのページ順に並べ替え、重複除去)を、連続する番号ごとの並びに分ける。"""
    runs: list[list[int]] = []
    for page_num in sorted(set(int(p) for p in page_indices)):
        if runs and runs[-1][-1] + 1 == page_num:
            runs[-1].append(page_num)
        else:
            runs.append([page_num])
    return runs


def run_detection(
    pdf_path: str,
    page_indices: list[int],
    settings: PiiSettings,
    *,
    progress_callback: "Callable[[int, int], None] | None" = None,
    warnings: "list[str] | None" = None,
) -> list[PiiDetection]:
    """指定ページ群の個人情報を検出する(バックグラウンドスレッドから呼ぶ想定)。

    ``settings.cross_page_detection`` が有効なときは、検出対象のうちページ番号が連続する
    並びごとにテキストを連結して解析する(1,3,5 のように飛んでいれば連結しない)。
    無効なときは従来どおりページごとに独立して検出する。

    ``warnings`` を渡すと、ユーザーに見せるべき注意(辞書のフォールバック等)を追記する。
    """
    results: list[PiiDetection] = []
    total = len(page_indices)
    done = 0
    ocr_cache: dict[int, tuple] = {}

    def tick(n: int = 1) -> None:
        nonlocal done
        for _ in range(n):
            done += 1
            if progress_callback is not None:
                progress_callback(done, total)

    def ocr_regions(page_num: int):
        if page_num not in ocr_cache:
            ocr_cache[page_num] = _ocr_regions(pdf_path, page_num)
        return ocr_cache[page_num]

    def add_detection(page_num: int, entity_type: str, entity_text: str, quads) -> None:
        ocr_rects, bounds = ocr_regions(page_num)
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

    def detect_single(page_num: int) -> None:
        try:
            text, chars = get_page_text_and_chars(pdf_path, page_num)
            for entity_type, entity_text, quads in detect_pii_in_text(text, chars, settings):
                add_detection(page_num, entity_type, entity_text, quads)
        except Exception:
            logger.exception("PII検出に失敗しました: page=%s", page_num)
        tick()

    # 前の窓が採用した結果が、重なりページ(今の窓の先頭ページ)へ食い込んだ長さ。
    # その範囲より前から始まる結果は、前の窓で検出済みなので捨てる。
    carry = [0]

    def analyze_window(window: list, *, final: bool) -> None:
        skip = carry[0]
        carry[0] = 0
        try:
            for page_num, entity_type, entity_text, quads in detect_pii_across_pages(
                window,
                settings,
                defer_last_page_starts=not final,
                skip_starts_before=skip,
                carry_out=carry,
            ):
                add_detection(page_num, entity_type, entity_text, quads)
        except Exception:
            logger.exception(
                "PII検出に失敗しました: pages=%s", [p for p, _, _ in window]
            )
        tick(len(window) if final else len(window) - 1)

    if not settings.cross_page_detection:
        for page_num in page_indices:
            detect_single(page_num)
    else:
        for run in _consecutive_runs(page_indices):
            if len(run) == 1:
                detect_single(run[0])
                continue
            window: list[tuple[int, str, list[dict]]] = []
            carry[0] = 0
            for page_num in run:
                try:
                    text, chars = get_page_text_and_chars(pdf_path, page_num)
                except Exception:
                    # 読めないページは連結できないので、そこで連結を区切る。
                    logger.exception("PII検出に失敗しました: page=%s", page_num)
                    if window:
                        analyze_window(window, final=True)
                        window = []
                    carry[0] = 0
                    tick()
                    continue
                window.append((page_num, text, chars))
                if len(window) >= CROSS_PAGE_WINDOW:
                    analyze_window(window, final=False)
                    window = [window[-1]]
            if window:
                analyze_window(window, final=True)

    # 辞書を読み込めずフォールバックした場合は、結果とは別にユーザーへ知らせる。
    if warnings is not None:
        analyzer = _analyzer_cache.get(
            (settings.sudachi_dict_type, settings.sudachi_split_mode)
        )
        message = analyzer.tokenizer_fallback_message if analyzer is not None else None
        if message and message not in warnings:
            warnings.append(message)
    return results
