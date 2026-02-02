"""Tests for folder watcher."""
import os
import tempfile
import time
import pytest
from PyQt6.QtCore import QCoreApplication
from src.controllers.folder_watcher import FolderWatcher


@pytest.fixture
def app():
    """Create QApplication for signal testing."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


@pytest.fixture
def temp_folder():
    """Create a temporary folder for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_folder_watcher_emits_file_added(app, temp_folder):
    """Test that file_added signal is emitted when a file is added."""
    watcher = FolderWatcher(temp_folder)
    watcher.start()

    added_files = []
    watcher.file_added.connect(lambda path: added_files.append(path))

    # Create a PDF file
    pdf_path = os.path.join(temp_folder, "test.pdf")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4")

    # Process events
    time.sleep(0.5)
    app.processEvents()

    watcher.stop()

    assert len(added_files) == 1
    assert added_files[0] == pdf_path


def test_folder_watcher_ignores_non_pdf(app, temp_folder):
    """Test that non-PDF files are ignored."""
    watcher = FolderWatcher(temp_folder)
    watcher.start()

    added_files = []
    watcher.file_added.connect(lambda path: added_files.append(path))

    # Create a non-PDF file
    txt_path = os.path.join(temp_folder, "test.txt")
    with open(txt_path, "w") as f:
        f.write("test")

    time.sleep(0.5)
    app.processEvents()

    watcher.stop()

    assert len(added_files) == 0
