"""Generate screenshots used by JusticePDFの使い方.docx.

Renders MainWindow / PageEditWindow / dialogs offscreen with sample PDFs
and saves PNGs to ``tools/manual_assets``.

Run:
    uv run python tools\\build_manual_screenshots.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterable

# Headless Qt — must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # PyMuPDF
from PyQt6.QtCore import QObject, Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import QApplication, QToolBar

from src.views import main_window, pdf_card
from src.views import page_edit_window as page_edit_window_module
from src.views.export_dialog import ExportOptionsDialog
from src.views.search_dialog import SearchDialog
from src.models.undo_manager import UndoManager

OUT_DIR = ROOT / "tools" / "manual_assets"


# ---------- Test doubles ----------

class FakeWatcher(QObject):
    """No-op file watcher mirroring the real ``FolderWatcher`` API."""

    file_added = pyqtSignal(str)
    file_removed = pyqtSignal(str)
    file_modified = pyqtSignal(str)
    folder_added = pyqtSignal(str)
    folder_removed = pyqtSignal(str)

    def __init__(self, folder_path: str):
        super().__init__()
        self._folder_path = folder_path

    def start(self) -> None:  # pragma: no cover - no-op
        pass

    def stop(self) -> None:  # pragma: no cover - no-op
        pass

    def get_subfolders(self) -> list[str]:
        return []


# ---------- Sample data ----------

SAMPLE_PDFS = [
    ("契約書_A社.pdf", "契約書 A 社", 3),
    ("見積書_B工務店.pdf", "見積書 B 工務店", 2),
    ("会議資料_4月.pdf", "会議資料 4 月", 5),
    ("打合せメモ.pdf", "打合せメモ", 1),
    ("仕様書_v3.pdf", "仕様書 v3", 4),
    ("提案書.pdf", "提案書", 6),
]


def _make_sample_pdf(path: Path, title: str, page_count: int) -> None:
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page(width=595, height=842)
        # Use Helvetica for ASCII, draw filename-ish placeholder text.
        page.insert_text(
            (60, 90), title, fontname="helv", fontsize=24
        )
        page.insert_text(
            (60, 130),
            f"page {i + 1} / {page_count}",
            fontname="helv",
            fontsize=14,
        )
        # Lorem-ipsum-ish body so text-search has hits.
        body = (
            "Sample document body. JusticePDF user manual demo.\n"
            "The quick brown fox jumps over the lazy dog.\n"
            "契約 / 見積 / 会議 / 提案 / 仕様 - sample keywords.\n"
        )
        page.insert_text((60, 170), body, fontname="helv", fontsize=11)
        # Decorative rectangle so thumbnails look distinct.
        rect = fitz.Rect(50, 60, 545, 200)
        page.draw_rect(rect, color=(0.3, 0.3, 0.7), width=1.2)
    doc.save(str(path))
    doc.close()


def _populate_work_dir(work_dir: Path) -> list[Path]:
    pdf_paths: list[Path] = []
    for name, title, n in SAMPLE_PDFS:
        p = work_dir / name
        _make_sample_pdf(p, title, n)
        pdf_paths.append(p)
    # Subfolders for folder cards.
    for sub in ("過去案件", "アーカイブ"):
        sd = work_dir / sub
        sd.mkdir(exist_ok=True)
        # Put one PDF inside so item count shows >0.
        _make_sample_pdf(sd / "old.pdf", "old document", 2)
    return pdf_paths


# ---------- Helpers ----------

def _save(widget, name: str) -> Path:
    """Grab a widget into a PNG and save under OUT_DIR."""
    QApplication.processEvents()
    pix = widget.grab()
    out = OUT_DIR / name
    pix.save(str(out))
    print(f"  saved {out.name}  ({pix.width()}x{pix.height()})")
    return out


def _patch_main_window(monkeypatch_targets: list[tuple]):
    """Apply monkey-patches similar to tests/conftest.py + window_factory."""
    setattr(main_window, "FolderWatcher", FakeWatcher)
    main_window.MainWindow._load_existing_files = lambda self: None  # type: ignore[assignment]
    monkeypatch_targets.append(None)  # placeholder


# ---------- Scene builders ----------

def build_main_window(work_dir: Path, with_folders: bool = True) -> "main_window.MainWindow":
    win = main_window.MainWindow()
    win.resize(1100, 720)
    # Override _work_dir to our sample dir so derived paths are sensible.
    win._work_dir = work_dir
    win.setWindowTitle(f"JusticePDF - {work_dir.name}")
    win._cards = []
    # Add PDF cards
    for p in sorted(work_dir.glob("*.pdf")):
        win._add_card(str(p))
    # Add folder cards
    if with_folders:
        for sub in sorted([p for p in work_dir.iterdir() if p.is_dir()]):
            win._add_folder_card(str(sub))
    win.show()
    QApplication.processEvents()
    return win


def scene_main_idle(work_dir: Path) -> None:
    print("[scene] 01_main_idle")
    win = build_main_window(work_dir)
    _save(win, "01_main_idle.png")
    win.close()


def scene_main_selection(work_dir: Path) -> None:
    print("[scene] 02_main_selection")
    win = build_main_window(work_dir)
    # Select first 3 PDF cards
    for card in win._cards[:3]:
        card.set_selected(True)
        win._selected_cards.append(card)
    win._update_button_states()
    QApplication.processEvents()
    _save(win, "02_main_selection.png")
    win.close()


def scene_main_drop_merge(work_dir: Path) -> None:
    print("[scene] 03_main_drop_merge")
    win = build_main_window(work_dir)
    # Highlight one card as merge target (green halo)
    if len(win._cards) >= 2:
        target = win._cards[2]
        target.set_drop_target(True)
        # Visually mark a source as locked (shows "being dragged" feel).
        win._cards[0].set_selected(True)
        win._selected_cards.append(win._cards[0])
    win._update_button_states()
    QApplication.processEvents()
    _save(win, "03_main_drop_merge.png")
    win.close()


def scene_toolbar(work_dir: Path) -> None:
    print("[scene] 04_toolbar")
    win = build_main_window(work_dir)
    toolbar = win.findChild(QToolBar)
    if toolbar is None:
        print("  ! toolbar not found, skipping")
        win.close()
        return
    QApplication.processEvents()
    _save(toolbar, "04_toolbar.png")
    win.close()


def scene_folder_selection(work_dir: Path) -> None:
    print("[scene] 11_folder_selection")
    win = build_main_window(work_dir)
    for fc in win._folder_cards:
        fc.set_selected(True)
        win._selected_folder_cards.append(fc)
    win._update_button_states()
    QApplication.processEvents()
    _save(win, "11_folder_selection.png")
    win.close()


def scene_multi_window(work_dir: Path, work_dir2: Path) -> None:
    print("[scene] 12_multi_window")
    win1 = build_main_window(work_dir)
    win1.resize(720, 520)
    win1.move(0, 0)
    win2 = build_main_window(work_dir2, with_folders=False)
    win2.resize(720, 520)
    win2.move(60, 60)
    QApplication.processEvents()
    # Composite: grab a virtual area covering both.
    # Since offscreen has no real screen, just save them side-by-side via QPixmap.
    from PyQt6.QtGui import QPixmap, QPainter
    pix1 = win1.grab()
    pix2 = win2.grab()
    gap = 24
    composite = QPixmap(pix1.width() + pix2.width() + gap, max(pix1.height(), pix2.height()))
    composite.fill(Qt.GlobalColor.white)
    painter = QPainter(composite)
    painter.drawPixmap(0, 0, pix1)
    painter.drawPixmap(pix1.width() + gap, 0, pix2)
    painter.end()
    out = OUT_DIR / "12_multi_window.png"
    composite.save(str(out))
    print(f"  saved {out.name}  ({composite.width()}x{composite.height()})")
    win1.close()
    win2.close()


def _make_page_edit_window(pdf_path: Path) -> "page_edit_window_module.PageEditWindow":
    pew = page_edit_window_module.PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    pew.resize(1000, 700)
    pew.show()
    QApplication.processEvents()
    # _load_pages is queued via QTimer.singleShot(0, …) — pump events.
    for _ in range(20):
        QApplication.processEvents()
    return pew


def scene_page_edit_grid(pdf_path: Path) -> None:
    print("[scene] 05_page_edit_grid")
    pew = _make_page_edit_window(pdf_path)
    # Select first page so it has a highlight
    if pew._thumbnails:
        pew._thumbnails[0].set_selected(True)
        pew._selected_thumbnails.append(pew._thumbnails[0])
    QApplication.processEvents()
    _save(pew, "05_page_edit_grid.png")
    pew.close()


def scene_zoom_view(pdf_path: Path) -> None:
    print("[scene] 06_zoom_view")
    pew = _make_page_edit_window(pdf_path)
    if not pew._thumbnails:
        print("  ! no thumbnails; skipping")
        pew.close()
        return
    pew._open_zoom_view(0)
    QApplication.processEvents()
    for _ in range(10):
        QApplication.processEvents()
    _save(pew, "06_zoom_view.png")
    pew.close()


def scene_zoom_annotation_panel(pdf_path: Path) -> None:
    print("[scene] 07_annotation_panel")
    pew = _make_page_edit_window(pdf_path)
    if not pew._thumbnails:
        pew.close()
        return
    pew._open_zoom_view(0)
    QApplication.processEvents()
    pew._set_zoom_annotation_drawer_open(True)
    QApplication.processEvents()
    for _ in range(10):
        QApplication.processEvents()
    _save(pew, "07_annotation_panel.png")
    pew.close()


def scene_search_dialog() -> None:
    print("[scene] 09_search_dialog")
    dlg = SearchDialog()
    dlg.resize(420, 110)
    dlg.show()
    # Populate input + status so the screenshot looks meaningful.
    dlg._input.setText("契約")
    dlg.set_status(2, 5)
    QApplication.processEvents()
    _save(dlg, "09_search_dialog.png")
    dlg.close()


def scene_export_dialog() -> None:
    print("[scene] 10_export_dialog")
    dlg = ExportOptionsDialog()
    dlg.resize(420, 360)
    dlg.show()
    QApplication.processEvents()
    _save(dlg, "10_export_dialog.png")
    dlg.close()


# ---------- Reused install screenshots ----------

INSTALL_REUSE_MAP = {
    "image16.png": "16_install_zip.png",
    "image17.png": "17_install_extract_menu.png",
    "image19.png": "19_install_extract_button.png",
}


def copy_install_images() -> None:
    src_dir = ROOT / ".tmp_manual" / "word" / "media"
    if not src_dir.is_dir():
        print(
            "  ! .tmp_manual/word/media not found — install screenshots will be missing.\n"
            "    Re-run unzip on the original docx into .tmp_manual/."
        )
        return
    for src_name, dst_name in INSTALL_REUSE_MAP.items():
        src = src_dir / src_name
        if src.exists():
            shutil.copy2(src, OUT_DIR / dst_name)
            print(f"  reused {dst_name}")


# ---------- Entrypoint ----------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Patch FolderWatcher / _load_existing_files (we drive cards manually).
    main_window.FolderWatcher = FakeWatcher  # type: ignore[assignment]
    main_window.MainWindow._load_existing_files = lambda self: None  # type: ignore[assignment]

    app = QApplication.instance() or QApplication(sys.argv)

    # The offscreen platform plugin loads zero system fonts on Windows,
    # so register Japanese-capable fonts explicitly to avoid tofu boxes.
    for path in (
        r"C:\Windows\Fonts\YuGothR.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        if Path(path).exists():
            QFontDatabase.addApplicationFont(path)
    for family in ("Yu Gothic UI", "Yu Gothic", "Meiryo UI", "Meiryo", "MS Gothic"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 9))
            print(f"app font: {family}")
            break

    qss_path = ROOT / "src" / "views" / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="justicepdf_manual_") as td:
        work_dir = Path(td) / "PDFs"
        work_dir.mkdir()
        pdf_paths = _populate_work_dir(work_dir)
        print(f"work dir: {work_dir}")

        # Secondary work dir for the multi-window shot.
        work_dir2 = Path(td) / "アーカイブ_別ウィンドウ"
        work_dir2.mkdir()
        for name, title, n in SAMPLE_PDFS[:3]:
            _make_sample_pdf(work_dir2 / name, title, n)

        scene_main_idle(work_dir)
        scene_main_selection(work_dir)
        scene_main_drop_merge(work_dir)
        scene_toolbar(work_dir)
        scene_folder_selection(work_dir)
        scene_multi_window(work_dir, work_dir2)

        # Use a multi-page PDF for page-edit / zoom shots.
        proposal_pdf = work_dir / "提案書.pdf"
        scene_page_edit_grid(proposal_pdf)
        scene_zoom_view(proposal_pdf)
        scene_zoom_annotation_panel(proposal_pdf)

        scene_search_dialog()
        scene_export_dialog()

        copy_install_images()

    print("\nDone. Outputs:")
    for p in sorted(OUT_DIR.glob("*.png")):
        print(f"  {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
