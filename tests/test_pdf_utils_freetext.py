from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    FreeTextAnnotData,
    PdfWritePermissionError,
    create_freetext_annot,
    delete_freetext_annot,
    get_page_pixmap,
    list_freetext_annots,
    rotate_pages,
    replace_freetext_annot,
)
from src.utils import pdf_utils


pytestmark = pytest.mark.usefixtures("qapp")


def _make_pdf(path, *, width: int = 320, height: int = 420) -> None:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    doc.save(path)
    doc.close()


def _assert_color_close(actual, expected) -> None:
    assert actual is not None
    for left, right in zip(actual, expected):
        assert abs(left - right) < 0.02


def _page_rgb(path, x: int, y: int, *, annots: bool = True) -> tuple[int, int, int]:
    with fitz.open(str(path)) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), annots=annots)
    offset = y * pix.stride + x * pix.n
    return tuple(pix.samples[offset:offset + 3])


def test_freetext_create_replace_delete_roundtrip(tmp_path):
    pdf_path = tmp_path / "roundtrip.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(24, 36, 164, 106),
            content="first",
            fontsize=15,
            text_color=(0.1, 0.2, 0.3),
            fill_color=(1.0, 1.0, 0.0),
            border_color=(1.0, 0.0, 0.0),
            border_width=2,
            opacity=0.55,
        ),
    )

    listed = list_freetext_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].content == "first"
    assert listed[0].border_width == 2
    _assert_color_close(listed[0].text_color, (0.1, 0.2, 0.3))
    _assert_color_close(listed[0].fill_color, (1.0, 1.0, 0.0))
    _assert_color_close(listed[0].border_color, (1.0, 0.0, 0.0))

    updated = replace_freetext_annot(
        str(pdf_path),
        0,
        created.xref,
        FreeTextAnnotData(
            page_num=0,
            xref=created.xref,
            rect=(40, 50, 220, 150),
            content="second",
            fontsize=18,
            text_color=(0.0, 0.0, 1.0),
            fill_color=(0.9, 1.0, 0.9),
            border_color=(0.0, 0.0, 0.0),
            border_width=3,
            opacity=0.7,
        ),
    )

    listed = list_freetext_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].xref == updated.xref
    assert listed[0].content == "second"
    assert listed[0].rect == (40.0, 50.0, 220.0, 150.0)
    assert listed[0].border_width == 3
    _assert_color_close(listed[0].fill_color, (0.9, 1.0, 0.9))

    assert delete_freetext_annot(str(pdf_path), 0, updated.xref) is True
    assert list_freetext_annots(str(pdf_path), 0) == []


def test_list_freetext_annots_reads_existing_richtext(tmp_path):
    pdf_path = tmp_path / "existing-richtext.pdf"
    _make_pdf(pdf_path)

    doc = fitz.open(str(pdf_path))
    page = doc[0]
    page.add_freetext_annot(
        fitz.Rect(40, 50, 180, 120),
        "rich text",
        richtext=True,
        opacity=0.8,
        style=(
            "font-size:16pt; font-family:Helvetica; color:#112233; "
            "border:3px solid #445566; background-color:#ffeeaa;"
        ),
    )
    doc.saveIncr()
    doc.close()

    annots = list_freetext_annots(str(pdf_path), 0)
    assert len(annots) == 1
    annot = annots[0]
    assert annot.content == "rich text"
    assert annot.rect == (40.0, 50.0, 180.0, 120.0)
    assert annot.fontsize == 16.0
    assert annot.border_width == 3.0
    assert abs(annot.opacity - 0.8) < 0.02
    _assert_color_close(annot.text_color, (0x11 / 255.0, 0x22 / 255.0, 0x33 / 255.0))
    _assert_color_close(annot.fill_color, (1.0, 0xEE / 255.0, 0xAA / 255.0))
    _assert_color_close(annot.border_color, (0x44 / 255.0, 0x55 / 255.0, 0x66 / 255.0))


def test_freetext_create_and_replace_keep_richtext_appearance_data(tmp_path):
    pdf_path = tmp_path / "richtext-appearance.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(30, 40, 180, 120),
            content="colorful",
            fontsize=16,
            text_color=(1.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.6),
            border_color=(0.0, 0.0, 1.0),
            border_width=2,
            opacity=1.0,
        ),
    )

    with fitz.open(str(pdf_path)) as doc:
        richtext_kind, richtext_value = doc.xref_get_key(created.xref, "RC")
        fill_kind, fill_value = doc.xref_get_key(created.xref, "C")
        subject_kind, subject_value = doc.xref_get_key(created.xref, "Subj")
        style_kind, style_value = doc.xref_get_key(created.xref, "DS")
    assert richtext_kind == "string"
    assert "colorful" in richtext_value
    assert fill_kind == "array"
    assert fill_value != "null"
    assert subject_kind == "string"
    assert "JusticePDF-FreeText:" in subject_value
    assert style_kind == "string"
    assert "margin:0" in style_value
    assert "padding:0" in style_value
    assert "border:0px solid transparent" in style_value

    replaced = replace_freetext_annot(
        str(pdf_path),
        0,
        created.xref,
        FreeTextAnnotData(
            page_num=0,
            xref=created.xref,
            rect=(50, 60, 210, 150),
            content="updated",
            fontsize=18,
            text_color=(0.0, 0.4, 0.0),
            fill_color=(0.9, 1.0, 0.9),
            border_color=(0.2, 0.2, 0.2),
            border_width=3,
            opacity=0.9,
        ),
    )

    with fitz.open(str(pdf_path)) as doc:
        richtext_kind, richtext_value = doc.xref_get_key(replaced.xref, "RC")
        fill_kind, fill_value = doc.xref_get_key(replaced.xref, "C")
        style_kind, style_value = doc.xref_get_key(replaced.xref, "DS")
    assert richtext_kind == "string"
    assert "updated" in richtext_value
    assert fill_kind == "array"
    assert fill_value != "null"
    assert style_kind == "string"
    assert "border:0px solid transparent" in style_value


def test_freetext_create_with_empty_content_generates_visible_border_appearance(tmp_path):
    pdf_path = tmp_path / "empty-border-appearance.pdf"
    _make_pdf(pdf_path, width=300, height=300)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(50, 50, 200, 150),
            content="",
            fontsize=14,
            text_color=(1.0, 0.0, 0.0),
            fill_color=None,
            border_color=(1.0, 0.0, 0.0),
            border_width=2,
            opacity=1.0,
        ),
    )

    with fitz.open(str(pdf_path)) as doc:
        da_kind, da_value = doc.xref_get_key(created.xref, "DA")
        ap_kind, ap_value = doc.xref_get_key(created.xref, "AP")
    assert da_kind == "string"
    assert "1 0 0 rg" in da_value
    assert ap_kind == "dict"
    assert ap_value != "null"
    assert _page_rgb(pdf_path, 50, 50) == (255, 0, 0)
    assert _page_rgb(pdf_path, 125, 100) == (255, 255, 255)


def test_freetext_replace_updates_empty_border_appearance(tmp_path):
    pdf_path = tmp_path / "replace-empty-border-appearance.pdf"
    _make_pdf(pdf_path, width=300, height=300)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 40, 180, 130),
            content="",
            fontsize=12,
            text_color=(0.0, 0.0, 0.0),
            fill_color=None,
            border_color=(0.0, 0.0, 0.0),
            border_width=1,
            opacity=1.0,
        ),
    )

    replaced = replace_freetext_annot(
        str(pdf_path),
        0,
        created.xref,
        FreeTextAnnotData(
            page_num=0,
            xref=created.xref,
            rect=(60, 60, 220, 170),
            content="",
            fontsize=16,
            text_color=(1.0, 0.0, 0.0),
            fill_color=None,
            border_color=(1.0, 0.0, 0.0),
            border_width=3,
            opacity=1.0,
        ),
    )

    with fitz.open(str(pdf_path)) as doc:
        da_kind, da_value = doc.xref_get_key(replaced.xref, "DA")
    assert da_kind == "string"
    assert "1 0 0 rg" in da_value
    assert _page_rgb(pdf_path, 60, 60) == (255, 0, 0)
    assert _page_rgb(pdf_path, 140, 110) == (255, 255, 255)


def test_freetext_create_preserves_transparent_fill_and_border_metadata(tmp_path):
    pdf_path = tmp_path / "transparent-colors.pdf"
    _make_pdf(pdf_path, width=300, height=300)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 40, 220, 170),
            content="transparent",
            fontsize=14,
            text_color=(1.0, 0.0, 0.0),
            fill_color=None,
            border_color=None,
            border_width=3,
            opacity=0.6,
        ),
    )

    listed = list_freetext_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].fill_color is None
    assert listed[0].border_color is None
    assert listed[0].border_width == 3.0
    assert abs(listed[0].opacity - 0.6) < 0.02

    with fitz.open(str(pdf_path)) as doc:
        style_kind, style_value = doc.xref_get_key(created.xref, "DS")
        border_kind, border_value = doc.xref_get_key(created.xref, "BS")
    assert style_kind == "string"
    assert "background-color:transparent" in style_value
    assert border_kind == "dict"
    assert "/W 0" in border_value
    assert _page_rgb(pdf_path, 60, 60) == (255, 255, 255)


def test_get_page_pixmap_can_exclude_annotation_appearance(tmp_path):
    pdf_path = tmp_path / "pixmap-annots-flag.pdf"
    _make_pdf(pdf_path)

    create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(40, 50, 240, 150),
            content="visible",
            fontsize=16,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=(0.0, 0.0, 0.0),
            border_width=1,
            opacity=1.0,
        ),
    )

    pix_with_annots = get_page_pixmap(str(pdf_path), 0, 1.0, annots=True)
    pix_without_annots = get_page_pixmap(str(pdf_path), 0, 1.0, annots=False)

    with_annots = pix_with_annots.toImage().pixelColor(200, 130)
    without_annots = pix_without_annots.toImage().pixelColor(200, 130)

    assert with_annots.red() > 240
    assert with_annots.green() > 240
    assert with_annots.blue() < 80
    assert without_annots.red() > 240
    assert without_annots.green() > 240
    assert without_annots.blue() > 240


def test_replace_freetext_annot_raises_permission_error_when_destination_is_locked(tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked-replace.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(24, 36, 164, 106),
            content="first",
            fontsize=15,
            text_color=(0.1, 0.2, 0.3),
            fill_color=(1.0, 1.0, 0.0),
            border_color=(1.0, 0.0, 0.0),
            border_width=2,
            opacity=0.55,
        ),
    )

    monkeypatch.setattr(
        fitz.Document,
        "saveIncr",
        lambda self: (_ for _ in ()).throw(RuntimeError("permission denied")),
    )
    monkeypatch.setattr(
        pdf_utils.shutil,
        "move",
        lambda src, dst: (_ for _ in ()).throw(PermissionError(13, "Permission denied", dst)),
    )

    with pytest.raises(PdfWritePermissionError):
        replace_freetext_annot(
            str(pdf_path),
            0,
            created.xref,
            FreeTextAnnotData(
                page_num=0,
                xref=created.xref,
                rect=(40, 50, 220, 150),
                content="second",
                fontsize=18,
                text_color=(0.0, 0.0, 1.0),
                fill_color=(0.9, 1.0, 0.9),
                border_color=(0.0, 0.0, 0.0),
                border_width=3,
                opacity=0.7,
            ),
        )


def test_rotate_pages_raises_permission_error_when_destination_is_locked(tmp_path, monkeypatch):
    pdf_path = tmp_path / "locked-rotate.pdf"
    _make_pdf(pdf_path)

    monkeypatch.setattr(
        fitz.Document,
        "saveIncr",
        lambda self: (_ for _ in ()).throw(RuntimeError("permission denied")),
    )
    monkeypatch.setattr(
        pdf_utils.shutil,
        "move",
        lambda src, dst: (_ for _ in ()).throw(PermissionError(13, "Permission denied", dst)),
    )

    with pytest.raises(PdfWritePermissionError):
        rotate_pages(str(pdf_path), [0], 90)
