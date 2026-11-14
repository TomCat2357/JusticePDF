"""テキスト（.txt / .md）ファイルの読み書き。文字コードと改行を判別して覚え、書き戻しで保つ。

読むときの文字コードの推測は :func:`text_to_pdf.read_text` と同じ順序
（UTF-8(BOM 可) → CP932 → 置換付き UTF-8）。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

_BOM = b"\xef\xbb\xbf"


@dataclass
class TextFile:
    text: str          # 改行は "\n" に正規化済み
    encoding: str      # "utf-8" / "utf-8-sig" / "cp932"
    newline: str       # 元の改行（"\n" / "\r\n" / "\r"）
    lossy: bool = False  # どの文字コードでも読めず、置換付きで読んだ（保存すると文字が失われる）


def read_head_bytes(path: str, limit: int) -> bytes:
    """先頭 ``limit`` バイトだけ読む（サムネイル用）。"""
    with open(path, "rb") as f:
        return f.read(limit)


def decode_bytes(raw: bytes, *, truncated: bool = False) -> tuple[str, str, bool]:
    """``(テキスト, 文字コード, 置換したか)``。

    ``truncated`` が真のとき、``raw`` は途中で切れているかもしれないので、末尾の
    不完全なマルチバイト文字を捨てて判定する（UTF-8 を CP932 と取り違えないため）。
    """
    candidates = [raw]
    if truncated:
        candidates += [raw[:-i] for i in (1, 2, 3) if len(raw) > i]
    for cand in candidates:
        try:
            text = cand.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return text.removeprefix("\ufeff"), ("utf-8-sig" if cand.startswith(_BOM) else "utf-8"), False
    for cand in candidates:
        try:
            return cand.decode("cp932"), "cp932", False
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8", True


def detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_file(path: str) -> TextFile:
    with open(path, "rb") as f:
        raw = f.read()
    text, encoding, lossy = decode_bytes(raw)
    return TextFile(normalize_newlines(text), encoding, detect_newline(text), lossy)


def encode_text(text: str, encoding: str, newline: str) -> bytes:
    """``text``（"\n" 改行）を元の文字コード・改行で符号化する。表せない文字があれば UnicodeEncodeError。"""
    data = text.replace("\n", newline).encode(encoding)
    return data


def write_bytes_atomic(path: str, data: bytes) -> None:
    """同じフォルダの一時ファイルへ書いてから置き換える（書き込み途中で壊さない）。"""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".~md-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
