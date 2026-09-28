"""src.pii.dedupe.dedupe_detections の重なり判定(overlap)の回帰テスト。

架空のダミーデータのみを使用する。
"""
from __future__ import annotations

from src.pii.dedupe import dedupe_detections


def test_touching_but_not_overlapping_spans_are_not_merged():
    """区間は半開区間[start, end)なので、境界が接するだけの2件は重複ではない。

    テキスト抽出では隣接するテーブルの列同士が区切り文字無しで連結される
    ことがあり(例:「公務員」の直後に区切り無しで日付が続く)、この場合
    それぞれの検出区間はちょうど接する(片方のendがもう片方のstartと同じ)。
    以前は overlap 判定に `<=` を使っていたため、このような隣接区間が誤って
    重複扱いされ、"widest"(既定)では短い方が消えてしまっていた。
    """
    plain = [
        {"start": 10, "end": 13, "entity": "PERSON", "text": "公務員"},
        {"start": 13, "end": 22, "entity": "DATE_TIME", "text": "昭和52年11月23日"},
    ]
    out = dedupe_detections({"plain": plain}, overlap="overlap", keep="widest")
    texts = {d["text"] for d in out["plain"]}
    assert texts == {"公務員", "昭和52年11月23日"}


def test_actually_overlapping_spans_are_still_merged():
    """本当に範囲が重なる場合は、従来通り重複除去されること(回帰確認)。"""
    plain = [
        {"start": 10, "end": 20, "entity": "PERSON", "text": "山田太郎さん"},
        {"start": 10, "end": 12, "entity": "PROPER_NOUN", "text": "山田"},
    ]
    out = dedupe_detections({"plain": plain}, overlap="overlap", keep="widest")
    assert len(out["plain"]) == 1
    assert out["plain"][0]["text"] == "山田太郎さん"
