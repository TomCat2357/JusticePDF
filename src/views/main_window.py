# src/views/main_window.py
"""Main window for JusticePDF application."""
import os
import logging
import time
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow,
    QApplication,
    QWidget,
    QVBoxLayout,
    QToolBar,
    QPushButton,
    QDialog,
    QScrollArea,
    QGridLayout,
    QInputDialog,
    QMessageBox,
    QFrame,
    QRubberBand,
    QProgressDialog,
    QMenu,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QEvent, QPoint, QRect
from PyQt6.QtGui import QKeySequence, QActionGroup

from src.views.pdf_card import PDFCard
from src.views.folder_card import FolderCard
from src.views.view_helpers import (
    clear_selection,
    log_undo_state,
    register_shortcuts,
    responsive_grid_metrics,
    viewport_width_or_fallback,
)
from src.controllers.folder_watcher import FolderWatcher
from src.models.undo_manager import UndoManager, UndoAction
from src.utils import app_settings, order_store
from src.utils.pdf_utils import (
    PdfWritePermissionError,
    rotate_pages,
    get_page_count,
    clear_pixmap_cache,
    clear_pixmap_cache_for_path,
    print_pdfs,
)
from src.views.print_dialog import PrintDialog
from src.views.settings_dialog import SettingsDialog
from src.utils.constants import (
    PDFCARD_MIME_TYPE,
    FOLDERCARD_MIME_TYPE,
)
from src.utils.path_utils import ensure_unique_path
from src.utils.windows_shell import show_native_file_context_menu
from src.workers.file_worker import FileOperationWorker
from src.workers.import_worker import ImportWorker
from src.views.main_window_fileops import FileOpsMixin
from src.views.main_window_split import SplitMixin
from src.views.main_window_import import ImportMixin
from src.views.main_window_export import ExportMixin
from src.views.main_window_dragdrop import DragDropMixin

logger = logging.getLogger(__name__)


class MainWindow(FileOpsMixin, SplitMixin, ImportMixin, ExportMixin, DragDropMixin, QMainWindow):
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
        # 内部操作で busy 登録した時刻（正規化パス -> time.monotonic()）。
        # watchdog イベントを取りこぼしても、TTL を過ぎた登録は reconcile の
        # 除外対象から外し、ディスク差分での再同期を再開させる（恒久ズレ防止）。
        self._busy_since: dict[str, float] = {}
        self._BUSY_TTL_SEC = 6.0
        # Last signature that was rendered successfully for each PDF card.
        # A missing entry (or a 0-page card) is retried by reconcile even when
        # watchdog's final modified event was lost during an external copy.
        self._file_signatures: dict[str, tuple[int, int, int, int]] = {}
        # norm path -> (last observed signature, consecutive observation count)
        self._file_stability: dict[
            str, tuple[tuple[int, int, int, int], int]
        ] = {}
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

        # Debounce persisting the folder order/sort mode (see order_store.py
        # and docs/folder-order-persistence-plan.md).
        self._order_dirty = False
        self._order_save_timer = QTimer(self)
        self._order_save_timer.setSingleShot(True)
        self._order_save_timer.setInterval(500)
        self._order_save_timer.timeout.connect(self._flush_order_save)

        # Low-frequency backstop poll for missed watchdog events (bulk copies,
        # cloud-synced folders, AV interference). A minimized window is still
        # polled so missed events are recovered before the user restores it.
        self._reconcile_poll_timer = QTimer(self)
        self._reconcile_poll_timer.setInterval(2000)
        self._reconcile_poll_timer.timeout.connect(self._maybe_poll_reconcile)
        self._reconcile_poll_timer.start()

        # Setup working directory
        if folder_path:
            self._work_dir = Path(folder_path)
            self._work_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._work_dir = app_settings.ensure_library_dir()
        self._is_root_window = folder_path is None

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
        self._watcher = self._create_watcher(str(self._work_dir))
        self._watcher.start()

        # Register this window in the cross-window registry
        MainWindow._instances.append(self)

        # Update title for non-root windows
        if folder_path is not None:
            self.setWindowTitle(f"JusticePDF - {self._work_dir.name}")

        # Load existing files
        self._load_existing_files()

    def _create_watcher(self, folder: str) -> FolderWatcher:
        """Create a FolderWatcher for *folder* with all signals connected.

        Keeps the signal wiring in one place.
        """
        watcher = FolderWatcher(folder)
        watcher.file_added.connect(self._on_file_added)
        watcher.file_removed.connect(self._on_file_removed)
        watcher.file_modified.connect(self._on_file_modified)
        watcher.folder_added.connect(self._on_folder_added)
        watcher.folder_removed.connect(self._on_folder_removed)
        return watcher

    @classmethod
    def open_external_folder(cls, source_dir: str) -> "MainWindow":
        """Open a folder launched from Explorer's「JusticePDFで開く」.

        The folder is copied into the PDFs library (configured via the
        設定ダイアログ; defaults to ``~/Documents/PDFs``) under a non-colliding
        name and that copy becomes this window's work dir; the folder's
        contents are imported into it once the event loop starts.

        Only this single window opens — the work dir is the copy itself, so
        there is no separate PDFs-library window.  (This differs from importing
        a file, which lands flat in the library window, and from a mixed
        file+folder launch, which still needs the library window as host.)
        """
        library = app_settings.ensure_library_dir()
        folder_name = os.path.basename(os.path.abspath(source_dir).rstrip(os.sep)) or "folder"
        dest = Path(str(ensure_unique_path(library, folder_name, pattern="{stem}({i}){ext}")))
        dest.mkdir(parents=True, exist_ok=True)

        window = cls(folder_path=str(dest))

        # Import the folder's *contents* (not the folder itself) into the copy,
        # reproducing its structure without nesting it under a same-named child.
        # Deferred so the ImportWorker's progress dialog runs after show().
        try:
            children = [str(p) for p in Path(source_dir).iterdir()]
        except OSError:
            children = []
        if children:
            QTimer.singleShot(0, lambda: window.import_external_paths(children))
        return window

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

        self._undo_btn = QPushButton("元に戻す")
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("やり直し")
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addSeparator()

        self._delete_btn = QPushButton("削除")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self._delete_btn)

        # 名前変更（ファイル名 / PDF名）を 1 つのボタンに統合し、
        # クリックでドロップダウンメニューを表示する。
        self._rename_btn = QPushButton("名前変更")
        self._rename_menu = QMenu(self._rename_btn)
        self._rename_file_action = self._rename_menu.addAction("ファイル名")
        self._rename_file_action.triggered.connect(self._on_rename)
        self._rename_title_action = self._rename_menu.addAction("PDF名")
        self._rename_title_action.triggered.connect(self._on_rename_pdf_title)
        # フォルダを1つだけ選択しているときに現れる「フォルダ名」変更。
        self._rename_folder_action = self._rename_menu.addAction("フォルダ名")
        self._rename_folder_action.triggered.connect(self._on_rename_folder_selected)
        self._rename_btn.setMenu(self._rename_menu)
        toolbar.addWidget(self._rename_btn)

        # 結合（複数→1つ）と分解（1つ→複数）は互いに逆操作なので 1 つのボタンに
        # 統合し、クリックでドロップダウンメニューを表示する。
        self._merge_split_btn = QPushButton("結合・分解")
        self._merge_split_btn.setToolTip("複数ファイルを1つに結合／1つのPDFを複数に分解")
        self._merge_split_menu = QMenu(self._merge_split_btn)
        self._merge_split_menu.setToolTipsVisible(True)
        self._merge_action = self._merge_split_menu.addAction("結合")
        self._merge_action.setToolTip(
            "選択したファイル・フォルダを1つのPDFに結合\n"
            "（フォルダ構成をしおりの階層として再現します）"
        )
        self._merge_action.triggered.connect(self._on_merge_selected)
        self._split_action = self._merge_split_menu.addAction("分解")
        self._split_action.setToolTip(
            "選択したPDFをしおりの最上位階層ごとに複数ファイルへ分解\n"
            "（しおりが無い場合は1ページずつ分解します）"
        )
        self._split_action.triggered.connect(self._on_split_selected)
        self._merge_split_btn.setMenu(self._merge_split_menu)
        toolbar.addWidget(self._merge_split_btn)

        toolbar.addSeparator()

        # インポート（ファイル / フォルダ）を 1 つのボタンに統合し、
        # クリックでドロップダウンメニューを表示する。
        self._import_btn = QPushButton("インポート")
        self._import_menu = QMenu(self._import_btn)
        import_files_action = self._import_menu.addAction("ファイルをインポート")
        import_files_action.triggered.connect(self._on_import)
        import_folder_action = self._import_menu.addAction("フォルダをインポート")
        import_folder_action.triggered.connect(self._on_import_folder)
        self._import_btn.setMenu(self._import_menu)
        toolbar.addWidget(self._import_btn)

        # 新規作成（ファイル / フォルダ）を 1 つのボタンに統合し、
        # クリックでドロップダウンメニューを表示する。
        self._new_btn = QPushButton("新規作成")
        self._new_menu = QMenu(self._new_btn)
        new_file_action = self._new_menu.addAction("ファイル")
        new_file_action.triggered.connect(self._on_new_file)
        new_folder_action = self._new_menu.addAction("フォルダ")
        new_folder_action.triggered.connect(self._on_new_folder)
        self._new_btn.setMenu(self._new_menu)
        toolbar.addWidget(self._new_btn)

        self._export_btn = QPushButton("エクスポート")
        self._export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self._export_btn)

        self._print_btn = QPushButton("印刷")
        self._print_btn.clicked.connect(self._on_print)
        toolbar.addWidget(self._print_btn)

        toolbar.addSeparator()

        self._rotate_btn = QPushButton("回転")
        self._rotate_btn.clicked.connect(self._on_rotate)
        toolbar.addWidget(self._rotate_btn)

        self._select_all_btn = QPushButton("すべて選択")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self._select_all_btn)

        # 並び替え（名前順・日付順 × 昇順・降順）を 1 つのボタンに統合し、
        # クリックでドロップダウンメニューを表示する。
        self._sort_btn = QPushButton("並び替え")
        self._sort_menu = QMenu(self._sort_btn)
        self._sort_action_group = QActionGroup(self)
        self._sort_action_group.setExclusive(True)

        def _add_sort_action(text: str, sort_type: str, ascending: bool):
            action = self._sort_menu.addAction(text)
            action.setCheckable(True)
            action.triggered.connect(lambda: self._apply_sort(sort_type, ascending))
            self._sort_action_group.addAction(action)
            return action

        self._sort_action_name_asc = _add_sort_action("名前順（昇順）", "name", True)
        self._sort_action_name_desc = _add_sort_action("名前順（降順）", "name", False)
        self._sort_action_date_asc = _add_sort_action("日付順（昇順）", "date", True)
        self._sort_action_date_desc = _add_sort_action("日付順（降順）", "date", False)
        self._sort_menu.addSeparator()
        self._sort_action_manual = self._sort_menu.addAction("手動順")
        self._sort_action_manual.setCheckable(True)
        self._sort_action_manual.triggered.connect(self._apply_manual_sort)
        self._sort_action_group.addAction(self._sort_action_manual)
        self._sort_btn.setMenu(self._sort_menu)
        toolbar.addWidget(self._sort_btn)
        self._sync_sort_menu_state()

        toolbar.addSeparator()

        # 設定（デフォルトで開くフォルダ等）。単独動作でファイル操作に
        # 影響しないため、選択状態に関わらず常に有効。
        self._settings_btn = QPushButton("設定")
        self._settings_btn.clicked.connect(self._on_open_settings)
        toolbar.addWidget(self._settings_btn)

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
        n_folders = len(self._selected_folder_cards)
        n_files = len(self._selected_cards)
        self._delete_btn.setEnabled(has_deletable and not busy)
        # 名前変更: ファイル1つだけ、またはフォルダ1つだけ選択しているとき有効。
        # 選択種別に応じてメニュー項目（ファイル名/PDF名 ↔ フォルダ名）を出し分ける。
        one_file = n_files == 1 and n_folders == 0
        one_folder = n_folders == 1 and n_files == 0
        self._rename_file_action.setVisible(one_file)
        self._rename_title_action.setVisible(one_file)
        self._rename_folder_action.setVisible(one_folder)
        self._rename_btn.setEnabled((one_file or one_folder) and not busy)
        self._rotate_btn.setEnabled(has_selection and not busy)
        self._undo_btn.setEnabled(self._undo_manager.can_undo() and not busy)
        self._redo_btn.setEnabled(self._undo_manager.can_redo() and not busy)
        # エクスポート: ファイルまたはフォルダを1つ以上選択しているとき有効
        self._export_btn.setEnabled((n_files >= 1 or n_folders >= 1) and not busy)
        # 結合: フォルダを1つ以上、またはファイルを2つ以上選択しているとき
        can_merge = n_folders >= 1 or n_files >= 2
        # 分解: PDFファイルをちょうど1つだけ選択しているとき
        can_split = n_files == 1 and n_folders == 0
        self._merge_action.setVisible(can_merge)
        self._split_action.setVisible(can_split)
        self._merge_split_btn.setEnabled((can_merge or can_split) and not busy)

    def _on_open_settings(self) -> None:
        """「設定」ボタンのハンドラ。デフォルトで開くフォルダを変更する。

        反映は次回起動から（開いているウィンドウの作業フォルダは切り替えない）。
        """
        dialog = SettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folder = dialog.selected_folder()
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "設定", f"フォルダを作成できません:\n{e}")
            return
        app_settings.set_library_dir(folder)
        QMessageBox.information(self, "設定", "次回起動時から有効になります。")

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
        # Any action that pushes/undoes/redoes typically mutates card order
        # (or the file set the manual order refers to); persist it. Harmless
        # no-op writes (unchanged order) are cheap and debounced.
        self._schedule_order_save()

    def _clear_undo_history(self) -> None:
        """Clear undo/redo history (for external file changes)."""
        self._undo_manager.clear()
        self._update_button_states()

    def _normalize_path(self, path: str) -> str:
        """Normalize paths for internal tracking."""
        return os.path.normcase(os.path.abspath(path))

    def _get_file_signature(self, path: str) -> tuple[int, int, int, int] | None:
        """Return a signature that changes for writes and atomic replacement."""
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)

    def _record_card_signature(self, card: PDFCard) -> None:
        """Remember the on-disk version represented by a successfully loaded card."""
        normalized = self._normalize_path(card.pdf_path)
        signature = self._get_file_signature(card.pdf_path)
        if card.page_count > 0 and signature is not None:
            self._file_signatures[normalized] = signature
        else:
            # Keep failed/partial loads retryable even if their stat is stable.
            self._file_signatures.pop(normalized, None)

    def _observe_file_stability(self, path: str) -> bool:
        """Return True after two equal signatures and a successful PDF parse.

        Explorer/cloud copies can be readable and non-empty before the PDF
        trailer has arrived.  Requiring both a stable stat and a successful
        parse prevents committing that transient state as a permanent 0-page
        card.  Parse failures remain queued for a later reconcile pass.
        """
        normalized = self._normalize_path(path)
        signature = self._get_file_signature(path)
        if signature is None or signature[0] <= 0:
            self._file_stability.pop(normalized, None)
            return False

        previous = self._file_stability.get(normalized)
        count = previous[1] + 1 if previous and previous[0] == signature else 1
        self._file_stability[normalized] = (signature, count)
        if count < 2:
            return False
        if get_page_count(path) <= 0:
            return False

        self._file_stability.pop(normalized, None)
        return True

    def _register_internal_add(self, paths: list[str]) -> None:
        """Mark paths as internally added to avoid clearing undo history."""
        now = time.monotonic()
        for path in paths:
            norm = self._normalize_path(path)
            self._internal_adds.add(norm)
            self._busy_since[norm] = now

    def _register_internal_remove(self, paths: list[str]) -> None:
        """Mark paths as internally removed to avoid clearing undo history."""
        now = time.monotonic()
        for path in paths:
            norm = self._normalize_path(path)
            self._internal_removes.add(norm)
            self._busy_since[norm] = now

    def _track_pending_rename(self, old_path: str, new_path: str) -> None:
        old_norm = self._normalize_path(old_path)
        new_norm = self._normalize_path(new_path)
        self._pending_rename_old_to_new[old_norm] = new_norm
        self._pending_rename_new_to_old[new_norm] = old_norm
        now = time.monotonic()
        self._busy_since[old_norm] = now
        self._busy_since[new_norm] = now

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


    def _load_existing_files(self) -> None:
        """Load existing subfolders and PDF files from the work directory.

        Restores the previously saved sort mode / manual order for this
        folder, if any (see order_store.py and
        docs/folder-order-persistence-plan.md §Phase 2). Falls back to the
        pre-persistence defaults ("manual" order, disk listing) when there is
        no saved entry or it is malformed.
        """
        entry = order_store.load_folder_order(self._work_dir)

        sort_order = "manual"
        sort_ascending = True
        if entry is not None:
            saved_sort_order = entry.get("sort_order")
            if saved_sort_order in ("manual", "name", "date"):
                sort_order = saved_sort_order
            saved_ascending = entry.get("sort_ascending")
            if isinstance(saved_ascending, bool):
                sort_ascending = saved_ascending
        self._sort_order = sort_order
        self._sort_ascending = sort_ascending

        disk_folders = list(self._watcher.get_subfolders())
        disk_files = list(self._watcher.get_pdf_files())

        if self._sort_order == "manual":
            saved_subfolders = (entry or {}).get("manual_subfolders") or []
            saved_files = (entry or {}).get("manual_files") or []
            if not isinstance(saved_subfolders, list):
                saved_subfolders = []
            if not isinstance(saved_files, list):
                saved_files = []

            # merge_order() sorts disk-only names case-insensitively when the
            # saved list is empty, which reproduces the pre-Phase-2 forced
            # name sort for subfolders when no saved order exists yet.
            folder_by_name = {os.path.basename(p): p for p in disk_folders}
            file_by_name = {os.path.basename(p): p for p in disk_files}
            ordered_folder_names = order_store.merge_order(list(folder_by_name), saved_subfolders)
            ordered_file_names = order_store.merge_order(list(file_by_name), saved_files)

            for name in ordered_folder_names:
                self._add_folder_card(folder_by_name[name])
            for name in ordered_file_names:
                self._add_card(file_by_name[name])
        else:
            for folder_path in disk_folders:
                self._add_folder_card(folder_path)
            for pdf_path in disk_files:
                self._add_card(pdf_path)
            self._sort_cards()

        self._sync_sort_menu_state()
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
            reserve_vertical_scrollbar=True,
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
        self._record_card_signature(card)
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

    def _schedule_order_save(self) -> None:
        """Debounced trigger for persisting the folder order/sort mode.

        Marks the state dirty and (re)starts a single-shot timer so rapid
        successive changes (e.g. multiple D&D moves) collapse into a single
        write. See docs/folder-order-persistence-plan.md §1-5.
        """
        self._order_dirty = True
        self._order_save_timer.start()

    def _flush_order_save(self) -> None:
        """Persist the current order/sort state immediately, if dirty.

        Invariant (see docs/folder-order-persistence-plan.md §1-4): manual
        order fields are only overwritten while ``_sort_order == "manual"``.
        When a name/date sort is active, only the sort mode itself is saved
        so a previously saved manual order survives switching back to it.
        """
        if not self._order_dirty:
            return
        self._order_dirty = False
        self._order_save_timer.stop()

        manual_files = None
        manual_subfolders = None
        if self._sort_order == "manual":
            manual_files = [os.path.basename(c.pdf_path) for c in self._cards]
            manual_subfolders = [os.path.basename(fc.folder_path) for fc in self._folder_cards]

        order_store.save_folder_order(
            self._work_dir,
            manual_files=manual_files,
            manual_subfolders=manual_subfolders,
            sort_order=self._sort_order,
            sort_ascending=self._sort_ascending,
        )

    def _maybe_poll_reconcile(self) -> None:
        """Backstop poll: scan while the window is alive and visible."""
        if self.isVisible():
            self._schedule_reconcile()

    def _is_file_ready(self, path: str) -> bool:
        """Check that a freshly-appeared PDF is stable and parseable."""
        return self._observe_file_stability(path)

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

        now = time.monotonic()
        ttl = self._BUSY_TTL_SEC
        raw_busy = (
            self._internal_adds
            | self._internal_removes
            | set(self._pending_rename_old_to_new.keys())
            | set(self._pending_rename_new_to_old.keys())
            | self._pending_rename_removed
            | self._pending_rename_added
        )
        # まだ新しい登録のみ busy 扱い。TTL を過ぎたものは watchdog イベントが
        # 来なかった（クラウド/ネットワーク/AV）とみなし reconcile に処理させる。
        # reconcile は冪等なので再処理は安全。期限切れでもパス自体は
        # _internal_*/rename dict に残し（_busy_since の刻印だけ消す）、遅延 watchdog
        # イベント到来時に undo 履歴の誤消去を防ぐ。
        busy = {p for p in raw_busy if (now - self._busy_since.get(p, 0.0)) < ttl}
        for p in list(self._busy_since):
            if p not in raw_busy or (now - self._busy_since[p]) >= ttl:
                self._busy_since.pop(p, None)

        changed = False

        # --- PDF cards ---
        card_by_norm = {self._normalize_path(c.pdf_path): c for c in self._cards}
        for norm, real in disk_pdf_norm.items():
            if norm in busy:
                # Observe only; never mutate cards while an internal operation
                # is protected.  If watchdog is lost, this lets the first pass
                # after TTL expiry complete the two-sample stability check.
                self._observe_file_stability(real)
                continue
            card = card_by_norm.get(norm)
            signature = self._get_file_signature(real)
            if card is not None:
                rendered_signature = self._file_signatures.get(norm)
                if card.page_count > 0 and signature == rendered_signature:
                    self._file_stability.pop(norm, None)
                    continue
                if not self._is_file_ready(real):
                    continue
                clear_pixmap_cache_for_path(real)
                card.refresh()
                self._record_card_signature(card)
                changed = True
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
            self._schedule_order_save()

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
                normalized = self._normalize_path(card.pdf_path)
                self._file_signatures.pop(normalized, None)
                self._file_stability.pop(normalized, None)
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
        cols, card_width = responsive_grid_metrics(
            available_width,
            self._preview_card_width,
            spacing,
            m.left() + m.right(),
        )

        # Keep the grid at least as wide as the viewport.  This prevents the
        # layout's size hint from making QScrollArea choose a wider content
        # widget than the current window.
        self._container.setMinimumWidth(max(1, int(available_width)))

        # The card width and thumbnail size are coupled.  Recalculate both so
        # cards fit the current viewport while preserving the preview aspect.
        thumb_size = max(1, int(round(card_width / self._preview_card_ratio)))
        pdf_thumbnail_size_changed = any(
            card._thumb_size != thumb_size for card in self._cards
        )
        for item in (*self._folder_cards, *self._cards):
            item.set_preview_size_fast(card_width, thumb_size)
        if pdf_thumbnail_size_changed:
            self._zoom_debounce_timer.start()

        all_items: list[QWidget] = list(self._folder_cards) + list(self._cards)
        for i, item in enumerate(all_items):
            row = i // cols
            col = i % cols
            self._grid_layout.addWidget(item, row, col)

    def _on_deferred_grid_refresh(self) -> None:
        """Callback for the debounced grid refresh timer."""
        self._refresh_grid()

    def _sort_cards(self) -> None:
        """Sort cards based on current sort order.

        フォルダはフォルダ同士、ファイルはファイル同士で並び替える
        （グリッド表示はフォルダが先、次にファイルの順）。
        """
        if self._sort_order == "name":
            self._cards.sort(key=lambda c: c.filename.lower(), reverse=not self._sort_ascending)
            self._folder_cards.sort(key=lambda c: c.filename.lower(), reverse=not self._sort_ascending)
        elif self._sort_order == "date":
            def get_mtime(path: str) -> float:
                try:
                    return os.path.getmtime(path)
                except OSError:
                    return 0.0
            self._cards.sort(key=lambda c: get_mtime(c.pdf_path), reverse=not self._sort_ascending)
            self._folder_cards.sort(key=lambda c: get_mtime(c.folder_path), reverse=not self._sort_ascending)

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
        self._refresh_grid()
        self._zoom_debounce_timer.start()

    def eventFilter(self, obj, event) -> bool:
        scroll_area = getattr(self, "_scroll_area", None)
        if scroll_area and obj is scroll_area.viewport():
            if event.type() == QEvent.Type.Resize:
                # A vertical scrollbar can appear after the grid is laid out,
                # reducing the viewport width by its own width.
                self._grid_refresh_timer.start()
                return False
            if event.type() != QEvent.Type.Wheel:
                return super().eventFilter(obj, event)
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
        self._schedule_order_save()

    def _on_folder_removed(self, path: str) -> None:
        """Handle subfolder removed from disk."""
        normalized = self._normalize_path(path)
        if normalized in self._internal_removes:
            self._internal_removes.discard(normalized)
            if self._get_folder_card_by_path(path) is None:
                return
        self._remove_folder_card(path)
        self._grid_refresh_timer.start()
        self._schedule_order_save()

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
        self._open_folder_in_new_window(fc.folder_path)

    def _open_folder_in_new_window(self, folder_path: str) -> "MainWindow | None":
        """Open ``folder_path`` in a cascaded child window (or focus if open).

        Shared by subfolder double-click and the Explorer "open folder" launch
        (which mirrors a dropped folder into the PDFs library, then opens the
        copy here).  Returns the window now showing the folder, or ``None`` if
        the path is not a directory.
        """
        if not os.path.isdir(folder_path):
            return None
        # If already open, bring that window to front.
        for w in MainWindow._instances:
            if w is self:
                continue
            try:
                if self._normalize_path(str(w._work_dir)) == self._normalize_path(folder_path):
                    if w.isMinimized():
                        w.setWindowState(
                            (w.windowState() & ~Qt.WindowState.WindowMinimized)
                            | Qt.WindowState.WindowActive
                        )
                    w.show()
                    w.raise_()
                    w.activateWindow()
                    return w
            except RuntimeError:
                continue

        new_window = MainWindow(folder_path)

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
        return new_window

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
        rename_action = menu.addAction("名前変更")
        delete_action = menu.addAction("削除")
        chosen = menu.exec(global_pos)
        if chosen is rename_action:
            self._rename_folder(fc)
        elif chosen is delete_action:
            self._delete_folder(fc)


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
                self._record_card_signature(card)
                self._refresh_page_edit_windows_for_paths([path])
                self._grid_refresh_timer.start()
                self._schedule_reconcile()
                return
        if normalized in self._internal_adds:
            self._internal_adds.discard(normalized)
        else:
            self._clear_undo_history()
        # Seed the stability observation and let reconcile add the card only
        # after the same signature is seen again and the PDF parses cleanly.
        self._observe_file_stability(path)
        self._schedule_reconcile()

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
        self._schedule_order_save()

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
                    self._record_card_signature(card)
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


    def _on_refresh(self) -> None:
        """Reload cards and open edit windows from disk."""
        clear_pixmap_cache()
        self._reconcile_with_disk()  # pick up files/folders added or removed externally
        self._refresh_all_views()    # re-render thumbnails of the (now-current) cards


    # ─────────────────────────────────────────────────────────────────
    # Import helpers — asynchronous with cancellation
    # ─────────────────────────────────────────────────────────────────


    def _on_print(self) -> None:
        """Print selected PDFs (or all PDFs if none selected)."""
        targets = (
            [c.pdf_path for c in self._selected_cards]
            if self._selected_cards
            else [c.pdf_path for c in self._cards]
        )
        if not targets:
            QMessageBox.information(self, "印刷", "印刷対象のPDFがありません。")
            return
        dialog = PrintDialog(targets, self, current_index=None)
        if dialog.exec() != PrintDialog.DialogCode.Accepted:
            return
        print_pdfs(targets, self, settings=dialog.get_settings(), printer=dialog.build_printer())

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

    def _apply_sort(self, sort_type: str, ascending: bool) -> None:
        """Sort cards by the specified type/direction with undo support.

        Only the sort mode is persisted here (§1-4 of the design doc): a
        previously saved manual order must survive switching to name/date
        sorting so switching back to "manual" restores it.
        """
        # Store paths instead of widget references. _sort_cards() reorders
        # _folder_cards too, so its pre-sort order must be captured and
        # restored on undo/redo as well — otherwise undoing a name/date sort
        # while sort_order returns to "manual" would persist the (still
        # name-sorted) folder order as manual_subfolders, clobbering it.
        old_paths = [card.pdf_path for card in self._cards]
        old_folder_paths = [fc.folder_path for fc in self._folder_cards]
        old_sort_order = self._sort_order
        old_ascending = self._sort_ascending

        self._sort_order = sort_type
        self._sort_ascending = ascending

        self._sort_cards()
        self._refresh_grid()
        self._sync_sort_menu_state()
        self._schedule_order_save()
        new_paths = [card.pdf_path for card in self._cards]
        new_folder_paths = [fc.folder_path for fc in self._folder_cards]

        def _reorder_folder_cards(paths: list[str]) -> None:
            by_path = {fc.folder_path: fc for fc in self._folder_cards}
            ordered = [by_path[p] for p in paths if p in by_path]
            extra = [fc for fc in self._folder_cards if fc.folder_path not in paths]
            self._folder_cards = ordered + extra

        def undo():
            self._rebuild_cards_from_paths(old_paths)
            _reorder_folder_cards(old_folder_paths)
            self._sort_order = old_sort_order
            self._sort_ascending = old_ascending
            self._refresh_grid()
            self._sync_sort_menu_state()
            self._schedule_order_save()

        def redo():
            self._rebuild_cards_from_paths(new_paths)
            _reorder_folder_cards(new_folder_paths)
            self._sort_order = sort_type
            self._sort_ascending = ascending
            self._refresh_grid()
            self._sync_sort_menu_state()
            self._schedule_order_save()

        self._undo_manager.add_action(UndoAction(
            description=f"Sort by {sort_type}",
            undo_func=undo,
            redo_func=redo
        ))
        self._update_button_states()

    def _apply_manual_sort(self) -> None:
        """Switch to manual order, restoring the saved order from the store.

        See docs/folder-order-persistence-plan.md §Phase 4. This is the only
        UI entry point for returning to "manual" once a name/date sort has
        been applied (D&D also sets it implicitly).
        """
        entry = order_store.load_folder_order(self._work_dir)
        saved_files = (entry or {}).get("manual_files") or []
        saved_subfolders = (entry or {}).get("manual_subfolders") or []
        if not isinstance(saved_files, list):
            saved_files = []
        if not isinstance(saved_subfolders, list):
            saved_subfolders = []

        file_by_name = {os.path.basename(c.pdf_path): c for c in self._cards}
        folder_by_name = {os.path.basename(fc.folder_path): fc for fc in self._folder_cards}
        ordered_file_names = order_store.merge_order(list(file_by_name), saved_files)
        ordered_folder_names = order_store.merge_order(list(folder_by_name), saved_subfolders)

        self._sort_order = "manual"
        self._cards = [file_by_name[n] for n in ordered_file_names]
        self._folder_cards = [folder_by_name[n] for n in ordered_folder_names]
        self._refresh_grid()
        self._sync_sort_menu_state()
        self._schedule_order_save()
        self._update_button_states()

    def _sync_sort_menu_state(self) -> None:
        """Reflect the current sort mode/direction in the sort menu's checked state."""
        actions = (
            self._sort_action_name_asc,
            self._sort_action_name_desc,
            self._sort_action_date_asc,
            self._sort_action_date_desc,
            self._sort_action_manual,
        )
        for action in actions:
            action.setChecked(False)

        if self._sort_order == "manual":
            self._sort_action_manual.setChecked(True)
            return
        mapping = {
            ("name", True): self._sort_action_name_asc,
            ("name", False): self._sort_action_name_desc,
            ("date", True): self._sort_action_date_asc,
            ("date", False): self._sort_action_date_desc,
        }
        action = mapping.get((self._sort_order, self._sort_ascending))
        if action is not None:
            action.setChecked(True)


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


    def resizeEvent(self, event) -> None:
        """Handle window resize."""
        super().resizeEvent(event)
        # As requested: even tiny resizes can reflow. The key fix is that the logic is identical to initial.
        self._refresh_grid()
        # The vertical scrollbar may be created by the refresh above. Run one
        # more pass after Qt has settled the viewport geometry.
        self._grid_refresh_timer.start()

    def closeEvent(self, event) -> None:
        """Handle window close."""
        self._undo_manager.remove_listener(self._on_undo_manager_changed)
        self._flush_order_save()
        self._watcher.stop()
        super().closeEvent(event)
