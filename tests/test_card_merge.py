"""Tests for PDF card merge functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from src.utils.pdf_utils import get_page_count, merge_pdfs


@pytest.fixture
def app():
    """Create QApplication for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def two_sample_pdfs():
    """Create two sample PDFs for testing."""
    files = []
    for i in range(2):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            filename = f.name
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((100, 100), f"Test {i}")
        doc.save(filename)
        doc.close()
        files.append(filename)
    yield files
    for f in files:
        if os.path.exists(f):
            os.unlink(f)


def test_merge_two_pdfs(app, two_sample_pdfs):
    """Test merging two PDFs."""
    pdf1, pdf2 = two_sample_pdfs

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out:
        output_path = out.name

    try:
        merge_pdfs(output_path, [pdf1, pdf2])
        assert get_page_count(output_path) == 2
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
