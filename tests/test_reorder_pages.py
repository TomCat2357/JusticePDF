"""reorder_pages: 少数ページの移動は move_page で行い、結果は select() と同じになる。"""
from __future__ import annotations

import random

import fitz
import pytest

from src.utils.pdf_utils import pages as pages_mod
from src.utils.pdf_utils.pages import _pages_to_move, reorder_pages


def _make_labeled(path, n, toc=True):
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 100), f"P{i}")
    if toc:
        doc.set_toc([[1, f"T{k}", k * 50 + 1] for k in range(n // 50)])
    doc.save(str(path))
    doc.close()


def _labels(path):
    with fitz.open(str(path)) as doc:
        return [page.get_text().strip() for page in doc]


def _toc(path):
    with fitz.open(str(path)) as doc:
        return doc.get_toc()


def test_pages_to_move_returns_only_pages_outside_longest_kept_run():
    assert _pages_to_move(list(range(10))) == []
    assert _pages_to_move([3, 0, 1, 2, 4]) == [3]
    assert sorted(_pages_to_move([2, 3, 4, 0, 1])) in ([0, 1], [2, 3, 4])


@pytest.mark.parametrize("seed", range(6))
def test_move_page_path_matches_select_for_random_few_page_moves(tmp_path, monkeypatch, seed):
    n = 420
    rng = random.Random(seed)
    base = tmp_path / "base.pdf"
    _make_labeled(base, n)
    order = list(range(n))
    for _ in range(rng.randint(1, 5)):
        page = order.pop(rng.randrange(len(order)))
        order.insert(rng.randrange(len(order) + 1), page)

    fast = tmp_path / "fast.pdf"
    slow = tmp_path / "slow.pdf"
    for p in (fast, slow):
        p.write_bytes(base.read_bytes())

    calls = []
    real_select = fitz.Document.select
    monkeypatch.setattr(fitz.Document, "select", lambda self, o: (calls.append(1), real_select(self, o))[1])
    reorder_pages(str(fast), order)
    assert not calls  # move_page だけで済んでいる

    monkeypatch.setattr(pages_mod, "_REORDER_MOVE_PAGE_MIN_PAGES", 10**9)
    reorder_pages(str(slow), order)
    assert calls

    assert _labels(fast) == _labels(slow) == [f"P{i}" for i in order]
    assert _toc(fast) == _toc(slow)


def test_many_moves_or_small_docs_fall_back_to_select(tmp_path, monkeypatch):
    calls = []
    real_select = fitz.Document.select
    monkeypatch.setattr(fitz.Document, "select", lambda self, o: (calls.append(1), real_select(self, o))[1])
    small = tmp_path / "small.pdf"
    _make_labeled(small, 10, toc=False)
    reorder_pages(str(small), [9, 8, 7, 6, 5, 4, 3, 2, 1, 0])
    assert calls and _labels(small) == [f"P{i}" for i in reversed(range(10))]

    calls.clear()
    big = tmp_path / "big.pdf"
    _make_labeled(big, 420, toc=False)
    order = list(reversed(range(420)))  # 動かすページが多い
    reorder_pages(str(big), order)
    assert calls and _labels(big) == [f"P{i}" for i in order]


def test_reorder_pages_move_page_round_trip_on_disk(tmp_path, monkeypatch):
    n = 420
    base = tmp_path / "base.pdf"
    _make_labeled(base, n)
    with fitz.open(str(base)) as doc:
        doc[7].add_text_annot((30, 30), "moved-note")
        doc.saveIncr()

    order = list(range(n))
    page = order.pop(7)
    order.insert(300, page)
    # 2回目の並べ替えは「現在のファイルのページ位置」で指定する。
    rel = list(range(n))
    rel.insert(5, rel.pop(200))
    order2 = [order[i] for i in rel]

    fast = tmp_path / "fast.pdf"
    ref = tmp_path / "ref.pdf"
    fast.write_bytes(base.read_bytes())
    ref.write_bytes(base.read_bytes())

    calls = []
    real_select = fitz.Document.select
    monkeypatch.setattr(fitz.Document, "select", lambda self, o: (calls.append(1), real_select(self, o))[1])
    reorder_pages(str(fast), order)
    assert not calls  # move_page の高速経路

    with fitz.open(str(ref)) as doc:
        real_select(doc, order)
        doc.save(str(tmp_path / "ref_out.pdf"))
    ref_out = tmp_path / "ref_out.pdf"

    def check(path, expected_order):
        with fitz.open(str(path)) as doc:
            assert not getattr(doc, "is_repaired", False)
            assert doc.page_count == n
            assert [p.get_text().strip() for p in doc] == [f"P{i}" for i in expected_order]
            idx = expected_order.index(7)
            annots = list(doc[idx].annots())
            assert [a.info.get("content") for a in annots] == ["moved-note"]
            for i, p in enumerate(doc):
                if i != idx:
                    assert not list(p.annots())

    check(fast, order)
    assert _toc(fast) == _toc(ref_out)

    reorder_pages(str(fast), rel)
    assert not calls
    check(fast, order2)
