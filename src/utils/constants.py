"""インポート/変換対象の拡張子定義(単一情報源)。"""

# ---------------------------------------------------------------------------
# 「重量文書」判定の閾値(単一情報源)
# ---------------------------------------------------------------------------
# ページ編集画面は通常、キャッシュ主体で全ページのサムネイルウィジェットを
# 先読みする(応答性・体験を優先)。しかしページ数が極端に多い、または
# ファイルサイズが極端に大きい PDF ではこの先読みが UI スレッドを長時間
# 占有し、フリーズしたように見える／メモリを圧迫する原因になる。
# ここで定めた閾値のいずれかを超えた PDF は「重量文書」とみなし、
# ウィジェット生成をチャンク分割し、サムネイル描画も表示範囲のみを
# 逐次処理する方式へ自動的に切り替える(src/views/page_edit_window.py 参照)。
#
# 以下は「設定で未指定のときに使う既定値」であり、実際の判定・利用は
# ユーザーが設定ダイアログで上書きできる src/utils/app_settings.py の
# 各 getter (heavy_pdf_*) 経由で行う。GUI を持たないコード(pdf_utils 等)
# から使う場合もこの既定値を経由すること。
HEAVY_PDF_PAGE_COUNT_THRESHOLD = 300
HEAVY_PDF_FILE_SIZE_MB = 150
HEAVY_PDF_FILE_SIZE_BYTES = HEAVY_PDF_FILE_SIZE_MB * 1024 * 1024
# 重量文書でサムネイルウィジェットを生成する際、1回のイベントループ処理で
# 生成するページ数。大きいほど初期表示は速いがUIブロック時間が伸びる。
HEAVY_PDF_WIDGET_CHUNK_SIZE = 120
# 重量文書でサムネイルを描画する際、1回のタイマー発火で処理するページ数
# (通常文書は 5 ページ/回)。1ページずつに絞ることで、埋め込み画像が
# 大きく描画が重いページがあっても他の操作を挟める。
HEAVY_PDF_RENDER_BATCH_SIZE = 1
# サムネイル/ページ画像キャッシュ(_PixmapCache)の既定の最大保持件数。
PIXMAP_CACHE_MAX_ENTRIES = 256

# 設定ダイアログでの入力値をクランプする許容範囲(単一情報源)。
# (下限, 上限) のタプル。
HEAVY_PDF_PAGE_COUNT_THRESHOLD_RANGE = (10, 100_000)
HEAVY_PDF_FILE_SIZE_MB_RANGE = (1, 10_000)
HEAVY_PDF_WIDGET_CHUNK_SIZE_RANGE = (10, 2_000)
HEAVY_PDF_RENDER_BATCH_SIZE_RANGE = (1, 50)
PIXMAP_CACHE_MAX_ENTRIES_RANGE = (32, 4_096)

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
# ドラッグ&ドロップで使う MIME タイプ(単一情報源)
# ---------------------------------------------------------------------------
# カード/サムネイルの各ビューが相互参照する定数。ビュー同士の循環 import を
# 避けるため、Qt 非依存のこのモジュールに集約する。
PDFCARD_MIME_TYPE = "application/x-pdfas-card"
FOLDERCARD_MIME_TYPE = "application/x-pdfas-folder"
PAGETHUMBNAIL_MIME_TYPE = "application/x-pdfas-page"

# ---------------------------------------------------------------------------
# FreeText (テキストボックス) のレイアウト共有定数
# ---------------------------------------------------------------------------
# テキストボックスは PDF の FreeText アノテーションとして保存されるが、
#   (1) JusticePDF の編集画面 (Qt で手描き)
#   (2) 保存 PDF を Acrobat が /RC リッチテキストから再レイアウトした表示
# の 2 つで「フォント・内側余白・行間」が食い違い、折り返し位置や位置がずれる。
# Acrobat は注釈を編集した瞬間に /DA・/RC からローカルフォントで外観を再生成する
# ため、フォントを埋め込んでも破棄され、完全一致は原理的に不可能。そこで
# 以下の定数を Qt 描画側 (page_edit_window) とリッチテキスト生成側 (pdf_utils)
# の双方が参照することで、両者を一致させる。最終値は Acrobat 実機で校正する
# (dev/calibrate_freetext_acrobat.py 参照)。
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
