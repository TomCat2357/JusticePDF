//! Main application state and controller

use crate::folder_watcher::FolderWatcher;
use crate::main_window::MainWindow;
use crate::page_edit_window::PageEditWindow;
use crate::undo_manager::UndoManager;
use eframe::egui;
use parking_lot::RwLock;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Main application state
pub struct JusticePdfApp {
    /// Main window state
    main_window: MainWindow,
    /// Page edit windows (keyed by PDF path)
    page_edit_windows: HashMap<PathBuf, PageEditWindow>,
    /// Shared undo manager
    undo_manager: Arc<RwLock<UndoManager>>,
    /// Folder watcher
    folder_watcher: Option<FolderWatcher>,
    /// Working directory
    work_dir: PathBuf,
    /// Pending file events from watcher
    pending_events: Arc<RwLock<Vec<FileEvent>>>,
}

/// File system events
#[derive(Debug, Clone)]
pub enum FileEvent {
    Added(PathBuf),
    Removed(PathBuf),
    Modified(PathBuf),
}

impl JusticePdfApp {
    pub fn new(_cc: &eframe::CreationContext<'_>) -> Self {
        // Setup work directory
        let work_dir = dirs::document_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("PDFs");

        if !work_dir.exists() {
            if let Err(e) = std::fs::create_dir_all(&work_dir) {
                log::error!("Failed to create work directory: {}", e);
            }
        }

        let undo_manager = Arc::new(RwLock::new(UndoManager::new(20)));
        let pending_events = Arc::new(RwLock::new(Vec::new()));

        // Setup folder watcher
        let folder_watcher = FolderWatcher::new(work_dir.clone(), pending_events.clone()).ok();

        // Create main window
        let main_window = MainWindow::new(
            work_dir.clone(),
            undo_manager.clone(),
            pending_events.clone(),
        );

        Self {
            main_window,
            page_edit_windows: HashMap::new(),
            undo_manager,
            folder_watcher,
            work_dir,
            pending_events,
        }
    }

    /// Process pending file events from folder watcher
    fn process_file_events(&mut self) {
        let events: Vec<FileEvent> = {
            let mut pending = self.pending_events.write();
            std::mem::take(&mut *pending)
        };

        for event in events {
            match event {
                FileEvent::Added(path) => {
                    log::debug!("File added: {:?}", path);
                    self.main_window.on_file_added(&path);
                }
                FileEvent::Removed(path) => {
                    log::debug!("File removed: {:?}", path);
                    self.main_window.on_file_removed(&path);
                    // Close any open page edit window for this file
                    self.page_edit_windows.remove(&path);
                }
                FileEvent::Modified(path) => {
                    log::debug!("File modified: {:?}", path);
                    self.main_window.on_file_modified(&path);
                    // Refresh page edit window if open
                    if let Some(window) = self.page_edit_windows.get_mut(&path) {
                        window.refresh();
                    }
                }
            }
        }
    }

    /// Open a page edit window for a PDF
    pub fn open_page_edit_window(&mut self, pdf_path: PathBuf) {
        if !self.page_edit_windows.contains_key(&pdf_path) {
            let window = PageEditWindow::new(
                pdf_path.clone(),
                self.undo_manager.clone(),
            );
            self.page_edit_windows.insert(pdf_path.clone(), window);
            self.main_window.lock_card(&pdf_path);
        }
    }

    /// Close a page edit window
    pub fn close_page_edit_window(&mut self, pdf_path: &PathBuf) {
        if self.page_edit_windows.remove(pdf_path).is_some() {
            self.main_window.unlock_card(pdf_path);
        }
    }
}

impl eframe::App for JusticePdfApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Process file events
        self.process_file_events();

        // Handle dropped files
        ctx.input(|i| {
            if !i.raw.dropped_files.is_empty() {
                for file in &i.raw.dropped_files {
                    if let Some(path) = &file.path {
                        if path.extension().map_or(false, |e| e == "pdf") {
                            self.main_window.import_file(path);
                        }
                    }
                }
            }
        });

        // Collect windows to open
        let mut windows_to_open: Vec<PathBuf> = Vec::new();

        // Draw main window
        egui::CentralPanel::default().show(ctx, |ui| {
            // Check for double-clicked card
            if let Some(path) = self.main_window.take_pending_edit_window() {
                windows_to_open.push(path);
            }

            self.main_window.ui(ui, ctx);
        });

        // Draw page edit windows
        let mut closed_windows = Vec::new();
        for (path, window) in &mut self.page_edit_windows {
            let mut open = true;
            egui::Window::new(format!("Edit: {}", path.file_name().unwrap_or_default().to_string_lossy()))
                .id(egui::Id::new(path))
                .open(&mut open)
                .default_size([800.0, 600.0])
                .resizable(true)
                .show(ctx, |ui| {
                    window.ui(ui, ctx);
                });

            if !open {
                closed_windows.push(path.clone());
            }
        }

        // Open new windows
        for path in windows_to_open {
            self.open_page_edit_window(path);
        }

        // Close windows
        for path in closed_windows {
            self.close_page_edit_window(&path);
        }

        // Request repaint if we have pending events
        if !self.pending_events.read().is_empty() {
            ctx.request_repaint();
        }
    }
}
