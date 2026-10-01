from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog

from src.views import settings_dialog
from src.views.settings_dialog import SettingsDialog


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_input_is_rejected_by_accept(monkeypatch, qtbot, text):
    # 警告ダイアログをモックし、accept() が呼ばれても閉じないことを確認する。
    warned = []
    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warned.append(True)),
    )
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog._folder_edit.setText(text)

    dialog.accept()

    assert warned == [True]
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_valid_absolute_path_is_accepted(qtbot, tmp_path):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    target = tmp_path / "library"
    dialog._folder_edit.setText(str(target))

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_folder() == str(target)


def test_selected_folder_expands_tilde(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog._folder_edit.setText("~/mylib")

    assert dialog.selected_folder() == str(tmp_path / "mylib")


def test_selected_folder_normalizes_relative_path_to_absolute(qtbot):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog._folder_edit.setText("relative_dir")

    result = Path(dialog.selected_folder())

    assert result.is_absolute()
    assert result.name == "relative_dir"


def test_save_mode_combo_defaults_to_auto_and_saves_manual(qtbot):
    from src.utils import app_settings

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    assert dialog._save_mode_combo.currentData() == "auto"

    dialog._save_mode_combo.setCurrentIndex(dialog._save_mode_combo.findData("manual"))
    dialog.save_heavy_pdf_settings()

    assert app_settings.edit_save_mode() == "manual"
    # 次に開くダイアログは保存済みの値を初期表示する
    again = SettingsDialog()
    qtbot.addWidget(again)
    assert again._save_mode_combo.currentData() == "manual"
