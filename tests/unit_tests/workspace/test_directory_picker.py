# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from agent_teams.workspace import directory_picker


def test_pick_workspace_directory_uses_zenity_on_linux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{tmp_path / 'selected-root'}\n",
            stderr="",
        )

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        directory_picker.shutil,
        "which",
        lambda value: f"/usr/bin/{value}" if value == "zenity" else None,
    )
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected == (tmp_path / "selected-root").resolve()
    assert captured["command"] == [
        "/usr/bin/zenity",
        "--file-selection",
        "--directory",
        "--title",
        "Select project folder",
        "--filename",
        f"{(tmp_path / 'start').resolve()}/",
    ]
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60.0


def test_pick_workspace_directory_uses_kdialog_fallback_on_linux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        captured["command"] = command
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{tmp_path / 'picked-from-kdialog'}\n",
            stderr="",
        )

    def fake_which(value: str) -> str | None:
        if value == "kdialog":
            return "/usr/bin/kdialog"
        return None

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(directory_picker.shutil, "which", fake_which)
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected == (tmp_path / "picked-from-kdialog").resolve()
    assert captured["command"] == [
        "/usr/bin/kdialog",
        "--getexistingdirectory",
        str((tmp_path / "start").resolve()),
        "--title",
        "Select project folder",
    ]


def test_pick_workspace_directory_returns_none_when_linux_picker_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (command, check, capture_output, text, timeout)
        return subprocess.CompletedProcess(
            args=["/usr/bin/zenity"],
            returncode=1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        directory_picker.shutil,
        "which",
        lambda value: f"/usr/bin/{value}" if value == "zenity" else None,
    )
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path)

    assert selected is None


def test_pick_workspace_directory_raises_when_no_linux_picker_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(directory_picker.shutil, "which", lambda value: None)

    with pytest.raises(RuntimeError, match="Native directory picker is unavailable"):
        _ = directory_picker.pick_workspace_directory(initial_dir=tmp_path)
