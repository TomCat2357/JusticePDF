"""手動保存モードの土台(pdf_utils の編集セッション)のテスト。"""
from __future__ import annotations

import os
import threading

import fitz
import pytest

from src.utils import pdf_utils
from src.utils.pdf_utils import (
    PdfSessionConflictError,
    PdfSessionError,
    ShapeAnnotData,
    ShapeType,
    _get_file_cache_token,
    close_session,
    create_shape_annot,
    delete_shape_annot,
    get_page_count,
    get_page_pixmap,
    get_session,
    list_ink_annot_xrefs,
    list_shape_annots,
    open_session,
    pages_changed_since,
    rotate_pages,
)
from src.utils.pdf_utils import common as pdf_common
from tests.helpers import make_pdf

pytestmark = pytest.mark.usefixtures("qapp")


def _shape(page_num: int = 0, rect=(40.0, 50.0, 140.0, 110.0)) -> ShapeAnnotData:
    return ShapeAnnotData(
        page_num=page_num,
        xref=0,
        rect=rect,
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(1.0, 0.0, 0.0),
        fill_color=(1.0, 0.0, 0.0),
        stroke_width=2.0,
        opacity=1.0,
    )


def _direct_shapes(path) -> list[int]:
    """セッションを通さずディスクを直接読んだときの図形注釈の xref。"""
    with fitz.open(str(path)) as doc:
        return [a.xref for a in (doc[0].annots(types=[fitz.PDF_ANNOT_SQUARE]) or [])]


@pytest.fixture
def session_pdf(tmp_path):
    path = tmp_path / "s.pdf"
    make_pdf(path, pages=2)
    yield path
    # テストが途中で失敗してもセッションを残さない
    session = get_session(str(path))
    if session is not None:
        session.close(discard=True)


def test_without_session_everything_works_as_before(tmp_path):
    path = tmp_path / "plain.pdf"
    make_pdf(path)
    assert get_session(str(path)) is None
    token_before = _get_file_cache_token(str(path))
    saved = create_shape_annot(str(path), _shape())
    assert _direct_shapes(path) == [saved.xref]  # 即ディスクに書かれる
    assert _get_file_cache_token(str(path)) != token_before
    assert get_page_count(str(path)) == 1


def test_session_edit_does_not_touch_disk_but_is_visible_through_utils(session_pdf):
    path = str(session_pdf)
    stat_before = os.stat(path)
    token_before = _get_file_cache_token(path)
    session = open_session(path)
    assert not session.dirty()

    saved = create_shape_annot(path, _shape())

    stat_after = os.stat(path)
    assert (stat_after.st_mtime_ns, stat_after.st_size) == (stat_before.st_mtime_ns, stat_before.st_size)
    assert session.dirty()
    assert [s.xref for s in list_shape_annots(path)] == [saved.xref]  # セッション経由では見える
    assert _direct_shapes(session_pdf) == []  # ディスクの直読みには見えない
    assert _get_file_cache_token(path) != token_before


def test_token_changes_on_every_edit_and_invalidates_pixmap_cache(session_pdf):
    path = str(session_pdf)
    open_session(path)
    tokens = [_get_file_cache_token(path)]
    first = create_shape_annot(path, _shape())
    tokens.append(_get_file_cache_token(path))
    delete_shape_annot(path, 0, first.xref)
    tokens.append(_get_file_cache_token(path))
    assert len(set(tokens)) == 3


def test_get_page_pixmap_includes_unsaved_annotation(session_pdf):
    path = str(session_pdf)
    open_session(path)
    before = get_page_pixmap(path, 0, zoom=1.0).toImage()
    create_shape_annot(path, _shape(rect=(40.0, 50.0, 140.0, 110.0)))
    after = get_page_pixmap(path, 0, zoom=1.0).toImage()
    assert before.pixelColor(90, 80).red() > 240 and before.pixelColor(90, 80).green() > 240
    color = after.pixelColor(90, 80)
    assert color.red() > 200 and color.green() < 80  # 未保存の赤塗りが描画される


def test_save_writes_to_disk_and_keeps_xrefs(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    saved = create_shape_annot(path, _shape())
    assert session.dirty()

    session.save()

    assert not session.dirty()
    assert _direct_shapes(session_pdf) == [saved.xref]  # xref が一致したまま保存される
    # 保存後も保持中のドキュメントで編集を続けられ、再度の保存も効く
    second = create_shape_annot(path, _shape(rect=(150.0, 150.0, 200.0, 200.0)))
    assert session.dirty()
    session.save()
    assert sorted(_direct_shapes(session_pdf)) == sorted([saved.xref, second.xref])


def test_other_thread_sees_disk_state(session_pdf):
    path = str(session_pdf)
    open_session(path)
    create_shape_annot(path, _shape())
    seen: list[int] = []
    errors: list[BaseException] = []

    def worker():
        try:
            seen.append(len(list_shape_annots(path)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert not errors
    assert seen == [0]  # 別スレッドはパスから開き直すのでディスクの状態(未保存分は無い)
    assert len(list_shape_annots(path)) == 1  # UIスレッドはセッション経由


def test_hide_xrefs_flag_is_restored_after_render(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    saved = create_shape_annot(path, _shape())
    session.save()
    generation = session.generation

    get_page_pixmap(path, 0, zoom=1.0, hide_xrefs=[saved.xref])

    page = session.doc[0]
    annot = page.load_annot(saved.xref)
    assert not (annot.flags & fitz.PDF_ANNOT_IS_HIDDEN)
    assert session.generation == generation  # 描画だけでは未保存にならない


def test_hide_xrefs_flag_is_restored_even_when_render_raises(session_pdf, monkeypatch):
    path = str(session_pdf)
    session = open_session(path)
    saved = create_shape_annot(path, _shape())
    session.save()

    def boom(self, *args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(fitz.Page, "get_pixmap", boom)
    # 例外は _render_page_pixmap 内で握りつぶされる(空の QPixmap が返る)
    get_page_pixmap(path, 0, zoom=1.0, hide_xrefs=[saved.xref])

    monkeypatch.undo()
    page = session.doc[0]
    annot = page.load_annot(saved.xref)
    assert not (annot.flags & fitz.PDF_ANNOT_IS_HIDDEN)


def test_save_falls_back_to_full_save_keeping_xrefs(session_pdf, monkeypatch):
    path = str(session_pdf)
    session = open_session(path)
    saved = create_shape_annot(path, _shape())

    def fail_incr(self, *args, **kwargs):
        raise RuntimeError("incremental save failed")

    monkeypatch.setattr(fitz.Document, "saveIncr", fail_incr)
    session.save()
    monkeypatch.undo()

    assert not session.dirty()
    assert _direct_shapes(session_pdf) == [saved.xref]
    # フォールバック後も保持ドキュメントは有効で、xref が維持されている
    again = create_shape_annot(path, _shape(rect=(150.0, 150.0, 200.0, 200.0)))
    assert again.xref != saved.xref
    session.save()
    assert saved.xref in _direct_shapes(session_pdf)


def test_save_never_uses_garbage_2_or_more(session_pdf, monkeypatch):
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())
    garbage_values: list[int] = []
    real_save = fitz.Document.save

    def spy(self, *args, **kwargs):
        garbage_values.append(kwargs.get("garbage", 0))
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Document, "saveIncr", lambda self: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(fitz.Document, "save", spy)
    session.save()
    assert garbage_values and all(g <= 1 for g in garbage_values)


def test_permission_error_on_save_is_wrapped(session_pdf, monkeypatch):
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())

    def deny(self, *args, **kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(fitz.Document, "saveIncr", deny)
    with pytest.raises(pdf_utils.PdfWritePermissionError):
        session.save()
    assert session.dirty()  # 失敗したら未保存のまま


def test_write_journal_chains_under_session(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    token0 = _get_file_cache_token(path)
    create_shape_annot(path, _shape(page_num=0))
    create_shape_annot(path, _shape(page_num=1))
    assert pages_changed_since(path, token0) == frozenset({0, 1})
    token1 = _get_file_cache_token(path)
    assert pages_changed_since(path, token1) == frozenset()
    # 保存してもトークンは変わらない(保持内容は同じ)ので履歴はそのまま使える
    session.save()
    assert _get_file_cache_token(path) == token1


def test_close_requires_discard_when_dirty(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())
    with pytest.raises(PdfSessionError):
        session.close()
    assert get_session(path) is session
    session.close(discard=True)
    assert get_session(path) is None
    assert _direct_shapes(session_pdf) == []  # 破棄したのでディスクは無変更


def test_double_open_session_is_rejected(session_pdf):
    open_session(str(session_pdf))
    with pytest.raises(PdfSessionError):
        open_session(str(session_pdf))


def test_dirty_listener_fires_on_state_changes_only(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    calls: list[bool] = []
    session.add_dirty_listener(lambda: calls.append(session.dirty()))
    create_shape_annot(path, _shape())
    create_shape_annot(path, _shape(rect=(150.0, 150.0, 200.0, 200.0)))
    session.save()
    assert calls == [True, False]


def test_disk_write_with_clean_session_releases_handle_and_reattaches(session_pdf):
    """ページ操作など別経路の書き込みは、保存済みセッションのハンドルを閉じてから行われる。"""
    path = str(session_pdf)
    session = open_session(path)
    assert session.doc is not None
    rotate_pages(path, [0], 90)  # 全体保存の置き換え(保持ハンドルがあると Windows では失敗する)
    assert session.doc is None  # 解放された
    with fitz.open(path) as doc:
        assert doc[0].rotation == 90
    # 次に読むとディスクから開き直し、回転後の内容が見える
    assert get_page_count(path) == 2
    assert session.doc is not None
    assert session.doc[0].rotation == 90


def test_disk_write_with_dirty_session_is_refused(session_pdf):
    """未保存の変更があるのに別経路でディスクへ書こうとしたら、保存し忘れとして拒否する。"""
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())
    with pytest.raises(PdfSessionConflictError):
        rotate_pages(path, [0], 90)
    with fitz.open(path) as doc:
        assert doc[0].rotation == 0
    assert session.dirty()


def test_release_requires_clean_session(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())
    with pytest.raises(PdfSessionError):
        session.release()


def test_no_remaining_plain_open_in_session_aware_functions():
    """置換漏れの確認: 注釈・描画モジュールに素の fitz.open(pdf_path) が残っていない
    (カード用のサムネイルだけがディスクを直接読む)。"""
    import inspect

    from src.utils.pdf_utils import annotations, rendering

    ann_src = inspect.getsource(annotations)
    assert "fitz.open(pdf_path)" not in ann_src.replace("``fitz.open``", "")
    render_src = inspect.getsource(rendering)
    assert render_src.count("fitz.open(pdf_path)") == 1  # get_pdf_card_info のみ
    assert "fitz.open(pdf_path)" in inspect.getsource(rendering.get_pdf_card_info)


def test_card_info_reads_disk_not_session(session_pdf):
    path = str(session_pdf)
    open_session(path)
    create_shape_annot(path, _shape(rect=(0.0, 0.0, 320.0, 420.0)))
    pix, count = pdf_utils.get_pdf_card_info(path, 64)
    assert count == 2
    color = pix.toImage().pixelColor(32, 40)
    assert color.green() > 200  # ディスク上のページは白のまま(未保存の赤塗りは含まれない)


def test_ink_xref_listing_uses_session(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    page = session.doc[0]
    annot = page.add_ink_annot([[(10, 10), (50, 50)]])
    xref = annot.xref
    session.mark_dirty()
    assert list_ink_annot_xrefs(path) == [xref]
    with fitz.open(path) as doc:
        assert not (doc[0].annots(types=[fitz.PDF_ANNOT_INK]) and list(doc[0].annots(types=[fitz.PDF_ANNOT_INK])))


def test_close_session_helper(session_pdf):
    path = str(session_pdf)
    open_session(path)
    close_session(path)
    assert get_session(path) is None
    # 登録が無いパスでも何も起きない
    close_session(path)


def test_write_callback_saves_dirty_session_then_write_proceeds(session_pdf):
    """未保存のセッションへ別経路から書かれそうなとき、保存の確認(コールバック)があればそれで保存して続行する。"""
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())
    calls: list[int] = []

    def ask() -> bool:
        calls.append(1)
        session.save()
        return True

    session.before_external_write = ask
    rotate_pages(path, [0], 90)

    assert calls == [1]
    with fitz.open(path) as doc:
        assert doc[0].rotation == 90
    assert _direct_shapes(session_pdf) != []  # 保存した注釈が回転で失われていない
    assert session.doc is None  # 書き込みの前に保持ハンドルは閉じられた


def test_write_callback_refusal_aborts_write(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    create_shape_annot(path, _shape())
    session.before_external_write = lambda: False
    with pytest.raises(PdfSessionConflictError):
        rotate_pages(path, [0], 90)
    with fitz.open(path) as doc:
        assert doc[0].rotation == 0
    assert session.dirty()
    # 既存の書き込みエラー処理(PdfWritePermissionError)で受けられる
    assert issubclass(PdfSessionConflictError, pdf_utils.PdfWritePermissionError)


def test_restyle_pii_annots_runs_in_memory_under_session(session_pdf):
    from src.utils.pdf_utils import (
        MarkupType,
        TextMarkupAnnotData,
        create_markup_annot,
        list_markup_annots,
        restyle_pii_annots,
    )

    path = str(session_pdf)
    doc = fitz.open(path)
    doc[0].insert_text((40, 60), "Hello markup world", fontsize=18)
    doc.saveIncr()
    doc.close()
    session = open_session(path)
    words = session.doc[0].get_text("words")
    x0, y0, x1, y1 = words[0][:4]
    create_markup_annot(
        path,
        TextMarkupAnnotData(
            page_num=0, xref=0, quads=[(x0, y0, x1, y1)], markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0), opacity=0.5, pii_entity="PERSON", pii_text="Hello",
        ),
    )
    session.save()
    stat_before = os.stat(path)

    changed = restyle_pii_annots(path, (0.0, 0.0, 1.0), 0.3)

    assert changed == 1
    assert (os.stat(path).st_mtime_ns, os.stat(path).st_size) == (stat_before.st_mtime_ns, stat_before.st_size)
    assert session.dirty()
    assert list_markup_annots(path, 0)[0].color == pytest.approx((0.0, 0.0, 1.0), abs=0.02)


def test_aborted_restyle_job_marks_session_dirty(session_pdf):
    """restyle ジョブを途中で止めたとき、メモリに残った途中までの変更は未保存として扱う。"""
    from src.utils.pdf_utils import (
        MarkupType,
        TextMarkupAnnotData,
        create_markup_annot,
        iter_restyle_pii_annots,
    )

    path = str(session_pdf)
    doc = fitz.open(path)
    doc[0].insert_text((40, 60), "Hello markup world", fontsize=18)
    doc.saveIncr()
    doc.close()
    session = open_session(path)
    x0, y0, x1, y1 = session.doc[0].get_text("words")[0][:4]
    create_markup_annot(
        path,
        TextMarkupAnnotData(
            page_num=0, xref=0, quads=[(x0, y0, x1, y1)], markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0), opacity=0.5, pii_entity="PERSON", pii_text="Hello",
        ),
    )
    session.save()
    assert not session.dirty()

    job = iter_restyle_pii_annots(path, (0.0, 0.0, 1.0), 0.3)
    next(job)  # 1ページ目を処理したところで中断
    job.close()

    assert session.dirty()


def test_repeated_saves_keep_file_valid_and_incrementally_savable(session_pdf):
    """同じハンドルでの2回目以降の saveIncr は xref が壊れ「修復された」ファイルになる(実測)。保存のたびに開き直す。"""
    path = str(session_pdf)
    session = open_session(path)
    for index in range(3):
        create_shape_annot(path, _shape(rect=(40.0, 50.0 + 40 * index, 140.0, 80.0 + 40 * index)))
        session.save()
        assert not session.dirty()
        with fitz.open(path) as check:
            assert check.is_repaired is False
            assert check.can_save_incrementally()
    assert len(_direct_shapes(session_pdf)) == 3
    # 保存後も保持中のドキュメントで編集を続けられ、削除も正しく保存される
    xrefs = _direct_shapes(session_pdf)
    delete_shape_annot(path, 0, xrefs[0])
    session.save()
    with fitz.open(path) as check:
        assert check.is_repaired is False
    assert len(_direct_shapes(session_pdf)) == 2
    session.close()


def test_failed_edit_in_session_marks_session_dirty(session_pdf, monkeypatch):
    """注釈関数が途中で例外になっても、メモリ上の部分変更を未保存扱いにする(画面・保存と食い違わせない)。"""
    path = str(session_pdf)
    session = open_session(path)
    created = create_shape_annot(path, _shape())
    session.save()
    assert not session.dirty()

    def boom(*args, **kwargs):
        raise RuntimeError("extract failed")

    monkeypatch.setattr(pdf_common, "_save_document_in_place", boom)
    monkeypatch.setattr(pdf_utils.annotations, "_save_document_in_place", boom)
    with pytest.raises(RuntimeError):
        delete_shape_annot(path, 0, created.xref)
    assert session.dirty()
    session.close(discard=True)


def test_disk_changed_externally_tracks_own_writes(session_pdf):
    path = str(session_pdf)
    session = open_session(path)
    assert not session.disk_changed_externally()
    create_shape_annot(path, _shape())
    session.save()
    assert not session.disk_changed_externally()  # 自分の保存では「外部変更」にならない
    with fitz.open(path) as other:
        other[0].add_text_annot((60, 60), "ext")
        other.saveIncr()
    assert session.disk_changed_externally()
    session.close(discard=True)
