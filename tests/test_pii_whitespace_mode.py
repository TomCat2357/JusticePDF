"""語句を検出/除外パターンへ登録するときの空白の扱い(pattern_whitespace_mode)のテスト。"""
from __future__ import annotations

import re

import fitz
import pytest

from src.pii.settings import (
    WHITESPACE_MODES,
    PiiSettings,
    exact_match_pattern,
    literal_to_pattern,
)
from src.pii.text_normalize import normalize_1to1
from src.utils.pdf_utils import list_pii_markup_annots
from src.views.pii_panel import ScopeChoiceDialog
from src.views.pii_settings_dialog import PiiSettingsDialog
from tests.helpers import open_zoom
from tests.test_page_edit_window_pii import (
    _create_window,
    _silence_message_boxes,
    _stub_detect_scope,
    _stub_exclude_scope,
)

pytestmark = pytest.mark.usefixtures("qapp")

MODES = [key for key, _ in WHITESPACE_MODES]


def _matches(pattern: str, text: str) -> bool:
    return re.fullmatch(pattern, text) is not None


# ---------------------------------------------------------------------------
# literal_to_pattern / exact_match_pattern
# ---------------------------------------------------------------------------


def test_whitespace_modes_keys_and_default():
    assert MODES == ["literal", "optional", "flexible", "any_gap"]
    assert PiiSettings().pattern_whitespace_mode == "optional"


@pytest.mark.parametrize(
    "text",
    ["山田 太郎", normalize_1to1("山田　太郎"), "山田\n太郎", "  山田 太郎 \n", "山田 \n 太郎"],
)
def test_optional_flexible_any_gap_patterns(text):
    assert literal_to_pattern(text, "optional") == r"山田\s*太郎"
    assert literal_to_pattern(text, "flexible") == r"山田\s+太郎"
    assert literal_to_pattern(text, "any_gap") == r"山\s*田\s*太\s*郎"


def test_literal_pattern_keeps_whitespace_escaped():
    assert literal_to_pattern("山田 太郎", "literal") == re.escape("山田 太郎")
    # 前後の空白は常に除く。
    assert literal_to_pattern("  山田 \n", "literal") == "山田"
    assert literal_to_pattern(" \n山田　 ", "flexible") == "山田"


@pytest.mark.parametrize("mode", ["optional", "flexible", "any_gap"])
def test_no_escaped_space_remains(mode):
    assert "\\ " not in literal_to_pattern("a b  c", mode)


def test_metacharacters_are_escaped_in_every_mode():
    text = "a.b (c)"
    assert literal_to_pattern(text, "literal") == re.escape(text)
    assert literal_to_pattern(text, "optional") == r"a\.b\s*\(c\)"
    assert literal_to_pattern(text, "flexible") == r"a\.b\s+\(c\)"
    assert literal_to_pattern(text, "any_gap") == r"a\s*\.\s*b\s*\(\s*c\s*\)"
    for mode in MODES:
        assert _matches(literal_to_pattern(text, mode), text)
        assert not _matches(literal_to_pattern(text, mode), "aXb (c)")


def test_unknown_mode_falls_back_to_optional():
    assert literal_to_pattern("山田 太郎", "bogus") == literal_to_pattern("山田 太郎", "optional")
    assert literal_to_pattern("山田 太郎", "") == literal_to_pattern("山田 太郎", "optional")


def test_empty_and_whitespace_only_text():
    for mode in MODES:
        assert literal_to_pattern("", mode) == ""
        assert literal_to_pattern(" \n　", mode) == ""


def test_generated_patterns_match_expected_variants():
    variants = ["山田太郎", "山田 太郎", "山田　太郎", "山田\n太郎", "山田  太郎", "山 田太郎"]
    expected = {
        "literal": {"山田 太郎"},
        "optional": {"山田太郎", "山田 太郎", "山田　太郎", "山田\n太郎", "山田  太郎"},
        "flexible": {"山田 太郎", "山田　太郎", "山田\n太郎", "山田  太郎"},
        "any_gap": set(variants),
    }
    for mode in MODES:
        pattern = literal_to_pattern("山田 太郎", mode)
        assert {v for v in variants if _matches(pattern, v)} == expected[mode], mode
    # 検出側は normalize_1to1 済み(全角空白→半角空白)のテキストで比べる。
    literal = literal_to_pattern(normalize_1to1("山田　太郎"), "literal")
    assert _matches(literal, normalize_1to1("山田　太郎"))
    assert not _matches(literal_to_pattern("山田 太郎", "flexible"), "山田太郎")


def test_exact_match_pattern_with_mode():
    assert exact_match_pattern("山田 太郎") == "^" + re.escape("山田 太郎") + "$"
    assert exact_match_pattern("山田 太郎", "literal") == "^" + re.escape("山田 太郎") + "$"
    assert exact_match_pattern("山田 太郎", "optional") == r"^山田\s*太郎$"
    assert exact_match_pattern("山田 太郎", "flexible") == r"^山田\s+太郎$"
    assert exact_match_pattern("山田 太郎", "any_gap") == r"^山\s*田\s*太\s*郎$"


# ---------------------------------------------------------------------------
# 永続化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_setting_round_trip_and_copy(mode):
    settings = PiiSettings()
    settings.pattern_whitespace_mode = mode
    settings.save()
    assert PiiSettings.load().pattern_whitespace_mode == mode
    assert settings.copy().pattern_whitespace_mode == mode


def test_setting_default_when_unset_and_invalid_falls_back():
    assert PiiSettings.load().pattern_whitespace_mode == "optional"
    settings = PiiSettings()
    settings.pattern_whitespace_mode = "bogus"
    settings.save()
    assert PiiSettings.load().pattern_whitespace_mode == "optional"


# ---------------------------------------------------------------------------
# 設定ダイアログ
# ---------------------------------------------------------------------------


def test_dialog_combo_lists_modes_and_loads_current(qtbot):
    settings = PiiSettings()
    settings.pattern_whitespace_mode = "flexible"
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    combo = dialog._whitespace_mode_combo
    assert [combo.itemData(i) for i in range(combo.count())] == MODES
    assert [combo.itemText(i) for i in range(combo.count())] == [
        label for _, label in WHITESPACE_MODES
    ]
    assert combo.currentData() == "flexible"
    assert combo.toolTip()


def test_dialog_default_and_save(qtbot):
    dialog = PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog)
    combo = dialog._whitespace_mode_combo
    assert combo.currentData() == "optional"
    combo.setCurrentIndex(combo.findData("any_gap"))
    assert dialog.result_settings().pattern_whitespace_mode == "any_gap"


# ---------------------------------------------------------------------------
# 右クリック「検出語に追加」「除外パターンに追加」
# ---------------------------------------------------------------------------


def _make_word_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.save(str(path))
    doc.close()


def _add_markup(pdf_path, rect_word: str, pii_text: str, entity: str = "PERSON") -> None:
    from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData, create_markup_annot

    with fitz.open(str(pdf_path)) as doc:
        rect = doc[0].search_for(rect_word)[0]
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((rect.x0, rect.y0, rect.x1, rect.y1),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(0.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity=entity,
            pii_text=pii_text,
        ),
    )


def _open_window(qtbot, monkeypatch, pdf_path, mode: str | None = None):
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    if mode is not None:
        settings = window._pii_settings().copy()
        settings.pattern_whitespace_mode = mode
        settings.save()
        window._pii_settings_cache = settings
    return window


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("literal", re.escape("山田 太郎")),
        ("optional", r"山田\s*太郎"),
        ("flexible", r"山田\s+太郎"),
        ("any_gap", r"山\s*田\s*太\s*郎"),
    ],
)
def test_add_detect_word_uses_whitespace_mode(qtbot, monkeypatch, tmp_path, mode, expected):
    pdf_path = tmp_path / "ws.pdf"
    _make_word_pdf(pdf_path)
    window = _open_window(qtbot, monkeypatch, pdf_path, mode)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_detect_word("PERSON", "山田　太郎 ")  # 全角空白+末尾空白

    assert window._pii_settings().additional_patterns == [("PERSON", expected)]
    assert PiiSettings.load().additional_patterns == [("PERSON", expected)]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("literal", "^" + re.escape("山田 太郎") + "$"),
        ("optional", r"^山田\s*太郎$"),
        ("flexible", r"^山田\s+太郎$"),
        ("any_gap", r"^山\s*田\s*太\s*郎$"),
    ],
)
def test_add_exclude_word_uses_whitespace_mode(qtbot, monkeypatch, tmp_path, mode, expected):
    pdf_path = tmp_path / "ws.pdf"
    _make_word_pdf(pdf_path)
    window = _open_window(qtbot, monkeypatch, pdf_path, mode)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_exclude_word("山田 太郎")

    assert window._pii_settings().text_exclusions_regex == [expected]
    assert PiiSettings.load().text_exclusions_regex == [expected]


def test_add_exclude_word_default_is_optional(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "ws.pdf"
    _make_word_pdf(pdf_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_exclude_word("山田 太郎")

    assert window._pii_settings().text_exclusions_regex == [r"^山田\s*太郎$"]


def test_add_exclude_word_removes_detected_rows_matching_pattern(qtbot, monkeypatch, tmp_path):
    """除外パターンに完全一致する検出済みの結果を、空白の表記ゆれを含めて削除する。"""
    pdf_path = tmp_path / "ws.pdf"
    _make_word_pdf(pdf_path)
    _add_markup(pdf_path, "SECRET", "山田 太郎")
    _add_markup(pdf_path, "SECRET", "山田　太郎")
    _add_markup(pdf_path, "SECRET", "山田太郎")
    _add_markup(pdf_path, "SECRET", "山田 太郎 ")  # 前後の空白
    _add_markup(pdf_path, "KEEPME", "山田 花子")  # 別の語句は消さない
    _add_markup(pdf_path, "SECRET", "山田 太郎", "MANUAL")  # 手動は消さない
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_ALL)

    window._on_pii_add_exclude_word("山田 太郎")

    remaining = sorted((a.pii_entity, a.pii_text) for a in list_pii_markup_annots(str(pdf_path)))
    assert remaining == [("MANUAL", "山田 太郎"), ("PERSON", "山田 花子")]


def test_add_exclude_word_flexible_keeps_rows_without_whitespace(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "ws.pdf"
    _make_word_pdf(pdf_path)
    _add_markup(pdf_path, "SECRET", "山田 太郎")
    _add_markup(pdf_path, "SECRET", "山田太郎")
    window = _open_window(qtbot, monkeypatch, pdf_path, "flexible")
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_ALL)

    window._on_pii_add_exclude_word("山田 太郎")

    assert [a.pii_text for a in list_pii_markup_annots(str(pdf_path))] == ["山田太郎"]


def test_add_detect_word_drops_exclusions_from_all_modes(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "ws.pdf"
    _make_word_pdf(pdf_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)

    word = "山田 太郎"
    settings = window._pii_settings().copy()
    regexes = []
    for mode in MODES:
        body = literal_to_pattern(word, mode)
        regexes += [body, f"^{body}$"]
    regexes += [re.escape(word), f"^{re.escape(word)}$"]
    keep = [r"\d+", "^" + literal_to_pattern("別 語", "optional") + "$"]
    settings.text_exclusions_regex = regexes + keep
    settings.pattern_whitespace_mode = "flexible"  # 除外の登録時とは別のモードでも外す
    settings.save()
    window._pii_settings_cache = settings

    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_NONE)
    window._on_pii_add_detect_word("PERSON", word)

    for current in (window._pii_settings(), PiiSettings.load()):
        assert current.text_exclusions_regex == keep
        assert current.additional_patterns == [("PERSON", r"山田\s+太郎")]
