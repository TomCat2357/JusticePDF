"""個人情報検出ドロワー(PageEditWindow統合)のテスト。

架空のダミーテキスト(山田太郎、090-1234-5678)を埋め込んだPDFを使う。
"""
from __future__ import annotations

import re

import fitz
import pytest

from src.models.undo_manager import UndoManager
from src.pii.settings import PiiSettings
from src.utils.pdf_utils import list_pii_markup_annots
from src.views import page_edit_pii as page_edit_pii_module
from src.views.page_edit_window import PageEditWindow
from src.views.pii_panel import ScopeChoiceDialog
from tests.helpers import open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


def _make_pii_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text(
        (40, 100), "山田太郎の電話番号は0901234567890", fontname="japan", fontsize=14
    )
    doc.save(str(path))
    doc.close()


def _make_job_title_pdf(path) -> None:
    """職業欄が直後の日付列と区切り文字無しで連結される表組みを模したPDF。

    「公務員」はどの自動検出エンティティにも該当しない(要望B/不具合Cの再現用)。
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 100), "丸尾幸男公務員昭和52年11月23日", fontname="japan", fontsize=14)
    doc.save(str(path))
    doc.close()


def _create_window(qtbot, pdf_path) -> PageEditWindow:
    window = PageEditWindow(str(pdf_path), UndoManager(max_size=20))
    qtbot.addWidget(window)
    window.show()
    window._load_pages()
    return window


def _stub_detect_scope(window, scope: str):
    """「検出語に追加」の範囲選択ダイアログ(``_ask_pii_detect_scope``)を、実際のモーダル表示無しに
    指定の範囲が選ばれたことにするスタブ。呼び出し引数は返り値のリストに記録される。
    """
    calls: list[tuple[str, str]] = []

    def fake(text, entity):
        calls.append((text, entity))
        return scope

    window._ask_pii_detect_scope = fake
    return calls


def _stub_exclude_scope(window, scope: str):
    """「除外語に追加」の範囲選択ダイアログ(``_ask_pii_exclude_scope``)のスタブ。"""
    calls: list[str] = []

    def fake(text):
        calls.append(text)
        return scope

    window._ask_pii_exclude_scope = fake
    return calls


def _silence_message_boxes(monkeypatch):
    """QMessageBox.information/warning を無害な no-op にし、呼び出しを記録する。"""
    shown: list[str] = []
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox,
        "information",
        staticmethod(lambda *a, **k: shown.append(str(a[2]) if len(a) > 2 else "")),
    )
    monkeypatch.setattr(
        page_edit_pii_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
    )
    return shown


def _remove_all_results(window) -> None:
    """結果一覧を全選択して「削除」する(「すべて削除」ボタンの廃止後の全件削除手順)。"""
    window._pii_panel._result_tree.selectAll()
    window._on_pii_remove_selected()


def test_pii_drawer_toggle_is_exclusive_with_annotation_drawer(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._toggle_zoom_annotation_drawer()
    assert window._zoom_annotation_open is True

    window._toggle_pii_drawer()
    assert window._pii_panel.is_open is True
    assert window._zoom_annotation_open is False


def test_panel_dropdown_replaces_separate_buttons_and_opens_one_panel(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-dropdown.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    btn = window._zoom_panel_btn
    assert btn.text() == "パネル"
    assert btn.isCheckable() and not btn.isChecked()
    assert [a.text() for a in window._zoom_panel_menu.actions()] == [
        "アノテーション",
        "個人情報検出",
        "OCR",
    ]
    # OCR も実際のドロワーを持つ(依存が無くてもパネルは開き、案内が出る)。
    assert window._zoom_ocr_action.isEnabled() is True
    # しおりは独立したボタンのまま。
    assert window._zoom_bookmark_btn.text() == "しおり"

    window._zoom_pii_btn.trigger()
    assert window._pii_panel.is_open is True
    assert btn.text() == "個人情報検出" and btn.isChecked()
    assert window._zoom_pii_btn.isChecked() and not window._zoom_object_btn.isChecked()

    # 別の項目を選ぶと切り替わる(ドロワーは排他)。
    window._zoom_object_btn.trigger()
    assert window._zoom_annotation_open is True
    assert window._pii_panel.is_open is False
    assert btn.text() == "アノテーション"
    assert window._zoom_object_btn.isChecked() and not window._zoom_pii_btn.isChecked()

    # 選択済みの項目をもう一度選ぶと閉じる。
    window._zoom_object_btn.trigger()
    assert window._zoom_annotation_open is False
    assert btn.text() == "パネル" and not btn.isChecked()
    assert not window._zoom_object_btn.isChecked()

    # プログラムからの開閉(しおり等との排他)も表示に追従する。
    window._toggle_pii_drawer()
    assert btn.text() == "個人情報検出" and window._zoom_pii_btn.isChecked()
    window._bookmarks_panel.set_open(True)
    assert window._pii_panel.is_open is False
    assert btn.text() == "パネル" and not btn.isChecked()

    # OCR も同じドロップダウンで排他的に開閉できる。
    window._zoom_ocr_action.trigger()
    assert window._ocr_panel.is_open is True
    assert window._bookmarks_panel.is_open is False
    assert btn.text() == "OCR" and btn.isChecked()
    assert window._zoom_ocr_action.isChecked() and not window._zoom_pii_btn.isChecked()
    window._zoom_pii_btn.trigger()
    assert window._ocr_panel.is_open is False and window._pii_panel.is_open is True
    assert btn.text() == "個人情報検出"
    window._zoom_object_btn.trigger()
    assert window._pii_panel.is_open is False and window._zoom_annotation_open is True
    window._zoom_ocr_action.trigger()
    assert window._zoom_annotation_open is False and window._ocr_panel.is_open is True
    window._zoom_ocr_action.trigger()  # 再選択で閉じる
    assert window._ocr_panel.is_open is False
    assert btn.text() == "パネル" and not btn.isChecked()


def test_pii_detection_creates_highlight_and_undo_removes_it(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    assert window._pii_worker is not None

    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)

    annots = list_pii_markup_annots(str(pdf_path))
    assert len(annots) >= 1
    assert {a.pii_entity for a in annots} & {"PERSON", "PHONE_NUMBER"}

    assert window._undo_manager.can_undo() is True
    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == len(annots)


def test_pii_detection_undo_redo_multiple_cycles(qtbot, tmp_path):
    """検出→取消→やり直し→取消 を繰り返しても件数が正しいこと(_AnnotRef回帰確認)。"""
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    count = len(list_pii_markup_annots(str(pdf_path)))
    assert count >= 1

    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count

    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.redo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count


def test_pii_remove_selected_undo_redo_multiple_cycles(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    count = len(list_pii_markup_annots(str(pdf_path)))
    assert count >= 1

    assert window._pii_panel._result_tree.topLevelItemCount() == count
    _remove_all_results(window)
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count

    window._undo_manager.redo()
    assert list_pii_markup_annots(str(pdf_path)) == []

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == count


def test_pii_select_all_and_delete_clears_highlights(qtbot, tmp_path):
    pdf_path = tmp_path / "pii.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    assert list_pii_markup_annots(str(pdf_path))

    _remove_all_results(window)
    assert list_pii_markup_annots(str(pdf_path)) == []


# ---------------------------------------------------------------------------
# 要望D: 「既存の結果を残して追加検出」
# ---------------------------------------------------------------------------


def test_keep_existing_checkbox_persists_to_settings(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-keep-persist.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    # set_keep_existing_checked() は「設定値をチェックボックスへ反映する」
    # 一方向の同期用API(シグナルを止めて書き換える)なので、ユーザー操作を
    # 模すにはチェックボックス自体を操作して toggled シグナルを発火させる。
    window._pii_panel._keep_existing_check.setChecked(True)
    assert window._pii_settings().keep_existing_on_detect is True


def test_keep_existing_detect_does_not_duplicate_or_remove_prior_results(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-keep.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    first_count = len(list_pii_markup_annots(str(pdf_path)))
    assert first_count >= 1

    window._pii_panel._keep_existing_check.setChecked(True)
    undo_depth_before = window._undo_manager.undo_count()
    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)
    # 同じページを検出し直しただけ(検出結果は同一)なので、既存分は消えず、
    # 重複した新規分も追加されない。新規追加が0件なので、Undoスタックにも
    # 積まれない(押しても直前の検出まで戻ってしまわないこと)。
    assert len(list_pii_markup_annots(str(pdf_path))) == first_count
    assert window._undo_manager.undo_count() == undo_depth_before


# ---------------------------------------------------------------------------
# 結果一覧の右クリック「検出語に追加」「除外語に追加」
# ---------------------------------------------------------------------------


def test_add_detect_word_registers_pattern_without_detecting(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-detect-word-register-only.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    calls = _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_detect_word("PERSON", "公務員")

    settings = window._pii_settings()
    assert ("PERSON", "公務員") in settings.additional_patterns
    assert list_pii_markup_annots(str(pdf_path)) == []
    # 範囲選択ダイアログには語句と選んだ種別が渡される。
    assert calls == [("公務員", "PERSON")]
    # 永続化もされている。
    assert ("PERSON", "公務員") in PiiSettings.load().additional_patterns


def test_add_detect_word_ignores_empty_text_and_non_detectable_entity(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-detect-word-invalid.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    calls = _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_ALL)

    window._on_pii_add_detect_word("PERSON", "")
    window._on_pii_add_detect_word("MANUAL", "公務員")  # 手動は検出語の種別にできない

    assert window._pii_settings().additional_patterns == []
    assert calls == []


def test_add_detect_word_does_not_register_duplicate_pattern(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-detect-word-dup.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_detect_word("PERSON", "公務員")
    window._on_pii_add_detect_word("PERSON", "公務員")

    assert window._pii_settings().additional_patterns.count(("PERSON", "公務員")) == 1


def test_add_detect_word_and_detect_current_page_adds_new_markup(qtbot, monkeypatch, tmp_path):
    """不具合Cの再現条件(隣接する日付列と区切り文字無しで連結)でも検出できること。"""
    pdf_path = tmp_path / "pii-detect-word-page.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_PAGE)

    window._on_pii_add_detect_word("PERSON", "公務員")

    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_text == "公務員" and a.pii_entity == "PERSON" for a in annots)

    # 同じパターンでもう一度部分再検出しても重複追加されないこと。
    count_after_first = len(annots)
    window._run_pattern_only_detection("PERSON", re.escape("公務員"), [0])
    assert len(list_pii_markup_annots(str(pdf_path))) == count_after_first


def test_add_detect_word_and_detect_all_pages(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-detect-word-all.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_ALL)

    window._on_pii_add_detect_word("PERSON", "公務員")

    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_text == "公務員" and a.pii_entity == "PERSON" for a in annots)


def test_add_detect_word_uses_chosen_entity(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-detect-word-entity.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_PAGE)

    window._on_pii_add_detect_word("LOCATION", "公務員")

    settings = window._pii_settings()
    assert ("LOCATION", "公務員") in settings.additional_patterns
    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_text == "公務員" and a.pii_entity == "LOCATION" for a in annots)
    assert not any(a.pii_entity == "MANUAL" for a in annots)


def test_add_detect_word_removes_identical_exclusions_everywhere(qtbot, monkeypatch, tmp_path):
    """同じ語句の除外(除外語・種別別・除外パターン)は検出語登録時に取り除く。他の語句は残す。"""
    pdf_path = tmp_path / "pii-detect-word-drop-exclusion.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)

    word = "山田(仮)"  # 正規表現の記号を含む(re.escape で変わる)語句
    settings = window._pii_settings().copy()
    settings.excluded_words = [word, "残る除外語"]
    settings.entity_exclusions = {
        "PERSON": [word, "別語"],
        "LOCATION": [word],
        "OTHER": ["残る語"],
    }
    settings.text_exclusions_regex = [re.escape(word), f"^{re.escape(word)}$", r"\d+"]
    settings.save()
    window._pii_settings_cache = settings

    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_NONE)
    window._on_pii_add_detect_word("PERSON", word)

    for current in (window._pii_settings(), PiiSettings.load()):
        assert current.excluded_words == ["残る除外語"]
        assert current.entity_exclusions == {"PERSON": ["別語"], "OTHER": ["残る語"]}
        assert current.text_exclusions_regex == [r"\d+"]
        assert ("PERSON", re.escape(word)) in current.additional_patterns


def test_detect_word_scan_respects_remaining_exclusions(qtbot, monkeypatch, tmp_path):
    """除外が最優先: 除外に残っている語句は、検出語に登録済みでも部分再検出されない。"""
    pdf_path = tmp_path / "pii-detect-word-excluded.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    settings = window._pii_settings().copy()
    settings.excluded_words = ["公務員"]
    window._pii_settings_cache = settings

    window._run_pattern_only_detection("PERSON", re.escape("公務員"), [0])

    assert list_pii_markup_annots(str(pdf_path)) == []


def test_ask_detect_and_exclude_scope_factories_use_scope_dialog(qtbot, monkeypatch, tmp_path):
    """範囲選択のファクトリは ScopeChoiceDialog.ask に委譲する(テストが差し替える境界)。"""
    pdf_path = tmp_path / "pii-scope-factory.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    seen = []

    def fake_ask(title, message, word, labels, parent=None):
        seen.append((title, word, labels, parent))
        return ScopeChoiceDialog.SCOPE_PAGE

    monkeypatch.setattr(page_edit_pii_module.ScopeChoiceDialog, "ask", staticmethod(fake_ask))

    assert window._ask_pii_detect_scope("語", "PERSON") == "page"
    assert window._ask_pii_exclude_scope("語") == "page"
    assert seen[0] == ("検出語に追加", "語", ("全ページで検出", "このページだけ検出", "検出しない"), window)
    assert seen[1] == (
        "除外語に追加",
        "語",
        ("全ページの検出済みを削除", "このページだけ削除", "削除しない"),
        window,
    )


def test_add_exclude_word_registers_without_touching_detect_words(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-exclude-word-register.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    settings = window._pii_settings().copy()
    settings.additional_patterns = [("PERSON", "公務員")]
    settings.save()
    window._pii_settings_cache = settings
    calls = _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_exclude_word("公務員")

    for current in (window._pii_settings(), PiiSettings.load()):
        assert current.excluded_words == ["公務員"]
        # 除外語の追加では検出語(追加パターン)から外さない(除外が優先されるだけ)。
        assert current.additional_patterns == [("PERSON", "公務員")]
    assert calls == ["公務員"]


def _make_two_page_word_pdf(path) -> None:
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=400, height=200)
        page.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.save(str(path))
    doc.close()


def _add_markup_for(pdf_path, page_num: int, word: str, entity: str) -> None:
    from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData, create_markup_annot

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
            pii_text=word,
        ),
    )


def test_add_exclude_word_deletes_detected_all_pages_except_manual_one_undo(
    qtbot, monkeypatch, tmp_path
):
    pdf_path = tmp_path / "pii-exclude-word-all.pdf"
    _make_two_page_word_pdf(pdf_path)
    _add_markup_for(pdf_path, 0, "SECRET", "PERSON")
    _add_markup_for(pdf_path, 1, "SECRET", "PERSON")
    _add_markup_for(pdf_path, 1, "SECRET", "MANUAL")  # 手動で置いたものは消さない
    _add_markup_for(pdf_path, 0, "KEEPME", "PERSON")  # 別の語句は消さない
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_ALL)
    undo_depth = window._undo_manager.undo_count()

    window._on_pii_add_exclude_word("SECRET")

    remaining = [(a.page_num, a.pii_entity, a.pii_text) for a in list_pii_markup_annots(str(pdf_path))]
    assert sorted(remaining) == [(0, "PERSON", "KEEPME"), (1, "MANUAL", "SECRET")]
    assert window._pii_settings().excluded_words == ["SECRET"]
    assert window._undo_manager.undo_count() == undo_depth + 1  # Undoは1回分

    window._undo_manager.undo()
    assert len(list_pii_markup_annots(str(pdf_path))) == 4


def test_add_exclude_word_deletes_only_current_page_when_page_scope(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-exclude-word-page.pdf"
    _make_two_page_word_pdf(pdf_path)
    _add_markup_for(pdf_path, 0, "SECRET", "PERSON")
    _add_markup_for(pdf_path, 1, "SECRET", "PERSON")
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)  # ページ1(index 0)を拡大表示
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_PAGE)

    window._on_pii_add_exclude_word("SECRET")

    remaining = [(a.page_num, a.pii_text) for a in list_pii_markup_annots(str(pdf_path))]
    assert remaining == [(1, "SECRET")]
    assert window._pii_settings().excluded_words == ["SECRET"]


def test_add_exclude_word_none_scope_keeps_existing_results(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-exclude-word-none.pdf"
    _make_two_page_word_pdf(pdf_path)
    _add_markup_for(pdf_path, 0, "SECRET", "PERSON")
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_NONE)

    window._on_pii_add_exclude_word("SECRET")

    assert len(list_pii_markup_annots(str(pdf_path))) == 1
    assert window._pii_settings().excluded_words == ["SECRET"]
    # 同じ語を再度追加しても二重登録しない。
    window._on_pii_add_exclude_word("SECRET")
    assert window._pii_settings().excluded_words == ["SECRET"]


def test_add_detect_word_after_exclude_removes_it_from_excluded_words(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-exclude-then-detect.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _silence_message_boxes(monkeypatch)
    _stub_exclude_scope(window, ScopeChoiceDialog.SCOPE_NONE)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_PAGE)

    window._on_pii_add_exclude_word("公務員")
    assert window._pii_settings().excluded_words == ["公務員"]

    window._on_pii_add_detect_word("PERSON", "公務員")

    assert window._pii_settings().excluded_words == []
    assert any(a.pii_text == "公務員" for a in list_pii_markup_annots(str(pdf_path)))


def test_add_detect_word_detection_only_adds_new_items_keeps_existing_untouched(
    qtbot, monkeypatch, tmp_path
):
    """検出語追加後の部分再検出は追加分だけで、既存の結果・手動選択は

    消したり作り直したりしない(xrefも維持される)こと。
    """
    from src.utils.pdf_utils import (
        MarkupType,
        ShapeAnnotData,
        ShapeType,
        TextMarkupAnnotData,
        create_markup_annot,
        create_shape_annot,
        list_pii_mask_shapes,
    )

    pdf_path = tmp_path / "pii-pattern-keeps-existing.pdf"
    _make_job_title_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    # 既存の自動検出結果を模した塗りつぶし候補。
    existing_markup = create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((40.0, 88.0, 88.0, 102.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity="PERSON",
            pii_text="丸尾幸男",
        ),
    )
    # ユーザーが手動で「テキスト候補」ツールにより塗りつぶし対象にした図形。
    existing_shape = create_shape_annot(
        str(pdf_path),
        ShapeAnnotData(
            page_num=0,
            xref=0,
            rect=(200.0, 10.0, 240.0, 40.0),
            shape_type=ShapeType.RECTANGLE,
            stroke_color=(0.0, 0.0, 0.0),
            fill_color=(0.0, 0.0, 0.0),
            stroke_width=1.2,
            opacity=0.35,
            pii_entity="MANUAL",
            pii_text="",
        ),
    )

    _silence_message_boxes(monkeypatch)
    _stub_detect_scope(window, ScopeChoiceDialog.SCOPE_ALL)
    window._on_pii_add_detect_word("PERSON", "公務員")

    markups = list_pii_markup_annots(str(pdf_path))
    shapes = list_pii_mask_shapes(str(pdf_path))

    # 既存の塗りつぶし候補・図形はxrefも含めてそのまま(削除→再作成されていない)。
    assert any(
        a.xref == existing_markup.xref and a.pii_text == "丸尾幸男" for a in markups
    )
    assert any(s.xref == existing_shape.xref for s in shapes)
    # 新しいパターンでの検出分だけが追加されている。
    assert any(a.pii_text == "公務員" and a.pii_entity == "PERSON" for a in markups)
    assert len(markups) == 2
    assert len(shapes) == 1


# ---------------------------------------------------------------------------
# 塗りつぶしの色・透明度(全種別共通)と、種別ごとの表示・検出チェックボックス
# ---------------------------------------------------------------------------


def _detect_current_page(window, qtbot) -> None:
    window._on_pii_detect_current_page()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=15000)


def _center_of_first_quad(zoom, annot):
    from PyQt6.QtCore import QPoint

    quad_rect = zoom._page_rect_to_widget_rect(zoom._rect_tuple_to_qrectf(annot.quads[0]))
    return QPoint(int(quad_rect.center().x()), int(quad_rect.center().y()))


def _rgb(pixel) -> tuple[int, int, int]:
    return (pixel.red(), pixel.green(), pixel.blue())


def test_mask_style_live_update_recolors_existing_marks_and_persists(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog

    pdf_path = tmp_path / "pii-mask-style.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _detect_current_page(window, qtbot)

    zoom = window._zoom_label
    panel = window._pii_panel
    target = next(a for a in zoom._annotations if getattr(a, "pii_entity", ""))
    center = _center_of_first_quad(zoom, target)

    # 既定は黒・透明度70(不透明度0.3)。
    assert zoom.pii_mask_style()[0] == (0.0, 0.0, 0.0)
    assert zoom.pii_mask_style()[1] == pytest.approx(0.3)
    faint = _rgb(zoom.grab().toImage().pixelColor(center))
    assert faint != (0, 0, 0)

    # 透明度0(不透明)にすると、その場で候補の中心がベタ塗りになる。
    panel._transparency_slider.setValue(0)
    assert _rgb(zoom.grab().toImage().pixelColor(center)) == (0, 0, 0)
    assert window._pii_settings().mask_transparency == 0

    # 色を変えると、既に作成済みの候補もその色に変わる(注釈自体の色は使わない)。
    stored_color = target.color
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor(255, 0, 0)))
    panel._color_btn.click()
    assert _rgb(zoom.grab().toImage().pixelColor(center)) == (255, 0, 0)
    assert stored_color != (1.0, 0.0, 0.0)
    assert zoom.pii_mask_style()[0] == (1.0, 0.0, 0.0)

    # 透明度100(完全に透明)なら塗りは見えなくなる(枠だけ薄く残る)。
    panel._transparency_slider.setValue(100)
    assert zoom.pii_mask_style()[1] == 0.0
    assert _rgb(zoom.grab().toImage().pixelColor(center)) != (255, 0, 0)

    # 設定は保存され、次回ウィンドウを開いたときにも引き継がれる。
    loaded = PiiSettings.load()
    assert loaded.mask_color == (1.0, 0.0, 0.0)
    assert loaded.mask_transparency == 100
    window2 = _create_window(qtbot, pdf_path)
    assert window2._pii_panel.mask_transparency() == 100
    assert window2._pii_panel.mask_color() == (1.0, 0.0, 0.0)
    assert window2._zoom_label.pii_mask_style() == ((1.0, 0.0, 0.0), 0.0)


def test_slider_drag_updates_canvas_but_saves_only_on_release(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "pii-slider-drag.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    saves = []
    original_save = PiiSettings.save

    def counting_save(self, *a, **k):
        saves.append(self.mask_transparency)
        return original_save(self, *a, **k)

    monkeypatch.setattr(PiiSettings, "save", counting_save)
    slider = window._pii_panel._transparency_slider
    slider.setSliderDown(True)
    for value in (60, 50, 40, 30):
        slider.setValue(value)
    # ドラッグ中はキャンバスへ即時反映されるが保存はしない。
    assert window._zoom_label.pii_mask_style()[1] == pytest.approx(0.7)
    assert saves == []
    slider.setSliderDown(False)  # 離した時点で1回だけ保存
    assert saves == [30]
    assert PiiSettings.load().mask_transparency == 30


def test_unchecked_entity_is_hidden_everywhere_and_persists(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-entity-hidden.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _detect_current_page(window, qtbot)

    zoom = window._zoom_label
    panel = window._pii_panel
    total = len(list_pii_markup_annots(str(pdf_path)))
    assert panel._result_tree.topLevelItemCount() == total
    target = next(a for a in zoom._annotations if getattr(a, "pii_entity", ""))
    entity = target.pii_entity
    center = _center_of_first_quad(zoom, target)
    assert zoom._annotation_hit_test(center)[0] is not None
    assert zoom._pii_target_at(center) is not None
    painted = zoom.grab().toImage()

    panel._entity_checks[entity].setChecked(False)

    # 一覧・キャンバス(描画・クリック・ホバー)から外れる。PDF上の注釈は消えない。
    assert entity in zoom.pii_hidden_entities()
    rows = window._build_pii_result_rows()
    assert all(r.entity != entity for r in rows)
    assert panel._result_tree.topLevelItemCount() == len(rows) < total
    assert zoom._annotation_hit_test(center)[0] is None
    assert zoom._pii_target_at(center) is None
    assert zoom.grab().toImage() != painted
    assert len(list_pii_markup_annots(str(pdf_path))) == total
    # 検出対象の設定(単一の情報源)にも反映され、保存される。
    assert window._pii_settings().enabled_entities[entity] is False
    assert PiiSettings.load().enabled_entities[entity] is False
    assert entity not in window._pii_settings().enabled_entity_list()

    # 再チェックで元に戻る。
    panel._entity_checks[entity].setChecked(True)
    assert entity not in zoom.pii_hidden_entities()
    assert zoom._annotation_hit_test(center)[0] is not None
    assert zoom.grab().toImage() == painted

    # 次回ウィンドウでもチェック状態が引き継がれる。
    panel._entity_checks[entity].setChecked(False)
    window2 = _create_window(qtbot, pdf_path)
    assert window2._pii_panel._entity_checks[entity].isChecked() is False
    assert entity in window2._zoom_label.pii_hidden_entities()


def test_unchecking_entity_clears_selection_of_that_entity(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-entity-hidden-selection.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _detect_current_page(window, qtbot)

    target = next(a for a in window._zoom_label._annotations if getattr(a, "pii_entity", ""))
    window._set_selected_zoom_annotation(target, open_drawer=False)
    assert window._selected_zoom_annotation is not None

    window._pii_panel._entity_checks[target.pii_entity].setChecked(False)

    assert window._selected_zoom_annotation is None


def test_unchecked_entities_are_not_detected(qtbot, tmp_path):
    pdf_path = tmp_path / "pii-entity-not-detected.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()

    for entity, check in window._pii_panel._entity_checks.items():
        if entity != "MANUAL":
            check.setChecked(False)
    _detect_current_page(window, qtbot)

    assert list_pii_markup_annots(str(pdf_path)) == []


def test_redetect_replaces_only_visible_entities_and_keeps_hidden(qtbot, tmp_path):
    """既定(既存結果を置換)の再検出は、チェックが外れた種別の既存結果には触れない。"""
    from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData, create_markup_annot

    pdf_path = tmp_path / "pii-redetect-hidden.pdf"
    _make_pii_pdf(pdf_path)
    hidden_annot = create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((10.0, 10.0, 50.0, 30.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(0.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity="YEAR",
            pii_text="SAMPLEYEAR",
        ),
    )
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    window._pii_panel._entity_checks["YEAR"].setChecked(False)

    _detect_current_page(window, qtbot)

    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.xref == hidden_annot.xref and a.pii_entity == "YEAR" for a in annots)


def test_unchecking_manual_disables_and_disarms_manual_tools(qtbot, tmp_path):
    from src.views.page_edit_annotations import CreateMode

    pdf_path = tmp_path / "pii-manual-hidden.pdf"
    _make_pii_pdf(pdf_path)
    window = _create_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    panel = window._pii_panel

    panel._mask_rect_btn.click()
    assert window._create_mode is CreateMode.MASK_SHAPE

    panel._entity_checks["MANUAL"].setChecked(False)

    assert window._create_mode is CreateMode.NONE
    assert not panel._mask_rect_btn.isChecked()
    assert not panel._mask_markup_btn.isEnabled()
    assert not panel._mask_rect_btn.isEnabled()
    assert not panel._mask_ellipse_btn.isEnabled()
    assert window._pii_settings().manual_visible is False
    assert "MANUAL" in window._zoom_label.pii_hidden_entities()
    # 手動は enabled_entities(検出対象)には混ぜない。
    assert "MANUAL" not in window._pii_settings().enabled_entities

    panel._entity_checks["MANUAL"].setChecked(True)
    assert panel._mask_rect_btn.isEnabled()
    assert window._pii_settings().manual_visible is True


def test_pii_mask_overlay_paints_markup_and_ellipse_only_inside():
    """ページ一覧サムネイル用の重ね描き: マーカーは矩形、塗り丸は楕円の内側だけを塗る。

    色は注釈自体の色ではなく、引数(設定の全種別共通の色)で塗られる。
    """
    from PyQt6.QtGui import QColor, QPixmap

    from src.utils.pdf_utils import MarkupType, ShapeAnnotData, ShapeType, TextMarkupAnnotData
    from src.views.page_edit_widgets import paint_pii_mask_overlay

    base = QPixmap(100, 50)
    base.fill(QColor("white"))
    markup = TextMarkupAnnotData(
        page_num=0,
        xref=1,
        quads=((20.0, 20.0, 60.0, 40.0),),
        markup_type=MarkupType.HIGHLIGHT,
        color=(0.0, 1.0, 0.0),  # 注釈自体の色(使われない)
        opacity=0.35,
        pii_entity="PERSON",
    )
    ellipse = ShapeAnnotData(
        page_num=0,
        xref=2,
        rect=(100.0, 0.0, 200.0, 100.0),
        shape_type=ShapeType.ELLIPSE,
        stroke_color=(0.0, 0.0, 1.0),  # 注釈自体の色(使われない)
        fill_color=(0.0, 0.0, 0.0),
        stroke_width=1.2,
        opacity=0.35,
        pii_entity="LOCATION",
    )
    image = paint_pii_mask_overlay(
        base, [markup, ellipse], (200.0, 100.0), (1.0, 0.0, 0.0), 1.0
    ).toImage()

    marker_px = image.pixelColor(20, 15)  # quad の内側(縮尺0.5)
    assert _rgb(marker_px) == (255, 0, 0)
    center_px = image.pixelColor(75, 25)  # 楕円の中心
    assert _rgb(center_px) == (255, 0, 0)
    corner_px = image.pixelColor(51, 1)  # 外接矩形の角(楕円の外側)は塗らない
    assert _rgb(corner_px) == (255, 255, 255)


def test_page_list_thumbnail_shows_pii_mask_targets(qtbot, tmp_path):
    """ページ一覧のサムネイルにも塗りつぶし対象が種別色で重ね描きされる。"""
    from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData, create_markup_annot

    pdf_path = tmp_path / "pii_thumb.pdf"
    _make_pii_pdf(pdf_path)
    create_markup_annot(
        str(pdf_path),
        TextMarkupAnnotData(
            page_num=0,
            xref=0,
            quads=((0.0, 0.0, 400.0, 200.0),),
            markup_type=MarkupType.HIGHLIGHT,
            color=(1.0, 0.0, 0.0),
            opacity=0.35,
            pii_entity="PERSON",
            pii_text="山田太郎",
        ),
    )
    window = _create_window(qtbot, pdf_path)
    style = window._pii_settings().copy()
    style.mask_color = (1.0, 0.0, 0.0)
    style.mask_transparency = 0
    window._pii_settings_cache = style
    window._reset_thumbnail_render_queue()
    window._thumbnails[0].invalidate_thumbnail()
    window._thumb_render_queue.append(0)
    window._thumb_render_queue_set.add(0)
    window._process_thumbnail_render_queue()
    thumb = window._thumbnails[0]
    assert thumb.thumbnail_loaded

    image = thumb._image_label.pixmap().toImage()
    px = image.pixelColor(image.width() // 2, 2)
    # 実PDF注釈(不透明度0.35=暗い灰色)ではなく、設定の色(赤)の重ね描きになる。
    assert px.red() > 200 and px.green() < 200 and px.blue() < 200

    # 種別のチェックを外すと、サムネイルにも描かれない。
    hidden_style = style.copy()
    hidden_style.enabled_entities["PERSON"] = False
    window._pii_settings_cache = hidden_style
    window._invalidate_and_requeue_thumbnails()
    window._process_thumbnail_render_queue()
    image = window._thumbnails[0]._image_label.pixmap().toImage()
    px = image.pixelColor(image.width() // 2, 2)
    assert not (px.red() > 200 and px.green() < 200 and px.blue() < 200)


def test_pii_mask_overlay_uses_opacity_and_skips_hidden_entities():
    """ページ一覧の重ね描きも、設定の不透明度に従い、非表示の種別は描かない。"""
    from PyQt6.QtGui import QColor, QPixmap

    from src.utils.pdf_utils import MarkupType, TextMarkupAnnotData
    from src.views.page_edit_widgets import paint_pii_mask_overlay

    base = QPixmap(100, 50)
    base.fill(QColor("white"))
    markup = TextMarkupAnnotData(
        page_num=0,
        xref=1,
        quads=((20.0, 20.0, 60.0, 40.0),),
        markup_type=MarkupType.HIGHLIGHT,
        color=(1.0, 0.0, 0.0),
        opacity=0.35,
        pii_entity="PERSON",
    )
    black_px = paint_pii_mask_overlay(base, [markup], (200.0, 100.0), (0.0, 0.0, 0.0), 1.0).toImage().pixelColor(20, 15)
    assert _rgb(black_px) == (0, 0, 0)
    half_px = paint_pii_mask_overlay(base, [markup], (200.0, 100.0), (0.0, 0.0, 0.0), 0.5).toImage().pixelColor(20, 15)
    assert 100 < half_px.red() < 160 and half_px.red() == half_px.green() == half_px.blue()
    clear_px = paint_pii_mask_overlay(base, [markup], (200.0, 100.0), (0.0, 0.0, 0.0), 0.0).toImage().pixelColor(20, 15)
    assert _rgb(clear_px) == (255, 255, 255)
    hidden_px = paint_pii_mask_overlay(
        base, [markup], (200.0, 100.0), (0.0, 0.0, 0.0), 1.0, {"PERSON"}
    ).toImage().pixelColor(20, 15)
    assert _rgb(hidden_px) == (255, 255, 255)
