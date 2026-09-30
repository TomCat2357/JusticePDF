"""RapidOCR のモデル(軽=mobile / 高精度=server)を事前ダウンロードする。

`python -m src.ocr.download_models` で実行する。アプリ実行時と同じモデル構成
(``RapidOCRService._get_engine``)でエンジンを一度構築することで、RapidOCR が
不足モデルを modelscope.cn から取得し、rapidocr パッケージ内の ``models``
フォルダ(``<site-packages>/rapidocr/models``)へ保存する。取得済みなら
再ダウンロードされない。
"""
from __future__ import annotations

import sys
from typing import Callable, Optional, Sequence

TIERS = ("light", "heavy")


def _build_engine(tier: str) -> None:
    from src.ocr.rapidocr_service import RapidOCRService

    RapidOCRService(tier)._get_engine()


def download_models(
    tiers: Sequence[str] = TIERS,
    *,
    builder: Optional[Callable[[str], None]] = None,
    available: Optional[bool] = None,
    out=None,
) -> int:
    """各 tier のエンジンを構築してモデルを揃える。成功で 0、失敗で 1 を返す。"""
    out = out or sys.stdout
    if available is None:
        from src.ocr.rapidocr_service import RapidOCRService

        available = RapidOCRService.is_available()
    if not available:
        print(
            "RapidOCR が導入されていません。`uv sync`(または `pip install -e \".[all]\"`)"
            "を実行してから再度実行してください。",
            file=out,
        )
        return 1

    build = builder or _build_engine
    failed = []
    for tier in tiers:
        label = "高精度(server)" if tier == "heavy" else "軽量(mobile)"
        print(f"OCRモデルを準備中: {label} ...", file=out, flush=True)
        try:
            build(tier)
        except Exception as exc:
            failed.append(tier)
            print(f"  失敗: {label}: {exc}", file=out)
        else:
            print(f"  完了: {label}", file=out)

    if failed:
        print(
            "一部のOCRモデルを取得できませんでした。ネットワーク接続"
            "(modelscope.cn)を確認して再実行してください。"
            "未取得のモデルはOCRの初回使用時に自動ダウンロードされます。",
            file=out,
        )
        return 1
    print("OCRモデルの準備が完了しました。", file=out)
    return 0


def main() -> int:
    return download_models()


if __name__ == "__main__":
    sys.exit(main())
