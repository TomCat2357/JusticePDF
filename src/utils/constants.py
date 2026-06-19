"""インポート/変換対象の拡張子定義(単一情報源)。"""

WORD_EXTS = {".doc", ".docx", ".docm"}
EXCEL_EXTS = {".xls", ".xlsx", ".xlsm"}
PPT_EXTS = {".ppt", ".pptx"}
OFFICE_EXTS = WORD_EXTS | EXCEL_EXTS | PPT_EXTS
# PyMuPDF (fitz) が開いて PDF 化できる画像拡張子。
# images_to_pdf() (src/utils/pdf_utils.py) が対応する形式と一致させること。
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif",
    ".jp2", ".jpx", ".ppm", ".pgm", ".pbm", ".pnm", ".pam", ".svg",
}
IMPORT_EXTS = {".pdf"} | OFFICE_EXTS | IMAGE_EXTS
# Archives expanded on drop/import (password-less only); handled separately
# from IMPORT_EXTS because a zip is extracted, not converted file-by-file.
ZIP_EXTS = {".zip"}

# ---------------------------------------------------------------------------
# FreeText (テキストボックス) のレイアウト共有定数
# ---------------------------------------------------------------------------
# テキストボックスは PDF の FreeText アノテーションとして保存されるが、
#   (1) JusticePDF の編集画面 (Qt で手描き)
#   (2) 保存 PDF を Acrobat が /RC リッチテキストから再レイアウトした表示
# の 2 つで「フォント・内側余白・行間」が食い違い、折り返し位置や位置がずれる。
# 以下の定数を Qt 描画側 (page_edit_window) とリッチテキスト生成側 (pdf_utils)
# の双方が参照することで、両者を一致させる。最終値は Acrobat 実機で校正する。
# (純 Python のみ。Qt/fitz には依存させない＝循環 import を避ける)

# 箱の端〜テキストの内側余白(PDF ポイント)。四辺すべてに適用し、
# キャンバス描画・インラインエディタ・PDF の /RD に共通で使う。
FREETEXT_TEXT_INSET_PT = 2.0

# 行間係数(無単位)。/RC/DS の line-height と Qt 側の行送りに共通適用する。
FREETEXT_LINE_HEIGHT = 1.15

# PDF 基本フォント(base-14)キーごとに、Acrobat が認識する基本フェイス名を割り当てる。
FREETEXT_PDF_BASE_FACE = {
    "Helv": "Helvetica",
    "Cour": "Courier",
    "TiRo": "Times New Roman",
}
# 基本フェイスの後ろに連結する CJK フォールバック群。日本語グリフが
# Qt 側と Acrobat 側で同じフォントになるよう、双方でこの並びを共有する。
FREETEXT_CJK_FALLBACK_FAMILIES = ("Yu Gothic", "Meiryo", "MS PGothic")
# CSS の総称ファミリ(最後に付与)。
FREETEXT_CSS_GENERIC = {"Helv": "sans-serif", "Cour": "monospace", "TiRo": "serif"}
FREETEXT_DEFAULT_FONT_KEY = "Helv"


def freetext_font_key(fontname: str | None) -> str:
    """フォント名(PDF タグ/CSS ファミリ列のいずれでも可)を base-14 キーに正規化する。

    CSS のフォールバック列 ("Helvetica, 'Yu Gothic', sans-serif" 等) を渡しても、
    先頭(主)ファミリで判定する。"sans-serif" の "serif" を Times と誤認しない。
    """
    name = (fontname or "").lower()
    primary = name.split(",", 1)[0].strip().strip('"').strip("'")
    if "cour" in primary or "mono" in primary:
        return "Cour"
    if "tiro" in primary or "times" in primary or ("serif" in primary and "sans" not in primary):
        return "TiRo"
    return "Helv"


def freetext_css_font_family(fontname: str | None) -> str:
    """`/DS`(リッチテキスト)に書く font-family のフォールバック列を返す。"""
    key = freetext_font_key(fontname)
    families = [FREETEXT_PDF_BASE_FACE[key], *FREETEXT_CJK_FALLBACK_FAMILIES]
    quoted = [f'"{fam}"' if " " in fam else fam for fam in families]
    quoted.append(FREETEXT_CSS_GENERIC[key])
    return ", ".join(quoted)


def freetext_canvas_font_families(fontname: str | None) -> tuple[str, ...]:
    """Qt の QFont.setFamilies に渡す実フォント名の並びを返す(総称名は含めない)。"""
    key = freetext_font_key(fontname)
    return (FREETEXT_PDF_BASE_FACE[key], *FREETEXT_CJK_FALLBACK_FAMILIES)
