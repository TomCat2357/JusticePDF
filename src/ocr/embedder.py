"""OCR結果のPDFへの埋め込み/削除。

Ported from PresidioPDF src/pdf/pdf_text_embedder.py(``PDFTextEmbedder``)。

OCRで認識した行を、不透明度0(見えない)の FreeText 注釈として、認識した位置に
埋め込む。PyMuPDF のテキスト抽出は注釈の外観ストリームの文字も拾うため、
``get_page_chars``(検出用のテキスト+文字座標)からそのまま読める
(実測: 日本語を含む行でも文字ごとの bbox が得られ、検出の quad に使える)。

JusticePDF は FreeText 注釈を編集対象として一覧/選択/消しゴム/コピーで扱うため、
OCR注釈は Subject の接頭辞(``JUSTICEPDF_OCR_SUBJECT_PREFIX``)で区別し、
``pdf_utils.annotations`` 側の一覧抽出から除外している(混同を防ぐ)。
Subject には行の矩形(表示座標)などを JSON で持たせ、削除→復元(Undo)で位置を
そのまま再現できるようにしている。

■ 文字位置の精度(実測にもとづく設計)
FreeText の文字は注釈矩形の左上から一定の字送りで並ぶだけで、1文字ずつの位置は
指定できない。しかも字送りはフォント選択に依存する(実測):

- 初期状態は Helvetica(欧数字は比例幅)。
- U+0250 以上の文字(かな・漢字・全角記号など)が現れると CJK フォント(全角=1em)に
  切り替わる。
- ASCII の「英字」が現れると Helvetica に戻る。数字・記号・空白は直前の状態を引き継ぐ
  (例: 「番号は090-1234」の数字は全角幅、「Tel 090-1234」の数字は比例幅)。

そのため「1行=1注釈」にすると、日本語の後ろの数字が全角幅になって行全体の文字位置が
実際の画像上の位置からずれ、黒塗りの位置がずれてしまう。そこで行を「CJKの連続」と
「それ以外(欧数字・空白・ASCII記号)の連続」に分け、それぞれを別の注釈にして、
行幅を各区間の予測幅(``text_width_em``。実測と一致)に比例して配分する。
区間の境界が実際の位置に近づき、``get_page_chars`` から得る文字 bbox(検出の quad)も
画像上の文字位置にほぼ重なる。
"""
from __future__ import annotations

import json
import logging
from typing import Iterable, List, Optional, Sequence

import fitz

from src.ocr.base import OCRResult
from src.utils.pdf_utils.annotations import JUSTICEPDF_OCR_SUBJECT_PREFIX
from src.utils.pdf_utils.common import (
    _acquire_doc,
    _mark_edit_failed,
    _open_doc,
    _release_doc,
    _save_document_in_place,
)

logger = logging.getLogger(__name__)

OCR_TITLE = "JusticePDF OCR"
# 埋め込みに使うフォント名。日本語(CJK)を含む行でも抽出できる(実測)。
OCR_FONTNAME = "japan"
# FreeText の文字bboxは rect.y0 を基準に、CJK区間では [y0 - 0.2fs, y0 + 1.0fs]、
# Latin区間では [y0 - 0.275fs, y0 + 1.099fs] の範囲に置かれる(実測)。
# 行の矩形と文字bboxがそろうよう、この係数で文字サイズと配置を決める。
_CJK_BBOX_HEIGHT = 1.2
_CJK_BBOX_CENTER = 0.4
_LATIN_BBOX_HEIGHT = 1.374
_LATIN_BBOX_CENTER = 0.412
# 文字bboxの高さが行の高さを超えてよい上限の倍率。
_MAX_HEIGHT_OVERSHOOT = 1.3
# CJKフォントへ切り替わる境界(これ以上の文字は Helvetica に無い)。
_CJK_THRESHOLD = 0x250


def is_ocr_subject(subject: str) -> bool:
    """Subject がOCR注釈のものか。"""
    return bool(subject) and subject.startswith(JUSTICEPDF_OCR_SUBJECT_PREFIX)


def is_ocr_annot(annot: fitz.Annot) -> bool:
    """注釈がOCRで埋め込んだ FreeText か。"""
    try:
        if annot.type[0] != fitz.PDF_ANNOT_FREE_TEXT:
            return False
        return is_ocr_subject(str((annot.info or {}).get("subject", "") or ""))
    except Exception:  # noqa: BLE001
        return False


def _is_cjk_char(ch: str) -> bool:
    return ord(ch) >= _CJK_THRESHOLD


def text_width_em(text: str) -> float:
    """``text`` を FreeText として fontsize=1 で描いたときの幅(em)。実測と一致する。

    初期状態は Helvetica。U+0250以上の文字で CJK(全角1em)になり、ASCII の英字で
    Helvetica に戻る。数字・記号・空白は直前の状態を引き継ぐ。
    """
    total = 0.0
    cjk = False
    for ch in text:
        if _is_cjk_char(ch):
            cjk = True
        elif ch.isalpha():
            cjk = False
        total += 1.0 if cjk else fitz.get_text_length(ch, fontname="helv", fontsize=1.0)
    return total


def split_segments(text: str) -> List[str]:
    """行を「CJKの連続」と「それ以外の連続」に分ける(空白は直前の区間に付ける)。

    各区間は単独の注釈になるので、区間の先頭は必ず期待どおりのフォント状態
    (CJK区間=全角、それ以外=Helvetica)から始まり、幅の予測が正確になる。
    先頭の空白だけは、続く区間に含める。
    """
    segments: List[str] = []
    current = ""
    current_is_cjk: Optional[bool] = None
    for ch in text:
        if ch.isspace():
            current += ch
            continue
        is_cjk = _is_cjk_char(ch)
        if current_is_cjk is None:
            current_is_cjk = is_cjk
        elif is_cjk != current_is_cjk:
            segments.append(current)
            current = ""
            current_is_cjk = is_cjk
        current += ch
    if current:
        segments.append(current)
    return segments


def _segment_is_cjk(segment: str) -> bool:
    for ch in segment:
        if not ch.isspace():
            return _is_cjk_char(ch)
    return False


def _layout_segments(text: str, rect: fitz.Rect) -> "list[tuple[str, float, fitz.Rect]]":
    """行の矩形(表示座標)を区間ごとに配分し、(区間テキスト, フォントサイズ, 注釈矩形)を返す。"""
    segments = split_segments(text.strip())
    widths = [max(text_width_em(seg), 1e-6) for seg in segments]
    total_em = sum(widths)
    scale = rect.width / total_em  # 1emあたりのpt(行の実幅から)
    center_y = (rect.y0 + rect.y1) / 2.0
    laid_out: list[tuple[str, float, fitz.Rect]] = []
    x = rect.x0
    for seg, width_em in zip(segments, widths):
        alloc = width_em * scale
        cjk = _segment_is_cjk(seg)
        height_per_fs = _CJK_BBOX_HEIGHT if cjk else _LATIN_BBOX_HEIGHT
        center_per_fs = _CJK_BBOX_CENTER if cjk else _LATIN_BBOX_CENTER
        # 文字サイズは基本的に行の実幅から決める(区間の文字が配分された幅を埋め、隣の区間との
        # 隙間に余計な空白が挿入されないように)。縦は行の高さの _MAX_HEIGHT_OVERSHOOT 倍までにとどめる。
        fontsize = max(
            1.0,
            min(72.0, scale * 0.98, _MAX_HEIGHT_OVERSHOOT * rect.height / height_per_fs),
        )
        top = center_y - center_per_fs * fontsize
        # 幅に余裕を持たせる(見積りの誤差で折り返されて文字が欠けるのを避ける)。
        annot_rect = fitz.Rect(
            x,
            top,
            x + alloc * 1.15 + fontsize,
            top + fontsize * 1.5,
        )
        laid_out.append((seg, fontsize, annot_rect))
        x += alloc
    return laid_out


def _encode_subject(
    rect: fitz.Rect, confidence: float, page_rotation: int, line: int, seg: int
) -> str:
    payload = {
        "rect": [round(float(v), 3) for v in rect],
        "conf": round(float(confidence), 3),
        "page_rotation": int(page_rotation),
        "line": int(line),
        "seg": int(seg),
    }
    return f"{JUSTICEPDF_OCR_SUBJECT_PREFIX}:" + json.dumps(payload, separators=(",", ":"))


def _decode_subject(subject: str) -> Optional[dict]:
    if not is_ocr_subject(subject):
        return None
    body = subject[len(JUSTICEPDF_OCR_SUBJECT_PREFIX):]
    if not body.startswith(":"):
        return {}
    try:
        data = json.loads(body[1:])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def embed_ocr_results(
    doc: fitz.Document, results: Iterable[OCRResult]
) -> int:
    """OCR結果(行単位・表示座標)を、見えないFreeText注釈として埋め込む。埋め込んだ「行」数を返す。

    1行は複数の注釈(CJK区間/それ以外の区間)になることがある(モジュールdocstring参照)。
    ページの回転に応じて注釈矩形を内部座標へ直し、文字の向きもページの回転に合わせる
    (見えないが、抽出される文字の並びが読み順になる)。
    """
    if not isinstance(doc, fitz.Document):
        raise TypeError("docはfitz.Documentである必要があります")

    inserted_lines = 0
    line_counter: dict[int, int] = {}
    for result in results:
        page_num = int(result.page_num)
        if page_num < 0 or page_num >= len(doc):
            continue
        text = str(result.text or "").strip()
        if not text:
            continue
        page = doc[page_num]
        rect = fitz.Rect(*result.rect) & page.rect
        if rect.is_empty or rect.width <= 0.0 or rect.height <= 0.0:
            continue
        # 同じページ内で行を区別する番号(既存の行があれば続き番号にする)。
        if page_num not in line_counter:
            existing = [
                int((_decode_subject(str((a.info or {}).get("subject", "") or "")) or {}).get("line", -1))
                for a in (page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]) or [])
                if is_ocr_annot(a)
            ]
            line_counter[page_num] = max(existing, default=-1) + 1
        line_id = line_counter[page_num]
        line_counter[page_num] += 1

        wrote_any = False
        for seg_index, (segment, fontsize, annot_rect) in enumerate(_layout_segments(text, rect)):
            if not segment.strip():
                continue
            internal_rect = annot_rect * page.derotation_matrix
            internal_rect.normalize()
            try:
                annot = page.add_freetext_annot(
                    internal_rect,
                    segment,
                    fontsize=fontsize,
                    fontname=OCR_FONTNAME,
                    text_color=(0, 0, 0),
                    fill_color=None,
                    border_width=0,
                    opacity=0.0,
                    align=fitz.TEXT_ALIGN_LEFT,
                    rotate=page.rotation,
                )
                annot.set_border(width=0)
                info = dict(annot.info or {})
                info["title"] = OCR_TITLE
                info["subject"] = _encode_subject(
                    rect, result.confidence, page.rotation, line_id, seg_index
                )
                info["content"] = segment
                annot.set_info(info)
                annot.update(opacity=0.0)
                wrote_any = True
            except Exception as exc:  # noqa: BLE001 - 1区間の失敗で全体を止めない
                logger.warning(
                    "OCRテキストの埋め込みに失敗: page=%s text=%s (%s)", page_num, segment, exc
                )
        if wrote_any:
            inserted_lines += 1
    return inserted_lines


def _resolve_pages(page_count: int, page_filter: Optional[Sequence[int]]) -> List[int]:
    if page_filter is None:
        return list(range(page_count))
    pages: List[int] = []
    for value in page_filter:
        try:
            page_num = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= page_num < page_count and page_num not in pages:
            pages.append(page_num)
    return pages


def remove_ocr_annots(
    doc: fitz.Document, page_filter: Optional[Sequence[int]] = None
) -> int:
    """埋め込んだOCR注釈(区間単位)を削除する(対象ページの指定が無ければ全ページ)。削除件数を返す。"""
    if not isinstance(doc, fitz.Document):
        raise TypeError("docはfitz.Documentである必要があります")
    removed = 0
    for page_num in _resolve_pages(len(doc), page_filter):
        page = doc[page_num]
        for annot in list(page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]) or []):
            if is_ocr_annot(annot):
                page.delete_annot(annot)
                removed += 1
    return removed


def read_ocr_results(
    doc: fitz.Document, page_filter: Optional[Sequence[int]] = None
) -> List[OCRResult]:
    """埋め込み済みOCR注釈を、行単位の ``OCRResult``(表示座標の行矩形+テキスト)として読み戻す。

    区間ごとの注釈を行(``line`` 番号)ごとにまとめ、区間の順に連結する。
    削除の取り消し(Undo)で元の位置へ復元するために使う。
    """
    results: List[OCRResult] = []
    for page_num in _resolve_pages(len(doc), page_filter):
        page = doc[page_num]
        lines: dict[int, dict] = {}
        for annot in page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]) or []:
            info = annot.info or {}
            metadata = _decode_subject(str(info.get("subject", "") or ""))
            if metadata is None:
                continue
            text = str(info.get("content", "") or "")
            if not text.strip():
                continue
            line = int(metadata.get("line", -1 - len(lines)))
            entry = lines.setdefault(line, {"segments": [], "meta": metadata})
            entry["segments"].append((int(metadata.get("seg", 0)), text))
            entry.setdefault("annot_rect", None)
            if entry["annot_rect"] is None:
                rect = fitz.Rect(annot.rect) * page.rotation_matrix
                rect.normalize()
                entry["annot_rect"] = rect
        for line in sorted(lines):
            entry = lines[line]
            metadata = entry["meta"]
            raw_rect = metadata.get("rect")
            if isinstance(raw_rect, list) and len(raw_rect) == 4:
                rect = fitz.Rect(*[float(v) for v in raw_rect])
            else:
                rect = entry["annot_rect"]
            text = "".join(t for _, t in sorted(entry["segments"], key=lambda s: s[0]))
            if not text.strip() or rect is None or rect.is_empty:
                continue
            results.append(
                OCRResult(
                    text=text,
                    x=rect.x0,
                    y=rect.y0,
                    width=rect.width,
                    height=rect.height,
                    page_num=page_num,
                    confidence=float(metadata.get("conf", 0.0) or 0.0),
                )
            )
    return results


def count_ocr_annots(pdf_path: str, page_filter: Optional[Sequence[int]] = None) -> int:
    """PDF内のOCR注釈(区間単位)の件数(読み取り専用)。"""
    try:
        with _open_doc(pdf_path) as doc:
            total = 0
            for page_num in _resolve_pages(len(doc), page_filter):
                for annot in doc[page_num].annots(types=[fitz.PDF_ANNOT_FREE_TEXT]) or []:
                    if is_ocr_annot(annot):
                        total += 1
            return total
    except Exception:  # noqa: BLE001
        logger.debug("count_ocr_annots failed: %s", pdf_path, exc_info=True)
        return 0


def count_ocr_lines(pdf_path: str, page_filter: Optional[Sequence[int]] = None) -> int:
    """PDF内に埋め込まれたOCRの「行」数(読み取り専用)。"""
    try:
        with _open_doc(pdf_path) as doc:
            return len(read_ocr_results(doc, page_filter))
    except Exception:  # noqa: BLE001
        logger.debug("count_ocr_lines failed: %s", pdf_path, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# ファイル単位の操作(メインスレッドから呼ぶ: 書き込みは1か所に集約する)
# ---------------------------------------------------------------------------


def snapshot_ocr_results(
    pdf_path: str, page_filter: Optional[Sequence[int]] = None
) -> List[OCRResult]:
    """指定ページの埋め込み済みOCRを読み戻す(ファイルは変更しない)。"""
    with _open_doc(pdf_path) as doc:
        return read_ocr_results(doc, page_filter)


def replace_ocr_in_file(
    pdf_path: str,
    page_filter: Optional[Sequence[int]],
    results: Iterable[OCRResult],
) -> tuple[int, int]:
    """対象ページの既存OCR注釈をすべて削除してから ``results`` を埋め込み、上書き保存する。

    OCRの実行・削除・それらの取り消し(Undo)はすべてこの関数で表せる
    (実行=新結果で置換 / 削除=空で置換 / 取り消し=直前の内容で置換)。
    戻り値は (削除した注釈数, 埋め込んだ行数)。変更が無ければ保存しない。
    ``PdfWritePermissionError`` は呼び出し側で処理する。
    """
    results = list(results)
    doc = _acquire_doc(pdf_path)
    try:
        removed = remove_ocr_annots(doc, page_filter)
        inserted = embed_ocr_results(doc, results)
        if removed or inserted:
            _save_document_in_place(doc, pdf_path)
        return removed, inserted
    except BaseException:
        _mark_edit_failed(doc)
        raise
    finally:
        _release_doc(doc)
