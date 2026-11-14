"""個別画面の検索バー（ツールバー直下の帯）。

Justice Desk の検索バーと同じ並び: ``Search`` アイコン + 入力欄 + ``ChevronUp`` 前へ +
``ChevronDown`` 次へ + 中止（検索中のみ）+ 状況の文字 + ``X``。ページ一覧・拡大ビュー共通。
検索の処理そのものは持たず、シグナルで持ち主（PageEditWindow）へ知らせる。
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QWidget,
)

from . import icons
from .ops import OPS, tooltip_text


class _SearchEdit(QLineEdit):
    """Enter=次へ / Shift+Enter=前へ / Esc=閉じる を受け取る入力欄。"""

    shift_enter = pyqtSignal()
    escape = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.shift_enter.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SearchBar(QWidget):
    """PDF 内テキスト検索の帯。

    Emits search_requested(query) when the query is new, next_requested / prev_requested
    for navigation (Enter on an unchanged query = next), cancel_requested and closed.
    """

    search_requested = pyqtSignal(str)
    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._last_query: str | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(6)

        icon_label = QLabel(self)
        icon_label.setPixmap(icons.pixmap("Search", "#475569", 15))
        icon_label.setFixedSize(15, 15)
        lay.addWidget(icon_label)

        self._input = _SearchEdit(self)
        self._input.setPlaceholderText("検索するテキスト")
        self._input.setMaximumWidth(256)
        self._input.setMinimumWidth(120)
        self._input.returnPressed.connect(self._on_return)
        self._input.shift_enter.connect(self._on_prev)
        self._input.escape.connect(self.close_bar)
        lay.addWidget(self._input, 1)

        self._prev_btn = self._nav_button("searchPrev", self._on_prev)
        self._next_btn = self._nav_button("searchNext", self._on_next)
        lay.addWidget(self._prev_btn)
        lay.addWidget(self._next_btn)

        self._cancel_btn = QToolButton(self)
        self._cancel_btn.setText("中止")
        self._cancel_btn.setToolTip("検索を中止")
        self._cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel_btn.setProperty("variant", "box")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._cancel_btn.setVisible(False)
        lay.addWidget(self._cancel_btn)

        # 状況の文字。文言が変わっても帯の高さ・位置が揺れないよう、最も長い表示の幅を確保する。
        self._status_label = QLabel("", self)
        self._status_label.setObjectName("searchStatus")
        longest = "検索中… 99,999 / 99,999 ページ（ヒット 9,999 ページ）"
        longest_jump = "ページ 9,999 / 9,999 ・検索中… 99,999 / 99,999 ページ"
        metrics = self._status_label.fontMetrics()
        self._status_label.setMinimumWidth(
            max(metrics.horizontalAdvance(longest), metrics.horizontalAdvance(longest_jump))
        )
        lay.addWidget(self._status_label)
        lay.addStretch(1)

        close_btn = QToolButton(self)
        close_btn.setIcon(icons.icon("X", size=15))
        close_btn.setIconSize(QSize(15, 15))
        close_btn.setProperty("variant", "flat")
        close_btn.setAutoRaise(True)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setToolTip(tooltip_text(OPS["searchClose"].label, OPS["searchClose"].keys))
        close_btn.clicked.connect(self.close_bar)
        lay.addWidget(close_btn)

        self._set_nav_enabled(False)

    # ---- 部品 --------------------------------------------------------
    def _nav_button(self, op_id: str, slot) -> QToolButton:
        op = OPS[op_id]
        b = QToolButton(self)
        b.setIcon(icons.icon(op.icon, size=15))
        b.setIconSize(QSize(15, 15))
        b.setProperty("variant", "flat")
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        extra = " / Shift+F3" if op_id == "searchPrev" else " / F3"
        b.setToolTip(f"{op.label} ({op.keys}{extra})")
        b.clicked.connect(lambda _=False: slot())
        return b

    def _set_nav_enabled(self, enabled: bool) -> None:
        self._prev_btn.setEnabled(enabled)
        self._next_btn.setEnabled(enabled)

    # ---- 入力 --------------------------------------------------------
    def _on_return(self) -> None:
        # 語が変わっていれば検索、同じなら次のヒットへ（先頭へ戻さない）。
        query = self._input.text().strip()
        if query and query == self._last_query and self._next_btn.isEnabled():
            self.next_requested.emit()
            return
        self._emit_search()

    def _on_next(self) -> None:
        self.next_requested.emit()

    def _on_prev(self) -> None:
        self.prev_requested.emit()

    def _emit_search(self) -> None:
        text = self._input.text().strip()
        self._last_query = text or None
        self.search_requested.emit(text)

    def submit(self) -> None:
        """入力欄の語で検索を始める（右クリックの「文書内で検索」などから）。"""
        self._emit_search()

    def close_bar(self) -> None:
        self.hide()
        self.closed.emit()

    # ---- 表示 --------------------------------------------------------
    def set_progress(self, scanned: int, total: int, hit_pages: int, current: int = 0) -> None:
        """検索の途中経過を表示する(見つかった分だけ前へ/次へを使える)。

        *hit_pages* はヒットしたページ数、*current* は
        現在のヒットが何ページ目か(1 始まり。まだ移動していなければ 0)。
        """
        scan = f"検索中… {scanned:,} / {total:,} ページ"
        if current > 0 and hit_pages > 0:
            text = f"ページ {current:,} / {hit_pages:,} ・{scan}"
        else:
            text = f"{scan}（ヒット {hit_pages:,} ページ）"
        self._status_label.setText(text)
        self._set_nav_enabled(hit_pages > 0)
        self._cancel_btn.setVisible(True)

    def set_status(self, current: int, hit_pages: int) -> None:
        """結果の表示を更新し、前へ/次への有効・無効を切り替える。"""
        self._cancel_btn.setVisible(False)
        if hit_pages <= 0:
            self._status_label.setText("見つかりません")
            self._set_nav_enabled(False)
        else:
            if current > 0:
                text = f"ページ {current:,} / {hit_pages:,}"
            else:
                text = f"{hit_pages:,} ページ"
            self._status_label.setText(text)
            self._set_nav_enabled(True)

    def clear_status(self) -> None:
        self._cancel_btn.setVisible(False)
        self._status_label.setText("")
        self._set_nav_enabled(False)
        self._last_query = None

    def text(self) -> str:
        return self._input.text()

    def set_text(self, text: str) -> None:
        self._input.setText(text)

    def has_input_focus(self) -> bool:
        return self._input.hasFocus()

    def focus_input(self) -> None:
        self._input.setFocus()
        self._input.selectAll()
