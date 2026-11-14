"""PDF 以外を含む結合（PDF に変換して結合）の統合テスト。

Office の変換はモック（実際の Office / LibreOffice は使わない）。.txt・.md・画像は実際に変換する。
"""
from __future__ import annotations

import glob
import shutil
import tempfile
from pathlib import Path

import fitz
import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.utils.pdf_utils import get_pdf_toc
from src.views import main_window, main_window_fileops, main_window_merge, pdf_card
from src.workers import import_worker
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def env(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _p, _s: (QPixmap(), 1))
    asked: list[str] = []

    def _question(*a, **k):
        asked.append(str(a[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    infos: list[str] = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, **k: infos.append(str(a[2]))))
    choices: list[tuple[str, list[str]]] = []
    answers: list[str] = []

    def _ask_choice(self, title, text, labels):
        choices.append((text, list(labels)))
        return answers.pop(0) if answers else labels[0]

    monkeypatch.setattr(main_window_merge.MergeMixin, "_ask_choice", _ask_choice)

    trashed: list[str] = []

    def _fake_trash(path):
        trashed.append(str(path))
        if Path(path).is_dir():
            shutil.rmtree(path)
        else:
            Path(path).unlink()

    monkeypatch.setattr(main_window_fileops, "send2trash", _fake_trash)

    converted_from: list[str] = []

    def fake_office(src, dest, soffice):
        converted_from.append(Path(src).name)
        make_pdf(dest, pages=2)

    monkeypatch.setattr(import_worker, "_convert_office", fake_office)

    work = tmp_path / "work"
    work.mkdir()

    def make_window():
        window = main_window.MainWindow(str(work))
        qtbot.addWidget(window)
        window.show()
        return window

    class Env:
        pass

    e = Env()
    e.work, e.asked, e.infos, e.choices, e.answers = work, asked, infos, choices, answers
    e.trashed, e.converted_from, e.make_window = trashed, converted_from, make_window
    return e


def _select(window, *names):
    window._clear_selection()
    for card in window._cards:
        if card.filename in names:
            card.set_selected(True)
            window._selected_cards.append(card)
    for fc in window._folder_cards:
        if fc.filename in names:
            fc.set_selected(True)
            window._selected_folder_cards.append(fc)
    window._update_button_states()


def _run_merge(window, qtbot):
    window._on_merge_selected()
    worker = window._active_import_worker
    if worker is not None:
        worker.wait()
    QApplication.processEvents()
    qtbot.waitUntil(
        lambda: not window._operation_in_progress and window._active_import_worker is None,
        timeout=15000,
    )
    QApplication.processEvents()


def _conv_tmp_dirs():
    return glob.glob(str(Path(tempfile.gettempdir()) / "justicepdf_merge_conv_*"))


def test_merge_with_conversion_keeps_originals(env, qtbot):
    work = env.work
    make_pdf(work / "a.pdf", pages=1)
    (work / "b.docx").write_bytes(b"D")
    (work / "c.txt").write_text("こんにちは\n世界", encoding="utf-8")
    window = env.make_window()
    before = set(_conv_tmp_dirs())
    _select(window, "a.pdf", "b.docx", "c.txt")
    _run_merge(window, qtbot)

    merged = work / "結合_a.pdf"
    assert merged.exists()
    # PDF に変換して結合する旨と、元が残る旨を確認で伝えている
    assert "PDF に変換して結合" in env.asked[0] and "元のファイルはそのまま" in env.asked[0]
    assert env.converted_from == ["b.docx"]
    with fitz.open(str(merged)) as doc:
        assert doc.page_count == 1 + 2 + 1
    assert [(e.level, e.title, e.page) for e in get_pdf_toc(str(merged))] == [
        (1, "a.pdf", 1), (1, "b.pdf", 2), (1, "c.pdf", 4)]
    # 元は動かさず、隣に変換結果の PDF も残さない
    assert env.trashed == []
    assert sorted(p.name for p in work.iterdir()) == sorted(
        ["a.pdf", "b.docx", "c.txt", "結合_a.pdf"])
    assert set(_conv_tmp_dirs()) == before  # 一時ファイルは片付いている


def test_merge_with_conversion_undo_redo(env, qtbot):
    work = env.work
    make_pdf(work / "a.pdf", pages=1)
    (work / "b.docx").write_bytes(b"D")
    window = env.make_window()
    _select(window, "a.pdf", "b.docx")
    _run_merge(window, qtbot)
    merged = work / "結合_a.pdf"
    assert merged.exists()

    window._undo_manager.undo()
    assert not merged.exists()
    assert (work / "a.pdf").exists() and (work / "b.docx").exists()
    assert window._get_card_by_path(str(merged)) is None

    window._undo_manager.redo()
    assert merged.exists()
    with fitz.open(str(merged)) as doc:
        assert doc.page_count == 3
    assert (work / "a.pdf").exists() and (work / "b.docx").exists()


def test_merge_images_and_markdown_run_for_real(env, qtbot):
    work = env.work
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pix.clear_with(200)
    pix.save(str(work / "a.png"))
    (work / "b.md").write_text("# 見出し\n\n本文", encoding="utf-8")
    window = env.make_window()
    _select(window, "a.png", "b.md")
    _run_merge(window, qtbot)
    merged = work / "結合_a.pdf"
    assert merged.exists()
    assert [(e.level, e.title) for e in get_pdf_toc(str(merged))] == [(1, "a.pdf"), (1, "b.pdf")]
    assert (work / "a.png").exists() and (work / "b.md").exists()


def test_merge_folder_with_non_pdf_recurses_into_bookmarks(env, qtbot):
    work = env.work
    make_pdf(work / "F" / "x.pdf", pages=1)
    (work / "F" / "Sub").mkdir()
    (work / "F" / "Sub" / "m.docx").write_bytes(b"D")
    make_pdf(work / "loose.pdf", pages=1)
    window = env.make_window()
    _select(window, "F", "loose.pdf")
    _run_merge(window, qtbot)
    merged = work / "結合_F.pdf"
    assert [(e.level, e.title, e.page) for e in get_pdf_toc(str(merged))] == [
        (1, "F", 1), (2, "Sub", 1), (3, "m.pdf", 1), (2, "x.pdf", 3), (1, "loose.pdf", 4)]
    assert (work / "F" / "x.pdf").exists() and env.trashed == []


def test_unsupported_items_can_be_skipped_or_cancelled(env, qtbot):
    work = env.work
    make_pdf(work / "a.pdf", pages=1)
    make_pdf(work / "b.pdf", pages=1)
    (work / "z.zip").write_bytes(b"Z")
    window = env.make_window()

    env.answers[:] = ["やめる"]
    _select(window, "a.pdf", "b.pdf", "z.zip")
    _run_merge(window, qtbot)
    assert not (work / "結合_a.pdf").exists() and env.asked == []
    assert env.choices[0][1] == ["抜かして続ける", "やめる"] and "z.zip" in env.choices[0][0]

    env.answers[:] = ["抜かして続ける"]
    _run_merge(window, qtbot)
    merged = work / "結合_a.pdf"
    assert merged.exists()
    with fitz.open(str(merged)) as doc:
        assert doc.page_count == 2
    assert (work / "z.zip").exists() and (work / "a.pdf").exists()  # 元は動かさない
    assert env.trashed == []


def test_pdf_only_merge_still_trashes_sources(env, qtbot):
    work = env.work
    make_pdf(work / "a.pdf")
    make_pdf(work / "b.pdf")
    window = env.make_window()
    _select(window, "a.pdf", "b.pdf")
    _run_merge(window, qtbot)
    assert sorted(Path(p).name for p in env.trashed) == ["a.pdf", "b.pdf"]
    assert "PDF に変換して結合" not in env.asked[0]
    assert env.choices == []


def test_conversion_failure_retry_skip_abort(env, qtbot, monkeypatch):
    work = env.work
    make_pdf(work / "a.pdf", pages=1)
    (work / "b.docx").write_bytes(b"D")
    (work / "c.docx").write_bytes(b"D")
    window = env.make_window()

    attempts: list[str] = []

    def flaky(src, dest, soffice):
        attempts.append(Path(src).name)
        if Path(src).name == "c.docx":
            raise RuntimeError("変換できません")
        make_pdf(dest, pages=2)

    monkeypatch.setattr(import_worker, "_convert_office", flaky)
    before = set(_conv_tmp_dirs())

    # 再試行 → もう一度失敗 → やめる: 何も作らず、一時ファイルも残さない
    env.answers[:] = ["再試行", "やめる"]
    _select(window, "a.pdf", "b.docx", "c.docx")
    _run_merge(window, qtbot)
    assert attempts == ["b.docx", "c.docx", "c.docx"]
    assert not (work / "結合_a.pdf").exists()
    assert set(_conv_tmp_dirs()) == before
    assert env.choices[0][1] == ["再試行", "抜かして続ける", "やめる"]

    # 抜かして続ける: 失敗した c.docx を除いて結合
    attempts.clear()
    env.answers[:] = ["抜かして続ける"]
    _run_merge(window, qtbot)
    merged = work / "結合_a.pdf"
    assert merged.exists()
    with fitz.open(str(merged)) as doc:
        assert doc.page_count == 3
    assert set(_conv_tmp_dirs()) == before
    assert (work / "c.docx").exists() and env.trashed == []


def test_cancel_during_conversion_cleans_temp(env, qtbot, monkeypatch):
    work = env.work
    make_pdf(work / "a.pdf", pages=1)
    (work / "b.docx").write_bytes(b"D")
    window = env.make_window()
    before = set(_conv_tmp_dirs())

    def cancelling(src, dest, soffice):
        window._active_import_worker.request_cancel()
        make_pdf(dest, pages=1)

    monkeypatch.setattr(import_worker, "_convert_office", cancelling)
    _select(window, "a.pdf", "b.docx")
    _run_merge(window, qtbot)
    assert not (work / "結合_a.pdf").exists()
    assert set(_conv_tmp_dirs()) == before
    assert not window._operation_in_progress


def test_item_count_limits(env, qtbot, monkeypatch):
    from src.utils import merge_plan

    work = env.work
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        make_pdf(work / n)
    window = env.make_window()

    # 上限を超える → 中止（確認も出さない）
    monkeypatch.setattr(merge_plan, "MAX_COUNT", 2)
    _select(window, "a.pdf", "b.pdf", "c.pdf")
    _run_merge(window, qtbot)
    assert env.asked == [] and any("上限" in m for m in env.infos)
    assert not (work / "結合_a.pdf").exists()

    # 注意の段 → 確認に一言足して続行
    monkeypatch.setattr(merge_plan, "MAX_COUNT", 1000)
    monkeypatch.setattr(merge_plan, "WARN_COUNT", 2)
    _run_merge(window, qtbot)
    assert "時間がかかり" in env.asked[0]
    assert (work / "結合_a.pdf").exists()


def test_drop_non_pdf_onto_pdf_is_still_not_merged(env, qtbot, monkeypatch):
    """D&D の結合は PDF 同士だけ（PDF 以外のカードは結合先にも結合元にもならない）。"""
    work = env.work
    (work / "a.docx").write_bytes(b"D")
    make_pdf(work / "c.pdf")
    window = env.make_window()
    docx = next(c for c in window._cards if c.filename == "a.docx")
    pdf = next(c for c in window._cards if c.filename == "c.pdf")
    started: list = []
    monkeypatch.setattr(window, "_run_file_operation", lambda *a, **k: started.append(1))
    window._on_card_merge(pdf, str(docx.pdf_path))
    assert started == []
