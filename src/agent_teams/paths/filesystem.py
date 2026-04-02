# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import IO

_WINDOWS_LONG_PATH_THRESHOLD = 248
_WINDOWS_EXTENDED_PATH_PREFIX = "\\\\?\\"
_WINDOWS_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC_PATH_RE = re.compile(r"^[\\/]{2}[^\\/]+[\\/][^\\/]+")


def _normalize_windows_path(raw_path: str) -> str:
    return raw_path.replace("/", "\\")


def _is_windows_absolute_path(raw_path: str) -> bool:
    return bool(
        _WINDOWS_DRIVE_PATH_RE.match(raw_path)
        or _WINDOWS_UNC_PATH_RE.match(raw_path)
        or raw_path.startswith(_WINDOWS_EXTENDED_PATH_PREFIX)
    )


def to_filesystem_path(path: Path) -> str:
    raw_path = str(path)
    if sys.platform != "win32":
        return raw_path
    if raw_path.startswith(_WINDOWS_EXTENDED_PATH_PREFIX):
        return raw_path

    normalized_path = _normalize_windows_path(raw_path)
    if not _is_windows_absolute_path(normalized_path):
        return normalized_path
    if len(normalized_path) < _WINDOWS_LONG_PATH_THRESHOLD:
        return normalized_path
    if normalized_path.startswith("\\\\"):
        return _WINDOWS_EXTENDED_UNC_PREFIX + normalized_path.lstrip("\\")
    return _WINDOWS_EXTENDED_PATH_PREFIX + normalized_path


def path_exists(path: Path) -> bool:
    return os.path.exists(to_filesystem_path(path))


def path_is_file(path: Path) -> bool:
    return os.path.isfile(to_filesystem_path(path))


def path_is_dir(path: Path) -> bool:
    return os.path.isdir(to_filesystem_path(path))


def path_stat(path: Path) -> os.stat_result:
    return os.stat(to_filesystem_path(path))


def read_text_file(
    path: Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> str:
    with open_text_file(path, encoding=encoding, newline=newline) as handle:
        return handle.read()


def read_bytes_file(path: Path) -> bytes:
    with open_binary_file(path, mode="rb") as handle:
        return handle.read()


def open_text_file(
    path: Path,
    *,
    mode: str = "r",
    encoding: str = "utf-8",
    newline: str | None = None,
) -> IO[str]:
    return open(
        to_filesystem_path(path),
        mode,
        encoding=encoding,
        newline=newline,
    )


def open_binary_file(path: Path, *, mode: str = "rb") -> IO[bytes]:
    return open(to_filesystem_path(path), mode)


def iter_dir_paths(path: Path) -> tuple[Path, ...]:
    entries: list[Path] = []
    with os.scandir(to_filesystem_path(path)) as iterator:
        for entry in iterator:
            entries.append(path / entry.name)
    return tuple(entries)


def make_dirs(path: Path, *, exist_ok: bool) -> None:
    os.makedirs(to_filesystem_path(path), exist_ok=exist_ok)


def replace_path(source: Path, target: Path) -> None:
    os.replace(to_filesystem_path(source), to_filesystem_path(target))


def unlink_path(path: Path, *, missing_ok: bool) -> None:
    try:
        os.unlink(to_filesystem_path(path))
    except FileNotFoundError:
        if not missing_ok:
            raise
