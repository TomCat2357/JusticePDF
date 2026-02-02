"""Tests for Undo/Redo manager."""
import pytest
from src.models.undo_manager import UndoManager, UndoAction


def test_undo_manager_can_undo_after_action():
    """Test that undo is available after adding an action."""
    manager = UndoManager()
    action = UndoAction(
        description="Test action",
        undo_func=lambda: None,
        redo_func=lambda: None
    )
    manager.add_action(action)
    assert manager.can_undo()
    assert not manager.can_redo()


def test_undo_manager_executes_undo():
    """Test that undo executes the undo function."""
    result = []
    manager = UndoManager()
    action = UndoAction(
        description="Add item",
        undo_func=lambda: result.append("undone"),
        redo_func=lambda: result.append("redone")
    )
    manager.add_action(action)
    manager.undo()
    assert result == ["undone"]
    assert manager.can_redo()


def test_undo_manager_executes_redo():
    """Test that redo executes the redo function."""
    result = []
    manager = UndoManager()
    action = UndoAction(
        description="Add item",
        undo_func=lambda: result.append("undone"),
        redo_func=lambda: result.append("redone")
    )
    manager.add_action(action)
    manager.undo()
    manager.redo()
    assert result == ["undone", "redone"]


def test_undo_manager_clears_redo_on_new_action():
    """Test that adding a new action clears redo stack."""
    manager = UndoManager()
    action1 = UndoAction("Action 1", lambda: None, lambda: None)
    action2 = UndoAction("Action 2", lambda: None, lambda: None)
    manager.add_action(action1)
    manager.undo()
    manager.add_action(action2)
    assert not manager.can_redo()


def test_undo_manager_respects_max_size():
    """Test that undo stack respects maximum size."""
    manager = UndoManager(max_size=3)
    for i in range(5):
        manager.add_action(UndoAction(f"Action {i}", lambda: None, lambda: None))
    count = 0
    while manager.can_undo():
        manager.undo()
        count += 1
    assert count == 3
