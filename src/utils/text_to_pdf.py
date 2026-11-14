"""プレーンテキスト（.txt）を PDF にする簡易変換（PyMuPDF）。

Word / LibreOffice に頼らず、日本語が出るように CJK フォントを埋め込んで組む。
システムの日本語フォントが見つからないときは PyMuPDF 内蔵の CJK フォント(japan)で組む。
"""
from __future__ import annotations

import os

import fitz

PAGE_WIDTH, PAGE_HEIGHT = 595.0, 842.0  # A4
MARGIN = 56.0
FONT_SIZE = 10.5
LINE_HEIGHT = FONT_SIZE * 1.55
TAB_SPACES = 4

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\YuGothR.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


def _load_font() -> fitz.Font:
    for path in _FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return fitz.Font(fontfile=path)
            except Exception:
                continue
    return fitz.Font("japan")


def read_text(path: str) -> str:
    """文字コードを推測して読む（UTF-8(BOM可) → CP932 → 置換付き UTF-8）。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _wrap(line: str, font: fitz.Font, max_width: float) -> list[str]:
    if not line:
        return [""]
    out: list[str] = []
    cur = ""
    cur_w = 0.0
    for ch in line:
        w = font.text_length(ch, fontsize=FONT_SIZE)
        if cur and cur_w + w > max_width:
            out.append(cur)
            cur, cur_w = "", 0.0
        cur += ch
        cur_w += w
    out.append(cur)
    return out


def text_to_pdf(src_path: str, dest_path: str) -> None:
    """``src_path`` のテキストを A4 縦の PDF にして ``dest_path`` へ書く。空ファイルは白紙 1 ページ。"""
    text = read_text(src_path).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " " * TAB_SPACES)
    font = _load_font()
    max_width = PAGE_WIDTH - MARGIN * 2
    lines: list[str] = []
    for raw_line in text.split("\n"):
        lines.extend(_wrap(raw_line, font, max_width))

    per_page = max(1, int((PAGE_HEIGHT - MARGIN * 2) // LINE_HEIGHT))
    doc = fitz.open()
    try:
        for start in range(0, max(1, len(lines)), per_page):
            page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            writer = fitz.TextWriter(page.rect)
            y = MARGIN + FONT_SIZE
            for line in lines[start:start + per_page]:
                if line.strip():
                    writer.append((MARGIN, y), line, font=font, fontsize=FONT_SIZE)
                y += LINE_HEIGHT
            writer.write_text(page)
        doc.save(dest_path, garbage=1, deflate=True)
    finally:
        doc.close()
