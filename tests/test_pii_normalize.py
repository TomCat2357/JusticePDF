"""検出用の1:1テキスト正規化(全角数字・ハイフン類)と、OCR由来quadの余白のテスト。

架空のダミー電話番号(090-1234-5678)のみを使う。
"""
from __future__ import annotations

import fitz
import pytest

from src.ocr.base import OCRResult
from src.ocr.embedder import embed_ocr_results
from src.pii.analyzer import Analyzer
from src.pii.config_manager import ConfigManager
from src.pii.detection_service import (
    OCR_QUAD_MARGIN_MIN_PT,
    OCR_QUAD_MARGIN_RATIO,
    detect_pii_in_text,
    expand_ocr_quads,
    run_detection,
)
from src.pii.settings import PiiSettings
from src.pii.text_normalize import normalize_1to1, original_span

pytestmark = pytest.mark.usefixtures("qapp")

FULLWIDTH_PHONE = "０９０－１２３４－５６７８"


# ---------------------------------------------------------------------------
# normalize_1to1 / original_span
# ---------------------------------------------------------------------------


def test_normalize_1to1_keeps_length_and_maps_fullwidth_ascii():
    for text in ("電話番号は" + FULLWIDTH_PHONE + "です", "ＡＢＣ１２３", "㈱山田商事", "ﾊﾝｶｸ ﾃｽﾄﾞ", "①②"):
        assert len(normalize_1to1(text)) == len(text)
    assert normalize_1to1(FULLWIDTH_PHONE) == "090-1234-5678"
    assert normalize_1to1("ＡＢＣ１２３") == "ABC123"
    # NFKC で複数文字になるもの(㈱→(株))は、文字数を変えないよう元の文字のまま。
    assert normalize_1to1("㈱山田商事") == "㈱山田商事"
    assert normalize_1to1("") == ""


@pytest.mark.parametrize(
    "dash",
    ["-", "－", "‐", "‑", "‒", "–", "—", "―", "−"],
)
def test_dash_variants_between_digits_become_ascii_hyphen(dash):
    assert normalize_1to1(f"090{dash}1234{dash}5678") == "090-1234-5678"
    assert normalize_1to1(f"０９０{dash}１２３４{dash}５６７８") == "090-1234-5678"


def test_prolonged_sound_mark_only_maps_between_digits():
    assert normalize_1to1("090ー1234ー5678") == "090-1234-5678"
    assert normalize_1to1("０９０ー１２３４") == "090-1234"
    # カタカナ語の長音や、数字と隣り合うだけの長音は変えない。
    assert normalize_1to1("ラーメン") == "ラーメン"
    assert normalize_1to1("3ー") == "3ー"
    assert normalize_1to1("ーA1") == "ーA1"
    # ダッシュ類も、数字に隣接しなければそのまま(文章中のダッシュを壊さない)。
    assert normalize_1to1("山田―太郎") == "山田―太郎"


def test_original_span_returns_page_text_not_normalized_text():
    original = "電話は" + FULLWIDTH_PHONE + "です"
    normalized = normalize_1to1(original)
    start = normalized.index("090")
    end = start + len("090-1234-5678")
    assert original_span(original, normalized, start, end, "090-1234-5678") == FULLWIDTH_PHONE
    # 語句が整形されている(空白除去など)場合は、対応する元の文字だけを拾う。
    original2 = "０９０ １２３４ ５６７８"
    normalized2 = normalize_1to1(original2)
    assert original_span(original2, normalized2, 0, len(original2), "090 1234 5678") == original2
    assert original_span(original2, normalized2, 0, len(original2), "09012345678") == "０９０１２３４５６７８"
    # 対応づけできないときは検出語をそのまま返す。
    assert original_span("abc", "abc", 0, 3, "zzz") == "zzz"
    assert original_span("abc", "abcd", 0, 3, "abc") == "abc"


def test_anchored_exclusion_patterns_and_entity_exclusions_match_normalized_detections():
    cm = ConfigManager(
        {"exclusions": {"text_exclusions_regex": ["^０９０－１２３４－５６７８$"], "entity_exclusions": {"PERSON": ["ＡＢＣ"]}}}
    )
    assert cm.is_entity_excluded("PHONE_NUMBER", "090-1234-5678") is True  # 正規化後の検出語
    assert cm.is_entity_excluded("PERSON", "ABC") is True
    assert cm.is_entity_excluded("LOCATION", "ABC") is False


# ---------------------------------------------------------------------------
# 検出: 全角の電話番号
# ---------------------------------------------------------------------------


def _chars_for(text: str) -> list[dict]:
    """テキストの各文字に、等間隔のbboxを付けた最小の文字リスト(1行)。"""
    return [
        {"c": ch, "bbox": (10.0 + i * 10.0, 20.0, 20.0 + i * 10.0, 34.0), "line_id": 0}
        for i, ch in enumerate(text)
    ]


@pytest.mark.parametrize("dash", ["-", "－", "‐", "−", "―", "ー"])
def test_detect_phone_with_fullwidth_digits_and_hyphen_variants(dash):
    for digits in ("090{d}1234{d}5678", "０９０{d}１２３４{d}５６７８"):
        phone = digits.format(d=dash)
        text = "電話番号は" + phone + "です"
        (hit,) = [
            r for r in detect_pii_in_text(text, _chars_for(text), PiiSettings()) if r[0] == "PHONE_NUMBER"
        ]
        entity, detected_text, quads = hit
        assert detected_text == phone  # 元の(全角のままの)文字列
        start = text.index(phone)
        # quad は元の文字のbbox(オフセットと座標が1対1)。
        assert quads == [(10.0 + start * 10.0, 20.0, 20.0 + (start + len(phone) - 1) * 10.0, 34.0)]


def _fullwidth_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "電話番号は" + FULLWIDTH_PHONE + "です", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def test_run_detection_finds_fullwidth_phone_in_normal_text_with_original_text_and_exact_quads(tmp_path):
    from src.pii.pdf_text_map import get_page_text_and_chars

    pdf_path = tmp_path / "fullwidth.pdf"
    _fullwidth_pdf(pdf_path)
    text, chars = get_page_text_and_chars(str(pdf_path), 0)
    start = text.index("０９０")
    end = start + len(FULLWIDTH_PHONE)

    hits = [d for d in run_detection(str(pdf_path), [0], PiiSettings()) if d.entity_type == "PHONE_NUMBER"]

    assert len(hits) == 1
    assert hits[0].text == FULLWIDTH_PHONE
    (quad,) = hits[0].quads
    assert quad == pytest.approx(
        (
            min(c["bbox"][0] for c in chars[start:end]),
            min(c["bbox"][1] for c in chars[start:end]),
            max(c["bbox"][2] for c in chars[start:end]),
            max(c["bbox"][3] for c in chars[start:end]),
        )
    )  # 通常のテキストの quad は広げない


def test_fullwidth_phone_in_ocr_embedded_text_is_detected_and_quad_is_expanded(tmp_path):
    from src.pii.pdf_text_map import get_page_text_and_chars

    pdf_path = tmp_path / "ocr-fullwidth.pdf"
    doc = fitz.open()
    doc.new_page(width=400, height=200)
    line = OCRResult("電話番号は" + FULLWIDTH_PHONE + "です", 30.0, 60.0, 300.0, 24.0, 0, 0.9)
    embed_ocr_results(doc, [line])
    doc.save(str(pdf_path))
    doc.close()
    text, chars = get_page_text_and_chars(str(pdf_path), 0)
    assert FULLWIDTH_PHONE in text
    start = text.index("０９０")
    end = start + len(FULLWIDTH_PHONE)
    raw = (
        min(c["bbox"][0] for c in chars[start:end]),
        min(c["bbox"][1] for c in chars[start:end]),
        max(c["bbox"][2] for c in chars[start:end]),
        max(c["bbox"][3] for c in chars[start:end]),
    )

    hits = [d for d in run_detection(str(pdf_path), [0], PiiSettings()) if d.entity_type == "PHONE_NUMBER"]

    assert len(hits) == 1 and hits[0].text == FULLWIDTH_PHONE
    quads = hits[0].quads
    assert quads
    margin = max(OCR_QUAD_MARGIN_MIN_PT, OCR_QUAD_MARGIN_RATIO * (raw[3] - raw[1]))
    # 文字bboxの外接矩形が、行の高さに比例する余白ぶんだけ広がっている(1つの区間に収まる場合)。
    assert min(q[0] for q in quads) == pytest.approx(raw[0] - margin, abs=0.01)
    assert max(q[2] for q in quads) == pytest.approx(raw[2] + margin, abs=0.01)
    assert min(q[1] for q in quads) == pytest.approx(raw[1] - margin, abs=0.01)
    assert max(q[3] for q in quads) == pytest.approx(raw[3] + margin, abs=0.01)


# ---------------------------------------------------------------------------
# OCR由来quadの余白
# ---------------------------------------------------------------------------


def test_expand_ocr_quads_margin_min_ratio_and_clamp():
    ocr_rects = [(0.0, 0.0, 400.0, 200.0)]
    bounds = (0.0, 0.0, 400.0, 200.0)
    # 高さ10 -> 0.25*10=2.5(最小2ptより大きい)
    (q,) = expand_ocr_quads([(100.0, 50.0, 140.0, 60.0)], ocr_rects, bounds)
    assert q == pytest.approx((97.5, 47.5, 142.5, 62.5))
    # 高さ4 -> 0.25*4=1 < 最小2pt
    (q,) = expand_ocr_quads([(100.0, 50.0, 140.0, 54.0)], ocr_rects, bounds)
    assert q == pytest.approx((98.0, 48.0, 142.0, 56.0))
    # 高さ40 -> 10pt
    (q,) = expand_ocr_quads([(100.0, 50.0, 140.0, 90.0)], ocr_rects, bounds)
    assert q == pytest.approx((90.0, 40.0, 150.0, 100.0))
    # ページの端では範囲内に収める。
    (q,) = expand_ocr_quads([(1.0, 1.0, 399.0, 199.0)], ocr_rects, bounds)
    assert q == (0.0, 0.0, 400.0, 200.0)
    # OCR注釈の外にある quad は広げない。
    outside = [(10.0, 150.0, 50.0, 160.0)]
    assert expand_ocr_quads(outside, [(0.0, 0.0, 400.0, 100.0)], bounds) == outside
    assert expand_ocr_quads(outside, [], None) == outside


def test_ocr_expansion_does_not_apply_to_pages_without_ocr(tmp_path):
    pdf_path = tmp_path / "no-ocr.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "架空太郎さんに連絡", fontname="japan", fontsize=14)
    doc.save(str(pdf_path))
    doc.close()
    from src.pii.pdf_text_map import get_page_text_and_chars

    text, chars = get_page_text_and_chars(str(pdf_path), 0)
    hits = run_detection(str(pdf_path), [0], PiiSettings())
    assert hits
    for d in hits:
        for quad in d.quads:
            assert any(
                quad[0] == pytest.approx(c["bbox"][0]) and quad[1] == pytest.approx(c["bbox"][1]) for c in chars
            )


def test_normalized_analysis_is_used_for_analyzer_but_offsets_align():
    settings = PiiSettings()
    analyzer = Analyzer(ConfigManager(settings.to_config_overrides()))
    text = "電話は" + FULLWIDTH_PHONE + "です"
    normalized = normalize_1to1(text)
    results = analyzer.analyze_text(normalized, settings.enabled_entity_list())
    phone = [r for r in results if r["entity_type"] == "PHONE_NUMBER"]
    assert phone and text[phone[0]["start"]:phone[0]["end"]] == FULLWIDTH_PHONE
    # 正規化していない全角のままでは(従来どおり)電話番号として認識されない。
    assert not [r for r in analyzer.analyze_text(text, settings.enabled_entity_list()) if r["entity_type"] == "PHONE_NUMBER"]


def test_add_detect_word_with_fullwidth_text_registers_normalized_pattern_and_detects(qtbot, monkeypatch, tmp_path):
    """全角の語句から「検出語に追加」しても、正規化後のテキストに一致するパターンで登録される。"""
    import re

    from src.utils.pdf_utils import list_pii_markup_annots
    from src.views import page_edit_pii as page_edit_pii_module
    from src.views.pii_panel import ScopeChoiceDialog
    from tests.helpers import create_page_edit_window, open_zoom

    pdf_path = tmp_path / "fullwidth-word.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "社員ＡＢＣ１２３です", fontname="japan", fontsize=14)
    doc.save(str(pdf_path))
    doc.close()
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    monkeypatch.setattr(page_edit_pii_module.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    window._ask_pii_detect_scope = lambda text, entity: ScopeChoiceDialog.SCOPE_PAGE

    window._on_pii_add_detect_word("OTHER", "ＡＢＣ１２３")

    assert ("OTHER", re.escape("ABC123")) in window._pii_settings().additional_patterns
    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_entity == "OTHER" and a.pii_text == "ＡＢＣ１２３" for a in annots)


# ---------------------------------------------------------------------------
# ユーザーの正規表現(追加パターン・除外パターン)の正規化
# ---------------------------------------------------------------------------


def test_normalize_pattern_maps_fullwidth_alnum_and_keeps_fullwidth_symbols_literal():
    from src.pii.text_normalize import normalize_pattern

    assert normalize_pattern("０９０") == "090"
    assert normalize_pattern("ＡＢＣ") == "ABC"
    assert normalize_pattern("株式会社（仮）") == r"株式会社\(仮\)"
    fullwidth = "＋＊？［］｜．＄＾｛｝＼（）"
    literals = [r"\+", r"\*", r"\?", r"\[", r"\]", r"\|", r"\.", r"\$", r"\^", r"\{", r"\}", r"\\", r"\(", r"\)"]
    for full, literal in zip(fullwidth, literals):
        assert normalize_pattern(full) == literal
    # 全角ハイフンは(文字クラス内でも範囲にならないよう)リテラル。
    assert normalize_pattern("０－９") == r"0\-9"
    # もともとの ASCII(本物の正規表現の構文)はそのまま通す。
    for regex in (r"\d{3}-\d{4}", "(a|b)+", "[0-9]*?", "^社員番号$", r"\bfoo\b"):
        assert normalize_pattern(regex) == regex
    assert normalize_pattern("") == ""


def _analyze_with(patterns, text, *, exclusions=None):
    settings = PiiSettings()
    for key in settings.enabled_engines:
        settings.enabled_engines[key] = False
    settings.additional_patterns = patterns
    if exclusions is not None:
        settings.text_exclusions_regex = exclusions
    analyzer = Analyzer(ConfigManager(settings.to_config_overrides()))
    return [r["text"] for r in analyzer.analyze_text(normalize_1to1(text), settings.enabled_entity_list())]


def test_additional_pattern_with_fullwidth_literals_matches_either_width():
    pattern = [("OTHER", "株式会社（仮）")]
    assert _analyze_with(pattern, "架空株式会社（仮）です") == ["株式会社(仮)"]
    assert _analyze_with(pattern, "架空株式会社(仮)です") == ["株式会社(仮)"]
    # 半角で書いたパターンも、全角の文書(=正規化後)にも一致する。
    assert _analyze_with([("OTHER", r"株式会社\(仮\)")], "架空株式会社（仮）です") == ["株式会社(仮)"]


def test_additional_pattern_with_fullwidth_digits_and_symbols():
    assert _analyze_with([("OTHER", "０９０")], "番号は090-1234と０９０-５６７８") == ["090", "090"]
    assert _analyze_with([("OTHER", "Ｃ＋＋")], "言語はC++です") == ["C++"]
    assert _analyze_with([("OTHER", "ａ．ｃ")], "a.c と abc") == ["a.c"]  # ．はリテラル(任意1文字ではない)


def test_halfwidth_regex_syntax_still_works():
    assert _analyze_with([("OTHER", r"\d{3}-\d{4}")], "090-1234と０９０－５６７８") == ["090-1234", "090-5678"]
    assert _analyze_with([("OTHER", "(?:社員|職員)番号[0-9]+")], "職員番号12と社員番号３４") == ["職員番号12", "社員番号34"]


def test_invalid_regex_is_still_skipped_without_error():
    assert _analyze_with([("OTHER", "[")], "文字列") == []  # ASCII の不正な正規表現
    # 全角の記号だけのパターンはリテラル(正規表現の構文エラーにならない)。
    assert sorted(_analyze_with([("OTHER", "（"), ("OTHER", "文字")], "（文字）")) == ["(", "文字"]


def test_exclusion_regex_with_fullwidth_characters_matches():
    settings = PiiSettings()
    text = "電話は090-1234-5678です"
    for regex in ("１２３４－５６７８", r"１２３４-\d{4}", "０９０"):
        settings.text_exclusions_regex = [regex]
        analyzer = Analyzer(ConfigManager(settings.to_config_overrides()))
        results = analyzer.analyze_text(text, settings.enabled_entity_list())
        assert not [r for r in results if r["entity_type"] == "PHONE_NUMBER"], regex


def test_pattern_only_detection_with_fullwidth_pattern(qtbot, monkeypatch, tmp_path):
    from src.utils.pdf_utils import list_pii_markup_annots
    from src.views import page_edit_pii as page_edit_pii_module
    from tests.helpers import create_page_edit_window, open_zoom

    pdf_path = tmp_path / "fullwidth-pattern.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "架空株式会社（仮）です", fontname="japan", fontsize=14)
    doc.save(str(pdf_path))
    doc.close()
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    monkeypatch.setattr(page_edit_pii_module.QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window._run_pattern_only_detection("OTHER", "株式会社（仮）", [0])

    annots = list_pii_markup_annots(str(pdf_path))
    assert [(a.pii_entity, a.pii_text) for a in annots] == [("OTHER", "株式会社（仮）")]  # 元の表記のまま


# ---------------------------------------------------------------------------
# 手動「テキスト候補」: OCR由来の文字は余白ぶん広げる
# ---------------------------------------------------------------------------


def _ocr_and_text_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 250), "SECRET KEEPME", fontsize=18)
    embed_ocr_results(doc, [OCRResult("山田太郎の電話番号", 40.0, 60.0, 162.0, 24.0, 0, 0.9)])
    doc.save(str(path))
    doc.close()


def _select(window, text: str) -> list[int]:
    joined = "".join(c["c"] for c in window._zoom_label._chars)
    start = joined.index(text)
    indices = list(range(start, start + len(text)))
    window._zoom_label._selected_char_indices = list(indices)
    return indices


def _assert_expanded(raw_quad, quad):
    rx0, ry0, rx1, ry1 = raw_quad
    margin = max(OCR_QUAD_MARGIN_MIN_PT, OCR_QUAD_MARGIN_RATIO * (ry1 - ry0))
    assert quad == pytest.approx((rx0 - margin, ry0 - margin, rx1 + margin, ry1 + margin), abs=0.01)


def test_manual_text_candidate_on_ocr_text_is_expanded_in_both_paths(qtbot, tmp_path):
    from src.utils.pdf_utils import list_pii_markup_annots
    from src.views.page_edit_annotations import CreateMode
    from tests.helpers import create_page_edit_window, open_zoom

    pdf_path = tmp_path / "manual-ocr.pdf"
    _ocr_and_text_pdf(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    # 1) 連続モード(ツールを先に押して選択→確定)
    window._activate_create_mode(CreateMode.MASK_MARKUP)
    indices = _select(window, "山田太郎")
    raw = window._zoom_label._quads_for_char_indices(indices)
    window._on_zoom_text_selection_released()
    (markup,) = list_pii_markup_annots(str(pdf_path), 0)
    assert len(markup.quads) == len(raw) == 1
    _assert_expanded(raw[0], markup.quads[0])
    assert markup.pii_text == "山田太郎"

    # 2) 先に選択してからボタンを押す(その場で追加)
    window._activate_create_mode(CreateMode.NONE)
    indices = _select(window, "電話番号")
    raw = window._zoom_label._quads_for_char_indices(indices)
    window._pii_panel._mask_markup_btn.click()
    markups = [m for m in list_pii_markup_annots(str(pdf_path), 0) if m.pii_text == "電話番号"]
    assert len(markups) == 1
    _assert_expanded(raw[0], markups[0].quads[0])


def test_manual_text_candidate_on_normal_text_is_not_expanded(qtbot, tmp_path):
    from src.utils.pdf_utils import list_pii_markup_annots
    from src.views.page_edit_annotations import CreateMode
    from tests.helpers import create_page_edit_window, open_zoom

    pdf_path = tmp_path / "manual-normal.pdf"
    _ocr_and_text_pdf(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._activate_create_mode(CreateMode.MASK_MARKUP)
    indices = _select(window, "SECRET")
    raw = window._zoom_label._quads_for_char_indices(indices)
    window._on_zoom_text_selection_released()

    (markup,) = list_pii_markup_annots(str(pdf_path), 0)
    assert markup.quads[0] == pytest.approx(raw[0], abs=0.01)  # 通常のテキストは広げない
