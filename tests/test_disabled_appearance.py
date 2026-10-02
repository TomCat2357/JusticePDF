from pathlib import Path

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QSlider

from src.views.page_edit_annotations import _ColorSwatchButton

_QSS = Path(__file__).resolve().parent.parent / "src" / "views" / "style.qss"


def _grab_center(widget) -> QColor:
    widget.resize(120, 28)
    img = widget.grab().toImage()
    return img.pixelColor(10, 4)


def test_swatch_disabled_is_lighter_than_enabled(qtbot):
    btn = _ColorSwatchButton()
    qtbot.addWidget(btn)
    btn.set_swatch_color(QColor(200, 0, 0))
    enabled = _grab_center(btn)
    btn.setEnabled(False)
    disabled = _grab_center(btn)
    assert enabled.red() > 150 and enabled.green() < 60
    assert disabled.green() > 150  # 地の色(#f4f4f0)寄りへ淡色化


def test_slider_disabled_rules_in_qss(qapp, qtbot):
    text = _QSS.read_text(encoding="utf-8")
    assert "QSlider::sub-page:horizontal:disabled" in text
    assert "QSlider::groove:horizontal:disabled" in text
    qapp.setStyleSheet(text)
    try:
        slider = QSlider()
        qtbot.addWidget(slider)
        slider.setEnabled(False)
        slider.resize(120, 20)
        slider.show()
        assert not slider.grab().isNull()
    finally:
        qapp.setStyleSheet("")
