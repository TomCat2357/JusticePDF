"""結合のための PDF 変換(PDF 以外を一時ファイルへ変換する)。

MainWindow の mixin。状態はすべて self 上に持つ。
変換は ImportWorker(MODE_CONVERT)で GUI スレッド外に走らせ、進捗とキャンセルは
他の長い処理(インポート・［PDFに変換］)と同じ QProgressDialog で出す。
"""
import os
import shutil
import tempfile
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

from src.workers.import_worker import MODE_CONVERT, ImportWorker, find_soffice

RETRY = "再試行"
SKIP = "抜かして続ける"
ABORT = "やめる"


class MergeMixin:
    """結合の確認ダイアログの選択肢と、PDF 以外の一時変換。"""

    def _ask_choice(self, title: str, text: str, labels: list[str]) -> str | None:
        """``labels`` のボタンを並べた確認を出し、押されたボタンの文字列を返す(閉じたら None)。

        先頭が既定のボタン、最後が閉じたとき(Esc)の扱い。
        """
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        buttons = [
            box.addButton(label, QMessageBox.ButtonRole.AcceptRole) for label in labels
        ]
        box.setDefaultButton(buttons[0])
        box.setEscapeButton(buttons[-1])
        box.exec()
        clicked = box.clickedButton()
        for label, button in zip(labels, buttons):
            if clicked is button:
                return label
        return None

    def _convert_for_merge(
        self,
        sources: list[str],
        on_done: Callable[[dict[str, str], str], None],
    ) -> None:
        """``sources`` を一時フォルダへ PDF に変換し、終わったら ``on_done(変換結果, 一時フォルダ)``。

        変換結果は「元のパス → 変換済みの一時 PDF」。失敗があれば 再試行 / 抜かして続ける /
        やめる を尋ねる。やめる・キャンセル・変換できたものが 0 件のときは ``on_done`` を
        呼ばず、一時フォルダも消す。``on_done`` を受け取った側が一時フォルダを消す責任を持つ。
        """
        tmp_dir = tempfile.mkdtemp(prefix="justicepdf_merge_conv_")
        converted: dict[str, str] = {}
        counter = [0]

        def _abort() -> None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        def _start(paths: list[str]) -> None:
            tree: list[tuple[str, str]] = []
            for src in paths:
                counter[0] += 1
                tree.append((src, os.path.join(tmp_dir, f"{counter[0]:04d}.pdf")))
            total = len(tree)

            progress = QProgressDialog("PDF に変換中...", "キャンセル", 0, total, self)
            progress.setWindowTitle("結合")
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)

            worker = ImportWorker(tree, find_soffice(), parent=self, mode=MODE_CONVERT)

            def _on_progress(current: int, total_: int, filename: str) -> None:
                progress.setMaximum(max(1, total_))
                progress.setValue(current)
                if filename:
                    progress.setLabelText(f"PDF に変換中 ({current + 1}/{total_}) {filename}")

            def _on_finished(imported: list, failed: list, cancelled: bool) -> None:
                progress.close()
                self._active_import_worker = None
                self._active_import_progress = None
                self._end_async_operation()
                worker.deleteLater()
                done = set(imported)
                for src, dest in tree:
                    if dest in done:
                        converted[src] = dest
                if cancelled:
                    _abort()
                    return
                if failed:
                    lines = "\n".join(
                        f"・{os.path.basename(s)}: {r}" for s, r in failed[:5])
                    if len(failed) > 5:
                        lines += f"\n…（他 {len(failed) - 5} 件）"
                    choice = self._ask_choice(
                        "結合",
                        f"{len(failed)} 件を PDF に変換できませんでした。\n{lines}",
                        [RETRY, SKIP, ABORT],
                    )
                    if choice == RETRY:
                        _start([s for s, _ in failed])
                        return
                    if choice != SKIP:
                        _abort()
                        return
                on_done(converted, tmp_dir)

            worker.progress_updated.connect(_on_progress)
            worker.finished_all.connect(_on_finished)
            progress.canceled.connect(worker.request_cancel)

            self._active_import_worker = worker
            self._active_import_progress = progress
            self._begin_async_operation()
            worker.start()

        _start(list(sources))
