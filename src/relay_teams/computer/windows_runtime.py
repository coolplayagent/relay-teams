# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ctypes
import json
import shlex
import shutil
import subprocess
import tempfile
import time
from ctypes import wintypes
from pathlib import Path, PureWindowsPath

from relay_teams.computer.models import (
    ComputerActionDescriptor,
    ComputerActionResult,
    ComputerActionRisk,
    ComputerActionTarget,
    ComputerActionType,
    ComputerObservation,
    ComputerPermissionScope,
    ComputerRuntimeKind,
    ComputerWindow,
    ExecutionSurface,
)
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

_WAIT_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.25
_COMMAND_TIMEOUT_SECONDS = 10.0
_POINTER_SETTLE_SECONDS = 0.05
_WINDOW_ACTIVATE_DELAY_SECONDS = 0.2

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_WHEEL = 0x0800
_WHEEL_DELTA = 120

_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_MENU = 0x12
_VK_RETURN = 0x0D
_VK_ESCAPE = 0x1B
_VK_TAB = 0x09
_VK_SPACE = 0x20
_VK_LEFT = 0x25
_VK_UP = 0x26
_VK_RIGHT = 0x27
_VK_DOWN = 0x28
_VK_DELETE = 0x2E
_VK_HOME = 0x24
_VK_END = 0x23
_VK_PRIOR = 0x21
_VK_NEXT = 0x22
_VK_INSERT = 0x2D
_VK_BACK = 0x08
_VK_LWIN = 0x5B
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77

_PROCESS_CREATION_FLAGS = getattr(
    subprocess,
    "CREATE_NEW_PROCESS_GROUP",
    0,
) | getattr(
    subprocess,
    "DETACHED_PROCESS",
    0,
)

_MODIFIER_VKEYS: dict[str, int] = {
    "alt": _VK_MENU,
    "cmd": _VK_LWIN,
    "command": _VK_LWIN,
    "control": _VK_CONTROL,
    "ctrl": _VK_CONTROL,
    "meta": _VK_LWIN,
    "shift": _VK_SHIFT,
    "super": _VK_LWIN,
    "win": _VK_LWIN,
    "windows": _VK_LWIN,
}

_NAMED_VKEYS: dict[str, int] = {
    "backspace": _VK_BACK,
    "delete": _VK_DELETE,
    "del": _VK_DELETE,
    "down": _VK_DOWN,
    "end": _VK_END,
    "enter": _VK_RETURN,
    "esc": _VK_ESCAPE,
    "escape": _VK_ESCAPE,
    "home": _VK_HOME,
    "insert": _VK_INSERT,
    "ins": _VK_INSERT,
    "left": _VK_LEFT,
    "minus": 0xBD,
    "pagedown": _VK_NEXT,
    "pageup": _VK_PRIOR,
    "period": 0xBE,
    "plus": 0xBB,
    "quote": 0xDE,
    "right": _VK_RIGHT,
    "semicolon": 0xBA,
    "slash": 0xBF,
    "space": _VK_SPACE,
    "tab": _VK_TAB,
    "up": _VK_UP,
}

_ULONG_PTR = ctypes.c_size_t


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyboardInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("payload", _InputUnion),
    ]


class WindowsDesktopRuntime:
    def __init__(self, *, project_root: Path) -> None:
        self._project_root = Path(project_root)

    async def capture_screen(self) -> ComputerActionResult:
        return await asyncio.to_thread(self._capture_screen_sync)

    async def list_windows(self) -> ComputerActionResult:
        return await asyncio.to_thread(self._list_windows_sync)

    async def focus_window(self, *, window_title: str) -> ComputerActionResult:
        return await asyncio.to_thread(self._focus_window_sync, window_title)

    async def click_at(self, *, x: int, y: int) -> ComputerActionResult:
        return await asyncio.to_thread(self._click_at_sync, x, y)

    async def double_click_at(self, *, x: int, y: int) -> ComputerActionResult:
        return await asyncio.to_thread(self._double_click_at_sync, x, y)

    async def drag_between(
        self,
        *,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> ComputerActionResult:
        return await asyncio.to_thread(
            self._drag_between_sync,
            start_x,
            start_y,
            end_x,
            end_y,
        )

    async def type_text(self, *, text: str) -> ComputerActionResult:
        return await asyncio.to_thread(self._type_text_sync, text)

    async def scroll_view(self, *, amount: int) -> ComputerActionResult:
        return await asyncio.to_thread(self._scroll_view_sync, amount)

    async def hotkey(self, *, shortcut: str) -> ComputerActionResult:
        return await asyncio.to_thread(self._hotkey_sync, shortcut)

    async def launch_app(self, *, app_name: str) -> ComputerActionResult:
        return await asyncio.to_thread(self._launch_app_sync, app_name)

    async def wait_for_window(self, *, window_title: str) -> ComputerActionResult:
        return await asyncio.to_thread(self._wait_for_window_sync, window_title)

    def _capture_screen_sync(self) -> ComputerActionResult:
        observation = self._build_observation(
            require_windows=False,
            require_input=False,
            require_screenshot=True,
        )
        return ComputerActionResult(
            action=self._descriptor(
                action=ComputerActionType.CAPTURE_SCREEN,
                permission_scope=ComputerPermissionScope.OBSERVE,
                risk_level=ComputerActionRisk.SAFE,
            ),
            message="Captured the current Windows desktop screenshot.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "windows",
                "virtual_screen_origin_x": observation.screenshot_origin_x,
                "virtual_screen_origin_y": observation.screenshot_origin_y,
            },
        )

    def _list_windows_sync(self) -> ComputerActionResult:
        observation = self._build_observation(
            require_windows=True,
            require_input=False,
            require_screenshot=False,
        )
        return ComputerActionResult(
            action=self._descriptor(
                action=ComputerActionType.LIST_WINDOWS,
                permission_scope=ComputerPermissionScope.OBSERVE,
                risk_level=ComputerActionRisk.SAFE,
            ),
            message="Listed visible windows in the Windows desktop runtime.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "windows",
            },
        )

    def _focus_window_sync(self, window_title: str) -> ComputerActionResult:
        matched_window = self._find_window(window_title)
        self._activate_window(matched_window.title)
        self._sleep(_WINDOW_ACTIVATE_DELAY_SECONDS)
        observation = self._build_observation(
            require_windows=True,
            require_input=True,
            require_screenshot=False,
        )
        return ComputerActionResult(
            action=self._descriptor(
                action=ComputerActionType.FOCUS_WINDOW,
                permission_scope=ComputerPermissionScope.WINDOW_MANAGEMENT,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(window_title=matched_window.title),
            ),
            message=f"Focused Windows desktop window: {matched_window.title}.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "windows",
            },
        )

    def _click_at_sync(self, x: int, y: int) -> ComputerActionResult:
        translated_x, translated_y = self._translate_screenshot_coordinates(x, y)
        self._set_cursor_position(translated_x, translated_y)
        self._mouse_click(repeat=1)
        self._sleep(_POINTER_SETTLE_SECONDS)
        return self._action_result(
            action=ComputerActionType.CLICK,
            permission_scope=ComputerPermissionScope.POINTER,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(x=x, y=y),
            message=f"Clicked Windows desktop coordinates ({x}, {y}).",
        )

    def _double_click_at_sync(self, x: int, y: int) -> ComputerActionResult:
        translated_x, translated_y = self._translate_screenshot_coordinates(x, y)
        self._set_cursor_position(translated_x, translated_y)
        self._mouse_click(repeat=2)
        self._sleep(_POINTER_SETTLE_SECONDS)
        return self._action_result(
            action=ComputerActionType.DOUBLE_CLICK,
            permission_scope=ComputerPermissionScope.POINTER,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(x=x, y=y),
            message=f"Double-clicked Windows desktop coordinates ({x}, {y}).",
        )

    def _drag_between_sync(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> ComputerActionResult:
        translated_start_x, translated_start_y = self._translate_screenshot_coordinates(
            start_x,
            start_y,
        )
        translated_end_x, translated_end_y = self._translate_screenshot_coordinates(
            end_x,
            end_y,
        )
        self._set_cursor_position(translated_start_x, translated_start_y)
        self._send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        self._sleep(_POINTER_SETTLE_SECONDS)
        self._set_cursor_position(translated_end_x, translated_end_y)
        self._sleep(_POINTER_SETTLE_SECONDS)
        self._send_mouse_event(_MOUSEEVENTF_LEFTUP)
        self._sleep(_POINTER_SETTLE_SECONDS)
        return self._action_result(
            action=ComputerActionType.DRAG,
            permission_scope=ComputerPermissionScope.DESTRUCTIVE,
            risk_level=ComputerActionRisk.DESTRUCTIVE,
            target=ComputerActionTarget(
                x=start_x,
                y=start_y,
                end_x=end_x,
                end_y=end_y,
            ),
            message=(
                "Dragged between Windows desktop coordinates "
                f"({start_x}, {start_y}) -> ({end_x}, {end_y})."
            ),
        )

    def _type_text_sync(self, text: str) -> ComputerActionResult:
        if not text.strip():
            raise ValueError("text is required")
        self._send_unicode_text(text)
        self._sleep(_POINTER_SETTLE_SECONDS)
        return self._action_result(
            action=ComputerActionType.TYPE_TEXT,
            permission_scope=ComputerPermissionScope.INPUT_TEXT,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(text=text),
            message=f"Typed text into the Windows desktop: {text}",
        )

    def _scroll_view_sync(self, amount: int) -> ComputerActionResult:
        if amount == 0:
            raise ValueError("amount must not be zero")
        self._send_mouse_event(
            _MOUSEEVENTF_WHEEL,
            data=amount * _WHEEL_DELTA,
        )
        self._sleep(_POINTER_SETTLE_SECONDS)
        return self._action_result(
            action=ComputerActionType.SCROLL,
            permission_scope=ComputerPermissionScope.POINTER,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(amount=amount),
            message=f"Scrolled the Windows desktop by {amount}.",
        )

    def _hotkey_sync(self, shortcut: str) -> ComputerActionResult:
        modifier_keys, normal_keys = self._normalize_shortcut(shortcut)
        self._send_hotkey_inputs(modifier_keys, normal_keys)
        self._sleep(_POINTER_SETTLE_SECONDS)
        return self._action_result(
            action=ComputerActionType.HOTKEY,
            permission_scope=ComputerPermissionScope.KEYBOARD_SHORTCUT,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(shortcut=shortcut),
            message=f"Sent Windows desktop shortcut: {shortcut}",
        )

    def _launch_app_sync(self, app_name: str) -> ComputerActionResult:
        before_windows = self._list_windows_snapshot(
            require_windows=False,
            require_input=False,
        )
        command = self._resolve_launch_command(app_name)
        self._spawn_process(command)
        window_queries = self._build_launch_window_queries(
            app_name=app_name,
            command=command,
        )
        matched_window = self._wait_for_window_match(
            queries=window_queries,
            before_windows=before_windows,
        )
        if matched_window is None:
            raise RuntimeError(f"App window did not appear within timeout: {app_name}")
        self._activate_window(matched_window.title)
        self._sleep(_WINDOW_ACTIVATE_DELAY_SECONDS)
        observation = self._build_observation(
            require_windows=True,
            require_input=True,
            require_screenshot=False,
        )
        return ComputerActionResult(
            action=self._descriptor(
                action=ComputerActionType.LAUNCH_APP,
                permission_scope=ComputerPermissionScope.APP_LAUNCH,
                risk_level=ComputerActionRisk.DESTRUCTIVE,
                target=ComputerActionTarget(
                    app_name=app_name,
                    window_title=matched_window.title,
                ),
            ),
            message=f"Launched Windows desktop app: {app_name}.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "windows",
                "launched_command": " ".join(command),
            },
        )

    def _wait_for_window_sync(self, window_title: str) -> ComputerActionResult:
        normalized_title = window_title.strip()
        if not normalized_title:
            raise ValueError("window_title is required")
        matched_window = self._wait_for_window_match(queries=(normalized_title,))
        if matched_window is None:
            raise RuntimeError(f"Window not found within timeout: {window_title}")
        observation = self._build_observation(
            require_windows=True,
            require_input=True,
            require_screenshot=False,
        )
        return ComputerActionResult(
            action=self._descriptor(
                action=ComputerActionType.WAIT_FOR_WINDOW,
                permission_scope=ComputerPermissionScope.OBSERVE,
                risk_level=ComputerActionRisk.SAFE,
                target=ComputerActionTarget(window_title=matched_window.title),
            ),
            message=f"Observed Windows desktop window: {matched_window.title}.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "windows",
            },
        )

    def _action_result(
        self,
        *,
        action: ComputerActionType,
        permission_scope: ComputerPermissionScope,
        risk_level: ComputerActionRisk,
        target: ComputerActionTarget,
        message: str,
    ) -> ComputerActionResult:
        observation = self._build_observation(
            require_windows=True,
            require_input=True,
            require_screenshot=False,
        )
        return ComputerActionResult(
            action=self._descriptor(
                action=action,
                permission_scope=permission_scope,
                risk_level=risk_level,
                target=target,
            ),
            message=message,
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "windows",
            },
        )

    def _descriptor(
        self,
        *,
        action: ComputerActionType,
        permission_scope: ComputerPermissionScope,
        risk_level: ComputerActionRisk,
        target: ComputerActionTarget | None = None,
    ) -> ComputerActionDescriptor:
        return ComputerActionDescriptor(
            action=action,
            runtime_kind=ComputerRuntimeKind.BUILTIN_TOOL,
            execution_surface=ExecutionSurface.DESKTOP,
            permission_scope=permission_scope,
            risk_level=risk_level,
            source="tool",
            target=target or ComputerActionTarget(),
        )

    def _build_observation(
        self,
        *,
        require_windows: bool,
        require_input: bool,
        require_screenshot: bool,
    ) -> ComputerObservation:
        screenshot_bytes: bytes | None = None
        screenshot_name = ""
        screenshot_mime_type: str | None = None
        screenshot_origin_x: int | None = None
        screenshot_origin_y: int | None = None
        screenshot_width: int | None = None
        screenshot_height: int | None = None
        if require_screenshot:
            (
                screenshot_bytes,
                screenshot_name,
                screenshot_mime_type,
                screenshot_origin_x,
                screenshot_origin_y,
                screenshot_width,
                screenshot_height,
            ) = self._capture_screenshot_bytes(required=True)
        windows = self._list_windows_snapshot(
            require_windows=require_windows,
            require_input=require_input,
        )
        focused_window = next(
            (window.title for window in windows if window.focused),
            "",
        )
        return ComputerObservation(
            text="Windows desktop runtime snapshot.",
            windows=windows,
            focused_window=focused_window,
            screenshot_bytes=screenshot_bytes,
            screenshot_mime_type=screenshot_mime_type,
            screenshot_name=screenshot_name,
            screenshot_origin_x=screenshot_origin_x,
            screenshot_origin_y=screenshot_origin_y,
            screenshot_width=screenshot_width,
            screenshot_height=screenshot_height,
        )

    def _capture_screenshot_bytes(
        self,
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
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            screenshot_path = Path(handle.name)

        try:
            payload = self._run_powershell_json(
                self._build_capture_screenshot_script(screenshot_path)
            )
            if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
                origin_x = self._read_json_int(payload, "left")
                origin_y = self._read_json_int(payload, "top")
                width = self._read_json_int(payload, "width")
                height = self._read_json_int(payload, "height")
                return (
                    screenshot_path.read_bytes(),
                    screenshot_path.name,
                    "image/png",
                    origin_x,
                    origin_y,
                    width,
                    height,
                )
            if required:
                raise RuntimeError(
                    "Windows desktop command produced no screenshot file."
                )
            LOGGER.warning(
                "Skipping Windows desktop screenshot because no screenshot was "
                "produced."
            )
            return None, "", None, None, None, None, None
        except RuntimeError:
            if required:
                raise
            LOGGER.warning(
                "Skipping Windows desktop screenshot because screenshot capture "
                "failed.",
            )
            return None, "", None, None, None, None, None
        finally:
            screenshot_path.unlink(missing_ok=True)

    def _build_capture_screenshot_script(self, screenshot_path: Path) -> str:
        literal_path = self._powershell_literal(str(screenshot_path))
        return "\n".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "Add-Type -AssemblyName System.Drawing",
                "$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen",
                "if ($bounds.Width -le 0 -or $bounds.Height -le 0) {",
                '    throw "Windows desktop screenshot requires an active desktop session."',
                "}",
                "$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height",
                "$graphics = [System.Drawing.Graphics]::FromImage($bitmap)",
                "$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)",
                (
                    "$bitmap.Save("
                    f"{literal_path}, "
                    "[System.Drawing.Imaging.ImageFormat]::Png)"
                ),
                "$graphics.Dispose()",
                "$bitmap.Dispose()",
                (
                    "[pscustomobject]@{ "
                    "left = $bounds.Left; "
                    "top = $bounds.Top; "
                    "width = $bounds.Width; "
                    "height = $bounds.Height "
                    "} "
                    "| ConvertTo-Json -Compress"
                ),
            ]
        )

    def _list_windows_snapshot(
        self,
        *,
        require_windows: bool,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        try:
            payload = self._run_powershell_json(self._build_list_windows_script())
        except RuntimeError:
            if require_windows or require_input:
                raise
            LOGGER.warning(
                "Skipping Windows desktop window discovery because PowerShell "
                "window enumeration failed."
            )
            return ()

        raw_windows: list[object]
        if isinstance(payload, list):
            raw_windows = payload
        elif isinstance(payload, dict):
            raw_windows = [payload]
        else:
            raw_windows = []

        windows: list[ComputerWindow] = []
        for item in raw_windows:
            if not isinstance(item, dict):
                continue
            window_id = self._read_json_text(item, "window_id")
            title = self._read_json_text(item, "title")
            if not window_id or not title:
                continue
            app_name = self._read_json_text(item, "app_name") or title
            focused = self._read_json_bool(item, "focused")
            windows.append(
                ComputerWindow(
                    window_id=window_id,
                    app_name=app_name,
                    title=title,
                    focused=focused,
                )
            )
        return tuple(windows)

    def _build_list_windows_script(self) -> str:
        return "\n".join(
            [
                "Add-Type @'",
                "using System;",
                "using System.Runtime.InteropServices;",
                "public static class RelayTeamsWin32 {",
                '    [DllImport("user32.dll")]',
                "    public static extern IntPtr GetForegroundWindow();",
                "}",
                "'@",
                "$foreground = [int64][RelayTeamsWin32]::GetForegroundWindow()",
                (
                    "$windows = Get-Process | Where-Object { "
                    "$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle "
                    "} | Sort-Object Id | ForEach-Object {"
                ),
                "    [pscustomobject]@{",
                "        window_id = ('0x{0:x}' -f [int64]$_.MainWindowHandle)",
                "        app_name = $_.ProcessName",
                "        title = $_.MainWindowTitle",
                "        focused = ([int64]$_.MainWindowHandle -eq $foreground)",
                "    }",
                "}",
                "$windows | ConvertTo-Json -Depth 3 -Compress",
            ]
        )

    def _find_window(self, query: str) -> ComputerWindow:
        normalized = query.strip()
        if not normalized:
            raise ValueError("window_title is required")
        windows = self._list_windows_snapshot(require_windows=True, require_input=False)
        for window in windows:
            if self._window_matches(window, normalized):
                return window
        raise RuntimeError(f"Window not found: {query}")

    def _wait_for_window_match(
        self,
        *,
        queries: tuple[str, ...] = (),
        before_windows: tuple[ComputerWindow, ...] = (),
    ) -> ComputerWindow | None:
        normalized_queries = self._normalize_match_queries(*queries)
        before_ids = {window.window_id for window in before_windows}
        deadline = self._time_monotonic() + _WAIT_TIMEOUT_SECONDS
        while self._time_monotonic() < deadline:
            windows = self._list_windows_snapshot(
                require_windows=True,
                require_input=False,
            )
            for window in windows:
                is_new_window = window.window_id not in before_ids
                if is_new_window and (
                    not normalized_queries
                    or self._window_matches_any(window, normalized_queries)
                ):
                    return window
                if normalized_queries and self._window_matches_any(
                    window,
                    normalized_queries,
                ):
                    return window
            self._sleep(_POLL_INTERVAL_SECONDS)
        return None

    def _build_launch_window_queries(
        self,
        *,
        app_name: str,
        command: list[str],
    ) -> tuple[str, ...]:
        raw_query = app_name.strip()
        parsed_command = shlex.split(raw_query, posix=False)
        first_input_token = parsed_command[0] if parsed_command else raw_query
        command_token = self._launch_match_token(command)
        candidate_queries: list[str] = [raw_query]
        for token in (first_input_token, command_token):
            if token is None:
                continue
            normalized_token = token.strip().strip('"').strip("'")
            if not normalized_token:
                continue
            token_path = PureWindowsPath(normalized_token)
            candidate_queries.append(normalized_token)
            candidate_queries.append(token_path.name)
            if token_path.suffix:
                candidate_queries.append(token_path.stem)
        return self._normalize_match_queries(*candidate_queries)

    def _launch_match_token(self, command: list[str]) -> str | None:
        if not command:
            return None
        if (
            len(command) >= 5
            and command[0].casefold() == "cmd"
            and command[1].casefold() == "/c"
            and command[2].casefold() == "start"
        ):
            return command[4]
        return command[0]

    def _normalize_match_queries(self, *queries: str) -> tuple[str, ...]:
        normalized_queries: list[str] = []
        seen_queries: set[str] = set()
        for query in queries:
            normalized_query = query.strip()
            if not normalized_query:
                continue
            dedupe_key = normalized_query.casefold()
            if dedupe_key in seen_queries:
                continue
            normalized_queries.append(normalized_query)
            seen_queries.add(dedupe_key)
        return tuple(normalized_queries)

    def _window_matches_any(
        self,
        window: ComputerWindow,
        queries: tuple[str, ...],
    ) -> bool:
        return any(self._window_matches(window, query) for query in queries)

    def _window_matches(self, window: ComputerWindow, query: str) -> bool:
        normalized_query = query.casefold()
        return (
            normalized_query in window.title.casefold()
            or normalized_query in window.app_name.casefold()
        )

    def _activate_window(self, window_title: str) -> None:
        literal_title = self._powershell_literal(window_title)
        self._run_powershell(
            "\n".join(
                [
                    "$wshell = New-Object -ComObject WScript.Shell",
                    f"if (-not $wshell.AppActivate({literal_title})) {{",
                    f"    throw {self._powershell_literal(f'Window not found: {window_title}')}",
                    "}",
                ]
            )
        )

    def _resolve_launch_command(self, app_name: str) -> list[str]:
        normalized = app_name.strip()
        if not normalized:
            raise ValueError("app_name is required")

        lowered = normalized.casefold()
        if lowered in {"calc", "calculator"}:
            return ["calc.exe"]

        parsed_command = shlex.split(normalized, posix=False)
        if parsed_command:
            first_token = parsed_command[0]
            if Path(first_token).exists() or shutil.which(first_token) is not None:
                return parsed_command

        if Path(normalized).exists() or shutil.which(normalized) is not None:
            return [normalized]

        return ["cmd", "/c", "start", "", normalized]

    def _spawn_process(self, command: list[str]) -> None:
        LOGGER.info("Launching Windows desktop app", extra={"command": command})
        subprocess.Popen(
            command,
            cwd=self._project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_PROCESS_CREATION_FLAGS,
        )

    def _send_unicode_text(self, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        inputs: list[_Input] = []
        for character in normalized:
            if character == "\n":
                inputs.extend(self._keyboard_inputs_for_virtual_key(_VK_RETURN))
                continue
            if character == "\t":
                inputs.extend(self._keyboard_inputs_for_virtual_key(_VK_TAB))
                continue
            codepoint = ord(character)
            inputs.append(
                self._keyboard_input(
                    virtual_key=0,
                    scan_code=codepoint,
                    flags=_KEYEVENTF_UNICODE,
                )
            )
            inputs.append(
                self._keyboard_input(
                    virtual_key=0,
                    scan_code=codepoint,
                    flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
                )
            )
        self._send_inputs(inputs)

    def _send_hotkey_inputs(
        self,
        modifier_keys: tuple[int, ...],
        normal_keys: tuple[int, ...],
    ) -> None:
        inputs: list[_Input] = []
        for key in modifier_keys:
            inputs.append(self._keyboard_input(virtual_key=key))
        for key in normal_keys:
            inputs.append(self._keyboard_input(virtual_key=key))
        for key in reversed(normal_keys):
            inputs.append(
                self._keyboard_input(
                    virtual_key=key,
                    flags=_KEYEVENTF_KEYUP,
                )
            )
        for key in reversed(modifier_keys):
            inputs.append(
                self._keyboard_input(
                    virtual_key=key,
                    flags=_KEYEVENTF_KEYUP,
                )
            )
        self._send_inputs(inputs)

    def _normalize_shortcut(
        self, shortcut: str
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        normalized = shortcut.strip()
        if not normalized:
            raise ValueError("shortcut is required")

        modifier_keys: list[int] = []
        normal_keys: list[int] = []
        for part in normalized.split("+"):
            token = part.strip()
            if not token:
                continue
            lowered = token.casefold()
            modifier_key = _MODIFIER_VKEYS.get(lowered)
            if modifier_key is not None:
                modifier_keys.append(modifier_key)
                continue
            normal_keys.append(self._resolve_virtual_key(token))

        if not normal_keys:
            raise ValueError("shortcut must include a non-modifier key")
        return tuple(modifier_keys), tuple(normal_keys)

    def _resolve_virtual_key(self, token: str) -> int:
        lowered = token.casefold()
        named_key = _NAMED_VKEYS.get(lowered)
        if named_key is not None:
            return named_key

        if len(token) == 1:
            character = token.upper()
            if character.isalpha() or character.isdigit():
                return ord(character)

        if lowered.startswith("f") and lowered[1:].isdigit():
            function_number = int(lowered[1:])
            if 1 <= function_number <= 24:
                return 0x6F + function_number

        raise ValueError(f"Unsupported shortcut key: {token}")

    def _keyboard_inputs_for_virtual_key(self, virtual_key: int) -> list[_Input]:
        return [
            self._keyboard_input(virtual_key=virtual_key),
            self._keyboard_input(
                virtual_key=virtual_key,
                flags=_KEYEVENTF_KEYUP,
            ),
        ]

    def _translate_screenshot_coordinates(self, x: int, y: int) -> tuple[int, int]:
        origin_x, origin_y = self._get_virtual_screen_origin()
        return x + origin_x, y + origin_y

    def _get_virtual_screen_origin(self) -> tuple[int, int]:
        user32 = self._load_user32()
        user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        user32.GetSystemMetrics.restype = ctypes.c_int
        return (
            int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)),
        )

    def _keyboard_input(
        self,
        *,
        virtual_key: int,
        scan_code: int = 0,
        flags: int = 0,
    ) -> _Input:
        return _Input(
            type=_INPUT_KEYBOARD,
            ki=_KeyboardInput(
                wVk=virtual_key,
                wScan=scan_code,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            ),
        )

    def _send_inputs(self, inputs: list[_Input]) -> None:
        if not inputs:
            return
        user32 = self._load_user32()
        user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT
        array_type = _Input * len(inputs)
        sent = user32.SendInput(
            len(inputs),
            array_type(*inputs),
            ctypes.sizeof(_Input),
        )
        if sent != len(inputs):
            raise RuntimeError("Windows desktop input injection failed.")

    def _set_cursor_position(self, x: int, y: int) -> None:
        user32 = self._load_user32()
        user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        user32.SetCursorPos.restype = wintypes.BOOL
        if not user32.SetCursorPos(x, y):
            raise RuntimeError(
                f"Windows desktop pointer movement failed at ({x}, {y})."
            )

    def _mouse_click(self, *, repeat: int) -> None:
        for index in range(repeat):
            self._send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
            self._send_mouse_event(_MOUSEEVENTF_LEFTUP)
            if index + 1 < repeat:
                self._sleep(_POINTER_SETTLE_SECONDS)

    def _send_mouse_event(self, flags: int, *, data: int = 0) -> None:
        user32 = self._load_user32()
        user32.mouse_event.argtypes = (
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            _ULONG_PTR,
        )
        user32.mouse_event.restype = None
        user32.mouse_event(flags, 0, 0, data, 0)

    def _load_user32(self) -> ctypes.WinDLL:
        try:
            return ctypes.WinDLL("user32", use_last_error=True)
        except OSError as exc:
            raise RuntimeError(
                "Windows desktop runtime requires an active Windows host."
            ) from exc

    def _run_powershell_json(self, script: str) -> object:
        output = self._run_powershell(script).strip()
        if not output:
            return []
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Windows desktop PowerShell command returned invalid JSON."
            ) from exc

    def _run_powershell(self, script: str) -> str:
        executable = self._resolve_powershell_executable()
        wrapped_script = "\n".join(
            [
                "$OutputEncoding = [System.Text.Encoding]::UTF8",
                "[Console]::InputEncoding = [System.Text.Encoding]::UTF8",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
                script,
            ]
        )
        try:
            completed = subprocess.run(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    wrapped_script,
                ],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Windows desktop PowerShell command timed out after "
                f"{_COMMAND_TIMEOUT_SECONDS:.0f}s."
            ) from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            details = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(f"Windows desktop PowerShell command failed: {details}")
        return completed.stdout or ""

    def _resolve_powershell_executable(self) -> str:
        powershell = shutil.which("powershell")
        if powershell is not None:
            return powershell
        pwsh = shutil.which("pwsh")
        if pwsh is not None:
            return pwsh
        raise RuntimeError("Windows desktop runtime requires PowerShell.")

    def _powershell_literal(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _read_json_text(self, payload: dict[object, object], key: str) -> str:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) else ""

    def _read_json_bool(self, payload: dict[object, object], key: str) -> bool:
        value = payload.get(key)
        return value is True

    def _read_json_int(self, payload: object, key: str) -> int | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get(key)
        return value if isinstance(value, int) else None

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _time_monotonic(self) -> float:
        return time.monotonic()
