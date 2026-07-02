"""Test configuration for headless Qt runs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication


@pytest.fixture(autouse=True)
def _isolated_qsettings(tmp_path):
    """QSettings をテストごとの一時ディレクトリへ向け、実ユーザー設定を汚さない。"""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path / "qsettings"),
    )


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
