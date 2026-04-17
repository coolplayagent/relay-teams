# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import types

import pytest

from relay_teams.workspace import directory_opener


def test_open_workspace_directory_uses_explorer_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "workspace-root"
    target_path.mkdir()
    captured_command: list[str] = []
    captured_kwargs: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured_command[:] = command
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(directory_opener.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        directory_opener.shutil,
        "which",
        lambda name: "C:/Windows/explorer.exe" if name == "explorer" else None,
    )
    monkeypatch.setattr(directory_opener.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        directory_opener.subprocess,
        "STARTUPINFO",
        lambda: types.SimpleNamespace(dwFlags=0, wShowWindow=0),
        raising=False,
    )
    monkeypatch.setattr(
        directory_opener.subprocess,
        "STARTF_USESHOWWINDOW",
        1,
        raising=False,
    )
    monkeypatch.setattr(directory_opener.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(
        directory_opener.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        2,
        raising=False,
    )
    monkeypatch.setattr(
        directory_opener.subprocess,
        "DETACHED_PROCESS",
        4,
        raising=False,
    )
    monkeypatch.setattr(
        directory_opener.subprocess,
        "CREATE_NO_WINDOW",
        8,
        raising=False,
    )

    directory_opener.open_workspace_directory(target_path)

    assert captured_command == [
        "C:/Windows/explorer.exe",
        str(target_path.resolve()),
    ]
    assert captured_kwargs == {
        "stdout": directory_opener.subprocess.DEVNULL,
        "stderr": directory_opener.subprocess.DEVNULL,
        "stdin": directory_opener.subprocess.DEVNULL,
        "creationflags": 14,
        "startupinfo": captured_kwargs["startupinfo"],
    }


def test_open_workspace_directory_falls_back_to_gio_on_linux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "workspace-root"
    target_path.mkdir()
    captured_command: list[str] = []
    captured_kwargs: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured_command[:] = command
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(directory_opener.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        directory_opener.shutil,
        "which",
        lambda name: "/usr/bin/gio" if name == "gio" else None,
    )
    monkeypatch.setattr(directory_opener.subprocess, "Popen", fake_popen)

    directory_opener.open_workspace_directory(target_path)

    assert captured_command == [
        "/usr/bin/gio",
        "open",
        str(target_path.resolve()),
    ]
    assert captured_kwargs == {
        "stdout": directory_opener.subprocess.DEVNULL,
        "stderr": directory_opener.subprocess.DEVNULL,
        "stdin": directory_opener.subprocess.DEVNULL,
        "start_new_session": True,
    }


def test_open_workspace_directory_uses_open_on_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "workspace-root"
    target_path.mkdir()
    captured_command: list[str] = []
    captured_kwargs: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured_command[:] = command
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(directory_opener.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        directory_opener.shutil,
        "which",
        lambda name: "/usr/bin/open" if name == "open" else None,
    )
    monkeypatch.setattr(directory_opener.subprocess, "Popen", fake_popen)

    directory_opener.open_workspace_directory(target_path)

    assert captured_command == [
        "/usr/bin/open",
        str(target_path.resolve()),
    ]
    assert captured_kwargs == {
        "stdout": directory_opener.subprocess.DEVNULL,
        "stderr": directory_opener.subprocess.DEVNULL,
        "stdin": directory_opener.subprocess.DEVNULL,
        "start_new_session": True,
    }


def test_open_workspace_directory_rejects_unsupported_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "workspace-root"
    target_path.mkdir()

    monkeypatch.setattr(directory_opener.platform, "system", lambda: "Plan9")

    with pytest.raises(RuntimeError, match="Native file manager is unavailable"):
        directory_opener.open_workspace_directory(target_path)


def test_open_workspace_directory_raises_when_launch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "workspace-root"
    target_path.mkdir()

    monkeypatch.setattr(directory_opener.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        directory_opener.shutil,
        "which",
        lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
    )

    def fail_popen(command: list[str], **kwargs: object) -> object:
        _ = (command, kwargs)
        raise OSError("permission denied")

    monkeypatch.setattr(directory_opener.subprocess, "Popen", fail_popen)

    with pytest.raises(RuntimeError, match="Failed to launch native file manager"):
        directory_opener.open_workspace_directory(target_path)
