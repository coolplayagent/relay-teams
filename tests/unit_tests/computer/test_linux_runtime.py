# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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


def test_linux_runtime_focus_window_activates_matched_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    matched_window = ComputerWindow(
        window_id="0x00000003",
        app_name="notepad",
        title="Untitled - Notepad",
        focused=True,
    )
    activated_window_ids: list[str] = []
    observation = ComputerObservation(
        text="Linux desktop runtime snapshot.",
        windows=(matched_window,),
        focused_window="Untitled - Notepad",
    )

    monkeypatch.setattr(runtime, "_find_window", lambda query: matched_window)
    monkeypatch.setattr(
        runtime,
        "_activate_window",
        lambda window_id: activated_window_ids.append(window_id),
    )
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: None)
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.focus_window(window_title="Notepad"))

    assert activated_window_ids == ["0x00000003"]
    assert result.observation is not None
    assert result.observation.focused_window == "Untitled - Notepad"
    assert result.action.target.window_title == "Untitled - Notepad"
    assert result.data["runtime_mode"] == "linux"


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


def test_linux_runtime_click_runs_xdotool_mousemove_and_click(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    commands: list[list[str]] = []
    observation = ComputerObservation(text="Linux desktop runtime snapshot.")

    monkeypatch.setattr(
        runtime, "_run_input_command", lambda command: commands.append(command)
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.click_at(x=125, y=250))

    assert commands == [["xdotool", "mousemove", "--sync", "125", "250", "click", "1"]]
    assert result.action.target.x == 125
    assert result.action.target.y == 250


def test_linux_runtime_double_click_runs_xdotool_repeat_click(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    commands: list[list[str]] = []
    observation = ComputerObservation(text="Linux desktop runtime snapshot.")

    monkeypatch.setattr(
        runtime, "_run_input_command", lambda command: commands.append(command)
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.double_click_at(x=125, y=250))

    assert commands == [
        [
            "xdotool",
            "mousemove",
            "--sync",
            "125",
            "250",
            "click",
            "--repeat",
            "2",
            "1",
        ]
    ]
    assert result.action.target.x == 125
    assert result.action.target.y == 250


def test_linux_runtime_drag_runs_xdotool_drag_sequence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    commands: list[list[str]] = []
    observation = ComputerObservation(text="Linux desktop runtime snapshot.")

    monkeypatch.setattr(
        runtime, "_run_input_command", lambda command: commands.append(command)
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(
        runtime.drag_between(start_x=10, start_y=20, end_x=400, end_y=500)
    )

    assert commands == [
        [
            "xdotool",
            "mousemove",
            "--sync",
            "10",
            "20",
            "mousedown",
            "1",
            "mousemove",
            "--sync",
            "400",
            "500",
            "mouseup",
            "1",
        ]
    ]
    assert result.action.target.x == 10
    assert result.action.target.y == 20
    assert result.action.target.end_x == 400
    assert result.action.target.end_y == 500


def test_linux_runtime_type_text_runs_xdotool_type_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    commands: list[list[str]] = []
    observation = ComputerObservation(text="Linux desktop runtime snapshot.")

    monkeypatch.setattr(
        runtime, "_run_input_command", lambda command: commands.append(command)
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.type_text(text="hello from linux"))

    assert commands == [["xdotool", "type", "--delay", "1", "--", "hello from linux"]]
    assert result.action.target.text == "hello from linux"
    assert result.data["runtime_mode"] == "linux"


def test_linux_runtime_type_text_requires_non_blank_text(
    tmp_path: Path,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    with pytest.raises(ValueError, match="text is required"):
        asyncio.run(runtime.type_text(text="   "))


def test_linux_runtime_scroll_view_uses_button_direction_and_repeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    commands: list[list[str]] = []
    observation = ComputerObservation(text="Linux desktop runtime snapshot.")

    monkeypatch.setattr(
        runtime, "_run_input_command", lambda command: commands.append(command)
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.scroll_view(amount=-3))

    assert commands == [["xdotool", "click", "--repeat", "3", "5"]]
    assert result.action.target.amount == -3


def test_linux_runtime_hotkey_runs_normalized_xdotool_key_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    commands: list[list[str]] = []
    observation = ComputerObservation(text="Linux desktop runtime snapshot.")

    monkeypatch.setattr(
        runtime, "_run_input_command", lambda command: commands.append(command)
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.hotkey(shortcut="Control+A"))

    assert commands == [["xdotool", "key", "ctrl+A"]]
    assert result.action.target.shortcut == "Control+A"
    assert result.data["runtime_mode"] == "linux"


def test_linux_runtime_hotkey_requires_non_blank_shortcut(
    tmp_path: Path,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    with pytest.raises(ValueError, match="shortcut is required"):
        asyncio.run(runtime.hotkey(shortcut="   "))


def test_linux_runtime_scroll_view_requires_non_zero_amount(
    tmp_path: Path,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)

    with pytest.raises(ValueError, match="amount must not be zero"):
        asyncio.run(runtime.scroll_view(amount=0))


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


def test_linux_runtime_wait_for_window_returns_observation_for_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = LinuxDesktopRuntime(project_root=tmp_path)
    matched_window = ComputerWindow(
        window_id="0x00000003",
        app_name="notepad",
        title="Untitled - Notepad",
        focused=True,
    )
    observation = ComputerObservation(
        text="Linux desktop runtime snapshot.",
        windows=(matched_window,),
        focused_window="Untitled - Notepad",
    )

    monkeypatch.setattr(
        runtime,
        "_wait_for_window_match",
        lambda **kwargs: matched_window,
    )
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.wait_for_window(window_title="Notepad"))

    assert result.observation is not None
    assert result.observation.focused_window == "Untitled - Notepad"
    assert result.action.target.window_title == "Untitled - Notepad"
    assert result.data["runtime_mode"] == "linux"


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
