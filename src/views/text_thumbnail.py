"""テキスト・Markdown のライブラリカード用サムネイル（本文の冒頭を紙のように描く）。

* ``.md`` / ``.markdown``: 先頭を Markdown として組版して描く（見出し・箇条書きなどが見える）。
* ``.txt``: 先頭の数行を等幅フォントで描く。

読むのは先頭 :data:`HEAD_BYTES` だけ（巨大ファイルでも軽い）。文字コードは
``text_file.decode_bytes`` で UTF-8(BOM 可) → CP932 の順に判定する。色は ``theme.COLORS``
（``paper`` / ``text``）から描画時に引くので、テーマ切り替え後の再描画に追従する。

スレッド: PDF のサムネイル（``get_pdf_card_info``）と同じく、カードの読み込み時に呼ぶ同期処理。
ただし描画は **QImage + QPainter + QTextDocument だけ**（QPixmap を使わない）なので GUI スレッド
外からも呼べる。結果は (パス, 更新時刻, サイズ, 一辺, テーマ) をキーに小さな LRU に残す。
失敗したら None を返し、呼び出し側が従来のアイコンカードに戻す。
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QImage, QPainter, QPalette, QTextDocument,
)

from src.utils import file_kinds
from src.utils.markdown_to_pdf import MONO_FAMILIES, build_document, jp_font
from src.utils.text_file import decode_bytes, normalize_newlines, read_head_bytes
from src.views import theme
from src.views.theme import COLORS, is_dark_theme

logger = logging.getLogger(__name__)

HEAD_BYTES = 64 * 1024     # 読む上限
MAX_CHARS = 1500           # 組版する文字数の上限（見える範囲に十分）
SHEET_WIDTH = 300.0        # 紙の幅（論理 px）。この幅で組んで一辺に縮小する
PAD = 14.0
CACHE_MAX = 256

_cache: OrderedDict[tuple, QImage] = OrderedDict()


def _ink_color() -> str:
    """紙に載せる文字色。``paper`` はダークテーマでも白（PDF のサムネイルと同じ）なので、
    ダークテーマの ``text``（明るい色）は使わず、濃い色にする。"""
    if is_dark_theme(theme.current_theme()):
        return "#1e293b"
    return COLORS["text"]


def supports(path: str) -> bool:
    return file_kinds.kind_of(path) == "text"


def _cache_get(key):
    img = _cache.get(key)
    if img is not None:
        _cache.move_to_end(key)
    return img


def _cache_put(key, img) -> None:
    _cache[key] = img
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def _mono_font(point_size: float) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES + ["monospace"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(point_size)
    return font


def _build_doc(path: str, text: str) -> QTextDocument:
    if file_kinds.is_markdown(path):
        from src.utils.markdown_to_pdf import base_url_for
        # 画像は読まない（一覧で多数のカードを描くとき遅くなるため）
        return build_document(text, base_url_for(path), font=jp_font(10.0), load_images=False)
    doc = QTextDocument()
    doc.setDefaultFont(_mono_font(9.0))
    doc.setPlainText(text)
    return doc


def render_text_thumbnail(path: str, size: int, dpr: float = 1.0) -> QImage | None:
    """``path`` の冒頭を描いた ``size`` × ``size``（論理 px）の紙。読めなければ None。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(path), st.st_mtime_ns, st.st_size, int(size), round(dpr, 2),
           theme.current_theme())
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        raw = read_head_bytes(path, HEAD_BYTES)
        truncated = st.st_size > HEAD_BYTES
        text, _enc, _lossy = decode_bytes(raw, truncated=truncated)
        text = normalize_newlines(text)[:MAX_CHARS]
        doc = _build_doc(path, text)
        doc.setTextWidth(SHEET_WIDTH - PAD * 2)
        pw = max(1, round(size * dpr))
        image = QImage(pw, pw, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(COLORS["paper"]))
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            scale = size / SHEET_WIDTH
            painter.scale(scale, scale)
            painter.translate(PAD, PAD)
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.palette.setColor(QPalette.ColorRole.Text, QColor(_ink_color()))
            visible_h = SHEET_WIDTH - PAD * 2
            ctx.clip = QRectF(0, 0, SHEET_WIDTH - PAD * 2, visible_h)
            painter.setClipRect(ctx.clip)
            doc.documentLayout().draw(painter, ctx)
        finally:
            painter.end()
    except Exception:
        logger.debug("text thumbnail failed: %s", path, exc_info=True)
        return None
    _cache_put(key, image)
    return image
