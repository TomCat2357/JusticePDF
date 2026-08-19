"""Helpers for inspecting and expanding ``.zip`` archives.

Used by the import pipeline so that a dropped/imported *password-less* zip is
expanded into a folder (named after the archive) whose contents then flow
through the normal Office/image -> PDF import conversion.

Password protected archives are detected up-front and reported back to the
caller rather than extracted; the rest of the import is left untouched.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# General-purpose bit flag 0 (0x1) marks an encrypted entry for both the
# legacy ZipCrypto scheme and WinZip AES, so it is sufficient to detect a
# password protected archive.
_ENCRYPTED_FLAG = 0x1
# General-purpose bit flag 11 (0x800) marks the filename as UTF-8.
_UTF8_NAME_FLAG = 0x800


class EncryptedZipError(Exception):
    """Raised when a zip cannot be expanded because it is password protected."""


class ZipImportPrep(NamedTuple):
    """Result of :func:`prepare_zip_imports`.

    Attributes:
        paths: Import paths with each plain zip replaced by its extracted
            folder (non-zip paths are passed through unchanged).
        temp_dirs: Temporary directories that hold extracted contents; the
            caller must delete them once the import has finished.
        encrypted: Base names of zips skipped because they were password
            protected.
        broken: Base names of ``.zip`` files that could not be opened/extracted.
    """

    paths: list[str]
    temp_dirs: list[str]
    encrypted: list[str]
    broken: list[str]


def is_encrypted_zip(path: str | Path) -> bool:
    """Return True if any entry in the zip is encrypted (password protected)."""
    with zipfile.ZipFile(path) as zf:
        return any(info.flag_bits & _ENCRYPTED_FLAG for info in zf.infolist())


_SJIS_LEAD_RANGES = ((0x81, 0x9F), (0xE0, 0xFC))
_SJIS_TRAIL_RANGES = ((0x40, 0x7E), (0x80, 0xFC))


def _is_sjis_lead_byte(b: int) -> bool:
    return any(lo <= b <= hi for lo, hi in _SJIS_LEAD_RANGES)


def _is_sjis_trail_byte(b: int) -> bool:
    return any(lo <= b <= hi for lo, hi in _SJIS_TRAIL_RANGES)


def _cp932_lenient_decode(raw: bytes) -> str:
    """Decode cp932 bytes byte-by-byte, tolerating a known corruption.

    Some tools "sanitize" archive member names by blindly rewriting every
    backslash byte (0x5C) to a forward slash (0x2F). 0x5C is also the second
    byte of many common cp932 kanji (e.g. "表" is 0x95 0x5C), so that rewrite
    turns the trailing byte of those characters into a stray 0x2F.

    A naive whole-string decode failure would otherwise fall back to raw
    cp437 mojibake, whose stray 0x2F/0x5C bytes look like path separators to
    :func:`_safe_relpath` and split one file into a bogus subfolder plus an
    oddly named (often hidden, dot-prefixed) file. Decoding byte-by-byte and
    always consuming both bytes of an attempted lead/trail pair — recovering
    the 0x5C corruption when that is the cause, else substituting a single
    replacement character — guarantees a corrupted trail byte can never leak
    out as a literal "/" or "\\" and be mistaken for a real separator.

    A lead byte is only treated as starting a pair when the following byte
    is a plausible trail byte, or is the specific corruption byte 0x2F.
    Otherwise the lead byte is decoded on its own (with a cp437 fallback,
    since cp437 is what non-cp932 archives such as Western-filename ones
    were decoded as before falling into this function). Without that guard,
    e.g. a cp437 name like "café.pdf" (0x82 = "é" in cp437, but also a valid
    cp932 lead byte) would have its trailing "." wrongly swallowed as an
    attempted — and failed — trail byte, corrupting the extension and
    silently dropping the file from import.
    """
    chars: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        next_byte = raw[i + 1] if i + 1 < n else None
        if _is_sjis_lead_byte(b) and next_byte is not None and (
            _is_sjis_trail_byte(next_byte) or next_byte == 0x2F
        ):
            pair = raw[i : i + 2]
            try:
                chars.append(pair.decode("cp932"))
            except UnicodeDecodeError:
                recovered = False
                if next_byte == 0x2F:
                    try:
                        chars.append(bytes((b, 0x5C)).decode("cp932"))
                        recovered = True
                    except UnicodeDecodeError:
                        pass
                if not recovered:
                    chars.append("\uFFFD")
            i += 2
            continue
        try:
            chars.append(bytes((b,)).decode("cp932"))
        except UnicodeDecodeError:
            try:
                chars.append(bytes((b,)).decode("cp437"))
            except UnicodeDecodeError:
                chars.append("\uFFFD")
        i += 1
    return "".join(chars)


def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """Best-effort decode of a member name, recovering Japanese filenames.

    When the UTF-8 flag is not set, :mod:`zipfile` decodes the raw bytes as
    cp437.  Archives created on Japanese Windows store names in cp932
    (Shift-JIS), so re-encode/decode to recover the original characters.

    If the whole-string decode fails (e.g. a corrupted trailing byte), fall
    back to a lenient byte-level decode instead of the raw cp437 mojibake —
    see :func:`_cp932_lenient_decode` for why that fallback matters.
    """
    if info.flag_bits & _UTF8_NAME_FLAG:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    try:
        return raw.decode("cp932")
    except UnicodeDecodeError:
        return _cp932_lenient_decode(raw)


def _safe_relpath(member_name: str) -> Path | None:
    """Return a sanitized relative path, or None if the entry should be skipped.

    Strips drive letters, leading separators, ``.`` and ``..`` components so a
    malicious archive cannot escape the destination directory ("zip slip").
    """
    member_name = member_name.replace("\\", "/")
    parts: list[str] = []
    for part in member_name.split("/"):
        # Drop empty segments, current-dir and any parent-dir traversal, and
        # any Windows drive component (e.g. ``C:``).
        if part in ("", ".", "..") or (len(part) == 2 and part[1] == ":"):
            continue
        parts.append(part)
    if not parts:
        return None
    return Path(*parts)


def extract_zip(zip_path: str | Path, dest_dir: str | Path) -> None:
    """Extract *zip_path* into *dest_dir*, preserving the internal structure.

    - Member names are decoded with a Japanese-Windows (cp932) fallback.
    - Path-traversal ("zip slip") entries are skipped.

    Raises:
        EncryptedZipError: If the archive is password protected.
    """
    dest_dir = Path(dest_dir)
    with zipfile.ZipFile(zip_path) as zf:
        if any(info.flag_bits & _ENCRYPTED_FLAG for info in zf.infolist()):
            raise EncryptedZipError(os.path.basename(str(zip_path)))
        for info in zf.infolist():
            rel = _safe_relpath(_decode_member_name(info))
            if rel is None:
                continue
            target = dest_dir / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def prepare_zip_imports(
    paths: list[str],
    *,
    temp_prefix: str = "justicepdf_zip_",
) -> ZipImportPrep:
    """Expand password-less ``.zip`` paths into temporary folders for import.

    Each plain zip is extracted into ``<temp>/<archive stem>`` so the importer
    creates a destination folder named after the archive.  Non-zip paths are
    returned unchanged.  Password protected and unreadable archives are
    reported via the result instead of being extracted.
    """
    out_paths: list[str] = []
    temp_dirs: list[str] = []
    encrypted: list[str] = []
    broken: list[str] = []

    for p in paths:
        if not p:
            continue
        if not (os.path.isfile(p) and os.path.splitext(p)[1].lower() == ".zip"):
            out_paths.append(p)
            continue

        name = os.path.basename(p)
        try:
            if not zipfile.is_zipfile(p):
                broken.append(name)
                continue
            if is_encrypted_zip(p):
                encrypted.append(name)
                continue
            tmp_root = tempfile.mkdtemp(prefix=temp_prefix)
            extract_dir = Path(tmp_root) / (Path(p).stem or "zip")
            extract_dir.mkdir(parents=True, exist_ok=True)
            extract_zip(p, extract_dir)
            temp_dirs.append(tmp_root)
            out_paths.append(str(extract_dir))
        except EncryptedZipError:
            encrypted.append(name)
        except (zipfile.BadZipFile, OSError) as exc:
            logger.debug("zip extraction failed for %s: %s", p, exc)
            broken.append(name)

    return ZipImportPrep(out_paths, temp_dirs, encrypted, broken)
