"""個人情報検出エンジン向けの設定マネージャ（トリム版）。

Ported from PresidioPDF src/core/config_manager.py。オリジナルはCLI引数・
YAML設定ファイルの読込・PDFマスキング(redaction)方式の設定など、
JusticePDF では使わない機能を多数含む大きなクラスだったため、
``src.pii.analyzer.Analyzer`` と ``src.pii.dedupe`` が実際に参照する
設定項目だけに絞って書き直した(YAML/クリック依存を排除)。

JusticePDF 側は :mod:`src.pii.settings` の ``PiiSettings.to_config_overrides()``
が組み立てる ``overrides`` 辞書をそのまま渡す。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Union

from src.pii.entity_types import ENTITY_TYPES

logger = logging.getLogger(__name__)


class ConfigManager:
    """個人情報検出エンジンの設定を保持し、正規化されたアクセサを提供する。"""

    ENTITY_TYPES = ENTITY_TYPES

    def __init__(self, overrides: Optional[Dict[str, Any]] = None) -> None:
        """
        Args:
            overrides: デフォルト設定へ再帰マージする上書き辞書
                (``PiiSettings.to_config_overrides()`` の戻り値を想定)。
        """
        self.config = self._deep_merge_dict(self._get_default_config(), overrides or {})

    def _get_default_config(self) -> Dict[str, Any]:
        return {
            "enabled_entities": {},
            "custom_recognizers": {},
            "custom_names": {
                "name_list": [],
                "name_patterns": [],
                "enabled": False,
                "use_with_auto_detection": True,
            },
            "exclusions": {
                "text_exclusions": [],
                "text_exclusions_regex": [],
                "entity_exclusions": {},
            },
            "nlp": {
                "sudachi_dict_type": "core",   # core | full | small
                "sudachi_split_mode": "C",     # A | B | C（Cが最長単位）
                "chunk_delimiter": "。",
                "chunk_max_chars": 15000,
            },
            # 検出エンジン(認識器)ごとのON/OFF。キーは src.pii.engines.ENGINE_KEYS。
            # 空辞書のまま(未設定)なら src.pii.engines.default_enabled_engines() を使う。
            "engines": {},
            "deduplication": {
                "enabled": False,
                "method": "overlap",  # overlap | exact | contain
                "overlap_mode": "partial_overlap",
                "priority": "wider_range",  # wider_range | narrower_range | entity_type
                "entity_priority_order": [
                    "INDIVIDUAL_NUMBER",
                    "PHONE_NUMBER",
                    "PERSON",
                    "LOCATION",
                    "DATE_TIME",
                    "YEAR",
                    "PROPER_NOUN",
                ],
            },
        }

    def _deep_merge_dict(self, base: Dict, override: Dict) -> Dict:
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge_dict(result[key], value)
            else:
                result[key] = value
        return result

    def _safe_get_config(self, key_path: str, default_value: Any = None):
        try:
            keys = key_path.split(".")
            value = self.config
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return default_value
            return value
        except (KeyError, TypeError, AttributeError):
            return default_value

    # --- 検出対象エンティティ -------------------------------------------

    def get_enabled_entities(self) -> List[str]:
        """有効なエンティティタイプのリストを返す。

        明示設定が無ければ(空辞書のままなら)全エンティティを有効化する。
        """
        enabled_entities = self._safe_get_config("enabled_entities", {})
        if not isinstance(enabled_entities, dict) or not enabled_entities:
            return list(self.ENTITY_TYPES)
        return [entity for entity, is_enabled in enabled_entities.items() if is_enabled]

    # --- 追加パターン / カスタム人名辞書 --------------------------------

    def get_custom_recognizers(self) -> Dict[str, Dict]:
        return {
            k: v
            for k, v in self.config["custom_recognizers"].items()
            if v.get("enabled", False)
        }

    def get_custom_names_config(self) -> Dict[str, Any]:
        return self._safe_get_config("custom_names", {})

    def get_additional_patterns_mapping(self) -> Dict[str, List[str]]:
        """追加検出用の正規表現パターンをエンティティ別に取得（順序保持）。

        - custom_recognizers: {name: {enabled, entity_type, patterns:[{regex}]}}
          をエンティティごとに連結
        - custom_names: name_list/name_patterns を PERSON に統合
        """
        mapping: Dict[str, List[str]] = {}

        try:
            cr = self.get_custom_recognizers()
            for _name, conf in cr.items():
                et = conf.get("entity_type")
                if not et:
                    continue
                pats = []
                for p in conf.get("patterns", []) or []:
                    regex = p.get("regex")
                    if isinstance(regex, str) and regex:
                        pats.append(regex)
                if pats:
                    mapping.setdefault(et, []).extend(pats)
        except Exception as e:
            logger.warning(f"custom_recognizersの展開に失敗: {e}")

        try:
            cn = self.get_custom_names_config()
            if cn.get("enabled", False):
                for w in cn.get("name_list", []) or []:
                    if isinstance(w, str) and w:
                        mapping.setdefault("PERSON", []).append(re.escape(w))
                for p in cn.get("name_patterns", []) or []:
                    rx = p.get("regex") if isinstance(p, dict) else None
                    if isinstance(rx, str) and rx:
                        mapping.setdefault("PERSON", []).append(rx)
        except Exception as e:
            logger.warning(f"custom_namesの展開に失敗: {e}")

        return mapping

    # --- 除外 -------------------------------------------------------------

    def get_text_exclusions(self) -> List[str]:
        return self._safe_get_config("exclusions.text_exclusions", [])

    def get_text_exclusions_regex(self) -> List[str]:
        return self._safe_get_config("exclusions.text_exclusions_regex", [])

    def get_entity_exclusions(
        self, entity_type: str = None
    ) -> Union[List[str], Dict[str, List[str]]]:
        entity_exclusions = self._safe_get_config("exclusions.entity_exclusions", {})
        if entity_type:
            return entity_exclusions.get(entity_type, [])
        return entity_exclusions

    def is_entity_excluded(self, entity_type: str, text: str) -> bool:
        """指定されたテキストが除外対象かどうかを判定する。

        - text_exclusions: 部分一致
        - text_exclusions_regex: 正規表現
        - entity_exclusions: エンティティ別の完全一致
        """
        text = text.strip()

        for exclusion in self.get_text_exclusions():
            if exclusion and exclusion in text:
                return True

        for pattern in self.get_text_exclusions_regex():
            if not pattern:
                continue
            try:
                if re.search(pattern, text):
                    return True
            except re.error as e:
                logger.warning(f"無効な除外正規表現をスキップ: {pattern}: {e}")

        if text in self.get_entity_exclusions(entity_type):
            return True

        return False

    # --- 形態素解析(SudachiPy) -------------------------------------------

    def get_sudachi_dict_type(self) -> str:
        return self._safe_get_config("nlp.sudachi_dict_type", "core")

    def get_sudachi_split_mode(self) -> str:
        return self._safe_get_config("nlp.sudachi_split_mode", "C")

    def get_chunk_delimiter(self) -> str:
        return self._safe_get_config("nlp.chunk_delimiter", "。")

    def get_chunk_max_chars(self) -> int:
        return self._safe_get_config("nlp.chunk_max_chars", 15000)

    # --- 検出エンジン(認識器)のON/OFF ------------------------------------

    def is_engine_enabled(self, key: str) -> bool:
        """指定した検出エンジンが有効かどうかを返す。

        明示設定が無ければ ``src.pii.engines.default_enabled_engines()`` の
        既定値を使う。
        """
        engines = self._safe_get_config("engines", {})
        if isinstance(engines, dict) and key in engines:
            return bool(engines[key])
        from src.pii.engines import default_enabled_engines

        return bool(default_enabled_engines().get(key, False))

    # --- 重複除去 -----------------------------------------------------------

    def is_deduplication_enabled(self) -> bool:
        return self._safe_get_config("deduplication.enabled", False)

    def get_deduplication_method(self) -> str:
        return self._safe_get_config("deduplication.method", "overlap")

    def get_deduplication_priority(self) -> str:
        return self._safe_get_config("deduplication.priority", "wider_range")

    def get_deduplication_overlap_mode(self) -> str:
        return self._safe_get_config("deduplication.overlap_mode", "partial_overlap")

    def get_entity_priority_order(self) -> List[str]:
        return self._safe_get_config(
            "deduplication.entity_priority_order",
            [
                "INDIVIDUAL_NUMBER",
                "PHONE_NUMBER",
                "PERSON",
                "LOCATION",
                "DATE_TIME",
                "YEAR",
                "PROPER_NOUN",
            ],
        )
