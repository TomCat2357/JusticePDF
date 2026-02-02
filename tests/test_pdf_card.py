"""Tests for PDF card widget."""
import os
import tempfile
import fitz
import pytest
from PyQt6.QtWidgets import QApplication
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


def test_pdf_card_displays_filename(app, sample_pdf):
    """Test that PDF card displays the filename."""
    card = PDFCard(sample_pdf)
    assert os.path.basename(sample_pdf) in card.filename


def test_pdf_card_displays_page_count(app, sample_pdf):
    """Test that PDF card displays page count."""
    card = PDFCard(sample_pdf)
    assert card.page_count == 1


def test_pdf_card_is_selectable(app, sample_pdf):
    """Test that PDF card can be selected."""
    card = PDFCard(sample_pdf)
    assert not card.is_selected
    card.set_selected(True)
    assert card.is_selected
