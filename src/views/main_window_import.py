"""ファイル/フォルダのインポート(Office変換・ZIP展開を含む)。

MainWindow の mixin。状態はすべて self 上に持つ。
main_window.py から機械的に移動したもの。
"""
import os
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable
from PyQt6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)
from PyQt6.QtCore import Qt
from send2trash import send2trash

from src.models.undo_manager import UndoAction
from src.utils.pdf_utils import (
    clear_pixmap_cache_for_path,
)
from src.utils.constants import (
    WORD_EXTS as _WORD_EXTS,
    EXCEL_EXTS as _EXCEL_EXTS,
    PPT_EXTS as _PPT_EXTS,
    OFFICE_EXTS as _OFFICE_EXTS,
    IMAGE_EXTS as _IMAGE_EXTS,
    IMPORT_EXTS as _IMPORT_EXTS,
    ZIP_EXTS as _ZIP_EXTS,
)
from src.utils.path_utils import ensure_unique_path
from src.utils.file_kinds import convert_kind, is_library_name
from src.utils.zip_utils import prepare_zip_imports
from src.workers.import_worker import (
    MODE_CONVERT,
    MODE_COPY,
    MODE_IMPORT,
    ImportWorker,
    find_soffice,
)

logger = logging.getLogger(__name__)


def _exts_to_filter(label: str, exts: set[str]) -> str:
    """Build a QFileDialog name-filter string like ``"Word (*.doc *.docx)"``."""
    pattern = " ".join(f"*{e}" for e in sorted(exts))
    return f"{label} ({pattern})"


IMPORT_OFFICE_WARN_THRESHOLD = 5


class ImportMixin:
    """ファイル/フォルダのインポート(Office変換・ZIP展開を含む)。"""

    def _on_import(self) -> None:
        """Handle import action (files)."""
        all_filter = _exts_to_filter("インポート可能なすべてのファイル", _IMPORT_EXTS | _ZIP_EXTS)
        filters = [
            all_filter,
            _exts_to_filter("PDF", {".pdf"}),
            _exts_to_filter("Word", _WORD_EXTS),
            _exts_to_filter("Excel", _EXCEL_EXTS),
            _exts_to_filter("PowerPoint", _PPT_EXTS),
            _exts_to_filter("画像", _IMAGE_EXTS),
            _exts_to_filter("ZIP", _ZIP_EXTS),
            "すべてのファイル (*)",
        ]
        dialog = QFileDialog(self, "インポート")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setNameFilters(filters)
        dialog.selectNameFilter(all_filter)
        if dialog.exec():
            paths = dialog.selectedFiles()
            if paths:
                self._import_paths(paths)
    def _on_import_folder(self) -> None:
        """Handle import action for a folder (preserves nested structure)."""
        folder = QFileDialog.getExistingDirectory(
            self, "フォルダをインポート", str(Path.home())
        )
        if folder:
            self._import_paths([folder])
    def _build_import_tree(
        self,
        paths: list[str],
        dest_root: Path,
        *,
        copy_only: bool = False,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Build a flat list of (src, dest) pairs for the importer.

        Directories are walked recursively; the nested structure is preserved
        relative to the dropped-root under ``dest_root``.  Leaf collisions are
        resolved via ``ensure_unique_path`` so neither the real work dir nor
        any new subfolder ever sees a clobber.  Office/image sources are given
        ``.pdf`` destinations.

        ``copy_only`` が真のときは変換しない取り込み（ドロップ用）: 拡張子を問わず
        すべてのファイルを同じ名前のままコピー対象にする（一時ファイル・隠しファイルを除く）。
        空のフォルダもそのまま作る。

        Returns ``(tree, top_dirs)`` where ``top_dirs`` lists the top-level
        folders newly created under ``dest_root`` for each imported directory
        (in input order).  Callers that mirror a dropped folder — e.g. the
        Explorer "open folder" launch — use this to open the copied folder.
        The directories are created here (``mkdir``) even when they contain no
        importable files, so ``top_dirs`` is reliable regardless of contents.
        """
        used_dest: set[str] = set()
        top_dirs: list[str] = []

        def _alloc_dest(parent: Path, filename: str) -> Path:
            parent.mkdir(parents=True, exist_ok=True)
            dest = ensure_unique_path(parent, filename, pattern="{stem}({i}){ext}")
            # If multiple sources resolve to the same destination during this
            # build pass (before the files are created) we bump further.
            while str(dest) in used_dest:
                dest = ensure_unique_path(parent, filename, pattern="{stem}({i}){ext}", use_original=False)
            used_dest.add(str(dest))
            return dest

        def _normalize_dest_name(src_name: str) -> str:
            base, ext = os.path.splitext(src_name)
            lower = ext.lower()
            if lower in _OFFICE_EXTS or lower in _IMAGE_EXTS:
                return f"{base}.pdf"
            return src_name

        tree: list[tuple[str, str]] = []
        for p in paths:
            if not p:
                continue
            if os.path.isdir(p):
                folder_name = os.path.basename(os.path.abspath(p).rstrip(os.sep)) or "folder"
                # Ensure the top-level import folder gets a unique name at dest_root.
                target_root = dest_root / folder_name
                if target_root.exists():
                    target_root = Path(
                        str(ensure_unique_path(dest_root, folder_name, pattern="{stem}({i}){ext}", use_original=False))
                    )
                target_root.mkdir(parents=True, exist_ok=True)
                top_dirs.append(str(target_root))
                for root, _dirs, files in os.walk(p):
                    if copy_only:
                        _dirs[:] = [d for d in _dirs if is_library_name(d)]
                    rel = os.path.relpath(root, p)
                    dest_parent = target_root if rel == "." else (target_root / rel)
                    dest_parent.mkdir(parents=True, exist_ok=True)
                    for fname in files:
                        src_full = os.path.join(root, fname)
                        ext = os.path.splitext(fname)[1].lower()
                        if copy_only:
                            if not is_library_name(fname):
                                continue
                        elif ext not in _IMPORT_EXTS:
                            continue
                        dest_name = fname if copy_only else _normalize_dest_name(fname)
                        dest_full = _alloc_dest(dest_parent, dest_name)
                        tree.append((src_full, str(dest_full)))
            elif os.path.isfile(p):
                ext = os.path.splitext(p)[1].lower()
                if copy_only:
                    if not is_library_name(p):
                        continue
                elif ext not in _IMPORT_EXTS:
                    continue
                base = os.path.basename(p)
                dest_name = base if copy_only else _normalize_dest_name(base)
                dest_full = _alloc_dest(dest_root, dest_name)
                tree.append((p, str(dest_full)))
        return tree, top_dirs
    def _count_office_files(self, tree: list[tuple[str, str]]) -> int:
        return sum(
            1 for src, _ in tree
            if os.path.splitext(src)[1].lower() in _OFFICE_EXTS
        )
    def import_external_paths(
        self,
        paths: list[str],
        *,
        open_imported_folders: bool = False,
    ) -> None:
        """Explorer の右クリックから渡されたパスを作業フォルダへ取り込む。

        ``main.py`` がコマンドライン引数（``%1``）で受け取ったパスを起動後に
        取り込むための公開エントリ。存在するパスだけを既存の ``_import_paths``
        に流す。

        ``open_imported_folders`` が真のときは、取り込んだフォルダ（PDFs 配下に
        作られたコピー）を新しいウィンドウで開く。フォルダを右クリックして
        「JusticePDFで開く」を実行したときに、フォルダごと PDFs にコピーして
        コピー先を開く挙動を、ファイルの取り込み挙動に揃えるために使う。
        """
        valid = [p for p in paths if Path(p).exists()]
        if not valid:
            return
        on_top_dirs = self._open_imported_folders if open_imported_folders else None
        self._import_paths(valid, on_top_dirs=on_top_dirs)
    def _open_imported_folders(self, top_dirs: list[str]) -> None:
        """Open each copied top-level folder produced by an external import."""
        for d in top_dirs:
            self._open_folder_in_new_window(d)
    def _import_paths(
        self,
        paths: list[str],
        *,
        dest_root: Path | None = None,
        on_top_dirs: "Callable[[list[str]], None] | None" = None,
        copy_only: bool = False,
    ) -> None:
        """Import PDF / Office / image files and folders into the work tree.

        ``copy_only`` が真（OS からのドロップ）のときは変換も ZIP 展開もせず、
        拡張子を問わずそのままコピーする。インポートメニュー・Explorer 連携は
        従来どおり ``copy_only=False``（PDF に変換して取り込む）。

        This is the main entry for both the Import button, the Import Folder
        button, drag-and-drop onto the main window, and drops onto a
        FolderCard (via ``dest_root``).  Runs asynchronously in an
        ``ImportWorker`` thread; the user may cancel mid-batch.

        A password-less ``.zip`` is expanded into a folder named after the
        archive (under ``dest_root``); its contents then go through the same
        conversion pipeline.  Password protected / unreadable archives are
        reported and skipped.

        ``on_top_dirs`` (if given) is called with the list of top-level folders
        created for imported directories as soon as they exist on disk — before
        the async copy/convert worker runs — so a caller can open the copied
        folder immediately and watch files land in it live.  It is invoked only
        when at least one directory was imported.
        """
        if self._operation_in_progress or self._active_import_worker is not None:
            QMessageBox.information(self, "インポート", "別のインポートが進行中です。完了までお待ちください。")
            return

        root = Path(dest_root) if dest_root else Path(self._work_dir)

        if copy_only:
            source_paths, cleanup_dirs = list(paths), []
        else:
            prep = prepare_zip_imports(paths)
            if prep.encrypted or prep.broken:
                self._notify_zip_problems(prep.encrypted, prep.broken)
            source_paths, cleanup_dirs = prep.paths, prep.temp_dirs

        tree, top_dirs = self._build_import_tree(source_paths, root, copy_only=copy_only)

        # Open the copied folder(s) right away.  The directories already exist
        # (created during the build pass); the import worker fills them in
        # afterwards and each opened window's FolderWatcher reflects new files
        # as they arrive.  Opening before _start_import_worker also lets its
        # internal-add pre-registration find the new window.
        if on_top_dirs and top_dirs:
            on_top_dirs(top_dirs)

        if not tree:
            self._cleanup_temp_dirs(cleanup_dirs)
            return

        office_count = 0 if copy_only else self._count_office_files(tree)
        if office_count > IMPORT_OFFICE_WARN_THRESHOLD:
            result = QMessageBox.question(
                self,
                "インポート確認",
                (
                    f"変換が必要なファイルが {office_count} 件あります。\n"
                    "変換には時間がかかります。続行しますか?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                self._cleanup_temp_dirs(cleanup_dirs)
                return

        self._start_import_worker(
            tree, cleanup_dirs=cleanup_dirs, mode=MODE_COPY if copy_only else MODE_IMPORT,
        )
    # ---- ［PDFに変換］ -----------------------------------------------
    def _selected_convertible_paths(self) -> list[str]:
        """選択中のファイルのうち PDF に変換できるもの（グリッド順）。"""
        return [
            c.pdf_path for c in self._cards
            if c in self._selected_cards and convert_kind(c.pdf_path)
        ]

    def _on_convert_to_pdf(self) -> None:
        """選択した Office・txt・Markdown・画像を、同じフォルダに「元の名前.pdf」として変換する。

        元のファイルは残す。同名があれば「名前(1).pdf」の連番（置き換えは訊かない）。
        PDF・フォルダ・変換できない種類は無視し、件数だけ完了時に伝える。
        """
        targets = self._selected_convertible_paths()
        unsupported = sum(
            1 for c in self._selected_cards
            if not c.is_pdf and not convert_kind(c.pdf_path)
        )
        self._convert_paths_to_pdf(targets, unsupported=unsupported)

    def _convert_paths_to_pdf(self, targets: list[str], *, unsupported: int = 0) -> None:
        """``targets``（Office・txt・Markdown・画像）を PDF に変換する（選択以外、例: Markdown ウィンドウからも使う）。"""
        if self._operation_in_progress or self._active_import_worker is not None:
            QMessageBox.information(self, "PDFに変換", "別の処理が進行中です。完了までお待ちください。")
            return
        if not targets:
            return
        tree = self._build_convert_tree(targets)
        self._start_import_worker(tree, mode=MODE_CONVERT, unsupported=unsupported)

    @staticmethod
    def _build_convert_tree(paths: list[str]) -> list[tuple[str, str]]:
        """``(元, 出力先)`` の一覧。出力先は元と同じフォルダの「<拡張子なしの名前>.pdf」で、重なれば連番。"""
        used: set[str] = set()
        tree: list[tuple[str, str]] = []
        for src in paths:
            folder = os.path.dirname(src)
            name = os.path.splitext(os.path.basename(src))[0] + ".pdf"
            stem = os.path.splitext(name)[0]
            dest = os.path.join(folder, name)
            i = 0
            while os.path.exists(dest) or os.path.normcase(dest) in used:
                i += 1
                dest = os.path.join(folder, f"{stem}({i}).pdf")
            dest = str(dest)
            used.add(os.path.normcase(dest))
            tree.append((src, dest))
        return tree
    def _notify_zip_problems(
        self,
        encrypted: list[str],
        broken: list[str],
    ) -> None:
        """Warn about zips that could not be expanded."""
        sections: list[str] = []
        if encrypted:
            names = "\n".join(f"・{n}" for n in encrypted)
            sections.append(
                f"パスワード付きのため展開できませんでした（{len(encrypted)} 件）:\n{names}"
            )
        if broken:
            names = "\n".join(f"・{n}" for n in broken)
            sections.append(
                f"ZIPとして開けませんでした（{len(broken)} 件）:\n{names}"
            )
        if sections:
            QMessageBox.warning(self, "ZIP展開", "\n\n".join(sections))
    def _cleanup_temp_dirs(self, dirs: list[str] | None) -> None:
        """Remove temporary extraction directories (best-effort)."""
        for d in dirs or []:
            shutil.rmtree(d, ignore_errors=True)
    def _start_import_worker(
        self,
        tree: list[tuple[str, str]],
        *,
        cleanup_dirs: list[str] | None = None,
        mode: str = MODE_IMPORT,
        unsupported: int = 0,
    ) -> None:
        total = len(tree)
        if total == 0:
            self._cleanup_temp_dirs(cleanup_dirs)
            return

        # Pre-register expected destinations so FolderWatcher events don't
        # clobber undo history on either the current or target window.
        for _, dest in tree:
            dest_win = self._find_window_by_workdir(os.path.dirname(dest))
            if dest_win is not None:
                dest_win._register_internal_add([dest])

        title = {MODE_COPY: "コピー", MODE_CONVERT: "PDFに変換"}.get(mode, "インポート")
        progress = QProgressDialog(
            "変換中..." if mode == MODE_CONVERT else f"{title}中...",
            "キャンセル",
            0,
            total,
            self,
        )
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = ImportWorker(tree, find_soffice(), parent=self, mode=mode)

        def _on_progress(current: int, total_: int, filename: str) -> None:
            progress.setMaximum(max(1, total_))
            progress.setValue(current)
            if filename:
                progress.setLabelText(
                    f"({current + 1}/{total_}) {filename}"
                )

        def _on_finished(imported: list, failed: list, cancelled: bool) -> None:
            progress.close()
            self._active_import_worker = None
            self._active_import_progress = None
            self._end_async_operation()
            self._on_import_finished(
                imported, failed, cancelled, tree, mode=mode, unsupported=unsupported)
            self._cleanup_temp_dirs(cleanup_dirs)
            worker.deleteLater()

        worker.progress_updated.connect(_on_progress)
        worker.finished_all.connect(_on_finished)
        progress.canceled.connect(worker.request_cancel)

        self._active_import_worker = worker
        self._active_import_progress = progress
        self._begin_async_operation()
        worker.start()
    def _on_import_finished(
        self,
        imported: list[str],
        failed: list[tuple[str, str]],
        cancelled: bool,
        tree: list[tuple[str, str]],
        *,
        mode: str = MODE_IMPORT,
        unsupported: int = 0,
    ) -> None:
        # Drop any pre-registered adds that never happened.
        imported_norm = {self._normalize_path(p) for p in imported}
        for _, dest in tree:
            norm = self._normalize_path(dest)
            if norm not in imported_norm:
                for w in list(type(self)._instances):
                    try:
                        w._internal_adds.discard(norm)
                    except Exception:
                        pass

        for dest in imported:
            clear_pixmap_cache_for_path(dest)

        if imported and self._folder_cards:
            imported_norm_paths = [self._normalize_path(p) for p in imported]
            for fc in self._folder_cards:
                fc_norm = self._normalize_path(fc.folder_path) + os.sep
                if any(p.startswith(fc_norm) for p in imported_norm_paths):
                    fc.refresh()

        if imported:
            backup_dir = tempfile.mkdtemp(prefix="pdfas_import_")
            backups: dict[str, str] = {}
            for dest_path in imported:
                backup_path = str(
                    ensure_unique_path(
                        backup_dir,
                        os.path.basename(dest_path),
                        pattern="{stem}({i}){ext}",
                    )
                )
                try:
                    shutil.copy2(dest_path, backup_path)
                except Exception:
                    logger.debug("Failed to backup %s for undo", dest_path, exc_info=True)
                    continue
                backups[dest_path] = backup_path

            def undo_import() -> None:
                for dest_path in list(backups.keys()):
                    win = self._find_window_by_path(dest_path) or self
                    win._register_internal_remove([dest_path])
                    if os.path.exists(dest_path):
                        try:
                            send2trash(dest_path)
                        except Exception:
                            logger.debug("send2trash failed for %s", dest_path, exc_info=True)

            def redo_import() -> None:
                for dest_path, backup_path in backups.items():
                    if os.path.exists(backup_path) and not os.path.exists(dest_path):
                        win = self._find_window_by_path(dest_path) or self
                        win._register_internal_add([dest_path])
                        shutil.copy2(backup_path, dest_path)

            self._undo_manager.add_action(UndoAction(
                description=(
                    f"Convert {len(imported)} file(s) to PDF" if mode == MODE_CONVERT
                    else f"Import {len(imported)} file(s)"
                ),
                undo_func=undo_import,
                redo_func=redo_import,
            ))

        if mode == MODE_CONVERT:
            self._show_convert_result(imported, failed, cancelled, unsupported)
            return

        if failed:
            details = "\n".join(f"- {os.path.basename(s)}: {r}" for s, r in failed[:20])
            if len(failed) > 20:
                details += f"\n...（他 {len(failed) - 20} 件）"
            QMessageBox.warning(self, "インポート結果", f"失敗: {len(failed)} 件\n\n{details}")

        if cancelled and not failed:
            QMessageBox.information(
                self,
                "インポート",
                f"インポートはキャンセルされました ({len(imported)} 件は既に処理済み)。",
            )

    def _show_convert_result(
        self,
        imported: list[str],
        failed: list[tuple[str, str]],
        cancelled: bool,
        unsupported: int,
    ) -> None:
        """［PDFに変換］の完了報告（成功件数・対象外件数・失敗件数と理由の先頭 3 件）。"""
        lines: list[str] = []
        if imported:
            lines.append(f"{len(imported)} 件を PDF に変換しました")
        if unsupported:
            lines.append(f"PDF に変換できない種類のファイル {unsupported} 件は対象外です")
        if failed:
            lines.append(f"{len(failed)} 件は失敗")
            lines.extend(f"- {os.path.basename(s)}: {r}" for s, r in failed[:3])
        if cancelled:
            lines.append("キャンセルされました")
        if not lines:
            return
        box = QMessageBox.warning if failed else QMessageBox.information
        box(self, "PDFに変換", "\n".join(lines))
