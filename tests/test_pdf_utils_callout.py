from __future__ import annotations

import pytest

from src.utils.pdf_utils import (
    FreeTextAnnotData,
    ShapeAnnotData,
    ShapeType,
    create_callout,
    delete_freetext_annot,
    list_freetext_annots,
    list_shape_annots,
)
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def test_create_callout_builds_single_freetext(tmp_path):
    pdf_path = tmp_path / "callout.pdf"
    make_pdf(pdf_path, width=400, height=500)

    text_data = create_callout(
        str(pdf_path),
        0,
        text_rect=(60.0, 60.0, 220.0, 110.0),
        target_point=(300.0, 260.0),
        text="ここに「至」を挿入",
    )

    # 単一の FreeText コールアウトとして生成される。
    assert isinstance(text_data, FreeTextAnnotData)
    assert text_data.content == "ここに「至」を挿入"
    assert len(text_data.callout_line) == 2
    assert text_data.callout_target == (300.0, 260.0)
    # 先端は挿入位置、末尾は本文ボックスへの接続点。
    assert text_data.callout_line[0] == (300.0, 260.0)

    # ディスク上には FreeText が 1 つだけ。図形オブジェクトは作られない。
    fts = list_freetext_annots(str(pdf_path), 0)
    shapes = list_shape_annots(str(pdf_path), 0)
    assert len(fts) == 1
    assert len(shapes) == 0


def test_callout_roundtrips_after_reopen(tmp_path):
    pdf_path = tmp_path / "callout-roundtrip.pdf"
    make_pdf(pdf_path, width=400, height=500)

    text_rect = (60.0, 60.0, 220.0, 110.0)
    create_callout(
        str(pdf_path),
        0,
        text_rect=text_rect,
        target_point=(300.0, 260.0),
        text="挿入",
    )

    # 再オープンしてもコールアウト点列・本文ボックス枠・矢印が保持される。
    listed = list_freetext_annots(str(pdf_path), 0)
    assert len(listed) == 1
    saved = listed[0]
    assert len(saved.callout_line) == 2
    assert saved.callout_target == (300.0, 260.0)
    # annot.rect の拡張に引きずられず、本文ボックス枠が復元される。
    assert saved.rect == pytest.approx(text_rect, abs=0.5)


def test_callout_can_be_deleted_as_single_object(tmp_path):
    pdf_path = tmp_path / "callout-del.pdf"
    make_pdf(pdf_path, width=400, height=500)

    text_data = create_callout(
        str(pdf_path),
        0,
        text_rect=(60.0, 60.0, 220.0, 110.0),
        target_point=(300.0, 260.0),
        text="挿入",
    )

    assert delete_freetext_annot(str(pdf_path), 0, text_data.xref) is True
    assert list_freetext_annots(str(pdf_path), 0) == []
    assert list_shape_annots(str(pdf_path), 0) == []


def test_callout_target_above_text_attaches_to_box_top(tmp_path):
    pdf_path = tmp_path / "callout-up.pdf"
    make_pdf(pdf_path, width=400, height=500)

    text_rect = (60.0, 300.0, 220.0, 350.0)
    text_data = create_callout(
        str(pdf_path),
        0,
        text_rect=text_rect,
        target_point=(140.0, 80.0),  # 本文より上
    )
    # ターゲットが上にあるので接続点は本文ボックスの上辺中央。
    box_attach = text_data.callout_line[1]
    assert box_attach[0] == pytest.approx((text_rect[0] + text_rect[2]) / 2.0)
    assert box_attach[1] == pytest.approx(text_rect[1], abs=0.5)


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
