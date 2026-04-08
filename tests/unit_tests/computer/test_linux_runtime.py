# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

from relay_teams.computer import (
    ComputerObservation,
    ComputerWindow,
    LinuxDesktopRuntime,
)


def test_linux_runtime_capture_screen_reads_generated_png(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    def fake_env(name: str) -> str:
        return ":0" if name == "DISPLAY" else ""

    def fake_which(name: str) -> str | None:
        if name == "gnome-screenshot":
            return f"/usr/bin/{name}"
        return None

    def fake_run(command: list[str]) -> str:
        output_path = Path(command[-1])
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nlinux")
        return ""

    monkeypatch.setattr(runtime, "_env", fake_env)
    monkeypatch.setattr(runtime, "_which", fake_which)
    monkeypatch.setattr(runtime, "_run_command", fake_run)

    result = asyncio.run(runtime.capture_screen())

    assert result.observation is not None
    assert result.observation.screenshot_bytes == b"\x89PNG\r\n\x1a\nlinux"
    assert result.observation.screenshot_mime_type == "image/png"
    assert result.data["runtime_mode"] == "linux"


def test_linux_runtime_list_windows_marks_active_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    def fake_env(name: str) -> str:
        return ":0" if name == "DISPLAY" else ""

    def fake_which(name: str) -> str | None:
        if name in {"wmctrl", "xdotool"}:
            return f"/usr/bin/{name}"
        return None

    def fake_run(command: list[str]) -> str:
        if command == ["xdotool", "getactivewindow"]:
            return "1\n"
        if command == ["wmctrl", "-lx"]:
            return (
                "0x00000001  0 host gnome-calculator.Gnome-calculator  Calculator\n"
                "0x00000002  0 host google-chrome.Google-chrome  Chrome DevTools\n"
            )
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(runtime, "_env", fake_env)
    monkeypatch.setattr(runtime, "_which", fake_which)
    monkeypatch.setattr(runtime, "_run_command", fake_run)

    result = asyncio.run(runtime.list_windows())

    assert result.observation is not None
    assert result.observation.focused_window == "Calculator"
    assert [window.title for window in result.observation.windows] == [
        "Calculator",
        "Chrome DevTools",
    ]
    assert result.observation.windows[0].focused is True
    assert result.observation.windows[1].focused is False


def test_linux_runtime_launch_app_records_real_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    spawned_commands: list[list[str]] = []
    matched_window = ComputerWindow(
        window_id="0x00000003",
        app_name="gnome calculator",
        title="Calculator",
        focused=True,
    )
    observation = ComputerObservation(
        text="Linux desktop runtime snapshot.",
        windows=(matched_window,),
        focused_window="Calculator",
    )

    def fake_list_windows_snapshot(
        *,
        require_windows: bool,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        _ = (require_windows, require_input)
        return ()

    def fake_resolve_launch_command(app_name: str) -> list[str]:
        assert app_name == "Calculator"
        return ["gnome-calculator"]

    def fake_spawn_process(command: list[str]) -> None:
        spawned_commands.append(command)

    def fake_wait_for_window_match(
        *,
        query: str,
        before_windows: tuple[ComputerWindow, ...] = (),
    ) -> ComputerWindow:
        assert query == "Calculator"
        assert before_windows == ()
        return matched_window

    def fake_build_observation(
        *,
        require_windows: bool,
        require_input: bool,
        require_screenshot: bool,
    ) -> ComputerObservation:
        _ = (require_windows, require_input, require_screenshot)
        return observation

    monkeypatch.setattr(runtime, "_list_windows_snapshot", fake_list_windows_snapshot)
    monkeypatch.setattr(runtime, "_resolve_launch_command", fake_resolve_launch_command)
    monkeypatch.setattr(runtime, "_spawn_process", fake_spawn_process)
    monkeypatch.setattr(runtime, "_wait_for_window_match", fake_wait_for_window_match)
    monkeypatch.setattr(runtime, "_build_observation", fake_build_observation)

    result = asyncio.run(runtime.launch_app(app_name="Calculator"))

    assert spawned_commands == [["gnome-calculator"]]
    assert result.observation is not None
    assert result.observation.focused_window == "Calculator"
    assert result.action.target.app_name == "Calculator"
    assert result.action.target.window_title == "Calculator"
    assert result.data["launched_command"] == "gnome-calculator"


def test_linux_runtime_resolves_calculator_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    def fake_which(name: str) -> str | None:
        if name == "gnome-calculator":
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(runtime, "_which", fake_which)

    command = runtime._resolve_launch_command("Calculator")

    assert command == ["gnome-calculator"]


def test_linux_runtime_lists_windows_with_xdotool_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    def fake_env(name: str) -> str:
        return ":0" if name == "DISPLAY" else ""

    def fake_which(name: str) -> str | None:
        if name in {"xdotool", "xprop"}:
            return f"/usr/bin/{name}"
        return None

    def fake_run(command: list[str]) -> str:
        if command == ["xdotool", "getactivewindow"]:
            return "3\n"
        if command == ["xdotool", "search", "--onlyvisible", "--name", "."]:
            return "3\n4\n"
        if command == ["xdotool", "getwindowname", "3"]:
            return "Calculator\n"
        if command == ["xdotool", "getwindowname", "4"]:
            return "Agent Teams - Google Chrome\n"
        if command == ["xprop", "-id", "3", "WM_CLASS"]:
            return 'WM_CLASS(STRING) = "gnome-calculator", "Gnome-calculator"\n'
        if command == ["xprop", "-id", "4", "WM_CLASS"]:
            return 'WM_CLASS(STRING) = "google-chrome", "Google-chrome"\n'
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(runtime, "_env", fake_env)
    monkeypatch.setattr(runtime, "_which", fake_which)
    monkeypatch.setattr(runtime, "_run_command", fake_run)

    result = asyncio.run(runtime.list_windows())

    assert result.observation is not None
    assert result.observation.focused_window == "Calculator"
    assert [window.app_name for window in result.observation.windows] == [
        "Gnome calculator",
        "Google chrome",
    ]


def test_linux_runtime_launch_environment_prefers_x11_compatible_backends(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)

    env = runtime._build_launch_environment()

    assert env["GDK_BACKEND"] == "x11"
    assert env["QT_QPA_PLATFORM"] == "xcb"
