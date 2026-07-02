"""画像エクスポート・PDF圧縮・ラスタライズ。"""
import logging
import os

import fitz



logger = logging.getLogger(__name__)


def _render_page_to_image_bytes(
    page: fitz.Page,
    dpi: int,
    *,
    image_format: str = "png",
    jpeg_quality: int = 75,
) -> tuple[bytes, str]:
    """Render a page to encoded image bytes.

    Args:
        page: Source page.
        dpi: Resolution for rasterization.
        image_format: "png" (lossless) or "jpeg"/"jpg" (lossy).
        jpeg_quality: JPEG quality (1-100); ignored for PNG.

    Returns:
        ``(data, ext)`` where ``ext`` is ".jpg" or ".png".
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0))
    if image_format.lower() in ("jpeg", "jpg"):
        if pix.alpha:  # JPEG cannot carry an alpha channel
            pix = fitz.Pixmap(pix, 0)
        return pix.tobytes("jpeg", jpg_quality=jpeg_quality), ".jpg"
    return pix.tobytes("png"), ".png"


def export_pages_as_images(
    pdf_path: str,
    output_dir: str,
    fmt: str = "png",
    dpi: int = 150,
    quality: int = 85,
    page_indices: list[int] | None = None,
) -> list[str]:
    """Export PDF pages as image files.

    Args:
        pdf_path: Source PDF file path.
        output_dir: Directory to save images.
        fmt: Image format ("png" or "jpeg").
        dpi: Resolution in DPI.
        quality: JPEG quality (1-100). Ignored for PNG.
        page_indices: Pages to export (0-based). None means all pages.

    Returns:
        List of created image file paths.
    """
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    created: list[str] = []

    doc = fitz.open(pdf_path)
    try:
        indices = page_indices if page_indices is not None else list(range(len(doc)))
        for page_num in indices:
            data, ext = _render_page_to_image_bytes(
                doc[page_num], dpi, image_format=fmt, jpeg_quality=quality
            )
            out_path = os.path.join(output_dir, f"{base}_p{page_num + 1}{ext}")
            with open(out_path, "wb") as f:
                f.write(data)
            created.append(out_path)
    finally:
        doc.close()

    return created


def images_to_pdf(image_paths: list[str], output_path: str) -> None:
    """Create a PDF from image files. Each image becomes one page.

    Args:
        image_paths: List of image file paths.
        output_path: Destination PDF path.
    """
    doc = fitz.open()
    try:
        for img_path in image_paths:
            img_doc = fitz.open(img_path)
            # fitz.open on an image creates a 1-page PDF-like document
            pdf_bytes = img_doc.convert_to_pdf()
            img_doc.close()
            img_pdf = fitz.open("pdf", pdf_bytes)
            doc.insert_pdf(img_pdf)
            img_pdf.close()
        doc.save(output_path, garbage=1, deflate=True)
    finally:
        doc.close()


def _downsample_images(
    doc: fitz.Document,
    max_dpi: int = 150,
    jpeg_quality: int = 75,
) -> None:
    """Re-compress images in *doc* in-place.

    Each image whose effective resolution exceeds *max_dpi* is
    down-scaled and re-encoded as JPEG at the given quality.
    Images already at or below the target resolution are still
    re-encoded if the JPEG result is smaller.
    """
    seen_xrefs: set[int] = set()

    # Suppress MuPDF C-library stderr noise (e.g. "Not a JPEG file")
    fitz.TOOLS.mupdf_display_errors(False)
    try:
        _downsample_images_inner(doc, max_dpi, jpeg_quality, seen_xrefs)
    finally:
        fitz.TOOLS.mupdf_display_errors(True)
        fitz.TOOLS.mupdf_warnings(reset=True)


def _downsample_images_inner(
    doc: fitz.Document,
    max_dpi: int,
    jpeg_quality: int,
    seen_xrefs: set[int],
) -> None:
    for page in doc:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            # Decode image via xref (handles all PDF filter types)
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception:
                continue

            orig_w = pix.width
            orig_h = pix.height

            # Get original compressed size for comparison
            try:
                raw_stream = doc.xref_stream_raw(xref)
                orig_size = len(raw_stream) if raw_stream else 0
            except Exception:
                orig_size = 0

            # Determine the effective DPI from page placement
            try:
                img_rects = page.get_image_rects(xref)
            except Exception:
                img_rects = []
            if img_rects:
                r = img_rects[0]
                eff_dpi_x = orig_w / (r.width / 72) if r.width else 9999
                eff_dpi_y = orig_h / (r.height / 72) if r.height else 9999
                eff_dpi = max(eff_dpi_x, eff_dpi_y)
            else:
                eff_dpi = 9999

            scale = min(1.0, max_dpi / eff_dpi) if eff_dpi > max_dpi else 1.0
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))

            # Ensure RGB without alpha for JPEG encoding
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)  # drop alpha channel
            if pix.colorspace != fitz.csRGB:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            # Resize via Pixmap if dimensions changed
            if new_w != orig_w or new_h != orig_h:
                # Build a scaled pixmap using a temporary single-page PDF
                tmp_doc = fitz.open()
                tmp_page = tmp_doc.new_page(width=new_w, height=new_h)
                tmp_page.insert_image(
                    fitz.Rect(0, 0, new_w, new_h),
                    pixmap=pix,
                )
                pix = tmp_page.get_pixmap(
                    matrix=fitz.Identity,
                    clip=fitz.Rect(0, 0, new_w, new_h),
                )
                tmp_doc.close()

            jpeg_bytes = pix.tobytes("jpeg", jpg_quality=jpeg_quality)

            # Only replace if the result is actually smaller
            if orig_size == 0 or len(jpeg_bytes) < orig_size:
                doc.update_stream(xref, jpeg_bytes, compress=False)
                doc.xref_set_key(xref, "Filter", "/DCTDecode")
                doc.xref_set_key(xref, "DecodeParms", "null")
                doc.xref_set_key(xref, "Width", str(new_w))
                doc.xref_set_key(xref, "Height", str(new_h))
                doc.xref_set_key(xref, "ColorSpace", "/DeviceRGB")
                doc.xref_set_key(xref, "BitsPerComponent", "8")
                doc.xref_set_key(xref, "Length", str(len(jpeg_bytes)))

    fitz.TOOLS.mupdf_warnings(reset=True)  # discard MuPDF stderr noise


def export_pdf_compressed(
    src_path: str,
    dst_path: str,
    optimize_level: int = 0,
    *,
    image_dpi: int = 150,
    image_quality: int = 75,
) -> None:
    """Export a PDF with optimization.

    The caller decides *image_dpi* / *image_quality*; for the standard/high/max
    presets the export dialog seeds them, and for the custom level the user sets
    them directly. This function only branches on *optimize_level*:

    Args:
        src_path: Source PDF file path.
        dst_path: Destination PDF file path.
        optimize_level: Optimization level.
            0 = no optimization (plain save),
            1 = cleanup only (garbage collection + deflate),
            >= 2 = image recompression using *image_dpi* / *image_quality*.
        image_dpi: Target max DPI for image recompression (levels >= 2).
        image_quality: JPEG quality (1-100) for image recompression (levels >= 2).
    """
    doc = fitz.open(src_path)
    try:
        if optimize_level >= 2:
            _downsample_images(doc, max_dpi=image_dpi, jpeg_quality=image_quality)

        save_opts: dict = {}
        if optimize_level >= 1:
            save_opts["garbage"] = 4
            save_opts["deflate"] = True
            save_opts["deflate_images"] = optimize_level < 2
            save_opts["deflate_fonts"] = True
            save_opts["clean"] = True
        doc.save(dst_path, **save_opts)
    finally:
        doc.close()


def rasterize_pdf(
    src_path: str,
    output_path: str,
    dpi: int = 150,
    *,
    image_format: str = "png",
    jpeg_quality: int = 75,
) -> None:
    """Create a rasterized (image-only) copy of a PDF.

    Each page is rendered to an image at the given DPI and embedded into
    a new page that keeps the original page dimensions.  The result looks
    identical but contains no selectable text or vector data.

    Args:
        src_path: Source PDF file path.
        output_path: Destination PDF path.
        dpi: Resolution for rasterization.
        image_format: "png" (lossless, sharp text) or "jpeg" (lossy,
            much smaller for photo/scan-heavy pages).
        jpeg_quality: JPEG quality (1-100); ignored for PNG.
    """
    src_doc = fitz.open(src_path)
    out_doc = fitz.open()
    try:
        for page_num in range(len(src_doc)):
            page = src_doc[page_num]
            img_data, _ = _render_page_to_image_bytes(
                page, dpi, image_format=image_format, jpeg_quality=jpeg_quality
            )
            # Keep the original page size; embed the image without re-encoding.
            out_page = out_doc.new_page(
                width=page.rect.width, height=page.rect.height
            )
            out_page.insert_image(out_page.rect, stream=img_data)
        # Pages are copied 1:1 in the same order, so the source bookmarks
        # (outline/TOC) stay valid; carry them over to the rasterized output.
        toc = src_doc.get_toc(simple=True)
        if toc:
            out_doc.set_toc(toc)
        out_doc.save(output_path, garbage=1, deflate=True)
    finally:
        src_doc.close()
        out_doc.close()


