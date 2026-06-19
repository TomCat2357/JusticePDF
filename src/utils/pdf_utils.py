"""PDF utility functions using PyMuPDF."""
import html
import json
import logging
import math
import os
import re
import shutil
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum

import fitz
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPainter, QPixmap, QImage, QPageLayout
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
from PyQt6.QtWidgets import QWidget

from src.utils.constants import (
    FREETEXT_LINE_HEIGHT,
    FREETEXT_TEXT_INSET_PT,
    freetext_css_font_family,
    freetext_font_key,
)


logger = logging.getLogger(__name__)
JUSTICEPDF_FREETEXT_SUBJECT_PREFIX = "JusticePDF-FreeText:"
JUSTICEPDF_SHAPE_SUBJECT_PREFIX = "JusticePDF-Shape:"
JUSTICEPDF_MARKUP_SUBJECT_PREFIX = "JusticePDF-Markup:"
JUSTICEPDF_NOTE_SUBJECT_PREFIX = "JusticePDF-Note:"
# 付箋アイコンの公称サイズ（PDF ポイント）。ヒットテスト用の矩形に使う。
NOTE_ICON_PDF_SIZE = 18.0


class PdfWritePermissionError(PermissionError):
    """Raised when a PDF cannot be overwritten because another app is using it."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        super().__init__(13, f"Permission denied: '{pdf_path}'", pdf_path)


@dataclass(slots=True)
class FreeTextAnnotData:
    page_num: int
    xref: int
    rect: tuple[float, float, float, float]
    content: str
    fontsize: float
    text_color: tuple[float, float, float]
    fill_color: tuple[float, float, float] | None
    border_color: tuple[float, float, float] | None
    border_width: float
    opacity: float
    fontname: str = "Helv"
    annotation_id: str = ""
    subject: str = ""
    text_rotation: int = 0
    group_id: str = ""
    # 校正コールアウト用。非空なら FreeTextCallout（本文ボックス＋引き出し線＋矢印）として描画する。
    # 表示座標系の点列 [target, box_attach]（先端＝挿入位置、末尾＝ボックス接続点）。
    callout_line: tuple[tuple[float, float], ...] = ()
    # 引き出し線の先端＝挿入位置（callout_line[0] と同じ）。利便性のために保持する。
    callout_target: tuple[float, float] | None = None


class ShapeType(str, Enum):
    LINE = "line"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    TRIANGLE = "triangle"
    BRACKET = "bracket"


@dataclass(slots=True)
class ShapeAnnotData:
    page_num: int
    xref: int
    rect: tuple[float, float, float, float]
    shape_type: ShapeType
    stroke_color: tuple[float, float, float] | None
    fill_color: tuple[float, float, float] | None
    stroke_width: float
    opacity: float
    rotation: float = 0.0
    arrow_start: bool = False
    arrow_end: bool = False
    bracket_style: str = "square"
    bracket_size: str = "medium"
    bracket_both_sides: bool = False
    bracket_side: str = "left"
    bracket_orientation: str = "vertical"  # "vertical" | "horizontal"
    group_id: str = ""
    vertices: tuple[tuple[float, float], ...] = ()
    triangle_apex: tuple[float, float] = (0.5, 0.0)
    annotation_id: str = ""
    subject: str = ""


class MarkupType(str, Enum):
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"


@dataclass(slots=True)
class TextMarkupAnnotData:
    """A text-anchored markup annotation (highlight / underline / strikeout).

    ``quads`` は表示座標系（``get_page_words`` と同じ）の単語矩形の並び。
    各要素は ``(x0, y0, x1, y1)``。
    """

    page_num: int
    xref: int
    quads: tuple[tuple[float, float, float, float], ...]
    markup_type: MarkupType
    color: tuple[float, float, float]
    opacity: float = 1.0
    annotation_id: str = ""
    subject: str = ""

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """Bounding box (x0, y0, x1, y1) covering all quads."""
        if not self.quads:
            return (0.0, 0.0, 0.0, 0.0)
        x0 = min(quad[0] for quad in self.quads)
        y0 = min(quad[1] for quad in self.quads)
        x1 = max(quad[2] for quad in self.quads)
        y1 = max(quad[3] for quad in self.quads)
        return (x0, y0, x1, y1)


@dataclass(slots=True)
class NoteAnnotData:
    """A sticky-note (PDF Text) annotation: a collapsible comment icon.

    ``point`` は付箋アイコン左上の表示座標。``content`` がコメント本文。
    """

    page_num: int
    xref: int
    point: tuple[float, float]
    content: str
    color: tuple[float, float, float]
    icon: str = "Note"
    opacity: float = 1.0
    annotation_id: str = ""
    subject: str = ""

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """Nominal icon rect (x0, y0, x1, y1) anchored at ``point``."""
        x, y = self.point
        return (x, y, x + NOTE_ICON_PDF_SIZE, y + NOTE_ICON_PDF_SIZE)


AnyAnnotData = FreeTextAnnotData | ShapeAnnotData | TextMarkupAnnotData | NoteAnnotData


@dataclass(slots=True)
class TocEntry:
    """A single PDF bookmark (outline) entry.

    PyMuPDF の TOC 仕様に合わせて level / page はともに 1 始まり。
    """

    level: int
    title: str
    page: int


class _PixmapCache:
    def __init__(self, maxsize: int = 256):
        self._maxsize = maxsize
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()

    def get(self, key: tuple) -> QPixmap | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: tuple, pixmap: QPixmap) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = pixmap
            return
        if len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = pixmap

    def clear(self) -> None:
        self._cache.clear()

    def clear_for_path(self, pdf_path: str) -> None:
        keys_to_remove = [k for k in self._cache if k[0] == pdf_path]
        for k in keys_to_remove:
            del self._cache[k]


_pixmap_cache = _PixmapCache(maxsize=256)


def clear_pixmap_cache_for_path(pdf_path: str) -> None:
    _pixmap_cache.clear_for_path(pdf_path)


def clear_pixmap_cache() -> None:
    _pixmap_cache.clear()

_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_DA_COLOR_RE = re.compile(rf"({_FLOAT_RE})\s+({_FLOAT_RE})\s+({_FLOAT_RE})\s+rg")
_DA_FONT_RE = re.compile(rf"/([^\s/]+)\s+({_FLOAT_RE})\s+Tf")
_CSS_DECL_RE = re.compile(r"\s*([^:]+)\s*:\s*([^;]+)\s*")
_CSS_BORDER_RE = re.compile(
    rf"({_FLOAT_RE})(?:px|pt)?\s+\w+\s+(#[0-9a-fA-F]{{6}}|rgb\([^)]+\))"
)


def _is_permission_denied_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return True
        if isinstance(current, OSError) and getattr(current, "errno", None) == 13:
            return True
        message = str(current).lower()
        if "permission denied" in message or "access is denied" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _save_document_in_place(
    doc: fitz.Document, pdf_path: str, *, incremental: bool = False
) -> None:
    """Persist a modified document.

    When *incremental* is True, tries ``saveIncr()`` first for speed
    (append-only, no rewrite).  Falls back to full save on failure.
    When False (default), uses full save with garbage collection to
    prevent file growth from repeated annotation edits.
    """
    if incremental:
        try:
            doc.saveIncr()
            _pixmap_cache.clear_for_path(pdf_path)
            return
        except Exception as error:
            if _is_permission_denied_error(error):
                raise PdfWritePermissionError(pdf_path) from error
            # Fall through to full save

    tmp_path: str | None = None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        doc.save(tmp_path, garbage=1, deflate=True)
    except Exception as error:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if _is_permission_denied_error(error):
            raise PdfWritePermissionError(pdf_path) from error
        raise
    try:
        shutil.move(tmp_path, pdf_path)
    except Exception as move_error:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if _is_permission_denied_error(move_error):
            raise PdfWritePermissionError(pdf_path) from move_error
        raise
    _pixmap_cache.clear_for_path(pdf_path)


def update_pdf_metadata_title(pdf_path: str, title: str) -> None:
    """PDFメタデータのTitleプロパティを更新する。"""
    with fitz.open(pdf_path) as doc:
        meta = doc.metadata
        meta['title'] = title
        doc.set_metadata(meta)
        _save_document_in_place(doc, pdf_path)


def get_pdf_metadata_title(pdf_path: str) -> str:
    """PDFメタデータのTitleプロパティを取得する。"""
    try:
        with fitz.open(pdf_path) as doc:
            meta = doc.metadata or {}
            title = meta.get("title")
            return str(title) if isinstance(title, str) else ""
    except Exception:
        logger.debug("Failed to read PDF metadata title: %s", pdf_path, exc_info=True)
        return ""


def get_pdf_toc(pdf_path: str) -> list[TocEntry]:
    """PDFのしおり(アウトライン/TOC)を取得する。

    取得できない場合や例外時は空リストを返す。各エントリの level / page は
    1 始まり（PyMuPDF仕様）。
    """
    try:
        with fitz.open(pdf_path) as doc:
            raw = doc.get_toc(simple=True)  # [[level, title, page], ...]
            return [
                TocEntry(level=int(level), title=str(title), page=int(page))
                for level, title, page in raw
            ]
    except Exception:
        logger.debug("Failed to read PDF TOC: %s", pdf_path, exc_info=True)
        return []


def normalize_toc(
    entries: list[TocEntry], *, page_count: int | None = None
) -> list[TocEntry]:
    """``set_toc`` が受理できる形にしおりを補正する。

    - 先頭は強制的に level 1
    - 各 level は直前の level+1 を上限にクランプ（下限 1）
    - 空タイトルは "(無題)" にフォールバック
    - page は page_count 指定時に 1..page_count へクランプ

    例外を投げず「正す」方針。UI での昇格/降格の中間状態でも安全に保存できる。
    """
    result: list[TocEntry] = []
    prev_level = 0
    for entry in entries:
        level = max(1, min(int(entry.level), prev_level + 1))
        title = entry.title if entry.title.strip() else "(無題)"
        page = int(entry.page)
        if page_count is not None and page_count > 0:
            page = max(1, min(page, page_count))
        else:
            page = max(1, page)
        result.append(TocEntry(level=level, title=title, page=page))
        prev_level = level
    return result


def update_pdf_toc(
    pdf_path: str, entries: list[TocEntry], *, incremental: bool = False
) -> None:
    """しおりを一括設定する。``entries`` が空ならしおりを全削除する。

    TOC 再構築は旧オブジェクトを残してファイルが肥大するため、既定では全保存
    （``incremental=False``）を用いる。
    """
    with fitz.open(pdf_path) as doc:
        normalized = normalize_toc(entries, page_count=doc.page_count)
        toc_list = [[e.level, e.title, e.page] for e in normalized]
        doc.set_toc(toc_list)
        _save_document_in_place(doc, pdf_path, incremental=incremental)


def _file_bookmark_title(pdf_path: str) -> str:
    """しおりに使うファイル名(カードと同じく拡張子つきの basename)。"""
    return os.path.basename(pdf_path)


def _file_toc_entries(title: str, start0: int, sub: list) -> list[TocEntry]:
    """新しく結合する1ファイル分の TOC を組む。

    開始ページにファイル名(level 1)を置き、そのファイルが元々持っていたしおり
    ``sub`` はその子(level+1)としてページをオフセットして並べる。
    ``start0`` は結合後ドキュメントでの 0 始まり開始ページ。
    """
    entries: list[TocEntry] = [TocEntry(level=1, title=title, page=start0 + 1)]
    for level, sub_title, page in sub:
        entries.append(
            TocEntry(level=int(level) + 1, title=str(sub_title), page=int(page) + start0)
        )
    return entries


def _offset_toc_entries(toc: list, offset: int) -> list[TocEntry]:
    """既存しおりを level は変えずページだけ ``offset`` して TocEntry 化する。

    結合先(dest)が既に持つファイル名しおりを再ネストせず、そのままの階層で残す
    ために使う。これにより1ファイルずつ重ねても階層が深くならない(フラットを維持)。
    """
    return [
        TocEntry(level=int(level), title=str(title), page=int(page) + offset)
        for level, title, page in toc
    ]


def _parse_float_array(value: str) -> list[float]:
    return [float(item) for item in re.findall(_FLOAT_RE, value or "")]


def _normalize_color(
    values: list[float] | tuple[float, ...] | None,
    fallback: tuple[float, float, float] | None = None,
) -> tuple[float, float, float] | None:
    if not values or len(values) < 3:
        return fallback
    return (float(values[0]), float(values[1]), float(values[2]))


def _parse_css_declarations(style: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in (style or "").split(";"):
        match = _CSS_DECL_RE.fullmatch(part.strip())
        if not match:
            continue
        key, value = match.groups()
        result[key.lower()] = value.strip()
    return result


def _css_color_to_rgb(value: str | None) -> tuple[float, float, float] | None:
    if not value:
        return None
    value = value.strip()
    if value.lower() == "transparent":
        return None
    if value.startswith("#") and len(value) == 7:
        return (
            int(value[1:3], 16) / 255.0,
            int(value[3:5], 16) / 255.0,
            int(value[5:7], 16) / 255.0,
        )
    if value.lower().startswith("rgb(") and value.endswith(")"):
        parts = [p.strip() for p in value[4:-1].split(",")]
        if len(parts) == 3:
            try:
                return (
                    float(parts[0]) / 255.0,
                    float(parts[1]) / 255.0,
                    float(parts[2]) / 255.0,
                )
            except ValueError:
                return None
    return None


def _color_to_css(color: tuple[float, float, float] | None) -> str:
    if color is None:
        return "transparent"
    r = max(0, min(255, round(color[0] * 255)))
    g = max(0, min(255, round(color[1] * 255)))
    b = max(0, min(255, round(color[2] * 255)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _parse_da(da_value: str) -> tuple[str, float, tuple[float, float, float]]:
    fontname = "Helv"
    fontsize = 11.0
    text_color = (0.0, 0.0, 0.0)

    font_match = _DA_FONT_RE.search(da_value or "")
    if font_match:
        fontname = font_match.group(1)
        fontsize = float(font_match.group(2))

    color_match = _DA_COLOR_RE.search(da_value or "")
    if color_match:
        text_color = (
            float(color_match.group(1)),
            float(color_match.group(2)),
            float(color_match.group(3)),
        )
    return fontname, fontsize, text_color


def _pdf_font_to_css(fontname: str) -> str:
    # base-14 タグ → Acrobat が認識する基本フェイス名。font-family の
    # フォールバック列(CJK 含む)は freetext_css_font_family() が組み立てる。
    name = (fontname or "Helv").lower()
    if "cour" in name:
        return "Courier"
    if "tiro" in name or "times" in name:
        return "Times New Roman"
    return "Helvetica"


def _css_font_to_pdf(fontname: str | None) -> str:
    # CSS のフォールバック列 ("Helvetica, 'Yu Gothic', sans-serif" 等) でも
    # 先頭(主)ファミリで判定する(共有ロジック)。
    return freetext_font_key(fontname)


def _extract_text_from_rc(rc_value: str) -> str:
    if not rc_value:
        return ""
    text = re.sub(r"<[^>]+>", "", rc_value)
    return html.unescape(text).strip()


def _decode_subject_metadata(subject: str) -> dict[str, object] | None:
    if not subject or not subject.startswith(JUSTICEPDF_FREETEXT_SUBJECT_PREFIX):
        return None
    raw = subject[len(JUSTICEPDF_FREETEXT_SUBJECT_PREFIX):]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _encode_subject_metadata(data: FreeTextAnnotData, *, page_rotation: int = 0) -> str:
    payload = {
        "text_color": list(data.text_color),
        "fill_color": list(data.fill_color) if data.fill_color is not None else None,
        "border_color": list(data.border_color) if data.border_color is not None else None,
        "border_width": float(data.border_width),
        "fontsize": float(data.fontsize),
        "fontname": data.fontname,
        "page_rotation": int(page_rotation),
    }
    if data.group_id:
        payload["group_id"] = data.group_id
    if data.callout_line:
        # コールアウト時は annot.rect が引き出し線を含むよう拡張されるため、
        # 本文ボックス枠とコールアウト点列を明示保存して復元時に使う。
        payload["callout_line"] = [[float(x), float(y)] for x, y in data.callout_line]
        payload["text_rect"] = [float(v) for v in data.rect]
    return JUSTICEPDF_FREETEXT_SUBJECT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _build_richtext_style(data: FreeTextAnnotData) -> str:
    parts = [
        f"font-size:{max(1.0, float(data.fontsize)):g}pt",
        f"font-family:{freetext_css_font_family(data.fontname)}",
        f"line-height:{FREETEXT_LINE_HEIGHT:g}",
        f"color:{_color_to_css(data.text_color)}",
        "margin:0",
        "padding:0",
    ]
    if data.fill_color is not None:
        parts.append(f"background-color:{_color_to_css(data.fill_color)}")
    else:
        parts.append("background-color:transparent")
    parts.append("border:0px solid transparent")
    return "; ".join(parts) + ";"


def _extract_freetext_data(
    doc: fitz.Document,
    page_num: int,
    annot: fitz.Annot,
) -> FreeTextAnnotData:
    xref = annot.xref
    info = annot.info
    subject = info.get("subject", "")
    metadata = _decode_subject_metadata(subject) or {}

    _, da_value = doc.xref_get_key(xref, "DA")
    fontname, fontsize, text_color = _parse_da(da_value)

    rect = tuple(annot.rect)
    page = doc[page_num]
    if page.rotation != 0:
        rect = tuple(fitz.Rect(rect) * page.rotation_matrix)

    _, fill_value = doc.xref_get_key(xref, "C")
    fill_color = _normalize_color(_parse_float_array(fill_value))

    _, bs_value = doc.xref_get_key(xref, "BS")
    bs_widths = _parse_float_array(bs_value)
    border_width = float(bs_widths[0]) if bs_widths else float(annot.border.get("width", 0.0))
    border_color: tuple[float, float, float] | None = None

    _, ds_value = doc.xref_get_key(xref, "DS")
    css = _parse_css_declarations(ds_value if ds_value != "null" else "")
    if "font-size" in css:
        try:
            fontsize = float(re.findall(_FLOAT_RE, css["font-size"])[0])
        except (IndexError, ValueError):
            pass
    if "font-family" in css:
        fontname = _css_font_to_pdf(css["font-family"])
    css_text_color = _css_color_to_rgb(css.get("color"))
    if css_text_color is not None:
        text_color = css_text_color
    css_fill_color = _css_color_to_rgb(css.get("background-color"))
    if css_fill_color is not None:
        fill_color = css_fill_color
    css_border_color = _css_color_to_rgb(css.get("border-color"))
    if css_border_color is None and "border" in css:
        border_match = _CSS_BORDER_RE.search(css["border"])
        if border_match:
            border_width = float(border_match.group(1))
            css_border_color = _css_color_to_rgb(border_match.group(2))
    if "border-width" in css:
        try:
            border_width = float(re.findall(_FLOAT_RE, css["border-width"])[0])
        except (IndexError, ValueError):
            pass
    if css_border_color is not None:
        border_color = css_border_color

    if isinstance(metadata.get("fontsize"), (int, float)):
        fontsize = float(metadata["fontsize"])
    if isinstance(metadata.get("fontname"), str):
        fontname = str(metadata["fontname"])
    metadata_text_color = metadata.get("text_color")
    if isinstance(metadata_text_color, list):
        parsed = _normalize_color(metadata_text_color)
        if parsed is not None:
            text_color = parsed
    metadata_fill_color = metadata.get("fill_color")
    fill_color_explicit_none = metadata_fill_color is None and "fill_color" in metadata
    if isinstance(metadata_fill_color, list):
        fill_color = _normalize_color(metadata_fill_color)
    elif fill_color_explicit_none:
        fill_color = None
    metadata_border_color = metadata.get("border_color")
    border_color_explicit_none = metadata_border_color is None and "border_color" in metadata
    if isinstance(metadata_border_color, list):
        border_color = _normalize_color(metadata_border_color)
    elif border_color_explicit_none:
        border_color = None
    if isinstance(metadata.get("border_width"), (int, float)):
        border_width = float(metadata["border_width"])

    if border_width <= 0:
        border_color = None
        border_width = 0.0
    elif border_color is None and not border_color_explicit_none:
        border_color = (0.0, 0.0, 0.0)

    _, contents_value = doc.xref_get_key(xref, "Contents")
    content = info.get("content") or (contents_value if contents_value != "null" else "")
    if not content:
        _, rc_value = doc.xref_get_key(xref, "RC")
        if rc_value != "null":
            content = _extract_text_from_rc(rc_value)

    _, ca_value = doc.xref_get_key(xref, "CA")
    if ca_value == "null":
        opacity = 1.0
    else:
        try:
            opacity = float(ca_value)
        except ValueError:
            opacity = float(annot.opacity or 1.0)

    _, name_value = doc.xref_get_key(xref, "NM")
    annotation_id = info.get("id") or (name_value if name_value != "null" else "")

    creation_rotation = int(metadata.get("page_rotation", 0))
    text_rotation = (page.rotation - creation_rotation) % 360

    # 校正コールアウト: コールアウト点列と本文ボックス枠を復元する。
    # annot.rect は引き出し線を含むよう拡張されるため、保存済みの text_rect で上書きする。
    callout_line: tuple[tuple[float, float], ...] = ()
    callout_target: tuple[float, float] | None = None
    callout_raw = metadata.get("callout_line")
    if isinstance(callout_raw, list):
        pts: list[tuple[float, float]] = []
        for p in callout_raw:
            if isinstance(p, list) and len(p) >= 2:
                pts.append((float(p[0]), float(p[1])))
        if pts:
            callout_line = tuple(pts)
            callout_target = pts[0]
    text_rect = metadata.get("text_rect")
    if isinstance(text_rect, list) and len(text_rect) == 4:
        rect = tuple(float(v) for v in text_rect)

    return FreeTextAnnotData(
        page_num=page_num,
        xref=xref,
        rect=(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
        content=content or "",
        fontsize=float(fontsize),
        text_color=text_color,
        fill_color=fill_color,
        border_color=border_color,
        border_width=float(border_width),
        opacity=max(0.0, min(1.0, float(opacity))),
        fontname=fontname or "Helv",
        annotation_id=annotation_id,
        subject=subject,
        text_rotation=text_rotation,
        group_id=str(metadata.get("group_id", "")),
        callout_line=callout_line,
        callout_target=callout_target,
    )


def _fix_rc_leading_whitespace(doc: fitz.Document, xref: int) -> None:
    """Remove leading whitespace after <body> tag in RC to fix Acrobat display."""
    kind, rc = doc.xref_get_key(xref, "RC")
    if kind != "string" or not rc:
        return
    fixed = re.sub(r"(<body[^>]*>)\s+", r"\1", rc)
    if fixed != rc:
        doc.xref_set_key(xref, "RC", fitz.get_pdf_str(fixed))


def _add_freetext_annot_to_page(page: fitz.Page, data: FreeTextAnnotData) -> fitz.Annot:
    rect = fitz.Rect(*data.rect)
    border_width = max(0.0, float(data.border_width))
    effective_border_width = border_width if border_width > 0 and data.border_color is not None else 0.0
    opacity = max(0.0, min(1.0, float(data.opacity)))
    if effective_border_width > 0:
        inset = effective_border_width / 2.0
        if rect.width > inset * 2 and rect.height > inset * 2:
            rect = fitz.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset)
    if page.rotation != 0:
        rect = rect * page.derotation_matrix
    # Determine the creation page rotation: use existing metadata (undo/restore)
    # or current page rotation (new/edit where subject is cleared).
    existing_metadata = _decode_subject_metadata(data.subject)
    if existing_metadata is not None and "page_rotation" in existing_metadata:
        creation_page_rotation = int(existing_metadata["page_rotation"])
    else:
        creation_page_rotation = page.rotation
    # 校正コールアウト: 表示座標のコールアウト点列を（ページ回転時は）派生座標へ変換して渡す。
    callout_kwargs: dict[str, object] = {}
    if data.callout_line:
        callout_pts = list(data.callout_line)
        if page.rotation != 0:
            m = page.derotation_matrix
            callout_pts = [tuple(fitz.Point(p) * m) for p in callout_pts]
        callout_kwargs["callout"] = [fitz.Point(p) for p in callout_pts]
        callout_kwargs["line_end"] = fitz.PDF_ANNOT_LE_OPEN_ARROW
    annot = page.add_freetext_annot(
        rect,
        data.content,
        fontsize=max(1.0, float(data.fontsize)),
        fontname=data.fontname or "Helv",
        text_color=data.text_color,
        fill_color=data.fill_color,
        border_width=effective_border_width,
        opacity=opacity,
        rotate=creation_page_rotation,
        richtext=True,
        style=_build_richtext_style(data),
        **callout_kwargs,
    )
    annot.set_border(width=effective_border_width)
    _fix_rc_leading_whitespace(page.parent, annot.xref)
    annot.update(
        fontsize=max(1.0, float(data.fontsize)),
        fontname=data.fontname or "Helv",
        text_color=data.text_color,
        border_color=data.border_color if effective_border_width > 0 else None,
        fill_color=data.fill_color,
        rotate=creation_page_rotation,
        opacity=opacity,
    )
    annot.set_info(subject=_encode_subject_metadata(data, page_rotation=creation_page_rotation))
    # テキストの内側余白を /RD (RectDifferences) で明示し、Acrobat の本文配置を
    # キャンバス側の余白と一致させる。/Rect は枠線がある場合 border/2 だけ内側に
    # 移動済みなので、その分を差し引いて /RD を決める。/RD は /Rect を変えないため
    # rect のラウンドトリップには影響しない。update() が /RD を [0 0 0 0] に
    # 戻すので、必ず最後に設定する。
    rd = FREETEXT_TEXT_INSET_PT + effective_border_width / 2.0
    if rd > 0:
        doc = page.parent
        doc.xref_set_key(annot.xref, "RD", f"[{rd:g} {rd:g} {rd:g} {rd:g}]")
    return annot


# ---------------------------------------------------------------------------
# Shape annotation helpers
# ---------------------------------------------------------------------------

_SHAPE_ANNOT_TYPES = [
    fitz.PDF_ANNOT_LINE,
    fitz.PDF_ANNOT_SQUARE,
    fitz.PDF_ANNOT_CIRCLE,
    fitz.PDF_ANNOT_POLYGON,
    fitz.PDF_ANNOT_POLY_LINE,
]


def _rotate_point(x: float, y: float, cx: float, cy: float, angle_deg: float) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    dx, dy = x - cx, y - cy
    return (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)


def _rotate_vertices(
    vertices: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    cx: float,
    cy: float,
    angle_deg: float,
) -> list[tuple[float, float]]:
    if angle_deg == 0.0:
        return list(vertices)
    return [_rotate_point(x, y, cx, cy, angle_deg) for x, y in vertices]


def _ellipse_vertices(cx: float, cy: float, rx: float, ry: float, n: int = 32) -> list[tuple[float, float]]:
    return [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _triangle_vertices(
    rect: tuple[float, float, float, float],
    apex: tuple[float, float] = (0.5, 0.0),
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    ax = min(1.0, max(0.0, float(apex[0])))
    ay = min(1.0, max(0.0, float(apex[1])))
    apex_x = x0 + ax * (x1 - x0)
    apex_y = y0 + ay * (y1 - y0)
    return [(apex_x, apex_y), (x1, y1), (x0, y1)]


def _rectangle_vertices(rect: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _bracket_vertices_square(
    rect: tuple[float, float, float, float],
    side: str = "left",
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    w = x1 - x0
    hook = min(w * 0.4, 8.0)
    if side == "left":
        return [(x0 + hook, y0), (x0, y0), (x0, y1), (x0 + hook, y1)]
    else:
        return [(x1 - hook, y0), (x1, y0), (x1, y1), (x1 - hook, y1)]


def _bracket_vertices_round(
    rect: tuple[float, float, float, float],
    side: str = "left",
    n: int = 16,
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    h = y1 - y0
    w = x1 - x0
    depth = min(w * 0.4, 12.0)
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        angle = math.pi * t - math.pi / 2
        dx = depth * (1.0 - math.cos(angle)) / 2.0
        dy = y0 + t * h
        if side == "left":
            pts.append((x0 + depth - dx, dy))
        else:
            pts.append((x1 - depth + dx, dy))
    return pts


def _bracket_vertices_curly(
    rect: tuple[float, float, float, float],
    side: str = "left",
    n: int = 20,
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = rect
    h = y1 - y0
    w = x1 - x0
    depth = min(w * 0.4, 14.0)
    mid_y = (y0 + y1) / 2
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        y = y0 + t * h
        if t <= 0.5:
            s = t / 0.5
            dx = depth * (1.0 - math.cos(math.pi * s)) / 2.0
        else:
            s = (t - 0.5) / 0.5
            dx = depth * (1.0 + math.cos(math.pi * s)) / 2.0
        if side == "left":
            pts.append((x0 + depth - dx, y))
        else:
            pts.append((x1 - depth + dx, y))
    return pts


def _bracket_vertices_horizontal(
    rect: tuple[float, float, float, float],
    style: str = "curly",
    side: str = "left",
    n: int = 20,
) -> list[tuple[float, float]]:
    """横向きの括弧頂点。

    側面 ``side`` は突起の向き: "left"=上向き(y0側), "right"=下向き(y1側)。
    縦向きの実装を x/y 入れ替えで再利用する。
    """
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    if style == "square":
        hook = min(h * 0.4, 8.0)
        if side == "left":
            return [(x0, y0 + hook), (x0, y0), (x1, y0), (x1, y0 + hook)]
        return [(x0, y1 - hook), (x0, y1), (x1, y1), (x1, y1 - hook)]
    depth = min(h * 0.4, 14.0 if style == "curly" else 12.0)
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        x = x0 + t * w
        if style == "round":
            angle = math.pi * t - math.pi / 2
            d = depth * (1.0 - math.cos(angle)) / 2.0
        else:  # curly
            if t <= 0.5:
                s = t / 0.5
                d = depth * (1.0 - math.cos(math.pi * s)) / 2.0
            else:
                s = (t - 0.5) / 0.5
                d = depth * (1.0 + math.cos(math.pi * s)) / 2.0
        if side == "left":
            pts.append((x, y0 + depth - d))
        else:
            pts.append((x, y1 - depth + d))
    return pts


def _compute_bracket_vertices(
    rect: tuple[float, float, float, float],
    style: str = "square",
    side: str = "left",
    orientation: str = "vertical",
) -> list[tuple[float, float]]:
    if orientation == "horizontal":
        return _bracket_vertices_horizontal(rect, style, side)
    if style == "round":
        return _bracket_vertices_round(rect, side)
    elif style == "curly":
        return _bracket_vertices_curly(rect, side)
    else:
        return _bracket_vertices_square(rect, side)


def _line_endpoints_from_data(data: ShapeAnnotData) -> tuple[tuple[float, float], tuple[float, float]]:
    rect = data.rect
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if data.vertices and len(data.vertices) >= 2:
        rx1, ry1 = data.vertices[0]
        rx2, ry2 = data.vertices[1]
    else:
        rx1, ry1 = (0.0, 0.5)
        rx2, ry2 = (1.0, 0.5)
    p1 = (rect[0] + rx1 * width, rect[1] + ry1 * height)
    p2 = (rect[0] + rx2 * width, rect[1] + ry2 * height)
    return p1, p2


def _compute_shape_vertices(data: ShapeAnnotData) -> list[tuple[float, float]]:
    rect = data.rect
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2

    if data.shape_type == ShapeType.LINE:
        p1, p2 = _line_endpoints_from_data(data)
        pts = [p1, p2]
    elif data.shape_type == ShapeType.RECTANGLE:
        pts = _rectangle_vertices(rect)
    elif data.shape_type == ShapeType.ELLIPSE:
        rx = (rect[2] - rect[0]) / 2
        ry = (rect[3] - rect[1]) / 2
        pts = _ellipse_vertices(cx, cy, rx, ry)
    elif data.shape_type == ShapeType.TRIANGLE:
        pts = _triangle_vertices(rect, data.triangle_apex)
    elif data.shape_type == ShapeType.BRACKET:
        pts = _compute_bracket_vertices(
            rect, data.bracket_style, data.bracket_side, data.bracket_orientation
        )
    else:
        pts = _rectangle_vertices(rect)

    if data.rotation != 0.0:
        pts = _rotate_vertices(pts, cx, cy, data.rotation)
    return pts


def _encode_shape_metadata(data: ShapeAnnotData, *, page_rotation: int = 0) -> str:
    payload: dict = {
        "shape_type": data.shape_type.value,
        "stroke_color": list(data.stroke_color) if data.stroke_color is not None else None,
        "fill_color": list(data.fill_color) if data.fill_color is not None else None,
        "stroke_width": float(data.stroke_width),
        "rotation": float(data.rotation),
        "original_rect": list(data.rect),
        "page_rotation": int(page_rotation),
    }
    if data.group_id:
        payload["group_id"] = data.group_id
    if data.shape_type == ShapeType.LINE:
        payload["arrow_start"] = data.arrow_start
        payload["arrow_end"] = data.arrow_end
        if data.vertices and len(data.vertices) >= 2:
            payload["vertices"] = [[float(v[0]), float(v[1])] for v in data.vertices[:2]]
    if data.shape_type == ShapeType.BRACKET:
        payload["bracket_style"] = data.bracket_style
        payload["bracket_size"] = data.bracket_size
        payload["bracket_both_sides"] = data.bracket_both_sides
        payload["bracket_side"] = data.bracket_side
        payload["bracket_orientation"] = data.bracket_orientation
    if data.shape_type == ShapeType.TRIANGLE:
        payload["triangle_apex"] = [float(data.triangle_apex[0]), float(data.triangle_apex[1])]
    return JUSTICEPDF_SHAPE_SUBJECT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _decode_shape_metadata(subject: str) -> dict | None:
    if not subject or not subject.startswith(JUSTICEPDF_SHAPE_SUBJECT_PREFIX):
        return None
    raw = subject[len(JUSTICEPDF_SHAPE_SUBJECT_PREFIX):]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _line_ending_code(arrow: bool) -> int:
    return fitz.PDF_ANNOT_LE_OPEN_ARROW if arrow else fitz.PDF_ANNOT_LE_NONE


def _add_shape_annot_to_page(page: fitz.Page, data: ShapeAnnotData) -> fitz.Annot:
    opacity = max(0.0, min(1.0, float(data.opacity)))
    stroke_width = max(0.0, float(data.stroke_width))
    # 透明枠（stroke_color is None）は stroke=[] を渡して /C [] を明示書き込みする。
    # stroke=None だと PyMuPDF は /C キーを書かず、MuPDF の外観生成器が
    # Square/Circle/Line の既定枠色（赤）を焼き込んでしまう（印刷時に赤線が出る不具合）。
    stroke = list(data.stroke_color) if data.stroke_color is not None else []
    fill = list(data.fill_color) if data.fill_color is not None else None
    rect = data.rect
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    rotation = data.rotation
    has_rotation = rotation != 0.0

    existing_metadata = _decode_shape_metadata(data.subject)
    if existing_metadata is not None and "page_rotation" in existing_metadata:
        creation_page_rotation = int(existing_metadata["page_rotation"])
    else:
        creation_page_rotation = page.rotation

    annot: fitz.Annot

    if data.shape_type == ShapeType.LINE:
        p1, p2 = _line_endpoints_from_data(data)
        if has_rotation:
            p1 = _rotate_point(*p1, cx, cy, rotation)
            p2 = _rotate_point(*p2, cx, cy, rotation)
        if page.rotation != 0:
            m = page.derotation_matrix
            p1 = tuple(fitz.Point(p1) * m)
            p2 = tuple(fitz.Point(p2) * m)
        annot = page.add_line_annot(fitz.Point(p1), fitz.Point(p2))
        annot.set_line_ends(_line_ending_code(data.arrow_start), _line_ending_code(data.arrow_end))

    elif data.shape_type == ShapeType.RECTANGLE and not has_rotation:
        r = fitz.Rect(*rect)
        if page.rotation != 0:
            r = r * page.derotation_matrix
        annot = page.add_rect_annot(r)

    elif data.shape_type == ShapeType.ELLIPSE and not has_rotation:
        r = fitz.Rect(*rect)
        if page.rotation != 0:
            r = r * page.derotation_matrix
        annot = page.add_circle_annot(r)

    elif data.shape_type in (ShapeType.RECTANGLE, ShapeType.ELLIPSE, ShapeType.TRIANGLE):
        verts = _compute_shape_vertices(data)
        if page.rotation != 0:
            m = page.derotation_matrix
            verts = [tuple(fitz.Point(v) * m) for v in verts]
        points = [fitz.Point(v) for v in verts]
        annot = page.add_polygon_annot(points)

    elif data.shape_type == ShapeType.BRACKET:
        verts = _compute_shape_vertices(data)
        if page.rotation != 0:
            m = page.derotation_matrix
            verts = [tuple(fitz.Point(v) * m) for v in verts]
        points = [fitz.Point(v) for v in verts]
        annot = page.add_polyline_annot(points)

    else:
        verts = _compute_shape_vertices(data)
        if page.rotation != 0:
            m = page.derotation_matrix
            verts = [tuple(fitz.Point(v) * m) for v in verts]
        points = [fitz.Point(v) for v in verts]
        annot = page.add_polygon_annot(points)

    annot.set_colors(stroke=stroke, fill=fill)
    annot.set_border(width=stroke_width)
    annot.set_opacity(opacity)
    annot.update()
    annot.set_info(subject=_encode_shape_metadata(data, page_rotation=creation_page_rotation))
    return annot


def _extract_shape_data(
    doc: fitz.Document,
    page_num: int,
    annot: fitz.Annot,
) -> ShapeAnnotData | None:
    xref = annot.xref
    info = annot.info
    subject = info.get("subject", "")
    metadata = _decode_shape_metadata(subject)
    if metadata is None:
        return None

    try:
        shape_type = ShapeType(metadata["shape_type"])
    except (KeyError, ValueError):
        return None

    original_rect = metadata.get("original_rect")
    if isinstance(original_rect, list) and len(original_rect) == 4:
        rect = tuple(float(v) for v in original_rect)
    else:
        r = annot.rect
        page = doc[page_num]
        if page.rotation != 0:
            r = fitz.Rect(r) * page.rotation_matrix
        rect = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))

    rotation = float(metadata.get("rotation", 0.0))
    stroke_color = _normalize_color(metadata.get("stroke_color"))
    fill_color = _normalize_color(metadata.get("fill_color"))
    stroke_width = float(metadata.get("stroke_width", 1.0))

    _, ca_value = doc.xref_get_key(xref, "CA")
    if ca_value == "null":
        opacity = 1.0
    else:
        try:
            opacity = float(ca_value)
        except ValueError:
            opacity = float(annot.opacity or 1.0)

    _, name_value = doc.xref_get_key(xref, "NM")
    annotation_id = info.get("id") or (name_value if name_value != "null" else "")

    arrow_start = bool(metadata.get("arrow_start", False))
    arrow_end = bool(metadata.get("arrow_end", False))
    vertices_raw = metadata.get("vertices")
    vertices: tuple[tuple[float, float], ...] = ()
    if isinstance(vertices_raw, list):
        parsed: list[tuple[float, float]] = []
        for v in vertices_raw:
            if isinstance(v, list) and len(v) >= 2:
                parsed.append((float(v[0]), float(v[1])))
        vertices = tuple(parsed)
    bracket_style = str(metadata.get("bracket_style", "square"))
    bracket_size = str(metadata.get("bracket_size", "medium"))
    bracket_both_sides = bool(metadata.get("bracket_both_sides", False))
    bracket_side = str(metadata.get("bracket_side", "left"))
    bracket_orientation = str(metadata.get("bracket_orientation", "vertical"))
    group_id = str(metadata.get("group_id", ""))
    triangle_apex_raw = metadata.get("triangle_apex")
    triangle_apex: tuple[float, float] = (0.5, 0.0)
    if isinstance(triangle_apex_raw, list) and len(triangle_apex_raw) >= 2:
        try:
            ax = min(1.0, max(0.0, float(triangle_apex_raw[0])))
            ay = min(1.0, max(0.0, float(triangle_apex_raw[1])))
            triangle_apex = (ax, ay)
        except (TypeError, ValueError):
            triangle_apex = (0.5, 0.0)

    return ShapeAnnotData(
        page_num=page_num,
        xref=xref,
        rect=rect,
        shape_type=shape_type,
        stroke_color=stroke_color,
        fill_color=fill_color,
        stroke_width=stroke_width,
        opacity=max(0.0, min(1.0, float(opacity))),
        rotation=rotation,
        arrow_start=arrow_start,
        arrow_end=arrow_end,
        bracket_style=bracket_style,
        bracket_size=bracket_size,
        bracket_both_sides=bracket_both_sides,
        bracket_side=bracket_side,
        bracket_orientation=bracket_orientation,
        group_id=group_id,
        vertices=vertices,
        triangle_apex=triangle_apex,
        annotation_id=annotation_id,
        subject=subject,
    )


def _page_numbers_for(doc: fitz.Document, page_num: int | None) -> range | list[int] | None:
    """対象ページ番号の列を返す。page_num が範囲外なら None。"""
    if page_num is None:
        return range(len(doc))
    if 0 <= page_num < len(doc):
        return [page_num]
    return None


def list_shape_annots(pdf_path: str, page_num: int | None = None) -> list[ShapeAnnotData]:
    results: list[ShapeAnnotData] = []
    try:
        with fitz.open(pdf_path) as doc:
            page_numbers = _page_numbers_for(doc, page_num)
            if page_numbers is None:
                return []

            for pn in page_numbers:
                page = doc[pn]
                annots = page.annots(types=_SHAPE_ANNOT_TYPES)
                if annots is None:
                    continue
                for annot in annots:
                    shape_data = _extract_shape_data(doc, pn, annot)
                    if shape_data is not None:
                        results.append(shape_data)
    except Exception:
        logger.debug("list_shape_annots failed: %s", pdf_path, exc_info=True)
    return results


def create_shape_annot(pdf_path: str, data: ShapeAnnotData) -> ShapeAnnotData:
    doc = fitz.open(pdf_path)
    try:
        if data.page_num < 0 or data.page_num >= len(doc):
            raise IndexError(f"page out of range: {data.page_num}")
        page = doc[data.page_num]
        annot = _add_shape_annot_to_page(page, data)
        saved = _extract_shape_data(doc, data.page_num, annot)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError("Failed to extract saved shape annotation")
        return saved
    finally:
        doc.close()


def delete_shape_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is None:
            return False
        if annot.type[0] not in _SHAPE_ANNOT_TYPES:
            return False
        page.delete_annot(annot)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def replace_shape_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: ShapeAnnotData,
) -> ShapeAnnotData:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
        replacement = _add_shape_annot_to_page(page, data)
        saved = _extract_shape_data(doc, page_num, replacement)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError("Failed to extract saved shape annotation")
        return saved
    finally:
        doc.close()


def create_bracket_pair(
    pdf_path: str,
    rect: tuple[float, float, float, float],
    page_num: int,
    *,
    bracket_style: str = "square",
    bracket_size: str = "medium",
    stroke_color: tuple[float, float, float] | None = (0.0, 0.0, 0.0),
    stroke_width: float = 1.0,
    opacity: float = 1.0,
    rotation: float = 0.0,
) -> tuple[ShapeAnnotData, ShapeAnnotData]:
    gid = uuid.uuid4().hex[:12]
    x0, y0, x1, y1 = rect
    w = x1 - x0
    bracket_w = max(8.0, w * 0.15)

    left_rect = (x0, y0, x0 + bracket_w, y1)
    right_rect = (x1 - bracket_w, y0, x1, y1)

    left_data = ShapeAnnotData(
        page_num=page_num, xref=0, rect=left_rect,
        shape_type=ShapeType.BRACKET,
        stroke_color=stroke_color, fill_color=None,
        stroke_width=stroke_width, opacity=opacity, rotation=rotation,
        bracket_style=bracket_style, bracket_size=bracket_size,
        bracket_both_sides=True, bracket_side="left", group_id=gid,
    )
    right_data = ShapeAnnotData(
        page_num=page_num, xref=0, rect=right_rect,
        shape_type=ShapeType.BRACKET,
        stroke_color=stroke_color, fill_color=None,
        stroke_width=stroke_width, opacity=opacity, rotation=rotation,
        bracket_style=bracket_style, bracket_size=bracket_size,
        bracket_both_sides=True, bracket_side="right", group_id=gid,
    )

    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]

        left_annot = _add_shape_annot_to_page(page, left_data)
        left_saved = _extract_shape_data(doc, page_num, left_annot)

        right_annot = _add_shape_annot_to_page(page, right_data)
        right_saved = _extract_shape_data(doc, page_num, right_annot)

        _save_document_in_place(doc, pdf_path)
        if left_saved is None or right_saved is None:
            raise RuntimeError("Failed to extract saved bracket annotations")
        return left_saved, right_saved
    finally:
        doc.close()


# --- Proofreading callout (single FreeTextCallout: text box + leader arrow) -

def _callout_box_attach(
    text_rect: tuple[float, float, float, float],
    target_point: tuple[float, float],
) -> tuple[float, float]:
    """本文ボックスから引き出し線を出す接続点（ボックス上辺/下辺の中央）を返す。

    ターゲットがボックスより下なら下辺、そうでなければ上辺の中央に接続する。
    """
    tx0, ty0, tx1, ty1 = text_rect
    cx = (tx0 + tx1) / 2.0
    ty = float(target_point[1])
    return (cx, ty1) if ty >= ty1 else (cx, ty0)


def create_callout(
    pdf_path: str,
    page_num: int,
    text_rect: tuple[float, float, float, float],
    target_point: tuple[float, float],
    *,
    text: str = "",
    text_color: tuple[float, float, float] = (0.85, 0.0, 0.0),
    fill_color: tuple[float, float, float] | None = (1.0, 1.0, 0.85),
    fontsize: float = 14.0,
    stroke_color: tuple[float, float, float] = (0.85, 0.0, 0.0),
    stroke_width: float = 1.5,
    opacity: float = 1.0,
    bracket_style: str = "curly",
) -> FreeTextAnnotData:
    """校正用の挿入コールアウトを作る。

    本文ボックス＋挿入位置を指す矢印付き引き出し線を、PDF ネイティブの
    FreeTextCallout（単一の FreeText 注釈）として 1 個生成する。

    ``bracket_style`` は後方互換のため引数として受け付けるが使用しない。
    """
    tx, ty = float(target_point[0]), float(target_point[1])
    box_attach = _callout_box_attach(text_rect, (tx, ty))
    callout_line = ((tx, ty), box_attach)

    text_data = FreeTextAnnotData(
        page_num=page_num, xref=0, rect=text_rect,
        content=text, fontsize=fontsize,
        text_color=text_color, fill_color=fill_color,
        border_color=stroke_color, border_width=max(1.0, float(stroke_width)),
        opacity=opacity,
        callout_line=callout_line, callout_target=(tx, ty),
    )

    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        text_annot = _add_freetext_annot_to_page(page, text_data)
        text_saved = _extract_freetext_data(doc, page_num, text_annot)
        _save_document_in_place(doc, pdf_path)
        if text_saved is None:
            raise RuntimeError("Failed to extract saved callout annotation")
        return text_saved
    finally:
        doc.close()


def list_annot_group(pdf_path: str, page_num: int, group_id: str) -> list[int]:
    """指定 group_id を持つ注釈（FreeText/Shape）の xref 一覧を返す。"""
    if not group_id:
        return []
    xrefs: list[int] = []
    for annot in list_freetext_annots(pdf_path, page_num):
        if annot.group_id == group_id:
            xrefs.append(annot.xref)
    for annot in list_shape_annots(pdf_path, page_num):
        if annot.group_id == group_id:
            xrefs.append(annot.xref)
    return xrefs


def delete_annot_group(pdf_path: str, page_num: int, group_id: str) -> int:
    """指定 group_id の注釈をまとめて削除し、削除数を返す。"""
    if not group_id:
        return 0
    doc = fitz.open(pdf_path)
    deleted = 0
    try:
        if page_num < 0 or page_num >= len(doc):
            return 0
        page = doc[page_num]
        for annot in list(page.annots() or []):
            subject = annot.info.get("subject", "")
            metadata = _decode_subject_metadata(subject) or _decode_shape_metadata(subject)
            if metadata is not None and str(metadata.get("group_id", "")) == group_id:
                page.delete_annot(annot)
                deleted += 1
        if deleted:
            _save_document_in_place(doc, pdf_path)
        return deleted
    finally:
        doc.close()


# --- Text markup annotations (highlight / underline / strikeout) ---------

_MARKUP_TYPE_TO_PDF = {
    MarkupType.HIGHLIGHT: fitz.PDF_ANNOT_HIGHLIGHT,
    MarkupType.UNDERLINE: fitz.PDF_ANNOT_UNDERLINE,
    MarkupType.STRIKEOUT: fitz.PDF_ANNOT_STRIKE_OUT,
}
_MARKUP_ANNOT_TYPES = list(_MARKUP_TYPE_TO_PDF.values())


def _encode_markup_metadata(data: TextMarkupAnnotData, *, page_rotation: int = 0) -> str:
    payload: dict = {
        "markup_type": data.markup_type.value,
        "color": list(data.color),
        "opacity": float(data.opacity),
        "quads": [[float(c) for c in quad] for quad in data.quads],
        "page_rotation": int(page_rotation),
    }
    return JUSTICEPDF_MARKUP_SUBJECT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _decode_markup_metadata(subject: str) -> dict | None:
    if not subject or not subject.startswith(JUSTICEPDF_MARKUP_SUBJECT_PREFIX):
        return None
    raw = subject[len(JUSTICEPDF_MARKUP_SUBJECT_PREFIX):]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _add_markup_annot_to_page(page: fitz.Page, data: TextMarkupAnnotData) -> fitz.Annot:
    quads: list[fitz.Quad] = []
    for rect in data.quads:
        quad = fitz.Rect(*rect).quad
        if page.rotation != 0:
            quad = quad * page.derotation_matrix
        quads.append(quad)
    if not quads:
        raise ValueError("markup annotation requires at least one quad")

    existing_metadata = _decode_markup_metadata(data.subject)
    if existing_metadata is not None and "page_rotation" in existing_metadata:
        creation_page_rotation = int(existing_metadata["page_rotation"])
    else:
        creation_page_rotation = page.rotation

    pdf_type = _MARKUP_TYPE_TO_PDF[data.markup_type]
    if pdf_type == fitz.PDF_ANNOT_HIGHLIGHT:
        annot = page.add_highlight_annot(quads=quads)
    elif pdf_type == fitz.PDF_ANNOT_UNDERLINE:
        annot = page.add_underline_annot(quads=quads)
    else:
        annot = page.add_strikeout_annot(quads=quads)

    annot.set_colors(stroke=list(data.color))
    annot.set_opacity(max(0.0, min(1.0, float(data.opacity))))
    annot.update()
    annot.set_info(subject=_encode_markup_metadata(data, page_rotation=creation_page_rotation))
    return annot


def _extract_markup_data(
    doc: fitz.Document,
    page_num: int,
    annot: fitz.Annot,
) -> TextMarkupAnnotData | None:
    xref = annot.xref
    info = annot.info
    subject = info.get("subject", "")
    metadata = _decode_markup_metadata(subject)
    if metadata is None:
        return None

    try:
        markup_type = MarkupType(metadata["markup_type"])
    except (KeyError, ValueError):
        return None

    quads_raw = metadata.get("quads")
    quads: list[tuple[float, float, float, float]] = []
    if isinstance(quads_raw, list):
        for quad in quads_raw:
            if isinstance(quad, list) and len(quad) == 4:
                quads.append(tuple(float(c) for c in quad))
    if not quads:
        return None

    color = _normalize_color(metadata.get("color")) or (1.0, 1.0, 0.0)

    _, ca_value = doc.xref_get_key(xref, "CA")
    if ca_value == "null":
        opacity = float(metadata.get("opacity", 1.0))
    else:
        try:
            opacity = float(ca_value)
        except ValueError:
            opacity = float(metadata.get("opacity", 1.0))

    _, name_value = doc.xref_get_key(xref, "NM")
    annotation_id = info.get("id") or (name_value if name_value != "null" else "")

    return TextMarkupAnnotData(
        page_num=page_num,
        xref=xref,
        quads=tuple(quads),
        markup_type=markup_type,
        color=color,
        opacity=max(0.0, min(1.0, float(opacity))),
        annotation_id=annotation_id,
        subject=subject,
    )


def list_markup_annots(pdf_path: str, page_num: int | None = None) -> list[TextMarkupAnnotData]:
    """Return JusticePDF text-markup annotations for one page or the whole document."""
    results: list[TextMarkupAnnotData] = []
    try:
        with fitz.open(pdf_path) as doc:
            page_numbers = _page_numbers_for(doc, page_num)
            if page_numbers is None:
                return []

            for pn in page_numbers:
                page = doc[pn]
                annots = page.annots(types=_MARKUP_ANNOT_TYPES)
                if annots is None:
                    continue
                for annot in annots:
                    markup_data = _extract_markup_data(doc, pn, annot)
                    if markup_data is not None:
                        results.append(markup_data)
    except Exception:
        logger.debug("list_markup_annots failed: %s", pdf_path, exc_info=True)
    return results


def create_markup_annot(pdf_path: str, data: TextMarkupAnnotData) -> TextMarkupAnnotData:
    doc = fitz.open(pdf_path)
    try:
        if data.page_num < 0 or data.page_num >= len(doc):
            raise IndexError(f"page out of range: {data.page_num}")
        page = doc[data.page_num]
        annot = _add_markup_annot_to_page(page, data)
        saved = _extract_markup_data(doc, data.page_num, annot)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError("Failed to extract saved markup annotation")
        return saved
    finally:
        doc.close()


def delete_markup_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is None:
            return False
        if annot.type[0] not in _MARKUP_ANNOT_TYPES:
            return False
        page.delete_annot(annot)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def replace_markup_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: TextMarkupAnnotData,
) -> TextMarkupAnnotData:
    """Replace a markup annotation (used for color / opacity changes)."""
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
        replacement = _add_markup_annot_to_page(page, data)
        saved = _extract_markup_data(doc, page_num, replacement)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError("Failed to extract saved markup annotation")
        return saved
    finally:
        doc.close()


# --- Sticky note (comment) annotations -----------------------------------

def _encode_note_metadata(data: "NoteAnnotData", *, page_rotation: int = 0) -> str:
    payload: dict = {
        "color": list(data.color),
        "icon": data.icon,
        "opacity": float(data.opacity),
        "point": [float(data.point[0]), float(data.point[1])],
        "page_rotation": int(page_rotation),
    }
    return JUSTICEPDF_NOTE_SUBJECT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _decode_note_metadata(subject: str) -> dict | None:
    if not subject or not subject.startswith(JUSTICEPDF_NOTE_SUBJECT_PREFIX):
        return None
    raw = subject[len(JUSTICEPDF_NOTE_SUBJECT_PREFIX):]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _add_note_annot_to_page(page: fitz.Page, data: "NoteAnnotData") -> fitz.Annot:
    point = fitz.Point(data.point[0], data.point[1])
    if page.rotation != 0:
        point = point * page.derotation_matrix

    existing_metadata = _decode_note_metadata(data.subject)
    if existing_metadata is not None and "page_rotation" in existing_metadata:
        creation_page_rotation = int(existing_metadata["page_rotation"])
    else:
        creation_page_rotation = page.rotation

    annot = page.add_text_annot(point, data.content or "", icon=data.icon or "Note")
    annot.set_colors(stroke=list(data.color))
    annot.set_opacity(max(0.0, min(1.0, float(data.opacity))))
    annot.update()
    annot.set_info(content=data.content or "", subject=_encode_note_metadata(data, page_rotation=creation_page_rotation))
    return annot


def _extract_note_data(
    doc: fitz.Document,
    page_num: int,
    annot: fitz.Annot,
) -> "NoteAnnotData | None":
    xref = annot.xref
    info = annot.info
    subject = info.get("subject", "")
    metadata = _decode_note_metadata(subject)
    if metadata is None:
        return None

    r = annot.rect
    page = doc[page_num]
    if page.rotation != 0:
        r = fitz.Rect(r) * page.rotation_matrix
    point = (float(r.x0), float(r.y0))

    content = info.get("content", "") or ""
    color = _normalize_color(metadata.get("color")) or (1.0, 0.92, 0.23)
    icon = str(metadata.get("icon", "Note")) or "Note"

    _, ca_value = doc.xref_get_key(xref, "CA")
    if ca_value == "null":
        opacity = float(metadata.get("opacity", 1.0))
    else:
        try:
            opacity = float(ca_value)
        except ValueError:
            opacity = float(metadata.get("opacity", 1.0))

    _, name_value = doc.xref_get_key(xref, "NM")
    annotation_id = info.get("id") or (name_value if name_value != "null" else "")

    return NoteAnnotData(
        page_num=page_num,
        xref=xref,
        point=point,
        content=content,
        color=color,
        icon=icon,
        opacity=max(0.0, min(1.0, float(opacity))),
        annotation_id=annotation_id,
        subject=subject,
    )


def list_note_annots(pdf_path: str, page_num: int | None = None) -> list["NoteAnnotData"]:
    """Return JusticePDF sticky-note annotations for one page or the whole document."""
    results: list[NoteAnnotData] = []
    try:
        with fitz.open(pdf_path) as doc:
            page_numbers = _page_numbers_for(doc, page_num)
            if page_numbers is None:
                return []

            for pn in page_numbers:
                page = doc[pn]
                annots = page.annots(types=[fitz.PDF_ANNOT_TEXT])
                if annots is None:
                    continue
                for annot in annots:
                    note_data = _extract_note_data(doc, pn, annot)
                    if note_data is not None:
                        results.append(note_data)
    except Exception:
        logger.debug("list_note_annots failed: %s", pdf_path, exc_info=True)
    return results


def create_note_annot(pdf_path: str, data: "NoteAnnotData") -> "NoteAnnotData":
    doc = fitz.open(pdf_path)
    try:
        if data.page_num < 0 or data.page_num >= len(doc):
            raise IndexError(f"page out of range: {data.page_num}")
        page = doc[data.page_num]
        annot = _add_note_annot_to_page(page, data)
        saved = _extract_note_data(doc, data.page_num, annot)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError("Failed to extract saved note annotation")
        return saved
    finally:
        doc.close()


def delete_note_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is None or annot.type[0] != fitz.PDF_ANNOT_TEXT:
            return False
        page.delete_annot(annot)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def replace_note_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: "NoteAnnotData",
) -> "NoteAnnotData":
    """Replace a note annotation (content / color / position changes)."""
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
        replacement = _add_note_annot_to_page(page, data)
        saved = _extract_note_data(doc, page_num, replacement)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError("Failed to extract saved note annotation")
        return saved
    finally:
        doc.close()


def _get_page_annot_xref_order(doc: fitz.Document, page_num: int) -> list[int]:
    page = doc[page_num]
    kind, value = doc.xref_get_key(page.xref, "Annots")
    if kind != "array" or not value:
        return []
    return [int(m.group(1)) for m in re.finditer(r"(\d+)\s+\d+\s+R", value)]


def _set_page_annot_xref_order(doc: fitz.Document, page_num: int, order: list[int]) -> None:
    page = doc[page_num]
    new_array = "[" + " ".join(f"{x} 0 R" for x in order) + "]"
    doc.xref_set_key(page.xref, "Annots", new_array)


def get_annot_xref_order(pdf_path: str, page_num: int) -> list[int]:
    try:
        with fitz.open(pdf_path) as doc:
            if page_num < 0 or page_num >= len(doc):
                return []
            return _get_page_annot_xref_order(doc, page_num)
    except Exception:
        logger.debug("get_annot_xref_order failed: %s", pdf_path, exc_info=True)
        return []


def set_annot_xref_order(pdf_path: str, page_num: int, order: list[int]) -> bool:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        current = _get_page_annot_xref_order(doc, page_num)
        if sorted(current) != sorted(order):
            return False
        if current == order:
            return True
        _set_page_annot_xref_order(doc, page_num, order)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def reorder_annot_on_page(pdf_path: str, page_num: int, xref: int, mode: str) -> bool:
    if mode not in ("front", "back", "forward", "backward"):
        return False
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        order = _get_page_annot_xref_order(doc, page_num)
        if xref not in order:
            return False
        idx = order.index(xref)
        if mode == "front":
            if idx == len(order) - 1:
                return False
            order.append(order.pop(idx))
        elif mode == "back":
            if idx == 0:
                return False
            order.insert(0, order.pop(idx))
        elif mode == "forward":
            if idx >= len(order) - 1:
                return False
            order[idx], order[idx + 1] = order[idx + 1], order[idx]
        else:
            if idx <= 0:
                return False
            order[idx], order[idx - 1] = order[idx - 1], order[idx]
        _set_page_annot_xref_order(doc, page_num, order)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def _get_file_cache_token(pdf_path: str) -> tuple[int, int, int]:
    """Return a filesystem-based token that changes when the file instance changes."""
    try:
        stat_result = os.stat(pdf_path)
    except OSError:
        return (0, 0, 0)
    return (
        int(getattr(stat_result, "st_mtime_ns", 0)),
        int(stat_result.st_size),
        int(getattr(stat_result, "st_ctime_ns", 0)),
    )


def _pixmap_to_qpixmap(pix: "fitz.Pixmap") -> QPixmap:
    """Convert a PyMuPDF Pixmap to QPixmap safely.

    QImage(data, ...) normally references the provided memory. Since pix.samples
    is backed by pix's internal buffer, we force a deep copy to avoid dangling
    references after pix is freed.
    """
    img = QImage(
        pix.samples,
        pix.width,
        pix.height,
        pix.stride,
        QImage.Format.Format_RGB888,
    ).copy()
    return QPixmap.fromImage(img)


def get_pdf_card_info(pdf_path: str, size: int = 128) -> tuple[QPixmap, int]:
    """Get (thumbnail, page_count) for main-grid cards with a single PDF open."""
    try:
        cache_token = _get_file_cache_token(pdf_path)
        with fitz.open(pdf_path) as doc:
            page_count = len(doc)
            if page_count == 0:
                return QPixmap(), 0
            cache_key = (pdf_path, 0, size, None, True, cache_token)
            cached = _pixmap_cache.get(cache_key)
            if cached is not None:
                return cached, page_count
            page = doc[0]
            zoom = size / max(page.rect.width, page.rect.height)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            qpix = _pixmap_to_qpixmap(pix)
            _pixmap_cache.put(cache_key, qpix)
            return qpix, page_count
    except Exception:
        logger.debug("get_pdf_card_info failed: %s", pdf_path, exc_info=True)
        return QPixmap(), 0


def _render_page_pixmap(
    pdf_path: str,
    page_num: int,
    *,
    size: int | None = None,
    zoom: float | None = None,
    annots: bool = True,
) -> QPixmap:
    """Render a PDF page to a pixmap with either size-based or zoom-based scaling."""
    try:
        cache_token = _get_file_cache_token(pdf_path)
        cache_key = (
            pdf_path,
            page_num,
            size,
            round(zoom, 4) if zoom is not None else None,
            bool(annots),
            cache_token,
        )
        cached = _pixmap_cache.get(cache_key)
        if cached is not None:
            return cached
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc) or page_num < 0:
                return QPixmap()
            page = doc[page_num]
            if size is not None:
                scale = size / max(page.rect.width, page.rect.height)
            else:
                scale = zoom or 1.0
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, annots=annots)
        qpix = _pixmap_to_qpixmap(pix)
        _pixmap_cache.put(cache_key, qpix)
        return qpix
    except Exception:
        logger.debug(
            "_render_page_pixmap failed: pdf=%s page=%s size=%s zoom=%s",
            pdf_path,
            page_num,
            size,
            zoom,
            exc_info=True,
        )
        return QPixmap()


def get_thumbnail(pdf_path: str, size: int = 128) -> QPixmap:
    """Generate a thumbnail of the first page of a PDF."""
    return _render_page_pixmap(pdf_path, 0, size=size)


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    try:
        with fitz.open(pdf_path) as doc:
            return len(doc)
    except Exception:
        logger.debug("get_page_count failed: %s", pdf_path, exc_info=True)
        return 0


def get_page_size_points(pdf_path: str, page_index: int) -> "tuple[float, float]":
    """Return a page's (width, height) in PDF points, or (0.0, 0.0) on error."""
    try:
        with fitz.open(pdf_path) as doc:
            if 0 <= page_index < len(doc):
                r = doc[page_index].rect
                return float(r.width), float(r.height)
    except Exception:
        logger.debug("get_page_size_points failed: %s p%s", pdf_path, page_index, exc_info=True)
    return 0.0, 0.0


def create_empty_pdf(pdf_path: str) -> None:
    """Create an empty PDF with 1 blank page.

    Note: PyMuPDF cannot save PDFs with 0 pages,
    so this creates a PDF with a single blank page.
    """
    doc = fitz.open()
    doc.new_page()  # Add one blank page
    doc.save(pdf_path)
    doc.close()


def get_page_thumbnail(pdf_path: str, page_num: int, size: int = 128) -> QPixmap:
    """Generate a thumbnail of a specific page."""
    return _render_page_pixmap(pdf_path, page_num, size=size)


def get_page_pixmap(pdf_path: str, page_num: int, zoom: float = 1.0, *, annots: bool = True) -> QPixmap:
    """Render a page at the given zoom factor."""
    return _render_page_pixmap(pdf_path, page_num, zoom=zoom, annots=annots)


def render_page_thumbnails_batch(pdf_path: str, page_nums: list[int], size: int = 128) -> dict[int, QPixmap]:
    """Batch-render thumbnails for multiple pages, using cache where possible.

    Opens the PDF only once for all cache-missed pages.
    """
    cache_token = _get_file_cache_token(pdf_path)
    result: dict[int, QPixmap] = {}
    miss_pages: list[int] = []

    for pn in page_nums:
        cache_key = (pdf_path, pn, size, None, True, cache_token)
        cached = _pixmap_cache.get(cache_key)
        if cached is not None:
            result[pn] = cached
        else:
            miss_pages.append(pn)

    if miss_pages:
        try:
            with fitz.open(pdf_path) as doc:
                for pn in miss_pages:
                    if pn < 0 or pn >= len(doc):
                        result[pn] = QPixmap()
                        continue
                    page = doc[pn]
                    scale = size / max(page.rect.width, page.rect.height)
                    mat = fitz.Matrix(scale, scale)
                    pix = page.get_pixmap(matrix=mat)
                    qpix = _pixmap_to_qpixmap(pix)
                    cache_key = (pdf_path, pn, size, None, True, cache_token)
                    _pixmap_cache.put(cache_key, qpix)
                    result[pn] = qpix
        except Exception:
            logger.debug("render_page_thumbnails_batch failed: %s", pdf_path, exc_info=True)
            for pn in miss_pages:
                if pn not in result:
                    result[pn] = QPixmap()

    return result


def get_page_words(pdf_path: str, page_num: int) -> list[tuple]:
    """Extract word-level text with coordinates for a page."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            return page.get_text("words")
    except Exception:
        logger.debug(
            "get_page_words failed: pdf=%s page=%s",
            pdf_path,
            page_num,
            exc_info=True,
        )
        return []


def get_page_chars(pdf_path: str, page_num: int) -> list[dict]:
    """Extract character-level text with coordinates for a page.

    Returns a flat list in reading order. Each entry::

        {"c": str, "bbox": (x0, y0, x1, y1), "line_id": int,
         "wmode": int, "dir": (cos, sin)}

    Spaces are kept (needed to reproduce gaps faithfully); other control
    characters are dropped. ``line_id`` increments globally across the page so
    consecutive entries with the same id belong to the same line.
    """
    chars: list[dict] = []
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            raw = page.get_text("rawdict")
            line_id = 0
            for block in raw.get("blocks", []):
                # type 0 = text block; skip image blocks (type 1).
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    wmode = line.get("wmode", 0)
                    direction = line.get("dir", (1.0, 0.0))
                    has_char = False
                    for span in line.get("spans", []):
                        for ch in span.get("chars", []):
                            text = ch.get("c", "")
                            if not text:
                                continue
                            # Drop control characters but keep spaces.
                            if text != " " and ord(text[0]) < 0x20:
                                continue
                            bbox = ch.get("bbox")
                            if bbox is None:
                                continue
                            chars.append(
                                {
                                    "c": text,
                                    "bbox": (
                                        float(bbox[0]),
                                        float(bbox[1]),
                                        float(bbox[2]),
                                        float(bbox[3]),
                                    ),
                                    "line_id": line_id,
                                    "wmode": int(wmode),
                                    "dir": (
                                        float(direction[0]),
                                        float(direction[1]),
                                    ),
                                }
                            )
                            has_char = True
                    if has_char:
                        line_id += 1
            return chars
    except Exception:
        logger.debug(
            "get_page_chars failed: pdf=%s page=%s",
            pdf_path,
            page_num,
            exc_info=True,
        )
        return []


def search_text_in_pdf(pdf_path: str, query: str) -> dict[int, list]:
    """Search query text across all pages.

    Returns {page_num: [fitz.Rect, ...]} for pages with at least one hit.
    Pages without hits are not included. PyMuPDF's search_for is case-insensitive
    by default.
    """
    if not query:
        return {}
    results: dict[int, list] = {}
    try:
        with fitz.open(pdf_path) as doc:
            for i in range(len(doc)):
                page = doc[i]
                rects = page.search_for(query)
                if rects:
                    results[i] = list(rects)
    except Exception:
        logger.debug(
            "search_text_in_pdf failed: pdf=%s query=%r",
            pdf_path,
            query,
            exc_info=True,
        )
        return {}
    return results


def get_page_links(pdf_path: str, page_num: int) -> list[dict]:
    """Extract link annotations with rectangles for a page."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_num >= len(doc):
                return []
            page = doc[page_num]
            links = page.get_links()
        normalized: list[dict] = []
        for link in links:
            rect = link.get("from")
            if rect is None:
                rect_tuple = None
            elif hasattr(rect, "x0"):
                rect_tuple = (rect.x0, rect.y0, rect.x1, rect.y1)
            else:
                rect_tuple = tuple(rect)
            item = dict(link)
            item["from"] = rect_tuple
            normalized.append(item)
        return normalized
    except Exception:
        logger.debug(
            "get_page_links failed: pdf=%s page=%s",
            pdf_path,
            page_num,
            exc_info=True,
        )
        return []


def list_freetext_annots(pdf_path: str, page_num: int | None = None) -> list[FreeTextAnnotData]:
    """Return normalized FreeText annotations for one page or the whole document."""
    results: list[FreeTextAnnotData] = []
    try:
        with fitz.open(pdf_path) as doc:
            page_numbers = _page_numbers_for(doc, page_num)
            if page_numbers is None:
                return []

            for pn in page_numbers:
                page = doc[pn]
                annots = page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT])
                if annots is None:
                    continue
                for annot in annots:
                    results.append(_extract_freetext_data(doc, pn, annot))
    except Exception:
        logger.debug("list_freetext_annots failed: %s", pdf_path, exc_info=True)
    return results


def create_freetext_annot(pdf_path: str, data: FreeTextAnnotData) -> FreeTextAnnotData:
    """Create a new FreeText annotation and return the normalized saved form."""
    doc = fitz.open(pdf_path)
    try:
        if data.page_num < 0 or data.page_num >= len(doc):
            raise IndexError(f"page out of range: {data.page_num}")
        page = doc[data.page_num]
        annot = _add_freetext_annot_to_page(page, data)
        saved = _extract_freetext_data(doc, data.page_num, annot)
        _save_document_in_place(doc, pdf_path)
        return saved
    finally:
        doc.close()


def delete_freetext_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    """Delete a FreeText annotation by page number and xref."""
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is None or annot.type[0] != fitz.PDF_ANNOT_FREE_TEXT:
            return False
        page.delete_annot(annot)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def replace_freetext_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: FreeTextAnnotData,
) -> FreeTextAnnotData:
    """Replace an existing FreeText annotation and return the saved replacement."""
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
        replacement = _add_freetext_annot_to_page(page, data)
        saved = _extract_freetext_data(doc, page_num, replacement)
        _save_document_in_place(doc, pdf_path)
        return saved
    finally:
        doc.close()


def merge_pdfs(
    output_path: str, pdf_paths: list[str], *, add_file_bookmarks: bool = False
) -> None:
    """Merge multiple PDFs into one.

    add_file_bookmarks=True のとき、結合した各ファイルの開始ページにファイル名の
    しおり(アウトライン)を level 1 で付け、各ファイルが元々持つしおりはその子として残す。
    ファイル名しおりは常にフラット(全ファイルが level 1)に並ぶ。
    """
    output_doc = fitz.open()
    entries: list[TocEntry] = []
    start = 0
    for path in pdf_paths:
        with fitz.open(path) as src_doc:
            count = len(src_doc)
            sub = src_doc.get_toc(simple=True) if add_file_bookmarks else []
            output_doc.insert_pdf(src_doc)
        if add_file_bookmarks and count > 0:
            entries.extend(_file_toc_entries(_file_bookmark_title(path), start, sub))
        start += count
    if add_file_bookmarks and entries:
        entries = normalize_toc(entries, page_count=len(output_doc))
        output_doc.set_toc([[e.level, e.title, e.page] for e in entries])
    output_doc.save(output_path)
    output_doc.close()


def merge_pdfs_in_place(
    dest_path: str,
    pdf_paths: list[str],
    *,
    insert_at: int | None = None,
) -> None:
    """Merge PDFs into an existing destination file in place.

    If insert_at is None, append to end. Otherwise insert sequentially starting at insert_at.
    Uses incremental save when possible; falls back to full save to temp.

    しおりは「フラットな単純引継ぎ」: 結合先(dest)自身の TOC と、挿入する各ファイルが
    元々持つ TOC を、ページ番号だけオフセットして連結する(各エントリの level はそのまま
    保持)。ファイル名の親しおりは付けないので、1ファイルずつ繰り返し重ねても階層は深く
    ならず、内部の親子関係(例: 章とその節)はそのまま引き継がれる。
    insert_at は None(末尾追加)または 0(先頭挿入)のときのみしおりを書き込む。
    """
    if not pdf_paths:
        return

    dest_doc = fitz.open(dest_path)
    try:
        dest_orig_count = len(dest_doc)
        dest_orig_toc = dest_doc.get_toc(simple=True)
        source_entries: list[TocEntry] = []
        if insert_at is None:
            start = dest_orig_count
            for path in pdf_paths:
                if path == dest_path:
                    continue
                with fitz.open(path) as src_doc:
                    count = len(src_doc)
                    sub = src_doc.get_toc(simple=True)
                    dest_doc.insert_pdf(src_doc)
                if count > 0 and sub:
                    source_entries.extend(_offset_toc_entries(sub, start))
                start += count
            dest_start = 0
        else:
            idx = insert_at
            for path in pdf_paths:
                if path == dest_path:
                    continue
                with fitz.open(path) as src_doc:
                    count = len(src_doc)
                    sub = src_doc.get_toc(simple=True)
                    dest_doc.insert_pdf(src_doc, start_at=idx)
                if count > 0 and sub:
                    source_entries.extend(_offset_toc_entries(sub, idx))
                idx += count
            total_inserted = idx - insert_at
            # 先頭挿入(insert_at==0)のとき、結合先の元ページは挿入分だけ後ろへずれる
            dest_start = total_inserted if insert_at == 0 else 0

        # しおり書き込みは insert_at が None / 0 のときのみ(結合先ページが連続するケース)
        if insert_at in (None, 0):
            entries = list(source_entries)
            if dest_orig_count > 0 and dest_orig_toc:
                # 結合先の既存しおりも level はそのまま、ページだけずらして引き継ぐ
                entries.extend(_offset_toc_entries(dest_orig_toc, dest_start))
            if entries:
                # ページ順に並べ替え(安定ソートなので同ページ内の親→子順は保たれる)
                entries.sort(key=lambda e: e.page)
                entries = normalize_toc(entries, page_count=len(dest_doc))
                dest_doc.set_toc([[e.level, e.title, e.page] for e in entries])
        _save_document_in_place(dest_doc, dest_path, incremental=True)
    finally:
        dest_doc.close()


def merge_paths_to_pdf(output_path: str, paths: list[str]) -> int:
    """選択したファイル/フォルダを1つのPDFに結合し、フォルダ構成を反映した階層しおりを付ける。

    - ``paths`` の各要素はファイル(.pdf)またはフォルダ。与えられた順に処理する
      (トップレベルの並び順は呼び出し側が決める)。
    - フォルダはしおりの見出し(その階層)になり、中身(.pdf とサブフォルダ)を名前順に
      子として再帰的に並べる。入れ子フォルダはさらに深い階層になる。
    - フォルダ見出しは、その配下で最初に現れるページを指す。
    - ファイルはファイル名の見出しになり、そのファイルが元々持つしおりを子として
      ぶら下げる(内部の階層もそのまま保持)。
    - PDF ページを1ページも含まないフォルダは見出しごと省略する。
    - 戻り値は結合したページ総数。0 のとき出力ファイルは作成されない。
    """
    output_doc = fitz.open()

    def build(path: str, level: int) -> list[TocEntry]:
        """``path`` 配下のページを output_doc に挿入しつつ、しおり項目を返す。"""
        if os.path.isdir(path):
            start0 = len(output_doc)
            child_entries: list[TocEntry] = []
            try:
                names = sorted(os.listdir(path), key=str.lower)
            except OSError:
                names = []
            for name in names:
                full = os.path.join(path, name)
                if os.path.isdir(full) or name.lower().endswith(".pdf"):
                    child_entries.extend(build(full, level + 1))
            if len(output_doc) <= start0:
                return []  # PDF ページを含まない空フォルダは省略
            title = os.path.basename(os.path.normpath(path)) or path
            return [TocEntry(level=level, title=title, page=start0 + 1), *child_entries]

        # ファイル(.pdf のみ): ファイル名を見出し(level)に、ファイルが元々持つしおりは
        # その子(level + 内部level)としてページをオフセットしてぶら下げる。
        if not path.lower().endswith(".pdf"):
            return []
        try:
            with fitz.open(path) as src_doc:
                count = len(src_doc)
                if count == 0:
                    return []
                start0 = len(output_doc)
                sub = src_doc.get_toc(simple=True)
                output_doc.insert_pdf(src_doc)
        except Exception:
            logger.warning("結合をスキップしました(開けません): %s", path)
            return []
        entries = [TocEntry(level=level, title=_file_bookmark_title(path), page=start0 + 1)]
        for sub_level, sub_title, sub_page in sub:
            entries.append(
                TocEntry(
                    level=level + int(sub_level),
                    title=str(sub_title),
                    page=int(sub_page) + start0,
                )
            )
        return entries

    try:
        entries: list[TocEntry] = []
        for path in paths:
            if os.path.exists(path):
                entries.extend(build(path, 1))
        total = len(output_doc)
        if total == 0:
            return 0
        entries = normalize_toc(entries, page_count=total)
        output_doc.set_toc([[e.level, e.title, e.page] for e in entries])
        output_doc.save(output_path)
        return total
    finally:
        output_doc.close()


def extract_pages(src_path: str, output_path: str, page_indices: list[int]) -> bool:
    """Extract specific pages from a PDF to a new file.

    Returns:
        True if extraction succeeded, False if no pages to extract.
    """
    src_doc = fitz.open(src_path)
    output_doc = fitz.open()
    for idx in page_indices:
        if 0 <= idx < len(src_doc):
            output_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)

    # Check if output has any pages
    if len(output_doc) == 0:
        output_doc.close()
        src_doc.close()
        return False

    output_doc.save(output_path)
    output_doc.close()
    src_doc.close()
    return True


def remove_pages(pdf_path: str, page_indices: list[int]) -> bool:
    """Remove specific pages from a PDF (in place).

    Returns:
        True if the file was deleted (all pages removed), False otherwise.
    """
    from send2trash import send2trash

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pages_to_remove = [idx for idx in page_indices if 0 <= idx < total_pages]

    if len(pages_to_remove) >= total_pages:
        # All pages removed - delete the file
        doc.close()
        send2trash(pdf_path)
        return True

    for idx in sorted(pages_to_remove, reverse=True):
        doc.delete_page(idx)
    try:
        _save_document_in_place(doc, pdf_path, incremental=True)
        return False
    finally:
        doc.close()


def rotate_pages(pdf_path: str, page_indices: list[int], angle: int = 90) -> None:
    """Rotate specific pages in a PDF (in place)."""
    doc = fitz.open(pdf_path)
    try:
        for idx in page_indices:
            if 0 <= idx < len(doc):
                page = doc[idx]
                page.set_rotation((page.rotation + angle) % 360)
        _save_document_in_place(doc, pdf_path, incremental=True)
    finally:
        doc.close()


def reorder_pages(pdf_path: str, new_order: list[int]) -> None:
    """Reorder pages in a PDF (in place)."""
    doc = fitz.open(pdf_path)
    try:
        doc.select(new_order)
        _save_document_in_place(doc, pdf_path, incremental=True)
    finally:
        doc.close()


def insert_pages(dest_path: str, src_path: str, insert_indices: list[int]) -> None:
    """Insert pages from src_path into dest_path at specified indices.
    
    Args:
        dest_path: Destination PDF path
        src_path: Source PDF path containing pages to insert
        insert_indices: List of positions where each page should be inserted
    """
    dest_doc = fitz.open(dest_path)
    src_doc = fitz.open(src_path)
    try:
        # Insert pages in reverse order to maintain correct indices
        for i in reversed(range(len(src_doc))):
            if i < len(insert_indices):
                insert_at = insert_indices[i]
                dest_doc.insert_pdf(src_doc, from_page=i, to_page=i, start_at=insert_at)

        _save_document_in_place(dest_doc, dest_path, incremental=True)
    finally:
        dest_doc.close()
        src_doc.close()


def _render_page_to_image_bytes(
    page: fitz.Page,
    dpi: int,
    *,
    image_format: str = "png",
    jpeg_quality: int = 75,
) -> tuple[bytes, str]:
    """Render a page to encoded image bytes.

    Args:
        page: Source page.
        dpi: Resolution for rasterization.
        image_format: "png" (lossless) or "jpeg"/"jpg" (lossy).
        jpeg_quality: JPEG quality (1-100); ignored for PNG.

    Returns:
        ``(data, ext)`` where ``ext`` is ".jpg" or ".png".
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0))
    if image_format.lower() in ("jpeg", "jpg"):
        if pix.alpha:  # JPEG cannot carry an alpha channel
            pix = fitz.Pixmap(pix, 0)
        return pix.tobytes("jpeg", jpg_quality=jpeg_quality), ".jpg"
    return pix.tobytes("png"), ".png"


def export_pages_as_images(
    pdf_path: str,
    output_dir: str,
    fmt: str = "png",
    dpi: int = 150,
    quality: int = 85,
    page_indices: list[int] | None = None,
) -> list[str]:
    """Export PDF pages as image files.

    Args:
        pdf_path: Source PDF file path.
        output_dir: Directory to save images.
        fmt: Image format ("png" or "jpeg").
        dpi: Resolution in DPI.
        quality: JPEG quality (1-100). Ignored for PNG.
        page_indices: Pages to export (0-based). None means all pages.

    Returns:
        List of created image file paths.
    """
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    created: list[str] = []

    doc = fitz.open(pdf_path)
    try:
        indices = page_indices if page_indices is not None else list(range(len(doc)))
        for page_num in indices:
            data, ext = _render_page_to_image_bytes(
                doc[page_num], dpi, image_format=fmt, jpeg_quality=quality
            )
            out_path = os.path.join(output_dir, f"{base}_p{page_num + 1}{ext}")
            with open(out_path, "wb") as f:
                f.write(data)
            created.append(out_path)
    finally:
        doc.close()

    return created


def images_to_pdf(image_paths: list[str], output_path: str) -> None:
    """Create a PDF from image files. Each image becomes one page.

    Args:
        image_paths: List of image file paths.
        output_path: Destination PDF path.
    """
    doc = fitz.open()
    try:
        for img_path in image_paths:
            img_doc = fitz.open(img_path)
            # fitz.open on an image creates a 1-page PDF-like document
            pdf_bytes = img_doc.convert_to_pdf()
            img_doc.close()
            img_pdf = fitz.open("pdf", pdf_bytes)
            doc.insert_pdf(img_pdf)
            img_pdf.close()
        doc.save(output_path, garbage=1, deflate=True)
    finally:
        doc.close()


def _downsample_images(
    doc: fitz.Document,
    max_dpi: int = 150,
    jpeg_quality: int = 75,
) -> None:
    """Re-compress images in *doc* in-place.

    Each image whose effective resolution exceeds *max_dpi* is
    down-scaled and re-encoded as JPEG at the given quality.
    Images already at or below the target resolution are still
    re-encoded if the JPEG result is smaller.
    """
    seen_xrefs: set[int] = set()

    # Suppress MuPDF C-library stderr noise (e.g. "Not a JPEG file")
    fitz.TOOLS.mupdf_display_errors(False)
    try:
        _downsample_images_inner(doc, max_dpi, jpeg_quality, seen_xrefs)
    finally:
        fitz.TOOLS.mupdf_display_errors(True)
        fitz.TOOLS.mupdf_warnings(reset=True)


def _downsample_images_inner(
    doc: fitz.Document,
    max_dpi: int,
    jpeg_quality: int,
    seen_xrefs: set[int],
) -> None:
    for page in doc:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            # Decode image via xref (handles all PDF filter types)
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception:
                continue

            orig_w = pix.width
            orig_h = pix.height

            # Get original compressed size for comparison
            try:
                raw_stream = doc.xref_stream_raw(xref)
                orig_size = len(raw_stream) if raw_stream else 0
            except Exception:
                orig_size = 0

            # Determine the effective DPI from page placement
            try:
                img_rects = page.get_image_rects(xref)
            except Exception:
                img_rects = []
            if img_rects:
                r = img_rects[0]
                eff_dpi_x = orig_w / (r.width / 72) if r.width else 9999
                eff_dpi_y = orig_h / (r.height / 72) if r.height else 9999
                eff_dpi = max(eff_dpi_x, eff_dpi_y)
            else:
                eff_dpi = 9999

            scale = min(1.0, max_dpi / eff_dpi) if eff_dpi > max_dpi else 1.0
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))

            # Ensure RGB without alpha for JPEG encoding
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)  # drop alpha channel
            if pix.colorspace != fitz.csRGB:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            # Resize via Pixmap if dimensions changed
            if new_w != orig_w or new_h != orig_h:
                # Build a scaled pixmap using a temporary single-page PDF
                tmp_doc = fitz.open()
                tmp_page = tmp_doc.new_page(width=new_w, height=new_h)
                tmp_page.insert_image(
                    fitz.Rect(0, 0, new_w, new_h),
                    pixmap=pix,
                )
                pix = tmp_page.get_pixmap(
                    matrix=fitz.Identity,
                    clip=fitz.Rect(0, 0, new_w, new_h),
                )
                tmp_doc.close()

            jpeg_bytes = pix.tobytes("jpeg", jpg_quality=jpeg_quality)

            # Only replace if the result is actually smaller
            if orig_size == 0 or len(jpeg_bytes) < orig_size:
                doc.update_stream(xref, jpeg_bytes, compress=False)
                doc.xref_set_key(xref, "Filter", "/DCTDecode")
                doc.xref_set_key(xref, "DecodeParms", "null")
                doc.xref_set_key(xref, "Width", str(new_w))
                doc.xref_set_key(xref, "Height", str(new_h))
                doc.xref_set_key(xref, "ColorSpace", "/DeviceRGB")
                doc.xref_set_key(xref, "BitsPerComponent", "8")
                doc.xref_set_key(xref, "Length", str(len(jpeg_bytes)))

    fitz.TOOLS.mupdf_warnings(reset=True)  # discard MuPDF stderr noise


def export_pdf_compressed(
    src_path: str,
    dst_path: str,
    optimize_level: int = 0,
    *,
    image_dpi: int = 150,
    image_quality: int = 75,
) -> None:
    """Export a PDF with optimization.

    The caller decides *image_dpi* / *image_quality*; for the standard/high/max
    presets the export dialog seeds them, and for the custom level the user sets
    them directly. This function only branches on *optimize_level*:

    Args:
        src_path: Source PDF file path.
        dst_path: Destination PDF file path.
        optimize_level: Optimization level.
            0 = no optimization (plain save),
            1 = cleanup only (garbage collection + deflate),
            >= 2 = image recompression using *image_dpi* / *image_quality*.
        image_dpi: Target max DPI for image recompression (levels >= 2).
        image_quality: JPEG quality (1-100) for image recompression (levels >= 2).
    """
    doc = fitz.open(src_path)
    try:
        if optimize_level >= 2:
            _downsample_images(doc, max_dpi=image_dpi, jpeg_quality=image_quality)

        save_opts: dict = {}
        if optimize_level >= 1:
            save_opts["garbage"] = 4
            save_opts["deflate"] = True
            save_opts["deflate_images"] = optimize_level < 2
            save_opts["deflate_fonts"] = True
            save_opts["clean"] = True
        doc.save(dst_path, **save_opts)
    finally:
        doc.close()


def rasterize_pdf(
    src_path: str,
    output_path: str,
    dpi: int = 150,
    *,
    image_format: str = "png",
    jpeg_quality: int = 75,
) -> None:
    """Create a rasterized (image-only) copy of a PDF.

    Each page is rendered to an image at the given DPI and embedded into
    a new page that keeps the original page dimensions.  The result looks
    identical but contains no selectable text or vector data.

    Args:
        src_path: Source PDF file path.
        output_path: Destination PDF path.
        dpi: Resolution for rasterization.
        image_format: "png" (lossless, sharp text) or "jpeg" (lossy,
            much smaller for photo/scan-heavy pages).
        jpeg_quality: JPEG quality (1-100); ignored for PNG.
    """
    src_doc = fitz.open(src_path)
    out_doc = fitz.open()
    try:
        for page_num in range(len(src_doc)):
            page = src_doc[page_num]
            img_data, _ = _render_page_to_image_bytes(
                page, dpi, image_format=image_format, jpeg_quality=jpeg_quality
            )
            # Keep the original page size; embed the image without re-encoding.
            out_page = out_doc.new_page(
                width=page.rect.width, height=page.rect.height
            )
            out_page.insert_image(out_page.rect, stream=img_data)
        # Pages are copied 1:1 in the same order, so the source bookmarks
        # (outline/TOC) stay valid; carry them over to the rasterized output.
        toc = src_doc.get_toc(simple=True)
        if toc:
            out_doc.set_toc(toc)
        out_doc.save(output_path, garbage=1, deflate=True)
    finally:
        src_doc.close()
        out_doc.close()


@dataclass(slots=True)
class PrintSettings:
    """User-chosen print options produced by the print dialog.

    ``page_numbers`` are 1-based indices over the *concatenation* of all pages
    across the printed PDFs in order (empty means "all pages"). ``page_size_id``
    holds a ``QPageSize.PageSizeId`` value.
    """
    printer_name: str = ""
    copies: int = 1
    collate: bool = True
    page_numbers: "list[int]" = field(default_factory=list)
    fit_mode: str = "fit"            # "fit" | "actual" | "shrink" | "custom"
    custom_scale_pct: int = 100
    orientation: str = "auto"        # "auto" | "portrait" | "landscape"
    page_size_id: object = None      # QPageSize.PageSizeId
    duplex: str = "none"             # "none" | "long" | "short"
    color: bool = True               # True = color, False = grayscale
    nup: int = 1                     # 1 / 2 / 4 / 6 / 9 / 16


# Cap raster dimensions (px) when rasterizing pages for print, so HighResolution
# printers (often 1200 dpi) don't produce hundreds-of-MB pixmaps per page.
_PRINT_MAX_RASTER_PX = 4000


def parse_page_range(spec: str, total: int) -> list[int]:
    """Parse a page-range string like ``"1-5, 8, 11-13"`` into a sorted, unique
    list of 1-based page numbers clamped to ``1..total``.

    Full-width commas/dashes are tolerated, whitespace is ignored, ranges are
    inclusive, reversed ranges (a>b) are skipped, and any malformed token is
    ignored. Returns an empty list for empty/invalid input.
    """
    if not spec or total <= 0:
        return []
    for ch in ("，", "、"):
        spec = spec.replace(ch, ",")
    for ch in ("－", "―", "‐", "−", "〜", "～", "–", "—"):
        spec = spec.replace(ch, "-")
    result: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                continue
            for p in range(a, b + 1):
                if 1 <= p <= total:
                    result.add(p)
            continue
        if re.fullmatch(r"\d+", token):
            p = int(token)
            if 1 <= p <= total:
                result.add(p)
    return sorted(result)


def build_flat_index(pdf_paths: "list[str]") -> "list[tuple[str, int]]":
    """Return a flat list mapping a 0-based running index to ``(pdf_path,
    page_index)`` across all *pdf_paths* in order."""
    flat: list[tuple[str, int]] = []
    for path in pdf_paths:
        for i in range(get_page_count(path)):
            flat.append((path, i))
    return flat


def nup_grid(nup: int, landscape: bool) -> "tuple[int, int]":
    """Return ``(rows, cols)`` for an N-up sheet. For 2 and 6, the split
    direction follows the physical sheet orientation."""
    base = {1: (1, 1), 2: (2, 1), 4: (2, 2), 6: (3, 2), 9: (3, 3), 16: (4, 4)}
    rows, cols = base.get(nup, (1, 1))
    if landscape and nup in (2, 6):
        rows, cols = cols, rows
    return rows, cols


def _page_is_landscape(pdf_path: str, page_index: int) -> bool:
    """Return True when the given page is wider than tall."""
    try:
        with fitz.open(pdf_path) as doc:
            if 0 <= page_index < len(doc):
                r = doc[page_index].rect
                return r.width > r.height
    except Exception:
        logger.debug("_page_is_landscape failed: %s p%s", pdf_path, page_index, exc_info=True)
    return False


def _draw_page_into_rect(
    painter: QPainter,
    pdf_path: str,
    page_index: int,
    rect: QRect,
    *,
    fit_mode: str,
    custom_scale_pct: int,
    printer_dpi: int,
    color: bool = True,
) -> None:
    """Rasterize one PDF page and draw it into *rect* (device pixels) according
    to *fit_mode*. Oversized output (actual/custom) is centered and clipped to
    *rect*; smaller output is centered."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_index < 0 or page_index >= len(doc):
                return
            page = doc[page_index]
            pw_pt = page.rect.width
            ph_pt = page.rect.height
            if pw_pt <= 0 or ph_pt <= 0 or rect.width() <= 0 or rect.height() <= 0:
                return

            dev_per_pt = printer_dpi / 72.0
            if fit_mode == "actual":
                place_w = pw_pt * dev_per_pt
                place_h = ph_pt * dev_per_pt
            elif fit_mode == "custom":
                f = max(custom_scale_pct, 1) / 100.0
                place_w = pw_pt * dev_per_pt * f
                place_h = ph_pt * dev_per_pt * f
            elif fit_mode == "shrink":
                place_w = pw_pt * dev_per_pt
                place_h = ph_pt * dev_per_pt
                if place_w > rect.width() or place_h > rect.height():
                    s = min(rect.width() / place_w, rect.height() / place_h)
                    place_w *= s
                    place_h *= s
            else:  # "fit"
                s = min(rect.width() / pw_pt, rect.height() / ph_pt)
                place_w = pw_pt * s
                place_h = ph_pt * s
            if place_w <= 0 or place_h <= 0:
                return

            # Render at a scale matching the placement size, capped for memory.
            render_scale = max(place_w / pw_pt, place_h / ph_pt)
            longest = max(pw_pt, ph_pt) * render_scale
            if longest > _PRINT_MAX_RASTER_PX:
                render_scale *= _PRINT_MAX_RASTER_PX / longest
            mat = fitz.Matrix(render_scale, render_scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = QImage(
                pix.samples, pix.width, pix.height, pix.stride,
                QImage.Format.Format_RGB888,
            ).copy()
            if not color:
                img = img.convertToFormat(QImage.Format.Format_Grayscale8)

            tw = round(place_w)
            th = round(place_h)
            tx = rect.x() + (rect.width() - tw) // 2
            ty = rect.y() + (rect.height() - th) // 2
            painter.save()
            try:
                painter.setClipRect(rect)
                painter.drawImage(QRect(tx, ty, tw, th), img)
            finally:
                painter.restore()
    except Exception:
        logger.debug("_draw_page_into_rect failed: %s p%s", pdf_path, page_index, exc_info=True)


def print_pdfs(
    pdf_paths: "list[str]",
    parent: "QWidget | None" = None,
    *,
    settings: "PrintSettings | None" = None,
    printer: "QPrinter | None" = None,
) -> None:
    """Print one or more PDF files.

    When *settings* and *printer* are supplied (normally by ``PrintDialog``),
    they are honored directly: page range, copies/collate, fit mode, N-up,
    color and orientation. ``settings.page_numbers`` are 1-based indices over
    the concatenation of all pages across *pdf_paths*.

    When called without *settings*, a basic system ``QPrintDialog`` is shown as
    a fallback and every page is printed fit-to-page (legacy behavior).

    Note: page orientation is fixed once for the whole job (mid-document
    orientation changes are unreliable across drivers); ``"auto"`` derives it
    from the first printed page.
    """
    if printer is None:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)

    if settings is None:
        dialog = QPrintDialog(printer, parent)
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return

    flat = build_flat_index(pdf_paths)
    if not flat:
        return

    if settings is not None and settings.page_numbers:
        targets = [n - 1 for n in settings.page_numbers if 1 <= n <= len(flat)]
    else:
        targets = list(range(len(flat)))
    if not targets:
        return

    nup = settings.nup if settings else 1
    fit_mode = settings.fit_mode if settings else "fit"
    custom_scale = settings.custom_scale_pct if settings else 100
    color = settings.color if settings else True
    copies = max(settings.copies if settings else 1, 1)
    collate = settings.collate if settings else True

    # Resolve "auto" orientation from the first printed page (once).
    if settings is not None and settings.orientation == "auto":
        first_path, first_idx = flat[targets[0]]
        printer.setPageOrientation(
            QPageLayout.Orientation.Landscape
            if _page_is_landscape(first_path, first_idx)
            else QPageLayout.Orientation.Portrait
        )

    # Let the driver replicate copies when it can; otherwise loop ourselves.
    driver_copies = printer.supportsMultipleCopies()
    if driver_copies:
        printer.setCopyCount(copies)
        printer.setCollateCopies(collate)
        copy_loops = 1
    else:
        copy_loops = copies

    dpi = printer.resolution()
    sheets = [targets[i:i + nup] for i in range(0, len(targets), nup)]

    def iter_sheets():
        if driver_copies or copy_loops == 1:
            yield from sheets
        elif collate:
            for _ in range(copy_loops):
                yield from sheets
        else:
            for sheet in sheets:
                for _ in range(copy_loops):
                    yield sheet

    painter = QPainter()
    if not painter.begin(printer):
        return
    try:
        first = True
        for sheet in iter_sheets():
            if not first:
                printer.newPage()
            first = False

            viewport = painter.viewport()
            rows, cols = nup_grid(nup, viewport.width() > viewport.height())
            cell_w = viewport.width() / cols
            cell_h = viewport.height() / rows
            for pos, flat_pos in enumerate(sheet):
                r = pos // cols
                c = pos % cols
                cx = viewport.x() + round(c * cell_w)
                cy = viewport.y() + round(r * cell_h)
                cw = viewport.x() + round((c + 1) * cell_w) - cx
                ch = viewport.y() + round((r + 1) * cell_h) - cy
                pdf_path, page_index = flat[flat_pos]
                _draw_page_into_rect(
                    painter, pdf_path, page_index, QRect(cx, cy, cw, ch),
                    fit_mode=fit_mode, custom_scale_pct=custom_scale,
                    printer_dpi=dpi, color=color,
                )
    finally:
        painter.end()
