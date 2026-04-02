# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from agent_teams.paths import filesystem


def test_to_filesystem_path_prefixes_long_windows_drive_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    long_path = Path("C:/") / ("nested/" * 40) / "file.txt"

    resolved = filesystem.to_filesystem_path(long_path)

    assert resolved.startswith("\\\\?\\C:\\")
    assert resolved.endswith("file.txt")


def test_to_filesystem_path_prefixes_long_windows_unc_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    long_unc = Path("//server/share") / ("nested/" * 40) / "file.txt"

    resolved = filesystem.to_filesystem_path(long_unc)

    assert resolved.startswith("\\\\?\\UNC\\server\\share\\")
    assert resolved.endswith("file.txt")


def test_to_filesystem_path_leaves_short_windows_path_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")

    resolved = filesystem.to_filesystem_path(Path("C:/demo/file.txt"))

    assert resolved == "C:\\demo\\file.txt"


def test_path_exists_uses_extended_length_path_for_windows_long_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    calls: list[str] = []

    def fake_exists(path: str) -> bool:
        calls.append(path)
        return True

    monkeypatch.setattr(filesystem.os.path, "exists", fake_exists)
    long_path = Path("C:/") / ("nested/" * 40) / "file.txt"

    assert filesystem.path_exists(long_path) is True
    assert calls == [filesystem.to_filesystem_path(long_path)]


class _FakeDirEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeScandir:
    def __enter__(self) -> list[_FakeDirEntry]:
        return [_FakeDirEntry("alpha.txt"), _FakeDirEntry("beta")]

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_iter_dir_paths_uses_extended_length_path_for_windows_long_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    calls: list[str] = []
    long_dir = Path("C:/") / ("nested/" * 40)

    def fake_scandir(path: str) -> _FakeScandir:
        calls.append(path)
        return _FakeScandir()

    monkeypatch.setattr(filesystem.os, "scandir", fake_scandir)

    entries = filesystem.iter_dir_paths(long_dir)

    assert entries == (long_dir / "alpha.txt", long_dir / "beta")
    assert calls == [filesystem.to_filesystem_path(long_dir)]


class _FakeHandle:
    def __enter__(self) -> _FakeHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> str:
        return "hello"


def test_open_text_file_uses_extended_length_path_for_windows_long_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    calls: list[tuple[str, str, str, str | None]] = []
    long_path = Path("C:/") / ("nested/" * 40) / "file.txt"

    def fake_open(
        path: str,
        mode: str,
        encoding: str | None = None,
        newline: str | None = None,
    ) -> _FakeHandle:
        calls.append((path, mode, encoding or "", newline))
        return _FakeHandle()

    monkeypatch.setattr(builtins, "open", fake_open)

    with filesystem.open_text_file(long_path, newline="") as handle:
        assert handle.read() == "hello"

    assert calls == [(filesystem.to_filesystem_path(long_path), "r", "utf-8", "")]


def test_make_dirs_uses_extended_length_path_for_windows_long_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    calls: list[tuple[str, bool]] = []
    long_dir = Path("C:/") / ("nested/" * 40)

    def fake_makedirs(path: str, exist_ok: bool) -> None:
        calls.append((path, exist_ok))

    monkeypatch.setattr(filesystem.os, "makedirs", fake_makedirs)

    filesystem.make_dirs(long_dir, exist_ok=True)

    assert calls == [(filesystem.to_filesystem_path(long_dir), True)]
