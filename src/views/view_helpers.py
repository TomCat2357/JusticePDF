"""Shared helper functions for Qt view classes."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction, QColor, QDrag, QFont, QKeySequence, QPainter
from PyQt6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QScrollArea,
    QWidget,
)

Shortcut = QKeySequence | QKeySequence.StandardKey | str


class UndoCounter(Protocol):
    """Minimal protocol for undo/redo count providers."""

    def undo_count(self) -> int:
        ...

    def redo_count(self) -> int:
        ...


class Selectable(Protocol):
    """Minimal protocol for selectable UI items."""

    def set_selected(self, selected: bool) -> None:
        ...


TSelectable = TypeVar("TSelectable", bound=Selectable)


def register_shortcuts(
    widget: QWidget,
    bindings: Iterable[tuple[Shortcut, Callable[[], None]]],
) -> None:
    """Register a batch of shortcuts on a widget."""
    for shortcut, handler in bindings:
        action = QAction(widget)
        action.setShortcut(shortcut)
        action.triggered.connect(handler)
        widget.addAction(action)


def log_undo_state(
    logger: logging.Logger,
    context_name: str,
    reason: str,
    undo_button: QAbstractButton | None,
    redo_button: QAbstractButton | None,
    undo_manager: UndoCounter,
) -> None:
    """Emit a consistent undo/redo debug line."""
    if not undo_button or not redo_button:
        return

    undo_color = "black" if undo_button.isEnabled() else "gray"
    redo_color = "black" if redo_button.isEnabled() else "gray"
    logger.debug(
        "[UndoState][%s] %s | undo=%s redo=%s undo_count=%s redo_count=%s",
        context_name,
        reason,
        undo_color,
        redo_color,
        undo_manager.undo_count(),
        undo_manager.redo_count(),
    )


def viewport_width_or_fallback(
    scroll_area: QScrollArea | None,
    fallback_width: int,
    *,
    reserve_vertical_scrollbar: bool = False,
) -> int:
    """Return viewport width when available, otherwise fallback width."""
    width = int(scroll_area.viewport().width()) if scroll_area else 0
    if width <= 0:
        return int(fallback_width)
    if reserve_vertical_scrollbar and scroll_area is not None:
        if scroll_area.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            # The bar can appear after the layout is calculated. Reserve its
            # width up front so appearing/disappearing does not oscillate the
            # number of columns or leave the last item clipped.
            bar_width = scroll_area.verticalScrollBar().sizeHint().width()
            reserved_width = (
                scroll_area.width()
                - 2 * scroll_area.frameWidth()
                - int(bar_width)
            )
            if reserved_width > 0:
                width = min(width, reserved_width)
    if width > 0:
        return width
    return int(fallback_width)


def responsive_grid_metrics(
    available_width: int,
    preferred_item_width: int,
    spacing: int,
    horizontal_margins: int = 0,
) -> tuple[int, int]:
    """Return ``(columns, item_width)`` for a width-constrained grid.

    ``preferred_item_width`` is the normal preview size.  The grid keeps as
    many columns as that size allows and leaves any remaining width unused so
    resizing the window does not resize the items.  The item remains at its
    preferred width even when it does not fit; callers can allow horizontal
    scrolling for that case.
    """
    usable_width = max(1, int(available_width) - max(0, int(horizontal_margins)))
    preferred_width = max(1, int(preferred_item_width))
    gap = max(0, int(spacing))
    columns = max(1, (usable_width + gap) // (preferred_width + gap))
    return columns, preferred_width


def clear_selection(items: list[TSelectable]) -> None:
    """Unselect and clear all selectable items in-place."""
    for item in items:
        item.set_selected(False)
    items.clear()


def apply_drag_pixmap(
    drag: QDrag,
    widget: QWidget,
    *,
    max_size: int = 100,
    count: int = 1,
    badge_size: int = 24,
    badge_font_size: int = 10,
) -> None:
    """Set a scaled grab of the widget as the drag pixmap and hot spot.

    When ``count`` > 1, draw a count badge in the top-right corner.
    """
    pixmap = widget.grab().scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio)
    if count > 1:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.GlobalColor.white)
        painter.setBrush(QColor(0, 120, 215))
        painter.drawEllipse(pixmap.width() - badge_size, 0, badge_size, badge_size)
        font = QFont()
        font.setBold(True)
        font.setPointSize(badge_font_size)
        painter.setFont(font)
        painter.drawText(pixmap.width() - badge_size, 0, badge_size, badge_size,
                         Qt.AlignmentFlag.AlignCenter, str(count))
        painter.end()
    drag.setPixmap(pixmap)
    drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))


def build_accept_cancel_box(
    dialog: "QDialog", accept_label: str, *, cancel_label: str = "キャンセル"
) -> tuple[QDialogButtonBox, QAbstractButton]:
    """accept/reject に接続済みの日本語ラベル付きボタンボックスを作る。

    戻り値は (ボタンボックス, accept ボタン)。accept ボタンはダイアログ側で
    有効/無効の切り替えに使う。
    """
    btn_box = QDialogButtonBox()
    ok_btn = btn_box.addButton(accept_label, QDialogButtonBox.ButtonRole.AcceptRole)
    btn_box.addButton(cancel_label, QDialogButtonBox.ButtonRole.RejectRole)
    btn_box.accepted.connect(dialog.accept)
    btn_box.rejected.connect(dialog.reject)
    return btn_box, ok_btn
