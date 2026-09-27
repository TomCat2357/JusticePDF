"""rasterize_pdf の黒塗り(black_fill_regions/hide_xrefs)オプションのテスト。

架空のダミーテキスト(SECRET NAME)を使う。
"""
from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils.export import rasterize_pdf

pytestmark = pytest.mark.usefixtures("qapp")


def _make_text_pdf(path, text: str = "SECRET NAME 090-1234-5678") -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), text, fontsize=18)
    doc.save(str(path))
    doc.close()


def test_rasterize_without_blackfill_keeps_default_behavior(tmp_path):
    """既存呼び出し(引数無し)の後方互換性を確認する。"""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    _make_text_pdf(src)

    rasterize_pdf(str(src), str(out), dpi=100)

    with fitz.open(str(out)) as doc:
        assert doc[0].get_text().strip() == ""  # ラスタライズ済みなので抽出不可


def test_rasterize_black_fill_removes_text_and_paints_black(tmp_path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    _make_text_pdf(src)

    region = (35.0, 80.0, 250.0, 115.0)
    rasterize_pdf(
        str(src),
        str(out),
        dpi=150,
        black_fill_regions={0: [region]},
    )

    with fitz.open(str(out)) as doc:
        page = doc[0]
        # ラスタライズなのでそもそもテキストは抽出できないが、黒塗り前後どちらも
        # 抽出不可であること自体は確認しておく(画像のみエクスポートの前提)。
        assert page.get_text().strip() == ""

        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        cx = int((region[0] + region[2]) / 2 * 2)
        cy = int((region[1] + region[3]) / 2 * 2)
        r, g, b = pix.pixel(cx, cy)[:3]
        assert (r, g, b) == (0, 0, 0)


def test_rasterize_black_fill_leaves_outside_region_unpainted(tmp_path):
    """黒塗り領域の外(ページ角)は黒く塗られていないことを確認する。"""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((40, 100), "SECRET", fontsize=18)
    doc.save(str(src))
    doc.close()

    rasterize_pdf(
        str(src),
        str(out),
        dpi=150,
        black_fill_regions={0: [(35.0, 80.0, 150.0, 115.0)]},
    )

    with fitz.open(str(out)) as out_doc:
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        r, g, b = pix.pixel(5, 5)[:3]  # ページ左上の角(白背景のまま)
        assert (r, g, b) != (0, 0, 0)


def _non_white_bbox(pdf_path, *, scale: int = 2):
    """ページを描画し、白(255,255,255)以外のピクセルの外接矩形(page座標系)を返す。

    表示座標系での「実際に見える位置」を、座標変換の仮定を挟まずに
    ピクセルそのものから求めるためのテスト用ヘルパー。
    """
    with fitz.open(pdf_path) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale))
    xs: list[int] = []
    ys: list[int] = []
    for y in range(pix.height):
        for x in range(pix.width):
            if pix.pixel(x, y)[:3] != (255, 255, 255):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale)


def test_rasterize_black_fill_on_rotated_page(tmp_path):
    """回転ページでも、get_page_charsから作った黒塗り領域が実際に文字を隠すこと。

    PyMuPDFの ``Page.draw_rect`` は(注釈のquadとは違い)ページの回転を
    ``get_page_chars`` と同じ座標系で扱う([1]で座標変換なしの実装を採用し、
    実測で検証済み)。本テストは、その前提そのものを回帰検知できるよう、
    座標変換の仮定を挟まず「文字が実際に見える位置(ピクセル)」を直接比較する:
    黒塗り前にテキストが見えていたピクセル範囲が、黒塗り後は真っ黒になって
    いることを確認する。
    """
    from src.utils.pdf_utils.rendering import get_page_chars

    src = tmp_path / "src_rotated.pdf"
    out = tmp_path / "out_rotated.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((40, 100), "SECRET", fontsize=18)
    page.set_rotation(90)
    doc.save(str(src))
    doc.close()

    # 黒塗り前: テキストが実際に見えているピクセル範囲を取得しておく。
    text_bbox = _non_white_bbox(src)
    assert text_bbox is not None, "回転ページ上にテキストが描画されていること"

    chars = get_page_chars(str(src), 0)
    assert chars, "回転ページから文字が抽出できていること"
    x0 = min(ch["bbox"][0] for ch in chars)
    y0 = min(ch["bbox"][1] for ch in chars)
    x1 = max(ch["bbox"][2] for ch in chars)
    y1 = max(ch["bbox"][3] for ch in chars)
    region = (x0 - 2, y0 - 2, x1 + 2, y1 + 2)

    rasterize_pdf(str(src), str(out), dpi=150, black_fill_regions={0: [region]})

    with fitz.open(str(out)) as out_doc:
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        cx = int((text_bbox[0] + text_bbox[2]) / 2.0 * 2)
        cy = int((text_bbox[1] + text_bbox[3]) / 2.0 * 2)
        assert pix.pixel(cx, cy)[:3] == (0, 0, 0)


def test_rasterize_black_fill_ellipse_paints_oval_not_corners(tmp_path):
    """black_fill_ellipses は外接矩形ではなく楕円の形に黒塗りすること。"""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(0, 0, 200, 200), color=(1, 1, 1), fill=(1, 1, 1))
    doc.save(str(src))
    doc.close()

    region = (20.0, 20.0, 180.0, 180.0)  # 円(中心100,100・半径80)
    rasterize_pdf(str(src), str(out), dpi=100, black_fill_ellipses={0: [region]})

    with fitz.open(str(out)) as out_doc:
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        # 中心は楕円の内側 → 黒
        assert pix.pixel(200, 200)[:3] == (0, 0, 0)
        # 外接矩形の角は楕円の外側 → 白のまま
        assert pix.pixel(45, 45)[:3] != (0, 0, 0)


def test_rasterize_hide_xrefs_hides_annotation_before_render(tmp_path):
    """hide_xrefs で指定した注釈が、ラスタライズ結果に写り込まないこと。"""
    src = tmp_path / "src.pdf"
    out_hidden = tmp_path / "out_hidden.pdf"
    out_visible = tmp_path / "out_visible.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(0, 0, 200, 200), color=(1, 1, 1), fill=(1, 1, 1))
    annot = page.add_highlight_annot(fitz.Rect(20, 20, 180, 180).quad)
    annot.set_colors(stroke=(1.0, 0.0, 0.0))
    annot.update()
    xref = annot.xref
    doc.save(str(src))
    doc.close()

    rasterize_pdf(str(src), str(out_visible), dpi=100)
    rasterize_pdf(str(src), str(out_hidden), dpi=100, hide_xrefs={0: [xref]})

    with fitz.open(str(out_visible)) as vis_doc, fitz.open(str(out_hidden)) as hidden_doc:
        vis_pix = vis_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        hidden_pix = hidden_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        cx, cy = 100 * 2, 100 * 2
        assert vis_pix.pixel(cx, cy)[:3] != hidden_pix.pixel(cx, cy)[:3]
