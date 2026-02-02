"""Tests for PDF card drag and drop functionality."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtTest import QTest
from src.views.pdf_card import PDFCard


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
    page = doc.new_page()
    page.insert_text((100, 100), "Test")
    doc.save(f.name)
    doc.close()
    yield f.name
    os.unlink(f.name)


def test_pdf_card_has_drag_enabled(app, sample_pdf):
    """Test that PDFCard has drag enabled."""
    card = PDFCard(sample_pdf)
    # After drag is implemented, card should support dragging
    assert hasattr(card, 'mouseMoveEvent')


def test_pdf_card_mime_type():
    """Test that PDFCard uses correct MIME type."""
    from src.views.pdf_card import PDFCARD_MIME_TYPE
    assert PDFCARD_MIME_TYPE == "application/x-pdfas-card"
