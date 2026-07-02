"""ページ描画・サムネイル・テキスト抽出/検索。"""
import logging

import fitz
from PyQt6.QtGui import QPixmap, QImage



logger = logging.getLogger(__name__)

from .common import _get_file_cache_token, _pixmap_cache


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
        cache_token = _get_file_cache_token(pdf_path)
        with fitz.open(pdf_path) as doc:
            page_count = len(doc)
            if page_count == 0:
                return QPixmap(), 0
            cache_key = (pdf_path, 0, size, None, True, cache_token)
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
    annots: bool = True,
) -> QPixmap:
    """Render a PDF page to a pixmap with either size-based or zoom-based scaling."""
    try:
        cache_token = _get_file_cache_token(pdf_path)
        cache_key = (
            pdf_path,
            page_num,
            size,
            round(zoom, 4) if zoom is not None else None,
            bool(annots),
            cache_token,
        )
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
            pix = page.get_pixmap(matrix=mat, annots=annots)
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


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    try:
        with fitz.open(pdf_path) as doc:
            return len(doc)
    except Exception:
        logger.debug("get_page_count failed: %s", pdf_path, exc_info=True)
        return 0


def get_page_size_points(pdf_path: str, page_index: int) -> "tuple[float, float]":
    """Return a page's (width, height) in PDF points, or (0.0, 0.0) on error."""
    try:
        with fitz.open(pdf_path) as doc:
            if 0 <= page_index < len(doc):
                r = doc[page_index].rect
                return float(r.width), float(r.height)
    except Exception:
        logger.debug("get_page_size_points failed: %s p%s", pdf_path, page_index, exc_info=True)
    return 0.0, 0.0



def get_page_thumbnail(pdf_path: str, page_num: int, size: int = 128) -> QPixmap:
    """Generate a thumbnail of a specific page."""
    return _render_page_pixmap(pdf_path, page_num, size=size)


def get_page_pixmap(pdf_path: str, page_num: int, zoom: float = 1.0, *, annots: bool = True) -> QPixmap:
    """Render a page at the given zoom factor."""
    return _render_page_pixmap(pdf_path, page_num, zoom=zoom, annots=annots)


def render_page_thumbnails_batch(pdf_path: str, page_nums: list[int], size: int = 128) -> dict[int, QPixmap]:
    """Batch-render thumbnails for multiple pages, using cache where possible.

    Opens the PDF only once for all cache-missed pages.
    """
    cache_token = _get_file_cache_token(pdf_path)
    result: dict[int, QPixmap] = {}
    miss_pages: list[int] = []

    for pn in page_nums:
        cache_key = (pdf_path, pn, size, None, True, cache_token)
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
                    cache_key = (pdf_path, pn, size, None, True, cache_token)
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


def get_page_chars(pdf_path: str, page_num: int) -> list[dict]:
    """Extract character-level text with coordinates for a page.

    Returns a flat list in reading order. Each entry::

        {"c": str, "bbox": (x0, y0, x1, y1), "line_id": int,
         "wmode": int, "dir": (cos, sin)}

    Spaces are kept (needed to reproduce gaps faithfully); other control
    characters are dropped. ``line_id`` increments globally across the page so
    consecutive entries with the same id belong to the same line.
    """
    chars: list[dict] = []
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            raw = page.get_text("rawdict")
            line_id = 0
            for block in raw.get("blocks", []):
                # type 0 = text block; skip image blocks (type 1).
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    wmode = line.get("wmode", 0)
                    direction = line.get("dir", (1.0, 0.0))
                    has_char = False
                    for span in line.get("spans", []):
                        for ch in span.get("chars", []):
                            text = ch.get("c", "")
                            if not text:
                                continue
                            # Drop control characters but keep spaces.
                            if text != " " and ord(text[0]) < 0x20:
                                continue
                            bbox = ch.get("bbox")
                            if bbox is None:
                                continue
                            chars.append(
                                {
                                    "c": text,
                                    "bbox": (
                                        float(bbox[0]),
                                        float(bbox[1]),
                                        float(bbox[2]),
                                        float(bbox[3]),
                                    ),
                                    "line_id": line_id,
                                    "wmode": int(wmode),
                                    "dir": (
                                        float(direction[0]),
                                        float(direction[1]),
                                    ),
                                }
                            )
                            has_char = True
                    if has_char:
                        line_id += 1
            return chars
    except Exception:
        logger.debug(
            "get_page_chars failed: pdf=%s page=%s",
            pdf_path,
            page_num,
            exc_info=True,
        )
        return []


def search_text_in_pdf(pdf_path: str, query: str) -> dict[int, list]:
    """Search query text across all pages.

    Returns {page_num: [fitz.Rect, ...]} for pages with at least one hit.
    Pages without hits are not included. PyMuPDF's search_for is case-insensitive
    by default.
    """
    if not query:
        return {}
    results: dict[int, list] = {}
    try:
        with fitz.open(pdf_path) as doc:
            for i in range(len(doc)):
                page = doc[i]
                rects = page.search_for(query)
                if rects:
                    results[i] = list(rects)
    except Exception:
        logger.debug(
            "search_text_in_pdf failed: pdf=%s query=%r",
            pdf_path,
            query,
            exc_info=True,
        )
        return {}
    return results


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

