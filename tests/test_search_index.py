"""検索の絞り込み索引(PageTextIndex/SearchScan)と、PageEditWindow のチャンク検索のテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils.common import _get_file_cache_token, _record_write
from src.utils.pdf_utils.search_index import (
    PageTextIndex,
    SearchScan,
    normalize_for_search,
)
from tests.helpers import create_page_edit_window, make_pdf

QUERIES = [
    "example", "EXAMPLE", "Example", "exam-ple", "exam ple", "the quick", "quick brown",
    "brown fox jumps", "fi", "file", "office", "ABC", "abc", "ＡＢＣ", "ａｂｃ", "a",
    "e", "-", " ", "x y", "hello world", "world hello", "ねこ", "日本語", "日本 語",
    "テスト", "ﾃｽﾄ", "ｶﾀｶﾅ", "カタカナ", "ガ", "ｶﾞ", "改行をまた", "また ぐ", "zzzqq",
    "co-operate", "cooperate", "Straße", "strasse", "İstanbul", "ß",
]


def _build_doc(path) -> None:
    doc = fitz.open()

    def new(lines, fontname="helv", fontsize=11):
        page = doc.new_page(width=400, height=500)
        y = 40
        for line in lines:
            page.insert_text((30, y), line, fontname=fontname, fontsize=fontsize)
            y += 18
        return page

    new(["This is an Example of mixed CASE text.", "The quick brown fox", "jumps over the lazy dog"])
    new(["A hyphenated exam-", "ple spans two lines.", "co-", "operate with us"])
    new(["words split across", "lines like hello", "world and more"])
    new(["ABC abc Abc", "and spaces x   y here"])
    new(["office file affix"])
    page = doc.new_page(width=400, height=500)
    # 全角/半角・日本語(組み込み CJK フォント)
    for k, line in enumerate(
        ["ＡＢＣ ａｂｃ ＥＸＡＭＰＬＥ", "日本語のテストです ねこ", "ｶﾀｶﾅ カタカナ ﾃｽﾄ ガ ｶﾞ", "改行をまた", "ぐ文章です"]
    ):
        page.insert_text((30, 40 + 18 * k), line, fontname="japan", fontsize=11)
    new(["Straße and STRASSE", "İstanbul ß"], fontname="helv")
    doc.new_page()  # 空ページ
    doc.save(str(path))
    doc.close()


def _plain_search(path, query) -> dict[int, list]:
    doc = fitz.open(str(path))
    try:
        out = {}
        for i in range(len(doc)):
            r = doc[i].search_for(query)
            if r:
                out[i] = [tuple(x) for x in r]
        return out
    finally:
        doc.close()


def _indexed_search(path, query, index=None, budget=None) -> dict[int, list]:
    doc = fitz.open(str(path))
    try:
        index = index or PageTextIndex(len(doc), None)
        scan = SearchScan(index, query, len(doc))
        out = {}
        while not scan.done:
            for p, rects in scan.step(lambda: doc, budget):
                out[p] = [tuple(x) for x in rects]
        return out
    finally:
        doc.close()


def test_normalize_for_search():
    assert normalize_for_search("Ｅxam-\nple ﬁ") == "ｅxamplefi"
    assert normalize_for_search("ﾃｽﾄ") == "ﾃｽﾄ"  # 半角カナは半角のまま(NFKC しない)
    assert normalize_for_search("a­b‐c‑d e") == "abcde"
    assert normalize_for_search(" - ") == ""


def test_flags_match_search_for_defaults():
    import inspect

    from src.utils.pdf_utils.search_index import SEARCH_TEXT_FLAGS

    assert SEARCH_TEXT_FLAGS == 83
    src = inspect.getsource(fitz.Page.search_for)
    assert "TEXT_DEHYPHENATE" in src and "TEXT_PRESERVE_WHITESPACE" in src
    assert "TEXT_PRESERVE_LIGATURES" in src and "TEXT_MEDIABOX_CLIP" in src


@pytest.fixture(scope="module")
def tricky_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("srch") / "tricky.pdf"
    _build_doc(path)
    return path


@pytest.mark.parametrize("query", QUERIES)
def test_indexed_search_equals_plain_search_for(tricky_pdf, query):
    expected = _plain_search(tricky_pdf, query)
    assert _indexed_search(tricky_pdf, query) == expected


def test_indexed_search_reuses_cache_and_stays_exact(tricky_pdf):
    doc = fitz.open(str(tricky_pdf))
    index = PageTextIndex(len(doc), None)
    doc.close()
    for query in QUERIES:  # 同じ索引を使い回しても結果は変わらない
        assert _indexed_search(tricky_pdf, query, index) == _plain_search(tricky_pdf, query)
    assert index.cached_count() == index.page_count


def test_prefilter_actually_filters(tricky_pdf, monkeypatch):
    calls = {"search_for": 0}
    orig = fitz.Page.search_for

    def counting(self, *a, **k):
        calls["search_for"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(fitz.Page, "search_for", counting)
    doc = fitz.open(str(tricky_pdf))
    index = PageTextIndex(len(doc), None)
    doc.close()
    _indexed_search(tricky_pdf, "zzzqq", index)
    assert calls["search_for"] == 0


def test_scan_respects_time_budget_and_resumes(tricky_pdf):
    expected = _plain_search(tricky_pdf, "e")
    assert _indexed_search(tricky_pdf, "e", budget=0.0) == expected


def test_index_cap_falls_back_to_direct_search(tricky_pdf, monkeypatch):
    monkeypatch.setattr("src.utils.pdf_utils.search_index.MAX_INDEX_CHARS", 0)
    assert _indexed_search(tricky_pdf, "the quick") == _plain_search(tricky_pdf, "the quick")


# ---------------------------------------------------------------------------
# PageEditWindow
# ---------------------------------------------------------------------------

def _make_text_pdf(path, pages, text_for):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 50), text_for(i), fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def chunked(monkeypatch):
    """小さい文書でもチャンク検索になるようにし、1ティックを数ページに絞る。"""
    from src.views.page_edit_window import PageEditWindow

    monkeypatch.setattr(PageEditWindow, "SEARCH_SYNC_MAX_PAGES", 5)
    monkeypatch.setattr(PageEditWindow, "SEARCH_TICK_BUDGET_S", 0.0)


def _window(qtbot, tmp_path, pages=120, every=10):
    path = tmp_path / "s.pdf"
    _make_text_pdf(path, pages, lambda i: f"alpha page {i} " + ("needle" if i % every == 3 else "hay"))
    window = create_page_edit_window(qtbot, path)
    qtbot.wait(50)  # 初回の _load_pages(QTimer.singleShot(0))を済ませる
    assert window._page_count == pages
    window._on_open_search()
    return window, path


def _wait_done(qtbot, window):
    qtbot.waitUntil(lambda: window._search_scan is None, timeout=15000)


def test_small_document_search_completes_synchronously(qtbot, tmp_path):
    window, _ = _window(qtbot, tmp_path, pages=40)
    window._on_search_execute("needle")
    assert window._search_scan is None
    assert window._search_hit_pages == [3, 13, 23, 33]
    assert window._search_current == 3
    assert window._search_dialog._status_label.text() == "ページ 1 / 4"
    window._on_search_execute("nothing-here")
    assert window._search_dialog._status_label.text() == "見つかりません"
    assert window._search_hit_pages == []


def test_chunked_search_reports_progress_and_jumps_once(qtbot, tmp_path, chunked, monkeypatch):
    window, path = _window(qtbot, tmp_path)
    jumps = []
    orig = window._jump_to_search_page
    monkeypatch.setattr(window, "_jump_to_search_page", lambda p: (jumps.append(p), orig(p))[1])
    window._on_search_execute("needle")
    assert window._search_scan is not None  # まだ走査中
    assert "検索中" in window._search_dialog._status_label.text()
    seen_partial = []
    qtbot.waitUntil(
        lambda: (seen_partial.append(len(window._search_hit_pages)), window._search_scan is None)[1],
        timeout=15000,
    )
    expected = sorted(_plain_search(path, "needle"))
    assert window._search_hit_pages == expected
    assert jumps == [expected[0]]
    assert any(0 < n < len(expected) for n in seen_partial) or len(expected) == len(window._search_hit_pages)
    assert window._search_dialog._status_label.text() == f"ページ 1 / {len(expected)}"
    assert window._search_hit_set == set(expected)
    assert set(window._search_hits) == set(expected)


def test_new_query_cancels_old_one(qtbot, tmp_path, chunked):
    window, path = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    assert window._search_scan is not None
    window._on_search_execute("page 7 ")
    _wait_done(qtbot, window)
    assert window._search_hit_pages == sorted(_plain_search(path, "page 7"))


def test_load_pages_and_clear_cancel_search(qtbot, tmp_path, chunked):
    window, _ = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    assert window._search_scan is not None
    window._load_pages()
    assert window._search_scan is None and window._search_index is None
    assert window._search_hit_pages == []
    window._on_search_execute("needle")
    window._clear_search_highlights()
    assert window._search_scan is None and not window._search_timer.isActive()


def test_cancel_button_keeps_partial_results(qtbot, tmp_path, chunked):
    window, _ = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    window._search_timer.stop()
    window._search_step()
    window._search_step()
    window._on_search_cancel()
    assert window._search_scan is None
    assert not window._search_timer.isActive()
    text = window._search_dialog._status_label.text()
    assert text.endswith("ページ") or text == "見つかりません"
    assert "検索中" not in text


def test_repeat_search_uses_cache_without_get_text(qtbot, tmp_path, chunked, monkeypatch):
    window, path = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    _wait_done(qtbot, window)
    first = dict(window._search_hits)
    assert window._search_index.cached_count() == window._page_count

    calls = {"n": 0}
    orig = fitz.TextPage.extractText

    def counting(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(fitz.TextPage, "extractText", counting)
    window._on_search_execute("needle")
    _wait_done(qtbot, window)
    assert calls["n"] == 0
    assert {k: [tuple(r) for r in v] for k, v in window._search_hits.items()} == {
        k: [tuple(r) for r in v] for k, v in first.items()
    }
    window._on_search_execute("hay")
    _wait_done(qtbot, window)
    assert calls["n"] == 0
    assert window._search_hit_pages == sorted(_plain_search(path, "hay"))


def test_annotation_write_invalidates_only_changed_pages(qtbot, tmp_path, chunked, monkeypatch):
    window, path = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    _wait_done(qtbot, window)
    index = window._search_index
    old_token = index.token
    # 注釈だけを書き込む操作(書き込み履歴に記録される)を再現する
    window._release_held_doc()
    doc = fitz.open(str(path))
    doc[5].insert_text((20, 120), "needle added", fontname="helv", fontsize=12)
    before = _get_file_cache_token(str(path))
    doc.saveIncr()
    doc.close()
    _record_write(str(path), before, [5])
    assert _get_file_cache_token(str(path)) != old_token
    # ここでは走査前なので索引のトークンを実ファイルの直前状態へ合わせる
    index.token = before

    calls = {"n": 0}
    orig = fitz.TextPage.extractText
    monkeypatch.setattr(
        fitz.TextPage, "extractText", lambda self, *a, **k: (calls.__setitem__("n", calls["n"] + 1), orig(self, *a, **k))[1]
    )
    window._on_search_execute("needle")
    _wait_done(qtbot, window)
    assert window._search_index is index  # 作り直していない
    assert calls["n"] == 1  # 変更した1ページだけ再抽出
    assert window._search_hit_pages == sorted(_plain_search(path, "needle"))
    assert 5 in window._search_hit_pages


def test_write_during_search_restarts_with_correct_results(qtbot, tmp_path, chunked):
    window, path = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    window._search_timer.stop()
    window._search_step()
    window._release_held_doc()
    doc = fitz.open(str(path))
    doc[100].insert_text((20, 120), "needle extra", fontname="helv", fontsize=12)
    doc.saveIncr()  # 履歴に無い書き込み → 索引は破棄される
    doc.close()
    window._search_step()  # トークン不一致を検出して最初からやり直す
    window._search_timer.start()
    _wait_done(qtbot, window)
    assert window._search_hit_pages == sorted(_plain_search(path, "needle"))
    assert 100 in window._search_hit_pages


def test_zoom_view_draws_hit_rects_after_chunked_search(qtbot, tmp_path, chunked):
    window, path = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    _wait_done(qtbot, window)
    first = window._search_hit_pages[0]
    window._zoom_page_num = first
    assert window._search_hits[first]
    window._jump_to_search_page(first)
    assert window._search_hits.get(window._zoom_page_num)


# ---------------------------------------------------------------------------
# 回帰: NFKC で文字をまたいで合成すると部分一致を取りこぼす
# ---------------------------------------------------------------------------

def _single_page_pdf(path, lines_fonts):
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    y = 40
    for text, font in lines_fonts:
        page.insert_text((30, y), text, fontname=font, fontsize=11)
        y += 20
    doc.save(str(path))
    doc.close()


@pytest.mark.parametrize(
    "text,font,queries",
    [
        ("ｶﾞ", "japan", ["ｶ", "ﾞ", "ｶﾞ"]),
        ("ガ", "japan", ["カ", "゙"]),
        ("ㄱㅏ", "korea", ["ㄱ", "ㅏ"]),
        ("й", "helv", ["и", "̆"]),
    ],
)
def test_no_false_negative_for_composable_sequences(tmp_path, text, font, queries):
    path = tmp_path / "c.pdf"
    _single_page_pdf(path, [(text, font)])
    for q in queries:
        assert _indexed_search(path, q) == _plain_search(path, q), q


def test_prefilter_never_misses_a_search_for_hit_fuzz(tricky_pdf):
    import random

    doc = fitz.open(str(tricky_pdf))
    flags = (
        fitz.TEXT_DEHYPHENATE | fitz.TEXT_PRESERVE_WHITESPACE
        | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP
    )
    rng = random.Random(1234)
    checked = 0
    try:
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text("text", flags=flags)
            norm = normalize_for_search(text)
            if not text.strip():
                continue
            for _ in range(80):
                a = rng.randrange(len(text))
                b = min(len(text), a + rng.randint(1, 6))
                q = text[a:b].replace("\n", " ").strip()
                if not q:
                    continue
                if page.search_for(q):
                    checked += 1
                    assert normalize_for_search(q) in norm, (i, q)
    finally:
        doc.close()
    assert checked > 50


def test_scan_uses_one_textpage_per_page(tricky_pdf, monkeypatch):
    calls = {"tp": 0}
    orig = fitz.TextPage.extractText

    def counting(self, *a, **k):
        calls["tp"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(fitz.TextPage, "extractText", counting)
    doc = fitz.open(str(tricky_pdf))
    n = len(doc)
    doc.close()
    _indexed_search(tricky_pdf, "e")
    assert calls["tp"] == n


# ---------------------------------------------------------------------------
# ウィンドウ: 再入・書き込み時の差分更新
# ---------------------------------------------------------------------------

def test_scan_cancelled_during_jump_stops_old_scan(qtbot, tmp_path, chunked, monkeypatch):
    window, path = _window(qtbot, tmp_path)
    started = []

    def jump(_page):
        if not started:
            started.append(1)
            window._invalidate_search_results()

    monkeypatch.setattr(window, "_jump_to_search_page", jump)
    window._on_search_execute("needle")
    qtbot.wait(200)
    assert window._search_scan is None
    assert window._search_hit_pages == []
    assert not window._search_timer.isActive()


def test_new_query_during_jump_wins(qtbot, tmp_path, chunked, monkeypatch):
    window, path = _window(qtbot, tmp_path)
    started = []
    orig = window._jump_to_search_page

    def jump(page):
        if not started:
            started.append(1)
            window._on_search_execute("page 7 ")
        else:
            orig(page)

    monkeypatch.setattr(window, "_jump_to_search_page", jump)
    window._on_search_execute("needle")
    _wait_done(qtbot, window)
    assert window._search_hit_pages == sorted(_plain_search(path, "page 7"))


def test_journaled_write_mid_scan_updates_only_changed_pages(qtbot, tmp_path, chunked):
    window, path = _window(qtbot, tmp_path)
    window._on_search_execute("needle")
    window._search_timer.stop()
    for _ in range(24):
        window._search_step()
    scan = window._search_scan
    assert scan is not None and 20 < scan.next_page < window._page_count
    removed = window._search_hit_pages[0]
    window._release_held_doc()
    window._end_search_hold()
    doc = fitz.open(str(path))
    for r in doc[removed].search_for("needle"):
        doc[removed].add_redact_annot(r)
    doc[removed].apply_redactions()
    doc[7].insert_text((20, 120), "needle added", fontname="helv", fontsize=12)
    before = _get_file_cache_token(str(path))
    doc.saveIncr()
    doc.close()
    _record_write(str(path), before, [removed, 7])

    window._search_step()
    assert window._search_scan is scan  # やり直していない
    assert removed not in window._search_hits and 7 in window._search_hits
    assert window._search_hit_pages == sorted(window._search_hit_pages)
    window._search_timer.start()
    _wait_done(qtbot, window)
    assert window._search_hit_pages == sorted(_plain_search(path, "needle"))
    assert window._search_hit_pages == sorted(window._search_hit_set)
