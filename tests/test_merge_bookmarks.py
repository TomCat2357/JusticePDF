from __future__ import annotations

from src.utils.pdf_utils import (
    get_pdf_toc,
    merge_paths_to_pdf,
    merge_pdfs,
    merge_pdfs_in_place,
)
from tests.helpers import make_pdf


# ---------------------------------------------------------------------------
# merge_pdfs_in_place: D&D で重ねたときの「フラットな単純引継ぎ」
#   各ファイルの元しおりを level はそのまま、ページだけオフセットして連結する。
#   ファイル名の親しおりは付けない(重ねても階層が深くならない)。
# ---------------------------------------------------------------------------


def test_merge_in_place_append_inherits_flat(tmp_path):
    # 末尾追加: 内部の親子関係(A1 > A1a)は保持しつつ、b の元しおりがフラットに続く。
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    make_pdf(a, pages=2, toc=[[1, "A1", 1], [2, "A1a", 1]])
    make_pdf(b, pages=3, toc=[[1, "B1", 2]])

    merge_pdfs_in_place(str(a), [str(b)])

    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "A1", 1),
        (2, "A1a", 1),
        (1, "B1", 4),  # b の page 2 + オフセット 2 = 4。ファイル名見出しは付かない。
    ]


def test_merge_in_place_insert_at_zero_inherits_flat(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    make_pdf(a, pages=2, toc=[[1, "A1", 1]])  # destination(末尾へ回る)
    make_pdf(b, pages=3, toc=[[1, "B1", 1]])  # 先頭に挿入

    merge_pdfs_in_place(str(a), [str(b)], insert_at=0)

    # ページ順: B(1-3), A(4-5)。どちらも level はそのまま、ファイル名見出しなし。
    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "B1", 1),
        (1, "A1", 4),
    ]


def test_merge_preserves_levels_no_filename_heading(tmp_path):
    # 重ねたファイルの元しおりは level を変えずに引き継がれる(子に降格しない)。
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    make_pdf(a, pages=2, toc=[[1, "A-Chapter", 1]])
    make_pdf(b, pages=3, toc=[[1, "B-Chapter", 2]])

    merge_pdfs_in_place(str(a), [str(b)])

    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "A-Chapter", 1),
        (1, "B-Chapter", 4),  # b の page 2 + オフセット 2 = 4、level 1 のまま
    ]


def test_repeated_merges_stay_flat(tmp_path):
    # 1ファイルずつ繰り返し重ねても階層は深くならず、しおりはフラットに連結される。
    a = tmp_path / "a.pdf"  # 結合先(蓄積していく)
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    d = tmp_path / "d.pdf"
    make_pdf(a, pages=1, toc=[[1, "a", 1]])
    make_pdf(b, pages=1, toc=[[1, "b", 1]])
    make_pdf(c, pages=1, toc=[[1, "c", 1]])
    make_pdf(d, pages=1, toc=[[1, "d", 1]])

    merge_pdfs_in_place(str(a), [str(b)])
    merge_pdfs_in_place(str(a), [str(c)])
    merge_pdfs_in_place(str(a), [str(d)])

    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "a", 1),
        (1, "b", 2),
        (1, "c", 3),
        (1, "d", 4),
    ]


def test_repeated_insert_at_zero_stay_flat(tmp_path):
    a = tmp_path / "a.pdf"  # 結合先(常に末尾へ回る)
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    make_pdf(a, pages=1, toc=[[1, "a", 1]])
    make_pdf(b, pages=1, toc=[[1, "b", 1]])
    make_pdf(c, pages=1, toc=[[1, "c", 1]])

    merge_pdfs_in_place(str(a), [str(b)], insert_at=0)
    merge_pdfs_in_place(str(a), [str(c)], insert_at=0)

    toc = get_pdf_toc(str(a))
    # ページ順: c(1), b(2), a(3)。すべて level 1、フラット。
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "c", 1),
        (1, "b", 2),
        (1, "a", 3),
    ]


def test_three_way_merge_in_place_flat(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    make_pdf(a, pages=2, toc=[[1, "a", 1]])
    make_pdf(b, pages=1, toc=[[1, "b", 1]])
    make_pdf(c, pages=4, toc=[[1, "c", 1]])

    merge_pdfs_in_place(str(a), [str(b), str(c)])

    toc = get_pdf_toc(str(a))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "a", 1),
        (1, "b", 3),  # a(2ページ)の後ろ
        (1, "c", 4),  # b(1ページ)の後ろ
    ]


def test_merge_tocless_pdfs_produce_empty_toc(tmp_path):
    # 内部しおりが無いファイル同士を重ねても、しおりは作られない(見出しを生成しない)。
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    make_pdf(a, pages=2)
    make_pdf(b, pages=3)

    merge_pdfs_in_place(str(a), [str(b)])

    assert get_pdf_toc(str(a)) == []


# ---------------------------------------------------------------------------
# merge_pdfs: 新規ファイルへの結合(GUI 未使用。ファイル名しおりのオプションは維持)
# ---------------------------------------------------------------------------


def test_merge_pdfs_fresh_adds_bookmarks(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    c = tmp_path / "c.pdf"
    out = tmp_path / "out.pdf"
    make_pdf(a, pages=2)
    make_pdf(b, pages=3)
    make_pdf(c, pages=1)

    merge_pdfs(str(out), [str(a), str(b), str(c)], add_file_bookmarks=True)

    toc = get_pdf_toc(str(out))
    assert [(e.title, e.page) for e in toc] == [
        ("a.pdf", 1),
        ("b.pdf", 3),
        ("c.pdf", 6),
    ]


# ---------------------------------------------------------------------------
# merge_paths_to_pdf: 「結合」ボタン → ファイル名を親、フォルダ構成を反映した階層しおり
#   各ファイル配下にはそのファイルが元々持つしおりを子としてネストする。
# ---------------------------------------------------------------------------


def test_merge_paths_builds_folder_hierarchy(tmp_path):
    # FolderA/ { a1.pdf, Sub/ { s1.pdf, s2.pdf } } と単独 loose.pdf を結合
    folder = tmp_path / "FolderA"
    sub = folder / "Sub"
    sub.mkdir(parents=True)
    make_pdf(folder / "a1.pdf", pages=1)
    make_pdf(sub / "s1.pdf", pages=1)
    make_pdf(sub / "s2.pdf", pages=1)
    loose = tmp_path / "loose.pdf"
    make_pdf(loose, pages=1)
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


def test_merge_paths_nests_file_internal_bookmarks(tmp_path):
    # ファイルが元々持つしおりは、ファイル名見出しの子として階層ごと引き継ぐ。
    loose = tmp_path / "loose.pdf"
    make_pdf(loose, pages=2, toc=[[1, "い", 1], [2, "i", 1], [1, "ろ", 2]])
    out = tmp_path / "merged.pdf"

    merge_paths_to_pdf(str(out), [str(loose)])

    toc = get_pdf_toc(str(out))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "loose.pdf", 1),
        (2, "い", 1),
        (3, "i", 1),   # い の子(内部 level 2 → 見出し level 1 + 2 = 3)
        (2, "ろ", 2),
    ]


def test_merge_paths_three_files_filename_parent_with_children(tmp_path):
    # ユーザーの例: A,B,C を結合 → ファイル名が親、元しおりが子。
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    c = tmp_path / "C.pdf"
    make_pdf(a, pages=2, toc=[[1, "い", 1], [2, "i", 1], [1, "ろ", 1], [1, "は", 2]])
    make_pdf(b, pages=1, toc=[[1, "に", 1], [1, "ほ", 1]])
    make_pdf(c, pages=1, toc=[[1, "へ", 1], [1, "と", 1]])
    out = tmp_path / "merged.pdf"

    merge_paths_to_pdf(str(out), [str(a), str(b), str(c)])

    toc = get_pdf_toc(str(out))
    assert [(e.level, e.title, e.page) for e in toc] == [
        (1, "A.pdf", 1),
        (2, "い", 1),
        (3, "i", 1),
        (2, "ろ", 1),
        (2, "は", 2),
        (1, "B.pdf", 3),
        (2, "に", 3),
        (2, "ほ", 3),
        (1, "C.pdf", 4),
        (2, "へ", 4),
        (2, "と", 4),
    ]


def test_merge_paths_top_level_order_is_preserved(tmp_path):
    # トップレベルは呼び出し側が渡した順(フォルダ→ファイル)で並ぶ
    folder = tmp_path / "Docs"
    folder.mkdir()
    make_pdf(folder / "inner.pdf", pages=1)
    x = tmp_path / "x.pdf"
    make_pdf(x, pages=2)
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
    make_pdf(x, pages=1)
    make_pdf(y, pages=1)
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
