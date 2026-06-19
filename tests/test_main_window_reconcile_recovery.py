"""watchdog がイベントを取りこぼしても、数秒（TTL）後にバックストップ・ポーリングが
ファイルの増減を再同期することを検証する。

根本原因: 内部操作で `busy` 登録されたパスは、解除が watchdog イベント発火時のみだった。
クラウド同期/ネットワーク/AV 干渉でイベントを取りこぼすと、そのパスが永久に `busy` のまま
残り、`_reconcile_with_disk` が二度と同期しなくなる。TTL を過ぎた登録は除外対象から外す
ことで自己回復させる。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap

from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher


PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


@pytest.fixture
def window_factory(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(
        pdf_card,
        "get_pdf_card_info",
        lambda _path, _size: (QPixmap(), 1),
    )

    def _create_window():
        window = main_window.MainWindow()
        qtbot.addWidget(window)
        window.show()
        return window

    return _create_window


def _expire_busy(window) -> None:
    """全 busy 刻印を TTL より前に遡らせ、次の reconcile で期限切れにする。"""
    for key in list(window._busy_since):
        window._busy_since[key] -= window._BUSY_TTL_SEC + 1.0


def test_leaked_internal_remove_recovers_after_ttl(window_factory):
    window = window_factory()
    doc = Path(window._work_dir) / "doc.pdf"
    doc.write_bytes(PDF_BYTES)
    window._add_card(str(doc))
    assert len(window._cards) == 1

    norm = window._normalize_path(str(doc))

    # 内部削除を登録するが watchdog 削除イベントは来ない（取りこぼし）。実ファイルは消す。
    window._register_internal_remove([str(doc)])
    doc.unlink()

    # まだ新しい間はカードを残す（in-flight race 保護を維持）。
    window._reconcile_with_disk()
    assert window._get_card_by_path(str(doc)) is not None
    assert len(window._cards) == 1

    # TTL を過ぎたら reconcile が処理を再開し、カードが消える（回復）。
    _expire_busy(window)
    window._reconcile_with_disk()
    assert window._get_card_by_path(str(doc)) is None
    assert len(window._cards) == 0
    # 期限切れ刻印は掃除され、_busy_since が無限肥大しない。
    assert norm not in window._busy_since


def test_leaked_internal_add_recovers_after_ttl(window_factory):
    window = window_factory()
    doc = Path(window._work_dir) / "doc.pdf"

    # 先に内部追加を登録（busy 化）してから実ファイルを作る。watchdog 追加イベントは来ない。
    window._register_internal_add([str(doc)])
    doc.write_bytes(PDF_BYTES)

    # まだ新しい間は追加しない（in-flight race 保護を維持）。
    window._reconcile_with_disk()
    assert window._get_card_by_path(str(doc)) is None
    assert len(window._cards) == 0

    # TTL を過ぎたら reconcile が処理を再開し、カードが追加される（回復）。
    _expire_busy(window)
    window._reconcile_with_disk()
    assert window._get_card_by_path(str(doc)) is not None
    assert len(window._cards) == 1


def test_late_watchdog_remove_after_expiry_does_not_clear_undo(window_factory, monkeypatch):
    window = window_factory()
    doc = Path(window._work_dir) / "doc.pdf"
    doc.write_bytes(PDF_BYTES)
    window._add_card(str(doc))
    norm = window._normalize_path(str(doc))

    window._register_internal_remove([str(doc)])
    doc.unlink()

    _expire_busy(window)
    window._reconcile_with_disk()
    assert len(window._cards) == 0
    # 期限切れでもパスは _internal_removes に残す（undo保護のため）。
    assert norm in window._internal_removes

    cleared: list[bool] = []
    monkeypatch.setattr(window, "_clear_undo_history", lambda: cleared.append(True))

    # 遅延 watchdog 削除イベントが後から到来しても undo 履歴を消さない
    # （パスが _internal_removes に在るので非消去ブランチを通る）。
    window._on_file_removed(str(doc))
    assert cleared == []
    assert norm not in window._internal_removes
