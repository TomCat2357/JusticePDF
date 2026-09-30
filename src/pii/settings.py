"""個人情報検出の設定（JusticePDF側で永続化）。

設定項目・UI 構成は PresidioPDF の設定ダイアログ
(``src/gui_pyqt/views/config_dialog.py``)に倣うが、保存先は JusticePDF の
既存の ``QSettings`` 慣習(``src.utils.app_settings``と同じ"<カテゴリ>/<項目>"
キー命名)に合わせる。JSON化が必要な複合値(リスト/辞書)は文字列として保存する。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from dataclasses import dataclass, field, replace as dataclass_replace

from PyQt6.QtCore import QSettings

from src.pii.engines import default_enabled_engines
from src.pii.entity_types import (
    ENTITY_TYPES,
    ENTITY_TYPES_WITH_MANUAL,
    HIGHLIGHT_COLORS,
    MANUAL_ENTITY_TYPE,
)

logger = logging.getLogger(__name__)

_PREFIX = "pii/"


def now_iso() -> str:
    """追加日時の保存形式(秒精度のローカル時刻 ISO 8601、例 ``2026-09-30T11:23:45``)。"""
    return datetime.now().isoformat(timespec="seconds")


def parse_added_at(value) -> datetime | None:
    """保存された追加日時を datetime へ。未記録・不正な値は None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_added_at(value) -> str:
    """一覧表示用の追加日時(``2026-09-30 11:23``)。未記録は空文字。"""
    parsed = parse_added_at(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else ""


def pattern_key(entity_type: str, regex: str) -> str:
    """検出パターン (種別, 正規表現) を追加日時辞書のキー文字列にする。"""
    return entity_type + "\t" + regex


# 右クリックから語句を検出/除外パターンへ登録するときの空白の扱い(キー, 表示名)。
# 既定は ``optional``。``literal_to_pattern`` が各キーを正規表現へ変換する。
WHITESPACE_MODES: tuple[tuple[str, str], ...] = (
    ("literal", "そのまま(完全一致)"),
    ("optional", "空白の有無を問わない"),
    ("flexible", "空白の種類・個数を問わない(空白必須)"),
    ("any_gap", "すべての文字の間で空白を許す"),
)
DEFAULT_WHITESPACE_MODE = "optional"
_WHITESPACE_MODE_KEYS = tuple(key for key, _ in WHITESPACE_MODES)


def normalize_whitespace_mode(value) -> str:
    """不正な空白モードは既定値(``optional``)にする。"""
    value = str(value or "")
    return value if value in _WHITESPACE_MODE_KEYS else DEFAULT_WHITESPACE_MODE


def literal_to_pattern(text: str, mode: str = DEFAULT_WHITESPACE_MODE) -> str:
    r"""語句を、空白の扱い(``mode``)に応じた正規表現の本体(アンカーなし)にする。

    前後の空白は除き、記号はエスケープする。空白の区切りは先に分割してから
    個別にエスケープして結合するため、``\ `` は残らない(``\s*`` / ``\s+`` になる)。

    - ``literal``: そのまま(空白も文字どおり)
    - ``optional``: 空白の連なりを ``\s*``(有無を問わない)
    - ``flexible``: 空白の連なりを ``\s+``(種類・個数を問わないが必須)
    - ``any_gap``: 空白を捨て、すべての文字の間に ``\s*``
    不明な ``mode`` は既定(``optional``)として扱う。
    """
    stripped = text.strip()
    mode = normalize_whitespace_mode(mode)
    if mode == "literal":
        return re.escape(stripped)
    if mode == "any_gap":
        chars = [re.escape(ch) for ch in stripped if not ch.isspace()]
        return r"\s*".join(chars)
    parts = [re.escape(piece) for piece in re.split(r"\s+", stripped) if piece]
    return (r"\s*" if mode == "optional" else r"\s+").join(parts)


def exact_match_pattern(text: str, mode: str = "literal") -> str:
    """語句に完全一致する除外パターン(``^語句$``、記号はエスケープ済み)を返す。

    除外パターンは ``re.search``(部分一致)で使われるため、旧「除外語」(完全一致)の
    移行や右クリック「除外パターンに追加」ではこの形で登録する。``mode`` は
    ``literal_to_pattern`` と同じ空白の扱い(既定は空白をそのまま扱う ``literal``)。
    """
    return "^" + literal_to_pattern(text, mode) + "$"


def _key(name: str) -> str:
    return _PREFIX + name


def _load_json(settings: QSettings, name: str, default):
    raw = settings.value(_key(name), "", type=str)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("設定値のJSON解析に失敗: %s", name)
        return default


def _save_json(settings: QSettings, name: str, value) -> None:
    settings.setValue(_key(name), json.dumps(value, ensure_ascii=False))


_DISPLAY_MODES = ("mark", "black", "hidden")

DEFAULT_MASK_COLOR: tuple[float, float, float] = (0.0, 0.0, 0.0)
DEFAULT_MASK_TRANSPARENCY = 70
# OCRで認識した文字を画面に重ねて表示するときの既定(色は赤、透明度50%)。
DEFAULT_OCR_TEXT_COLOR: tuple[float, float, float] = (1.0, 0.0, 0.0)
DEFAULT_OCR_TEXT_TRANSPARENCY = 50


def _clamp_transparency(value, default: int = DEFAULT_MASK_TRANSPARENCY) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_mask_color(
    value, default: tuple[float, float, float] = DEFAULT_MASK_COLOR
) -> tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            r, g, b = (max(0.0, min(1.0, float(c))) for c in value)
            return (r, g, b)
        except (TypeError, ValueError):
            pass
    return default


def _normalize_display_mode(value) -> str:
    value = str(value or "")
    return value if value in _DISPLAY_MODES else "mark"


@dataclass(slots=True)
class PiiSettings:
    """個人情報検出の設定一式。"""

    # 検出対象エンティティ（未設定キーは有効とみなす）
    enabled_entities: dict[str, bool] = field(
        default_factory=lambda: {et: True for et in ENTITY_TYPES}
    )
    # 旧「種別別のマーカー色」(RGB 0.0-1.0)。塗りつぶし候補の色は単一の
    # ``mask_color`` に統一したため現在の UI は使わないが、旧設定を読み込める
    # よう(および ``color_for`` の後方互換のため)フィールドは残している。
    colors: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    # 除外設定
    text_exclusions_regex: list[str] = field(default_factory=list)
    entity_exclusions: dict[str, list[str]] = field(default_factory=dict)
    # 検出パターン(旧称: 追加検出パターン): (entity_type, regex) のリスト（左が高優先）。
    # 除外パターン(text_exclusions_regex)に一致した結果は、検出パターン由来でも除外される。
    additional_patterns: list[tuple[str, str]] = field(default_factory=list)
    # 追加日時(ISO文字列)。リスト本体(旧形式の文字列/タプルのまま)とは別に、
    # 除外パターンは ``{パターン: 日時}``、検出パターンは ``{pattern_key(): 日時}`` で持つ。
    # 旧設定(日時なし)はキーが無いだけで、そのまま読み込める。
    text_exclusions_added_at: dict[str, str] = field(default_factory=dict)
    additional_patterns_added_at: dict[str, str] = field(default_factory=dict)
    # 追加の人名リスト（PERSON として検出）
    custom_names: list[str] = field(default_factory=list)
    # 重複除去（src.pii.dedupe.dedupe_detections への橋渡し）。
    # 個々の認識器(正規表現/形態素/日時)は互いの重複を除去しないため、
    # 既定で有効にして「同じ範囲に複数のハイライトが重なる」見た目を避ける。
    dedupe_enabled: bool = True
    dedupe_overlap: str = "overlap"  # overlap | exact | contain
    dedupe_keep: str = "widest"  # widest | first | last | entity-order
    entity_priority_order: list[str] = field(
        default_factory=lambda: [
            "INDIVIDUAL_NUMBER",
            "PHONE_NUMBER",
            "PERSON",
            "LOCATION",
            "DATE_TIME",
            "YEAR",
            "PROPER_NOUN",
        ]
    )
    # SudachiPy
    sudachi_dict_type: str = "core"  # core | full | small
    sudachi_split_mode: str = "C"  # A | B | C
    # OCR（rapidocr が導入されている場合のみ実際に使われる）
    ocr_enabled: bool = False
    ocr_dpi: int = 300
    # OCRで認識した文字を、画面(ズームビュー)に重ねて表示するか。表示だけの設定で、
    # PDFへ埋め込んだテキスト(見えない注釈)自体は変えない。色・透明度は再OCRなしで
    # いつでも変えられる(次に描画するときに反映される)。
    ocr_text_visible: bool = False
    ocr_text_color: tuple[float, float, float] = DEFAULT_OCR_TEXT_COLOR
    # 認識文字の透明度(0=不透明 / 100=完全に透明)。
    ocr_text_transparency: int = DEFAULT_OCR_TEXT_TRANSPARENCY
    # 検出エンジン(認識器)ごとのON/OFF。キーは src.pii.engines.ENGINE_KEYS。
    enabled_engines: dict[str, bool] = field(default_factory=default_enabled_engines)
    # 検出ボタン押下時、既存の検出結果・手動追加分を消さずに新規検出分だけ
    # 追加するかどうか(オフなら従来通り、検出し直したページの既存結果を置換)。
    keep_existing_on_detect: bool = False
    # 旧「表示」コンボ("mark" | "black" | "hidden")。UI では使わなくなった
    # (色と透明度に置き換え、「非表示」は種別チェックボックスで代替)。旧設定から
    # ``mask_transparency`` を移行するためだけに読み込む。
    display_mode: str = "mark"
    # 塗りつぶし候補・図形の色(全種別共通、RGB 0.0-1.0)。
    mask_color: tuple[float, float, float] = DEFAULT_MASK_COLOR
    # 塗りつぶし候補の透明度(0=不透明のベタ塗り / 100=完全に透明)。
    mask_transparency: int = DEFAULT_MASK_TRANSPARENCY
    # 「手動」種別(手動で追加した候補・図形)を一覧/ページ上で表示・出力の
    # 対象にするか。自動検出の8種別は ``enabled_entities`` が持つ。
    manual_visible: bool = True
    # 右クリックで語句を検出/除外パターンへ登録するときの空白の扱い
    # (``WHITESPACE_MODES`` のキー。``literal_to_pattern`` 参照)。
    pattern_whitespace_mode: str = DEFAULT_WHITESPACE_MODE

    def add_exclusion(self, pattern: str, added_at: str | None = None) -> bool:
        """除外パターンを追加し追加日時を記録する。すでにあれば何もせず False。"""
        if not pattern or pattern in self.text_exclusions_regex:
            return False
        self.text_exclusions_regex.append(pattern)
        self.text_exclusions_added_at[pattern] = added_at or now_iso()
        return True

    def add_additional_pattern(
        self, entity_type: str, regex: str, added_at: str | None = None
    ) -> bool:
        """検出パターンを追加し追加日時を記録する。すでにあれば何もせず False。"""
        entry = (entity_type, regex)
        if not entity_type or not regex or entry in self.additional_patterns:
            return False
        self.additional_patterns.append(entry)
        self.additional_patterns_added_at[pattern_key(entity_type, regex)] = (
            added_at or now_iso()
        )
        return True

    def replace_exclusion(self, old: str, new: str, added_at: str | None = None) -> bool:
        """除外パターン ``old`` を ``new`` に置き換える(一覧上の位置は維持、追加日時は更新)。

        ``old`` が無い・``new`` が空・``new`` が ``old`` と同じ・すでに他の項目にある
        (重複になる)ときは何もせず False。
        """
        if not new or new == old or old not in self.text_exclusions_regex:
            return False
        if new in self.text_exclusions_regex:
            return False
        self.text_exclusions_regex[self.text_exclusions_regex.index(old)] = new
        self.text_exclusions_added_at.pop(old, None)
        self.text_exclusions_added_at[new] = added_at or now_iso()
        return True

    def replace_additional_pattern(
        self,
        old: tuple[str, str],
        new: tuple[str, str],
        added_at: str | None = None,
    ) -> bool:
        """検出パターン ``old`` を ``new`` に置き換える(位置は維持、追加日時は更新)。

        ``old`` が無い・``new`` の種別/正規表現が空・同じ内容・すでに他の項目にある
        (重複になる)ときは何もせず False。
        """
        old = (old[0], old[1])
        new = (new[0], new[1])
        if not new[0] or not new[1] or new == old or old not in self.additional_patterns:
            return False
        if new in self.additional_patterns:
            return False
        self.additional_patterns[self.additional_patterns.index(old)] = new
        self.additional_patterns_added_at.pop(pattern_key(*old), None)
        self.additional_patterns_added_at[pattern_key(*new)] = added_at or now_iso()
        return True

    def prune_added_at(self) -> None:
        """一覧から消えたパターンの追加日時を捨てる(削除後の残骸を保存しない)。"""
        live = set(self.text_exclusions_regex)
        self.text_exclusions_added_at = {
            k: v for k, v in self.text_exclusions_added_at.items() if k in live
        }
        live_keys = {pattern_key(e, rx) for e, rx in self.additional_patterns}
        self.additional_patterns_added_at = {
            k: v for k, v in self.additional_patterns_added_at.items() if k in live_keys
        }

    def is_entity_enabled(self, entity_type: str) -> bool:
        return bool(self.enabled_entities.get(entity_type, True))

    def is_entity_visible(self, entity_type: str) -> bool:
        """種別が「表示・検出する」状態か(自動検出8種別は enabled_entities、手動は manual_visible)。"""
        if entity_type == MANUAL_ENTITY_TYPE:
            return bool(self.manual_visible)
        return self.is_entity_enabled(entity_type)

    def hidden_entities(self) -> set[str]:
        """非表示(チェックが外れている)種別の集合。手動も含む。"""
        return {et for et in ENTITY_TYPES_WITH_MANUAL if not self.is_entity_visible(et)}

    @property
    def mask_opacity(self) -> float:
        """塗りつぶしの不透明度(0.0-1.0)。透明度0 -> 1.0、透明度100 -> 0.0。"""
        return (100 - _clamp_transparency(self.mask_transparency)) / 100.0

    @property
    def ocr_text_opacity(self) -> float:
        """認識文字の不透明度(0.0-1.0)。透明度0 -> 1.0、透明度100 -> 0.0。"""
        return (100 - _clamp_transparency(
            self.ocr_text_transparency, DEFAULT_OCR_TEXT_TRANSPARENCY
        )) / 100.0

    def is_engine_enabled(self, engine_key: str) -> bool:
        return bool(self.enabled_engines.get(engine_key, False))

    def enabled_entity_list(self) -> list[str]:
        return [et for et in ENTITY_TYPES if self.is_entity_enabled(et)]

    def color_for(self, entity_type: str) -> tuple[float, float, float]:
        color = self.colors.get(entity_type)
        if color is not None:
            return (float(color[0]), float(color[1]), float(color[2]))
        default = HIGHLIGHT_COLORS.get(entity_type, [0.9, 0.9, 0.9])
        return (float(default[0]), float(default[1]), float(default[2]))

    def to_config_overrides(self) -> dict:
        """``src.pii.config_manager.ConfigManager`` に渡す overrides 辞書を組み立てる。"""
        custom_recognizers: dict[str, dict] = {}
        by_entity: dict[str, list[str]] = {}
        for entity_type, regex in self.additional_patterns:
            if entity_type and regex:
                by_entity.setdefault(entity_type, []).append(regex)
        for i, (entity_type, patterns) in enumerate(by_entity.items()):
            custom_recognizers[f"pii_settings_{i}"] = {
                "enabled": True,
                "entity_type": entity_type,
                "patterns": [{"regex": rx} for rx in patterns],
            }

        return {
            "enabled_entities": dict(self.enabled_entities),
            "custom_recognizers": custom_recognizers,
            "custom_names": {
                "name_list": list(self.custom_names),
                "name_patterns": [],
                "enabled": bool(self.custom_names),
                "use_with_auto_detection": True,
            },
            "exclusions": {
                "text_exclusions_regex": list(self.text_exclusions_regex),
                "entity_exclusions": {
                    k: list(v) for k, v in self.entity_exclusions.items()
                },
            },
            "nlp": {
                "sudachi_dict_type": self.sudachi_dict_type,
                "sudachi_split_mode": self.sudachi_split_mode,
            },
            "deduplication": {
                "enabled": self.dedupe_enabled,
                "method": self.dedupe_overlap,
                "entity_priority_order": list(self.entity_priority_order),
            },
            "engines": dict(self.enabled_engines),
        }

    @staticmethod
    def load(settings: QSettings | None = None) -> "PiiSettings":
        """QSettings から設定を読み込む(未設定項目は既定値)。"""
        s = settings or QSettings()
        default = PiiSettings()
        enabled_entities = _load_json(s, "enabled_entities", None)
        colors_raw = _load_json(s, "colors", None)
        colors: dict[str, tuple[float, float, float]] = {}
        if isinstance(colors_raw, dict):
            for k, v in colors_raw.items():
                if isinstance(v, list) and len(v) == 3:
                    colors[k] = (float(v[0]), float(v[1]), float(v[2]))
        additional_patterns_raw = _load_json(s, "additional_patterns", None)
        additional_patterns = [
            (str(item[0]), str(item[1]))
            for item in (additional_patterns_raw or [])
            if isinstance(item, (list, tuple)) and len(item) == 2
        ]
        entity_exclusions_raw = _load_json(s, "entity_exclusions", None)
        entity_exclusions = {
            str(k): [str(x) for x in v]
            for k, v in (entity_exclusions_raw or {}).items()
        } if isinstance(entity_exclusions_raw, dict) else {}

        enabled_engines_raw = _load_json(s, "enabled_engines", None)
        enabled_engines = dict(default.enabled_engines)
        if isinstance(enabled_engines_raw, dict):
            # 削除済みエンジン(旧GiNZA/Janome等)のキーは読み捨てる。
            enabled_engines.update(
                {
                    str(k): bool(v)
                    for k, v in enabled_engines_raw.items()
                    if str(k) in enabled_engines
                }
            )

        # 追加日時。旧設定には無いので、無ければ空(表示は空欄)。
        def _load_added_at(name: str) -> dict[str, str]:
            raw = _load_json(s, name, None)
            if not isinstance(raw, dict):
                return {}
            return {str(k): str(v) for k, v in raw.items() if parse_added_at(v)}

        text_exclusions_added_at = _load_added_at("text_exclusions_added_at")
        additional_patterns_added_at = _load_added_at("additional_patterns_added_at")

        # 旧「除外ワード(部分一致)」は除外パターン(re.search=部分一致)と機能が
        # 重複していたため廃止した。保存済みの除外ワードは記号をエスケープして
        # 除外パターンへ移し、同じ除外が効き続けるようにする。
        text_exclusions_regex = [
            str(x) for x in (_load_json(s, "text_exclusions_regex", []) or [])
        ]
        migrated_at = now_iso()  # 移行した項目の追加日時(=移行した時刻)
        for word in _load_json(s, "text_exclusions", []) or []:
            escaped = re.escape(str(word))
            if word and escaped not in text_exclusions_regex:
                text_exclusions_regex.append(escaped)
                text_exclusions_added_at[escaped] = migrated_at

        # 旧「除外語」(完全一致)も除外パターンへ移す。除外パターンは部分一致
        # (re.search)なので、完全一致の意味を保つため ``^語句$`` にする。
        # 検出語の前後の空白は除いて比べていたため、語句も strip してから移す。
        excluded_words_raw = _load_json(s, "excluded_words", None)
        if isinstance(excluded_words_raw, list):
            for word in excluded_words_raw:
                word = str(word).strip()
                if not word:
                    continue
                anchored = exact_match_pattern(word)
                if anchored not in text_exclusions_regex:
                    text_exclusions_regex.append(anchored)
                    text_exclusions_added_at[anchored] = migrated_at

        display_mode = _normalize_display_mode(
            s.value(_key("display_mode"), default.display_mode, type=str)
        )
        # 旧設定からの移行: 新キーが無く旧「黒塗り」モードだった場合は透明度0
        # (不透明のベタ塗り)、それ以外は既定値にする。
        if s.contains(_key("mask_transparency")):
            mask_transparency = _clamp_transparency(
                s.value(_key("mask_transparency"), default.mask_transparency, type=int)
            )
        elif display_mode == "black":
            mask_transparency = 0
        else:
            mask_transparency = default.mask_transparency

        return PiiSettings(
            enabled_entities=(
                {str(k): bool(v) for k, v in enabled_entities.items()}
                if isinstance(enabled_entities, dict) and enabled_entities
                else dict(default.enabled_entities)
            ),
            colors=colors,
            text_exclusions_regex=text_exclusions_regex,
            entity_exclusions=entity_exclusions,
            additional_patterns=additional_patterns,
            text_exclusions_added_at=text_exclusions_added_at,
            additional_patterns_added_at=additional_patterns_added_at,
            custom_names=list(_load_json(s, "custom_names", []) or []),
            dedupe_enabled=bool(
                s.value(_key("dedupe_enabled"), default.dedupe_enabled, type=bool)
            ),
            dedupe_overlap=str(s.value(_key("dedupe_overlap"), default.dedupe_overlap, type=str)),
            dedupe_keep=str(s.value(_key("dedupe_keep"), default.dedupe_keep, type=str)),
            entity_priority_order=list(
                _load_json(s, "entity_priority_order", default.entity_priority_order)
                or default.entity_priority_order
            ),
            sudachi_dict_type=str(
                s.value(_key("sudachi_dict_type"), default.sudachi_dict_type, type=str)
            ),
            sudachi_split_mode=str(
                s.value(_key("sudachi_split_mode"), default.sudachi_split_mode, type=str)
            ),
            ocr_enabled=bool(s.value(_key("ocr_enabled"), False, type=bool)),
            ocr_dpi=int(s.value(_key("ocr_dpi"), default.ocr_dpi, type=int)),
            ocr_text_visible=bool(s.value(_key("ocr_text_visible"), False, type=bool)),
            ocr_text_color=_normalize_mask_color(
                _load_json(s, "ocr_text_color", None), DEFAULT_OCR_TEXT_COLOR
            ),
            ocr_text_transparency=_clamp_transparency(
                s.value(
                    _key("ocr_text_transparency"),
                    DEFAULT_OCR_TEXT_TRANSPARENCY,
                    type=int,
                ),
                DEFAULT_OCR_TEXT_TRANSPARENCY,
            ),
            enabled_engines=enabled_engines,
            keep_existing_on_detect=bool(
                s.value(_key("keep_existing_on_detect"), False, type=bool)
            ),
            display_mode=display_mode,
            mask_color=_normalize_mask_color(_load_json(s, "mask_color", None)),
            mask_transparency=mask_transparency,
            manual_visible=bool(s.value(_key("manual_visible"), True, type=bool)),
            pattern_whitespace_mode=normalize_whitespace_mode(
                s.value(
                    _key("pattern_whitespace_mode"),
                    default.pattern_whitespace_mode,
                    type=str,
                )
            ),
        )

    def save(self, settings: QSettings | None = None) -> None:
        """QSettings へ設定を保存する。"""
        s = settings or QSettings()
        _save_json(s, "enabled_entities", self.enabled_entities)
        _save_json(s, "colors", {k: list(v) for k, v in self.colors.items()})
        self.prune_added_at()
        _save_json(s, "text_exclusions_regex", self.text_exclusions_regex)
        _save_json(s, "text_exclusions_added_at", self.text_exclusions_added_at)
        _save_json(s, "additional_patterns_added_at", self.additional_patterns_added_at)
        # 旧「除外ワード」は load 時に除外パターンへ移行済みなので消しておく
        # (残すと次回 load で再移行され、削除した除外パターンが復活してしまう)。
        s.remove(_key("text_exclusions"))
        _save_json(s, "entity_exclusions", self.entity_exclusions)
        # 旧「除外語」も load 時に除外パターン(^語句$)へ移行済みなので消しておく。
        s.remove(_key("excluded_words"))
        _save_json(s, "additional_patterns", [list(p) for p in self.additional_patterns])
        _save_json(s, "custom_names", self.custom_names)
        s.setValue(_key("dedupe_enabled"), self.dedupe_enabled)
        s.setValue(_key("dedupe_overlap"), self.dedupe_overlap)
        s.setValue(_key("dedupe_keep"), self.dedupe_keep)
        _save_json(s, "entity_priority_order", self.entity_priority_order)
        s.setValue(_key("sudachi_dict_type"), self.sudachi_dict_type)
        s.setValue(_key("sudachi_split_mode"), self.sudachi_split_mode)
        s.setValue(_key("ocr_enabled"), self.ocr_enabled)
        s.setValue(_key("ocr_dpi"), self.ocr_dpi)
        s.setValue(_key("ocr_text_visible"), self.ocr_text_visible)
        _save_json(
            s,
            "ocr_text_color",
            list(_normalize_mask_color(self.ocr_text_color, DEFAULT_OCR_TEXT_COLOR)),
        )
        s.setValue(
            _key("ocr_text_transparency"),
            _clamp_transparency(self.ocr_text_transparency, DEFAULT_OCR_TEXT_TRANSPARENCY),
        )
        _save_json(s, "enabled_engines", self.enabled_engines)
        s.setValue(_key("keep_existing_on_detect"), self.keep_existing_on_detect)
        s.setValue(_key("display_mode"), self.display_mode)
        _save_json(s, "mask_color", list(_normalize_mask_color(self.mask_color)))
        s.setValue(_key("mask_transparency"), _clamp_transparency(self.mask_transparency))
        s.setValue(_key("manual_visible"), self.manual_visible)
        s.setValue(
            _key("pattern_whitespace_mode"),
            normalize_whitespace_mode(self.pattern_whitespace_mode),
        )

    def copy(self) -> "PiiSettings":
        """独立編集用のディープコピーを返す(可変フィールドの参照共有を避ける)。

        ``dataclasses.replace`` は指定しなかったフィールドを浅くコピーするだけ
        なので、dict/list フィールド(enabled_entities 等)は元のインスタンスと
        同じオブジェクトを共有してしまう。設定ダイアログはコピー上で編集し、
        キャンセル時に元の設定を汚さないことを前提にしているため、
        可変フィールドは明示的に複製する。
        """
        return dataclass_replace(
            self,
            enabled_entities=dict(self.enabled_entities),
            colors=dict(self.colors),
            text_exclusions_regex=list(self.text_exclusions_regex),
            entity_exclusions={k: list(v) for k, v in self.entity_exclusions.items()},
            additional_patterns=list(self.additional_patterns),
            text_exclusions_added_at=dict(self.text_exclusions_added_at),
            additional_patterns_added_at=dict(self.additional_patterns_added_at),
            custom_names=list(self.custom_names),
            entity_priority_order=list(self.entity_priority_order),
            enabled_engines=dict(self.enabled_engines),
        )
