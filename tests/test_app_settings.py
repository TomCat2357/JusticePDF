from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QPixmap

from src.utils import app_settings
from src.utils.constants import (
    HEAVY_PDF_FILE_SIZE_MB_RANGE,
    HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE,
    HEAVY_PDF_RENDER_BATCH_SIZE_RANGE,
    HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE,
    PIXMAP_CACHE_MAX_ENTRIES_RANGE,
)
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


# ---------------------------------------------------------------------------
# 大容量PDF(重量文書)の逐次処理設定
# ---------------------------------------------------------------------------

def test_heavy_pdf_settings_default_to_constants():
    assert app_settings.heavy_pdf_page_count_threshold() == 300
    assert app_settings.heavy_pdf_file_size_mb() == 150
    assert app_settings.heavy_pdf_file_size_bytes() == 150 * 1024 * 1024
    assert app_settings.heavy_pdf_widget_chunk_size() == 120
    assert app_settings.heavy_pdf_render_batch_size() == 1
    assert app_settings.pixmap_cache_max_entries() == 256


@pytest.mark.parametrize(
    "getter,setter",
    [
        (app_settings.heavy_pdf_page_count_threshold, app_settings.set_heavy_pdf_page_count_threshold),
        (app_settings.heavy_pdf_file_size_mb, app_settings.set_heavy_pdf_file_size_mb),
        (app_settings.heavy_pdf_widget_chunk_size, app_settings.set_heavy_pdf_widget_chunk_size),
        (app_settings.heavy_pdf_render_batch_size, app_settings.set_heavy_pdf_render_batch_size),
        (app_settings.pixmap_cache_max_entries, app_settings.set_pixmap_cache_max_entries),
    ],
)
def test_heavy_pdf_settings_round_trip(getter, setter):
    setter(999_999)  # 範囲外なのでクランプされる想定
    clamped = getter()
    setter(clamped)  # クランプ後の値で再設定しても変わらないことを確認
    assert getter() == clamped


@pytest.mark.parametrize(
    "setter,getter,value_range",
    [
        (
            app_settings.set_heavy_pdf_page_count_threshold,
            app_settings.heavy_pdf_page_count_threshold,
            HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE,
        ),
        (
            app_settings.set_heavy_pdf_file_size_mb,
            app_settings.heavy_pdf_file_size_mb,
            HEAVY_PDF_FILE_SIZE_MB_RANGE,
        ),
        (
            app_settings.set_heavy_pdf_widget_chunk_size,
            app_settings.heavy_pdf_widget_chunk_size,
            HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE,
        ),
        (
            app_settings.set_heavy_pdf_render_batch_size,
            app_settings.heavy_pdf_render_batch_size,
            HEAVY_PDF_RENDER_BATCH_SIZE_RANGE,
        ),
        (
            app_settings.set_pixmap_cache_max_entries,
            app_settings.pixmap_cache_max_entries,
            PIXMAP_CACHE_MAX_ENTRIES_RANGE,
        ),
    ],
)
def test_heavy_pdf_settings_clamp_out_of_range_values(setter, getter, value_range):
    lo, hi = value_range
    setter(lo - 1000)
    assert getter() == lo
    setter(hi + 1000)
    assert getter() == hi


def test_heavy_pdf_page_count_threshold_falls_back_on_corrupt_value():
    QSettings().setValue(app_settings.HEAVY_PDF_PAGE_COUNT_THRESHOLD_KEY, "not-a-number")
    assert app_settings.heavy_pdf_page_count_threshold() == 300


def test_heavy_pdf_file_size_bytes_reflects_mb_setting():
    app_settings.set_heavy_pdf_file_size_mb(7)
    assert app_settings.heavy_pdf_file_size_bytes() == 7 * 1024 * 1024
