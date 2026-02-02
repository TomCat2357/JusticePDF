"""Tests for page extraction via drag and drop."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.utils.pdf_utils import extract_pages, get_page_count


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def multi_page_pdf():
    """Create a multi-page PDF for testing."""
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    f.close()
    doc = fitz.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((100, 100), f"Page {i+1}")
    doc.save(f.name)
    doc.close()
    yield f.name
    os.unlink(f.name)


def test_extract_single_page(app, multi_page_pdf):
    """Test extracting a single page."""
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    output_path = f.name
    f.close()

    try:
        extract_pages(multi_page_pdf, output_path, [2])  # Extract page 3 (0-indexed)
        assert get_page_count(output_path) == 1
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
