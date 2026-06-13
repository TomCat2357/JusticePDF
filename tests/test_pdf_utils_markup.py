from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    MarkupType,
    TextMarkupAnnotData,
    create_markup_annot,
    delete_markup_annot,
    list_markup_annots,
    replace_markup_annot,
)
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def _assert_color_close(actual, expected) -> None:
    assert actual is not None
    for left, right in zip(actual, expected):
        assert abs(left - right) < 0.02


@pytest.mark.parametrize(
    "markup_type",
    [MarkupType.HIGHLIGHT, MarkupType.UNDERLINE, MarkupType.STRIKEOUT],
)
def test_markup_create_list_delete_roundtrip(tmp_path, markup_type):
    pdf_path = tmp_path / f"markup-{markup_type.value}.pdf"
    make_pdf(pdf_path)

    quads = ((40.0, 60.0, 120.0, 76.0), (40.0, 80.0, 160.0, 96.0))
    created = create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=quads,
            markup_type=markup_type,
            color=(1.0, 0.85, 0.0),
            opacity=0.5,
        ),
    )
    assert created.xref > 0

    listed = list_markup_annots(str(pdf_path), 0)
    assert len(listed) == 1
    annot = listed[0]
    assert annot.markup_type == markup_type
    assert len(annot.quads) == 2
    assert annot.quads[0] == pytest.approx(quads[0])
    assert annot.quads[1] == pytest.approx(quads[1])
    _assert_color_close(annot.color, (1.0, 0.85, 0.0))
    assert abs(annot.opacity - 0.5) < 0.02

    assert delete_markup_annot(str(pdf_path), 0, created.xref) is True
    assert list_markup_annots(str(pdf_path), 0) == []


def test_markup_replace_changes_color(tmp_path):
    pdf_path = tmp_path / "markup-replace.pdf"
    make_pdf(pdf_path)

    created = create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((30.0, 40.0, 100.0, 56.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0),
            opacity=0.4,
        ),
    )

    replaced = replace_markup_annot(
        str(pdf_path),
        0,
        created.xref,
        TextMarkupAnnotData(
            page_num=0,
            xref=created.xref,
            quads=((30.0, 40.0, 100.0, 56.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(0.2, 0.6, 1.0),
            opacity=0.7,
        ),
    )

    listed = list_markup_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].xref == replaced.xref
    _assert_color_close(listed[0].color, (0.2, 0.6, 1.0))
    assert abs(listed[0].opacity - 0.7) < 0.02


def test_markup_renders_into_pixmap(tmp_path):
    pdf_path = tmp_path / "markup-render.pdf"
    make_pdf(pdf_path, width=300, height=200)

    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((40.0, 40.0, 200.0, 80.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0),
            opacity=1.0,
        ),
    )

    with fitz.open(str(pdf_path)) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), annots=True)
    offset = 60 * pix.stride + 120 * pix.n
    r_val, g_val, b_val = pix.samples[offset], pix.samples[offset + 1], pix.samples[offset + 2]
    assert r_val > 200 and g_val > 200 and b_val < 120


def test_markup_only_lists_justicepdf_annots(tmp_path):
    pdf_path = tmp_path / "markup-foreign.pdf"
    make_pdf(pdf_path)

    # A highlight without JusticePDF subject metadata should be ignored.
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    page.add_highlight_annot(quads=[fitz.Rect(20, 20, 80, 36).quad])
    doc.saveIncr()
    doc.close()

    assert list_markup_annots(str(pdf_path), 0) == []


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_markup_roundtrips_quads_on_rotated_page(tmp_path, rotation):
    pdf_path = tmp_path / f"markup-rot-{rotation}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=320, height=420)
    page.set_rotation(rotation)
    doc.save(str(pdf_path))
    doc.close()

    quads = ((50.0, 50.0, 200.0, 70.0),)
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=quads,
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 1.0, 0.0),
            opacity=1.0,
        ),
    )

    listed = list_markup_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].quads[0] == pytest.approx(quads[0])
