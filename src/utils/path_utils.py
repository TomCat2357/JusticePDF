"""Path utility helpers for generating unique filenames."""
import re
from pathlib import Path


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
