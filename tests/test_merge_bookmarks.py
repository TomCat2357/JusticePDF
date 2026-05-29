from __future__ import annotations

import fitz

from src.utils.pdf_utils import (
    get_pdf_toc,
    merge_paths_to_pdf,
    merge_pdfs,
    merge_pdfs_in_place,
)


def _make_pdf(path, *, pages: int, toc: list | None = None) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=300, height=400)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def test_merge_in_place_append_adds_file_bookmarks(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)

    merge_pdfs_in_place(str(a), [str(b)], add_file_bookmarks=True)

    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "a.pdf", 1),
        (1, "b.pdf", 3),
    ]


def test_merge_in_place_insert_at_zero(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)  # destination
    _make_pdf(b, pages=3)  # inserted first

    merge_pdfs_in_place(str(a), [str(b)], insert_at=0, add_file_bookmarks=True)

    # Final order: B (pages 1-3), then A (pages 4-5)
    toc = get_pdf_toc(str(a))
    assert [(e.title, e.page) for e in toc] == [
        ("b.pdf", 1),
        ("a.pdf", 4),
    ]


def test_merge_keeps_source_bookmarks_as_children(tmp_path):
    # 挿入したファイル(b)が元々持つしおりは、ファイル名しおりの子(level 2)になる。
    # 結合先(a)の既存しおりは再ネストせずそのまま残る。
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2, toc=[[1, "A-Chapter", 1]])
    _make_pdf(b, pages=3, toc=[[1, "B-Chapter", 2]])

    merge_pdfs_in_place(str(a), [str(b)], add_file_bookmarks=True)

    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "A-Chapter", 1),  # 結合先の既存しおりはそのまま(再ネストしない)
        (1, "b.pdf", 3),
        (2, "B-Chapter", 4),  # B-Chapter was page 2 in b -> 2 + 2 (offset) = 4
    ]


def test_repeated_merges_stay_flat(tmp_path):
    # ユーザー報告のケース: 1ファイルずつ繰り返し重ねても階層が深くならない。
    a = tmp_path / "a.pdf"  # 結合先(蓄積していく)
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    d = tmp_path / "d.pdf"
    _make_pdf(a, pages=1)
    _make_pdf(b, pages=1)
    _make_pdf(c, pages=1)
    _make_pdf(d, pages=1)

    # 末尾追加で1ファイルずつ重ねる
    merge_pdfs_in_place(str(a), [str(b)], add_file_bookmarks=True)
    merge_pdfs_in_place(str(a), [str(c)], add_file_bookmarks=True)
    merge_pdfs_in_place(str(a), [str(d)], add_file_bookmarks=True)

    toc = get_pdf_toc(str(a))
    # すべて level 1 のフラットなファイル名しおり(重ねた順番でネストしない)
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "a.pdf", 1),
        (1, "b.pdf", 2),
        (1, "c.pdf", 3),
        (1, "d.pdf", 4),
    ]


def test_repeated_insert_at_zero_stay_flat(tmp_path):
    # 先頭挿入(Ctrl+ドラッグの重ね)を繰り返してもフラットを維持する。
    a = tmp_path / "a.pdf"  # 結合先(常に末尾に残る)
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    _make_pdf(a, pages=1)
    _make_pdf(b, pages=1)
    _make_pdf(c, pages=1)

    merge_pdfs_in_place(str(a), [str(b)], insert_at=0, add_file_bookmarks=True)
    merge_pdfs_in_place(str(a), [str(c)], insert_at=0, add_file_bookmarks=True)

    toc = get_pdf_toc(str(a))
    # ページ順: c(1), b(2), a(3)。すべて level 1。
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "c.pdf", 1),
        (1, "b.pdf", 2),
        (1, "a.pdf", 3),
    ]


def test_merge_pdfs_fresh_adds_bookmarks(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    out = tmp_path / "out.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)
    _make_pdf(c, pages=1)

    merge_pdfs(str(out), [str(a), str(b), str(c)], add_file_bookmarks=True)

    toc = get_pdf_toc(str(out))
    assert [(e.title, e.page) for e in toc] == [
        ("a.pdf", 1),
        ("b.pdf", 3),
        ("c.pdf", 6),
    ]


def test_merge_without_flag_adds_no_bookmarks(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=3)

    merge_pdfs_in_place(str(a), [str(b)])  # default: no bookmarks

    assert get_pdf_toc(str(a)) == []


def test_three_way_merge_in_place(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    _make_pdf(a, pages=2)
    _make_pdf(b, pages=1)
    _make_pdf(c, pages=4)

    merge_pdfs_in_place(str(a), [str(b), str(c)], add_file_bookmarks=True)

    toc = get_pdf_toc(str(a))
    assert [(e.title, e.page) for e in toc] == [
        ("a.pdf", 1),
        ("b.pdf", 3),
        ("c.pdf", 4),
    ]


# ---------------------------------------------------------------------------
# merge_paths_to_pdf: 選択ファイル/フォルダ → フォルダ構成を反映した階層しおり
# ---------------------------------------------------------------------------


def test_merge_paths_builds_folder_hierarchy(tmp_path):
    # FolderA/ { a1.pdf, Sub/ { s1.pdf, s2.pdf } } と単独 loose.pdf を結合
    folder = tmp_path / "FolderA"
    sub = folder / "Sub"
    sub.mkdir(parents=True)
    _make_pdf(folder / "a1.pdf", pages=1)
    _make_pdf(sub / "s1.pdf", pages=1)
    _make_pdf(sub / "s2.pdf", pages=1)
    loose = tmp_path / "loose.pdf"
    _make_pdf(loose, pages=1)
    out = tmp_path / "merged.pdf"

    total = merge_paths_to_pdf(str(out), [str(folder), str(loose)])

    assert total == 4
    toc = get_pdf_toc(str(out))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "FolderA", 1),
        (2, "a1.pdf", 1),      # 名前順: a1.pdf < Sub
        (2, "Sub", 2),
        (3, "s1.pdf", 2),
        (3, "s2.pdf", 3),
        (1, "loose.pdf", 4),
    ]


def test_merge_paths_top_level_order_is_preserved(tmp_path):
    # トップレベルは呼び出し側が渡した順(フォルダ→ファイル)で並ぶ
    folder = tmp_path / "Docs"
    folder.mkdir()
    _make_pdf(folder / "inner.pdf", pages=1)
    x = tmp_path / "x.pdf"
    _make_pdf(x, pages=2)
    out = tmp_path / "merged.pdf"

    merge_paths_to_pdf(str(out), [str(folder), str(x)])

    toc = get_pdf_toc(str(out))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "Docs", 1),
        (2, "inner.pdf", 1),
        (1, "x.pdf", 2),
    ]


def test_merge_paths_skips_empty_folders(tmp_path):
    # PDF を含まないフォルダは見出しごと省略される
    empty = tmp_path / "Empty"
    empty.mkdir()
    (empty / "note.txt").write_text("not a pdf", encoding="utf-8")
    x = tmp_path / "x.pdf"
    y = tmp_path / "y.pdf"
    _make_pdf(x, pages=1)
    _make_pdf(y, pages=1)
    out = tmp_path / "merged.pdf"

    total = merge_paths_to_pdf(str(out), [str(empty), str(x), str(y)])

    assert total == 2
    toc = get_pdf_toc(str(out))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "x.pdf", 1),
        (1, "y.pdf", 2),
    ]


def test_merge_paths_nothing_mergeable_returns_zero(tmp_path):
    # 結合できる PDF が無ければ 0 を返し、出力ファイルは作られない
    empty = tmp_path / "Empty"
    empty.mkdir()
    out = tmp_path / "merged.pdf"

    total = merge_paths_to_pdf(str(out), [str(empty)])

    assert total == 0
    assert not out.exists()
