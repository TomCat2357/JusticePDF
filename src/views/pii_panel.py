"""個人情報(PII)検出ドロワー。

ページ編集画面のズームビュー右側にドロワーとして組み込む独立部品。
``BookmarksPanel`` と同じ「表示専用・状態は呼び出し側(page_edit_window)が持つ」
方針に従う。本パネルはPDFの読み書きを一切行わず、ボタン操作をシグナルで
通知するだけ。実際の検出実行・注釈の作成/削除・Undo登録は
``src.views.page_edit_pii.PiiDrawerMixin`` が担う。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QPushButton,
    QSlider,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.pii.entity_types import (
    ENTITY_TYPES,
    ENTITY_TYPES_WITH_MANUAL,
    MANUAL_ENTITY_TYPE,
    get_entity_type_name_ja,
)
from src.pii.text_normalize import normalize_pattern
from src.utils.pdf_utils import ShapeAnnotData, ShapeType, TextMarkupAnnotData

logger = logging.getLogger(__name__)

_ANNOT_ROLE = Qt.ItemDataRole.UserRole

_COLUMN_TO_SORT_FIELD = {0: "text", 1: "entity", 2: "page"}
_SORT_FIELD_TO_COLUMN = {v: k for k, v in _COLUMN_TO_SORT_FIELD.items()}

# 結果一覧をExcel等へコピーするときの見出し行(タブ区切り)。
_TSV_HEADER = "語句\t種別\tページ"


# 結果一覧の「語句」列に出す最大文字数(超えた分は「…」で省略)。
DISPLAY_TEXT_MAX_CHARS = 60


@dataclass(slots=True)
class PiiResultRow:
    """結果一覧1行分の表示用データ(マーカー/図形どちらも同じ形で扱う)。"""

    annot: "TextMarkupAnnotData | ShapeAnnotData"
    page_num: int
    entity: str
    text: str  # マッチした文字列(無ければ空文字)
    kind: str  # "markup" | "shape"
    # ページ内の出現位置(x0, y0, x1, y1)。一覧の並び(ページ内の読み順)にだけ使う隠しデータ
    # (マーカーは先頭quad、図形は矩形。ページの表示座標系=回転ページでは回転後)。
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @property
    def wrap_text(self) -> str:
        """折り返し表示用の語句(改行・連続空白だけ整形し、省略はしない)。"""
        return self._single_line() or ("[図形]" if self.kind == "shape" else "(テキストなし)")

    def _single_line(self) -> str:
        return " ".join(self.text.split()) if self.text else ""

    @property
    def display_text(self) -> str:
        """一覧に出す1行の語句(改行・連続空白は空白1つにまとめ、長ければ省略する)。

        保存済みのデータに改行が含まれていても、行が縦に伸びないようにする。
        """
        if self.text:
            single = " ".join(self.text.split())
            if single:
                if len(single) > DISPLAY_TEXT_MAX_CHARS:
                    return single[: DISPLAY_TEXT_MAX_CHARS - 1] + "…"
                return single
        return "[図形]" if self.kind == "shape" else "(テキストなし)"


def reading_order_keys(
    rows: "list[PiiResultRow]", vertical: bool = False
) -> "list[tuple[int, float]]":
    """各行のページ内の読み順キー ``(行/列の番号, 行内/列内の位置)`` を ``rows`` と同じ並びで返す。

    ページごとに独立して番号を振る。横書きは上→下(同じ行は左→右)、縦書きは
    右の列→左の列(同じ列は上→下)。文字の高さ(縦書きは幅)が微妙にずれた語が
    左右(上下)逆転しないよう、中心座標でソートしてから、先頭の要素の高さ(幅)の
    半分以内のものを同じ行(列)にまとめて番号を振る。図形(塗り四角など)は大きいことが
    あるため、上端(縦書きは右端)を位置とし、行(列)をまとめる基準には使わない。
    """
    keys: list[tuple[int, float]] = [(0, 0.0)] * len(rows)
    by_page: dict[int, list[int]] = {}
    for i, row in enumerate(rows):
        by_page.setdefault(row.page_num, []).append(i)
    for indices in by_page.values():
        if vertical:
            # 列の軸は x(右ほど先)。列の幅=矩形の幅、列内の位置は上(y0)から。
            def center(i: int) -> float:
                b = rows[i].bbox
                if rows[i].kind == "shape":
                    return -b[2]  # 図形は右端を位置にする
                return -(b[0] + b[2]) / 2

            def extent(i: int) -> float:
                b = rows[i].bbox
                # 大きな図形が列の基準になって、別の列の文字を巻き込まないよう許容幅は0
                return 0.0 if rows[i].kind == "shape" else b[2] - b[0]

            def within(i: int) -> float:
                return rows[i].bbox[1]
        else:
            def center(i: int) -> float:
                b = rows[i].bbox
                if rows[i].kind == "shape":
                    return b[1]  # 図形は上端を位置にする
                return (b[1] + b[3]) / 2

            def extent(i: int) -> float:
                b = rows[i].bbox
                # 大きな図形が行の基準になって、別の行の文字を巻き込まないよう許容幅は0
                return 0.0 if rows[i].kind == "shape" else b[3] - b[1]

            def within(i: int) -> float:
                return rows[i].bbox[0]

        line_no = -1
        anchor = 0.0
        tolerance = 0.0
        for i in sorted(indices, key=center):
            if line_no < 0 or center(i) - anchor > tolerance:
                line_no += 1
                anchor = center(i)
                tolerance = extent(i) / 2
            keys[i] = (line_no, within(i))
    return keys


def _tsv_cell(text: str) -> str:
    """TSVの1セル分に整形する(タブ・改行はセル/行を壊すため空白に置き換える)。"""
    return str(text).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def rows_to_tsv(rows: "list[PiiResultRow]") -> str:
    """結果行をExcel貼り付け用のTSV文字列にする(見出し行つき、ページは数字のみ)。"""
    lines = [_TSV_HEADER]
    for row in rows:
        lines.append(
            "\t".join(
                (
                    _tsv_cell(row.text or row.display_text),  # 省略せず全文
                    _tsv_cell(get_entity_type_name_ja(row.entity or "OTHER")),
                    str(row.page_num + 1),
                )
            )
        )
    return "\n".join(lines)


# 正規表現の入力補助。「一致のしかた」と「空白を許容する」の2つの部品から、基になる語句の
# 正規表現を組み立ててテキストボックスに入れる。テキストボックスを手で編集すると
# 「自由入力」状態になる(部品を操作すると、また部品から作り直す)。
# 検出パターンは、ページ本文全体(``re.MULTILINE``)に ``finditer`` で当てるため ``^``/``$`` は
# 行頭・行末の意味にしかならず、「一致のしかた」は出さない(空白を許容のみ)。
# 除外パターンは、検出された1件の文字列に ``re.search`` で当てるため、
# 完全一致(``^…$``)・前方一致・後方一致のいずれにも意味がある。
MATCH_EXACT = "exact"
MATCH_PREFIX = "prefix"
MATCH_SUFFIX = "suffix"
MATCH_PARTIAL = "partial"
MATCH_MODES: "tuple[tuple[str, str], ...]" = (
    (MATCH_EXACT, "完全一致"),
    (MATCH_PREFIX, "前方一致"),
    (MATCH_SUFFIX, "後方一致"),
    (MATCH_PARTIAL, "部分一致"),
)
GAP_CHECK_LABEL = "空白を許容する（各文字の間の空白・改行を許す）"
FREE_INPUT_HINT = "手で編集中（自由入力）です。「一致のしかた」「空白を許容する」を変えると、語句から作り直します。"

# 検出パターンに追加するときの種別の選択肢のうち、設定に登録しない「手動」の表示名。
MANUAL_CHOICE_LABEL = "手動（検出パターンには追加しない）"
# 右クリックメニューのサブメニュー項目(検出・除外で共通)。
DETAIL_ACTION_LABEL = "詳細指定…（正規表現を編集）"
EXCLUDE_SIMPLE_LABEL = "簡易指定（完全一致）"


def build_pattern(match: str, gap: bool, base: str, gap_base: "str | None" = None) -> str:
    r"""「一致のしかた」と「空白を許容」から正規表現を組み立てる。

    ``base`` は語句を ``re.escape`` した本体(空白の扱いは設定どおり)、``gap_base`` は
    語句の空白を除いた各文字を ``re.escape`` して ``\s*`` でつないだ本体
    (``\s`` は半角・全角空白や改行にも一致する)。``gap`` が True なら ``gap_base`` を使う
    (省略時は ``base``)。``match`` は完全一致 ``^…$`` / 前方一致 ``^…`` / 後方一致 ``…$`` /
    部分一致(``…`` そのまま)。
    """
    body = (gap_base if gap_base is not None else base) if gap else base
    if match == MATCH_EXACT:
        return f"^{body}$"
    if match == MATCH_PREFIX:
        return f"^{body}"
    if match == MATCH_SUFFIX:
        return f"{body}$"
    return body


def validate_pii_pattern(pattern: str) -> str:
    """正規表現として使えるか調べ、使えなければ日本語のエラー文を、使えれば空文字を返す。

    検出時と同じく、全角記号などの正規化(``normalize_pattern``)をかけた後でコンパイルする。
    """
    if not pattern.strip():
        return "正規表現が空です。語句を入力してください。"
    try:
        re.compile(normalize_pattern(pattern))
    except re.error as error:
        return f"正規表現が正しくありません: {error}"
    return ""


def scope_target(word: str, pattern: "str | None" = None) -> "tuple[str, str]":
    """範囲選択ダイアログに出す「(見出し, 表示する文字列)」を返す。

    語句とパターンを二重に出さず1か所にまとめる。パターンが語句と異なる(簡易指定で
    ``^…$`` や ``\s*`` が付く、詳細指定で編集した等)ときは、実際に登録・検出に使う
    パターンを「パターン」として、そうでなければ「語句」として出す。
    """
    if pattern and pattern != word:
        return "パターン", pattern
    return "語句", word


class FittedTextLabel(QFrame):
    """読み取り専用の語句表示。設定「結果一覧の語句の表示」と同じ規則で表示する。

    ``mode`` が "wrap" なら ``max_lines`` 行まで折り返し、それ以外は1行。入りきらない分は
    末尾を「…」で省略する(全文はツールチップ)。改行・連続空白は空白1つにまとめる。
    横スクロールは出さず、ダイアログの幅に合わせて再計算する。
    """

    _PAD = 4

    def __init__(self, text: str, mode: str = "ellipsis", max_lines: int = 3, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self._full = text or ""
        self._lines = max(1, int(max_lines)) if mode == "wrap" else 1
        self._flat = " ".join(self._full.split())
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setToolTip(self._full)  # 省略されても全文を確認できるように

    def full_text(self) -> str:
        return self._full

    def max_lines(self) -> int:
        return self._lines

    def _text_width(self, total: "int | None" = None) -> int:
        total = self.width() if total is None else total
        width = total - 2 * (self.frameWidth() + self._PAD)
        return width if width > 20 else 420  # 未表示(幅が未確定)のときの仮の幅

    def shown_text(self, total_width: "int | None" = None) -> str:
        """現在の幅で実際に表示する(省略済みの)文字列。"""
        return fit_wrapped_text(self.fontMetrics(), self._flat, self._text_width(total_width), self._lines)

    def _flags(self) -> int:
        return int(Qt.TextFlag.TextWordWrap) | int(Qt.TextFlag.TextWrapAnywhere)

    def _height_for(self, total_width: "int | None") -> int:
        fm = self.fontMetrics()
        width = self._text_width(total_width)
        shown = fit_wrapped_text(fm, self._flat, width, self._lines)
        h = fm.boundingRect(QRect(0, 0, width, 100000), self._flags(), shown).height()
        h = max(h, fm.lineSpacing())
        return h + 2 * (self.frameWidth() + self._PAD)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._height_for(width)

    def sizeHint(self) -> QSize:
        return QSize(420, self._height_for(420))

    def minimumSizeHint(self) -> QSize:
        return QSize(120, self._height_for(120))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        inset = self.frameWidth() + self._PAD
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        painter.setPen(self.palette().color(self.foregroundRole()))
        flags = self._flags() | int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignTop)
        painter.drawText(rect, flags, self.shown_text())
        painter.end()


class PiiPatternInputDialog(QDialog):
    """「検出パターンに追加」「除外パターンに追加」で最初に開く、語句(正規表現)の入力ダイアログ。

    - 語句(読み取り専用)は、右クリックした結果の語句。
    - 入力補助は独立した2つの部品: 「一致のしかた」(完全/前方/後方/部分。``with_match`` が
      True のときだけ出す=除外パターン用)と「空白を許容する」チェック。変えるたびに
      テキストボックスを基の語句から作り直す。テキストボックスを手で編集すると
      「自由入力」状態になる(``free``)。
    - ``with_entity`` が True のときは種別のドロップダウンも出す(「検出パターンに追加」用。
      末尾に「手動(検出パターンには追加しない)」を含む)。
    - OK(Enterキー)で確定。正規表現が不正なときはエラーを示して閉じない。

    ``state()`` は ``{"entity", "match", "gap", "free", "text"}``。戻るボタンで呼び直すとき
    ``state=`` に渡すと入力内容(種類・部品・自由入力か・テキスト)を復元する。
    """

    def __init__(
        self,
        title: str,
        message: str,
        word: str,
        base_pattern: str,
        gap_pattern: str | None = None,
        *,
        with_entity: bool = False,
        with_match: bool = False,
        default_match: str = MATCH_PARTIAL,
        default_gap: bool = False,
        default_entity: str = "PERSON",
        state: "dict | None" = None,
        parent: QWidget | None = None,
        text_display: "tuple[str, int]" = ("ellipsis", 3),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self._base = base_pattern
        self._gap_base = gap_pattern if gap_pattern is not None else base_pattern
        self._with_match = with_match
        self._free = False

        layout = QVBoxLayout(self)
        self._message_label = QLabel(message)
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        form = QFormLayout()
        # 設定「結果一覧の語句の表示」(1行省略/折り返し+最大行数)に従って表示する(読み取り専用)。
        mode, max_lines = text_display
        self._word_edit = FittedTextLabel(word, mode, max_lines)
        form.addRow("選んだ語句:", self._word_edit)

        self._entity_combo: QComboBox | None = None
        if with_entity:
            self._entity_combo = QComboBox()
            for entity_type in ENTITY_TYPES:
                self._entity_combo.addItem(get_entity_type_name_ja(entity_type), entity_type)
            self._entity_combo.addItem(MANUAL_CHOICE_LABEL, MANUAL_ENTITY_TYPE)
            form.addRow("種類:", self._entity_combo)

        self._match_combo: QComboBox | None = None
        if with_match:
            self._match_combo = QComboBox()
            for key, label in MATCH_MODES:
                self._match_combo.addItem(label, key)
            form.addRow("一致のしかた:", self._match_combo)

        self._gap_check = QCheckBox(GAP_CHECK_LABEL)
        form.addRow("", self._gap_check)

        self._pattern_edit = QLineEdit()
        form.addRow("正規表現:", self._pattern_edit)
        layout.addLayout(form)

        self._free_label = QLabel(FREE_INPUT_HINT)
        self._free_label.setWordWrap(True)
        self._free_label.setVisible(False)
        layout.addWidget(self._free_label)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #c0392b;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("OK（次へ）")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 初期値(戻ってきたときは前回の入力内容を復元する)。
        src = state or {}
        if self._entity_combo is not None:
            idx = self._entity_combo.findData(src.get("entity", default_entity))
            if idx >= 0:
                self._entity_combo.setCurrentIndex(idx)
        self._set_parts(src.get("match", default_match), bool(src.get("gap", default_gap)))
        if state and "text" in state:
            self._pattern_edit.setText(str(state["text"]))
            self._set_free(bool(state.get("free", False)))
        else:
            self._pattern_edit.setText(self._build())

        if self._match_combo is not None:
            self._match_combo.currentIndexChanged.connect(self._on_parts_changed)
        self._gap_check.toggled.connect(self._on_parts_changed)
        self._pattern_edit.textEdited.connect(self._on_text_edited)
        self._pattern_edit.textChanged.connect(lambda _t: self._error_label.setVisible(False))
        self._pattern_edit.returnPressed.connect(self._on_ok)
        self._pattern_edit.setFocus()
        self._pattern_edit.selectAll()

    # --- 入力補助 ---
    def _set_parts(self, match: str, gap: bool) -> None:
        if self._match_combo is not None:
            idx = self._match_combo.findData(match)
            with QSignalBlocker(self._match_combo):
                self._match_combo.setCurrentIndex(max(idx, 0))
        with QSignalBlocker(self._gap_check):
            self._gap_check.setChecked(gap)

    def _set_free(self, free: bool) -> None:
        self._free = free
        self._free_label.setVisible(free)

    def _build(self) -> str:
        return build_pattern(self.match(), self.gap(), self._base, self._gap_base)

    def _on_parts_changed(self, *_args) -> None:
        self._set_free(False)
        self._pattern_edit.setText(self._build())

    def _on_text_edited(self, _text: str) -> None:
        """テキストボックスを手で編集したら「自由入力」状態にする。"""
        self._set_free(True)

    def _on_ok(self) -> None:
        error = validate_pii_pattern(self._pattern_edit.text())
        if error:
            self._error_label.setText(error)
            self._error_label.setVisible(True)
            return
        self.accept()

    # --- 結果 ---
    def match(self) -> str:
        """「一致のしかた」(``MATCH_*``)。この部品を出さない検出パターン側は部分一致。"""
        if self._match_combo is None:
            return MATCH_PARTIAL
        return str(self._match_combo.currentData())

    def gap(self) -> bool:
        return self._gap_check.isChecked()

    def is_free(self) -> bool:
        return self._free

    def pattern(self) -> str:
        return self._pattern_edit.text()

    def entity(self) -> str:
        if self._entity_combo is None:
            return ""
        return str(self._entity_combo.currentData())

    def state(self) -> dict:
        return {
            "entity": self.entity(),
            "match": self.match(),
            "gap": self.gap(),
            "free": self._free,
            "text": self.pattern(),
        }

    @staticmethod
    def ask(*args, **kwargs) -> "dict | None":
        """ダイアログを表示し、OKなら ``state()`` を、キャンセルなら None を返す。"""
        dialog = PiiPatternInputDialog(*args, **kwargs)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.state()


class ScopeChoiceDialog(QDialog):
    """「検出パターンに追加」「除外パターンに追加」の入力の後に開く、範囲選択の小さなダイアログ。

    語句(読み取り専用)と説明文を示し、「全ページ / このページだけ / しない」の
    3択(ボタン文言は呼び出し側が指定)から選ばせる。ダイアログを閉じた場合は
    「しない」扱い。``allow_back`` が True のときだけ「← 語句の入力に戻る」ボタンを
    出し、押すと ``SCOPE_BACK`` を返す(戻り先の入力ダイアログが無い呼び出しでは出さない)。
    """

    SCOPE_ALL = "all"
    SCOPE_PAGE = "page"
    SCOPE_NONE = "none"
    SCOPE_BACK = "back"
    BACK_LABEL = "← 語句の入力に戻る"

    def __init__(
        self,
        title: str,
        message: str,
        word: str,
        labels: "tuple[str, str, str]",
        parent: QWidget | None = None,
        allow_back: bool = False,
        *,
        pattern: "str | None" = None,
        text_display: "tuple[str, int]" = ("ellipsis", 3),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self._scope: str = self.SCOPE_NONE

        layout = QVBoxLayout(self)
        form = QFormLayout()
        # 語句(パターン)の表示は1か所だけ。設定「結果一覧の語句の表示」に従い、
        # 1行なら末尾「…」省略、折り返しなら最大行数まで折り返して超過分を「…」省略する。
        label, shown = scope_target(word, pattern)
        mode, max_lines = text_display
        self._text_edit = FittedTextLabel(shown, mode, max_lines)
        form.addRow(f"{label}:", self._text_edit)
        layout.addLayout(form)

        self._message_label = QLabel(message)
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        buttons = QHBoxLayout()
        self._back_btn: QPushButton | None = None
        if allow_back:
            self._back_btn = QPushButton(self.BACK_LABEL)
            self._back_btn.setAutoDefault(False)
            self._back_btn.clicked.connect(lambda _checked=False: self._choose(self.SCOPE_BACK))
            buttons.addWidget(self._back_btn)
            buttons.addStretch(1)
        self._all_btn = QPushButton(labels[0])
        self._page_btn = QPushButton(labels[1])
        self._none_btn = QPushButton(labels[2])
        for btn, scope in (
            (self._all_btn, self.SCOPE_ALL),
            (self._page_btn, self.SCOPE_PAGE),
            (self._none_btn, self.SCOPE_NONE),
        ):
            btn.clicked.connect(lambda _checked=False, sc=scope: self._choose(sc))
            buttons.addWidget(btn)
        self._all_btn.setDefault(True)
        layout.addLayout(buttons)

    def _choose(self, scope: str) -> None:
        self._scope = scope
        self.accept()

    def scope(self) -> str:
        return self._scope

    @staticmethod
    def ask(
        title: str,
        message: str,
        word: str,
        labels: "tuple[str, str, str]",
        parent: QWidget | None = None,
        allow_back: bool = False,
        *,
        pattern: "str | None" = None,
        text_display: "tuple[str, int]" = ("ellipsis", 3),
    ) -> str:
        """ダイアログを表示し、選ばれた範囲(``SCOPE_*``)を返す。閉じた場合は SCOPE_NONE。"""
        dialog = ScopeChoiceDialog(
            title, message, word, labels, parent, allow_back,
            pattern=pattern, text_display=text_display,
        )
        dialog.exec()
        return dialog.scope()


class PiiPanel(QFrame):
    """個人情報検出ドロワー。

    Signals
    -------
    open_changed(bool)
        ドロワーの開閉が切り替わったとき発火。
    detect_current_page_requested()
        「このページだけ検出」ボタン押下時。
    detect_all_pages_requested()
        「全ページを検出」ボタン押下時。
    mask_markup_tool_toggled(bool)
        「テキスト候補」ツール(手動でテキストを選択→塗りつぶし候補化)のON/OFF。
    mask_rect_tool_toggled(bool)
        「塗り四角」ツールのON/OFF。
    mask_ellipse_tool_toggled(bool)
        「塗り丸」ツールのON/OFF。
    remove_selected_requested()
        「削除」ボタン押下時、または結果一覧でDeleteキー押下時。
    add_detect_word_requested(str, str)
        結果一覧の右クリックメニュー「検出パターンに追加」。(text, 種類)。種類が空文字なら
        「詳細指定…」(入力ダイアログで種類・正規表現を選ぶ)、種類があれば簡易指定
        (入力ダイアログ無しで範囲選択へ。「手動」なら検出パターンには登録しない)。
    add_exclude_word_requested(str, bool)
        結果一覧の右クリックメニュー「除外パターンに追加」。(text, 簡易か)。
        True=簡易指定(完全一致で、入力ダイアログ無しで範囲選択へ)、False=詳細指定…。
    detect_manual_requested(str)
        手動扱いの候補としての検出要求(設定は保存しない)。右クリックメニューからは
        直接出さず、「検出パターンに追加」の種類「手動」から行う。
    settings_requested()
        「設定...」ボタン押下時。
    export_requested()
        「エクスポート...」ボタン押下時(黒塗りしたPDF/画像の書き出し)。
    result_activated(object)
        結果一覧の項目がクリックされたとき、対応する注釈データを伴って発火
        (``TextMarkupAnnotData`` または ``ShapeAnnotData``)。
    entity_visibility_changed(str, bool)
        「表示・検出する種別」のチェックボックスが操作されたとき (entity_type, checked)。
    mask_color_changed(object)
        「色」ボタンでダイアログから色が選ばれたとき。RGB(0.0-1.0)の3要素タプル。
    mask_transparency_changed(int, bool)
        「透明度」スライダが動いたとき (値, 確定したか)。ドラッグ中は確定=False
        (画面の即時反映だけ行い、保存はドラッグ終了時にする)。
    """

    open_changed = pyqtSignal(bool)
    detect_current_page_requested = pyqtSignal()
    detect_all_pages_requested = pyqtSignal()
    keep_existing_toggled = pyqtSignal(bool)
    mask_markup_tool_toggled = pyqtSignal(bool)
    mask_rect_tool_toggled = pyqtSignal(bool)
    mask_ellipse_tool_toggled = pyqtSignal(bool)
    remove_selected_requested = pyqtSignal()
    add_detect_word_requested = pyqtSignal(str, str)
    add_exclude_word_requested = pyqtSignal(str, bool)
    detect_manual_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()
    export_requested = pyqtSignal()
    result_activated = pyqtSignal(object)
    entity_visibility_changed = pyqtSignal(str, bool)
    mask_color_changed = pyqtSignal(object)
    mask_transparency_changed = pyqtSignal(int, bool)

    DRAWER_WIDTH = 340
    _MIN_TEXT_COLUMN_WIDTH = 60

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("piiDrawer")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._is_open = False
        self._collapsed_width = 32
        self._rows: list[PiiResultRow] = []
        self._mask_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._busy = False
        self._canvas_available = True
        self._manual_tools_enabled = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("◀")
        self._toggle_btn.setToolTip("個人情報検出")
        self._toggle_btn.setFixedWidth(32)
        self._toggle_btn.clicked.connect(self.toggle)
        layout.addWidget(self._toggle_btn)

        self._panel = QWidget()
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        panel_layout.addWidget(QLabel("個人情報検出"))

        # --- 検出実行 ---
        # 検出範囲は左から「全ページ」→「このページだけ」の順に並べる。追加検出
        # パターン登録ダイアログの3択(全ページ/このページだけ/登録のみ)と
        # 表記を揃え、範囲の選び方を一貫させる。
        detect_row = QHBoxLayout()
        self._detect_all_btn = QPushButton("全ページを検出")
        self._detect_all_btn.setToolTip("すべてのページを個人情報検出します。")
        self._detect_all_btn.clicked.connect(self.detect_all_pages_requested.emit)
        detect_row.addWidget(self._detect_all_btn)
        self._detect_current_btn = QPushButton("このページだけ検出")
        self._detect_current_btn.setToolTip("表示中のページだけを個人情報検出します。")
        self._detect_current_btn.clicked.connect(self.detect_current_page_requested.emit)
        detect_row.addWidget(self._detect_current_btn)
        panel_layout.addLayout(detect_row)

        self._keep_existing_check = QCheckBox("既存の結果を残して追加検出")
        self._keep_existing_check.setToolTip(
            "オンにすると、検出済みの塗りつぶし候補・手動追加分は消さずに、"
            "新しく見つかったものだけを追加します"
            "(同じページ・同じ位置・同じ語句のものは重複して追加しません)。\n"
            "オフの場合は従来通り、検出し直したページの結果を置き換えます。"
        )
        self._keep_existing_check.toggled.connect(self.keep_existing_toggled.emit)
        panel_layout.addWidget(self._keep_existing_check)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        panel_layout.addWidget(self._progress_bar)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: palette(dark);")
        panel_layout.addWidget(self._status_label)

        # --- 手動で塗りつぶし候補を追加 ---
        # 手動追加分の種別は常に「手動」(種別は選ばせない)。
        manual_group = QGroupBox("手動で塗りつぶし候補を追加")
        manual_layout = QVBoxLayout(manual_group)

        tools_row = QHBoxLayout()
        self._mask_markup_btn = QToolButton()
        self._mask_markup_btn.setText("テキスト候補")
        self._mask_markup_btn.setToolTip(
            "ページ上のテキストをドラッグ選択して塗りつぶし候補にします。\n"
            "先にテキストを選択してから押すと、その場で追加します。\n"
            "先に押すと連続モードになり、選択するたびに追加します(Esc で終了)。"
        )
        self._mask_markup_btn.setCheckable(True)
        self._mask_markup_btn.toggled.connect(self.mask_markup_tool_toggled.emit)
        tools_row.addWidget(self._mask_markup_btn)
        self._mask_rect_btn = QToolButton()
        self._mask_rect_btn.setText("塗り四角")
        self._mask_rect_btn.setToolTip(
            "ドラッグで塗りつぶし用の四角を描きます(文字の無い写真等にも使えます)。"
        )
        self._mask_rect_btn.setCheckable(True)
        self._mask_rect_btn.toggled.connect(self.mask_rect_tool_toggled.emit)
        tools_row.addWidget(self._mask_rect_btn)
        self._mask_ellipse_btn = QToolButton()
        self._mask_ellipse_btn.setText("塗り丸")
        self._mask_ellipse_btn.setToolTip(
            "ドラッグで塗りつぶし用の楕円を描きます(印影等にも使えます)。"
        )
        self._mask_ellipse_btn.setCheckable(True)
        self._mask_ellipse_btn.toggled.connect(self.mask_ellipse_tool_toggled.emit)
        tools_row.addWidget(self._mask_ellipse_btn)
        # ボタンを押してもフォーカスを奪わない(ページ上で選択済みのテキスト選択や
        # キー操作(Esc)の受け取り先を変えないため)。
        for btn in (self._mask_markup_btn, self._mask_rect_btn, self._mask_ellipse_btn):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        manual_layout.addLayout(tools_row)
        panel_layout.addWidget(manual_group)

        # --- 結果一覧 ---
        # 元の「検出結果・塗りつぶし対象」は何を指しているか分かりにくいとの
        # 要望を受け、一覧の実際の役割(自動検出+手動追加分をまとめた、
        # 「エクスポート時に塗りつぶされる対象そのもの」の一覧)が伝わる表記に
        # 変更した。表示する種別は下の「表示・検出する種別」で絞り込める。
        result_label = QLabel("塗りつぶし対象一覧")
        result_label.setToolTip(
            "自動検出された箇所と手動で追加した候補/図形の一覧です。\n"
            "ここに表示されている項目はすべて塗りつぶし(黒塗り/文字削除)の対象になります。\n"
            "不要な項目は選択して「削除」で一覧から外してください。"
        )
        panel_layout.addWidget(result_label)

        # 種別ごとの「表示・検出する」チェックボックス(3列)。自動検出の8種別は
        # 検出対象の設定そのもの、「手動」は手動追加分の表示設定。
        entity_group = QGroupBox("表示・検出する種別")
        entity_group.setToolTip(
            "チェックした種別だけを、一覧・ページ上・エクスポートの対象にし、検出も行います。\n"
            "チェックを外した種別は一覧・ページ上に出ず、クリックもできず、"
            "検出・エクスポートの対象外になります(PDF上の注釈は消えません)。"
        )
        entity_grid = QGridLayout(entity_group)
        entity_grid.setContentsMargins(8, 4, 8, 4)
        self._entity_checks: dict[str, QCheckBox] = {}
        for i, entity_type in enumerate(ENTITY_TYPES_WITH_MANUAL):
            check = QCheckBox(get_entity_type_name_ja(entity_type))
            check.setChecked(True)
            check.toggled.connect(
                lambda checked, et=entity_type: self.entity_visibility_changed.emit(et, checked)
            )
            self._entity_checks[entity_type] = check
            entity_grid.addWidget(check, i // 3, i % 3)
        panel_layout.addWidget(entity_group)

        # 塗りつぶしの色(全種別共通)と透明度。
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("色:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(40, 22)
        self._color_btn.setToolTip("塗りつぶしの色を選びます(全種別共通・エクスポートにも使われます)。")
        self._color_btn.clicked.connect(self._on_color_button_clicked)
        style_row.addWidget(self._color_btn)
        style_row.addSpacing(8)
        style_row.addWidget(QLabel("透明度:"))
        self._transparency_slider = QSlider(Qt.Orientation.Horizontal)
        self._transparency_slider.setRange(0, 100)
        self._transparency_slider.setValue(70)
        self._transparency_slider.setToolTip(
            "0%: 不透明(黒塗りエクスポートと同じベタ塗り)/ 100%: 完全に透明\n"
            "(ページ上の見た目のみ。エクスポートは常に不透明で塗りつぶします)"
        )
        self._transparency_slider.valueChanged.connect(self._on_transparency_value_changed)
        self._transparency_slider.sliderReleased.connect(self._on_transparency_released)
        style_row.addWidget(self._transparency_slider, 1)
        self._transparency_label = QLabel("70%")
        self._transparency_label.setMinimumWidth(36)
        style_row.addWidget(self._transparency_label)
        panel_layout.addLayout(style_row)
        self._apply_color_swatch()

        # 並び替えは列ヘッダのクリックで行う(同じ列の再クリックで昇順/降順を反転)。
        self._sort_field = "page"
        self._sort_ascending = True
        self._result_order_vertical = False  # ページ内の並び順(False=横書き)
        self._columns_user_sized = False  # ユーザーが列幅をドラッグしたら True(自動の幅合わせを止める)
        self._fitting_columns = False
        self._text_wrap = False  # 語句列: False=1行で末尾省略 / True=折り返し(上限行数まで)
        self._text_max_lines = 3
        self._result_tree = _ResultTree()
        self._result_tree.setObjectName("piiResultTree")
        self._result_tree.setColumnCount(3)
        self._result_tree.setHeaderLabels(["語句", "種別", "ページ"])
        self._result_tree.setRootIsDecorated(False)
        self._result_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._result_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._result_tree.itemClicked.connect(self._on_result_item_clicked)
        self._result_tree.itemSelectionChanged.connect(self._update_button_states)
        self._result_tree.customContextMenuRequested.connect(self._on_result_context_menu)
        self._result_tree.delete_pressed.connect(self._on_result_delete_pressed)
        self._result_tree.copy_pressed.connect(self.copy_results_to_clipboard)
        header = self._result_tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_result_header_clicked)
        header.sectionResized.connect(self._on_result_section_resized)
        header.setStretchLastSection(False)
        self._result_tree.resized.connect(self._fit_result_columns)
        self._update_sort_indicator()
        panel_layout.addWidget(self._result_tree, 1)

        # --- 操作ボタン ---
        # 全件削除は結果一覧で Ctrl+A → 「削除」で行える。
        # 削除・設定・エクスポートは1行に並べる(縦に3段積むと結果一覧が狭くなるため)。
        action_row = QHBoxLayout()
        self._remove_selected_btn = QPushButton("削除")
        self._remove_selected_btn.setToolTip("結果一覧で選択した項目を塗りつぶし対象から削除します。")
        self._remove_selected_btn.clicked.connect(self.remove_selected_requested.emit)
        action_row.addWidget(self._remove_selected_btn)

        self._settings_btn = QPushButton("設定...")
        self._settings_btn.clicked.connect(self.settings_requested.emit)
        action_row.addWidget(self._settings_btn)

        self._export_btn = QPushButton("エクスポート...")
        self._export_btn.setToolTip(
            "チェック中の種別の塗りつぶし対象を黒塗りし(下の文字も削除)、"
            "PDF・画像として書き出します。\n"
            "出力形式・解像度・圧縮などは、開くダイアログで選べます。"
        )
        self._export_btn.clicked.connect(self.export_requested.emit)
        action_row.addWidget(self._export_btn)
        panel_layout.addLayout(action_row)

        panel_layout.addStretch()
        layout.addWidget(self._panel)

        self.set_open(False)
        self._update_button_states()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._is_open

    def set_open(self, is_open: bool) -> None:
        is_open = bool(is_open)
        changed = is_open != self._is_open
        self._is_open = is_open
        self._panel.setVisible(is_open)
        self.setFixedWidth(self.DRAWER_WIDTH if is_open else self._collapsed_width)
        self._toggle_btn.setText("▶" if is_open else "◀")
        if changed:
            self.open_changed.emit(is_open)

    def toggle(self) -> None:
        self.set_open(not self._is_open)

    def use_external_toggle(self) -> None:
        """開閉トグルを外部ボタンに委譲する(BookmarksPanelと同じ規約)。"""
        self._collapsed_width = 0
        self._toggle_btn.hide()
        if not self._is_open:
            self.setFixedWidth(0)

    def set_busy(self, busy: bool, status: str = "") -> None:
        """検出実行中はボタンを無効化し、進捗表示を出す。"""
        self._busy = bool(busy)
        self._detect_all_btn.setEnabled(not busy)
        self._apply_canvas_state()
        self._progress_bar.setVisible(busy)
        if busy:
            self._progress_bar.setRange(0, 0)  # 総ページ数が分かるまでは不定進捗
        self._status_label.setText(status)

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(done)
        self._status_label.setText(f"検出中... ({done}/{total})")

    def set_status(self, status: str) -> None:
        """進捗表示を変えずにステータス行だけを書き換える。"""
        self._status_label.setText(status)

    def keep_existing_checked(self) -> bool:
        """「既存の結果を残して追加検出」チェックボックスの状態を返す。"""
        return self._keep_existing_check.isChecked()

    def set_keep_existing_checked(self, checked: bool) -> None:
        with self._signal_blockers(self._keep_existing_check):
            self._keep_existing_check.setChecked(bool(checked))

    # --- 種別チェックボックス ---
    def entity_visibility(self) -> dict[str, bool]:
        """種別 -> チェック状態(自動検出の8種別+手動)。"""
        return {et: check.isChecked() for et, check in self._entity_checks.items()}

    def set_entity_visibility(self, states: "dict[str, bool]") -> None:
        """設定値をチェックボックスへ反映する(シグナルは発火しない)。未指定の種別はオン。"""
        for entity_type, check in self._entity_checks.items():
            with self._signal_blockers(check):
                check.setChecked(bool(states.get(entity_type, True)))

    # --- 色・透明度 ---
    def mask_color(self) -> tuple[float, float, float]:
        return self._mask_color

    def mask_transparency(self) -> int:
        return int(self._transparency_slider.value())

    def set_mask_style(self, color: "tuple[float, float, float]", transparency: int) -> None:
        """色(RGB 0.0-1.0)と透明度(0-100)を反映する(シグナルは発火しない)。"""
        self._mask_color = (float(color[0]), float(color[1]), float(color[2]))
        self._apply_color_swatch()
        with self._signal_blockers(self._transparency_slider):
            self._transparency_slider.setValue(max(0, min(100, int(transparency))))
        self._transparency_label.setText(f"{self._transparency_slider.value()}%")

    # --- 手動ツール ---
    def set_manual_tools_enabled(self, enabled: bool) -> None:
        """手動追加ツール(テキスト候補/塗り四角/塗り丸)の有効/無効を切り替える。

        種別「手動」のチェックが外れている間は、追加しても見えないため無効にする。
        """
        self._manual_tools_enabled = bool(enabled)
        self._apply_canvas_state()

    def set_canvas_available(self, available: bool) -> None:
        """拡大表示(単ページ)の画面があるか。無い(ページ一覧)間は、ページ画面が要る操作
        (このページだけ検出/テキスト候補/塗り四角/塗り丸)を無効にする。"""
        self._canvas_available = bool(available)
        self._apply_canvas_state()

    def _apply_canvas_state(self) -> None:
        canvas = self._canvas_available
        self._detect_current_btn.setEnabled(canvas and not self._busy)
        tools_enabled = canvas and self._manual_tools_enabled
        for btn in (self._mask_markup_btn, self._mask_rect_btn, self._mask_ellipse_btn):
            btn.setEnabled(tools_enabled)
        hint = "" if canvas else "ページをダブルクリックして拡大表示すると使用できます"
        if not canvas:
            for btn in (self._detect_current_btn, self._mask_markup_btn,
                        self._mask_rect_btn, self._mask_ellipse_btn):
                if not hasattr(btn, "_base_tooltip"):
                    btn._base_tooltip = btn.toolTip()
                btn.setToolTip(hint)
        else:
            for btn in (self._detect_current_btn, self._mask_markup_btn,
                        self._mask_rect_btn, self._mask_ellipse_btn):
                if hasattr(btn, "_base_tooltip"):
                    btn.setToolTip(btn._base_tooltip)

    def set_mask_markup_tool_active(self, enabled: bool) -> None:
        """呼び出し側(mixin)の実際の作成モードにボタンのチェック状態を同期する。"""
        with self._signal_blockers(self._mask_markup_btn):
            self._mask_markup_btn.setChecked(bool(enabled))

    def set_mask_shape_tool_active(self, shape_type: "ShapeType | None") -> None:
        with self._signal_blockers(self._mask_rect_btn, self._mask_ellipse_btn):
            self._mask_rect_btn.setChecked(shape_type == ShapeType.RECTANGLE)
            self._mask_ellipse_btn.setChecked(shape_type == ShapeType.ELLIPSE)

    def set_results(self, rows: list[PiiResultRow]) -> None:
        """検出結果・塗りつぶし用図形の一覧を、現在の並び順設定で再描画する。"""
        self._rows = list(rows)
        self._rebuild_result_tree()
        self._update_button_states()

    def selected_results(self) -> list["TextMarkupAnnotData | ShapeAnnotData"]:
        items = self._result_tree.selectedItems()
        return [item.data(0, _ANNOT_ROLE).annot for item in items if item.data(0, _ANNOT_ROLE) is not None]

    def select_result(self, annot: object) -> bool:
        """``annot`` に対応する行だけを選択(アクティブ)にして、見える位置へスクロールする。

        ページ上で塗りつぶし候補をクリックしたときに、一覧の該当行へ追従させるために使う。
        一致は (種類, ページ, xref) で判定する。行が無ければ選択は変えず False を返す。
        ``result_activated`` は発火しない(ページ側の選択との往復を避ける)。
        """
        target = (type(annot), getattr(annot, "page_num", None), getattr(annot, "xref", None))
        tree = self._result_tree
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            row = item.data(0, _ANNOT_ROLE)
            if row is None:
                continue
            if (type(row.annot), row.annot.page_num, row.annot.xref) != target:
                continue
            tree.clearSelection()
            tree.setCurrentItem(item)  # 現在行+選択の両方を更新する
            tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            return True
        return False

    def copy_results_to_clipboard(self) -> None:
        """結果一覧をTSVとしてクリップボードへコピーする(Excelへそのまま貼り付け可)。

        選択行があれば選択行(画面上の並び順)、無ければ全行をコピーする。
        """
        rows = self._rows_for_copy()
        QGuiApplication.clipboard().setText(rows_to_tsv(rows))

    def _rows_for_copy(self) -> list[PiiResultRow]:
        tree = self._result_tree
        all_items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        selected = [item for item in all_items if item.isSelected()]
        items = selected or all_items
        rows: list[PiiResultRow] = []
        for item in items:
            row = item.data(0, _ANNOT_ROLE)
            if row is not None:
                rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _signal_blockers(self, *widgets):
        return _MultiSignalBlocker(widgets)

    def set_sort(self, field: str, ascending: bool = True) -> None:
        """並び替えキー("page" | "text" | "entity")と方向を設定して再描画する。"""
        self._sort_field = field if field in _SORT_FIELD_TO_COLUMN else "page"
        self._sort_ascending = bool(ascending)
        self._update_sort_indicator()
        self._rebuild_result_tree()

    def _on_result_header_clicked(self, column: int) -> None:
        """列ヘッダクリックで並び替える。

        同じ列を続けてクリックしたときは昇順/降順を反転し、別の列をクリック
        したときはその列の昇順に切り替える。
        """
        field = _COLUMN_TO_SORT_FIELD.get(column, "page")
        if field == self._sort_field:
            self.set_sort(field, not self._sort_ascending)
        else:
            self.set_sort(field, True)

    def _update_sort_indicator(self) -> None:
        order = Qt.SortOrder.AscendingOrder if self._sort_ascending else Qt.SortOrder.DescendingOrder
        self._result_tree.header().setSortIndicator(_SORT_FIELD_TO_COLUMN[self._sort_field], order)

    def _apply_color_swatch(self) -> None:
        r, g, b = (max(0, min(255, round(c * 255))) for c in self._mask_color)
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r}, {g}, {b}); border: 1px solid palette(mid); }}"
        )

    def _on_color_button_clicked(self) -> None:
        r, g, b = (max(0, min(255, round(c * 255))) for c in self._mask_color)
        chosen = QColorDialog.getColor(QColor(r, g, b), self, "塗りつぶしの色")
        if not chosen.isValid():
            return
        self._mask_color = (chosen.redF(), chosen.greenF(), chosen.blueF())
        self._apply_color_swatch()
        self.mask_color_changed.emit(self._mask_color)

    def _on_transparency_value_changed(self, value: int) -> None:
        self._transparency_label.setText(f"{value}%")
        # ドラッグ中は画面の即時反映だけ(確定=False)。キー操作/クリックでの
        # 変更のようにドラッグでない場合はその場で確定として通知する。
        self.mask_transparency_changed.emit(int(value), not self._transparency_slider.isSliderDown())

    def _on_transparency_released(self) -> None:
        self.mask_transparency_changed.emit(int(self._transparency_slider.value()), True)

    def _on_result_delete_pressed(self) -> None:
        if self._result_tree.selectedItems():
            self.remove_selected_requested.emit()

    def _sort_key(self, row: "PiiResultRow", order: "tuple[int, float]"):
        """並び替えキー。``order`` はページ内の読み順(``reading_order_keys``)。"""
        entity_ja = get_entity_type_name_ja(row.entity or "OTHER")
        # ページ内は語句順ではなく、文書上の出現位置(読み順)にする。
        if self._sort_field == "text":
            return (row.display_text, row.page_num, order, entity_ja)
        if self._sort_field == "entity":
            return (entity_ja, row.page_num, order, row.display_text)
        return (row.page_num, order, row.display_text, entity_ja)  # "page"(既定)

    def set_result_order_mode(self, mode: str) -> None:
        """ページ内の並び順("horizontal" 横書き | "vertical" 縦書き)を設定して再描画する。"""
        vertical = mode == "vertical"
        if vertical == self._result_order_vertical:
            return
        self._result_order_vertical = vertical
        self._rebuild_result_tree()

    def set_result_text_display(self, mode: str, max_lines: int = 3) -> None:
        """語句列の表示方法("ellipsis" 1行省略 | "wrap" 折り返し)と折り返しの最大行数を設定する。

        表示用テキストだけを変える。実データ(``_ANNOT_ROLE``/``row.text``)とコピーには影響しない。
        """
        wrap = mode == "wrap"
        max_lines = max(1, int(max_lines))
        if wrap == self._text_wrap and max_lines == self._text_max_lines:
            return
        self._text_wrap = wrap
        self._text_max_lines = max_lines
        self._rebuild_result_tree()

    def _on_result_section_resized(self, column: int, _old: int, _new: int) -> None:
        if not self._fitting_columns:
            # ユーザーが列幅をドラッグした。以降は自動の幅合わせをやめ、その幅を尊重する
            # (一覧に収まらなければ横スクロールになる)。
            self._columns_user_sized = True
        if column == 0 and self._text_wrap:
            self._result_tree.scheduleDelayedItemsLayout()  # 行の高さを再計算

    def _fit_result_columns(self) -> None:
        """既定の列幅: 「種別」「ページ」は内容幅、「語句」は残り幅いっぱいに伸縮させる。

        一覧がパネル幅に収まり、長い語句は「…」で省略(折り返しは折り返し)されて横スクロールが
        出ない。ユーザーが列幅をドラッグした後は何もしない(その幅を保つ)。
        """
        tree = self._result_tree
        if self._columns_user_sized or self._fitting_columns:
            return
        self._fitting_columns = True
        try:
            for col in (1, 2):
                tree.resizeColumnToContents(col)
            avail = tree.viewport().width()
            if avail <= 0:
                return  # まだ表示されていない(表示時のリサイズで合わせ直す)
            rest = avail - tree.columnWidth(1) - tree.columnWidth(2)
            tree.setColumnWidth(0, max(self._MIN_TEXT_COLUMN_WIDTH, rest))
        finally:
            self._fitting_columns = False
        if self._text_wrap:
            tree.scheduleDelayedItemsLayout()

    def _rebuild_result_tree(self) -> None:
        self._result_tree.clear()
        orders = reading_order_keys(self._rows, self._result_order_vertical)
        keyed = sorted(
            zip(self._rows, orders),
            key=lambda pair: self._sort_key(pair[0], pair[1]),
            reverse=not self._sort_ascending,
        )
        for row, _order in keyed:
            entity_ja = get_entity_type_name_ja(row.entity or "OTHER")
            shown = row.wrap_text if self._text_wrap else row.display_text
            item = QTreeWidgetItem([shown, entity_ja, f"p.{row.page_num + 1}"])
            if row.text and shown != row.text:
                item.setToolTip(0, row.text)  # 省略・整形前の全文
            item.setData(0, _ANNOT_ROLE, row)
            self._result_tree.addTopLevelItem(item)
        if self._text_wrap:
            self._result_tree.setItemDelegateForColumn(
                0, _WrapTextDelegate(self._result_tree, self._text_max_lines)
            )
        else:
            self._result_tree.setItemDelegateForColumn(0, None)
        self._fit_result_columns()

    def _on_result_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        row: PiiResultRow | None = item.data(0, _ANNOT_ROLE)
        if row is not None:
            self.result_activated.emit(row.annot)

    def _on_result_context_menu(self, pos) -> None:
        item = self._result_tree.itemAt(pos)
        if item is None:
            return
        row: PiiResultRow | None = item.data(0, _ANNOT_ROLE)
        if row is None:
            return
        menu = QMenu(self._result_tree)
        copy_action = menu.addAction("コピー")
        menu.addSeparator()
        # 検出パターン/除外パターンへの追加は、どちらもサブメニュー。「簡易」(入力ダイアログ無しで
        # すぐ範囲選択へ)と「詳細指定…」(入力ダイアログで正規表現を編集)の構成にそろえる
        # (手動追加分の行でも、語句があれば登録できる)。「手動」で検出したいときは
        # 検出パターン側の「手動(検出パターンには追加しない)」を選ぶ。
        has_text = bool(row.text)
        detect_menu = menu.addMenu("検出パターンに追加")
        detect_menu.menuAction().setEnabled(has_text)
        for entity_type in ENTITY_TYPES:
            act = detect_menu.addAction(get_entity_type_name_ja(entity_type))
            act.setData(("detect", entity_type))
        detect_menu.addAction(MANUAL_CHOICE_LABEL).setData(("detect", MANUAL_ENTITY_TYPE))
        detect_menu.addSeparator()
        detect_menu.addAction(DETAIL_ACTION_LABEL).setData(("detect", ""))
        exclude_menu = menu.addMenu("除外パターンに追加")
        exclude_menu.menuAction().setEnabled(has_text)
        exclude_menu.addAction(EXCLUDE_SIMPLE_LABEL).setData(("exclude", True))
        exclude_menu.addSeparator()
        exclude_menu.addAction(DETAIL_ACTION_LABEL).setData(("exclude", False))
        chosen = menu.exec(self._result_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is copy_action:
            self.copy_results_to_clipboard()
            return
        data = chosen.data()
        if not data:
            return
        kind, value = data
        if kind == "detect":
            self.add_detect_word_requested.emit(row.text, value)
        elif kind == "exclude":
            self.add_exclude_word_requested.emit(row.text, value)

    def _update_button_states(self) -> None:
        has_results = self._result_tree.topLevelItemCount() > 0
        self._export_btn.setEnabled(has_results)
        self._remove_selected_btn.setEnabled(bool(self._result_tree.selectedItems()))


def fit_wrapped_text(fm: QFontMetrics, text: str, width: int, max_lines: int) -> str:
    """``width`` px で折り返したとき ``max_lines`` 行に収まるよう、超える分を「…」で省略する。"""
    if width <= 0:
        return text
    flags = int(Qt.TextFlag.TextWordWrap) | int(Qt.TextFlag.TextWrapAnywhere)
    limit = fm.lineSpacing() * max_lines

    def fits(t: str) -> bool:
        return fm.boundingRect(QRect(0, 0, width, 100000), flags, t).height() <= limit

    if fits(text):
        return text
    lo, hi = 0, len(text)  # 「text[:lo] + …」が収まる最大の lo を探す
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(text[:mid] + "…"):
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


class _WrapTextDelegate(QStyledItemDelegate):
    """「語句」列を最大 ``max_lines`` 行で折り返して描画する(超過分は「…」)。"""

    _MARGIN = 8  # 左右の余白の合計(px)

    def __init__(self, view: QTreeWidget, max_lines: int) -> None:
        super().__init__(view)
        self._view = view
        self.max_lines = max_lines

    def _fitted(self, option: QStyleOptionViewItem, text: str) -> str:
        width = self._view.columnWidth(0) - self._MARGIN
        return fit_wrapped_text(QFontMetrics(option.font), text, width, self.max_lines)

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = self._fitted(opt, opt.text)
        opt.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        style = opt.widget.style() if opt.widget else self._view.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

    def sizeHint(self, option, index) -> QSize:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        fm = QFontMetrics(opt.font)
        text = self._fitted(opt, opt.text)
        flags = int(Qt.TextFlag.TextWordWrap) | int(Qt.TextFlag.TextWrapAnywhere)
        width = max(1, self._view.columnWidth(0) - self._MARGIN)
        h = fm.boundingRect(QRect(0, 0, width, 100000), flags, text).height()
        return QSize(self._view.columnWidth(0), max(h, fm.lineSpacing()) + 6)


class _ResultTree(QTreeWidget):
    """結果一覧。Ctrl+A / Ctrl+C / Delete をウィンドウのショートカットより優先して受け取る。

    ページ編集ウィンドウには Ctrl+A(全ページ選択)・Delete(ページ削除)などの
    ウィンドウショートカットがあり、そのままでは一覧にフォーカスがあっても
    そちらが発火してしまう。ShortcutOverride を受理して一覧側で処理する。
    """

    delete_pressed = pyqtSignal()
    copy_pressed = pyqtSignal()
    resized = pyqtSignal()

    def viewportEvent(self, event) -> bool:
        handled = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize:
            self.resized.emit()  # 縦スクロールバーの出入りによる幅の変化も含む
        return handled

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and (
            event.matches(QKeySequence.StandardKey.SelectAll)
            or event.matches(QKeySequence.StandardKey.Delete)
            or event.matches(QKeySequence.StandardKey.Copy)
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Delete):
            self.delete_pressed.emit()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _MultiSignalBlocker:
    """with構文で複数ウィジェットのシグナルを一括ブロックする(QSignalBlockerの複数版)。"""

    def __init__(self, widgets) -> None:
        self._widgets = list(widgets)
        self._blockers: list = []

    def __enter__(self) -> None:
        self._blockers = [QSignalBlocker(w) for w in self._widgets]
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        self._blockers = []
