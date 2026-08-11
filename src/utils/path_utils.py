"""Path utility helpers for generating unique filenames."""
import re
from pathlib import Path

# Windows で使用できないファイル名文字(予約語チェックは別途)
_ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Windows 予約デバイス名(拡張子を除いたstem部分で大文字小文字を問わず判定)
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, *, fallback: str = "(無題)", max_stem: int = 100) -> str:
    """ファイル名の「名前部分」として安全な文字列に正規化する。

    拡張子は扱わない(呼び出し側で付ける)、純粋な名前サニタイズ関数。

    - Windows禁止文字(``\\ / : * ? " < > |``)と制御文字(0x00-0x1F)は ``_`` に置換
    - 前後の空白を除去し、さらに末尾の ``.`` と空白を除去
      (Windowsは末尾ドット/空白を無視するため、明示的に削っておく)
    - Windows予約名(CON, PRN, AUX, NUL, COM1-9, LPT1-9。大文字小文字問わず)は
      末尾に ``_`` を付けて回避する
    - 結果が空文字になった場合は ``fallback`` にフォールバック
    - ``max_stem`` 文字を超える場合は切り詰める
    """
    sanitized = _ILLEGAL_FILENAME_CHARS_RE.sub("_", name)
    sanitized = sanitized.strip()
    sanitized = sanitized.rstrip(". ")

    if sanitized.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized += "_"

    if not sanitized:
        sanitized = fallback

    if len(sanitized) > max_stem:
        sanitized = sanitized[:max_stem]

    return sanitized


def ensure_unique_path(
    directory: str | Path,
    filename: str,
    pattern: str = "{stem}({i}){ext}",
    *,
    use_original: bool = True,
) -> Path:
    """Return a path that avoids collisions in the target directory.

    Args:
        directory: Directory to place the file in.
        filename: Original filename to base uniqueness on.
        pattern: Pattern for uniqueness, supports {stem}, {ext}, and {i}.
        use_original: If True, return the original filename when available.
    """
    directory = Path(directory)
    base = Path(filename)

    # Strip existing (N) suffix from stem to avoid nesting like xxx(1)(1).pdf
    stem = base.stem
    ext = base.suffix
    m = re.match(r'^(.*?)\(\d+\)$', stem)
    if m:
        stem = m.group(1)

    if use_original:
        candidate = directory / base.name
        if not candidate.exists():
            return candidate

    i = 1
    while True:
        new_name = pattern.format(stem=stem, ext=ext, i=i)
        candidate = directory / new_name
        if not candidate.exists():
            return candidate
        i += 1
