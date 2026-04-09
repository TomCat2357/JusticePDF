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


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_freetext_annot_renders_at_correct_position_on_rotated_page(tmp_path, rotation):
    """Annotations created on rotated pages must render at the visual position."""
    pdf_path = tmp_path / f"rotated-{rotation}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=320, height=420)
    page.set_rotation(rotation)
    doc.save(str(pdf_path))
    doc.close()

    # Create annotation at visual top-left area (50,50)-(200,100)
    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0,
            xref=0,
            rect=(50, 50, 200, 100),
            content="rotated",
            fontsize=12,
            text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None,
            border_width=0,
            opacity=1.0,
        ),
    )

    # Roundtrip: read back should return the same visual coordinates
    listed = list_freetext_annots(str(pdf_path), 0)
    assert len(listed) == 1
    r = listed[0].rect
    assert abs(r[0] - 50) < 1 and abs(r[1] - 50) < 1
    assert abs(r[2] - 200) < 1 and abs(r[3] - 100) < 1

    # Render with annotations and verify yellow pixel at visual (100, 75)
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), annots=True)
    offset = 75 * pix.stride + 100 * pix.n
    r_val, g_val, b_val = pix.samples[offset], pix.samples[offset + 1], pix.samples[offset + 2]
    assert r_val > 200 and g_val > 200 and b_val < 80, (
        f"Expected yellow at (100,75) but got ({r_val},{g_val},{b_val})"
    )


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_freetext_annot_has_correct_rotate_key_on_rotated_page(tmp_path, rotation):
    """Annotation /Rotate must match page rotation so text appears upright."""
    pdf_path = tmp_path / f"rotate-key-{rotation}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=320, height=420)
    page.set_rotation(rotation)
    doc.save(str(pdf_path))
    doc.close()

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0,
            rect=(50, 50, 200, 100), content="test",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
        ),
    )

    with fitz.open(str(pdf_path)) as doc:
        _, rotate_val = doc.xref_get_key(created.xref, "Rotate")
        assert int(rotate_val) == rotation


def test_freetext_text_rotation_after_page_rotation(tmp_path):
    """text_rotation must reflect delta between current and creation rotation."""
    pdf_path = tmp_path / "text-rot.pdf"
    _make_pdf(pdf_path)

    create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0,
            rect=(50, 50, 200, 100), content="hello",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
        ),
    )

    # Before rotation: text_rotation should be 0
    listed = list_freetext_annots(str(pdf_path), 0)
    assert listed[0].text_rotation == 0

    # Rotate page to 90
    rotate_pages(str(pdf_path), [0], 90)
    listed = list_freetext_annots(str(pdf_path), 0)
    assert listed[0].text_rotation == 90


def test_freetext_edit_resets_rotation_on_rotated_page(tmp_path):
    """Replacing with subject='' resets page_rotation to current page rotation."""
    pdf_path = tmp_path / "edit-reset.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0,
            rect=(50, 50, 200, 100), content="original",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
        ),
    )

    # Rotate page to 90
    rotate_pages(str(pdf_path), [0], 90)
    listed = list_freetext_annots(str(pdf_path), 0)
    assert listed[0].text_rotation == 90

    # Replace with subject="" (simulates edit)
    edited = replace_freetext_annot(
        str(pdf_path), 0, listed[0].xref,
        FreeTextAnnotData(
            page_num=0, xref=listed[0].xref,
            rect=listed[0].rect, content="edited",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
            subject="",
        ),
    )

    # After edit: text_rotation should be 0 (reset to upright)
    listed2 = list_freetext_annots(str(pdf_path), 0)
    assert listed2[0].text_rotation == 0
    assert listed2[0].content == "edited"

    # /Rotate in PDF should match current page rotation (90)
    with fitz.open(str(pdf_path)) as doc:
        _, rotate_val = doc.xref_get_key(listed2[0].xref, "Rotate")
        assert int(rotate_val) == 90


def test_freetext_undo_restores_original_rotation(tmp_path):
    """Restoring old annotation data (with subject metadata) preserves original rotation."""
    pdf_path = tmp_path / "undo-rot.pdf"
    _make_pdf(pdf_path)

    created = create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0,
            rect=(50, 50, 200, 100), content="original",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
        ),
    )

    # Rotate page to 90
    rotate_pages(str(pdf_path), [0], 90)

    # Read the annotation (has text_rotation=90, subject with page_rotation=0)
    old_data = list_freetext_annots(str(pdf_path), 0)[0]
    assert old_data.text_rotation == 90

    # Edit (subject="") to reset rotation
    edited = replace_freetext_annot(
        str(pdf_path), 0, old_data.xref,
        FreeTextAnnotData(
            page_num=0, xref=old_data.xref,
            rect=old_data.rect, content="edited",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
            subject="",
        ),
    )
    assert list_freetext_annots(str(pdf_path), 0)[0].text_rotation == 0

    # Undo: restore old_data (which has subject with page_rotation=0)
    restored = replace_freetext_annot(
        str(pdf_path), 0, edited.xref, old_data,
    )

    # text_rotation should be back to 90
    listed = list_freetext_annots(str(pdf_path), 0)
    assert listed[0].text_rotation == 90
    assert listed[0].content == "original"

    # /Rotate in PDF should be 0 (original creation rotation)
    with fitz.open(str(pdf_path)) as doc:
        _, rotate_val = doc.xref_get_key(listed[0].xref, "Rotate")
        assert int(rotate_val) == 0


def test_freetext_move_preserves_rotation(tmp_path):
    """Move (with subject preserved) should keep original rotation."""
    pdf_path = tmp_path / "move-rot.pdf"
    _make_pdf(pdf_path)

    create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0,
            rect=(50, 50, 200, 100), content="moveme",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
        ),
    )

    rotate_pages(str(pdf_path), [0], 90)
    old_data = list_freetext_annots(str(pdf_path), 0)[0]
    assert old_data.text_rotation == 90

    # Move: replace with old subject preserved (simulates move)
    moved = replace_freetext_annot(
        str(pdf_path), 0, old_data.xref,
        FreeTextAnnotData(
            page_num=0, xref=old_data.xref,
            rect=(60, 60, 210, 110), content="moveme",
            fontsize=12, text_color=(0.0, 0.0, 0.0),
            fill_color=(1.0, 1.0, 0.0),
            border_color=None, border_width=0, opacity=1.0,
            subject=old_data.subject,
        ),
    )

    listed = list_freetext_annots(str(pdf_path), 0)
    assert listed[0].text_rotation == 90


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
