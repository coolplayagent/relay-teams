# -*- coding: utf-8 -*-
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

_IGNORED_DIR_NAMES = frozenset({".git", "__pycache__"})


def compute_plugin_tree_sha256(plugin_root: Path) -> str:
    resolved_root = plugin_root.expanduser().resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ValueError(f"Plugin directory does not exist: {resolved_root}")
    digest = sha256()
    for path in sorted(
        item
        for item in resolved_root.rglob("*")
        if item.is_file() and not _is_ignored_path(root=resolved_root, path=item)
    ):
        relative_path = path.relative_to(resolved_root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def verify_plugin_tree_sha256(*, plugin_root: Path, expected_sha256: str) -> None:
    normalized_expected = expected_sha256.strip().lower()
    if not normalized_expected:
        return
    actual = compute_plugin_tree_sha256(plugin_root)
    if actual != normalized_expected:
        raise ValueError(
            "Plugin source checksum mismatch: "
            f"expected {normalized_expected}, got {actual}"
        )


def _is_ignored_path(*, root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    return any(part in _IGNORED_DIR_NAMES for part in relative_parts)
