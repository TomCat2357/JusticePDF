"""Folder watcher for monitoring PDF and subfolder changes."""
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from PyQt6.QtCore import QObject, pyqtSignal


class PDFEventHandler(FileSystemEventHandler):
    """Handle file system events for PDF files and subfolders."""

    def __init__(self, watcher: "FolderWatcher"):
        super().__init__()
        self._watcher = watcher

    def _is_pdf(self, path: str) -> bool:
        return path.lower().endswith(".pdf")

    def _is_direct_child(self, path: str) -> bool:
        """Only emit for entries directly inside the watched folder."""
        try:
            parent = os.path.dirname(os.path.abspath(path))
            return os.path.normcase(parent) == os.path.normcase(
                os.path.abspath(self._watcher.folder_path)
            )
        except Exception:
            return True

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            if self._is_direct_child(event.src_path):
                self._watcher.folder_added.emit(event.src_path)
        elif self._is_pdf(event.src_path):
            self._watcher.file_added.emit(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            if self._is_direct_child(event.src_path):
                self._watcher.folder_removed.emit(event.src_path)
        elif self._is_pdf(event.src_path):
            self._watcher.file_removed.emit(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_pdf(event.src_path):
            self._watcher.file_modified.emit(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            if self._is_direct_child(event.src_path):
                self._watcher.folder_removed.emit(event.src_path)
            if self._is_direct_child(event.dest_path):
                self._watcher.folder_added.emit(event.dest_path)
            return
        if self._is_pdf(event.src_path):
            self._watcher.file_removed.emit(event.src_path)
        if self._is_pdf(event.dest_path):
            self._watcher.file_added.emit(event.dest_path)


class FolderWatcher(QObject):
    """Watch a folder for PDF file and subfolder changes.

    Emits Qt signals when PDF files or direct-child subfolders are
    added, removed, or modified.
    """

    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)
    folder_added = pyqtSignal(str)
    folder_removed = pyqtSignal(str)

    def __init__(self, folder_path: str, parent=None):
        super().__init__(parent)
        self._folder_path = folder_path
        self._observer = Observer()
        self._handler = PDFEventHandler(self)

    @property
    def folder_path(self) -> str:
        return self._folder_path

    def start(self) -> None:
        self._observer.schedule(self._handler, self._folder_path, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=1)

    def get_pdf_files(self) -> list[str]:
        files = []
        for filename in os.listdir(self._folder_path):
            if filename.lower().endswith(".pdf"):
                files.append(os.path.join(self._folder_path, filename))
        return files

    def get_subfolders(self) -> list[str]:
        dirs = []
        try:
            for name in os.listdir(self._folder_path):
                full = os.path.join(self._folder_path, name)
                if os.path.isdir(full):
                    dirs.append(full)
        except OSError:
            pass
        return dirs
