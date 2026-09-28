"""個人情報検出「エンジン」(認識器)の一覧・利用可否判定。

以前の PresidioPDF では複数の認識器(エンジン)を検出設定でON/OFF選択できたが、
JusticePDF へ移植した際に単一の固定パイプライン(正規表現+SudachiPy形態素解析+
日時パターン、``src.pii.analyzer.Analyzer._analyze_text_single``)へ統合されて
おり、選択できなくなっていた。本モジュールは「エンジン」を検出手法の単位
として定義し直し、``src.pii.settings.PiiSettings.enabled_engines`` 経由で
個別にON/OFFできるようにする。

GiNZA(spaCy)・Janomeは一度追加したが撤去した。理由:
    - GiNZA/spaCyはPresidioPDF自身も2026-06のPython 3.14移行時に撤去済みで、
      本家がSudachiPyへ一本化した経緯を踏まえ、JusticePDFでも追随しないことにした。
      加えて実機検証で、2026-09時点でもPython 3.14 + pydanticの組み合わせで
      `import spacy` 自体が失敗する既知の上流未修正バグを確認しており、
      必須依存にしても実質機能しなかった。
    - JanomeはSudachiPyと同種の検出を重複して行うだけで、既定で有効な
      SudachiPy(PresidioPDFと同じ検出エンジン)に対する明確な優位性が
      確立できなかったため、依存を増やしてまで維持する理由が無いと判断した。
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
