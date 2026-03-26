# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Sequence
from contextlib import suppress
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
from agent_teams.logger import get_logger, log_event
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.provider_contracts import LLMProvider, LLMRequest
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.workspace import WorkspaceManager

LOGGER = get_logger(__name__)
_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS = 60.0
_EXTERNAL_ACP_EMPTY_RESPONSE_REPROMPT = (
    "Your previous reply was empty. Answer the user's last request with a non-empty "
    "assistant message now. If you need to return an image, reply with a single "
    "data:image/...;base64,... URL. Do not return an empty response."
)


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
                output = await self._prompt_until_output(
                    agent_id=agent_id,
                    handle=handle,
                    request=request,
                    state=state,
                    prompt_text=prompt_text,
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
            if output:
                self._append_assistant_message(
                    request=request,
                    content=output,
                )
            return output

    async def _prompt_until_output(
        self,
        *,
        agent_id: str,
        handle: _ConversationHandle,
        request: LLMRequest,
        state: _ActivePromptState,
        prompt_text: str,
    ) -> str:
        prompts = [prompt_text, _EXTERNAL_ACP_EMPTY_RESPONSE_REPROMPT]
        for attempt_index, attempt_prompt in enumerate(prompts):
            state.reset_activity()
            request_task = asyncio.create_task(
                handle.transport.send_request(
                    "session/prompt",
                    {
                        "sessionId": handle.external_session_id,
                        "messageId": _prompt_message_id(
                            request=request,
                            attempt_index=attempt_index,
                        ),
                        "prompt": [{"type": "text", "text": attempt_prompt}],
                    },
                )
            )
            await self._await_prompt_response(
                agent_id=agent_id,
                handle=handle,
                state=state,
                request_task=request_task,
            )
            output = state.resolve_output()
            if output:
                return output
            if attempt_index == 0:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="external_agent.prompt.empty_response_retry",
                    message=(
                        "External ACP prompt returned an empty response; retrying "
                        "with a non-empty answer instruction"
                    ),
                    payload={
                        "agent_id": agent_id,
                        "run_id": request.run_id,
                        "session_id": request.session_id,
                    },
                )
                continue
            raise RuntimeError("External ACP prompt returned an empty response")
        return ""

    async def _await_prompt_response(
        self,
        *,
        agent_id: str,
        handle: _ConversationHandle,
        state: _ActivePromptState,
        request_task: asyncio.Task[dict[str, JsonValue]],
    ) -> None:
        while True:
            activity_task = asyncio.create_task(state.wait_for_activity())
            done, pending = await asyncio.wait(
                {request_task, activity_task},
                timeout=_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if activity_task in pending:
                activity_task.cancel()
            if request_task in done:
                _ = await request_task
                return
            if activity_task in done:
                with suppress(asyncio.CancelledError):
                    await activity_task
                continue
            await self._handle_prompt_timeout(
                agent_id=agent_id,
                handle=handle,
                state=state,
                request_task=request_task,
            )
            return

    async def _handle_prompt_timeout(
        self,
        *,
        agent_id: str,
        handle: _ConversationHandle,
        state: _ActivePromptState,
        request_task: asyncio.Task[dict[str, JsonValue]],
    ) -> None:
        with suppress(Exception):
            await self._cancel_prompt(handle)
        request_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await request_task
        fallback_output = state.timeout_fallback_output()
        if fallback_output is not None:
            log_event(
                LOGGER,
                logging.WARNING,
                event="external_agent.prompt.timeout_fallback",
                message=(
                    "External ACP prompt timed out after inactivity; using tool "
                    "result fallback"
                ),
                payload={
                    "agent_id": agent_id,
                    "run_id": state.request.run_id,
                    "session_id": state.request.session_id,
                },
            )
            state.text_chunks.append(fallback_output)
            self._publish_text_delta(request=state.request, text=fallback_output)
            return
        log_event(
            LOGGER,
            logging.ERROR,
            event="external_agent.prompt.timeout",
            message="External ACP prompt timed out after inactivity",
            payload={
                "agent_id": agent_id,
                "run_id": state.request.run_id,
                "session_id": state.request.session_id,
                "timeout_seconds": _EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS,
            },
        )
        timeout_seconds = _EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS
        raise RuntimeError(
            "External ACP prompt timed out after "
            f"{timeout_seconds:g} seconds without updates"
        )

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
        handle.active_prompt.mark_activity()
        if update_name == "agent_message_chunk":
            text = _extract_content_text(update.get("content"))
            if not text:
                return
            handle.active_prompt.text_chunks.append(text)
            self._publish_text_delta(request=request, text=text)
            return
        if update_name == "agent_thought_chunk":
            text = _extract_content_text(update.get("content"))
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
            handle.active_prompt.note_tool_result(tool_result)
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
        self.last_tool_result_text: str | None = None
        self._activity_event = asyncio.Event()

    def mark_activity(self) -> None:
        self._activity_event.set()

    async def wait_for_activity(self) -> None:
        await self._activity_event.wait()
        self._activity_event.clear()

    def reset_activity(self) -> None:
        self._activity_event.clear()

    def note_tool_result(self, tool_result: JsonValue) -> None:
        text = _extract_tool_result_text(tool_result)
        if text is not None:
            self.last_tool_result_text = text

    def resolve_output(self) -> str:
        text_output = "".join(self.text_chunks).strip()
        if text_output:
            return text_output
        fallback_output = self.timeout_fallback_output()
        if fallback_output is not None:
            return fallback_output
        return ""

    def timeout_fallback_output(self) -> str | None:
        if self.text_chunks:
            return None
        return _extract_image_timeout_fallback(self.last_tool_result_text)


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
    raw_content = first_item.get("content")
    inner_content = _as_object(raw_content)
    text = _extract_content_text(raw_content)
    if not text:
        return {}
    if _optional_str(inner_content.get("type")) != "text":
        return {"text": text}
    try:
        return cast(JsonValue, json.loads(text))
    except json.JSONDecodeError:
        return {"text": text}


def _extract_tool_result_text(tool_result: JsonValue) -> str | None:
    if not isinstance(tool_result, dict):
        return None
    return _optional_str(tool_result.get("text"))


def _extract_image_timeout_fallback(text: str | None) -> str | None:
    normalized = _optional_str(text)
    if normalized is None:
        return None
    if normalized.startswith("data:image/") and ";base64," in normalized:
        return normalized
    compact = "".join(normalized.split())
    if len(compact) < 64:
        return None
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None
    mime_type = _detect_supported_image_mime_type(decoded)
    if mime_type is not None:
        return f"data:{mime_type};base64,{compact}"
    return None


def _detect_supported_image_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _prompt_message_id(*, request: LLMRequest, attempt_index: int) -> str:
    suffix = "" if attempt_index == 0 else f":retry-{attempt_index}"
    return f"{request.run_id}:{request.task_id}{suffix}"


def _extract_content_text(content: JsonValue | None) -> str | None:
    content_object = _as_object(content)
    content_type = _optional_str(content_object.get("type"))
    if content_type == "text":
        return _optional_str(content_object.get("text"))
    if content_type in {"image", "audio"}:
        return _extract_binary_content_text(content_object)
    if content_type == "resource_link":
        return _optional_str(content_object.get("uri"))
    if content_type == "resource":
        resource = _as_object(content_object.get("resource"))
        text = _optional_str(resource.get("text"))
        if text is not None:
            return text
        return _extract_binary_resource_text(resource)
    return None


def _extract_binary_content_text(content: dict[str, JsonValue]) -> str | None:
    data = _optional_str(content.get("data"))
    mime_type = _optional_str(content.get("mimeType"))
    if data is not None:
        if mime_type is not None:
            return f"data:{mime_type};base64,{data}"
        return data
    return _optional_str(content.get("uri"))


def _extract_binary_resource_text(resource: dict[str, JsonValue]) -> str | None:
    blob = _optional_str(resource.get("blob"))
    mime_type = _optional_str(resource.get("mimeType"))
    if blob is not None:
        if mime_type is not None:
            return f"data:{mime_type};base64,{blob}"
        return blob
    return _optional_str(resource.get("uri"))


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
