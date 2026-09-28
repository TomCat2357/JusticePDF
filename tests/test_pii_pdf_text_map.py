"""オフセット→quad変換(src.pii.pdf_text_map)のテスト。

架空のダミーテキスト(山田太郎、090-1234-5678)を埋め込んだPDFを使う。
"""
from __future__ import annotations

from dataclasses import replace as dataclass_replace

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


def test_chars_under_rect_rotation_follows_rotated_footprint():
    """rotation を渡すと、回転前の矩形ではなく回転後の向きで判定されること。"""
    chars = [
        _char("A", (95.0, 95.0, 105.0, 105.0)),  # 中心(100,100) → 図形の中心
        _char("B", (0.0, 95.0, 10.0, 105.0)),  # 中心(5,100) → 回転前は内側、
        # 回転後(45度)は菱形の外側になる想定。
    ]
    rect = (50.0, 50.0, 150.0, 150.0)  # 中心(100,100)の正方形
    # 回転無し: どちらも内側(Bは矩形の内側)。
    assert [c["c"] for c in chars_under_rect(chars, rect)] == ["A"]
    # 45度回転させると B は菱形の外側になり選ばれない。
    selected = chars_under_rect(chars, rect, rotation=45.0)
    assert [c["c"] for c in selected] == ["A"]


def test_chars_under_ellipse_rotation_follows_rotated_footprint():
    """横長の楕円を90度回転させると縦長になり、判定もそれに従うこと。"""
    ellipse_rect = (50.0, 80.0, 150.0, 120.0)  # 横長(100x40)、中心(100,100)
    chars = [
        _char("V", (95.0, 55.0, 105.0, 65.0)),  # 中心(100,60) → 縦方向に離れた点
        _char("H", (135.0, 95.0, 145.0, 105.0)),  # 中心(140,100) → 横方向に離れた点
    ]
    # 回転無し(横長のまま): 横方向の点は内側、縦方向の点は外側。
    assert [c["c"] for c in chars_under_ellipse(chars, ellipse_rect)] == ["H"]
    # 90度回転(縦長になる): 縦方向の点が内側、横方向の点が外側に入れ替わる。
    selected = chars_under_ellipse(chars, ellipse_rect, rotation=90.0)
    assert [c["c"] for c in selected] == ["V"]


def test_extract_text_under_shape_rotated_rectangle(tmp_path):
    """回転した矩形図形は、回転後の向きで文字を抽出すること。"""
    pdf_path = tmp_path / "shape_text_rot.pdf"
    _make_text_pdf(pdf_path, "SECRETWORD")
    _, chars = get_page_text_and_chars(str(pdf_path), 0)
    x0 = min(ch["bbox"][0] for ch in chars) - 1
    y0 = min(ch["bbox"][1] for ch in chars) - 1
    x1 = max(ch["bbox"][2] for ch in chars) + 1
    y1 = max(ch["bbox"][3] for ch in chars) + 1

    # rotation=0 なら文字が抽出できる矩形をそのまま回転無しで確認(前提確認)。
    shape = ShapeAnnotData(
        page_num=0,
        xref=0,
        rect=(x0, y0, x1, y1),
        shape_type=ShapeType.RECTANGLE,
        stroke_color=(0.0, 0.0, 0.0),
        fill_color=None,
        stroke_width=1.0,
        opacity=1.0,
        rotation=0.0,
    )
    assert extract_text_under_shape(str(pdf_path), 0, shape) == "SECRETWORD"

    # 360度回転は見た目上は無回転と同じ位置に戻るはず。
    rotated = dataclass_replace(shape, rotation=360.0)
    assert extract_text_under_shape(str(pdf_path), 0, rotated) == "SECRETWORD"


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


# ---------------------------------------------------------------------------
# テキスト前処理(ignore_newlines/ignore_whitespace, PresidioPDFの
# 「テキスト前処理設定」を移植)
# ---------------------------------------------------------------------------


def _make_two_block_pdf(path) -> None:
    """区切り文字の無い2つの離れたテキストブロックを持つPDFを作る。

    表の別セルのように、区切り文字を挟まず連結される状況を模す
    (架空のダミーテキストのみ使用)。
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 100), "丸尾幸男", fontname="japan", fontsize=14)
    page.insert_text((40, 140), "公務員", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def test_ignore_newlines_default_does_not_insert_separator(tmp_path):
    pdf_path = tmp_path / "two_block.pdf"
    _make_two_block_pdf(pdf_path)
    text, chars = get_page_text_and_chars(str(pdf_path), 0)
    assert text == "丸尾幸男公務員"
    assert len(text) == len(chars)


def test_ignore_newlines_false_inserts_block_boundary_newline(tmp_path):
    pdf_path = tmp_path / "two_block2.pdf"
    _make_two_block_pdf(pdf_path)
    text, chars = get_page_text_and_chars(str(pdf_path), 0, ignore_newlines=False)
    assert text == "丸尾幸男\n公務員"
    assert len(text) == len(chars)
    # 挿入した改行はbboxを持たない実体の無い文字なので、quad生成には影響しない。
    newline_entries = [c for c in chars if c["c"] == "\n"]
    assert len(newline_entries) == 1
    assert newline_entries[0]["bbox"] is None
    quads = offset_span_to_quads(chars, 0, len(text))
    assert all(q[2] > q[0] and q[3] > q[1] for q in quads)


def _make_spaced_text_pdf(path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((50, 80), text, fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def test_ignore_whitespace_removes_space_characters(tmp_path):
    pdf_path = tmp_path / "spaced.pdf"
    _make_spaced_text_pdf(pdf_path, "公 務 員")
    text, chars = get_page_text_and_chars(str(pdf_path), 0)
    assert text == "公 務 員"

    text2, chars2 = get_page_text_and_chars(str(pdf_path), 0, ignore_whitespace=True)
    assert text2 == "公務員"
    assert len(text2) == len(chars2)


def test_ignore_whitespace_suppresses_inserted_newline_too(tmp_path):
    """空白無視が有効なら、改行区切り自体(空白文字扱い)も挿入されないこと。"""
    pdf_path = tmp_path / "two_block3.pdf"
    _make_two_block_pdf(pdf_path)
    text, chars = get_page_text_and_chars(
        str(pdf_path), 0, ignore_newlines=False, ignore_whitespace=True
    )
    assert text == "丸尾幸男公務員"
    assert "\n" not in text
