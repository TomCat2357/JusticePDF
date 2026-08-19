from __future__ import annotations

from PyQt6.QtCore import QRect, Qt

from src.models.undo_manager import UndoManager
from src.views.page_edit_window import PageEditWindow
from src.views.view_helpers import responsive_grid_metrics
from tests.helpers import make_pdf


def _assert_items_fit_horizontally(scroll_area, container, items) -> None:
    viewport = scroll_area.viewport()
    viewport_rect = viewport.rect()
    for item in items:
        item_rect = item.geometry()
        top_left = container.mapTo(viewport, item_rect.topLeft())
        bottom_right = container.mapTo(viewport, item_rect.bottomRight())
        visible_rect = QRect(top_left, bottom_right).normalized()
        assert visible_rect.left() >= viewport_rect.left()
        assert visible_rect.right() <= viewport_rect.right()


def test_responsive_grid_metrics_keeps_the_row_inside_the_viewport() -> None:
    columns, item_width = responsive_grid_metrics(
        available_width=800,
        preferred_item_width=150,
        spacing=10,
        horizontal_margins=20,
    )

    assert columns == 4
    assert item_width == 187
    assert columns * item_width + (columns - 1) * 10 + 20 <= 800


def test_responsive_grid_metrics_shrinks_a_single_item_when_needed() -> None:
    columns, item_width = responsive_grid_metrics(
        available_width=120,
        preferred_item_width=150,
        spacing=10,
        horizontal_margins=20,
    )

    assert columns == 1
    assert item_width == 100


def test_page_grid_reflows_without_horizontal_overflow(qtbot, tmp_path) -> None:
    pdf_path = tmp_path / "responsive-pages.pdf"
    make_pdf(pdf_path, pages=12)

    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()

    for width, height in ((800, 700), (800, 300), (500, 300), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(20)

        assert (
            window._grid_scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert not window._grid_scroll.horizontalScrollBar().isVisible()
        _assert_items_fit_horizontally(
            window._grid_scroll,
            window._container,
            window._thumbnails,
        )
