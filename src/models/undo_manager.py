"""Undo/Redo manager for PDFas operations."""
from dataclasses import dataclass
from typing import Callable
from collections import deque


@dataclass
class UndoAction:
    """Represents a single undoable action."""
    description: str
    undo_func: Callable[[], None]
    redo_func: Callable[[], None]


class UndoManager:
    """Manages undo/redo operations."""

    def __init__(self, max_size: int = 100):
        self._undo_stack: deque[UndoAction] = deque(maxlen=max_size)
        self._redo_stack: list[UndoAction] = []
        self._max_size = max_size

    def add_action(self, action: UndoAction) -> None:
        self._undo_stack.append(action)
        self._redo_stack.clear()

    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    def undo(self) -> str | None:
        if not self.can_undo():
            return None
        action = self._undo_stack.pop()
        action.undo_func()
        self._redo_stack.append(action)
        return action.description

    def redo(self) -> str | None:
        if not self.can_redo():
            return None
        action = self._redo_stack.pop()
        action.redo_func()
        self._undo_stack.append(action)
        return action.description

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()

    def get_undo_description(self) -> str | None:
        if self._undo_stack:
            return self._undo_stack[-1].description
        return None

    def get_redo_description(self) -> str | None:
        if self._redo_stack:
            return self._redo_stack[-1].description
        return None
