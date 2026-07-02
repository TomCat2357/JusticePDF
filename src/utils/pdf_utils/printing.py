"""印刷設定と印刷実行(N-up・ページ範囲・フィットモード)。"""
import logging
import re
from dataclasses import dataclass, field

import fitz
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QPainter, QImage, QPageLayout
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtWidgets import QWidget



logger = logging.getLogger(__name__)

from .rendering import get_page_count


@dataclass(slots=True)
class PrintSettings:
    """User-chosen print options produced by the print dialog.

    ``page_numbers`` are 1-based indices over the *concatenation* of all pages
    across the printed PDFs in order (empty means "all pages"). ``page_size_id``
    holds a ``QPageSize.PageSizeId`` value.
    """
    printer_name: str = ""
    copies: int = 1
    collate: bool = True
    page_numbers: "list[int]" = field(default_factory=list)
    fit_mode: str = "fit"            # "fit" | "actual" | "shrink" | "custom"
    custom_scale_pct: int = 100
    orientation: str = "auto"        # "auto" | "portrait" | "landscape"
    page_size_id: object = None      # QPageSize.PageSizeId
    duplex: str = "none"             # "none" | "long" | "short"
    color: bool = True               # True = color, False = grayscale
    nup: int = 1                     # 1 / 2 / 4 / 6 / 9 / 16


# Cap raster dimensions (px) when rasterizing pages for print, so HighResolution
# printers (often 1200 dpi) don't produce hundreds-of-MB pixmaps per page.
_PRINT_MAX_RASTER_PX = 4000


def parse_page_range(spec: str, total: int) -> list[int]:
    """Parse a page-range string like ``"1-5, 8, 11-13"`` into a sorted, unique
    list of 1-based page numbers clamped to ``1..total``.

    Full-width commas/dashes are tolerated, whitespace is ignored, ranges are
    inclusive, reversed ranges (a>b) are skipped, and any malformed token is
    ignored. Returns an empty list for empty/invalid input.
    """
    if not spec or total <= 0:
        return []
    for ch in ("，", "、"):
        spec = spec.replace(ch, ",")
    for ch in ("－", "―", "‐", "−", "〜", "～", "–", "—"):
        spec = spec.replace(ch, "-")
    result: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                continue
            for p in range(a, b + 1):
                if 1 <= p <= total:
                    result.add(p)
            continue
        if re.fullmatch(r"\d+", token):
            p = int(token)
            if 1 <= p <= total:
                result.add(p)
    return sorted(result)


def build_flat_index(pdf_paths: "list[str]") -> "list[tuple[str, int]]":
    """Return a flat list mapping a 0-based running index to ``(pdf_path,
    page_index)`` across all *pdf_paths* in order."""
    flat: list[tuple[str, int]] = []
    for path in pdf_paths:
        for i in range(get_page_count(path)):
            flat.append((path, i))
    return flat


def nup_grid(nup: int, landscape: bool) -> "tuple[int, int]":
    """Return ``(rows, cols)`` for an N-up sheet. For 2 and 6, the split
    direction follows the physical sheet orientation."""
    base = {1: (1, 1), 2: (2, 1), 4: (2, 2), 6: (3, 2), 9: (3, 3), 16: (4, 4)}
    rows, cols = base.get(nup, (1, 1))
    if landscape and nup in (2, 6):
        rows, cols = cols, rows
    return rows, cols


def _page_is_landscape(pdf_path: str, page_index: int) -> bool:
    """Return True when the given page is wider than tall."""
    try:
        with fitz.open(pdf_path) as doc:
            if 0 <= page_index < len(doc):
                r = doc[page_index].rect
                return r.width > r.height
    except Exception:
        logger.debug("_page_is_landscape failed: %s p%s", pdf_path, page_index, exc_info=True)
    return False


def _draw_page_into_rect(
    painter: QPainter,
    pdf_path: str,
    page_index: int,
    rect: QRect,
    *,
    fit_mode: str,
    custom_scale_pct: int,
    printer_dpi: int,
    color: bool = True,
) -> None:
    """Rasterize one PDF page and draw it into *rect* (device pixels) according
    to *fit_mode*. Oversized output (actual/custom) is centered and clipped to
    *rect*; smaller output is centered."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_index < 0 or page_index >= len(doc):
                return
            page = doc[page_index]
            pw_pt = page.rect.width
            ph_pt = page.rect.height
            if pw_pt <= 0 or ph_pt <= 0 or rect.width() <= 0 or rect.height() <= 0:
                return

            dev_per_pt = printer_dpi / 72.0
            if fit_mode == "actual":
                place_w = pw_pt * dev_per_pt
                place_h = ph_pt * dev_per_pt
            elif fit_mode == "custom":
                f = max(custom_scale_pct, 1) / 100.0
                place_w = pw_pt * dev_per_pt * f
                place_h = ph_pt * dev_per_pt * f
            elif fit_mode == "shrink":
                place_w = pw_pt * dev_per_pt
                place_h = ph_pt * dev_per_pt
                if place_w > rect.width() or place_h > rect.height():
                    s = min(rect.width() / place_w, rect.height() / place_h)
                    place_w *= s
                    place_h *= s
            else:  # "fit"
                s = min(rect.width() / pw_pt, rect.height() / ph_pt)
                place_w = pw_pt * s
                place_h = ph_pt * s
            if place_w <= 0 or place_h <= 0:
                return

            # Render at a scale matching the placement size, capped for memory.
            render_scale = max(place_w / pw_pt, place_h / ph_pt)
            longest = max(pw_pt, ph_pt) * render_scale
            if longest > _PRINT_MAX_RASTER_PX:
                render_scale *= _PRINT_MAX_RASTER_PX / longest
            mat = fitz.Matrix(render_scale, render_scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = QImage(
                pix.samples, pix.width, pix.height, pix.stride,
                QImage.Format.Format_RGB888,
            ).copy()
            if not color:
                img = img.convertToFormat(QImage.Format.Format_Grayscale8)

            tw = round(place_w)
            th = round(place_h)
            tx = rect.x() + (rect.width() - tw) // 2
            ty = rect.y() + (rect.height() - th) // 2
            painter.save()
            try:
                painter.setClipRect(rect)
                painter.drawImage(QRect(tx, ty, tw, th), img)
            finally:
                painter.restore()
    except Exception:
        logger.debug("_draw_page_into_rect failed: %s p%s", pdf_path, page_index, exc_info=True)


def print_pdfs(
    pdf_paths: "list[str]",
    parent: "QWidget | None" = None,
    *,
    settings: "PrintSettings | None" = None,
    printer: "QPrinter | None" = None,
) -> None:
    """Print one or more PDF files.

    When *settings* and *printer* are supplied (normally by ``PrintDialog``),
    they are honored directly: page range, copies/collate, fit mode, N-up,
    color and orientation. ``settings.page_numbers`` are 1-based indices over
    the concatenation of all pages across *pdf_paths*.

    When called without *settings*, a basic system ``QPrintDialog`` is shown as
    a fallback and every page is printed fit-to-page (legacy behavior).

    Note: page orientation is fixed once for the whole job (mid-document
    orientation changes are unreliable across drivers); ``"auto"`` derives it
    from the first printed page.
    """
    if printer is None:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)

    if settings is None:
        dialog = QPrintDialog(printer, parent)
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return

    flat = build_flat_index(pdf_paths)
    if not flat:
        return

    if settings is not None and settings.page_numbers:
        targets = [n - 1 for n in settings.page_numbers if 1 <= n <= len(flat)]
    else:
        targets = list(range(len(flat)))
    if not targets:
        return

    nup = settings.nup if settings else 1
    fit_mode = settings.fit_mode if settings else "fit"
    custom_scale = settings.custom_scale_pct if settings else 100
    color = settings.color if settings else True
    copies = max(settings.copies if settings else 1, 1)
    collate = settings.collate if settings else True

    # Resolve "auto" orientation from the first printed page (once).
    if settings is not None and settings.orientation == "auto":
        first_path, first_idx = flat[targets[0]]
        printer.setPageOrientation(
            QPageLayout.Orientation.Landscape
            if _page_is_landscape(first_path, first_idx)
            else QPageLayout.Orientation.Portrait
        )

    # Let the driver replicate copies when it can; otherwise loop ourselves.
    driver_copies = printer.supportsMultipleCopies()
    if driver_copies:
        printer.setCopyCount(copies)
        printer.setCollateCopies(collate)
        copy_loops = 1
    else:
        copy_loops = copies

    dpi = printer.resolution()
    sheets = [targets[i:i + nup] for i in range(0, len(targets), nup)]

    def iter_sheets():
        if driver_copies or copy_loops == 1:
            yield from sheets
        elif collate:
            for _ in range(copy_loops):
                yield from sheets
        else:
            for sheet in sheets:
                for _ in range(copy_loops):
                    yield sheet

    painter = QPainter()
    if not painter.begin(printer):
        return
    try:
        first = True
        for sheet in iter_sheets():
            if not first:
                printer.newPage()
            first = False

            viewport = painter.viewport()
            rows, cols = nup_grid(nup, viewport.width() > viewport.height())
            cell_w = viewport.width() / cols
            cell_h = viewport.height() / rows
            for pos, flat_pos in enumerate(sheet):
                r = pos // cols
                c = pos % cols
                cx = viewport.x() + round(c * cell_w)
                cy = viewport.y() + round(r * cell_h)
                cw = viewport.x() + round((c + 1) * cell_w) - cx
                ch = viewport.y() + round((r + 1) * cell_h) - cy
                pdf_path, page_index = flat[flat_pos]
                _draw_page_into_rect(
                    painter, pdf_path, page_index, QRect(cx, cy, cw, ch),
                    fit_mode=fit_mode, custom_scale_pct=custom_scale,
                    printer_dpi=dpi, color=color,
                )
    finally:
        painter.end()
