"""PiiDetectWorker のシグナル配線テスト(.run()を直接呼び、スレッド無しで検証)。"""
from __future__ import annotations

import fitz
import pytest

from src.pii.detection_service import PiiDetection
from src.pii.settings import PiiSettings
from src.workers.pii_detect_worker import PiiDetectWorker

pytestmark = pytest.mark.usefixtures("qapp")


def _make_pii_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "090-1234-5678", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def test_worker_emits_progress_and_finished(tmp_path):
    pdf_path = tmp_path / "worker.pdf"
    _make_pii_pdf(pdf_path)

    worker = PiiDetectWorker(str(pdf_path), [0], PiiSettings())
    progress_calls: list[tuple[int, int]] = []
    finished_results: list[list[PiiDetection]] = []
    worker.progress.connect(lambda done, total: progress_calls.append((done, total)))
    worker.finished.connect(finished_results.append)

    # run() を直接呼ぶことでQThreadの実スレッド起動を避け、同期的に検証する。
    worker.run()

    assert progress_calls == [(1, 1)]
    assert len(finished_results) == 1
    results = finished_results[0]
    assert any(r.entity_type == "PHONE_NUMBER" for r in results)


def test_worker_emits_error_on_failure(tmp_path, monkeypatch):
    from src.workers import pii_detect_worker

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pii_detect_worker, "run_detection", boom)

    worker = PiiDetectWorker(str(tmp_path / "missing.pdf"), [0], PiiSettings())
    errors: list[Exception] = []
    worker.error.connect(errors.append)
    worker.run()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
