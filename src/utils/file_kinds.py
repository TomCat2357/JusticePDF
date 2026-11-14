"""ライブラリ画面に載せるファイルと、その「種類」の定義（ここ 1 か所で決める）。

Web 版（JusticePDF-ONLINE）の FILE_KINDS / fileKindOf に合わせた分類。
色は :data:`theme.COLORS` のキー名で持ち、描画のたびに引く（テーマ切り替えに追従するため）。
"""
from __future__ import annotations

import os

# 種類キー → (表示名)。並び順は一覧での種類の並び（フォルダ → PDF → …）。
FILE_KINDS: dict[str, str] = {
    "folder": "フォルダ",
    "pdf": "PDF",
    "word": "Word・ドキュメント",
    "excel": "Excel・スプレッドシート",
    "powerpoint": "PowerPoint・スライド",
    "text": "テキスト・Markdown",
    "image": "画像",
    "zip": "zip",
    "other": "その他",
}
KIND_ORDER: tuple[str, ...] = tuple(FILE_KINDS)

_EXT_TO_KIND: dict[str, str] = {}
for _kind, _exts in {
    "pdf": ("pdf",),
    "excel": ("xlsx", "xls", "xlsm", "csv"),
    "word": ("docx", "doc", "docm"),
    "powerpoint": ("pptx", "ppt"),
    "image": ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg"),
    "zip": ("zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "lzh"),
    "text": ("txt", "md", "markdown"),
}.items():
    for _e in _exts:
        _EXT_TO_KIND[_e] = _kind

# 種類 → (lucide アイコン名, theme.COLORS のキー)。PDF はサムネイルを描くので持たない。
KIND_ICONS: dict[str, tuple[str, str]] = {
    "folder": ("Folder", "folder-icon"),
    "excel": ("FileSpreadsheet", "kind-excel"),
    "word": ("FileText", "kind-word"),
    "powerpoint": ("Presentation", "kind-powerpoint"),
    "image": ("FileImage", "kind-image"),
    "zip": ("FileArchive", "kind-zip"),
    "text": ("FileText", "kind-text"),
    "other": ("File", "kind-other"),
}
_DEFAULT_ICON = KIND_ICONS["other"]

# 同期・ダウンロード途中や一時ファイルにつく拡張子。
_TEMP_EXTS = frozenset({
    "tmp", "temp", "partial", "part", "crdownload", "download", "opdownload",
    "swp", "swo", "bak", "lnk", "odtmp",
})
_JUNK_NAMES = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})

_FILE_ATTRIBUTE_HIDDEN = 0x2


def extension(path: str) -> str:
    """小文字・ドット無しの拡張子（無ければ空文字）。"""
    return os.path.splitext(os.path.basename(str(path)))[1].lstrip(".").lower()


def is_pdf(path: str) -> bool:
    return str(path).lower().endswith(".pdf")


def kind_of(path: str) -> str:
    """ファイルの種類キー（フォルダは判定しない。PDF 以外で未知の拡張子は ``other``）。"""
    return _EXT_TO_KIND.get(extension(path), "other")


def kind_label(kind: str) -> str:
    return FILE_KINDS.get(kind, FILE_KINDS["other"])


def kind_icon(kind: str) -> tuple[str, str]:
    """``(lucide アイコン名, COLORS のキー)``。"""
    return KIND_ICONS.get(kind, _DEFAULT_ICON)


def is_library_name(name: str) -> bool:
    """ファイル名だけで決まる除外（Office の一時ファイル・隠し・同期途中の名前など）。"""
    base = os.path.basename(str(name))
    if not base:
        return False
    low = base.lower()
    if base.startswith(".") or base.startswith("~$") or low in _JUNK_NAMES:
        return False
    if low.endswith("~") or extension(base) in _TEMP_EXTS:
        return False
    return True


def is_library_file(path: str) -> bool:
    """ライブラリの一覧に出すファイルか。名前による除外に加え、Windows の隠し属性も除く。"""
    if not is_library_name(path):
        return False
    try:
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return True  # 消えた直後などは名前だけで判断する
    return not (attrs & _FILE_ATTRIBUTE_HIDDEN)


def format_size(size: int) -> str:
    """ファイルサイズの短い表記（例 ``12 KB``、``1.4 MB``）。"""
    n = float(max(0, int(size)))
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if n >= 100 or unit == "KB" else f"{n:.1f} {unit}"
    return f"{n:.0f} TB"
