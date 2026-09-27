"""ページテキスト⇔座標(quad)変換ヘルパー。

PresidioPDF は独自の ``PDFTextLocator``/``PDFBlockTextMapper``(文字単位の
グローバルオフセット表・行/ブロック単位の座標索引)でこれを行っていたが、
JusticePDF は既に ``src.utils.pdf_utils.rendering.get_page_chars`` で
1ページ分の文字＋bbox＋line_id を提供しており、拡大ビューの文字装飾
(``page_edit_widgets.ZoomPageWidget._quads_for_char_indices``)もこれを使って
同じ「同一line_idを連結してbboxを外接矩形にまとめる」方式でquadを作っている。

そのため本モジュールは PresidioPDF のロケータ実装を移植するのではなく、
JusticePDF の既存プリミティブに合わせて新規に書いた薄いアダプタである
(quadの流儀・座標系は ``_quads_for_char_indices`` と揃えてあるので、
生成したハイライトは手動で選択して作ったものと完全互換に振る舞う)。
"""
from __future__ import annotations

from src.utils.pdf_utils import get_page_chars


def get_page_text_and_chars(pdf_path: str, page_num: int) -> tuple[str, list[dict]]:
    """ページのテキストと、各文字に対応する座標情報を1対1で返す。

    戻り値の ``text[i]`` は ``chars[i]["c"]`` と対応する
    (``src.pii.analyzer.Analyzer.analyze_text`` に渡すオフセットが、
    そのまま ``chars`` のインデックスとして使える)。
    """
    chars = get_page_chars(pdf_path, page_num)
    text = "".join(ch.get("c", "") for ch in chars)
    return text, chars


def offset_span_to_quads(
    chars: list[dict], start: int, end: int
) -> list[tuple[float, float, float, float]]:
    """[start, end) の文字区間を、行ごとに外接矩形へまとめたquad列へ変換する。

    ``page_edit_widgets.ZoomPageWidget._quads_for_char_indices`` と同じ
    「同一 line_id が連続する範囲を1本のquadにまとめる」規則に合わせている。
    bbox を持たない文字（改行の区切りなど）はスキップする。
    """
    quads: list[tuple[float, float, float, float]] = []
    run: list[dict] = []
    prev_line = None

    def flush() -> None:
        if not run:
            return
        x0 = min(ch["bbox"][0] for ch in run)
        y0 = min(ch["bbox"][1] for ch in run)
        x1 = max(ch["bbox"][2] for ch in run)
        y1 = max(ch["bbox"][3] for ch in run)
        quads.append((float(x0), float(y0), float(x1), float(y1)))

    for idx in range(max(0, start), min(end, len(chars))):
        ch = chars[idx]
        bbox = ch.get("bbox")
        if not bbox:
            continue
        line_id = ch.get("line_id")
        if run and line_id != prev_line:
            flush()
            run = []
        run.append(ch)
        prev_line = line_id
    flush()
    return quads


# --- 図形(塗りつぶし用矩形/楕円)の下にある文字の抽出 ------------------------
#
# 「塗りつぶし用の四角・丸」は文字を持たない領域(写真/印影/手書き等)にも
# 使えるため、検出結果として quad/オフセットではなく「図形の矩形と重なる
# 文字」を直接見つける必要がある。判定はいずれも文字bboxの中心点を使う
# (半分だけ図形に掛かる文字を含めるかどうかの一貫した基準として)。


def chars_under_rect(
    chars: list[dict], rect: tuple[float, float, float, float]
) -> list[dict]:
    """bboxの中心が矩形 ``rect`` の内側にある文字を、元の順序を保って返す。"""
    x0, y0, x1, y1 = rect
    result: list[dict] = []
    for ch in chars:
        bbox = ch.get("bbox")
        if not bbox:
            continue
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            result.append(ch)
    return result


def chars_under_ellipse(
    chars: list[dict], rect: tuple[float, float, float, float]
) -> list[dict]:
    """bboxの中心が、``rect`` に内接する楕円の内側にある文字を返す。"""
    x0, y0, x1, y1 = rect
    cx0 = (x0 + x1) / 2.0
    cy0 = (y0 + y1) / 2.0
    rx = (x1 - x0) / 2.0
    ry = (y1 - y0) / 2.0
    if rx <= 0 or ry <= 0:
        return []
    result: list[dict] = []
    for ch in chars:
        bbox = ch.get("bbox")
        if not bbox:
            continue
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        if ((cx - cx0) / rx) ** 2 + ((cy - cy0) / ry) ** 2 <= 1.0:
            result.append(ch)
    return result


def join_chars_reading_order(chars: list[dict]) -> str:
    """文字リストを読み順のまま連結する(行が変わる箇所には改行を挟む)。"""
    parts: list[str] = []
    prev_line = None
    for ch in chars:
        line_id = ch.get("line_id")
        if prev_line is not None and line_id != prev_line:
            parts.append("\n")
        parts.append(ch.get("c", ""))
        prev_line = line_id
    return "".join(parts)


def text_under_rect(chars: list[dict], rect: tuple[float, float, float, float]) -> str:
    """矩形の下にある文字列を読み順で連結して返す(無ければ空文字)。"""
    return join_chars_reading_order(chars_under_rect(chars, rect))


def text_under_ellipse(chars: list[dict], rect: tuple[float, float, float, float]) -> str:
    """楕円(rectに内接)の下にある文字列を読み順で連結して返す(無ければ空文字)。"""
    return join_chars_reading_order(chars_under_ellipse(chars, rect))


def extract_text_under_shape(pdf_path: str, page_num: int, shape) -> str:
    """``ShapeAnnotData`` の下にある文字列を抽出する(矩形/楕円のみ対応)。

    それ以外の図形種別(線・三角形・括弧等)は文字を持たない前提のため
    常に空文字を返す。
    """
    from src.utils.pdf_utils import ShapeType  # 遅延importで循環参照を避ける

    if shape.shape_type not in (ShapeType.RECTANGLE, ShapeType.ELLIPSE):
        return ""
    chars = get_page_chars(pdf_path, page_num)
    if not chars:
        return ""
    if shape.shape_type == ShapeType.ELLIPSE:
        return text_under_ellipse(chars, shape.rect)
    return text_under_rect(chars, shape.rect)
