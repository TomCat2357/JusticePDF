"""アプリバー（メイン窓の上端）。Justice Desk のヘッダーに合わせた青い帯。"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from src.views import icons
from src.views.ops import OPS
from src.views.theme import COLORS

APP_TITLE = "JusticePDF"


class AppBar(QWidget):
    settings_requested = pyqtSignal()
    help_requested = pyqtSignal()
    about_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 12, 6)
        lay.setSpacing(2)

        self._title = QLabel(APP_TITLE, self)
        self._title.setObjectName("appBarTitle")
        lay.addWidget(self._title)
        lay.addStretch(1)

        self.settings_btn = self._button("settings", self.settings_requested)
        self.help_btn = self._button("help", self.help_requested)
        self.about_btn = self._button("about", self.about_requested)
        for b in (self.settings_btn, self.help_btn, self.about_btn):
            lay.addWidget(b)

        fg = COLORS["app-bar-text"]
        self.setStyleSheet(f"""
            QWidget#appBar {{ background-color: {COLORS['app-bar']}; }}
            QLabel#appBarTitle {{ background: transparent; color: {fg};
                                 font-size: 16px; font-weight: bold; }}
            QToolButton#appBarBtn {{ background: transparent; color: {fg}; border: none;
                                    border-radius: 4px; padding: 2px 8px; font-size: 12px;
                                    min-height: 0; }}
            QToolButton#appBarBtn:hover {{ background-color: {COLORS['app-bar-hover']}; }}
        """)

    def _button(self, op_id: str, signal) -> QToolButton:
        op = OPS[op_id]
        b = QToolButton(self)
        b.setObjectName("appBarBtn")
        b.setText(op.label)
        b.setIcon(icons.icon(op.icon, COLORS["app-bar-text"], size=14))
        b.setIconSize(QSize(14, 14))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setToolTip(op.label)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setAutoRaise(True)
        b.clicked.connect(lambda _=False: signal.emit())
        return b
