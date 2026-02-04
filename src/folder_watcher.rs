//! Folder watcher for monitoring PDF changes

use crate::app::FileEvent;
use notify::{
    Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Result as NotifyResult, Watcher,
};
use parking_lot::RwLock;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

/// Watch a folder for PDF file changes
pub struct FolderWatcher {
    /// The watched folder path
    folder_path: PathBuf,
    /// The notify watcher instance
    _watcher: RecommendedWatcher,
}

impl FolderWatcher {
    /// Create a new folder watcher
    pub fn new(
        folder_path: PathBuf,
        events: Arc<RwLock<Vec<FileEvent>>>,
    ) -> NotifyResult<Self> {
        let folder_clone = folder_path.clone();

        // Create watcher with debouncing
        let mut watcher = notify::recommended_watcher(move |res: NotifyResult<Event>| {
            if let Ok(event) = res {
                Self::handle_event(&folder_clone, event, &events);
            }
        })?;

        // Configure and start watching
        watcher.watch(&folder_path, RecursiveMode::NonRecursive)?;

        log::info!("Started watching folder: {:?}", folder_path);

        Ok(Self {
            folder_path,
            _watcher: watcher,
        })
    }

    /// Handle a file system event
    fn handle_event(
        folder_path: &Path,
        event: Event,
        events: &Arc<RwLock<Vec<FileEvent>>>,
    ) {
        for path in event.paths {
            // Only process PDF files
            if !Self::is_pdf(&path) {
                continue;
            }

            // Only process files in our watched folder (not subdirectories)
            if path.parent() != Some(folder_path) {
                continue;
            }

            let file_event = match event.kind {
                EventKind::Create(_) => Some(FileEvent::Added(path)),
                EventKind::Remove(_) => Some(FileEvent::Removed(path)),
                EventKind::Modify(_) => Some(FileEvent::Modified(path)),
                EventKind::Access(_) => None, // Ignore access events
                EventKind::Other => None,
                EventKind::Any => None,
            };

            if let Some(file_event) = file_event {
                log::debug!("File event: {:?}", file_event);
                events.write().push(file_event);
            }
        }
    }

    /// Check if a path is a PDF file
    fn is_pdf(path: &Path) -> bool {
        path.extension()
            .map(|ext| ext.to_ascii_lowercase() == "pdf")
            .unwrap_or(false)
    }

    /// Get the watched folder path
    pub fn folder_path(&self) -> &Path {
        &self.folder_path
    }

    /// Get all PDF files currently in the folder
    pub fn get_pdf_files(&self) -> Vec<PathBuf> {
        let mut files = Vec::new();

        if let Ok(entries) = std::fs::read_dir(&self.folder_path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if Self::is_pdf(&path) && path.is_file() {
                    files.push(path);
                }
            }
        }

        // Sort by modification time (newest first)
        files.sort_by(|a, b| {
            let mtime_a = std::fs::metadata(a)
                .and_then(|m| m.modified())
                .ok();
            let mtime_b = std::fs::metadata(b)
                .and_then(|m| m.modified())
                .ok();
            mtime_b.cmp(&mtime_a)
        });

        files
    }
}

/// Simple debouncer for file events
pub struct EventDebouncer {
    /// Pending events
    pending: std::collections::HashMap<PathBuf, (FileEvent, std::time::Instant)>,
    /// Debounce duration
    debounce_duration: Duration,
}

impl EventDebouncer {
    /// Create a new debouncer
    pub fn new(debounce_ms: u64) -> Self {
        Self {
            pending: std::collections::HashMap::new(),
            debounce_duration: Duration::from_millis(debounce_ms),
        }
    }

    /// Add an event to the debouncer
    pub fn add(&mut self, path: PathBuf, event: FileEvent) {
        self.pending.insert(path, (event, std::time::Instant::now()));
    }

    /// Get events that are ready to be processed
    pub fn drain_ready(&mut self) -> Vec<FileEvent> {
        let now = std::time::Instant::now();
        let mut ready = Vec::new();
        let mut expired_keys = Vec::new();

        for (path, (event, time)) in &self.pending {
            if now.duration_since(*time) >= self.debounce_duration {
                ready.push(event.clone());
                expired_keys.push(path.clone());
            }
        }

        for key in expired_keys {
            self.pending.remove(&key);
        }

        ready
    }

    /// Cancel pending events for a path
    pub fn cancel(&mut self, path: &Path) {
        self.pending.remove(path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread::sleep;
    use tempfile::tempdir;

    #[test]
    fn test_is_pdf() {
        assert!(FolderWatcher::is_pdf(Path::new("test.pdf")));
        assert!(FolderWatcher::is_pdf(Path::new("test.PDF")));
        assert!(!FolderWatcher::is_pdf(Path::new("test.txt")));
        assert!(!FolderWatcher::is_pdf(Path::new("test")));
    }

    #[test]
    fn test_debouncer() {
        let mut debouncer = EventDebouncer::new(50);

        debouncer.add(
            PathBuf::from("test.pdf"),
            FileEvent::Modified(PathBuf::from("test.pdf")),
        );

        // Should not be ready immediately
        assert!(debouncer.drain_ready().is_empty());

        // Wait for debounce period
        sleep(Duration::from_millis(60));

        // Should now be ready
        let ready = debouncer.drain_ready();
        assert_eq!(ready.len(), 1);
    }
}
