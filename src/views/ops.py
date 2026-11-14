"""操作の表（Justice Desk の ``OPS`` に相当）と、そこから QAction / ツールボタンを作る部品。

ツールバー・右クリック・メニューで同じ「アイコン + 表示名 + キー表記」を使うための
1 か所の定義。``keys`` は表示用で、実際のショートカット登録は呼び出し側が決める
（``make_action(..., shortcut=...)`` を渡したときだけ登録する）。
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QToolButton

from . import icons

# 右クリックの節の並び（これ以外の順は作らない）。
CTX_GROUPS = ["open", "comment", "select", "history", "clip", "edit",
              "output", "find", "link", "reload", "danger"]

# QAction に持たせる property 名
PROP_OP_ID = "op_id"
PROP_GROUP = "op_group"
PROP_ICON = "icon_name"


@dataclass(frozen=True)
class Op:
    id: str
    icon: str
    label: str
    keys: str | None
    group: str          # CTX_GROUPS のどれか。ツールバーだけの項目は ""
    desc: str | None = None


def _ops(*rows: tuple) -> dict[str, Op]:
    return {r[0]: Op(*r) for r in rows}


#          id            icon                 label                       keys        group      desc
OPS: dict[str, Op] = _ops(
    # 開く
    ("open",         "FolderOpen",        "開く",                       None,       "open",    None),
    ("openApp",      "ExternalLink",      "既定のアプリで開く",         None,       "open",    None),
    ("edit",         "Pencil",            "編集",                       None,       "open",    None),
    ("openLocation", "FolderSearch",      "場所を開く",                 None,       "open",    None),
    ("openZoom",     "Maximize2",         "拡大ビューで開く",           None,       "open",    None),
    ("openPanel",    "SlidersHorizontal", "注釈のパネルで開く",         None,       "open",    None),
    # 選択
    ("selectall",    "CheckSquare",       "すべて選択",                 "Ctrl+A",   "select",  None),
    ("deselect",     "Square",            "選択を解除",                 "Esc",      "select",  None),
    # 元に戻す
    ("undo",         "Undo2",             "元に戻す",                   "Ctrl+Z",   "history", None),
    ("redo",         "Redo2",             "やり直す",                   "Ctrl+Y",   "history", None),
    # クリップボード
    ("cut",          "Scissors",          "切り取り",                   "Ctrl+X",   "clip",    None),
    ("copy",         "Copy",              "コピー",                     "Ctrl+C",   "clip",    None),
    ("duplicate",    "CopyPlus",          "複製",                       None,       "clip",    None),
    ("paste",        "ClipboardPaste",    "貼り付け",                   "Ctrl+V",   "clip",    None),
    # 編集
    ("rename",       "Pencil",            "名前変更",                   "F2",       "edit",    None),
    ("rotate",       "RotateCw",          "回転",                       "R",        "edit",    None),
    ("rotateRight",  "RotateCw",          "右に90°回転",                "R",        "edit",    None),
    ("rotateLeft",   "RotateCcw",         "左に90°回転",                None,       "edit",    None),
    ("rotate180",    "Repeat",            "180°回転",                   None,       "edit",    None),
    ("bookmarkAdd",  "BookmarkPlus",      "しおりを追加",               None,       "edit",    None),
    ("highlight",    "Highlighter",       "マーカー",                   None,       "edit",    None),
    ("underline",    "Underline",         "下線",                       None,       "edit",    None),
    ("strikeout",    "Strikethrough",     "取り消し線",                 None,       "edit",    None),
    ("redact",       "EyeOff",            "墨消し",                     None,       "edit",    None),
    ("toFront",      "ArrowUpToLine",     "最前面へ",                   None,       "edit",    None),
    ("toBack",       "ArrowDownToLine",   "最背面へ",                   None,       "edit",    None),
    # 作る・書き出す
    ("extract",      "FileOutput",        "抽出",                       None,       "output",  None),
    ("convertPdf",   "FileCog",           "PDFに変換",                  None,       "output",  None),
    ("unzip",        "PackageOpen",       "zipを解凍",                  None,       "output",  None),
    ("piiBatch",     "ShieldAlert",       "個人情報をチェック",         None,       "output",  None),
    ("export",       "Download",          "エクスポート",               "Ctrl+E",   "output",  None),
    ("print",        "Printer",           "印刷",                       "Ctrl+P",   "output",  None),
    # 探す
    ("find",         "Search",            "文書内で検索",               "Ctrl+F",   "find",    None),
    ("google",       "ExternalLink",      "Google で検索",              None,       "find",    None),
    ("translate",    "Languages",         "Google 翻訳で開く",          None,       "find",    None),
    # 再読み込み・削除
    ("reload",       "RefreshCw",         "再読み込み",                 None,       "reload",  None),
    ("delete",       "Trash2",            "削除",                       "Delete",   "danger",  None),
    # ツールバーだけの共通項目（group なし）
    ("back",         "ArrowLeft",         "戻る",                       "Alt+←",    "",        None),
    ("forward",      "ArrowRight",        "進む",                       "Alt+→",    "",        None),
    ("up",           "ArrowUp",           "上のフォルダへ",             "Alt+↑",    "",        None),
    ("save",         "Save",              "保存",                       "Ctrl+S",   "",        None),
    ("panel",        "PanelRight",        "パネル",                     "Alt+P",    "",        None),
)


def tooltip_text(label: str, keys: str | None) -> str:
    """ツールチップは「表示名 (キー)」。キーが無ければ表示名だけ。"""
    return f"{label} ({keys})" if keys else label


def make_action(parent, op_id: str, slot=None, *, shortcut=None, label: str | None = None,
                checkable: bool = False) -> QAction:
    """OPS の定義から QAction を作る（アイコン・表示名・ツールチップ）。

    shortcut を渡したときだけ setShortcut する（keys は表示用）。
    """
    op = OPS[op_id]
    text = label if label is not None else op.label
    action = QAction(icons.icon(op.icon), text, parent)
    action.setToolTip(tooltip_text(text, op.keys))
    action.setCheckable(checkable)
    action.setProperty(PROP_OP_ID, op.id)
    action.setProperty(PROP_GROUP, op.group)
    action.setProperty(PROP_ICON, op.icon)
    if shortcut is not None:
        action.setShortcut(shortcut)
    if slot is not None:
        action.triggered.connect(slot)
    return action


def action_group(action: QAction) -> str:
    """右クリックの節（CTX_GROUPS のどれか。不明なら ""）。"""
    return action.property(PROP_GROUP) or ""


def make_tool_button(action_or_None=None, *, icon=None, tooltip: str | None = None,
                     menu=None, variant: str = "flat", parent=None) -> QToolButton:
    """ツールバー用のアイコンボタン（flat）/ 拡大ビュー用の枠付き（box）。

    - IconOnly・autoRaise・NoFocus（拡大ビューで Space を手のひらに使うため、
      ボタンが Space を受けないようにする）。
    - menu があれば InstantPopup + 「アイコン + 小さな ▼」の横長アイコン。
    - action を渡すと setDefaultAction（テキストは action に残る）。
    - icon は QIcon かアイコン名。
    """
    btn = QToolButton(parent)
    btn.setProperty("variant", variant)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setAutoRaise(True)
    px = 16 if variant == "box" else 17
    btn.setIconSize(QSize(px, px))

    icon_name = icon if isinstance(icon, str) else None
    if action_or_None is not None:
        btn.setDefaultAction(action_or_None)
        if icon_name is None:
            icon_name = action_or_None.property(PROP_ICON)
    if icon is not None and not menu:
        btn.setIcon(icons.icon(icon, size=px) if isinstance(icon, str) else icon)
    if menu is not None:
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        if icon_name:
            btn.setIconSize(QSize(*icons.chevron_icon_size(px)))
            btn.setIcon(icons.icon_with_chevron(icon_name, size=px))
        elif icon is not None:
            btn.setIcon(icon)
    if tooltip is not None:
        btn.setToolTip(tooltip)
    return btn
