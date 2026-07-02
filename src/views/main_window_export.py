"""PDF/画像エクスポート。

MainWindow の mixin。状態はすべて self 上に持つ。
main_window.py から機械的に移動したもの。
"""
import os
import logging
import shutil
from pathlib import Path
from typing import Callable
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMessageBox,
)

from src.utils.pdf_utils import (
    export_pages_as_images,
    export_pdf_compressed,
    rasterize_pdf,
)
from src.views.export_dialog import ExportOptionsDialog
from src.utils.path_utils import ensure_unique_path

logger = logging.getLogger(__name__)


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string (B/KB/MB/GB)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


class ExportMixin:
    """PDF/画像エクスポート。"""

    def _on_export(self) -> None:
        """Export the selected PDFs to a chosen folder.

        Files and/or folders may be selected together. Selected PDFs are
        exported flat into the destination; selected folders are reproduced
        with their directory structure, exporting only the ``.pdf`` files
        inside (non-PDF files are ignored). If nothing is selected, a dialog
        prompts the user to make a selection and the export is aborted. A
        dialog lets the user pick format, DPI, quality, and compression
        settings before choosing the output directory.
        """
        if not self._selected_cards and not self._selected_folder_cards:
            QMessageBox.information(
                self, "エクスポート", "エクスポートするファイル・フォルダを選択してください。"
            )
            return

        jobs = self._collect_export_jobs()
        if not jobs:
            QMessageBox.information(
                self, "エクスポート", "エクスポートできる PDF がありませんでした。"
            )
            return

        dialog = ExportOptionsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.get_options()

        dst_dir = QFileDialog.getExistingDirectory(self, "エクスポート先フォルダを選択")
        if not dst_dir:
            return

        fmt = options["format"]
        if fmt == "pdf":
            self._export_as_pdf(
                jobs, dst_dir,
                optimize_level=options["pdf_optimize_level"],
                image_dpi=options["pdf_image_dpi"],
                image_quality=options["pdf_image_quality"],
                rasterize=options["rasterize"],
                rasterize_format=options["rasterize_format"],
            )
        else:
            self._export_as_images(
                jobs, dst_dir, fmt,
                dpi=options["dpi"], quality=options["jpeg_quality"],
            )
    def _collect_export_jobs(self) -> list[tuple[str, str]]:
        """Build ``(src_pdf_abs, rel_dst)`` jobs for the current selection.

        - Selected PDF cards map to ``(path, basename)`` → flat at the export
          destination root.
        - Selected folder cards are walked recursively; only ``.pdf`` files are
          included, with ``rel_dst`` preserving the folder structure
          (``<folder>/<sub>/<file>.pdf``). Non-PDF files are skipped entirely,
          and subfolders containing no PDFs produce no jobs (so empty dirs are
          not created at the destination).

        All jobs are collected before any writing happens, so exporting into a
        directory under a source folder does not cause recursive copying.
        """
        jobs: list[tuple[str, str]] = []
        for card in self._selected_cards:
            jobs.append((card.pdf_path, os.path.basename(card.pdf_path)))
        for fc in self._selected_folder_cards:
            folder = fc.folder_path
            parent = os.path.dirname(os.path.abspath(folder).rstrip(os.sep))
            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    if not fname.lower().endswith(".pdf"):
                        continue
                    src_full = os.path.join(root, fname)
                    rel = os.path.relpath(src_full, parent)
                    jobs.append((src_full, rel))
        return jobs
    def _export_as_pdf(
        self,
        jobs: list[tuple[str, str]],
        dst_dir: str,
        *,
        optimize_level: int = 0,
        image_dpi: int = 150,
        image_quality: int = 75,
        rasterize: bool = False,
        rasterize_format: str = "jpeg",
    ) -> None:
        """Copy, optimize-export, or rasterize PDF files to the destination directory.

        ``jobs`` is a list of ``(src_pdf_abs, rel_dst)`` where ``rel_dst`` is the
        path (relative to ``dst_dir``) to write to, preserving any folder
        structure for folder exports.
        """
        # Aggregate before/after sizes for files that were actually
        # compressed or rasterized (size changes); plain copies are excluded.
        total_before = 0
        total_after = 0
        compressed_count = 0

        def _export_one(src: str, parent: Path, rel: str) -> int:
            nonlocal total_before, total_after, compressed_count
            dst_path = ensure_unique_path(
                parent, os.path.basename(rel), pattern="{stem}({i}){ext}"
            )
            transformed = rasterize or optimize_level > 0
            if rasterize:
                rasterize_pdf(
                    src, str(dst_path),
                    dpi=image_dpi,
                    image_format=rasterize_format,
                    jpeg_quality=image_quality,
                )
            elif optimize_level > 0:
                export_pdf_compressed(
                    src, str(dst_path),
                    optimize_level=optimize_level,
                    image_dpi=image_dpi,
                    image_quality=image_quality,
                )
            else:
                shutil.copy2(src, dst_path)
            if transformed:
                # Best-effort size aggregation; failures here must not
                # turn a successful export into a reported failure.
                try:
                    total_before += os.path.getsize(src)
                    total_after += os.path.getsize(str(dst_path))
                    compressed_count += 1
                except OSError:
                    pass
            return 1

        ok, failed = self._run_export_jobs(jobs, dst_dir, _export_one)

        self._show_export_result(
            ok, failed,
            total_before=total_before,
            total_after=total_after,
            compressed_count=compressed_count,
        )
    def _export_as_images(
        self,
        jobs: list[tuple[str, str]],
        dst_dir: str,
        fmt: str,
        *,
        dpi: int = 150,
        quality: int = 85,
    ) -> None:
        """Export all pages of each job's PDF as images.

        ``jobs`` is a list of ``(src_pdf_abs, rel_dst)``; the page images are
        written into the directory mirroring ``rel_dst``'s parent so folder
        structure is preserved for folder exports.
        """
        def _export_one(src: str, parent: Path, rel: str) -> int:
            created = export_pages_as_images(
                src, str(parent), fmt=fmt, dpi=dpi, quality=quality,
            )
            return len(created)

        ok, failed = self._run_export_jobs(jobs, dst_dir, _export_one)

        label = "ページ" if fmt != "pdf" else "件"
        self._show_export_result(ok, failed, label=label)
    def _run_export_jobs(
        self,
        jobs: list[tuple[str, str]],
        dst_dir: str,
        process: Callable[[str, Path, str], int],
    ) -> tuple[int, list[tuple[str, str]]]:
        """エクスポートジョブ共通ループ(存在チェック/出力先mkdir/失敗収集)。

        process(src, parent_dir, rel) は成功件数の加算分を返す。
        """
        ok = 0
        failed: list[tuple[str, str]] = []
        for src, rel in jobs:
            try:
                if not os.path.exists(src):
                    failed.append((src, "元ファイルが見つかりません"))
                    continue
                parent = Path(dst_dir) / os.path.dirname(rel)
                parent.mkdir(parents=True, exist_ok=True)
                ok += process(src, parent, rel)
            except Exception as e:
                failed.append((src, str(e)))
        return ok, failed
    def _show_export_result(
        self,
        ok: int,
        failed: list[tuple[str, str]],
        label: str = "件",
        *,
        total_before: int | None = None,
        total_after: int | None = None,
        compressed_count: int = 0,
    ) -> None:
        size_line = ""
        if compressed_count > 0 and total_before is not None and total_after is not None:
            before = _format_size(total_before)
            after = _format_size(total_after)
            if total_before > 0:
                reduction = (1 - total_after / total_before) * 100
                size_line = f"\n圧縮前: {before}  →  圧縮後: {after}（-{reduction:.0f}%）"
            else:
                size_line = f"\n圧縮前: {before}  →  圧縮後: {after}"

        if failed:
            details = "\n".join([f"- {os.path.basename(s)}: {r}" for s, r in failed[:20]])
            if len(failed) > 20:
                details += f"\n...（他 {len(failed) - 20} 件）"
            QMessageBox.warning(
                self,
                "エクスポート結果",
                f"{ok} {label}エクスポートしました。{size_line}\n失敗: {len(failed)} 件\n\n{details}",
            )
        else:
            QMessageBox.information(
                self,
                "エクスポート結果",
                f"{ok} {label}エクスポートしました。{size_line}",
            )
