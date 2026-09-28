"""OCR先行検出(テキストレイヤの無いページの補完)・OCRモデル選択のテスト。

架空のダミーデータのみを使用する。実際のRapidOCRモデル読み込み(ネットワーク
アクセスを伴う)はテスト環境に依存するため、いずれもモック(monkeypatch)で
配線だけを確認する。
"""
from __future__ import annotations

import fitz
import pytest

from src.pii import ocr_support
from src.pii.detection_service import run_detection
from src.pii.settings import PiiSettings

pytestmark = pytest.mark.usefixtures("qapp")


def _make_blank_pdf(path) -> None:
    """テキストレイヤの無い(=スキャン画像相当の)ページを持つPDFを作る。"""
    doc = fitz.open()
    doc.new_page(width=400, height=200)
    doc.save(str(path))
    doc.close()


def _make_text_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "090-1234-5678", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


@pytest.fixture(autouse=True)
def _clear_ocr_engine_cache():
    ocr_support._engines.clear()
    yield
    ocr_support._engines.clear()


def test_run_detection_falls_back_to_ocr_when_text_layer_is_empty(tmp_path, monkeypatch):
    pdf_path = tmp_path / "blank.pdf"
    _make_blank_pdf(pdf_path)

    calls: list[dict] = []

    def fake_ocr(pdf_path_arg, page_num, *, dpi, tier):
        calls.append({"pdf_path": pdf_path_arg, "page_num": page_num, "dpi": dpi, "tier": tier})
        text = "09012345678"
        chars = [
            {"c": ch, "bbox": (i * 10.0, 0.0, i * 10.0 + 10.0, 10.0), "line_id": 0}
            for i, ch in enumerate(text)
        ]
        return text, chars

    monkeypatch.setattr(ocr_support, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr_support, "ocr_page_text_and_chars", fake_ocr)

    settings = PiiSettings()
    settings.ocr_enabled = True
    settings.ocr_dpi = 250
    settings.ocr_tier = "heavy"

    results = run_detection(str(pdf_path), [0], settings)

    assert len(calls) == 1
    assert calls[0]["dpi"] == 250
    assert calls[0]["tier"] == "heavy"
    assert any(r.entity_type == "PHONE_NUMBER" for r in results)


def test_run_detection_skips_ocr_when_disabled(tmp_path, monkeypatch):
    pdf_path = tmp_path / "blank2.pdf"
    _make_blank_pdf(pdf_path)

    called = []
    monkeypatch.setattr(ocr_support, "is_ocr_available", lambda: True)
    monkeypatch.setattr(
        ocr_support,
        "ocr_page_text_and_chars",
        lambda *a, **k: called.append(1) or ("", []),
    )

    settings = PiiSettings()
    settings.ocr_enabled = False  # 既定値と同じ(明示)

    run_detection(str(pdf_path), [0], settings)
    assert called == []


def test_run_detection_skips_ocr_when_page_already_has_text(tmp_path, monkeypatch):
    pdf_path = tmp_path / "has_text.pdf"
    _make_text_pdf(pdf_path)

    called = []
    monkeypatch.setattr(ocr_support, "is_ocr_available", lambda: True)
    monkeypatch.setattr(
        ocr_support,
        "ocr_page_text_and_chars",
        lambda *a, **k: called.append(1) or ("", []),
    )

    settings = PiiSettings()
    settings.ocr_enabled = True

    results = run_detection(str(pdf_path), [0], settings)
    assert called == []
    assert any(r.entity_type == "PHONE_NUMBER" for r in results)


def test_get_engine_selects_server_model_for_heavy_tier(monkeypatch):
    import rapidocr as rapidocr_module

    captured: dict = {}

    class _FakeRapidOCR:
        def __init__(self, params=None):
            captured.update(params or {})

    monkeypatch.setattr(rapidocr_module, "RapidOCR", _FakeRapidOCR)

    engine = ocr_support._get_engine("heavy")
    assert isinstance(engine, _FakeRapidOCR)
    assert captured["Det.model_type"] == rapidocr_module.ModelType.SERVER
    assert captured["Rec.model_type"] == rapidocr_module.ModelType.SERVER


def test_get_engine_selects_mobile_model_for_light_tier(monkeypatch):
    import rapidocr as rapidocr_module

    captured: dict = {}

    class _FakeRapidOCR:
        def __init__(self, params=None):
            captured.update(params or {})

    monkeypatch.setattr(rapidocr_module, "RapidOCR", _FakeRapidOCR)

    engine = ocr_support._get_engine("light")
    assert isinstance(engine, _FakeRapidOCR)
    assert captured["Det.model_type"] == rapidocr_module.ModelType.MOBILE
    assert captured["Rec.model_type"] == rapidocr_module.ModelType.MOBILE


def test_get_engine_caches_per_tier(monkeypatch):
    import rapidocr as rapidocr_module

    build_count = {"n": 0}

    class _FakeRapidOCR:
        def __init__(self, params=None):
            build_count["n"] += 1

    monkeypatch.setattr(rapidocr_module, "RapidOCR", _FakeRapidOCR)

    e1 = ocr_support._get_engine("light")
    e2 = ocr_support._get_engine("light")
    assert e1 is e2
    assert build_count["n"] == 1

    ocr_support._get_engine("heavy")
    assert build_count["n"] == 2
