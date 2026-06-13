from __future__ import annotations

import pytest

from src.utils.pdf_utils import (
    FreeTextAnnotData,
    ShapeAnnotData,
    ShapeType,
    create_callout,
    delete_annot_group,
    list_annot_group,
    list_freetext_annots,
    list_shape_annots,
)
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def test_create_callout_builds_grouped_text_brace_leader(tmp_path):
    pdf_path = tmp_path / "callout.pdf"
    make_pdf(pdf_path, width=400, height=500)

    text_data, brace, leader = create_callout(
        str(pdf_path),
        0,
        text_rect=(60.0, 60.0, 220.0, 110.0),
        target_point=(300.0, 260.0),
        text="ここに「至」を挿入",
    )

    assert text_data.group_id
    gid = text_data.group_id
    assert brace.group_id == gid
    assert leader.group_id == gid

    # 種類の確認
    assert isinstance(text_data, FreeTextAnnotData)
    assert text_data.content == "ここに「至」を挿入"
    assert isinstance(brace, ShapeAnnotData) and brace.shape_type == ShapeType.BRACKET
    assert brace.bracket_orientation == "horizontal"
    assert isinstance(leader, ShapeAnnotData) and leader.shape_type == ShapeType.LINE
    assert leader.arrow_end is True

    # ディスク上に3つの注釈（FreeText 1 + Shape 2）。
    fts = list_freetext_annots(str(pdf_path), 0)
    shapes = list_shape_annots(str(pdf_path), 0)
    assert len(fts) == 1
    assert len(shapes) == 2


def test_list_and_delete_annot_group(tmp_path):
    pdf_path = tmp_path / "callout-del.pdf"
    make_pdf(pdf_path, width=400, height=500)

    text_data, _brace, _leader = create_callout(
        str(pdf_path),
        0,
        text_rect=(60.0, 60.0, 220.0, 110.0),
        target_point=(300.0, 260.0),
        text="挿入",
    )
    gid = text_data.group_id

    xrefs = list_annot_group(str(pdf_path), 0, gid)
    assert len(xrefs) == 3

    deleted = delete_annot_group(str(pdf_path), 0, gid)
    assert deleted == 3
    assert list_freetext_annots(str(pdf_path), 0) == []
    assert list_shape_annots(str(pdf_path), 0) == []


def test_callout_target_above_text_uses_upward_brace(tmp_path):
    pdf_path = tmp_path / "callout-up.pdf"
    make_pdf(pdf_path, width=400, height=500)

    _text, brace, _leader = create_callout(
        str(pdf_path),
        0,
        text_rect=(60.0, 300.0, 220.0, 350.0),
        target_point=(140.0, 80.0),  # 本文より上
    )
    # 突起は上向き（side="left"）
    assert brace.bracket_side == "left"


def test_horizontal_bracket_roundtrips(tmp_path):
    """単体の横向き括弧も保存・復元できる。"""
    from src.utils.pdf_utils import create_shape_annot

    pdf_path = tmp_path / "hbracket.pdf"
    make_pdf(pdf_path, width=400, height=300)

    saved = create_shape_annot(
        str(pdf_path),
        ShapeAnnotData(
            page_num=0, xref=0, rect=(50.0, 100.0, 250.0, 120.0),
            shape_type=ShapeType.BRACKET,
            stroke_color=(0.0, 0.0, 0.0), fill_color=None,
            stroke_width=1.5, opacity=1.0,
            bracket_style="curly", bracket_side="right",
            bracket_orientation="horizontal",
        ),
    )
    assert saved.bracket_orientation == "horizontal"
    listed = list_shape_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].bracket_orientation == "horizontal"
