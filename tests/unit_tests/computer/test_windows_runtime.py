# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

from relay_teams.computer import (
    ComputerObservation,
    ComputerWindow,
    WindowsDesktopRuntime,
)


def test_windows_runtime_capture_screen_reads_generated_png(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    focused_window = ComputerWindow(
        window_id="0x10",
        app_name="Calculator",
        title="Calculator",
        focused=True,
    )

    def fake_capture_screenshot_bytes(
        *,
        required: bool,
    ) -> tuple[bytes | None, str, str | None, int | None, int | None]:
        assert required is True
        return (b"\x89PNG\r\n\x1a\nwindows", "desktop.png", "image/png", 1440, 900)

    def fake_list_windows_snapshot(
        *,
        require_windows: bool,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        _ = (require_windows, require_input)
        return (focused_window,)

    monkeypatch.setattr(
        runtime,
        "_capture_screenshot_bytes",
        fake_capture_screenshot_bytes,
    )
    monkeypatch.setattr(runtime, "_list_windows_snapshot", fake_list_windows_snapshot)

    result = asyncio.run(runtime.capture_screen())

    assert result.observation is not None
    assert result.observation.screenshot_bytes == b"\x89PNG\r\n\x1a\nwindows"
    assert result.observation.screenshot_mime_type == "image/png"
    assert result.observation.screenshot_width == 1440
    assert result.observation.screenshot_height == 900
    assert result.data["runtime_mode"] == "windows"


def test_windows_runtime_list_windows_marks_active_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    windows = (
        ComputerWindow(
            window_id="0x10",
            app_name="Calculator",
            title="Calculator",
            focused=True,
        ),
        ComputerWindow(
            window_id="0x11",
            app_name="Code",
            title="relay-teams - Visual Studio Code",
            focused=False,
        ),
    )

    def fake_list_windows_snapshot(
        *,
        require_windows: bool,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        assert require_windows is True
        assert require_input is False
        return windows

    def fail_capture_screenshot_bytes(
        *,
        required: bool,
    ) -> tuple[bytes | None, str, str | None, int | None, int | None]:
        raise AssertionError(f"unexpected screenshot capture: {required}")

    monkeypatch.setattr(
        runtime,
        "_capture_screenshot_bytes",
        fail_capture_screenshot_bytes,
    )
    monkeypatch.setattr(runtime, "_list_windows_snapshot", fake_list_windows_snapshot)

    result = asyncio.run(runtime.list_windows())

    assert result.observation is not None
    assert result.observation.focused_window == "Calculator"
    assert [window.title for window in result.observation.windows] == [
        "Calculator",
        "relay-teams - Visual Studio Code",
    ]


def test_windows_runtime_launch_app_records_real_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    spawned_commands: list[list[str]] = []
    activated_titles: list[str] = []
    matched_window = ComputerWindow(
        window_id="0x20",
        app_name="Calculator",
        title="Calculator",
        focused=True,
    )
    observation = ComputerObservation(
        text="Windows desktop runtime snapshot.",
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
        return ["calc.exe"]

    def fake_spawn_process(command: list[str]) -> None:
        spawned_commands.append(command)

    def fake_wait_for_window_match(
        *,
        queries: tuple[str, ...],
        before_windows: tuple[ComputerWindow, ...] = (),
    ) -> ComputerWindow:
        assert queries == ("Calculator", "calc.exe", "calc")
        assert before_windows == ()
        return matched_window

    def fake_activate_window(window_title: str) -> None:
        activated_titles.append(window_title)

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
    monkeypatch.setattr(runtime, "_activate_window", fake_activate_window)
    monkeypatch.setattr(runtime, "_build_observation", fake_build_observation)
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: None)

    result = asyncio.run(runtime.launch_app(app_name="Calculator"))

    assert spawned_commands == [["calc.exe"]]
    assert activated_titles == ["Calculator"]
    assert result.observation is not None
    assert result.observation.focused_window == "Calculator"
    assert result.action.target.app_name == "Calculator"
    assert result.action.target.window_title == "Calculator"
    assert result.data["launched_command"] == "calc.exe"


def test_windows_runtime_resolves_calculator_candidates(tmp_path: Path) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)

    command = runtime._resolve_launch_command("Calculator")

    assert command == ["calc.exe"]


def test_windows_runtime_build_launch_window_queries_normalizes_path_command(
    tmp_path: Path,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)

    queries = runtime._build_launch_window_queries(
        app_name='"C:\\Program Files\\App\\app.exe" --flag',
        command=["C:\\Program Files\\App\\app.exe", "--flag"],
    )

    assert queries == (
        '"C:\\Program Files\\App\\app.exe" --flag',
        "C:\\Program Files\\App\\app.exe",
        "app.exe",
        "app",
    )
