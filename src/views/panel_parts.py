"""右側パネル（注釈・しおり・墨消し・OCR）で共通に使う部品。

Justice Desk の右パネルに合わせた、見出し（パネル名 + 閉じる）・節の見出し・
アイコンだけのツールボタン・折り返す並び（FlowLayout）をここにまとめる。
見た目は ``styles/panels.qss`` の objectName / property で決める。
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons

TOOL_ICON_SIZE = 16
_ICON_NORMAL = "#334155"
_ICON_ON = "#ffffff"


class PanelHeader(QFrame):
    """太字のパネル名 + 右端の × （閉じる）。"""

    close_requested = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)
        self._title = QLabel(title)
        self._title.setObjectName("panelTitle")
        layout.addWidget(self._title, 1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("panelClose")
        self.close_button.setIcon(icons.icon("X", "#475569", size=15))
        self.close_button.setIconSize(QSize(15, 15))
        self.close_button.setToolTip("閉じる")
        self.close_button.setAutoRaise(True)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.close_button)

    def title(self) -> str:
        return self._title.text()


def make_panel_shell(panel: QWidget, title: str, on_close) -> QWidget:
    """``panel`` の中に「見出し + 本体」を作り、本体（余白 10px の入れ物）を返す。

    呼び出し側は返ってきた本体に QVBoxLayout を付けて中身を並べる。
    ``on_close`` は見出しの × が押されたときに呼ぶ。
    """
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    header = PanelHeader(title)
    header.close_requested.connect(on_close)
    outer.addWidget(header)
    body = QWidget()
    body.setObjectName("panelBody")
    outer.addWidget(body, 1)
    panel.panel_header = header  # type: ignore[attr-defined]
    return body


def mark_plain_containers(root: QWidget) -> None:
    """パネルの中の入れ物（素の QWidget）を透明にして、白い地を見せる。

    アプリ全体の QSS が QWidget すべてに薄灰の地を付けるため、入れ物ごとに印を付けて
    ``panels.qss`` の ``QWidget[panelPlain="true"]`` で透明にする。パネルを組み終えた後に呼ぶ。
    """
    for w in [root, *root.findChildren(QWidget)]:
        if type(w) is QWidget:
            # 一覧（ツリー・リスト）のビューポートは一覧自身の地（白）を見せる。
            parent = w.parentWidget()
            if isinstance(parent, QAbstractItemView) and parent.viewport() is w:
                continue
            w.setProperty("panelPlain", True)


def section_label(text: str) -> QLabel:
    """節の見出し（太字 + 上罫線）。"""
    label = QLabel(text)
    label.setObjectName("panelSection")
    return label


def icon_label(name: str, tooltip: str, color: str = "#475569") -> QLabel:
    """フォーム行の頭に置く小さなアイコン（ツールチップに名前）。"""
    label = QLabel()
    label.setPixmap(icons.pixmap(name, color, 16))
    label.setFixedWidth(20)
    label.setToolTip(tooltip)
    label.setAccessibleName(tooltip)
    label.setObjectName("panelIconLabel")
    return label


def tool_icon(name: str) -> QIcon:
    """通常は濃い灰色、押下中（チェック ON）は白のアイコン。"""
    ic = QIcon()
    size = QSize(TOOL_ICON_SIZE, TOOL_ICON_SIZE)
    normal = icons.icon(name, _ICON_NORMAL, size=TOOL_ICON_SIZE)
    on = icons.icon(name, _ICON_ON, size=TOOL_ICON_SIZE)
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected, QIcon.Mode.Disabled):
        ic.addPixmap(normal.pixmap(size, mode), mode, QIcon.State.Off)
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
        ic.addPixmap(on.pixmap(size), mode, QIcon.State.On)
    ic.addPixmap(normal.pixmap(size, QIcon.Mode.Disabled), QIcon.Mode.Disabled, QIcon.State.On)
    return ic


def tool_tip_text(label: str, desc: str | None = None) -> str:
    """ツールチップは「名称」+ 改行 + 短い説明。"""
    return f"{label}\n{desc}" if desc else label


def make_tool_button(
    icon_name: str,
    label: str,
    desc: str | None = None,
    *,
    checkable: bool = False,
    parent: QWidget | None = None,
) -> QToolButton:
    """32×28 のアイコンだけのボタン。checkable なら押下中は青地・白アイコン。"""
    btn = QToolButton(parent)
    btn.setProperty("panelTool", True)
    btn.setIcon(tool_icon(icon_name))
    btn.setIconSize(QSize(TOOL_ICON_SIZE, TOOL_ICON_SIZE))
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn.setToolTip(tool_tip_text(label, desc))
    btn.setAccessibleName(label)
    btn.setCheckable(checkable)
    btn.setFixedSize(32, 28)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return btn


def make_small_button(
    icon_name: str | None,
    text: str = "",
    tooltip: str | None = None,
    *,
    parent: QWidget | None = None,
) -> QToolButton:
    """アイコン + 短い文字（text が空ならアイコンだけ）の小さなボタン。"""
    btn = QToolButton(parent)
    btn.setProperty("panelSmall", True)
    if icon_name:
        btn.setIcon(icons.icon(icon_name, _ICON_NORMAL, size=15))
        btn.setIconSize(QSize(15, 15))
    if text:
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    else:
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setFixedWidth(28)
    btn.setToolTip(tooltip or text)
    if not text:
        btn.setAccessibleName(tooltip or "")
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return btn


def make_action_button(
    icon_name: str,
    text: str,
    tooltip: str | None = None,
    *,
    kind: str = "default",
    parent: QWidget | None = None,
) -> QToolButton:
    """アイコン + 文字の操作ボタン。kind="primary-green" で緑地・白文字。"""
    btn = QToolButton(parent)
    btn.setProperty("panelAction", kind)
    color = "#ffffff" if kind == "primary-green" else _ICON_NORMAL
    btn.setIcon(icons.icon(icon_name, color, size=15))
    btn.setIconSize(QSize(15, 15))
    btn.setText(text)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return btn


def make_separator() -> QFrame:
    """ツールのグループの間に入れる縦の区切り線。"""
    sep = QFrame()
    sep.setObjectName("panelToolSep")
    sep.setFixedSize(1, 20)
    return sep


class FlowLayout(QLayout):
    """幅に収まらなくなったら次の行へ折り返す並び（Qt の定番の FlowLayout）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        h_spacing: int = 4,
        v_spacing: int = 4,
        min_width: int = 240,
    ) -> None:
        super().__init__(parent)
        self._min_width = min_width
        self._items: list[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        # 最小幅を最大の 1 個にすると、親の最小の高さが「1 行に 1 個」の高さになって
        # しまうため、パネルの幅に近い値を最小幅にする。
        size.setWidth(max(size.width(), self._min_width))
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_h > 0:
                x = area.x()
                y += line_h + self._v
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


def flow_widget(*widgets: QWidget, h_spacing: int = 4, v_spacing: int = 4) -> QWidget:
    """ウィジェットを折り返して並べる入れ物。"""
    host = QWidget()
    layout = FlowLayout(host, h_spacing, v_spacing)
    for w in widgets:
        layout.addWidget(w)
    host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return host
