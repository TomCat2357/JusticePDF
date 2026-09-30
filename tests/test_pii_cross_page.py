"""ページをまたぐ語の検出(PiiSettings.cross_page_detection)のテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.pii.detection_service import CROSS_PAGE_WINDOW, run_detection
from src.pii.settings import PiiSettings


def _make_pdf(path, page_texts: list[str]) -> None:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=400, height=200)
        if text:
            page.insert_text((40, 100), text, fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def _settings(cross: bool) -> PiiSettings:
    settings = PiiSettings()
    settings.cross_page_detection = cross
    return settings


def test_setting_defaults_on_and_round_trips():
    assert PiiSettings().cross_page_detection is True
    settings = PiiSettings()
    settings.cross_page_detection = False
    settings.save()
    assert PiiSettings.load().cross_page_detection is False


def test_phone_number_split_across_pages_is_detected_on_both_pages(tmp_path):
    pdf_path = tmp_path / "split-phone.pdf"
    _make_pdf(pdf_path, ["連絡先は 090-1234", "-5678 です"])

    results = run_detection(str(pdf_path), [0, 1], _settings(True))

    phones = [r for r in results if r.entity_type == "PHONE_NUMBER"]
    assert sorted(r.page_num for r in phones) == [0, 1]
    # どちらの断片も、断片ではなくマッチ全体の文字列を持つ。
    assert {r.text.replace(" ", "") for r in phones} == {"090-1234-5678"}
    # quad は自分のページの文字の範囲に収まる。
    assert all(r.quads for r in phones)


def test_person_name_split_across_pages_is_detected_on_both_pages(tmp_path):
    pdf_path = tmp_path / "split-name.pdf"
    _make_pdf(pdf_path, ["担当者は山", "田太郎です"])

    results = run_detection(str(pdf_path), [0, 1], _settings(True))

    people = [r for r in results if r.entity_type == "PERSON" and "田" in r.text and "山" in r.text]
    assert sorted(r.page_num for r in people) == [0, 1]
    # 辞書によって「山田」または「山田太郎」として検出される。どちらでも両ページの断片が同じ全体文字列を持つ。
    assert len({r.text for r in people}) == 1
    assert next(iter({r.text for r in people})).startswith("山田")


def test_non_consecutive_pages_are_not_joined(tmp_path):
    pdf_path = tmp_path / "gap.pdf"
    _make_pdf(pdf_path, ["連絡先は 090-1234", "関係のないページ", "-5678 です"])

    results = run_detection(str(pdf_path), [0, 2], _settings(True))

    assert [r for r in results if r.entity_type == "PHONE_NUMBER"] == []


def test_setting_off_matches_page_by_page_detection(tmp_path):
    pdf_path = tmp_path / "off.pdf"
    _make_pdf(pdf_path, ["連絡先は 090-1234", "-5678 です"])

    off = run_detection(str(pdf_path), [0, 1], _settings(False))
    per_page = [
        r
        for page in (0, 1)
        for r in run_detection(str(pdf_path), [page], _settings(False))
    ]

    assert [r for r in off if r.entity_type == "PHONE_NUMBER"] == []
    assert off == per_page


def test_single_page_result_is_identical_with_and_without_cross_page(tmp_path):
    pdf_path = tmp_path / "single.pdf"
    _make_pdf(pdf_path, ["電話 090-1234-5678 山田太郎"])

    assert run_detection(str(pdf_path), [0], _settings(True)) == run_detection(
        str(pdf_path), [0], _settings(False)
    )


def test_progress_reports_every_page_once_and_long_runs_are_windowed(tmp_path):
    pages = CROSS_PAGE_WINDOW * 2 + 3
    pdf_path = tmp_path / "long.pdf"
    texts = [f"{i}ページ" for i in range(pages)]
    texts[CROSS_PAGE_WINDOW - 1] = "連絡先は 090-1234"  # 窓の境界をまたぐ電話番号
    texts[CROSS_PAGE_WINDOW] = "-5678 です"
    _make_pdf(pdf_path, texts)

    calls: list[tuple[int, int]] = []
    results = run_detection(
        str(pdf_path),
        list(range(pages)),
        _settings(True),
        progress_callback=lambda done, total: calls.append((done, total)),
    )

    assert calls == [(i, pages) for i in range(1, pages + 1)]
    phones = [r for r in results if r.entity_type == "PHONE_NUMBER"]
    assert sorted(r.page_num for r in phones) == [CROSS_PAGE_WINDOW - 1, CROSS_PAGE_WINDOW]


def test_unreadable_page_does_not_stop_other_pages(tmp_path, monkeypatch):
    from src.pii import detection_service

    pdf_path = tmp_path / "broken.pdf"
    _make_pdf(pdf_path, ["連絡先は 090-1234", "-5678 です", "電話 080-1111-2222"])
    original = detection_service.get_page_text_and_chars

    def flaky(path, page_num):
        if page_num == 1:
            raise RuntimeError("boom")
        return original(path, page_num)

    monkeypatch.setattr(detection_service, "get_page_text_and_chars", flaky)
    calls: list[tuple[int, int]] = []
    results = run_detection(
        str(pdf_path),
        [0, 1, 2],
        _settings(True),
        progress_callback=lambda done, total: calls.append((done, total)),
    )

    assert calls[-1] == (3, 3) and len(calls) == 3
    assert [r.page_num for r in results if r.entity_type == "PHONE_NUMBER"] == [2]


def test_dialog_has_cross_page_checkbox(qapp, qtbot):
    from src.views.pii_settings_dialog import PiiSettingsDialog

    settings = PiiSettings()
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog._cross_page_check.text() == "ページをまたぐ語も検出する"
    assert dialog._cross_page_check.isChecked()

    dialog._cross_page_check.setChecked(False)
    assert dialog.result_settings().cross_page_detection is False


def test_word_spanning_into_overlap_page_is_not_duplicated_by_next_window(tmp_path, monkeypatch):
    from src.pii import detection_service

    # 窓1=[p0,p1,p2]の重なりページはp2。地名がp1末尾からp2先頭へまたがる。窓2=[p2,p3,p4]。
    # 修正前は窓2がp2の「代田」だけを部分行として再検出していた。
    pdf_path = tmp_path / "overlap.pdf"
    _make_pdf(pdf_path, ["表紙", "住所は東京都千", "代田区です", "関係なし", "終わり"])

    monkeypatch.setattr(detection_service, "CROSS_PAGE_WINDOW", 100)
    expected = run_detection(str(pdf_path), [0, 1, 2, 3, 4], _settings(True))
    monkeypatch.setattr(detection_service, "CROSS_PAGE_WINDOW", 3)
    results = run_detection(str(pdf_path), [0, 1, 2, 3, 4], _settings(True))

    assert results == expected  # 窓で区切っても、連続範囲を一度に解析した結果と同じ
    locations = [r for r in results if r.entity_type == "LOCATION"]
    assert sorted(r.page_num for r in locations) == [1, 2]  # 各ページ1件ずつ、部分行なし
    assert len({r.text for r in locations}) == 1
