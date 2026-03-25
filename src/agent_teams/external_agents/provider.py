# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import cast

from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.messages import UserPromptPart

from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.external_agents.acp_client import (
    AcpTransportClient,
    build_acp_transport,
)
from agent_teams.external_agents.config_service import ExternalAgentConfigService
from agent_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentSessionRecord,
    ExternalAgentSessionStatus,
)
from agent_teams.external_agents.session_repository import (
    ExternalAgentSessionRepository,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.provider_contracts import LLMProvider, LLMRequest
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.workspace import WorkspaceManager


class ExternalAcpProvider(LLMProvider):
    def __init__(
        self,
        *,
        role: RoleDefinition,
        session_manager: ExternalAcpSessionManager,
    ) -> None:
        self._role = role
        self._session_manager = session_manager

    async def generate(self, request: LLMRequest) -> str:
        if not self._role.bound_agent_id:
            raise RuntimeError(
                f"Role {self._role.role_id} is not bound to an external agent"
            )
        return await self._session_manager.prompt(
            agent_id=self._role.bound_agent_id,
            role=self._role,
            request=request,
        )


class ExternalAcpSessionManager:
    def __init__(
        self,
        *,
        config_service: ExternalAgentConfigService,
        session_repo: ExternalAgentSessionRepository,
        message_repo: MessageRepository,
        run_event_hub: RunEventHub,
        workspace_manager: WorkspaceManager,
        mcp_registry: McpRegistry,
    ) -> None:
        self._config_service = config_service
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._run_event_hub = run_event_hub
        self._workspace_manager = workspace_manager
        self._mcp_registry = mcp_registry
        self._conversations: dict[str, _ConversationHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def prompt(
        self,
        *,
        agent_id: str,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> str:
        key = _conversation_key(
            session_id=request.session_id,
            role_id=request.role_id,
            agent_id=agent_id,
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            agent = self._config_service.resolve_runtime_agent(agent_id)
            handle = await self._ensure_conversation(
                key=key,
                agent=agent,
                role=role,
                request=request,
            )
            prompt_text = _extract_latest_user_prompt(
                self._message_repo.get_history_for_conversation_task(
                    request.conversation_id,
                    request.task_id,
                )
            )
            if prompt_text is None:
                prompt_text = str(request.user_prompt or "").strip()
            if not prompt_text:
                raise RuntimeError(
                    "External ACP prompt could not resolve a user message for the task"
                )
            state = _ActivePromptState(request=request)
            handle.active_prompt = state
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.MODEL_STEP_STARTED,
                    payload_json=json.dumps(
                        {
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            try:
                _ = await handle.transport.send_request(
                    "session/prompt",
                    {
                        "sessionId": handle.external_session_id,
                        "messageId": f"{request.run_id}:{request.task_id}",
                        "prompt": [{"type": "text", "text": prompt_text}],
                    },
                )
            except asyncio.CancelledError:
                await self._cancel_prompt(handle)
                raise
            finally:
                if state.thinking_started:
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.THINKING_FINISHED,
                            payload_json=json.dumps(
                                {
                                    "part_index": 0,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=request.session_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        instance_id=request.instance_id,
                        role_id=request.role_id,
                        event_type=RunEventType.MODEL_STEP_FINISHED,
                        payload_json=json.dumps(
                            {
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                handle.active_prompt = None
            output = "".join(state.text_chunks).strip()
            if output:
                self._append_assistant_message(
                    request=request,
                    content=output,
                )
            return output

    async def close(self) -> None:
        handles = list(self._conversations.values())
        self._conversations.clear()
        for handle in handles:
            await handle.transport.close()

    async def _ensure_conversation(
        self,
        *,
        key: str,
        agent: ExternalAgentConfig,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> _ConversationHandle:
        existing = self._conversations.get(key)
        if existing is not None:
            await existing.transport.start()
            return existing

        async def on_message(message: dict[str, JsonValue]) -> None:
            await self._handle_transport_message(
                key=key,
                message=message,
            )

        workspace = self._workspace_manager.resolve(
            session_id=request.session_id,
            role_id=request.role_id,
            instance_id=request.instance_id,
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
        )
        transport = build_acp_transport(
            config=agent,
            on_message=on_message,
            runtime_cwd=str(workspace.resolve_workdir()),
        )
        await transport.start()
        _ = await transport.send_request("initialize", {"protocolVersion": 1})
        persisted = self._session_repo.get(
            session_id=request.session_id,
            role_id=request.role_id,
            agent_id=agent.agent_id,
        )
        external_session_id = await self._load_or_create_remote_session(
            transport=transport,
            persisted=persisted,
            workspace=workspace.resolve_workdir(),
            role=role,
            agent=agent,
        )
        handle = _ConversationHandle(
            transport=transport,
            external_session_id=external_session_id,
        )
        self._conversations[key] = handle
        self._session_repo.upsert(
            ExternalAgentSessionRecord(
                session_id=request.session_id,
                role_id=request.role_id,
                agent_id=agent.agent_id,
                transport=agent.transport.transport,
                external_session_id=external_session_id,
                status=ExternalAgentSessionStatus.READY,
            )
        )
        return handle

    async def _load_or_create_remote_session(
        self,
        *,
        transport: AcpTransportClient,
        persisted: ExternalAgentSessionRecord | None,
        workspace,
        role: RoleDefinition,
        agent: ExternalAgentConfig,
    ) -> str:
        session_params = {
            "cwd": str(workspace),
            "mcpServers": _build_mcp_servers_for_role(
                role=role,
                mcp_registry=self._mcp_registry,
            ),
        }
        if persisted is not None:
            try:
                result = await transport.send_request(
                    "session/load",
                    {
                        "sessionId": persisted.external_session_id,
                        **session_params,
                    },
                )
                loaded_session_id = _required_str(result, "sessionId")
                return loaded_session_id
            except Exception:
                self._session_repo.delete(
                    session_id=persisted.session_id,
                    role_id=persisted.role_id,
                    agent_id=persisted.agent_id,
                )
        created = await transport.send_request("session/new", session_params)
        return _required_str(created, "sessionId")

    async def _handle_transport_message(
        self,
        *,
        key: str,
        message: dict[str, JsonValue],
    ) -> None:
        method = _optional_str(message.get("method"))
        if method != "session/update":
            return
        params = _as_object(message.get("params"))
        update = _as_object(params.get("update"))
        handle = self._conversations.get(key)
        if handle is None or handle.active_prompt is None:
            return
        request = handle.active_prompt.request
        update_name = _required_str(update, "sessionUpdate")
        if update_name == "agent_message_chunk":
            content = _as_object(update.get("content"))
            text = _optional_str(content.get("text"))
            if not text:
                return
            handle.active_prompt.text_chunks.append(text)
            self._publish_text_delta(request=request, text=text)
            return
        if update_name == "agent_thought_chunk":
            content = _as_object(update.get("content"))
            text = _optional_str(content.get("text"))
            if not text:
                return
            if not handle.active_prompt.thinking_started:
                handle.active_prompt.thinking_started = True
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=request.session_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        instance_id=request.instance_id,
                        role_id=request.role_id,
                        event_type=RunEventType.THINKING_STARTED,
                        payload_json=json.dumps(
                            {
                                "part_index": 0,
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.THINKING_DELTA,
                    payload_json=json.dumps(
                        {
                            "part_index": 0,
                            "text": text,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            return
        if update_name == "tool_call":
            payload = {
                "tool_call_id": update.get("toolCallId"),
                "tool_name": _optional_str(update.get("title")) or "tool",
            }
            raw_input = update.get("rawInput")
            if raw_input is not None:
                payload["args"] = raw_input
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TOOL_CALL,
                    payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                )
            )
            return
        if update_name == "tool_call_update":
            tool_result = _extract_tool_result(update)
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TOOL_RESULT,
                    payload_json=json.dumps(
                        {
                            "tool_call_id": update.get("toolCallId"),
                            "tool_name": _optional_str(update.get("title")) or "tool",
                            "result": tool_result,
                            "error": _optional_str(update.get("status")) == "failed",
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            )

    async def _cancel_prompt(self, handle: _ConversationHandle) -> None:
        await handle.transport.send_notification(
            "session/cancel",
            {"sessionId": handle.external_session_id},
        )

    def _publish_text_delta(self, *, request: LLMRequest, text: str) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.TEXT_DELTA,
                payload_json=json.dumps(
                    {
                        "text": text,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    def _append_assistant_message(
        self,
        *,
        request: LLMRequest,
        content: str,
    ) -> None:
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[
                ModelResponse(
                    parts=[TextPart(content=content)],
                    timestamp=datetime.now(tz=timezone.utc),
                )
            ],
        )


class _ConversationHandle:
    def __init__(
        self,
        *,
        transport: AcpTransportClient,
        external_session_id: str,
    ) -> None:
        self.transport = transport
        self.external_session_id = external_session_id
        self.active_prompt: _ActivePromptState | None = None


class _ActivePromptState:
    def __init__(self, *, request: LLMRequest) -> None:
        self.request = request
        self.text_chunks: list[str] = []
        self.thinking_started = False


def _conversation_key(*, session_id: str, role_id: str, agent_id: str) -> str:
    return f"{session_id}:{role_id}:{agent_id}"


def _build_mcp_servers_for_role(
    *,
    role: RoleDefinition,
    mcp_registry: McpRegistry,
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for server_name in role.mcp_servers:
        spec = mcp_registry.get_spec(server_name)
        payload = {str(key): value for key, value in spec.server_config.items()}
        payload["id"] = server_name
        payload["name"] = server_name
        result.append(payload)
    return result


def _extract_latest_user_prompt(
    history: Sequence[ModelMessage],
) -> str | None:
    for message in reversed(history):
        if not isinstance(message, ModelRequest):
            continue
        prompt_parts = [
            part for part in message.parts if isinstance(part, UserPromptPart)
        ]
        if len(prompt_parts) != len(message.parts):
            continue
        combined = "\n".join(
            str(part.content or "").strip() for part in prompt_parts
        ).strip()
        if combined:
            return combined
    return None


def _extract_tool_result(update: dict[str, JsonValue]) -> JsonValue:
    content = update.get("content")
    if not isinstance(content, list) or not content:
        return {}
    first_item = content[0]
    if not isinstance(first_item, dict):
        return {}
    inner_content = _as_object(first_item.get("content"))
    text = _optional_str(inner_content.get("text"))
    if not text:
        return {}
    try:
        return cast(JsonValue, json.loads(text))
    except json.JSONDecodeError:
        return {"text": text}


def _as_object(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _required_str(payload: dict[str, JsonValue], key: str) -> str:
    value = _optional_str(payload.get(key))
    if value is None:
        raise RuntimeError(f"{key} must be a non-empty string")
    return value


def _optional_str(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
