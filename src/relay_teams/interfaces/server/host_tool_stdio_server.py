# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from pydantic import JsonValue

from relay_teams.external_agents.host_tool_bridge import (
    HOST_TOOL_CONFIG_DIR_ENV,
    HOST_TOOL_CONVERSATION_ID_ENV,
    HOST_TOOL_INSTANCE_ID_ENV,
    HOST_TOOL_ROLE_ID_ENV,
    HOST_TOOL_RUN_ID_ENV,
    HOST_TOOL_SESSION_ID_ENV,
    HOST_TOOL_TASK_ID_ENV,
    HOST_TOOL_TRACE_ID_ENV,
    HOST_TOOL_WORKSPACE_ID_ENV,
    ExternalAcpHostToolBridge,
)
from relay_teams.interfaces.server.container import ServerContainer
from relay_teams.providers.provider_contracts import LLMRequest


async def _run_stdio_server() -> None:
    config_dir = Path(_required_env(HOST_TOOL_CONFIG_DIR_ENV))
    role_id = _required_env(HOST_TOOL_ROLE_ID_ENV)
    request = LLMRequest(
        run_id=_required_env(HOST_TOOL_RUN_ID_ENV),
        trace_id=_required_env(HOST_TOOL_TRACE_ID_ENV),
        task_id=_required_env(HOST_TOOL_TASK_ID_ENV),
        session_id=_required_env(HOST_TOOL_SESSION_ID_ENV),
        workspace_id=_required_env(HOST_TOOL_WORKSPACE_ID_ENV),
        conversation_id=os.environ.get(HOST_TOOL_CONVERSATION_ID_ENV, ""),
        instance_id=_required_env(HOST_TOOL_INSTANCE_ID_ENV),
        role_id=role_id,
        system_prompt="",
        user_prompt=None,
    )
    container = ServerContainer(
        config_dir=config_dir,
        manage_runtime_state=False,
    )
    role = container.role_registry.get(role_id)
    bridge = ExternalAcpHostToolBridge(
        task_repo=container.task_repo,
        shared_store=container.shared_store,
        event_bus=container.event_log,
        injection_manager=container.injection_manager,
        run_event_hub=container.run_event_hub,
        agent_repo=container.agent_repo,
        approval_ticket_repo=container.approval_ticket_repo,
        user_question_repo=container.user_question_repo,
        run_runtime_repo=container.run_runtime_repo,
        run_intent_repo=container.run_intent_repo,
        background_task_service=container.background_task_service,
        todo_service=container.todo_service,
        monitor_service=container.monitor_service,
        workspace_manager=container.workspace_manager,
        media_asset_service=container.media_asset_service,
        role_memory_service=container.role_memory_service,
        tool_registry=container.tool_registry,
        message_repo=container.message_repo,
        get_mcp_registry=lambda: container.mcp_registry,
        get_skill_registry=lambda: container.skill_registry,
        get_role_registry=lambda: container.role_registry,
        get_task_execution_service=lambda: container.task_execution_service,
        get_task_service=lambda: container.task_service,
        run_control_manager=container.run_control_manager,
        tool_approval_manager=container.tool_approval_manager,
        user_question_manager=container.user_question_manager,
        tool_approval_policy=container.tool_approval_policy,
        runtime_role_resolver=container.runtime_role_resolver,
        shell_approval_repo=container.shell_approval_repo,
        get_notification_service=lambda: container.notification_service,
        resolve_model_config=container.resolve_external_agent_model_config,
        metric_recorder=container.metric_recorder,
        im_tool_service=container.im_tool_service,
        computer_runtime=container.computer_runtime,
        reminder_service=getattr(container, "reminder_service", None),
    )

    async def unavailable_request(
        _method: str,
        _params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        raise RuntimeError("ACP request transport is unavailable in stdio mode.")

    async def ignore_notification(
        _method: str,
        _params: dict[str, JsonValue],
    ) -> None:
        return None

    await bridge.configure(
        role=role,
        session_id=request.session_id,
        external_session_id="",
        send_request=unavailable_request,
        send_notification=ignore_notification,
    )
    bridge.bind_active_request(request)
    try:
        await bridge.require_server().run_stdio_async(
            show_banner=False,
            log_level="ERROR",
        )
    finally:
        await bridge.close()


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_stdio_server())


if __name__ == "__main__":
    main()
