"""ライブラリに載せるファイルと種類の定義（src/utils/file_kinds.py）のテスト。"""
from __future__ import annotations

import os

import pytest

from src.utils import file_kinds
from src.views.theme import COLORS


@pytest.mark.parametrize(
    "name, kind",
    [
        ("a.pdf", "pdf"), ("A.PDF", "pdf"),
        ("b.xlsx", "excel"), ("b.xls", "excel"), ("b.xlsm", "excel"), ("b.csv", "excel"),
        ("c.docx", "word"), ("c.doc", "word"), ("c.docm", "word"),
        ("d.pptx", "powerpoint"), ("d.ppt", "powerpoint"),
        ("e.png", "image"), ("e.JPG", "image"), ("e.jpeg", "image"), ("e.svg", "image"),
        ("e.tiff", "image"), ("e.webp", "image"),
        ("f.zip", "zip"), ("f.7z", "zip"), ("f.rar", "zip"), ("f.tar", "zip"), ("f.gz", "zip"),
        ("g.txt", "text"), ("g.md", "text"), ("g.markdown", "text"),
        ("h.exe", "other"), ("noext", "other"),
    ],
)
def test_kind_of(name, kind):
    assert file_kinds.kind_of(name) == kind


def test_is_pdf():
    assert file_kinds.is_pdf("x/y.PDF")
    assert not file_kinds.is_pdf("x/y.pdf.txt")


def test_kind_order_and_labels():
    assert file_kinds.KIND_ORDER == (
        "folder", "pdf", "word", "excel", "powerpoint", "text", "image", "zip", "other"
    )
    assert file_kinds.kind_label("excel") == "Excel・スプレッドシート"


def test_icon_colors_exist_in_theme():
    for kind, (_icon, color_key) in file_kinds.KIND_ICONS.items():
        assert color_key in COLORS, kind


@pytest.mark.parametrize(
    "name",
    ["~$report.docx", "scratch.tmp", "desktop.ini", "Desktop.INI", "Thumbs.db",
     ".DS_Store", ".hidden.txt", "movie.crdownload", "x.partial", "backup~"],
)
def test_excluded_names(name):
    assert not file_kinds.is_library_name(name)
    assert not file_kinds.is_library_file(name)


@pytest.mark.parametrize("name", ["report.docx", "a.pdf", "memo.txt", "data.bin", "x.zip"])
def test_included_names(name):
    assert file_kinds.is_library_name(name)
    assert file_kinds.is_library_file(name)  # 存在しなくても名前だけで判断


@pytest.mark.skipif(os.name != "nt", reason="隠し属性は Windows のみ")
def test_hidden_attribute_excluded(tmp_path):
    import ctypes

    f = tmp_path / "visible-name.txt"
    f.write_text("x")
    assert file_kinds.is_library_file(str(f))
    ctypes.windll.kernel32.SetFileAttributesW(str(f), 0x2)
    assert not file_kinds.is_library_file(str(f))


def test_format_size():
    assert file_kinds.format_size(0) == "0 B"
    assert file_kinds.format_size(1023) == "1023 B"
    assert file_kinds.format_size(2048) == "2 KB"
    assert file_kinds.format_size(1024 * 1024 * 3 // 2) == "1.5 MB"
