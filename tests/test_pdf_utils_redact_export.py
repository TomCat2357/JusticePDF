"""塗りつぶし部分のテキストを本当に削除するエクスポート(redact_pdf_remove_text)のテスト。

架空のダミーテキスト(SECRET NAME / KEEPME)を使う。PresidioPDFの run_mask
(``add_redact_annot`` + ``apply_redactions``)相当の挙動を検証する。
"""
from __future__ import annotations

import fitz
import pytest

from src.pii.pdf_text_map import get_page_text_and_chars
from src.utils.pdf_utils.export import redact_pdf_remove_text

pytestmark = pytest.mark.usefixtures("qapp")


def _word_rect(chars, text, word):
    start = text.index(word)
    end = start + len(word)
    xs = [chars[i]["bbox"][0] for i in range(start, end)]
    xs += [chars[i]["bbox"][2] for i in range(start, end)]
    ys = [chars[i]["bbox"][1] for i in range(start, end)]
    ys += [chars[i]["bbox"][3] for i in range(start, end)]
    return (min(xs), min(ys), max(xs), max(ys))


def test_redact_removes_target_text_but_keeps_other_text(tmp_path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.save(str(src))
    doc.close()

    text, chars = get_page_text_and_chars(str(src), 0)
    rect = _word_rect(chars, text, "SECRET")

    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [rect]})

    with fitz.open(str(out)) as out_doc:
        remaining = out_doc[0].get_text()
    assert "SECRET" not in remaining
    assert "KEEPME" in remaining


def test_redact_leaves_source_file_untouched(tmp_path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.save(str(src))
    doc.close()

    text, chars = get_page_text_and_chars(str(src), 0)
    rect = _word_rect(chars, text, "SECRET")
    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [rect]})

    with fitz.open(str(src)) as src_doc:
        assert "SECRET" in src_doc[0].get_text()


def test_redact_paints_black_over_removed_text(tmp_path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((40, 100), "SECRET", fontsize=18)
    doc.save(str(src))
    doc.close()

    text, chars = get_page_text_and_chars(str(src), 0)
    rect = _word_rect(chars, text, "SECRET")
    padded = (rect[0] - 1, rect[1] - 1, rect[2] + 1, rect[3] + 1)
    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [padded]})

    with fitz.open(str(out)) as out_doc:
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        cx = int((rect[0] + rect[2]) / 2 * 2)
        cy = int((rect[1] + rect[3]) / 2 * 2)
        assert pix.pixel(cx, cy)[:3] == (0, 0, 0)


def test_redact_removes_text_on_rotated_page(tmp_path):
    """回転ページでも add_redact_annot が正しい位置(derotation_matrix)に効くこと。

    draw_rect/draw_oval とは逆に、add_redact_annot は他の注釈と同じ未回転の
    内部座標系を扱う。表示座標系のまま(derotation_matrixを掛けずに)渡すと
    ずれた位置が削除されてしまう回帰を検知する。
    """
    src = tmp_path / "src_rot.pdf"
    out = tmp_path / "out_rot.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((40, 100), "SECRET", fontsize=18)
    page.set_rotation(90)
    doc.save(str(src))
    doc.close()

    text, chars = get_page_text_and_chars(str(src), 0)
    rect = _word_rect(chars, text, "SECRET")
    padded = (rect[0] - 2, rect[1] - 2, rect[2] + 2, rect[3] + 2)

    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [padded]})

    with fitz.open(str(out)) as out_doc:
        assert "SECRET" not in out_doc[0].get_text()
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        cx = int((rect[0] + rect[2]) / 2 * 2)
        cy = int((rect[1] + rect[3]) / 2 * 2)
        assert pix.pixel(cx, cy)[:3] == (0, 0, 0)


def test_redact_ellipse_removes_only_chars_inside_and_draws_oval(tmp_path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((40, 100), "SECRET", fontsize=18)
    page.insert_text((250, 100), "KEEPME", fontsize=18)
    doc.save(str(src))
    doc.close()

    text, chars = get_page_text_and_chars(str(src), 0)
    secret_rect = _word_rect(chars, text, "SECRET")
    ellipse_rect = (
        secret_rect[0] - 10,
        secret_rect[1] - 10,
        secret_rect[2] + 10,
        secret_rect[3] + 10,
    )

    redact_pdf_remove_text(str(src), str(out), redact_ellipses={0: [ellipse_rect]})

    with fitz.open(str(out)) as out_doc:
        remaining = out_doc[0].get_text()
        assert "SECRET" not in remaining
        assert "KEEPME" in remaining
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        cx = int((ellipse_rect[0] + ellipse_rect[2]) / 2 * 2)
        cy = int((ellipse_rect[1] + ellipse_rect[3]) / 2 * 2)
        assert pix.pixel(cx, cy)[:3] == (0, 0, 0)
        # 楕円の外接矩形の角は楕円の外側なので黒く塗られていないこと
        corner_x = int(ellipse_rect[0] * 2) + 1
        corner_y = int(ellipse_rect[1] * 2) + 1
        assert pix.pixel(corner_x, corner_y)[:3] != (0, 0, 0)


def test_redact_padding_does_not_eat_adjacent_cjk_text(tmp_path):
    """日本語には単語間のスペースが無いため、1ptパディングが隣接文字まで
    誤って削除しないことを確認する(架空の氏名「山田太郎」を対象にする)。
    """
    src = tmp_path / "src_cjk.pdf"
    out = tmp_path / "out_cjk.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text(
        (40, 100), "山田太郎の電話番号は0901234567890", fontname="japan", fontsize=14
    )
    doc.save(str(src))
    doc.close()

    full_text, chars = get_page_text_and_chars(str(src), 0)
    rect = _word_rect(chars, full_text, "山田太郎")
    padded = (rect[0] - 1.0, rect[1] - 1.0, rect[2] + 1.0, rect[3] + 1.0)

    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [padded]})

    with fitz.open(str(out)) as out_doc:
        remaining = out_doc[0].get_text()
    assert "山田太郎" not in remaining
    assert "電話番号" in remaining


def test_redact_ellipse_blanks_image_pixels_with_no_text_under_it(tmp_path):
    """写真・印影のように文字を持たない画像でも、楕円の下は画像ピクセルごと
    黒塗りされること(文字が無いと chars_under_ellipse が空になり、文字だけを
    見る経路では何もredactされない問題の回帰テスト)。

    画像ピクセルの黒塗りは(丸ではなく)楕円の外接矩形いっぱいに効く実装の
    ため、外接矩形の外は影響を受けないことも確認する。
    """
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
    # 中央に赤い画像(架空の印影の代わり)を1枚配置する。
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
    pix.set_rect(pix.irect, (255, 0, 0))
    image_rect = fitz.Rect(150, 50, 250, 150)
    page.insert_image(image_rect, pixmap=pix)
    doc.save(str(src))
    doc.close()

    ellipse_rect = (150.0, 50.0, 250.0, 150.0)  # 画像と同じ外接矩形(=楕円)
    redact_pdf_remove_text(str(src), str(out), redact_ellipses={0: [ellipse_rect]})

    with fitz.open(str(out)) as out_doc:
        pixmap = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        # 楕円(=外接矩形)の中心は黒塗りされていること。
        cx = int((ellipse_rect[0] + ellipse_rect[2]) / 2 * 2)
        cy = int((ellipse_rect[1] + ellipse_rect[3]) / 2 * 2)
        assert pixmap.pixel(cx, cy)[:3] == (0, 0, 0)
        # 画像の外接矩形の外側(画像が元々無かった白背景)は影響を受けないこと。
        outside_x = int((image_rect.x1 + 20) * 2)
        outside_y = int((image_rect.y1 + 20) * 2)
        assert pixmap.pixel(outside_x, outside_y)[:3] == (255, 255, 255)


def test_redact_rotated_rect_removes_only_text_inside_rotation_and_draws_it(tmp_path):
    """回転した矩形は、回転前の外接矩形ではなく実際に回転した四角形の内側
    にある文字だけを削除し、黒塗りもその回転した輪郭で描かれること
    (回転を無視して外接矩形の位置に黒塗りされてしまう不具合の回帰テスト)。
    """
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(0, 0, 300, 300), color=(1, 1, 1), fill=(1, 1, 1))
    # INSIDE: 回転した図形の中心付近(回転しても内側)。
    page.insert_text((140, 152), "INSIDE", fontsize=10)
    # XX: 回転前の外接矩形の角付近(回転後の菱形の外側)。文字ごとに中心点で
    # 判定するため、単語全体が確実に菱形の外側に収まる短い文字列にする。
    page.insert_text((100, 107), "XX", fontsize=8)
    doc.save(str(src))
    doc.close()

    # 100x100の正方形(中心150,150)を45度回転させた菱形。
    region = (100.0, 100.0, 200.0, 200.0, 45.0)
    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [region]})

    with fitz.open(str(out)) as out_doc:
        remaining = out_doc[0].get_text()
        assert "INSIDE" not in remaining
        assert "XX" in remaining

        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(300, 300)[:3] == (0, 0, 0)  # 中心 → 黒
        # 回転前の矩形の角(外接矩形の内側・菱形の外側)は塗られていないこと
        assert pix.pixel(int(102 * 2), int(102 * 2))[:3] != (0, 0, 0)


def test_redact_rotated_ellipse_follows_rotation(tmp_path):
    """回転した楕円も、回転後の向きで文字判定・黒塗りが行われること。"""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.draw_rect(fitz.Rect(0, 0, 400, 300), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((150, 152), "INSIDE", fontsize=10)
    page.insert_text((195, 152), "FAROUT", fontsize=10)
    doc.save(str(src))
    doc.close()

    region = (100.0, 130.0, 220.0, 170.0, 90.0)  # 横長(120x40)→90度回転で縦長
    redact_pdf_remove_text(str(src), str(out), redact_ellipses={0: [region]})

    with fitz.open(str(out)) as out_doc:
        remaining = out_doc[0].get_text()
        assert "INSIDE" not in remaining
        assert "FAROUT" in remaining

        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(int(160 * 2), int(150 * 2))[:3] == (0, 0, 0)
        assert pix.pixel(int(210 * 2), int(150 * 2))[:3] != (0, 0, 0)


def test_redact_removes_only_target_annot_keeps_normal_ones(tmp_path):
    """候補マーカー/塗りつぶし図形のxrefだけを消し、通常の注釈は残ること。"""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    candidate = page.add_highlight_annot(fitz.Rect(35, 80, 130, 115).quad)
    candidate.set_colors(stroke=(1.0, 0.0, 0.0))
    candidate.update()
    normal = page.add_highlight_annot(fitz.Rect(150, 80, 300, 115).quad)
    normal.set_colors(stroke=(1.0, 1.0, 0.0))
    normal.update()
    candidate_xref = candidate.xref
    doc.save(str(src))
    doc.close()

    text, chars = get_page_text_and_chars(str(src), 0)
    rect = _word_rect(chars, text, "SECRET")
    redact_pdf_remove_text(
        str(src),
        str(out),
        remove_xrefs={0: [candidate_xref]},
        redact_rects={0: [rect]},
    )

    with fitz.open(str(out)) as out_doc:
        page_out = out_doc[0]
        remaining = list(page_out.annots() or [])
        # garbage collection (save時)でxrefは振り直されるため、xrefそのものでは
        # 比較せず「候補は消え、通常の注釈は1件だけ残る」ことを内容で確認する。
        assert len(remaining) == 1
        stroke = remaining[0].colors.get("stroke")
        assert stroke is not None and tuple(round(c, 2) for c in stroke) == (1.0, 1.0, 0.0)


def test_redact_fill_color_paints_chosen_color_for_rect_and_ellipse(tmp_path):
    """fill_color 指定時、redact後の塗りつぶしが黒ではなく指定色になること。"""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(0, 0, 200, 200), color=(1, 1, 1), fill=(1, 1, 1))
    doc.save(str(src))
    doc.close()

    redact_pdf_remove_text(
        str(src),
        str(out),
        redact_rects={0: [(10.0, 10.0, 60.0, 60.0)]},
        redact_ellipses={0: [(100.0, 10.0, 190.0, 100.0)]},
        fill_color=(0.0, 0.0, 1.0),
    )

    with fitz.open(str(out)) as out_doc:
        pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        assert pix.pixel(70, 70)[:3] == (0, 0, 255)
        assert pix.pixel(290, 110)[:3] == (0, 0, 255)
