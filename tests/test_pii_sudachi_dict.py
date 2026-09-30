"""Sudachi辞書の選択(設定・ダイアログ・未導入時のcoreフォールバックと警告)のテスト。"""
from __future__ import annotations

import fitz
import pytest
from PyQt6.QtWidgets import QComboBox

from src.pii import detection_service
from src.pii.detection_service import run_detection
from src.pii.settings import PiiSettings
from src.pii.sudachi_tokenizer import SudachiTokenizer, sudachi_dict_available
from src.views import page_edit_pii as page_edit_pii_module
from src.views import pii_settings_dialog
from src.views.pii_settings_dialog import PiiSettingsDialog
from src.workers.pii_detect_worker import PiiDetectWorker
from tests.helpers import create_page_edit_window

MISSING_DICT = "nosuchdict"  # 導入されていない辞書(環境に依らず必ず読み込めない名前)

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(autouse=True)
def _clean_analyzer_cache():
    detection_service.clear_analyzer_cache()
    yield
    detection_service.clear_analyzer_cache()


def _make_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "090-1234-5678 山田太郎", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def test_dict_type_settings_round_trip():
    settings = PiiSettings()
    settings.sudachi_dict_type = "full"
    settings.save()
    assert PiiSettings.load().sudachi_dict_type == "full"


def test_availability_helper_knows_installed_core_and_missing_dict():
    assert sudachi_dict_available("core") is True
    assert sudachi_dict_available(MISSING_DICT) is False


def test_tokenizer_falls_back_to_core_with_message():
    tokenizer = SudachiTokenizer(dict_type=MISSING_DICT)
    assert tokenizer.fallback_message and MISSING_DICT in tokenizer.fallback_message
    assert tokenizer.tokenize("山田太郎")  # core で実際に分かち書きできる
    assert SudachiTokenizer(dict_type="core").fallback_message is None


def test_detection_with_missing_dict_falls_back_to_core_and_warns(tmp_path):
    pdf_path = tmp_path / "fallback.pdf"
    _make_pdf(pdf_path)
    settings = PiiSettings()
    settings.sudachi_dict_type = MISSING_DICT

    warnings: list[str] = []
    results = run_detection(str(pdf_path), [0], settings, warnings=warnings)

    assert any(r.entity_type == "PERSON" for r in results)  # 形態素解析(core)で検出できた
    assert len(warnings) == 1 and MISSING_DICT in warnings[0]

    # 2回目(Analyzerはキャッシュ済み)でも警告は出る。
    again: list[str] = []
    run_detection(str(pdf_path), [0], settings, warnings=again)
    assert len(again) == 1

    # 通常の辞書では警告なし。
    ok: list[str] = []
    run_detection(str(pdf_path), [0], PiiSettings(), warnings=ok)
    assert ok == []


def test_changing_dict_type_discards_old_analyzer():
    first = PiiSettings()
    detection_service._get_analyzer(first)
    second = PiiSettings()
    second.sudachi_dict_type = MISSING_DICT
    detection_service._get_analyzer(second)
    assert list(detection_service._analyzer_cache) == [(MISSING_DICT, "C")]


def test_worker_emits_warnings_before_finished(tmp_path):
    pdf_path = tmp_path / "worker-warn.pdf"
    _make_pdf(pdf_path)
    settings = PiiSettings()
    settings.sudachi_dict_type = MISSING_DICT

    worker = PiiDetectWorker(str(pdf_path), [0], settings)
    events: list[str] = []
    worker.warnings_raised.connect(lambda msgs: events.append("warn"))
    worker.finished.connect(lambda results: events.append("finished"))
    worker.run()

    assert events == ["warn", "finished"]


def test_window_shows_dictionary_warning_to_user(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "window-warn.pdf"
    _make_pdf(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    shown: list[str] = []
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text: shown.append(text)),
    )

    window._on_pii_detect_warnings(["辞書を読み込めませんでした"])

    qtbot.waitUntil(lambda: bool(shown), timeout=2000)
    assert "辞書を読み込めませんでした" in shown[0]


def test_dialog_disables_uninstalled_dicts(qtbot, monkeypatch):
    monkeypatch.setattr(
        pii_settings_dialog, "sudachi_dict_available", lambda t: t == "core"
    )
    dialog = PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog)

    combo: QComboBox = dialog._sudachi_dict_combo
    items = {combo.itemData(i): combo.model().item(i) for i in range(combo.count())}
    assert set(items) == {"small", "core", "full"}
    assert items["core"].isEnabled()
    assert not items["small"].isEnabled() and "未インストール" in items["small"].text()
    assert not items["full"].isEnabled() and "未インストール" in items["full"].text()
    assert combo.currentData() == "core"
    assert dialog.result_settings().sudachi_dict_type == "core"


def test_dialog_selects_and_saves_installed_dict(qtbot, monkeypatch):
    monkeypatch.setattr(pii_settings_dialog, "sudachi_dict_available", lambda t: True)
    settings = PiiSettings()
    settings.sudachi_dict_type = "full"
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog._sudachi_dict_combo.currentData() == "full"

    dialog._sudachi_dict_combo.setCurrentIndex(dialog._sudachi_dict_combo.findData("small"))
    assert dialog.result_settings().sudachi_dict_type == "small"


def test_dialog_keeps_stored_uninstalled_dict(qtbot, monkeypatch):
    """設定に残った未導入の辞書は、勝手にcoreへ書き換えず(実行時にフォールバックする)そのまま保つ。"""
    monkeypatch.setattr(
        pii_settings_dialog, "sudachi_dict_available", lambda t: t == "core"
    )
    settings = PiiSettings()
    settings.sudachi_dict_type = "full"
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog._sudachi_dict_combo.currentData() == "full"
    assert dialog.result_settings().sudachi_dict_type == "full"
