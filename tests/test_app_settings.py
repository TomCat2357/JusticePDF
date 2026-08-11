from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap

from src.utils import app_settings
from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher


@pytest.fixture
def env(monkeypatch, tmp_path):
    """MainWindow をヘッドレスに構築できるよう世界を差し替える。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(
        pdf_card,
        "get_pdf_card_info",
        lambda _path, _size: (QPixmap(), 1),
    )
    return tmp_path


def test_library_dir_defaults_to_home_documents_pdfs(env):
    assert app_settings.library_dir() == env / "Documents" / "PDFs"


def test_set_library_dir_round_trips(env, tmp_path):
    custom = tmp_path / "custom_library"
    app_settings.set_library_dir(custom)

    assert app_settings.library_dir() == custom


def test_main_window_uses_configured_library_dir(env, qtbot, tmp_path):
    custom = tmp_path / "custom_library"
    app_settings.set_library_dir(custom)

    window = main_window.MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert Path(window._work_dir) == custom
    assert custom.is_dir()


def test_missing_configured_drive_falls_back_to_default(env, qtbot, tmp_path):
    # Windows でファイル名に使えない文字(?)を含むパス。ドライブ切断/共有消失時と
    # 同様に mkdir が OSError を送出するケースを再現する。
    invalid_path = tmp_path / "bad?dir"
    app_settings.set_library_dir(invalid_path)

    # 前提: このパスへの mkdir が実際に OSError を送出すること
    # (そうでなければ以下のフォールバック検証は意味を持たない)。
    with pytest.raises(OSError):
        invalid_path.mkdir(parents=True, exist_ok=True)

    # MainWindow() の構築自体がクラッシュせず、既定値へフォールバックする。
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert Path(window._work_dir) == env / "Documents" / "PDFs"

    # open_external_folder 側のフォールバックも同様に確認する。
    src = env / "folder"
    src.mkdir()
    win2 = main_window.MainWindow.open_external_folder(str(src))
    qtbot.addWidget(win2)

    assert win2 is not None
    assert Path(win2._work_dir) == (env / "Documents" / "PDFs" / "folder")
