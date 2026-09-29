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


def _clamp_transparency(value) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_MASK_TRANSPARENCY


def _normalize_mask_color(value) -> tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            r, g, b = (max(0.0, min(1.0, float(c))) for c in value)
            return (r, g, b)
        except (TypeError, ValueError):
            pass
    return DEFAULT_MASK_COLOR


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
    # 除外語: 種別を問わない完全一致の除外語(前後の空白は無視)。追加パターンに
    # 登録した語句でも、除外語に入っていれば検出されない(除外が追加より優先)。
    excluded_words: list[str] = field(default_factory=list)
    # 追加検出パターン: (entity_type, regex) のリスト（左が高優先）
    additional_patterns: list[tuple[str, str]] = field(default_factory=list)
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
                "excluded_words": list(self.excluded_words),
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

        excluded_words_raw = _load_json(s, "excluded_words", None)
        excluded_words = (
            [str(x) for x in excluded_words_raw if str(x)]
            if isinstance(excluded_words_raw, list)
            else []
        )

        # 旧「除外ワード(部分一致)」は除外パターン(re.search=部分一致)と機能が
        # 重複していたため廃止した。保存済みの除外ワードは記号をエスケープして
        # 除外パターンへ移し、同じ除外が効き続けるようにする。
        text_exclusions_regex = [
            str(x) for x in (_load_json(s, "text_exclusions_regex", []) or [])
        ]
        for word in _load_json(s, "text_exclusions", []) or []:
            escaped = re.escape(str(word))
            if word and escaped not in text_exclusions_regex:
                text_exclusions_regex.append(escaped)

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
            excluded_words=excluded_words,
            additional_patterns=additional_patterns,
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
            enabled_engines=enabled_engines,
            keep_existing_on_detect=bool(
                s.value(_key("keep_existing_on_detect"), False, type=bool)
            ),
            display_mode=display_mode,
            mask_color=_normalize_mask_color(_load_json(s, "mask_color", None)),
            mask_transparency=mask_transparency,
            manual_visible=bool(s.value(_key("manual_visible"), True, type=bool)),
        )

    def save(self, settings: QSettings | None = None) -> None:
        """QSettings へ設定を保存する。"""
        s = settings or QSettings()
        _save_json(s, "enabled_entities", self.enabled_entities)
        _save_json(s, "colors", {k: list(v) for k, v in self.colors.items()})
        _save_json(s, "text_exclusions_regex", self.text_exclusions_regex)
        # 旧「除外ワード」は load 時に除外パターンへ移行済みなので消しておく
        # (残すと次回 load で再移行され、削除した除外パターンが復活してしまう)。
        s.remove(_key("text_exclusions"))
        _save_json(s, "entity_exclusions", self.entity_exclusions)
        _save_json(s, "excluded_words", self.excluded_words)
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
        _save_json(s, "enabled_engines", self.enabled_engines)
        s.setValue(_key("keep_existing_on_detect"), self.keep_existing_on_detect)
        s.setValue(_key("display_mode"), self.display_mode)
        _save_json(s, "mask_color", list(_normalize_mask_color(self.mask_color)))
        s.setValue(_key("mask_transparency"), _clamp_transparency(self.mask_transparency))
        s.setValue(_key("manual_visible"), self.manual_visible)

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
            excluded_words=list(self.excluded_words),
            additional_patterns=list(self.additional_patterns),
            custom_names=list(self.custom_names),
            entity_priority_order=list(self.entity_priority_order),
            enabled_engines=dict(self.enabled_engines),
        )
