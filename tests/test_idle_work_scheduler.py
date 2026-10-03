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
from tests.helpers import create_page_edit_window


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


def _window(qtbot, tmp_path, pages=40):
    path = tmp_path / "idle.pdf"
    _make_text_pdf(path, pages)
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
