"""ページ並べ替え後に、個人情報検出の結果一覧・OCR・しおりがページに追従するかのテスト。

注釈(個人情報検出のマーカー/OCRテキスト)としおりの実体はPDF側にありページと一緒に動く。
画面側のコピー(結果一覧・しおりツリー)は、並べ替え後に作り直されないと古いページ番号のままになる。
"""
from __future__ import annotations

import fitz
import pytest
from PyQt6.QtCore import Qt

from src.ocr.base import OCRResult
from src.ocr.embedder import count_ocr_annots, replace_ocr_in_file
from src.utils.pdf_utils import list_pii_markup_annots
from src.views.page_edit_annotations import CreateMode
from tests.helpers import create_page_edit_window, open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


def _make_three_page_pdf(path, *, toc=None) -> None:
    doc = fitz.open()
    for text in ("SECRET ONE", "PAGE TWO", "PAGE THREE"):
        page = doc.new_page(width=400, height=200)
        page.insert_text((40, 100), text, fontsize=18)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def _move_first_page_to_end(window, monkeypatch) -> None:
    """先頭ページを末尾へドラッグ移動したのと同じ経路(_handle_page_reorder)を通す。"""
    monkeypatch.setattr(window, "_get_drop_page_index", lambda _pos: 3)
    window._handle_page_reorder([0], None)


def _create_pii_candidate_on_first_page(window) -> None:
    """ズームビューの1ページ目で「SECRET」を手動の塗りつぶし候補にする。"""
    label = window._zoom_label
    text = "".join(ch["c"] for ch in label._chars)
    start = text.index("SECRET")
    window._activate_create_mode(CreateMode.MASK_MARKUP)
    label._selected_char_indices = list(range(start, start + len("SECRET")))
    window._on_zoom_text_selection_released()


def _result_pages(window) -> list[int]:
    tree = window._pii_panel._result_tree
    return [
        tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).page_num
        for i in range(tree.topLevelItemCount())
    ]


@pytest.mark.usefixtures("qtbot")
def test_pii_result_list_follows_page_reorder_and_undo(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "pii-follow.pdf"
    _make_three_page_pdf(pdf_path)

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _create_pii_candidate_on_first_page(window)
    assert _result_pages(window) == [0]

    _move_first_page_to_end(window, monkeypatch)

    # 注釈自体はページと一緒に動く。結果一覧の行も新しいページ番号に追従する。
    assert [a.page_num for a in list_pii_markup_annots(str(pdf_path))] == [2]
    assert _result_pages(window) == [2]

    window._on_undo()
    assert _result_pages(window) == [0]
    window._on_redo()
    assert _result_pages(window) == [2]


@pytest.mark.usefixtures("qtbot")
def test_ocr_text_follows_page_reorder(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "ocr-follow.pdf"
    _make_three_page_pdf(pdf_path)
    line = OCRResult(text="OCR TEXT", x=40, y=20, width=120, height=20, page_num=0, confidence=0.9)
    replace_ocr_in_file(str(pdf_path), [0], [line])
    assert count_ocr_annots(str(pdf_path), [0]) > 0

    window = create_page_edit_window(qtbot, pdf_path)
    _move_first_page_to_end(window, monkeypatch)

    # OCRテキストは移動したページ(現在の3ページ目)に付いたまま。元の位置には残らない。
    assert count_ocr_annots(str(pdf_path), [0]) == 0
    assert count_ocr_annots(str(pdf_path), [1]) == 0
    assert count_ocr_annots(str(pdf_path), [2]) > 0


@pytest.mark.usefixtures("qtbot")
def test_bookmark_tree_follows_page_reorder(qtbot, tmp_path, monkeypatch):
    pdf_path = tmp_path / "bookmark-follow.pdf"
    _make_three_page_pdf(pdf_path, toc=[[1, "One", 1], [1, "Two", 2], [1, "Three", 3]])

    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._bookmarks_panel.set_open(True)
    assert [(e.title, e.page) for e in window._bookmarks_panel._tree_to_entries()] == [
        ("One", 1),
        ("Two", 2),
        ("Three", 3),
    ]

    _move_first_page_to_end(window, monkeypatch)

    # しおりは移動したページに付いて行く(One は3ページ目へ)。パネルの表示も同期する。
    assert [(e.title, e.page) for e in window._bookmarks_panel._tree_to_entries()] == [
        ("One", 3),
        ("Two", 1),
        ("Three", 2),
    ]
