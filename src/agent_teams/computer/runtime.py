# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Protocol

from agent_teams.computer.linux_runtime import LinuxDesktopRuntime
from agent_teams.computer.models import (
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


class ComputerRuntime(Protocol):
    async def capture_screen(self) -> ComputerActionResult: ...

    async def list_windows(self) -> ComputerActionResult: ...

    async def focus_window(self, *, window_title: str) -> ComputerActionResult: ...

    async def click_at(self, *, x: int, y: int) -> ComputerActionResult: ...

    async def double_click_at(self, *, x: int, y: int) -> ComputerActionResult: ...

    async def drag_between(
        self,
        *,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> ComputerActionResult: ...

    async def type_text(self, *, text: str) -> ComputerActionResult: ...

    async def scroll_view(self, *, amount: int) -> ComputerActionResult: ...

    async def hotkey(self, *, shortcut: str) -> ComputerActionResult: ...

    async def launch_app(self, *, app_name: str) -> ComputerActionResult: ...

    async def wait_for_window(self, *, window_title: str) -> ComputerActionResult: ...


class DisabledComputerRuntime:
    def __init__(self, *, reason: str | None = None) -> None:
        base_message = (
            "Computer runtime is not configured. Set "
            "AGENT_TEAMS_COMPUTER_RUNTIME=fake for scripted validation."
        )
        if reason:
            self._message = f"{base_message} {reason}"
        else:
            self._message = base_message

    async def capture_screen(self) -> ComputerActionResult:
        raise RuntimeError(self._message)

    async def list_windows(self) -> ComputerActionResult:
        raise RuntimeError(self._message)

    async def focus_window(self, *, window_title: str) -> ComputerActionResult:
        _ = window_title
        raise RuntimeError(self._message)

    async def click_at(self, *, x: int, y: int) -> ComputerActionResult:
        _ = (x, y)
        raise RuntimeError(self._message)

    async def double_click_at(self, *, x: int, y: int) -> ComputerActionResult:
        _ = (x, y)
        raise RuntimeError(self._message)

    async def drag_between(
        self,
        *,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> ComputerActionResult:
        _ = (start_x, start_y, end_x, end_y)
        raise RuntimeError(self._message)

    async def type_text(self, *, text: str) -> ComputerActionResult:
        _ = text
        raise RuntimeError(self._message)

    async def scroll_view(self, *, amount: int) -> ComputerActionResult:
        _ = amount
        raise RuntimeError(self._message)

    async def hotkey(self, *, shortcut: str) -> ComputerActionResult:
        _ = shortcut
        raise RuntimeError(self._message)

    async def launch_app(self, *, app_name: str) -> ComputerActionResult:
        _ = app_name
        raise RuntimeError(self._message)

    async def wait_for_window(self, *, window_title: str) -> ComputerActionResult:
        _ = window_title
        raise RuntimeError(self._message)


class ScriptedComputerRuntime:
    def __init__(
        self, *, project_root: Path, screenshot_path: Path | None = None
    ) -> None:
        self._project_root = Path(project_root)
        self._screenshot_path = (
            screenshot_path
            if screenshot_path is not None
            else self._project_root / "docs" / "agent_teams.png"
        )
        self._windows: list[ComputerWindow] = [
            ComputerWindow(
                window_id="window-agent-teams",
                app_name="Agent Teams",
                title="Agent Teams Demo",
                focused=True,
            ),
            ComputerWindow(
                window_id="window-browser",
                app_name="Browser",
                title="Chrome DevTools",
                focused=False,
            ),
        ]

    async def capture_screen(self) -> ComputerActionResult:
        return self._observation_result(
            action=self._descriptor(
                action=ComputerActionType.CAPTURE_SCREEN,
                permission_scope=ComputerPermissionScope.OBSERVE,
                risk_level=ComputerActionRisk.SAFE,
            ),
            message="Captured the current desktop screenshot.",
        )

    async def list_windows(self) -> ComputerActionResult:
        return self._observation_result(
            action=self._descriptor(
                action=ComputerActionType.LIST_WINDOWS,
                permission_scope=ComputerPermissionScope.OBSERVE,
                risk_level=ComputerActionRisk.SAFE,
            ),
            message="Listed visible windows in the scripted desktop runtime.",
        )

    async def focus_window(self, *, window_title: str) -> ComputerActionResult:
        focused_title = self._focus_window(window_title)
        return self._observation_result(
            action=self._descriptor(
                action=ComputerActionType.FOCUS_WINDOW,
                permission_scope=ComputerPermissionScope.WINDOW_MANAGEMENT,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(window_title=focused_title),
            ),
            message=f"Focused window: {focused_title}.",
        )

    async def click_at(self, *, x: int, y: int) -> ComputerActionResult:
        return self._action_result(
            action=self._descriptor(
                action=ComputerActionType.CLICK,
                permission_scope=ComputerPermissionScope.POINTER,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(x=x, y=y),
            ),
            message=f"Clicked at ({x}, {y}) in the scripted runtime.",
        )

    async def double_click_at(self, *, x: int, y: int) -> ComputerActionResult:
        return self._action_result(
            action=self._descriptor(
                action=ComputerActionType.DOUBLE_CLICK,
                permission_scope=ComputerPermissionScope.POINTER,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(x=x, y=y),
            ),
            message=f"Double-clicked at ({x}, {y}) in the scripted runtime.",
        )

    async def drag_between(
        self,
        *,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> ComputerActionResult:
        return self._action_result(
            action=self._descriptor(
                action=ComputerActionType.DRAG,
                permission_scope=ComputerPermissionScope.DESTRUCTIVE,
                risk_level=ComputerActionRisk.DESTRUCTIVE,
                target=ComputerActionTarget(
                    x=start_x,
                    y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                ),
            ),
            message=(
                "Dragged between scripted desktop coordinates "
                f"({start_x}, {start_y}) -> ({end_x}, {end_y})."
            ),
        )

    async def type_text(self, *, text: str) -> ComputerActionResult:
        return self._action_result(
            action=self._descriptor(
                action=ComputerActionType.TYPE_TEXT,
                permission_scope=ComputerPermissionScope.INPUT_TEXT,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(text=text),
            ),
            message=f"Typed text in the scripted runtime: {text}",
        )

    async def scroll_view(self, *, amount: int) -> ComputerActionResult:
        return self._action_result(
            action=self._descriptor(
                action=ComputerActionType.SCROLL,
                permission_scope=ComputerPermissionScope.POINTER,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(amount=amount),
            ),
            message=f"Scrolled by {amount} in the scripted runtime.",
        )

    async def hotkey(self, *, shortcut: str) -> ComputerActionResult:
        return self._action_result(
            action=self._descriptor(
                action=ComputerActionType.HOTKEY,
                permission_scope=ComputerPermissionScope.KEYBOARD_SHORTCUT,
                risk_level=ComputerActionRisk.GUARDED,
                target=ComputerActionTarget(shortcut=shortcut),
            ),
            message=f"Sent shortcut in the scripted runtime: {shortcut}",
        )

    async def launch_app(self, *, app_name: str) -> ComputerActionResult:
        next_window = ComputerWindow(
            window_id=f"window-{app_name.casefold().replace(' ', '-')}",
            app_name=app_name,
            title=f"{app_name} Window",
            focused=True,
        )
        self._windows = [
            window.model_copy(update={"focused": False}) for window in self._windows
        ]
        self._windows.append(next_window)
        return self._observation_result(
            action=self._descriptor(
                action=ComputerActionType.LAUNCH_APP,
                permission_scope=ComputerPermissionScope.APP_LAUNCH,
                risk_level=ComputerActionRisk.DESTRUCTIVE,
                target=ComputerActionTarget(app_name=app_name),
            ),
            message=f"Launched scripted app: {app_name}.",
        )

    async def wait_for_window(self, *, window_title: str) -> ComputerActionResult:
        return self._observation_result(
            action=self._descriptor(
                action=ComputerActionType.WAIT_FOR_WINDOW,
                permission_scope=ComputerPermissionScope.OBSERVE,
                risk_level=ComputerActionRisk.SAFE,
                target=ComputerActionTarget(window_title=window_title),
            ),
            message=f"Observed window in scripted runtime: {window_title}.",
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

    def _action_result(
        self,
        *,
        action: ComputerActionDescriptor,
        message: str,
    ) -> ComputerActionResult:
        return ComputerActionResult(
            action=action,
            message=message,
            observation=self._build_observation(),
        )

    def _observation_result(
        self,
        *,
        action: ComputerActionDescriptor,
        message: str,
    ) -> ComputerActionResult:
        return ComputerActionResult(
            action=action,
            message=message,
            observation=self._build_observation(),
            data={
                "window_count": len(self._windows),
            },
        )

    def _build_observation(self) -> ComputerObservation:
        screenshot_bytes = None
        if self._screenshot_path.exists():
            screenshot_bytes = self._screenshot_path.read_bytes()
        focused_window = next(
            (window.title for window in self._windows if window.focused),
            "",
        )
        return ComputerObservation(
            text="Scripted computer runtime snapshot.",
            windows=tuple(self._windows),
            focused_window=focused_window,
            screenshot_bytes=screenshot_bytes,
            screenshot_mime_type="image/png" if screenshot_bytes is not None else None,
            screenshot_name="scripted-desktop.png",
        )

    def _focus_window(self, window_title: str) -> str:
        normalized = window_title.strip()
        if not normalized:
            raise ValueError("window_title is required")
        updated: list[ComputerWindow] = []
        matched_title = ""
        for window in self._windows:
            is_match = normalized.casefold() in window.title.casefold()
            if is_match:
                matched_title = window.title
            updated.append(window.model_copy(update={"focused": is_match}))
        if not matched_title:
            raise ValueError(f"Window not found: {window_title}")
        self._windows = updated
        return matched_title


def build_default_computer_runtime(*, project_root: Path) -> ComputerRuntime:
    mode = os.environ.get("AGENT_TEAMS_COMPUTER_RUNTIME", "").strip().casefold()
    if mode == "fake":
        return ScriptedComputerRuntime(project_root=project_root)

    system_name = _platform_system()
    if system_name == "linux":
        return LinuxDesktopRuntime(project_root=project_root)
    if system_name == "darwin":
        return DisabledComputerRuntime(
            reason="macOS desktop control has not been implemented yet."
        )
    if system_name:
        return DisabledComputerRuntime(
            reason=f"Unsupported host platform: {platform.system()}."
        )
    return DisabledComputerRuntime(reason="Unable to detect the host platform.")


def _platform_system() -> str:
    return platform.system().strip().casefold()
