"""Tests for password-less .zip expansion on drop/import."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from src.utils import zip_utils
from src.views import main_window, pdf_card

_PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


def _make_plain_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _make_encrypted_zip(path: Path) -> None:
    """Write a zip whose single entry has the 'encrypted' flag bit set.

    stdlib zipfile cannot encrypt content (and resets general-purpose flag bits
    while writing), so the flag is flipped directly in the raw header bytes —
    enough to exercise the password-protected detection path.
    """
    _make_plain_zip(path, {"secret.pdf": _PDF_BYTES})
    data = bytearray(path.read_bytes())
    # General purpose bit flag lives at +6 in the local file header (PK\x03\x04)
    # and +8 in the central directory header (PK\x01\x02).
    data[data.find(b"PK\x03\x04") + 6] |= zip_utils._ENCRYPTED_FLAG
    data[data.find(b"PK\x01\x02") + 8] |= zip_utils._ENCRYPTED_FLAG
    path.write_bytes(bytes(data))


# ─────────────────────────────────────────────────────────────────
# zip_utils unit tests
# ─────────────────────────────────────────────────────────────────

def test_is_encrypted_zip(tmp_path):
    plain = tmp_path / "plain.zip"
    _make_plain_zip(plain, {"a.pdf": _PDF_BYTES})
    assert zip_utils.is_encrypted_zip(plain) is False

    locked = tmp_path / "locked.zip"
    _make_encrypted_zip(locked)
    assert zip_utils.is_encrypted_zip(locked) is True


def test_extract_zip_preserves_structure(tmp_path):
    src = tmp_path / "src.zip"
    _make_plain_zip(src, {"a.pdf": _PDF_BYTES, "sub/b.pdf": _PDF_BYTES})
    dest = tmp_path / "out"
    zip_utils.extract_zip(src, dest)
    assert (dest / "a.pdf").read_bytes() == _PDF_BYTES
    assert (dest / "sub" / "b.pdf").read_bytes() == _PDF_BYTES


def test_extract_zip_raises_on_encrypted(tmp_path):
    locked = tmp_path / "locked.zip"
    _make_encrypted_zip(locked)
    with pytest.raises(zip_utils.EncryptedZipError):
        zip_utils.extract_zip(locked, tmp_path / "out")


def test_extract_zip_blocks_path_traversal(tmp_path):
    src = tmp_path / "evil.zip"
    _make_plain_zip(src, {"../escape.txt": b"x"})
    dest = tmp_path / "out"
    zip_utils.extract_zip(src, dest)
    # The '..' is stripped, so the entry stays inside dest and never escapes.
    assert not (tmp_path / "escape.txt").exists()
    assert (dest / "escape.txt").read_bytes() == b"x"


def test_decode_member_name_recovers_japanese():
    original = "資料/添付書類.pdf"
    info = zipfile.ZipInfo()
    # Simulate a legacy cp932 name stored without the UTF-8 flag: zipfile would
    # have decoded the raw cp932 bytes as cp437.
    info.filename = original.encode("cp932").decode("cp437")
    info.flag_bits = 0
    assert zip_utils._decode_member_name(info) == original


def test_decode_member_name_keeps_utf8_flagged():
    info = zipfile.ZipInfo()
    info.filename = "報告書.pdf"
    info.flag_bits = zip_utils._UTF8_NAME_FLAG
    assert zip_utils._decode_member_name(info) == "報告書.pdf"


def test_prepare_zip_imports_expands_plain_zip(tmp_path):
    zip_path = tmp_path / "事件記録.zip"
    _make_plain_zip(zip_path, {"a.pdf": _PDF_BYTES})
    other = tmp_path / "loose.pdf"
    other.write_bytes(_PDF_BYTES)

    prep = zip_utils.prepare_zip_imports([str(zip_path), str(other)])
    try:
        assert prep.encrypted == []
        assert prep.broken == []
        # The loose file is passed through; the zip is replaced by its folder.
        assert str(other) in prep.paths
        zip_folders = [p for p in prep.paths if p != str(other)]
        assert len(zip_folders) == 1
        folder = Path(zip_folders[0])
        assert folder.name == "事件記録"  # named after the archive stem
        assert (folder / "a.pdf").read_bytes() == _PDF_BYTES
        assert len(prep.temp_dirs) == 1
    finally:
        for d in prep.temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def test_prepare_zip_imports_reports_encrypted(tmp_path):
    locked = tmp_path / "locked.zip"
    _make_encrypted_zip(locked)
    prep = zip_utils.prepare_zip_imports([str(locked)])
    assert prep.encrypted == ["locked.zip"]
    assert prep.paths == []
    assert prep.temp_dirs == []


def test_prepare_zip_imports_reports_broken(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")
    prep = zip_utils.prepare_zip_imports([str(broken)])
    assert prep.broken == ["broken.zip"]
    assert prep.paths == []


# ─────────────────────────────────────────────────────────────────
# MainWindow integration
# ─────────────────────────────────────────────────────────────────

class _FakeWatcher(QObject):
    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)
    folder_added = pyqtSignal(str)
    folder_removed = pyqtSignal(str)

    def __init__(self, folder_path: str):
        super().__init__()
        self._folder_path = folder_path

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_subfolders(self) -> list[str]:
        return []


@pytest.fixture
def window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", _FakeWatcher)
    monkeypatch.setattr(main_window.MainWindow, "_load_existing_files", lambda self: None)
    monkeypatch.setattr(
        pdf_card,
        "get_pdf_card_info",
        lambda _path, _size: (QPixmap(), 1),
    )
    win = main_window.MainWindow()
    qtbot.addWidget(win)
    win.show()
    return win


def test_import_zip_creates_folder_with_pdfs(window, tmp_path):
    zip_path = tmp_path / "記録一式.zip"
    _make_plain_zip(zip_path, {"a.pdf": _PDF_BYTES, "sub/b.pdf": _PDF_BYTES})

    window._import_paths([str(zip_path)])

    worker = window._active_import_worker
    assert worker is not None
    worker.wait()
    QApplication.processEvents()

    folder = Path(window._work_dir) / "記録一式"
    assert (folder / "a.pdf").exists()
    assert (folder / "sub" / "b.pdf").exists()


def test_import_encrypted_zip_warns_and_skips(window, monkeypatch, tmp_path):
    locked = tmp_path / "locked.zip"
    _make_encrypted_zip(locked)

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        staticmethod(lambda _p, title, text: captured.update(title=title, text=text)),
    )

    window._import_paths([str(locked)])

    # Nothing is imported; the user is told why.
    assert window._active_import_worker is None
    assert captured["title"] == "ZIP展開"
    assert "パスワード付き" in captured["text"]
    assert not (Path(window._work_dir) / "locked").exists()
