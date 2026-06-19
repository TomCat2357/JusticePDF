from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    ShapeAnnotData,
    ShapeType,
    create_shape_annot,
)
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def test_transparent_border_rectangle_writes_empty_stroke_color(tmp_path):
    """透明枠（stroke_color=None）の四角形は /C を「空配列 []」として書き込むこと。

    /C キーが不在のままだと、MuPDF の外観ストリーム生成器が Square 注釈の
    既定枠色（赤）を焼き込み、印刷時に枠が赤く出る不具合になる。
    """
    pdf_path = tmp_path / "white.pdf"
    make_pdf(pdf_path, width=400, height=300, fill=(1.0, 1.0, 1.0))

    saved = create_shape_annot(
        str(pdf_path),
        ShapeAnnotData(
            page_num=0,
            xref=0,
            rect=(50.0, 50.0, 250.0, 200.0),
            shape_type=ShapeType.RECTANGLE,
            stroke_color=None,  # 枠線 = 透明
            fill_color=None,
            stroke_width=1.0,
            opacity=1.0,
        ),
    )

    # メタデータ上は透明（None）のままラウンドトリップする。
    assert saved.stroke_color is None

    # 保存済み Square 注釈の /C は「不在(null)」ではなく明示的な空配列 [] であること。
    with fitz.open(str(pdf_path)) as doc:
        key_type, key_value = doc.xref_get_key(saved.xref, "C")

    assert key_type == "array"
    assert key_value.replace(" ", "") == "[]"
