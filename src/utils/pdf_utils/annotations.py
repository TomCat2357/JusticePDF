"""アノテーション(FreeText/図形/マークアップ/付箋)のデータモデルとCRUD。"""
import html
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import fitz

from src.utils.constants import (
    FREETEXT_LINE_HEIGHT,
    FREETEXT_TEXT_INSET_PT,
    freetext_css_font_family,
    freetext_font_key,
)


logger = logging.getLogger(__name__)

from .common import _save_document_in_place


JUSTICEPDF_FREETEXT_SUBJECT_PREFIX = "JusticePDF-FreeText:"
JUSTICEPDF_SHAPE_SUBJECT_PREFIX = "JusticePDF-Shape:"
JUSTICEPDF_MARKUP_SUBJECT_PREFIX = "JusticePDF-Markup:"
JUSTICEPDF_NOTE_SUBJECT_PREFIX = "JusticePDF-Note:"
# 付箋アイコンの公称サイズ（PDF ポイント）。ヒットテスト用の矩形に使う。
NOTE_ICON_PDF_SIZE = 18.0


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



_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_DA_COLOR_RE = re.compile(rf"({_FLOAT_RE})\s+({_FLOAT_RE})\s+({_FLOAT_RE})\s+rg")
_DA_FONT_RE = re.compile(rf"/([^\s/]+)\s+({_FLOAT_RE})\s+Tf")
_CSS_DECL_RE = re.compile(r"\s*([^:]+)\s*:\s*([^;]+)\s*")
_CSS_BORDER_RE = re.compile(
    rf"({_FLOAT_RE})(?:px|pt)?\s+\w+\s+(#[0-9a-fA-F]{{6}}|rgb\([^)]+\))"
)



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


def _decode_prefixed_json(prefix: str, subject: str) -> dict | None:
    """Subject 文字列から prefix 付き JSON メタデータを取り出す(全アノテーション種共通)。"""
    if not subject or not subject.startswith(prefix):
        return None
    try:
        data = json.loads(subject[len(prefix):])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _encode_prefixed_json(prefix: str, payload: dict) -> str:
    """prefix 付き JSON メタデータ文字列を組み立てる(全アノテーション種共通)。"""
    return prefix + json.dumps(payload, separators=(",", ":"))


def _decode_subject_metadata(subject: str) -> dict[str, object] | None:
    return _decode_prefixed_json(JUSTICEPDF_FREETEXT_SUBJECT_PREFIX, subject)


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
    return _encode_prefixed_json(JUSTICEPDF_FREETEXT_SUBJECT_PREFIX, payload)


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
    return _encode_prefixed_json(JUSTICEPDF_SHAPE_SUBJECT_PREFIX, payload)


def _decode_shape_metadata(subject: str) -> dict | None:
    return _decode_prefixed_json(JUSTICEPDF_SHAPE_SUBJECT_PREFIX, subject)


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


# --- 汎用アノテーション CRUD --------------------------------------------
# shape / markup / note / freetext の list/create/delete/replace は
# 「型フィルタ・ページへの追加関数・抽出関数」だけが異なる同一骨格のため、
# 種別ごとの定義(_AnnotFamily)を渡して共通実装で処理する。


@dataclass(frozen=True)
class _AnnotFamily:
    """アノテーション1種別分の CRUD 構成要素。"""

    label: str  # ログ/エラーメッセージ用の種別名
    types: list[int]  # 対象とする PDF アノテーション型コード
    add_fn: Callable[[fitz.Page, Any], fitz.Annot]
    extract_fn: Callable[[fitz.Document, int, fitz.Annot], Any]


def _list_annots(family: _AnnotFamily, pdf_path: str, page_num: int | None) -> list:
    results: list = []
    try:
        with fitz.open(pdf_path) as doc:
            page_numbers = _page_numbers_for(doc, page_num)
            if page_numbers is None:
                return []

            for pn in page_numbers:
                page = doc[pn]
                annots = page.annots(types=family.types)
                if annots is None:
                    continue
                for annot in annots:
                    data = family.extract_fn(doc, pn, annot)
                    if data is not None:
                        results.append(data)
    except Exception:
        logger.debug("list_%s_annots failed: %s", family.label, pdf_path, exc_info=True)
    return results


def _create_annot(family: _AnnotFamily, pdf_path: str, data: Any) -> Any:
    doc = fitz.open(pdf_path)
    try:
        if data.page_num < 0 or data.page_num >= len(doc):
            raise IndexError(f"page out of range: {data.page_num}")
        page = doc[data.page_num]
        annot = family.add_fn(page, data)
        saved = family.extract_fn(doc, data.page_num, annot)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError(f"Failed to extract saved {family.label} annotation")
        return saved
    finally:
        doc.close()


def _delete_annot(family: _AnnotFamily, pdf_path: str, page_num: int, xref: int) -> bool:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return False
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is None or annot.type[0] not in family.types:
            return False
        page.delete_annot(annot)
        _save_document_in_place(doc, pdf_path)
        return True
    finally:
        doc.close()


def _replace_annot(family: _AnnotFamily, pdf_path: str, page_num: int, xref: int, data: Any) -> Any:
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"page out of range: {page_num}")
        page = doc[page_num]
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
        replacement = family.add_fn(page, data)
        saved = family.extract_fn(doc, page_num, replacement)
        _save_document_in_place(doc, pdf_path)
        if saved is None:
            raise RuntimeError(f"Failed to extract saved {family.label} annotation")
        return saved
    finally:
        doc.close()



_SHAPE_FAMILY = _AnnotFamily(
    label="shape",
    types=_SHAPE_ANNOT_TYPES,
    add_fn=_add_shape_annot_to_page,
    extract_fn=_extract_shape_data,
)


def list_shape_annots(pdf_path: str, page_num: int | None = None) -> list[ShapeAnnotData]:
    return _list_annots(_SHAPE_FAMILY, pdf_path, page_num)


def create_shape_annot(pdf_path: str, data: ShapeAnnotData) -> ShapeAnnotData:
    return _create_annot(_SHAPE_FAMILY, pdf_path, data)


def delete_shape_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    return _delete_annot(_SHAPE_FAMILY, pdf_path, page_num, xref)


def replace_shape_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: ShapeAnnotData,
) -> ShapeAnnotData:
    return _replace_annot(_SHAPE_FAMILY, pdf_path, page_num, xref, data)


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

    return _create_annot(_FREETEXT_FAMILY, pdf_path, text_data)


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
    return _encode_prefixed_json(JUSTICEPDF_MARKUP_SUBJECT_PREFIX, payload)


def _decode_markup_metadata(subject: str) -> dict | None:
    return _decode_prefixed_json(JUSTICEPDF_MARKUP_SUBJECT_PREFIX, subject)


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


_MARKUP_FAMILY = _AnnotFamily(
    label="markup",
    types=_MARKUP_ANNOT_TYPES,
    add_fn=_add_markup_annot_to_page,
    extract_fn=_extract_markup_data,
)


def list_markup_annots(pdf_path: str, page_num: int | None = None) -> list[TextMarkupAnnotData]:
    """Return JusticePDF text-markup annotations for one page or the whole document."""
    return _list_annots(_MARKUP_FAMILY, pdf_path, page_num)


def create_markup_annot(pdf_path: str, data: TextMarkupAnnotData) -> TextMarkupAnnotData:
    return _create_annot(_MARKUP_FAMILY, pdf_path, data)


def delete_markup_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    return _delete_annot(_MARKUP_FAMILY, pdf_path, page_num, xref)


def replace_markup_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: TextMarkupAnnotData,
) -> TextMarkupAnnotData:
    """Replace a markup annotation (used for color / opacity changes)."""
    return _replace_annot(_MARKUP_FAMILY, pdf_path, page_num, xref, data)


# --- Sticky note (comment) annotations -----------------------------------


def _encode_note_metadata(data: "NoteAnnotData", *, page_rotation: int = 0) -> str:
    payload: dict = {
        "color": list(data.color),
        "icon": data.icon,
        "opacity": float(data.opacity),
        "point": [float(data.point[0]), float(data.point[1])],
        "page_rotation": int(page_rotation),
    }
    return _encode_prefixed_json(JUSTICEPDF_NOTE_SUBJECT_PREFIX, payload)


def _decode_note_metadata(subject: str) -> dict | None:
    return _decode_prefixed_json(JUSTICEPDF_NOTE_SUBJECT_PREFIX, subject)


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


_NOTE_FAMILY = _AnnotFamily(
    label="note",
    types=[fitz.PDF_ANNOT_TEXT],
    add_fn=_add_note_annot_to_page,
    extract_fn=_extract_note_data,
)


def list_note_annots(pdf_path: str, page_num: int | None = None) -> list["NoteAnnotData"]:
    """Return JusticePDF sticky-note annotations for one page or the whole document."""
    return _list_annots(_NOTE_FAMILY, pdf_path, page_num)


def create_note_annot(pdf_path: str, data: "NoteAnnotData") -> "NoteAnnotData":
    return _create_annot(_NOTE_FAMILY, pdf_path, data)


def delete_note_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    return _delete_annot(_NOTE_FAMILY, pdf_path, page_num, xref)


def replace_note_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: "NoteAnnotData",
) -> "NoteAnnotData":
    """Replace a note annotation (content / color / position changes)."""
    return _replace_annot(_NOTE_FAMILY, pdf_path, page_num, xref, data)



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



_FREETEXT_FAMILY = _AnnotFamily(
    label="freetext",
    types=[fitz.PDF_ANNOT_FREE_TEXT],
    add_fn=_add_freetext_annot_to_page,
    extract_fn=_extract_freetext_data,
)


def list_freetext_annots(pdf_path: str, page_num: int | None = None) -> list[FreeTextAnnotData]:
    """Return normalized FreeText annotations for one page or the whole document."""
    return _list_annots(_FREETEXT_FAMILY, pdf_path, page_num)


def create_freetext_annot(pdf_path: str, data: FreeTextAnnotData) -> FreeTextAnnotData:
    """Create a new FreeText annotation and return the normalized saved form."""
    return _create_annot(_FREETEXT_FAMILY, pdf_path, data)


def delete_freetext_annot(pdf_path: str, page_num: int, xref: int) -> bool:
    """Delete a FreeText annotation by page number and xref."""
    return _delete_annot(_FREETEXT_FAMILY, pdf_path, page_num, xref)


def replace_freetext_annot(
    pdf_path: str,
    page_num: int,
    xref: int,
    data: FreeTextAnnotData,
) -> FreeTextAnnotData:
    """Replace an existing FreeText annotation and return the saved replacement."""
    return _replace_annot(_FREETEXT_FAMILY, pdf_path, page_num, xref, data)

