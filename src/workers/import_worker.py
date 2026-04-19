"""Background worker that performs file import + Office/image conversion.

The worker processes a precomputed list of (source_path, dest_path) pairs.
Office documents are converted to PDF via MS Office COM (Windows) or
LibreOffice headless fallback.  The worker is cancellable between files;
in-flight conversions complete before the loop exits.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

_OFFICE_EXTS = {
    ".doc", ".docx", ".docm",
    ".xls", ".xlsx", ".xlsm",
    ".ppt", ".pptx",
}
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif",
}


def find_soffice() -> str | None:
    """Locate a LibreOffice soffice executable, if installed."""
    candidates: list[str] = []
    if sys.platform == "win32":
        for pf in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]:
            if pf:
                candidates.append(os.path.join(pf, "LibreOffice", "program", "soffice.exe"))
    else:
        candidates = ["/usr/bin/soffice", "/usr/local/bin/soffice", "/opt/libreoffice/program/soffice"]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    import shutil as sh
    return sh.which("soffice")


def _convert_via_office_com(src_path: str, dest_pdf_path: Path) -> None:
    """Convert Office file to PDF using COM automation (Windows + MS Office)."""
    import win32com.client  # type: ignore

    ext = os.path.splitext(src_path)[1].lower()
    abs_src = os.path.abspath(src_path)
    abs_dest = str(dest_pdf_path.resolve())

    if ext in {".doc", ".docx", ".docm"}:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(abs_src)
            doc.SaveAs(abs_dest, FileFormat=17)
            doc.Close(False)
        finally:
            word.Quit()
    elif ext in {".xls", ".xlsx", ".xlsm"}:
        excel = win32com.client.Dispatch("Excel.Application")
        excel.Visible = False
        try:
            wb = excel.Workbooks.Open(abs_src)
            wb.ExportAsFixedFormat(0, abs_dest)
            wb.Close(False)
        finally:
            excel.Quit()
    elif ext in {".ppt", ".pptx"}:
        ppt = win32com.client.Dispatch("PowerPoint.Application")
        try:
            presentation = ppt.Presentations.Open(abs_src, WithWindow=False)
            presentation.SaveAs(abs_dest, 32)
            presentation.Close()
        finally:
            ppt.Quit()
    else:
        raise ValueError(f"Unsupported Office extension: {ext}")


def _convert_via_libreoffice(src_path: str, dest_pdf_path: Path, soffice: str) -> None:
    """Convert Office file to PDF using LibreOffice headless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, src_path],
            capture_output=True,
            timeout=120,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(f"soffice failed: {result.stderr.decode(errors='replace')}")

        base_name = os.path.splitext(os.path.basename(src_path))[0]
        generated = os.path.join(tmpdir, f"{base_name}.pdf")
        if not os.path.exists(generated):
            raise RuntimeError("LibreOffice did not produce a PDF")
        shutil.move(generated, dest_pdf_path)


def _convert_office(src_path: str, dest_pdf_path: Path, soffice: str | None) -> None:
    if sys.platform == "win32":
        try:
            _convert_via_office_com(src_path, dest_pdf_path)
            return
        except Exception as e:
            logger.debug("Office COM conversion failed: %s", e)
    if soffice:
        _convert_via_libreoffice(src_path, dest_pdf_path, soffice)
        return
    raise RuntimeError("Office変換に必要なソフトウェアが見つかりません (MS Office または LibreOffice)")


def _convert_image(src_path: str, dest_pdf_path: Path) -> None:
    from src.utils.pdf_utils import images_to_pdf
    images_to_pdf([src_path], str(dest_pdf_path))


class ImportWorker(QThread):
    """Run a batch of (src, dest) imports on a background thread.

    Signals:
        progress_updated(current, total, filename): emitted before each file.
        file_imported(src, dest): emitted after a successful import.
        file_failed(src, reason): emitted after a failed import.
        finished_all(imported, failed, cancelled): emitted when the batch ends.
            - imported: list[str] of produced dest paths.
            - failed: list[tuple[str, str]] of (src, reason).
            - cancelled: bool — True when the user cancelled mid-batch.
    """

    progress_updated = pyqtSignal(int, int, str)
    file_imported = pyqtSignal(str, str)
    file_failed = pyqtSignal(str, str)
    finished_all = pyqtSignal(list, list, bool)

    def __init__(
        self,
        tree: list[tuple[str, str]],
        soffice: str | None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._tree = list(tree)
        self._soffice = soffice
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        co_initialized = False
        if sys.platform == "win32":
            try:
                import pythoncom  # type: ignore
                pythoncom.CoInitialize()
                co_initialized = True
            except Exception:
                logger.debug("pythoncom.CoInitialize failed", exc_info=True)

        imported: list[str] = []
        failed: list[tuple[str, str]] = []
        total = len(self._tree)

        try:
            for i, (src, dest) in enumerate(self._tree):
                if self._cancel.is_set():
                    break
                self.progress_updated.emit(i, total, os.path.basename(src))
                try:
                    dest_path = Path(dest)
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    ext = os.path.splitext(src)[1].lower()
                    if ext == ".pdf":
                        shutil.copy2(src, dest_path)
                    elif ext in _OFFICE_EXTS:
                        _convert_office(src, dest_path, self._soffice)
                    elif ext in _IMAGE_EXTS:
                        _convert_image(src, dest_path)
                    else:
                        raise RuntimeError(f"未対応の拡張子: {ext}")
                    imported.append(str(dest_path))
                    self.file_imported.emit(src, str(dest_path))
                except Exception as e:
                    reason = str(e)
                    failed.append((src, reason))
                    self.file_failed.emit(src, reason)
        finally:
            if co_initialized:
                try:
                    import pythoncom  # type: ignore
                    pythoncom.CoUninitialize()
                except Exception:
                    logger.debug("pythoncom.CoUninitialize failed", exc_info=True)
            self.progress_updated.emit(total, total, "")
            self.finished_all.emit(imported, failed, self._cancel.is_set())
