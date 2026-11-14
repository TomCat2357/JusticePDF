"""Markdown の PDF 変換・テキストのサムネイル・Markdown ウィンドウのテスト。"""
from __future__ import annotations

import base64
from pathlib import Path

import fitz
import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.utils import file_kinds, text_file
from src.utils.markdown_to_pdf import base_url_for, load_image_resource, markdown_to_pdf
from src.views import main_window, markdown_window, pdf_card, text_thumbnail, theme
from src.views.markdown_window import MarkdownWindow, open_link
from tests.helpers import FakeWatcher


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
    for name in ("information", "warning"):  # 完了報告で止まらないように
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))

    def _create(work_dir: Path):
        window = main_window.MainWindow(str(work_dir))
        qtbot.addWidget(window)
        window.show()
        return window

    return _create


def _png(path: Path, color="red", w=40, h=30) -> None:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path), "PNG")


def _data_uri_png() -> str:
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(QColor("blue"))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(ba)).decode()


# ---- file_kinds / text_file ------------------------------------------------

def test_markdown_kind_helpers():
    assert file_kinds.is_markdown("a.md") and file_kinds.is_markdown("A.MARKDOWN")
    assert not file_kinds.is_markdown("a.txt")
    assert file_kinds.convert_kind("a.md") == "markdown"
    assert file_kinds.convert_kind("a.txt") == "text"
    assert file_kinds.kind_of("a.md") == "text"


def test_text_file_roundtrip_keeps_encoding_and_newline(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes("日本語\r\n二行目\r\n".encode("cp932"))
    tf = text_file.read_text_file(str(p))
    assert (tf.encoding, tf.newline, tf.text, tf.lossy) == ("cp932", "\r\n", "日本語\n二行目\n", False)
    assert text_file.encode_text(tf.text, tf.encoding, tf.newline) == p.read_bytes()

    p.write_bytes(b"\xef\xbb\xbf" + "あ\n".encode("utf-8"))
    tf = text_file.read_text_file(str(p))
    assert (tf.encoding, tf.newline, tf.text) == ("utf-8-sig", "\n", "あ\n")
    p.write_bytes("あ".encode("utf-8"))
    assert text_file.read_text_file(str(p)).encoding == "utf-8"


def test_decode_truncated_utf8_is_not_mistaken_for_cp932():
    raw = ("あいう" * 10).encode("utf-8")[:-1]  # 末尾の 1 文字が途中で切れている
    text, enc, lossy = text_file.decode_bytes(raw, truncated=True)
    assert enc == "utf-8" and not lossy and text.startswith("あいう")


# ---- Markdown -> PDF ----------------------------------------------------------

def test_markdown_to_pdf_makes_a4_pdf_with_local_image(tmp_path, qtbot):
    _png(tmp_path / "pic.png")
    src = tmp_path / "note.md"
    src.write_text("# 見出し\n\n本文です。\n\n![x](pic.png)\n", encoding="utf-8")
    out = tmp_path / "note.pdf"
    markdown_to_pdf(str(src), str(out))
    doc = fitz.open(str(out))
    assert len(doc) >= 1
    assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (595, 842)
    assert len(doc[0].get_images()) >= 1  # 相対パスの画像が埋め込まれた
    doc.close()


def test_markdown_to_pdf_paginates_and_handles_empty(tmp_path, qtbot):
    src = tmp_path / "long.md"
    src.write_text("\n\n".join(f"段落 {i} " + "あ" * 80 for i in range(150)), encoding="utf-8")
    out = tmp_path / "long.pdf"
    markdown_to_pdf(str(src), str(out))
    with fitz.open(str(out)) as doc:
        assert len(doc) >= 3
    empty = tmp_path / "empty.md"
    empty.write_bytes(b"")
    markdown_to_pdf(str(empty), str(tmp_path / "empty.pdf"))
    with fitz.open(str(tmp_path / "empty.pdf")) as doc:
        assert len(doc) == 1


def test_markdown_to_pdf_reads_cp932(tmp_path, qtbot):
    src = tmp_path / "sj.md"
    src.write_bytes("# 日本語\n".encode("cp932"))
    markdown_to_pdf(str(src), str(tmp_path / "sj.pdf"))
    assert (tmp_path / "sj.pdf").stat().st_size > 0


def test_image_resources_are_local_or_data_only(tmp_path, qtbot):
    _png(tmp_path / "pic.png")
    base = base_url_for(str(tmp_path / "x.md"))
    assert load_image_resource(base, QUrl("pic.png")) is not None
    assert load_image_resource(base, QUrl(_data_uri_png())) is not None
    assert load_image_resource(base, QUrl("http://127.0.0.1:9/x.png")) is None
    assert load_image_resource(base, QUrl("https://example.invalid/x.png")) is None
    assert load_image_resource(base, QUrl("missing.png")) is None
    assert load_image_resource(base, QUrl("file://server/share/x.png")) is None  # UNC 経由の取得


def test_import_worker_dispatches_markdown(make_window, work, monkeypatch):
    called = []
    from src.workers import import_worker

    monkeypatch.setattr(import_worker, "_convert_markdown",
                        lambda s, d: called.append(Path(s).name) or Path(d).write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(import_worker, "_convert_text",
                        lambda s, d: pytest.fail(".md が text_to_pdf に回った"))
    (work / "a.md").write_text("# hi", encoding="utf-8")
    window = make_window(work)
    window._clear_selection()
    card = window._cards[0]
    card.set_selected(True)
    window._selected_cards.append(card)
    window._on_convert_to_pdf()
    worker = window._active_import_worker
    if worker is not None:
        worker.wait()
    QApplication.processEvents()
    assert called == ["a.md"]
    assert (work / "a.pdf").exists()


def test_markdown_conversion_in_real_worker_thread(tmp_path, qtbot):
    from src.workers.import_worker import MODE_CONVERT, ImportWorker

    _png(tmp_path / "pic.png")
    src = tmp_path / "t.md"
    src.write_text("# T\n\n![x](pic.png)\n", encoding="utf-8")
    dest = tmp_path / "t.pdf"
    worker = ImportWorker([(str(src), str(dest))], None, mode=MODE_CONVERT)
    with qtbot.waitSignal(worker.finished_all, timeout=30000) as blocker:
        worker.start()
    imported, failed, cancelled = blocker.args
    worker.wait()
    assert failed == [] and imported == [str(dest)]
    with fitz.open(str(dest)) as doc:
        assert len(doc[0].get_images()) >= 1


# ---- サムネイル -------------------------------------------------------------------

def _non_paper_pixels(img: QImage) -> int:
    paper = QColor(theme.COLORS["paper"]).rgb()
    return sum(1 for y in range(img.height()) for x in range(img.width()) if img.pixel(x, y) != paper)


def test_text_thumbnail_renders_md_and_txt(tmp_path, qtbot):
    md = tmp_path / "a.md"
    md.write_text("# 見出し\n\n- 項目 1\n- 項目 2\n\n本文 text " * 5, encoding="utf-8")
    txt = tmp_path / "a.txt"
    txt.write_text("line one\nline two\n" * 20, encoding="utf-8")
    for p in (md, txt):
        img = text_thumbnail.render_text_thumbnail(str(p), 136, 1.0)
        assert img is not None and (img.width(), img.height()) == (136, 136)
        assert img.pixel(1, 1) == QColor(theme.COLORS["paper"]).rgb()  # 紙の色の地
        assert _non_paper_pixels(img) > 50  # 文字が描かれている
    # 同じ条件ではキャッシュから返る
    assert text_thumbnail.render_text_thumbnail(str(txt), 136, 1.0) is text_thumbnail.render_text_thumbnail(
        str(txt), 136, 1.0)


def test_text_thumbnail_reads_only_head_and_falls_back(tmp_path, qtbot, monkeypatch):
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (text_thumbnail.HEAD_BYTES * 3))
    seen = []
    real = text_thumbnail.read_head_bytes
    monkeypatch.setattr(text_thumbnail, "read_head_bytes", lambda p, n: seen.append(n) or real(p, n))
    assert text_thumbnail.render_text_thumbnail(str(big), 100, 1.0) is not None
    assert seen == [text_thumbnail.HEAD_BYTES]
    assert text_thumbnail.render_text_thumbnail(str(tmp_path / "missing.txt"), 100, 1.0) is None
    monkeypatch.setattr(text_thumbnail, "_build_doc", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    other = tmp_path / "other.txt"
    other.write_text("hello")
    assert text_thumbnail.render_text_thumbnail(str(other), 100, 1.0) is None


def test_text_thumbnail_follows_theme(tmp_path, qtbot):
    p = tmp_path / "t.txt"
    p.write_text("hello world " * 10)
    before = text_thumbnail.render_text_thumbnail(str(p), 100, 1.0)
    try:
        theme.set_theme("b")
        after = text_thumbnail.render_text_thumbnail(str(p), 100, 1.0)
        assert after is not before  # テーマが変わればキャッシュを使い回さず描き直す
        assert after.pixel(1, 1) == QColor(theme.COLORS["paper"]).rgb()
        theme.set_theme("g")  # ダークでも紙は白なので、文字は濃い色（明るい text 色は使わない）
        assert text_thumbnail._ink_color() == "#1e293b"
    finally:
        theme.set_theme("standard")


def test_cards_use_text_thumbnail_and_fall_back_to_icon(make_window, work, monkeypatch):
    (work / "a.md").write_text("# 見出し\n\n本文", encoding="utf-8")
    (work / "b.txt").write_text("plain text\n", encoding="utf-8")
    (work / "c.docx").write_bytes(b"PK")
    window = make_window(work)
    cards = {c.filename: c for c in window._cards}
    assert cards["a.md"]._page_count_label.text() == "MD"  # 拡張子バッジは残る
    calls = []
    real = text_thumbnail.render_text_thumbnail
    monkeypatch.setattr(text_thumbnail, "render_text_thumbnail",
                        lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    for c in cards.values():
        c.refresh()
    assert sorted(Path(p).name for p in calls) == ["a.md", "b.txt"]  # docx はアイコンのまま
    monkeypatch.setattr(text_thumbnail, "render_text_thumbnail", lambda *a, **k: None)
    cards["a.md"].refresh()  # 失敗してもアイコンカードで落ちない
    assert not cards["a.md"]._thumbnail_label.pixmap().isNull()


def _type(win, text: str) -> None:
    """編集欄の内容を打ち込んだ扱いで置き換える（setPlainText は変更扱いにならないため）。"""
    win._editor.selectAll()
    win._editor.insertPlainText(text)


# ---- Markdown ウィンドウ ------------------------------------------------------------

@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(markdown_window.QDesktopServices, "openUrl", lambda u: urls.append(u.toString()) or True)
    return urls


def test_double_click_markdown_opens_window_and_txt_uses_os(make_window, work, qtbot, opened):
    (work / "a.md").write_text("# タイトル\n\n本文", encoding="utf-8")
    (work / "b.txt").write_text("x")
    window = make_window(work)
    window._open_with_default_app = lambda p: opened.append(Path(p).name)
    cards = {c.filename: c for c in window._cards}
    window._on_card_double_clicked(cards["a.md"])
    wins = [w for w in QApplication.topLevelWidgets() if isinstance(w, MarkdownWindow)]
    assert len(wins) == 1 and wins[0].isVisible()
    assert "タイトル" in wins[0]._browser.toPlainText()
    window._on_card_double_clicked(cards["a.md"])  # 同じファイルは前面へ（二重に開かない）
    assert len([w for w in QApplication.topLevelWidgets() if isinstance(w, MarkdownWindow) and w.isVisible()]) == 1
    window._on_card_double_clicked(cards["b.txt"])
    assert opened == ["b.txt"]
    wins[0].close()


def test_markdown_context_menu_has_in_app_and_default_app(make_window, work):
    (work / "a.md").write_text("x")
    (work / "b.docx").write_bytes(b"PK")
    window = make_window(work)
    labels = {}
    for name in ("a.md", "b.docx"):
        window._clear_selection()
        card = next(c for c in window._cards if c.filename == name)
        card.set_selected(True)
        window._selected_cards.append(card)
        menu = window._build_card_context_menu()
        labels[name] = [a.text() for a in menu.actions()]
    assert "開く" in labels["a.md"] and "既定のアプリで開く" in labels["a.md"]
    assert "開く" not in labels["b.docx"] and "既定のアプリで開く" in labels["b.docx"]


def test_context_menu_default_app_opens_markdown_with_os(make_window, work, monkeypatch):
    (work / "a.md").write_text("x")
    window = make_window(work)
    opened = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl",
                        lambda u: opened.append(Path(u.toLocalFile()).name) or True)
    window._clear_selection()
    window._cards[0].set_selected(True)
    window._selected_cards.append(window._cards[0])
    menu = window._build_card_context_menu()
    next(a for a in menu.actions() if a.text() == "既定のアプリで開く").trigger()
    assert opened == ["a.md"]
    assert not [w for w in QApplication.topLevelWidgets() if isinstance(w, MarkdownWindow)]


def test_markdown_window_preview_edit_save_keeps_cp932_and_crlf(tmp_path, qtbot):
    p = tmp_path / "n.md"
    p.write_bytes("# 題\r\n本文\r\n".encode("cp932"))
    win = MarkdownWindow(str(p))
    qtbot.addWidget(win)
    win.show()
    assert win.encoding == "cp932" and not win.is_dirty and not win.is_editing()
    assert "題" in win._browser.toPlainText()
    win.set_edit_mode(True)
    assert win.is_editing()
    _type(win, "# 新しい題\n追記\n")
    assert win.is_dirty
    assert win.save()
    assert not win.is_dirty
    assert p.read_bytes() == "# 新しい題\r\n追記\r\n".encode("cp932")
    win.set_edit_mode(False)  # プレビューが編集内容に更新される
    assert "新しい題" in win._browser.toPlainText()


def test_markdown_window_unencodable_char_asks_and_saves_utf8(tmp_path, qtbot, monkeypatch):
    p = tmp_path / "n.md"
    p.write_bytes("日本語\n".encode("cp932"))
    win = MarkdownWindow(str(p))
    qtbot.addWidget(win)
    _type(win, "日本語 \U0001F600\n")
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    assert win.save() is False and win.is_dirty and len(asked) == 1
    assert p.read_bytes() == "日本語\n".encode("cp932")  # 中止したので元のまま
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert win.save() is True
    assert win.encoding == "utf-8"
    assert p.read_bytes() == "日本語 \U0001F600\n".encode("utf-8")


def test_markdown_window_ctrl_s_and_close_prompt(tmp_path, qtbot, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("a\n", encoding="utf-8")
    win = MarkdownWindow(str(p))
    qtbot.addWidget(win)
    win.show()
    _type(win, "b\n")
    assert win._save_action.isEnabled()
    assert win._save_action.shortcut().toString() == "Ctrl+S"

    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))
    win.close()
    assert win.isVisible() and win.is_dirty  # キャンセルで閉じない
    win.close()  # 破棄して閉じる
    assert p.read_text(encoding="utf-8") == "a\n"

    win2 = MarkdownWindow(str(p))
    qtbot.addWidget(win2)
    win2.show()
    _type(win2, "c\n")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    win2.close()
    assert p.read_text(encoding="utf-8") == "c\n"


def test_markdown_window_convert_saves_first_and_emits(tmp_path, qtbot):
    p = tmp_path / "n.md"
    p.write_text("a\n", encoding="utf-8")
    win = MarkdownWindow(str(p))
    qtbot.addWidget(win)
    _type(win, "z\n")
    with qtbot.waitSignal(win.convert_requested, timeout=1000) as sig:
        win._convert_action.trigger()
    assert sig.args == [str(p)]
    assert p.read_text(encoding="utf-8") == "z\n"  # 変換前に保存される


def test_markdown_window_convert_runs_through_main_window(make_window, work, qtbot, monkeypatch):
    (work / "a.md").write_text("# a\n", encoding="utf-8")
    (work / "a.pdf").write_bytes(b"%PDF-existing")
    from src.workers import import_worker

    monkeypatch.setattr(import_worker, "_convert_markdown",
                        lambda s, d: Path(d).write_bytes(b"%PDF-fake"))
    window = make_window(work)
    window._open_markdown_window(str(work / "a.md"))
    win = next(w for w in QApplication.topLevelWidgets() if isinstance(w, MarkdownWindow))
    win._convert_action.trigger()
    worker = window._active_import_worker
    if worker is not None:
        worker.wait()
    QApplication.processEvents()
    assert (work / "a(1).pdf").read_bytes() == b"%PDF-fake"  # 既存 a.pdf は置き換えず連番
    assert (work / "a.pdf").read_bytes() == b"%PDF-existing"
    win.close()


def test_markdown_links_open_externally_and_remote_images_not_fetched(tmp_path, qtbot, opened):
    p = tmp_path / "n.md"
    (tmp_path / "other.md").write_text("x")
    p.write_text("[web](https://example.com/a) [mail](mailto:a@example.com) [loc](other.md) "
                 "[js](javascript:alert(1)) ![r](http://127.0.0.1:9/x.png)\n", encoding="utf-8")
    win = MarkdownWindow(str(p))
    qtbot.addWidget(win)
    base = base_url_for(str(p))
    assert open_link(QUrl("https://example.com/a"), base)
    assert open_link(QUrl("mailto:a@example.com"), base)
    assert open_link(QUrl("other.md"), base)
    assert not open_link(QUrl("javascript:alert(1)"), base)
    assert opened[0] == "https://example.com/a" and opened[2].endswith("/other.md")
    # 描画中に外部へ取りに行かない（loadResource は None を返す）
    from PyQt6.QtGui import QTextDocument
    assert win._browser.loadResource(QTextDocument.ResourceType.ImageResource.value,
                                     QUrl("http://127.0.0.1:9/x.png")) is None


def test_markdown_window_renders_local_image_and_follows_theme(tmp_path, qtbot):
    _png(tmp_path / "pic.png", "green")
    p = tmp_path / "n.md"
    p.write_text("![pic](pic.png)\n", encoding="utf-8")
    win = MarkdownWindow(str(p))
    qtbot.addWidget(win)
    win.show()
    img = win._browser.loadResource(2, QUrl("pic.png"))
    assert isinstance(img, QImage) and not img.isNull()
    from PyQt6.QtGui import QPalette
    app = QApplication.instance()
    old_sheet, old_palette = app.styleSheet(), app.palette()
    try:
        theme.apply_theme(app, "g")  # ダークテーマ
        assert win._browser.palette().color(QPalette.ColorRole.Link).name() == QColor(
            theme.COLORS["mode-pages"]).name()
    finally:
        theme.apply_theme(app, "standard")
        app.setStyleSheet(old_sheet)
        app.setPalette(old_palette)


def test_oversized_markdown_is_not_converted_or_opened_in_app(make_window, work, tmp_path, monkeypatch, qtbot):
    from src.utils import markdown_to_pdf as m

    monkeypatch.setattr(m, "MAX_BYTES", 10)
    big = tmp_path / "big.md"
    big.write_text("x" * 100)
    with pytest.raises(RuntimeError):
        markdown_to_pdf(str(big), str(tmp_path / "big.pdf"))
    (work / "big.md").write_text("x" * 100)
    window = make_window(work)
    opened = []
    window._open_with_default_app = lambda p: opened.append(Path(p).name)
    window._on_card_double_clicked(window._cards[0])
    assert opened == ["big.md"]
    assert not [w for w in QApplication.topLevelWidgets() if isinstance(w, MarkdownWindow)]


def test_text_card_zoom_rescales_without_rerendering(make_window, work, monkeypatch):
    (work / "a.md").write_text("# t body")
    window = make_window(work)
    card = window._cards[0]
    calls = []
    real = text_thumbnail.render_text_thumbnail
    monkeypatch.setattr(text_thumbnail, "render_text_thumbnail", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    card.set_preview_size_fast(card._card_width + 40, card._thumb_size + 40)
    assert calls == []
