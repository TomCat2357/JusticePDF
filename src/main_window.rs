//! Main window for JusticePDF application

use crate::app::FileEvent;
use crate::path_utils::{ensure_unique_numbered_path, ensure_unique_path, normalize_path};
use crate::pdf_card::{PdfCard, PdfCardResponse, SelectionMode, CARD_WIDTH, THUMBNAIL_SIZE};
use crate::pdf_utils::{
    extract_pages, get_page_count, merge_pdfs, merge_pdfs_in_place, remove_pages, rotate_pages,
};
use crate::undo_manager::{UndoAction, UndoManager};
use egui::{Color32, Context, Id, Response, Sense, Ui, Vec2};
use parking_lot::RwLock;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Office file extensions that can be converted to PDF
const OFFICE_EXTS: &[&str] = &["doc", "docx", "docm", "xls", "xlsx", "xlsm", "ppt", "pptx"];

/// Importable file extensions
const IMPORT_EXTS: &[&str] = &[
    "pdf", "doc", "docx", "docm", "xls", "xlsx", "xlsm", "ppt", "pptx",
];

/// Sort order for cards
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SortOrder {
    Manual,
    Name,
    Date,
}

/// Main application window state
pub struct MainWindow {
    /// PDF cards
    cards: Vec<PdfCard>,
    /// Selected card indices
    selected_indices: Vec<usize>,
    /// Current sort order
    sort_order: SortOrder,
    /// Sort ascending
    sort_ascending: bool,
    /// Working directory
    work_dir: PathBuf,
    /// Undo manager
    undo_manager: Arc<RwLock<UndoManager>>,
    /// File events from watcher
    pending_events: Arc<RwLock<Vec<FileEvent>>>,
    /// Internal adds (to avoid clearing undo history)
    internal_adds: HashSet<PathBuf>,
    /// Internal removes (to avoid clearing undo history)
    internal_removes: HashSet<PathBuf>,
    /// Pending page edit window to open
    pending_edit_window: Option<PathBuf>,
    /// Drop indicator index
    drop_indicator_index: Option<usize>,
    /// Drag source paths
    drag_source_paths: Vec<PathBuf>,
    /// Is dragging
    is_dragging: bool,
    /// Modified timers (path -> last modification time)
    modified_last_mtime: HashMap<PathBuf, std::time::SystemTime>,
}

impl MainWindow {
    /// Create a new main window
    pub fn new(
        work_dir: PathBuf,
        undo_manager: Arc<RwLock<UndoManager>>,
        pending_events: Arc<RwLock<Vec<FileEvent>>>,
    ) -> Self {
        let mut window = Self {
            cards: Vec::new(),
            selected_indices: Vec::new(),
            sort_order: SortOrder::Manual,
            sort_ascending: true,
            work_dir,
            undo_manager,
            pending_events,
            internal_adds: HashSet::new(),
            internal_removes: HashSet::new(),
            pending_edit_window: None,
            drop_indicator_index: None,
            drag_source_paths: Vec::new(),
            is_dragging: false,
            modified_last_mtime: HashMap::new(),
        };

        // Load existing files
        window.load_existing_files();
        window
    }

    /// Load existing PDF files from work directory
    fn load_existing_files(&mut self) {
        if let Ok(entries) = fs::read_dir(&self.work_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().map_or(false, |e| e == "pdf") {
                    self.cards.push(PdfCard::new(path));
                }
            }
        }
    }

    /// Take the pending edit window path (for opening)
    pub fn take_pending_edit_window(&mut self) -> Option<PathBuf> {
        self.pending_edit_window.take()
    }

    /// Handle file added event
    pub fn on_file_added(&mut self, path: &Path) {
        let normalized = normalize_path(path);

        // Check if it's an internal add
        if self.internal_adds.remove(&normalized) {
            // Already handled internally
        } else {
            // External add - clear undo history
            self.undo_manager.write().clear();
        }

        // Check if card already exists
        if self.cards.iter().any(|c| c.pdf_path() == path) {
            return;
        }

        // Add new card
        self.cards.push(PdfCard::new(path.to_path_buf()));
    }

    /// Handle file removed event
    pub fn on_file_removed(&mut self, path: &Path) {
        let normalized = normalize_path(path);

        // Check if it's an internal remove
        if self.internal_removes.remove(&normalized) {
            // Already handled internally
        } else {
            // External remove - clear undo history
            self.undo_manager.write().clear();
        }

        // Remove card
        self.remove_card(path);
    }

    /// Handle file modified event
    pub fn on_file_modified(&mut self, path: &Path) {
        // Debounce: check if the file actually changed
        if let Ok(metadata) = fs::metadata(path) {
            if let Ok(mtime) = metadata.modified() {
                let normalized = normalize_path(path);
                if let Some(last_mtime) = self.modified_last_mtime.get(&normalized) {
                    if *last_mtime == mtime {
                        return; // No change
                    }
                }
                self.modified_last_mtime.insert(normalized, mtime);
            }
        }

        // Refresh the card
        for card in &mut self.cards {
            if card.pdf_path() == path {
                card.refresh();
                break;
            }
        }
    }

    /// Remove a card by path
    fn remove_card(&mut self, path: &Path) {
        // Remove from selection
        self.selected_indices
            .retain(|&i| i < self.cards.len() && self.cards[i].pdf_path() != path);

        // Remove card
        self.cards.retain(|c| c.pdf_path() != path);
    }

    /// Get card by path
    fn get_card_by_path(&self, path: &Path) -> Option<&PdfCard> {
        self.cards.iter().find(|c| c.pdf_path() == path)
    }

    /// Get mutable card by path
    fn get_card_by_path_mut(&mut self, path: &Path) -> Option<&mut PdfCard> {
        self.cards.iter_mut().find(|c| c.pdf_path() == path)
    }

    /// Lock a card
    pub fn lock_card(&mut self, path: &Path) {
        if let Some(card) = self.get_card_by_path_mut(path) {
            card.set_locked(true);
            // Deselect locked card
            if let Some(idx) = self.cards.iter().position(|c| c.pdf_path() == path) {
                self.selected_indices.retain(|&i| i != idx);
                self.cards[idx].set_selected(false);
            }
        }
    }

    /// Unlock a card
    pub fn unlock_card(&mut self, path: &Path) {
        if let Some(card) = self.get_card_by_path_mut(path) {
            card.set_locked(false);
        }
    }

    /// Import a file
    pub fn import_file(&mut self, path: &Path) {
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

        if ext.to_lowercase() == "pdf" {
            self.copy_pdf_into_workdir(path);
        } else if OFFICE_EXTS.contains(&ext.to_lowercase().as_str()) {
            // TODO: Implement Office conversion
            log::warn!("Office file conversion not yet implemented");
        }
    }

    /// Copy a PDF into the work directory
    fn copy_pdf_into_workdir(&mut self, src_path: &Path) {
        let filename = src_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "document.pdf".to_string());

        let dest_path = ensure_unique_numbered_path(&self.work_dir, &filename);

        self.internal_adds.insert(normalize_path(&dest_path));

        if let Err(e) = fs::copy(src_path, &dest_path) {
            log::error!("Failed to copy PDF: {}", e);
            self.internal_adds.remove(&normalize_path(&dest_path));
        }
    }

    /// Clear selection
    fn clear_selection(&mut self) {
        for idx in &self.selected_indices {
            if *idx < self.cards.len() {
                self.cards[*idx].set_selected(false);
            }
        }
        self.selected_indices.clear();
    }

    /// Handle card click
    fn handle_card_click(&mut self, idx: usize, modifiers: &egui::Modifiers) {
        let mode = SelectionMode::from_modifiers(modifiers);

        match mode {
            SelectionMode::Single => {
                if !self.selected_indices.contains(&idx) {
                    self.clear_selection();
                    self.selected_indices.push(idx);
                    self.cards[idx].set_selected(true);
                }
            }
            SelectionMode::Toggle => {
                if let Some(pos) = self.selected_indices.iter().position(|&i| i == idx) {
                    self.selected_indices.remove(pos);
                    self.cards[idx].set_selected(false);
                } else {
                    self.selected_indices.push(idx);
                    self.cards[idx].set_selected(true);
                }
            }
            SelectionMode::Range => {
                if let Some(&last_idx) = self.selected_indices.last() {
                    let (start, end) = if last_idx < idx {
                        (last_idx, idx)
                    } else {
                        (idx, last_idx)
                    };
                    for i in start..=end {
                        if !self.selected_indices.contains(&i) {
                            self.selected_indices.push(i);
                            self.cards[i].set_selected(true);
                        }
                    }
                } else {
                    self.selected_indices.push(idx);
                    self.cards[idx].set_selected(true);
                }
            }
        }
    }

    /// Handle delete action
    fn on_delete(&mut self) {
        if self.selected_indices.is_empty() {
            return;
        }

        let paths: Vec<PathBuf> = self
            .selected_indices
            .iter()
            .filter_map(|&i| self.cards.get(i).map(|c| c.pdf_path().to_path_buf()))
            .collect();

        // Create backups for undo
        let backup_dir = tempfile::tempdir().unwrap();
        let mut backups = HashMap::new();

        for path in &paths {
            let backup_path = backup_dir.path().join(path.file_name().unwrap());
            if fs::copy(path, &backup_path).is_ok() {
                backups.insert(path.clone(), backup_path);
            }
        }

        // Mark as internal removes
        for path in &paths {
            self.internal_removes.insert(normalize_path(path));
        }

        // Delete files
        for path in &paths {
            if let Err(e) = trash::delete(path) {
                log::error!("Failed to delete {:?}: {}", path, e);
            }
        }

        self.clear_selection();

        // Note: In a real implementation, we'd add this to the undo manager
        // with proper cleanup of the backup directory
    }

    /// Handle rotate action
    fn on_rotate(&mut self) {
        if self.selected_indices.is_empty() {
            return;
        }

        for &idx in &self.selected_indices {
            if let Some(card) = self.cards.get_mut(idx) {
                if let Ok(page_count) = get_page_count(card.pdf_path()) {
                    let indices: Vec<usize> = (0..page_count).collect();
                    if let Err(e) = rotate_pages(card.pdf_path(), &indices, 90) {
                        log::error!("Failed to rotate: {}", e);
                    } else {
                        card.refresh();
                    }
                }
            }
        }
    }

    /// Handle select all action
    fn on_select_all(&mut self) {
        self.clear_selection();
        for i in 0..self.cards.len() {
            self.selected_indices.push(i);
            self.cards[i].set_selected(true);
        }
    }

    /// Handle sort by name
    fn on_sort_by_name(&mut self) {
        if self.sort_order == SortOrder::Name {
            self.sort_ascending = !self.sort_ascending;
        } else {
            self.sort_order = SortOrder::Name;
            self.sort_ascending = true;
        }
        self.sort_cards();
    }

    /// Handle sort by date
    fn on_sort_by_date(&mut self) {
        if self.sort_order == SortOrder::Date {
            self.sort_ascending = !self.sort_ascending;
        } else {
            self.sort_order = SortOrder::Date;
            self.sort_ascending = false;
        }
        self.sort_cards();
    }

    /// Sort cards based on current order
    fn sort_cards(&mut self) {
        self.clear_selection();

        match self.sort_order {
            SortOrder::Manual => {}
            SortOrder::Name => {
                self.cards.sort_by(|a, b| {
                    let cmp = a.filename().to_lowercase().cmp(&b.filename().to_lowercase());
                    if self.sort_ascending {
                        cmp
                    } else {
                        cmp.reverse()
                    }
                });
            }
            SortOrder::Date => {
                self.cards.sort_by(|a, b| {
                    let mtime_a = fs::metadata(a.pdf_path())
                        .and_then(|m| m.modified())
                        .ok();
                    let mtime_b = fs::metadata(b.pdf_path())
                        .and_then(|m| m.modified())
                        .ok();
                    let cmp = mtime_a.cmp(&mtime_b);
                    if self.sort_ascending {
                        cmp
                    } else {
                        cmp.reverse()
                    }
                });
            }
        }
    }

    /// Calculate grid columns based on available width
    fn calculate_columns(&self, available_width: f32) -> usize {
        let spacing = 10.0;
        let usable = available_width - 20.0; // margins
        ((usable + spacing) / (CARD_WIDTH + spacing)).max(1.0) as usize
    }

    /// Get drop index from position
    fn get_drop_index(&self, pos: egui::Pos2, cols: usize, spacing: f32) -> usize {
        let margin = 10.0;
        let cell_width = CARD_WIDTH + spacing;
        let cell_height = THUMBNAIL_SIZE + 50.0 + spacing;

        let col = ((pos.x - margin) / cell_width).floor() as usize;
        let row = ((pos.y - margin) / cell_height).floor() as usize;

        let idx = row * cols + col;
        idx.min(self.cards.len())
    }

    /// Draw the main window UI
    pub fn ui(&mut self, ui: &mut Ui, ctx: &Context) {
        // Toolbar
        ui.horizontal(|ui| {
            let can_undo = self.undo_manager.read().can_undo();
            let can_redo = self.undo_manager.read().can_redo();
            let has_selection = !self.selected_indices.is_empty();

            if ui.add_enabled(can_undo, egui::Button::new("Undo")).clicked() {
                self.undo_manager.write().undo();
            }

            if ui.add_enabled(can_redo, egui::Button::new("Redo")).clicked() {
                self.undo_manager.write().redo();
            }

            ui.separator();

            if ui
                .add_enabled(has_selection, egui::Button::new("Delete"))
                .clicked()
            {
                self.on_delete();
            }

            if ui.button("Rename").clicked() {
                // TODO: Implement rename dialog
            }

            ui.separator();

            if ui.button("Import").clicked() {
                // TODO: Implement file dialog
            }

            if ui.button("Export").clicked() {
                // TODO: Implement export
            }

            ui.separator();

            if ui
                .add_enabled(has_selection, egui::Button::new("Rotate"))
                .clicked()
            {
                self.on_rotate();
            }

            if ui.button("Select All").clicked() {
                self.on_select_all();
            }

            if ui.button("Sort Name").clicked() {
                self.on_sort_by_name();
            }

            if ui.button("Sort Date").clicked() {
                self.on_sort_by_date();
            }
        });

        ui.separator();

        // Card grid
        let available_width = ui.available_width();
        let cols = self.calculate_columns(available_width);
        let spacing = 10.0;

        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                ui.spacing_mut().item_spacing = Vec2::splat(spacing);

                egui::Grid::new("pdf_grid")
                    .num_columns(cols)
                    .spacing([spacing, spacing])
                    .show(ui, |ui| {
                        let mut clicked_idx = None;
                        let mut double_clicked_idx = None;

                        for (idx, card) in self.cards.iter_mut().enumerate() {
                            let response = card.ui(ui, ctx);

                            if response.clicked {
                                clicked_idx = Some(idx);
                            }

                            if response.double_clicked {
                                double_clicked_idx = Some(idx);
                            }

                            // End row after cols items
                            if (idx + 1) % cols == 0 {
                                ui.end_row();
                            }
                        }

                        // Handle clicks after iteration (to avoid borrow issues)
                        if let Some(idx) = clicked_idx {
                            let modifiers = ui.input(|i| i.modifiers);
                            self.handle_card_click(idx, &modifiers);
                        }

                        if let Some(idx) = double_clicked_idx {
                            if let Some(card) = self.cards.get(idx) {
                                self.pending_edit_window = Some(card.pdf_path().to_path_buf());
                            }
                        }
                    });
            });

        // Handle keyboard shortcuts
        if ui.input(|i| i.key_pressed(egui::Key::Delete)) {
            self.on_delete();
        }

        if ui.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::A)) {
            self.on_select_all();
        }

        if ui.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::Z)) {
            self.undo_manager.write().undo();
        }

        if ui.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::Y)) {
            self.undo_manager.write().redo();
        }
    }
}
