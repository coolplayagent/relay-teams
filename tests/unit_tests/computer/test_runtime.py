# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import struct

from relay_teams.computer import (
    DisabledComputerRuntime,
    LinuxDesktopRuntime,
    ScriptedComputerRuntime,
    WindowsDesktopRuntime,
    build_default_computer_runtime,
)


def test_build_default_computer_runtime_returns_scripted_runtime_in_fake_mode(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_TEAMS_COMPUTER_RUNTIME", "fake")

    runtime = build_default_computer_runtime(project_root=tmp_path)

    assert isinstance(runtime, ScriptedComputerRuntime)
    result = asyncio.run(runtime.capture_screen())
    assert result.observation is not None
    assert result.observation.screenshot_bytes is not None
    screenshot_bytes = result.observation.screenshot_bytes
    assert screenshot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", screenshot_bytes[16:24]) == (320, 180)
    assert len(screenshot_bytes) > 500


def test_scripted_computer_runtime_uses_explicit_screenshot_path(tmp_path) -> None:
    screenshot_path = tmp_path / "example.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    runtime = ScriptedComputerRuntime(
        project_root=tmp_path,
        screenshot_path=screenshot_path,
    )

    result = asyncio.run(runtime.capture_screen())
    assert result.observation is not None
    assert result.observation.screenshot_bytes == b"\x89PNG\r\n\x1a\nfake"


def test_build_default_computer_runtime_auto_detects_windows_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_TEAMS_COMPUTER_RUNTIME", raising=False)
    monkeypatch.setattr(
        "relay_teams.computer.runtime._platform_system",
        lambda: "windows",
    )

    runtime = build_default_computer_runtime(project_root=tmp_path)

    assert isinstance(runtime, WindowsDesktopRuntime)


def test_build_default_computer_runtime_auto_detects_linux_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_TEAMS_COMPUTER_RUNTIME", raising=False)
    monkeypatch.setattr(
        "relay_teams.computer.runtime._platform_system",
        lambda: "linux",
    )

    runtime = build_default_computer_runtime(project_root=tmp_path)

    assert isinstance(runtime, LinuxDesktopRuntime)


def test_build_default_computer_runtime_returns_disabled_runtime_on_macos(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_TEAMS_COMPUTER_RUNTIME", raising=False)
    monkeypatch.setattr(
        "relay_teams.computer.runtime._platform_system",
        lambda: "darwin",
    )

    runtime = build_default_computer_runtime(project_root=tmp_path)

    assert isinstance(runtime, DisabledComputerRuntime)


def test_scripted_runtime_launch_and_focus_updates_window_state(tmp_path) -> None:
    screenshot_path = tmp_path / "desktop.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nfocus")
    runtime = ScriptedComputerRuntime(
        project_root=tmp_path,
        screenshot_path=screenshot_path,
    )

    launch_result = asyncio.run(runtime.launch_app(app_name="Calculator"))
    assert launch_result.observation is not None
    assert launch_result.observation.focused_window == "Calculator Window"
    assert any(
        window.title == "Calculator Window"
        for window in launch_result.observation.windows
    )

    focus_result = asyncio.run(runtime.focus_window(window_title="Agent Teams"))
    assert focus_result.observation is not None
    assert focus_result.observation.focused_window == "Agent Teams Demo"
