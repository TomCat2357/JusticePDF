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
    # ページ構成(枚数・順序・回転・内容)を変える操作なら True。False(注釈の編集だけ)なら
    # Undo/Redo 後にページ一覧全体を作り直さず、表示中のページだけ更新する。
    # 迷ったら True のままにする(従来どおり全体を再読込する安全側の既定値)。
    affects_pages: bool = True


class UndoManager:
    """Manages undo/redo operations."""

    def __init__(self, max_size: int = 100):
        self._undo_stack: deque[UndoAction] = deque(maxlen=max_size)
        self._redo_stack: list[UndoAction] = []
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

    def peek_undo(self) -> UndoAction | None:
        """次に undo される操作を(取り出さずに)返す。"""
        return self._undo_stack[-1] if self._undo_stack else None

    def peek_redo(self) -> UndoAction | None:
        """次に redo される操作を(取り出さずに)返す。"""
        return self._redo_stack[-1] if self._redo_stack else None

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
