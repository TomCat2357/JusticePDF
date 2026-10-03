"""ページ一覧グリッドの配置計算(ウィジェットを持たない純粋な計算)。

仮想化したページグリッドは、全ページ分のウィジェットを持たず、見えているセルにだけ
ウィジェットを割り当てる。そのため「何番目のセルがどこにあるか」「この範囲に見えているのは
何ページ目か」「ドロップ位置はどのページの前後か」を、ウィジェットの geometry ではなく
行・列の算術だけで求める。ここにはその計算だけを置く(QRect / QPoint は値型として使う)。
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect

# ドロップ位置インジケータの幅(縦線)。
INDICATOR_WIDTH = 3
# インジケータをセルの左/右に置くときの隙間(従来の表示位置と同じ)。
INDICATOR_LEFT_OFFSET = 5
INDICATOR_RIGHT_OFFSET = 2


@dataclass(frozen=True)
class GridMetrics:
    """グリッド全体の寸法。セルは左上から行優先で並び、余白は全周同じ。"""

    cols: int
    item_w: int
    item_h: int
    margin: int
    spacing: int
    count: int

    def __post_init__(self) -> None:
        # 不正値でゼロ除算にならないよう最小値へ丸める(frozen なので object.__setattr__)。
        object.__setattr__(self, "cols", max(1, int(self.cols)))
        object.__setattr__(self, "item_w", max(1, int(self.item_w)))
        object.__setattr__(self, "item_h", max(1, int(self.item_h)))
        object.__setattr__(self, "margin", max(0, int(self.margin)))
        object.__setattr__(self, "spacing", max(0, int(self.spacing)))
        object.__setattr__(self, "count", max(0, int(self.count)))

    # --- 派生値 -----------------------------------------------------
    @property
    def pitch_x(self) -> int:
        return self.item_w + self.spacing

    @property
    def pitch_y(self) -> int:
        return self.item_h + self.spacing

    @property
    def rows(self) -> int:
        return -(-self.count // self.cols)

    @property
    def content_width(self) -> int:
        return 2 * self.margin + self.cols * self.item_w + (self.cols - 1) * self.spacing

    @property
    def content_height(self) -> int:
        if self.count == 0:
            return 2 * self.margin
        return 2 * self.margin + self.rows * self.pitch_y - self.spacing

    # --- セル -------------------------------------------------------
    def cell_rect(self, index: int) -> QRect:
        """index 番目のセルの矩形(グリッド座標)。範囲外の index も計算上の位置を返す。"""
        row, col = divmod(index, self.cols)
        return QRect(
            self.margin + col * self.pitch_x,
            self.margin + row * self.pitch_y,
            self.item_w,
            self.item_h,
        )

    def visible_range(self, y0: int, y1: int, overscan: int = 2) -> tuple[int, int]:
        """縦範囲 [y0, y1] にかかる行(前後に overscan 行を足す)のページ範囲 [start, stop)。"""
        if self.count == 0:
            return (0, 0)
        if y0 > y1:
            y0, y1 = y1, y0
        last_row = self.rows - 1
        ov = max(0, int(overscan))
        first = min(max((y0 - self.margin) // self.pitch_y, 0), last_row)
        last = min(max((y1 - self.margin) // self.pitch_y, 0), last_row)
        r0 = max(0, first - ov)
        r1 = min(last_row, last + ov)
        return (r0 * self.cols, min(self.count, (r1 + 1) * self.cols))

    def first_visible_row(self, y0: int) -> int:
        """縦位置 y0(ビューポートの上端)に少しでもかかっている最初の行。

        セルの下端が y0 より下にある最初の行(行間の隙間だけが見えている行は数えない)。
        """
        if self.count == 0:
            return 0
        row = (y0 - self.margin - self.item_h) // self.pitch_y + 1
        return min(max(row, 0), self.rows - 1)

    def pages_in_rect(self, rect: QRect) -> list[int]:
        """rect と交差するセルのページ番号(昇順)。余白や隙間だけに重なる矩形は空。"""
        if self.count == 0 or rect.isEmpty():
            return []
        top, bottom = rect.top(), rect.bottom()
        left, right = rect.left(), rect.right()
        m = self.margin
        row_lo = max(0, (top - m) // self.pitch_y)
        row_hi = min(self.rows - 1, (bottom - m) // self.pitch_y)
        col_lo = max(0, (left - m) // self.pitch_x)
        col_hi = min(self.cols - 1, (right - m) // self.pitch_x)
        rows = [
            r
            for r in range(row_lo, row_hi + 1)
            if top <= m + r * self.pitch_y + self.item_h - 1 and bottom >= m + r * self.pitch_y
        ]
        cols = [
            c
            for c in range(col_lo, col_hi + 1)
            if left <= m + c * self.pitch_x + self.item_w - 1 and right >= m + c * self.pitch_x
        ]
        result: list[int] = []
        for r in rows:
            for c in cols:
                i = r * self.cols + c
                if i < self.count:
                    result.append(i)
        return result

    # --- ドロップ位置 -----------------------------------------------
    def drop_index(self, pos: QPoint) -> int:
        """ドロップ位置 pos の挿入先インデックス(0..count)。"""
        return self.drop_target(pos)[0]

    def drop_target(self, pos: QPoint) -> tuple[int, bool]:
        """``(挿入先インデックス, 行末か)``。

        行末(その行の最後のセルより右)は「その行の末尾」を返す(文書末尾ではない)。
        行末のときの 2 つ目の値は True。次の行の先頭と同じインデックスになるため、
        インジケータを前の行の右側へ描くかどうかの区別に使う。
        """
        if self.count == 0:
            return (0, False)
        pad = self.spacing // 2
        m = self.margin
        row = (pos.y() - m + pad) // self.pitch_y
        if row < 0:
            return (0, False)
        if row >= self.rows:
            return (self.count, True)
        col = (pos.x() - m + pad) // self.pitch_x
        row_start = row * self.cols
        row_len = min(self.cols, self.count - row_start)
        if col < 0:
            return (row_start, False)
        if col >= row_len:
            return (row_start + row_len, True)
        i = row_start + col
        if pos.x() < self.cell_rect(i).center().x():
            return (i, False)
        # 行の最後のセルの右半分は行末扱い。
        return (i + 1, col == row_len - 1)

    def indicator_rect(self, index: int, *, row_end: bool = False) -> QRect | None:
        """挿入位置 index に出す縦線の矩形。ページが無いときは None。

        ``row_end`` が真(または index == count)なら index-1 のセルの右側、
        そうでなければ index のセルの左側。
        """
        if self.count == 0:
            return None
        index = max(0, min(index, self.count))
        if index == self.count or (row_end and index > 0):
            ref = self.cell_rect(index - 1)
            x = ref.right() + INDICATOR_RIGHT_OFFSET
        else:
            ref = self.cell_rect(index)
            x = ref.left() - INDICATOR_LEFT_OFFSET
        return QRect(x, ref.top(), INDICATOR_WIDTH, self.item_h)
