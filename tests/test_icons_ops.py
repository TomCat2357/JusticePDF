from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QWidget

from src.views import icons
from src.views.context_menu import build_context_menu
from src.views.ops import make_action
from src.views.theme import load_app_stylesheet


def test_icons_and_ops_basics(qapp):
    assert not icons.icon("RotateCw").isNull()
    assert not icons.icon("rotate-cw").isNull()
    assert not icons.icon_with_chevron("Search").isNull()
    with pytest.raises(KeyError):
        icons.icon("NoSuchIcon")
    # 色の印が残らず、全 QSS が組み立てられる
    assert "@" not in load_app_stylesheet()

    parent = QWidget()
    assert make_action(parent, "undo").toolTip() == "元に戻す (Ctrl+Z)"
    assert make_action(parent, "rename", label="名前を変更").toolTip().startswith("名前を変更")
    assert make_action(parent, "reload").toolTip() == "再読み込み"


def test_context_menu_orders_by_group_with_separators(qapp):
    parent = QWidget()
    menu = build_context_menu(
        parent, "見出し",
        [make_action(parent, "delete"), make_action(parent, "copy"), make_action(parent, "open"),
         ("clip", "Scissors", "切り取り", None, "Ctrl+X", False)],
        "注記",
    )
    acts = menu.actions()
    texts = [("-" if a.isSeparator() else a.text().split("\t")[0]) for a in acts]
    assert texts == ["見出し", "-", "開く", "-", "コピー", "切り取り", "-", "削除", "-", "注記"]
    assert not acts[0].isEnabled() and not acts[5].isEnabled()
