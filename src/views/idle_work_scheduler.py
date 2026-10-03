"""操作していない間(アイドル中)に先回りして行うバックグラウンド処理のスケジューラ。

ユーザー操作の邪魔をしないことを最優先にする:

- 入力(マウス・キー・ホイール・タブレット・タッチ)が最後にあってから一定時間(既定0.7秒)
  経つまで何もしない。入力の記録はアプリ全体で1つの ``IdleInputMonitor`` が行う。
- 1回の処理は約25msの「スライス」で区切り、スライスの間に必ずイベントループへ戻る
  (再開は約12msの間隔を空け、0msの自己ループにしない)。
- ウィンドウが前面で、モーダルが無く、マウスボタンが押されておらず、サムネイル描画・検索走査・
  PII注釈の書き込みが動いていないときだけ進める。
- 文書は ``hold_doc(linger=True)`` の中で開く(ファイルのメモリ上の写しでハンドルは掴まない)。
  スライスごとにウィンドウの ``_held_doc_idle_timer`` を再始動するので、処理が止まって一定時間
  経つと写しは自動で閉じられる。

ジョブ(``IdleJob``)は優先順に並べて渡す。実行待ちのジョブが無いときはタイマーを再始動せず、
``poke()`` / ``reset_all()`` / ファイル更新の検出 / ``start()`` で再び動き出す。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication

from src.utils import app_settings
from src.utils.pdf_utils.common import _get_file_cache_token, _open_doc, hold_doc
from src.utils.pdf_utils.search_index import fill_index_page

if TYPE_CHECKING:
    from src.views.page_edit_window import PageEditWindow


def _input_event_types() -> frozenset:
    names = (
        "MouseMove",
        "MouseButtonPress",
        "MouseButtonRelease",
        "MouseButtonDblClick",
        "Wheel",
        "KeyPress",
        "KeyRelease",
        "TabletMove",
        "TabletPress",
        "TabletRelease",
        "TouchBegin",
        "TouchUpdate",
        "TouchEnd",
    )
    return frozenset(getattr(QEvent.Type, n) for n in names if hasattr(QEvent.Type, n))


_INPUT_EVENT_TYPES = _input_event_types()


class IdleInputMonitor(QObject):
    """アプリ全体の最後の入力時刻を記録する(プロセスで1つ)。"""

    _instance: "IdleInputMonitor | None" = None

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._clock = clock
        self._last_input = clock()
        self._installed_on: "QApplication | None" = None

    @classmethod
    def instance(cls) -> "IdleInputMonitor":
        if cls._instance is None:
            cls._instance = cls()
        cls._instance.ensure_installed()
        return cls._instance

    @classmethod
    def set_instance(cls, monitor: "IdleInputMonitor | None") -> None:
        """テスト用: 共有インスタンスを差し替える(None で破棄)。"""
        old = cls._instance
        if old is not None and old is not monitor:
            old.uninstall()
        cls._instance = monitor
        if monitor is not None:
            monitor.ensure_installed()

    def ensure_installed(self) -> None:
        app = QApplication.instance()
        if app is None or self._installed_on is app:
            return
        app.installEventFilter(self)
        self._installed_on = app

    def uninstall(self) -> None:
        app = self._installed_on
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass
        self._installed_on = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() in _INPUT_EVENT_TYPES:
            self._last_input = self._clock()
        return False

    def note_input(self) -> None:
        self._last_input = self._clock()

    def idle_for(self) -> float:
        """最後の入力からの経過秒数。"""
        return max(0.0, self._clock() - self._last_input)

    def force_idle(self) -> None:
        """テスト用: 十分長くアイドルだったことにする。"""
        self._last_input = self._clock() - 1e9


class IdleJob(Protocol):
    name: str

    def wants_run(self) -> bool:
        """今やるべき仕事があるか(軽い判定)。"""

    def step(self, deadline: float) -> bool:
        """*deadline*(perf_counter 基準)まで進める。もうやることが無ければ True。"""

    def reset(self) -> None:
        """状態を破棄して最初から見直す(ファイル更新・再読み込み時)。"""


class IdleWorkScheduler(QObject):
    """ウィンドウごとの、アイドル時間のバックグラウンド処理スケジューラ。"""

    POLL_MS = 200  # ゲートで止められているときの再確認間隔

    def __init__(
        self,
        window: "PageEditWindow",
        jobs: Sequence[IdleJob],
        *,
        clock: Callable[[], float] = time.perf_counter,
        idle_s: float = 0.7,
        budget_s: float = 0.025,
        gap_ms: int = 12,
        monitor: "IdleInputMonitor | None" = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._jobs = list(jobs)
        self._clock = clock
        self._idle_s = idle_s
        self._budget_s = budget_s
        self._gap_ms = max(1, int(gap_ms))
        self._monitor = monitor
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._started = False
        self._stopped = False
        self._forced = False
        self._token = None
        self._worked = False  # 直近でスライスを実行した(保持中の文書を最終的に手放すため)

    # --- 公開API ---

    @property
    def jobs(self) -> list:
        return self._jobs

    def is_armed(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self._stopped:
            return
        if self._monitor is None:
            self._monitor = IdleInputMonitor.instance()
        self._started = True
        self._token = self._current_token()
        self._arm()

    def stop(self) -> None:
        self._stopped = True
        self._started = False
        self._timer.stop()

    def poke(self) -> None:
        """状況が変わった(検索終了・スクロール停止・ズーム移動など)。止まっていれば再開を試みる。"""
        if self._started and not self._stopped and not self._timer.isActive():
            self._arm()

    def reset_all(self) -> None:
        for job in self._jobs:
            job.reset()
        self._token = self._current_token()
        if self._started:
            self._arm()

    def force_idle(self) -> None:
        """テスト用: アイドル判定とアクティブウィンドウ判定を無視する。"""
        self._forced = True

    def _arm(self, delay_ms: "int | None" = None) -> None:
        if self._stopped:
            return
        self._timer.start(self._next_delay_ms() if delay_ms is None else max(1, int(delay_ms)))

    def _next_delay_ms(self) -> int:
        """次の判定までの待ち時間。アイドルまでの残りがあればそれに合わせ、最低でも gap_ms。"""
        remaining = 0.0
        if not self._forced and self._monitor is not None:
            remaining = self._idle_s - self._monitor.idle_for()
        if remaining > 0:
            return max(self._gap_ms, int(math.ceil(remaining * 1000)))
        return self._gap_ms

    # --- 実行 ---

    def _current_token(self):
        try:
            return _get_file_cache_token(self._window._pdf_path)
        except Exception:  # noqa: BLE001 - ファイルが無い等でも落とさない
            return None

    def _pick_job(self) -> "IdleJob | None":
        for job in self._jobs:
            if job.wants_run():
                return job
        return None

    def _gates_open(self) -> bool:
        """設定以外の「今は動かしてよいか」の判定(アイドル時間は別)。"""
        w = self._window
        if not w.isVisible() or w.isMinimized():
            return False
        if not self._forced and not self._window_is_active():
            return False
        if QApplication.activeModalWidget() is not None:
            return False
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return False
        if w._grid_slider_down():
            return False
        if w._thumb_render_queue or w._thumb_render_timer.isActive() or w._scroll_debounce_timer.isActive():
            return False
        if w._search_scan is not None:
            return False
        if self._pii_restyle_busy():
            return False
        return True

    def _window_is_active(self) -> bool:
        active = QApplication.activeWindow()
        w = self._window
        while active is not None:
            if active is w:
                return True
            active = active.parentWidget()
        return False

    def _pii_restyle_busy(self) -> bool:
        w = self._window
        if getattr(w, "_pii_restyle_job", None) is not None:
            return True
        for name in ("_pii_restyle_timer", "_pii_restyle_tick_timer"):
            timer = getattr(w, name, None)
            if timer is not None and timer.isActive():
                return True
        return False

    def _tick(self) -> None:
        if self._stopped or not self._started:
            return
        if not app_settings.idle_work_enabled():
            return  # poke() / reset_all() / start() で再開する
        token = self._current_token()
        if token != self._token:
            self.reset_all()
            return
        job = self._pick_job()
        if job is None:
            self._finish_work()
            return
        if not self._gates_open():
            self._arm(self.POLL_MS)
            return
        if not self._forced and self._monitor is not None:
            idle = self._monitor.idle_for()
            if idle < self._idle_s:
                self._arm()
                return
        self._run_job(job)
        if self._stopped:
            return
        if self._pick_job() is not None:
            self._arm(self._gap_ms)
        else:
            self._finish_work()

    def _finish_work(self) -> None:
        """やることが無くなった。保持した文書の解放を予約する(再始動は poke 等に任せる)。"""
        if self._worked:
            self._worked = False
            self._window._held_doc_idle_timer.start()

    def _run_job(self, job: IdleJob) -> bool:
        w = self._window
        self._worked = True
        deadline = self._clock() + self._budget_s
        try:
            with hold_doc(w._pdf_path, linger=True):
                done = job.step(deadline)
        finally:
            w._held_doc_idle_timer.start()
        return bool(done)

    # --- テスト用フック ---

    def run_slice(self) -> "str | None":
        """ゲートを見ずに1スライス実行する。実行したジョブ名(無ければ None)を返す。"""
        job = self._pick_job()
        if job is None:
            return None
        self._run_job(job)
        return job.name

    def drain(self, max_slices: int = 100_000) -> int:
        """実行待ちのジョブが無くなるまでスライスを繰り返す。実行したスライス数を返す。"""
        n = 0
        while n < max_slices and self.run_slice() is not None:
            n += 1
        return n


class SearchIndexJob:
    """検索用のページ別テキスト索引(``PageTextIndex``)を全ページ分あらかじめ作る。

    抽出と正規化は ``SearchScan`` と同じ ``fill_index_page`` を使う。表示中のページ
    (拡大ビューならそのページ、一覧なら先頭の見えているページ)から外側へ向かって埋める。
    """

    name = "search_index"

    def __init__(self, window: "PageEditWindow") -> None:
        self._w = window
        self._complete = False
        self._order: "list[int] | None" = None
        self._pos = 0

    def reset(self) -> None:
        self._complete = False
        self._order = None
        self._pos = 0

    def wants_run(self) -> bool:
        w = self._w
        if self._complete or w._page_count <= 0:
            return False
        return app_settings.idle_search_index_enabled()

    def _start_page(self) -> int:
        w = self._w
        zoom_view = getattr(w, "_zoom_view", None)
        if w._zoom_page_num is not None and zoom_view is not None and zoom_view.isVisible():
            return w._zoom_page_num
        first = w._grid.first_visible_page()
        return first if first is not None else 0

    @staticmethod
    def _outward_order(start: int, count: int) -> list[int]:
        start = min(max(start, 0), count - 1)
        order = [start]
        for d in range(1, count):
            hi, lo = start + d, start - d
            if hi < count:
                order.append(hi)
            if lo >= 0:
                order.append(lo)
            if hi >= count and lo < 0:
                break
        return order

    def step(self, deadline: float) -> bool:
        w = self._w
        index = w._search_index_for_current()
        if not index.can_store():
            self._complete = True
            return True
        if self._order is None or len(self._order) != index.page_count:
            self._order = self._outward_order(self._start_page(), index.page_count)
            self._pos = 0
        order = self._order
        texts = index.texts
        with _open_doc(w._pdf_path) as doc:
            if len(doc) != index.page_count:
                w._search_index = None
                self.reset()
                self._complete = True  # ページ構成の変更は _load_pages → reset_all で再開する
                return True
            while self._pos < len(order):
                i = order[self._pos]
                self._pos += 1
                if texts[i] is not None:
                    continue
                fill_index_page(index, doc[i], i)
                if not index.can_store():
                    self._complete = True
                    return True
                if time.perf_counter() >= deadline:
                    return False
        self._complete = True
        return True
