//! Undo/Redo manager for JusticePDF operations

use std::collections::VecDeque;
use std::sync::Arc;

/// Represents a single undoable action
pub struct UndoAction {
    /// Description of the action
    pub description: String,
    /// Function to undo the action
    pub undo_func: Box<dyn FnMut() + Send + Sync>,
    /// Function to redo the action
    pub redo_func: Box<dyn FnMut() + Send + Sync>,
}

impl UndoAction {
    /// Create a new undo action
    pub fn new<U, R>(description: impl Into<String>, undo_func: U, redo_func: R) -> Self
    where
        U: FnMut() + Send + Sync + 'static,
        R: FnMut() + Send + Sync + 'static,
    {
        Self {
            description: description.into(),
            undo_func: Box::new(undo_func),
            redo_func: Box::new(redo_func),
        }
    }
}

impl std::fmt::Debug for UndoAction {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("UndoAction")
            .field("description", &self.description)
            .finish()
    }
}

/// Listener callback type
pub type UndoListener = Box<dyn Fn(&str) + Send + Sync>;

/// Manages undo/redo operations
pub struct UndoManager {
    /// Stack of actions that can be undone
    undo_stack: VecDeque<UndoAction>,
    /// Stack of actions that can be redone
    redo_stack: Vec<UndoAction>,
    /// Maximum number of undo actions to keep
    max_size: usize,
    /// Listeners for state changes
    listeners: Vec<Arc<UndoListener>>,
}

impl UndoManager {
    /// Create a new undo manager with the given maximum size
    pub fn new(max_size: usize) -> Self {
        Self {
            undo_stack: VecDeque::with_capacity(max_size),
            redo_stack: Vec::new(),
            max_size,
            listeners: Vec::new(),
        }
    }

    /// Register a listener for undo/redo state changes
    pub fn add_listener(&mut self, callback: impl Fn(&str) + Send + Sync + 'static) {
        self.listeners.push(Arc::new(Box::new(callback)));
    }

    /// Remove a listener (by reference comparison - simplified)
    pub fn remove_listener(&mut self, _callback: &Arc<UndoListener>) {
        // In Rust, comparing closures is tricky, so we'd typically use IDs
        // For now, this is a no-op placeholder
    }

    /// Notify listeners about a state change
    fn notify(&self, reason: &str) {
        for listener in &self.listeners {
            listener(reason);
        }
    }

    /// Add an action to the undo stack
    pub fn add_action(&mut self, action: UndoAction) {
        // Clear redo stack when new action is added
        self.redo_stack.clear();

        // Remove oldest action if at capacity
        if self.undo_stack.len() >= self.max_size {
            self.undo_stack.pop_front();
        }

        let description = action.description.clone();
        self.undo_stack.push_back(action);
        self.notify(&format!("add:{}", description));
    }

    /// Check if undo is available
    pub fn can_undo(&self) -> bool {
        !self.undo_stack.is_empty()
    }

    /// Check if redo is available
    pub fn can_redo(&self) -> bool {
        !self.redo_stack.is_empty()
    }

    /// Undo the last action
    pub fn undo(&mut self) -> Option<String> {
        if let Some(mut action) = self.undo_stack.pop_back() {
            let description = action.description.clone();
            (action.undo_func)();
            self.redo_stack.push(action);
            self.notify(&format!("undo:{}", description));
            Some(description)
        } else {
            None
        }
    }

    /// Redo the last undone action
    pub fn redo(&mut self) -> Option<String> {
        if let Some(mut action) = self.redo_stack.pop() {
            let description = action.description.clone();
            (action.redo_func)();
            self.undo_stack.push_back(action);
            self.notify(&format!("redo:{}", description));
            Some(description)
        } else {
            None
        }
    }

    /// Clear all undo/redo history
    pub fn clear(&mut self) {
        self.undo_stack.clear();
        self.redo_stack.clear();
        self.notify("clear");
    }

    /// Get the number of undo actions available
    pub fn undo_count(&self) -> usize {
        self.undo_stack.len()
    }

    /// Get the number of redo actions available
    pub fn redo_count(&self) -> usize {
        self.redo_stack.len()
    }

    /// Get the description of the next undo action
    pub fn get_undo_description(&self) -> Option<&str> {
        self.undo_stack.back().map(|a| a.description.as_str())
    }

    /// Get the description of the next redo action
    pub fn get_redo_description(&self) -> Option<&str> {
        self.redo_stack.last().map(|a| a.description.as_str())
    }
}

impl Default for UndoManager {
    fn default() -> Self {
        Self::new(100)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicI32, Ordering};
    use std::sync::Arc;

    #[test]
    fn test_undo_redo() {
        let mut manager = UndoManager::new(10);
        let counter = Arc::new(AtomicI32::new(0));

        let counter_undo = counter.clone();
        let counter_redo = counter.clone();

        manager.add_action(UndoAction::new(
            "Increment",
            move || {
                counter_undo.fetch_sub(1, Ordering::SeqCst);
            },
            move || {
                counter_redo.fetch_add(1, Ordering::SeqCst);
            },
        ));

        // Simulate the initial action
        counter.fetch_add(1, Ordering::SeqCst);
        assert_eq!(counter.load(Ordering::SeqCst), 1);

        // Undo
        assert!(manager.can_undo());
        manager.undo();
        assert_eq!(counter.load(Ordering::SeqCst), 0);

        // Redo
        assert!(manager.can_redo());
        manager.redo();
        assert_eq!(counter.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn test_max_size() {
        let mut manager = UndoManager::new(3);

        for i in 0..5 {
            manager.add_action(UndoAction::new(
                format!("Action {}", i),
                || {},
                || {},
            ));
        }

        assert_eq!(manager.undo_count(), 3);
        assert_eq!(manager.get_undo_description(), Some("Action 4"));
    }

    #[test]
    fn test_clear() {
        let mut manager = UndoManager::new(10);

        manager.add_action(UndoAction::new("Test", || {}, || {}));
        manager.undo();

        assert!(manager.can_redo());
        manager.clear();
        assert!(!manager.can_undo());
        assert!(!manager.can_redo());
    }
}
