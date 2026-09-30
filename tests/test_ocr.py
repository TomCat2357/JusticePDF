"""OCR(src.ocr: RapidOCR移植)のテスト。

実エンジン(モデルのダウンロードが必要)には依存しない。認識結果を固定した偽の
エンジンを使い、白塗り・座標換算・埋め込み→抽出・削除・一覧からの除外・検出・
エクスポートでの取り残し防止を検証する。架空のダミーテキストのみを使う。
"""
from __future__ import annotations

import fitz
import pytest

from src.ocr import RapidOCRService, get_ocr_service, is_ocr_available
from src.ocr.base import OCRResult, _OCRServiceBase
from src.ocr.embedder import (
    count_ocr_annots,
    count_ocr_lines,
    embed_ocr_results,
    is_ocr_annot,
    read_ocr_results,
    remove_ocr_annots,
    replace_ocr_in_file,
    snapshot_ocr_results,
    split_segments,
    text_width_em,
)
from src.ocr.pipeline import page_has_text, run_ocr_pages
from src.pii.settings import PiiSettings
from src.utils.pdf_utils import (
    create_freetext_annot,
    FreeTextAnnotData,
    get_page_chars,
    list_freetext_annots,
    list_pii_markup_annots,
    redact_pdf_remove_text,
)
from src.views import page_edit_ocr as page_edit_ocr_module
from src.views import page_edit_pii as page_edit_pii_module
from src.workers.ocr_worker import OcrWorker
from tests.helpers import create_page_edit_window, open_zoom

pytestmark = pytest.mark.usefixtures("qapp")


# ---------------------------------------------------------------------------
# 偽のOCRエンジン
# ---------------------------------------------------------------------------


class FakeOcrService(_OCRServiceBase):
    """固定の行(PDFのpt・表示座標)を、実エンジンと同じ流れで返す偽のバックエンド。

    画像ピクセル座標の四隅点(quad)として返し、基盤の ``_finalize_results`` で
    72/dpi 倍して pt へ戻す(=座標換算も実コードで検証される)。
    """

    def __init__(self, lines_by_page: dict[int, list[tuple[str, tuple[float, float, float, float]]]]):
        self.lines_by_page = lines_by_page
        self.calls: list[dict] = []

    def run_ocr_on_page(
        self,
        page_pixmap,
        existing_text_rects=None,
        *,
        page_num=0,
        scale_x=1.0,
        scale_y=1.0,
        offset_x=0.0,
        offset_y=0.0,
    ):
        image = self._prepare_image(page_pixmap, existing_text_rects)
        self.calls.append(
            {
                "page_num": page_num,
                "size": (page_pixmap.width, page_pixmap.height),
                "existing_rects": list(existing_text_rects or []),
                "image": image,
                "scale": scale_x,
            }
        )
        items = []
        for text, (x0, y0, x1, y1) in self.lines_by_page.get(page_num, []):
            sx, sy = 1.0 / scale_x, 1.0 / scale_y
            quad = [[x0 * sx, y0 * sy], [x1 * sx, y0 * sy], [x1 * sx, y1 * sy], [x0 * sx, y1 * sy]]
            items.append((text, quad, 0.95))
        return self._finalize_results(
            items,
            page_num=page_num,
            scale_x=scale_x,
            scale_y=scale_y,
            offset_x=offset_x,
            offset_y=offset_y,
        )


def _blank_pdf(path, pages: int = 1, text: str | None = None) -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=200)
        page.draw_rect(fitz.Rect(0, 0, 400, 200), color=(1, 1, 1), fill=(1, 1, 1))
        if text is not None:
            page.insert_text((40, 100), text, fontsize=18)
    doc.save(str(path))
    doc.close()


def _text_of(chars: list[dict]) -> str:
    return "".join(c["c"] for c in chars)


# ---------------------------------------------------------------------------
# 基盤: 白塗り・座標換算・正規化
# ---------------------------------------------------------------------------


def test_normalize_rect_accepts_quad_flat_and_dict_forms():
    norm = _OCRServiceBase._normalize_rect
    assert norm([[10, 20], [50, 22], [52, 40], [9, 38]]) == (9.0, 20.0, 52.0, 40.0)
    assert norm([10, 20, 50, 22, 52, 40, 9, 38]) == (9.0, 20.0, 52.0, 40.0)
    assert norm([1, 2, 30, 40]) == (1.0, 2.0, 30.0, 40.0)
    assert norm({"x": 1, "y": 2, "width": 10, "height": 5}) == (1.0, 2.0, 11.0, 7.0)
    assert norm([5, 5, 5, 9]) is None  # 幅ゼロは無効


def test_finalize_results_scales_pixels_to_points():
    service = FakeOcrService({})
    results = service._finalize_results(
        [("山田太郎", [[100, 200], [300, 200], [300, 260], [100, 260]], 0.9), ("", [[0, 0], [1, 0], [1, 1], [0, 1]], 0.5)],
        page_num=3,
        scale_x=72.0 / 300.0,
        scale_y=72.0 / 300.0,
    )
    assert len(results) == 1  # 空文字の行は捨てる
    (r,) = results
    assert (r.text, r.page_num) == ("山田太郎", 3)
    assert (r.x, r.y, r.width, r.height) == pytest.approx((24.0, 48.0, 48.0, 14.4))


def test_prepare_image_whites_out_existing_text_rects():
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 60), False)
    pix.set_rect(pix.irect, (0, 0, 0))  # 真っ黒なページ
    service = FakeOcrService({})

    image = service._prepare_image(pix, [(10, 10, 50, 30)])

    assert image.getpixel((20, 20)) == (255, 255, 255)  # 既存テキスト矩形の内側は白
    assert image.getpixel((5, 5)) == (0, 0, 0)  # 外側はそのまま
    assert image.getpixel((80, 50)) == (0, 0, 0)


def test_extract_existing_text_rects_scaled_by_dpi_and_rotation(tmp_path):
    pdf_path = tmp_path / "text.pdf"
    _blank_pdf(pdf_path, text="SECRET")
    with fitz.open(str(pdf_path)) as doc:
        (x0, y0, x1, y1) = doc[0].search_for("SECRET")[0]
        rects = _OCRServiceBase.extract_existing_text_rects(doc[0], 144)
    assert len(rects) == 1
    # 144dpi = 2倍。span矩形は文字の外接(行高)なので、SECRET の検索矩形を含む。
    rx0, ry0, rx1, ry1 = rects[0]
    assert rx0 == pytest.approx(x0 * 2, abs=1.0)
    assert rx1 >= x1 * 2 - 1.0
    assert ry0 <= y0 * 2 + 1.0 and ry1 >= y1 * 2 - 1.0

    # 回転ページでは画像(表示向き)の座標へ直される。
    rotated = tmp_path / "rot.pdf"
    _blank_pdf(rotated, text="SECRET")
    with fitz.open(str(rotated)) as doc:
        doc[0].set_rotation(90)
        display = fitz.Rect(*doc[0].get_text("dict")["blocks"][0]["bbox"]) * doc[0].rotation_matrix
        display.normalize()
        (rx0, ry0, rx1, ry1) = _OCRServiceBase.extract_existing_text_rects(doc[0], 72)[0]
    assert (rx0, ry0, rx1, ry1) == pytest.approx((display.x0, display.y0, display.x1, display.y1), abs=1.0)


def test_rapidocr_output_to_items_handles_boxes_and_legacy():
    class Output:
        class _Box(list):
            def tolist(self):
                return list(self)

        boxes = [_Box([[0, 0], [10, 0], [10, 5], [0, 5]])]
        txts = ["行"]
        scores = [0.8]

    assert RapidOCRService._output_to_items(Output()) == [("行", [[0, 0], [10, 0], [10, 5], [0, 5]], 0.8)]
    assert RapidOCRService._output_to_items(None) == []
    legacy = [[[[0, 0], [4, 0], [4, 4], [0, 4]], "旧", 0.5]]
    assert RapidOCRService._output_to_items(legacy)[0][0] == "旧"


def test_availability_helpers_do_not_import_engine(monkeypatch):
    monkeypatch.setattr(RapidOCRService, "_AVAILABILITY_CACHE", False)
    assert is_ocr_available() is False
    with pytest.raises(RuntimeError, match="uv sync --extra ocr"):
        get_ocr_service({"tier": "light"})


# ---------------------------------------------------------------------------
# 埋め込み → 抽出 → 削除
# ---------------------------------------------------------------------------


def test_embed_then_extract_roundtrip_gives_text_and_usable_char_boxes(tmp_path):
    pdf_path = tmp_path / "embed.pdf"
    _blank_pdf(pdf_path)
    line_rect = (40.0, 60.0, 40.0 + 18 * 9, 84.0)  # 9文字(全角)分の幅・高さ24
    lines = [
        OCRResult("山田太郎の電話番号", *line_rect[:2], line_rect[2] - line_rect[0], line_rect[3] - line_rect[1], 0, 0.9),
        OCRResult("SECRET 090-1234-5678", 40.0, 120.0, 150.0, 18.0, 0, 0.9),
    ]
    with fitz.open(str(pdf_path)) as doc:
        assert embed_ocr_results(doc, lines) == 2
        doc.saveIncr()

    chars = get_page_chars(str(pdf_path), 0)
    assert _text_of(chars).replace("\n", "") == "山田太郎の電話番号SECRET 090-1234-5678"
    # 1行目の文字bboxは、行の矩形とほぼそろう(検出のquad/黒塗りに使える精度)。
    first = [c for c in chars if c["line_id"] == chars[0]["line_id"]]
    x0 = min(c["bbox"][0] for c in first)
    x1 = max(c["bbox"][2] for c in first)
    y0 = min(c["bbox"][1] for c in first)
    y1 = max(c["bbox"][3] for c in first)
    assert x0 == pytest.approx(line_rect[0], abs=1.0)
    assert line_rect[0] < x1 <= line_rect[2] + 1.0
    assert x1 - x0 >= (line_rect[2] - line_rect[0]) * 0.85
    assert y0 == pytest.approx(line_rect[1], abs=1.5) and y1 == pytest.approx(line_rect[3], abs=1.5)
    # 2行目(欧数字混じり)の文字boxも行の高さ付近にある。
    second = [c for c in chars if c["line_id"] != chars[0]["line_id"]]
    assert min(c["bbox"][1] for c in second) >= 120.0 - 4.0 and max(c["bbox"][3] for c in second) <= 138.0 + 4.0
    # 見えない(不透明度0)ので、ページを画像化しても何も描かれない。
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[0].get_pixmap()
        assert pix.samples.count(b"\x00") == 0 or min(pix.samples) == 255


def test_ocr_annots_have_distinct_subject_and_are_removed_by_subject(tmp_path):
    pdf_path = tmp_path / "remove.pdf"
    _blank_pdf(pdf_path)
    create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0, rect=(200.0, 20.0, 300.0, 50.0), content="通常の注釈",
            fontsize=12.0, text_color=(0, 0, 0), fill_color=None, border_color=None,
            border_width=0.0, opacity=1.0,
        ),
    )
    with fitz.open(str(pdf_path)) as doc:
        embed_ocr_results(doc, [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9)])
        subjects = sorted(a.info["subject"].split(":")[0] for a in doc[0].annots())
        ocr_flags = [is_ocr_annot(a) for a in doc[0].annots()]
        assert subjects[0].startswith("JusticePDF-FreeText") and subjects[1] == "JusticePDF-OCR"
        assert sorted(ocr_flags) == [False, True]
        assert all(a.info["title"] == "JusticePDF OCR" for a in doc[0].annots() if is_ocr_annot(a))
        assert remove_ocr_annots(doc) == 1  # OCRだけ削除される
        remaining = [a.info["subject"] for a in doc[0].annots()]
        assert len(remaining) == 1 and remaining[0].startswith("JusticePDF-FreeText")


def test_read_snapshot_and_replace_in_file_restore_exact_lines(tmp_path):
    pdf_path = tmp_path / "replace.pdf"
    _blank_pdf(pdf_path, pages=2)
    first = [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9), OCRResult("架空商事", 40, 100, 80, 20, 0, 0.8)]
    second = [OCRResult("別の語", 30, 30, 60, 20, 1, 0.7)]
    assert replace_ocr_in_file(str(pdf_path), None, first + second) == (0, 3)
    assert count_ocr_annots(str(pdf_path)) == 3

    before_page0 = snapshot_ocr_results(str(pdf_path), [0])
    assert [(r.text, r.page_num) for r in before_page0] == [("山田太郎", 0), ("架空商事", 0)]
    assert (before_page0[0].x, before_page0[0].y, before_page0[0].width, before_page0[0].height) == pytest.approx(
        (40, 60, 80, 20), abs=0.01
    )

    # 置換(ページ0だけ): 1ページ目は入れ替わり、2ページ目は触られない。
    removed, inserted = replace_ocr_in_file(str(pdf_path), [0], [OCRResult("新しい行", 40, 60, 80, 20, 0, 0.9)])
    assert (removed, inserted) == (2, 1)
    assert [r.text for r in snapshot_ocr_results(str(pdf_path), [0])] == ["新しい行"]
    assert [r.text for r in snapshot_ocr_results(str(pdf_path), [1])] == ["別の語"]

    # 直前の内容で置換すれば元に戻る(Undoの仕組み)。
    replace_ocr_in_file(str(pdf_path), [0], before_page0)
    assert [r.text for r in snapshot_ocr_results(str(pdf_path), [0])] == ["山田太郎", "架空商事"]

    # 空で置換=削除。変更が無ければ保存しない。
    assert replace_ocr_in_file(str(pdf_path), None, []) == (3, 0)
    before = pdf_path.read_bytes()
    assert replace_ocr_in_file(str(pdf_path), None, []) == (0, 0)
    assert pdf_path.read_bytes() == before


_WIDTH_MODEL_SAMPLES = [
    "山田太郎",
    "090-1234-5678",
    "山田太郎の電話番号は090-1234-5678",
    "ABC山田123",
    "Tel: 03-1234-5678 東京都新宿区1-2-3",
    "ｱｲｳ半角",
    "ＡＢＣ１２３",
    "住所 東京都 abc 番号 123",
    "山田 abc 123 東京",
    "abc 山 def",
    "a b  c",
]


@pytest.mark.parametrize("text", _WIDTH_MODEL_SAMPLES)
def test_text_width_model_matches_actual_freetext_layout(tmp_path, text):
    """実測: FreeText の字送りは、フォント状態(Helvetica/CJK)の切り替え規則で予測できる。"""
    doc = fitz.open()
    page = doc.new_page(width=800, height=200)
    annot = page.add_freetext_annot(
        fitz.Rect(20, 50, 780, 80), text, fontsize=10.0, fontname="japan",
        text_color=(0, 0, 0), fill_color=None, border_width=0, opacity=0.0,
    )
    annot.update(opacity=0.0)
    path = tmp_path / "width.pdf"
    doc.save(str(path))
    chars = get_page_chars(str(path), 0)
    actual_em = (chars[-1]["bbox"][2] - chars[0]["bbox"][0]) / 10.0
    assert text_width_em(text) == pytest.approx(actual_em, abs=0.02)


def test_split_segments_separates_cjk_and_latin_runs_keeping_spaces():
    assert split_segments("山田太郎の電話番号は090-1234-5678") == ["山田太郎の電話番号は", "090-1234-5678"]
    assert split_segments("Tel: 03-1234 東京都 abc") == ["Tel: 03-1234 ", "東京都 ", "abc"]
    assert split_segments("  山田 太郎") == ["  山田 太郎"]
    assert split_segments("ABC") == ["ABC"] and split_segments("") == []
    # 区間をつなぎ直すと元の文字列(前後の空白は除く)に戻る。
    for text in _WIDTH_MODEL_SAMPLES:
        assert "".join(split_segments(text)) == text


def test_mixed_line_is_split_so_char_positions_follow_the_real_line_width(tmp_path):
    """日本語+数字の行: 1注釈だと数字が全角幅になり、行末が欠けて位置もずれる(実測)。
    区間ごとの注釈にすると、文字が欠けず、区間の位置が行幅への配分どおりになる。"""
    pdf_path = tmp_path / "mixed.pdf"
    _blank_pdf(pdf_path)
    line = OCRResult("山田太郎の電話番号は090-1234-5678", 40.0, 40.0, 306.0, 24.0, 0, 0.9)
    with fitz.open(str(pdf_path)) as doc:
        assert embed_ocr_results(doc, [line]) == 1  # 1行
        doc.saveIncr()
    assert count_ocr_annots(str(pdf_path)) == 2  # CJK区間 + 数字の区間
    assert count_ocr_lines(str(pdf_path)) == 1

    chars = get_page_chars(str(pdf_path), 0)
    text = _text_of(chars)
    assert text == "山田太郎の電話番号は090-1234-5678"  # 欠けない
    cjk_em = text_width_em("山田太郎の電話番号は")
    total_em = cjk_em + text_width_em("090-1234-5678")
    boundary = 40.0 + 306.0 * cjk_em / total_em
    cjk_chars = [c for c in chars if ord(c["c"]) >= 0x250]
    digit_chars = [c for c in chars if c["c"] in "0123456789-"]
    assert max(c["bbox"][2] for c in cjk_chars) == pytest.approx(boundary, abs=6.0)
    assert min(c["bbox"][0] for c in digit_chars) == pytest.approx(boundary, abs=6.0)
    assert max(c["bbox"][2] for c in digit_chars) == pytest.approx(40.0 + 306.0, abs=6.0)
    # 名前の部分(先頭4文字)は、実際の行の左端から始まり、実際の幅の 4/(全体) に近い範囲に収まる。
    name = chars[:4]
    assert min(c["bbox"][0] for c in name) == pytest.approx(40.0, abs=1.0)
    assert max(c["bbox"][2] for c in name) == pytest.approx(40.0 + 306.0 * 4 / total_em, abs=6.0)
    # 読み戻すと1行に復元される(Undo用)。
    (restored,) = snapshot_ocr_results(str(pdf_path))
    assert restored.text == "山田太郎の電話番号は090-1234-5678"
    assert (restored.x, restored.y, restored.width, restored.height) == pytest.approx(
        (40.0, 40.0, 306.0, 24.0), abs=0.01
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_embed_on_rotated_pages_keeps_text_extractable(tmp_path, rotation):
    pdf_path = tmp_path / f"rot{rotation}.pdf"
    _blank_pdf(pdf_path)
    with fitz.open(str(pdf_path)) as doc:
        doc[0].set_rotation(rotation)
        # 回転後の表示座標(幅200x高さ400 または 400x200)に収まる行。
        assert embed_ocr_results(doc, [OCRResult("山田太郎の電話番号", 10, 60, 170, 20, 0, 0.9)]) == 1
        doc.saveIncr()
    text = _text_of(get_page_chars(str(pdf_path), 0))
    assert text.replace("\n", "") == "山田太郎の電話番号"
    with fitz.open(str(pdf_path)) as doc:
        assert read_ocr_results(doc)[0].text == "山田太郎の電話番号"


# ---------------------------------------------------------------------------
# パイプライン(run_ocr_pages)
# ---------------------------------------------------------------------------


def test_run_ocr_pages_uses_dpi_scale_whiteout_and_progress(tmp_path):
    pdf_path = tmp_path / "pipeline.pdf"
    _blank_pdf(pdf_path, text="SECRET")
    service = FakeOcrService({0: [("山田太郎", (40.0, 20.0, 120.0, 40.0))]})
    progress = []

    results = run_ocr_pages(
        str(pdf_path), [0], dpi=144, service=service, progress_callback=lambda d, t: progress.append((d, t))
    )

    (call,) = service.calls
    assert call["size"] == (800, 400)  # 400x200pt を 144dpi で画像化
    assert call["scale"] == pytest.approx(0.5)  # 72/dpi
    # 既存テキスト(SECRET)の矩形が画像座標(2倍)で白塗り対象として渡り、実際に白い。
    assert len(call["existing_rects"]) == 1
    rx0, ry0, rx1, ry1 = call["existing_rects"][0]
    assert 70 <= rx0 <= 90 and rx1 > 100
    assert call["image"].getpixel((int((rx0 + rx1) / 2), int((ry0 + ry1) / 2))) == (255, 255, 255)
    (r,) = results[0]
    assert (r.text, r.x, r.y, r.width, r.height) == ("山田太郎", pytest.approx(40.0), pytest.approx(20.0), pytest.approx(80.0), pytest.approx(20.0))
    assert progress == [(1, 1)]


def test_run_ocr_pages_only_textless_skips_pages_with_text_and_ignores_old_ocr(tmp_path):
    pdf_path = tmp_path / "textless.pdf"
    doc = fitz.open()
    page_a = doc.new_page(width=400, height=200)
    page_a.insert_text((40, 100), "SECRET", fontsize=18)
    doc.new_page(width=400, height=200)  # テキストなし
    doc.new_page(width=400, height=200)  # OCR済みにする
    doc.save(str(pdf_path))
    doc.close()
    replace_ocr_in_file(str(pdf_path), [2], [OCRResult("済みの行", 40, 60, 80, 20, 2, 0.9)])
    service = FakeOcrService({1: [("山田太郎", (40.0, 20.0, 120.0, 40.0))]})

    results = run_ocr_pages(str(pdf_path), [0, 1, 2], dpi=72, only_textless=True, service=service)

    assert sorted(results) == [1]  # テキスト有り(ページ0)・OCR済み(ページ2)はスキップ
    assert [c["page_num"] for c in service.calls] == [1]
    assert page_has_text(str(pdf_path), 0) and not page_has_text(str(pdf_path), 1)
    assert page_has_text(str(pdf_path), 2)  # 埋め込み済みのOCRもテキストとして扱う


def test_run_ocr_pages_rerun_does_not_whiteout_with_previous_ocr(tmp_path):
    """再実行では、以前のOCR注釈のテキストが白塗り対象にならない(ファイルも変更しない)。"""
    pdf_path = tmp_path / "rerun.pdf"
    _blank_pdf(pdf_path)
    replace_ocr_in_file(str(pdf_path), None, [OCRResult("以前の行", 40, 60, 80, 20, 0, 0.9)])
    before = pdf_path.read_bytes()
    service = FakeOcrService({0: [("新しい行", (40.0, 60.0, 120.0, 80.0))]})

    results = run_ocr_pages(str(pdf_path), [0], dpi=72, service=service)

    assert service.calls[0]["existing_rects"] == []  # 古いOCRは白塗りされない
    assert results[0][0].text == "新しい行"
    assert pdf_path.read_bytes() == before  # 読み取り専用


def test_run_ocr_pages_validates_dpi_and_out_of_range_pages(tmp_path):
    pdf_path = tmp_path / "range.pdf"
    _blank_pdf(pdf_path)
    service = FakeOcrService({})
    with pytest.raises(ValueError):
        run_ocr_pages(str(pdf_path), [0], dpi=0, service=service)
    assert run_ocr_pages(str(pdf_path), [5], dpi=72, service=service) == {}


def test_ocr_worker_emits_results_and_progress(qtbot, tmp_path):
    pdf_path = tmp_path / "worker.pdf"
    _blank_pdf(pdf_path, pages=2)
    service = FakeOcrService({1: [("山田太郎", (40.0, 20.0, 120.0, 40.0))]})
    worker = OcrWorker(str(pdf_path), [0, 1], dpi=72, service=service)
    progress = []
    worker.progress.connect(lambda d, t: progress.append((d, t)))

    with qtbot.waitSignal(worker.finished, timeout=15000) as blocker:
        worker.start()

    results = blocker.args[0]
    assert sorted(results) == [0, 1] and results[0] == [] and results[1][0].text == "山田太郎"
    assert progress == [(1, 2), (2, 2)]
    worker.wait()


def test_ocr_worker_reports_errors(qtbot, tmp_path):
    pdf_path = tmp_path / "worker-error.pdf"
    _blank_pdf(pdf_path)

    class Broken(FakeOcrService):
        def run_ocr_on_page(self, *a, **k):
            raise RuntimeError("engine failed")

    worker = OcrWorker(str(pdf_path), [0], dpi=72, service=Broken({}))
    with qtbot.waitSignal(worker.error, timeout=15000) as blocker:
        worker.start()
    assert "engine failed" in str(blocker.args[0])
    worker.wait()


# ---------------------------------------------------------------------------
# ガード1: OCR注釈はユーザーの注釈として一覧/編集の対象にしない
# ---------------------------------------------------------------------------


def test_ocr_annots_are_excluded_from_freetext_listing(tmp_path):
    pdf_path = tmp_path / "guard1.pdf"
    _blank_pdf(pdf_path)
    replace_ocr_in_file(str(pdf_path), None, [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9)])
    assert list_freetext_annots(str(pdf_path)) == []
    create_freetext_annot(
        str(pdf_path),
        FreeTextAnnotData(
            page_num=0, xref=0, rect=(200.0, 20.0, 300.0, 50.0), content="通常の注釈",
            fontsize=12.0, text_color=(0, 0, 0), fill_color=None, border_color=None,
            border_width=0.0, opacity=1.0,
        ),
    )
    listed = list_freetext_annots(str(pdf_path))
    assert [a.content for a in listed] == ["通常の注釈"]


def test_ocr_page_shows_no_annotation_drawer_entries_and_is_not_hit_testable(qtbot, tmp_path):
    from PyQt6.QtCore import QPoint

    pdf_path = tmp_path / "guard1-window.pdf"
    _blank_pdf(pdf_path)
    replace_ocr_in_file(str(pdf_path), None, [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9)])
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._zoom_object_btn.trigger()  # アノテーションドロワーを開く

    assert window._zoom_annotations == []
    assert window._zoom_label._annotations == []
    # 付箋一覧(ドロワー内)も空。OCRの位置をクリックしても注釈は選ばれない。
    label = window._zoom_label
    offset = label._pixmap_offset()
    center = QPoint(int(offset.x() + 80), int(offset.y() + 70))
    assert label._annotation_hit_test(center)[0] is None
    # ただしOCRテキストはテキスト選択・検索用の文字として取れる。
    assert "山田太郎" in "".join(c["c"] for c in label._chars)


# ---------------------------------------------------------------------------
# ウィンドウ統合(偽のOCRエンジン)
# ---------------------------------------------------------------------------


def _install_fake_ocr(window, monkeypatch, service):
    monkeypatch.setattr(page_edit_ocr_module, "is_ocr_available", lambda: True)
    created = []

    def create(pages, *, only_textless):
        worker = OcrWorker(
            window._pdf_path,
            pages,
            dpi=window._ocr_dpi(),
            only_textless=only_textless,
            service=service,
            parent=window,
        )
        created.append(worker)
        return worker

    window._create_ocr_worker = create
    window._ocr_panel.set_available(True)
    monkeypatch.setattr(
        page_edit_ocr_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
    )
    return created


def _wait_ocr_idle(qtbot, window):
    qtbot.waitUntil(lambda: window._ocr_worker is None, timeout=20000)


def test_ocr_panel_run_undo_redo_and_clear(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "window-ocr.pdf"
    _blank_pdf(pdf_path, pages=2)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    service = FakeOcrService(
        {
            0: [("山田太郎の電話番号", (40.0, 60.0, 202.0, 84.0))],
            1: [("別ページの行", (40.0, 60.0, 130.0, 84.0))],
        }
    )
    _install_fake_ocr(window, monkeypatch, service)
    window._zoom_ocr_action.trigger()
    panel = window._ocr_panel
    assert panel.is_open and panel._ocr_all_btn.isEnabled()

    # このページだけOCR
    panel._ocr_page_btn.click()
    assert not panel._ocr_page_btn.isEnabled()  # 実行中は無効
    _wait_ocr_idle(qtbot, window)
    assert count_ocr_annots(str(pdf_path)) == 1
    assert "山田太郎" in _text_of(get_page_chars(str(pdf_path), 0))
    assert "山田太郎" in "".join(c["c"] for c in window._zoom_label._chars)  # 画面のテキストにも反映
    assert "OCR完了" in panel.status_text()
    assert panel._ocr_page_btn.isEnabled()

    # 全ページOCR(ページ0は置き換え=重複しない)
    panel._ocr_all_btn.click()
    _wait_ocr_idle(qtbot, window)
    assert count_ocr_annots(str(pdf_path)) == 2
    assert count_ocr_annots(str(pdf_path), [0]) == 1

    # Undo/Redo
    window._undo_manager.undo()
    assert count_ocr_annots(str(pdf_path)) == 1
    assert "山田太郎" in _text_of(get_page_chars(str(pdf_path), 0))  # 1回目の結果に戻る
    window._undo_manager.undo()
    assert count_ocr_annots(str(pdf_path)) == 0
    window._undo_manager.redo()
    window._undo_manager.redo()
    assert count_ocr_annots(str(pdf_path)) == 2

    # このページのOCRテキストを削除(Undoで復元)
    panel._clear_page_btn.click()
    assert count_ocr_annots(str(pdf_path), [0]) == 0 and count_ocr_annots(str(pdf_path), [1]) == 1
    assert "山田太郎" not in "".join(c["c"] for c in window._zoom_label._chars)
    window._undo_manager.undo()
    assert count_ocr_annots(str(pdf_path), [0]) == 1

    # 全ページ削除
    panel._clear_all_btn.click()
    assert count_ocr_annots(str(pdf_path)) == 0
    panel._clear_all_btn.click()  # 何も無ければ案内だけ(Undo履歴を積まない)
    assert "削除するOCRテキストはありません" in panel.status_text()


def test_ocr_panel_without_engine_disables_run_and_shows_hint(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "no-engine.pdf"
    _blank_pdf(pdf_path)
    monkeypatch.setattr(page_edit_ocr_module, "is_ocr_available", lambda: False)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)

    window._zoom_ocr_action.trigger()

    panel = window._ocr_panel
    assert panel.is_open and not panel.is_available()
    assert not panel._ocr_all_btn.isEnabled() and not panel._ocr_page_btn.isEnabled()
    assert panel._clear_all_btn.isEnabled()  # 削除はエンジン無しでも可能
    assert not panel._hint_label.isHidden()
    assert "uv sync --extra ocr" in panel._hint_label.text()
    assert window._run_ocr([0]) is False
    assert "uv sync --extra ocr" in panel.status_text()


def _detect_and_wait(qtbot, window):
    window._on_pii_detect_current_page()
    qtbot.waitUntil(
        lambda: window._pii_worker is None and window._ocr_worker is None, timeout=30000
    )


def test_detection_after_ocr_for_textless_page(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "detect-after-ocr.pdf"
    _blank_pdf(pdf_path)  # テキストレイヤ無し(スキャンを模す)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    service = FakeOcrService({0: [("山田太郎の電話番号は090-1234-5678", (40.0, 60.0, 40.0 + 18 * 17, 84.0))]})
    _install_fake_ocr(window, monkeypatch, service)
    settings = window._pii_settings().copy()
    settings.ocr_enabled = True
    window._pii_settings_cache = settings

    _detect_and_wait(qtbot, window)

    assert len(service.calls) == 1
    assert count_ocr_lines(str(pdf_path)) == 1
    annots = list_pii_markup_annots(str(pdf_path))
    assert {a.pii_entity for a in annots} & {"PERSON", "PHONE_NUMBER"}
    person = [a for a in annots if "山田" in a.pii_text]
    assert person and person[0].quads[0][0] == pytest.approx(40.0, abs=8.0)  # OCR行の左端付近(OCR由来のquadは余白ぶん広がる)
    # OCR埋め込みと検出は別々のUndo操作。
    window._undo_manager.undo()
    assert list_pii_markup_annots(str(pdf_path)) == []
    assert count_ocr_lines(str(pdf_path)) == 1
    window._undo_manager.undo()
    assert count_ocr_lines(str(pdf_path)) == 0


def test_detection_ocr_only_for_pages_without_text_and_off_by_default(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "detect-mixed.pdf"
    doc = fitz.open()
    with_text = doc.new_page(width=400, height=200)
    with_text.insert_text((40, 100), "SECRET KEEPME", fontsize=18)
    doc.new_page(width=400, height=200)
    doc.save(str(pdf_path))
    doc.close()
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    service = FakeOcrService({1: [("架空太郎", (40.0, 60.0, 112.0, 84.0))]})
    _install_fake_ocr(window, monkeypatch, service)

    # 既定(OCR設定オフ)ではOCRしない。
    window._on_pii_detect_all_pages()
    qtbot.waitUntil(lambda: window._pii_worker is None, timeout=30000)
    assert service.calls == []

    settings = window._pii_settings().copy()
    settings.ocr_enabled = True
    window._pii_settings_cache = settings
    window._on_pii_detect_all_pages()
    qtbot.waitUntil(lambda: window._pii_worker is None and window._ocr_worker is None, timeout=30000)
    assert [c["page_num"] for c in service.calls] == [1]  # テキストの無いページだけ
    assert count_ocr_annots(str(pdf_path), [0]) == 0 and count_ocr_annots(str(pdf_path), [1]) == 1


def test_detection_continues_without_ocr_when_engine_missing_or_fails(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "detect-ocr-fail.pdf"
    _blank_pdf(pdf_path, text="山田太郎の電話番号は090-1234-5678")
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    settings = window._pii_settings().copy()
    settings.ocr_enabled = True
    window._pii_settings_cache = settings

    # 依存が無い: OCRを飛ばして通常どおり検出される。
    monkeypatch.setattr(page_edit_ocr_module, "is_ocr_available", lambda: False)
    _detect_and_wait(qtbot, window)
    assert list_pii_markup_annots(str(pdf_path))

    # OCRがエラー: 警告を出さずに検出は続行される(テキストがあるページなので対象外だが、
    # ここではエラー経路で検出が止まらないことだけを確認する)。
    window._pii_panel._result_tree.selectAll()
    window._on_pii_remove_selected()
    assert list_pii_markup_annots(str(pdf_path)) == []

    class Broken(FakeOcrService):
        def run_ocr_on_page(self, *a, **k):
            raise RuntimeError("boom")

    blank = tmp_path / "detect-ocr-fail2.pdf"
    _blank_pdf(blank)
    window2 = create_page_edit_window(qtbot, blank)
    open_zoom(window2, qtbot)
    window2._toggle_pii_drawer()
    window2._pii_settings_cache = settings.copy()
    _install_fake_ocr(window2, monkeypatch, Broken({}))
    _detect_and_wait(qtbot, window2)
    assert window2._pii_worker is None and window2._ocr_worker is None
    assert window2._pii_panel._detect_current_btn.isEnabled()


# ---------------------------------------------------------------------------
# ガード2: エクスポートで、削除した文字がOCR注釈に取り残されない
# ---------------------------------------------------------------------------


def _ocr_pdf_with_two_lines(path) -> None:
    _blank_pdf(path)
    replace_ocr_in_file(
        str(path),
        None,
        [
            OCRResult("山田太郎の電話番号は090-1234-5678", 40.0, 40.0, 306.0, 24.0, 0, 0.9),
            OCRResult("架空商事の住所", 40.0, 120.0, 126.0, 24.0, 0, 0.9),
        ],
    )


def _all_annot_text(pdf_path) -> str:
    parts = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            for annot in page.annots() or []:
                parts.append(str(annot.info))
        for xref in range(1, doc.xref_length()):
            try:
                parts.append(doc.xref_object(xref, compressed=False))
            except Exception:  # noqa: BLE001
                continue
    return "\n".join(parts)


def _region_over(chars: list[dict], start: int, end: int) -> tuple[float, float, float, float]:
    return (
        min(c["bbox"][0] for c in chars[start:end]) - 1,
        min(c["bbox"][1] for c in chars[start:end]) - 1,
        max(c["bbox"][2] for c in chars[start:end]) + 1,
        max(c["bbox"][3] for c in chars[start:end]) + 1,
    )


def test_redaction_removes_overlapping_ocr_annots_whole_line(tmp_path):
    """実測(PyMuPDF/MuPDF): apply_redactions は、領域と重なる FreeText 注釈を(一部が重なる
    だけでも)注釈ごと削除する。文字も/Contentsも出力に残らず、重ならない行は残る。
    """
    src = tmp_path / "redact-src.pdf"
    _ocr_pdf_with_two_lines(src)
    chars = get_page_chars(str(src), 0)
    text = _text_of(chars)
    start = text.index("090-1234-5678")
    region = _region_over(chars, start, start + 11)  # 行の末尾の電話番号部分だけ

    out = tmp_path / "redact-out.pdf"
    redact_pdf_remove_text(str(src), str(out), redact_rects={0: [region]}, scrub_ocr_annots=False)

    with fitz.open(str(out)) as doc:
        page_text = doc[0].get_text()
    assert "090-1234-5678" not in page_text
    assert "090-1234-5678" not in _all_annot_text(out)
    assert "架空商事の住所" in page_text  # 重ならない行は残る
    assert count_ocr_lines(str(out)) == 1


def test_scrub_ocr_annots_is_a_safety_net_independent_of_redaction(tmp_path):
    """redaction が注釈を消さない版でも取り残さないよう、領域と重なる行を明示的に削除する。"""
    from src.utils.pdf_utils.export import _scrub_ocr_annots

    src = tmp_path / "scrub-src.pdf"
    _ocr_pdf_with_two_lines(src)
    with fitz.open(str(src)) as doc:
        page = doc[0]
        # 1行目(2区間の注釈)とは重なるが、2行目とは重ならない領域。行全体(両区間)が取り除かれる。
        assert _scrub_ocr_annots(page, [(200.0, 45.0, 260.0, 60.0)]) == 2
        assert _scrub_ocr_annots(page, []) == 0
        assert _scrub_ocr_annots(page, [(0.0, 180.0, 400.0, 200.0)]) == 0  # どの行とも重ならない
        remaining = read_ocr_results(doc)
        assert [r.text for r in remaining] == ["架空商事の住所"]


def test_pii_export_removes_ocr_text_of_masked_words(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "export-ocr.pdf"
    _ocr_pdf_with_two_lines(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    window._toggle_pii_drawer()
    _detect_and_wait(qtbot, window)
    annots = list_pii_markup_annots(str(pdf_path))
    assert any(a.pii_entity == "PHONE_NUMBER" and "090-1234-5678" in a.pii_text for a in annots)

    out_path = tmp_path / "out-ocr.pdf"
    options = {
        "format": "pdf", "dpi": 150, "jpeg_quality": 85, "pdf_optimize_level": 0,
        "pdf_image_dpi": 150, "pdf_image_quality": 75, "rasterize": False, "rasterize_format": "png",
    }
    window._ask_pii_export_options = lambda: options
    monkeypatch.setattr(
        page_edit_pii_module.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_path), ""))
    )
    monkeypatch.setattr(page_edit_pii_module.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(page_edit_pii_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    window._on_pii_export_requested()

    assert out_path.exists()
    with fitz.open(str(out_path)) as doc:
        text = doc[0].get_text()
    assert "090-1234-5678" not in text
    everything = _all_annot_text(out_path)
    assert "090-1234-5678" not in everything  # 注釈のContents/Subject/オブジェクトのどこにも残らない
    assert "pii_entity" not in everything
    # 領域と重ならないOCR行は、テキストとして残る。
    assert "架空商事の住所" in text


# ---------------------------------------------------------------------------
# main.py: PyQt6 より先に onnxruntime / cv2 を import する
# ---------------------------------------------------------------------------


def test_main_imports_onnxruntime_and_cv2_before_pyqt6():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "src" / "main.py").read_text(encoding="utf-8")
    assert "import onnxruntime" in source and "import cv2" in source
    assert source.index("import onnxruntime") < source.index("from PyQt6")
    assert source.index("import cv2") < source.index("from PyQt6")
    assert "except ImportError" in source  # 未導入でも起動できる


def test_settings_dialog_ocr_tab_uses_new_package(qtbot, monkeypatch):
    from src.views import pii_settings_dialog as dialog_module

    monkeypatch.setattr(dialog_module, "is_ocr_available", lambda: False)
    dialog = dialog_module.PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog)
    assert dialog._ocr_enabled_check.isEnabled() is False

    monkeypatch.setattr(dialog_module, "is_ocr_available", lambda: True)
    dialog2 = dialog_module.PiiSettingsDialog(PiiSettings())
    qtbot.addWidget(dialog2)
    assert dialog2._ocr_enabled_check.isEnabled() is True


def test_ocr_run_finding_nothing_keeps_existing_ocr_and_pushes_no_undo(qtbot, monkeypatch, tmp_path):
    pdf_path = tmp_path / "window-ocr-empty.pdf"
    _blank_pdf(pdf_path)
    replace_ocr_in_file(str(pdf_path), None, [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9)])
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    _install_fake_ocr(window, monkeypatch, FakeOcrService({}))  # 何も認識しない
    window._zoom_ocr_action.trigger()
    undo_depth = window._undo_manager.undo_count()

    window._ocr_panel._ocr_page_btn.click()
    _wait_ocr_idle(qtbot, window)

    assert count_ocr_lines(str(pdf_path)) == 1  # 以前の結果は残る
    assert window._undo_manager.undo_count() == undo_depth  # 変化が無いのでUndoに積まない
    assert "認識できませんでした" in window._ocr_panel.status_text()


# ---------------------------------------------------------------------------
# 認識した文字の重ね表示(色・透明度。表示専用、事後変更可)
# ---------------------------------------------------------------------------


def _ink_bbox(image, min_alpha_diff: int = 8):
    """白背景のQImageで、白以外の画素の外接矩形(x0, y0, x1, y1)。無ければ None。"""
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if 255 - min(c.red(), c.green(), c.blue()) >= min_alpha_diff:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _paint_overlay_image(lines, color, opacity, size=(420, 160)):
    from PyQt6.QtGui import QImage, QPainter

    from src.views.ocr_overlay import paint_ocr_lines

    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    drawn = paint_ocr_lines(painter, lines, color, opacity)
    painter.end()
    return image, drawn


def _require_japanese_font():
    """日本語グリフの描画を検証するテスト用。フォントの無い環境(一部のCI/offscreen)ではスキップ。"""
    from PyQt6.QtGui import QFontDatabase

    if not QFontDatabase.families(QFontDatabase.WritingSystem.Japanese):
        pytest.skip("日本語フォントが無い環境")


@pytest.mark.parametrize("text", ["山田太郎 090-1234-5678", "Hello World", "東京都千代田区"])
def test_overlay_text_is_fitted_to_the_line_rect(text):
    from PyQt6.QtCore import QRectF

    if any(ord(c) > 0x3000 for c in text):
        _require_japanese_font()

    rect = QRectF(40, 40, 300, 50)
    image, drawn = _paint_overlay_image([(text, rect)], (0.0, 0.0, 0.0), 1.0)
    assert drawn == 1
    x0, y0, x1, y1 = _ink_bbox(image)
    # 字面(インク)の外接矩形が行の矩形にほぼ一致する(縦横とも数px以内)。
    assert abs(x0 - rect.left()) <= 3 and abs(x1 - rect.right()) <= 3
    assert abs(y0 - rect.top()) <= 3 and abs(y1 - rect.bottom()) <= 3


def test_overlay_uses_given_color_and_opacity_and_zero_draws_nothing():
    from PyQt6.QtCore import QRectF

    line = [("山田太郎", QRectF(20, 20, 200, 60))]
    hidden, drawn = _paint_overlay_image(line, (1.0, 0.0, 0.0), 0.0)
    assert drawn == 0 and _ink_bbox(hidden) is None

    def darkest(image):
        best = None
        for y in range(image.height()):
            for x in range(image.width()):
                c = image.pixelColor(x, y)
                if best is None or c.green() < best.green():
                    best = c
        return best

    strong, _ = _paint_overlay_image(line, (1.0, 0.0, 0.0), 1.0)
    faint, _ = _paint_overlay_image(line, (1.0, 0.0, 0.0), 0.3)
    blue, _ = _paint_overlay_image(line, (0.0, 0.0, 1.0), 1.0)
    s, f, b = darkest(strong), darkest(faint), darkest(blue)
    assert s.red() > 200 and s.green() < 60 and s.blue() < 60  # ほぼ純粋な赤
    assert f.green() > s.green() + 80  # 透明度が上がるほど白に近い
    assert b.blue() > 200 and b.red() < 60  # 色の変更が反映される


def test_overlay_vertical_line_stacks_characters():
    from PyQt6.QtCore import QRectF

    _require_japanese_font()

    rect = QRectF(50, 10, 40, 140)
    image, drawn = _paint_overlay_image([("山田太郎です", rect)], (0.0, 0.0, 0.0), 1.0)
    assert drawn == 1
    x0, y0, x1, y1 = _ink_bbox(image)
    assert x0 >= rect.left() - 3 and x1 <= rect.right() + 3
    assert y0 >= rect.top() - 3 and y1 <= rect.bottom() + 3
    assert (y1 - y0) > rect.height() * 0.6  # 縦に並んでいる(横書きで潰れていない)


def test_ocr_text_style_settings_roundtrip_and_clamp():
    settings = PiiSettings()
    assert settings.ocr_text_visible is False
    settings.ocr_text_visible = True
    settings.ocr_text_color = (0.1, 0.5, 0.9)
    settings.ocr_text_transparency = 25
    settings.save()
    loaded = PiiSettings.load()
    assert loaded.ocr_text_visible is True
    assert loaded.ocr_text_color == pytest.approx((0.1, 0.5, 0.9))
    assert loaded.ocr_text_transparency == 25
    assert loaded.ocr_text_opacity == pytest.approx(0.75)
    loaded.ocr_text_transparency = 500
    assert loaded.ocr_text_opacity == 0.0


def test_ocr_text_overlay_follows_panel_style_without_touching_pdf(qtbot, tmp_path):
    pdf_path = tmp_path / "overlay-window.pdf"
    _blank_pdf(pdf_path)
    replace_ocr_in_file(
        str(pdf_path),
        None,
        [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9), OCRResult("Hello", 40, 100, 60, 20, 0, 0.9)],
    )
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    label = window._zoom_label
    panel = window._ocr_panel
    before = pdf_path.read_bytes()

    # 既定は非表示(従来どおり何も重ならない)。
    assert label._ocr_lines == [] or label._ocr_text_opacity == 0.0

    # 表示をオン: OCR済みの行が、再OCRなしで重なる(不透明度=1-透明度)。
    panel._text_visible_check.setChecked(True)
    assert [t for t, _ in label._ocr_lines] == ["山田太郎", "Hello"]
    assert label._ocr_text_opacity == pytest.approx(0.5)
    assert label._ocr_lines[0][1] == pytest.approx((40.0, 60.0, 120.0, 80.0))

    # 事後に色・透明度を変える。画面へ即時反映し、設定へ保存する。
    panel._text_transparency_slider.setValue(20)
    assert label._ocr_text_opacity == pytest.approx(0.8)
    panel._text_color = (0.0, 0.0, 1.0)
    panel._emit_text_style()
    assert label._ocr_text_color == (0.0, 0.0, 1.0)
    saved = PiiSettings.load()
    assert saved.ocr_text_visible and saved.ocr_text_color == pytest.approx((0.0, 0.0, 1.0))
    assert saved.ocr_text_transparency == 20

    # 表示するページを描き直しても重ね表示は維持され、オフにすると消える。
    window._refresh_current_zoom_page()
    assert len(label._ocr_lines) == 2 and label._ocr_text_opacity == pytest.approx(0.8)
    panel._text_visible_check.setChecked(False)
    assert label._ocr_lines == []

    # 表示専用: PDFのバイト列は一切変わらない。
    assert pdf_path.read_bytes() == before


def test_ocr_text_overlay_restores_saved_style_and_undo_of_ocr_updates_it(qtbot, tmp_path):
    saved = PiiSettings()
    saved.ocr_text_visible = True
    saved.ocr_text_color = (0.0, 0.6, 0.0)
    saved.ocr_text_transparency = 10
    saved.save()
    pdf_path = tmp_path / "overlay-restore.pdf"
    _blank_pdf(pdf_path)
    window = create_page_edit_window(qtbot, pdf_path)
    open_zoom(window, qtbot)
    label = window._zoom_label
    assert window._ocr_panel.text_style() == (True, pytest.approx((0.0, 0.6, 0.0)), 10)
    assert label._ocr_lines == []  # OCR前は何も無い

    window._apply_ocr_lines([0], [OCRResult("山田太郎", 40, 60, 80, 20, 0, 0.9)], "OCR")
    assert [t for t, _ in label._ocr_lines] == ["山田太郎"]  # OCR実行直後から色付きで重なる
    assert label._ocr_text_opacity == pytest.approx(0.9)
    window._undo_manager.undo()
    assert label._ocr_lines == []
