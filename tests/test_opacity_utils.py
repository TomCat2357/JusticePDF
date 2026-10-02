import pytest

from src.utils.opacity_utils import (
    opacity_to_transparency_percent,
    transparency_percent_to_opacity,
)


@pytest.mark.parametrize(
    "opacity, percent",
    [(1.0, 0), (0.0, 100), (0.4, 60), (0.65, 35), (0.5, 50)],
)
def test_opacity_to_transparency_percent(opacity, percent):
    assert opacity_to_transparency_percent(opacity) == percent


@pytest.mark.parametrize(
    "percent, opacity",
    [(0, 1.0), (100, 0.0), (60, 0.4), (35, 0.65)],
)
def test_transparency_percent_to_opacity(percent, opacity):
    assert transparency_percent_to_opacity(percent) == pytest.approx(opacity)


def test_out_of_range_is_clamped():
    assert opacity_to_transparency_percent(1.5) == 0
    assert opacity_to_transparency_percent(-0.2) == 100
    assert transparency_percent_to_opacity(-10) == 1.0
    assert transparency_percent_to_opacity(150) == 0.0


def test_round_trip():
    for percent in range(101):
        assert opacity_to_transparency_percent(transparency_percent_to_opacity(percent)) == percent
