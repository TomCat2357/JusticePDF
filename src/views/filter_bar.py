"""名前で絞り込むバー（ツールバー直下）と、その検索語の解釈。

書き方: 空白区切り = かつ / ``OR`` = または / 語の前の ``-`` = 除外。
照合は NFKC 正規化 + 大文字小文字を区別しない。
"""
from __future__ import annotations

import unicodedata

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

from src.views import icons, theme

PLACEHOLDER = "名前で絞り込み  例: 契約 見積 OR 請求"


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def parse_name_filter(query: str) -> list[tuple[list[str], list[str]]]:
    """検索語を [(含める語のリスト, 除く語のリスト), ...]（OR の各組）へ。空なら []。"""
    groups: list[tuple[list[str], list[str]]] = []
    inc: list[str] = []
    exc: list[str] = []
    for tok in unicodedata.normalize("NFKC", query).split():
        if tok == "OR":
            if inc or exc:
                groups.append((inc, exc))
            inc, exc = [], []
            continue
        if tok.startswith("-"):
            if len(tok) > 1:
                exc.append(tok[1:].casefold())
            continue
        inc.append(tok.casefold())
    if inc or exc:
        groups.append((inc, exc))
    return groups


def name_matches(name: str, groups: list[tuple[list[str], list[str]]]) -> bool:
    """絞り込みなし（groups が空）なら常に True。"""
    if not groups:
        return True
    n = _norm(name)
    for inc, exc in groups:
        if all(t in n for t in inc) and not any(t in n for t in exc):
            return True
    return False


class FilterBar(QWidget):
    """Search アイコン + 入力欄 + X。入力のたびに ``text_changed`` を出す。"""

    text_changed = pyqtSignal(str)
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filterBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        icon = QLabel(self)
        theme.bind(icon, lambda w: w.setPixmap(icons.pixmap("Search", "@icon-muted", 15)))
        lay.addWidget(icon)
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(PLACEHOLDER)
        self._edit.setClearButtonEnabled(False)
        self._edit.textChanged.connect(self.text_changed)
        lay.addWidget(self._edit, 1)
        close = QToolButton(self)
        close.setIcon(icons.icon("X", size=15))
        close.setAutoRaise(True)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setToolTip("閉じる (Esc)")
        close.clicked.connect(self.close_requested)
        lay.addWidget(close)
        self.hide()

    def line_edit(self) -> QLineEdit:
        return self._edit

    def text(self) -> str:
        return self._edit.text()

    def focus_input(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._edit.selectAll()

    def clear(self) -> None:
        self._edit.clear()
