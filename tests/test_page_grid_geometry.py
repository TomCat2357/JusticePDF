from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QRect

from src.views.page_grid_geometry import GridMetrics


def make(count: int, cols: int = 4, *, margin: int = 10, spacing: int = 10, w: int = 150, h: int = 150):
    return GridMetrics(cols=cols, item_w=w, item_h=h, margin=margin, spacing=spacing, count=count)


# --- 寸法 -----------------------------------------------------------


def test_rows_and_content_size():
    g = make(10, cols=4)
    assert g.rows == 3
    assert g.content_width == 2 * 10 + 4 * 150 + 3 * 10
    assert g.content_height == 2 * 10 + 3 * 160 - 10


def test_content_height_exact_row_multiple_and_single_row():
    assert make(8, cols=4).rows == 2
    assert make(8, cols=4).content_height == 20 + 2 * 160 - 10
    assert make(1, cols=4).rows == 1
    assert make(1, cols=4).content_height == 20 + 150


def test_empty_grid():
    g = make(0)
    assert g.rows == 0
    assert g.content_height == 2 * g.margin
    assert g.visible_range(0, 1000) == (0, 0)
    assert g.pages_in_rect(QRect(0, 0, 1000, 1000)) == []
    assert g.drop_index(QPoint(50, 50)) == 0
    assert g.drop_index(QPoint(-50, -50)) == 0
    assert g.indicator_rect(0) is None


def test_invalid_values_are_clamped():
    g = GridMetrics(cols=0, item_w=0, item_h=-3, margin=-1, spacing=-1, count=-5)
    assert g.cols == 1 and g.item_w == 1 and g.item_h == 1
    assert g.margin == 0 and g.spacing == 0 and g.count == 0


def test_single_column_geometry():
    g = make(3, cols=1)
    assert g.content_width == 20 + 150
    assert g.cell_rect(0) == QRect(10, 10, 150, 150)
    assert g.cell_rect(2) == QRect(10, 10 + 2 * 160, 150, 150)
    assert g.rows == 3


# --- cell_rect ------------------------------------------------------


def test_cell_rect_row_major_with_margin():
    g = make(10, cols=4)
    assert g.cell_rect(0) == QRect(10, 10, 150, 150)
    assert g.cell_rect(3) == QRect(10 + 3 * 160, 10, 150, 150)
    assert g.cell_rect(4) == QRect(10, 10 + 160, 150, 150)
    assert g.cell_rect(9) == QRect(10 + 160, 10 + 2 * 160, 150, 150)


def test_cell_rect_zero_margin_and_spacing():
    g = make(6, cols=3, margin=0, spacing=0, w=100, h=80)
    assert g.cell_rect(4) == QRect(100, 80, 100, 80)
    assert g.content_width == 300
    assert g.content_height == 160


def test_non_square_cells_use_separate_pitches():
    g = make(6, cols=3, w=100, h=60)
    assert g.pitch_x == 110 and g.pitch_y == 70
    assert g.cell_rect(4) == QRect(10 + 110, 10 + 70, 100, 60)


# --- visible_range --------------------------------------------------


def test_visible_range_basic_no_overscan():
    g = make(40, cols=4)  # 10 rows, pitch 160
    # y=0..100 -> row 0 only
    assert g.visible_range(0, 100, overscan=0) == (0, 4)
    # y=170 is in row 1
    assert g.visible_range(170, 175, overscan=0) == (4, 8)
    # spans rows 1..2
    assert g.visible_range(170, 340, overscan=0) == (4, 12)


def test_visible_range_overscan_clips_at_start_and_end():
    g = make(40, cols=4)
    assert g.visible_range(0, 100, overscan=2) == (0, 12)  # rows 0..2
    assert g.visible_range(g.content_height - 10, g.content_height, overscan=2) == (28, 40)


def test_visible_range_overscan_middle():
    g = make(40, cols=4)
    start, stop = g.visible_range(5 * 160 + 10, 5 * 160 + 20, overscan=2)
    assert (start, stop) == (3 * 4, 8 * 4)


def test_visible_range_partial_last_row_clamps_to_count():
    g = make(10, cols=4)  # rows 0,1,2 ; last row has 2
    assert g.visible_range(0, g.content_height, overscan=0) == (0, 10)
    assert g.visible_range(2 * 160 + 10, 2 * 160 + 20, overscan=0) == (8, 10)


def test_visible_range_beyond_content_and_negative():
    g = make(10, cols=4)
    start, stop = g.visible_range(100000, 100500, overscan=0)
    assert (start, stop) == (8, 10)
    assert g.visible_range(-500, -100, overscan=0) == (0, 4)


def test_visible_range_swapped_arguments():
    g = make(40, cols=4)
    assert g.visible_range(340, 170, overscan=0) == g.visible_range(170, 340, overscan=0)


def test_visible_range_cols_one():
    g = make(5, cols=1)
    assert g.visible_range(0, 10, overscan=0) == (0, 1)
    assert g.visible_range(0, 10, overscan=1) == (0, 2)


# --- pages_in_rect --------------------------------------------------


def test_pages_in_rect_single_cell():
    g = make(10, cols=4)
    assert g.pages_in_rect(QRect(20, 20, 5, 5)) == [0]


def test_pages_in_rect_spans_rows_and_cols():
    g = make(10, cols=4)
    # cells 1,2 in row 0 and 5,6 in row 1
    rect = QRect(g.cell_rect(1).center(), g.cell_rect(6).center()).normalized()
    assert g.pages_in_rect(rect) == [1, 2, 5, 6]


def test_pages_in_rect_gap_only_is_empty():
    g = make(10, cols=4)
    # the 10px gap between cell 0 and cell 1 (x: 160..169)
    assert g.pages_in_rect(QRect(161, 20, 5, 5)) == []
    # the gap between row 0 and row 1 (y: 160..169)
    assert g.pages_in_rect(QRect(20, 161, 5, 5)) == []
    # margin only
    assert g.pages_in_rect(QRect(0, 0, 5, 5)) == []


def test_pages_in_rect_touching_edges():
    g = make(10, cols=4)
    c = g.cell_rect(0)
    # one pixel overlap on the right edge
    assert g.pages_in_rect(QRect(c.right(), c.top(), 5, 5)) == [0]
    # starting at the first pixel of the gap -> nothing
    assert g.pages_in_rect(QRect(c.right() + 1, c.top(), 5, 5)) == []


def test_pages_in_rect_skips_indices_past_count():
    g = make(10, cols=4)
    whole = QRect(0, 0, g.content_width, g.content_height)
    assert g.pages_in_rect(whole) == list(range(10))


def test_pages_in_rect_clipped_to_grid():
    g = make(10, cols=4)
    assert g.pages_in_rect(QRect(-1000, -1000, 100, 100)) == []
    assert g.pages_in_rect(QRect(-1000, -1000, 1100, 1100)) == [0]
    assert g.pages_in_rect(QRect(10_000, 10_000, 50, 50)) == []


def test_pages_in_rect_empty_rect():
    g = make(10, cols=4)
    assert g.pages_in_rect(QRect()) == []


def test_pages_in_rect_matches_brute_force():
    g = make(23, cols=5)
    rects = [
        QRect(0, 0, 300, 300),
        QRect(155, 155, 20, 20),
        QRect(400, 100, 500, 400),
        QRect(30, 700, 800, 40),
    ]
    for rect in rects:
        expected = [i for i in range(g.count) if rect.intersects(g.cell_rect(i))]
        assert g.pages_in_rect(rect) == expected


# --- drop_index -----------------------------------------------------


def test_drop_index_before_first_cell():
    g = make(10, cols=4)
    assert g.drop_index(QPoint(0, 0)) == 0
    assert g.drop_index(QPoint(-100, -100)) == 0
    assert g.drop_index(QPoint(g.cell_rect(0).left() + 1, -50)) == 0


def test_drop_index_left_and_right_half_of_cell():
    g = make(10, cols=4)
    c = g.cell_rect(1)
    assert g.drop_index(QPoint(c.left() + 1, c.center().y())) == 1
    assert g.drop_index(QPoint(c.center().x() - 1, c.center().y())) == 1
    assert g.drop_index(QPoint(c.center().x(), c.center().y())) == 2
    assert g.drop_index(QPoint(c.right(), c.center().y())) == 2


def test_drop_index_second_row():
    g = make(10, cols=4)
    c = g.cell_rect(5)
    assert g.drop_index(QPoint(c.left() + 2, c.top() + 2)) == 5
    assert g.drop_index(QPoint(c.right() - 2, c.top() + 2)) == 6


def test_drop_index_in_gaps_is_covered_by_expanded_cells():
    g = make(10, cols=4)
    # gap between cell 0 and 1: left half of the gap belongs to cell 0 (right half of 0 => 1),
    # right half belongs to cell 1 (left half => 1)
    gap_x = g.cell_rect(0).right() + 1
    assert g.drop_index(QPoint(gap_x, 50)) == 1
    assert g.drop_index(QPoint(gap_x + 8, 50)) == 1
    # vertical gap between row 0 and row 1 belongs to one of them: no jump to the document end
    gap_y = g.cell_rect(0).bottom() + 1
    assert g.drop_index(QPoint(20, gap_y)) == 0
    assert g.drop_index(QPoint(20, gap_y + 8)) == 4


def test_drop_index_row_end_returns_end_of_that_row_not_document_end():
    g = make(10, cols=4)
    row0_right = g.cell_rect(3).right()
    # to the right of the last cell of row 0
    assert g.drop_index(QPoint(row0_right + 200, g.cell_rect(0).center().y())) == 4
    # the right half of the last cell in the row also ends the row
    assert g.drop_index(QPoint(row0_right - 2, g.cell_rect(3).center().y())) == 4
    # row 1
    assert g.drop_index(QPoint(row0_right + 200, g.cell_rect(4).center().y())) == 8


def test_drop_index_partial_last_row_end_is_document_end():
    g = make(10, cols=4)
    # row 2 only has cells 8 and 9
    y = g.cell_rect(8).center().y()
    assert g.drop_index(QPoint(g.cell_rect(9).right() + 300, y)) == 10
    assert g.drop_index(QPoint(g.cell_rect(3).center().x(), y)) == 10  # empty slot in the last row


def test_drop_index_below_last_row_returns_count():
    g = make(10, cols=4)
    assert g.drop_index(QPoint(50, g.content_height + 500)) == 10
    assert g.drop_index(QPoint(50, g.cell_rect(9).bottom() + 40)) == 10


def test_drop_index_left_of_margin_returns_row_start():
    g = make(10, cols=4)
    y = g.cell_rect(4).center().y()
    assert g.drop_index(QPoint(-50, y)) == 4


def test_drop_index_cols_one():
    g = make(3, cols=1)
    assert g.drop_index(QPoint(20, g.cell_rect(1).top() + 5)) == 1
    assert g.drop_index(QPoint(g.cell_rect(1).right() + 100, g.cell_rect(1).top() + 5)) == 2
    assert g.drop_index(QPoint(20, g.cell_rect(2).bottom() + 100)) == 3


def test_drop_index_zero_margin_zero_spacing():
    g = make(6, cols=3, margin=0, spacing=0, w=100, h=100)
    assert g.drop_index(QPoint(48, 10)) == 0
    assert g.drop_index(QPoint(49, 10)) == 1
    assert g.drop_index(QPoint(299, 10)) == 3  # row end
    assert g.drop_index(QPoint(10, 150)) == 3


def test_drop_index_with_odd_spacing_never_raises():
    g = make(10, cols=4, spacing=9)
    for x in range(-20, g.content_width + 20, 7):
        for y in range(-20, g.content_height + 20, 7):
            assert 0 <= g.drop_index(QPoint(x, y)) <= 10


def test_drop_target_row_end_flag():
    g = make(10, cols=4)
    y = g.cell_rect(0).center().y()
    assert g.drop_target(QPoint(g.cell_rect(3).right() + 50, y)) == (4, True)
    assert g.drop_target(QPoint(g.cell_rect(1).left() + 1, y)) == (1, False)
    assert g.drop_target(QPoint(g.cell_rect(0).center().x(), y)) == (1, False)
    assert g.drop_target(QPoint(g.cell_rect(0).left(), -100)) == (0, False)
    assert g.drop_target(QPoint(50, g.content_height + 100)) == (10, True)


# --- indicator ------------------------------------------------------


def test_indicator_at_first_cell():
    g = make(10, cols=4)
    r = g.indicator_rect(0)
    assert r == QRect(g.cell_rect(0).left() - 5, g.cell_rect(0).top(), 3, 150)


def test_indicator_between_cells_is_left_of_next_cell():
    g = make(10, cols=4)
    r = g.indicator_rect(2)
    assert r.left() == g.cell_rect(2).left() - 5
    assert r.top() == g.cell_rect(2).top()
    assert r.height() == g.item_h


def test_indicator_row_start_vs_previous_row_end():
    g = make(10, cols=4)
    start_of_row1 = g.indicator_rect(4)
    assert start_of_row1.left() == g.cell_rect(4).left() - 5
    assert start_of_row1.top() == g.cell_rect(4).top()
    end_of_row0 = g.indicator_rect(4, row_end=True)
    assert end_of_row0.left() == g.cell_rect(3).right() + 2
    assert end_of_row0.top() == g.cell_rect(3).top()


def test_indicator_at_document_end():
    g = make(10, cols=4)
    r = g.indicator_rect(10)
    assert r.left() == g.cell_rect(9).right() + 2
    assert r.top() == g.cell_rect(9).top()


def test_indicator_at_document_end_when_last_row_full():
    g = make(8, cols=4)
    r = g.indicator_rect(8)
    assert r.left() == g.cell_rect(7).right() + 2
    assert r.top() == g.cell_rect(7).top()


def test_indicator_clamps_index():
    g = make(3, cols=4)
    assert g.indicator_rect(99) == g.indicator_rect(3)
    assert g.indicator_rect(-5) == g.indicator_rect(0)


@pytest.mark.parametrize("count,cols", [(1, 1), (7, 3), (12, 4), (5, 8)])
def test_drop_then_indicator_roundtrip_never_leaves_grid(count, cols):
    g = make(count, cols=cols)
    for x in range(-30, g.content_width + 30, 11):
        for y in range(-30, g.content_height + 30, 11):
            idx, row_end = g.drop_target(QPoint(x, y))
            rect = g.indicator_rect(idx, row_end=row_end)
            assert rect is not None
            assert rect.top() >= g.margin
            assert rect.bottom() <= g.content_height
