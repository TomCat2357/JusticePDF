"""Tests for main window drag and drop functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from src.views.main_window import MainWindow


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_accepts_drops(app):
    """Test that MainWindow accepts drops."""
    window = MainWindow()
    assert window._container.acceptDrops()
    window.close()
