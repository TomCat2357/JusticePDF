"""Tests for PDF utilities."""
import os
import tempfile
import fitz
import pytest
from src.utils.pdf_utils import get_thumbnail, get_page_count, create_empty_pdf


@pytest.fixture
def sample_pdf():
    """Create a sample PDF for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, "sample.pdf")
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 100), "Test Page 1")
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 100), "Test Page 2")
        doc.save(pdf_path)
        doc.close()
        yield pdf_path


def test_get_thumbnail_returns_qpixmap(qapp, sample_pdf):
    """Test that get_thumbnail returns a QPixmap."""
    from PyQt6.QtGui import QPixmap
    thumbnail = get_thumbnail(sample_pdf, size=128)
    assert isinstance(thumbnail, QPixmap)
    assert not thumbnail.isNull()


def test_get_page_count(sample_pdf):
    """Test that get_page_count returns correct count."""
    count = get_page_count(sample_pdf)
    assert count == 2


def test_create_empty_pdf(qapp):
    """Test that create_empty_pdf creates a PDF with 1 blank page.

    Note: PyMuPDF cannot save PDFs with 0 pages,
    so create_empty_pdf creates a PDF with a single blank page.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "empty.pdf")
        create_empty_pdf(path)
        assert os.path.exists(path)
        assert get_page_count(path) == 1
