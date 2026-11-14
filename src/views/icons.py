"""lucide / 自前 SVG のアイコンを QIcon・QPixmap にして返す（Justice Desk と同じ絵柄）。

SVG は ``src/resources/icons/lucide/``（dev/gen_lucide_icons.js で書き出し）と
``src/resources/icons/custom/``（自前 5 種）に置く。``currentColor`` を指定色へ
置き換えて QSvgRenderer で描く。描画は呼ばれたときに行うので、QApplication が
無い状態で import しても落ちない。
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
_SEARCH_DIRS = (ICONS_DIR / "lucide", ICONS_DIR / "custom")

DEFAULT_COLOR = "#334155"  # slate-700（theme.TEXT_COLOR と同じ）
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
    c = QColor(color if color is not None else DEFAULT_COLOR)
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


def _build_icon(parts, width: int, height: int, color: str, disabled_opacity: float) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(_render_parts(parts, width, height, dpr, color), QIcon.Mode.Normal)
        ic.addPixmap(_render_parts(parts, width, height, dpr, color, disabled_opacity),
                     QIcon.Mode.Disabled)
    return ic


def icon(name: str, color=None, *, size: int = 17, disabled_opacity: float = 0.4) -> QIcon:
    """アイコン名（PascalCase / kebab-case）から QIcon を作る。無い名前は KeyError。"""
    _svg_source(name)  # 早めに KeyError
    hex_color = _color_hex(color)
    key = ("icon", _kebab(name), hex_color, size, disabled_opacity)
    ic = _icon_cache.get(key)
    if ic is None:
        ic = _build_icon([(name, 0, size)], size, size, hex_color, disabled_opacity)
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
    hex_color = _color_hex(color)
    key = ("chevron", _kebab(name), hex_color, size)
    ic = _icon_cache.get(key)
    if ic is None:
        w, h = chevron_icon_size(size)
        parts = [(name, 0, size), ("ChevronDown", size + CHEVRON_GAP, CHEVRON_SIZE)]
        ic = _build_icon(parts, w, h, hex_color, 0.4)
        _icon_cache[key] = ic
    return ic
