"""lucide / 自前 SVG のアイコンを QIcon・QPixmap にして返す（Justice Desk と同じ絵柄）。

SVG は ``src/resources/icons/lucide/``（dev/gen_lucide_icons.js で書き出し）と
``src/resources/icons/custom/``（自前 5 種）に置く。``currentColor`` を指定色へ
置き換えて QSvgRenderer で描く。描画は呼ばれたときに行うので、QApplication が
無い状態で import しても落ちない。
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QByteArray, QPoint, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from src.views.theme import COLORS

ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
_SEARCH_DIRS = (ICONS_DIR / "lucide", ICONS_DIR / "custom")

# 色の指定: ``"#rrggbb"`` などの固定色、または ``"@text"`` のようにテーマの色トークン名を
# ``@`` で始めて書く（QIcon は描画のたびにその時のテーマの色で描くので、テーマを切り替えても
# 作り直さずに追従する）。省略（None）は ``"@text"``（標準テーマでは slate-700 = #334155）。
DEFAULT_COLOR = "@text"
CHEVRON_SIZE = 12
CHEVRON_GAP = 2

_svg_cache: dict[str, str] = {}
_icon_cache: dict[tuple, QIcon] = {}


def _kebab(name: str) -> str:
    """``RotateCw`` -> ``rotate-cw``、``Trash2`` -> ``trash-2``（kebab のまま渡してもよい）。"""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    s = re.sub(r"([A-Za-z])([0-9])", r"\1-\2", s)
    return s.lower()


def _svg_source(name: str) -> str:
    key = _kebab(name)
    cached = _svg_cache.get(key)
    if cached is not None:
        return cached
    for d in _SEARCH_DIRS:
        path = d / f"{key}.svg"
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            _svg_cache[key] = text
            return text
    raise KeyError(f"アイコンが見つかりません: {name!r}")


def _color_hex(color) -> str:
    """固定色・トークン名（``@名前``）を ``#rrggbb`` へ。トークンはいまのテーマの値。"""
    if color is None:
        color = DEFAULT_COLOR
    if isinstance(color, str) and color.startswith("@"):
        color = COLORS[color[1:]]
    c = QColor(color)
    if not c.isValid():
        raise ValueError(f"色を解釈できません: {color!r}")
    return c.name(QColor.NameFormat.HexRgb)


def _default_dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _render_parts(parts, width: int, height: int, dpr: float, color: str,
                  opacity: float = 1.0) -> QPixmap:
    """parts = [(name, x, size), ...] を横に並べて 1 枚の透明 QPixmap に描く。"""
    pm = QPixmap(round(width * dpr), round(height * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(opacity)
    for name, x, size in parts:
        svg = _svg_source(name).replace("currentColor", color)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        y = (height - size) / 2
        renderer.render(painter, QRectF(x * dpr, y * dpr, size * dpr, size * dpr))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


_renderer_cache: dict[tuple[str, str], QSvgRenderer] = {}


def _renderer(name: str, color: str) -> QSvgRenderer:
    key = (_kebab(name), color)
    r = _renderer_cache.get(key)
    if r is None:
        svg = _svg_source(name).replace("currentColor", color)
        r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        _renderer_cache[key] = r
    return r


class _ThemedIconEngine(QIconEngine):
    """色をテーマのトークンで持ち、描かれるたびにその時の色で SVG を描く QIcon の中身。"""

    def __init__(self, parts, width: int, height: int, color, disabled_opacity: float,
                 on_color=None) -> None:
        super().__init__()
        self._on_color = on_color  # State.On（チェック ON）のときだけ使う色
        self._parts = parts
        self._w = width
        self._h = height
        self._color = color
        self._disabled_opacity = disabled_opacity

    def paint(self, painter, rect, mode, state) -> None:  # noqa: D102 - Qt override
        use_on = self._on_color is not None and state == QIcon.State.On
        color = _color_hex(self._on_color if use_on else self._color)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(painter.opacity() * self._disabled_opacity)
        sx = rect.width() / self._w
        sy = rect.height() / self._h
        for name, x, size in self._parts:
            y = (self._h - size) / 2
            _renderer(name, color).render(
                painter,
                QRectF(rect.x() + x * sx, rect.y() + y * sy, size * sx, size * sy),
            )
        painter.restore()

    def actualSize(self, size, mode, state) -> QSize:  # noqa: D102 - Qt override
        return QSize(self._w, self._h).scaled(size, Qt.AspectRatioMode.KeepAspectRatio)

    def pixmap(self, size, mode, state) -> QPixmap:  # noqa: D102 - Qt override
        actual = self.actualSize(size, mode, state)
        pm = QPixmap(actual)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        self.paint(painter, QRect(QPoint(0, 0), actual), mode, state)
        painter.end()
        return pm

    def clone(self) -> "_ThemedIconEngine":  # noqa: D102 - Qt override
        return _ThemedIconEngine(self._parts, self._w, self._h, self._color,
                                 self._disabled_opacity, self._on_color)


def _build_icon(parts, width: int, height: int, color, disabled_opacity: float,
                on_color=None) -> QIcon:
    return QIcon(_ThemedIconEngine(parts, width, height, color, disabled_opacity, on_color))


def icon(name: str, color=None, *, size: int = 17, disabled_opacity: float = 0.4,
         on_color=None) -> QIcon:
    """アイコン名（PascalCase / kebab-case）から QIcon を作る。無い名前は KeyError。

    ``on_color`` を渡すと、チェック ON（QIcon.State.On）のときだけその色で描く。
    """
    _svg_source(name)  # 早めに KeyError
    _color_hex(color)  # 色の指定の誤りも早めに
    if on_color is not None:
        _color_hex(on_color)
    key = ("icon", _kebab(name), color, size, disabled_opacity, on_color)
    ic = _icon_cache.get(key)
    if ic is None:
        ic = _build_icon([(name, 0, size)], size, size, color, disabled_opacity, on_color)
        _icon_cache[key] = ic
    return ic


def pixmap(name: str, color=None, size: int = 17, dpr: float | None = None) -> QPixmap:
    """QLabel に貼る用の QPixmap。dpr 省略時は画面の倍率。"""
    _svg_source(name)
    return _render_parts([(name, 0, size)], size, size,
                         dpr if dpr is not None else _default_dpr(), _color_hex(color))


def chevron_icon_size(size: int = 17) -> tuple[int, int]:
    """icon_with_chevron の絵の大きさ（QToolButton.setIconSize に渡す）。"""
    return (size + CHEVRON_GAP + CHEVRON_SIZE, size)


def icon_with_chevron(name: str, color=None, size: int = 17) -> QIcon:
    """アイコンの右に小さな ChevronDown を並べた横長アイコン（ドロップダウンボタン用）。

    QToolButton 側は ``setIconSize(QSize(*chevron_icon_size(size)))`` と、
    メニュー記号を QSS で隠す（QToolButton::menu-indicator）前提。
    """
    _svg_source(name)
    _color_hex(color)
    key = ("chevron", _kebab(name), color, size)
    ic = _icon_cache.get(key)
    if ic is None:
        w, h = chevron_icon_size(size)
        parts = [(name, 0, size), ("ChevronDown", size + CHEVRON_GAP, CHEVRON_SIZE)]
        ic = _build_icon(parts, w, h, color, 0.4)
        _icon_cache[key] = ic
    return ic
