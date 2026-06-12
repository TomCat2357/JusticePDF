from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    PdfWritePermissionError,
    TocEntry,
    get_pdf_toc,
    normalize_toc,
    rasterize_pdf,
    update_pdf_toc,
)
from src.utils import pdf_utils
from tests.helpers import make_pdf


def test_get_pdf_toc_empty(tmp_path):
    pdf_path = tmp_path / "empty.pdf"
    make_pdf(pdf_path, pages=5)
    assert get_pdf_toc(str(pdf_path)) == []


def test_set_get_toc_roundtrip_hierarchy(tmp_path):
    pdf_path = tmp_path / "toc.pdf"
    make_pdf(pdf_path, pages=5)

    entries = [
        TocEntry(1, "Chapter 1", 1),
        TocEntry(2, "Section 1.1", 2),
        TocEntry(2, "Section 1.2", 2),
        TocEntry(3, "Subsection 1.2.1", 3),
        TocEntry(1, "Chapter 2", 4),
    ]
    update_pdf_toc(str(pdf_path), entries)

    result = get_pdf_toc(str(pdf_path))
    assert result == entries


def test_update_toc_empty_clears_all(tmp_path):
    pdf_path = tmp_path / "clear.pdf"
    make_pdf(pdf_path, pages=5)

    update_pdf_toc(str(pdf_path), [TocEntry(1, "Only", 1)])
    assert len(get_pdf_toc(str(pdf_path))) == 1

    update_pdf_toc(str(pdf_path), [])
    assert get_pdf_toc(str(pdf_path)) == []


def test_normalize_toc_first_level_forced_to_one():
    result = normalize_toc([TocEntry(3, "Deep", 1)])
    assert result[0].level == 1


def test_normalize_toc_clamps_level_step():
    # 1 -> 3 (gap of 2) must be clamped to 1 -> 2
    result = normalize_toc([TocEntry(1, "A", 1), TocEntry(3, "B", 1)])
    assert [e.level for e in result] == [1, 2]


def test_normalize_toc_empty_title_fallback():
    result = normalize_toc([TocEntry(1, "   ", 1)])
    assert result[0].title == "(無題)"


def test_normalize_toc_clamps_page_range():
    result = normalize_toc(
        [TocEntry(1, "lo", 0), TocEntry(1, "hi", 99)], page_count=5
    )
    assert [e.page for e in result] == [1, 5]


def test_update_toc_with_broken_levels_does_not_raise(tmp_path):
    pdf_path = tmp_path / "broken.pdf"
    make_pdf(pdf_path, pages=5)

    # Starts at level 3 with a +2 gap afterwards — invalid for set_toc directly.
    update_pdf_toc(
        str(pdf_path),
        [TocEntry(3, "A", 1), TocEntry(5, "B", 2)],
    )
    result = get_pdf_toc(str(pdf_path))
    assert [e.level for e in result] == [1, 2]


def test_update_toc_page_is_one_based(tmp_path):
    pdf_path = tmp_path / "onebased.pdf"
    make_pdf(pdf_path, pages=5)

    update_pdf_toc(str(pdf_path), [TocEntry(1, "T", 2)])
    with fitz.open(str(pdf_path)) as doc:
        raw = doc.get_toc(simple=True)
    assert raw[0][2] == 2


def test_rasterize_pdf_preserves_bookmarks(tmp_path):
    src_path = tmp_path / "src.pdf"
    out_path = tmp_path / "out.pdf"
    make_pdf(src_path, pages=4)

    entries = [
        TocEntry(1, "Chapter 1", 1),
        TocEntry(2, "Section 1.1", 2),
        TocEntry(1, "Chapter 2", 3),
    ]
    update_pdf_toc(str(src_path), entries)

    rasterize_pdf(str(src_path), str(out_path), dpi=72)

    result = get_pdf_toc(str(out_path))
    assert [(e.level, e.title, e.page) for e in result] == [
        (1, "Chapter 1", 1),
        (2, "Section 1.1", 2),
        (1, "Chapter 2", 3),
    ]


def test_rasterize_pdf_without_bookmarks(tmp_path):
    src_path = tmp_path / "src.pdf"
    out_path = tmp_path / "out.pdf"
    make_pdf(src_path, pages=3)

    rasterize_pdf(str(src_path), str(out_path), dpi=72)

    assert get_pdf_toc(str(out_path)) == []


def test_update_toc_raises_permission_error_when_locked(tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked.pdf"
    make_pdf(pdf_path, pages=5)

    monkeypatch.setattr(
        pdf_utils.shutil,
        "move",
        lambda src, dst: (_ for _ in ()).throw(
            PermissionError(13, "Permission denied", dst)
        ),
    )

    with pytest.raises(PdfWritePermissionError):
        update_pdf_toc(str(pdf_path), [TocEntry(1, "T", 1)])
