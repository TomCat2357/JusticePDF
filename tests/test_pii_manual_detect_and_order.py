"""結果一覧の右クリック「手動扱いで検出」「検出結果から削除」、ページ内の並び順、
「皆さん/皆様」の人名誤検出のテスト。"""
from __future__ import annotations

import fitz
import pytest
from PyQt6.QtWidgets import QMenu

from src.pii.entity_types import MANUAL_ENTITY_TYPE
from src.pii.regex_recognizers import detect_regex_entities
from src.pii.settings import PiiSettings
from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData, create_markup_annot, list_pii_markup_annots
from src.views.pii_panel import PiiPanel, PiiResultRow, ScopeChoiceDialog, reading_order_keys
from src.views.pii_settings_dialog import PiiSettingsDialog
from tests.helpers import open_zoom
from tests.test_page_edit_window_pii import _create_window, _silence_message_boxes

pytestmark = pytest.mark.usefixtures("qapp")


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------


def _make_pdf(path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=200)
        page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.save(str(path))
    doc.close()


def _add_markup(pdf_path, page_num: int, word: str, entity: str, pii_text: str | None = None) -> None:
    with fitz.open(str(pdf_path)) as doc:
        rect = doc[page_num].search_for(word)[0]
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=page_num,
            xref=0,
            quads=((rect.x0, rect.y0, rect.x1, rect.y1),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(0.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity=entity,
            pii_text=word if pii_text is None else pii_text,
        ),
    )


def _open_window(qtbot, monkeypatch, pdf_path):
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    return window


def _stub(window, name: str, scope: str):
    calls: list[str] = []

    def fake(text):
        calls.append(text)
        return scope

    setattr(window, name, fake)
    return calls


def _entities(pdf_path) -> list[tuple[int, str, str]]:
    return sorted((a.page_num, a.pii_entity, a.pii_text) for a in list_pii_markup_annots(str(pdf_path)))


# ---------------------------------------------------------------------------
# 手動扱いで検出(検出語に登録しない)
# ---------------------------------------------------------------------------


def test_detect_manual_adds_manual_candidates_without_touching_settings(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "m.pdf"
    _make_pdf(pdf_path, pages=2)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    before = window._pii_settings().copy()
    calls = _stub(window, "_ask_pii_manual_detect_scope", ScopeChoiceDialog.SCOPE_ALL)

    window._on_pii_detect_manual("SECRET")

    assert calls == ["SECRET"]
    assert _entities(pdf_path) == [(0, MANUAL_ENTITY_TYPE, "SECRET"), (1, MANUAL_ENTITY_TYPE, "SECRET")]
    assert window._pii_settings() == before
    loaded = PiiSettings.load()
    assert loaded.additional_patterns == []
    assert loaded.text_exclusions_regex == []
    # Undo 1回で戻る
    window._undo_manager.undo()
    assert _entities(pdf_path) == []


def test_detect_manual_page_scope_only_current_page(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "m.pdf"
    _make_pdf(pdf_path, pages=2)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub(window, "_ask_pii_manual_detect_scope", ScopeChoiceDialog.SCOPE_PAGE)

    window._on_pii_detect_manual("SECRET")

    assert _entities(pdf_path) == [(window._zoom_page_num, MANUAL_ENTITY_TYPE, "SECRET")]


def test_detect_manual_none_scope_does_nothing(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "m.pdf"
    _make_pdf(pdf_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub(window, "_ask_pii_manual_detect_scope", ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_detect_manual("SECRET")

    assert _entities(pdf_path) == []


def test_detect_manual_ignores_exclusions_and_survives_exclude_add(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "m.pdf"
    _make_pdf(pdf_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    settings = window._pii_settings().copy()
    settings.add_exclusion("^SECRET$")
    settings.save()
    window._pii_settings_cache = settings
    _stub(window, "_ask_pii_manual_detect_scope", ScopeChoiceDialog.SCOPE_ALL)

    window._on_pii_detect_manual("SECRET")  # 除外に入っている語でも検出できる

    assert _entities(pdf_path) == [(0, MANUAL_ENTITY_TYPE, "SECRET")]
    assert window._pii_settings().text_exclusions_regex == ["^SECRET$"]

    # その後「除外パターンに追加」しても、手動分は消えない
    _stub(window, "_ask_pii_exclude_scope", ScopeChoiceDialog.SCOPE_ALL)
    window._on_pii_add_exclude_word("SECRET")
    assert _entities(pdf_path) == [(0, MANUAL_ENTITY_TYPE, "SECRET")]


# ---------------------------------------------------------------------------
# 検出結果から削除(除外に登録しない)
# ---------------------------------------------------------------------------


def _prepare_removal_pdf(tmp_path):
    pdf_path = tmp_path / "r.pdf"
    _make_pdf(pdf_path, pages=2)
    _add_markup(pdf_path, 0, "SECRET", "PERSON")
    _add_markup(pdf_path, 1, "SECRET", "PERSON")
    _add_markup(pdf_path, 0, "SECRET", MANUAL_ENTITY_TYPE)  # 手動は消さない
    _add_markup(pdf_path, 0, "KEEPME", "PERSON")  # 別の語句は消さない
    return pdf_path


def test_remove_detected_all_pages_keeps_manual_and_settings_and_undo(qtbot, monkeypatch, tmp_path):
    pdf_path = _prepare_removal_pdf(tmp_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    before = window._pii_settings().copy()
    calls = _stub(window, "_ask_pii_remove_detected_scope", ScopeChoiceDialog.SCOPE_ALL)
    original = _entities(pdf_path)

    window._on_pii_remove_detected("SECRET")

    assert calls == ["SECRET"]
    assert _entities(pdf_path) == [
        (0, MANUAL_ENTITY_TYPE, "SECRET"),
        (0, "PERSON", "KEEPME"),
    ]
    assert window._pii_settings() == before
    assert PiiSettings.load().text_exclusions_regex == []
    window._undo_manager.undo()  # 1回で全部戻る
    assert _entities(pdf_path) == original


def test_remove_detected_page_scope_only_current_page(qtbot, monkeypatch, tmp_path):
    pdf_path = _prepare_removal_pdf(tmp_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub(window, "_ask_pii_remove_detected_scope", ScopeChoiceDialog.SCOPE_PAGE)

    window._on_pii_remove_detected("SECRET")

    page = window._zoom_page_num
    remaining = [e for e in _entities(pdf_path) if e[2] == "SECRET" and e[1] == "PERSON"]
    assert remaining == [(p, "PERSON", "SECRET") for p in (0, 1) if p != page]


def test_remove_detected_none_scope_does_nothing(qtbot, monkeypatch, tmp_path):
    pdf_path = _prepare_removal_pdf(tmp_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub(window, "_ask_pii_remove_detected_scope", ScopeChoiceDialog.SCOPE_NONE)
    original = _entities(pdf_path)

    window._on_pii_remove_detected("SECRET")

    assert _entities(pdf_path) == original


def test_panel_signals_are_connected_to_window_handlers(qtbot, monkeypatch, tmp_path):
    pdf_path = _prepare_removal_pdf(tmp_path)
    window = _open_window(qtbot, monkeypatch, pdf_path)
    _stub(window, "_ask_pii_remove_detected_scope", ScopeChoiceDialog.SCOPE_ALL)
    window._pii_panel.remove_detected_requested.emit("SECRET")
    assert (0, "PERSON", "SECRET") not in _entities(pdf_path)
    _stub(window, "_ask_pii_manual_detect_scope", ScopeChoiceDialog.SCOPE_ALL)
    window._pii_panel.detect_manual_requested.emit("KEEPME")
    # 同じページ・位置・語句が検出済み(PERSON)の箇所は重複として追加されない
    assert (0, MANUAL_ENTITY_TYPE, "KEEPME") not in _entities(pdf_path)
    assert (1, MANUAL_ENTITY_TYPE, "KEEPME") in _entities(pdf_path)


# ---------------------------------------------------------------------------
# パネルの右クリックメニュー
# ---------------------------------------------------------------------------


def _row(page_num, entity, text, bbox=(0.0, 0.0, 10.0, 10.0)) -> PiiResultRow:
    annot = TextMarkupAnnotData(
        page_num=page_num,
        xref=0,
        quads=(bbox,),
        markup_type=MarkupType.HIGHLIGHT,
        color=(1.0, 0.0, 0.0),
        pii_entity=entity,
        pii_text=text,
    )
    return PiiResultRow(
        annot=annot, page_num=page_num, entity=entity, text=text, kind="markup", bbox=bbox
    )


def _find_action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def _open_context_menu(panel, monkeypatch, pick):
    opened = {}

    def fake_exec(self, *_args, **_kwargs):
        opened["menu"] = self
        return _find_action(self, pick) if pick else None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    item = panel._result_tree.topLevelItem(0)
    panel._on_result_context_menu(panel._result_tree.visualItemRect(item).center())
    return opened


MANUAL_LABEL = "手動扱いで検出(検出語に登録しない)"
REMOVE_LABEL = "検出結果から削除(除外に登録しない)"


def test_context_menu_new_items_placed_next_to_persistent_ones_and_emit(qtbot, monkeypatch):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "山田太郎")])
    manual, removed = [], []
    panel.detect_manual_requested.connect(manual.append)
    panel.remove_detected_requested.connect(removed.append)

    opened = _open_context_menu(panel, monkeypatch, MANUAL_LABEL)
    texts = [a.text() for a in opened["menu"].actions()]
    assert texts.index("検出語に追加") + 1 == texts.index(MANUAL_LABEL)
    assert texts.index("除外パターンに追加") + 1 == texts.index(REMOVE_LABEL)
    assert manual == ["山田太郎"] and removed == []

    _open_context_menu(panel, monkeypatch, REMOVE_LABEL)
    assert removed == ["山田太郎"]


def test_context_menu_new_items_disabled_without_text(qtbot, monkeypatch):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_results([_row(0, "PERSON", "")])

    opened = _open_context_menu(panel, monkeypatch, None)

    assert _find_action(opened["menu"], MANUAL_LABEL).isEnabled() is False
    assert _find_action(opened["menu"], REMOVE_LABEL).isEnabled() is False


# ---------------------------------------------------------------------------
# ページ内の並び順
# ---------------------------------------------------------------------------


def _texts(panel) -> list[str]:
    tree = panel._result_tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _pages(panel) -> list[str]:
    tree = panel._result_tree
    return [tree.topLevelItem(i).text(2) for i in range(tree.topLevelItemCount())]


def test_horizontal_order_tolerates_small_y_offset_within_same_line(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    # 同じ行(高さ10)の語。右の語のほうが y が 2 上にずれている(単純な (y, x) だと逆転する)。
    rows = [
        _row(0, "PERSON", "右の語", (100.0, 98.0, 130.0, 108.0)),
        _row(0, "PERSON", "左の語", (10.0, 100.0, 40.0, 110.0)),
        _row(0, "PERSON", "次の行", (10.0, 130.0, 40.0, 140.0)),
        _row(0, "PERSON", "一番上", (200.0, 20.0, 230.0, 30.0)),
    ]
    panel.set_results(rows)

    assert _texts(panel) == ["一番上", "左の語", "右の語", "次の行"]


def test_vertical_order_right_column_first_then_top_to_bottom(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_result_order_mode("vertical")
    # 幅10の列。右列(x≈300)、左列(x≈100)。列内の x が少しずれても同じ列。
    rows = [
        _row(0, "PERSON", "左列上", (100.0, 20.0, 110.0, 30.0)),
        _row(0, "PERSON", "右列下", (302.0, 80.0, 312.0, 90.0)),
        _row(0, "PERSON", "右列上", (300.0, 20.0, 310.0, 30.0)),
        _row(0, "PERSON", "左列下", (100.0, 80.0, 110.0, 90.0)),
    ]
    panel.set_results(rows)

    assert _texts(panel) == ["右列上", "右列下", "左列上", "左列下"]


def test_page_sort_groups_by_page_then_position_and_descending_reverses_all(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [
        _row(1, "PERSON", "p2下", (10.0, 100.0, 20.0, 110.0)),
        _row(0, "PERSON", "p1右", (100.0, 10.0, 110.0, 20.0)),
        _row(1, "PERSON", "p2上", (10.0, 10.0, 20.0, 20.0)),
        _row(0, "PERSON", "p1左", (10.0, 10.0, 20.0, 20.0)),
    ]
    panel.set_results(rows)
    ascending = _texts(panel)
    assert ascending == ["p1左", "p1右", "p2上", "p2下"]

    panel.set_sort("page", ascending=False)
    assert _texts(panel) == list(reversed(ascending))


def test_text_and_entity_sort_use_position_as_secondary_key(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    rows = [
        _row(0, "PERSON", "同じ語", (100.0, 10.0, 110.0, 20.0)),
        _row(0, "PERSON", "同じ語", (10.0, 10.0, 20.0, 20.0)),
        _row(0, "PERSON", "同じ語", (10.0, 60.0, 20.0, 70.0)),
    ]
    panel.set_results(rows)
    for field in ("text", "entity"):
        panel.set_sort(field)
        bboxes = [
            panel._result_tree.topLevelItem(i).data(0, 0x0100).bbox for i in range(3)
        ]
        assert bboxes == [(10.0, 10.0, 20.0, 20.0), (100.0, 10.0, 110.0, 20.0), (10.0, 60.0, 20.0, 70.0)]


def _shape_row(page_num, bbox) -> PiiResultRow:
    row = _row(page_num, MANUAL_ENTITY_TYPE, "[図形]", bbox)
    row.kind = "shape"
    return row


def test_large_shape_does_not_merge_marker_lines(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    # 段落全体を覆う大きな図形(上端 y=10)と、その範囲内の3行のマーカー。
    # 各行は右の語のほうが左の語より下に少しずれている。
    rows = [
        _shape_row(0, (0.0, 10.0, 300.0, 200.0)),
        _row(0, "PERSON", "1行目右", (150.0, 42.0, 190.0, 52.0)),
        _row(0, "PERSON", "1行目左", (10.0, 40.0, 50.0, 50.0)),
        _row(0, "PERSON", "2行目右", (150.0, 92.0, 190.0, 102.0)),
        _row(0, "PERSON", "2行目左", (10.0, 90.0, 50.0, 100.0)),
        _row(0, "PERSON", "3行目左", (10.0, 140.0, 50.0, 150.0)),
    ]
    panel.set_results(rows)

    assert _texts(panel) == [
        "[図形]", "1行目左", "1行目右", "2行目左", "2行目右", "3行目左",
    ]


def test_large_shape_does_not_merge_marker_columns_in_vertical_mode(qtbot):
    panel = PiiPanel()
    qtbot.addWidget(panel)
    panel.set_result_order_mode("vertical")
    rows = [
        _shape_row(0, (0.0, 0.0, 300.0, 200.0)),  # 右端 x=300
        _row(0, "PERSON", "右列下", (252.0, 90.0, 262.0, 100.0)),
        _row(0, "PERSON", "右列上", (250.0, 10.0, 260.0, 20.0)),
        _row(0, "PERSON", "左列下", (150.0, 90.0, 160.0, 100.0)),
        _row(0, "PERSON", "左列上", (152.0, 10.0, 162.0, 20.0)),
    ]
    panel.set_results(rows)

    assert _texts(panel) == ["[図形]", "右列上", "右列下", "左列上", "左列下"]


def test_reading_order_keys_are_independent_per_page():
    rows = [
        _row(0, "PERSON", "a", (0.0, 500.0, 10.0, 510.0)),
        _row(1, "PERSON", "b", (0.0, 5.0, 10.0, 15.0)),
    ]
    keys = reading_order_keys(rows, vertical=False)
    assert keys[0][0] == 0 and keys[1][0] == 0


def test_window_builds_rows_with_bbox_from_first_quad_and_applies_settings_order(
    qtbot, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "o.pdf"
    _make_pdf(pdf_path)
    _add_markup(pdf_path, 0, "KEEPME", "PERSON")
    _add_markup(pdf_path, 0, "SECRET", "PERSON")
    window = _open_window(qtbot, monkeypatch, pdf_path)

    rows = window._build_pii_result_rows()
    by_text = {r.text: r for r in rows}
    assert by_text["SECRET"].bbox[0] < by_text["KEEPME"].bbox[0]
    window._reload_pii_results()
    assert _texts(window._pii_panel) == ["SECRET", "KEEPME"]  # 同じ行: 左→右

    settings = window._pii_settings().copy()
    settings.result_order_mode = "vertical"
    window._pii_settings_cache = settings
    window._sync_pii_panel_from_settings()
    # 縦書き: 右の列(KEEPME)が先
    assert _texts(window._pii_panel) == ["KEEPME", "SECRET"]


# ---------------------------------------------------------------------------
# 設定(保存・読込・ダイアログ)
# ---------------------------------------------------------------------------


def test_result_order_mode_default_round_trip_and_copy():
    assert PiiSettings().result_order_mode == "horizontal"
    settings = PiiSettings()
    settings.result_order_mode = "vertical"
    settings.save()
    assert PiiSettings.load().result_order_mode == "vertical"
    assert settings.copy().result_order_mode == "vertical"


def test_result_order_mode_old_settings_without_key_and_invalid_value():
    from PyQt6.QtCore import QSettings

    PiiSettings().save()
    QSettings().remove("pii/result_order_mode")  # 旧設定ファイルを模す
    assert PiiSettings.load().result_order_mode == "horizontal"
    QSettings().setValue("pii/result_order_mode", "bogus")
    assert PiiSettings.load().result_order_mode == "horizontal"


def test_settings_dialog_result_order_combo(qtbot):
    settings = PiiSettings()
    settings.result_order_mode = "vertical"
    dialog = PiiSettingsDialog(settings)
    qtbot.addWidget(dialog)
    combo = dialog._result_order_combo
    assert combo.currentData() == "vertical"
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "横書き(上→下・左→右)",
        "縦書き(右→左・上→下)",
    ]
    combo.setCurrentIndex(combo.findData("horizontal"))
    assert dialog.result_settings().result_order_mode == "horizontal"


# ---------------------------------------------------------------------------
# 「皆さん/皆様」の人名誤検出
# ---------------------------------------------------------------------------


def _person_texts(text: str) -> list[str]:
    return [r["text"] for r in detect_regex_entities(text, ["PERSON"])]


@pytest.mark.parametrize("word", ["皆さん", "皆様", "お客様", "奥様", "神様", "各位様"])
def test_generic_honorific_words_are_not_person(word):
    assert _person_texts(f"本日は{word}にお知らせします") == []


def test_real_names_with_honorific_still_detected():
    assert _person_texts("田中さんと林様と森くん") == ["田中さん", "林様", "森くん"]
    assert _person_texts("皆さんと田中さん") == ["田中さん"]
