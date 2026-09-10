"""Ink(手書き)注釈の xref 列挙ユーティリティのテスト。"""
from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import list_ink_annot_xrefs
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def test_list_ink_annot_xrefs_returns_only_ink(tmp_path):
    pdf_path = tmp_path / "ink-list.pdf"
    make_pdf(pdf_path, pages=2)

    with fitz.open(str(pdf_path)) as doc:
        page0 = doc[0]
        ink_annot = page0.add_ink_annot([[(10, 10), (50, 50)]])
        ink_annot.update()
        ink_xref = ink_annot.xref

        rect_annot = page0.add_rect_annot(fitz.Rect(60, 60, 100, 100))
        rect_annot.update()

        page1 = doc[1]
        ink_annot2 = page1.add_ink_annot([[(20, 20), (60, 60)]])
        ink_annot2.update()
        ink_xref2 = ink_annot2.xref

        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    # ページ指定: そのページの Ink だけを返す(非 Ink は含まない)。
    assert list_ink_annot_xrefs(str(pdf_path), 0) == [ink_xref]
    assert list_ink_annot_xrefs(str(pdf_path), 1) == [ink_xref2]

    # ページ省略: 文書全体の Ink をまとめて返す。
    assert set(list_ink_annot_xrefs(str(pdf_path))) == {ink_xref, ink_xref2}


def test_list_ink_annot_xrefs_empty_when_no_ink(tmp_path):
    pdf_path = tmp_path / "ink-empty.pdf"
    make_pdf(pdf_path)

    with fitz.open(str(pdf_path)) as doc:
        page = doc[0]
        rect_annot = page.add_rect_annot(fitz.Rect(10, 10, 50, 50))
        rect_annot.update()
        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)

    assert list_ink_annot_xrefs(str(pdf_path), 0) == []


def test_list_ink_annot_xrefs_missing_file_returns_empty() -> None:
    assert list_ink_annot_xrefs("/nonexistent/path/does-not-exist.pdf") == []
