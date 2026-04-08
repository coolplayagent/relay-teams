# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

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

_SCREENSHOT_COMMAND_NAMES: tuple[str, ...] = (
    "gnome-screenshot",
    "grim",
    "scrot",
    "import",
    "flameshot",
)
_WINDOW_DISCOVERY_COMMAND_NAMES: tuple[str, ...] = ("wmctrl", "xdotool")
_WAIT_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.25
_COMMAND_TIMEOUT_SECONDS = 10.0


class LinuxDesktopRuntime:
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
            message="Captured the current Linux desktop screenshot.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "linux",
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
            message="Listed visible windows in the Linux desktop runtime.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "linux",
            },
        )

    def _focus_window_sync(self, window_title: str) -> ComputerActionResult:
        matched_window = self._find_window(window_title)
        self._activate_window(matched_window.window_id)
        self._sleep(0.2)
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
            message=f"Focused Linux desktop window: {matched_window.title}.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "linux",
            },
        )

    def _click_at_sync(self, x: int, y: int) -> ComputerActionResult:
        self._run_input_command(
            ["xdotool", "mousemove", "--sync", str(x), str(y), "click", "1"]
        )
        return self._action_result(
            action=ComputerActionType.CLICK,
            permission_scope=ComputerPermissionScope.POINTER,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(x=x, y=y),
            message=f"Clicked Linux desktop coordinates ({x}, {y}).",
        )

    def _double_click_at_sync(self, x: int, y: int) -> ComputerActionResult:
        self._run_input_command(
            [
                "xdotool",
                "mousemove",
                "--sync",
                str(x),
                str(y),
                "click",
                "--repeat",
                "2",
                "1",
            ]
        )
        return self._action_result(
            action=ComputerActionType.DOUBLE_CLICK,
            permission_scope=ComputerPermissionScope.POINTER,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(x=x, y=y),
            message=f"Double-clicked Linux desktop coordinates ({x}, {y}).",
        )

    def _drag_between_sync(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> ComputerActionResult:
        self._run_input_command(
            [
                "xdotool",
                "mousemove",
                "--sync",
                str(start_x),
                str(start_y),
                "mousedown",
                "1",
                "mousemove",
                "--sync",
                str(end_x),
                str(end_y),
                "mouseup",
                "1",
            ]
        )
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
                "Dragged between Linux desktop coordinates "
                f"({start_x}, {start_y}) -> ({end_x}, {end_y})."
            ),
        )

    def _type_text_sync(self, text: str) -> ComputerActionResult:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("text is required")
        self._run_input_command(["xdotool", "type", "--delay", "1", "--", text])
        return self._action_result(
            action=ComputerActionType.TYPE_TEXT,
            permission_scope=ComputerPermissionScope.INPUT_TEXT,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(text=text),
            message=f"Typed text into the Linux desktop: {text}",
        )

    def _scroll_view_sync(self, amount: int) -> ComputerActionResult:
        button = "4" if amount > 0 else "5"
        repeat = abs(amount)
        if repeat == 0:
            raise ValueError("amount must not be zero")
        self._run_input_command(["xdotool", "click", "--repeat", str(repeat), button])
        return self._action_result(
            action=ComputerActionType.SCROLL,
            permission_scope=ComputerPermissionScope.POINTER,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(amount=amount),
            message=f"Scrolled the Linux desktop by {amount}.",
        )

    def _hotkey_sync(self, shortcut: str) -> ComputerActionResult:
        normalized_shortcut = self._normalize_shortcut(shortcut)
        self._run_input_command(["xdotool", "key", normalized_shortcut])
        return self._action_result(
            action=ComputerActionType.HOTKEY,
            permission_scope=ComputerPermissionScope.KEYBOARD_SHORTCUT,
            risk_level=ComputerActionRisk.GUARDED,
            target=ComputerActionTarget(shortcut=shortcut),
            message=f"Sent Linux desktop shortcut: {shortcut}",
        )

    def _launch_app_sync(self, app_name: str) -> ComputerActionResult:
        before_windows = self._list_windows_snapshot(
            require_windows=False,
            require_input=False,
        )
        command = self._resolve_launch_command(app_name)
        self._spawn_process(command)
        matched_window = self._wait_for_window_match(
            query=app_name,
            before_windows=before_windows,
        )
        if matched_window is None:
            raise RuntimeError(f"App window did not appear within timeout: {app_name}")
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
            message=f"Launched Linux desktop app: {app_name}.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "linux",
                "launched_command": " ".join(command),
            },
        )

    def _wait_for_window_sync(self, window_title: str) -> ComputerActionResult:
        matched_window = self._wait_for_window_match(query=window_title)
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
            message=f"Observed Linux desktop window: {matched_window.title}.",
            observation=observation,
            data={
                "window_count": len(observation.windows),
                "runtime_mode": "linux",
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
                "runtime_mode": "linux",
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
        screenshot_bytes, screenshot_name, screenshot_mime_type = (
            self._capture_screenshot_bytes(required=require_screenshot)
        )
        windows = self._list_windows_snapshot(
            require_windows=require_windows,
            require_input=require_input,
        )
        focused_window = next(
            (window.title for window in windows if window.focused), ""
        )
        return ComputerObservation(
            text="Linux desktop runtime snapshot.",
            windows=windows,
            focused_window=focused_window,
            screenshot_bytes=screenshot_bytes,
            screenshot_mime_type=screenshot_mime_type,
            screenshot_name=screenshot_name,
        )

    def _capture_screenshot_bytes(
        self,
        *,
        required: bool,
    ) -> tuple[bytes | None, str, str | None]:
        self._require_display()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            screenshot_path = Path(handle.name)

        try:
            commands = list(self._iter_screenshot_commands(screenshot_path))
            if not commands:
                if required:
                    raise RuntimeError(
                        "Linux desktop screenshots require one of: "
                        f"{', '.join(_SCREENSHOT_COMMAND_NAMES)}."
                    )
                LOGGER.warning(
                    "Skipping Linux desktop screenshot because no screenshot tool is "
                    "installed."
                )
                return None, "", None

            failures: list[str] = []
            for command in commands:
                screenshot_path.unlink(missing_ok=True)
                try:
                    self._run_command(command)
                except RuntimeError as exc:
                    failures.append(str(exc))
                    continue
                if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
                    return (
                        screenshot_path.read_bytes(),
                        screenshot_path.name,
                        "image/png",
                    )
                failures.append(
                    f"Linux desktop command produced no screenshot file: {' '.join(command)}"
                )
            if required:
                raise RuntimeError("; ".join(failures))
            LOGGER.warning(
                "Skipping Linux desktop screenshot after command failures: %s",
                "; ".join(failures),
            )
            return None, "", None
        finally:
            screenshot_path.unlink(missing_ok=True)

    def _iter_screenshot_commands(
        self,
        screenshot_path: Path,
    ) -> tuple[list[str], ...]:
        commands: list[list[str]] = []
        if self._which("gnome-screenshot") is not None:
            commands.append(["gnome-screenshot", "-f", str(screenshot_path)])
        if self._which("grim") is not None:
            commands.append(["grim", str(screenshot_path)])
        if self._which("scrot") is not None:
            commands.append(["scrot", str(screenshot_path)])
        if self._which("import") is not None:
            commands.append(["import", "-window", "root", str(screenshot_path)])
        if self._which("flameshot") is not None:
            commands.append(["flameshot", "full", "-p", str(screenshot_path)])
        return tuple(commands)

    def _list_windows_snapshot(
        self,
        *,
        require_windows: bool,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        self._require_display()
        if self._which("wmctrl") is not None:
            return self._list_windows_with_wmctrl(require_input=require_input)
        if self._which("xdotool") is not None:
            return self._list_windows_with_xdotool(require_input=require_input)
        if require_windows:
            raise RuntimeError(
                "Linux desktop window discovery requires wmctrl or xdotool."
            )
        LOGGER.warning(
            "Skipping Linux desktop window discovery because none of %s are installed.",
            ", ".join(_WINDOW_DISCOVERY_COMMAND_NAMES),
        )
        return ()

    def _list_windows_with_wmctrl(
        self,
        *,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        active_window_id = self._get_active_window_id(required=require_input)
        output = self._run_command(["wmctrl", "-lx"])
        windows: list[ComputerWindow] = []
        for line in output.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            window_id_text, _desktop, _host, wm_class, title = parts
            focused = (
                active_window_id is not None
                and self._normalize_window_id(window_id_text) == active_window_id
            )
            windows.append(
                ComputerWindow(
                    window_id=window_id_text,
                    app_name=self._derive_app_name(wm_class=wm_class, title=title),
                    title=title.strip() or wm_class,
                    focused=focused,
                )
            )
        return tuple(windows)

    def _list_windows_with_xdotool(
        self,
        *,
        require_input: bool,
    ) -> tuple[ComputerWindow, ...]:
        active_window_id = self._get_active_window_id(required=require_input)
        output = self._run_command(
            ["xdotool", "search", "--onlyvisible", "--name", "."]
        )
        windows: list[ComputerWindow] = []
        seen_ids: set[int] = set()
        for line in output.splitlines():
            window_id_text = line.strip()
            if not window_id_text:
                continue
            try:
                normalized_id = self._normalize_window_id(window_id_text)
            except ValueError:
                continue
            if normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            title = self._safe_run_command(["xdotool", "getwindowname", window_id_text])
            if title is None:
                continue
            wm_class = self._read_window_class(window_id_text)
            focused = active_window_id is not None and normalized_id == active_window_id
            windows.append(
                ComputerWindow(
                    window_id=window_id_text,
                    app_name=self._derive_app_name(wm_class=wm_class, title=title),
                    title=title.strip() or wm_class,
                    focused=focused,
                )
            )
        return tuple(windows)

    def _read_window_class(self, window_id_text: str) -> str:
        xprop_path = self._which("xprop")
        if xprop_path is None:
            return ""
        output = self._safe_run_command(["xprop", "-id", window_id_text, "WM_CLASS"])
        if output is None:
            return ""
        quoted_parts = output.split('"')
        quoted_values = [
            quoted_parts[index] for index in range(1, len(quoted_parts), 2)
        ]
        if quoted_values:
            return quoted_values[-1]
        return output.strip()

    def _activate_window(self, window_id: str) -> None:
        if self._which("wmctrl") is not None:
            self._run_command(["wmctrl", "-ia", window_id])
            return
        self._run_input_command(["xdotool", "windowactivate", "--sync", window_id])

    def _get_active_window_id(self, *, required: bool) -> int | None:
        if self._which("xdotool") is None:
            if required:
                raise RuntimeError("Linux desktop input control requires xdotool.")
            return None
        output = self._safe_run_command(["xdotool", "getactivewindow"])
        if output is None:
            output = self._safe_run_command(["xdotool", "getwindowfocus"])
        if output is None:
            if required:
                raise RuntimeError("Linux desktop input control could not find focus.")
            return None
        normalized = output.strip()
        if not normalized:
            return None
        return int(normalized)

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
        query: str,
        before_windows: tuple[ComputerWindow, ...] = (),
    ) -> ComputerWindow | None:
        normalized_query = query.strip()
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
                    not normalized_query
                    or self._window_matches(window, normalized_query)
                ):
                    return window
                if normalized_query and self._window_matches(window, normalized_query):
                    return window
            self._sleep(_POLL_INTERVAL_SECONDS)
        return None

    def _window_matches(self, window: ComputerWindow, query: str) -> bool:
        normalized_query = query.casefold()
        return (
            normalized_query in window.title.casefold()
            or normalized_query in window.app_name.casefold()
        )

    def _run_input_command(self, command: Sequence[str]) -> str:
        self._require_display()
        if self._which("xdotool") is None:
            raise RuntimeError("Linux desktop input control requires xdotool.")
        return self._run_command(command)

    def _resolve_launch_command(self, app_name: str) -> list[str]:
        normalized = app_name.strip()
        if not normalized:
            raise ValueError("app_name is required")

        direct_command = shlex.split(normalized)
        if direct_command and self._which(direct_command[0]) is not None:
            return direct_command

        gtk_launch = self._which("gtk-launch")
        flatpak = self._which("flatpak")
        lowered = normalized.casefold()

        candidates: list[list[str]] = []
        if lowered in {"calculator", "calc"}:
            if gtk_launch is not None:
                candidates.append(["gtk-launch", "org.gnome.Calculator"])
            candidates.extend(
                [
                    ["gnome-calculator"],
                    ["kcalc"],
                    ["mate-calc"],
                    ["galculator"],
                    ["xcalc"],
                ]
            )
            if flatpak is not None:
                candidates.append(["flatpak", "run", "org.gnome.Calculator"])
        else:
            candidates.append([normalized])

        for command in candidates:
            if self._which(command[0]) is not None:
                return command
        raise RuntimeError(
            "Could not resolve a Linux desktop launch command for "
            f"{app_name!r}. Install a matching app binary or pass an executable name."
        )

    def _spawn_process(self, command: Sequence[str]) -> None:
        self._require_display()
        LOGGER.info("Launching Linux desktop app", extra={"command": list(command)})
        launch_env = self._build_launch_environment()
        subprocess.Popen(
            list(command),
            cwd=self._project_root,
            env=launch_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _run_command(self, command: Sequence[str]) -> str:
        try:
            completed = subprocess.run(
                list(command),
                cwd=self._project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Linux desktop command timed out: "
                f"{' '.join(command)} after {_COMMAND_TIMEOUT_SECONDS:.0f}s"
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            details = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(
                f"Linux desktop command failed: {' '.join(command)}: {details}"
            )
        return completed.stdout

    def _safe_run_command(self, command: Sequence[str]) -> str | None:
        try:
            return self._run_command(command)
        except RuntimeError:
            LOGGER.debug(
                "Linux desktop command failed during optional discovery",
                extra={"command": list(command)},
            )
            return None

    def _require_display(self) -> None:
        if self._display_available():
            return
        raise RuntimeError(
            "Linux desktop runtime requires an active graphical session with DISPLAY "
            "or WAYLAND_DISPLAY set."
        )

    def _display_available(self) -> bool:
        display = self._env("DISPLAY").strip()
        wayland_display = self._env("WAYLAND_DISPLAY").strip()
        return bool(display or wayland_display)

    def _derive_app_name(self, *, wm_class: str, title: str) -> str:
        class_name = wm_class.split(".", 1)[0].replace("-", " ").strip()
        if class_name:
            return class_name
        return title.strip() or wm_class

    def _normalize_window_id(self, value: str) -> int:
        normalized = value.strip().casefold()
        if normalized.startswith("0x"):
            return int(normalized, 16)
        return int(normalized)

    def _normalize_shortcut(self, shortcut: str) -> str:
        normalized = shortcut.strip()
        if not normalized:
            raise ValueError("shortcut is required")
        key_aliases = {
            "control": "ctrl",
            "ctrl": "ctrl",
            "command": "super",
            "cmd": "super",
            "meta": "super",
            "option": "alt",
            "alt": "alt",
            "shift": "shift",
            "enter": "Return",
            "return": "Return",
            "esc": "Escape",
            "escape": "Escape",
            "del": "Delete",
            "delete": "Delete",
            "tab": "Tab",
            "space": "space",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
        }
        normalized_parts: list[str] = []
        for part in normalized.split("+"):
            token = part.strip()
            if not token:
                continue
            alias = key_aliases.get(token.casefold())
            normalized_parts.append(alias if alias is not None else token)
        if not normalized_parts:
            raise ValueError("shortcut is required")
        return "+".join(normalized_parts)

    def _which(self, name: str) -> str | None:
        return shutil.which(name)

    def _env(self, name: str) -> str:
        return os.environ.get(name, "")

    def _build_launch_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("GDK_BACKEND", "x11")
        env.setdefault("QT_QPA_PLATFORM", "xcb")
        return env

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _time_monotonic(self) -> float:
        return time.monotonic()
