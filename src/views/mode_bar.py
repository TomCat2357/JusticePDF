"""モード帯（Justice Desk のペイン上端の帯）。

背景はモード色、文字は白。左から モード名チップ / 戻る・進む・上へ / パンくず /
右端に再読み込み。
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                             QToolButton, QWidget)

from . import icons, theme
from .ops import OPS, tooltip_text

BAR_HEIGHT = 26
_WHITE = "#ffffff"

# モード -> (チップの文字, アイコン)
MODES = {
    "folder": ("フォルダ", "FolderOpen"),
    "pages": ("ページ一覧", "LayoutGrid"),
    "zoom": ("ページ表示（拡大）", "LayoutGrid"),
}
ELLIPSIS = "…"


class _CrumbLabel(QLabel):
    """クリックできる区間。ホバーで下線。"""

    clicked = pyqtSignal()

    def __init__(self, text: str, bold: bool = False, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        f = self.font()
        f.setPixelSize(12)
        f.setBold(bold)
        self.setFont(f)

    def _underline(self, on: bool) -> None:
        f = self.font()
        f.setUnderline(on)
        self.setFont(f)

    def enterEvent(self, e):
        self._underline(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._underline(False)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _Host(QWidget):
    resized = pyqtSignal()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class ModeBar(QWidget):
    back_requested = pyqtSignal()
    forward_requested = pyqtSignal()
    up_requested = pyqtSignal()
    crumb_requested = pyqtSignal(object)
    reload_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modeBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(BAR_HEIGHT)
        self._mode = "folder"
        self._crumbs: list[tuple[str, object, str | None]] = []
        self._rendered_key = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)

        # モード名チップ
        self._chip = QFrame(self)
        self._chip.setObjectName("modeChip")
        chip_lay = QHBoxLayout(self._chip)
        chip_lay.setContentsMargins(6, 1, 6, 1)
        chip_lay.setSpacing(4)
        self._chip_icon = QLabel(self._chip)
        self._chip_icon.setFixedSize(12, 12)
        self._chip_text = QLabel(self._chip)
        self._chip_text.setObjectName("modeChipText")
        chip_lay.addWidget(self._chip_icon)
        chip_lay.addWidget(self._chip_text)
        lay.addWidget(self._chip)

        # 戻る・進む・上へ
        self._back = self._nav_button("back", "ArrowLeft", self.back_requested)
        self._forward = self._nav_button("forward", "ArrowRight", self.forward_requested)
        self._up = self._nav_button("up", "ArrowUp", self.up_requested)
        for b in (self._back, self._forward, self._up):
            lay.addWidget(b)

        # 場所の表示（パンくず）
        self._host = _Host(self)
        self._host.setMinimumWidth(0)
        self._host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._crumb_lay = QHBoxLayout(self._host)
        self._crumb_lay.setContentsMargins(0, 0, 0, 0)
        self._crumb_lay.setSpacing(2)
        self._host.resized.connect(self._render_crumbs)
        lay.addWidget(self._host, 1)

        # 再読み込み
        self._reload = self._nav_button("reload", "RefreshCw", self.reload_requested, px=14)
        lay.addWidget(self._reload)

        self._apply_style()
        self.set_mode("folder")
        self.set_nav_enabled(False, False, False)

    # ---- 部品 --------------------------------------------------------
    def _nav_button(self, op_id: str, icon_name: str, signal, px: int = 15) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("modeBarBtn")
        b.setIcon(icons.icon(icon_name, _WHITE, size=px))
        b.setIconSize(QSize(px, px))
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        op = OPS[op_id]
        b.setToolTip(tooltip_text(op.label, op.keys))
        b.clicked.connect(lambda _=False: signal.emit())
        return b

    def _apply_style(self) -> None:
        bg = theme.mode_color(self._mode)
        self.setStyleSheet(f"""
            QWidget#modeBar {{ background-color: {bg}; }}
            QWidget#modeBar QLabel {{ background: transparent; color: {_WHITE}; }}
            QFrame#modeChip {{ background-color: rgba(255,255,255,0.22); border-radius: 4px; }}
            QFrame#modeChip QLabel {{ background: transparent; }}
            QLabel#modeChipText {{ font-size: 11px; font-weight: bold; }}
            QToolButton#modeBarBtn {{ background: transparent; border: none; border-radius: 4px;
                                     padding: 2px; min-height: 0; }}
            QToolButton#modeBarBtn:hover {{ background-color: rgba(255,255,255,0.25); }}
            QToolButton#modeBarBtn:disabled {{ background: transparent; }}
        """)

    # ---- API ---------------------------------------------------------
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise KeyError(f"未知のモード: {mode!r}")
        self._mode = mode
        text, icon_name = MODES[mode]
        self._chip_text.setText(text)
        self._chip_icon.setPixmap(icons.pixmap(icon_name, _WHITE, 12))
        self._up.setVisible(mode == "folder")
        self._apply_style()

    def set_crumbs(self, crumbs) -> None:
        """crumbs = [(表示名, payload), ...]。区間をクリックすると crumb_requested(payload)。"""
        self._crumbs = [(str(c[0]), c[1], c[2] if len(c) > 2 else None) for c in crumbs]
        self._rendered_key = None
        self._render_crumbs()

    def set_nav_enabled(self, back: bool, forward: bool, up: bool) -> None:
        self._back.setEnabled(bool(back))
        self._forward.setEnabled(bool(forward))
        self._up.setEnabled(bool(up))

    def set_back_tooltip(self, text: str | None) -> None:
        self._set_nav_tooltip(self._back, "back", text)

    def set_forward_tooltip(self, text: str | None) -> None:
        self._set_nav_tooltip(self._forward, "forward", text)

    def set_up_tooltip(self, text: str | None) -> None:
        self._set_nav_tooltip(self._up, "up", text)

    @staticmethod
    def _set_nav_tooltip(btn: QToolButton, op_id: str, text: str | None) -> None:
        op = OPS[op_id]
        label = f"{op.label}: {text}" if text else op.label
        btn.setToolTip(tooltip_text(label, op.keys))

    def crumb_texts(self) -> list[str]:
        """いま表示している区間の文字（折り畳み「…」を含む）。確認用。"""
        out = []
        for i in range(self._crumb_lay.count()):
            w = self._crumb_lay.itemAt(i).widget()
            if isinstance(w, QLabel) and w.property("crumb"):
                out.append(w.text())
        return out

    # ---- パンくずの描画 ----------------------------------------------
    def _visible_indexes(self, avail: int) -> tuple[list[int], list[int]]:
        """(表示する区間の index, 畳む区間の index) を返す。先頭と末尾は残す。"""
        n = len(self._crumbs)
        base = QFont(self.font())
        base.setPixelSize(12)
        fm = QFontMetrics(base)
        bold = QFont(base)
        bold.setBold(True)
        fmb = QFontMetrics(bold)
        sep = 13 + 2 * self._crumb_lay.spacing()
        widths = [fm.horizontalAdvance(c[0]) + 4 for c in self._crumbs]
        if n:
            widths[-1] = fmb.horizontalAdvance(self._crumbs[-1][0]) + 4
            if self._crumbs[0][2]:
                widths[0] += 18  # 先頭の区間のアイコン
        total = sum(widths) + sep * (n - 1)
        if n <= 2 or total <= avail:
            return list(range(n)), []
        ell = fm.horizontalAdvance(ELLIPSIS) + 4 + sep
        used = widths[0] + sep + ell + widths[-1]
        keep_tail: list[int] = []
        for i in range(n - 2, 0, -1):
            need = widths[i] + sep
            if used + need > avail:
                break
            used += need
            keep_tail.append(i)
        keep_tail.reverse()
        shown = [0] + keep_tail + [n - 1]
        hidden = [i for i in range(1, n - 1) if i not in keep_tail]
        return shown, hidden

    def _render_crumbs(self) -> None:
        avail = self._host.width()
        shown, hidden = self._visible_indexes(avail if avail > 0 else 10**6)
        key = (tuple(shown), tuple(hidden), tuple(self._crumbs_labels()))
        if key == self._rendered_key:
            return
        self._rendered_key = key
        while self._crumb_lay.count():
            item = self._crumb_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        last = len(self._crumbs) - 1
        for pos, i in enumerate(shown):
            if pos > 0:
                self._add_separator()
            if hidden and pos == 1:
                self._add_ellipsis(hidden)
                self._add_separator()
            label, payload, icon_name = self._crumbs[i]
            if icon_name:
                ic = QLabel(self._host)
                ic.setPixmap(icons.pixmap(icon_name, _WHITE, 14))
                ic.setFixedSize(14, 14)
                self._crumb_lay.addWidget(ic)
            w = _CrumbLabel(label, bold=(i == last), parent=self._host)
            w.setProperty("crumb", True)
            w.clicked.connect(lambda p=payload: self.crumb_requested.emit(p))
            self._crumb_lay.addWidget(w)
        self._crumb_lay.addStretch(1)

    def _crumbs_labels(self):
        return [c[0] for c in self._crumbs]

    def _add_separator(self) -> None:
        s = QLabel(self._host)
        s.setPixmap(icons.pixmap("ChevronRight", _WHITE, 13))
        s.setFixedSize(13, 13)
        self._crumb_lay.addWidget(s)

    def _add_ellipsis(self, hidden: list[int]) -> None:
        e = QLabel(ELLIPSIS, self._host)
        e.setProperty("crumb", True)
        e.setToolTip(" / ".join(self._crumbs[i][0] for i in hidden))
        self._crumb_lay.addWidget(e)
