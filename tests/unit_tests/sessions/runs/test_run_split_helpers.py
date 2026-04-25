# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import cast, override

import pytest

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.media import (
    ContentPart,
    InlineMediaContentPart,
    MediaModality,
    TextContentPart,
)
from relay_teams.media.asset_service import MediaAssetService
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.media_run_executor import MediaRunExecutor
from relay_teams.sessions.runs.run_event_publisher import RunEventPublisher
from relay_teams.sessions.runs.run_interactions import parse_tool_approval_action
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RunEvent,
    RunKind,
    RunTopologySnapshot,
)
from relay_teams.sessions.runs.run_terminal_results import RunTerminalResultService
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_models import SessionMode


class _FakeRoleRegistry:
    def get_main_agent_role_id(self) -> str:
        return "main-role"


def _build_media_executor(
    *,
    role_registry: RoleRegistry | None = None,
) -> MediaRunExecutor:
    return MediaRunExecutor(
        session_repo=cast(SessionRepository, object()),
        get_role_registry=lambda: role_registry,
        provider_factory=lambda _role, _session_id: LLMProvider(),
        require_agent_repo=lambda: cast(AgentInstanceRepository, object()),
        require_task_repo=lambda: cast(TaskRepository, object()),
        require_message_repo=lambda: cast(MessageRepository, object()),
        require_media_asset_service=lambda: cast(MediaAssetService, object()),
        event_publisher=cast(RunEventPublisher, object()),
        terminal_results=cast(RunTerminalResultService, object()),
    )


def test_parse_tool_approval_action_accepts_all_supported_actions() -> None:
    assert parse_tool_approval_action("approve") == "approve"
    assert parse_tool_approval_action("approve_once") == "approve_once"
    assert parse_tool_approval_action("approve_exact") == "approve_exact"
    assert parse_tool_approval_action("approve_prefix") == "approve_prefix"
    assert parse_tool_approval_action("deny") == "deny"


def test_parse_tool_approval_action_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="Unsupported action: later"):
        parse_tool_approval_action("later")


@pytest.mark.asyncio
async def test_run_event_publisher_async_tolerates_publish_failure() -> None:
    class _FailingAsyncRunEventHub:
        async def publish_async(self, event: RunEvent) -> None:
            _ = event
            raise RuntimeError("publish failed")

    publisher = RunEventPublisher(
        run_event_hub=cast(RunEventHub, _FailingAsyncRunEventHub()),
        get_runtime=lambda _run_id: None,
        get_run_runtime_repo=lambda: None,
        get_notification_service=lambda: None,
    )

    await publisher.safe_publish_run_event_async(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        ),
        failure_event="run.event.publish_failed",
    )


def test_execute_native_generation_rejects_inline_media_output() -> None:
    class InlineMediaProvider(LLMProvider):
        @override
        async def generate_image(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
            return (
                InlineMediaContentPart(
                    modality=MediaModality.IMAGE,
                    mime_type="image/png",
                    base64_data="abc",
                ),
            )

    request = LLMRequest(
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        instance_id="instance-1",
        role_id="role-1",
        system_prompt="system",
        user_prompt="draw",
        run_kind=RunKind.GENERATE_IMAGE,
    )

    with pytest.raises(
        RuntimeError,
        match="Unsupported native generation output part kind: inline_media",
    ):
        asyncio.run(
            MediaRunExecutor.execute_native_generation(
                provider=InlineMediaProvider(),
                request=request,
            )
        )


def test_execute_native_generation_supports_audio_and_video_outputs() -> None:
    class NativeProvider(LLMProvider):
        @override
        async def generate_audio(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
            return (TextContentPart(text="audio-ready"),)

        @override
        async def generate_video(self, _request: LLMRequest) -> tuple[ContentPart, ...]:
            return (TextContentPart(text="video-ready"),)

    request = LLMRequest(
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        instance_id="instance-1",
        role_id="role-1",
        system_prompt="system",
        user_prompt="generate",
        run_kind=RunKind.GENERATE_AUDIO,
    )

    audio_output = asyncio.run(
        MediaRunExecutor.execute_native_generation(
            provider=NativeProvider(),
            request=request,
        )
    )
    video_output = asyncio.run(
        MediaRunExecutor.execute_native_generation(
            provider=NativeProvider(),
            request=request.model_copy(update={"run_kind": RunKind.GENERATE_VIDEO}),
        )
    )

    assert audio_output == (TextContentPart(text="audio-ready"),)
    assert video_output == (TextContentPart(text="video-ready"),)


def test_execute_native_generation_rejects_conversation_run_kind() -> None:
    request = LLMRequest(
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        instance_id="instance-1",
        role_id="role-1",
        system_prompt="system",
        user_prompt="chat",
        run_kind=RunKind.CONVERSATION,
    )

    with pytest.raises(
        RuntimeError,
        match="Unsupported native generation run kind: conversation",
    ):
        asyncio.run(
            MediaRunExecutor.execute_native_generation(
                provider=LLMProvider(),
                request=request,
            )
        )


def test_media_executor_prefers_target_generation_role_id() -> None:
    executor = _build_media_executor()

    role_id = executor.resolve_generation_role_id(
        IntentInput(
            session_id="session-1",
            target_role_id="image-role",
        )
    )

    assert role_id == "image-role"


def test_media_executor_prefers_topology_generation_role_id() -> None:
    executor = _build_media_executor(
        role_registry=cast(RoleRegistry, _FakeRoleRegistry())
    )

    role_id = executor.resolve_generation_role_id(
        IntentInput(
            session_id="session-1",
            topology=RunTopologySnapshot(
                session_mode=SessionMode.NORMAL,
                main_agent_role_id="main-role",
                normal_root_role_id="normal-role",
                coordinator_role_id="coordinator-role",
            ),
        )
    )

    assert role_id == "normal-role"


def test_media_executor_requires_role_registry_without_explicit_role() -> None:
    executor = _build_media_executor()

    with pytest.raises(
        RuntimeError,
        match="SessionRunService requires role_registry for media generation",
    ):
        executor.resolve_generation_role_id(IntentInput(session_id="session-1"))
