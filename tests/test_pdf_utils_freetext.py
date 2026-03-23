from __future__ import annotations

import fitz

from src.utils.pdf_utils import (
    FreeTextAnnotData,
    create_freetext_annot,
    delete_freetext_annot,
    get_page_pixmap,
    list_freetext_annots,
    replace_freetext_annot,
)


def _make_pdf(path, *, width: int = 320, height: int = 420) -> None:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    doc.save(path)
    doc.close()


def _assert_color_close(actual, expected) -> None:
    assert actual is not None
    for left, right in zip(actual, expected):
        assert abs(left - right) < 0.02


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
