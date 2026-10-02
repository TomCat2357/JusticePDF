"""Windows shell integration helpers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


def _normalize_existing_paths(paths: Sequence[str]) -> list[str]:
    """Return normalized existing paths, preserving first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()

    for path in paths:
        normalized_path = os.path.abspath(path)
        if normalized_path in seen or not os.path.exists(normalized_path):
            continue
        seen.add(normalized_path)
        normalized.append(normalized_path)

    return normalized


def show_native_file_context_menu(
    parent_hwnd: int,
    paths: Sequence[str],
    screen_pos,
) -> bool:
    """Show the native Explorer context menu for one or more files."""
    if sys.platform != "win32":
        return False

    normalized_paths = _normalize_existing_paths(paths)
    if not normalized_paths:
        return False

    parent_dirs = {str(Path(path).parent) for path in normalized_paths}
    if len(parent_dirs) != 1:
        logger.warning("Native context menu requires files from the same parent directory.")
        return False

    try:
        x = int(screen_pos.x())
        y = int(screen_pos.y())
    except AttributeError:
        x, y = screen_pos
        x = int(x)
        y = int(y)

    import pythoncom
    import win32con
    import win32gui
    from win32com.shell import shell, shellcon

    menu = None
    co_initialized = False

    try:
        pythoncom.CoInitialize()
        co_initialized = True

        desktop = shell.SHGetDesktopFolder()
        parent_dir = next(iter(parent_dirs))
        _, parent_pidl, _ = desktop.ParseDisplayName(0, None, parent_dir)
        parent_folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)

        item_pidls = []
        for path in normalized_paths:
            _, item_pidl, _ = parent_folder.ParseDisplayName(0, None, Path(path).name)
            item_pidls.append(item_pidl)

        _, context_menu = parent_folder.GetUIObjectOf(
            int(parent_hwnd),
            item_pidls,
            shell.IID_IContextMenu,
        )

        menu = win32gui.CreatePopupMenu()
        context_menu.QueryContextMenu(
            menu,
            0,
            1,
            0x7FFF,
            shellcon.CMF_EXPLORE | shellcon.CMF_CANRENAME,
        )

        if parent_hwnd and win32gui.IsWindow(int(parent_hwnd)):
            try:
                win32gui.SetForegroundWindow(int(parent_hwnd))
            except Exception:
                logger.debug("SetForegroundWindow failed; continuing without foreground activation.", exc_info=True)

        command_id = win32gui.TrackPopupMenu(
            menu,
            win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD,
            x,
            y,
            0,
            int(parent_hwnd),
            None,
        )

        if parent_hwnd and win32gui.IsWindow(int(parent_hwnd)):
            win32gui.PostMessage(int(parent_hwnd), win32con.WM_NULL, 0, 0)

        if command_id <= 0:
            return False

        context_menu.InvokeCommand(
            (
                0,
                int(parent_hwnd),
                command_id - 1,
                None,
                None,
                win32con.SW_SHOWNORMAL,
                0,
                0,
            )
        )
        return True
    except Exception:
        logger.exception("Failed to show native Windows context menu.")
        return False
    finally:
        if menu is not None:
            try:
                win32gui.DestroyMenu(menu)
            except Exception:
                logger.debug("Popup menu cleanup failed.", exc_info=True)
        if co_initialized:
            pythoncom.CoUninitialize()
