//! Zoom view widget for viewing PDF pages at high resolution

use crate::pdf_utils::{PdfLink, TextWord};
use egui::{Color32, Context, Pos2, Rect, Response, Sense, TextureHandle, Ui, Vec2};
use image::RgbaImage;
use std::path::Path;

/// Zoom page view widget
pub struct ZoomView {
    /// Page texture
    texture: Option<TextureHandle>,
    /// Zoom factor
    zoom_factor: f32,
    /// Words on the page
    words: Vec<TextWord>,
    /// Word rectangles (scaled)
    word_rects: Vec<Rect>,
    /// Links on the page
    links: Vec<PdfLink>,
    /// Link rectangles (scaled)
    link_rects: Vec<Option<Rect>>,
    /// Selected word indices
    selected_word_indices: Vec<usize>,
    /// Selection start position
    selection_origin: Option<Pos2>,
    /// Current selection rectangle
    selection_rect: Option<Rect>,
    /// Is selection active
    selection_active: bool,
    /// Pressed link (for click handling)
    pressed_link: Option<usize>,
}

impl ZoomView {
    /// Create a new zoom view
    pub fn new() -> Self {
        Self {
            texture: None,
            zoom_factor: 1.0,
            words: Vec::new(),
            word_rects: Vec::new(),
            links: Vec::new(),
            link_rects: Vec::new(),
            selected_word_indices: Vec::new(),
            selection_origin: None,
            selection_rect: None,
            selection_active: false,
            pressed_link: None,
        }
    }

    /// Set the page content
    pub fn set_page(
        &mut self,
        ctx: &Context,
        pdf_path: &Path,
        page_num: usize,
        image: RgbaImage,
        words: Vec<TextWord>,
        links: Vec<PdfLink>,
        zoom_factor: f32,
    ) {
        self.zoom_factor = zoom_factor;
        self.words = words;
        self.links = links;

        // Create texture
        let size = [image.width() as usize, image.height() as usize];
        let pixels: Vec<Color32> = image
            .pixels()
            .map(|p| Color32::from_rgba_unmultiplied(p[0], p[1], p[2], p[3]))
            .collect();

        let color_image = egui::ColorImage { size, pixels };
        self.texture = Some(ctx.load_texture(
            format!("zoom_page_{}_{}", pdf_path.display(), page_num),
            color_image,
            egui::TextureOptions::LINEAR,
        ));

        // Calculate word rectangles
        self.word_rects = self
            .words
            .iter()
            .map(|word| {
                Rect::from_min_max(
                    Pos2::new(word.x0 * zoom_factor, word.y0 * zoom_factor),
                    Pos2::new(word.x1 * zoom_factor, word.y1 * zoom_factor),
                )
            })
            .collect();

        // Calculate link rectangles
        self.link_rects = self
            .links
            .iter()
            .map(|link| {
                link.rect.map(|(x0, y0, x1, y1)| {
                    Rect::from_min_max(
                        Pos2::new(x0 * zoom_factor, y0 * zoom_factor),
                        Pos2::new(x1 * zoom_factor, y1 * zoom_factor),
                    )
                })
            })
            .collect();

        // Clear selection
        self.selected_word_indices.clear();
        self.selection_origin = None;
        self.selection_rect = None;
        self.selection_active = false;
        self.pressed_link = None;
    }

    /// Get the link at a position
    fn link_at(&self, pos: Pos2) -> Option<usize> {
        for (i, rect) in self.link_rects.iter().enumerate() {
            if let Some(rect) = rect {
                if rect.contains(pos) {
                    return Some(i);
                }
            }
        }
        None
    }

    /// Get the word index at a position
    fn word_at(&self, pos: Pos2) -> Option<usize> {
        for (i, rect) in self.word_rects.iter().enumerate() {
            if rect.contains(pos) {
                return Some(i);
            }
        }
        None
    }

    /// Update selection based on current selection rectangle
    fn update_selection(&mut self) {
        self.selected_word_indices.clear();

        if let Some(sel_rect) = self.selection_rect {
            for (i, rect) in self.word_rects.iter().enumerate() {
                if rect.intersects(sel_rect) {
                    self.selected_word_indices.push(i);
                }
            }
        }
    }

    /// Get selected text
    pub fn selected_text(&self) -> String {
        if self.selected_word_indices.is_empty() {
            return String::new();
        }

        let mut selected_words: Vec<&TextWord> = self
            .selected_word_indices
            .iter()
            .filter_map(|&i| self.words.get(i))
            .collect();

        // Sort by position (block, line, word)
        selected_words.sort_by(|a, b| {
            (a.block_num, a.line_num, a.word_num).cmp(&(b.block_num, b.line_num, b.word_num))
        });

        let mut lines: Vec<String> = Vec::new();
        let mut current_line: Vec<&str> = Vec::new();
        let mut current_key: Option<(usize, usize)> = None;

        for word in selected_words {
            let key = (word.block_num, word.line_num);

            if current_key.is_some() && current_key != Some(key) {
                if !current_line.is_empty() {
                    lines.push(current_line.join(" "));
                    current_line.clear();
                }
            }

            current_line.push(&word.text);
            current_key = Some(key);
        }

        if !current_line.is_empty() {
            lines.push(current_line.join(" "));
        }

        lines.join("\n")
    }

    /// Draw the zoom view
    pub fn ui(&mut self, ui: &mut Ui) -> ZoomViewResponse {
        let mut response = ZoomViewResponse::default();

        if let Some(texture) = &self.texture {
            let size = texture.size_vec2();

            let (rect, ui_response) = ui.allocate_exact_size(size, Sense::click_and_drag());

            // Draw page image
            let painter = ui.painter_at(rect);
            painter.image(
                texture.id(),
                rect,
                Rect::from_min_max(Pos2::ZERO, Pos2::new(1.0, 1.0)),
                Color32::WHITE,
            );

            // Draw word selection highlights
            if !self.selected_word_indices.is_empty() {
                for &idx in &self.selected_word_indices {
                    if let Some(word_rect) = self.word_rects.get(idx) {
                        let highlight_rect = word_rect.translate(rect.min.to_vec2());
                        painter.rect_filled(
                            highlight_rect,
                            0.0,
                            Color32::from_rgba_unmultiplied(0, 120, 215, 80),
                        );
                    }
                }
            }

            // Draw selection rectangle
            if let Some(sel_rect) = self.selection_rect {
                let screen_rect = sel_rect.translate(rect.min.to_vec2());
                painter.rect_stroke(
                    screen_rect,
                    0.0,
                    egui::Stroke::new(1.0, Color32::from_rgb(0, 120, 215)),
                );
            }

            // Handle interactions
            let local_pos = ui_response.hover_pos().map(|p| Pos2::new(p.x - rect.min.x, p.y - rect.min.y));

            // Update cursor based on hover
            if let Some(pos) = local_pos {
                if self.link_at(pos).is_some() {
                    ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
                } else if self.word_at(pos).is_some() {
                    ui.ctx().set_cursor_icon(egui::CursorIcon::Text);
                }
            }

            // Handle mouse press
            if ui_response.drag_started() {
                if let Some(pos) = local_pos {
                    self.selection_origin = Some(pos);
                    self.selection_rect = Some(Rect::from_min_max(pos, pos));
                    self.pressed_link = self.link_at(pos);
                    self.selection_active = self.pressed_link.is_none();

                    if self.selection_active {
                        self.update_selection();
                    }
                }
            }

            // Handle mouse drag
            if ui_response.dragged() {
                if let (Some(origin), Some(pos)) = (self.selection_origin, local_pos) {
                    if self.selection_active || self.pressed_link.is_some() {
                        // Check drag distance
                        if (pos - origin).length() >= 3.0 {
                            self.selection_active = true;
                            self.pressed_link = None;
                        }
                    }

                    if self.selection_active {
                        self.selection_rect = Some(Rect::from_two_pos(origin, pos));
                        self.update_selection();
                    }
                }
            }

            // Handle mouse release
            if ui_response.drag_stopped() {
                if self.selection_active {
                    // Finalize selection
                    if let Some(sel_rect) = self.selection_rect {
                        if sel_rect.width() < 3.0 && sel_rect.height() < 3.0 {
                            // Click, not drag - select single word
                            if let Some(pos) = local_pos {
                                self.selected_word_indices = self
                                    .word_at(pos)
                                    .map(|i| vec![i])
                                    .unwrap_or_default();
                            }
                        }
                    }
                    self.selection_rect = None;
                    self.selection_active = false;
                } else if let Some(link_idx) = self.pressed_link {
                    // Link click
                    if let Some(link) = self.links.get(link_idx) {
                        response.link_clicked = Some(link.clone());
                    }
                }

                self.selection_origin = None;
                self.pressed_link = None;
            }

            // Copy shortcut
            if ui.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::C)) {
                let text = self.selected_text();
                if !text.is_empty() {
                    ui.ctx().copy_text(text);
                }
            }
        } else {
            ui.label("Loading...");
        }

        response
    }
}

impl Default for ZoomView {
    fn default() -> Self {
        Self::new()
    }
}

/// Response from zoom view interaction
#[derive(Default)]
pub struct ZoomViewResponse {
    pub link_clicked: Option<PdfLink>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_zoom_view_creation() {
        let view = ZoomView::new();
        assert!(view.texture.is_none());
        assert!(view.selected_word_indices.is_empty());
    }
}
