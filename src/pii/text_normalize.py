"""検出用のテキスト正規化(文字数を変えない1:1の正規化)。

全角の数字・英字(``０９０－１２３４－５６７８``、``ＡＢＣ``)は、そのままでは電話番号などの
正規表現に一致しない。かといって ``unicodedata.normalize("NFKC", text)`` を丸ごとかけると
``㈱`` → ``(株)`` のように文字数が変わり、検出結果のオフセットとページ上の文字座標
(``get_page_chars`` の1文字ごとのbbox)が対応しなくなる。

そこで1文字ずつ NFKC をかけ、結果がちょうど1文字のときだけ置き換える(それ以外は元の
文字のまま)。これでオフセットと文字座標は常に1対1で保たれる。ハイフン類は NFKC では
全角ハイフンマイナス(U+FF0D)しか ASCII の ``-`` にならないため、数字に隣接する
ダッシュ類(‐ ‑ ‒ – — ― − など)も ``-`` に揃える(長音記号「ー」は、両隣が数字の
ときだけ。カタカナ語の長音は変えない)。
検出結果の語句(``pii_text``)には正規化前の元の文字列を使う(``original_span``)。
"""
from __future__ import annotations

import re
import unicodedata

# 数字に隣接しているときだけ ASCII のハイフンへ揃えるダッシュ類。
_DASH_CHARS = frozenset("‐‑‒–—―−﹣－⁃")
# 長音記号(カタカナ語では変えない)。両隣が数字のときだけハイフンとみなす。
_PROLONGED_MARKS = frozenset("ーｰ")


def _nfkc_1to1(ch: str) -> str:
    normalized = unicodedata.normalize("NFKC", ch)
    return normalized if len(normalized) == 1 else ch


def normalize_1to1(text: str) -> str:
    """1文字ずつ NFKC をかけ(結果が1文字のときだけ採用)、数字隣接のダッシュ類を ``-`` にする。

    戻り値は入力と同じ長さ(``len(result) == len(text)``)。
    """
    chars = [_nfkc_1to1(ch) for ch in text]
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch in _DASH_CHARS:
            prev_digit = i > 0 and chars[i - 1].isascii() and chars[i - 1].isdigit()
            next_digit = i + 1 < n and chars[i + 1].isascii() and chars[i + 1].isdigit()
            if prev_digit or next_digit:
                chars[i] = "-"
        elif ch in _PROLONGED_MARKS:
            if (
                i > 0
                and i + 1 < n
                and chars[i - 1].isascii()
                and chars[i - 1].isdigit()
                and chars[i + 1].isascii()
                and chars[i + 1].isdigit()
            ):
                chars[i] = "-"
    return "".join(chars)


def normalize_pattern(pattern: str) -> str:
    """ユーザーが書いた正規表現を、検出用テキスト(``normalize_1to1`` 済み)に合わせて正規化する。

    1文字ずつ NFKC をかけ(結果が1文字のときだけ採用)、文字が変わった場合は次のとおり。

    - 変わった結果が正規表現の特殊文字(``（``→``(``、``＋``→``+``、``＊``、``？``、
      ``［``、``｜``、``．``、``＄``、``＾``、``｛``、``＼`` など)なら ``re.escape`` して
      **リテラル**として残す(全角の記号は「その文字そのもの」を書いたものとみなす。
      例: ``株式会社（仮）`` は「株式会社(仮)」という文字列に一致する)。
    - それ以外(全角の数字・英字など)は半角へ揃える(``０９０`` → ``090``)。

    もともとの ASCII 文字(本物の正規表現の構文: ``\\d``、``(``、``+`` など)は
    変えずにそのまま通す。全角の記号をリテラルにした結果、半角の記号を書いても
    全角を書いても同じ文字列に一致する。
    """
    out: list[str] = []
    for ch in pattern:
        normalized = _nfkc_1to1(ch)
        if normalized != ch and re.escape(normalized) != normalized:
            out.append(re.escape(normalized))
        else:
            out.append(normalized)
    return "".join(out)


def original_span(
    original: str, normalized: str, start: int, end: int, detected_text: str
) -> str:
    """検出結果の語句を、正規化前の元の文字列で返す。

    通常は ``original[start:end]``。検出エンジンが語句を整形している場合
    (電話番号の空白除去など、``normalized[start:end]`` と ``detected_text`` が
    異なる場合)は、``detected_text`` の各文字を ``normalized[start:end]`` の中から
    順に対応づけ、対応した位置の元の文字を並べる。対応づけできなければ
    ``detected_text`` をそのまま返す。
    """
    if not (0 <= start <= end <= len(original)) or len(original) != len(normalized):
        return detected_text
    if normalized[start:end] == detected_text:
        return original[start:end]
    picked: list[str] = []
    cursor = start
    for ch in detected_text:
        while cursor < end and normalized[cursor] != ch:
            cursor += 1
        if cursor >= end:
            return detected_text
        picked.append(original[cursor])
        cursor += 1
    return "".join(picked)
