#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正規表現マッチ位置の補助ユーティリティ。

Ported from PresidioPDF src/core/regex_match_utils.py（無改修）。
"""
from __future__ import annotations

from typing import Match, Tuple


def resolve_mark_span(match: Match[str]) -> Tuple[int, int]:
    """マーク対象の範囲(start, end)を返す。

    仕様:
    - キャプチャグループがある場合は、最初に実際にマッチしたグループ範囲を優先する
    - 有効なキャプチャがない場合は、通常のマッチ全体(0番)を返す
    """
    full_start, full_end = match.span()
    group_count = int(getattr(match.re, "groups", 0) or 0)
    if group_count <= 0:
        return full_start, full_end

    for group_index in range(1, group_count + 1):
        try:
            g_start, g_end = match.span(group_index)
        except IndexError:
            continue
        if g_start < 0 or g_end < 0:
            continue
        if g_start == g_end:
            continue
        return g_start, g_end

    return full_start, full_end
