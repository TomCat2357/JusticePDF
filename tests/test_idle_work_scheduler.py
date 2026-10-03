"""アイドル時間のバックグラウンド処理(IdleInputMonitor / IdleWorkScheduler / SearchIndexJob)。"""
from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.utils import app_settings
from src.utils.pdf_utils import common
from src.utils.pdf_utils.annotations import NoteAnnotData, create_note_annot
from src.views.idle_work_scheduler import IdleInputMonitor, IdleWorkScheduler, SearchIndexJob
from src.views.idle_work_scheduler import (
    InkScanJob,
    PiiScanJob,
    ThumbPrefetchJob,
    ZoomPrerenderJob,
)
from src.views.page_edit_window import ZoomPageLayout
from tests.helpers import create_page_edit_window, open_zoom


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class SpyJob:
    name = "spy"

    def __init__(self) -> None:
        self.steps = 0
        self.resets = 0
        self.want = True

    def wants_run(self) -> bool:
        return self.want

    def step(self, deadline: float) -> bool:
        self.steps += 1
        return False

    def reset(self) -> None:
        self.resets += 1


def _make_text_pdf(path, pages: int) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=200)
        text = f"alpha page {i} " + ("needle" if i % 10 == 3 else "hay")
        page.insert_text((20, 50), text, fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def idle_on():
    """マスターを入れる(ウィンドウ構築中はまだ切っておき、実タイマーが先に仕事を進めないようにする)。"""
    yield


def _window(qtbot, tmp_path, pages=40, prep=None):
    path = tmp_path / "idle.pdf"
    _make_text_pdf(path, pages)
    if prep is not None:
        prep(path)
    app_settings.set_idle_work_enabled(False)
    window = create_page_edit_window(qtbot, path)
    qtbot.wait(50)
    assert window._page_count == pages
    qtbot.waitUntil(
        lambda: not window._thumb_render_queue and not window._thumb_render_timer.isActive(),
        timeout=15000,
    )
    window._idle_scheduler._timer.stop()
    app_settings.set_idle_work_enabled(True)
    return window, path


def _spy_scheduler(window, monitor=None):
    spy = SpyJob()
    sched = IdleWorkScheduler(window, [spy], monitor=monitor or IdleInputMonitor(clock=FakeClock()))
    sched._started = True
    sched._token = sched._current_token()
    return sched, spy


def _note(path, page_num):
    create_note_annot(
        str(path),
        NoteAnnotData(page_num=page_num, xref=0, point=(20, 20), content="n", color=(1, 1, 0)),
    )


# --- IdleInputMonitor ---


def test_monitor_input_event_resets_idle(qtbot):
    clock = FakeClock()
    monitor = IdleInputMonitor(clock=clock)
    IdleInputMonitor.set_instance(monitor)
    try:
        clock.now += 5.0
        assert monitor.idle_for() == pytest.approx(5.0)
        widget = QWidget()
        qtbot.addWidget(widget)
        ev = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(widget, ev)
        assert monitor.idle_for() == pytest.approx(0.0)
        clock.now += 1.5
        assert monitor.idle_for() == pytest.approx(1.5)
        monitor.force_idle()
        assert monitor.idle_for() > 1e6
    finally:
        IdleInputMonitor.set_instance(None)


def test_arm_delay_math(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=3)
    clock = FakeClock()
    monitor = IdleInputMonitor(clock=clock)
    sched = IdleWorkScheduler(window, [SpyJob()], monitor=monitor)
    sched._started = True
    monitor.note_input()
    clock.now += 0.2
    sched._arm()
    assert sched._timer.interval() == 500  # 0.7 - 0.2 秒
    clock.now += 0.495  # 残り 5ms は gap(12ms)に切り上げ
    sched._arm()
    assert sched._timer.interval() == 12
    clock.now += 10
    sched._arm()
    assert sched._timer.interval() == 12  # 0ms の自己ループにはしない
    sched.force_idle()
    monitor.note_input()
    sched._arm()
    assert sched._timer.interval() == 12
    sched.stop()


# --- ゲート ---


def test_gates_block_slice(qtbot, tmp_path, idle_on, monkeypatch):
    window, _ = _window(qtbot, tmp_path, pages=5)
    sched, spy = _spy_scheduler(window)
    sched.force_idle()

    sched._tick()
    assert spy.steps == 1  # 全ゲート開
    assert sched._timer.isActive()

    def blocked(label):
        before = spy.steps
        sched._timer.stop()
        sched._tick()
        assert spy.steps == before, label
        assert sched._timer.isActive() and sched._timer.interval() == sched.POLL_MS, label

    window._thumb_render_queue.append(1)
    blocked("thumb queue")
    window._thumb_render_queue.clear()

    window._search_scan = object()
    blocked("search scan")
    window._search_scan = None

    with monkeypatch.context() as m:
        m.setattr(window, "_grid_slider_down", lambda: True)
        blocked("slider")

    with monkeypatch.context() as m:
        m.setattr(QApplication, "activeModalWidget", staticmethod(lambda: window))
        blocked("modal")

    window._pii_restyle_job = object()
    blocked("pii restyle")
    window._pii_restyle_job = None

    before = spy.steps
    sched._tick()
    assert spy.steps == before + 1  # 解除後は再び動く

    # 設定のマスタースイッチ: 動かず、タイマーも張り直さない
    app_settings.set_idle_work_enabled(False)
    before = spy.steps
    sched._timer.stop()
    sched._tick()
    assert spy.steps == before and not sched._timer.isActive()
    sched.stop()


def test_active_window_and_idle_gates(qtbot, tmp_path, idle_on, monkeypatch):
    window, _ = _window(qtbot, tmp_path, pages=5)
    clock = FakeClock()
    monitor = IdleInputMonitor(clock=clock)
    sched, spy = _spy_scheduler(window, monitor)
    # 強制なし: 入力直後はアイドル不足で動かない
    monkeypatch.setattr(sched, "_window_is_active", lambda: True)
    monitor.note_input()
    sched._tick()
    assert spy.steps == 0 and sched._timer.interval() == 700
    clock.now += 0.8
    sched._tick()
    assert spy.steps == 1
    # アクティブでなければ動かない
    monkeypatch.setattr(sched, "_window_is_active", lambda: False)
    sched._tick()
    assert spy.steps == 1
    sched.stop()


def test_token_change_resets_all(qtbot, tmp_path, idle_on):
    window, path = _window(qtbot, tmp_path, pages=5)
    sched, spy = _spy_scheduler(window)
    sched.force_idle()
    _note(path, 1)
    sched._tick()
    assert spy.resets == 1 and spy.steps == 0
    assert sched._timer.isActive()
    sched._tick()
    assert spy.resets == 1 and spy.steps == 1
    sched.stop()


def test_no_rearm_when_no_job_wants(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=5)
    sched, spy = _spy_scheduler(window)
    sched.force_idle()
    spy.want = False
    sched._tick()
    assert not sched._timer.isActive() and spy.steps == 0
    spy.want = True
    sched.poke()
    assert sched._timer.isActive()
    sched.stop()


# --- SearchIndexJob ---


def _count_extract(monkeypatch) -> list:
    calls: list = []
    orig = fitz.TextPage.extractText

    def counting(self, *a, **kw):
        calls.append(1)
        return orig(self, *a, **kw)

    monkeypatch.setattr(fitz.TextPage, "extractText", counting)
    return calls


def _plain_search(path, query):
    out = {}
    with fitz.open(str(path)) as doc:
        for i in range(len(doc)):
            rects = doc[i].search_for(query)
            if rects:
                out[i] = [tuple(r) for r in rects]
    return out


def test_search_index_job_drain_then_search_extracts_nothing(qtbot, tmp_path, idle_on, monkeypatch):
    window, path = _window(qtbot, tmp_path, pages=40)
    sched = window._idle_scheduler
    sched.force_idle()
    assert sched.drain() >= 1
    index = window._search_index
    assert index is not None and index.cached_count() == 40
    assert sched.run_slice() is None  # もうやることが無い
    window._on_open_search()
    calls = _count_extract(monkeypatch)
    window._on_search_execute("needle")
    assert window._search_scan is None
    assert calls == []  # 抽出ゼロ
    expected = _plain_search(path, "needle")
    assert window._search_hit_pages == sorted(expected)
    assert {p: [tuple(r) for r in window._search_hits[p]] for p in window._search_hits} == expected
    window._on_search_execute("zzzqq")
    assert window._search_hit_pages == [] and calls == []


def test_search_index_job_outward_order(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=5)
    job = SearchIndexJob(window)
    assert job._outward_order(5, 8) == [5, 6, 4, 7, 3, 2, 1, 0]
    assert job._outward_order(0, 3) == [0, 1, 2]
    assert job._outward_order(7, 8)[:3] == [7, 6, 5]
    assert sorted(job._outward_order(7, 8)) == list(range(8))


def test_annotation_write_invalidates_only_changed_pages(qtbot, tmp_path, idle_on, monkeypatch):
    window, path = _window(qtbot, tmp_path, pages=30)
    sched = window._idle_scheduler
    sched.force_idle()
    sched.drain()
    assert window._search_index.cached_count() == 30
    _note(path, 2)
    sched._tick()  # トークン変化 → reset_all
    assert sched._token == sched._current_token()
    calls = _count_extract(monkeypatch)
    sched.drain()
    assert len(calls) == 1  # 変わったページだけ取り直す
    assert window._search_index.cached_count() == 30


def test_page_count_mismatch_stops_job(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=10)
    job = window._idle_scheduler.jobs[0]
    window._page_count = 12  # 索引とファイルのページ数がずれた状況
    window._search_index = None
    assert job.step(float("inf")) is True
    assert window._search_index is None
    assert not job.wants_run()
    window._page_count = 10


def test_job_setting_disables_search_index(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=5)
    app_settings.set_idle_search_index_enabled(False)
    assert window._idle_scheduler.run_slice() is None


def test_held_docs_released_after_drain(qtbot, tmp_path, idle_on):
    window, path = _window(qtbot, tmp_path, pages=20)
    sched = window._idle_scheduler
    sched.force_idle()
    sched.drain()
    assert any(k[1] == str(path) for k in common._held_docs)
    assert window._held_doc_idle_timer.isActive()
    window._held_doc_idle_timer.timeout.emit()  # 3 秒待たずに解放を発火
    assert not any(k[1] == str(path) for k in common._held_docs)


def test_close_stops_scheduler(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=5)
    sched = window._idle_scheduler
    sched.poke()
    assert sched._timer.isActive()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window.close()
    assert not sched._timer.isActive()
    sched.poke()
    assert not sched._timer.isActive()


# --- ZoomPrerenderJob ---


def _count_fitz_renders(monkeypatch) -> list:
    calls: list = []
    orig = fitz.Page.get_pixmap

    def counting(self, *a, **kw):
        calls.append(self.number)
        return orig(self, *a, **kw)

    monkeypatch.setattr(fitz.Page, "get_pixmap", counting)
    return calls


def _job_of(window, cls):
    return next(j for j in window._idle_scheduler.jobs if isinstance(j, cls))


def _drain_job(window, job, limit=5000):
    n = 0
    while job.wants_run() and n < limit:
        job.step(float("inf"))
        n += 1
    assert n < limit
    return n


def test_zoom_prerender_next_page_hits_cache(qtbot, tmp_path, idle_on, monkeypatch):
    # 次ページに注釈があっても(隠す集合が非空でも)ライブ描画と同じキーに当たる
    window, _ = _window(qtbot, tmp_path, pages=6, prep=lambda p: (_note(p, 1), _note(p, 0)))
    open_zoom(window, qtbot)
    sched = window._idle_scheduler
    sched._timer.stop()
    sched.force_idle()
    job = _job_of(window, ZoomPrerenderJob)
    assert job.wants_run()
    names = []
    while (name := sched.run_slice()) is not None:
        names.append(name)
    assert names.count("zoom_prerender") == 2  # 次ページの画像・文字(検索索引が先に終わる)
    assert 1 in window._zoom_text_cache
    calls = _count_fitz_renders(monkeypatch)
    window._on_zoom_next_page()
    assert window._zoom_page_num == 1
    assert calls == []  # 次ページは描画キャッシュから
    window._on_zoom_prev_page()  # 前ページ(0)は元々ライブ描画済み
    assert calls == []


def test_zoom_prerender_prev_page(qtbot, tmp_path, idle_on, monkeypatch):
    window, _ = _window(qtbot, tmp_path, pages=5)
    open_zoom(window, qtbot)
    window._zoom_page_num = 4
    window._render_zoom()
    sched = window._idle_scheduler
    sched._timer.stop()
    sched.force_idle()
    sched.drain()
    calls = _count_fitz_renders(monkeypatch)
    window._on_zoom_prev_page()
    assert window._zoom_page_num == 3 and calls == []


def test_zoom_prerender_not_wanted_in_multi_layout_or_grid(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=6)
    job = _job_of(window, ZoomPrerenderJob)
    assert not job.wants_run()  # ページ一覧表示中
    open_zoom(window, qtbot)
    assert job.wants_run()
    multi = next(layout for layout in ZoomPageLayout if layout is not ZoomPageLayout.SINGLE)
    window._zoom_page_layout = multi
    assert not job.wants_run()
    window._zoom_page_layout = ZoomPageLayout.SINGLE
    assert job.wants_run()
    app_settings.set_idle_zoom_prerender_enabled(False)
    assert not job.wants_run()


def test_zoom_prerender_targets_recomputed_on_zoom_and_ink(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=6)
    open_zoom(window, qtbot)
    sched = window._idle_scheduler
    sched._timer.stop()
    sched.force_idle()
    job = _job_of(window, ZoomPrerenderJob)
    _drain_job(window, job)
    assert not job.wants_run()
    window._set_zoom_percent(int(round(window._zoom_factor * 100)) + 25)
    sched._timer.stop()
    assert job.wants_run()
    _drain_job(window, job)
    assert not job.wants_run()
    window._on_toggle_ink_visibility(not window._show_ink_annots)
    sched._timer.stop()
    assert job.wants_run()


# --- PiiScanJob / InkScanJob ---


def _heavy_window(qtbot, tmp_path):
    window, path = _window(qtbot, tmp_path, pages=305)
    assert window._is_heavy_document
    return window, path


def _spy_scanner(monkeypatch, name):
    import src.views.page_edit_window as mod

    scans: list = []
    orig = getattr(mod, name)
    monkeypatch.setattr(
        mod, name, lambda p, pages=None: (scans.append(None if pages is None else list(pages)), orig(p, pages))[1]
    )
    return scans


def test_pii_scan_job_completes_and_later_full_call_does_not_rescan(qtbot, tmp_path, idle_on, monkeypatch):
    window, _ = _heavy_window(qtbot, tmp_path)
    window._pii_targets_by_page_cache = None
    window._pii_targets_scanned_pages = None
    scans = _spy_scanner(monkeypatch, "list_pii_targets_by_page")
    sched = window._idle_scheduler
    sched.force_idle()
    job = _job_of(window, PiiScanJob)
    assert job.wants_run()
    _drain_job(window, job)
    assert window._pii_targets_by_page_cache is not None
    assert window._pii_targets_scanned_pages is None
    assert sorted(p for pages in scans if pages for p in pages) == list(range(305))
    n = len(scans)
    window._get_pii_targets_by_page()  # pages=None: 再走査しない
    window._get_pii_targets_by_page([1, 2, 3])
    assert len(scans) == n
    assert not job.wants_run()


def test_pii_scan_job_token_change_rescans_changed_page_only(qtbot, tmp_path, idle_on, monkeypatch):
    window, path = _heavy_window(qtbot, tmp_path)
    job = _job_of(window, PiiScanJob)
    _drain_job(window, job)
    assert window._pii_targets_scanned_pages is None
    _note(path, 7)
    scans = _spy_scanner(monkeypatch, "list_pii_targets_by_page")
    window._get_pii_targets_by_page([0])  # 変更ページだけ取り直す(全ページは走査し直さない)
    assert scans == [[7]]
    assert window._pii_targets_scanned_pages is None


def test_ink_scan_job_only_when_ink_hidden(qtbot, tmp_path, idle_on, monkeypatch):
    window, _ = _heavy_window(qtbot, tmp_path)
    window._ink_xrefs_by_page_cache = None
    window._ink_xrefs_scanned_pages = None
    job = _job_of(window, InkScanJob)
    window._show_ink_annots = True
    assert not job.wants_run()
    window._show_ink_annots = False
    assert job.wants_run()
    scans = _spy_scanner(monkeypatch, "list_ink_annot_xrefs_by_page")
    _drain_job(window, job)
    assert window._ink_xrefs_scanned_pages is None and window._ink_xrefs_by_page_cache is not None
    n = len(scans)
    window._get_ink_xrefs_by_page()
    assert len(scans) == n
    window._ink_xrefs_by_page_cache = None  # 他の経路でキャッシュが捨てられたら再び対象になる
    assert job.wants_run()


def test_scan_jobs_not_wanted_for_light_documents(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=5)
    window._show_ink_annots = False
    assert not _job_of(window, PiiScanJob).wants_run()
    assert not _job_of(window, InkScanJob).wants_run()


def test_scan_job_zero_budget_still_progresses_partially(qtbot, tmp_path, idle_on):
    import time as _t

    window, _ = _heavy_window(qtbot, tmp_path)
    window._pii_targets_by_page_cache = None
    window._pii_targets_scanned_pages = None
    job = _job_of(window, PiiScanJob)
    assert job.step(_t.perf_counter()) is False
    scanned = window._pii_targets_scanned_pages
    assert scanned and len(scanned) < 305


def test_scan_job_setting_off(qtbot, tmp_path, idle_on):
    window, _ = _heavy_window(qtbot, tmp_path)
    window._pii_targets_by_page_cache = None
    app_settings.set_idle_pii_scan_enabled(False)
    assert not _job_of(window, PiiScanJob).wants_run()


# --- ThumbPrefetchJob ---


def test_thumb_prefetch_renders_beyond_prefetch_range(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=420)
    assert not window._prerender_all_pages()
    window._idle_scheduler.force_idle()
    job = _job_of(window, ThumbPrefetchJob)
    assert job.wants_run()
    p0, p1 = window._grid.prefetch_page_range()
    v0, v1 = window._grid.visible_page_range(0)
    visible_before = [p for p in range(v0, v1) if p in window._rendered]
    assert visible_before
    for _ in range(12):
        assert job.wants_run()
        job.step(float("inf"))
    beyond = [p for p in window._rendered if not (p0 <= p < p1)]
    assert len(beyond) >= 12
    assert len(window._rendered) <= window._rendered_cap()
    for p in visible_before:
        assert p in window._rendered
    # 先読みで描いたものは追い出し順の先頭
    assert set(list(window._rendered)[: len(beyond)]) == set(beyond)


def test_thumb_prefetch_stops_at_headroom_and_never_evicts_visible(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=420)
    window._idle_scheduler.force_idle()
    job = _job_of(window, ThumbPrefetchJob)
    v0, v1 = window._grid.visible_page_range(0)
    visible = [p for p in range(v0, v1) if p in window._rendered]
    _drain_job(window, job)
    cap = window._rendered_cap()
    p0, p1 = window._grid.prefetch_page_range()
    assert len(window._rendered) <= cap
    outside = [p for p in window._rendered if not (p0 <= p < p1)]
    assert len(outside) <= cap - (p1 - p0) - 8
    for p in visible:
        assert p in window._rendered
    assert not job.wants_run()


def test_thumb_prefetch_restarts_when_range_changes(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=1200)
    window._idle_scheduler.force_idle()
    job = _job_of(window, ThumbPrefetchJob)
    _drain_job(window, job)
    assert not job.wants_run()
    bar = window._grid_scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    qtbot.waitUntil(
        lambda: not window._thumb_render_queue and not window._scroll_debounce_timer.isActive(),
        timeout=15000,
    )
    assert job.wants_run()
    _drain_job(window, job)
    assert len(window._rendered) <= window._rendered_cap()
    p0, p1 = window._grid.prefetch_page_range()
    assert any(p >= p1 for p in window._rendered) or p1 >= window._page_count
    assert all(p in window._rendered for p in range(*window._grid.visible_page_range(0)))


def test_thumb_prefetch_not_wanted_for_small_docs(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=10)
    assert window._prerender_all_pages()
    assert not _job_of(window, ThumbPrefetchJob).wants_run()


def test_render_thumbnail_batch_populates_rendered(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=10)
    window._rendered.clear()
    window._render_thumbnail_batch([2, 3])
    assert 2 in window._rendered and 3 in window._rendered


def test_poke_rearms_after_master_turned_on(qtbot, tmp_path, idle_on):
    window, _ = _window(qtbot, tmp_path, pages=5)
    sched = window._idle_scheduler
    app_settings.set_idle_work_enabled(False)
    sched._timer.stop()
    sched._tick()  # 設定オフ: 張り直さない
    assert not sched._timer.isActive()
    app_settings.set_idle_work_enabled(True)
    sched.poke()  # 設定ダイアログ保存後に main_window が呼ぶ
    assert sched._timer.isActive()
