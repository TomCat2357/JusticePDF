"""結合の「下調べ」と「計画」（Qt に触れない純粋ロジック）。

Web 版（JusticePDF-ONLINE）の ``Client_Lib_MergePlan`` に合わせた分類と上限:

- 選択項目（ファイル・フォルダ）を展開し、PDF / 変換して結合できるもの / 変換できないもの に分ける。
- 結合の順はグリッド順（上の階層は渡された順、フォルダの中は名前順）。
- 件数・サイズの上限は 2 段: 注意（確認ダイアログに一言足す）/ 中止（始める前に止める）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.utils.file_kinds import convert_kind, is_library_file, is_pdf

MB = 1024 * 1024
WARN_COUNT = 100          # 注意: 件数がこれを超える
WARN_BYTES = 200 * MB     # 注意: 合計サイズがこれを超える
MAX_COUNT = 1000          # 中止: 件数がこれを超える
MAX_DEPTH = 20            # フォルダの深さの上限（循環・異常な深さへの保険）

KIND_PDF = "pdf"
KIND_CONVERT = "convert"
KIND_UNSUPPORTED = "unsupported"


@dataclass(slots=True)
class MergeFile:
    """結合に使う（または使えない）1 ファイル。"""

    path: str
    kind: str                 # KIND_PDF / KIND_CONVERT / KIND_UNSUPPORTED
    convert: str | None       # convert_kind の値（KIND_CONVERT のとき）
    size: int
    rel_dir: str = ""         # フォルダ階層（"A/B"。トップは ""）

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass(slots=True)
class MergePlan:
    files: list[MergeFile] = field(default_factory=list)        # 結合する順（PDF + 変換するもの）
    unsupported: list[MergeFile] = field(default_factory=list)  # 変換できず、結合に入れないもの
    folders: int = 0
    errors: list[str] = field(default_factory=list)             # 読めなかったフォルダ・深すぎる所

    @property
    def converts(self) -> list[MergeFile]:
        return [f for f in self.files if f.kind == KIND_CONVERT]

    @property
    def pdf_count(self) -> int:
        return sum(1 for f in self.files if f.kind == KIND_PDF)

    @property
    def count(self) -> int:
        """結合に使うファイル数（PDF + 変換するもの）。"""
        return len(self.files)

    @property
    def bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def has_non_pdf(self) -> bool:
        """PDF 以外（変換できないものも含む）が 1 つでもあるか。真なら元のファイルは動かさない（コピーとしてだけ結合）。"""
        return bool(self.unsupported) or any(f.kind == KIND_CONVERT for f in self.files)


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _classify(path: str, rel_dir: str) -> MergeFile:
    if is_pdf(path):
        return MergeFile(path, KIND_PDF, None, _size(path), rel_dir)
    kind = convert_kind(path)
    if kind:
        return MergeFile(path, KIND_CONVERT, kind, _size(path), rel_dir)
    return MergeFile(path, KIND_UNSUPPORTED, None, _size(path), rel_dir)


def scan_paths(paths: list[str], *, max_depth: int = MAX_DEPTH) -> MergePlan:
    """``paths``（ファイル・フォルダ。渡された順が上の階層の順）を展開して計画を作る。

    フォルダの中は名前順（大小文字無視。``merge_paths_to_pdf`` と同じ）。フォルダの中の
    一時ファイル・隠しファイルは数えない（PDF は常に数える）。同じフォルダを 2 度は辿らない。
    """
    plan = MergePlan()
    visited: set[str] = set()

    def walk_dir(path: str, rel: str, depth: int) -> None:
        real = os.path.normcase(os.path.realpath(path))
        if real in visited:
            return
        visited.add(real)
        if depth > max_depth:
            plan.errors.append(f"{path}: フォルダの階層が深すぎるため、これより下は含めていません。")
            return
        plan.folders += 1
        try:
            names = sorted(os.listdir(path), key=str.lower)
        except OSError as exc:
            plan.errors.append(f"{path}: {exc}")
            return
        inner = rel
        for name in names:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                walk_dir(full, f"{inner}/{name}" if inner else name, depth + 1)
            elif is_pdf(name) or is_library_file(full):
                _add(full, inner)

    def _add(path: str, rel: str) -> None:
        f = _classify(path, rel)
        (plan.unsupported if f.kind == KIND_UNSUPPORTED else plan.files).append(f)

    for path in paths:
        if os.path.isdir(path):
            base = os.path.basename(os.path.normpath(path)) or path
            walk_dir(path, base, 0)
        elif os.path.exists(path):
            _add(path, "")
    return plan


# ---- 上限 ----------------------------------------------------------------

@dataclass(slots=True)
class LimitStatus:
    level: str      # "ok" | "warn" | "block"
    count: int
    bytes: int


def assess_limits(count: int, total_bytes: int) -> LimitStatus:
    """件数・合計サイズを上限と比べる（1000 件超で中止、100 件超または 200 MB 超で注意）。"""
    if count > MAX_COUNT:
        level = "block"
    elif count > WARN_COUNT or total_bytes > WARN_BYTES:
        level = "warn"
    else:
        level = "ok"
    return LimitStatus(level, count, total_bytes)


def warn_note(status: LimitStatus) -> str:
    return (f"{status.count} 件・約 {max(1, round(status.bytes / MB))} MB あります。"
            "時間がかかり、メモリが足りないと途中で止まることがあります。")


def block_note(status: LimitStatus) -> str:
    return f"{status.count:,} 件で、上限の {MAX_COUNT:,} 件を超えているため結合できません。フォルダを分けて結合してください。"
