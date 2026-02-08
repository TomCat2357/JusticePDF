"""PDF utility functions using PyMuPDF."""
import logging
import os
import shutil
import tempfile
from collections import OrderedDict

import fitz
from PyQt6.QtGui import QPixmap, QImage


logger = logging.getLogger(__name__)


class _PixmapCache:
    def __init__(self, maxsize: int = 256):
        self._maxsize = maxsize
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()

    def get(self, key: tuple) -> QPixmap | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: tuple, pixmap: QPixmap) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = pixmap
            return
        if len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = pixmap

    def clear(self) -> None:
        self._cache.clear()


_pixmap_cache = _PixmapCache(maxsize=256)


def _get_file_mtime(pdf_path: str) -> float:
    try:
        return os.path.getmtime(pdf_path)
    except OSError:
        return 0.0


def _pixmap_to_qpixmap(pix: "fitz.Pixmap") -> QPixmap:
    """Convert a PyMuPDF Pixmap to QPixmap safely.

    QImage(data, ...) normally references the provided memory. Since pix.samples
    is backed by pix's internal buffer, we force a deep copy to avoid dangling
    references after pix is freed.
    """
    img = QImage(
        pix.samples,
        pix.width,
        pix.height,
        pix.stride,
        QImage.Format.Format_RGB888,
    ).copy()
    return QPixmap.fromImage(img)


def get_pdf_card_info(pdf_path: str, size: int = 128) -> tuple[QPixmap, int]:
    """Get (thumbnail, page_count) for main-grid cards with a single PDF open."""
    try:
        mtime = _get_file_mtime(pdf_path)
        with fitz.open(pdf_path) as doc:
            page_count = len(doc)
            if page_count == 0:
                return QPixmap(), 0
            cache_key = (pdf_path, 0, size, None, mtime)
            cached = _pixmap_cache.get(cache_key)
            if cached is not None:
                return cached, page_count
            page = doc[0]
            zoom = size / max(page.rect.width, page.rect.height)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            qpix = _pixmap_to_qpixmap(pix)
            _pixmap_cache.put(cache_key, qpix)
            return qpix, page_count
    except Exception:
        logger.debug("get_pdf_card_info failed: %s", pdf_path, exc_info=True)
        return QPixmap(), 0


def _render_page_pixmap(
    pdf_path: str,
    page_num: int,
    *,
    size: int | None = None,
    zoom: float | None = None,
) -> QPixmap:
    """Render a PDF page to a pixmap with either size-based or zoom-based scaling."""
    try:
        mtime = _get_file_mtime(pdf_path)
        cache_key = (pdf_path, page_num, size, round(zoom, 4) if zoom is not None else None, mtime)
        cached = _pixmap_cache.get(cache_key)
        if cached is not None:
            return cached
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc) or page_num < 0:
                return QPixmap()
            page = doc[page_num]
            if size is not None:
                scale = size / max(page.rect.width, page.rect.height)
            else:
                scale = zoom or 1.0
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
        qpix = _pixmap_to_qpixmap(pix)
        _pixmap_cache.put(cache_key, qpix)
        return qpix
    except Exception:
        logger.debug(
            "_render_page_pixmap failed: pdf=%s page=%s size=%s zoom=%s",
            pdf_path,
            page_num,
            size,
            zoom,
            exc_info=True,
        )
        return QPixmap()


def get_thumbnail(pdf_path: str, size: int = 128) -> QPixmap:
    """Generate a thumbnail of the first page of a PDF."""
    return _render_page_pixmap(pdf_path, 0, size=size)


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    try:
        with fitz.open(pdf_path) as doc:
            return len(doc)
    except Exception:
        logger.debug("get_page_count failed: %s", pdf_path, exc_info=True)
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
    return _render_page_pixmap(pdf_path, page_num, size=size)


def get_page_pixmap(pdf_path: str, page_num: int, zoom: float = 1.0) -> QPixmap:
    """Render a page at the given zoom factor."""
    return _render_page_pixmap(pdf_path, page_num, zoom=zoom)


def render_page_thumbnails_batch(pdf_path: str, page_nums: list[int], size: int = 128) -> dict[int, QPixmap]:
    """Batch-render thumbnails for multiple pages, using cache where possible.

    Opens the PDF only once for all cache-missed pages.
    """
    mtime = _get_file_mtime(pdf_path)
    result: dict[int, QPixmap] = {}
    miss_pages: list[int] = []

    for pn in page_nums:
        cache_key = (pdf_path, pn, size, None, mtime)
        cached = _pixmap_cache.get(cache_key)
        if cached is not None:
            result[pn] = cached
        else:
            miss_pages.append(pn)

    if miss_pages:
        try:
            with fitz.open(pdf_path) as doc:
                for pn in miss_pages:
                    if pn < 0 or pn >= len(doc):
                        result[pn] = QPixmap()
                        continue
                    page = doc[pn]
                    scale = size / max(page.rect.width, page.rect.height)
                    mat = fitz.Matrix(scale, scale)
                    pix = page.get_pixmap(matrix=mat)
                    qpix = _pixmap_to_qpixmap(pix)
                    cache_key = (pdf_path, pn, size, None, mtime)
                    _pixmap_cache.put(cache_key, qpix)
                    result[pn] = qpix
        except Exception:
            logger.debug("render_page_thumbnails_batch failed: %s", pdf_path, exc_info=True)
            for pn in miss_pages:
                if pn not in result:
                    result[pn] = QPixmap()

    return result


def get_page_words(pdf_path: str, page_num: int) -> list[tuple]:
    """Extract word-level text with coordinates for a page."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            return page.get_text("words")
    except Exception:
        logger.debug(
            "get_page_words failed: pdf=%s page=%s",
            pdf_path,
            page_num,
            exc_info=True,
        )
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
        logger.debug(
            "get_page_links failed: pdf=%s page=%s",
            pdf_path,
            page_num,
            exc_info=True,
        )
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


def merge_pdfs_in_place(
    dest_path: str,
    pdf_paths: list[str],
    *,
    insert_at: int | None = None,
) -> None:
    """Merge PDFs into an existing destination file in place.

    If insert_at is None, append to end. Otherwise insert sequentially starting at insert_at.
    Uses incremental save when possible; falls back to full save to temp.
    """
    if not pdf_paths:
        return

    tmp_path: str | None = None
    dest_doc = fitz.open(dest_path)
    try:
        if insert_at is None:
            for path in pdf_paths:
                if path == dest_path:
                    continue
                with fitz.open(path) as src_doc:
                    dest_doc.insert_pdf(src_doc)
        else:
            idx = insert_at
            for path in pdf_paths:
                if path == dest_path:
                    continue
                with fitz.open(path) as src_doc:
                    dest_doc.insert_pdf(src_doc, start_at=idx)
                    idx += len(src_doc)
        try:
            dest_doc.saveIncr()
            return
        except Exception:
            pass

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            dest_doc.save(tmp_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    finally:
        dest_doc.close()

    if tmp_path is not None:
        shutil.move(tmp_path, dest_path)


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
