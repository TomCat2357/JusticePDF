"""PDF utility functions using PyMuPDF."""
import fitz
from PyQt6.QtGui import QPixmap, QImage


def get_thumbnail(pdf_path: str, size: int = 128) -> QPixmap:
    """Generate a thumbnail of the first page of a PDF."""
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return QPixmap()
        page = doc[0]
        zoom = size / max(page.rect.width, page.rect.height)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        doc.close()
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)
    except Exception:
        return QPixmap()


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    try:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


def create_empty_pdf(pdf_path: str) -> None:
    """Create an empty PDF with 1 blank page.

    Note: PyMuPDF cannot save PDFs with 0 pages,
    so this creates a PDF with a single blank page.
    """
    doc = fitz.open()
    doc.new_page()  # Add one blank page
    doc.save(pdf_path)
    doc.close()


def get_page_thumbnail(pdf_path: str, page_num: int, size: int = 128) -> QPixmap:
    """Generate a thumbnail of a specific page."""
    try:
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            doc.close()
            return QPixmap()
        page = doc[page_num]
        zoom = size / max(page.rect.width, page.rect.height)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        doc.close()
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)
    except Exception:
        return QPixmap()


def get_page_pixmap(pdf_path: str, page_num: int, zoom: float = 1.0) -> QPixmap:
    """Render a page at the given zoom factor."""
    try:
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            doc.close()
            return QPixmap()
        page = doc[page_num]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        doc.close()
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)
    except Exception:
        return QPixmap()


def get_page_words(pdf_path: str, page_num: int) -> list[tuple]:
    """Extract word-level text with coordinates for a page."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            return page.get_text("words")
    except Exception:
        return []


def get_page_links(pdf_path: str, page_num: int) -> list[dict]:
    """Extract link annotations with rectangles for a page."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            links = page.get_links()
        normalized: list[dict] = []
        for link in links:
            rect = link.get("from")
            if rect is None:
                rect_tuple = None
            elif hasattr(rect, "x0"):
                rect_tuple = (rect.x0, rect.y0, rect.x1, rect.y1)
            else:
                rect_tuple = tuple(rect)
            item = dict(link)
            item["from"] = rect_tuple
            normalized.append(item)
        return normalized
    except Exception:
        return []


def merge_pdfs(output_path: str, pdf_paths: list[str]) -> None:
    """Merge multiple PDFs into one."""
    output_doc = fitz.open()
    for path in pdf_paths:
        src_doc = fitz.open(path)
        output_doc.insert_pdf(src_doc)
        src_doc.close()
    output_doc.save(output_path)
    output_doc.close()


def extract_pages(src_path: str, output_path: str, page_indices: list[int]) -> bool:
    """Extract specific pages from a PDF to a new file.

    Returns:
        True if extraction succeeded, False if no pages to extract.
    """
    src_doc = fitz.open(src_path)
    output_doc = fitz.open()
    for idx in page_indices:
        if 0 <= idx < len(src_doc):
            output_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)

    # Check if output has any pages
    if len(output_doc) == 0:
        output_doc.close()
        src_doc.close()
        return False

    output_doc.save(output_path)
    output_doc.close()
    src_doc.close()
    return True


def remove_pages(pdf_path: str, page_indices: list[int]) -> bool:
    """Remove specific pages from a PDF (in place).

    Returns:
        True if the file was deleted (all pages removed), False otherwise.
    """
    from send2trash import send2trash

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pages_to_remove = [idx for idx in page_indices if 0 <= idx < total_pages]

    if len(pages_to_remove) >= total_pages:
        # All pages removed - delete the file
        doc.close()
        send2trash(pdf_path)
        return True

    for idx in sorted(pages_to_remove, reverse=True):
        doc.delete_page(idx)
    doc.saveIncr()
    doc.close()
    return False


def rotate_pages(pdf_path: str, page_indices: list[int], angle: int = 90) -> None:
    """Rotate specific pages in a PDF (in place)."""
    doc = fitz.open(pdf_path)
    for idx in page_indices:
        if 0 <= idx < len(doc):
            page = doc[idx]
            page.set_rotation((page.rotation + angle) % 360)
    doc.saveIncr()
    doc.close()


def reorder_pages(pdf_path: str, new_order: list[int]) -> None:
    """Reorder pages in a PDF (in place)."""
    doc = fitz.open(pdf_path)
    doc.select(new_order)
    doc.saveIncr()
    doc.close()


def insert_pages(dest_path: str, src_path: str, insert_indices: list[int]) -> None:
    """Insert pages from src_path into dest_path at specified indices.
    
    Args:
        dest_path: Destination PDF path
        src_path: Source PDF path containing pages to insert
        insert_indices: List of positions where each page should be inserted
    """
    dest_doc = fitz.open(dest_path)
    src_doc = fitz.open(src_path)
    
    # Insert pages in reverse order to maintain correct indices
    for i in reversed(range(len(src_doc))):
        if i < len(insert_indices):
            insert_at = insert_indices[i]
            dest_doc.insert_pdf(src_doc, from_page=i, to_page=i, start_at=insert_at)
    
    dest_doc.saveIncr()
    dest_doc.close()
    src_doc.close()
