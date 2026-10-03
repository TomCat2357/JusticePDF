"""Modeless search dialog for PDF page text search."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SearchDialog(QDialog):
    """Modeless dialog that searches text within the current PDF.

    Emits search_requested(query) on Enter / search button press, and
    next_requested / prev_requested for navigation.
    """

    search_requested = pyqtSignal(str)
    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF 内検索")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setModal(False)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("検索したい語句を入力")
        self._input.returnPressed.connect(self._emit_search)
        input_row.addWidget(self._input, 1)

        self._search_btn = QPushButton("検索")
        self._search_btn.clicked.connect(self._emit_search)
        input_row.addWidget(self._search_btn)

        layout.addLayout(input_row)

        nav_row = QHBoxLayout()
        self._prev_btn = QPushButton("◀ 前へ")
        self._prev_btn.clicked.connect(self.prev_requested.emit)
        self._prev_btn.setEnabled(False)
        nav_row.addWidget(self._prev_btn)

        self._next_btn = QPushButton("次へ ▶")
        self._next_btn.clicked.connect(self.next_requested.emit)
        self._next_btn.setEnabled(False)
        nav_row.addWidget(self._next_btn)

        self._cancel_btn = QPushButton("中止")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._cancel_btn.setVisible(False)
        nav_row.addWidget(self._cancel_btn)

        nav_row.addStretch(1)

        self._status_label = QLabel("")
        nav_row.addWidget(self._status_label)

        layout.addLayout(nav_row)

    def _emit_search(self) -> None:
        text = self._input.text().strip()
        self.search_requested.emit(text)

    def set_progress(self, scanned: int, total: int, hits: int, current: int = 0) -> None:
        """検索の途中経過を表示する(見つかった分だけ前へ/次へを使える)。"""
        text = f"検索中… {scanned:,} / {total:,} ページ（{hits:,} 件）"
        if current > 0 and hits > 0:
            text = f"{current} / {hits:,} 件 ・ 検索中… {scanned:,} / {total:,} ページ"
        self._status_label.setText(text)
        self._prev_btn.setEnabled(hits > 0)
        self._next_btn.setEnabled(hits > 0)
        self._cancel_btn.setVisible(True)

    def set_status(self, current: int, total: int) -> None:
        """Update the hit count display and enable/disable nav buttons."""
        self._cancel_btn.setVisible(False)
        if total <= 0:
            self._status_label.setText("見つかりません")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
        else:
            self._status_label.setText(f"{current} / {total} 件")
            self._prev_btn.setEnabled(True)
            self._next_btn.setEnabled(True)

    def clear_status(self) -> None:
        self._cancel_btn.setVisible(False)
        self._status_label.setText("")
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)

    def focus_input(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
