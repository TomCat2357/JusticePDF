"""ファイル分解(結合の逆操作)。

MainWindow の mixin。既存の「結合」(``_on_merge_selected``,
main_window_fileops.py)を構造的に踏襲する: Phase A(即時UI更新) →
Phase B(バックアップ→実行→ゴミ箱)を ``_run_file_operation`` で非同期実行し、
Undo/Redo を登録する。
"""
import os
import logging
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox
from send2trash import send2trash

from src.models.undo_manager import UndoAction
from src.utils.pdf_utils import plan_split, split_pdf_by_toc

logger = logging.getLogger(__name__)


class SplitMixin:
    """PDFファイルの分解(しおりの最上位階層、または1ページずつ)。"""

    def _on_split_selected(self) -> None:
        """選択した1つのPDFを、しおりの最上位階層(無ければページ単位)で分解する。

        - ちょうど1つのPDFファイルが選択されている場合のみ動作する。
        - 分解後、元のファイルはゴミ箱へ移動する(Undo で復元可)。
        - 生成された各ファイルは現在のフォルダへ書き出し、カードとして追加・選択する。
        """
        if self._operation_in_progress:
            return

        file_paths = self._selected_card_paths_in_grid_order()
        if len(file_paths) != 1:
            return
        src_path = file_paths[0]

        parts, mode = plan_split(src_path)
        if not parts:
            QMessageBox.warning(self, "分解", "分解できるページがありません。")
            return

        basename = os.path.basename(src_path)
        n = len(parts)
        if mode == "toc":
            preview_lines = "\n".join(
                f"　{i + 1}. {p.filename}" for i, p in enumerate(parts[:5])
            )
            if n > 5:
                preview_lines += f"\n　…他 {n - 5} 件"
            message = (
                f"「{basename}」を しおりの最上位階層 で {n} 個のファイルに分解します。\n\n"
                f"{preview_lines}\n\n"
                "分解後、元のファイルはゴミ箱へ移動します\n"
                "（「元に戻す」で復元できます）。\n\n"
                "続けますか？"
            )
        else:
            message = (
                f"「{basename}」には しおりがありません。\n"
                f"ページごとに {n} 個のファイルに分解します。\n"
                f"（{parts[0].filename} … {parts[-1].filename}）\n\n"
                "分解後、元のファイルはゴミ箱へ移動します\n"
                "（「元に戻す」で復元できます）。\n\n"
                "続けますか？"
            )

        reply = QMessageBox.question(
            self,
            "分解",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        out_dir = str(self._work_dir)
        card = self._get_card_by_path(src_path)

        # --- Phase A: 即時 UI 更新(分解元カードを外す) ---
        self._register_internal_remove([src_path])
        if card is not None:
            if card in self._selected_cards:
                self._selected_cards.remove(card)
            if card in self._cards:
                self._cards.remove(card)
            card.deleteLater()
        self._refresh_grid()
        self._begin_async_operation()

        # --- Phase B: 重い I/O — バックアップ → 分解 → 元ファイルをゴミ箱へ ---
        def _do_io() -> dict[str, object]:
            backup_dir = tempfile.mkdtemp(prefix="justicepdf_split_backup_")
            backup_path = str(Path(backup_dir) / Path(src_path).name)
            shutil.copy2(src_path, backup_path)

            created_paths = split_pdf_by_toc(src_path, out_dir, parts)

            try:
                send2trash(src_path)
            except OSError:
                # ゴミ箱へ送れなかった場合は生成済みファイルを削除してロールバック
                for p in created_paths:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                raise
            return {"backup_path": backup_path, "created_paths": created_paths}

        def _select_paths(paths_to_select: list[str]) -> None:
            self._clear_selection()
            for path in paths_to_select:
                c = self._get_card_by_path(path)
                if c is not None:
                    c.set_selected(True)
                    self._selected_cards.append(c)
            self._update_button_states()

        def _on_success(result: object) -> None:
            info: dict[str, object] = result  # type: ignore[assignment]
            backup_path: str = info["backup_path"]  # type: ignore[assignment]
            created_paths: list[str] = info["created_paths"]  # type: ignore[assignment]

            self._clear_selection()
            for p in created_paths:
                c = self._get_card_by_path(p) or self._add_card(p)
                c.set_selected(True)
                self._selected_cards.append(c)
            self._refresh_grid()
            self._update_button_states()

            # Redo で出力名が変わりうる(同名ファイルが増えていれば ensure_unique_path が
            # (1) を付ける)ため、undo は「最後に実際に作られたパス」を見る必要がある。
            current_paths: list[str] = list(created_paths)

            def do_split_sync() -> list[str]:
                self._register_internal_add(current_paths)
                new_created = split_pdf_by_toc(src_path, out_dir, parts)
                self._register_internal_add(new_created)
                current_paths[:] = new_created
                self._register_internal_remove([src_path])
                if os.path.exists(src_path):
                    try:
                        send2trash(src_path)
                    except Exception:
                        pass
                self._remove_card(src_path)
                self._clear_selection()
                for p in new_created:
                    c = self._get_card_by_path(p) or self._add_card(p)
                    c.set_selected(True)
                    self._selected_cards.append(c)
                self._refresh_grid()
                self._update_button_states()
                return new_created

            def undo_split() -> None:
                self._register_internal_remove(current_paths)
                for p in current_paths:
                    self._remove_card(p)
                    if os.path.exists(p):
                        try:
                            send2trash(p)
                        except Exception:
                            try:
                                os.unlink(p)
                            except OSError:
                                pass
                self._register_internal_add([src_path])
                if not os.path.exists(src_path):
                    shutil.copy2(backup_path, src_path)
                if self._get_card_by_path(src_path) is None:
                    self._add_card(src_path)
                self._refresh_grid()
                _select_paths([src_path])

            def redo_split() -> None:
                do_split_sync()

            self._undo_manager.add_action(UndoAction(
                description="分解",
                undo_func=undo_split,
                redo_func=redo_split,
            ))
            self._end_async_operation()

        def _on_error(exc: Exception) -> None:
            # ロールバック: 外したカードを戻し、予約を解除する
            if os.path.exists(src_path):
                self._internal_removes.discard(self._normalize_path(src_path))
                if self._get_card_by_path(src_path) is None:
                    self._add_card(src_path)
            self._refresh_grid()
            self._end_async_operation()
            QMessageBox.warning(self, "分解", f"分解に失敗しました: {exc}")

        self._run_file_operation(_do_io, _on_success, _on_error)
