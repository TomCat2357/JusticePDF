"""ページ本文検索の高速化: ページ別の正規化テキストによる先行絞り込みと、チャンク分割の走査。

最終的なヒット(ページと矩形)は常に ``page.search_for(query)`` で確定する。ここで持つ
正規化テキストは「候補ページの絞り込み」だけに使い、``search_for`` より必ず緩くなる
ように作る(偽陰性を出さない)。

- 抽出は ``search_for`` が既定で使うのと同じフラグ(DEHYPHENATE | PRESERVE_WHITESPACE |
  PRESERVE_LIGATURES | MEDIABOX_CLIP = 83)で行い、文字列が同一になるようにする。
  1ページ分の TextPage を抽出と ``search_for(textpage=...)`` で共有する。
- 本文と検索語を同じ関数で正規化する: casefold → 空白をすべて除去 → ハイフン類
  (``-``・ソフトハイフン・U+2010・U+2011)を除去。NFKC などの文字をまたぐ合成は行わない
  (MuPDF の検索は幅・互換・結合文字の折り畳みをしないので、合成すると「ｶﾞ」に対する
  「ｶ」のような部分一致を取りこぼす)。除去と casefold は部分文字列の関係を保つ。
- 正規化後の検索語が正規化後の本文に含まれるページだけを候補にする。
  正規化後の検索語が空なら全ページが候補。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import fitz

# search_for が既定で使う抽出フラグ(Page.search_for の既定値と同じ)
SEARCH_TEXT_FLAGS = (
    fitz.TEXT_DEHYPHENATE
    | fitz.TEXT_PRESERVE_WHITESPACE
    | fitz.TEXT_PRESERVE_LIGATURES
    | fitz.TEXT_MEDIABOX_CLIP
)

# キャッシュする正規化テキストの総文字数の上限(超えたら新規ページはキャッシュせず直接検索する)
MAX_INDEX_CHARS = 20_000_000

_HYPHEN_TABLE = {ord(c): None for c in ("-", "­", "‐", "‑")}


def normalize_for_search(text: str) -> str:
    """絞り込み用の正規化(本文・検索語の両方に同じものを使う)。"""
    text = text.casefold()
    text = "".join(text.split())
    return text.translate(_HYPHEN_TABLE)


class PageTextIndex:
    """1つのPDFのページ別正規化テキスト(未取得のページは None)。"""

    __slots__ = ("token", "texts", "total_chars")

    def __init__(self, page_count: int, token: Any) -> None:
        self.token = token
        self.texts: list[str | None] = [None] * page_count
        self.total_chars = 0

    @property
    def page_count(self) -> int:
        return len(self.texts)

    def can_store(self) -> bool:
        return self.total_chars < MAX_INDEX_CHARS

    def set_text(self, page: int, raw_text: str) -> str:
        norm = normalize_for_search(raw_text)
        old = self.texts[page]
        if old is not None:
            self.total_chars -= len(old)
        self.texts[page] = norm
        self.total_chars += len(norm)
        return norm

    def invalidate_pages(self, pages) -> None:
        for p in pages:
            if 0 <= p < len(self.texts):
                old = self.texts[p]
                if old is not None:
                    self.total_chars -= len(old)
                    self.texts[p] = None

    def cached_count(self) -> int:
        return sum(1 for t in self.texts if t is not None)


class SearchScan:
    """ページを昇順に走査する検索の状態。``step`` を時間予算つきで繰り返し呼ぶ。"""

    def __init__(self, index: PageTextIndex, query: str, page_count: int) -> None:
        self.index = index
        self.query = query
        self.norm_query = normalize_for_search(query)
        self.page_count = page_count
        self.next_page = 0

    @property
    def done(self) -> bool:
        return self.next_page >= self.page_count

    def check_page(self, doc: "fitz.Document", i: int) -> "list | None":
        """1ページを調べ、ヒットの矩形(なければ None)を返す。索引にテキストを入れる。"""
        index = self.index
        nq = self.norm_query
        text = index.texts[i]
        page = doc[i]
        tp = None
        if text is None and index.can_store():
            tp = page.get_textpage(flags=SEARCH_TEXT_FLAGS)
            text = index.set_text(i, tp.extractText())
        if text is None:
            candidate = True  # キャッシュ上限超過: 絞り込まず直接検索
        else:
            candidate = (not nq) or (nq in text)
        if not candidate:
            return None
        if tp is not None:
            rects = page.search_for(self.query, textpage=tp)
        else:
            rects = page.search_for(self.query)
        return list(rects) if rects else None

    def step(
        self,
        get_doc: Callable[[], "fitz.Document"],
        budget_s: "float | None",
    ) -> list[tuple[int, list]]:
        """予算(秒)が尽きるか終わるまで走査し、見つかった ``[(page, rects)]`` を返す。

        *get_doc* は文書が必要になったとき(キャッシュに無いページ・候補ページ)だけ呼ぶ。
        予算は文書に触れたページごとに確認する。
        """
        index = self.index
        texts = index.texts
        nq = self.norm_query
        found: list[tuple[int, list]] = []
        deadline = None if budget_s is None else time.perf_counter() + budget_s
        n = 0
        while self.next_page < self.page_count:
            i = self.next_page
            text = texts[i]
            touched = True
            if text is not None and nq and nq not in text:
                touched = False  # キャッシュ済みで候補でない: 文書に触れない
            else:
                rects = self.check_page(get_doc(), i)
                if rects:
                    found.append((i, rects))
            self.next_page = i + 1
            n += 1
            if deadline is not None and (touched or (n & 255) == 0) and time.perf_counter() >= deadline:
                break
        return found
