"""個人情報検出「エンジン」(認識器)の一覧・利用可否判定。

以前の PresidioPDF では複数の認識器(エンジン)を検出設定でON/OFF選択できたが、
JusticePDF へ移植した際に単一の固定パイプライン(正規表現+SudachiPy形態素解析+
日時パターン、``src.pii.analyzer.Analyzer._analyze_text_single``)へ統合されて
おり、選択できなくなっていた。本モジュールは「エンジン」を検出手法の単位
(正規表現/形態素解析/日時パターン/外部NLPライブラリ)として定義し直し、
``src.pii.settings.PiiSettings.enabled_engines`` 経由で個別にON/OFFできるように
する。

GiNZA(spaCy日本語モデル)・Janome は任意依存(未導入でも動作)。``is_available``
は実際に import できるかどうかで判定し、未導入の場合は設定ダイアログ側で
チェックボックスをグレーアウトする(選択してもエラーにはならず、単に検出に
寄与しない)。
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


def _ginza_available() -> bool:
    """spaCy/GiNZAが実際に読み込めるかどうかを判定する。

    パッケージが `pip`/`uv` で導入されていても、実行系のPythonバージョンと
    依存(pydantic等)の組み合わせによっては import 自体が例外を送出することが
    ある(実例: Python 3.14 と pydantic 2.x の組み合わせで、pydanticが内部で
    使う ``typing._eval_type`` の引数がPython 3.14で非互換になり、spaCyの
    import 中に ``TypeError`` が発生する。2026-09時点でPyPIに公開されている
    最新版・ベータ版のpydanticでも未修正)。``find_spec`` によるモジュール
    存在チェックだけでは「入っているのに動かない」状態を「利用可能」と誤判定
    してしまうため、``src.pii.ginza_recognizer.is_ready()`` で実際に読み込みを
    試みた結果を使う(初回のみコストがかかるが、結果はプロセス内でキャッシュ
    されるため以降は一瞬で返る)。
    """
    try:
        from src.pii.ginza_recognizer import is_ready

        return is_ready()
    except Exception:
        return False


def _janome_available() -> bool:
    """Janomeが実際に読み込めるかどうかを判定する(理由は_ginza_availableと同様)。"""
    try:
        from src.pii.janome_recognizer import is_ready

        return is_ready()
    except Exception:
        return False


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
    EngineInfo(
        key="ginza",
        name_ja="GiNZA/spaCy",
        description_ja=(
            "GiNZA(spaCy日本語モデル)の統計的固有表現認識で人名・地名を補助的に検出します。"
            "利用にはspaCyとja_ginza(またはja_ginza_electra)モデルの追加導入が必要です。"
        ),
        default_enabled=False,
        is_available=_ginza_available,
    ),
    EngineInfo(
        key="janome",
        name_ja="Janome",
        description_ja=(
            "Janome形態素解析による固有名詞検出を補助的に使います"
            "(SudachiPyが使えない環境向けの代替/補完)。利用にはjanomeの追加導入が必要です。"
        ),
        default_enabled=False,
        is_available=_janome_available,
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
