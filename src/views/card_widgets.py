"""ライブラリのカード（PDF / フォルダ）で共有する小さな部品。"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel

from src.views.theme import COLORS

# カードの寸法（サムネイル枠の外側）。枠(透明な 2px)+余白 6px を左右に持つ。
CARD_PAD = 6
CARD_BORDER = 2
CARD_EXTRA = 2 * (CARD_PAD + CARD_BORDER)


def card_width_for(thumb_size: int) -> int:
    """サムネイル枠の一辺からカードの幅を求める。"""
    return int(thumb_size) + CARD_EXTRA


def thumb_for_card_width(card_width: int) -> int:
    return max(1, int(card_width) - CARD_EXTRA)


def paper_pixmap(pix: QPixmap) -> QPixmap:
    """紙の外観: 1px の slate-300 の枠を付けたコピーを返す。"""
    if pix.isNull():
        return pix
    out = QPixmap(pix)
    dpr = out.devicePixelRatio() or 1.0
    painter = QPainter(out)
    pen = QPen(QColor(COLORS["slate-300"]))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(0.5, 0.5, out.width() / dpr - 1, out.height() / dpr - 1))
    painter.end()
    return out


def elide_lines(text: str, fm: QFontMetrics, width: int, max_lines: int = 2) -> str:
    """文字単位で折り返し、max_lines 行を超える分は最終行を「…」で省く。"""
    width = max(1, int(width))
    lines: list[str] = []
    cur = ""
    for i, ch in enumerate(text):
        if cur and fm.horizontalAdvance(cur + ch) > width:
            lines.append(cur)
            cur = ""
            if len(lines) == max_lines:
                # 残りは最終行の末尾へ寄せて省略する
                last = lines[-1] + text[i:]
                lines[-1] = fm.elidedText(last, Qt.TextElideMode.ElideRight, width)
                return "\n".join(lines)
        cur += ch
    if cur:
        lines.append(cur)
    return "\n".join(lines)


class ElidedNameLabel(QLabel):
    """名前を中央揃えで 2 行まで表示し、超える分は「…」で省くラベル。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setWordWrap(False)

    def set_full_text(self, text: str, width: int) -> None:
        self._full_text = text
        self.setFixedWidth(max(1, int(width)))
        self.setToolTip(text)
        self._relayout()

    def _relayout(self) -> None:
        self.ensurePolished()
        shown = elide_lines(self._full_text, self.fontMetrics(), self.width())
        self.setText(shown)
        self.setFixedHeight(max(1, self.fontMetrics().lineSpacing() * (shown.count("\n") + 1) + 2))

    def full_text(self) -> str:
        return self._full_text

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # QSS のフォントが効いた後で、もう一度折り返しを計算する
        self._relayout()
