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
    hide_xrefs: "dict[int, list[int]] | None" = None,
    black_fill_regions: "dict[int, list[tuple[float, float, float, float]]] | None" = None,
    black_fill_ellipses: "dict[int, list[tuple[float, float, float, float]]] | None" = None,
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
        hide_xrefs: Optional ``{page_num: [xref, ...]}`` of annotations to hide
            before rendering (same convention as
            ``rendering.render_page_thumbnails_batch``). Used by the "個人情報
            検出" drawer's blackout export to omit the PII highlight
            annotations themselves so only the solid black fill remains.
        black_fill_regions: Optional ``{page_num: [(x0, y0, x1, y1), ...]}``
            of rectangles in the *display* coordinate system (the same one
            used throughout ``pdf_utils`` for quads, e.g.
            ``TextMarkupAnnotData.quads`` / ``get_page_words`` / ``page.rect``)
            to paint solid black (drawn directly into the page content, not
            as an annotation) before rasterizing, so the underlying text is
            genuinely gone from the exported image. Used for "黒塗りして
            画像のみエクスポート". Unlike annotation quads (which PyMuPDF
            stores in the page's unrotated internal space and therefore need
            ``derotation_matrix``, see ``annotations._add_markup_annot_to_page``),
            ``Page.draw_rect`` already operates in the same rotated/display
            space as ``page.rect`` -- verified empirically with
            ``page.set_rotation(90)`` -- so no extra transform is applied here.
        black_fill_ellipses: Same convention as ``black_fill_regions`` but each
            rectangle is filled as an ellipse inscribed in it (``Page.draw_oval``),
            for "塗りつぶし用の丸" mask shapes so the rasterized result keeps the
            shape's actual outline instead of a bounding-box rectangle.
    """
    src_doc = fitz.open(src_path)
    out_doc = fitz.open()
    try:
        for page_num in range(len(src_doc)):
            page = src_doc[page_num]
            hide_set = set((hide_xrefs or {}).get(page_num, ()))
            if hide_set:
                for annot in page.annots() or []:
                    if annot.xref in hide_set:
                        annot.set_flags(annot.flags | fitz.PDF_ANNOT_IS_HIDDEN)
            for rect in (black_fill_regions or {}).get(page_num, ()):
                page.draw_rect(
                    fitz.Rect(*rect), color=(0, 0, 0), fill=(0, 0, 0), width=0
                )
            for rect in (black_fill_ellipses or {}).get(page_num, ()):
                page.draw_oval(
                    fitz.Rect(*rect), color=(0, 0, 0), fill=(0, 0, 0), width=0
                )
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


def redact_pdf_remove_text(
    src_path: str,
    output_path: str,
    *,
    remove_xrefs: "dict[int, list[int]] | None" = None,
    redact_rects: "dict[int, list[tuple[float, float, float, float]]] | None" = None,
    redact_ellipses: "dict[int, list[tuple[float, float, float, float]]] | None" = None,
) -> None:
    """PresidioPDF ``run_mask`` 相当の「文字を本当に削除」するエクスポート。

    ``rasterize_pdf`` (画像のみエクスポート)とは異なり、出力は通常のテキスト
    PDFのまま保たれる。塗りつぶし対象の下にある文字そのものを
    ``page.add_redact_annot`` + ``page.apply_redactions`` で削除し、
    (画像がある場合は既定でその領域のピクセルも黒塗りする)、その位置に
    実際に見える黒塗りを残す。

    Args:
        src_path: 元のPDF(このパスは変更しない。常に別ファイルへ保存する)。
        output_path: 保存先PDFパス。
        remove_xrefs: ``{page_num: [xref, ...]}``。塗りつぶし候補のマーカー
            注釈・塗りつぶし用図形注釈そのもの(候補/図形の見た目)を出力から
            取り除くための xref 一覧。通常の注釈は含めないこと。
        redact_rects: ``{page_num: [(x0, y0, x1, y1), ...]}`` (表示座標系)。
            塗りつぶし候補の各quad、および「塗りつぶし用の四角」の矩形。
            ``add_redact_annot(rect, fill=(0, 0, 0))`` で文字を削除しつつ、
            そのまま矩形の黒塗りが残る。
        redact_ellipses: ``{page_num: [(x0, y0, x1, y1), ...]}`` (表示座標系、
            楕円の外接矩形)。「塗りつぶし用の丸」用。楕円に内接する文字だけを
            (``src.pii.pdf_text_map.chars_under_ellipse`` で判定し)個別に
            redactした上で、見た目を丸に揃えるため楕円を上から黒く描画する。
    """
    from src.pii.pdf_text_map import chars_under_ellipse
    from src.utils.pdf_utils.rendering import get_page_chars

    doc = fitz.open(src_path)
    try:
        page_indices = sorted(
            set((remove_xrefs or {}).keys())
            | set((redact_rects or {}).keys())
            | set((redact_ellipses or {}).keys())
        )
        for page_num in page_indices:
            if page_num < 0 or page_num >= len(doc):
                continue
            page = doc[page_num]

            for xref in (remove_xrefs or {}).get(page_num, ()):
                annot = page.load_annot(xref)
                if annot is not None:
                    page.delete_annot(annot)

            rects = list((redact_rects or {}).get(page_num, ()))
            ellipse_rects = list((redact_ellipses or {}).get(page_num, ()))

            def _to_internal(rect: tuple[float, float, float, float]) -> fitz.Rect:
                # add_redact_annot は他の注釈(add_highlight_annot等)と同じく
                # ページの未回転の内部座標系を扱うため derotation_matrix が必要
                # (draw_rect/draw_oval とは逆の規則。annotations._add_markup_annot_to_page
                # 参照。回転ページでの黒塗りテスト test_redact_removes_text_on_rotated_page
                # で実測して確認済み)。
                r = fitz.Rect(*rect)
                if page.rotation != 0:
                    r = r * page.derotation_matrix
                return r

            had_redaction = False
            for rect in rects:
                r = _to_internal(rect)
                if r.is_empty or r.is_infinite:
                    continue
                page.add_redact_annot(r, fill=(0, 0, 0))
                had_redaction = True

            ellipse_char_count = 0
            if ellipse_rects:
                chars = get_page_chars(src_path, page_num)
                for rect in ellipse_rects:
                    for ch in chars_under_ellipse(chars, rect):
                        bbox = ch.get("bbox")
                        if not bbox:
                            continue
                        r = _to_internal(bbox)
                        if r.is_empty or r.is_infinite:
                            continue
                        page.add_redact_annot(r, fill=(0, 0, 0))
                        had_redaction = True
                        ellipse_char_count += 1

            if had_redaction:
                try:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
                except TypeError:
                    # 古い PyMuPDF は images 引数を持たない。
                    page.apply_redactions()

            # 楕円: 文字が無い部分(写真・印影等の画像)は上のループでは文字が
            # 検出されずredactされないため、外接矩形全体に対して「画像ピクセルは
            # 黒塗り・文字は削除しない(text=PDF_REDACT_TEXT_NONE)」を追加で
            # 適用する。楕円の外だが外接矩形の内側にある文字を誤って消さない
            # ための text=NONE で、古い PyMuPDF がこの引数を持たない場合は
            # 安全側に倒してこの追加パスをスキップする(文字削除は保証済み、
            # 画像の黒塗りが外接矩形いっぱいにならないだけ)。
            if ellipse_rects and hasattr(fitz, "PDF_REDACT_TEXT_NONE"):
                try:
                    for rect in ellipse_rects:
                        r = _to_internal(rect)
                        if r.is_empty or r.is_infinite:
                            continue
                        # fill無し・cross_out無し: 見た目には何も残さず、画像ピクセルの
                        # 黒塗りだけを起こす(見た目の丸は後段でdraw_ovalが描く)。
                        page.add_redact_annot(r, cross_out=False)
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_PIXELS,
                        text=fitz.PDF_REDACT_TEXT_NONE,
                    )
                except TypeError:
                    pass

            # 楕円は外接矩形ではなく丸として見えるよう、redaction後に描き直す。
            for rect in ellipse_rects:
                r = _to_internal(rect)
                if r.is_empty or r.is_infinite:
                    continue
                shape = page.new_shape()
                shape.draw_oval(r)
                shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0)
                shape.commit()

        doc.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()


