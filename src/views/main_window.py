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
    QDialog, QFileDialog, QInputDialog, QMessageBox, QFrame, QRubberBand,
    QProgressDialog,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QEvent, QPoint, QRect
from PyQt6.QtGui import QKeySequence
from send2trash import send2trash

from src.views.pdf_card import PDFCard, PDFCARD_MIME_TYPE
from src.views.folder_card import FolderCard, FOLDERCARD_MIME_TYPE
from src.views.view_helpers import (
    clear_selection,
    log_undo_state,
    register_shortcuts,
    viewport_width_or_fallback,
)
from src.controllers.folder_watcher import FolderWatcher
from src.models.undo_manager import UndoManager, UndoAction
from src.utils.pdf_utils import (
    PdfWritePermissionError,
    rotate_pages, get_page_count, get_pdf_metadata_title, update_pdf_metadata_title,
    clear_pixmap_cache, clear_pixmap_cache_for_path, print_pdfs,
    export_pages_as_images, export_pdf_compressed, images_to_pdf, rasterize_pdf,
)
from src.views.export_dialog import ExportOptionsDialog
from src.utils.path_utils import ensure_unique_path
from src.utils.trash_utils import build_trash_failure_message
from src.utils.windows_shell import show_native_file_context_menu
from src.workers.file_worker import FileOperationWorker
from src.workers.import_worker import ImportWorker, find_soffice

logger = logging.getLogger(__name__)

_WORD_EXTS = {".doc", ".docx", ".docm"}
_EXCEL_EXTS = {".xls", ".xlsx", ".xlsm"}
_PPT_EXTS = {".ppt", ".pptx"}
_OFFICE_EXTS = _WORD_EXTS | _EXCEL_EXTS | _PPT_EXTS
# Image extensions importable here must match what conversion supports;
# kept in sync with _IMAGE_EXTS in src/workers/import_worker.py.
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif",
    ".jp2", ".jpx", ".ppm", ".pgm", ".pbm", ".pnm", ".pam", ".svg",
}
_IMPORT_EXTS = {".pdf"} | _OFFICE_EXTS | _IMAGE_EXTS


def _exts_to_filter(label: str, exts: set[str]) -> str:
    """Build a QFileDialog name-filter string like ``"Word (*.doc *.docx)"``."""
    pattern = " ".join(f"*{e}" for e in sorted(exts))
    return f"{label} ({pattern})"

# If conversion-required files exceed this count the user is warned first.
IMPORT_OFFICE_WARN_THRESHOLD = 5


class MainWindow(QMainWindow):
    """Main application window.

    Displays PDF files as cards in a grid layout.
    """

    PREVIEW_THUMB_MIN = 80
    PREVIEW_THUMB_MAX = 400
    PREVIEW_THUMB_STEP = 20

    # Registry of live MainWindow instances for cross-window DnD.
    _instances: list["MainWindow"] = []

    def __init__(self, folder_path: str | None = None):
        super().__init__()
        self._cards: list[PDFCard] = []
        self._folder_cards: list[FolderCard] = []
        self._selected_folder_cards: list[FolderCard] = []
        self._selected_cards: list[PDFCard] = []
        self._child_windows: list[QMainWindow] = []
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
        self._zoom_debounce_timer.setInterval(60)
        self._zoom_debounce_timer.timeout.connect(self._render_visible_cards_hq)

        # Debounce file modified events (path -> single-shot timer)
        self._modified_timers: dict[str, QTimer] = {}
        # Track last processed mtime to avoid redundant refreshes
        self._modified_last_mtime: dict[str, float] = {}
        self._modified_debounce_ms = 120

        # Debounce grid refresh for rapid file-removal events (watchdog)
        self._grid_refresh_timer = QTimer(self)
        self._grid_refresh_timer.setSingleShot(True)
        self._grid_refresh_timer.setInterval(50)
        self._grid_refresh_timer.timeout.connect(self._on_deferred_grid_refresh)

        # Debounce reconcile-with-disk calls (window activation fires repeatedly)
        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setSingleShot(True)
        self._reconcile_timer.setInterval(150)
        self._reconcile_timer.timeout.connect(self._reconcile_with_disk)

        # Low-frequency backstop poll for missed watchdog events (bulk copies,
        # cloud-synced folders, AV interference). Runs only while visible.
        self._reconcile_poll_timer = QTimer(self)
        self._reconcile_poll_timer.setInterval(4000)
        self._reconcile_poll_timer.timeout.connect(self._maybe_poll_reconcile)
        self._reconcile_poll_timer.start()

        # Setup working directory
        self._work_dir = Path(folder_path) if folder_path else Path.home() / "Documents" / "PDFs"
        self._is_root_window = folder_path is None
        self._work_dir.mkdir(parents=True, exist_ok=True)

        # Undo manager
        self._undo_manager = UndoManager(max_size=20)

        # Async file operation state
        self._operation_in_progress: bool = False
        self._active_worker: FileOperationWorker | None = None
        self._active_import_worker: ImportWorker | None = None
        self._active_import_progress: QProgressDialog | None = None

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
        self._watcher.folder_added.connect(self._on_folder_added)
        self._watcher.folder_removed.connect(self._on_folder_removed)
        self._watcher.start()

        # Register this window in the cross-window registry
        MainWindow._instances.append(self)

        # Update title for non-root windows
        if folder_path is not None:
            self.setWindowTitle(f"JusticePDF - {self._work_dir.name}")

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
        self._drop_indicator.setStyleSheet("background-color: #4f46e5;")
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
        self._delete_btn.setObjectName("danger")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename)
        toolbar.addWidget(self._rename_btn)

        self._title_btn = QPushButton("Title")
        self._title_btn.clicked.connect(self._on_rename_pdf_title)
        toolbar.addWidget(self._title_btn)

        toolbar.addSeparator()

        self._import_btn = QPushButton("Import")
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        self._import_folder_btn = QPushButton("Import Folder")
        self._import_folder_btn.clicked.connect(self._on_import_folder)
        toolbar.addWidget(self._import_folder_btn)

        self._new_folder_btn = QPushButton("New Folder")
        self._new_folder_btn.clicked.connect(self._on_new_folder)
        toolbar.addWidget(self._new_folder_btn)

        self._export_btn = QPushButton("Export")
        self._export_btn.setObjectName("primary")
        self._export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self._export_btn)

        self._print_btn = QPushButton("Print")
        self._print_btn.clicked.connect(self._on_print)
        toolbar.addWidget(self._print_btn)

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
                (QKeySequence.StandardKey.Print, self._on_print),
            ),
        )

    def _update_button_states(self) -> None:
        """Update toolbar button enabled states."""
        busy = self._operation_in_progress
        has_selection = len(self._selected_cards) > 0
        has_deletable = has_selection or len(self._selected_folder_cards) > 0
        has_any = len(self._cards) > 0
        self._delete_btn.setEnabled(has_deletable and not busy)
        self._rename_btn.setEnabled(len(self._selected_cards) == 1 and not busy)
        self._title_btn.setEnabled(len(self._selected_cards) == 1 and not busy)
        self._rotate_btn.setEnabled(has_selection and not busy)
        self._undo_btn.setEnabled(self._undo_manager.can_undo() and not busy)
        self._redo_btn.setEnabled(self._undo_manager.can_redo() and not busy)
        self._export_btn.setEnabled(has_any)

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
        """Load existing subfolders and PDF files from the work directory."""
        for folder_path in sorted(self._watcher.get_subfolders(), key=lambda p: os.path.basename(p).lower()):
            self._add_folder_card(folder_path)
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
        self._schedule_reconcile()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._schedule_reconcile()

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

    def _connect_folder_card_signals(self, fc: FolderCard) -> FolderCard:
        fc.clicked.connect(self._on_folder_card_clicked)
        fc.double_clicked.connect(self._on_folder_card_double_clicked)
        fc.dropped_on.connect(self._on_folder_card_dropped_on)
        fc.context_menu_requested.connect(self._on_folder_card_context_menu_requested)
        return fc

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

    def _add_folder_card(self, folder_path: str, insert_index: int | None = None) -> FolderCard:
        fc = self._connect_folder_card_signals(
            FolderCard(
                folder_path,
                card_width=self._preview_card_width,
                thumb_size=self._preview_thumb_size,
            )
        )
        if insert_index is None or insert_index >= len(self._folder_cards):
            self._folder_cards.append(fc)
        else:
            self._folder_cards.insert(max(0, insert_index), fc)
        return fc

    def _get_folder_card_by_path(self, folder_path: str) -> FolderCard | None:
        normalized = self._normalize_path(folder_path)
        for fc in self._folder_cards:
            if self._normalize_path(fc.folder_path) == normalized:
                return fc
        return None

    def _remove_folder_card(self, folder_path: str) -> None:
        fc = self._get_folder_card_by_path(folder_path)
        if fc is None:
            return
        if fc in self._selected_folder_cards:
            self._selected_folder_cards.remove(fc)
        self._folder_cards.remove(fc)
        fc.deleteLater()

    def _schedule_reconcile(self) -> None:
        """Debounced trigger for _reconcile_with_disk()."""
        self._reconcile_timer.start()

    def _maybe_poll_reconcile(self) -> None:
        """Backstop poll: only scan the directory while the window is visible."""
        if self.isVisible() and not self.isMinimized():
            self._schedule_reconcile()

    def _is_file_ready(self, path: str) -> bool:
        """Best-effort check that a freshly-appeared file is fully written
        (not still being copied / exclusively locked by another process)."""
        try:
            if os.path.getsize(path) <= 0:
                return False
            with open(path, "rb"):
                pass
            return True
        except OSError:
            return False

    def _reconcile_with_disk(self) -> None:
        """Diff the on-disk contents of the work dir against the displayed
        cards and add/remove cards so the view matches disk.

        Backstop for missed watchdog events. Does not touch undo history and
        skips any path that is part of an in-flight internal op / pending rename.
        """
        if self._operation_in_progress:
            return

        work_dir = str(self._work_dir)
        try:
            entries = os.listdir(work_dir)
        except OSError:
            return

        disk_pdf_norm: dict[str, str] = {}  # norm -> real path
        disk_dir_norm: dict[str, str] = {}
        for name in entries:
            full = os.path.join(work_dir, name)
            try:
                if os.path.isdir(full):
                    disk_dir_norm[self._normalize_path(full)] = full
                elif name.lower().endswith(".pdf") and os.path.isfile(full):
                    disk_pdf_norm[self._normalize_path(full)] = full
            except OSError:
                continue

        busy = (
            self._internal_adds
            | self._internal_removes
            | set(self._pending_rename_old_to_new.keys())
            | set(self._pending_rename_new_to_old.keys())
            | self._pending_rename_removed
            | self._pending_rename_added
        )

        changed = False

        # --- PDF cards ---
        card_norm = {self._normalize_path(c.pdf_path) for c in self._cards}
        for norm, real in disk_pdf_norm.items():
            if norm in card_norm or norm in busy:
                continue
            if not self._is_file_ready(real):  # still being copied; pick it up next pass
                continue
            self._add_card(real, insert_index=None)
            changed = True
        for card in self._cards[:]:
            norm = self._normalize_path(card.pdf_path)
            if norm in disk_pdf_norm or norm in busy:
                continue
            self._remove_card(card.pdf_path)
            changed = True

        # --- folder cards ---
        fc_norm = {self._normalize_path(fc.folder_path) for fc in self._folder_cards}
        for norm, real in disk_dir_norm.items():
            if norm in fc_norm or norm in busy:
                continue
            self._add_folder_card(real)
            changed = True
        for fc in self._folder_cards[:]:
            norm = self._normalize_path(fc.folder_path)
            if norm in disk_dir_norm or norm in busy:
                continue
            self._remove_folder_card(fc.folder_path)
            changed = True

        if changed:
            self._update_button_states()
            self._grid_refresh_timer.start()

    def _rebuild_cards_from_paths(self, paths: list[str]) -> None:
        """Rebuild PDFCards from a list of paths, reusing existing cards where possible.

        Cards whose paths appear in *paths* are kept as-is (no thumbnail
        re-render).  Only genuinely new paths cause a PDFCard to be created.
        Cards not present in *paths* are disposed of.
        """
        existing: dict[str, PDFCard] = {card.pdf_path: card for card in self._cards}
        new_cards: list[PDFCard] = []
        reused_paths: set[str] = set()

        for path in paths:
            if not os.path.exists(path):
                continue
            if path in existing and path not in reused_paths:
                new_cards.append(existing[path])
                reused_paths.add(path)
            else:
                card = self._connect_card_signals(
                    PDFCard(
                        path,
                        card_width=self._preview_card_width,
                        thumb_size=self._preview_thumb_size,
                    )
                )
                new_cards.append(card)

        for card in self._cards:
            if card.pdf_path not in reused_paths:
                card.deleteLater()

        self._cards = new_cards
        self._selected_cards = [c for c in self._selected_cards if c in new_cards]

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

        all_items: list[QWidget] = list(self._folder_cards) + list(self._cards)
        for i, item in enumerate(all_items):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(item, row, col)

    def _on_deferred_grid_refresh(self) -> None:
        """Callback for the debounced grid refresh timer."""
        self._refresh_grid()

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
        """Clear all selections (PDF cards and folder cards)."""
        clear_selection(self._selected_cards)
        clear_selection(self._selected_folder_cards)
        self._update_button_states()

    def _selected_card_paths_in_grid_order(self) -> list[str]:
        """Return selected card paths ordered by the visible card grid."""
        return [card.pdf_path for card in self._cards if card in self._selected_cards]

    def _selected_folder_paths_in_grid_order(self) -> list[str]:
        """Return selected folder paths ordered by the visible folder grid."""
        return [fc.folder_path for fc in self._folder_cards if fc in self._selected_folder_cards]

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
                if isinstance(child, (PDFCard, FolderCard)):
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
            for fc in self._folder_cards:
                if rect.intersects(fc.geometry()):
                    fc.set_selected(True)
                    self._selected_folder_cards.append(fc)
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

    def _on_card_double_clicked(self, card: PDFCard, open_zoom: bool = False) -> None:
        """Handle card double-click - open page edit window.

        When ``open_zoom`` is True (Alt+double-click), the window is
        immediately switched into enlarged single-page mode on page 0.
        """
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
                if open_zoom:
                    try:
                        widget._open_zoom_view(0)
                    except Exception:
                        logger.debug("_open_zoom_view failed", exc_info=True)
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

        if open_zoom:
            # Defer slightly so the window's initial layout finishes first.
            QTimer.singleShot(0, lambda w=window: w._open_zoom_view(0))

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
        import tempfile
        from pathlib import Path

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

        worker = FileOperationWorker(_do_io, parent=self)
        worker.finished.connect(_on_success)
        worker.error.connect(_on_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._active_worker = worker
        worker.start()

    def _on_folder_added(self, path: str) -> None:
        """Handle subfolder created on disk."""
        normalized = self._normalize_path(path)
        for fc in self._folder_cards:
            if self._normalize_path(fc.folder_path) == normalized:
                fc.refresh()
                self._grid_refresh_timer.start()
                self._internal_adds.discard(normalized)
                return
        if normalized in self._internal_adds:
            self._internal_adds.discard(normalized)
            return
        self._add_folder_card(path)
        self._grid_refresh_timer.start()

    def _on_folder_removed(self, path: str) -> None:
        """Handle subfolder removed from disk."""
        normalized = self._normalize_path(path)
        if normalized in self._internal_removes:
            self._internal_removes.discard(normalized)
            if self._get_folder_card_by_path(path) is None:
                return
        self._remove_folder_card(path)
        self._grid_refresh_timer.start()

    def _on_folder_card_clicked(self, fc: FolderCard) -> None:
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if fc in self._selected_folder_cards:
                fc.set_selected(False)
                self._selected_folder_cards.remove(fc)
            else:
                fc.set_selected(True)
                self._selected_folder_cards.append(fc)
        else:
            if fc not in self._selected_folder_cards:
                self._clear_selection()
                fc.set_selected(True)
                self._selected_folder_cards.append(fc)
        self._update_button_states()

    def _on_folder_card_double_clicked(self, fc: FolderCard, alt_pressed: bool = False) -> None:
        """Open a new MainWindow scoped to the clicked subfolder."""
        if not os.path.isdir(fc.folder_path):
            return
        # If already open, bring that window to front.
        for w in MainWindow._instances:
            if w is self:
                continue
            try:
                if self._normalize_path(str(w._work_dir)) == self._normalize_path(fc.folder_path):
                    if w.isMinimized():
                        w.setWindowState(
                            (w.windowState() & ~Qt.WindowState.WindowMinimized)
                            | Qt.WindowState.WindowActive
                        )
                    w.show()
                    w.raise_()
                    w.activateWindow()
                    return
            except RuntimeError:
                continue

        new_window = MainWindow(fc.folder_path)

        existing_count = sum(
            1 for w in MainWindow._instances
            if w is not new_window and w.isVisible()
        )
        main_geo = self.geometry()
        screen = self.screen().availableGeometry()
        cascade_offset = 40
        new_x = main_geo.right() + 10
        if new_x + new_window.width() > screen.right():
            new_x = max(screen.left(), screen.right() - new_window.width())
        max_y = screen.bottom() - new_window.height() // 2
        cycles_fit = max(1, (max_y - main_geo.top()) // cascade_offset + 1)
        new_y = main_geo.top() + ((existing_count % cycles_fit) * cascade_offset)
        new_window.move(new_x, new_y)
        new_window.show()

        # Hold a reference so Qt doesn't GC the new top-level window.
        self._child_windows.append(new_window)

    def _on_folder_card_dropped_on(
        self,
        fc: FolderCard,
        payloads: dict,
    ) -> None:
        """Handle drops onto a folder card (move/copy into that folder).

        Processes folder and PDF payloads together so a multi-selection
        (folders + PDFs) is moved/copied in one gesture.
        """
        dest_dir = Path(fc.folder_path)
        if not dest_dir.exists():
            return
        modifiers = QApplication.keyboardModifiers()
        is_copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        dest_norm = self._normalize_path(str(dest_dir))

        folder_payload = payloads.get(FOLDERCARD_MIME_TYPE)
        pdf_payload = payloads.get(PDFCARD_MIME_TYPE)
        page_payload = payloads.get("application/x-pdfas-page")
        url_payload = payloads.get("text/uri-list")

        handled = False

        if folder_payload:
            src_folders = [
                p for p in folder_payload.split('|')
                if p and os.path.isdir(p) and self._normalize_path(p) != dest_norm
            ]
            for src in src_folders:
                self._move_or_copy_folder_into_dir(src, dest_dir, is_copy=is_copy)
            if src_folders:
                handled = True

        if pdf_payload:
            paths = [p for p in pdf_payload.split('|') if p]
            if paths:
                self._move_or_copy_files_into_dir(paths, dest_dir, is_copy=is_copy)
                handled = True

        if page_payload:
            self._handle_page_extraction(page_payload, drop_pos=None, dest_dir=dest_dir)
            handled = True

        if not handled and url_payload:
            urls = [p for p in url_payload.split('|') if p]
            if urls:
                self._import_paths(urls, dest_root=dest_dir)
                handled = True

        if handled:
            fc.refresh()

    def _on_folder_card_context_menu_requested(
        self,
        fc: FolderCard,
        global_pos: QPoint,
    ) -> None:
        """Handle Explorer-style right-click selection and menu opening."""
        if fc not in self._selected_folder_cards:
            self._clear_selection()
            fc.set_selected(True)
            self._selected_folder_cards.append(fc)
            self._update_button_states()

        paths = self._selected_folder_paths_in_grid_order()
        if show_native_file_context_menu(int(self.winId()), paths, global_pos):
            return

        # Fallback for non-Windows or when the native menu cannot be shown.
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(global_pos)
        if chosen is rename_action:
            self._rename_folder(fc)
        elif chosen is delete_action:
            self._delete_folder(fc)

    def _rename_folder(self, fc: FolderCard) -> None:
        old_path = fc.folder_path
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "Rename Folder", "New folder name:", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return
        parent_dir = os.path.dirname(old_path)
        new_path = os.path.join(parent_dir, new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Rename Folder", f"'{new_name}' は既に存在します。")
            return
        try:
            os.rename(old_path, new_path)
        except OSError as e:
            QMessageBox.warning(self, "Rename Folder", f"リネームに失敗しました: {e}")
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
        title = "Delete Folder" if folder_paths else "Delete"

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

        worker = FileOperationWorker(_do_io, parent=self)
        worker.finished.connect(_on_success)
        worker.error.connect(_on_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._active_worker = worker
        worker.start()

    def _on_new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name:
            return
        dest = self._work_dir / name
        if dest.exists():
            QMessageBox.warning(self, "New Folder", f"'{name}' は既に存在します。")
            return
        try:
            dest.mkdir(parents=True, exist_ok=False)
        except OSError as e:
            QMessageBox.warning(self, "New Folder", f"作成に失敗しました: {e}")
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
            QMessageBox.warning(self, "Move Folder", "フォルダを自身の中に移動できません。")
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
            QMessageBox.warning(self, "Move Folder", f"フォルダの移動に失敗しました: {e}")
            return None

        if not is_copy and source_parent_win is not None:
            source_parent_win._remove_folder_card(source)
            source_parent_win._grid_refresh_timer.start()

        if dest_parent_win is not None:
            if dest_parent_win._get_folder_card_by_path(str(target)) is None:
                dest_parent_win._add_folder_card(str(target))
            dest_parent_win._grid_refresh_timer.start()

        return str(target)

    def _find_window_by_path(self, path: str) -> "MainWindow | None":
        """Find the MainWindow whose work directory contains the given file path."""
        path_norm = self._normalize_path(path)
        best: "MainWindow | None" = None
        best_len = -1
        for w in list(MainWindow._instances):
            try:
                wd = self._normalize_path(str(w._work_dir))
                if path_norm == wd or path_norm.startswith(wd + os.sep):
                    if len(wd) > best_len:
                        best = w
                        best_len = len(wd)
            except RuntimeError:
                continue
        return best

    def _find_window_by_workdir(self, workdir: str) -> "MainWindow | None":
        norm = self._normalize_path(workdir)
        for w in list(MainWindow._instances):
            try:
                if self._normalize_path(str(w._work_dir)) == norm:
                    return w
            except RuntimeError:
                continue
        return None

    def closeEvent(self, event) -> None:
        try:
            if self._active_import_worker is not None:
                self._active_import_worker.request_cancel()
                self._active_import_worker.wait(2000)
        except Exception:
            pass
        try:
            self._reconcile_poll_timer.stop()
        except Exception:
            pass
        try:
            self._watcher.stop()
        except Exception:
            pass
        try:
            MainWindow._instances.remove(self)
        except ValueError:
            pass
        super().closeEvent(event)

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
                self._grid_refresh_timer.start()
                return
        if normalized in self._internal_adds:
            self._internal_adds.discard(normalized)
        else:
            self._clear_undo_history()
        new_card = self._add_card(path, insert_index=None)
        self._grid_refresh_timer.start()

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
        self._grid_refresh_timer.start()
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
        try:
            self._undo_manager.undo()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return
        self._update_button_states()

    def _on_redo(self) -> None:
        """Handle redo action."""
        try:
            self._undo_manager.redo()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return
        self._update_button_states()

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

    def _on_refresh(self) -> None:
        """Reload cards and open edit windows from disk."""
        clear_pixmap_cache()
        self._reconcile_with_disk()  # pick up files/folders added or removed externally
        self._refresh_all_views()    # re-render thumbnails of the (now-current) cards

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

        import tempfile
        from pathlib import Path

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

        worker = FileOperationWorker(_do_io, parent=self)
        worker.finished.connect(_on_success)
        worker.error.connect(_on_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._active_worker = worker
        worker.start()

    def _on_rename(self) -> None:
        """Handle rename action."""
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
            self, "Rename PDF Title", "New PDF title:", text=old_title
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

    def _on_import(self) -> None:
        """Handle import action (files)."""
        all_filter = _exts_to_filter("All importable files", _IMPORT_EXTS)
        filters = [
            all_filter,
            _exts_to_filter("PDF", {".pdf"}),
            _exts_to_filter("Word", _WORD_EXTS),
            _exts_to_filter("Excel", _EXCEL_EXTS),
            _exts_to_filter("PowerPoint", _PPT_EXTS),
            _exts_to_filter("Images", _IMAGE_EXTS),
            "All files (*)",
        ]
        dialog = QFileDialog(self, "Import")
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
            self, "Import Folder", str(Path.home())
        )
        if folder:
            self._import_paths([folder])

    # ─────────────────────────────────────────────────────────────────
    # Import helpers — asynchronous with cancellation
    # ─────────────────────────────────────────────────────────────────

    def _build_import_tree(
        self,
        paths: list[str],
        dest_root: Path,
    ) -> list[tuple[str, str]]:
        """Build a flat list of (src, dest) pairs for the importer.

        Directories are walked recursively; the nested structure is preserved
        relative to the dropped-root under ``dest_root``.  Leaf collisions are
        resolved via ``ensure_unique_path`` so neither the real work dir nor
        any new subfolder ever sees a clobber.  Office/image sources are given
        ``.pdf`` destinations.
        """
        used_dest: set[str] = set()

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
                for root, _dirs, files in os.walk(p):
                    rel = os.path.relpath(root, p)
                    dest_parent = target_root if rel == "." else (target_root / rel)
                    dest_parent.mkdir(parents=True, exist_ok=True)
                    for fname in files:
                        src_full = os.path.join(root, fname)
                        ext = os.path.splitext(fname)[1].lower()
                        if ext not in _IMPORT_EXTS:
                            continue
                        dest_name = _normalize_dest_name(fname)
                        dest_full = _alloc_dest(dest_parent, dest_name)
                        tree.append((src_full, str(dest_full)))
            elif os.path.isfile(p):
                ext = os.path.splitext(p)[1].lower()
                if ext not in _IMPORT_EXTS:
                    continue
                dest_name = _normalize_dest_name(os.path.basename(p))
                dest_full = _alloc_dest(dest_root, dest_name)
                tree.append((p, str(dest_full)))
        return tree

    def _count_office_files(self, tree: list[tuple[str, str]]) -> int:
        return sum(
            1 for src, _ in tree
            if os.path.splitext(src)[1].lower() in _OFFICE_EXTS
        )

    def _import_paths(
        self,
        paths: list[str],
        *,
        dest_root: Path | None = None,
    ) -> None:
        """Import PDF / Office / image files and folders into the work tree.

        This is the main entry for both the Import button, the Import Folder
        button, drag-and-drop onto the main window, and drops onto a
        FolderCard (via ``dest_root``).  Runs asynchronously in an
        ``ImportWorker`` thread; the user may cancel mid-batch.
        """
        if self._operation_in_progress or self._active_import_worker is not None:
            QMessageBox.information(self, "Import", "別のインポートが進行中です。完了までお待ちください。")
            return

        root = Path(dest_root) if dest_root else Path(self._work_dir)
        tree = self._build_import_tree(paths, root)
        if not tree:
            return

        office_count = self._count_office_files(tree)
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
                return

        self._start_import_worker(tree)

    def _start_import_worker(self, tree: list[tuple[str, str]]) -> None:
        total = len(tree)
        if total == 0:
            return

        # Pre-register expected destinations so FolderWatcher events don't
        # clobber undo history on either the current or target window.
        for _, dest in tree:
            dest_win = self._find_window_by_workdir(os.path.dirname(dest))
            if dest_win is not None:
                dest_win._register_internal_add([dest])

        progress = QProgressDialog(
            "インポート中...",
            "キャンセル",
            0,
            total,
            self,
        )
        progress.setWindowTitle("インポート")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = ImportWorker(tree, find_soffice(), parent=self)

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
            self._on_import_finished(imported, failed, cancelled, tree)
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
    ) -> None:
        # Drop any pre-registered adds that never happened.
        imported_norm = {self._normalize_path(p) for p in imported}
        for _, dest in tree:
            norm = self._normalize_path(dest)
            if norm not in imported_norm:
                for w in list(MainWindow._instances):
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
                description=f"Import {len(imported)} file(s)",
                undo_func=undo_import,
                redo_func=redo_import,
            ))

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

    def _on_export(self) -> None:
        """Export selected PDFs (or all PDFs if none selected) to a chosen folder.

        A dialog lets the user pick format, DPI, quality, and compression
        settings before choosing the output directory.
        """
        targets = [c.pdf_path for c in self._selected_cards] if self._selected_cards else [c.pdf_path for c in self._cards]

        if not targets:
            QMessageBox.information(self, "Export", "エクスポート対象のPDFがありません。")
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
                targets, dst_dir,
                optimize_level=options["pdf_optimize_level"],
                image_dpi=options["pdf_image_dpi"],
                image_quality=options["pdf_image_quality"],
                rasterize=options["rasterize"],
            )
        else:
            self._export_as_images(
                targets, dst_dir, fmt,
                dpi=options["dpi"], quality=options["jpeg_quality"],
            )

    def _export_as_pdf(
        self,
        targets: list[str],
        dst_dir: str,
        *,
        optimize_level: int = 0,
        image_dpi: int = 150,
        image_quality: int = 75,
        rasterize: bool = False,
    ) -> None:
        """Copy, optimize-export, or rasterize PDF files to the destination directory."""
        ok = 0
        failed: list[tuple[str, str]] = []

        for src in targets:
            try:
                if not os.path.exists(src):
                    failed.append((src, "元ファイルが見つかりません"))
                    continue

                filename = os.path.basename(src)
                dst_path = ensure_unique_path(dst_dir, filename, pattern="{stem}({i}){ext}")
                if rasterize:
                    rasterize_pdf(src, str(dst_path))
                elif optimize_level > 0:
                    export_pdf_compressed(
                        src, str(dst_path),
                        optimize_level=optimize_level,
                        image_dpi=image_dpi,
                        image_quality=image_quality,
                    )
                else:
                    shutil.copy2(src, dst_path)
                ok += 1
            except Exception as e:
                failed.append((src, str(e)))

        self._show_export_result(ok, failed)

    def _export_as_images(
        self,
        targets: list[str],
        dst_dir: str,
        fmt: str,
        *,
        dpi: int = 150,
        quality: int = 85,
    ) -> None:
        """Export all pages of each target PDF as images."""
        ok = 0
        failed: list[tuple[str, str]] = []

        for src in targets:
            try:
                if not os.path.exists(src):
                    failed.append((src, "元ファイルが見つかりません"))
                    continue
                created = export_pages_as_images(
                    src, dst_dir, fmt=fmt, dpi=dpi, quality=quality,
                )
                ok += len(created)
            except Exception as e:
                failed.append((src, str(e)))

        label = "ページ" if fmt != "pdf" else "件"
        self._show_export_result(ok, failed, label=label)

    def _show_export_result(
        self,
        ok: int,
        failed: list[tuple[str, str]],
        label: str = "件",
    ) -> None:
        if failed:
            details = "\n".join([f"- {os.path.basename(s)}: {r}" for s, r in failed[:20]])
            if len(failed) > 20:
                details += f"\n...（他 {len(failed) - 20} 件）"
            QMessageBox.warning(
                self,
                "エクスポート結果",
                f"{ok} {label}エクスポートしました。\n失敗: {len(failed)} 件\n\n{details}",
            )
        else:
            QMessageBox.information(self, "エクスポート結果", f"{ok} {label}エクスポートしました。")

    def _on_print(self) -> None:
        """Print selected PDFs (or all PDFs if none selected)."""
        targets = (
            [c.pdf_path for c in self._selected_cards]
            if self._selected_cards
            else [c.pdf_path for c in self._cards]
        )
        if not targets:
            QMessageBox.information(self, "Print", "印刷対象のPDFがありません。")
            return
        print_pdfs(targets, self)

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

        try:
            do_rotate()
        except PdfWritePermissionError as error:
            self._handle_pdf_write_permission_denied(error)
            return

        self._undo_manager.add_action(UndoAction(
            description=f"Rotate {len(rotations)} PDF(s)",
            undo_func=undo_rotate,
            redo_func=do_rotate
        ))

    def _on_select_all(self) -> None:
        """Handle select all action (includes folder cards)."""
        self._clear_selection()
        for fc in self._folder_cards:
            fc.set_selected(True)
            self._selected_folder_cards.append(fc)
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
                        self._clear_all_drop_targets(except_card=target_card)
                        target_card.set_drop_target(True)
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
                        self._clear_all_drop_targets(except_card=target_card)
                        target_card.set_drop_target(True)
                        self._drop_indicator_index = -2
                        return

            # Show insert indicator
            self._show_drop_indicator(drop_pos)
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
        from src.views.page_edit_window import PAGETHUMBNAIL_MIME_TYPE

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
            
        except PdfWritePermissionError as error:
            shutil.copy2(backup_path, target_path)
            self._handle_pdf_write_permission_denied(error)
            return
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
                if ext in _IMPORT_EXTS:
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
