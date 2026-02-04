//! Page edit window for editing PDF pages

use crate::pdf_card::SelectionMode;
use crate::pdf_utils::{
    extract_pages, get_page_count, get_page_links, get_page_pixmap, get_page_thumbnail,
    get_page_words, insert_pages, remove_pages, reorder_pages, rotate_pages, PdfLink, TextWord,
};
use crate::undo_manager::{UndoAction, UndoManager};
use crate::zoom_view::ZoomView;
use egui::{Color32, Context, Sense, TextureHandle, Ui, Vec2};
use image::RgbaImage;
use parking_lot::RwLock;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// MIME type for page thumbnail drag and drop
pub const PAGETHUMBNAIL_MIME_TYPE: &str = "application/x-pdfas-page";

/// Thumbnail size
const THUMBNAIL_SIZE: u32 = 120;

/// Page thumbnail widget
#[derive(Clone)]
pub struct PageThumbnail {
    /// Page number (0-indexed)
    page_num: usize,
    /// Display number (for UI)
    display_num: usize,
    /// Is selected
    is_selected: bool,
    /// Thumbnail texture
    thumbnail: Option<TextureHandle>,
    /// Thumbnail image data
    thumbnail_image: Option<RgbaImage>,
    /// Needs reload
    needs_reload: bool,
}

impl PageThumbnail {
    /// Create a new page thumbnail
    fn new(page_num: usize) -> Self {
        Self {
            page_num,
            display_num: page_num,
            is_selected: false,
            thumbnail: None,
            thumbnail_image: None,
            needs_reload: true,
        }
    }

    /// Get page number
    pub fn page_num(&self) -> usize {
        self.page_num
    }

    /// Check if selected
    pub fn is_selected(&self) -> bool {
        self.is_selected
    }

    /// Set selection state
    pub fn set_selected(&mut self, selected: bool) {
        self.is_selected = selected;
    }

    /// Refresh thumbnail
    pub fn refresh(&mut self) {
        self.needs_reload = true;
        self.thumbnail = None;
    }

    /// Load thumbnail
    fn load_thumbnail(&mut self, pdf_path: &Path) {
        if !self.needs_reload {
            return;
        }

        match get_page_thumbnail(pdf_path, self.page_num, THUMBNAIL_SIZE) {
            Ok(image) => {
                self.thumbnail_image = Some(image);
                self.needs_reload = false;
            }
            Err(e) => {
                log::warn!("Failed to load page thumbnail: {}", e);
                self.thumbnail_image = None;
                self.needs_reload = false;
            }
        }
    }

    /// Get or create texture
    fn get_texture(&mut self, ctx: &Context, pdf_path: &Path) -> Option<&TextureHandle> {
        self.load_thumbnail(pdf_path);

        if self.thumbnail.is_none() {
            if let Some(image) = &self.thumbnail_image {
                let size = [image.width() as usize, image.height() as usize];
                let pixels: Vec<Color32> = image
                    .pixels()
                    .map(|p| Color32::from_rgba_unmultiplied(p[0], p[1], p[2], p[3]))
                    .collect();

                let color_image = egui::ColorImage { size, pixels };
                let texture = ctx.load_texture(
                    format!("page_thumb_{}_{}", pdf_path.display(), self.page_num),
                    color_image,
                    egui::TextureOptions::LINEAR,
                );
                self.thumbnail = Some(texture);
            }
        }

        self.thumbnail.as_ref()
    }

    /// Draw the thumbnail
    fn ui(&mut self, ui: &mut Ui, ctx: &Context, pdf_path: &Path) -> PageThumbnailResponse {
        let mut response = PageThumbnailResponse::default();
        let thumb_size = THUMBNAIL_SIZE as f32;

        let (rect, thumb_response) = ui.allocate_exact_size(
            Vec2::new(thumb_size + 10.0, thumb_size + 30.0),
            Sense::click_and_drag(),
        );

        // Background
        let bg_color = if self.is_selected {
            Color32::from_rgb(204, 229, 255)
        } else {
            Color32::WHITE
        };

        let border_color = if self.is_selected {
            Color32::from_rgb(0, 123, 255)
        } else {
            Color32::from_rgb(204, 204, 204)
        };

        let painter = ui.painter_at(rect);
        painter.rect_filled(rect, 4.0, bg_color);
        painter.rect_stroke(rect, 4.0, egui::Stroke::new(if self.is_selected { 2.0 } else { 1.0 }, border_color));

        // Thumbnail
        let thumb_rect = egui::Rect::from_min_size(
            rect.min + Vec2::new(5.0, 5.0),
            Vec2::splat(thumb_size),
        );

        if let Some(texture) = self.get_texture(ctx, pdf_path) {
            let size = texture.size_vec2();
            let scale = (thumb_size / size.x.max(size.y)).min(1.0);
            let display_size = size * scale;
            let center_offset = (Vec2::splat(thumb_size) - display_size) / 2.0;

            let img_rect = egui::Rect::from_min_size(
                thumb_rect.min + center_offset,
                display_size,
            );

            painter.image(
                texture.id(),
                img_rect,
                egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                Color32::WHITE,
            );
        } else {
            painter.rect_filled(thumb_rect, 2.0, Color32::from_rgb(240, 240, 240));
        }

        // Page number
        painter.text(
            egui::pos2(rect.center().x, thumb_rect.bottom() + 10.0),
            egui::Align2::CENTER_CENTER,
            format!("{}", self.display_num + 1),
            egui::FontId::proportional(12.0),
            Color32::BLACK,
        );

        // Handle interactions
        if thumb_response.clicked() {
            response.clicked = true;
        }

        if thumb_response.double_clicked() {
            response.double_clicked = true;
        }

        if thumb_response.dragged() {
            response.drag_started = true;
        }

        response
    }
}

/// Response from page thumbnail interaction
#[derive(Default)]
pub struct PageThumbnailResponse {
    pub clicked: bool,
    pub double_clicked: bool,
    pub drag_started: bool,
}

/// View mode for the page edit window
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ViewMode {
    Grid,
    Zoom,
}

/// Page edit window state
pub struct PageEditWindow {
    /// Path to the PDF file
    pdf_path: PathBuf,
    /// Page thumbnails
    thumbnails: Vec<PageThumbnail>,
    /// Selected thumbnail indices
    selected_indices: Vec<usize>,
    /// Undo manager
    undo_manager: Arc<RwLock<UndoManager>>,
    /// Current view mode
    view_mode: ViewMode,
    /// Zoom view state
    zoom_view: ZoomView,
    /// Current zoom page (when in zoom mode)
    zoom_page_num: Option<usize>,
    /// Zoom factor (percentage)
    zoom_percent: i32,
    /// Drop indicator index
    drop_indicator_index: Option<usize>,
    /// Text cache for zoom view (page_num -> (words, links))
    text_cache: HashMap<usize, (Vec<TextWord>, Vec<PdfLink>)>,
}

impl PageEditWindow {
    /// Create a new page edit window
    pub fn new(pdf_path: PathBuf, undo_manager: Arc<RwLock<UndoManager>>) -> Self {
        let mut window = Self {
            pdf_path,
            thumbnails: Vec::new(),
            selected_indices: Vec::new(),
            undo_manager,
            view_mode: ViewMode::Grid,
            zoom_view: ZoomView::new(),
            zoom_page_num: None,
            zoom_percent: 100,
            drop_indicator_index: None,
            text_cache: HashMap::new(),
        };

        window.load_pages();
        window
    }

    /// Load pages from the PDF
    fn load_pages(&mut self) {
        self.thumbnails.clear();
        self.selected_indices.clear();
        self.text_cache.clear();

        match get_page_count(&self.pdf_path) {
            Ok(count) => {
                for i in 0..count {
                    self.thumbnails.push(PageThumbnail::new(i));
                }
            }
            Err(e) => {
                log::error!("Failed to get page count: {}", e);
            }
        }
    }

    /// Refresh all pages
    pub fn refresh(&mut self) {
        for thumb in &mut self.thumbnails {
            thumb.refresh();
        }
        self.text_cache.clear();
    }

    /// Clear selection
    fn clear_selection(&mut self) {
        for idx in &self.selected_indices {
            if *idx < self.thumbnails.len() {
                self.thumbnails[*idx].set_selected(false);
            }
        }
        self.selected_indices.clear();
    }

    /// Handle thumbnail click
    fn handle_thumbnail_click(&mut self, idx: usize, modifiers: &egui::Modifiers) {
        let mode = SelectionMode::from_modifiers(modifiers);

        match mode {
            SelectionMode::Single => {
                if !self.selected_indices.contains(&idx) {
                    self.clear_selection();
                    self.selected_indices.push(idx);
                    self.thumbnails[idx].set_selected(true);
                }
            }
            SelectionMode::Toggle => {
                if let Some(pos) = self.selected_indices.iter().position(|&i| i == idx) {
                    self.selected_indices.remove(pos);
                    self.thumbnails[idx].set_selected(false);
                } else {
                    self.selected_indices.push(idx);
                    self.thumbnails[idx].set_selected(true);
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
                            self.thumbnails[i].set_selected(true);
                        }
                    }
                } else {
                    self.selected_indices.push(idx);
                    self.thumbnails[idx].set_selected(true);
                }
            }
        }
    }

    /// Open zoom view for a page
    fn open_zoom_view(&mut self, page_num: usize) {
        self.zoom_page_num = Some(page_num);
        self.zoom_percent = 100;
        self.view_mode = ViewMode::Zoom;
    }

    /// Exit zoom view
    fn exit_zoom_view(&mut self) {
        self.view_mode = ViewMode::Grid;
        self.zoom_page_num = None;
    }

    /// Handle delete action
    fn on_delete(&mut self) {
        if self.selected_indices.is_empty() {
            return;
        }

        let indices: Vec<usize> = self.selected_indices.clone();

        if let Err(e) = remove_pages(&self.pdf_path, &indices) {
            log::error!("Failed to remove pages: {}", e);
            return;
        }

        self.load_pages();
    }

    /// Handle rotate action
    fn on_rotate(&mut self) {
        if self.selected_indices.is_empty() {
            return;
        }

        let indices: Vec<usize> = self.selected_indices.clone();

        if let Err(e) = rotate_pages(&self.pdf_path, &indices, 90) {
            log::error!("Failed to rotate pages: {}", e);
            return;
        }

        for idx in &indices {
            if *idx < self.thumbnails.len() {
                self.thumbnails[*idx].refresh();
            }
        }
    }

    /// Handle select all action
    fn on_select_all(&mut self) {
        self.clear_selection();
        for i in 0..self.thumbnails.len() {
            self.selected_indices.push(i);
            self.thumbnails[i].set_selected(true);
        }
    }

    /// Render zoom page
    fn render_zoom_page(&mut self, ctx: &Context) {
        if let Some(page_num) = self.zoom_page_num {
            let zoom = self.zoom_percent as f32 / 100.0;

            match get_page_pixmap(&self.pdf_path, page_num, zoom) {
                Ok(image) => {
                    // Get or cache text info
                    let (words, links) = self.text_cache.entry(page_num).or_insert_with(|| {
                        let words = get_page_words(&self.pdf_path, page_num).unwrap_or_default();
                        let links = get_page_links(&self.pdf_path, page_num).unwrap_or_default();
                        (words, links)
                    });

                    self.zoom_view.set_page(ctx, &self.pdf_path, page_num, image, words.clone(), links.clone(), zoom);
                }
                Err(e) => {
                    log::error!("Failed to render page: {}", e);
                }
            }
        }
    }

    /// Calculate grid columns
    fn calculate_columns(&self, available_width: f32) -> usize {
        let spacing = 10.0;
        let cell_width = THUMBNAIL_SIZE as f32 + 10.0 + spacing;
        let usable = available_width - 20.0;
        ((usable + spacing) / cell_width).max(1.0) as usize
    }

    /// Draw the page edit window UI
    pub fn ui(&mut self, ui: &mut Ui, ctx: &Context) {
        match self.view_mode {
            ViewMode::Grid => self.ui_grid(ui, ctx),
            ViewMode::Zoom => self.ui_zoom(ui, ctx),
        }
    }

    /// Draw grid view
    fn ui_grid(&mut self, ui: &mut Ui, ctx: &Context) {
        // Toolbar
        ui.horizontal(|ui| {
            let can_undo = self.undo_manager.read().can_undo();
            let can_redo = self.undo_manager.read().can_redo();
            let has_selection = !self.selected_indices.is_empty();

            if ui.add_enabled(can_undo, egui::Button::new("Undo")).clicked() {
                self.undo_manager.write().undo();
                self.load_pages();
            }

            if ui.add_enabled(can_redo, egui::Button::new("Redo")).clicked() {
                self.undo_manager.write().redo();
                self.load_pages();
            }

            ui.separator();

            if ui
                .add_enabled(has_selection, egui::Button::new("Delete"))
                .clicked()
            {
                self.on_delete();
            }

            if ui.button("Rename").clicked() {
                // TODO: Implement rename
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
        });

        ui.separator();

        // Page grid
        let available_width = ui.available_width();
        let cols = self.calculate_columns(available_width);
        let spacing = 10.0;

        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                ui.spacing_mut().item_spacing = Vec2::splat(spacing);

                egui::Grid::new("page_grid")
                    .num_columns(cols)
                    .spacing([spacing, spacing])
                    .show(ui, |ui| {
                        let mut clicked_idx = None;
                        let mut double_clicked_idx = None;

                        for (idx, thumb) in self.thumbnails.iter_mut().enumerate() {
                            let response = thumb.ui(ui, ctx, &self.pdf_path);

                            if response.clicked {
                                clicked_idx = Some(idx);
                            }

                            if response.double_clicked {
                                double_clicked_idx = Some(idx);
                            }

                            if (idx + 1) % cols == 0 {
                                ui.end_row();
                            }
                        }

                        if let Some(idx) = clicked_idx {
                            let modifiers = ui.input(|i| i.modifiers);
                            self.handle_thumbnail_click(idx, &modifiers);
                        }

                        if let Some(idx) = double_clicked_idx {
                            self.open_zoom_view(idx);
                        }
                    });
            });

        // Keyboard shortcuts
        if ui.input(|i| i.key_pressed(egui::Key::Delete)) {
            self.on_delete();
        }

        if ui.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::A)) {
            self.on_select_all();
        }
    }

    /// Draw zoom view
    fn ui_zoom(&mut self, ui: &mut Ui, ctx: &Context) {
        // Render the current page
        self.render_zoom_page(ctx);

        // Zoom controls
        ui.horizontal(|ui| {
            if ui.button("Back").clicked() {
                self.exit_zoom_view();
            }

            ui.separator();

            if ui.button("-").clicked() {
                self.zoom_percent = (self.zoom_percent - 5).max(25);
                self.render_zoom_page(ctx);
            }

            if ui.button("+").clicked() {
                self.zoom_percent = (self.zoom_percent + 5).min(400);
                self.render_zoom_page(ctx);
            }

            if ui.button("100%").clicked() {
                self.zoom_percent = 100;
                self.render_zoom_page(ctx);
            }

            ui.label(format!("{}%", self.zoom_percent));

            ui.separator();

            // Page navigation
            let page_count = self.thumbnails.len();
            let current_page = self.zoom_page_num.unwrap_or(0);

            if ui.add_enabled(current_page > 0, egui::Button::new("<")).clicked() {
                self.zoom_page_num = Some(current_page.saturating_sub(1));
                self.render_zoom_page(ctx);
            }

            ui.label(format!("{}/{}", current_page + 1, page_count));

            if ui.add_enabled(current_page < page_count.saturating_sub(1), egui::Button::new(">")).clicked() {
                self.zoom_page_num = Some(current_page + 1);
                self.render_zoom_page(ctx);
            }
        });

        ui.separator();

        // Zoom view content
        egui::ScrollArea::both()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                self.zoom_view.ui(ui);
            });

        // Keyboard shortcuts
        if ui.input(|i| i.key_pressed(egui::Key::Escape)) {
            self.exit_zoom_view();
        }

        // Ctrl+wheel zoom
        if ui.input(|i| i.modifiers.ctrl) {
            let scroll = ui.input(|i| i.smooth_scroll_delta.y);
            if scroll != 0.0 {
                let delta = if scroll > 0.0 { 5 } else { -5 };
                self.zoom_percent = (self.zoom_percent + delta).clamp(25, 400);
                self.render_zoom_page(ctx);
            }
        }
    }
}
