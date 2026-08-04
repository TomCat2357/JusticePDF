"""Tests for the Qt-free folder order persistence store."""
from __future__ import annotations

import json

from src.utils import order_store


# ─────────────────────────────────────────────────────────────────
# merge_order
# ─────────────────────────────────────────────────────────────────

def test_merge_order_restores_saved_order_unchanged():
    disk = ["a.pdf", "b.pdf", "c.pdf"]
    saved = ["b.pdf", "a.pdf", "c.pdf"]
    assert order_store.merge_order(disk, saved) == ["b.pdf", "a.pdf", "c.pdf"]


def test_merge_order_appends_new_disk_files_in_natural_order():
    disk = ["a.pdf", "b.pdf", "z.pdf", "d.pdf"]
    saved = ["b.pdf", "a.pdf"]
    # New files "z.pdf" and "d.pdf" are appended, sorted case-insensitively.
    assert order_store.merge_order(disk, saved) == ["b.pdf", "a.pdf", "d.pdf", "z.pdf"]


def test_merge_order_drops_deleted_files():
    disk = ["a.pdf", "c.pdf"]
    saved = ["b.pdf", "a.pdf", "c.pdf"]
    assert order_store.merge_order(disk, saved) == ["a.pdf", "c.pdf"]


def test_merge_order_empty_saved():
    disk = ["b.pdf", "a.pdf"]
    saved: list[str] = []
    assert order_store.merge_order(disk, saved) == ["a.pdf", "b.pdf"]


def test_merge_order_empty_disk():
    disk: list[str] = []
    saved = ["a.pdf", "b.pdf"]
    assert order_store.merge_order(disk, saved) == []


def test_merge_order_case_insensitive_natural_sort_of_added():
    disk = ["Banana.pdf", "apple.pdf", "cherry.pdf"]
    saved: list[str] = []
    assert order_store.merge_order(disk, saved) == ["apple.pdf", "Banana.pdf", "cherry.pdf"]


# ─────────────────────────────────────────────────────────────────
# folder_key
# ─────────────────────────────────────────────────────────────────

def test_folder_key_normalizes_case(tmp_path):
    folder = tmp_path / "MyFolder"
    folder.mkdir()
    lower = str(folder).lower()
    upper = str(folder).upper()
    assert order_store.folder_key(lower) == order_store.folder_key(upper)


def test_folder_key_normalizes_separators(tmp_path):
    folder = tmp_path / "sub" / "dir"
    folder.mkdir(parents=True)
    with_forward_slashes = str(folder).replace("\\", "/")
    assert order_store.folder_key(with_forward_slashes) == order_store.folder_key(str(folder))


def test_folder_key_resolves_relative_paths(tmp_path, monkeypatch):
    folder = tmp_path / "rel"
    folder.mkdir()
    monkeypatch.chdir(tmp_path)
    assert order_store.folder_key("rel") == order_store.folder_key(str(folder))


# ─────────────────────────────────────────────────────────────────
# save_folder_order / load_folder_order round trip
# ─────────────────────────────────────────────────────────────────

def test_save_and_load_round_trip(tmp_path):
    store_path = tmp_path / "folder_order.json"
    folder = tmp_path / "case1"
    folder.mkdir()

    order_store.save_folder_order(
        folder,
        manual_files=["b.pdf", "a.pdf"],
        manual_subfolders=["2024", "2023"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )

    entry = order_store.load_folder_order(folder, store_path=store_path)
    assert entry is not None
    assert entry["manual_files"] == ["b.pdf", "a.pdf"]
    assert entry["manual_subfolders"] == ["2024", "2023"]
    assert entry["sort_order"] == "manual"
    assert entry["sort_ascending"] is True
    assert "updated_at" in entry


def test_load_folder_order_missing_entry_returns_none(tmp_path):
    store_path = tmp_path / "folder_order.json"
    folder = tmp_path / "never_saved"
    folder.mkdir()
    assert order_store.load_folder_order(folder, store_path=store_path) is None


def test_load_folder_order_missing_store_file_returns_none(tmp_path):
    store_path = tmp_path / "does_not_exist" / "folder_order.json"
    folder = tmp_path / "case2"
    folder.mkdir()
    assert order_store.load_folder_order(folder, store_path=store_path) is None


# ─────────────────────────────────────────────────────────────────
# The core invariant: manual_* survives sort-order-only updates
# ─────────────────────────────────────────────────────────────────

def test_save_with_none_manual_preserves_existing_manual_order(tmp_path):
    store_path = tmp_path / "folder_order.json"
    folder = tmp_path / "case3"
    folder.mkdir()

    order_store.save_folder_order(
        folder,
        manual_files=["z.pdf", "a.pdf"],
        manual_subfolders=["sub2", "sub1"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )

    # User switches to name-order sorting; manual_files/subfolders must not
    # be overwritten even though they aren't passed this time.
    order_store.save_folder_order(
        folder,
        sort_order="name",
        sort_ascending=False,
        store_path=store_path,
    )

    entry = order_store.load_folder_order(folder, store_path=store_path)
    assert entry["manual_files"] == ["z.pdf", "a.pdf"]
    assert entry["manual_subfolders"] == ["sub2", "sub1"]
    assert entry["sort_order"] == "name"
    assert entry["sort_ascending"] is False


def test_save_with_none_manual_on_fresh_entry_defaults_to_empty(tmp_path):
    store_path = tmp_path / "folder_order.json"
    folder = tmp_path / "case4"
    folder.mkdir()

    order_store.save_folder_order(
        folder,
        sort_order="date",
        sort_ascending=True,
        store_path=store_path,
    )

    entry = order_store.load_folder_order(folder, store_path=store_path)
    assert entry["manual_files"] == []
    assert entry["manual_subfolders"] == []
    assert entry["sort_order"] == "date"


# ─────────────────────────────────────────────────────────────────
# Corrupt store handling
# ─────────────────────────────────────────────────────────────────

def test_load_folder_order_corrupt_json_returns_none(tmp_path):
    store_path = tmp_path / "folder_order.json"
    store_path.write_text("{not valid json", encoding="utf-8")
    folder = tmp_path / "case5"
    folder.mkdir()
    assert order_store.load_folder_order(folder, store_path=store_path) is None


def test_save_folder_order_does_not_raise_on_corrupt_store(tmp_path):
    store_path = tmp_path / "folder_order.json"
    store_path.write_text("{not valid json", encoding="utf-8")
    folder = tmp_path / "case6"
    folder.mkdir()

    # Corrupt existing store must not prevent a fresh save from succeeding.
    order_store.save_folder_order(
        folder,
        manual_files=["a.pdf"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )
    entry = order_store.load_folder_order(folder, store_path=store_path)
    assert entry["manual_files"] == ["a.pdf"]


# ─────────────────────────────────────────────────────────────────
# Read-modify-write: multiple folders don't clobber each other
# ─────────────────────────────────────────────────────────────────

def test_multiple_folders_saved_independently(tmp_path):
    store_path = tmp_path / "folder_order.json"
    folder_a = tmp_path / "folder_a"
    folder_b = tmp_path / "folder_b"
    folder_a.mkdir()
    folder_b.mkdir()

    order_store.save_folder_order(
        folder_a,
        manual_files=["a1.pdf"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )
    order_store.save_folder_order(
        folder_b,
        manual_files=["b1.pdf"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )

    entry_a = order_store.load_folder_order(folder_a, store_path=store_path)
    entry_b = order_store.load_folder_order(folder_b, store_path=store_path)
    assert entry_a["manual_files"] == ["a1.pdf"]
    assert entry_b["manual_files"] == ["b1.pdf"]

    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(raw["folders"]) == 2


# ─────────────────────────────────────────────────────────────────
# prune
# ─────────────────────────────────────────────────────────────────

def test_prune_removes_nonexistent_folders(tmp_path):
    store_path = tmp_path / "folder_order.json"
    existing = tmp_path / "still_here"
    existing.mkdir()
    missing = tmp_path / "deleted_folder"

    order_store.save_folder_order(
        existing,
        manual_files=["a.pdf"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )
    order_store.save_folder_order(
        missing,
        manual_files=["b.pdf"],
        sort_order="manual",
        sort_ascending=True,
        store_path=store_path,
    )

    order_store.prune(store_path=store_path)

    assert order_store.load_folder_order(existing, store_path=store_path) is not None
    assert order_store.load_folder_order(missing, store_path=store_path) is None
