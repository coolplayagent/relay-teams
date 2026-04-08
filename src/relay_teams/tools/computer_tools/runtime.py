# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.computer import (
    ComputerActionResult,
    ComputerActionTarget,
    ComputerRuntime,
    describe_builtin_tool,
)
from relay_teams.media import MediaModality
from relay_teams.tools.runtime import (
    ToolApprovalRequest,
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)

_DESCRIPTIONS = {
    "capture_screen": "Capture a desktop screenshot and attach it to the run timeline.",
    "list_windows": "List visible desktop windows and report which window is focused.",
    "focus_window": "Move focus to a desktop window by title.",
    "click_at": "Click a desktop coordinate.",
    "double_click_at": "Double-click a desktop coordinate.",
    "drag_between": "Drag between desktop coordinates.",
    "type_text": "Type text into the active desktop window.",
    "scroll_view": "Scroll the active desktop view by a signed amount.",
    "hotkey": "Send a keyboard shortcut to the active desktop window.",
    "launch_app": "Launch an application in the desktop runtime.",
    "wait_for_window": "Wait for a window title to appear in the desktop runtime.",
}


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=_DESCRIPTIONS["capture_screen"])
    async def capture_screen(ctx: ToolContext) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.capture_screen()
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="capture_screen",
            args_summary={},
            action=_action,
            approval_request=_approval_request("capture_screen"),
        )

    @agent.tool(description=_DESCRIPTIONS["list_windows"])
    async def list_windows(ctx: ToolContext) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.list_windows()
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="list_windows",
            args_summary={},
            action=_action,
            approval_request=_approval_request("list_windows"),
        )

    @agent.tool(description=_DESCRIPTIONS["focus_window"])
    async def focus_window(
        ctx: ToolContext,
        window_title: str,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.focus_window(window_title=window_title)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="focus_window",
            args_summary={"window_title": window_title},
            action=_action,
            approval_request=_approval_request(
                "focus_window",
                target=ComputerActionTarget(window_title=window_title),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["click_at"])
    async def click_at(
        ctx: ToolContext,
        x: int,
        y: int,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.click_at(x=x, y=y)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="click_at",
            args_summary={"x": x, "y": y},
            action=_action,
            approval_request=_approval_request(
                "click_at",
                target=ComputerActionTarget(x=x, y=y),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["double_click_at"])
    async def double_click_at(
        ctx: ToolContext,
        x: int,
        y: int,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.double_click_at(x=x, y=y)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="double_click_at",
            args_summary={"x": x, "y": y},
            action=_action,
            approval_request=_approval_request(
                "double_click_at",
                target=ComputerActionTarget(x=x, y=y),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["drag_between"])
    async def drag_between(
        ctx: ToolContext,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.drag_between(
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
            )
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="drag_between",
            args_summary={
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
            },
            action=_action,
            approval_request=_approval_request(
                "drag_between",
                target=ComputerActionTarget(
                    x=start_x,
                    y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                ),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["type_text"])
    async def type_text(
        ctx: ToolContext,
        text: str,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.type_text(text=text)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="type_text",
            args_summary={"text": text},
            action=_action,
            approval_request=_approval_request(
                "type_text",
                target=ComputerActionTarget(text=text),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["scroll_view"])
    async def scroll_view(
        ctx: ToolContext,
        amount: int,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.scroll_view(amount=amount)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="scroll_view",
            args_summary={"amount": amount},
            action=_action,
            approval_request=_approval_request(
                "scroll_view",
                target=ComputerActionTarget(amount=amount),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["hotkey"])
    async def hotkey(
        ctx: ToolContext,
        shortcut: str,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.hotkey(shortcut=shortcut)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="hotkey",
            args_summary={"shortcut": shortcut},
            action=_action,
            approval_request=_approval_request(
                "hotkey",
                target=ComputerActionTarget(shortcut=shortcut),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["launch_app"])
    async def launch_app(
        ctx: ToolContext,
        app_name: str,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.launch_app(app_name=app_name)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="launch_app",
            args_summary={"app_name": app_name},
            action=_action,
            approval_request=_approval_request(
                "launch_app",
                target=ComputerActionTarget(app_name=app_name),
            ),
        )

    @agent.tool(description=_DESCRIPTIONS["wait_for_window"])
    async def wait_for_window(
        ctx: ToolContext,
        window_title: str,
    ) -> dict[str, JsonValue]:
        runtime = _require_computer_runtime(ctx)

        async def _action() -> ToolResultProjection:
            result = await runtime.wait_for_window(window_title=window_title)
            return _project_result(ctx=ctx, result=result)

        return await execute_tool(
            ctx,
            tool_name="wait_for_window",
            args_summary={"window_title": window_title},
            action=_action,
            approval_request=_approval_request(
                "wait_for_window",
                target=ComputerActionTarget(window_title=window_title),
            ),
        )


def _require_computer_runtime(ctx: ToolContext) -> ComputerRuntime:
    runtime = ctx.deps.computer_runtime
    if runtime is None:
        raise RuntimeError("Computer runtime is unavailable in this execution context.")
    return runtime


def _approval_request(
    tool_name: str,
    *,
    target: ComputerActionTarget | None = None,
) -> ToolApprovalRequest | None:
    descriptor = describe_builtin_tool(tool_name)
    if descriptor is None:
        return None
    if target is not None:
        descriptor = descriptor.model_copy(update={"target": target})
    return ToolApprovalRequest(
        permission_scope=descriptor.permission_scope,
        risk_level=descriptor.risk_level,
        target_summary=descriptor.target_summary(),
        source=descriptor.source,
        execution_surface=descriptor.execution_surface,
    )


def _project_result(
    *,
    ctx: ToolContext,
    result: ComputerActionResult,
) -> ToolResultProjection:
    content: list[dict[str, JsonValue]] = []
    internal_data = result.model_dump(
        mode="json",
        exclude={"observation": {"screenshot_bytes"}},
        exclude_none=True,
    )
    observation = result.observation
    if (
        observation is not None
        and observation.screenshot_bytes is not None
        and observation.screenshot_mime_type is not None
        and ctx.deps.media_asset_service is not None
    ):
        record = ctx.deps.media_asset_service.store_bytes(
            session_id=ctx.deps.session_id,
            workspace_id=ctx.deps.workspace_id,
            modality=MediaModality.IMAGE,
            mime_type=observation.screenshot_mime_type,
            data=observation.screenshot_bytes,
            name=observation.screenshot_name or "computer-screenshot.png",
            width=observation.screenshot_width,
            height=observation.screenshot_height,
            source="computer_tool",
        )
        content.append(
            ctx.deps.media_asset_service.to_content_part(record).model_dump(mode="json")
        )
        internal_data["media_asset_id"] = record.asset_id
    return ToolResultProjection(
        visible_data=result.to_visible_payload(content=tuple(content)),
        internal_data=internal_data,
    )
