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

from src.pii import ocr_support
from src.pii.analyzer import Analyzer
from src.pii.config_manager import ConfigManager
from src.pii.dedupe import dedupe_detections
from src.pii.pdf_text_map import get_page_text_and_chars, offset_span_to_quads
from src.pii.settings import PiiSettings
from src.pii.sudachi_tokenizer import sudachi_dict_available

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
    """テキスト+文字座標から (entity_type, text, quads) のリストを返す(1ページ分)。"""
    if not text.strip():
        return []

    analyzer = _get_analyzer(settings)
    entities = settings.enabled_entity_list()
    model_results = analyzer.analyze_text(text, entities)

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
                "text": r["text"],
                "_quads": quads,
            }
        )

    if settings.dedupe_enabled and plain:
        deduped = dedupe_detections(
            {"plain": plain},
            overlap=settings.dedupe_overlap,
            keep=settings.dedupe_keep,
            entity_priority=settings.entity_priority_order,
            entity_overlap_mode=settings.entity_overlap_mode,
        )
        plain = deduped.get("plain", [])

    return [(str(d["entity"]), str(d["text"]), d["_quads"]) for d in plain]


def run_detection(
    pdf_path: str,
    page_indices: list[int],
    settings: PiiSettings,
    *,
    progress_callback: "Callable[[int, int], None] | None" = None,
) -> list[PiiDetection]:
    """指定ページ群の個人情報を検出する(バックグラウンドスレッドから呼ぶ想定)。"""
    # SudachiPy辞書(特に"full": 必須依存にしていない。pyproject.toml参照)が
    # 未導入の場合、ページごとの検出ループ内で毎回失敗してログを埋め尽くし、
    # かつ結果が黙って0件になるだけでは原因が分かりにくい。事前にチェックして
    # ここで例外を送出することで、ワーカーの error シグナル経由でダイアログに
    # 分かりやすいメッセージを表示できるようにする。
    if settings.is_engine_enabled("sudachi") and not sudachi_dict_available(
        settings.sudachi_dict_type
    ):
        raise ModuleNotFoundError(
            f"Sudachi辞書「{settings.sudachi_dict_type}」が導入されていないため、"
            "形態素解析(SudachiPy)エンジンを使用できません。"
            "設定で辞書を変更するか、パッケージを追加導入してください"
            f"(`uv add sudachidict-{settings.sudachi_dict_type}`)。"
        )
    results: list[PiiDetection] = []
    total = len(page_indices)
    for done, page_num in enumerate(page_indices, start=1):
        try:
            text, chars = get_page_text_and_chars(
                pdf_path,
                page_num,
                ignore_newlines=settings.ignore_newlines,
                ignore_whitespace=settings.ignore_whitespace,
            )
            # テキストレイヤの無いページ(スキャン画像等)はOCRで文字・座標を
            # 補完してから検出する(設定でOCRを有効にし、かつ導入済みの場合のみ)。
            # ignore_newlines/ignore_whitespaceはOCR結果には(行単位の矩形から
            # 文字幅で近似した座標のため)適用しない。
            if not text.strip() and settings.ocr_enabled and ocr_support.is_ocr_available():
                text, chars = ocr_support.ocr_page_text_and_chars(
                    pdf_path, page_num, dpi=settings.ocr_dpi, tier=settings.ocr_tier
                )
            for entity_type, entity_text, quads in detect_pii_in_text(text, chars, settings):
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
