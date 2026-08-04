"""ファイル操作(結合/削除/リネーム/新規作成/移動コピー)。

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
    QApplication,
    QInputDialog,
    QMessageBox,
)
from PyQt6.QtCore import Qt
from send2trash import send2trash

from src.views.pdf_card import PDFCard
from src.views.folder_card import FolderCard
from src.models.undo_manager import UndoAction
from src.utils.pdf_utils import (
    PdfWritePermissionError,
    get_pdf_metadata_title,
    update_pdf_metadata_title,
    create_empty_pdf,
)
from src.utils.path_utils import ensure_unique_path
from src.utils.trash_utils import build_trash_failure_message
from src.workers.file_worker import FileOperationWorker

logger = logging.getLogger(__name__)


class FileOpsMixin:
    """ファイル操作(結合/削除/リネーム/新規作成/移動コピー)。"""

    def _begin_async_operation(self) -> None:
        """Mark start of a background file operation."""
        self._operation_in_progress = True
        self._update_button_states()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    def _end_async_operation(self) -> None:
        """Mark end of a background file operation."""
        self._operation_in_progress = False
        self._active_worker = None
        QApplication.restoreOverrideCursor()
        self._update_button_states()
    def _run_file_operation(
        self,
        do_io: Callable[[], object],
        on_success: Callable,
        on_error: Callable[[Exception], None],
    ) -> None:
        """Run blocking I/O on a FileOperationWorker with standard wiring.

        各ファイル操作(結合/削除など)で同一だったワーカー生成〜起動処理の共通化。
        on_success/on_error 側が undo 登録と _end_async_operation() を行う。
        """
        worker = FileOperationWorker(do_io, parent=self)
        worker.finished.connect(on_success)
        worker.error.connect(on_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._active_worker = worker
        worker.start()
    def _perform_rename(self, old_path: str, new_path: str) -> None:
        old_norm = self._normalize_path(old_path)
        new_norm = self._normalize_path(new_path)
        if old_norm == new_norm:
            return

        self._track_pending_rename(old_path, new_path)
        self._register_internal_remove([old_path])
        self._register_internal_add([new_path])

        try:
            os.rename(old_path, new_path)
        except Exception:
            self._pending_rename_old_to_new.pop(old_norm, None)
            self._pending_rename_new_to_old.pop(new_norm, None)
            self._pending_rename_removed.discard(old_norm)
            self._pending_rename_added.discard(new_norm)
            self._internal_removes.discard(old_norm)
            self._internal_adds.discard(new_norm)
            raise

        card = self._get_card_by_path(old_path)
        if card:
            card._pdf_path = new_path
            card.refresh()
        self._update_page_edit_windows_for_rename(old_path, new_path)
        self._refresh_grid()
        self._update_button_states()
    def _on_card_merge(self, target_card: PDFCard, source_paths_str: str) -> None:
        """Handle card merge (drop cards on another card) with undo support.

        Async — UI updates instantly, heavy I/O runs in background thread.

        Supports multiple selected cards - merges all into target.
        Order: earlier cards (in grid) appear first in merged PDF (sources first, then target).

        Undo restores:
        - File bytes of target + all merged sources
        - Grid order
        - Selection state before the merge

        Redo restores:
        - Merged state (same order)
        - Selection state after the merge

        Export is NOT an undoable action and should not affect undo history.
        """
        from src.utils.pdf_utils import merge_pdfs_in_place

        if self._operation_in_progress:
            return

        # Snapshot grid order and selection before mutation
        old_paths = [c.pdf_path for c in self._cards]
        pre_selected_paths = [c.pdf_path for c in self._selected_cards]

        target_path = target_card.pdf_path

        raw_paths = [p for p in source_paths_str.split('|') if p]
        card_paths = {c.pdf_path for c in self._cards}
        source_paths = [p for p in raw_paths if p in card_paths and p != target_path]

        # Backward-compat: if only one path provided and it's part of a multi-selection,
        # use the full selection order (grid order) instead.
        if len(source_paths) == 1:
            src_card = self._get_card_by_path(source_paths[0])
            if src_card and src_card in self._selected_cards and len(self._selected_cards) > 1:
                source_paths = [
                    c.pdf_path for c in self._cards
                    if c in self._selected_cards and c.pdf_path != target_path
                ]

        if not source_paths:
            return

        # The first source (drag order) will be the merge destination
        merge_dest_path = source_paths[0]

        # New order after merge: remove all sources, place merge result at target's position
        new_paths = []
        for p in old_paths:
            if p == target_path:
                new_paths.append(merge_dest_path)  # Replace target position with merge result
            elif p not in source_paths:
                new_paths.append(p)  # Keep non-source paths

        # --- Phase A: immediate UI update (main thread) ---
        trash_paths = [target_path] + source_paths[1:]
        self._register_internal_remove(trash_paths)
        self._rebuild_cards_from_paths(new_paths)
        self._sort_order = "manual"
        self._refresh_grid()

        # Select merge destination immediately
        self._clear_selection()
        dest_card = self._get_card_by_path(merge_dest_path)
        if dest_card:
            dest_card.set_selected(True)
            self._selected_cards.append(dest_card)
        self._update_button_states()

        self._begin_async_operation()

        # --- Phase B: heavy I/O in background ---
        def _do_io() -> dict[str, str]:
            # Backup involved PDFs (target + sources) so undo/redo is byte-stable
            backup_dir = tempfile.mkdtemp(prefix="justicepdf_merge_backup_")
            backups: dict[str, str] = {}
            for p in [*source_paths, target_path]:
                src = Path(p)
                dst = Path(backup_dir) / f"{src.stem}__{abs(hash(p))}{src.suffix}"
                shutil.copy2(p, dst)
                backups[p] = str(dst)

            # Merge PDFs
            merge_pdfs_in_place(merge_dest_path, source_paths[1:] + [target_path])

            # Trash merged-away files (batch)
            send2trash(trash_paths)

            return backups

        # --- Phase C: completion on main thread ---
        def _select_paths(paths_to_select: list[str]) -> None:
            self._clear_selection()
            for card in self._cards:
                if card.pdf_path in paths_to_select:
                    card.set_selected(True)
                    self._selected_cards.append(card)
            self._update_button_states()

        def _on_success(backups: object) -> None:
            backups_dict: dict[str, str] = backups  # type: ignore[assignment]

            # Refresh merged card thumbnail (file content changed)
            card = self._get_card_by_path(merge_dest_path)
            if card:
                card.refresh()

            def do_merge_sync() -> None:
                merge_pdfs_in_place(merge_dest_path, source_paths[1:] + [target_path])
                self._register_internal_remove(trash_paths)
                send2trash(trash_paths)
                self._rebuild_cards_from_paths(new_paths)
                self._sort_order = "manual"
                self._refresh_grid()
                _select_paths([merge_dest_path])
                card = self._get_card_by_path(merge_dest_path)
                if card:
                    card.refresh()

            def undo_merge() -> None:
                self._register_internal_add(list(backups_dict.keys()))
                for original_path, backup_path in backups_dict.items():
                    shutil.copy2(backup_path, original_path)
                self._rebuild_cards_from_paths(old_paths)
                self._refresh_grid()
                _select_paths(pre_selected_paths)

            def redo_merge() -> None:
                do_merge_sync()

            self._undo_manager.add_action(UndoAction(
                description=f"Merge {len(source_paths)} file(s)",
                undo_func=undo_merge,
                redo_func=redo_merge,
            ))
            self._end_async_operation()

        def _on_error(exc: Exception) -> None:
            # Rollback: restore original card layout
            self._register_internal_add(trash_paths)
            self._rebuild_cards_from_paths(old_paths)
            self._refresh_grid()
            _select_paths(pre_selected_paths)
            self._end_async_operation()
            if isinstance(exc, PdfWritePermissionError):
                self._handle_pdf_write_permission_denied(exc)
            else:
                self._handle_file_operation_error(exc, merge_dest_path, "マージ")

        self._run_file_operation(_do_io, _on_success, _on_error)
    def _on_rename_folder_selected(self) -> None:
        """ツールバー「名前変更→フォルダ名」から呼ばれ、選択中フォルダをリネームする。"""
        if len(self._selected_folder_cards) != 1 or self._selected_cards:
            return
        self._rename_folder(self._selected_folder_cards[0])
    def _rename_folder(self, fc: FolderCard) -> None:
        old_path = fc.folder_path
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "フォルダ名の変更", "新しいフォルダ名:", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return
        parent_dir = os.path.dirname(old_path)
        new_path = os.path.join(parent_dir, new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "フォルダ名の変更", f"'{new_name}' は既に存在します。")
            return
        try:
            os.rename(old_path, new_path)
        except OSError as e:
            QMessageBox.warning(self, "フォルダ名の変更", f"リネームに失敗しました: {e}")
            return

        def do_rename() -> None:
            if os.path.exists(old_path):
                os.rename(old_path, new_path)

        def undo_rename() -> None:
            if os.path.exists(new_path):
                os.rename(new_path, old_path)

        self._undo_manager.add_action(UndoAction(
            description=f"Rename folder {old_name}",
            undo_func=undo_rename,
            redo_func=do_rename,
        ))
    def _delete_folder(self, fc: FolderCard) -> None:
        """Delete a single folder using the same pipeline as the toolbar action."""
        if fc not in self._selected_folder_cards:
            self._clear_selection()
            fc.set_selected(True)
            self._selected_folder_cards.append(fc)
            self._update_button_states()
        self._delete_selected_folders()
    def _delete_selected_folders(self, *, also_pdfs: bool = False) -> None:
        """Delete selected folders (and optionally selected PDFs) to trash.

        Backups are taken in a temp directory so the operation can be undone.
        """
        if self._operation_in_progress:
            return

        folder_paths = [fc.folder_path for fc in self._selected_folder_cards]
        pdf_cards = list(self._selected_cards) if also_pdfs else []
        pdf_paths = [c.pdf_path for c in pdf_cards]
        if not folder_paths and not pdf_paths:
            return

        all_paths = folder_paths + pdf_paths
        title = "フォルダの削除" if folder_paths else "削除"

        # Phase A: immediate UI update
        self._register_internal_remove(all_paths)
        for path in folder_paths:
            self._remove_folder_card(path)
        for card in pdf_cards:
            if card in self._cards:
                self._cards.remove(card)
            card.deleteLater()
        self._selected_folder_cards.clear()
        self._selected_cards.clear()
        self._refresh_grid()
        self._begin_async_operation()

        # Phase B: heavy I/O in background — backup, then send2trash
        def _do_io() -> dict[str, dict[str, str]]:
            backup_dir = tempfile.mkdtemp(prefix="pdfas_backup_")
            folder_backups: dict[str, str] = {}
            pdf_backups: dict[str, str] = {}
            for path in folder_paths:
                backup_path = Path(backup_dir) / Path(path).name
                shutil.copytree(path, backup_path)
                folder_backups[path] = str(backup_path)
            for path in pdf_paths:
                backup_path = Path(backup_dir) / Path(path).name
                shutil.copy2(path, backup_path)
                pdf_backups[path] = str(backup_path)

            deleted: list[str] = []
            try:
                for path in all_paths:
                    send2trash(path)
                    deleted.append(path)
            except OSError:
                # Restore anything we already trashed before the failure.
                for restored in deleted:
                    bp = folder_backups.get(restored) or pdf_backups.get(restored)
                    if not bp or not os.path.exists(bp):
                        continue
                    if restored in folder_backups:
                        shutil.copytree(bp, restored)
                    else:
                        shutil.copy2(bp, restored)
                raise
            return {"folders": folder_backups, "pdfs": pdf_backups}

        def _on_success(result: object) -> None:
            backups: dict[str, dict[str, str]] = result  # type: ignore[assignment]
            folder_backups = backups["folders"]
            pdf_backups = backups["pdfs"]
            backup_paths = list(folder_backups.keys()) + list(pdf_backups.keys())

            def undo_delete() -> None:
                self._register_internal_add(backup_paths)
                for original, backup in folder_backups.items():
                    if not os.path.exists(original):
                        shutil.copytree(backup, original)
                    # The folder watcher does not auto-create cards when
                    # _internal_adds is set, so add the card explicitly.
                    if self._get_folder_card_by_path(original) is None:
                        self._add_folder_card(original)
                for original, backup in pdf_backups.items():
                    if not os.path.exists(original):
                        shutil.copy2(backup, original)
                self._refresh_grid()

            def redo_delete() -> None:
                self._register_internal_remove(backup_paths)
                for path in backup_paths:
                    if os.path.exists(path):
                        send2trash(path)

            n_folders = len(folder_backups)
            n_pdfs = len(pdf_backups)
            if n_folders and n_pdfs:
                desc = f"Delete {n_folders} folder(s) and {n_pdfs} PDF(s)"
            elif n_folders:
                desc = f"Delete {n_folders} folder(s)"
            else:
                desc = f"Delete {n_pdfs} PDF(s)"
            self._undo_manager.add_action(UndoAction(
                description=desc,
                undo_func=undo_delete,
                redo_func=redo_delete,
            ))
            self._end_async_operation()

        def _on_error(exc: Exception) -> None:
            for path in folder_paths:
                if os.path.exists(path):
                    self._internal_removes.discard(self._normalize_path(path))
                    self._add_folder_card(path)
            existing = {c.pdf_path for c in self._cards}
            for path in pdf_paths:
                if os.path.exists(path) and path not in existing:
                    self._internal_removes.discard(self._normalize_path(path))
                    self._add_card(path)
            self._refresh_grid()
            self._end_async_operation()
            first = all_paths[0] if all_paths else ""
            if isinstance(exc, OSError) and first:
                QMessageBox.warning(
                    self,
                    "削除できません",
                    build_trash_failure_message(first, exc),
                )
            else:
                QMessageBox.warning(self, title, f"削除に失敗しました: {exc}")

        self._run_file_operation(_do_io, _on_success, _on_error)
    def _on_merge_selected(self) -> None:
        """選択したファイル・フォルダを1つのPDFに結合する。

        - トップレベルの並び順はグリッド表示順(フォルダが先、次にファイル)。
        - フォルダ構成はしおり(目次)の階層として再現される
          (:func:`merge_paths_to_pdf`)。
        - 結合後、元のファイル/フォルダはゴミ箱へ移動する(Undo で復元可)。
        - 出力は現在のフォルダに「結合_<最初の項目名>.pdf」として自動命名する。
        """
        from src.utils.pdf_utils import merge_paths_to_pdf

        if self._operation_in_progress:
            return

        folder_paths = self._selected_folder_paths_in_grid_order()
        file_paths = self._selected_card_paths_in_grid_order()
        # トップレベルの並び: フォルダ(グリッド順) → 単独ファイル(グリッド順)
        items = folder_paths + file_paths
        if not items or (not folder_paths and len(file_paths) < 2):
            return

        reply = QMessageBox.question(
            self,
            "結合",
            f"選択した {len(items)} 項目を1つのPDFに結合します。\n"
            "フォルダ構成はしおり(目次)の階層として再現されます。\n\n"
            "結合後、元のファイル・フォルダはゴミ箱へ移動します"
            "（「元に戻す」で復元できます）。\n\n続けますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        first_stem = Path(items[0].rstrip("/\\")).stem or "merged"
        output_path = str(
            ensure_unique_path(
                str(self._work_dir), f"結合_{first_stem}.pdf", pattern="{stem}({i}){ext}"
            )
        )
        pdf_cards = [c for c in self._cards if c.pdf_path in set(file_paths)]

        # Phase A: 即時 UI 更新(結合元カードを外し、新ファイルは内部追加として予約)
        self._register_internal_remove(items)
        self._register_internal_add([output_path])
        for path in folder_paths:
            self._remove_folder_card(path)
        for card in pdf_cards:
            if card in self._cards:
                self._cards.remove(card)
            card.deleteLater()
        self._selected_folder_cards.clear()
        self._selected_cards.clear()
        self._refresh_grid()
        self._begin_async_operation()

        # Phase B: 重い I/O — バックアップ → 結合 → 結合元をゴミ箱へ
        def _do_io() -> dict[str, dict[str, str]]:
            backup_dir = tempfile.mkdtemp(prefix="justicepdf_merge_backup_")
            folder_backups: dict[str, str] = {}
            pdf_backups: dict[str, str] = {}
            for path in folder_paths:
                bp = Path(backup_dir) / Path(path).name
                shutil.copytree(path, bp)
                folder_backups[path] = str(bp)
            for path in file_paths:
                bp = Path(backup_dir) / Path(path).name
                shutil.copy2(path, bp)
                pdf_backups[path] = str(bp)

            total = merge_paths_to_pdf(output_path, items)
            if total <= 0:
                raise RuntimeError("結合できるPDFページがありませんでした。")

            deleted: list[str] = []
            try:
                for path in items:
                    send2trash(path)
                    deleted.append(path)
            except OSError:
                # 途中失敗時は、すでにゴミ箱へ送った分を復元してから送出
                for restored in deleted:
                    bp = folder_backups.get(restored) or pdf_backups.get(restored)
                    if not bp or not os.path.exists(bp):
                        continue
                    if restored in folder_backups:
                        shutil.copytree(bp, restored)
                    else:
                        shutil.copy2(bp, restored)
                if os.path.exists(output_path):
                    os.unlink(output_path)
                raise
            return {"folders": folder_backups, "pdfs": pdf_backups}

        def _on_success(result: object) -> None:
            backups: dict[str, dict[str, str]] = result  # type: ignore[assignment]
            folder_backups = backups["folders"]
            pdf_backups = backups["pdfs"]
            restore_paths = list(folder_backups.keys()) + list(pdf_backups.keys())

            # 結合結果カードを追加(ウォッチャが先に追加していれば再利用)して選択
            self._clear_selection()
            new_card = self._get_card_by_path(output_path) or self._add_card(output_path)
            new_card.set_selected(True)
            self._selected_cards.append(new_card)
            self._refresh_grid()
            self._update_button_states()

            def undo_merge() -> None:
                # 結合結果を取り消し、元のファイル/フォルダを復元する
                self._register_internal_remove([output_path])
                self._remove_card(output_path)
                if os.path.exists(output_path):
                    try:
                        send2trash(output_path)
                    except Exception:
                        try:
                            os.unlink(output_path)
                        except OSError:
                            pass
                self._register_internal_add(restore_paths)
                for original, backup in folder_backups.items():
                    if not os.path.exists(original):
                        shutil.copytree(backup, original)
                    if self._get_folder_card_by_path(original) is None:
                        self._add_folder_card(original)
                for original, backup in pdf_backups.items():
                    if not os.path.exists(original):
                        shutil.copy2(backup, original)
                    if self._get_card_by_path(original) is None:
                        self._add_card(original)
                self._refresh_grid()

            def redo_merge() -> None:
                # 再結合 → 結合元を再びゴミ箱へ
                self._register_internal_add([output_path])
                merge_paths_to_pdf(output_path, items)
                self._register_internal_remove(restore_paths)
                for path in folder_paths:
                    self._remove_folder_card(path)
                for path in file_paths:
                    self._remove_card(path)
                for path in restore_paths:
                    if os.path.exists(path):
                        try:
                            send2trash(path)
                        except Exception:
                            pass
                self._clear_selection()
                card = self._get_card_by_path(output_path) or self._add_card(output_path)
                card.set_selected(True)
                self._selected_cards.append(card)
                self._refresh_grid()
                self._update_button_states()

            self._undo_manager.add_action(UndoAction(
                description=f"Merge {len(items)} item(s)",
                undo_func=undo_merge,
                redo_func=redo_merge,
            ))
            self._end_async_operation()

        def _on_error(exc: Exception) -> None:
            # ロールバック: 外したカードを戻し、予約を解除する
            self._internal_adds.discard(self._normalize_path(output_path))
            for path in folder_paths:
                if os.path.exists(path):
                    self._internal_removes.discard(self._normalize_path(path))
                    if self._get_folder_card_by_path(path) is None:
                        self._add_folder_card(path)
            existing = {c.pdf_path for c in self._cards}
            for path in file_paths:
                if os.path.exists(path) and path not in existing:
                    self._internal_removes.discard(self._normalize_path(path))
                    self._add_card(path)
            self._refresh_grid()
            self._end_async_operation()
            QMessageBox.warning(self, "結合", f"結合に失敗しました: {exc}")

        self._run_file_operation(_do_io, _on_success, _on_error)
    def _on_new_file(self) -> None:
        """Create a new blank 1-page PDF in the current folder."""
        dest = Path(str(ensure_unique_path(
            self._work_dir, "新規ファイル.pdf", pattern="{stem}({i}){ext}"
        )))
        try:
            create_empty_pdf(str(dest))
        except Exception as e:
            QMessageBox.warning(self, "新規ファイル", f"作成に失敗しました: {e}")
            return

        self._register_internal_add([str(dest)])

        def undo_create() -> None:
            if dest.exists():
                try:
                    send2trash(str(dest))
                except Exception:
                    pass

        def redo_create() -> None:
            if not dest.exists():
                self._register_internal_add([str(dest)])
                create_empty_pdf(str(dest))

        self._undo_manager.add_action(UndoAction(
            description=f"Create file {dest.name}",
            undo_func=undo_create,
            redo_func=redo_create,
        ))
    def _on_new_folder(self) -> None:
        """適当な既定名でフォルダを即作成する（後で「名前変更」でリネーム可能）。"""
        # 既定名「新規フォルダ」。重複時は「新規フォルダ (2)」「(3)」… と採番する
        # （Windows 日本語エクスプローラ準拠で開始番号は 2）。
        base = "新規フォルダ"
        dest = self._work_dir / base
        if dest.exists():
            i = 2
            while (self._work_dir / f"{base} ({i})").exists():
                i += 1
            dest = self._work_dir / f"{base} ({i})"
        name = dest.name
        try:
            dest.mkdir(parents=True, exist_ok=False)
        except OSError as e:
            QMessageBox.warning(self, "新規フォルダ", f"作成に失敗しました: {e}")
            return

        def undo_create() -> None:
            if dest.exists():
                try:
                    send2trash(str(dest))
                except Exception:
                    pass

        def redo_create() -> None:
            if not dest.exists():
                dest.mkdir(parents=True, exist_ok=False)

        self._undo_manager.add_action(UndoAction(
            description=f"Create folder {name}",
            undo_func=undo_create,
            redo_func=redo_create,
        ))
    def _move_or_copy_files_into_dir(
        self,
        source_paths: list[str],
        dest_dir: Path,
        *,
        is_copy: bool,
    ) -> None:
        """Move or copy PDF files into another directory."""
        if not source_paths:
            return
        dest_str = str(dest_dir)
        if self._normalize_path(dest_str) == self._normalize_path(str(self._work_dir)):
            # Same folder — nothing to move.  Let the existing reorder path handle it.
            return

        source_win = self._find_window_by_path(source_paths[0])
        dest_paths: list[str] = []
        actually_copied: list[tuple[str, str]] = []

        for src in source_paths:
            if not os.path.exists(src):
                continue
            new_path = str(
                ensure_unique_path(
                    dest_dir,
                    os.path.basename(src),
                    pattern="{stem}({i}){ext}",
                )
            )
            try:
                if source_win is not None:
                    source_win._register_internal_remove([src])
                # Register internal add on the window that actually owns dest_dir.
                dest_win = self._find_window_by_workdir(str(dest_dir))
                if dest_win is not None:
                    dest_win._register_internal_add([new_path])
                shutil.copy2(src, new_path)
                if not is_copy:
                    send2trash(src)
                actually_copied.append((src, new_path))
                dest_paths.append(new_path)
            except Exception as e:
                logger.debug("Move/copy failed for %s -> %s: %s", src, new_path, e)

        if not actually_copied:
            return

        def undo_move() -> None:
            for src, dest in actually_copied:
                try:
                    if is_copy:
                        if os.path.exists(dest):
                            send2trash(dest)
                    else:
                        if os.path.exists(dest) and not os.path.exists(src):
                            shutil.copy2(dest, src)
                            send2trash(dest)
                except Exception:
                    logger.debug("undo_move failed for %s -> %s", src, dest, exc_info=True)

        def redo_move() -> None:
            for src, dest in actually_copied:
                try:
                    if is_copy:
                        if not os.path.exists(dest) and os.path.exists(src):
                            shutil.copy2(src, dest)
                    else:
                        if not os.path.exists(dest) and os.path.exists(src):
                            shutil.copy2(src, dest)
                            send2trash(src)
                except Exception:
                    logger.debug("redo_move failed for %s -> %s", src, dest, exc_info=True)

        action = "Copy" if is_copy else "Move"
        self._undo_manager.add_action(UndoAction(
            description=f"{action} {len(actually_copied)} file(s)",
            undo_func=undo_move,
            redo_func=redo_move,
        ))
    def _move_or_copy_folder_into_dir(
        self,
        source: str,
        dest_dir: Path,
        *,
        is_copy: bool,
    ) -> str | None:
        if not os.path.isdir(source):
            return None
        src_norm = self._normalize_path(source)
        dest_norm = self._normalize_path(str(dest_dir))
        # Refuse if moving folder into itself or its own descendant.
        if dest_norm == src_norm or dest_norm.startswith(src_norm + os.sep):
            QMessageBox.warning(self, "フォルダの移動", "フォルダを自身の中に移動できません。")
            return None
        base_name = os.path.basename(source.rstrip(os.sep)) or "folder"
        target = dest_dir / base_name
        if target.exists():
            target = Path(str(ensure_unique_path(dest_dir, base_name, pattern="{stem}({i}){ext}")))

        source_parent = os.path.dirname(source.rstrip(os.sep))
        source_parent_win = self._find_window_by_workdir(source_parent) if source_parent else None
        dest_parent_win = self._find_window_by_workdir(str(dest_dir))

        if not is_copy and source_parent_win is not None:
            source_parent_win._register_internal_remove([source])
        if dest_parent_win is not None:
            dest_parent_win._register_internal_add([str(target)])

        try:
            if is_copy:
                shutil.copytree(source, target)
            else:
                shutil.move(source, target)
        except Exception as e:
            if not is_copy and source_parent_win is not None:
                source_parent_win._internal_removes.discard(src_norm)
            if dest_parent_win is not None:
                dest_parent_win._internal_adds.discard(self._normalize_path(str(target)))
            QMessageBox.warning(self, "フォルダの移動", f"フォルダの移動に失敗しました: {e}")
            return None

        if not is_copy and source_parent_win is not None:
            source_parent_win._remove_folder_card(source)
            source_parent_win._grid_refresh_timer.start()
            source_parent_win._schedule_order_save()

        if dest_parent_win is not None:
            if dest_parent_win._get_folder_card_by_path(str(target)) is None:
                dest_parent_win._add_folder_card(str(target))
            dest_parent_win._grid_refresh_timer.start()
            dest_parent_win._schedule_order_save()

        return str(target)
    def _handle_pdf_write_permission_denied(self, error: PdfWritePermissionError) -> None:
        logger.warning("PDF write blocked in main window for %s", error.pdf_path)
        logger.debug("PDF write blocked in main window for %s", error.pdf_path, exc_info=True)
        pdf_name = os.path.basename(error.pdf_path)
        QMessageBox.warning(
            self,
            "PDFを編集できません",
            (
                "このPDFは他のアプリで使用中のため保存できません。\n\n"
                f"{pdf_name}\n\n"
                "Acrobat などで閉じてから、もう一度お試しください。"
            ),
        )
    def _handle_file_operation_error(self, error: Exception, pdf_path: str, action: str) -> None:
        logger.warning("%s failed for %s", action, pdf_path)
        logger.debug("%s failed for %s", action, pdf_path, exc_info=True)
        pdf_name = os.path.basename(pdf_path)
        QMessageBox.warning(
            self,
            f"{action}できません",
            f"{action}に失敗しました。\n\n{pdf_name}\n\n{error}",
        )
    def _on_delete(self) -> None:
        """Handle delete action (async — UI updates instantly)."""
        if self._operation_in_progress:
            return
        has_pdfs = bool(self._selected_cards)
        has_folders = bool(self._selected_folder_cards)
        if not has_pdfs and not has_folders:
            return
        if has_folders:
            # Folders involved → no undo (backup is impractical for large trees)
            self._delete_selected_folders(also_pdfs=has_pdfs)
            return

        paths = [card.pdf_path for card in self._selected_cards]
        old_paths = [c.pdf_path for c in self._cards]

        # --- Phase A: immediate UI update (main thread) ---
        self._register_internal_remove(paths)
        for card in self._selected_cards[:]:
            if card in self._cards:
                self._cards.remove(card)
            card.deleteLater()
        self._selected_cards.clear()
        self._refresh_grid()
        self._begin_async_operation()

        # --- Phase B: heavy I/O in background ---
        def _do_io() -> dict[str, str]:
            backup_dir = tempfile.mkdtemp(prefix="pdfas_backup_")
            backups: dict[str, str] = {}
            for path in paths:
                backup_path = Path(backup_dir) / Path(path).name
                shutil.copy2(path, backup_path)
                backups[path] = str(backup_path)

            deleted_paths: list[str] = []
            try:
                for path in paths:
                    send2trash(path)
                    deleted_paths.append(path)
            except OSError:
                # Rollback already-deleted files from backup
                for restored in deleted_paths:
                    bp = backups.get(restored)
                    if bp and os.path.exists(bp):
                        shutil.copy2(bp, restored)
                raise
            return backups

        def _on_success(backups: object) -> None:
            backups_dict: dict[str, str] = backups  # type: ignore[assignment]

            def undo_delete() -> None:
                self._register_internal_add(list(backups_dict.keys()))
                for original_path, backup_path in backups_dict.items():
                    shutil.copy2(backup_path, original_path)

            def redo_delete() -> None:
                self._register_internal_remove(list(backups_dict.keys()))
                for path in backups_dict:
                    send2trash(path)

            self._undo_manager.add_action(UndoAction(
                description=f"Delete {len(paths)} PDF(s)",
                undo_func=undo_delete,
                redo_func=redo_delete,
            ))
            self._end_async_operation()

        def _on_error(exc: Exception) -> None:
            # Rollback: restore cards
            self._register_internal_add(paths)
            self._rebuild_cards_from_paths(old_paths)
            self._refresh_grid()
            self._end_async_operation()
            if isinstance(exc, OSError):
                QMessageBox.warning(
                    self,
                    "削除できません",
                    build_trash_failure_message(paths[0], exc),
                )

        self._run_file_operation(_do_io, _on_success, _on_error)
    def _on_rename(self) -> None:
        """Handle rename action."""
        if len(self._selected_cards) != 1:
            return

        card = self._selected_cards[0]
        old_name = card.filename
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old_name
        )

        if ok and new_name and new_name != old_name:
            if not new_name.lower().endswith(".pdf"):
                new_name += ".pdf"
            old_path = card.pdf_path
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            if self._normalize_path(old_path) == self._normalize_path(new_path):
                return

            if os.path.exists(new_path):
                new_path = str(ensure_unique_path(
                    os.path.dirname(old_path),
                    new_name,
                    pattern="{stem}({i}){ext}",
                    use_original=False,
                ))

            def do_rename() -> None:
                self._perform_rename(old_path, new_path)

            def undo_rename() -> None:
                self._perform_rename(new_path, old_path)

            try:
                do_rename()
            except OSError as error:
                self._handle_file_operation_error(error, old_path, "名前変更")
                return
            self._undo_manager.add_action(UndoAction(
                description="Rename PDF",
                undo_func=undo_rename,
                redo_func=do_rename
            ))
    def _on_rename_pdf_title(self) -> None:
        """Handle PDF metadata title rename action."""
        if len(self._selected_cards) != 1:
            return

        card = self._selected_cards[0]
        old_path = card.pdf_path
        old_title = get_pdf_metadata_title(old_path) or os.path.splitext(card.filename)[0]
        new_title, ok = QInputDialog.getText(
            self, "PDFタイトルの変更", "新しいPDFタイトル:", text=old_title
        )

        if not ok or not new_title or new_title == old_title:
            return

        def do_rename_pdf_title() -> None:
            update_pdf_metadata_title(old_path, new_title)
            self._refresh_cards_for_paths([old_path])
            self._refresh_grid()

        def undo_rename_pdf_title() -> None:
            update_pdf_metadata_title(old_path, old_title)
            self._refresh_cards_for_paths([old_path])
            self._refresh_grid()

        try:
            do_rename_pdf_title()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return
        except Exception as error:
            self._handle_file_operation_error(error, old_path, "PDFタイトル変更")
            return
        self._undo_manager.add_action(UndoAction(
            description="Rename PDF Name",
            undo_func=undo_rename_pdf_title,
            redo_func=do_rename_pdf_title
        ))
