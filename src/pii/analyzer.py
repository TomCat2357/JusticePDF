"""形態素解析（SudachiPy）ベースの PII 分析。

Ported from PresidioPDF src/analysis/analyzer.py（import 先を ``src.pii.*``
へ書き換え。検出ロジックは無改修）。

SudachiPy 直叩きの固有名詞サブ品詞分類＋正規表現認識器で個人情報を検出する。
検出結果の dict 形状（``{start, end, entity_type, text}``）は
JusticePDF 側（``src.pii.detection_service``）でそのまま利用する。
"""

import logging
from typing import List, Dict

from src.pii.config_manager import ConfigManager
from src.pii.regex_match_utils import resolve_mark_span
from src.pii.sudachi_tokenizer import SudachiTokenizer
from src.pii.regex_recognizers import detect_regex_entities
from src.pii.pos_ne_recognizer import detect_pos_entities
from src.pii.datetime_recognizer import detect_datetime

logger = logging.getLogger(__name__)


class Analyzer:
    """SudachiPy エンジンの設定と PII 分析を担当するクラス"""
    # SudachiPy の入力上限（バイト）に合わせ、上限値手前でチャンクを確定して
    # tokenization エラーを回避する。
    _SUDACHI_MAX_INPUT_BYTES = 49149
    _SUDACHI_SAFE_MARGIN_BYTES = 1024

    def __init__(self, config_manager: ConfigManager):
        """
        Args:
            config_manager: 設定管理インスタンス
        """
        self.config_manager = config_manager
        self._chunk_delimiter = config_manager.get_chunk_delimiter()
        self._chunk_max_chars = config_manager.get_chunk_max_chars()
        self._tokenizer = SudachiTokenizer(
            dict_type=config_manager.get_sudachi_dict_type(),
            split_mode=config_manager.get_sudachi_split_mode(),
        )

    def analyze_text(self, text: str, entities: List[str] = None) -> List[Dict]:
        """テキストの個人情報を解析（大容量ファイル対応）"""
        if entities is None:
            entities = self.config_manager.get_enabled_entities()

        if self._needs_chunking(text):
            logger.info(
                f"大容量テキスト検出 ({len(text):,} 文字 / {self._utf8_len(text):,} bytes)"
                " - チャンク処理を実行"
            )
            return self._analyze_text_chunked(text, entities)

        return self._analyze_text_single(text, entities)

    def _analyze_text_single(self, text: str, entities: List[str]) -> List[Dict]:
        """単一テキストの個人情報解析（追加>モデル>除外）"""
        import re

        # 1) 追加パターンの適用（右→左適用、左定義が高優先、長さ無視）
        add_map = self.config_manager.get_additional_patterns_mapping()
        add_candidates = []
        priority_seq = []
        for etype, pats in add_map.items():
            if etype not in entities:
                continue
            # パターン順序: 左(0)が高優先。適用は右→左。
            for idx, pat in enumerate(pats):
                priority_seq.append((etype, idx, pat))
        # 探索: 右→左
        for etype, idx, pat in reversed(priority_seq):
            try:
                cre = re.compile(pat, re.MULTILINE)
            except re.error as e:
                logger.warning(f"無効な追加正規表現をスキップ: {pat}: {e}")
                continue
            for m in cre.finditer(text):
                s, e = resolve_mark_span(m)
                if s == e:
                    continue
                add_candidates.append(
                    {
                        "start": s,
                        "end": e,
                        "entity_type": etype,
                        "text": text[s:e],
                        "_prio": idx,  # 低いほど高優先
                    }
                )

        # 追加候補の非重複化（優先度のみで解決）
        add_selected: List[Dict] = []
        occupied: List[tuple] = []
        for cand in sorted(add_candidates, key=lambda x: (x["_prio"], x["start"])):
            s, e = cand["start"], cand["end"]
            if any(not (e <= os or oe <= s) for (os, oe) in occupied):
                continue
            occupied.append((s, e))
            add_selected.append({k: v for k, v in cand.items() if not k.startswith("_")})

        # 2) モデル検出（正規表現認識器 + 形態素 NE + 日時）。返却は素の dict。
        analyzer_results: List[Dict] = []
        analyzer_results += detect_regex_entities(text, entities)
        analyzer_results += detect_pos_entities(self._tokenizer, text, entities)
        analyzer_results += detect_datetime(text, entities)

        # 3) モデル結果に除外適用＆追加と重複するものを抑制
        def overlaps_any(span):
            s, e = span
            for os, oe in occupied:
                if not (e <= os or oe <= s):
                    return True
            return False

        model_filtered: List[Dict] = []
        for r in analyzer_results:
            entity_type = r["entity_type"]
            if entity_type not in entities:
                continue
            s, e = r["start"], r["end"]
            ent_text = text[s:e]
            refined_text = self._refine_entity_text(ent_text, entity_type, text, s, e)
            if not refined_text or refined_text.isspace():
                continue
            rs, re_ = self._calculate_refined_positions(text, s, e, refined_text)
            # 追加と重なればスキップ（追加優先）
            if overlaps_any((rs, re_)):
                continue
            if not self._is_valid_entity_candidate(entity_type, refined_text):
                logger.debug(
                    f"エンティティ候補を妥当性検証で除外: '{refined_text}' ({entity_type})"
                )
                continue
            # 除外はモデル結果のみに適用
            if self.config_manager.is_entity_excluded(entity_type, refined_text):
                logger.debug(f"エンティティ除外: '{refined_text}' ({entity_type})")
                continue
            model_filtered.append(
                {
                    "start": rs,
                    "end": re_,
                    "entity_type": entity_type,
                    "text": refined_text,
                }
            )

        results = add_selected + model_filtered
        return sorted(results, key=lambda x: x["start"])

    @staticmethod
    def _digits_only(text: str) -> str:
        """数字以外を除去した文字列を返す。"""
        return "".join(ch for ch in str(text or "") if ch.isdigit())

    @classmethod
    def _is_valid_entity_candidate(cls, entity_type: str, text: str) -> bool:
        """既知エンティティ候補に追加の妥当性検証を適用する。"""
        if entity_type == "PHONE_NUMBER":
            return cls._is_valid_phone_number(text)
        if entity_type == "INDIVIDUAL_NUMBER":
            return cls._is_valid_individual_number(text)
        return True

    @classmethod
    def _is_valid_phone_number(cls, text: str) -> bool:
        """日本国内の電話番号として妥当な候補のみを許可する。"""
        digits = cls._digits_only(text)
        if len(digits) not in {10, 11}:
            return False
        if len(set(digits)) == 1:
            return False

        try:
            import phonenumbers

            parsed = phonenumbers.parse(str(text), "JP")
        except Exception:
            return False

        return (
            phonenumbers.is_possible_number(parsed)
            and phonenumbers.is_valid_number_for_region(parsed, "JP")
        )

    @classmethod
    def _is_valid_individual_number(cls, text: str) -> bool:
        """マイナンバー（個人番号）の書式と検査用数字を検証する。"""
        digits = cls._digits_only(text)
        if len(digits) != 12:
            return False
        if len(set(digits)) == 1:
            return False

        return cls._calculate_individual_number_check_digit(digits[:-1]) == int(
            digits[-1]
        )

    @staticmethod
    def _calculate_individual_number_check_digit(body: str) -> int:
        """11桁本体から個人番号の検査用数字を算出する。"""
        weights = [2, 3, 4, 5, 6, 7, 2, 3, 4, 5, 6]
        total = sum(int(digit) * weight for digit, weight in zip(reversed(body), weights))
        remainder = total % 11
        if remainder <= 1:
            return 0
        return 11 - remainder

    def _analyze_text_chunked(self, text: str, entities: List[str]) -> List[Dict]:
        """チャンク分割による大容量テキスト解析"""
        chunks = self._chunk_text(text)
        all_results = []

        logger.info(f"テキストを{len(chunks)}個のチャンクに分割して処理")

        for i, chunk_info in enumerate(chunks):
            chunk_text = chunk_info["text"]
            start_offset = chunk_info["start_offset"]

            try:
                logger.debug(
                    f"チャンク {i+1}/{len(chunks)} 処理中 ({len(chunk_text):,} 文字)"
                )
                chunk_results = self._analyze_text_single(chunk_text, entities)

                for result in chunk_results:
                    result["start"] += start_offset
                    result["end"] += start_offset

                all_results.extend(chunk_results)

            except Exception as e:
                logger.error(f"チャンク {i+1} の処理でエラー: {e}")
                continue

        return sorted(all_results, key=lambda x: x["start"])

    @staticmethod
    def _utf8_len(text: str) -> int:
        """UTF-8バイト長を返す"""
        return len(text.encode("utf-8"))

    def _chunk_max_bytes(self) -> int:
        """Sudachi入力上限より手前の安全なチャンクバイト上限"""
        return max(1, self._SUDACHI_MAX_INPUT_BYTES - self._SUDACHI_SAFE_MARGIN_BYTES)

    def _is_within_chunk_limits(self, text: str, max_chars: int, max_bytes: int) -> bool:
        """文字数・UTF-8バイト数の両方でチャンク制約を判定"""
        return len(text) <= max_chars and self._utf8_len(text) <= max_bytes

    def _needs_chunking(self, text: str) -> bool:
        """文字数またはUTF-8バイト数が上限を超える場合にチャンク分割が必要"""
        return not self._is_within_chunk_limits(
            text,
            self._chunk_max_chars,
            self._chunk_max_bytes(),
        )

    def _split_text_by_hard_limits(
        self,
        text: str,
        start_offset: int,
        max_chars: int,
        max_bytes: int,
    ) -> List[Dict]:
        """文字数・バイト数制約でテキストを強制分割する"""
        chunks: List[Dict] = []
        cursor = 0
        text_length = len(text)

        while cursor < text_length:
            end = min(cursor + max_chars, text_length)
            if end <= cursor:
                end = min(cursor + 1, text_length)

            candidate = text[cursor:end]
            if self._utf8_len(candidate) > max_bytes:
                low = cursor + 1
                high = end
                best = low
                while low <= high:
                    mid = (low + high) // 2
                    probe = text[cursor:mid]
                    if self._utf8_len(probe) <= max_bytes:
                        best = mid
                        low = mid + 1
                    else:
                        high = mid - 1
                end = best
                candidate = text[cursor:end]

            chunks.append({"text": candidate, "start_offset": start_offset + cursor})
            cursor = end

        return chunks

    def _chunk_text(self, text: str) -> List[Dict]:
        """テキストをチャンクに分割（区切り文字→文字数/バイト数フォールバック）"""
        max_chars = self._chunk_max_chars
        max_bytes = self._chunk_max_bytes()

        if self._is_within_chunk_limits(text, max_chars, max_bytes):
            return [{"text": text, "start_offset": 0}]

        delimiter = self._chunk_delimiter
        chunks: List[Dict] = []

        if delimiter == "":
            logger.info("区切り文字が未設定のため文字数/バイト数で分割")
            return self._split_text_by_hard_limits(text, 0, max_chars, max_bytes)

        # 1) 区切り文字で分割を試みる
        segments = text.split(delimiter)

        if len(segments) > 1:
            current_chunk = ""
            current_offset = 0

            for i, segment in enumerate(segments):
                # 最後のセグメント以外は区切り文字を付加
                seg = segment + delimiter if i < len(segments) - 1 else segment
                test_chunk = current_chunk + seg

                if self._is_within_chunk_limits(test_chunk, max_chars, max_bytes):
                    current_chunk = test_chunk
                    continue

                if current_chunk:
                    chunks.append(
                        {"text": current_chunk, "start_offset": current_offset}
                    )
                    current_offset += len(current_chunk)
                    current_chunk = ""

                if self._is_within_chunk_limits(seg, max_chars, max_bytes):
                    current_chunk = seg
                    continue

                forced_chunks = self._split_text_by_hard_limits(
                    seg,
                    current_offset,
                    max_chars,
                    max_bytes,
                )
                chunks.extend(forced_chunks)
                if forced_chunks:
                    last_chunk = forced_chunks[-1]
                    current_offset = last_chunk["start_offset"] + len(last_chunk["text"])
                else:
                    current_offset += len(seg)

            if current_chunk:
                chunks.append(
                    {"text": current_chunk, "start_offset": current_offset}
                )
        else:
            # 2) 区切り文字がない場合、文字数/バイト数で強制分割
            logger.info(
                f"区切り文字 '{delimiter}' が見つからないため文字数/バイト数で分割"
            )
            chunks = self._split_text_by_hard_limits(text, 0, max_chars, max_bytes)

        return chunks if chunks else [{"text": text, "start_offset": 0}]

    def _refine_entity_text(
        self, entity_text: str, entity_type: str, full_text: str, start: int, end: int
    ) -> str:
        """エンティティタイプに応じてテキスト境界を調整"""
        import re

        if entity_type == "PERSON":
            refined = re.sub(r"[0-9\-\s]*", "", entity_text).strip()
            if refined:
                return refined

        elif entity_type == "LOCATION":
            refined = re.sub(r"[0-9\-\s]*", "", entity_text).strip()
            if refined:
                return refined

        elif entity_type == "PHONE_NUMBER":
            refined = re.findall(r"[0-9\-]+", entity_text)
            if refined:
                return "".join(refined)

        return entity_text

    def _calculate_refined_positions(
        self, full_text: str, original_start: int, original_end: int, refined_text: str
    ) -> tuple:
        """調整されたテキストの新しい位置を計算"""
        original_text = full_text[original_start:original_end]

        if refined_text in original_text:
            relative_start = original_text.find(refined_text)
            if relative_start != -1:
                new_start = original_start + relative_start
                new_end = new_start + len(refined_text)
                return new_start, new_end

        return original_start, original_end
