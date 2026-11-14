"""Justice Desk（ONLINE 版）の標準テーマの色と、アプリ全体の QSS の組み立て。

QSS(``styles/*.qss``)の中では色を ``@slate-300@`` のような印で書き、
:func:`load_app_stylesheet` が 4 ファイルを順に連結して印を実際の色へ置き換える。
"""
from __future__ import annotations

import re
from pathlib import Path

STYLES_DIR = Path(__file__).resolve().parent / "styles"
QSS_FILES = ("base.qss", "library.qss", "page_edit.qss", "panels.qss")

FONT_FAMILY = '"Noto Sans JP", "Yu Gothic UI", "Meiryo UI", sans-serif'


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


COLORS: dict[str, str] = {
    "white": "#ffffff",
    # slate
    "slate-50": "#f8fafc",
    "slate-100": "#f1f5f9",
    "slate-200": "#e2e8f0",
    "slate-300": "#cbd5e1",
    "slate-400": "#94a3b8",
    "slate-500": "#64748b",
    "slate-600": "#475569",
    "slate-700": "#334155",
    "slate-800": "#1e293b",
    "slate-900": "#0f172a",
    # blue
    "blue-50": "#eff6ff",
    "blue-100": "#dbeafe",
    "blue-500": "#3b82f6",
    "blue-600": "#2563eb",
    "blue-700": "#1d4ed8",
    "blue-800": "#1e40af",
    # アプリバー
    "app-bar": "#1d4ed8",
    "app-bar-text": "#ffffff",
    "app-bar-hover": "#2563eb",
    # モード色（帯・枠）と薄色（ツールバーの地）
    "mode-folder": "#2563eb",
    "mode-pages": "#c2410c",
    "mode-folder-soft": "#eff6ff",
    "mode-pages-soft": "#fff7ed",
    "mode-preview": "#15803d",
    "mode-preview-soft": "#f0fdf4",
    "mode-text": "#7e22ce",
    "mode-text-soft": "#faf5ff",
    # カード・選択
    "select-bg": "#dbeafe",
    "select-ring": "#3b82f6",
    "dnd-ring": "#22c55e",
    "dnd-bg": "#dcfce7",
    "insert-line": "#2563eb",
    "drop-area": "#38bdf8",
    "drop-area-bg": "#f0f9ff",
    # フォルダカード
    "folder-card-bg": "#fffbeb",
    "folder-card-border": "#fde68a",
    "folder-icon": "#f59e0b",
    "folder-text": "#b45309",
    "star": "#f59e0b",
    # バッジ（slate-600 の 85%）
    "badge-bg": "rgba(71, 85, 105, 0.85)",
    "badge-text": "#ffffff",
    # ページ一覧
    "page-list-bg": "#e2e8f0",
    "page-card-bg": "#f8fafc",
    "hit-bg": "#fef9c3",
    "hit-ring": "#facc15",
    "hit-current-bg": "#fef08a",
    "hit-current-ring": "#c2410c",
    "zoom-bg": "rgba(100, 116, 139, 0.8)",
    "paper": "#ffffff",
    "line": "#e5e7eb",
    # 危険・警告
    "danger": "#dc2626",
    "danger-soft": "#fef2f2",
    "danger-text": "#b91c1c",
    "danger-muted": "#fca5a5",
    "warn": "#b45309",
    "warn-soft": "#fffbeb",
    # ラバーバンド
    "rubber-fill": "rgba(37, 99, 235, 0.10)",
}
# モード色の 35%（ツールバーの下罫線）
COLORS["mode-folder-line"] = _rgba(COLORS["mode-folder"], 0.35)
COLORS["mode-pages-line"] = _rgba(COLORS["mode-pages"], 0.35)

# 既定の文字色（ツールバーのボタン・アイコン）
TEXT_COLOR = COLORS["slate-700"]

# "zoom"（拡大ビュー）は pages と同じ色。
_MODE_KEYS = {"folder": "folder", "pages": "pages", "zoom": "pages"}


def mode_color(mode: str) -> str:
    """モード帯・枠の色。"""
    return COLORS[f"mode-{_MODE_KEYS[mode]}"]


def mode_soft_color(mode: str) -> str:
    """モードの薄色（ツールバーの地）。"""
    return COLORS[f"mode-{_MODE_KEYS[mode]}-soft"]


_TOKEN_RE = re.compile(r"@([a-z0-9][a-z0-9-]*)@")


def apply_tokens(text: str, colors: dict[str, str] | None = None) -> str:
    """QSS の ``@名前@`` を色へ置き換える。未定義の名前は KeyError。"""
    table = COLORS if colors is None else colors
    return _TOKEN_RE.sub(lambda m: table[m.group(1)], text)


def load_app_stylesheet() -> str:
    """styles/ の 4 ファイルを順に連結し、色の印を置き換えた QSS を返す。"""
    parts = [(STYLES_DIR / name).read_text(encoding="utf-8") for name in QSS_FILES]
    return apply_tokens("\n".join(parts))
