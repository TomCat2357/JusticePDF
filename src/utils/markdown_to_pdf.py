"""Markdown（.md / .markdown）を PDF にする変換（Qt のみ。追加の依存なし）。

``QTextDocument.setMarkdown`` で組版し、``QPdfWriter`` へ印刷する。

スレッド: ``ImportWorker``（QThread）から呼ばれる。QTextDocument / QPdfWriter / QImage /
QFont は GUI スレッド外でも使える（QPixmap だけが GUI スレッド専用）ので、画像は
``loadResource`` で **QImage** として返し、QPixmap を経由しない。

画像: ローカルファイル（md のあるフォルダ基準の相対パスを含む）と ``data:`` URI だけ読む。
``http(s)`` など外部の画像は取りに行かない（プレビューと同じ規則: :func:`load_image_resource`）。
"""
from __future__ import annotations

import base64
import os
import urllib.parse

from PyQt6.QtCore import QByteArray, QMarginsF, QRectF, QSizeF, QUrl
from PyQt6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument

from src.utils.text_file import normalize_newlines
from src.utils.text_to_pdf import read_text

# 日本語が出るフォント（先頭から順に、入っているものが使われる）。
JP_FONT_FAMILIES = ["Yu Gothic UI", "Meiryo UI", "Meiryo", "Hiragino Sans", "Noto Sans CJK JP", "MS Gothic"]
BODY_POINT_SIZE = 10.0
PAGE_MARGIN_MM = 18.0
MAX_BYTES = 2 * 1024 * 1024  # これを超える Markdown はアプリ内で扱わない（変換・プレビュー）
PDF_DPI = 300
DOC_DPI = 96.0  # QTextDocument の論理解像度（フォントの px 換算の基準）
MONO_FAMILIES = ["Consolas", "BIZ UDGothic", "MS Gothic", "Courier New"]


def jp_font(point_size: float = BODY_POINT_SIZE) -> QFont:
    font = QFont()
    font.setFamilies(JP_FONT_FAMILIES)
    font.setPointSizeF(point_size)
    return font


def base_url_for(path: str) -> QUrl:
    """``path`` のあるフォルダを指す基準 URL（末尾に / を付け、相対パスが解決できるように）。"""
    folder = os.path.dirname(os.path.abspath(path))
    url = QUrl.fromLocalFile(folder)
    s = url.toString()
    return QUrl(s if s.endswith("/") else s + "/")


def _load_data_uri(uri: str) -> QImage | None:
    head, sep, payload = uri.partition(",")
    if not sep or not head.lower().startswith("data:image/"):
        return None
    try:
        if head.lower().endswith(";base64"):
            raw = base64.b64decode(urllib.parse.unquote(payload), validate=False)
        else:
            raw = urllib.parse.unquote_to_bytes(payload)
    except Exception:
        return None
    image = QImage()
    image.loadFromData(QByteArray(raw))
    return None if image.isNull() else image


def load_image_resource(base_url: QUrl, name: QUrl) -> QImage | None:
    """文書中の画像参照を QImage にする。ローカルファイルと data: だけ。外部 URL は None（取得しない）。"""
    if name.scheme().lower() == "data":
        return _load_data_uri(name.toString())
    resolved = base_url.resolved(name)
    if resolved.isLocalFile() and not resolved.host():  # file://host/... や UNC（外部への取得）は読まない
        image = QImage(resolved.toLocalFile())
        return None if image.isNull() else image
    if not name.scheme() and not resolved.scheme():
        image = QImage(name.toString())  # スキームなしの素のパス
        return None if image.isNull() else image
    return None


class LocalMarkdownDocument(QTextDocument):
    """外部ネットワークへ出ない QTextDocument（画像はローカルと data: のみ、QImage で返す）。"""

    def __init__(self, base_url: QUrl | None = None, *, load_images: bool = True, parent=None):
        super().__init__(parent)
        self._load_images = load_images
        if base_url is not None:
            self.setBaseUrl(base_url)

    def loadResource(self, type_, name):  # noqa: N802 (Qt の名前)
        if int(type_) == int(QTextDocument.ResourceType.ImageResource.value) and self._load_images:
            return load_image_resource(self.baseUrl(), name)
        return None


def build_document(text: str, base_url: QUrl | None, *, font: QFont | None = None,
                   load_images: bool = True) -> LocalMarkdownDocument:
    doc = LocalMarkdownDocument(base_url, load_images=load_images)
    doc.setDefaultFont(font or jp_font())
    doc.setMarkdown(text)
    return doc


def markdown_to_pdf(src_path: str, dest_path: str) -> None:
    """``src_path`` の Markdown を A4 縦の PDF にして ``dest_path`` へ書く。

    ``QTextDocument.print`` は用紙の余白を二重に取る（Qt の挙動）ので使わず、文書を
    ページ高さで組んでページごとに ``drawContents`` で描く。
    """
    if os.path.getsize(src_path) > MAX_BYTES:
        raise RuntimeError(f"Markdown が大きすぎます（{MAX_BYTES // (1024 * 1024)} MB まで）")
    text = normalize_newlines(read_text(src_path))
    doc = build_document(text, base_url_for(src_path))
    writer = QPdfWriter(dest_path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM),
                          QPageLayout.Unit.Millimeter)
    writer.setResolution(PDF_DPI)
    area = writer.pageLayout().paintRectPixels(PDF_DPI)
    scale = PDF_DPI / DOC_DPI
    page_w, page_h = area.width() / scale, area.height() / scale
    doc.setPageSize(QSizeF(page_w, page_h))
    painter = QPainter(writer)
    try:
        painter.scale(scale, scale)
        for i in range(max(1, doc.pageCount())):
            if i:
                writer.newPage()
            painter.save()
            painter.translate(0, -i * page_h)
            doc.drawContents(painter, QRectF(0, i * page_h, page_w, page_h))
            painter.restore()
    finally:
        painter.end()
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) == 0:
        raise RuntimeError("Markdown を PDF にできませんでした")
