"""Shared helper functions for Qt view classes."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar

from PyQt6.QtGui import QAction, QKeySequence
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
