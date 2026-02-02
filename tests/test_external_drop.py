"""Tests for external file drop functionality."""
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QUrl, QMimeData


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mime_data_with_pdf_url(app):
    """Test that MIME data with PDF URL is recognized."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/path/to/test.pdf")])
    assert mime.hasUrls()
    assert mime.urls()[0].toLocalFile().endswith('.pdf')
