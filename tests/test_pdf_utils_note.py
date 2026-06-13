from __future__ import annotations

import fitz
import pytest

from src.utils.pdf_utils import (
    NoteAnnotData,
    create_note_annot,
    delete_note_annot,
    list_note_annots,
    replace_note_annot,
)
from tests.helpers import make_pdf


pytestmark = pytest.mark.usefixtures("qapp")


def _assert_color_close(actual, expected) -> None:
    assert actual is not None
    for left, right in zip(actual, expected):
        assert abs(left - right) < 0.02


def test_note_create_list_delete_roundtrip(tmp_path):
    pdf_path = tmp_path / "note.pdf"
    make_pdf(pdf_path)

    created = create_note_annot(
        str(pdf_path),
        NoteAnnotData(
            page_num=0,
            xref=0,
            point=(120.0, 90.0),
            content="確認が必要",
            color=(1.0, 0.85, 0.0),
            opacity=0.9,
        ),
    )
    assert created.xref > 0

    listed = list_note_annots(str(pdf_path), 0)
    assert len(listed) == 1
    note = listed[0]
    assert note.content == "確認が必要"
    assert note.point[0] == pytest.approx(120.0, abs=1.0)
    assert note.point[1] == pytest.approx(90.0, abs=1.0)
    _assert_color_close(note.color, (1.0, 0.85, 0.0))
    assert abs(note.opacity - 0.9) < 0.05

    assert delete_note_annot(str(pdf_path), 0, created.xref) is True
    assert list_note_annots(str(pdf_path), 0) == []


def test_note_replace_updates_content_and_color(tmp_path):
    pdf_path = tmp_path / "note-replace.pdf"
    make_pdf(pdf_path)

    created = create_note_annot(
        str(pdf_path),
        NoteAnnotData(
            page_num=0, xref=0, point=(50.0, 50.0),
            content="first", color=(1.0, 1.0, 0.0),
        ),
    )

    replaced = replace_note_annot(
        str(pdf_path), 0, created.xref,
        NoteAnnotData(
            page_num=0, xref=created.xref, point=(50.0, 50.0),
            content="second", color=(0.2, 0.6, 1.0),
        ),
    )

    listed = list_note_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].xref == replaced.xref
    assert listed[0].content == "second"
    _assert_color_close(listed[0].color, (0.2, 0.6, 1.0))


def test_note_only_lists_justicepdf_annots(tmp_path):
    pdf_path = tmp_path / "note-foreign.pdf"
    make_pdf(pdf_path)

    doc = fitz.open(str(pdf_path))
    doc[0].add_text_annot(fitz.Point(30, 30), "foreign", icon="Note")
    doc.saveIncr()
    doc.close()

    assert list_note_annots(str(pdf_path), 0) == []


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_note_roundtrips_point_on_rotated_page(tmp_path, rotation):
    pdf_path = tmp_path / f"note-rot-{rotation}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=320, height=420)
    page.set_rotation(rotation)
    doc.save(str(pdf_path))
    doc.close()

    create_note_annot(
        str(pdf_path),
        NoteAnnotData(
            page_num=0, xref=0, point=(80.0, 100.0),
            content="rotated", color=(1.0, 1.0, 0.0),
        ),
    )

    listed = list_note_annots(str(pdf_path), 0)
    assert len(listed) == 1
    assert listed[0].point[0] == pytest.approx(80.0, abs=1.5)
    assert listed[0].point[1] == pytest.approx(100.0, abs=1.5)


def test_list_note_annots_whole_document(tmp_path):
    pdf_path = tmp_path / "note-multi.pdf"
    make_pdf(pdf_path, pages=3)

    create_note_annot(str(pdf_path), NoteAnnotData(page_num=0, xref=0, point=(40, 40), content="p0", color=(1, 1, 0)))
    create_note_annot(str(pdf_path), NoteAnnotData(page_num=2, xref=0, point=(40, 40), content="p2", color=(1, 1, 0)))

    everything = list_note_annots(str(pdf_path))
    assert {n.page_num for n in everything} == {0, 2}
    assert list_note_annots(str(pdf_path), 1) == []
