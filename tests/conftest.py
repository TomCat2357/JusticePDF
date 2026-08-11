"""Test configuration for headless Qt runs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from src.utils import order_store


@pytest.fixture(autouse=True)
def _isolated_qsettings(tmp_path):
    """QSettings をテストごとの一時ディレクトリへ向け、実ユーザー設定を汚さない。"""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path / "qsettings"),
    )
    # setPath だけでは QSettings のプロセス内キャッシュ(format/scope/org/app が
    # キー)に載った前テストの値が新しいパスへ漏れてくるため、明示的に消す。
    QSettings().clear()


@pytest.fixture(autouse=True)
def _isolated_order_store(tmp_path, monkeypatch):
    """order_store の保存先をテストごとの一時ファイルへ向け、本番の %APPDATA% を汚さない。"""
    monkeypatch.setattr(order_store, "DEFAULT_STORE_PATH", tmp_path / "folder_order.json")


@pytest.fixture(autouse=True)
def _flush_qt_events_after_test():
    """Drain pending Qt events between tests.

    qtbot deletes widgets created during a test, but queued paint/expose
    events can outlive the C++ widget and fire later (during another test's
    event loop or at shutdown), crashing the offscreen platform plugin.
    Draining pending events while the widgets are still alive keeps teardown
    deterministic.
    """
    yield
    app = QApplication.instance()
    if app is None:
        return
    app.processEvents()
