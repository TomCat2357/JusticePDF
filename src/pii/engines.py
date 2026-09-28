"""個人情報検出「エンジン」(認識器)の一覧・利用可否判定。

以前の PresidioPDF では複数の認識器(エンジン)を検出設定でON/OFF選択できたが、
JusticePDF へ移植した際に単一の固定パイプライン(正規表現+SudachiPy形態素解析+
日時パターン、``src.pii.analyzer.Analyzer._analyze_text_single``)へ統合されて
おり、選択できなくなっていた。本モジュールは「エンジン」を検出手法の単位
(正規表現/形態素解析/日時パターン)として定義し直し、
``src.pii.settings.PiiSettings.enabled_engines`` 経由で個別にON/OFFできるように
する。

``is_available`` は実際に import できるかどうかで判定し、未導入の場合は設定
ダイアログ側でチェックボックスをグレーアウトする。以前は GiNZA(spaCy)・Janome も
任意エンジンとして並べていたが、どちらも依存関係に含めておらず通常の導入では
常に選択不可だったため削除した(旧設定に残るキーは読み込み時に無視される)。
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Callable, Dict, List


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # find_spec は名前空間パッケージの一部でImportError/ValueErrorを
        # 送出することがあるため、未導入と同じ扱いにする。
        return False


def _sudachi_available() -> bool:
    return _module_available("sudachipy")



@dataclass(frozen=True, slots=True)
class EngineInfo:
    key: str
    name_ja: str
    description_ja: str
    default_enabled: bool
    is_available: Callable[[], bool]


# 順序はそのまま設定ダイアログの表示順になる。
ENGINES: List[EngineInfo] = [
    EngineInfo(
        key="regex",
        name_ja="正規表現ベース",
        description_ja="電話番号・マイナンバー・敬称付き人名・和暦年号などを正規表現で検出します。",
        default_enabled=True,
        is_available=lambda: True,
    ),
    EngineInfo(
        key="sudachi",
        name_ja="形態素解析(SudachiPy)",
        description_ja="固有名詞(人名・地名・その他の固有名詞)をSudachiPyの形態素解析で検出します。",
        default_enabled=True,
        is_available=_sudachi_available,
    ),
    EngineInfo(
        key="datetime",
        name_ja="日時パターン",
        description_ja="和暦・西暦の日付表現を正規表現で検出します。",
        default_enabled=True,
        is_available=lambda: True,
    ),
]

ENGINE_KEYS: List[str] = [engine.key for engine in ENGINES]
_ENGINE_BY_KEY: Dict[str, EngineInfo] = {engine.key: engine for engine in ENGINES}


def default_enabled_engines() -> Dict[str, bool]:
    """既定のエンジン有効状態を返す(既存設定に無いキーの補完に使う)。"""
    return {engine.key: engine.default_enabled for engine in ENGINES}


def is_engine_available(key: str) -> bool:
    """エンジンが実行環境に導入されていて実際に使えるかどうかを返す。"""
    engine = _ENGINE_BY_KEY.get(key)
    if engine is None:
        return False
    try:
        return bool(engine.is_available())
    except Exception:
        # 可用性チェック自体が失敗する場合も「未導入」扱いにしてエラーにしない。
        return False
