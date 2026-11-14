"""右クリックメニュー（Justice Desk 風）。

見出し行（対象名）+ 動詞だけの項目 + 全項目アイコン + 節の自動区切り
（open → comment → select → history → clip → edit → output → find → link → reload → danger）
+ 末尾の注記行。キー表記は右端に出すだけで、実際のショートカットは登録しない。
"""
from __future__ import annotations

from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import QMenu

from . import icons
from .ops import CTX_GROUPS, OPS, PROP_GROUP, PROP_ICON, PROP_OP_ID

MENU_ICON_SIZE = 14
MENU_ICON_COLOR = "#64748b"  # slate-500


def _group_rank(group: str) -> int:
    try:
        return CTX_GROUPS.index(group)
    except ValueError:
        return len(CTX_GROUPS)


def _small_note(menu: QMenu, text: str) -> QAction:
    """選べない小さな灰色の行（見出し・注記）。"""
    a = QAction(text, menu)
    a.setEnabled(False)
    f = QFont(menu.font())
    f.setPixelSize(11)
    a.setFont(f)
    return a


def _from_action(menu: QMenu, source: QAction) -> tuple[str, QAction]:
    group = source.property(PROP_GROUP) or ""
    icon_name = source.property(PROP_ICON)
    op = OPS.get(source.property(PROP_OP_ID) or "")
    text = source.text().replace("&", "")
    keys = op.keys if op else None
    proxy = QAction(f"{text}\t{keys}" if keys else text, menu)
    if icon_name:
        proxy.setIcon(icons.icon(icon_name, MENU_ICON_COLOR, size=MENU_ICON_SIZE))
    proxy.setEnabled(source.isEnabled())
    if source.isCheckable():
        proxy.setCheckable(True)
        proxy.setChecked(source.isChecked())
    proxy.triggered.connect(lambda _=False, s=source: s.trigger())
    return group, proxy


def _from_tuple(menu: QMenu, entry: tuple) -> tuple[str, QAction]:
    group, icon_name, label, slot, keys, enabled = entry
    a = QAction(f"{label}\t{keys}" if keys else label, menu)
    if icon_name:
        a.setIcon(icons.icon(icon_name, MENU_ICON_COLOR, size=MENU_ICON_SIZE))
    a.setEnabled(bool(enabled))
    if slot is not None:
        a.triggered.connect(slot)
    return group, a


def build_context_menu(parent, header: str | None, entries, footer: str | None = None) -> QMenu:
    """右クリックメニューを作る。

    entries: QAction（make_action で作ったもの = 節を持つ）、または
    ``(group, icon, label, slot, keys, enabled)`` のタプル。節の順に並べ替え、
    節の間に区切り線を入れる。
    """
    menu = QMenu(parent)
    rows: list[tuple[int, int, str, QAction]] = []
    for i, entry in enumerate(entries):
        group, act = (_from_action(menu, entry) if isinstance(entry, QAction)
                      else _from_tuple(menu, tuple(entry)))
        rows.append((_group_rank(group), i, group, act))
    rows.sort(key=lambda r: (r[0], r[1]))  # 節の順、同じ節の中は渡された順

    if header:
        menu.addAction(_small_note(menu, header))
        menu.addSeparator()
    prev_rank = None
    for rank, _i, _group, act in rows:
        if prev_rank is not None and rank != prev_rank:
            menu.addSeparator()
        menu.addAction(act)
        prev_rank = rank
    if footer:
        if rows:
            menu.addSeparator()
        menu.addAction(_small_note(menu, footer))
    return menu
