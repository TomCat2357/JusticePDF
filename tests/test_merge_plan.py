"""結合の計画（merge_plan）と、変換済み PDF を含む merge_paths_to_pdf のテスト。

Web 版 tests/mergeplan.test.js の plan / assessLimits に相当する部分を移植したもの。
"""
from __future__ import annotations

import os

from src.utils import merge_plan as mp
from src.utils.merge_plan import MB, assess_limits, scan_paths
from src.utils.pdf_utils import get_pdf_toc, merge_paths_to_pdf
from tests.helpers import make_pdf


def _touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _names(files):
    return [(f.rel_dir + "/" if f.rel_dir else "") + f.name for f in files]


def test_scan_expands_folders_in_order_and_classifies(tmp_path):
    top = _touch(tmp_path / "top.pdf")
    _touch(tmp_path / "G" / "p.pdf")
    _touch(tmp_path / "F" / "b.pdf")
    _touch(tmp_path / "F" / "A.pdf")
    _touch(tmp_path / "F" / "memo.docx", b"12345")
    _touch(tmp_path / "F" / "z.zip")
    _touch(tmp_path / "F" / "Sub" / "pic.png")
    _touch(tmp_path / "F" / "~$memo.docx")  # Office の一時ファイルは数えない
    plan = scan_paths([top, str(tmp_path / "G"), str(tmp_path / "F")])

    # 上の階層は渡された順、フォルダの中は名前順（大小文字無視）
    assert _names(plan.files) == [
        "top.pdf", "G/p.pdf", "F/A.pdf", "F/b.pdf", "F/memo.docx", "F/Sub/pic.png"]
    assert [f.convert for f in plan.converts] == ["office", "image"]
    assert _names(plan.unsupported) == ["F/z.zip"]
    assert plan.has_non_pdf
    assert (plan.count, plan.pdf_count, plan.folders) == (6, 4, 3)
    assert plan.bytes == 1 * 4 + 5 + 1  # pdf 4 件 + docx + png


def test_scan_pdf_only_is_not_copy_only(tmp_path):
    _touch(tmp_path / "e" / ".keep.tmp")
    (tmp_path / "e2").mkdir()
    plan = scan_paths([str(tmp_path / "e"), str(tmp_path / "e2"), _touch(tmp_path / "a.pdf")])
    assert not plan.has_non_pdf
    assert _names(plan.files) == ["a.pdf"]  # 空のフォルダ・一時ファイルは入らない


def test_scan_top_level_order_is_kept(tmp_path):
    a = _touch(tmp_path / "a.pdf")
    b = _touch(tmp_path / "b.pdf")
    _touch(tmp_path / "G" / "x.pdf")
    plan = scan_paths([a, str(tmp_path / "G"), b])
    assert _names(plan.files) == ["a.pdf", "G/x.pdf", "b.pdf"]


def test_scan_top_level_unsupported_is_reported(tmp_path):
    plan = scan_paths([_touch(tmp_path / "a.pdf"), _touch(tmp_path / "b.zip"), _touch(tmp_path / "c.xyz")])
    assert _names(plan.unsupported) == ["b.zip", "c.xyz"]
    assert plan.has_non_pdf


def test_scan_depth_limit(tmp_path):
    deep = tmp_path / "d0"
    cur = deep
    for i in range(1, 5):
        cur = cur / f"d{i}"
    _touch(cur / "x.pdf")
    plan = scan_paths([str(deep)], max_depth=2)
    assert plan.files == [] and plan.errors


def test_assess_limits_thresholds():
    lv = lambda count, mb: assess_limits(count, int(mb * MB)).level  # noqa: E731
    assert [lv(100, 200), lv(101, 1), lv(5, 201), lv(1000, 9999), lv(1001, 1)] == [
        "ok", "warn", "warn", "warn", "block"]
    assert (mp.WARN_COUNT, mp.WARN_BYTES, mp.MAX_COUNT) == (100, 200 * MB, 1000)


def test_limit_notes():
    assert "1,234 件" in mp.block_note(assess_limits(1234, 1))
    assert "150 件・約 12 MB" in mp.warn_note(assess_limits(150, 12 * MB))


def test_merge_paths_with_converted_builds_bookmarks(tmp_path):
    """変換済みの一時 PDF は、元ファイルの位置・名前（拡張子なし + .pdf）で結合される。"""
    a = tmp_path / "a.pdf"
    make_pdf(a, pages=1)
    folder = tmp_path / "F"
    _touch(folder / "memo.docx")
    make_pdf(folder / "z.pdf", pages=2)
    _touch(folder / "ignored.zip")
    tmp_pdf = tmp_path / "tmp" / "0001.pdf"
    make_pdf(tmp_pdf, pages=3)

    out = tmp_path / "out.pdf"
    total = merge_paths_to_pdf(
        str(out), [str(a), str(folder)], {str(folder / "memo.docx"): str(tmp_pdf)})
    assert total == 1 + 3 + 2
    assert [(e.level, e.title, e.page) for e in get_pdf_toc(str(out))] == [
        (1, "a.pdf", 1),
        (1, "F", 2),
        (2, "memo.pdf", 2),
        (2, "z.pdf", 5),
    ]
    assert os.path.exists(tmp_pdf)  # 一時ファイルの掃除は呼び出し側の責任
