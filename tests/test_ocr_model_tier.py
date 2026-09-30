"""OCRモデル種別(PiiSettings.ocr_model_tier)の設定・ダイアログ・配線のテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.ocr import pipeline as ocr_pipeline
from src.pii.settings import PiiSettings, normalize_ocr_model_tier
from src.views import pii_settings_dialog
from src.views.pii_settings_dialog import PiiSettingsDialog
from tests.helpers import create_page_edit_window

pytestmark = pytest.mark.usefixtures("qapp")


def _blank_pdf(path) -> None:
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    doc.save(str(path))
    doc.close()


def test_default_is_light_and_round_trips():
    assert PiiSettings().ocr_model_tier == "light"
    settings = PiiSettings()
    settings.ocr_model_tier = "heavy"
    settings.save()
    assert PiiSettings.load().ocr_model_tier == "heavy"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("heavy", "heavy"),
        ("HEAVY", "heavy"),
        ("light", "light"),
        ("", "light"),
        (None, "light"),
        ("x", "light"),
    ],
)
def test_normalize_ocr_model_tier(value, expected):
    assert normalize_ocr_model_tier(value) == expected


def test_dialog_ocr_tab_selects_tier(qtbot, monkeypatch):
    monkeypatch.setattr(pii_settings_dialog, "is_ocr_available", lambda: True)
    dialog = PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog)
    combo = dialog._ocr_tier_combo
    assert combo.currentData() == "light"
    assert "ダウンロード" in combo.itemText(combo.findData("heavy"))

    combo.setCurrentIndex(combo.findData("heavy"))
    assert dialog.result_settings().ocr_model_tier == "heavy"


def test_window_passes_configured_tier_to_ocr_worker(qtbot, tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    _blank_pdf(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    settings = window._pii_settings().copy()
    settings.ocr_model_tier = "heavy"
    window._pii_settings_cache = settings

    worker = window._create_ocr_worker([0], only_textless=False)

    assert worker._tier == "heavy"


def test_run_ocr_pages_requests_service_with_configured_tier(tmp_path, monkeypatch):
    pdf_path = tmp_path / "blank.pdf"
    _blank_pdf(pdf_path)
    requested: list = []

    class FakeService:
        def run_ocr_on_page(self, *args, **kwargs):
            return []

    def fake_get_ocr_service(settings=None):
        requested.append(settings)
        return FakeService()

    monkeypatch.setattr(ocr_pipeline, "get_ocr_service", fake_get_ocr_service)
    ocr_pipeline.run_ocr_pages(str(pdf_path), [0], dpi=72, tier="heavy")

    assert requested == [{"tier": "heavy"}]
