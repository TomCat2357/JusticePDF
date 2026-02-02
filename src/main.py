"""JusticePDF application entry point."""
import sys
import argparse
import logging
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
    args = parser.parse_args()

    # Configure logging
    log_level = getattr(logging, args.log_level)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s',
        datefmt='%H:%M:%S'
    )

    app = QApplication(sys.argv)
    app.setApplicationName("JusticePDF")
    app.setApplicationDisplayName("JusticePDF")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
