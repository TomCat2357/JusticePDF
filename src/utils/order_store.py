"""Persistence for per-folder file/subfolder ordering.

Stores the manual drag-and-drop order (and the active sort mode) for each
folder ever opened by the app in a single central JSON file, keyed by the
folder's normalized absolute path. See ``docs/folder-order-persistence-plan.md``
for the design rationale.

This module is intentionally Qt-free (no ``QStandardPaths``) so it can be
unit tested without a running ``QApplication``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def _default_store_path() -> Path:
    """Return the default store location under ``%APPDATA%``.

    Falls back to ``~/.justicepdf`` when ``APPDATA`` is not set (e.g. on
    non-Windows platforms or a stripped-down environment).
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "JusticePDF" / "JusticePDF" / "folder_order.json"
    return Path.home() / ".justicepdf" / "folder_order.json"


DEFAULT_STORE_PATH = _default_store_path()


def folder_key(path: str | Path) -> str:
    """Normalize a folder path into a stable, case-insensitive lookup key."""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def merge_order(disk_names: list[str], saved_names: list[str]) -> list[str]:
    """Combine the current directory listing with a previously saved order.

    Rules (see design doc §1-3):
        1. Names present in both keep the saved relative order, first.
        2. Names only on disk are appended at the end, sorted case-insensitively.
        3. Names only in the saved order (no longer on disk) are dropped.
    """
    disk_set = set(disk_names)
    saved_set = set(saved_names)
    kept = [n for n in saved_names if n in disk_set]
    added = sorted((n for n in disk_names if n not in saved_set), key=str.lower)
    return kept + added


def _load_store(store_path: Path) -> dict[str, Any]:
    """Load the raw store dict, returning an empty skeleton on any failure."""
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": _SCHEMA_VERSION, "folders": {}}

    if not isinstance(data, dict):
        return {"version": _SCHEMA_VERSION, "folders": {}}
    folders = data.get("folders")
    if not isinstance(folders, dict):
        folders = {}
    return {"version": _SCHEMA_VERSION, "folders": folders}


def _write_store(store_path: Path, data: dict[str, Any]) -> None:
    """Atomically write ``data`` to ``store_path`` via a temp file + replace."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(store_path.parent), prefix=".folder_order_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, store_path)
    finally:
        # If os.replace succeeded the temp file no longer exists; ignore
        # failures removing a leftover temp file after a write error.
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def load_folder_order(folder: str | Path, store_path: Path | None = None) -> dict | None:
    """Return the saved order entry for ``folder``, or ``None`` if unavailable.

    Any failure reading or parsing the store (missing file, corrupt JSON,
    permission error) is swallowed and treated as "no saved order".
    """
    path = Path(store_path) if store_path is not None else DEFAULT_STORE_PATH
    try:
        data = _load_store(path)
        entry = data["folders"].get(folder_key(folder))
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    return entry


def save_folder_order(
    folder: str | Path,
    *,
    manual_files: list[str] | None = None,
    manual_subfolders: list[str] | None = None,
    sort_order: str,
    sort_ascending: bool,
    store_path: Path | None = None,
) -> None:
    """Persist the order/sort state for ``folder``.

    ``manual_files``/``manual_subfolders`` are optional: pass ``None`` to
    leave the previously saved manual order untouched while still updating
    ``sort_order``/``sort_ascending`` (e.g. when the user switches to name/date
    sorting, the manual order must survive so switching back to "manual"
    restores it). This is a hard invariant of the design.

    Failures (permission errors, unwritable path, etc.) are logged and
    swallowed; persistence must never crash the app.
    """
    path = Path(store_path) if store_path is not None else DEFAULT_STORE_PATH
    key = folder_key(folder)
    try:
        data = _load_store(path)
        folders = data["folders"]
        existing = folders.get(key)
        if not isinstance(existing, dict):
            existing = {}

        entry = dict(existing)
        if manual_files is not None:
            entry["manual_files"] = list(manual_files)
        else:
            entry.setdefault("manual_files", existing.get("manual_files", []))
        if manual_subfolders is not None:
            entry["manual_subfolders"] = list(manual_subfolders)
        else:
            entry.setdefault("manual_subfolders", existing.get("manual_subfolders", []))
        entry["sort_order"] = sort_order
        entry["sort_ascending"] = sort_ascending
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()

        folders[key] = entry
        data["folders"] = folders
        _write_store(path, data)
    except Exception:
        logger.warning("Failed to save folder order for %s", folder, exc_info=True)


def prune(store_path: Path | None = None, max_entries: int = 500) -> None:
    """Drop entries for folders that no longer exist, and cap the store size.

    When the number of remaining entries still exceeds ``max_entries``, the
    oldest entries (by ``updated_at``) are dropped first.
    """
    path = Path(store_path) if store_path is not None else DEFAULT_STORE_PATH
    try:
        data = _load_store(path)
        folders = data["folders"]

        alive = {k: v for k, v in folders.items() if os.path.isdir(k)}

        if len(alive) > max_entries:
            def _updated_at(item: tuple[str, Any]) -> str:
                value = item[1]
                if isinstance(value, dict):
                    return str(value.get("updated_at", ""))
                return ""

            ordered = sorted(alive.items(), key=_updated_at, reverse=True)
            alive = dict(ordered[:max_entries])

        if alive != folders:
            data["folders"] = alive
            _write_store(path, data)
    except Exception:
        logger.warning("Failed to prune folder order store.", exc_info=True)
