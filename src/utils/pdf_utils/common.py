"""pdf_utils共通基盤: 書き込み権限エラー・保存プリミティブ・ピクスマップキャッシュ。"""
import itertools
import logging
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

import fitz
from PyQt6.QtGui import QPixmap

from src.utils import app_settings
from src.utils.constants import PIXMAP_CACHE_MAX_ENTRIES



logger = logging.getLogger(__name__)


def is_heavy_pdf(pdf_path: str, page_count: int | None = None) -> bool:
    """ページ数またはファイルサイズが閾値を超える「重量文書」かどうか判定する。

    しきい値は設定ダイアログでユーザーが変更でき(``app_settings``)、
    未設定なら ``src.utils.constants`` の既定値にフォールバックする。
    ``page_count`` が既に分かっていれば再取得せずに使う(呼び出し側で
    ``get_page_count`` 済みのことが多いため、二重に PDF を開かない)。
    """
    if page_count is not None and page_count > app_settings.heavy_pdf_page_count_threshold():
        return True
    try:
        if os.path.getsize(pdf_path) > app_settings.heavy_pdf_file_size_bytes():
            return True
    except OSError:
        pass
    return False


class PdfWritePermissionError(PermissionError):
    """Raised when a PDF cannot be overwritten because another app is using it."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        super().__init__(13, f"Permission denied: '{pdf_path}'", pdf_path)




class _PixmapCache:
    def __init__(self, maxsize: int = 256):
        self._maxsize = max(1, int(maxsize))
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()

    def get(self, key: tuple) -> QPixmap | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: tuple, pixmap: QPixmap) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = pixmap
            return
        if len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = pixmap

    def clear(self) -> None:
        self._cache.clear()

    def clear_for_path(self, pdf_path: str) -> None:
        keys_to_remove = [k for k in self._cache if k[0] == pdf_path]
        for k in keys_to_remove:
            del self._cache[k]

    def set_maxsize(self, maxsize: int) -> None:
        """上限件数を変更する(設定ダイアログからの即時反映用)。

        縮小した場合は古いエントリから追い出して、すぐに新しい上限へ従う。
        """
        self._maxsize = max(1, int(maxsize))
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)


# 起動時の実際の上限値は、QApplication 生成後に main.py が
# set_pixmap_cache_max_entries(app_settings.pixmap_cache_max_entries()) を
# 呼んで反映する(QSettings は QApplication の組織名/アプリ名設定に依存する
# ため、モジュール読み込み時点ではまだ呼び出せない)。ここでは素の既定値で
# 初期化しておく。
_pixmap_cache = _PixmapCache(maxsize=PIXMAP_CACHE_MAX_ENTRIES)


def clear_pixmap_cache_for_path(pdf_path: str) -> None:
    _pixmap_cache.clear_for_path(pdf_path)


def set_pixmap_cache_max_entries(maxsize: int) -> None:
    """設定で変更されたキャッシュ上限件数を、実行中のキャッシュへ即時反映する。"""
    _pixmap_cache.set_maxsize(maxsize)


def clear_pixmap_cache() -> None:
    _pixmap_cache.clear()


def _is_permission_denied_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return True
        if isinstance(current, OSError) and getattr(current, "errno", None) == 13:
            return True
        message = str(current).lower()
        if "permission denied" in message or "access is denied" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


# 注釈の書き込み履歴: パス -> [(保存前トークン, 保存後トークン, 変更ページ集合)]。
# 画面側のキャッシュ(PII対象のページ別集計など)が「自分の書き込みで変わったページだけ」
# 再計算できるようにするためのもの。トークンが連鎖しない(=外部変更やページ構成の変更が
# 挟まった)場合は利用側が全ページ再計算にフォールバックする。
_WRITE_JOURNAL_MAX = 64
_write_journal: dict[str, list[tuple[tuple[int, int, int], tuple[int, int, int], frozenset[int]]]] = {}


def _journal_key(pdf_path: str) -> str:
    return os.path.normcase(os.path.abspath(pdf_path))


def pages_changed_since(
    pdf_path: str, token: tuple[int, int, int]
) -> "frozenset[int] | None":
    """*token* の時点から現在までの変更が、すべて記録済みの注釈書き込みなら変更ページ集合を返す。

    現在のファイルと同じトークンなら空集合。履歴が途切れていて追跡できない場合は None
    (呼び出し側は全ページ再計算する)。
    """
    current = _get_file_cache_token(pdf_path)
    if token == current:
        return frozenset()
    entries = _write_journal.get(_journal_key(pdf_path))
    if not entries:
        return None
    pages: set[int] = set()
    cursor = token
    progressed = True
    while cursor != current and progressed:
        progressed = False
        for before, after, changed in entries:
            if before == cursor and after != cursor:
                pages |= changed
                cursor = after
                progressed = True
                break
    if cursor != current:
        return None
    return frozenset(pages)


def _record_write(
    pdf_path: str,
    before: tuple[int, int, int],
    changed_pages: "Iterable[int]",
) -> None:
    after = _get_file_cache_token(pdf_path)
    entries = _write_journal.setdefault(_journal_key(pdf_path), [])
    entries.append((before, after, frozenset(int(p) for p in changed_pages)))
    if len(entries) > _WRITE_JOURNAL_MAX:
        del entries[: len(entries) - _WRITE_JOURNAL_MAX]


def _save_document_in_place(
    doc: fitz.Document,
    pdf_path: str,
    *,
    incremental: bool = False,
    changed_pages: "Iterable[int] | None" = None,
) -> None:
    """Persist a modified document.

    When *incremental* is True, tries ``saveIncr()`` first for speed
    (append-only, no rewrite).  Falls back to full save on failure.
    When False (default), uses full save with garbage collection to
    prevent file growth from repeated annotation edits.

    *changed_pages* (注釈だけを書き換えた操作が渡す) を指定すると、保存前後のファイル
    トークンとともに書き込み履歴へ記録する(``pages_changed_since`` 参照)。
    """
    token_before = _get_file_cache_token(pdf_path) if changed_pages is not None else None
    session = get_session(pdf_path)
    if session is not None and session.doc is doc:
        # 手動保存モード: 保持中のドキュメントへの変更なのでディスクには書かず、
        # 「未保存」の印(世代)だけ進める。世代はファイルトークンに反映されるため、
        # 下の書き込み履歴・各種キャッシュの無効化は自動保存モードと同じ仕組みで連鎖する。
        session.mark_dirty()
        _pixmap_cache.clear_for_path(pdf_path)
    else:
        _release_session_before_disk_write(pdf_path)
        _save_document_in_place_impl(doc, pdf_path, incremental=incremental)
    if changed_pages is not None and token_before is not None:
        _record_write(pdf_path, token_before, changed_pages)


def _save_document_in_place_impl(
    doc: fitz.Document, pdf_path: str, *, incremental: bool = False
) -> None:
    if incremental:
        try:
            doc.saveIncr()
            _pixmap_cache.clear_for_path(pdf_path)
            return
        except Exception as error:
            if _is_permission_denied_error(error):
                raise PdfWritePermissionError(pdf_path) from error
            # Fall through to full save

    tmp_path: str | None = None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        doc.save(tmp_path, garbage=1, deflate=True)
    except Exception as error:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if _is_permission_denied_error(error):
            raise PdfWritePermissionError(pdf_path) from error
        raise
    try:
        shutil.move(tmp_path, pdf_path)
    except Exception as move_error:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if _is_permission_denied_error(move_error):
            raise PdfWritePermissionError(pdf_path) from move_error
        raise
    _pixmap_cache.clear_for_path(pdf_path)



def _get_file_cache_token(pdf_path: str) -> tuple[int, int, int]:
    """キャッシュ無効化用のトークン。ファイルの実体か、手動保存セッションの世代で変わる。

    セッション(メモリ上で編集中のドキュメント)があるときは、ディスク上のファイルが変わらなくても
    編集のたびに変わる世代ベースのトークンを返す。これでピクスマップ・PII集計などの
    キャッシュが未保存の編集内容に対して正しく無効化される。
    """
    session = get_session(pdf_path)
    if session is not None and session.doc is not None:
        return (-1, session.generation, session.uid & 0xFFFF)
    return _get_disk_file_token(pdf_path)


def _get_disk_file_token(pdf_path: str) -> tuple[int, int, int]:
    """ディスク上のファイルの状態だけで決まるトークン(セッションは考慮しない)。"""
    try:
        stat_result = os.stat(pdf_path)
    except OSError:
        return (0, 0, 0)
    return (
        int(getattr(stat_result, "st_mtime_ns", 0)),
        int(stat_result.st_size),
        int(getattr(stat_result, "st_ctime_ns", 0)),
    )



# ---------------------------------------------------------------------------
# 手動保存モード用の編集セッション
#
# 1つの編集ウィンドウ = 1セッション。パス(正規化済み)をキーにしたレジストリへ登録すると、
# 登録後は pdf_utils の「注釈・描画」系関数が ``fitz.open(path)`` の代わりに ``_open_doc(path)`` を
# 通じて、保持している同じ ``fitz.Document`` を使い回す。書き込み(``_save_document_in_place``)は
# ディスクへ出さずメモリ上の変更+「未保存」の印だけにし、``PdfSession.save()`` で初めて書く。
# 大きいPDFでは1回の増分保存が約1秒かかるため、これを操作のたびに払わないのが目的。
#
# 守ること:
#   * 保持中のドキュメントは UI スレッド(owner_thread)専用。ほかのスレッドからは
#     従来どおりパスから別に開く(ディスクの状態が見える)。
#   * ``garbage>=2`` で保存してはいけない(保持中ドキュメントの xref を付け替えてしまい、
#     以後の増分保存や Undo の xref 参照が壊れる)。このモジュールの全体保存は garbage=1 のみ。
#   * Windows では保持中のハンドルがあるとファイルの置き換え・改名・ゴミ箱送りが
#     PermissionError になる。ディスクへ別経路で書く前には ``release()``/``close()`` で閉じる。
# ---------------------------------------------------------------------------
class PdfSessionError(RuntimeError):
    """セッションの使い方の誤り(未保存のまま閉じる・二重登録など)。"""


class PdfSession:
    """メモリ上で編集中の PDF 1つ分。"""

    _uid_counter = itertools.count(1)

    def __init__(self, pdf_path: str):
        self.raw_path = pdf_path
        self.path = _journal_key(pdf_path)
        self.uid = next(PdfSession._uid_counter)
        self.owner_thread = threading.get_ident()
        self.generation = 0
        self.saved_generation = 0
        self.size_at_open = _safe_getsize(pdf_path)
        self.doc: "fitz.Document | None" = fitz.open(pdf_path)
        # 最後に読み込んだ/書き込んだ時点のディスク上のファイルの状態。外部で書き換えられたかの判定用。
        self._disk_token = _get_disk_file_token(pdf_path)
        # 全体保存の置き換えに失敗したときだけ使う退避ファイル(保持内容の写し)。
        self._backing_tmp: str | None = None
        self._dirty_listeners: list[Callable[[], None]] = []
        # 未保存のまま別経路でディスクへ書かれそうなときに呼ばれる。保存できたら True を返す。
        # (編集ウィンドウが「保存しますか?」を出す。無ければ書き込みは拒否される)
        self.before_external_write: Callable[[], bool] | None = None

    # --- 状態 -----------------------------------------------------------
    def dirty(self) -> bool:
        return self.generation != self.saved_generation

    def add_dirty_listener(self, callback: Callable[[], None]) -> None:
        """未保存状態が変わったとき(未保存になった・保存した)に呼ばれる。"""
        if callback not in self._dirty_listeners:
            self._dirty_listeners.append(callback)

    def remove_dirty_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._dirty_listeners:
            self._dirty_listeners.remove(callback)

    def _notify_dirty_changed(self) -> None:
        for callback in list(self._dirty_listeners):
            try:
                callback()
            except Exception:
                logger.exception("PdfSession dirty listener failed")

    def mark_dirty(self) -> None:
        """保持中のドキュメントを変更した印を付ける(ディスクには書かない)。"""
        was_dirty = self.dirty()
        self.generation += 1
        _pixmap_cache.clear_for_path(self.raw_path)
        _pixmap_cache.clear_for_path(self.path)
        if not was_dirty:
            self._notify_dirty_changed()

    # --- 保存 -----------------------------------------------------------
    def save(self) -> None:
        """未保存の変更をファイルへ書く。まず増分保存、だめなら全体保存(garbage=1)へ。"""
        if self.doc is None or not self.dirty():
            return
        generation = self.generation
        doc = self.doc
        incremental_ok = False
        if self._backing_tmp is None:
            try:
                incremental_ok = bool(doc.can_save_incrementally())
            except Exception:
                incremental_ok = False
        if incremental_ok:
            try:
                doc.saveIncr()
                # 同じハンドルで続けて saveIncr すると、2回目以降の増分更新の xref が壊れ、
                # 次に開いたときに「修復された」ファイルになる(実測)。保存のたびに開き直す。
                self._reopen_after_incremental_save()
                self._mark_saved(generation)
                return
            except Exception as error:
                if _is_permission_denied_error(error):
                    raise PdfWritePermissionError(self.raw_path) from error
                logger.debug("saveIncr failed, falling back to full save", exc_info=True)
        self._full_save()
        self._mark_saved(generation)

    def _reopen_after_incremental_save(self) -> None:
        """増分保存した直後にハンドルを開き直す(内容・xref は同じなので世代は進めない)。

        開き直せなかったときは doc を空にしておく(次に読まれたとき ``_attach`` がディスクから開き直す)。
        """
        old = self.doc
        self.doc = None
        if old is not None:
            try:
                old.close()
            except Exception:
                logger.debug("failed to close doc after incremental save", exc_info=True)
        try:
            self.doc = fitz.open(self.raw_path)
        except Exception:
            logger.warning("failed to reopen after incremental save: %s", self.raw_path, exc_info=True)
            self.doc = None

    def disk_changed_externally(self) -> bool:
        """最後に読み込み/保存した後で、ディスク上のファイルが(自分以外に)書き換えられたか。"""
        return _get_disk_file_token(self.raw_path) != self._disk_token

    def _mark_saved(self, generation: int) -> None:
        was_dirty = self.dirty()
        self.saved_generation = generation
        self._disk_token = _get_disk_file_token(self.raw_path)
        # ファイルの実体は変わったが、保持中ドキュメントの内容は同じなので世代は進めない
        # (描画キャッシュはそのまま使える)。
        if was_dirty and not self.dirty():
            self._notify_dirty_changed()

    def _full_save(self) -> None:
        """全体保存(garbage=1・xref 不変)→ いったん閉じて置き換え → 開き直す。

        garbage>=2 は xref を付け替えるので使わない(上のモジュール注記を参照)。
        """
        assert self.doc is not None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            self.doc.save(tmp_path, garbage=1, deflate=True)
        except Exception as error:
            _unlink_quietly(tmp_path)
            if _is_permission_denied_error(error):
                raise PdfWritePermissionError(self.raw_path) from error
            raise
        # Windows では開いたままだと置き換えられないため、先に閉じる。
        self.doc.close()
        self.doc = None
        old_backing = self._backing_tmp
        try:
            shutil.move(tmp_path, self.raw_path)
        except Exception as move_error:
            # 置き換えに失敗しても保持内容(編集)を失わないよう、退避ファイルから開き直す。
            self.doc = fitz.open(tmp_path)
            self._backing_tmp = tmp_path
            _unlink_quietly(old_backing)
            if _is_permission_denied_error(move_error):
                raise PdfWritePermissionError(self.raw_path) from move_error
            raise
        self._backing_tmp = None
        _unlink_quietly(old_backing)
        self.doc = fitz.open(self.raw_path)

    # --- ハンドルの解放・再取得 ------------------------------------------------
    def release(self, *, force: bool = False) -> None:
        """保持中のハンドルを閉じる(未保存の変更が無いときだけ)。

        別経路でファイルを書き換える操作(全体保存の置き換え・改名・ゴミ箱)の前に使う。
        セッションは登録されたままで、次に ``_open_doc`` で読まれたときにディスクから開き直す。
        *force* は未保存の変更を捨てて閉じる(呼び出し側が捨ててよいと確認した場合だけ)。
        """
        if self.doc is None:
            return
        if self.dirty() and not force:
            raise PdfSessionError(f"unsaved changes: {self.raw_path}")
        self.doc.close()
        self.doc = None
        self._advance_generation()

    def _attach(self) -> "fitz.Document":
        if self.doc is None:
            self.doc = fitz.open(self.raw_path)
            self._disk_token = _get_disk_file_token(self.raw_path)
            self._advance_generation()
        return self.doc

    def _advance_generation(self) -> None:
        # ディスク側が書き換わっている可能性があるので、世代を進めて古いキャッシュを無効にする。
        self.generation += 1
        self.saved_generation = self.generation
        _pixmap_cache.clear_for_path(self.raw_path)
        _pixmap_cache.clear_for_path(self.path)

    def close(self, *, discard: bool = False) -> None:
        """セッションを終了してレジストリから外す。

        未保存の変更があるときは ``discard=True``(破棄)でなければ閉じない。
        """
        if self.dirty() and not discard:
            raise PdfSessionError(f"unsaved changes: {self.raw_path}")
        if self.doc is not None:
            try:
                self.doc.close()
            finally:
                self.doc = None
        _unlink_quietly(self._backing_tmp)
        self._backing_tmp = None
        if _sessions.get(self.path) is self:
            del _sessions[self.path]
        _pixmap_cache.clear_for_path(self.raw_path)
        _pixmap_cache.clear_for_path(self.path)
        self._dirty_listeners.clear()
        self.before_external_write = None


_sessions: dict[str, PdfSession] = {}


def _safe_getsize(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _unlink_quietly(path: "str | None") -> None:
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            logger.debug("failed to remove temp file: %s", path, exc_info=True)


def open_session(pdf_path: str) -> PdfSession:
    """*pdf_path* を保持して編集するセッションを開始し、レジストリへ登録する。"""
    key = _journal_key(pdf_path)
    if key in _sessions:
        raise PdfSessionError(f"session already open: {pdf_path}")
    session = PdfSession(pdf_path)
    _sessions[key] = session
    return session


def get_session(pdf_path: str) -> "PdfSession | None":
    if not _sessions:
        return None
    return _sessions.get(_journal_key(pdf_path))


def close_session(pdf_path: str, *, discard: bool = False) -> None:
    session = get_session(pdf_path)
    if session is not None:
        session.close(discard=discard)


class PdfSessionConflictError(PdfWritePermissionError):
    """セッションが未保存の変更を抱えたまま、別経路でディスクへ書こうとした(呼び出し側の保存忘れ)。

    既存の書き込み系の呼び出し元は ``PdfWritePermissionError`` を握って警告を出すため、
    それを継承しておく(想定外の経路から来ても、未処理の例外でアプリが落ちない)。
    """


def _release_session_before_disk_write(pdf_path: str) -> None:
    """保持中のセッションがあるパスへ別経路で書く前に、ハンドルを閉じておく。

    変更が無ければ(保存済みなら)ハンドルを閉じるだけ。未保存の変更があれば、
    ディスクへ書いても保持内容と食い違うため、保存し忘れとして例外にする。
    """
    release_held_docs(pdf_path)
    session = get_session(pdf_path)
    if session is None or session.doc is None:
        return
    if session.owner_thread != threading.get_ident():
        raise PdfSessionConflictError(pdf_path)
    if session.dirty():
        # 保存の確認(ダイアログ)を出せるウィンドウが居れば、保存してもらってから続ける。
        callback = session.before_external_write
        if callback is None or not callback() or session.dirty():
            raise PdfSessionConflictError(pdf_path)
    session.release()


def release_session_for_path(pdf_path: str) -> None:
    """*pdf_path* のセッションが保持するハンドルを閉じる(保存済みのときだけ)。

    ゴミ箱送り・改名など、``_save_document_in_place`` を通らずにファイルを動かす操作の前に使う。
    セッションが無い・既に解放済みなら何もしない。次に読まれたときにディスクから開き直される。
    """
    _release_session_before_disk_write(pdf_path)


def _open_doc_for_write(pdf_path: str) -> fitz.Document:
    """ファイルを直接書き換える操作用に、素のドキュメントを開く(``with`` でも ``close()`` でも使える)。

    開く**前に**セッションを保存・解放する。開いた後に保存してもドキュメントが古い内容のままに
    なり、書き込みで直前の保存が失われるため。未保存でも保存の確認(``before_external_write``)が
    あればそこで保存される。
    """
    _release_session_before_disk_write(pdf_path)
    return fitz.open(pdf_path)


def _acquire_doc(pdf_path: str) -> fitz.Document:
    """セッションがあれば保持中のドキュメントを、無ければ新しく開いたドキュメントを返す。

    返したドキュメントは ``_release_doc`` で手放すこと(セッションのものは閉じない)。
    """
    session = get_session(pdf_path)
    if session is not None:
        if session.owner_thread == threading.get_ident():
            return session._attach()
        logger.debug("session doc requested from another thread; opening from disk: %s", pdf_path)
    held = _held_docs.get((threading.get_ident(), pdf_path))
    if held is not None and held.depth > 0 and held.is_current():
        return held.doc
    return fitz.open(pdf_path)


# hold_doc() で使い回しているドキュメント: (スレッドID, パス) -> _HeldDoc
_held_docs: "dict[tuple[int, str], _HeldDoc]" = {}

# 保持(linger)するときにファイル全体をメモリへ読み込む上限。超える文書は保持せず従来どおり開き直す。
HELD_DOC_STREAM_MAX_BYTES = 768 * 1024 * 1024


def _file_token(pdf_path: str) -> "tuple[int, int] | None":
    try:
        st = os.stat(pdf_path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class _HeldDoc:
    """hold_doc() が持つドキュメント。``depth`` はスコープの入れ子数(>0 の間だけ ``_open_doc`` が使う)。"""

    __slots__ = ("doc", "path", "token", "linger", "depth", "last_used")

    def __init__(self, doc: fitz.Document, path: str, token, linger: bool) -> None:
        self.doc = doc
        self.path = path
        self.token = token
        self.linger = linger
        self.depth = 0
        self.last_used = time.monotonic()

    def is_current(self) -> bool:
        """開いた後にファイルが書き換えられていないか(更新日時とサイズで判定)。"""
        return not self.doc.is_closed and self.token == _file_token(self.path)

    def close(self) -> None:
        try:
            if not self.doc.is_closed:
                self.doc.close()
        except Exception:
            logger.debug("failed to close held doc: %s", self.path, exc_info=True)


def release_held_docs(pdf_path: "str | None" = None) -> None:
    """``hold_doc(..., linger=True)`` が保持しているドキュメントを閉じる(スコープ使用中のものは残す)。

    *pdf_path* を省略すると全パスが対象。保持ドキュメントはファイルのメモリ上の写しで、
    ファイルハンドルは掴んでいないため書き込み・置換を妨げない。このため呼び忘れても
    ファイルは壊れない(開いた後の更新はファイルの更新日時・サイズで検出して使わない)。
    メモリを早く手放すために、アイドル・ウィンドウを閉じる・書き込みの前に呼ぶ。
    他スレッドが持つものは触らない(次の使用時に更新検出で捨てられる)。
    """
    tid = threading.get_ident()
    for key, held in tuple(_held_docs.items()):
        if key[0] != tid or held.depth > 0:
            continue
        if pdf_path is not None and key[1] != pdf_path:
            continue
        _held_docs.pop(key, None)
        held.close()


@contextmanager
def hold_doc(pdf_path: str, *, linger: bool = False) -> Iterator[None]:
    """スコープ内の ``_open_doc(pdf_path)`` が同じドキュメントを使い回すようにする。

    重量文書は開き直すたびに最初の ``load_page`` でページツリー解析が走り数百ミリ秒かかる。
    1回の描画バッチで複数の関数が開き直すのを避けるために使う。セッションがあるパスでは
    既に保持中のドキュメントが使われるので何もしない。スコープ内では書き込み系の関数
    (``_open_doc_for_write``)を呼ばないこと。

    ``linger=False``: スコープを抜けるとき必ず閉じる(ファイルを直接開く)。
    ``linger=True``: スコープを抜けても閉じず、次の ``hold_doc(..., linger=True)`` で使い回す
    (バッチをまたいで開き直しを避ける)。ファイル全体を読み込んだメモリ上の写しを使うのでファイル
    ハンドルを掴まない。更新日時・サイズが変わると捨てて開き直す。``release_held_docs()`` で閉じる。
    """
    key = (threading.get_ident(), pdf_path)
    if get_session(pdf_path) is not None:
        yield
        return
    held = _held_docs.get(key)
    if held is not None and not (held.depth > 0 or (held.linger and held.is_current())):
        _held_docs.pop(key, None)
        held.close()
        held = None
    created = False
    if held is None:
        token = _file_token(pdf_path)
        use_stream = linger and token is not None and token[1] <= HELD_DOC_STREAM_MAX_BYTES
        if use_stream:
            with open(pdf_path, "rb") as f:
                data = f.read()
            doc = fitz.open("pdf", data)
        else:
            doc = fitz.open(pdf_path)
        held = _HeldDoc(doc, pdf_path, token, linger=use_stream)
        _held_docs[key] = held
        created = True
    held.depth += 1
    try:
        yield
    finally:
        held.depth -= 1
        held.last_used = time.monotonic()
        if held.depth == 0 and not held.linger:
            if _held_docs.get(key) is held:
                _held_docs.pop(key, None)
            held.close()


def _mark_edit_failed(doc: fitz.Document) -> None:
    """書き込み系の関数が途中の例外で失敗したとき、セッションのドキュメントを未保存扱いにする。

    途中までの変更が保持中のドキュメントに残るため、未保存の印を付けて(キャッシュも捨てて)
    画面・保存内容と食い違ったまま気づかれないのを防ぐ。セッションのドキュメントでなければ何もしない。
    """
    for session in tuple(_sessions.values()):
        if session.doc is doc:
            session.mark_dirty()
            return


def _release_doc(doc: fitz.Document) -> None:
    for session in tuple(_sessions.values()):
        if session.doc is doc:
            return
    if any(h.doc is doc for h in tuple(_held_docs.values())):
        return  # hold_doc() の持ち主が閉じる
    doc.close()


@contextmanager
def _open_doc(pdf_path: str) -> Iterator[fitz.Document]:
    """``with fitz.open(pdf_path) as doc`` の代わり。セッションがあるパスでは保持中のドキュメントを使う。"""
    doc = _acquire_doc(pdf_path)
    try:
        yield doc
    finally:
        _release_doc(doc)


def compact_pdf_in_place(pdf_path: str) -> None:
    """増分保存で積み上がった不要オブジェクトを、全体保存(garbage=1)で整理する。

    注釈編集は増分保存(追記のみ)で速く保存するが、繰り返すとファイルが肥大する。
    ウィンドウを閉じるときなどに1回だけ呼んで整理する。
    """
    with _open_doc_for_write(pdf_path) as doc:
        _save_document_in_place(doc, pdf_path)
