"""JusticePDF application entry point."""
import sys
import argparse
import logging
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication
from src.views.main_window import MainWindow


def main():
    """Application entry point."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="JusticePDF - PDF management application")
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )
    parser.add_argument(
        'paths',
        nargs='*',
        help='開くファイル/フォルダ（Explorer の右クリック「JusticePDFで開く」から渡される）',
    )
    args = parser.parse_args()

    # Configure logging
    log_level = getattr(logging, args.log_level)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s',
        datefmt='%H:%M:%S'
    )

    app = QApplication(sys.argv)
    app.setOrganizationName("JusticePDF")
    app.setApplicationName("JusticePDF")
    app.setApplicationDisplayName("JusticePDF")

    qss_path = Path(__file__).parent / "views" / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # Explorer の右クリックから渡されたパスを振り分ける。
    #   - 単一フォルダのみ -> そのフォルダを作業フォルダとして開く
    #   - ファイルを含む    -> 既定の作業フォルダで起動し、起動後に取り込む
    arg_paths = [Path(p) for p in args.paths]
    dirs = [p for p in arg_paths if p.is_dir()]
    files = [p for p in arg_paths if p.is_file()]

    if len(arg_paths) == 1 and dirs:
        window = MainWindow(folder_path=str(dirs[0]))
    else:
        window = MainWindow()
        if files:
            file_strs = [str(p) for p in files]
            # イベントループ開始後に取り込む（ImportWorker はバックグラウンド
            # スレッド＋進捗ダイアログを使うため、show 後に遅延実行する）。
            QTimer.singleShot(0, lambda: window.import_external_paths(file_strs))
    window.show()

    flags = window.windowFlags()
    window.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
    window.show()
    window.setWindowFlags(flags)
    window.show()
    window.raise_()
    window.activateWindow()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
