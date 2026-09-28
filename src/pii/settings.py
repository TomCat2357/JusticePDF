"""個人情報検出の設定（JusticePDF側で永続化）。

設定項目・UI 構成は PresidioPDF の設定ダイアログ
(``src/gui_pyqt/views/config_dialog.py``)に倣うが、保存先は JusticePDF の
既存の ``QSettings`` 慣習(``src.utils.app_settings``と同じ"<カテゴリ>/<項目>"
キー命名)に合わせる。JSON化が必要な複合値(リスト/辞書)は文字列として保存する。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace as dataclass_replace

from PyQt6.QtCore import QSettings

from src.pii.engines import default_enabled_engines
from src.pii.entity_types import ENTITY_TYPES, HIGHLIGHT_COLORS

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


@dataclass(slots=True)
class PiiSettings:
    """個人情報検出の設定一式。"""

    # 検出対象エンティティ（未設定キーは有効とみなす）
    enabled_entities: dict[str, bool] = field(
        default_factory=lambda: {et: True for et in ENTITY_TYPES}
    )
    # マーカー色（エンティティ別、RGB 0.0-1.0）。未指定は entity_types の既定色。
    colors: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    # 除外設定
    text_exclusions: list[str] = field(default_factory=list)
    text_exclusions_regex: list[str] = field(default_factory=list)
    entity_exclusions: dict[str, list[str]] = field(default_factory=dict)
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
    # 対象重複判定(PresidioPDFの「対象重複判定」に対応)。
    # "any"  = 異なるエンティティ種別同士の重なりも重複とみなす(既定・従来動作)。
    # "same" = 同じ種別同士の重なりのみ重複とみなす(異なる種別同士は、
    #          一方がPROPER_NOUN〔汎用の固有名詞候補〕で、かつ一方が他方を
    #          完全に包含する場合のみ例外的に重複扱いにする)。
    entity_overlap_mode: str = "any"  # any | same
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
    # テキスト前処理(PresidioPDFの「テキスト前処理設定」に対応)。
    # ignore_newlines=True(既定・従来動作): ブロック境界に区切り文字を挿入
    # しない(従来通りの見た目・オフセット)。False にすると、表の別セル等
    # 区切り文字の無いブロック境界に改行(\n)を1文字挿入してから検出する
    # (異なるセルの文字列が誤って連結されて検出されるのを防げる一方、
    # 改行を挟んだ検出は行えなくなる)。
    ignore_newlines: bool = True
    # ignore_whitespace=True にすると、検出対象テキストから空白文字
    # (半角/全角スペース等)を除去してから検出する(セル内の字間調整で
    # 挿入された空白によって正規表現が一致しなくなるケースに対応)。
    ignore_whitespace: bool = False
    # OCR（rapidocr が導入されている場合のみ実際に使われる）
    ocr_enabled: bool = False
    ocr_dpi: int = 300
    # RapidOCRのモデル規模("light"=軽量/mobile、"heavy"=高精度/server)。
    ocr_tier: str = "light"  # light | heavy
    # 検出エンジン(認識器)ごとのON/OFF。キーは src.pii.engines.ENGINE_KEYS。
    enabled_engines: dict[str, bool] = field(default_factory=default_enabled_engines)
    # 検出ボタン押下時、既存の検出結果・手動追加分を消さずに新規検出分だけ
    # 追加するかどうか(オフなら従来通り、検出し直したページの既存結果を置換)。
    keep_existing_on_detect: bool = False

    def is_entity_enabled(self, entity_type: str) -> bool:
        return bool(self.enabled_entities.get(entity_type, True))

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
                "text_exclusions": list(self.text_exclusions),
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
                "entity_overlap_mode": self.entity_overlap_mode,
            },
            "text_preprocess": {
                "ignore_newlines": self.ignore_newlines,
                "ignore_whitespace": self.ignore_whitespace,
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
            # 既知のエンジンキーだけ取り込む。以前 GiNZA/Janome エンジンを
            # 選べた頃の設定ファイルに残っているキーは、対応するエンジンが
            # 撤去された今は無害な未知キーとして無視する(エラーにしない)。
            enabled_engines.update(
                {
                    str(k): bool(v)
                    for k, v in enabled_engines_raw.items()
                    if str(k) in enabled_engines
                }
            )

        return PiiSettings(
            enabled_entities=(
                {str(k): bool(v) for k, v in enabled_entities.items()}
                if isinstance(enabled_entities, dict) and enabled_entities
                else dict(default.enabled_entities)
            ),
            colors=colors,
            text_exclusions=list(_load_json(s, "text_exclusions", []) or []),
            text_exclusions_regex=list(_load_json(s, "text_exclusions_regex", []) or []),
            entity_exclusions=entity_exclusions,
            additional_patterns=additional_patterns,
            custom_names=list(_load_json(s, "custom_names", []) or []),
            dedupe_enabled=bool(
                s.value(_key("dedupe_enabled"), default.dedupe_enabled, type=bool)
            ),
            dedupe_overlap=str(s.value(_key("dedupe_overlap"), default.dedupe_overlap, type=str)),
            dedupe_keep=str(s.value(_key("dedupe_keep"), default.dedupe_keep, type=str)),
            entity_overlap_mode=str(
                s.value(_key("entity_overlap_mode"), default.entity_overlap_mode, type=str)
            ),
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
            ignore_newlines=bool(
                s.value(_key("ignore_newlines"), default.ignore_newlines, type=bool)
            ),
            ignore_whitespace=bool(
                s.value(_key("ignore_whitespace"), default.ignore_whitespace, type=bool)
            ),
            ocr_enabled=bool(s.value(_key("ocr_enabled"), False, type=bool)),
            ocr_dpi=int(s.value(_key("ocr_dpi"), default.ocr_dpi, type=int)),
            ocr_tier=str(s.value(_key("ocr_tier"), default.ocr_tier, type=str)),
            enabled_engines=enabled_engines,
            keep_existing_on_detect=bool(
                s.value(_key("keep_existing_on_detect"), False, type=bool)
            ),
        )

    def save(self, settings: QSettings | None = None) -> None:
        """QSettings へ設定を保存する。"""
        s = settings or QSettings()
        _save_json(s, "enabled_entities", self.enabled_entities)
        _save_json(s, "colors", {k: list(v) for k, v in self.colors.items()})
        _save_json(s, "text_exclusions", self.text_exclusions)
        _save_json(s, "text_exclusions_regex", self.text_exclusions_regex)
        _save_json(s, "entity_exclusions", self.entity_exclusions)
        _save_json(s, "additional_patterns", [list(p) for p in self.additional_patterns])
        _save_json(s, "custom_names", self.custom_names)
        s.setValue(_key("dedupe_enabled"), self.dedupe_enabled)
        s.setValue(_key("dedupe_overlap"), self.dedupe_overlap)
        s.setValue(_key("dedupe_keep"), self.dedupe_keep)
        s.setValue(_key("entity_overlap_mode"), self.entity_overlap_mode)
        _save_json(s, "entity_priority_order", self.entity_priority_order)
        s.setValue(_key("sudachi_dict_type"), self.sudachi_dict_type)
        s.setValue(_key("sudachi_split_mode"), self.sudachi_split_mode)
        s.setValue(_key("ignore_newlines"), self.ignore_newlines)
        s.setValue(_key("ignore_whitespace"), self.ignore_whitespace)
        s.setValue(_key("ocr_enabled"), self.ocr_enabled)
        s.setValue(_key("ocr_dpi"), self.ocr_dpi)
        s.setValue(_key("ocr_tier"), self.ocr_tier)
        _save_json(s, "enabled_engines", self.enabled_engines)
        s.setValue(_key("keep_existing_on_detect"), self.keep_existing_on_detect)

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
            text_exclusions=list(self.text_exclusions),
            text_exclusions_regex=list(self.text_exclusions_regex),
            entity_exclusions={k: list(v) for k, v in self.entity_exclusions.items()},
            additional_patterns=list(self.additional_patterns),
            custom_names=list(self.custom_names),
            entity_priority_order=list(self.entity_priority_order),
            enabled_engines=dict(self.enabled_engines),
        )
