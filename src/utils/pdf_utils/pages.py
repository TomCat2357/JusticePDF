"""TOC(しおり)・メタデータ・ページ操作・PDF結合。"""
import logging
import os
from dataclasses import dataclass

import fitz



logger = logging.getLogger(__name__)

from .common import _save_document_in_place


@dataclass(slots=True)
class TocEntry:
    """A single PDF bookmark (outline) entry.

    PyMuPDF の TOC 仕様に合わせて level / page はともに 1 始まり。
    """

    level: int
    title: str
    page: int



def update_pdf_metadata_title(pdf_path: str, title: str) -> None:
    """PDFメタデータのTitleプロパティを更新する。"""
    with fitz.open(pdf_path) as doc:
        meta = doc.metadata
        meta['title'] = title
        doc.set_metadata(meta)
        _save_document_in_place(doc, pdf_path)


def get_pdf_metadata_title(pdf_path: str) -> str:
    """PDFメタデータのTitleプロパティを取得する。"""
    try:
        with fitz.open(pdf_path) as doc:
            meta = doc.metadata or {}
            title = meta.get("title")
            return str(title) if isinstance(title, str) else ""
    except Exception:
        logger.debug("Failed to read PDF metadata title: %s", pdf_path, exc_info=True)
        return ""


def get_pdf_toc(pdf_path: str) -> list[TocEntry]:
    """PDFのしおり(アウトライン/TOC)を取得する。

    取得できない場合や例外時は空リストを返す。各エントリの level / page は
    1 始まり（PyMuPDF仕様）。
    """
    try:
        with fitz.open(pdf_path) as doc:
            raw = doc.get_toc(simple=True)  # [[level, title, page], ...]
            return [
                TocEntry(level=int(level), title=str(title), page=int(page))
                for level, title, page in raw
            ]
    except Exception:
        logger.debug("Failed to read PDF TOC: %s", pdf_path, exc_info=True)
        return []


def normalize_toc(
    entries: list[TocEntry], *, page_count: int | None = None
) -> list[TocEntry]:
    """``set_toc`` が受理できる形にしおりを補正する。

    - 先頭は強制的に level 1
    - 各 level は直前の level+1 を上限にクランプ（下限 1）
    - 空タイトルは "(無題)" にフォールバック
    - page は page_count 指定時に 1..page_count へクランプ

    例外を投げず「正す」方針。UI での昇格/降格の中間状態でも安全に保存できる。
    """
    result: list[TocEntry] = []
    prev_level = 0
    for entry in entries:
        level = max(1, min(int(entry.level), prev_level + 1))
        title = entry.title if entry.title.strip() else "(無題)"
        page = int(entry.page)
        if page_count is not None and page_count > 0:
            page = max(1, min(page, page_count))
        else:
            page = max(1, page)
        result.append(TocEntry(level=level, title=title, page=page))
        prev_level = level
    return result


def update_pdf_toc(
    pdf_path: str, entries: list[TocEntry], *, incremental: bool = False
) -> None:
    """しおりを一括設定する。``entries`` が空ならしおりを全削除する。

    TOC 再構築は旧オブジェクトを残してファイルが肥大するため、既定では全保存
    （``incremental=False``）を用いる。
    """
    with fitz.open(pdf_path) as doc:
        normalized = normalize_toc(entries, page_count=doc.page_count)
        toc_list = [[e.level, e.title, e.page] for e in normalized]
        doc.set_toc(toc_list)
        _save_document_in_place(doc, pdf_path, incremental=incremental)


def _file_bookmark_title(pdf_path: str) -> str:
    """しおりに使うファイル名(カードと同じく拡張子つきの basename)。"""
    return os.path.basename(pdf_path)


def _file_toc_entries(title: str, start0: int, sub: list) -> list[TocEntry]:
    """新しく結合する1ファイル分の TOC を組む。

    開始ページにファイル名(level 1)を置き、そのファイルが元々持っていたしおり
    ``sub`` はその子(level+1)としてページをオフセットして並べる。
    ``start0`` は結合後ドキュメントでの 0 始まり開始ページ。
    """
    entries: list[TocEntry] = [TocEntry(level=1, title=title, page=start0 + 1)]
    for level, sub_title, page in sub:
        entries.append(
            TocEntry(level=int(level) + 1, title=str(sub_title), page=int(page) + start0)
        )
    return entries


def _offset_toc_entries(toc: list, offset: int) -> list[TocEntry]:
    """既存しおりを level は変えずページだけ ``offset`` して TocEntry 化する。

    結合先(dest)が既に持つファイル名しおりを再ネストせず、そのままの階層で残す
    ために使う。これにより1ファイルずつ重ねても階層が深くならない(フラットを維持)。
    """
    return [
        TocEntry(level=int(level), title=str(title), page=int(page) + offset)
        for level, title, page in toc
    ]



def create_empty_pdf(pdf_path: str) -> None:
    """Create an empty PDF with 1 blank page.

    Note: PyMuPDF cannot save PDFs with 0 pages,
    so this creates a PDF with a single blank page.
    """
    doc = fitz.open()
    doc.new_page()  # Add one blank page
    doc.save(pdf_path)
    doc.close()



def merge_pdfs(
    output_path: str, pdf_paths: list[str], *, add_file_bookmarks: bool = False
) -> None:
    """Merge multiple PDFs into one.

    add_file_bookmarks=True のとき、結合した各ファイルの開始ページにファイル名の
    しおり(アウトライン)を level 1 で付け、各ファイルが元々持つしおりはその子として残す。
    ファイル名しおりは常にフラット(全ファイルが level 1)に並ぶ。
    """
    output_doc = fitz.open()
    entries: list[TocEntry] = []
    start = 0
    for path in pdf_paths:
        with fitz.open(path) as src_doc:
            count = len(src_doc)
            sub = src_doc.get_toc(simple=True) if add_file_bookmarks else []
            output_doc.insert_pdf(src_doc)
        if add_file_bookmarks and count > 0:
            entries.extend(_file_toc_entries(_file_bookmark_title(path), start, sub))
        start += count
    if add_file_bookmarks and entries:
        entries = normalize_toc(entries, page_count=len(output_doc))
        output_doc.set_toc([[e.level, e.title, e.page] for e in entries])
    output_doc.save(output_path)
    output_doc.close()


def merge_pdfs_in_place(
    dest_path: str,
    pdf_paths: list[str],
    *,
    insert_at: int | None = None,
) -> None:
    """Merge PDFs into an existing destination file in place.

    If insert_at is None, append to end. Otherwise insert sequentially starting at insert_at.
    Uses incremental save when possible; falls back to full save to temp.

    しおりは「フラットな単純引継ぎ」: 結合先(dest)自身の TOC と、挿入する各ファイルが
    元々持つ TOC を、ページ番号だけオフセットして連結する(各エントリの level はそのまま
    保持)。ファイル名の親しおりは付けないので、1ファイルずつ繰り返し重ねても階層は深く
    ならず、内部の親子関係(例: 章とその節)はそのまま引き継がれる。
    insert_at は None(末尾追加)または 0(先頭挿入)のときのみしおりを書き込む。
    """
    if not pdf_paths:
        return

    dest_doc = fitz.open(dest_path)
    try:
        dest_orig_count = len(dest_doc)
        dest_orig_toc = dest_doc.get_toc(simple=True)
        source_entries: list[TocEntry] = []
        if insert_at is None:
            start = dest_orig_count
            for path in pdf_paths:
                if path == dest_path:
                    continue
                with fitz.open(path) as src_doc:
                    count = len(src_doc)
                    sub = src_doc.get_toc(simple=True)
                    dest_doc.insert_pdf(src_doc)
                if count > 0 and sub:
                    source_entries.extend(_offset_toc_entries(sub, start))
                start += count
            dest_start = 0
        else:
            idx = insert_at
            for path in pdf_paths:
                if path == dest_path:
                    continue
                with fitz.open(path) as src_doc:
                    count = len(src_doc)
                    sub = src_doc.get_toc(simple=True)
                    dest_doc.insert_pdf(src_doc, start_at=idx)
                if count > 0 and sub:
                    source_entries.extend(_offset_toc_entries(sub, idx))
                idx += count
            total_inserted = idx - insert_at
            # 先頭挿入(insert_at==0)のとき、結合先の元ページは挿入分だけ後ろへずれる
            dest_start = total_inserted if insert_at == 0 else 0

        # しおり書き込みは insert_at が None / 0 のときのみ(結合先ページが連続するケース)
        if insert_at in (None, 0):
            entries = list(source_entries)
            if dest_orig_count > 0 and dest_orig_toc:
                # 結合先の既存しおりも level はそのまま、ページだけずらして引き継ぐ
                entries.extend(_offset_toc_entries(dest_orig_toc, dest_start))
            if entries:
                # ページ順に並べ替え(安定ソートなので同ページ内の親→子順は保たれる)
                entries.sort(key=lambda e: e.page)
                entries = normalize_toc(entries, page_count=len(dest_doc))
                dest_doc.set_toc([[e.level, e.title, e.page] for e in entries])
        _save_document_in_place(dest_doc, dest_path, incremental=True)
    finally:
        dest_doc.close()


def merge_paths_to_pdf(output_path: str, paths: list[str]) -> int:
    """選択したファイル/フォルダを1つのPDFに結合し、フォルダ構成を反映した階層しおりを付ける。

    - ``paths`` の各要素はファイル(.pdf)またはフォルダ。与えられた順に処理する
      (トップレベルの並び順は呼び出し側が決める)。
    - フォルダはしおりの見出し(その階層)になり、中身(.pdf とサブフォルダ)を名前順に
      子として再帰的に並べる。入れ子フォルダはさらに深い階層になる。
    - フォルダ見出しは、その配下で最初に現れるページを指す。
    - ファイルはファイル名の見出しになり、そのファイルが元々持つしおりを子として
      ぶら下げる(内部の階層もそのまま保持)。
    - PDF ページを1ページも含まないフォルダは見出しごと省略する。
    - 戻り値は結合したページ総数。0 のとき出力ファイルは作成されない。
    """
    output_doc = fitz.open()

    def build(path: str, level: int) -> list[TocEntry]:
        """``path`` 配下のページを output_doc に挿入しつつ、しおり項目を返す。"""
        if os.path.isdir(path):
            start0 = len(output_doc)
            child_entries: list[TocEntry] = []
            try:
                names = sorted(os.listdir(path), key=str.lower)
            except OSError:
                names = []
            for name in names:
                full = os.path.join(path, name)
                if os.path.isdir(full) or name.lower().endswith(".pdf"):
                    child_entries.extend(build(full, level + 1))
            if len(output_doc) <= start0:
                return []  # PDF ページを含まない空フォルダは省略
            title = os.path.basename(os.path.normpath(path)) or path
            return [TocEntry(level=level, title=title, page=start0 + 1), *child_entries]

        # ファイル(.pdf のみ): ファイル名を見出し(level)に、ファイルが元々持つしおりは
        # その子(level + 内部level)としてページをオフセットしてぶら下げる。
        if not path.lower().endswith(".pdf"):
            return []
        try:
            with fitz.open(path) as src_doc:
                count = len(src_doc)
                if count == 0:
                    return []
                start0 = len(output_doc)
                sub = src_doc.get_toc(simple=True)
                output_doc.insert_pdf(src_doc)
        except Exception:
            logger.warning("結合をスキップしました(開けません): %s", path)
            return []
        entries = [TocEntry(level=level, title=_file_bookmark_title(path), page=start0 + 1)]
        for sub_level, sub_title, sub_page in sub:
            entries.append(
                TocEntry(
                    level=level + int(sub_level),
                    title=str(sub_title),
                    page=int(sub_page) + start0,
                )
            )
        return entries

    try:
        entries: list[TocEntry] = []
        for path in paths:
            if os.path.exists(path):
                entries.extend(build(path, 1))
        total = len(output_doc)
        if total == 0:
            return 0
        entries = normalize_toc(entries, page_count=total)
        output_doc.set_toc([[e.level, e.title, e.page] for e in entries])
        output_doc.save(output_path)
        return total
    finally:
        output_doc.close()


def extract_pages(src_path: str, output_path: str, page_indices: list[int]) -> bool:
    """Extract specific pages from a PDF to a new file.

    Returns:
        True if extraction succeeded, False if no pages to extract.
    """
    src_doc = fitz.open(src_path)
    output_doc = fitz.open()
    for idx in page_indices:
        if 0 <= idx < len(src_doc):
            output_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)

    # Check if output has any pages
    if len(output_doc) == 0:
        output_doc.close()
        src_doc.close()
        return False

    output_doc.save(output_path)
    output_doc.close()
    src_doc.close()
    return True


def remove_pages(pdf_path: str, page_indices: list[int]) -> bool:
    """Remove specific pages from a PDF (in place).

    Returns:
        True if the file was deleted (all pages removed), False otherwise.
    """
    from send2trash import send2trash

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pages_to_remove = [idx for idx in page_indices if 0 <= idx < total_pages]

    if len(pages_to_remove) >= total_pages:
        # All pages removed - delete the file
        doc.close()
        send2trash(pdf_path)
        return True

    for idx in sorted(pages_to_remove, reverse=True):
        doc.delete_page(idx)
    try:
        _save_document_in_place(doc, pdf_path, incremental=True)
        return False
    finally:
        doc.close()


def rotate_pages(pdf_path: str, page_indices: list[int], angle: int = 90) -> None:
    """Rotate specific pages in a PDF (in place)."""
    doc = fitz.open(pdf_path)
    try:
        for idx in page_indices:
            if 0 <= idx < len(doc):
                page = doc[idx]
                page.set_rotation((page.rotation + angle) % 360)
        _save_document_in_place(doc, pdf_path, incremental=True)
    finally:
        doc.close()


def reorder_pages(pdf_path: str, new_order: list[int]) -> None:
    """Reorder pages in a PDF (in place)."""
    doc = fitz.open(pdf_path)
    try:
        doc.select(new_order)
        _save_document_in_place(doc, pdf_path, incremental=True)
    finally:
        doc.close()


def insert_pages(dest_path: str, src_path: str, insert_indices: list[int]) -> None:
    """Insert pages from src_path into dest_path at specified indices.
    
    Args:
        dest_path: Destination PDF path
        src_path: Source PDF path containing pages to insert
        insert_indices: List of positions where each page should be inserted
    """
    dest_doc = fitz.open(dest_path)
    src_doc = fitz.open(src_path)
    try:
        # Insert pages in reverse order to maintain correct indices
        for i in reversed(range(len(src_doc))):
            if i < len(insert_indices):
                insert_at = insert_indices[i]
                dest_doc.insert_pdf(src_doc, from_page=i, to_page=i, start_at=insert_at)

        _save_document_in_place(dest_doc, dest_path, incremental=True)
    finally:
        dest_doc.close()
        src_doc.close()

