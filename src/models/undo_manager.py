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
    # この操作を積んだ(未保存の変更を持つ)ウィンドウを識別する不透明なトークン。
    # 手動保存モードで変更を破棄したとき、そのウィンドウの未保存分の操作だけを共有の
    # UndoManager から取り除くために使う(``purge_owner``)。保存すると外れる(``release_owner``)。
    # 設計書の ``owner_path`` はファイル名変更で値が古くなるため、パスではなくトークンにした。
    owner: object | None = None
    # Undo/Redo がファイルを直接書き換える操作(ページ構成・しおり・タイトル・ファイル名など、
    # 手動保存モードのメモリ上では編集できないもの)なら True。実行前に未保存の編集を保存させる。
    writes_file: bool = False


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

    def release_owner(self, owner: object) -> None:
        """*owner* の操作を「保存済み」扱いにする(以後 ``purge_owner`` の対象にしない)。"""
        for action in (*self._undo_stack, *self._redo_stack):
            if action.owner is owner:
                action.owner = None

    def purge_owner(self, owner: object) -> int:
        """*owner* が積んだ(未保存の)操作を Undo/Redo の両スタックから取り除く。除いた件数を返す。

        ウィンドウが未保存の変更を破棄したあとに、その操作が残っていると、後で
        メイン画面などから Undo したときにディスクへ意図しない書き込みをしてしまう。
        """
        removed = 0
        kept_undo = [a for a in self._undo_stack if a.owner is not owner]
        removed += len(self._undo_stack) - len(kept_undo)
        if removed:
            self._undo_stack.clear()
            self._undo_stack.extend(kept_undo)
        kept_redo = [a for a in self._redo_stack if a.owner is not owner]
        removed_redo = len(self._redo_stack) - len(kept_redo)
        if removed_redo:
            self._redo_stack[:] = kept_redo
        removed += removed_redo
        if removed:
            self._notify("purge")
        return removed

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._notify("clear")

    def undo_count(self) -> int:
        return len(self._undo_stack)

    def redo_count(self) -> int:
        return len(self._redo_stack)
