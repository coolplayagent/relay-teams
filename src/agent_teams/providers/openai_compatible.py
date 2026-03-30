# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Sequence
from typing import TYPE_CHECKING, final, override

import httpx
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse

from agent_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from agent_teams.agents.execution.llm_session import AgentLlmSession
from agent_teams.computer import ComputerRuntime
from agent_teams.media import ContentPart, MediaAssetService, MediaModality
from agent_teams.metrics import MetricRecorder
from agent_teams.net.llm_client import build_llm_http_client
from agent_teams.providers.model_config import LlmRetryConfig
from agent_teams.providers.provider_contracts import (
    LLMProvider,
    LLMRequest,
    ProviderCapabilities,
)
from agent_teams.sessions.runs.run_models import (
    AudioGenerationConfig,
    ImageGenerationConfig,
    VideoGenerationConfig,
)

if TYPE_CHECKING:
    from agent_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from agent_teams.agents.orchestration.task_orchestration_service import (
        TaskOrchestrationService,
    )
    from agent_teams.mcp.mcp_registry import McpRegistry
    from agent_teams.notifications import NotificationService
    from agent_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
    from agent_teams.roles.memory_service import RoleMemoryService
    from agent_teams.agents.execution.subagent_reflection import (
        SubagentReflectionService,
    )
    from agent_teams.roles.role_registry import RoleRegistry
    from agent_teams.sessions.runs.run_control_manager import RunControlManager
    from agent_teams.sessions.runs.event_stream import RunEventHub
    from agent_teams.sessions.runs.injection_queue import RunInjectionManager
    from agent_teams.skills.skill_registry import SkillRegistry
    from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
    from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
    from agent_teams.sessions.runs.event_log import EventLog
    from agent_teams.agents.execution.message_repository import MessageRepository
    from agent_teams.sessions.session_history_marker_repository import (
        SessionHistoryMarkerRepository,
    )
    from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
    from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
    from agent_teams.persistence.shared_state_repo import SharedStateRepository
    from agent_teams.agents.tasks.task_repository import TaskRepository
    from agent_teams.providers.token_usage_repo import TokenUsageRepository
    from agent_teams.tools.registry import ToolRegistry
    from agent_teams.tools.runtime import (
        ToolApprovalManager,
        ToolApprovalPolicy,
    )
    from agent_teams.workspace import WorkspaceManager
    from agent_teams.gateway.im import ImToolService


@final
class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        run_intent_repo: RunIntentRepository,
        workspace_manager: WorkspaceManager,
        media_asset_service: MediaAssetService,
        role_memory_service: RoleMemoryService | None,
        subagent_reflection_service: SubagentReflectionService | None,
        tool_registry: ToolRegistry,
        mcp_registry: McpRegistry,
        skill_registry: SkillRegistry,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        message_repo: MessageRepository,
        session_history_marker_repo: SessionHistoryMarkerRepository,
        role_registry: RoleRegistry,
        task_execution_service: TaskExecutionService,
        task_service: TaskOrchestrationService,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        tool_approval_policy: ToolApprovalPolicy,
        notification_service: NotificationService | None = None,
        token_usage_repo: TokenUsageRepository | None = None,
        metric_recorder: MetricRecorder | None = None,
        retry_config: LlmRetryConfig | None = None,
        im_tool_service: ImToolService | None = None,
        computer_runtime: ComputerRuntime | None = None,
    ) -> None:
        self._config_ref = config
        self._media_asset_service = media_asset_service
        self._session = AgentLlmSession(
            config=config,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_bus,
            injection_manager=injection_manager,
            run_event_hub=run_event_hub,
            agent_repo=agent_repo,
            approval_ticket_repo=approval_ticket_repo,
            run_runtime_repo=run_runtime_repo,
            run_intent_repo=run_intent_repo,
            workspace_manager=workspace_manager,
            media_asset_service=media_asset_service,
            role_memory_service=role_memory_service,
            subagent_reflection_service=subagent_reflection_service,
            conversation_compaction_service=ConversationCompactionService(
                config=config,
                retry_config=retry_config or LlmRetryConfig(),
                message_repo=message_repo,
                session_history_marker_repo=session_history_marker_repo,
            ),
            tool_registry=tool_registry,
            mcp_registry=mcp_registry,
            skill_registry=skill_registry,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            message_repo=message_repo,
            role_registry=role_registry,
            task_execution_service=task_execution_service,
            task_service=task_service,
            run_control_manager=run_control_manager,
            tool_approval_manager=tool_approval_manager,
            tool_approval_policy=tool_approval_policy,
            notification_service=notification_service,
            token_usage_repo=token_usage_repo,
            metric_recorder=metric_recorder,
            retry_config=retry_config,
            im_tool_service=im_tool_service,
            computer_runtime=computer_runtime,
        )

    @override
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            input_modalities=(
                MediaModality.IMAGE,
                MediaModality.AUDIO,
                MediaModality.VIDEO,
            ),
            conversation_output_modalities=(
                MediaModality.IMAGE,
                MediaModality.AUDIO,
                MediaModality.VIDEO,
            ),
            native_generation_modalities=(
                MediaModality.IMAGE,
                MediaModality.AUDIO,
                MediaModality.VIDEO,
            ),
            async_generation_modalities=(MediaModality.VIDEO,),
        )

    @override
    async def generate(self, request: LLMRequest) -> str:
        return await self._session.run(request)

    @override
    async def generate_image(self, request: LLMRequest) -> tuple[ContentPart, ...]:
        config = (
            request.generation_config
            if isinstance(request.generation_config, ImageGenerationConfig)
            else ImageGenerationConfig()
        )
        payload: dict[str, object] = {
            "model": self._config.model,
            "prompt": request.prompt_text,
            "n": config.count,
        }
        if config.size is not None and config.size.strip():
            payload["size"] = config.size
        if config.seed is not None:
            payload["seed"] = config.seed
        return await self._request_media_generation(
            request=request,
            endpoint_path="images/generations",
            modality=MediaModality.IMAGE,
            payload=payload,
            default_mime_type="image/png",
            source="provider_generate_image",
        )

    @override
    async def generate_audio(self, request: LLMRequest) -> tuple[ContentPart, ...]:
        config = (
            request.generation_config
            if isinstance(request.generation_config, AudioGenerationConfig)
            else AudioGenerationConfig()
        )
        payload: dict[str, object] = {
            "model": self._config.model,
            "input": request.prompt_text,
        }
        if config.voice is not None and config.voice.strip():
            payload["voice"] = config.voice
        if config.format is not None and config.format.strip():
            payload["response_format"] = config.format
        return await self._request_media_generation(
            request=request,
            endpoint_path="audio/speech",
            modality=MediaModality.AUDIO,
            payload=payload,
            default_mime_type="audio/mpeg",
            source="provider_generate_audio",
        )

    @override
    async def generate_video(self, request: LLMRequest) -> tuple[ContentPart, ...]:
        config = (
            request.generation_config
            if isinstance(request.generation_config, VideoGenerationConfig)
            else VideoGenerationConfig()
        )
        payload: dict[str, object] = {
            "model": self._config.model,
            "prompt": request.prompt_text,
            "n": config.count,
        }
        if config.resolution is not None and config.resolution.strip():
            payload["resolution"] = config.resolution
        if config.duration_ms is not None:
            payload["duration_ms"] = config.duration_ms
        if config.seed is not None:
            payload["seed"] = config.seed
        return await self._request_media_generation(
            request=request,
            endpoint_path="videos/generations",
            modality=MediaModality.VIDEO,
            payload=payload,
            default_mime_type="video/mp4",
            source="provider_generate_video",
        )

    @property
    def _config(self) -> ModelEndpointConfig:
        return self._config_ref

    def _publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        return self._session._publish_tool_call_events_from_messages(
            request=request,
            messages=messages,
            published_tool_call_ids=published_tool_call_ids,
        )

    def _publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        self._session._publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=messages,
        )

    def _build_model_api_error_message(self, error: ModelAPIError) -> str:
        return self._session._build_model_api_error_message(error)

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"_session", "_config_ref", "_media_asset_service"}:
            object.__setattr__(self, name, value)
            return
        if "_session" not in self.__dict__:
            object.__setattr__(self, name, value)
            return
        setattr(self._session, name, value)

    async def _request_media_generation(
        self,
        *,
        request: LLMRequest,
        endpoint_path: str,
        modality: MediaModality,
        payload: dict[str, object],
        default_mime_type: str,
        source: str,
    ) -> tuple[ContentPart, ...]:
        client = build_llm_http_client(
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
        )
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        response = await client.post(
            self._build_endpoint_url(endpoint_path),
            json=payload,
            headers=headers,
        )
        if response.status_code == 202:
            response = await self._poll_async_generation(
                client=client,
                response=response,
                headers=headers,
            )
        return self._store_media_response(
            request=request,
            response=response,
            modality=modality,
            default_mime_type=default_mime_type,
            source=source,
        )

    async def _poll_async_generation(
        self,
        *,
        client: httpx.AsyncClient,
        response: httpx.Response,
        headers: dict[str, str],
    ) -> httpx.Response:
        status_url = self._extract_status_url(response)
        if status_url is None:
            raise RuntimeError(
                "Provider returned an asynchronous media job without a status URL"
            )
        for _attempt in range(60):
            await asyncio.sleep(2.0)
            status_response = await client.get(status_url, headers=headers)
            content_type = str(status_response.headers.get("content-type") or "")
            if "json" not in content_type.lower():
                return status_response
            payload = status_response.json()
            status = _extract_status(payload)
            if status in {"queued", "pending", "running", "processing", "in_progress"}:
                continue
            if status in {"succeeded", "completed", "done", "finished", ""}:
                return status_response
            if status in {"failed", "error", "cancelled", "canceled"}:
                detail = _extract_error_message(payload) or "Media generation failed"
                raise RuntimeError(detail)
            if _looks_like_media_payload(payload):
                return status_response
        raise RuntimeError("Timed out while waiting for asynchronous media generation")

    def _store_media_response(
        self,
        *,
        request: LLMRequest,
        response: httpx.Response,
        modality: MediaModality,
        default_mime_type: str,
        source: str,
    ) -> tuple[ContentPart, ...]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_format_http_error(response)) from exc
        content_type = str(response.headers.get("content-type") or "").split(";")[0]
        normalized_type = content_type.strip().lower()
        if normalized_type.startswith(f"{modality.value}/"):
            record = self._media_asset_service.store_bytes(
                session_id=request.session_id,
                workspace_id=request.workspace_id,
                modality=modality,
                mime_type=normalized_type or default_mime_type,
                data=response.content,
                size_bytes=len(response.content),
                source=source,
            )
            return (self._media_asset_service.to_content_part(record),)
        payload = response.json()
        media_ref = self._extract_media_reference(
            request=request,
            payload=payload,
            modality=modality,
            default_mime_type=default_mime_type,
            source=source,
        )
        return (media_ref,)

    def _extract_media_reference(
        self,
        *,
        request: LLMRequest,
        payload: object,
        modality: MediaModality,
        default_mime_type: str,
        source: str,
    ) -> ContentPart:
        candidate = _extract_primary_candidate(payload)
        url = _extract_url(candidate)
        if url is not None:
            record = self._media_asset_service.store_remote_reference(
                session_id=request.session_id,
                workspace_id=request.workspace_id,
                modality=modality,
                mime_type=_extract_mime_type(candidate) or default_mime_type,
                url=url,
                name=_extract_name(candidate),
                source=source,
            )
            return self._media_asset_service.to_content_part(record)
        encoded = _extract_base64_payload(candidate)
        if encoded is None:
            raise RuntimeError("Provider response did not contain a media payload")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(
                "Provider returned an invalid base64 media payload"
            ) from exc
        record = self._media_asset_service.store_bytes(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            modality=modality,
            mime_type=_extract_mime_type(candidate) or default_mime_type,
            data=raw,
            name=_extract_name(candidate),
            size_bytes=len(raw),
            source=source,
        )
        return self._media_asset_service.to_content_part(record)

    def _build_endpoint_url(self, endpoint_path: str) -> str:
        base_url = self._config.base_url.rstrip("/")
        relative_path = endpoint_path.lstrip("/")
        return f"{base_url}/{relative_path}"

    def _extract_status_url(self, response: httpx.Response) -> str | None:
        for header_name in ("operation-location", "location", "content-location"):
            raw = str(response.headers.get(header_name) or "").strip()
            if raw:
                if raw.startswith("http://") or raw.startswith("https://"):
                    return raw
                return self._build_endpoint_url(raw)
        try:
            payload = response.json()
        except ValueError:
            return None
        for key in ("status_url", "statusUrl", "operation_url", "operationUrl"):
            value = _extract_str(payload, key)
            if value:
                if value.startswith("http://") or value.startswith("https://"):
                    return value
                return self._build_endpoint_url(value)
        return None


def _extract_primary_candidate(payload: object) -> object:
    if isinstance(payload, dict):
        data_value = payload.get("data")
        if isinstance(data_value, list) and data_value:
            return data_value[0]
        for key in ("result", "output", "media", "image", "audio", "video"):
            candidate = payload.get(key)
            if candidate is not None:
                return candidate
    return payload


def _extract_str(payload: object, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_url(payload: object) -> str | None:
    if isinstance(payload, str) and payload.startswith(("http://", "https://")):
        return payload
    for key in ("url", "output_url", "resource_url", "resourceUrl"):
        value = _extract_str(payload, key)
        if value is not None:
            return value
    return None


def _extract_name(payload: object) -> str:
    for key in ("name", "filename", "file_name"):
        value = _extract_str(payload, key)
        if value is not None:
            return value
    return ""


def _extract_mime_type(payload: object) -> str | None:
    for key in ("mime_type", "mimeType", "content_type", "contentType", "type"):
        value = _extract_str(payload, key)
        if value is not None and "/" in value:
            return value
    return None


def _extract_base64_payload(payload: object) -> str | None:
    if isinstance(payload, str):
        return _strip_data_url_prefix(payload)
    if not isinstance(payload, dict):
        return None
    for key in (
        "b64_json",
        "b64_audio",
        "b64_video",
        "base64",
        "content",
        "audio",
        "video",
        "image",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _strip_data_url_prefix(value)
    return None


def _strip_data_url_prefix(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("data:") and "," in normalized:
        return normalized.split(",", 1)[1]
    return normalized


def _extract_status(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("status")
    if isinstance(value, str):
        return value.strip().lower()
    return ""


def _extract_error_message(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _looks_like_media_payload(payload: object) -> bool:
    candidate = _extract_primary_candidate(payload)
    return (
        _extract_url(candidate) is not None
        or _extract_base64_payload(candidate) is not None
    )


def _format_http_error(response: httpx.Response) -> str:
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if payload is not None:
        detail = _extract_error_message(payload) or ""
    if not detail:
        detail = response.text.strip()
    if not detail:
        detail = f"HTTP {response.status_code}"
    return f"Media generation request failed: {detail}"
