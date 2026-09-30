"""OCRで認識した文字を、ズームビューへ重ねて描く(表示専用)。

PDFへ埋め込んだOCRテキストは不透明度0の FreeText 注釈で、PDF側の描画では
1文字ずつの位置を指定できない(文字の字送りがフォント任せで、画像上の文字と
ずれて二重に見える)。そこで、画面に見せるときは注釈を描かず、認識した「行」の
矩形にちょうど収まるよう、行ごとに文字を拡縮して Qt で描く。

- 文字サイズ: 行矩形の高さから決める(縦は行の高さに合わせる)。
- 字送り: 行矩形の幅に合うよう、行全体を横方向に拡縮する(欧数字が混ざる行でも
  行頭・行末が画像上の文字にそろう)。
- フォント: 日本語グリフを持つフォントを優先し、無い文字は Qt のフォールバック。
- 縦書きの行(縦長の矩形)は、1文字ずつ均等に縦へ並べる。
色と透明度は呼び出し側(設定)から渡す。PDFの内容は一切変えない。
"""
from __future__ import annotations

from typing import Iterable, Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter

# 日本語を含む文字を描くための優先フォント(無ければ次、最後は Qt のフォールバック)。
OCR_OVERLAY_FONT_FAMILIES = [
    "Yu Gothic UI",
    "Meiryo UI",
    "Meiryo",
    "MS UI Gothic",
    "Noto Sans CJK JP",
    "sans-serif",
]
# 縦長(高さが幅のこの倍率を超える)で2文字以上の行は縦書きとみなす。
_VERTICAL_RATIO = 1.6
# 基準フォントのピクセルサイズ(この大きさで測って、行矩形へ拡縮する)。
_BASE_PIXEL_SIZE = 100
# 字面の高さがフォントの行送りのこの割合に満たない行(「ー」「-」だけ等)は字面合わせにしない。
_MIN_TIGHT_HEIGHT = 0.3


def overlay_font() -> QFont:
    font = QFont()
    font.setFamilies(OCR_OVERLAY_FONT_FAMILIES)
    font.setPixelSize(_BASE_PIXEL_SIZE)
    return font


def is_vertical_line(text: str, rect: QRectF) -> bool:
    return len(text) >= 2 and rect.width() > 0 and rect.height() > rect.width() * _VERTICAL_RATIO


def _draw_fitted(painter: QPainter, font: QFont, text: str, rect: QRectF) -> None:
    """``text`` の字面(インク)が ``rect`` にちょうど収まるよう拡縮して描く。

    OCRの行矩形は文字の字面に沿った矩形なので、フォントの行送り(ascent+descent)では
    なく、文字の実際の外接矩形(``tightBoundingRect``)を矩形へ合わせる。これで縦位置も
    画像上の文字にそろう。「ー」や「-」だけのように字面が極端に扁平な行は、伸ばしすぎて
    崩れないようフォントの行送りで合わせる。
    """
    if rect.width() <= 0 or rect.height() <= 0:
        return
    metrics = QFontMetricsF(font)
    advance = metrics.horizontalAdvance(text)
    height = metrics.height()
    if advance <= 0 or height <= 0:
        return
    tight = metrics.tightBoundingRect(text)
    if tight.width() > 0 and tight.height() >= _MIN_TIGHT_HEIGHT * height:
        painter.save()
        painter.translate(rect.left(), rect.top())
        painter.scale(rect.width() / tight.width(), rect.height() / tight.height())
        # tight は (0, baseline) 基準の外接矩形。左上を原点に合わせて描く。
        painter.drawText(QPointF(-tight.left(), -tight.top()), text)
        painter.restore()
        return
    painter.save()
    painter.translate(rect.left(), rect.top())
    painter.scale(rect.width() / advance, rect.height() / height)
    painter.drawText(QPointF(0.0, metrics.ascent()), text)
    painter.restore()


def paint_ocr_lines(
    painter: QPainter,
    lines: "Iterable[tuple[str, QRectF]]",
    color: "Sequence[float]",
    opacity: float,
) -> int:
    """``lines``(テキスト, 描画先の矩形[ウィジェット座標])を、指定の色と不透明度で描く。

    ``color`` は RGB 0.0-1.0、``opacity`` は 0.0(透明)〜1.0(不透明)。描いた行数を返す
    (不透明度0なら何も描かず 0)。
    """
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity <= 0.0:
        return 0
    text_color = QColor.fromRgbF(
        max(0.0, min(1.0, float(color[0]))),
        max(0.0, min(1.0, float(color[1]))),
        max(0.0, min(1.0, float(color[2]))),
        opacity,
    )
    font = overlay_font()
    painter.save()
    painter.setFont(font)
    painter.setPen(text_color)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    drawn = 0
    for text, rect in lines:
        text = str(text or "").strip()
        if not text or rect.isEmpty():
            continue
        if is_vertical_line(text, rect):
            cell = rect.height() / len(text)
            for i, ch in enumerate(text):
                if ch.isspace():
                    continue
                _draw_fitted(
                    painter, font, ch, QRectF(rect.left(), rect.top() + i * cell, rect.width(), cell)
                )
        else:
            _draw_fitted(painter, font, text, rect)
        drawn += 1
    painter.restore()
    return drawn
