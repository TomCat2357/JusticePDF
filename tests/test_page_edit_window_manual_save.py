"""手動保存モード(メモリ上で編集し、保存ボタンでファイルへ書く)の編集ウィンドウのテスト。"""
from __future__ import annotations

import os

import fitz
import pytest
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from src.models.undo_manager import UndoAction, UndoManager
from src.utils import app_settings
from src.utils.pdf_utils import (
    ShapeType,
    get_session,
    list_shape_annots,
)
from src.views.page_edit_window import PageEditWindow, ensure_path_saved
from tests.helpers import make_pdf, open_zoom

pytestmark = pytest.mark.usefixtures("qapp")

Button = QMessageBox.StandardButton


def _make_window(qtbot, pdf, *, manual: bool = True, undo_manager: UndoManager | None = None):
    app_settings.set_edit_save_mode("manual" if manual else "auto")
    window = PageEditWindow(str(pdf), undo_manager or UndoManager(max_size=50))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()
    return window


def _direct_shape_count(pdf) -> int:
    """セッションを通さずディスクを直接読んだときの図形注釈の件数。"""
    with fitz.open(str(pdf)) as doc:
        return sum(len(list(page.annots(types=[fitz.PDF_ANNOT_SQUARE]) or [])) for page in doc)


def _draw_rect(window, qtbot, pdf, expected: int, *, offset: float = 0.0) -> None:
    window._on_zoom_shape_create_requested(
        ShapeType.RECTANGLE, (40.0 + offset, 40.0), (140.0 + offset, 100.0)
    )
    qtbot.waitUntil(lambda: len(list_shape_annots(str(pdf), 0)) == expected)


def _answer(monkeypatch, button, calls: list | None = None):
    def fake(*args, **kwargs):
        if calls is not None:
            calls.append(args[2] if len(args) > 2 else "")
        return button

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake))


def _stat_key(pdf):
    st = os.stat(pdf)
    return (st.st_mtime_ns, st.st_size)


def test_auto_mode_has_no_session_and_hides_save_button(qtbot, tmp_path):
    pdf = tmp_path / "auto.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf, manual=False)
    open_zoom(window, qtbot)

    assert window._session is None
    assert get_session(str(pdf)) is None
    assert not window._save_action.isVisible()
    window._on_save_shortcut()  # 自動保存では何もしない
    _draw_rect(window, qtbot, pdf, 1)
    assert _direct_shape_count(pdf) == 1  # 従来どおり即ファイルへ書かれる
    assert not window.windowTitle().endswith(" *")


def test_default_mode_is_auto(qtbot, tmp_path):
    pdf = tmp_path / "default.pdf"
    make_pdf(pdf)
    window = PageEditWindow(str(pdf), UndoManager())
    qtbot.addWidget(window)
    assert window._session is None


def test_manual_mode_edits_stay_in_memory_until_save(qtbot, tmp_path):
    pdf = tmp_path / "manual.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    before = _stat_key(pdf)

    assert window._session is not None
    assert window._save_action.isVisible()
    assert not window._save_btn.isEnabled()
    assert not window.windowTitle().endswith(" *")

    _draw_rect(window, qtbot, pdf, 1)

    assert _stat_key(pdf) == before  # ファイルは変わらない
    assert _direct_shape_count(pdf) == 0
    assert window.windowTitle().endswith(" *")
    assert window._save_btn.isEnabled()

    assert window._on_save() is True

    assert _direct_shape_count(pdf) == 1
    assert not window.windowTitle().endswith(" *")
    assert not window._save_btn.isEnabled()
    window._session.close()  # テストの後始末(未保存なし)


def test_manual_mode_undo_redo_are_memory_only_and_dirty(qtbot, tmp_path):
    pdf = tmp_path / "undo.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    assert window._on_save()
    before = _stat_key(pdf)

    window._on_undo()  # 保存点を越えて Undo すると再び未保存になる
    assert list_shape_annots(str(pdf), 0) == []
    assert _direct_shape_count(pdf) == 1
    assert window._session.dirty()
    assert window.windowTitle().endswith(" *")
    window._on_redo()
    assert len(list_shape_annots(str(pdf), 0)) == 1
    assert _stat_key(pdf) == before
    assert window._on_save()
    window._session.close()


def test_ctrl_s_saves_in_manual_mode(qtbot, tmp_path):
    pdf = tmp_path / "ctrl_s.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    window._on_save_shortcut()
    assert _direct_shape_count(pdf) == 1
    window._session.close()


def test_close_prompt_save_writes_file(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "close_save.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    _answer(monkeypatch, Button.Save)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert _direct_shape_count(pdf) == 1
    assert get_session(str(pdf)) is None  # セッションは閉じてハンドルも解放済み
    os.replace(pdf, tmp_path / "moved.pdf")  # Windows でも置き換えできる(ハンドルが残っていない)


def test_close_prompt_discard_keeps_file_and_purges_undo(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "close_discard.pdf"
    make_pdf(pdf)
    manager = UndoManager()
    foreign = []
    manager.add_action(UndoAction("other window", lambda: foreign.append("u"), lambda: foreign.append("r")))
    window = _make_window(qtbot, pdf, undo_manager=manager)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    assert manager.undo_count() == 2
    before = _stat_key(pdf)
    _answer(monkeypatch, Button.Discard)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert _stat_key(pdf) == before
    assert _direct_shape_count(pdf) == 0
    assert get_session(str(pdf)) is None
    # このウィンドウの未保存分は共有 UndoManager から消え、ほかの操作は残る
    assert manager.undo_count() == 1
    assert manager.peek_undo().description == "other window"


def test_close_discard_only_purges_actions_since_last_save(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "close_discard2.pdf"
    make_pdf(pdf)
    manager = UndoManager()
    window = _make_window(qtbot, pdf, undo_manager=manager)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    assert window._on_save()  # 1つ目は保存済み
    _draw_rect(window, qtbot, pdf, 2, offset=100.0)  # 2つ目は未保存
    assert manager.undo_count() == 2
    _answer(monkeypatch, Button.Discard)

    window.closeEvent(QCloseEvent())

    assert _direct_shape_count(pdf) == 1
    assert manager.undo_count() == 1  # 保存済みの1つ目だけ残る


def test_close_prompt_cancel_keeps_window_and_session(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "close_cancel.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    _answer(monkeypatch, Button.Cancel)

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert get_session(str(pdf)) is window._session
    assert window._session.dirty()
    assert len(list_shape_annots(str(pdf), 0)) == 1
    # 後始末
    _answer(monkeypatch, Button.Discard)
    window.closeEvent(QCloseEvent())


def test_close_without_changes_does_not_prompt(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "close_clean.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    calls: list = []
    _answer(monkeypatch, Button.Cancel, calls)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert calls == []
    assert get_session(str(pdf)) is None


def test_page_operation_asks_to_save_first(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "rotate.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    calls: list = []

    # キャンセル: 回転されず、未保存のまま
    _answer(monkeypatch, Button.Cancel, calls)
    window._rotate_zoom_page()
    assert len(calls) == 1 and "ページの回転" in calls[0]
    with fitz.open(str(pdf)) as doc:
        assert doc[0].rotation == 0
    assert window._session.dirty()

    # 保存: 注釈も回転もファイルに入り、セッションは使い続けられる
    _answer(monkeypatch, Button.Save)
    window._rotate_zoom_page()
    with fitz.open(str(pdf)) as doc:
        assert doc[0].rotation == 90
    assert _direct_shape_count(pdf) == 1
    assert not window._session.dirty()
    assert len(list_shape_annots(str(pdf), 0)) == 1
    window._session.close()


def test_page_operation_undo_after_dirty_edit_asks_to_save(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "undo_page.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    window._rotate_zoom_page()  # クリーンなので確認なしでファイルへ
    _draw_rect(window, qtbot, pdf, 1)
    calls: list = []
    _answer(monkeypatch, Button.Save, calls)

    window._on_undo()  # 注釈の取り消し(メモリ上): 確認なし
    assert calls == []
    window._on_undo()  # 回転の取り消し(ファイル書き換え): 保存の確認が出る
    assert len(calls) == 1
    with fitz.open(str(pdf)) as doc:
        assert doc[0].rotation == 0
    window._session.close(discard=True)


def test_rename_saves_releases_handle_and_reopens_session(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "before.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    _answer(monkeypatch, Button.Save)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("after.pdf", True)))

    window._on_rename()

    new_path = tmp_path / "after.pdf"
    assert new_path.exists() and not pdf.exists()
    assert _direct_shape_count(new_path) == 1  # 保存してから改名された
    assert window._pdf_path == str(new_path)
    assert get_session(str(pdf)) is None
    assert get_session(str(new_path)) is window._session
    assert "after.pdf" in window.windowTitle()
    window._session.close()


def test_rename_cancelled_save_does_not_rename(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "keep.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    _answer(monkeypatch, Button.Cancel)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("other.pdf", True)))

    window._on_rename()

    assert pdf.exists() and not (tmp_path / "other.pdf").exists()
    assert window._session.dirty()
    window._session.close(discard=True)


def test_pii_detection_asks_to_save_before_starting_worker(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "detect.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    started: list = []
    monkeypatch.setattr(window, "_start_pii_detection_worker", lambda pages: started.append(pages))
    monkeypatch.setattr(window._pii_settings(), "ocr_enabled", False, raising=False)

    _answer(monkeypatch, Button.Cancel)
    window._run_pii_detection([0])
    assert started == []
    assert _direct_shape_count(pdf) == 0

    _answer(monkeypatch, Button.Save)
    window._run_pii_detection([0])
    assert started == [[0]]
    assert _direct_shape_count(pdf) == 1  # ワーカーが読むファイルに未保存分が入っている
    window._session.close()


def test_pii_detection_worker_start_saves_silently_after_ocr(qtbot, tmp_path, monkeypatch):
    """検出前OCR(メモリへ埋め込み済み)の続きで検出を始めるときは、確認なしで保存してから読ませる。"""
    pdf = tmp_path / "detect2.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    calls: list = []
    _answer(monkeypatch, Button.Cancel, calls)

    class StopHere(Exception):
        pass

    def boom(*args, **kwargs):
        raise StopHere

    from src.views import page_edit_pii

    monkeypatch.setattr(page_edit_pii, "PiiDetectWorker", boom)
    with pytest.raises(StopHere):
        window._start_pii_detection_worker([0])
    assert calls == []  # 確認ダイアログは出ない
    assert _direct_shape_count(pdf) == 1
    window._session.close()


def test_ocr_asks_to_save_before_starting_worker(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "ocr.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    from src.views import page_edit_ocr

    monkeypatch.setattr(page_edit_ocr, "is_ocr_available", lambda: True)

    class StopHere(Exception):
        pass

    def boom(*args, **kwargs):
        raise StopHere

    monkeypatch.setattr(window, "_create_ocr_worker", boom)
    _answer(monkeypatch, Button.Cancel)
    assert window._run_ocr([0]) is False
    assert _direct_shape_count(pdf) == 0

    _answer(monkeypatch, Button.Save)
    with pytest.raises(StopHere):
        window._run_ocr([0])
    assert _direct_shape_count(pdf) == 1
    window._session.close()


def test_save_is_blocked_while_worker_is_running(qtbot, tmp_path):
    pdf = tmp_path / "busy.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    window._pii_worker = object()  # 実行中のワーカーに見立てる
    window._update_save_button()
    assert not window._save_btn.isEnabled()
    assert window._on_save() is False
    assert _direct_shape_count(pdf) == 0
    window._pii_worker = None
    window._update_save_button()
    assert window._save_btn.isEnabled()
    assert window._on_save() is True
    window._session.close()


def test_refresh_from_disk_with_unsaved_changes_asks_first(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "reload.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    with fitz.open(str(pdf)) as doc:  # 外部で書き換えられた
        doc[1].set_rotation(90)
        doc.saveIncr()

    _answer(monkeypatch, Button.No)
    window.refresh_from_disk()
    assert window._session.dirty()
    assert len(list_shape_annots(str(pdf), 0)) == 1  # 無視: 未保存の変更は残る

    _answer(monkeypatch, Button.Yes)
    window.refresh_from_disk()
    assert not window._session.dirty()
    assert list_shape_annots(str(pdf), 0) == []  # 再読み込み: 未保存の変更は破棄
    assert window._undo_manager.undo_count() == 0  # その操作の履歴も消える
    window._session.close()


def test_refresh_from_disk_when_clean_reloads_external_change(qtbot, tmp_path):
    pdf = tmp_path / "ext.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    # 外部で回転された
    with fitz.open(str(pdf)) as doc:
        doc[0].set_rotation(90)
        doc.saveIncr()

    window.refresh_from_disk()

    assert window._session is not None and not window._session.dirty()
    from src.utils.pdf_utils import get_page_size_points

    assert get_page_size_points(str(pdf), 0) == (420.0, 320.0)  # 回転後の向きが見える
    window._session.close()


def test_ensure_path_saved_saves_other_window(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "other.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)

    _answer(monkeypatch, Button.Cancel)
    assert ensure_path_saved(str(pdf), "ページの移動") is False
    assert _direct_shape_count(pdf) == 0
    _answer(monkeypatch, Button.Save)
    assert ensure_path_saved(str(pdf), "ページの移動") is True
    assert _direct_shape_count(pdf) == 1
    assert ensure_path_saved(str(tmp_path / "nothing.pdf"), "x") is True
    window._session.close()


def test_main_screen_style_write_to_dirty_window_file_prompts_via_hook(qtbot, tmp_path, monkeypatch):
    """メイン画面などがページ構成を直接書き換えようとしたら、編集ウィンドウが保存を確認する。"""
    from src.utils.pdf_utils import rotate_pages

    pdf = tmp_path / "hook.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)

    _answer(monkeypatch, Button.Save)
    rotate_pages(str(pdf), [0], 90)
    with fitz.open(str(pdf)) as doc:
        assert doc[0].rotation == 90
    assert _direct_shape_count(pdf) == 1
    window._session.close()


def test_session_opened_in_manual_mode_and_closed_by_discard_leaves_no_registry(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "registry.pdf"
    make_pdf(pdf)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    _answer(monkeypatch, Button.Discard)
    window.closeEvent(QCloseEvent())
    assert get_session(str(pdf)) is None
    # 同じファイルをもう一度開ける
    window2 = _make_window(qtbot, pdf)
    assert window2._session is not None
    assert list_shape_annots(str(pdf), 0) == []
    window2._session.close()


def test_undo_manager_purge_and_release_owner():
    manager = UndoManager()
    owner = object()
    other = object()
    manager.add_action(UndoAction("a", lambda: None, lambda: None, owner=owner))
    manager.add_action(UndoAction("b", lambda: None, lambda: None, owner=other))
    manager.add_action(UndoAction("c", lambda: None, lambda: None, owner=owner))
    manager.add_action(UndoAction("d", lambda: None, lambda: None))
    manager.undo()  # d -> redo スタック
    manager.undo()  # c -> redo スタック(owner)
    notified: list[str] = []
    manager.add_listener(notified.append)

    assert manager.purge_owner(owner) == 2  # undo の a と redo の c
    assert [manager.peek_undo().description, manager.peek_redo().description] == ["b", "d"]
    assert notified == ["purge"]

    manager.release_owner(other)
    assert manager.purge_owner(other) == 0
    assert manager.undo_count() == 1


def test_rename_undo_from_shared_manager_releases_reopened_session(qtbot, tmp_path, monkeypatch):
    """メイン画面など編集ウィンドウ以外から Undo/Redo しても、保持ハンドルのせいで改名が失敗しない。"""
    pdf = tmp_path / "ren_a.pdf"
    make_pdf(pdf)
    manager = UndoManager()
    window = _make_window(qtbot, pdf, undo_manager=manager)
    open_zoom(window, qtbot)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("ren_b.pdf", True)))
    window._on_rename()
    new_path = tmp_path / "ren_b.pdf"
    assert new_path.exists()
    # 改名後にセッションが新パスで開き直され、ハンドルを保持している
    assert window._session.doc is not None

    manager.undo()  # 編集ウィンドウの _on_undo を通さない(メイン画面からの Undo を模す)

    assert pdf.exists() and not new_path.exists()
    assert window._pdf_path == str(pdf)
    assert get_session(str(pdf)) is window._session

    manager.redo()
    assert new_path.exists() and not pdf.exists()
    window._session.close()


def test_close_with_only_pending_restyle_applies_it_without_prompt(qtbot, tmp_path, monkeypatch):
    from src.utils.pdf_utils import (
        MarkupType,
        TextMarkupAnnotData,
        create_markup_annot,
        list_markup_annots,
    )

    pdf = tmp_path / "restyle_close.pdf"
    doc = fitz.open()
    page = doc.new_page(width=320, height=420)
    page.insert_text((40, 60), "Hello markup world", fontsize=18)
    doc.save(str(pdf))
    doc.close()
    x0, y0, x1, y1 = fitz.open(str(pdf))[0].get_text("words")[0][:4]
    create_markup_annot(
        str(pdf),
        TextMarkupAnnotData(
            page_num=0, xref=0, quads=[(x0, y0, x1, y1)], markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0), opacity=0.5, pii_entity="PERSON", pii_text="Hello",
        ),
    )
    window = _make_window(qtbot, pdf)
    window._pii_settings().mask_color = (0.0, 0.0, 1.0)
    window._schedule_pii_restyle()  # 予約だけ(まだ実行されていない)
    assert not window._session.dirty()
    calls: list = []
    _answer(monkeypatch, Button.Cancel, calls)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert calls == []  # 確認なし
    assert get_session(str(pdf)) is None
    with fitz.open(str(pdf)) as check:
        annot = next(iter(check[0].annots()))
        assert annot.colors["stroke"] == pytest.approx((0.0, 0.0, 1.0), abs=0.02)
    assert list_markup_annots(str(pdf), 0)[0].color == pytest.approx((0.0, 0.0, 1.0), abs=0.02)


def test_refresh_without_external_change_keeps_unsaved_edits_without_prompt(qtbot, tmp_path, monkeypatch):
    """F5 などで「外部で変更された」と偽って未保存の編集を捨てさせない(外部変更が無ければ確認も再読込もしない)。"""
    pdf = tmp_path / "noext.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    calls: list = []
    _answer(monkeypatch, Button.Yes, calls)

    window.refresh_from_disk()

    assert calls == []  # 確認ダイアログは出ない
    assert window._session.dirty()
    assert len(list_shape_annots(str(pdf), 0)) == 1
    window._session.close(discard=True)


def test_main_window_write_error_for_cancelled_save_shows_no_warning(monkeypatch):
    """保存確認のキャンセル(PdfSessionConflictError)で「他のアプリが使用中」と案内しない。"""
    from src.utils.pdf_utils import PdfSessionConflictError, PdfWritePermissionError
    from src.views.main_window_fileops import FileOpsMixin

    shown: list = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a)))

    FileOpsMixin._handle_pdf_write_permission_denied(object(), PdfSessionConflictError("x.pdf"))
    assert shown == []
    FileOpsMixin._handle_pdf_write_permission_denied(object(), PdfWritePermissionError("x.pdf"))
    assert len(shown) == 1  # 本当の権限エラーは従来どおり警告


def test_failed_edit_in_manual_mode_is_dirty_and_not_pushed_to_undo(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "fail.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    window._flash_zoom_hint = lambda *a, **k: None

    def failing_do():
        raise RuntimeError("boom")

    before = window._undo_manager.undo_count()
    assert window._push_undoable("failing", failing_do, lambda: None) is False
    assert window._undo_manager.undo_count() == before
    window._session.close(discard=True)


def test_ensure_path_saved_matches_differently_written_path(qtbot, tmp_path, monkeypatch):
    pdf = tmp_path / "norm.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    open_zoom(window, qtbot)
    _draw_rect(window, qtbot, pdf, 1)
    _answer(monkeypatch, Button.Save)

    assert ensure_path_saved(str(tmp_path / "." / "NORM.PDF"), "テスト") is True

    assert not window._session.dirty()
    assert _direct_shape_count(pdf) == 1
    window._session.close()


def test_release_handle_with_running_restyle_job_drops_partial_restyle_only(qtbot, tmp_path):
    """実行中の restyle を止めて途中経過(未保存扱い)を捨ててからハンドルを閉じる。例外にならず、ジョブは走り直しを予約する。"""
    pdf = tmp_path / "rel.pdf"
    make_pdf(pdf, pages=2)
    window = _make_window(qtbot, pdf)
    session = window._session

    def fake_job():
        try:
            yield
        except GeneratorExit:
            session.mark_dirty()  # 途中経過が残ったことを表す(iter_restyle_pii_annots と同じ)
            raise

    job = fake_job()
    next(job)
    window._pii_restyle_job = job

    window._release_session_handle()

    assert session.doc is None and not session.dirty()
    assert window._pii_restyle_job is None
    assert window._pii_restyle_timer is not None and window._pii_restyle_timer.isActive()  # 走り直しを予約
    window._abort_pii_restyle_job()
    session.close()
