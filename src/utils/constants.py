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
