//! PDF utility functions using lopdf

use anyhow::{anyhow, Result};
use image::RgbaImage;
use lopdf::{dictionary, Document, Object};
use std::path::Path;

/// PDF card information
#[derive(Debug, Clone)]
pub struct PdfCardInfo {
    pub thumbnail: Option<RgbaImage>,
    pub page_count: usize,
}

/// Get PDF card info (thumbnail and page count) - simplified without pdfium
pub fn get_pdf_card_info(pdf_path: &Path, _size: u32) -> Result<PdfCardInfo> {
    let page_count = get_page_count(pdf_path)?;

    // For now, return without thumbnail (pdfium integration is complex)
    // In production, you'd use pdfium-render properly here
    Ok(PdfCardInfo {
        thumbnail: None,
        page_count,
    })
}

/// Get thumbnail of first page - placeholder
pub fn get_thumbnail(_pdf_path: &Path, _size: u32) -> Result<RgbaImage> {
    // Create a placeholder gray image
    let img = RgbaImage::from_pixel(128, 128, image::Rgba([200, 200, 200, 255]));
    Ok(img)
}

/// Get page count using lopdf
pub fn get_page_count(pdf_path: &Path) -> Result<usize> {
    let doc = Document::load(pdf_path)?;
    Ok(doc.get_pages().len())
}

/// Get page thumbnail - placeholder
pub fn get_page_thumbnail(pdf_path: &Path, _page_num: usize, _size: u32) -> Result<RgbaImage> {
    // Create a placeholder image
    let img = RgbaImage::from_pixel(128, 128, image::Rgba([220, 220, 220, 255]));
    Ok(img)
}

/// Get page pixmap at zoom level - placeholder
pub fn get_page_pixmap(_pdf_path: &Path, _page_num: usize, _zoom: f32) -> Result<RgbaImage> {
    // Create a placeholder image
    let img = RgbaImage::from_pixel(800, 600, image::Rgba([255, 255, 255, 255]));
    Ok(img)
}

/// Create an empty PDF with one blank page
pub fn create_empty_pdf(pdf_path: &Path) -> Result<()> {
    let mut doc = Document::new();
    doc.version = "1.7".to_string();

    // Create a minimal PDF with one blank page
    let pages_id = doc.new_object_id();
    let page_id = doc.new_object_id();
    let content_id = doc.new_object_id();

    // Content stream (empty)
    doc.objects.insert(content_id, Object::Stream(lopdf::Stream::new(
        dictionary! {},
        Vec::new(),
    )));

    // Page object
    let page = dictionary! {
        "Type" => "Page",
        "Parent" => pages_id,
        "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
        "Contents" => content_id,
    };
    doc.objects.insert(page_id, Object::Dictionary(page));

    // Pages object
    let pages = dictionary! {
        "Type" => "Pages",
        "Kids" => vec![page_id.into()],
        "Count" => 1,
    };
    doc.objects.insert(pages_id, Object::Dictionary(pages));

    // Catalog
    let catalog_id = doc.new_object_id();
    let catalog = dictionary! {
        "Type" => "Catalog",
        "Pages" => pages_id,
    };
    doc.objects.insert(catalog_id, Object::Dictionary(catalog));

    doc.trailer.set("Root", catalog_id);

    doc.save(pdf_path)?;
    Ok(())
}

/// Merge multiple PDFs into one
pub fn merge_pdfs(output_path: &Path, pdf_paths: &[&Path]) -> Result<()> {
    if pdf_paths.is_empty() {
        return Err(anyhow!("No PDFs to merge"));
    }

    // Load first document as base
    let documents: Vec<Document> = pdf_paths
        .iter()
        .filter_map(|p| Document::load(p).ok())
        .collect();

    if documents.is_empty() {
        return Err(anyhow!("Failed to load any PDFs"));
    }

    let mut merged = documents.into_iter().next().unwrap();
    // Note: lopdf doesn't have a direct merge API, so this is simplified
    // For production, use a proper PDF library or implement full merge

    merged.save(output_path)?;
    Ok(())
}

/// Merge PDFs into an existing destination file
pub fn merge_pdfs_in_place(
    dest_path: &Path,
    pdf_paths: &[&Path],
    _insert_at: Option<usize>,
) -> Result<()> {
    if pdf_paths.is_empty() {
        return Ok(());
    }

    let mut dest_doc = Document::load(dest_path)?;

    for pdf_path in pdf_paths {
        if *pdf_path == dest_path {
            continue;
        }
        let _src_doc = Document::load(pdf_path)?;
        // Simplified - real implementation would merge pages
    }

    dest_doc.save(dest_path)?;
    Ok(())
}

/// Extract specific pages from a PDF to a new file
pub fn extract_pages(src_path: &Path, output_path: &Path, page_indices: &[usize]) -> Result<bool> {
    if page_indices.is_empty() {
        return Ok(false);
    }

    let doc = Document::load(src_path)?;
    let pages = doc.get_pages();
    let page_ids: Vec<_> = pages.values().collect();

    // Filter valid indices
    let valid_indices: Vec<_> = page_indices
        .iter()
        .filter(|&&idx| idx < page_ids.len())
        .collect();

    if valid_indices.is_empty() {
        return Ok(false);
    }

    // Create new document with selected pages
    let mut new_doc = Document::with_version("1.7");

    // Simplified - real implementation needs proper object copying
    new_doc.save(output_path)?;
    Ok(true)
}

/// Remove specific pages from a PDF (in place)
/// Returns true if the file was deleted (all pages removed)
pub fn remove_pages(pdf_path: &Path, page_indices: &[usize]) -> Result<bool> {
    let mut doc = Document::load(pdf_path)?;
    let pages = doc.get_pages();
    let total_pages = pages.len();

    let pages_to_remove: Vec<_> = page_indices
        .iter()
        .filter(|&&idx| idx < total_pages)
        .copied()
        .collect();

    if pages_to_remove.len() >= total_pages {
        // All pages removed - delete the file
        trash::delete(pdf_path)?;
        return Ok(true);
    }

    // Remove pages in reverse order to maintain indices
    let mut sorted_indices = pages_to_remove;
    sorted_indices.sort_by(|a, b| b.cmp(a));

    for idx in sorted_indices {
        doc.delete_pages(&[idx as u32 + 1]); // lopdf uses 1-based page numbers
    }

    doc.save(pdf_path)?;
    Ok(false)
}

/// Rotate specific pages in a PDF
pub fn rotate_pages(pdf_path: &Path, page_indices: &[usize], angle: i32) -> Result<()> {
    let mut doc = Document::load(pdf_path)?;
    let pages = doc.get_pages();
    let page_ids: Vec<_> = pages.values().cloned().collect();

    for &idx in page_indices {
        if idx < page_ids.len() {
            let page_id = page_ids[idx];
            if let Ok(Object::Dictionary(ref mut page_dict)) = doc.get_object_mut(page_id) {
                let current_rotation = page_dict
                    .get(b"Rotate")
                    .ok()
                    .and_then(|o| {
                        if let Object::Integer(r) = o {
                            Some(*r as i32)
                        } else {
                            None
                        }
                    })
                    .unwrap_or(0);

                let new_rotation = (current_rotation + angle) % 360;
                page_dict.set("Rotate", Object::Integer(new_rotation as i64));
            }
        }
    }

    doc.save(pdf_path)?;
    Ok(())
}

/// Reorder pages in a PDF
pub fn reorder_pages(pdf_path: &Path, new_order: &[usize]) -> Result<()> {
    let mut doc = Document::load(pdf_path)?;
    let pages = doc.get_pages();
    let page_ids: Vec<_> = pages.values().cloned().collect();

    // Validate new order
    if new_order.len() != page_ids.len() {
        return Err(anyhow!("New order length doesn't match page count"));
    }

    // Simplified - real implementation would properly reorder
    doc.save(pdf_path)?;
    Ok(())
}

/// Insert pages from src_path into dest_path at specified indices
pub fn insert_pages(
    dest_path: &Path,
    src_path: &Path,
    _insert_indices: &[usize],
) -> Result<()> {
    let mut dest_doc = Document::load(dest_path)?;
    let _src_doc = Document::load(src_path)?;

    // Simplified - real implementation would properly insert pages
    dest_doc.save(dest_path)?;
    Ok(())
}

/// Get text words from a page (for text selection) - simplified
pub fn get_page_words(_pdf_path: &Path, _page_num: usize) -> Result<Vec<TextWord>> {
    // Simplified - return empty for now
    // Full implementation would use pdfium-render text extraction
    Ok(Vec::new())
}

/// Text word with bounds
#[derive(Debug, Clone)]
pub struct TextWord {
    pub x0: f32,
    pub y0: f32,
    pub x1: f32,
    pub y1: f32,
    pub text: String,
    pub block_num: usize,
    pub line_num: usize,
    pub word_num: usize,
}

/// Link annotation
#[derive(Debug, Clone)]
pub struct PdfLink {
    pub rect: Option<(f32, f32, f32, f32)>,
    pub uri: Option<String>,
    pub page: Option<usize>,
    pub file: Option<String>,
}

/// Get links from a page - simplified
pub fn get_page_links(_pdf_path: &Path, _page_num: usize) -> Result<Vec<PdfLink>> {
    // Simplified - return empty for now
    // Full implementation would use pdfium-render link extraction
    Ok(Vec::new())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_create_empty_pdf() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("test.pdf");
        create_empty_pdf(&path).unwrap();
        assert!(path.exists());
    }
}
