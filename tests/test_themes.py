"""表示テーマ（ONLINE 版 Client_Theme.html の色の移植）のテスト。"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QApplication, QWidget

from src.utils import app_settings
from src.views import icons, theme, themes

# 兄弟フォルダの ONLINE 版（環境変数 JUSTICEPDF_ONLINE_DIR で場所を指定してもよい）。
_ONLINE_DIR = Path(os.environ.get("JUSTICEPDF_ONLINE_DIR") or
                   Path(__file__).resolve().parents[2] / "JusticePDF-ONLINE")
ONLINE_THEME_HTML = _ONLINE_DIR / "src" / "Client_Theme.html"

# ONLINE 版の THEMES 一覧の id の並び。
EXPECTED_IDS = ["standard", "b", "d", "e", "f", "h", "j", "k", "l", "m", "o", "i", "g", "n",
                "p", "q", "r", "s"]
DARK_IDS = ["g", "n", "p", "q", "r", "s"]

# 移植前の標準テーマの COLORS（そのまま再現できること）。
STANDARD_SNAPSHOT = {
    "white": "#ffffff", "slate-50": "#f8fafc", "slate-100": "#f1f5f9", "slate-200": "#e2e8f0",
    "slate-300": "#cbd5e1", "slate-400": "#94a3b8", "slate-500": "#64748b",
    "slate-600": "#475569", "slate-700": "#334155", "slate-800": "#1e293b",
    "slate-900": "#0f172a", "blue-50": "#eff6ff", "blue-100": "#dbeafe", "blue-500": "#3b82f6",
    "blue-600": "#2563eb", "blue-700": "#1d4ed8", "blue-800": "#1e40af",
    "app-bar": "#1d4ed8", "app-bar-text": "#ffffff", "app-bar-hover": "#2563eb",
    "mode-folder": "#2563eb", "mode-pages": "#c2410c", "mode-folder-soft": "#eff6ff",
    "mode-pages-soft": "#fff7ed", "mode-preview": "#15803d", "mode-preview-soft": "#f0fdf4",
    "mode-text": "#7e22ce", "mode-text-soft": "#faf5ff", "select-bg": "#dbeafe",
    "select-ring": "#3b82f6", "dnd-ring": "#22c55e", "dnd-bg": "#dcfce7",
    "insert-line": "#2563eb", "drop-area": "#38bdf8", "drop-area-bg": "#f0f9ff",
    "folder-card-bg": "#fffbeb", "folder-card-border": "#fde68a", "folder-icon": "#f59e0b",
    "folder-text": "#b45309", "star": "#f59e0b", "badge-bg": "rgba(71, 85, 105, 0.85)",
    "badge-text": "#ffffff", "page-list-bg": "#e2e8f0", "page-card-bg": "#f8fafc",
    "hit-bg": "#fef9c3", "hit-ring": "#facc15", "hit-current-bg": "#fef08a",
    "hit-current-ring": "#c2410c", "zoom-bg": "rgba(100, 116, 139, 0.8)", "paper": "#ffffff",
    "line": "#e5e7eb", "danger": "#dc2626", "danger-soft": "#fef2f2", "danger-text": "#b91c1c",
    "danger-muted": "#fca5a5", "warn": "#b45309", "warn-soft": "#fffbeb",
    "rubber-fill": "rgba(59, 130, 246, 0.2)", "card-hover": "rgba(226, 232, 240, 0.7)",
    "mode-folder-line": "rgba(37, 99, 235, 0.35)", "mode-pages-line": "rgba(194, 65, 12, 0.35)",
}


def test_theme_list_matches_online():
    assert [t.id for t in themes.THEMES] == EXPECTED_IDS
    assert len(themes.THEMES) == 18
    assert themes.THEME_IDS == tuple(EXPECTED_IDS)
    for t in themes.THEMES:
        assert t.label and t.desc
        for color in (t.header, t.bg, t.card, t.accent):
            assert QColor(color).isValid()


@pytest.mark.skipif(not ONLINE_THEME_HTML.exists(), reason="ONLINE 版のフォルダが無い")
def test_theme_list_matches_online_source():
    src = ONLINE_THEME_HTML.read_text(encoding="utf-8")
    online = re.findall(
        r"\{ id: '(\w+)', label: '([^']*)', desc: '[^']*', swatch: \{ header: '(#\w+)', "
        r"bg: '(#\w+)', card: '(#\w+)', accent: '(#\w+)' \} \}", src)
    assert [(t.id, t.label, t.header, t.bg, t.card, t.accent) for t in themes.THEMES] == online
    assert "ALIASES = { a: 'standard', c: 'h' }" in src
    assert themes.ALIASES == {"a": "standard", "c": "h"}


def test_standard_theme_reproduces_previous_colors():
    colors = theme.build_colors("standard")
    for key, value in STANDARD_SNAPSHOT.items():
        assert colors[key] == value, key


@pytest.mark.parametrize("theme_id", EXPECTED_IDS)
def test_every_theme_builds_stylesheet_without_unknown_tokens(theme_id):
    colors = theme.build_colors(theme_id)
    qss = theme.load_app_stylesheet(colors)  # 未定義のトークンがあれば KeyError
    assert "@" not in re.sub(r"/\*.*?\*/", "", qss, flags=re.S)
    for key, value in colors.items():
        if value.startswith("rgba("):
            continue
        assert QColor(value).isValid(), (theme_id, key, value)


@pytest.mark.parametrize("theme_id", DARK_IDS)
def test_dark_themes_keep_paper_white_and_flip_text(theme_id):
    colors = theme.build_colors(theme_id)
    assert theme.is_dark_theme(theme_id)
    assert colors["paper"] == "#ffffff"
    assert QColor(colors["slate-100"]).lightness() < 80  # 地は暗い
    assert QColor(colors["white"]).lightness() < 80
    assert QColor(colors["t-slate-800"]).lightness() > 180  # 文字は明るい
    qss = theme.load_app_stylesheet(colors)
    assert f"color: {colors['t-slate-800']};" in qss  # color: の印は文字用の値になる


@pytest.mark.parametrize("theme_id", [i for i in EXPECTED_IDS if i not in DARK_IDS])
def test_light_themes_are_not_dark(theme_id):
    assert not theme.is_dark_theme(theme_id)
    assert QColor(theme.build_colors(theme_id)["slate-100"]).lightness() > 180


def test_app_bar_uses_header_base_color():
    assert theme.build_colors("b")["app-bar"] == "#1f2a44"
    assert theme.build_colors("k")["app-bar"] == "#fbe9ee"
    assert theme.build_colors("f")["app-bar"] == "#f5b210"  # グラデーションの下地の単色


def test_normalize_theme_id():
    assert theme.normalize_theme_id("g") == "g"
    assert theme.normalize_theme_id("a") == "standard"
    assert theme.normalize_theme_id("c") == "h"
    for bad in ("zzz", "", None, 5, ["g"]):
        assert theme.normalize_theme_id(bad) == "standard"


def test_settings_roundtrip_and_invalid_values():
    assert app_settings.theme_id() == "standard"
    app_settings.set_theme_id("n")
    assert app_settings.theme_id() == "n"
    app_settings.set_theme_id("nonsense")
    assert app_settings.theme_id() == "standard"
    QSettings().setValue(app_settings.THEME_KEY, "c")  # 廃止 ID は読み替え
    assert app_settings.theme_id() == "h"
    QSettings().setValue(app_settings.THEME_KEY, "???")
    assert app_settings.theme_id() == "standard"


def test_set_theme_swaps_colors_in_place():
    same_dict = theme.COLORS
    theme.set_theme("g")
    assert theme.COLORS is same_dict
    assert theme.COLORS["paper"] == "#ffffff"
    assert theme.COLORS["app-bar"] == "#090e19"
    assert theme.TEXT_COLOR == theme.COLORS["text"] == theme.text_color()
    assert theme.mode_color("folder") == theme.COLORS["mode-folder"]
    theme.set_theme("standard")
    assert theme.COLORS["app-bar"] == "#1d4ed8"


def test_apply_theme_updates_app_and_bound_widgets(qtbot):
    app = QApplication.instance()
    old_sheet, old_palette = app.styleSheet(), app.palette()
    w = QWidget()
    qtbot.addWidget(w)
    seen: list[str] = []
    theme.bind(w, lambda owner: seen.append(theme.COLORS["app-bar"]))
    try:
        assert theme.apply_theme(app, "g") == "g"
        assert theme.COLORS["slate-100"] in app.styleSheet()
        window_role = app.palette().ColorRole.Window
        assert app.palette().color(window_role) == QColor(theme.COLORS["slate-100"])
        assert seen[-1] == "#090e19"
        assert theme.apply_theme(app, "standard") == "standard"
        assert seen[-1] == "#1d4ed8"
    finally:
        app.setStyleSheet(old_sheet)
        app.setPalette(old_palette)


def _first_opaque_color(img):
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 200:
                return c.name()
    return None


def _close(a, b, tol=3):
    """プリマルチプライ済みピクセルの丸め誤差を許して色を比べる。"""
    ca, cb = QColor(a), QColor(b)
    return all(abs(x - y) <= tol for x, y in
               ((ca.red(), cb.red()), (ca.green(), cb.green()), (ca.blue(), cb.blue())))


def test_icons_follow_theme(qtbot):
    ic = icons.icon("Search")
    light = ic.pixmap(16, 16).toImage()
    theme.set_theme("g")
    dark = ic.pixmap(16, 16).toImage()
    assert light != dark  # 同じ QIcon が、テーマ切り替え後は別の色で描かれる
    assert _close(_first_opaque_color(light), "#334155")
    assert _close(_first_opaque_color(dark), theme.COLORS["text"])
    fixed = icons.icon("Search", "#ff0000").pixmap(16, 16).toImage()
    assert _close(_first_opaque_color(fixed), "#ff0000")  # 固定色は変わらない
    on = icons.icon("Search", "@text", on_color="#ffffff")
    on_img = on.pixmap(16, 16, QIcon.Mode.Normal, QIcon.State.On).toImage()
    assert _close(_first_opaque_color(on_img), "#ffffff")


def test_settings_dialog_lists_all_themes_and_saves_selection(qtbot):
    from PyQt6.QtCore import Qt

    from src.views.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    lst = dialog._theme_list
    assert lst.count() == 18
    ids = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())]
    assert ids == EXPECTED_IDS
    assert dialog.selected_theme() == "standard"
    lst.setCurrentRow(EXPECTED_IDS.index("q"))
    assert dialog.selected_theme() == "q"
    dialog.save_heavy_pdf_settings()
    assert app_settings.theme_id() == "q"
