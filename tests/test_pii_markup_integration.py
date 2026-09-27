"""PII検出結果を通常のマーカー注釈として保存する統合テスト。

TextMarkupAnnotData.pii_entity にエンティティ種別を保持し、往復できることを
確認する。架空のダミーテキスト(山田太郎)を使う。
"""
from __future__ import annotations

import pytest

from src.pii.entity_types import get_highlight_color
from src.utils.pdf_utils import (
    MarkupType,
    TextMarkupAnnotData,
    create_markup_annot,
    create_markup_annots,
    delete_markup_annots,
    list_markup_annots,
    list_pii_markup_annots,
)
from tests.helpers import make_pdf

pytestmark = pytest.mark.usefixtures("qapp")


def test_pii_highlight_roundtrips_entity_metadata(tmp_path):
    pdf_path = tmp_path / "pii-highlight.pdf"
    make_pdf(pdf_path)

    quads = ((40.0, 60.0, 120.0, 76.0),)
    created = create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=quads,
            markup_type=MarkupType.HIGHLIGHT,
            color=get_highlight_color("PERSON"),
            opacity=0.4,
            pii_entity="PERSON",
        ),
    )
    assert created.pii_entity == "PERSON"

    listed = list_markup_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].pii_entity == "PERSON"

    pii_only = list_pii_markup_annots(str(pdf_path), 0)
    assert len(pii_only) == 1
    assert pii_only[0].xref == created.xref


def test_pii_highlight_roundtrips_matched_text(tmp_path):
    """検出時にマッチした文字列(pii_text)が結果一覧表示用に往復すること。"""
    pdf_path = tmp_path / "pii-highlight-text.pdf"
    make_pdf(pdf_path)

    created = create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((40.0, 60.0, 120.0, 76.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=get_highlight_color("PERSON"),
            opacity=0.4,
            pii_entity="PERSON",
            pii_text="山田太郎",
        ),
    )
    assert created.pii_text == "山田太郎"

    listed = list_pii_markup_annots(str(pdf_path), 0)
    assert listed[0].pii_text == "山田太郎"


def test_legacy_pii_highlight_without_pii_text_defaults_to_empty(tmp_path):
    """旧バージョンが作成した(pii_textを持たない)ハイライトも壊れず読めること。"""
    pdf_path = tmp_path / "pii-legacy.pdf"
    make_pdf(pdf_path)

    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((40.0, 60.0, 120.0, 76.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=get_highlight_color("PERSON"),
            opacity=0.4,
            pii_entity="PERSON",
        ),
    )
    listed = list_pii_markup_annots(str(pdf_path), 0)
    assert listed[0].pii_entity == "PERSON"
    assert listed[0].pii_text == ""


def test_manual_highlight_has_empty_pii_entity(tmp_path):
    """PIIドロワー以外(手動)で作ったマーカーは pii_entity が空文字のままであること。"""
    pdf_path = tmp_path / "manual-highlight.pdf"
    make_pdf(pdf_path)

    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((10.0, 10.0, 50.0, 20.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0),
            opacity=0.4,
        ),
    )

    assert list_markup_annots(str(pdf_path), 0)[0].pii_entity == ""
    assert list_pii_markup_annots(str(pdf_path), 0) == []


def test_batch_create_and_delete_markup_annots(tmp_path):
    pdf_path = tmp_path / "pii-batch.pdf"
    make_pdf(pdf_path, pages=2)

    items = [
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((10.0, 10.0, 60.0, 24.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=get_highlight_color("PERSON"),
            opacity=0.35,
            pii_entity="PERSON",
        ),
        TextMarkupAnnotData(
            page_num=1,
            xref=0,
            quads=((10.0, 30.0, 90.0, 44.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=get_highlight_color("PHONE_NUMBER"),
            opacity=0.35,
            pii_entity="PHONE_NUMBER",
        ),
    ]
    saved = create_markup_annots(str(pdf_path), items)
    assert len(saved) == 2
    assert {s.pii_entity for s in saved} == {"PERSON", "PHONE_NUMBER"}
    assert len(list_pii_markup_annots(str(pdf_path))) == 2

    refs = [(s.page_num, s.xref) for s in saved]
    deleted = delete_markup_annots(str(pdf_path), refs)
    assert deleted == 2
    assert list_pii_markup_annots(str(pdf_path)) == []


def test_create_markup_annots_skips_out_of_range_page(tmp_path):
    pdf_path = tmp_path / "pii-oob.pdf"
    make_pdf(pdf_path, pages=1)

    items = [
        TextMarkupAnnotData(
            page_num=5,  # out of range
            xref=0,
            quads=((0.0, 0.0, 10.0, 10.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 0.0, 0.0),
            pii_entity="PERSON",
        ),
    ]
    saved = create_markup_annots(str(pdf_path), items)
    assert saved == []
