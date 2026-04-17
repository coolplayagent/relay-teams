# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import relay_teams.computer.windows_runtime as windows_runtime_module

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
    ) -> tuple[
        bytes | None,
        str,
        str | None,
        int | None,
        int | None,
        int | None,
        int | None,
    ]:
        assert required is True
        return (
            b"\x89PNG\r\n\x1a\nwindows",
            "desktop.png",
            "image/png",
            -1920,
            -40,
            1440,
            900,
        )

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
    assert result.observation.screenshot_origin_x == -1920
    assert result.observation.screenshot_origin_y == -40
    assert result.observation.screenshot_width == 1440
    assert result.observation.screenshot_height == 900
    assert result.data["runtime_mode"] == "windows"
    assert result.data["virtual_screen_origin_x"] == -1920
    assert result.data["virtual_screen_origin_y"] == -40


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
    ) -> tuple[
        bytes | None,
        str,
        str | None,
        int | None,
        int | None,
        int | None,
        int | None,
    ]:
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
    activated_window_ids: list[str] = []
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
        allow_existing_matches: bool = True,
    ) -> ComputerWindow:
        assert queries == ("Calculator", "calc.exe", "calc")
        assert before_windows == ()
        assert allow_existing_matches is False
        return matched_window

    def fake_activate_window(window_id: str) -> None:
        activated_window_ids.append(window_id)

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
    assert activated_window_ids == ["0x20"]
    assert result.observation is not None
    assert result.observation.focused_window == "Calculator"
    assert result.action.target.app_name == "Calculator"
    assert result.action.target.window_title == "Calculator"
    assert result.data["launched_command"] == "calc.exe"


def test_windows_runtime_resolves_calculator_candidates(tmp_path: Path) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)

    command = runtime._resolve_launch_command("Calculator")

    assert command == ["calc.exe"]


def test_windows_runtime_click_translates_virtual_screen_origin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    cursor_positions: list[tuple[int, int]] = []
    observation = ComputerObservation(text="Windows desktop runtime snapshot.")

    monkeypatch.setattr(runtime, "_get_virtual_screen_origin", lambda: (-1920, -40))
    monkeypatch.setattr(
        runtime,
        "_set_cursor_position",
        lambda x, y: cursor_positions.append((x, y)),
    )
    monkeypatch.setattr(runtime, "_mouse_click", lambda *, repeat: None)
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: None)
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(runtime.click_at(x=125, y=250))

    assert cursor_positions == [(-1795, 210)]
    assert result.action.target.x == 125
    assert result.action.target.y == 250


def test_windows_runtime_drag_translates_virtual_screen_origin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    cursor_positions: list[tuple[int, int]] = []
    mouse_events: list[int] = []
    observation = ComputerObservation(text="Windows desktop runtime snapshot.")

    monkeypatch.setattr(runtime, "_get_virtual_screen_origin", lambda: (-1600, -120))
    monkeypatch.setattr(
        runtime,
        "_set_cursor_position",
        lambda x, y: cursor_positions.append((x, y)),
    )
    monkeypatch.setattr(
        runtime,
        "_send_mouse_event",
        lambda flags, *, data=0: mouse_events.append(flags),
    )
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: None)
    monkeypatch.setattr(
        runtime,
        "_build_observation",
        lambda **kwargs: observation,
    )

    result = asyncio.run(
        runtime.drag_between(start_x=10, start_y=20, end_x=400, end_y=500)
    )

    assert cursor_positions == [(-1590, -100), (-1200, 380)]
    assert mouse_events == [0x0002, 0x0004]
    assert result.action.target.x == 10
    assert result.action.target.y == 20
    assert result.action.target.end_x == 400
    assert result.action.target.end_y == 500


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


def test_windows_runtime_build_launch_window_queries_handles_cmd_start_without_title(
    tmp_path: Path,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)

    queries = runtime._build_launch_window_queries(
        app_name="cmd /c start notepad.exe",
        command=["cmd", "/c", "start", "notepad.exe"],
    )

    assert queries == (
        "cmd /c start notepad.exe",
        "cmd",
        "notepad.exe",
        "notepad",
    )


def test_windows_runtime_build_list_windows_script_uses_hwnd_enumeration(
    tmp_path: Path,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)

    script = runtime._build_list_windows_script()

    assert "EnumWindows" in script
    assert "GetWindowThreadProcessId" in script
    assert "GetWindowTextLength" in script
    assert "MainWindowHandle" not in script


def test_windows_runtime_wait_for_window_match_ignores_existing_launch_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    existing_window = ComputerWindow(
        window_id="0x10",
        app_name="notepad",
        title="notes.txt - Notepad",
        focused=True,
    )
    times = iter((0.0, 0.0, 11.0))

    monkeypatch.setattr(
        runtime,
        "_list_windows_snapshot",
        lambda **kwargs: (existing_window,),
    )
    monkeypatch.setattr(runtime, "_time_monotonic", lambda: next(times))
    monkeypatch.setattr(runtime, "_sleep", lambda seconds: None)

    matched_window = runtime._wait_for_window_match(
        queries=("notepad.exe", "notepad"),
        before_windows=(existing_window,),
        allow_existing_matches=False,
    )

    assert matched_window is None


def test_windows_runtime_send_unicode_text_uses_utf16_surrogate_pairs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)
    captured_inputs: list[tuple[int, int]] = []

    def fake_send_inputs(
        inputs: list[windows_runtime_module._Input],
    ) -> None:
        captured_inputs.extend(
            (int(item.ki.wScan), int(item.ki.dwFlags)) for item in inputs
        )

    monkeypatch.setattr(runtime, "_send_inputs", fake_send_inputs)

    runtime._send_unicode_text("🙂")

    assert captured_inputs == [
        (0xD83D, 0x0004),
        (0xD83D, 0x0006),
        (0xDE42, 0x0004),
        (0xDE42, 0x0006),
    ]


def test_windows_runtime_wait_for_window_requires_title(
    tmp_path: Path,
) -> None:
    runtime = WindowsDesktopRuntime(project_root=tmp_path)

    with pytest.raises(ValueError, match="window_title is required"):
        asyncio.run(runtime.wait_for_window(window_title="   "))
