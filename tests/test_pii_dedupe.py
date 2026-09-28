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


# ---------------------------------------------------------------------------
# entity_overlap_mode(PresidioPDFの「対象重複判定」を移植)
# ---------------------------------------------------------------------------


def test_entity_overlap_mode_any_merges_different_entities_by_default():
    """既定("any")は種別を問わず重なりを統合する(従来動作、明示的な回帰確認)。"""
    plain = [
        {"start": 10, "end": 20, "entity": "LOCATION", "text": "東京都新宿区西新宿"},
        {"start": 10, "end": 12, "entity": "PERSON", "text": "東京"},
    ]
    out = dedupe_detections(
        {"plain": plain}, overlap="overlap", keep="widest", entity_overlap_mode="any"
    )
    assert len(out["plain"]) == 1


def test_entity_overlap_mode_same_keeps_different_entities_when_not_contained():
    """"same"モードでは、種別が異なり包含関係も無ければ別物として残ること。

    (PROPER_NOUN絡みでない限り、種別が異なるだけでは重複扱いしない)
    """
    plain = [
        {"start": 10, "end": 13, "entity": "PERSON", "text": "公務員"},
        {"start": 10, "end": 22, "entity": "DATE_TIME", "text": "公務員は休みだった"},
    ]
    out = dedupe_detections(
        {"plain": plain}, overlap="overlap", keep="widest", entity_overlap_mode="same"
    )
    assert len(out["plain"]) == 2


def test_entity_overlap_mode_same_still_merges_contained_proper_noun():
    """"same"モードでも、一方がPROPER_NOUNで包含関係があれば重複扱いすること。"""
    plain = [
        {"start": 10, "end": 20, "entity": "PERSON", "text": "山田太郎さん"},
        {"start": 10, "end": 12, "entity": "PROPER_NOUN", "text": "山田"},
    ]
    out = dedupe_detections(
        {"plain": plain}, overlap="overlap", keep="widest", entity_overlap_mode="same"
    )
    assert len(out["plain"]) == 1
    assert out["plain"][0]["text"] == "山田太郎さん"


def test_entity_overlap_mode_same_merges_within_same_entity():
    """"same"モードでも、同じ種別同士の重なりは通常通り統合されること。"""
    plain = [
        {"start": 10, "end": 20, "entity": "LOCATION", "text": "東京都新宿区西新宿"},
        {"start": 10, "end": 12, "entity": "LOCATION", "text": "東京"},
    ]
    out = dedupe_detections(
        {"plain": plain}, overlap="overlap", keep="widest", entity_overlap_mode="same"
    )
    assert len(out["plain"]) == 1
