"""塗りつぶし用図形(RECTANGLE/ELLIPSE + pii_entity)のデータモデルのテスト。

架空のダミーテキスト(SECRETWORD)を使う。通常の図形(pii_entity無し)とは
別扱いになること、下にある文字列(pii_text)が往復すること、
``list_pii_mask_shapes`` が通常図形を含まないことを確認する。
"""
from __future__ import annotations

from dataclasses import replace as dataclass_replace

import pytest

from src.utils.pdf_utils import (
    ShapeAnnotData,
    ShapeType,
    create_shape_annot,
    list_pii_mask_shapes,
    list_shape_annots,
    replace_shape_annot,
)
from tests.helpers import make_pdf

pytestmark = pytest.mark.usefixtures("qapp")


def _mask_rect(page_num=0, rect=(50.0, 50.0, 150.0, 100.0), entity="OTHER", text="") -> ShapeAnnotData:
    return ShapeAnnotData(
        page_num=page_num,
        xref=0,
        rect=rect,
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(0.1, 0.1, 0.1),
        fill_color=(0.0, 0.0, 0.0),
        stroke_width=1.2,
        opacity=0.35,
        pii_entity=entity,
        pii_text=text,
    )


def test_mask_shape_roundtrips_pii_entity_and_text(tmp_path):
    pdf_path = tmp_path / "mask-shape.pdf"
    make_pdf(pdf_path)

    created = create_shape_annot(str(pdf_path), _mask_rect(text="SECRETWORD"))
    assert created.pii_entity == "OTHER"
    assert created.pii_text == "SECRETWORD"

    listed = list_shape_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].pii_entity == "OTHER"
    assert listed[0].pii_text == "SECRETWORD"


def test_normal_shape_has_empty_pii_entity_and_is_excluded_from_mask_list(tmp_path):
    pdf_path = tmp_path / "normal-shape.pdf"
    make_pdf(pdf_path)

    create_shape_annot(
        str(pdf_path),
        ShapeAnnotData(
            page_num=0,
            xref=0,
            rect=(10.0, 10.0, 60.0, 40.0),
            shape_type=ShapeType.RECTANGLE,
            stroke_color=(0.0, 0.0, 1.0),
            fill_color=None,
            stroke_width=1.0,
            opacity=1.0,
        ),
    )
    listed = list_shape_annots(str(pdf_path), 0)
    assert listed[0].pii_entity == ""
    assert list_pii_mask_shapes(str(pdf_path), 0) == []


def test_list_pii_mask_shapes_excludes_normal_shapes(tmp_path):
    pdf_path = tmp_path / "mixed-shapes.pdf"
    make_pdf(pdf_path)

    create_shape_annot(str(pdf_path), _mask_rect())
    create_shape_annot(
        str(pdf_path),
        ShapeAnnotData(
            page_num=0,
            xref=0,
            rect=(200.0, 200.0, 260.0, 240.0),
            shape_type=ShapeType.ELLIPSE,
            stroke_color=(0.0, 0.5, 0.0),
            fill_color=None,
            stroke_width=1.0,
            opacity=1.0,
        ),
    )

    all_shapes = list_shape_annots(str(pdf_path), 0)
    assert len(all_shapes) == 2
    mask_only = list_pii_mask_shapes(str(pdf_path), 0)
    assert len(mask_only) == 1
    assert mask_only[0].pii_entity == "OTHER"


def test_mask_ellipse_shape_roundtrips(tmp_path):
    pdf_path = tmp_path / "mask-ellipse.pdf"
    make_pdf(pdf_path)

    data = ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(30.0, 30.0, 130.0, 90.0),
        shape_type=ShapeType.ELLIPSE,
        stroke_color=(0.1, 0.1, 0.1),
        fill_color=(0.0, 0.0, 0.0),
        stroke_width=1.2,
        opacity=0.35,
        pii_entity="PERSON",
        pii_text="山田太郎",
    )
    created = create_shape_annot(str(pdf_path), data)
    assert created.shape_type == ShapeType.ELLIPSE
    assert created.pii_entity == "PERSON"
    assert created.pii_text == "山田太郎"

    mask_only = list_pii_mask_shapes(str(pdf_path), 0)
    assert len(mask_only) == 1
    assert mask_only[0].shape_type == ShapeType.ELLIPSE


def test_mask_shape_flag_survives_geometry_replace(tmp_path):
    """移動/リサイズ(replace_shape_annot)を経てもpii_entity/pii_textが保たれること。

    ズームビューの移動/リサイズは既存図形を削除して
    ``dataclasses.replace(old, rect=new_rect)`` した内容で作り直す実装のため、
    それが塗りつぶし用図形の判定フラグを消してしまわないことを確認する
    (通常図形と塗りつぶし用図形とで別の編集コードパスを持たない設計の前提)。
    """
    pdf_path = tmp_path / "mask-shape-move.pdf"
    make_pdf(pdf_path)

    created = create_shape_annot(str(pdf_path), _mask_rect(text="SECRETWORD"))
    moved_data = dataclass_replace(created, rect=(80.0, 80.0, 180.0, 130.0))
    moved = replace_shape_annot(str(pdf_path), created.page_num, created.xref, moved_data)

    assert moved.rect == (80.0, 80.0, 180.0, 130.0)
    assert moved.pii_entity == "OTHER"
    assert moved.pii_text == "SECRETWORD"
    assert list_pii_mask_shapes(str(pdf_path), 0)[0].xref == moved.xref
