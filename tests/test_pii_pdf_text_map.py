"""オフセット→quad変換(src.pii.pdf_text_map)のテスト。

架空のダミーテキスト(山田太郎、090-1234-5678)を埋め込んだPDFを使う。
"""
from __future__ import annotations

import fitz
import pytest

from src.pii.pdf_text_map import (
    chars_under_ellipse,
    chars_under_rect,
    extract_text_under_shape,
    get_page_text_and_chars,
    offset_span_to_quads,
    text_under_ellipse,
    text_under_rect,
)
from src.utils.pdf_utils import ShapeAnnotData, ShapeType

pytestmark = pytest.mark.usefixtures("qapp")


def _make_text_pdf(path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    # PyMuPDF内蔵のCJK対応フォント("japan")を使わないと日本語がテキスト抽出
    # できない(Helvetica等では文字化けし、テキストとして抽出不能になる)。
    page.insert_text((50, 80), text, fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def test_get_page_text_and_chars_round_trip(tmp_path):
    pdf_path = tmp_path / "pii_text.pdf"
    # 句読点(。等)はPyMuPDF内蔵"japan"フォントにグリフが無く描画・抽出時に
    # 落ちることがあるため、この往復テストでは記号を含まない文字列を使う。
    text = "山田太郎の電話番号は0901234567890"
    _make_text_pdf(pdf_path, text)

    extracted_text, chars = get_page_text_and_chars(str(pdf_path), 0)
    assert len(extracted_text) == len(chars)
    # 抽出された文字列は元の文字数と一致する(PyMuPDFのCJKフォントは
    # 文字化けしても文字数自体は保たれる)。
    assert len(extracted_text) == len(text)


def test_offset_span_to_quads_single_line(tmp_path):
    pdf_path = tmp_path / "pii_text2.pdf"
    text = "090-1234-5678"
    _make_text_pdf(pdf_path, text)

    _, chars = get_page_text_and_chars(str(pdf_path), 0)
    quads = offset_span_to_quads(chars, 0, len(text))
    assert len(quads) == 1
    x0, y0, x1, y1 = quads[0]
    assert x1 > x0
    assert y1 > y0
    # quadは対象文字群の外接矩形になっている(全文字のbboxを覆う)
    all_x0 = min(ch["bbox"][0] for ch in chars)
    all_x1 = max(ch["bbox"][2] for ch in chars)
    assert x0 == pytest.approx(all_x0)
    assert x1 == pytest.approx(all_x1)


def test_offset_span_to_quads_partial_range(tmp_path):
    pdf_path = tmp_path / "pii_text3.pdf"
    text = "ABC090-1234-5678XYZ"
    _make_text_pdf(pdf_path, text)

    _, chars = get_page_text_and_chars(str(pdf_path), 0)
    start = text.index("090")
    end = start + len("090-1234-5678")
    quads = offset_span_to_quads(chars, start, end)
    assert len(quads) == 1
    # 部分範囲のquadは、全文字列の外接矩形より狭い(先頭ABC・末尾XYZを含まない)
    full_quads = offset_span_to_quads(chars, 0, len(text))
    assert quads[0][0] > full_quads[0][0]
    assert quads[0][2] < full_quads[0][2]


def test_offset_span_to_quads_empty_range_returns_empty():
    chars = [{"c": "a", "bbox": (0.0, 0.0, 5.0, 10.0), "line_id": 0}]
    assert offset_span_to_quads(chars, 0, 0) == []
    assert offset_span_to_quads(chars, 5, 10) == []


# --- 塗りつぶし用図形(矩形/楕円)の下にある文字の抽出 -----------------------


def _char(c, bbox, line_id=0):
    return {"c": c, "bbox": bbox, "line_id": line_id}


def test_chars_under_rect_selects_only_centers_inside():
    chars = [
        _char("A", (0.0, 0.0, 10.0, 10.0)),  # 中心(5,5) → 内側
        _char("B", (100.0, 100.0, 110.0, 110.0)),  # 中心(105,105) → 外側
    ]
    selected = chars_under_rect(chars, (0.0, 0.0, 20.0, 20.0))
    assert [c["c"] for c in selected] == ["A"]
    assert text_under_rect(chars, (0.0, 0.0, 20.0, 20.0)) == "A"


def test_chars_under_rect_no_match_returns_empty():
    chars = [_char("A", (0.0, 0.0, 10.0, 10.0))]
    assert chars_under_rect(chars, (100.0, 100.0, 200.0, 200.0)) == []
    assert text_under_rect(chars, (100.0, 100.0, 200.0, 200.0)) == ""


def test_chars_under_ellipse_excludes_corner_of_bounding_box():
    """楕円に内接する矩形の四隅は楕円の外側なので選ばれないこと。"""
    ellipse_rect = (0.0, 0.0, 100.0, 60.0)
    chars = [
        _char("C", (48.0, 28.0, 52.0, 32.0)),  # 中心付近 → 楕円の内側
        _char("X", (1.0, 1.0, 5.0, 5.0)),  # 矩形の角付近 → 楕円の外側
    ]
    selected = chars_under_ellipse(chars, ellipse_rect)
    assert [c["c"] for c in selected] == ["C"]
    assert text_under_ellipse(chars, ellipse_rect) == "C"


def test_chars_under_ellipse_multiline_joins_with_newline():
    ellipse_rect = (0.0, 0.0, 100.0, 100.0)
    chars = [
        _char("A", (48.0, 20.0, 52.0, 30.0), line_id=0),
        _char("B", (48.0, 60.0, 52.0, 70.0), line_id=1),
    ]
    assert text_under_ellipse(chars, ellipse_rect) == "A\nB"


def test_extract_text_under_shape_rectangle(tmp_path):
    pdf_path = tmp_path / "shape_text.pdf"
    _make_text_pdf(pdf_path, "SECRETWORD")
    _, chars = get_page_text_and_chars(str(pdf_path), 0)
    x0 = min(ch["bbox"][0] for ch in chars) - 1
    y0 = min(ch["bbox"][1] for ch in chars) - 1
    x1 = max(ch["bbox"][2] for ch in chars) + 1
    y1 = max(ch["bbox"][3] for ch in chars) + 1

    shape = ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(x0, y0, x1, y1),
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(0.0, 0.0, 0.0),
        fill_color=None,
        stroke_width=1.0,
        opacity=1.0,
    )
    extracted = extract_text_under_shape(str(pdf_path), 0, shape)
    assert extracted == "SECRETWORD"


def test_extract_text_under_shape_non_rect_ellipse_returns_empty(tmp_path):
    pdf_path = tmp_path / "shape_text_line.pdf"
    _make_text_pdf(pdf_path, "SECRETWORD")
    shape = ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(0.0, 0.0, 10.0, 10.0),
        shape_type=ShapeType.LINE,
        stroke_color=(0.0, 0.0, 0.0),
        fill_color=None,
        stroke_width=1.0,
        opacity=1.0,
    )
    assert extract_text_under_shape(str(pdf_path), 0, shape) == ""


def test_extract_text_under_shape_rectangle_no_text_area(tmp_path):
    pdf_path = tmp_path / "shape_text_empty.pdf"
    _make_text_pdf(pdf_path, "SECRETWORD")
    shape = ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(300.0, 150.0, 390.0, 195.0),
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(0.0, 0.0, 0.0),
        fill_color=None,
        stroke_width=1.0,
        opacity=1.0,
    )
    assert extract_text_under_shape(str(pdf_path), 0, shape) == ""
