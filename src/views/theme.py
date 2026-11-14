"""Justice Desk（ONLINE 版）の表示テーマの色と、アプリ全体の QSS の組み立て。

QSS(``styles/*.qss``)の中では色を ``@slate-300@`` のような印で書き、
:func:`load_app_stylesheet` が 4 ファイルを順に連結して印を実際の色へ置き換える。
テーマの一覧と配色の元データは ``themes.py``。:func:`build_colors` がそれを色トークンの辞書へ
割り当て、:func:`apply_theme` が QApplication へ反映する。
"""
from __future__ import annotations

import re
import weakref
from collections.abc import Callable
from pathlib import Path

from src.views.themes import DEFAULT_THEME, PALETTES, normalize_theme_id

STYLES_DIR = Path(__file__).resolve().parent / "styles"
QSS_FILES = ("base.qss", "library.qss", "page_edit.qss", "panels.qss")

FONT_FAMILY = '"Noto Sans JP", "Yu Gothic UI", "Meiryo UI", sans-serif'

_SHADES = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900)


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def _hex(rgb) -> str:
    r, g, b = (int(x) for x in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix(rgb_a, rgb_b, ratio: float) -> tuple[int, ...]:
    """rgb_a に rgb_b を ratio（0〜1）だけ混ぜる。"""
    return tuple(round(a + (b - a) * ratio) for a, b in zip(rgb_a[:3], rgb_b[:3]))


def is_dark_theme(theme_id: str) -> bool:
    return bool(PALETTES[normalize_theme_id(theme_id)].get("dark"))


def build_colors(theme_id: str = DEFAULT_THEME) -> dict[str, str]:
    """テーマの色トークン辞書を組み立てる。

    標準テーマの値を土台に、テーマごとの差分（``themes.PALETTES``）を重ねる。
    トークンの意味: ``slate-*`` ``blue-*`` ``white`` は地・罫線・塗りの色、``t-slate-*``
    ``t-blue-*`` ``t-white`` は文字・アイコンの色（ダークでは地と文字で明暗が逆になるため
    別に持つ。QSS の ``color:`` に書いた印は :func:`apply_tokens` がこちらへ置き換える）。
    """
    tid = normalize_theme_id(theme_id)
    own = PALETTES[tid]
    p: dict[str, object] = dict(PALETTES[DEFAULT_THEME])
    p.update(own)
    dark = bool(own.get("dark"))

    def c(name: str) -> str:  # 地・罫線・塗り
        return _hex(p[f"c-{name}"])

    def t(name: str) -> str:  # 文字・アイコン（未定義なら地と同じ値）
        v = own.get(f"t-{name}")
        return _hex(v if v is not None else p[f"c-{name}"])

    paper = own.get("c-paper") or p["c-white"]  # ダークだけ白に固定。ほかは地の白と同じ
    hover = p["hdr-hover"]
    hover_css = _rgba(_hex(hover), hover[3]) if len(hover) > 3 else _hex(hover)
    hit = p["hit-bg"]

    colors: dict[str, str] = {
        "white": c("white"),
        "t-white": "#ffffff",  # 濃い塗りの上に載せる文字の白（ダークでも白のまま）
    }
    for n in _SHADES:
        colors[f"slate-{n}"] = c(f"slate-{n}")
        colors[f"t-slate-{n}"] = t(f"slate-{n}")
        colors[f"blue-{n}"] = c(f"blue-{n}")
        colors[f"t-blue-{n}"] = t(f"blue-{n}")
    colors.update({
        # アプリバー（ONLINE 版のヘッダー）。背景は単色（グラデーション・模様は移植しない）
        "app-bar": _hex(p["hdr-bg"]),
        "app-bar-text": _hex(p["hdr-fg"]),
        "app-bar-title": _hex(p["hdr-title"]),
        "app-bar-hover": hover_css,
        # モード色（帯・枠）と薄色（ツールバーの地）
        "mode-folder": _hex(p["mode-folder"]),
        "mode-pages": _hex(p["mode-pages"]),
        "mode-folder-soft": _hex(p["mode-folder-bg"]),
        "mode-pages-soft": _hex(p["mode-pages-bg"]),
        "mode-preview": _hex(p["mode-preview"]),
        "mode-preview-soft": _hex(p["mode-preview-bg"]),
        "mode-text": _hex(p["mode-text"]),
        "mode-text-soft": _hex(p["mode-text-bg"]),
        # カード・選択
        "select-bg": c("blue-100"),
        "select-ring": c("blue-500"),
        "dnd-ring": c("green-500"),
        "dnd-bg": c("green-100"),
        "insert-line": c("blue-600"),
        "drop-area": c("sky-400"),
        "drop-area-bg": c("sky-50"),
        # フォルダカード
        "folder-card-bg": c("folder-50"),
        "folder-card-border": c("folder-200"),
        "folder-icon": t("folder-500"),
        "folder-text": t("folder-700"),
        "star": t("folder-500"),
        # バッジ（slate-600 の 85%）
        "badge-bg": _rgba(c("slate-600"), 0.85),
        "badge-text": "#ffffff",
        # ページ一覧
        "page-list-bg": c("slate-200"),
        "page-card-bg": c("slate-50"),
        "hit-bg": _hex(hit),
        "hit-ring": "#facc15",
        "hit-current-bg": _hex(_mix(hit, (250, 204, 21), 0.25)) if dark else "#fef08a",
        "hit-current-ring": "#fb923c" if dark else "#c2410c",
        "zoom-bg": _rgba(c("slate-500"), 0.8),
        "paper": _hex(paper),
        "line": _hex(p["c-line"]),
        # 危険・警告（文字に使う色は文字用の値）
        "danger": t("red-600"),
        "danger-soft": c("red-50"),
        "danger-text": t("red-700"),
        "danger-muted": c("red-400") if dark else c("red-300"),
        "warn": t("amber-700"),
        "warn-soft": c("amber-50"),
        # PDF 以外のファイルの種類色（アイコン）。文字系の 2 つはテーマの文字色に合わせる
        "kind-excel": "#16a34a",
        "kind-word": "#2563eb",
        "kind-powerpoint": "#ea580c",
        "kind-image": "#9333ea",
        "kind-zip": "#d97706",
        "kind-text": t("slate-600"),
        "kind-other": t("slate-400"),
        # ラバーバンド
        "rubber-fill": _rgba(c("blue-500"), 0.2),
        # カードのホバー（slate-200 の 70%）
        "card-hover": _rgba(c("slate-200"), 0.7),
        # Python 側で使う文字・アイコンの色（slate-700 / 600 / 500 / 400 の文字用の値）
        "text": t("slate-700"),
        "icon-strong": t("slate-600"),
        "icon-muted": t("slate-500"),
        "icon-disabled": t("slate-400"),
    })
    # しおりの階層色（帯と題名）
    for i in (1, 2, 3, 4):
        colors[f"bm-band-{i}"] = _hex(p[f"bm-c{i}"])
        colors[f"bm-text-{i}"] = _hex(p[f"bm-t{i}"])
    # モード色の 35%（ツールバーの下罫線）
    colors["mode-folder-line"] = _rgba(colors["mode-folder"], 0.35)
    colors["mode-pages-line"] = _rgba(colors["mode-pages"], 0.35)
    return colors


# 現在のテーマの色トークン。テーマを切り替えると中身を入れ替える（辞書そのものは同じ
# オブジェクトのまま）ので、``from theme import COLORS`` した側も描画のたびに読めば追従する。
COLORS: dict[str, str] = build_colors(DEFAULT_THEME)
_current_theme = DEFAULT_THEME

# 既定の文字色（ツールバーのボタン・アイコン）。テーマ切り替えで更新する。
TEXT_COLOR = COLORS["text"]

# "zoom"（拡大ビュー）は pages と同じ色。
_MODE_KEYS = {"folder": "folder", "pages": "pages", "zoom": "pages"}


def mode_color(mode: str) -> str:
    """モード帯・枠の色。"""
    return COLORS[f"mode-{_MODE_KEYS[mode]}"]


def mode_soft_color(mode: str) -> str:
    """モードの薄色（ツールバーの地）。"""
    return COLORS[f"mode-{_MODE_KEYS[mode]}-soft"]


def current_theme() -> str:
    return _current_theme


def text_color() -> str:
    """いまのテーマの既定の文字・アイコンの色。"""
    return COLORS["text"]


# ---- 切り替えの通知 ---------------------------------------------------------
_bindings: list[tuple[weakref.ref, Callable]] = []


def bind(owner, refresh: Callable, *, call_now: bool = True) -> None:
    """``refresh(owner)`` をテーマが切り替わるたびに呼ぶ（owner は弱参照。消えたら自動で外れる）。

    ウィジェットが色を Python 側（setStyleSheet・アイコン・描画キャッシュ）で持つとき、
    その組み立てを ``refresh`` に置き、作成時に ``bind`` する。``call_now`` なら今も 1 回呼ぶ。
    ``refresh`` は owner 以外を閉じ込めない関数（lambda self: ...）にすること。
    """
    _bindings.append((weakref.ref(owner), refresh))
    if call_now:
        refresh(owner)


def _notify() -> None:
    alive = []
    for ref, fn in _bindings:
        owner = ref()
        if owner is None:
            continue
        try:
            fn(owner)
        except RuntimeError:  # C++ 側が先に消えたウィジェット
            continue
        alive.append((ref, fn))
    _bindings[:] = alive


def set_theme(theme_id) -> str:
    """色トークン（COLORS・TEXT_COLOR）を指定のテーマへ入れ替える。QSS・描画の更新は :func:`apply_theme`。"""
    global _current_theme, TEXT_COLOR
    tid = normalize_theme_id(theme_id)
    new = build_colors(tid)
    COLORS.clear()
    COLORS.update(new)
    _current_theme = tid
    TEXT_COLOR = COLORS["text"]
    return tid


_original_palette = None


def _build_palette(colors: dict[str, str]):
    from PyQt6.QtGui import QColor, QPalette

    pal = QPalette()
    role = QPalette.ColorRole
    mapping = {
        role.Window: colors["slate-100"], role.WindowText: colors["t-slate-800"],
        role.Base: colors["white"], role.AlternateBase: colors["slate-50"],
        role.Text: colors["t-slate-800"], role.Button: colors["slate-100"],
        role.ButtonText: colors["t-slate-800"], role.ToolTipBase: colors["slate-200"],
        role.ToolTipText: colors["t-slate-800"], role.Highlight: colors["blue-600"],
        role.HighlightedText: "#ffffff", role.PlaceholderText: colors["t-slate-400"],
        role.BrightText: "#ffffff", role.Link: colors["t-blue-600"],
    }
    for r, hexv in mapping.items():
        pal.setColor(r, QColor(hexv))
    for r in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, r, QColor(colors["t-slate-400"]))
    return pal


def apply_theme(app, theme_id) -> str:
    """テーマを切り替える: 色トークン → QApplication の QSS・パレット → 登録済みウィジェットの再描画。

    ダークのときだけ QSS で塗りきれない部分（ネイティブのダイアログ・メニューの既定色）のために
    QPalette も入れ替える。ライト系へ戻すときは最初のテーマ適用前のパレットへ戻す。
    """
    global _original_palette
    tid = set_theme(theme_id)
    if app is not None:
        if _original_palette is None:
            _original_palette = app.palette()
        app.setPalette(_build_palette(COLORS) if is_dark_theme(tid) else _original_palette)
        app.setStyleSheet(load_app_stylesheet())
    _notify()
    if app is not None:
        for w in app.allWidgets():
            w.update()
    return tid


_TOKEN_RE = re.compile(r"@([a-z0-9][a-z0-9-]*)@")
# 文字色の宣言（color / selection-color。border-bottom-color などは含めない）に書かれた印
_TEXT_TOKEN_RE = re.compile(r"(?<![\w-])((?:selection-)?color\s*:\s*)@([a-z0-9][a-z0-9-]*)@")


def apply_tokens(text: str, colors: dict[str, str] | None = None) -> str:
    """QSS の ``@名前@`` を色へ置き換える。未定義の名前は KeyError。

    ``color:`` ``selection-color:`` に書いた印は、文字用の値（``t-<名前>``）があればそちらへ
    置き換える（ダークテーマで「地の slate-800」ではなく「明るい文字色」になる）。
    """
    table = COLORS if colors is None else colors

    def _text(m: re.Match) -> str:
        name = m.group(2)
        return m.group(1) + table.get(f"t-{name}", table[name])

    text = _TEXT_TOKEN_RE.sub(_text, text)
    return _TOKEN_RE.sub(lambda m: table[m.group(1)], text)


def load_app_stylesheet(colors: dict[str, str] | None = None) -> str:
    """styles/ の 4 ファイルを順に連結し、色の印を置き換えた QSS を返す。"""
    parts = [(STYLES_DIR / name).read_text(encoding="utf-8") for name in QSS_FILES]
    return apply_tokens("\n".join(parts), colors)
