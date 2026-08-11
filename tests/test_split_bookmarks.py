from __future__ import annotations

from src.utils.path_utils import sanitize_filename
from src.utils.pdf_utils import (
    get_pdf_toc,
    merge_paths_to_pdf,
    plan_split,
    split_pdf_by_toc,
)
from tests.helpers import make_pdf


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------


def test_sanitize_filename_replaces_illegal_chars():
    assert sanitize_filename("第1章/概要") == "第1章_概要"
    assert sanitize_filename("A:B") == "A_B"
    assert sanitize_filename('a*b?c"d<e>f|g\\h/i') == "a_b_c_d_e_f_g_h_i"


def test_sanitize_filename_strips_control_chars():
    assert sanitize_filename("a\x00b\x1fc") == "a_b_c"


def test_sanitize_filename_strips_leading_trailing_whitespace_and_dots():
    assert sanitize_filename("  hello  ") == "hello"
    assert sanitize_filename("hello...") == "hello"
    assert sanitize_filename("hello. .") == "hello"


def test_sanitize_filename_reserved_names():
    assert sanitize_filename("CON") == "CON_"
    assert sanitize_filename("con") == "con_"
    assert sanitize_filename("COM1") == "COM1_"
    assert sanitize_filename("lpt9") == "lpt9_"
    # 予約名でない通常の名前はそのまま
    assert sanitize_filename("CONTACT") == "CONTACT"


def test_sanitize_filename_empty_falls_back():
    assert sanitize_filename("") == "(無題)"
    assert sanitize_filename("   ") == "(無題)"
    assert sanitize_filename("...") == "(無題)"
    assert sanitize_filename(".", fallback="empty") == "empty"


def test_sanitize_filename_truncates_long_stem():
    long_name = "あ" * 150
    result = sanitize_filename(long_name, max_stem=100)
    assert len(result) == 100


# ---------------------------------------------------------------------------
# plan_split / split_pdf_by_toc: しおり階層あり
# ---------------------------------------------------------------------------


def test_plan_and_split_by_top_level_bookmarks(tmp_path):
    src = tmp_path / "報告書.pdf"
    make_pdf(
        src,
        pages=6,
        toc=[
            [1, "第1章", 1],
            [2, "1-1節", 1],
            [1, "第2章", 3],
            [1, "第3章", 5],
        ],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts, mode = plan_split(str(src))
    assert mode == "toc"
    assert len(parts) == 3
    assert [p.filename for p in parts] == ["第1章.pdf", "第2章.pdf", "第3章.pdf"]

    created = split_pdf_by_toc(str(src), str(out_dir), parts)
    assert len(created) == 3

    import fitz

    with fitz.open(created[0]) as d:
        assert d.page_count == 2
    with fitz.open(created[1]) as d:
        assert d.page_count == 2
    with fitz.open(created[2]) as d:
        assert d.page_count == 2

    toc0 = get_pdf_toc(created[0])
    assert [(e.level, e.title, e.page) for e in toc0] == [(1, "1-1節", 1)]
    toc1 = get_pdf_toc(created[1])
    assert toc1 == []
    toc2 = get_pdf_toc(created[2])
    assert toc2 == []


def test_plan_split_leading_dangling_pages(tmp_path):
    # level1 の最初が p.3 → 先頭に「しおり前ページ」パーツ(2ページ)が生成される
    src = tmp_path / "資料.pdf"
    make_pdf(
        src,
        pages=5,
        toc=[
            [1, "本編", 3],
            [1, "付録", 5],
        ],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts, mode = plan_split(str(src))
    assert mode == "toc"
    assert len(parts) == 3
    assert parts[0].filename == "資料_しおり前ページ.pdf"
    assert parts[0].title == ""
    assert parts[0].start0 == 0
    assert parts[0].end0 == 1  # p.1, p.2 (0始まり 0,1)
    assert parts[1].filename == "本編.pdf"
    assert parts[2].filename == "付録.pdf"

    created = split_pdf_by_toc(str(src), str(out_dir), parts)
    assert len(created) == 3

    import fitz

    with fitz.open(created[0]) as d:
        assert d.page_count == 2
    with fitz.open(created[1]) as d:
        assert d.page_count == 2
    with fitz.open(created[2]) as d:
        assert d.page_count == 1


def test_plan_split_no_leading_part_when_first_bookmark_is_page1(tmp_path):
    src = tmp_path / "x.pdf"
    make_pdf(src, pages=3, toc=[[1, "A", 1], [1, "B", 2]])

    parts, mode = plan_split(str(src))
    assert mode == "toc"
    assert len(parts) == 2
    assert parts[0].title == "A"


# ---------------------------------------------------------------------------
# plan_split / split_pdf_by_toc: しおりなし → 1ページ1ファイル
# ---------------------------------------------------------------------------


def test_plan_split_page_mode_no_toc(tmp_path):
    src = tmp_path / "無題.pdf"
    make_pdf(src, pages=12)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts, mode = plan_split(str(src))
    assert mode == "page"
    assert len(parts) == 12
    assert parts[0].filename == "無題_001p.pdf"
    assert parts[-1].filename == "無題_012p.pdf"
    assert all(p.title == "" for p in parts)

    created = split_pdf_by_toc(str(src), str(out_dir), parts)
    assert len(created) == 12
    import fitz

    for path in created:
        with fitz.open(path) as d:
            assert d.page_count == 1


def test_plan_split_page_mode_width_scales_with_page_count(tmp_path):
    src = tmp_path / "large.pdf"
    make_pdf(src, pages=150)

    parts, mode = plan_split(str(src))
    assert mode == "page"
    assert len(parts) == 150
    # 150ページなら幅3桁のまま
    assert parts[0].filename == "large_001p.pdf"
    assert parts[-1].filename == "large_150p.pdf"


def test_plan_split_page_mode_width_expands_past_999(tmp_path):
    # 1000ページ以上なら幅が4桁に広がる(Explorerの並び順が崩れないように)
    src = tmp_path / "huge.pdf"
    make_pdf(src, pages=1000)

    parts, mode = plan_split(str(src))
    assert mode == "page"
    assert len(parts) == 1000
    assert parts[0].filename == "huge_0001p.pdf"
    assert parts[998].filename == "huge_0999p.pdf"
    assert parts[-1].filename == "huge_1000p.pdf"


# ---------------------------------------------------------------------------
# ファイル名サニタイズ(しおりタイトル由来)
# ---------------------------------------------------------------------------


def test_split_filename_sanitizes_illegal_title_chars(tmp_path):
    src = tmp_path / "x.pdf"
    make_pdf(src, pages=2, toc=[[1, "第1章/概要", 1], [1, "A:B", 2]])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts, mode = plan_split(str(src))
    assert mode == "toc"
    assert parts[0].filename == "第1章_概要.pdf"
    assert parts[1].filename == "A_B.pdf"


def test_split_filename_title_ending_in_pdf_does_not_double_extension(tmp_path):
    src = tmp_path / "x.pdf"
    make_pdf(src, pages=2, toc=[[1, "報告書.pdf", 1]])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts, mode = plan_split(str(src))
    assert mode == "toc"
    assert parts[0].filename == "報告書.pdf"

    created = split_pdf_by_toc(str(src), str(out_dir), parts)
    assert len(created) == 1
    import os

    assert os.path.basename(created[0]) == "報告書.pdf"


def test_split_duplicate_bookmark_titles_get_unique_names(tmp_path):
    src = tmp_path / "x.pdf"
    make_pdf(src, pages=3, toc=[[1, "X", 1], [1, "X", 2], [1, "X", 3]])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    parts, mode = plan_split(str(src))
    assert mode == "toc"
    assert len(parts) == 3
    # プラン上はすべて同じファイル名(実際の一意化は書き出し時)
    assert [p.filename for p in parts] == ["X.pdf", "X.pdf", "X.pdf"]

    created = split_pdf_by_toc(str(src), str(out_dir), parts)
    import os

    names = sorted(os.path.basename(p) for p in created)
    assert names == ["X(1).pdf", "X(2).pdf", "X.pdf"]


# ---------------------------------------------------------------------------
# 往復テスト: merge_paths_to_pdf → plan_split + split_pdf_by_toc で復元
# ---------------------------------------------------------------------------


def test_round_trip_merge_then_split_restores_files(tmp_path):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    c = tmp_path / "C.pdf"
    make_pdf(a, pages=2, toc=[[1, "い", 1], [2, "i", 1], [1, "ろ", 2]])
    make_pdf(b, pages=1, toc=[[1, "に", 1]])
    make_pdf(c, pages=3, toc=[[1, "へ", 1], [1, "と", 3]])

    merged = tmp_path / "merged.pdf"
    # フォルダは混ぜない(ファイル名見出しが level 1 になるようにするため)
    total = merge_paths_to_pdf(str(merged), [str(a), str(b), str(c)])
    assert total == 6

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    parts, mode = plan_split(str(merged))
    assert mode == "toc"
    assert [p.filename for p in parts] == ["A.pdf", "B.pdf", "C.pdf"]

    created = split_pdf_by_toc(str(merged), str(out_dir), parts)
    assert len(created) == 3

    import fitz
    import os

    by_name = {os.path.basename(p): p for p in created}
    with fitz.open(by_name["A.pdf"]) as d:
        assert d.page_count == 2
    with fitz.open(by_name["B.pdf"]) as d:
        assert d.page_count == 1
    with fitz.open(by_name["C.pdf"]) as d:
        assert d.page_count == 3

    toc_a = get_pdf_toc(by_name["A.pdf"])
    assert [(e.level, e.title, e.page) for e in toc_a] == [
        (1, "い", 1),
        (2, "i", 1),
        (1, "ろ", 2),
    ]
    toc_b = get_pdf_toc(by_name["B.pdf"])
    assert [(e.level, e.title, e.page) for e in toc_b] == [(1, "に", 1)]
    toc_c = get_pdf_toc(by_name["C.pdf"])
    assert [(e.level, e.title, e.page) for e in toc_c] == [
        (1, "へ", 1),
        (1, "と", 3),
    ]
