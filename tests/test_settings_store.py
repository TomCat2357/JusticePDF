"""settings_store(INI 保存先の解決・レジストリからの一回限りの移行)のテスト。"""
import pytest
from PyQt6.QtCore import QCoreApplication, QSettings

from src.utils import settings_store


@pytest.fixture
def restore_app_names():
    org, app = QCoreApplication.organizationName(), QCoreApplication.applicationName()
    yield
    QCoreApplication.setOrganizationName(org)
    QCoreApplication.setApplicationName(app)


def test_paths_are_under_app_root(tmp_path):
    assert settings_store.settings_file(tmp_path) == tmp_path / "settings" / "JusticePDF.ini"
    # 既定のアプリルートは src の親(JusticePDF フォルダ)
    assert (settings_store.APP_ROOT / "src" / "utils" / "settings_store.py").exists()


def test_configure_points_default_qsettings_to_ini(tmp_path, monkeypatch, restore_app_names):
    monkeypatch.setattr(settings_store, "migrate_from_registry", lambda p: 0)
    assert settings_store.configure(tmp_path) is True
    s = QSettings()
    s.setValue("freetext/fontsize", 18)
    s.setValue("pii/json", '["a,b", "c"]')
    s.sync()
    ini = tmp_path / "settings" / "JusticePDF.ini"
    assert ini.exists()
    assert s.fileName() == str(ini).replace("\\", "/")
    assert QSettings().value("freetext/fontsize", 0, type=int) == 18
    assert QSettings().value("pii/json", "", type=str) == '["a,b", "c"]'


def test_configure_falls_back_when_folder_unusable(tmp_path, restore_app_names):
    blocker = tmp_path / "settings"
    blocker.write_text("file", encoding="utf-8")  # フォルダを作れない状況
    assert settings_store.configure(tmp_path) is False
    assert QCoreApplication.organizationName() == "JusticePDF"


def test_migrate_copies_registry_once(tmp_path, monkeypatch):
    legacy = {"print/copies": 3, "pii/mask_transparency": 40, "general/default_folder": "X"}

    class FakeLegacy:
        def __init__(self, *args):
            pass

        def allKeys(self):
            return list(legacy)

        def value(self, key):
            return legacy[key]

    monkeypatch.setattr(settings_store, "QSettings", _patched_qsettings(FakeLegacy))
    ini = tmp_path / "settings" / "JusticePDF.ini"
    ini.parent.mkdir()
    assert settings_store.migrate_from_registry(ini) == 3
    dest = QSettings(str(ini), QSettings.Format.IniFormat)
    assert dest.value("print/copies", 0, type=int) == 3
    assert dest.value("pii/mask_transparency", 0, type=int) == 40
    assert dest.value("general/default_folder", "", type=str) == "X"
    # INI が既にあれば再移行しない
    legacy["print/copies"] = 9
    assert settings_store.migrate_from_registry(ini) == 0
    assert QSettings(str(ini), QSettings.Format.IniFormat).value("print/copies", 0, type=int) == 3


def _patched_qsettings(fake_legacy):
    """NativeFormat(2引数以上の org/app 指定)だけ偽物に、ファイル指定は本物にする。"""

    def factory(*args):
        if len(args) == 4:
            return fake_legacy(*args)
        return QSettings(*args)

    factory.Format = QSettings.Format
    factory.Scope = QSettings.Scope
    return factory
