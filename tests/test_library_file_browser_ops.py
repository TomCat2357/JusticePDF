"""ライブラリのファイルブラウザ化（後半）のテスト。

外部ドロップは変換せずコピー、インポートメニューは従来どおり変換、［PDFに変換］、
PDF 専用操作が PDF 以外を対象にしないこと、名前変更の拡張子保持、種類での絞り込みと種類順。
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from src.utils import app_settings, file_kinds
from src.views import main_window, main_window_import, pdf_card
from src.workers import import_worker
from tests.helpers import FakeWatcher, make_pdf


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture
def make_window(monkeypatch, qtbot, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(main_window, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(pdf_card, "get_pdf_card_info", lambda _p, _s: (QPixmap(), 1))
    # 完了報告のメッセージボックスで止まらないようにする（呼ばれた内容は messages に残す）
    messages: list[str] = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, _n=name, **k: messages.append(str(a[2]))),
        )

    def _create(work_dir: Path):
        window = main_window.MainWindow(str(work_dir))
        qtbot.addWidget(window)
        window.show()
        window._test_messages = messages
        return window

    return _create


def _drain(win) -> None:
    QApplication.processEvents()
    worker = win._active_import_worker
    if worker is not None:
        worker.wait()
    QApplication.processEvents()


def _select(window, *names: str) -> None:
    window._clear_selection()
    for card in window._cards:
        if card.filename in names:
            card.set_selected(True)
            window._selected_cards.append(card)
    for fc in window._folder_cards:
        if fc.filename in names:
            fc.set_selected(True)
            window._selected_folder_cards.append(fc)
    window._update_button_states()


def _fake_converters(monkeypatch):
    """変換本体はモック（出力先に印だけ書く）。"""
    calls: list[tuple[str, str]] = []

    def fake_office(src, dest, soffice):
        calls.append(("office", src))
        Path(dest).write_bytes(b"%PDF-fake")

    def fake_image(src, dest):
        calls.append(("image", src))
        Path(dest).write_bytes(b"%PDF-fake")

    monkeypatch.setattr(import_worker, "_convert_office", fake_office)
    monkeypatch.setattr(import_worker, "_convert_image", fake_image)
    return calls


# ---- 外部ドロップ: 変換せずコピー --------------------------------------------

def test_drop_copies_without_converting(make_window, work, tmp_path, monkeypatch):
    calls = _fake_converters(monkeypatch)
    src = tmp_path / "outside"
    src.mkdir()
    (src / "report.docx").write_bytes(b"DOCX-BYTES")
    (src / "note.txt").write_text("こんにちは")
    (src / "data.xyz123").write_bytes(b"\x00\x01")
    (src / "empty.dat").write_bytes(b"")
    (src / "bundle.zip").write_bytes(b"PK-not-expanded")
    window = make_window(work)

    window._handle_external_file_drop([QUrl.fromLocalFile(str(p)) for p in sorted(src.iterdir())])
    _drain(window)

    assert calls == []  # 変換は呼ばれない
    assert sorted(p.name for p in work.iterdir()) == [
        "bundle.zip", "data.xyz123", "empty.dat", "note.txt", "report.docx"]
    assert (work / "report.docx").read_bytes() == b"DOCX-BYTES"
    assert (work / "empty.dat").read_bytes() == b""
    assert (work / "bundle.zip").read_bytes() == b"PK-not-expanded"  # 展開しない


def test_drop_skips_junk_and_renames_collision(make_window, work, tmp_path):
    (work / "a.txt").write_text("old")
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("new")
    (src / "~$tmp.docx").write_bytes(b"x")
    (src / "x.tmp").write_bytes(b"x")
    window = make_window(work)

    window._handle_external_file_drop([QUrl.fromLocalFile(str(p)) for p in src.iterdir()])
    _drain(window)

    assert sorted(p.name for p in work.iterdir()) == ["a(1).txt", "a.txt"]
    assert (work / "a.txt").read_text() == "old"
    assert (work / "a(1).txt").read_text() == "new"


def test_drop_folder_keeps_structure_and_empty_dirs(make_window, work, tmp_path):
    src = tmp_path / "proj"
    (src / "sub" / "deep").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "top.xlsx").write_bytes(b"X")
    (src / "sub" / "deep" / "a.pptx").write_bytes(b"P")
    window = make_window(work)

    window._handle_external_file_drop([QUrl.fromLocalFile(str(src))])
    _drain(window)

    assert (work / "proj" / "top.xlsx").read_bytes() == b"X"
    assert (work / "proj" / "sub" / "deep" / "a.pptx").read_bytes() == b"P"
    assert (work / "proj" / "empty").is_dir()


def test_drop_on_folder_card_copies_into_it(make_window, work, tmp_path):
    (work / "dest").mkdir()
    src = tmp_path / "f.docx"
    src.write_bytes(b"D")
    window = make_window(work)
    fc = window._folder_cards[0]

    window._on_folder_card_dropped_on(fc, {"text/uri-list": str(src)})
    _drain(window)

    assert (work / "dest" / "f.docx").read_bytes() == b"D"


def test_drop_undo_trashes_copies(make_window, work, tmp_path, monkeypatch):
    trashed: list[str] = []
    monkeypatch.setattr(main_window_import, "send2trash", lambda p: trashed.append(p))
    src = tmp_path / "m.txt"
    src.write_text("x")
    window = make_window(work)
    window._import_paths([str(src)], copy_only=True)
    _drain(window)

    window._undo_manager.undo()
    assert [Path(p).name for p in trashed] == ["m.txt"]


def test_import_menu_path_still_converts(make_window, work, tmp_path, monkeypatch):
    calls = _fake_converters(monkeypatch)
    src = tmp_path / "doc.docx"
    src.write_bytes(b"D")
    (tmp_path / "other.xyz").write_bytes(b"?")
    window = make_window(work)

    window._import_paths([str(src), str(tmp_path / "other.xyz")])  # copy_only なし = インポート
    _drain(window)

    assert calls == [("office", str(src))]
    assert sorted(p.name for p in work.iterdir()) == ["doc.pdf"]  # 変換され、未対応は取り込まれない


# ---- ［PDFに変換］ --------------------------------------------------------

def test_convert_kind_rules():
    ck = file_kinds.convert_kind
    for name in ("a.doc", "a.DOCX", "a.docm", "a.xls", "a.xlsx", "a.xlsm", "a.ppt", "a.pptx"):
        assert ck(name) == "office"
    assert ck("a.txt") == "text"
    for name in ("a.png", "a.jpg", "a.jpeg", "a.gif", "a.webp", "a.bmp", "a.svg", "a.tif"):
        assert ck(name) == "image"
    assert ck("a.md") == "markdown" and ck("a.MARKDOWN") == "markdown"
    for name in ("a.pdf", "a.csv", "a.zip", "a.xyz", "noext"):
        assert ck(name) is None


def test_convert_names_and_numbering(make_window, work, monkeypatch):
    calls = _fake_converters(monkeypatch)
    (work / "a.docx").write_bytes(b"D")
    (work / "a.xlsx").write_bytes(b"X")
    make_pdf(work / "a.pdf")
    window = make_window(work)
    _select(window, "a.docx", "a.xlsx")

    window._on_convert_to_pdf()
    _drain(window)

    assert sorted(p.name for p in work.iterdir()) == [
        "a(1).pdf", "a(2).pdf", "a.docx", "a.pdf", "a.xlsx"]  # 元は残り、既存 a.pdf は置き換えない
    assert len(calls) == 2
    assert window._test_messages[-1].splitlines()[0] == "2 件を PDF に変換しました"


def test_convert_ignores_pdf_folders_and_reports_unsupported(make_window, work, monkeypatch):
    _fake_converters(monkeypatch)
    (work / "p.png").write_bytes(b"I")
    (work / "t.csv").write_bytes(b"1,2")
    (work / "z.zip").write_bytes(b"Z")
    make_pdf(work / "doc.pdf")
    (work / "dir").mkdir()
    window = make_window(work)
    _select(window, "p.png", "t.csv", "z.zip", "doc.pdf", "dir")

    window._on_convert_to_pdf()
    _drain(window)

    assert (work / "p.pdf").exists() and not (work / "t.pdf").exists()
    lines = window._test_messages[-1].splitlines()
    assert lines == ["1 件を PDF に変換しました",
                     "PDF に変換できない種類のファイル 2 件は対象外です"]


def test_convert_reports_failures_with_first_three_reasons(make_window, work, monkeypatch):
    def boom(src, dest, soffice):
        raise RuntimeError(f"壊れている: {Path(src).name}")

    monkeypatch.setattr(import_worker, "_convert_office", boom)
    for i in range(5):
        (work / f"f{i}.docx").write_bytes(b"D")
    window = make_window(work)
    _select(window, *[f"f{i}.docx" for i in range(5)])

    window._on_convert_to_pdf()
    _drain(window)

    lines = window._test_messages[-1].splitlines()
    assert lines[0] == "5 件は失敗"
    assert len(lines) == 4  # 見出し + 理由 3 件
    assert not list(work.glob("*.pdf"))


def test_convert_undo_trashes_created_pdf(make_window, work, monkeypatch):
    _fake_converters(monkeypatch)
    trashed: list[str] = []
    monkeypatch.setattr(main_window_import, "send2trash", lambda p: trashed.append(p))
    (work / "a.docx").write_bytes(b"D")
    window = make_window(work)
    _select(window, "a.docx")
    window._on_convert_to_pdf()
    _drain(window)

    window._undo_manager.undo()
    assert [Path(p).name for p in trashed] == ["a.pdf"]
    assert (work / "a.docx").exists()


def test_convert_buttons_enabled_state(make_window, work):
    (work / "a.docx").write_bytes(b"D")
    (work / "b.zip").write_bytes(b"Z")
    make_pdf(work / "c.pdf")
    window = make_window(work)

    _select(window)
    assert not window._convert_btn.isEnabled()
    _select(window, "b.zip")
    assert not window._convert_btn.isEnabled()
    assert window._convert_btn.toolTip() == "PDF に変換できる種類のファイルが選ばれていません"
    _select(window, "c.pdf")
    assert not window._convert_btn.isEnabled()
    _select(window, "a.docx", "b.zip")
    assert window._convert_btn.isEnabled()

    menu = window._build_card_context_menu()
    act = next(a for a in menu.actions() if a.text().startswith("PDFに変換"))
    assert act.isEnabled()
    menu.close()
    _select(window, "b.zip")
    menu = window._build_card_context_menu()
    act = next(a for a in menu.actions() if a.text().startswith("PDFに変換"))
    assert not act.isEnabled()
    assert act.toolTip() == "PDF に変換できる種類のファイルが選ばれていません"
    menu.close()


def test_text_to_pdf_renders_japanese(tmp_path):
    from src.utils.text_to_pdf import text_to_pdf

    src = tmp_path / "memo.txt"
    src.write_text("日本語のメモ\nsecond line", encoding="utf-8")
    out = tmp_path / "memo.pdf"
    text_to_pdf(str(src), str(out))
    with fitz.open(str(out)) as doc:
        assert doc.page_count == 1
        assert "日本語のメモ" in doc[0].get_text()

    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    text_to_pdf(str(empty), str(tmp_path / "empty.pdf"))
    with fitz.open(str(tmp_path / "empty.pdf")) as doc:
        assert doc.page_count == 1


# ---- PDF 専用操作は PDF 以外を対象にしない -----------------------------------

def test_pdf_only_buttons_disabled_without_pdf(make_window, work):
    (work / "a.docx").write_bytes(b"D")
    (work / "b.txt").write_bytes(b"T")
    (work / "x.zip").write_bytes(b"Z")
    make_pdf(work / "c.pdf")
    make_pdf(work / "d.pdf")
    (work / "dir").mkdir()
    window = make_window(work)

    _select(window, "a.docx")
    assert not window._rotate_btn.isEnabled()
    assert not window._export_btn.isEnabled()
    assert not window._print_btn.isEnabled()
    assert not window._merge_split_btn.isEnabled()
    assert not window._rename_title_action.isVisible() and not window._rename_title_action.isEnabled() \
        or not window._rename_title_action.isVisible()
    assert window._rename_btn.isEnabled()  # 名前変更は種類を問わない

    _select(window, "a.docx", "x.zip")  # 変換できない種類は結合の対象にならない
    assert not window._merge_split_btn.isEnabled()

    _select(window, "a.docx", "b.txt")  # 変換できるファイルが 2 件なら、PDF でなくても結合できる
    assert window._merge_action.isVisible() and window._merge_split_btn.isEnabled()
    assert not window._rotate_btn.isEnabled() and not window._export_btn.isEnabled()

    _select(window, "a.docx", "c.pdf")  # 混在: 結合も PDF の分解も可（回転・印刷・エクスポートは PDF だけ）
    assert window._rotate_btn.isEnabled() and window._export_btn.isEnabled()
    assert window._print_btn.isEnabled()
    assert window._split_action.isVisible() and window._merge_action.isVisible()

    _select(window, "c.pdf", "d.pdf", "a.docx")
    assert window._merge_action.isVisible()

    _select(window, "a.docx", "dir")  # フォルダがあればエクスポート・結合の対象になりうる
    assert window._export_btn.isEnabled() and window._merge_split_btn.isEnabled()


def test_context_menu_pdf_only_entries(make_window, work):
    (work / "a.docx").write_bytes(b"D")
    (work / "b.docx").write_bytes(b"D")
    make_pdf(work / "c.pdf")
    window = make_window(work)

    def entries(*names):
        _select(window, *names)
        menu = window._build_card_context_menu()
        result = {a.text().split("\t")[0]: a.isEnabled() for a in menu.actions() if a.text()}
        menu.close()
        return result

    one = entries("a.docx")
    assert "既定のアプリで開く" in one and "拡大ビューで開く" not in one and "PDF名を変更" not in one
    assert one["分解"] is False
    assert one["エクスポート"] is False and one["印刷"] is False

    two = entries("a.docx", "b.docx")
    assert two["結合"] is True and two["印刷"] is False

    mixed = entries("a.docx", "c.pdf")
    assert mixed["結合"] is True and mixed["印刷"] is True and mixed["エクスポート"] is True


def test_rotate_and_split_and_export_only_touch_pdfs(make_window, work, monkeypatch):
    (work / "a.docx").write_bytes(b"D")
    make_pdf(work / "c.pdf", pages=2)
    window = make_window(work)

    rotated: list[str] = []
    monkeypatch.setattr(main_window, "rotate_pages", lambda p, idx, ang: rotated.append(Path(p).name))
    _select(window, "a.docx", "c.pdf")
    window._on_rotate(90)
    assert rotated == ["c.pdf"]

    _select(window, "a.docx", "c.pdf")
    assert window._collect_export_jobs() == [(str(work / "c.pdf"), "c.pdf")]
    _select(window, "a.docx")
    assert window._collect_export_jobs() == []
    assert window._selected_pdf_paths_in_grid_order() == []


def test_print_targets_exclude_non_pdf(make_window, work, monkeypatch):
    (work / "a.docx").write_bytes(b"D")
    make_pdf(work / "c.pdf")
    window = make_window(work)
    seen: list[list[str]] = []

    class FakeDialog:
        class DialogCode:
            Accepted = 1

        def __init__(self, targets, *a, **k):
            seen.append(list(targets))

        def exec(self):
            return 0

    monkeypatch.setattr(main_window, "PrintDialog", FakeDialog)
    _select(window)
    window._on_print()  # 選択なし: 全 PDF
    _select(window, "a.docx", "c.pdf")
    window._on_print()
    assert seen == [[str(work / "c.pdf")], [str(work / "c.pdf")]]


def test_card_merge_ignores_non_pdf(make_window, work, monkeypatch):
    (work / "a.docx").write_bytes(b"D")
    make_pdf(work / "c.pdf")
    window = make_window(work)
    docx = next(c for c in window._cards if c.filename == "a.docx")
    pdf = next(c for c in window._cards if c.filename == "c.pdf")

    started: list = []
    monkeypatch.setattr(window, "_run_file_operation", lambda *a, **k: started.append(1))
    # 非 PDF が結合先
    window._on_card_merge(docx, str(pdf.pdf_path))
    # 非 PDF だけが結合元
    window._on_card_merge(pdf, str(docx.pdf_path))
    assert started == []
    before = (work / "c.pdf").read_bytes()
    window._handle_card_copy_merge(str(pdf.pdf_path), docx)
    window._handle_card_copy_merge(str(docx.pdf_path), pdf)
    assert (work / "c.pdf").read_bytes() == before


def test_merge_selected_ignores_unsupported_kinds(make_window, work, monkeypatch):
    (work / "a.zip").write_bytes(b"D")
    make_pdf(work / "c.pdf")
    window = make_window(work)
    asked: list = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No))
    _select(window, "a.zip", "c.pdf")  # 結合できるのは 1 件だけ → 結合しない
    window._on_merge_selected()
    assert asked == []


def test_page_drop_on_non_pdf_card_makes_new_pdf(make_window, work, monkeypatch):
    (work / "a.docx").write_bytes(b"D")
    make_pdf(work / "c.pdf", pages=3)
    window = make_window(work)
    docx = next(c for c in window._cards if c.filename == "a.docx")
    pos = docx.geometry().center()
    assert window._get_card_at_pos(pos) is docx

    monkeypatch.setattr(
        main_window.QApplication, "keyboardModifiers",
        staticmethod(lambda: main_window.Qt.KeyboardModifier.ControlModifier))  # コピー
    window._handle_page_extraction(f"{work / 'c.pdf'}|0", pos)

    assert (work / "a.docx").read_bytes() == b"D"  # 非 PDF には差し込まない
    new = work / "c(1).pdf"
    assert new.exists()
    with fitz.open(str(new)) as doc:
        assert doc.page_count == 1


# ---- 名前変更の拡張子保持 --------------------------------------------------

@pytest.mark.parametrize("old,typed,expected", [
    ("report.docx", "memo", "memo.docx"),
    ("report.docx", "memo.txt", "memo.txt.docx"),
    ("report.docx", "memo.DOCX", "memo.DOCX"),
    ("photo.png", "new name", "new name.png"),
    ("c.pdf", "other", "other.pdf"),
    ("README", "README2", "README2"),
])
def test_rename_keeps_extension(make_window, work, monkeypatch, old, typed, expected):
    if old.endswith(".pdf"):
        make_pdf(work / old)
    else:
        (work / old).write_bytes(b"x")
    window = make_window(work)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (typed, True)))
    _select(window, old)

    window._on_rename()

    assert (work / expected).exists()
    assert not (work / old).exists()


def test_rename_pdf_title_ignores_non_pdf(make_window, work, monkeypatch):
    (work / "a.docx").write_bytes(b"D")
    window = make_window(work)
    called: list = []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: called.append(1) or ("x", True)))
    _select(window, "a.docx")
    window._on_rename_pdf_title()
    assert called == []


# ---- 種類での絞り込みと種類順 ------------------------------------------------

def _populate_kinds(work):
    make_pdf(work / "a.pdf")
    (work / "b.docx").write_bytes(b"D")
    (work / "c.xlsx").write_bytes(b"X")
    (work / "d.png").write_bytes(b"I")
    (work / "e.png").write_bytes(b"I")
    (work / "sub").mkdir()


def test_kind_counts_and_menu(make_window, work):
    _populate_kinds(work)
    window = make_window(work)
    counts = window._kind_counts()
    assert counts["folder"] == 1 and counts["pdf"] == 1 and counts["image"] == 2
    assert counts["word"] == 1 and counts["excel"] == 1 and counts["zip"] == 0

    window._rebuild_kind_menu()
    texts = [a.text() for a in window._kind_menu.actions() if a.text()]
    assert texts[0] == "すべて"
    assert "画像（2）" in texts and "フォルダ（1）" in texts and "zip（0）" in texts
    assert len(texts) == 1 + len(file_kinds.FILE_KINDS)


def test_kind_filter_hides_and_persists(make_window, work):
    _populate_kinds(work)
    window = make_window(work)
    window._set_hidden_kinds({"image", "folder"})
    window._refresh_grid()

    visible = [i for i in (*window._folder_cards, *window._cards) if window._item_visible(i)]
    assert sorted(i.filename for i in visible) == ["a.pdf", "b.docx", "c.xlsx"]
    assert window._filter_active()
    assert app_settings.hidden_file_kinds() == {"image", "folder"}

    # 名前の絞り込みと併用（AND）
    window._on_filter_text_changed("b")
    visible = [i for i in (*window._folder_cards, *window._cards) if window._item_visible(i)]
    assert [i.filename for i in visible] == ["b.docx"]

    # 別の窓（フォルダを移った先・新しい窓）でも保存した状態を使う
    other = make_window(work)
    assert other._hidden_kinds == {"image", "folder"}

    window._set_hidden_kinds(set())
    assert app_settings.hidden_file_kinds() == set()
    assert not window._filter_active() or window._filter_groups


def test_kind_filter_default_shows_everything(make_window, work):
    _populate_kinds(work)
    window = make_window(work)
    assert window._hidden_kinds == set()
    assert all(window._item_visible(i) for i in (*window._folder_cards, *window._cards))


def test_kind_filter_drops_hidden_from_selection(make_window, work):
    _populate_kinds(work)
    window = make_window(work)
    _select(window, "a.pdf", "d.png")
    window._set_hidden_kinds({"image"})
    assert [c.filename for c in window._selected_cards] == ["a.pdf"]


def test_hidden_kinds_ignores_unknown_values():
    from PyQt6.QtCore import QSettings

    QSettings().setValue(app_settings.HIDDEN_FILE_KINDS_KEY, "image,bogus,zip")
    assert app_settings.hidden_file_kinds() == {"image", "zip"}


def test_sort_by_kind(make_window, work):
    make_pdf(work / "z.pdf")
    make_pdf(work / "b.pdf")
    (work / "m.docx").write_bytes(b"D")
    (work / "a.png").write_bytes(b"I")
    (work / "x.xlsx").write_bytes(b"X")
    (work / "u.unknown").write_bytes(b"?")
    window = make_window(work)

    window._apply_sort("kind", True)
    assert [c.filename for c in window._cards] == [
        "b.pdf", "z.pdf", "m.docx", "x.xlsx", "a.png", "u.unknown"]
    assert window._sort_action_kind_asc.isChecked()
    assert window._status_sort.text() == "種類順（昇順）"

    window._apply_sort("kind", False)
    assert [c.filename for c in window._cards] == [
        "u.unknown", "a.png", "x.xlsx", "m.docx", "z.pdf", "b.pdf"]
    assert window._sort_action_kind_desc.isChecked()
