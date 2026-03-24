"""PDF utility functions using PyMuPDF."""
import html
import json
import logging
import os
import re
import shutil
import tempfile
from collections import OrderedDict
from dataclasses import dataclass

import fitz
from PyQt6.QtGui import QPixmap, QImage


logger = logging.getLogger(__name__)
JUSTICEPDF_FREETEXT_SUBJECT_PREFIX = "JusticePDF-FreeText:"


@dataclass(slots=True)
class FreeTextAnnotData:
    page_num: int
    xref: int
    rect: tuple[float, float, float, float]
    content: str
    fontsize: float
    text_color: tuple[float, float, float]
    fill_color: tuple[float, float, float] | None
    border_color: tuple[float, float, float] | None
    border_width: float
    fill_opacity: float
    fontname: str = "Helv"
    annotation_id: str = ""
    subject: str = ""


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

_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_DA_COLOR_RE = re.compile(rf"({_FLOAT_RE})\s+({_FLOAT_RE})\s+({_FLOAT_RE})\s+rg")
_DA_FONT_RE = re.compile(rf"/([^\s/]+)\s+({_FLOAT_RE})\s+Tf")
_CSS_DECL_RE = re.compile(r"\s*([^:]+)\s*:\s*([^;]+)\s*")
_CSS_BORDER_RE = re.compile(
    rf"({_FLOAT_RE})(?:px|pt)?\s+\w+\s+(#[0-9a-fA-F]{{6}}|rgb\([^)]+\))"
)


def _save_document_in_place(doc: fitz.Document, pdf_path: str) -> None:
    """Persist a modified document, falling back when incremental save fails."""
    try:
        doc.saveIncr()
    except Exception:
        tmp_path: str | None = None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            doc.save(tmp_path)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        shutil.move(tmp_path, pdf_path)
    _pixmap_cache.clear()


def _parse_float_array(value: str) -> list[float]:
    return [float(item) for item in re.findall(_FLOAT_RE, value or "")]


def _normalize_color(
    values: list[float] | tuple[float, ...] | None,
    fallback: tuple[float, float, float] | None = None,
) -> tuple[float, float, float] | None:
    if not values or len(values) < 3:
        return fallback
    return (float(values[0]), float(values[1]), float(values[2]))


def _parse_css_declarations(style: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in (style or "").split(";"):
        match = _CSS_DECL_RE.fullmatch(part.strip())
        if not match:
            continue
        key, value = match.groups()
        result[key.lower()] = value.strip()
    return result


def _css_color_to_rgb(value: str | None) -> tuple[float, float, float] | None:
    if not value:
        return None
    value = value.strip()
    if value.lower() == "transparent":
        return None
    if value.startswith("#") and len(value) == 7:
        return (
            int(value[1:3], 16) / 255.0,
            int(value[3:5], 16) / 255.0,
            int(value[5:7], 16) / 255.0,
        )
    if value.lower().startswith("rgb(") and value.endswith(")"):
        parts = [p.strip() for p in value[4:-1].split(",")]
        if len(parts) == 3:
            try:
                return (
                    float(parts[0]) / 255.0,
                    float(parts[1]) / 255.0,
                    float(parts[2]) / 255.0,
                )
            except ValueError:
                return None
    return None


def _color_to_css(color: tuple[float, float, float] | None) -> str:
    if color is None:
        return "transparent"
    r = max(0, min(255, round(color[0] * 255)))
    g = max(0, min(255, round(color[1] * 255)))
    b = max(0, min(255, round(color[2] * 255)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _parse_da(da_value: str) -> tuple[str, float, tuple[float, float, float]]:
    fontname = "Helv"
    fontsize = 11.0
    text_color = (0.0, 0.0, 0.0)

    font_match = _DA_FONT_RE.search(da_value or "")
    if font_match:
        fontname = font_match.group(1)
        fontsize = float(font_match.group(2))

    color_match = _DA_COLOR_RE.search(da_value or "")
    if color_match:
        text_color = (
            float(color_match.group(1)),
            float(color_match.group(2)),
            float(color_match.group(3)),
        )
    return fontname, fontsize, text_color


def _pdf_font_to_css(fontname: str) -> str:
    name = (fontname or "Helv").lower()
    if "cour" in name:
        return "Courier"
    if "tiro" in name or "times" in name:
        return "Times New Roman"
    return "Helvetica"


def _css_font_to_pdf(fontname: str | None) -> str:
    name = (fontname or "").lower()
    if "cour" in name:
        return "Cour"
    if "times" in name or "serif" in name:
        return "TiRo"
    return "Helv"


def _extract_text_from_rc(rc_value: str) -> str:
    if not rc_value:
        return ""
    text = re.sub(r"<[^>]+>", "", rc_value)
    return html.unescape(text).strip()


def _decode_subject_metadata(subject: str) -> dict[str, object] | None:
    if not subject or not subject.startswith(JUSTICEPDF_FREETEXT_SUBJECT_PREFIX):
        return None
    raw = subject[len(JUSTICEPDF_FREETEXT_SUBJECT_PREFIX):]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _encode_subject_metadata(data: FreeTextAnnotData) -> str:
    payload = {
        "text_color": list(data.text_color),
        "fill_color": list(data.fill_color) if data.fill_color is not None else None,
        "border_color": list(data.border_color) if data.border_color is not None else None,
        "border_width": float(data.border_width),
        "fontsize": float(data.fontsize),
        "fontname": data.fontname,
        "fill_opacity": float(data.fill_opacity),
    }
    return JUSTICEPDF_FREETEXT_SUBJECT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _build_richtext_style(data: FreeTextAnnotData) -> str:
    parts = [
        f"font-size:{max(1.0, float(data.fontsize)):g}pt",
        f"font-family:{_pdf_font_to_css(data.fontname)}",
        f"color:{_color_to_css(data.text_color)}",
        "margin:0",
        "padding:0",
    ]
    if data.fill_color is not None:
        parts.append(f"background-color:{_color_to_css(data.fill_color)}")
    parts.append("border:0px solid transparent")
    return "; ".join(parts) + ";"


def _extract_freetext_data(
    doc: fitz.Document,
    page_num: int,
    annot: fitz.Annot,
) -> FreeTextAnnotData:
    xref = annot.xref
    info = annot.info
    subject = info.get("subject", "")
    metadata = _decode_subject_metadata(subject) or {}

    _, da_value = doc.xref_get_key(xref, "DA")
    fontname, fontsize, text_color = _parse_da(da_value)

    rect = tuple(annot.rect)

    _, fill_value = doc.xref_get_key(xref, "C")
    fill_color = _normalize_color(_parse_float_array(fill_value))

    _, bs_value = doc.xref_get_key(xref, "BS")
    bs_widths = _parse_float_array(bs_value)
    border_width = float(bs_widths[0]) if bs_widths else float(annot.border.get("width", 0.0))
    border_color: tuple[float, float, float] | None = None

    _, ds_value = doc.xref_get_key(xref, "DS")
    css = _parse_css_declarations(ds_value if ds_value != "null" else "")
    if "font-size" in css:
        try:
            fontsize = float(re.findall(_FLOAT_RE, css["font-size"])[0])
        except (IndexError, ValueError):
            pass
    if "font-family" in css:
        fontname = _css_font_to_pdf(css["font-family"])
    css_text_color = _css_color_to_rgb(css.get("color"))
    if css_text_color is not None:
        text_color = css_text_color
    css_fill_color = _css_color_to_rgb(css.get("background-color"))
    if css_fill_color is not None:
        fill_color = css_fill_color
    css_border_color = _css_color_to_rgb(css.get("border-color"))
    if css_border_color is None and "border" in css:
        border_match = _CSS_BORDER_RE.search(css["border"])
        if border_match:
            border_width = float(border_match.group(1))
            css_border_color = _css_color_to_rgb(border_match.group(2))
    if "border-width" in css:
        try:
            border_width = float(re.findall(_FLOAT_RE, css["border-width"])[0])
        except (IndexError, ValueError):
            pass
    if css_border_color is not None:
        border_color = css_border_color

    if isinstance(metadata.get("fontsize"), (int, float)):
        fontsize = float(metadata["fontsize"])
    if isinstance(metadata.get("fontname"), str):
        fontname = str(metadata["fontname"])
    metadata_text_color = metadata.get("text_color")
    if isinstance(metadata_text_color, list):
        parsed = _normalize_color(metadata_text_color)
        if parsed is not None:
            text_color = parsed
    metadata_fill_color = metadata.get("fill_color")
    if isinstance(metadata_fill_color, list):
        fill_color = _normalize_color(metadata_fill_color)
    elif metadata_fill_color is None and "fill_color" in metadata:
        fill_color = None
    metadata_border_color = metadata.get("border_color")
    if isinstance(metadata_border_color, list):
        border_color = _normalize_color(metadata_border_color)
    elif metadata_border_color is None and "border_color" in metadata:
        border_color = None
    if isinstance(metadata.get("border_width"), (int, float)):
        border_width = float(metadata["border_width"])

    if border_width <= 0:
        border_color = None
        border_width = 0.0
    elif border_color is None:
        border_color = (0.0, 0.0, 0.0)

    _, contents_value = doc.xref_get_key(xref, "Contents")
    content = info.get("content") or (contents_value if contents_value != "null" else "")
    if not content:
        _, rc_value = doc.xref_get_key(xref, "RC")
        if rc_value != "null":
            content = _extract_text_from_rc(rc_value)

    # Fill opacity: prefer metadata value (new format); fall back to CA attribute (old format).
    if "fill_opacity" in metadata:
        try:
            fill_opacity = float(metadata["fill_opacity"])
        except (TypeError, ValueError):
            fill_opacity = 1.0
    else:
        _, ca_value = doc.xref_get_key(xref, "CA")
        if ca_value == "null":
            fill_opacity = 1.0
        else:
            try:
                fill_opacity = float(ca_value)
            except ValueError:
                fill_opacity = float(annot.opacity or 1.0)

    _, name_value = doc.xref_get_key(xref, "NM")
    annotation_id = info.get("id") or (name_value if name_value != "null" else "")

    return FreeTextAnnotData(
        page_num=page_num,
        xref=xref,
        rect=(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
        content=content or "",
        fontsize=float(fontsize),
        text_color=text_color,
        fill_color=fill_color,
        border_color=border_color,
        border_width=float(border_width),
        fill_opacity=max(0.0, min(1.0, fill_opacity)),
        fontname=fontname or "Helv",
        annotation_id=annotation_id,
        subject=subject,
    )


def _apply_fill_opacity_to_annot(doc: fitz.Document, annot: fitz.Annot, fill_opacity: float) -> None:
    """Modify the annotation's AP stream to apply opacity only to the fill/background rect."""
    if fill_opacity >= 1.0 - 1e-6:
        return

    xref = annot.xref
    _, n_val = doc.xref_get_key(xref, "AP/N")
    if not n_val or n_val == "null":
        return

    parts = n_val.strip().split()
    if not parts:
        return
    try:
        n_xref = int(parts[0])
    except ValueError:
        return

    stream_bytes = doc.xref_stream(n_xref)
    if not stream_bytes:
        return

    stream_str = stream_bytes.decode("latin-1")

    # Add an ExtGState with non-stroking (fill) opacity only.
    doc.xref_set_key(
        n_xref,
        "Resources/ExtGState",
        f"<</GS_bg <</Type /ExtGState /ca {fill_opacity:.6f}>> >>",
    )

    # Find fill-rect pattern: "R G B rg  X Y W H re  f" and wrap with the GS.
    num = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    sp = r"[ \t\r\n]+"
    fill_pat = re.compile(
        r"("
        + num + sp + num + sp + num + sp + r"rg" + sp
        + num + sp + num + sp + num + sp + num + sp + r"re" + sp
        + r"f(?=[ \t\r\n]|$)"
        + r")",
        re.MULTILINE,
    )

    match = fill_pat.search(stream_str)
    if match:
        start, end = match.span()
        modified = (
            stream_str[:start]
            + "q /GS_bg gs "
            + match.group(1).strip()
            + " Q\n"
            + stream_str[end:]
        )
        doc.update_stream(n_xref, modified.encode("latin-1"), new=True)


def _add_freetext_annot_to_page(page: fitz.Page, data: FreeTextAnnotData) -> fitz.Annot:
    rect = fitz.Rect(*data.rect)
    if data.border_width > 0:
        inset = float(data.border_width) / 2.0
        if rect.width > inset * 2 and rect.height > inset * 2:
            rect = fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset)
    annot = page.add_freetext_annot(
        rect,
        data.content,
        fontsize=max(1.0, float(data.fontsize)),
        fontname=data.fontname or "Helv",
        text_color=data.text_color,
        fill_color=data.fill_color,
        border_width=max(0.0, float(data.border_width)),
        opacity=1.0,
        richtext=True,
        style=_build_richtext_style(data),
    )
    annot.set_info(subject=_encode_subject_metadata(data))
    _apply_fill_opacity_to_annot(page.parent, annot, data.fill_opacity)
    return annot


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


def list_freetext_annots(pdf_path: str, page_num: int | None = None) -> list[FreeTextAnnotData]:
    """Return normalized FreeText annotations for one page or the whole document."""
    results: list[FreeTextAnnotData] = []
    try:
        with fitz.open(pdf_path) as doc:
            page_numbers: range | list[int]
            if page_num is None:
                page_numbers = range(len(doc))
            elif 0 <= page_num < len(doc):
                page_numbers = [page_num]
            else:
                return []

            for pn in page_numbers:
                page = doc[pn]
                annots = page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT])
                if annots is None:
                    continue
                for annot in annots:
                    results.append(_extract_freetext_data(doc, pn, annot))
    except Exception:
        logger.debug("list_freetext_annots failed: %s", pdf_path, exc_info=True)
    return results


def create_freetext_annot(pdf_path: str, data: FreeTextAnnotData) -> FreeTextAnnotData:
    """Create a new FreeText annotation and return the normalized saved form."""
    doc = fitz.open(pdf_path)
    try:
        if data.page_num < 0 or data.page_num >= len(doc):
            raise IndexError(f"page out of range: {data.page_num}")
        page = doc[data.page_num]
        annot = _add_freetext_annot_to_page(page, data)
        saved = _extract_freetext_data(doc, data.page_num, annot)
        _save_document_in_place(doc, pdf_path)
        return saved
    finally:
        doc.close()


def delete_freetext_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    """Delete a FreeText annotation by page number and xref."""
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is None or annot.type[0] != fitz.PDF_ANNOT_FREE_TEXT:
            return False
        page.delete_annot(annot)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def replace_freetext_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: FreeTextAnnotData,
) -> FreeTextAnnotData:
    """Replace an existing FreeText annotation and return the saved replacement."""
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
        replacement = _add_freetext_annot_to_page(page, data)
        saved = _extract_freetext_data(doc, page_num, replacement)
        _save_document_in_place(doc, pdf_path)
        return saved
    finally:
        doc.close()


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
