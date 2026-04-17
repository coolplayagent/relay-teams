# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ExecutionSurface(str, Enum):
    API = "api"
    BROWSER = "browser"
    DESKTOP = "desktop"
    HYBRID = "hybrid"


class ComputerRuntimeKind(str, Enum):
    BUILTIN_TOOL = "builtin_tool"
    APP_MCP = "app_mcp"
    SESSION_MCP_ACP = "session_mcp_acp"
    EXTERNAL_ACP = "external_acp"


class ComputerPermissionScope(str, Enum):
    OBSERVE = "observe"
    INPUT_TEXT = "input_text"
    POINTER = "pointer"
    KEYBOARD_SHORTCUT = "keyboard_shortcut"
    WINDOW_MANAGEMENT = "window_management"
    APP_LAUNCH = "app_launch"
    DESTRUCTIVE = "destructive"


class ComputerActionRisk(str, Enum):
    SAFE = "safe"
    GUARDED = "guarded"
    DESTRUCTIVE = "destructive"


class ComputerActionType(str, Enum):
    CAPTURE_SCREEN = "capture_screen"
    LIST_WINDOWS = "list_windows"
    FOCUS_WINDOW = "focus_window"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    DRAG = "drag"
    TYPE_TEXT = "type_text"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    LAUNCH_APP = "launch_app"
    WAIT_FOR_WINDOW = "wait_for_window"


class ComputerWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_id: str = Field(min_length=1)
    app_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    focused: bool = False


class ComputerActionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_name: str = ""
    window_title: str = ""
    x: int | None = None
    y: int | None = None
    end_x: int | None = None
    end_y: int | None = None
    text: str = ""
    shortcut: str = ""
    amount: int | None = None

    def summary(self) -> str:
        fragments: list[str] = []
        if self.app_name:
            fragments.append(self.app_name)
        if self.window_title:
            fragments.append(self.window_title)
        if self.x is not None and self.y is not None:
            fragments.append(f"({self.x}, {self.y})")
        if self.end_x is not None and self.end_y is not None:
            fragments.append(f"-> ({self.end_x}, {self.end_y})")
        if self.shortcut:
            fragments.append(self.shortcut)
        if self.text:
            fragments.append(self.text[:48])
        if self.amount is not None:
            fragments.append(str(self.amount))
        return " | ".join(fragment for fragment in fragments if fragment)


class ComputerObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = ""
    windows: tuple[ComputerWindow, ...] = ()
    focused_window: str = ""
    screenshot_bytes: bytes | None = None
    screenshot_mime_type: str | None = None
    screenshot_name: str = ""
    screenshot_origin_x: int | None = None
    screenshot_origin_y: int | None = None
    screenshot_width: int | None = Field(default=None, ge=0)
    screenshot_height: int | None = Field(default=None, ge=0)


class ComputerActionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ComputerActionType
    runtime_kind: ComputerRuntimeKind
    execution_surface: ExecutionSurface = ExecutionSurface.DESKTOP
    permission_scope: ComputerPermissionScope
    risk_level: ComputerActionRisk
    source: str = ""
    server_name: str = ""
    target: ComputerActionTarget = Field(default_factory=ComputerActionTarget)

    def target_summary(self) -> str:
        return self.target.summary()

    def to_payload(self) -> dict[str, JsonValue]:
        payload = self.model_dump(mode="json")
        payload["target_summary"] = self.target_summary()
        return payload


class ComputerActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ComputerActionDescriptor
    message: str = ""
    observation: ComputerObservation | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)

    def to_visible_payload(
        self,
        *,
        content: tuple[dict[str, JsonValue], ...] = (),
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "text": self.message,
            "computer": self.action.to_payload(),
        }
        if self.observation is not None:
            payload["observation"] = self.observation.model_dump(
                mode="json",
                exclude={
                    "screenshot_bytes",
                },
                exclude_none=True,
            )
        if content:
            payload["content"] = list(content)
        if self.data:
            payload["data"] = dict(self.data)
        return payload
