"""ドラッグ&ドロップ(カード並べ替え/コピー/結合/ページ抽出/外部ファイル)。

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
)
from PyQt6.QtCore import Qt
from send2trash import send2trash

from src.views.pdf_card import PDFCard
from src.models.undo_manager import UndoAction
from src.utils.pdf_utils import (
    PdfWritePermissionError,
)
from src.utils.constants import (
    IMPORT_EXTS as _IMPORT_EXTS,
    ZIP_EXTS as _ZIP_EXTS,
    PDFCARD_MIME_TYPE,
    FOLDERCARD_MIME_TYPE,
    PAGETHUMBNAIL_MIME_TYPE,
)
from src.utils.path_utils import ensure_unique_path

logger = logging.getLogger(__name__)


class DragDropMixin:
    """ドラッグ&ドロップ(カード並べ替え/コピー/結合/ページ抽出/外部ファイル)。"""

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event."""

        md = event.mimeData()
        if md.hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif md.hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
        elif md.hasFormat(FOLDERCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif md.hasUrls():
            for url in md.urls():
                local = url.toLocalFile()
                if not local:
                    continue
                if os.path.isdir(local):
                    event.acceptProposedAction()
                    return
                ext = os.path.splitext(local)[1].lower()
                if ext in _IMPORT_EXTS or ext in _ZIP_EXTS:
                    event.acceptProposedAction()
                    return
    def dragMoveEvent(self, event) -> None:
        """Handle drag move event - show drop indicator."""
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            # 複数カードドラッグ: ドラッグ中の全カードが結合先から除外される
            self._drag_move_show_indicator(
                event, PDFCARD_MIME_TYPE, lambda data: data.split('|')
            )
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            # ページサムネイルドラッグ: 抽出元PDFのみ結合先から除外される
            self._drag_move_show_indicator(
                event, PAGETHUMBNAIL_MIME_TYPE, lambda data: [data.split('|')[0]]
            )
        elif event.mimeData().hasFormat(FOLDERCARD_MIME_TYPE):
            modifiers = QApplication.keyboardModifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                event.setDropAction(Qt.DropAction.CopyAction)
            else:
                event.setDropAction(Qt.DropAction.MoveAction)
            event.acceptProposedAction()
            self._hide_drop_indicator()
            self._clear_all_drop_targets()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hide_drop_indicator()
            self._clear_all_drop_targets()
    def _drag_move_show_indicator(
        self,
        event,
        mime_type: str,
        decode_excluded: Callable[[str], list[str]],
    ) -> None:
        """カード系ドラッグ中の表示更新(カード中央=結合モード/それ以外=挿入位置)。

        decode_excluded は MIME ペイロード文字列から「結合先にできないパス」の
        一覧を返す(自己ドロップ除外)。
        """
        # Set drop action based on Ctrl key
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            event.setDropAction(Qt.DropAction.CopyAction)
        else:
            event.setDropAction(Qt.DropAction.MoveAction)

        event.acceptProposedAction()
        drop_pos = self._container.mapFrom(self, event.position().toPoint())
        target_card = self._get_card_at_pos(drop_pos)

        # Check for merge mode on card center
        if target_card:
            # Locked cards cannot be merge targets
            if target_card.is_locked:
                self._show_drop_indicator(drop_pos)
                return

            card_rect = target_card.geometry()
            edge_margin = card_rect.width() * 0.15  # 70% center = 15% edges

            # Check self-drop exclusion
            data = event.mimeData().data(mime_type).data().decode('utf-8')
            if target_card.pdf_path not in decode_excluded(data):
                if drop_pos.x() > card_rect.left() + edge_margin and drop_pos.x() < card_rect.right() - edge_margin:
                    # On card center - merge mode
                    self._hide_drop_indicator()
                    self._clear_all_drop_targets(except_card=target_card)
                    target_card.set_drop_target(True)
                    self._drop_indicator_index = -2  # Special value for merge
                    return

        # Show insert indicator
        self._show_drop_indicator(drop_pos)
    def _clear_all_drop_targets(self, except_card=None) -> None:
        """Turn off merge-highlight on every card (optionally skipping one)."""
        for card in self._cards:
            if card is except_card:
                continue
            if card.is_drop_target:
                card.set_drop_target(False)
    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave event - hide drop indicator."""
        self._hide_drop_indicator()
        self._clear_all_drop_targets()
        super().dragLeaveEvent(event)
    def _show_drop_indicator(self, pos) -> None:
        """Show drop indicator at the appropriate position."""
        # Reset any card merge highlighting
        self._clear_all_drop_targets()

        idx = self._get_drop_index(pos)
        if idx == self._drop_indicator_index:
            return

        self._drop_indicator_index = idx

        if not self._cards:
            self._drop_indicator.hide()
            return

        # Calculate indicator position
        if idx == 0:
            ref_card = self._cards[0]
            x = ref_card.geometry().left() - 5
        elif idx >= len(self._cards):
            ref_card = self._cards[-1]
            x = ref_card.geometry().right() + 2
        else:
            ref_card = self._cards[idx]
            x = ref_card.geometry().left() - 5

        card_rect = self._cards[0].geometry() if self._cards else None
        if card_rect:
            self._drop_indicator.setFixedHeight(card_rect.height())
            self._drop_indicator.move(x, ref_card.geometry().top())
            self._drop_indicator.raise_()
            self._drop_indicator.show()
    def _hide_drop_indicator(self) -> None:
        """Hide the drop indicator."""
        self._drop_indicator.hide()
        self._drop_indicator_index = -1
    def dropEvent(self, event) -> None:
        """Handle drop event."""

        logger.debug(f"MainWindow.dropEvent called, mimeData formats: {event.mimeData().formats()}")
        
        drop_mode = self._drop_indicator_index
        self._hide_drop_indicator()
        self._clear_all_drop_targets()

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            logger.debug(f"PDFCARD drop: source_path={source_path}, drop_pos={drop_pos}")

            # Check if Ctrl key is pressed for copy operation
            modifiers = QApplication.keyboardModifiers()
            is_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            logger.debug(f"is_copy={is_copy}, _drop_indicator_index={self._drop_indicator_index}")

            # Detect cross-window drop: any source path outside this work dir.
            source_paths = [p for p in source_path.split('|') if p]
            wd_norm = self._normalize_path(str(self._work_dir))
            is_cross_window = any(
                not self._normalize_path(os.path.dirname(p)) == wd_norm
                for p in source_paths
            )

            if is_cross_window:
                self._handle_cross_window_card_drop(source_paths, drop_pos, is_copy=is_copy)
            elif drop_mode == -2:  # Overlay mode (merge)
                target_card = self._get_card_at_pos(drop_pos)
                if target_card:
                    if is_copy:
                        self._handle_card_copy_merge(source_path, target_card)
                    else:
                        self._on_card_merge(target_card, source_path)
            else:  # Insert mode
                if is_copy:
                    self._handle_card_copy(source_path, drop_pos)
                else:
                    self._handle_card_drop(source_path, drop_pos)

            # Also move/copy any sibling folders from the same multi-selection drag.
            if event.mimeData().hasFormat(FOLDERCARD_MIME_TYPE):
                raw = bytes(event.mimeData().data(FOLDERCARD_MIME_TYPE)).decode("utf-8", errors="replace")
                dest_dir = Path(self._work_dir)
                dest_norm = self._normalize_path(str(dest_dir))
                for src in (p for p in raw.split('|') if p):
                    if os.path.isdir(src) and self._normalize_path(src) != dest_norm:
                        self._move_or_copy_folder_into_dir(src, dest_dir, is_copy=is_copy)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            logger.debug(f"PAGETHUMBNAIL drop: data={data}, drop_pos={drop_pos}")
            self._handle_page_extraction(data, drop_pos)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(FOLDERCARD_MIME_TYPE):
            raw = bytes(event.mimeData().data(FOLDERCARD_MIME_TYPE)).decode("utf-8", errors="replace")
            modifiers = QApplication.keyboardModifiers()
            is_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            dest_dir = Path(self._work_dir)
            dest_norm = self._normalize_path(str(dest_dir))
            for src in (p for p in raw.split('|') if p):
                if os.path.isdir(src) and self._normalize_path(src) != dest_norm:
                    self._move_or_copy_folder_into_dir(src, dest_dir, is_copy=is_copy)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            logger.debug(f"URL drop: {event.mimeData().urls()}")
            self._handle_external_file_drop(event.mimeData().urls())
            event.acceptProposedAction()
        else:
            logger.debug("Unknown drop format, ignoring")
    def _handle_cross_window_card_drop(
        self,
        source_paths: list[str],
        drop_pos,
        *,
        is_copy: bool,
    ) -> None:
        """Move or copy PDFs from another MainWindow into this window."""
        dest_dir = Path(self._work_dir)
        valid = [p for p in source_paths if p and os.path.exists(p)]
        if not valid:
            return

        # Avoid trying to move into the same directory.
        dest_norm = self._normalize_path(str(dest_dir))
        valid = [
            p for p in valid
            if self._normalize_path(os.path.dirname(p)) != dest_norm
        ]
        if not valid:
            return

        insert_index = self._get_drop_index(drop_pos)
        if insert_index is None or insert_index < 0:
            insert_index = len(self._cards)

        actually_copied: list[tuple[str, str]] = []
        for src in valid:
            source_win = self._find_window_by_path(src)
            new_path = str(
                ensure_unique_path(
                    dest_dir,
                    os.path.basename(src),
                    pattern="{stem}({i}){ext}",
                )
            )
            try:
                self._register_internal_add([new_path])
                if source_win is not None and not is_copy:
                    source_win._register_internal_remove([src])
                shutil.copy2(src, new_path)
                if not is_copy:
                    try:
                        send2trash(src)
                    except Exception:
                        logger.debug("send2trash failed for %s", src, exc_info=True)
                actually_copied.append((src, new_path))
            except Exception as e:
                logger.debug("Cross-window copy failed %s -> %s: %s", src, new_path, e)

        if not actually_copied:
            return

        # Insert cards at drop position in this window; update source window.
        for offset, (_src, dest) in enumerate(actually_copied):
            self._add_card(dest, insert_index=insert_index + offset)
        self._sort_order = "manual"
        self._refresh_grid()

        def undo_move() -> None:
            for src, dest in actually_copied:
                try:
                    if is_copy:
                        if os.path.exists(dest):
                            self._register_internal_remove([dest])
                            send2trash(dest)
                    else:
                        if not os.path.exists(src) and os.path.exists(dest):
                            source_win2 = self._find_window_by_workdir(os.path.dirname(src))
                            if source_win2 is not None:
                                source_win2._register_internal_add([src])
                            shutil.copy2(dest, src)
                            self._register_internal_remove([dest])
                            send2trash(dest)
                except Exception:
                    logger.debug("undo cross-window failed for %s -> %s", src, dest, exc_info=True)

        def redo_move() -> None:
            for src, dest in actually_copied:
                try:
                    if is_copy:
                        if not os.path.exists(dest) and os.path.exists(src):
                            self._register_internal_add([dest])
                            shutil.copy2(src, dest)
                    else:
                        if not os.path.exists(dest) and os.path.exists(src):
                            self._register_internal_add([dest])
                            source_win2 = self._find_window_by_path(src)
                            if source_win2 is not None:
                                source_win2._register_internal_remove([src])
                            shutil.copy2(src, dest)
                            send2trash(src)
                except Exception:
                    logger.debug("redo cross-window failed for %s -> %s", src, dest, exc_info=True)

        action = "Copy" if is_copy else "Move"
        self._undo_manager.add_action(UndoAction(
            description=f"{action} {len(actually_copied)} file(s)",
            undo_func=undo_move,
            redo_func=redo_move,
        ))
    def _handle_card_drop(self, source_paths_str: str, drop_pos) -> None:
        """Handle internal card drop for reordering.
        
        Supports multiple selected cards.
        """
        source_paths = source_paths_str.split('|')
        source_cards = []
        for path in source_paths:
            for card in self._cards:
                if card.pdf_path == path:
                    source_cards.append(card)
                    break

        if not source_cards:
            return

        target_idx = self._get_drop_index(drop_pos)
        if target_idx == -1:
            return

        # Check if any move is needed
        source_indices = [self._cards.index(c) for c in source_cards]
        if all(idx == target_idx + i for i, idx in enumerate(source_indices)):
            return  # Already in place

        # Store paths instead of widget references
        old_paths = [card.pdf_path for card in self._cards]
        old_sort_order = self._sort_order
        old_sort_ascending = self._sort_ascending

        # Remove source cards
        for card in source_cards:
            self._cards.remove(card)

        # Calculate adjusted insert position
        insert_idx = target_idx - sum(1 for i in source_indices if i < target_idx)
        insert_idx = max(0, min(insert_idx, len(self._cards)))

        # Insert in order
        for i, card in enumerate(source_cards):
            self._cards.insert(insert_idx + i, card)

        self._sort_order = "manual"
        self._refresh_grid()

        # Select moved cards
        self._clear_selection()
        for card in source_cards:
            card.set_selected(True)
            self._selected_cards.append(card)

        # Store new paths for redo
        new_paths = [card.pdf_path for card in self._cards]
        moved_paths = source_paths  # Paths of moved cards for re-selection

        def undo_reorder():
            self._rebuild_cards_from_paths(old_paths)
            self._sort_order = old_sort_order
            self._sort_ascending = old_sort_ascending
            self._refresh_grid()

        def redo_reorder():
            self._rebuild_cards_from_paths(new_paths)
            self._sort_order = "manual"
            self._refresh_grid()
            # Re-select moved cards
            self._clear_selection()
            for card in self._cards:
                if card.pdf_path in moved_paths:
                    card.set_selected(True)
                    self._selected_cards.append(card)

        self._undo_manager.add_action(UndoAction(
            description=f"Move {len(source_cards)} card(s)",
            undo_func=undo_reorder,
            redo_func=redo_reorder
        ))
    def _handle_card_copy(self, source_paths_str: str, drop_pos) -> None:
        """Handle Ctrl+drag copy operation (insert at position).
        
        Creates copies of source PDFs and inserts them at drop position.
        """
        source_paths = source_paths_str.split('|')
        target_idx = self._get_drop_index(drop_pos)
        if target_idx == -1:
            target_idx = len(self._cards)
        
        copied_paths = []
        copied_cards = []
        
        try:
            # Copy files
            for src_path in source_paths:
                new_path = str(
                    ensure_unique_path(
                        Path(src_path).parent,
                        Path(src_path).name,
                        pattern="{stem}({i}){ext}",
                        use_original=False,
                    )
                )
                self._register_internal_add([new_path])
                shutil.copy2(src_path, new_path)
                copied_paths.append(new_path)
            
            # Add cards for copied files
            for i, new_path in enumerate(copied_paths):
                card = self._connect_card_signals(
                    PDFCard(
                        new_path,
                        card_width=self._preview_card_width,
                        thumb_size=self._preview_thumb_size,
                    )
                )
                
                insert_idx = target_idx + i
                if insert_idx >= len(self._cards):
                    self._cards.append(card)
                else:
                    self._cards.insert(insert_idx, card)
                copied_cards.append(card)
            
            self._sort_order = "manual"
            self._refresh_grid()
            
            # Select copied cards
            self._clear_selection()
            for card in copied_cards:
                card.set_selected(True)
                self._selected_cards.append(card)
            
            # Store paths for undo/redo (no widget references)
            new_paths = [card.pdf_path for card in self._cards]
            old_paths = [p for p in new_paths if p not in copied_paths]
            
            def undo_copy():
                # Delete copied files and rebuild
                self._register_internal_remove(copied_paths)
                for path in copied_paths:
                    if os.path.exists(path):
                        send2trash(path)
                self._rebuild_cards_from_paths(old_paths)
                self._refresh_grid()
            
            def redo_copy():
                # Re-copy files if needed and rebuild
                for i, src_path in enumerate(source_paths):
                    new_path = copied_paths[i]
                    if not os.path.exists(new_path):
                        self._register_internal_add([new_path])
                        shutil.copy2(src_path, new_path)
                self._rebuild_cards_from_paths(new_paths)
                self._sort_order = "manual"
                self._refresh_grid()
                # Re-select copied cards
                self._clear_selection()
                for card in self._cards:
                    if card.pdf_path in copied_paths:
                        card.set_selected(True)
                        self._selected_cards.append(card)
            
            self._undo_manager.add_action(UndoAction(
                description=f"Copy {len(copied_paths)} file(s)",
                undo_func=undo_copy,
                redo_func=redo_copy
            ))
            
        except Exception as e:
            # Rollback on error
            for path in copied_paths:
                self._internal_adds.discard(self._normalize_path(path))
            for path in copied_paths:
                if os.path.exists(path):
                    os.unlink(path)
            for card in copied_cards:
                if card in self._cards:
                    self._cards.remove(card)
                card.deleteLater()
            raise
    def _handle_card_copy_merge(self, source_paths_str: str, target_card: PDFCard) -> None:
        """Handle Ctrl+drag copy merge operation.
        
        Copies source PDF pages to the beginning of target PDF.
        Source files remain unchanged.
        """
        from src.utils.pdf_utils import merge_pdfs_in_place

        source_paths = source_paths_str.split('|')
        target_path = target_card.pdf_path

        # Create backup of target
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as backup:
            backup_path = backup.name
        shutil.copy2(target_path, backup_path)

        try:
            # Merge: source pages first, then target pages
            merge_pdfs_in_place(target_path, source_paths, insert_at=0)

            target_card.refresh()
            
            # Select target card
            self._clear_selection()
            target_card.set_selected(True)
            self._selected_cards.append(target_card)
            
            # Prepare undo/redo
            def undo_copy_merge():
                shutil.copy2(backup_path, target_path)
                target_card.refresh()
            
            def redo_copy_merge():
                merge_pdfs_in_place(target_path, source_paths, insert_at=0)
                target_card.refresh()
            
            self._undo_manager.add_action(UndoAction(
                description=f"Copy merge {len(source_paths)} file(s)",
                undo_func=undo_copy_merge,
                redo_func=redo_copy_merge
            ))
            
        except PdfWritePermissionError as error:
            shutil.copy2(backup_path, target_path)
            self._handle_pdf_write_permission_denied(error)
            return
        except Exception:
            # Rollback on error
            shutil.copy2(backup_path, target_path)
            raise
    def _get_drop_index(self, pos) -> int:
        """Get the index where the drop should occur."""
        pad = max(10, self._grid_layout.spacing())
        for i, card in enumerate(self._cards):
            card_rect = card.geometry()
            expanded_rect = card_rect.adjusted(-pad, -pad, pad, pad)
            if expanded_rect.contains(pos):
                center_x = card_rect.center().x()
                if pos.x() < center_x:
                    return i
                else:
                    return i + 1

        if self._cards:
            return len(self._cards)
        return 0
    def _handle_external_file_drop(self, urls) -> None:
        """Handle external file drop (import).

        Accepts both files with importable extensions and whole directories
        (which are walked recursively while preserving structure).
        """
        paths: list[str] = []
        for url in urls:
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isdir(local):
                paths.append(local)
            else:
                ext = os.path.splitext(local)[1].lower()
                if ext in _IMPORT_EXTS or ext in _ZIP_EXTS:
                    paths.append(local)
        if paths:
            self._import_paths(paths)
    def _handle_page_extraction(
        self,
        data: str,
        drop_pos=None,
        *,
        dest_dir: Path | None = None,
    ) -> None:
        """Handle page extraction from page edit window.

        When ``dest_dir`` is provided (folder-card drop), the extracted page
        is always saved to that directory as a new PDF — regardless of
        drop position — and no existing card is merged into.
        """
        from src.utils.pdf_utils import extract_pages, remove_pages, insert_pages
        from src.views.page_edit_window import PageEditWindow

        logger.debug(f"_handle_page_extraction called with data={data}, drop_pos={drop_pos}")

        pdf_path, page_nums_str = data.split('|')
        page_nums = sorted(set(int(n) for n in page_nums_str.split(',') if n))
        logger.debug(f"Parsed pdf_path={pdf_path}, page_nums={page_nums}")

        if not page_nums:
            logger.debug("No page_nums, returning early")
            return

        if drop_pos is None and dest_dir is None:
            logger.debug("No drop_pos and no dest_dir, ignoring")
            return

        effective_work_dir = Path(dest_dir) if dest_dir is not None else Path(self._work_dir)

        if dest_dir is not None:
            target_card = None
        else:
            target_card = self._get_card_at_pos(drop_pos) if drop_pos is not None else None
        logger.debug(f"target_card at drop_pos: {target_card}")
        is_new_target = target_card is None
        if not is_new_target:
            if target_card.is_locked:
                logger.debug("Target card locked, ignoring")
                return
            if target_card.pdf_path == pdf_path:
                logger.debug("Same file, ignoring")
                return
            target_path = target_card.pdf_path
            insert_index = None
        else:
            target_path = str(
                ensure_unique_path(
                    effective_work_dir,
                    Path(pdf_path).name,
                    pattern="{stem}({i}){ext}",
                    use_original=False,
                )
            )
            insert_index = (
                self._get_drop_index(drop_pos)
                if (drop_pos is not None and dest_dir is None)
                else None
            )
            logger.debug(f"No target card, creating new PDF at {target_path} (insert_index={insert_index})")

        modifiers = QApplication.keyboardModifiers()
        is_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        logger.debug(f"is_copy={is_copy}")

        old_sort_order = self._sort_order
        old_sort_ascending = self._sort_ascending
        old_paths = [card.pdf_path for card in self._cards]

        backup_dir = tempfile.mkdtemp(prefix="pdfas_page_extract_")
        source_backup = None
        target_backup = None
        if not is_copy:
            source_backup = Path(backup_dir) / Path(pdf_path).name
            shutil.copy2(pdf_path, source_backup)
        if not is_new_target and target_path:
            target_backup = Path(backup_dir) / Path(target_path).name
            shutil.copy2(target_path, target_backup)

        source_was_deleted = False

        def _refresh_card(path: str) -> None:
            for card in self._cards:
                if card.pdf_path == path:
                    card.refresh()
                    break

        def _select_single_card(path: str) -> None:
            card = self._get_card_by_path(path)
            if card:
                self._clear_selection()
                card.set_selected(True)
                self._selected_cards.append(card)

        def _reload_page_windows(paths: list[str], removed_indices: dict[str, list[int]] | None = None) -> None:
            """PageEditWindowのページを更新する

            Args:
                paths: 更新対象のPDFパスのリスト
                removed_indices: パスごとの削除されたページインデックスの辞書（差分更新用）
            """
            for window in QApplication.topLevelWidgets():
                if isinstance(window, PageEditWindow) and window._pdf_path in paths:
                    logger.debug(f"Reloading pages in PageEditWindow for {window._pdf_path}")
                    # 差分更新が可能な場合は差分更新を使用
                    if removed_indices and window._pdf_path in removed_indices:
                        indices = removed_indices[window._pdf_path]
                        logger.debug(f"Using differential update for indices: {indices}")
                        window._remove_page_thumbnails(indices)
                    else:
                        window._load_pages()

        def do_extraction() -> bool:
            nonlocal source_was_deleted
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp_path = tmp.name
                logger.debug(f"Extracting pages to tmp_path={tmp_path}")
                if not extract_pages(pdf_path, tmp_path, page_nums):
                    logger.debug("extract_pages failed, returning")
                    return False

                if is_new_target:
                    # Register internal add on whatever window owns the target dir.
                    dest_win_for_add = self._find_window_by_workdir(os.path.dirname(target_path)) or self
                    dest_win_for_add._register_internal_add([target_path])
                    shutil.move(tmp_path, target_path)
                    tmp_path = None
                    if dest_win_for_add is self:
                        self._add_card(target_path, insert_index=insert_index)
                        self._sort_order = "manual"
                        self._refresh_grid()
                        _select_single_card(target_path)
                    else:
                        dest_win_for_add._add_card(target_path, insert_index=None)
                        dest_win_for_add._sort_order = "manual"
                        dest_win_for_add._refresh_grid()
                else:
                    logger.debug(f"Appending pages to target_card: {target_path}")
                    insert_pages(target_path, tmp_path, [0] * len(page_nums))
                    _refresh_card(target_path)
                    _reload_page_windows([target_path])
                    _select_single_card(target_path)

                if not is_copy:
                    logger.debug("Removing pages from source")
                    source_was_deleted = remove_pages(pdf_path, page_nums)
                    if source_was_deleted:
                        self._register_internal_remove([pdf_path])
                        logger.debug("Source file deleted, closing PageEditWindow and removing card")
                        for window in QApplication.topLevelWidgets():
                            if isinstance(window, PageEditWindow) and window._pdf_path == pdf_path:
                                window.close()
                                break
                        self._remove_card(pdf_path)
                        self._refresh_grid()
                    else:
                        # 差分更新を使用（page_numsが削除されたインデックス）
                        _reload_page_windows([pdf_path], {pdf_path: page_nums})
                
                return True
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    logger.debug(f"Cleaning up tmp_path={tmp_path}")
                    os.unlink(tmp_path)

        def undo_extraction() -> None:
            if is_new_target and target_path and os.path.exists(target_path):
                self._register_internal_remove([target_path])
                send2trash(target_path)
            if target_backup and target_path and os.path.exists(target_backup):
                shutil.copy2(target_backup, target_path)
                _refresh_card(target_path)
                _reload_page_windows([target_path])

            if source_backup and os.path.exists(source_backup):
                self._register_internal_add([pdf_path])
                shutil.copy2(source_backup, pdf_path)
                _reload_page_windows([pdf_path])
            self._sort_order = old_sort_order
            self._sort_ascending = old_sort_ascending
            self._rebuild_cards_from_paths(old_paths)
            self._refresh_grid()

        def redo_extraction() -> None:
            do_extraction()

        try:
            if not do_extraction():
                return
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return

        action = "Copy" if is_copy else "Move"
        self._undo_manager.add_action(UndoAction(
            description=f"{action} {len(page_nums)} page(s)",
            undo_func=undo_extraction,
            redo_func=redo_extraction
        ))
