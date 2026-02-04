//! PDF card widget for displaying PDF files

use crate::pdf_utils::{get_pdf_card_info, PdfCardInfo};
use egui::{Color32, Image, Response, Sense, TextureHandle, Ui, Vec2, Widget};
use image::RgbaImage;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// MIME type for PDF card drag and drop
pub const PDFCARD_MIME_TYPE: &str = "application/x-pdfas-card";

/// Card dimensions
pub const CARD_WIDTH: f32 = 150.0;
pub const THUMBNAIL_SIZE: f32 = 120.0;

/// Widget representing a PDF file as a card
#[derive(Clone)]
pub struct PdfCard {
    /// Path to the PDF file
    pdf_path: PathBuf,
    /// Is the card selected
    is_selected: bool,
    /// Is the card locked (being edited)
    is_locked: bool,
    /// Page count
    page_count: usize,
    /// Thumbnail texture handle
    thumbnail: Option<TextureHandle>,
    /// Thumbnail image data (before conversion to texture)
    thumbnail_image: Option<RgbaImage>,
    /// Whether we need to reload the thumbnail
    needs_reload: bool,
}

impl PdfCard {
    /// Create a new PDF card
    pub fn new(pdf_path: PathBuf) -> Self {
        Self {
            pdf_path,
            is_selected: false,
            is_locked: false,
            page_count: 0,
            thumbnail: None,
            thumbnail_image: None,
            needs_reload: true,
        }
    }

    /// Get the PDF path
    pub fn pdf_path(&self) -> &Path {
        &self.pdf_path
    }

    /// Set the PDF path (for rename operations)
    pub fn set_pdf_path(&mut self, path: PathBuf) {
        self.pdf_path = path;
        self.needs_reload = true;
    }

    /// Get the filename
    pub fn filename(&self) -> String {
        self.pdf_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default()
    }

    /// Get the page count
    pub fn page_count(&self) -> usize {
        self.page_count
    }

    /// Check if selected
    pub fn is_selected(&self) -> bool {
        self.is_selected
    }

    /// Set selection state
    pub fn set_selected(&mut self, selected: bool) {
        self.is_selected = selected;
    }

    /// Check if locked
    pub fn is_locked(&self) -> bool {
        self.is_locked
    }

    /// Set locked state
    pub fn set_locked(&mut self, locked: bool) {
        self.is_locked = locked;
    }

    /// Refresh the card (reload thumbnail and page count)
    pub fn refresh(&mut self) {
        self.needs_reload = true;
        self.thumbnail = None;
    }

    /// Load PDF info (thumbnail and page count)
    fn load_info(&mut self) {
        if !self.needs_reload {
            return;
        }

        match get_pdf_card_info(&self.pdf_path, THUMBNAIL_SIZE as u32) {
            Ok(info) => {
                self.page_count = info.page_count;
                self.thumbnail_image = info.thumbnail;
                self.needs_reload = false;
            }
            Err(e) => {
                log::warn!("Failed to load PDF info for {:?}: {}", self.pdf_path, e);
                self.page_count = 0;
                self.thumbnail_image = None;
                self.needs_reload = false;
            }
        }
    }

    /// Get or create the texture handle
    fn get_texture(&mut self, ctx: &egui::Context) -> Option<&TextureHandle> {
        // Load info if needed
        self.load_info();

        // Create texture if we have image data but no texture
        if self.thumbnail.is_none() {
            if let Some(image) = &self.thumbnail_image {
                let size = [image.width() as usize, image.height() as usize];
                let pixels: Vec<Color32> = image
                    .pixels()
                    .map(|p| Color32::from_rgba_unmultiplied(p[0], p[1], p[2], p[3]))
                    .collect();

                let color_image = egui::ColorImage { size, pixels };
                let texture = ctx.load_texture(
                    format!("pdf_thumb_{}", self.pdf_path.display()),
                    color_image,
                    egui::TextureOptions::LINEAR,
                );
                self.thumbnail = Some(texture);
            }
        }

        self.thumbnail.as_ref()
    }

    /// Draw the card and return the response
    pub fn ui(&mut self, ui: &mut Ui, ctx: &egui::Context) -> PdfCardResponse {
        let mut response = PdfCardResponse::default();

        // Card frame
        let (rect, card_response) = ui.allocate_exact_size(
            Vec2::new(CARD_WIDTH, THUMBNAIL_SIZE + 50.0),
            Sense::click_and_drag(),
        );

        // Background color based on state
        let bg_color = if self.is_locked {
            Color32::from_rgb(208, 208, 208)
        } else if self.is_selected {
            Color32::from_rgb(204, 229, 255)
        } else {
            Color32::WHITE
        };

        let border_color = if self.is_selected && !self.is_locked {
            Color32::from_rgb(0, 123, 255)
        } else {
            Color32::from_rgb(204, 204, 204)
        };

        let border_width = if self.is_selected { 2.0 } else { 1.0 };

        // Draw card background
        let painter = ui.painter_at(rect);
        painter.rect_filled(rect, 4.0, bg_color);
        painter.rect_stroke(rect, 4.0, egui::Stroke::new(border_width, border_color));

        // Draw thumbnail
        let thumb_rect = egui::Rect::from_min_size(
            rect.min + Vec2::new((CARD_WIDTH - THUMBNAIL_SIZE) / 2.0, 5.0),
            Vec2::splat(THUMBNAIL_SIZE),
        );

        if let Some(texture) = self.get_texture(ctx) {
            let size = texture.size_vec2();
            let scale = (THUMBNAIL_SIZE / size.x.max(size.y)).min(1.0);
            let display_size = size * scale;
            let center_offset = (Vec2::splat(THUMBNAIL_SIZE) - display_size) / 2.0;

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
            // Draw placeholder
            painter.rect_filled(thumb_rect, 2.0, Color32::from_rgb(240, 240, 240));
            painter.text(
                thumb_rect.center(),
                egui::Align2::CENTER_CENTER,
                "(empty)",
                egui::FontId::default(),
                Color32::GRAY,
            );
        }

        // Draw page count badge
        let badge_text = format!("{}p", self.page_count);
        let badge_galley = ui.painter().layout_no_wrap(
            badge_text.clone(),
            egui::FontId::proportional(11.0),
            Color32::WHITE,
        );

        let badge_rect = egui::Rect::from_min_size(
            egui::pos2(
                thumb_rect.right() - badge_galley.rect.width() - 10.0,
                thumb_rect.top() + 3.0,
            ),
            badge_galley.rect.size() + Vec2::new(10.0, 4.0),
        );

        painter.rect_filled(badge_rect, 3.0, Color32::from_rgba_unmultiplied(0, 0, 0, 180));
        painter.galley(
            badge_rect.min + Vec2::new(5.0, 2.0),
            badge_galley,
            Color32::WHITE,
        );

        // Draw filename
        let filename = self.filename();
        let text_rect = egui::Rect::from_min_max(
            egui::pos2(rect.min.x + 5.0, thumb_rect.bottom() + 5.0),
            egui::pos2(rect.max.x - 5.0, rect.max.y - 5.0),
        );

        ui.painter().text(
            text_rect.center_top(),
            egui::Align2::CENTER_TOP,
            &filename,
            egui::FontId::proportional(12.0),
            Color32::BLACK,
        );

        // Apply opacity effect if locked
        if self.is_locked {
            painter.rect_filled(rect, 4.0, Color32::from_rgba_unmultiplied(255, 255, 255, 128));
        }

        // Handle interactions
        if card_response.clicked() {
            response.clicked = true;
        }

        if card_response.double_clicked() {
            response.double_clicked = true;
        }

        if card_response.dragged() && !self.is_locked {
            response.drag_started = true;
        }

        if card_response.hovered() {
            response.hovered = true;
        }

        response
    }
}

/// Response from a PDF card interaction
#[derive(Default)]
pub struct PdfCardResponse {
    pub clicked: bool,
    pub double_clicked: bool,
    pub drag_started: bool,
    pub hovered: bool,
}

/// Selection state for PDF cards
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectionMode {
    Single,
    Toggle,  // Ctrl+click
    Range,   // Shift+click
}

impl SelectionMode {
    /// Determine selection mode from keyboard modifiers
    pub fn from_modifiers(modifiers: &egui::Modifiers) -> Self {
        if modifiers.ctrl || modifiers.command {
            SelectionMode::Toggle
        } else if modifiers.shift {
            SelectionMode::Range
        } else {
            SelectionMode::Single
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_card_creation() {
        let card = PdfCard::new(PathBuf::from("/test/file.pdf"));
        assert_eq!(card.filename(), "file.pdf");
        assert!(!card.is_selected());
        assert!(!card.is_locked());
    }

    #[test]
    fn test_selection_mode() {
        let no_mods = egui::Modifiers::NONE;
        let ctrl = egui::Modifiers::CTRL;
        let shift = egui::Modifiers::SHIFT;

        assert_eq!(SelectionMode::from_modifiers(&no_mods), SelectionMode::Single);
        assert_eq!(SelectionMode::from_modifiers(&ctrl), SelectionMode::Toggle);
        assert_eq!(SelectionMode::from_modifiers(&shift), SelectionMode::Range);
    }
}
