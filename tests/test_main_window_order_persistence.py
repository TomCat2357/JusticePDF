"""フォルダごとのファイル並び順永続化（Phase 2 復元 / Phase 3 保存）のテスト。

設計: docs/folder-order-persistence-plan.md
実装: src/utils/order_store.py（変更不可）+ src/views/main_window*.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QPixmap

from src.utils import order_store
from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def make_window(monkeypatch, qtbot):
    """order_store の復元/保存ロジックを実際に通す MainWindow ファクトリ。

    他の MainWindow テストの window_factory と異なり、``_load_existing_files``
    を no-op に monkeypatch しない（Phase 2 の復元ロジックそのものを検証するため）。
    """
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(
        pdf_card, "get_pdf_card_info", lambda _path, _size: (QPixmap(), 1)
    )

    def _create(work_dir: Path) -> main_window.MainWindow:
        window = main_window.MainWindow(str(work_dir))
        qtbot.addWidget(window)
        window.show()
        return window

    return _create


def _basenames(paths: list[str]) -> list[str]:
    return [os.path.basename(p) for p in paths]


def _card_names(window) -> list[str]:
    return _basenames([c.pdf_path for c in window._cards])


def _folder_names(window) -> list[str]:
    return _basenames([fc.folder_path for fc in window._folder_cards])


# ─────────────────────────────────────────────────────────────────
# D&D 相当の並び替え -> 保存
# ─────────────────────────────────────────────────────────────────

def test_dnd_reorder_persists_order(make_window, monkeypatch, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    window = make_window(work_dir)
    assert _card_names(window) == ["a.pdf", "b.pdf", "c.pdf"]

    # "c.pdf" を先頭へ移動する実際の D&D 経路
    # (main_window_dragdrop.py:_handle_card_drop) を通す。
    # _get_drop_index() はカードのウィジェット座標に依存するため、
    # ヘッドレステストでは固定化してドロップ位置の計算自体はテスト対象外にする。
    monkeypatch.setattr(window, "_get_drop_index", lambda _pos: 0)
    c_path = str(work_dir / "c.pdf")
    window._handle_card_drop(c_path, QPoint(0, 0))

    assert _card_names(window) == ["c.pdf", "a.pdf", "b.pdf"]
    assert window._sort_order == "manual"
    assert window._order_dirty is True

    window._flush_order_save()
    assert window._order_dirty is False

    entry = order_store.load_folder_order(work_dir)
    assert entry is not None
    assert entry["manual_files"] == ["c.pdf", "a.pdf", "b.pdf"]
    assert entry["sort_order"] == "manual"


# ─────────────────────────────────────────────────────────────────
# 再構築で保存済み順が復元される
# ─────────────────────────────────────────────────────────────────

def test_reopening_window_restores_saved_manual_order(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    order_store.save_folder_order(
        work_dir,
        manual_files=["c.pdf", "a.pdf", "b.pdf"],
        manual_subfolders=[],
        sort_order="manual",
        sort_ascending=True,
    )

    window = make_window(work_dir)
    assert window._sort_order == "manual"
    assert _card_names(window) == ["c.pdf", "a.pdf", "b.pdf"]


# ─────────────────────────────────────────────────────────────────
# アプリ外での追加・削除の突き合わせ
# ─────────────────────────────────────────────────────────────────

def test_externally_added_file_appears_at_end(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    order_store.save_folder_order(
        work_dir,
        manual_files=["c.pdf", "a.pdf", "b.pdf"],
        manual_subfolders=[],
        sort_order="manual",
        sort_ascending=True,
    )

    # ウィンドウが閉じている間に、アプリの外(エクスプローラ等)で追加されたファイル。
    make_pdf(work_dir / "d.pdf")

    window = make_window(work_dir)
    assert _card_names(window) == ["c.pdf", "a.pdf", "b.pdf", "d.pdf"]


def test_externally_removed_file_is_dropped(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    order_store.save_folder_order(
        work_dir,
        manual_files=["c.pdf", "a.pdf", "b.pdf"],
        manual_subfolders=[],
        sort_order="manual",
        sort_ascending=True,
    )

    # ウィンドウが閉じている間に、アプリの外で削除されたファイル。
    (work_dir / "b.pdf").unlink()

    window = make_window(work_dir)
    assert _card_names(window) == ["c.pdf", "a.pdf"]


# ─────────────────────────────────────────────────────────────────
# 名前順を適用しても保存済みの手動順が破壊されない
# ─────────────────────────────────────────────────────────────────

def test_name_sort_does_not_clobber_saved_manual_order(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    order_store.save_folder_order(
        work_dir,
        manual_files=["c.pdf", "a.pdf", "b.pdf"],
        manual_subfolders=[],
        sort_order="manual",
        sort_ascending=True,
    )

    window = make_window(work_dir)
    assert _card_names(window) == ["c.pdf", "a.pdf", "b.pdf"]

    # 名前順（昇順）を適用 -> 保存。manual_files は書き換えられてはいけない。
    window._apply_sort("name", True)
    assert _card_names(window) == ["a.pdf", "b.pdf", "c.pdf"]
    window._flush_order_save()

    entry = order_store.load_folder_order(work_dir)
    assert entry["sort_order"] == "name"
    assert entry["sort_ascending"] is True
    assert entry["manual_files"] == ["c.pdf", "a.pdf", "b.pdf"]

    # 「手動順」に戻すと、元の手動順が復元される。
    window._apply_manual_sort()
    assert window._sort_order == "manual"
    assert _card_names(window) == ["c.pdf", "a.pdf", "b.pdf"]


# ─────────────────────────────────────────────────────────────────
# sort_order / sort_ascending の永続化
# ─────────────────────────────────────────────────────────────────

def test_sort_mode_restored_across_restart(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")

    window = make_window(work_dir)
    window._apply_sort("date", False)
    window._flush_order_save()
    window.close()

    window2 = make_window(work_dir)
    assert window2._sort_order == "date"
    assert window2._sort_ascending is False
    assert window2._sort_action_date_desc.isChecked() is True


def test_restored_name_sort_actually_reorders_cards(make_window, tmp_path):
    """sort_order/ascending の復元だけでなく、実際に _sort_cards() が
    再適用されてカード順に反映されることを確認する（date は mtime の
    分解能次第でタイになり得るため name で決定的に検証する）。
    """
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    order_store.save_folder_order(
        work_dir,
        manual_files=[],
        manual_subfolders=[],
        sort_order="name",
        sort_ascending=False,
    )

    window = make_window(work_dir)
    assert window._sort_order == "name"
    assert window._sort_ascending is False
    assert _card_names(window) == ["c.pdf", "b.pdf", "a.pdf"]


# ─────────────────────────────────────────────────────────────────
# closeEvent での即時フラッシュ（ウィンドウを閉じても順序が残る）
# ─────────────────────────────────────────────────────────────────

def test_close_event_flushes_pending_order_save(make_window, monkeypatch, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    make_pdf(work_dir / "a.pdf")
    make_pdf(work_dir / "b.pdf")
    make_pdf(work_dir / "c.pdf")

    window = make_window(work_dir)
    monkeypatch.setattr(window, "_get_drop_index", lambda _pos: 0)
    window._handle_card_drop(str(work_dir / "c.pdf"), QPoint(0, 0))
    assert window._order_dirty is True  # debounce timer still pending, no flush yet

    window.close()  # closeEvent must flush immediately, not rely on the debounce timer

    entry = order_store.load_folder_order(work_dir)
    assert entry is not None
    assert entry["manual_files"] == ["c.pdf", "a.pdf", "b.pdf"]


# ─────────────────────────────────────────────────────────────────
# Undo で名前順を取り消しても、保存済みのフォルダ手動順が壊れない
# ─────────────────────────────────────────────────────────────────

def test_undo_name_sort_preserves_manual_subfolder_order(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "zeta").mkdir()
    (work_dir / "alpha").mkdir()

    order_store.save_folder_order(
        work_dir,
        manual_files=[],
        manual_subfolders=["zeta", "alpha"],
        sort_order="manual",
        sort_ascending=True,
    )

    window = make_window(work_dir)
    assert _folder_names(window) == ["zeta", "alpha"]

    window._apply_sort("name", True)
    assert _folder_names(window) == ["alpha", "zeta"]

    window._on_undo()
    assert window._sort_order == "manual"
    assert _folder_names(window) == ["zeta", "alpha"]

    window._flush_order_save()
    entry = order_store.load_folder_order(work_dir)
    assert entry["manual_subfolders"] == ["zeta", "alpha"]


# ─────────────────────────────────────────────────────────────────
# 回帰防止: 保存済み順が無い新規フォルダのサブフォルダは従来通り名前順
# ─────────────────────────────────────────────────────────────────

def test_new_folder_subfolders_default_to_name_sort(make_window, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "zeta").mkdir()
    (work_dir / "alpha").mkdir()
    (work_dir / "mid").mkdir()

    window = make_window(work_dir)
    assert _folder_names(window) == ["alpha", "mid", "zeta"]
