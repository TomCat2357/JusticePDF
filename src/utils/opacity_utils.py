"""UI の「透明度(%)」と内部の不透明度(0.0-1.0)の変換。

UI は 0% = 不透明 / 100% = 完全に透明(スライダーは右ほど透明)、
内部モデル・PDF(/CA)・描画は 0.0 = 透明 / 1.0 = 不透明。この向きの違いは
ここだけで吸収する。
"""

from __future__ import annotations


def opacity_to_transparency_percent(opacity: float) -> int:
    """不透明度(0.0-1.0)を透明度(0-100 の整数%)へ変換する。範囲外は丸める。"""
    clamped = max(0.0, min(1.0, float(opacity)))
    return 100 - round(clamped * 100)


def transparency_percent_to_opacity(percent: int) -> float:
    """透明度(0-100%)を不透明度(0.0-1.0)へ変換する。範囲外は丸める。"""
    clamped = max(0, min(100, int(percent)))
    return (100 - clamped) / 100.0
