"""Tests for password-less .zip expansion on drop/import."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from src.utils import zip_utils
from src.views import main_window, pdf_card
from tests.helpers import FakeWatcher

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


def test_extract_zip_keeps_real_separator_before_corrupted_segment(tmp_path):
    """The lenient cp932 fallback must not eat a genuine directory separator.

    Only the trailing segment's "表" is corrupted (0x95 0x5c -> 0x95 0x2f);
    the "/" between "資料" and the filename is a real, uncorrupted separator
    and must still split into two path parts, not get merged into one name.
    """
    real_dir = "資料"
    original_name = "10_災害対策基本法の新旧対照表.pdf"
    corrupted_member = (
        real_dir.encode("cp932")
        + b"/"
        + original_name.encode("cp932").replace(b"\x95\x5c", b"\x95/")
    )
    placeholder = "P" * len(corrupted_member)
    src = tmp_path / "src.zip"
    _make_plain_zip(src, {placeholder: _PDF_BYTES})
    data = src.read_bytes().replace(placeholder.encode("ascii"), corrupted_member)
    src.write_bytes(data)

    dest = tmp_path / "out"
    zip_utils.extract_zip(src, dest)
    assert (dest / real_dir / original_name).read_bytes() == _PDF_BYTES
    assert list(dest.iterdir()) == [dest / real_dir]
    assert list((dest / real_dir).iterdir()) == [dest / real_dir / original_name]


def test_extract_zip_raises_on_encrypted(tmp_path):
    locked = tmp_path / "locked.zip"
    _make_encrypted_zip(locked)
    with pytest.raises(zip_utils.EncryptedZipError):
        zip_utils.extract_zip(locked, tmp_path / "out")


def test_extract_zip_recovers_backslash_sanitized_kanji_filename(tmp_path):
    """End-to-end regression for the corrupted-trailing-byte bug: extraction
    must produce one correctly named file, not an empty folder plus a hidden
    ".pdf" (see test_decode_member_name_recovers_backslash_sanitized_kanji).

    zipfile.writestr() auto-sets the UTF-8 flag for any non-ASCII filename,
    which would sidestep the cp932 fallback path entirely and defeat the
    point of this test. So, as with _make_encrypted_zip above, the archive
    is built with an ASCII placeholder name (keeping the UTF-8 flag off)
    and the raw member-name bytes are then patched directly to the
    corrupted cp932 bytes actually seen in the field.
    """
    original = "10_災害対策基本法の新旧対照表.pdf"
    corrupted_cp932 = original.encode("cp932").replace(b"\x95\x5c", b"\x95/")
    placeholder = "P" * len(corrupted_cp932)
    src = tmp_path / "src.zip"
    _make_plain_zip(src, {placeholder: _PDF_BYTES})
    data = src.read_bytes().replace(placeholder.encode("ascii"), corrupted_cp932)
    src.write_bytes(data)

    dest = tmp_path / "out"
    zip_utils.extract_zip(src, dest)
    assert (dest / original).read_bytes() == _PDF_BYTES
    assert list(dest.iterdir()) == [dest / original]


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


def test_decode_member_name_keeps_western_cp437_extension():
    """Regression guard for a bug introduced and caught during review: the
    byte-level fallback must not treat every cp932 lead byte (0x81-0x9F,
    0xE0-0xFC) as always starting a 2-byte pair. 0x82 is both a valid cp932
    lead byte and, in cp437, the accented "é" — a common byte in Western
    filenames stored without the UTF-8 flag. Naively pairing it with
    whatever follows (here "." before the extension) swallowed the
    following byte as a failed trail byte, corrupting the extension and
    causing the importer's extension check to silently drop the file - the
    same user-visible symptom this whole fix exists to eliminate, just for
    a different filename charset.
    """
    for raw, expected_name in (
        (b"caf\x82.pdf", "café.pdf"),
        (b"expos\x82-final.pdf", "exposé-final.pdf"),
    ):
        info = zipfile.ZipInfo()
        info.filename = raw.decode("cp437")
        info.flag_bits = 0
        decoded = zip_utils._decode_member_name(info)
        assert decoded == expected_name
        assert Path(decoded).suffix == ".pdf"


def test_decode_member_name_recovers_backslash_sanitized_kanji():
    """Regression: some zip builders rewrite every 0x5C byte to 0x2F, which
    corrupts the trailing byte of cp932 kanji like "表" (0x95 0x5C -> 0x95 0x2F).

    Before the fix, the whole-string decode failure fell back to raw cp437
    mojibake whose stray "/" was then mistaken for a path separator by
    _safe_relpath, splitting a single file into an empty folder plus a
    hidden ".pdf" file (see actual case: "10_...新旧対照表.pdf").
    """
    original = "10_災害対策基本法の新旧対照表.pdf"
    corrupted_cp932 = original.encode("cp932").replace(b"\x95\x5c", b"\x95/")
    info = zipfile.ZipInfo()
    info.filename = corrupted_cp932.decode("cp437")
    info.flag_bits = 0
    decoded = zip_utils._decode_member_name(info)
    assert decoded == original
    # The core safety property, independent of exact recovery: no stray
    # separator can leak out of a corrupted multi-byte sequence.
    assert zip_utils._safe_relpath(decoded) == Path(original)


def test_decode_member_name_undecodable_byte_never_leaks_separator():
    """A corruption pattern we can't recover must still never produce a
    literal "/" or "\\" that _safe_relpath would mistake for a directory
    boundary. 0x82 is a valid cp932 lead byte, but (0x82, 0x5c) is not a
    valid pair, so the 0x5c-recovery attempt itself fails here too -
    exercising the final placeholder fallback, not just the happy path."""
    raw = b"10_\x8d\xd0\x8aQ\x91\xce\x82/.pdf"
    info = zipfile.ZipInfo()
    info.filename = raw.decode("cp437")
    info.flag_bits = 0
    decoded = zip_utils._decode_member_name(info)
    assert "/" not in decoded and "\\" not in decoded
    assert len(zip_utils._safe_relpath(decoded).parts) == 1


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

@pytest.fixture
def window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
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
