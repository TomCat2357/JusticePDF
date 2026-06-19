"""テキストボックス表示を Acrobat とそろえるための校正ハーネス。

JusticePDF のテキストボックスは PDF の FreeText アノテーションとして保存されるが、
JusticePDF 自身の編集画面(Qt 手描き)と Acrobat の再レイアウト表示で、フォント・
内側余白・行間が食い違い、折り返し位置や位置がずれる。

このスクリプトは:
  1. サンプル文字列入りのテキストボックスを実際の FreeText として書き込んだ PDF を生成
     -> Acrobat で開いて「折り返し位置・上端からの位置・行間」を確認する。
  2. JusticePDF の編集画面と同じ共有ヘルパ(freetext_canvas_font_families /
     _build_freetext_document / FREETEXT_TEXT_INSET_PT / FREETEXT_LINE_HEIGHT)で
     同じボックスを PNG にレンダリングする
     -> Acrobat 表示と並べて見比べる。

校正ループ:
  src/utils/constants.py の以下を調整して、PDF(Acrobat)と PNG(JusticePDF)が一致する
  まで本スクリプトを再実行する:
    - FREETEXT_TEXT_INSET_PT   … 箱端〜本文の余白(位置合わせ)
    - FREETEXT_LINE_HEIGHT     … 行間(複数行の間隔合わせ)
    - FREETEXT_CJK_FALLBACK_FAMILIES … 日本語フォント(折り返し位置の支配要因)

Run:
    uv run python dev\\calibrate_freetext_acrobat.py
出力:
    dev\\calibration_out\\freetext_sample.pdf   (Acrobat で開く)
    dev\\calibration_out\\freetext_justicepdf.png (JusticePDF 側の見た目)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Headless Qt — must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # PyMuPDF
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette
from PyQt6.QtGui import QAbstractTextDocumentLayout
from PyQt6.QtWidgets import QApplication

from src.utils.constants import (
    FREETEXT_LINE_HEIGHT,
    FREETEXT_TEXT_INSET_PT,
    freetext_canvas_font_families,
)
from src.utils.pdf_utils import FreeTextAnnotData, create_freetext_annot
from src.views.page_edit_window import _build_freetext_document, _pixel_size_to_pointf

OUT_DIR = Path(__file__).resolve().parent / "calibration_out"

# (rect[x0,y0,x1,y1] in PDF points, fontsize pt, text)
SAMPLE = "個人情報が事業者に行く旨付記することとしております。"
URL = "https://docs.google.com/document/d/1cdjkxECwXOh5fe6IepGwd9jWmKuXyhUi/edit"
BOXES = [
    ((40, 60, 300, 110), 14.0, SAMPLE),
    ((40, 130, 220, 200), 14.0, SAMPLE),          # 狭い幅で折り返しを誘発
    ((40, 220, 300, 320), 12.0, SAMPLE + "\n" + URL),  # 日本語＋URL の混在
]
PAGE_W, PAGE_H = 360.0, 360.0
SCALE = 2.0  # PNG レンダリング倍率(zoom 相当)


def build_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.save(str(path))
    doc.close()
    for rect, fontsize, text in BOXES:
        create_freetext_annot(
            str(path),
            FreeTextAnnotData(
                page_num=0, xref=0, rect=rect, content=text,
                fontsize=fontsize, text_color=(1.0, 0.0, 0.0),
                fill_color=None, border_color=None, border_width=0, opacity=1.0,
            ),
        )


def render_justicepdf_png(pdf_path: Path, png_path: Path) -> None:
    """編集画面と同じ共有ヘルパでボックスを描画(annots=False の素のページ＋手描き)。"""
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), annots=False)
    image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    doc.close()

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    for rect, fontsize, text in BOXES:
        x0, y0, x1, y1 = (v * SCALE for v in rect)
        paint_rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        font = painter.font()
        pixel_size = max(10, round(fontsize * SCALE))
        font.setPointSizeF(_pixel_size_to_pointf(pixel_size))
        font.setFamilies(list(freetext_canvas_font_families("Helv")))
        inset = FREETEXT_TEXT_INSET_PT * SCALE  # 枠線なし前提
        text_rect = paint_rect.adjusted(inset, inset, -inset, -inset)
        document = _build_freetext_document(text, font, text_rect.width())
        painter.save()
        painter.translate(text_rect.topLeft())
        painter.setClipRect(QRectF(0, 0, text_rect.width(), text_rect.height()))
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor(255, 0, 0))
        document.documentLayout().draw(painter, ctx)
        painter.restore()
    painter.end()
    image.save(str(png_path))


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT_DIR / "freetext_sample.pdf"
    png_path = OUT_DIR / "freetext_justicepdf.png"
    build_pdf(pdf_path)
    render_justicepdf_png(pdf_path, png_path)
    print("現在の共有定数:")
    print(f"  FREETEXT_TEXT_INSET_PT = {FREETEXT_TEXT_INSET_PT}")
    print(f"  FREETEXT_LINE_HEIGHT   = {FREETEXT_LINE_HEIGHT}")
    print(f"  canvas families        = {freetext_canvas_font_families('Helv')}")
    print()
    print(f"PDF (Acrobat で開く): {pdf_path}")
    print(f"PNG (JusticePDF 表示): {png_path}")
    print("両者を見比べ、折り返し位置・上端位置・行間が一致するまで")
    print("src/utils/constants.py の値を調整して再実行してください。")
    del app


if __name__ == "__main__":
    main()
