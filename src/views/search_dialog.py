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

        layout.addLayout(nav_row)

        # 状態表示は専用の行に置き、文言が変わってもダイアログの大きさが変わらないよう
        # 最も長い表示が収まる最小幅を確保する。
        self._status_label = QLabel("")
        longest = "ページ 9,999 / 9,999（全 99,999 件）・検索中… 99,999 / 99,999 ページ"
        self._status_label.setMinimumWidth(
            self._status_label.fontMetrics().horizontalAdvance(longest)
        )
        self._status_label.setMinimumHeight(self._status_label.fontMetrics().height() + 2)
        layout.addWidget(self._status_label)

    def _emit_search(self) -> None:
        text = self._input.text().strip()
        self.search_requested.emit(text)

    def set_progress(
        self, scanned: int, total: int, hit_pages: int, occurrences: int, current: int = 0
    ) -> None:
        """検索の途中経過を表示する(見つかった分だけ前へ/次へを使える)。

        *hit_pages* はヒットしたページ数、*occurrences* は全ヒット件数、*current* は
        現在のヒットが何ページ目か(1 始まり。まだ移動していなければ 0)。
        """
        scan = f"検索中… {scanned:,} / {total:,} ページ"
        if current > 0 and hit_pages > 0:
            text = f"ページ {current:,} / {hit_pages:,}（全 {occurrences:,} 件）・{scan}"
        else:
            text = f"{scan}（{occurrences:,} 件）"
        self._status_label.setText(text)
        self._prev_btn.setEnabled(hit_pages > 0)
        self._next_btn.setEnabled(hit_pages > 0)
        self._cancel_btn.setVisible(True)

    def set_status(self, current: int, hit_pages: int, occurrences: int) -> None:
        """結果の表示を更新し、前へ/次への有効・無効を切り替える。"""
        self._cancel_btn.setVisible(False)
        if hit_pages <= 0:
            self._status_label.setText("見つかりません")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
        else:
            if current > 0:
                text = f"ページ {current:,} / {hit_pages:,}（全 {occurrences:,} 件）"
            else:
                text = f"{hit_pages:,} ページ（全 {occurrences:,} 件）"
            self._status_label.setText(text)
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
