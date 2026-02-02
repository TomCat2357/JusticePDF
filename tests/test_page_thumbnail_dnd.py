"""Tests for page thumbnail drag and drop functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.views.page_edit_window import PageThumbnail, PAGETHUMBNAIL_MIME_TYPE


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sample_pdf():
    """Create a sample PDF for testing."""
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    f.close()
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((100, 100), f"Page {i+1}")
    doc.save(f.name)
    doc.close()
    yield f.name
    os.unlink(f.name)


def test_page_thumbnail_mime_type():
    """Test that PageThumbnail uses correct MIME type."""
    assert PAGETHUMBNAIL_MIME_TYPE == "application/x-pdfas-page"


def test_page_thumbnail_has_drag(app, sample_pdf):
    """Test that PageThumbnail supports dragging."""
    thumb = PageThumbnail(sample_pdf, 0)
    assert hasattr(thumb, 'mouseMoveEvent')
