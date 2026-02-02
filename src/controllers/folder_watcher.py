"""Folder watcher for monitoring PDF changes."""
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from PyQt6.QtCore import QObject, pyqtSignal


class PDFEventHandler(FileSystemEventHandler):
    """Handle file system events for PDF files."""

    def __init__(self, watcher: "FolderWatcher"):
        super().__init__()
        self._watcher = watcher

    def _is_pdf(self, path: str) -> bool:
        """Check if the path is a PDF file."""
        return path.lower().endswith(".pdf")

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_added.emit(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_removed.emit(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_modified.emit(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            if self._is_pdf(event.src_path):
                self._watcher.file_removed.emit(event.src_path)
            if self._is_pdf(event.dest_path):
                self._watcher.file_added.emit(event.dest_path)


class FolderWatcher(QObject):
    """Watch a folder for PDF file changes.

    Emits Qt signals when PDF files are added, removed, or modified.
    """

    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)

    def __init__(self, folder_path: str, parent=None):
        super().__init__(parent)
        self._folder_path = folder_path
        self._observer = Observer()
        self._handler = PDFEventHandler(self)

    @property
    def folder_path(self) -> str:
        """Get the watched folder path."""
        return self._folder_path

    def start(self) -> None:
        """Start watching the folder."""
        self._observer.schedule(self._handler, self._folder_path, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        """Stop watching the folder."""
        self._observer.stop()
        self._observer.join(timeout=1)

    def get_pdf_files(self) -> list[str]:
        """Get all PDF files currently in the folder.

        Returns:
            List of absolute paths to PDF files.
        """
        files = []
        for filename in os.listdir(self._folder_path):
            if filename.lower().endswith(".pdf"):
                files.append(os.path.join(self._folder_path, filename))
        return files
