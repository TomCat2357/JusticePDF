"""Shared helper functions for Qt view classes."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction, QColor, QDrag, QFont, QKeySequence, QPainter
from PyQt6.QtWidgets import QAbstractButton, QScrollArea, QWidget

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
) -> int:
    """Return viewport width when available, otherwise fallback width."""
    width = int(scroll_area.viewport().width()) if scroll_area else 0
    if width > 0:
        return width
    return int(fallback_width)


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
