"""Tests for src.utils.app_settings."""
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QSettings

from src.utils import app_settings


def test_resolve_relative_path_is_home_based():
    resolved = app_settings.resolve_pdfs_dir("PDFs")
    assert resolved == Path.home() / "PDFs"


def test_resolve_nested_relative_path_is_home_based():
    resolved = app_settings.resolve_pdfs_dir("work/cases")
    assert resolved == Path.home() / "work" / "cases"


def test_resolve_absolute_path_kept(tmp_path):
    resolved = app_settings.resolve_pdfs_dir(str(tmp_path / "lib"))
    assert resolved == tmp_path / "lib"


def test_resolve_tilde_expanded():
    resolved = app_settings.resolve_pdfs_dir("~/PDFs")
    assert resolved == Path.home() / "PDFs"


@pytest.fixture
def isolated_settings(tmp_path):
    """Point QSettings at a temp ini file so tests don't touch real config."""
    QCoreApplication.setOrganizationName("JusticePDFTest")
    QCoreApplication.setApplicationName("JusticePDFTest")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path),
    )
    QSettings().clear()
    yield
    QSettings().clear()


def test_merge_add_bookmarks_defaults_false(isolated_settings):
    assert app_settings.get_merge_add_bookmarks() is False


def test_merge_add_bookmarks_roundtrip(isolated_settings):
    app_settings.set_merge_add_bookmarks(True)
    assert app_settings.get_merge_add_bookmarks() is True
    app_settings.set_merge_add_bookmarks(False)
    assert app_settings.get_merge_add_bookmarks() is False


def test_pdfs_dir_defaults_to_documents(isolated_settings):
    assert app_settings.get_pdfs_dir() == app_settings.default_pdfs_dir()


def test_pdfs_dir_roundtrip_relative(isolated_settings):
    app_settings.set_pdfs_dir("MyPDFs")
    assert app_settings.get_pdfs_dir_raw() == "MyPDFs"
    assert app_settings.get_pdfs_dir() == Path.home() / "MyPDFs"


def test_pdfs_dir_roundtrip_absolute(isolated_settings, tmp_path):
    target = tmp_path / "lib"
    app_settings.set_pdfs_dir(str(target))
    assert app_settings.get_pdfs_dir() == target
