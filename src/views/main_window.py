# src/views/main_window.py
"""Main window for JusticePDF application."""
import os
import sys
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QScrollArea, QGridLayout,
    QFileDialog, QInputDialog, QMessageBox, QFrame, QRubberBand, QProgressDialog
)
from PyQt6.QtCore import Qt, QSize, QTimer, QEvent, QPoint, QRect
from PyQt6.QtGui import QKeySequence
from send2trash import send2trash

from src.views.pdf_card import PDFCard, PDFCARD_MIME_TYPE
from src.views.view_helpers import (
    clear_selection,
    log_undo_state,
    register_shortcuts,
    viewport_width_or_fallback,
)
from src.controllers.folder_watcher import FolderWatcher
from src.models.undo_manager import UndoManager, UndoAction
from src.utils.pdf_utils import (
    rotate_pages, get_page_count, get_pdf_metadata_title, update_pdf_metadata_title,
    clear_pixmap_cache, clear_pixmap_cache_for_path
)
from src.utils.path_utils import ensure_unique_path
from src.utils.windows_shell import show_native_file_context_menu

logger = logging.getLogger(__name__)

_OFFICE_EXTS = {
    ".doc", ".docx", ".docm",
    ".xls", ".xlsx", ".xlsm",
    ".ppt", ".pptx",
}
_IMPORT_EXTS = {".pdf"} | _OFFICE_EXTS


class MainWindow(QMainWindow):
    """Main application window.

    Displays PDF files as cards in a grid layout.
    """

    PREVIEW_THUMB_MIN = 80
    PREVIEW_THUMB_MAX = 400
    PREVIEW_THUMB_STEP = 20

    def __init__(self):
        super().__init__()
        self._cards: list[PDFCard] = []
        self._selected_cards: list[PDFCard] = []
        self._sort_order = "manual"  # "name", "date", or "manual"
        self._sort_ascending = True
        # Ensure initial layout uses the same logic as subsequent resizes
        self._did_initial_grid_layout = False
        self._internal_adds: set[str] = set()
        self._internal_removes: set[str] = set()
        self._pending_rename_old_to_new: dict[str, str] = {}
        self._pending_rename_new_to_old: dict[str, str] = {}
        self._pending_rename_removed: set[str] = set()
        self._pending_rename_added: set[str] = set()
        self._preview_thumb_size = PDFCard.THUMBNAIL_SIZE
        self._preview_card_ratio = PDFCard.CARD_WIDTH / PDFCard.THUMBNAIL_SIZE
        self._preview_card_width = int(round(self._preview_thumb_size * self._preview_card_ratio))

        # Zoom debounce timer for Ctrl+wheel
        self._zoom_debounce_timer = QTimer(self)
        self._zoom_debounce_timer.setSingleShot(True)
        self._zoom_debounce_timer.setInterval(150)
        self._zoom_debounce_timer.timeout.connect(self._render_visible_cards_hq)

        # Debounce file modified events (path -> single-shot timer)
        self._modified_timers: dict[str, QTimer] = {}
        # Track last processed mtime to avoid redundant refreshes
        self._modified_last_mtime: dict[str, float] = {}
        self._modified_debounce_ms = 250

        # Setup working directory
        self._work_dir = Path.home() / "Documents" / "PDFs"
        self._work_dir.mkdir(parents=True, exist_ok=True)

        # Undo manager
        self._undo_manager = UndoManager(max_size=20)

        # Drop indicator
        self._drop_indicator = None
        self._drop_indicator_index = -1

        # Rubber band selection
        self._rubber_band = None
        self._rubber_band_origin = None

        # Setup UI
        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()
        self._undo_manager.add_listener(self._on_undo_manager_changed)

        # Setup folder watcher
        self._watcher = FolderWatcher(str(self._work_dir))
        self._watcher.file_added.connect(self._on_file_added)
        self._watcher.file_removed.connect(self._on_file_removed)
        self._watcher.file_modified.connect(self._on_file_modified)
        self._watcher.start()

        # Load existing files
        self._load_existing_files()

    def _setup_ui(self) -> None:
        """Set up the main UI."""
        self.setWindowTitle("JusticePDF")
        self.resize(1000, 700)
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Keep as an attribute so we can reliably use viewport width for column calculation.
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.viewport().installEventFilter(self)
        layout.addWidget(self._scroll_area)

        self._container = QWidget()
        self._container.setAcceptDrops(True)
        self._scroll_area.setWidget(self._container)

        self._grid_layout = QGridLayout(self._container)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Drop indicator line
        self._drop_indicator = QFrame(self._container)
        self._drop_indicator.setFrameShape(QFrame.Shape.VLine)
        self._drop_indicator.setStyleSheet("background-color: #007bff;")
        self._drop_indicator.setFixedWidth(3)
        self._drop_indicator.hide()

        # Rubber band for selection
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self._container)

    def _setup_toolbar(self) -> None:
        """Set up the toolbar."""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("Redo")
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addSeparator()

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename)
        toolbar.addWidget(self._rename_btn)

        toolbar.addSeparator()

        self._import_btn = QPushButton("Import")
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        self._export_btn = QPushButton("Export")
        self._export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self._export_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._refresh_btn)

        toolbar.addSeparator()

        self._rotate_btn = QPushButton("Rotate")
        self._rotate_btn.clicked.connect(self._on_rotate)
        toolbar.addWidget(self._rotate_btn)

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self._select_all_btn)

        self._sort_name_btn = QPushButton("Sort Name")
        self._sort_name_btn.clicked.connect(self._on_sort_by_name)
        toolbar.addWidget(self._sort_name_btn)

        self._sort_date_btn = QPushButton("Sort Date")
        self._sort_date_btn.clicked.connect(self._on_sort_by_date)
        toolbar.addWidget(self._sort_date_btn)

        self._update_button_states()

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        register_shortcuts(
            self,
            (
                (QKeySequence.StandardKey.Undo, self._on_undo),
                (QKeySequence.StandardKey.Redo, self._on_redo),
                (QKeySequence.StandardKey.Delete, self._on_delete),
                (QKeySequence(Qt.Key.Key_F2), self._on_rename),
                (QKeySequence("Shift+F2"), self._on_rename_pdf_title),
                (QKeySequence.StandardKey.SelectAll, self._on_select_all),
                (QKeySequence("Ctrl+E"), self._on_export),
            ),
        )

    def _update_button_states(self) -> None:
        """Update toolbar button enabled states."""
        has_selection = len(self._selected_cards) > 0
        has_any = len(self._cards) > 0
        self._delete_btn.setEnabled(has_selection)
        self._rename_btn.setEnabled(len(self._selected_cards) == 1)
        self._rotate_btn.setEnabled(has_selection)
        self._undo_btn.setEnabled(self._undo_manager.can_undo())
        self._redo_btn.setEnabled(self._undo_manager.can_redo())
        self._export_btn.setEnabled(has_any)

    def _debug_undo_state(self, reason: str) -> None:
        log_undo_state(
            logger=logger,
            context_name="MainWindow",
            reason=reason,
            undo_button=self._undo_btn,
            redo_button=self._redo_btn,
            undo_manager=self._undo_manager,
        )

    def _on_undo_manager_changed(self, reason: str) -> None:
        self._update_button_states()
        self._debug_undo_state(reason)

    def _clear_undo_history(self) -> None:
        """Clear undo/redo history (for external file changes)."""
        self._undo_manager.clear()
        self._update_button_states()

    def _normalize_path(self, path: str) -> str:
        """Normalize paths for internal tracking."""
        return os.path.normcase(os.path.abspath(path))

    def _register_internal_add(self, paths: list[str]) -> None:
        """Mark paths as internally added to avoid clearing undo history."""
        for path in paths:
            self._internal_adds.add(self._normalize_path(path))

    def _register_internal_remove(self, paths: list[str]) -> None:
        """Mark paths as internally removed to avoid clearing undo history."""
        for path in paths:
            self._internal_removes.add(self._normalize_path(path))

    def _track_pending_rename(self, old_path: str, new_path: str) -> None:
        old_norm = self._normalize_path(old_path)
        new_norm = self._normalize_path(new_path)
        self._pending_rename_old_to_new[old_norm] = new_norm
        self._pending_rename_new_to_old[new_norm] = old_norm

    def _finalize_pending_rename(self, old_norm: str, new_norm: str) -> None:
        if old_norm in self._pending_rename_removed and new_norm in self._pending_rename_added:
            self._pending_rename_removed.discard(old_norm)
            self._pending_rename_added.discard(new_norm)
            self._pending_rename_old_to_new.pop(old_norm, None)
            self._pending_rename_new_to_old.pop(new_norm, None)

    def _update_page_edit_windows_for_rename(self, old_path: str, new_path: str) -> None:
        from PyQt6.QtWidgets import QApplication
        from src.views.page_edit_window import PageEditWindow

        new_name = os.path.basename(new_path)
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, PageEditWindow) and widget._pdf_path == old_path:
                widget._pdf_path = new_path
                widget.setWindowTitle(f"JusticePDF - Edit: {new_name}")

    def _refresh_page_edit_windows_for_paths(self, paths: list[str]) -> None:
        """指定PDFのPageEditWindowを外部更新として再描画する。"""
        if not paths:
            return

        from src.views.page_edit_window import PageEditWindow

        normalized_paths = {self._normalize_path(path) for path in paths}
        for widget in QApplication.topLevelWidgets():
            if (
                isinstance(widget, PageEditWindow)
                and self._normalize_path(widget._pdf_path) in normalized_paths
            ):
                widget.refresh_from_disk()

    def _get_open_page_edit_windows(self) -> list[object]:
        from src.views.page_edit_window import PageEditWindow

        return [
            widget
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, PageEditWindow)
        ]

    def _refresh_cards_for_paths(self, paths: list[str]) -> None:
        normalized_paths = {self._normalize_path(path) for path in paths}
        for card in self._cards:
            if self._normalize_path(card.pdf_path) in normalized_paths:
                card.refresh()

    def _refresh_all_views(self) -> None:
        card_paths = [card.pdf_path for card in self._cards]
        if card_paths:
            self._refresh_cards_for_paths(card_paths)
            self._refresh_grid()
        for widget in self._get_open_page_edit_windows():
            widget.refresh_from_disk()

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

    def _load_existing_files(self) -> None:
        """Load existing PDF files from the work directory."""
        for pdf_path in self._watcher.get_pdf_files():
            self._add_card(pdf_path)
        # Do not refresh here: at this point viewport width is often 0 and causes "initial-only" layout.
        # Initial refresh is triggered once after the window is shown (showEvent).

    def showEvent(self, event) -> None:
        """Run the first grid layout after the window is shown so viewport width is valid."""
        super().showEvent(event)
        if not self._did_initial_grid_layout:
            self._did_initial_grid_layout = True
            QTimer.singleShot(0, self._refresh_grid)

    def _grid_available_width(self) -> int:
        """Width source for column calculation (always consistent)."""
        # Fallback is only for the very early lifecycle before viewport width is ready.
        return viewport_width_or_fallback(
            getattr(self, "_scroll_area", None),
            self.width(),
        )

    def _connect_card_signals(self, card: PDFCard) -> PDFCard:
        """Connect all card-level signals used by the main window."""
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)
        card.dropped_on.connect(self._on_card_merge)
        card.context_menu_requested.connect(self._on_card_context_menu_requested)
        return card

    def _add_card(self, pdf_path: str, insert_index: int | None = None) -> PDFCard:
        """Add a new card for a PDF file."""
        card = self._connect_card_signals(
            PDFCard(
                pdf_path,
                card_width=self._preview_card_width,
                thumb_size=self._preview_thumb_size,
            )
        )
        if insert_index is None or insert_index >= len(self._cards):
            self._cards.append(card)
        else:
            self._cards.insert(max(0, insert_index), card)
        return card

    def _rebuild_cards_from_paths(self, paths: list[str]) -> None:
        """Rebuild PDFCards from a list of paths.
        
        This method safely disposes of all existing cards and creates
        new ones from the given paths. Used by undo/redo operations
        to avoid holding Widget references in closures.
        """
        # Safely dispose of existing cards
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        self._selected_cards.clear()
        
        # Create new cards for existing files
        for path in paths:
            if os.path.exists(path):
                card = self._connect_card_signals(
                    PDFCard(
                        path,
                        card_width=self._preview_card_width,
                        thumb_size=self._preview_thumb_size,
                    )
                )
                self._cards.append(card)

    def _remove_card(self, pdf_path: str) -> None:
        """Remove a card for a PDF file."""
        logger.debug(f"_remove_card called for {pdf_path}")
        for card in self._cards[:]:
            if card.pdf_path == pdf_path:
                logger.debug(f"Found card to remove: {card}")
                if card in self._selected_cards:
                    self._selected_cards.remove(card)
                self._cards.remove(card)
                card.deleteLater()
                logger.debug("Card removed successfully")
                break
        else:
            logger.debug(f"No card found for {pdf_path}")

    def _refresh_grid(self, *, sort_cards: bool = False) -> None:
        """Refresh the grid layout."""
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if sort_cards:
            self._sort_cards()

        available_width = self._grid_available_width()
        spacing = self._grid_layout.horizontalSpacing()
        if spacing < 0:
            spacing = self._grid_layout.spacing()
        spacing = int(spacing)
        m = self._grid_layout.contentsMargins()
        usable = max(1, int(available_width) - int(m.left() + m.right()))

        # n*W + (n-1)*S <= usable  => cols = floor((usable + S) / (W + S))
        w = int(self._preview_card_width)
        cols = max(1, int((usable + spacing) // (w + spacing)))
        
        for i, card in enumerate(self._cards):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(card, row, col)

    def _sort_cards(self) -> None:
        """Sort cards based on current sort order."""
        if self._sort_order == "name":
            self._cards.sort(key=lambda c: c.filename.lower(), reverse=not self._sort_ascending)
        elif self._sort_order == "date":
            def get_mtime(card: PDFCard) -> float:
                try:
                    return os.path.getmtime(card.pdf_path)
                except OSError:
                    return 0.0
            self._cards.sort(key=get_mtime, reverse=not self._sort_ascending)

    def _get_visible_cards(self) -> list[PDFCard]:
        """Return cards currently visible in the scroll area viewport."""
        if not hasattr(self, "_scroll_area") or not self._cards:
            return []
        viewport = self._scroll_area.viewport()
        top_left = self._container.mapFrom(viewport, QPoint(0, 0))
        bottom_right = self._container.mapFrom(
            viewport,
            QPoint(max(0, viewport.width() - 1), max(0, viewport.height() - 1)),
        )
        visible_rect = QRect(top_left, bottom_right).normalized()
        return [card for card in self._cards if card.geometry().intersects(visible_rect)]

    def _render_visible_cards_hq(self) -> None:
        """Re-render visible cards at full quality after zoom debounce."""
        for card in self._get_visible_cards():
            card.render_high_quality()

    def _clear_selection(self) -> None:
        """Clear all selections."""
        clear_selection(self._selected_cards)
        self._update_button_states()

    def _selected_card_paths_in_grid_order(self) -> list[str]:
        """Return selected card paths ordered by the visible card grid."""
        return [card.pdf_path for card in self._cards if card in self._selected_cards]

    def _set_preview_thumb_size(self, size: int) -> None:
        size = max(self.PREVIEW_THUMB_MIN, min(self.PREVIEW_THUMB_MAX, int(size)))
        if size == self._preview_thumb_size:
            return
        self._preview_thumb_size = size
        self._preview_card_width = int(round(self._preview_thumb_size * self._preview_card_ratio))
        for card in self._cards:
            card.set_preview_size_fast(self._preview_card_width, self._preview_thumb_size)
        self._refresh_grid()
        self._zoom_debounce_timer.start()

    def eventFilter(self, obj, event) -> bool:
        scroll_area = getattr(self, "_scroll_area", None)
        if scroll_area and obj is scroll_area.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta != 0:
                    step = self.PREVIEW_THUMB_STEP if delta > 0 else -self.PREVIEW_THUMB_STEP
                    self._set_preview_thumb_size(self._preview_thumb_size + step)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:
        """Handle mouse press - clear selection when clicking empty area or start rubber band."""
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            while child is not None:
                if isinstance(child, PDFCard):
                    super().mousePressEvent(event)
                    return
                child = child.parent()
            # Start rubber band selection on empty area
            container_pos = self._container.mapFrom(self, event.pos())
            self._rubber_band_origin = container_pos
            self._rubber_band.setGeometry(container_pos.x(), container_pos.y(), 0, 0)
            self._rubber_band.show()
            self._clear_selection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for rubber band selection."""
        if self._rubber_band_origin is not None:
            container_pos = self._container.mapFrom(self, event.pos())
            from PyQt6.QtCore import QRect
            rect = QRect(self._rubber_band_origin, container_pos).normalized()
            self._rubber_band.setGeometry(rect)
            # Select cards intersecting with rubber band
            self._clear_selection()
            for card in self._cards:
                if rect.intersects(card.geometry()):
                    card.set_selected(True)
                    self._selected_cards.append(card)
            self._update_button_states()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Handle mouse release to end rubber band selection."""
        if event.button() == Qt.MouseButton.LeftButton and self._rubber_band_origin is not None:
            self._rubber_band.hide()
            self._rubber_band_origin = None
        super().mouseReleaseEvent(event)

    def _on_card_clicked(self, card: PDFCard) -> None:
        """Handle card click."""
        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if card in self._selected_cards:
                card.set_selected(False)
                self._selected_cards.remove(card)
            else:
                card.set_selected(True)
                self._selected_cards.append(card)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            if self._selected_cards:
                start_idx = self._cards.index(self._selected_cards[-1])
                end_idx = self._cards.index(card)
                if start_idx > end_idx:
                    start_idx, end_idx = end_idx, start_idx
                for i in range(start_idx, end_idx + 1):
                    if self._cards[i] not in self._selected_cards:
                        self._cards[i].set_selected(True)
                        self._selected_cards.append(self._cards[i])
            else:
                card.set_selected(True)
                self._selected_cards.append(card)
        else:
            if card in self._selected_cards:
                pass  # Preserve multi-selection
            else:
                self._clear_selection()
                card.set_selected(True)
                self._selected_cards.append(card)

        self._update_button_states()

    def _on_card_context_menu_requested(self, card: PDFCard, global_pos: QPoint) -> None:
        """Handle Explorer-style right-click selection and menu opening."""
        if card not in self._selected_cards:
            self._clear_selection()
            card.set_selected(True)
            self._selected_cards.append(card)
            self._update_button_states()

        show_native_file_context_menu(
            int(self.winId()),
            self._selected_card_paths_in_grid_order(),
            global_pos,
        )

    def _on_card_double_clicked(self, card: PDFCard) -> None:
        """Handle card double-click - open page edit window."""
        from src.views.page_edit_window import PageEditWindow
        # If a window for this file is already open, bring it to front.
        for widget in QApplication.topLevelWidgets():
            if (
                isinstance(widget, PageEditWindow)
                and widget._pdf_path == card.pdf_path
                and widget.isVisible()
            ):
                if widget.isMinimized():
                    widget.setWindowState(
                        (widget.windowState() & ~Qt.WindowState.WindowMinimized)
                        | Qt.WindowState.WindowActive
                    )
                widget.show()
                widget.raise_()
                widget.activateWindow()
                # Ensure the card is locked while the window is visible.
                self.lock_card(card.pdf_path)
                return

        # Lock the card before opening the edit window
        self.lock_card(card.pdf_path)
        window = PageEditWindow(card.pdf_path, self._undo_manager, self)

        # 既存のPageEditWindowの数をカウント（カスケード用）
        existing_count = sum(
            1 for w in QApplication.topLevelWidgets()
            if isinstance(w, PageEditWindow) and w.isVisible() and w is not window
        )

        # メインウィンドウの右側に配置（カスケード）
        main_geo = self.geometry()
        screen = self.screen().availableGeometry()
        cascade_offset = 150  # ページアイコン約1.5個分

        new_x = main_geo.right() + 10

        # 画面からはみ出る場合は調整
        if new_x + window.width() > screen.right():
            new_x = screen.right() - window.width()

        # 下端判定: 画面半分はみ出してもOK
        max_y = screen.bottom() - window.height() // 2
        # 一番上に戻っても、またoffsetずつ下がる（サイクル）
        cycles_fit = max(1, (max_y - main_geo.top()) // cascade_offset + 1)
        new_y = main_geo.top() + ((existing_count % cycles_fit) * cascade_offset)

        window.move(new_x, new_y)
        window.show()

    def _on_card_merge(self, target_card: PDFCard, source_paths_str: str) -> None:
        """Handle card merge (drop cards on another card) with undo support.

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
        import tempfile
        from pathlib import Path

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

        # Backup involved PDFs (target + sources) so undo/redo is byte-stable
        backup_dir = tempfile.mkdtemp(prefix="justicepdf_merge_backup_")
        backups: dict[str, str] = {}
        for p in [*source_paths, target_path]:
            src = Path(p)
            dst = Path(backup_dir) / f"{src.stem}__{abs(hash(p))}{src.suffix}"
            shutil.copy2(p, dst)
            backups[p] = str(dst)

        def _select_paths(paths: list[str]) -> None:
            self._clear_selection()
            for card in self._cards:
                if card.pdf_path in paths:
                    card.set_selected(True)
                    self._selected_cards.append(card)
            self._update_button_states()

        # Post-selection is captured after do_merge runs once
        post_selected_paths: list[str] = []

        def do_merge() -> None:
            nonlocal post_selected_paths
            merge_pdfs_in_place(merge_dest_path, source_paths[1:] + [target_path])

            # Trash the target and other source PDFs (keep first source as merge destination)
            self._register_internal_remove([target_path] + source_paths[1:])
            send2trash(target_path)
            for p in source_paths[1:]:
                send2trash(p)

            # Rebuild cards from paths (avoid stale QWidget references)
            self._rebuild_cards_from_paths(new_paths)
            self._sort_order = "manual"
            self._refresh_grid()

            # Select merge destination (first source), and remember it for redo
            _select_paths([merge_dest_path])
            post_selected_paths = [merge_dest_path]

            card = self._get_card_by_path(merge_dest_path)
            if card:
                card.refresh()

        def undo_merge() -> None:
            self._register_internal_add(list(backups.keys()))
            # Restore file bytes
            for original_path, backup_path in backups.items():
                shutil.copy2(backup_path, original_path)

            self._rebuild_cards_from_paths(old_paths)
            self._refresh_grid()
            _select_paths(pre_selected_paths)

        def redo_merge() -> None:
            do_merge()
            # Ensure redo selection matches the post-merge selection
            if post_selected_paths:
                _select_paths(post_selected_paths)

        try:
            do_merge()
        except Exception:
            # Best-effort rollback
            try:
                undo_merge()
            except Exception:
                pass
            raise

        # Register undo action
        self._undo_manager.add_action(UndoAction(
            description=f"Merge {len(source_paths)} file(s)",
            undo_func=undo_merge,
            redo_func=redo_merge,
        ))

    def _on_file_added(self, path: str) -> None:
        """Handle new file added to folder."""
        clear_pixmap_cache_for_path(path)
        normalized = self._normalize_path(path)
        self._modified_last_mtime.pop(normalized, None)
        if normalized in self._pending_rename_new_to_old:
            self._internal_adds.discard(normalized)
            self._pending_rename_added.add(normalized)
            old_norm = self._pending_rename_new_to_old.get(normalized)
            if old_norm:
                self._finalize_pending_rename(old_norm, normalized)
            return
        for card in self._cards:
            if self._normalize_path(card.pdf_path) == normalized:
                # The path was reused for a different file before the old card was removed.
                # Refresh in place rather than treating this as a duplicate add.
                card.refresh()
                self._refresh_page_edit_windows_for_paths([path])
                self._refresh_grid()
                return
        if normalized in self._internal_adds:
            self._internal_adds.discard(normalized)
        else:
            self._clear_undo_history()
        new_card = self._add_card(path, insert_index=None)
        self._refresh_grid()

    def _on_file_removed(self, path: str) -> None:
        """Handle file removed from folder."""
        # If a save is in progress, there may be a pending debounced refresh.
        self._cancel_debounced_modified(path)
        clear_pixmap_cache_for_path(path)
        normalized = self._normalize_path(path)
        if normalized in self._pending_rename_old_to_new:
            self._internal_removes.discard(normalized)
            self._pending_rename_removed.add(normalized)
            new_norm = self._pending_rename_old_to_new.get(normalized)
            if new_norm:
                self._finalize_pending_rename(normalized, new_norm)
            return
        if os.path.exists(path):
            # Watchdog can deliver a stale remove after the path has already been recreated.
            self._internal_removes.discard(normalized)
            return
        if normalized in self._internal_removes:
            self._internal_removes.discard(normalized)
        else:
            self._clear_undo_history()
        self._remove_card(path)
        self._refresh_grid()
        self._update_button_states()

    def _on_file_modified(self, path: str) -> None:
        """Handle file modified.

        Many editors save by writing the file multiple times. Debounce per-path to avoid
        repeated expensive refreshes (PDF open, thumbnail regen, layout).
        """
        self._schedule_debounced_modified(path)

    def _schedule_debounced_modified(self, path: str) -> None:
        """Debounce refresh for a modified PDF path."""
        normalized = self._normalize_path(path)

        timer = self._modified_timers.get(normalized)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=path: self._process_debounced_modified(p))
            self._modified_timers[normalized] = timer

        # Restart debounce window on every modified event
        timer.start(self._modified_debounce_ms)

    def _cancel_debounced_modified(self, path: str) -> None:
        """Cancel any pending debounced refresh for the given path."""
        normalized = self._normalize_path(path)
        timer = self._modified_timers.pop(normalized, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._modified_last_mtime.pop(normalized, None)

    def _process_debounced_modified(self, path: str) -> None:
        """Run the actual refresh once after debounce window ends."""
        normalized = self._normalize_path(path)

        # File might be temporarily missing while being replaced.
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            logger.debug("modified debounce: file missing or inaccessible: %s", path)
            return

        last = self._modified_last_mtime.get(normalized)
        if last is not None and mtime == last:
            # No real change since last processed refresh.
            return

        self._modified_last_mtime[normalized] = mtime

        for card in self._cards:
            if self._normalize_path(card.pdf_path) == normalized:
                try:
                    card.refresh()
                except Exception:
                    logger.debug("modified debounce: card.refresh() failed for %s", path, exc_info=True)
                break

    def _on_undo(self) -> None:
        """Handle undo action."""
        self._undo_manager.undo()
        self._update_button_states()

    def _on_redo(self) -> None:
        """Handle redo action."""
        self._undo_manager.redo()
        self._update_button_states()

    def _on_refresh(self) -> None:
        """Reload cards and open edit windows from disk."""
        clear_pixmap_cache()
        self._refresh_all_views()

    def _on_delete(self) -> None:
        """Handle delete action."""
        if not self._selected_cards:
            return

        import tempfile
        import shutil
        from pathlib import Path

        paths = [card.pdf_path for card in self._selected_cards]

        backup_dir = tempfile.mkdtemp(prefix="pdfas_backup_")
        backups = {}
        for path in paths:
            backup_path = Path(backup_dir) / Path(path).name
            shutil.copy2(path, backup_path)
            backups[path] = str(backup_path)

        def do_delete():
            self._register_internal_remove(paths)
            for path in paths:
                send2trash(path)
            self._clear_selection()

        def undo_delete():
            self._register_internal_add(list(backups.keys()))
            for original_path, backup_path in backups.items():
                shutil.copy2(backup_path, original_path)

        do_delete()

        self._undo_manager.add_action(UndoAction(
            description=f"Delete {len(paths)} PDF(s)",
            undo_func=undo_delete,
            redo_func=do_delete
        ))

    def _on_rename(self) -> None:
        """Handle rename action."""
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._on_rename_pdf_title()
            return

        if len(self._selected_cards) != 1:
            return

        card = self._selected_cards[0]
        old_name = card.filename
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=old_name
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

            do_rename()
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
            self, "Rename PDF Name", "New PDF name:", text=old_title
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

        do_rename_pdf_title()
        self._undo_manager.add_action(UndoAction(
            description="Rename PDF Name",
            undo_func=undo_rename_pdf_title,
            redo_func=do_rename_pdf_title
        ))

    def _on_import(self) -> None:
        """Handle import action."""
        ext_list = " ".join(f"*{e}" for e in sorted(_IMPORT_EXTS))
        dialog = QFileDialog(self, "Import")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setNameFilter(f"Importable Files ({ext_list})")
        if dialog.exec():
            paths = dialog.selectedFiles()
            if paths:
                self._import_paths(paths)

    # ─────────────────────────────────────────────────────────────────
    # Office Import helpers
    # ─────────────────────────────────────────────────────────────────

    def _import_paths(self, paths: list[str]) -> None:
        """Import PDF or Office files into the work directory."""
        failed: list[tuple[str, str]] = []
        imported_paths: list[str] = []
        office_total = sum(
            1 for p in paths if os.path.splitext(p)[1].lower() in _OFFICE_EXTS
        )
        office_index = 0
        progress = None
        cursor_set = False
        if office_total:
            progress = QProgressDialog(
                "OfficeファイルをPDFに変換中...",
                None,
                0,
                office_total,
                self,
            )
            progress.setWindowTitle("変換中")
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setCancelButton(None)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cursor_set = True
            QApplication.processEvents()

        for src_path in paths:
            ext = os.path.splitext(src_path)[1].lower()
            if progress and ext in _OFFICE_EXTS:
                progress.setLabelText(
                    f"変換中 ({office_index + 1}/{office_total}): "
                    f"{os.path.basename(src_path)}"
                )
                progress.setValue(office_index)
                QApplication.processEvents()
            try:
                dest_path = None
                if ext == ".pdf":
                    dest_path = self._copy_pdf_into_workdir(src_path)
                elif ext in _OFFICE_EXTS:
                    try:
                        dest_path = self._convert_office_to_pdf_into_workdir(src_path)
                    finally:
                        if progress:
                            office_index += 1
                            progress.setValue(office_index)
                            QApplication.processEvents()
                else:
                    failed.append((src_path, "未対応の拡張子です"))
                if dest_path:
                    imported_paths.append(dest_path)
            except Exception as e:
                failed.append((src_path, str(e)))

        if progress:
            progress.close()
        if cursor_set:
            QApplication.restoreOverrideCursor()

        if imported_paths:
            backup_dir = tempfile.mkdtemp(prefix="pdfas_import_")
            backups: dict[str, str] = {}
            for dest_path in imported_paths:
                backup_path = str(
                    ensure_unique_path(
                        backup_dir,
                        os.path.basename(dest_path),
                        pattern="{stem}({i}){ext}",
                    )
                )
                shutil.copy2(dest_path, backup_path)
                backups[dest_path] = backup_path

            def undo_import() -> None:
                self._register_internal_remove(list(backups.keys()))
                for dest_path in backups.keys():
                    if os.path.exists(dest_path):
                        send2trash(dest_path)

            def redo_import() -> None:
                for dest_path, backup_path in backups.items():
                    if os.path.exists(backup_path) and not os.path.exists(dest_path):
                        self._register_internal_add([dest_path])
                        shutil.copy2(backup_path, dest_path)

            self._undo_manager.add_action(UndoAction(
                description=f"Import {len(imported_paths)} file(s)",
                undo_func=undo_import,
                redo_func=redo_import
            ))

        if failed:
            details = "\n".join(f"- {os.path.basename(s)}: {r}" for s, r in failed[:20])
            if len(failed) > 20:
                details += f"\n...（他 {len(failed) - 20} 件）"
            QMessageBox.warning(self, "インポート結果", f"失敗: {len(failed)} 件\n\n{details}")

    def _copy_pdf_into_workdir(self, src_path: str) -> str:
        """Copy a PDF into the work directory with unique name."""
        filename = os.path.basename(src_path)
        dest_path = ensure_unique_path(self._work_dir, filename, pattern="{stem}({i}){ext}")
        dest_str = str(dest_path)
        self._register_internal_add([dest_str])
        try:
            shutil.copy2(src_path, dest_path)
        except Exception:
            self._internal_adds.discard(self._normalize_path(dest_str))
            raise
        clear_pixmap_cache_for_path(dest_str)
        return dest_str

    def _convert_office_to_pdf_into_workdir(self, src_path: str) -> str:
        """Convert Office document to PDF and place it in work directory."""
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        dest_path = ensure_unique_path(self._work_dir, f"{base_name}.pdf", pattern="{stem}({i}){ext}")
        dest_str = str(dest_path)
        self._register_internal_add([dest_str])

        # Try Microsoft Office COM first (Windows only)
        if sys.platform == "win32":
            try:
                self._convert_via_office_com(src_path, dest_path)
                return dest_str
            except Exception as e:
                logger.debug(f"Office COM conversion failed: {e}")

        # Fallback to LibreOffice
        soffice = self._find_soffice()
        if soffice:
            try:
                self._convert_via_libreoffice(src_path, dest_path, soffice)
                return dest_str
            except Exception as e:
                logger.debug(f"LibreOffice conversion failed: {e}")

        self._internal_adds.discard(self._normalize_path(dest_str))
        raise RuntimeError("Office変換に必要なソフトウェアが見つかりません (MS Office または LibreOffice)")

    def _convert_via_office_com(self, src_path: str, dest_pdf_path: Path) -> None:
        """Convert Office file to PDF using COM automation (Windows + MS Office)."""
        import win32com.client  # type: ignore

        ext = os.path.splitext(src_path)[1].lower()
        abs_src = os.path.abspath(src_path)
        abs_dest = str(dest_pdf_path.resolve())

        if ext in {".doc", ".docx", ".docm"}:
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            try:
                doc = word.Documents.Open(abs_src)
                doc.SaveAs(abs_dest, FileFormat=17)  # wdFormatPDF
                doc.Close(False)
            finally:
                word.Quit()

        elif ext in {".xls", ".xlsx", ".xlsm"}:
            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = False
            try:
                wb = excel.Workbooks.Open(abs_src)
                wb.ExportAsFixedFormat(0, abs_dest)  # xlTypePDF
                wb.Close(False)
            finally:
                excel.Quit()

        elif ext in {".ppt", ".pptx"}:
            ppt = win32com.client.Dispatch("PowerPoint.Application")
            # PowerPoint may need to be visible briefly
            try:
                presentation = ppt.Presentations.Open(abs_src, WithWindow=False)
                presentation.SaveAs(abs_dest, 32)  # ppSaveAsPDF
                presentation.Close()
            finally:
                ppt.Quit()

        else:
            raise ValueError(f"Unsupported Office extension: {ext}")

    def _find_soffice(self) -> str | None:
        """Find LibreOffice soffice executable."""
        candidates = []
        if sys.platform == "win32":
            for pf in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]:
                if pf:
                    candidates.append(os.path.join(pf, "LibreOffice", "program", "soffice.exe"))
        else:
            candidates = ["/usr/bin/soffice", "/usr/local/bin/soffice", "/opt/libreoffice/program/soffice"]

        for path in candidates:
            if path and os.path.isfile(path):
                return path

        # Try PATH
        import shutil as sh
        return sh.which("soffice")

    def _convert_via_libreoffice(self, src_path: str, dest_pdf_path: Path, soffice: str) -> None:
        """Convert Office file to PDF using LibreOffice headless."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, src_path],
                capture_output=True,
                timeout=120,
                creationflags=creationflags,
            )
            if result.returncode != 0:
                raise RuntimeError(f"soffice failed: {result.stderr.decode(errors='replace')}")

            # Find generated PDF
            base_name = os.path.splitext(os.path.basename(src_path))[0]
            generated = os.path.join(tmpdir, f"{base_name}.pdf")
            if not os.path.exists(generated):
                raise RuntimeError("LibreOffice did not produce a PDF")

            shutil.move(generated, dest_pdf_path)

    def _on_export(self) -> None:
        """Export selected PDFs (or all PDFs if none selected) to a chosen folder."""
        targets = [c.pdf_path for c in self._selected_cards] if self._selected_cards else [c.pdf_path for c in self._cards]

        if not targets:
            QMessageBox.information(self, "Export", "エクスポート対象のPDFがありません。")
            return

        dst_dir = QFileDialog.getExistingDirectory(self, "エクスポート先フォルダを選択")
        if not dst_dir:
            return

        ok = 0
        failed: list[tuple[str, str]] = []

        for src in targets:
            try:
                if not os.path.exists(src):
                    failed.append((src, "元ファイルが見つかりません"))
                    continue

                filename = os.path.basename(src)
                dst_path = ensure_unique_path(dst_dir, filename, pattern="{stem}({i}){ext}")
                shutil.copy2(src, dst_path)
                ok += 1
            except Exception as e:
                failed.append((src, str(e)))

        if failed:
            details = "\n".join([f"- {os.path.basename(s)}: {r}" for s, r in failed[:20]])
            if len(failed) > 20:
                details += f"\n...（他 {len(failed) - 20} 件）"
            QMessageBox.warning(
                self,
                "エクスポート結果",
                f"{ok} 件コピーしました。\n失敗: {len(failed)} 件\n\n{details}",
            )
        else:
            QMessageBox.information(self, "エクスポート結果", f"{ok} 件コピーしました。")

    def _on_rotate(self) -> None:
        """Handle rotate action."""
        # Store paths and page indices instead of widget references
        rotations = []

        for card in self._selected_cards:
            page_count = get_page_count(card.pdf_path)
            if page_count > 0:
                indices = list(range(page_count))
                rotations.append((card.pdf_path, indices))

        if not rotations:
            return

        def do_rotate():
            rotated_paths: list[str] = []
            for pdf_path, indices in rotations:
                rotate_pages(pdf_path, indices, 90)
                rotated_paths.append(pdf_path)
                # Find and refresh the card for this path
                for card in self._cards:
                    if card.pdf_path == pdf_path:
                        card.refresh()
                        break
            self._refresh_page_edit_windows_for_paths(rotated_paths)

        def undo_rotate():
            rotated_paths: list[str] = []
            for pdf_path, indices in rotations:
                rotate_pages(pdf_path, indices, 270)
                rotated_paths.append(pdf_path)
                # Find and refresh the card for this path
                for card in self._cards:
                    if card.pdf_path == pdf_path:
                        card.refresh()
                        break
            self._refresh_page_edit_windows_for_paths(rotated_paths)

        do_rotate()

        self._undo_manager.add_action(UndoAction(
            description=f"Rotate {len(rotations)} PDF(s)",
            undo_func=undo_rotate,
            redo_func=do_rotate
        ))

    def _on_select_all(self) -> None:
        """Handle select all action."""
        self._clear_selection()
        for card in self._cards:
            card.set_selected(True)
            self._selected_cards.append(card)
        self._update_button_states()

    def _on_sort_by_name(self) -> None:
        """Handle sort by name."""
        self._sort_by("name", default_ascending=True)

    def _on_sort_by_date(self) -> None:
        """Handle sort by date."""
        self._sort_by("date", default_ascending=False)

    def _sort_by(self, sort_type: str, default_ascending: bool) -> None:
        """Sort cards by the specified type with undo support."""
        # Store paths instead of widget references
        old_paths = [card.pdf_path for card in self._cards]
        old_sort_order = self._sort_order
        old_ascending = self._sort_ascending

        if self._sort_order == sort_type:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_order = sort_type
            self._sort_ascending = default_ascending

        new_ascending = self._sort_ascending
        self._sort_cards()
        self._refresh_grid()
        new_paths = [card.pdf_path for card in self._cards]

        def undo():
            self._rebuild_cards_from_paths(old_paths)
            self._sort_order = old_sort_order
            self._sort_ascending = old_ascending
            self._refresh_grid()

        def redo():
            self._rebuild_cards_from_paths(new_paths)
            self._sort_order = sort_type
            self._sort_ascending = new_ascending
            self._refresh_grid()

        self._undo_manager.add_action(UndoAction(
            description=f"Sort by {sort_type}",
            undo_func=undo,
            redo_func=redo
        ))
        self._update_button_states()

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event."""
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                ext = os.path.splitext(url.toLocalFile())[1].lower()
                if ext in _IMPORT_EXTS:
                    event.acceptProposedAction()
                    return

    def dragMoveEvent(self, event) -> None:
        """Handle drag move event - show drop indicator."""
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE
        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
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
                source_paths = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8').split('|')
                if target_card.pdf_path not in source_paths:
                    if drop_pos.x() > card_rect.left() + edge_margin and drop_pos.x() < card_rect.right() - edge_margin:
                        # On card center - merge mode
                        self._hide_drop_indicator()
                        target_card.setStyleSheet("PDFCard { background-color: #90EE90; border: 2px solid #228B22; }")
                        self._drop_indicator_index = -2  # Special value for merge
                        return

            # Show insert indicator
            self._show_drop_indicator(drop_pos)
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            modifiers = QApplication.keyboardModifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                event.setDropAction(Qt.DropAction.CopyAction)
            else:
                event.setDropAction(Qt.DropAction.MoveAction)

            event.acceptProposedAction()
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            target_card = self._get_card_at_pos(drop_pos)

            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            source_path = data.split('|')[0]

            # Check for merge mode on card center
            if target_card:
                # Locked cards cannot be merge targets
                if target_card.is_locked:
                    self._show_drop_indicator(drop_pos)
                    return

                card_rect = target_card.geometry()
                edge_margin = card_rect.width() * 0.15  # 70% center = 15% edges

                if target_card.pdf_path != source_path:
                    if drop_pos.x() > card_rect.left() + edge_margin and drop_pos.x() < card_rect.right() - edge_margin:
                        # On card center - merge mode
                        self._hide_drop_indicator()
                        target_card.setStyleSheet("PDFCard { background-color: #90EE90; border: 2px solid #228B22; }")
                        self._drop_indicator_index = -2
                        return

            # Show insert indicator
            self._show_drop_indicator(drop_pos)
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hide_drop_indicator()

    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave event - hide drop indicator."""
        self._hide_drop_indicator()
        # Reset any card highlighting
        for card in self._cards:
            card._update_style()
        super().dragLeaveEvent(event)

    def _show_drop_indicator(self, pos) -> None:
        """Show drop indicator at the appropriate position."""
        # Reset any card merge highlighting
        for card in self._cards:
            if not card.is_selected:
                card._update_style()

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
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

        logger.debug(f"MainWindow.dropEvent called, mimeData formats: {event.mimeData().formats()}")
        
        drop_mode = self._drop_indicator_index
        self._hide_drop_indicator()
        # Reset any card highlighting
        for card in self._cards:
            card._update_style()

        if event.mimeData().hasFormat(PDFCARD_MIME_TYPE):
            source_path = event.mimeData().data(PDFCARD_MIME_TYPE).data().decode('utf-8')
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            logger.debug(f"PDFCARD drop: source_path={source_path}, drop_pos={drop_pos}")
            
            # Check if Ctrl key is pressed for copy operation
            modifiers = QApplication.keyboardModifiers()
            is_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            logger.debug(f"is_copy={is_copy}, _drop_indicator_index={self._drop_indicator_index}")
            
            if drop_mode == -2:  # Overlay mode (merge)
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
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(PAGETHUMBNAIL_MIME_TYPE):
            data = event.mimeData().data(PAGETHUMBNAIL_MIME_TYPE).data().decode('utf-8')
            drop_pos = self._container.mapFrom(self, event.position().toPoint())
            logger.debug(f"PAGETHUMBNAIL drop: data={data}, drop_pos={drop_pos}")
            self._handle_page_extraction(data, drop_pos)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            logger.debug(f"URL drop: {event.mimeData().urls()}")
            self._handle_external_file_drop(event.mimeData().urls())
            event.acceptProposedAction()
        else:
            logger.debug("Unknown drop format, ignoring")

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
        import shutil
        from pathlib import Path
        
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
        import tempfile
        import shutil
        
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
            
        except Exception:
            # Rollback on error
            shutil.copy2(backup_path, target_path)
            raise
        finally:
            # Keep backup for undo; it will be cleaned up eventually
            pass

    def _get_card_at_pos(self, pos):
        """Return the card at the given container position, if any."""
        for card in self._cards:
            if card.geometry().contains(pos):
                return card
        return None

    def _get_card_by_path(self, pdf_path: str) -> PDFCard | None:
        """Return the card with the given PDF path, if any."""
        for card in self._cards:
            if card.pdf_path == pdf_path:
                return card
        return None

    def lock_card(self, pdf_path: str) -> None:
        """Lock a card (when being edited in PageEditWindow)."""
        card = self._get_card_by_path(pdf_path)
        if card:
            card.set_locked(True)
            # Deselect the locked card
            if card in self._selected_cards:
                self._selected_cards.remove(card)
                card.set_selected(False)

    def unlock_card(self, pdf_path: str) -> None:
        """Unlock a card (when PageEditWindow is closed)."""
        card = self._get_card_by_path(pdf_path)
        if card:
            card.set_locked(False)

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
        """Handle external file drop (import)."""
        paths = []
        for url in urls:
            local = url.toLocalFile()
            ext = os.path.splitext(local)[1].lower()
            if ext in _IMPORT_EXTS:
                paths.append(local)
        if paths:
            self._import_paths(paths)

    def _handle_page_extraction(self, data: str, drop_pos=None) -> None:
        """Handle page extraction from page edit window."""
        import tempfile
        import shutil
        from src.utils.pdf_utils import extract_pages, remove_pages, insert_pages
        from PyQt6.QtWidgets import QApplication
        from src.views.page_edit_window import PageEditWindow

        logger.debug(f"_handle_page_extraction called with data={data}, drop_pos={drop_pos}")

        pdf_path, page_nums_str = data.split('|')
        page_nums = sorted(set(int(n) for n in page_nums_str.split(',') if n))
        logger.debug(f"Parsed pdf_path={pdf_path}, page_nums={page_nums}")
        
        if not page_nums:
            logger.debug("No page_nums, returning early")
            return

        if drop_pos is None:
            logger.debug("No drop_pos, ignoring")
            return

        target_card = self._get_card_at_pos(drop_pos)
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
                    self._work_dir,
                    Path(pdf_path).name,
                    pattern="{stem}({i}){ext}",
                    use_original=False,
                )
            )
            insert_index = self._get_drop_index(drop_pos)
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
                    self._register_internal_add([target_path])
                    shutil.move(tmp_path, target_path)
                    tmp_path = None
                    self._add_card(target_path, insert_index=insert_index)
                    self._sort_order = "manual"
                    self._refresh_grid()
                    _select_single_card(target_path)
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

        if not do_extraction():
            return

        action = "Copy" if is_copy else "Move"
        self._undo_manager.add_action(UndoAction(
            description=f"{action} {len(page_nums)} page(s)",
            undo_func=undo_extraction,
            redo_func=redo_extraction
        ))

    def resizeEvent(self, event) -> None:
        """Handle window resize."""
        super().resizeEvent(event)
        # As requested: even tiny resizes can reflow. The key fix is that the logic is identical to initial.
        self._refresh_grid()

    def closeEvent(self, event) -> None:
        """Handle window close."""
        self._undo_manager.remove_listener(self._on_undo_manager_changed)
        self._watcher.stop()
        super().closeEvent(event)
