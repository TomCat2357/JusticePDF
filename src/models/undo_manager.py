"""Undo/Redo manager for JusticePDF operations."""
from dataclasses import dataclass
from typing import Callable
from collections import deque
import logging

logger = logging.getLogger(__name__)


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
        self._listeners: list[Callable[[str], None]] = []

    def add_listener(self, callback: Callable[[str], None]) -> None:
        """Register a listener for undo/redo state changes."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str], None]) -> None:
        """Remove a previously registered listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self, reason: str) -> None:
        """Notify listeners about a state change."""
        for callback in list(self._listeners):
            try:
                callback(reason)
            except Exception:
                logger.exception("UndoManager listener failed")

    def add_action(self, action: UndoAction) -> None:
        self._undo_stack.append(action)
        self._redo_stack.clear()
        self._notify(f"add:{action.description}")

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
        self._notify(f"undo:{action.description}")
        return action.description

    def redo(self) -> str | None:
        if not self.can_redo():
            return None
        action = self._redo_stack.pop()
        action.redo_func()
        self._undo_stack.append(action)
        self._notify(f"redo:{action.description}")
        return action.description

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._notify("clear")

    def undo_count(self) -> int:
        return len(self._undo_stack)

    def redo_count(self) -> int:
        return len(self._redo_stack)

    def get_undo_description(self) -> str | None:
        if self._undo_stack:
            return self._undo_stack[-1].description
        return None

    def get_redo_description(self) -> str | None:
        if self._redo_stack:
            return self._redo_stack[-1].description
        return None
