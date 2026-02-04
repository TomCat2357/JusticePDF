//! JusticePDF - Visual PDF merge/split application
//!
//! A desktop application for managing PDF files with drag-and-drop support.

mod app;
mod pdf_utils;
mod path_utils;
mod undo_manager;
mod folder_watcher;
mod pdf_card;
mod main_window;
mod page_edit_window;
mod zoom_view;

use eframe::egui;
use log::LevelFilter;

fn main() -> eframe::Result<()> {
    // Initialize logging
    env_logger::Builder::new()
        .filter_level(LevelFilter::Info)
        .format_timestamp_secs()
        .init();

    log::info!("Starting JusticePDF");

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1000.0, 700.0])
            .with_min_inner_size([600.0, 400.0])
            .with_title("JusticePDF")
            .with_drag_and_drop(true),
        ..Default::default()
    };

    eframe::run_native(
        "JusticePDF",
        options,
        Box::new(|cc| Ok(Box::new(app::JusticePdfApp::new(cc)))),
    )
}
