# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import cast, override

import pytest

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.media import (
    ContentPart,
    InlineMediaContentPart,
    MediaModality,
    TextContentPart,
)
from relay_teams.media.asset_service import MediaAssetService
from relay_teams.notifications import (
    NotificationContext,
    NotificationService,
    NotificationType,
)
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.media_run_executor import MediaRunExecutor
import relay_teams.sessions.runs.run_event_publisher as run_event_publisher_module
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
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
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


@pytest.mark.asyncio
async def test_run_event_publisher_async_uses_async_notification_emit() -> None:
    class _AsyncNotificationService:
        def __init__(self) -> None:
            self.calls: list[NotificationContext] = []

        def emit(
            self,
            *,
            notification_type: NotificationType,
            title: str,
            body: str,
            context: NotificationContext,
            dedupe_key: str | None = None,
        ) -> bool:
            _ = notification_type
            _ = title
            _ = body
            _ = context
            _ = dedupe_key
            raise AssertionError("sync emit must not be used")

        async def emit_async(
            self,
            *,
            notification_type: NotificationType,
            title: str,
            body: str,
            context: NotificationContext,
            dedupe_key: str | None = None,
        ) -> bool:
            _ = notification_type
            _ = title
            _ = body
            _ = dedupe_key
            self.calls.append(context)
            return True

    notification_service = _AsyncNotificationService()
    publisher = RunEventPublisher(
        run_event_hub=RunEventHub(),
        get_runtime=lambda _run_id: None,
        get_run_runtime_repo=lambda: None,
        get_notification_service=lambda: cast(
            NotificationService, notification_service
        ),
    )

    await publisher.emit_notification_async(
        notification_type=NotificationType.RUN_COMPLETED,
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        title="Run Completed",
        body="Done",
        session_mode="orchestration",
        run_kind="generate_image",
    )

    assert notification_service.calls == [
        NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            session_mode="orchestration",
            run_kind="generate_image",
        )
    ]


@pytest.mark.asyncio
async def test_run_event_publisher_async_runtime_update_does_not_call_sync() -> None:
    class _RuntimeRepo:
        def __init__(self) -> None:
            self.async_changes: dict[str, object] | None = None

        def update(self, run_id: str, **changes: object) -> None:
            _ = (run_id, changes)
            raise AssertionError("sync update should not be called")

        async def update_async(self, run_id: str, **changes: object) -> None:
            self.async_changes = {"run_id": run_id, **changes}

    runtime_repo = _RuntimeRepo()
    publisher = RunEventPublisher(
        run_event_hub=RunEventHub(),
        get_runtime=lambda _run_id: None,
        get_run_runtime_repo=lambda: cast(RunRuntimeRepository, runtime_repo),
        get_notification_service=lambda: None,
    )

    await publisher.safe_runtime_update_async("run-1", phase="terminal")

    assert runtime_repo.async_changes == {"run_id": "run-1", "phase": "terminal"}


@pytest.mark.asyncio
async def test_run_event_publisher_runtime_update_timeout_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SlowRuntimeRepo:
        def update(self, run_id: str, **changes: object) -> None:
            _ = (run_id, changes)
            raise AssertionError("sync update should not be called")

        async def update_async(self, run_id: str, **changes: object) -> None:
            _ = (run_id, changes)
            await asyncio.Event().wait()

    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS",
        0,
    )
    publisher = RunEventPublisher(
        run_event_hub=RunEventHub(),
        get_runtime=lambda _run_id: None,
        get_run_runtime_repo=lambda: cast(RunRuntimeRepository, _SlowRuntimeRepo()),
        get_notification_service=lambda: None,
    )

    await publisher.safe_runtime_update_async("run-1", phase="terminal")


@pytest.mark.asyncio
async def test_run_event_publisher_runtime_update_retries_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RetryRuntimeRepo:
        def __init__(self) -> None:
            self.attempts = 0
            self.retry_completed = asyncio.Event()
            self.async_changes: dict[str, object] | None = None

        def update(self, run_id: str, **changes: object) -> None:
            _ = (run_id, changes)
            raise AssertionError("sync update should not be called")

        async def update_async(self, run_id: str, **changes: object) -> None:
            self.attempts += 1
            if self.attempts == 1:
                await asyncio.Event().wait()
            self.async_changes = {"run_id": run_id, **changes}
            self.retry_completed.set()

    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS",
        2,
    )
    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_RETRY_DELAY_SECONDS",
        0.01,
    )
    runtime_repo = _RetryRuntimeRepo()
    publisher = RunEventPublisher(
        run_event_hub=RunEventHub(),
        get_runtime=lambda _run_id: None,
        get_run_runtime_repo=lambda: cast(RunRuntimeRepository, runtime_repo),
        get_notification_service=lambda: None,
    )

    await publisher.safe_runtime_update_async("run-1", phase="terminal")
    await asyncio.wait_for(runtime_repo.retry_completed.wait(), timeout=1)

    assert runtime_repo.attempts == 2
    assert runtime_repo.async_changes == {"run_id": "run-1", "phase": "terminal"}


@pytest.mark.asyncio
async def test_run_event_publisher_runtime_update_retry_skips_terminal_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RetryRuntimeRepo:
        def __init__(self) -> None:
            self.attempts = 0
            self.retry_attempted = asyncio.Event()

        def update(self, run_id: str, **changes: object) -> None:
            _ = (run_id, changes)
            raise AssertionError("sync update should not be called")

        async def update_async(self, run_id: str, **changes: object) -> None:
            self.attempts += 1
            if self.attempts == 1:
                await asyncio.Event().wait()
            self.retry_attempted.set()
            _ = (run_id, changes)

    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS",
        2,
    )
    monkeypatch.setattr(
        run_event_publisher_module,
        "_RUN_RUNTIME_UPDATE_RETRY_DELAY_SECONDS",
        0.01,
    )
    runtime = RunRuntimeRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
    )
    runtime_repo = _RetryRuntimeRepo()
    publisher = RunEventPublisher(
        run_event_hub=RunEventHub(),
        get_runtime=lambda _run_id: runtime,
        get_run_runtime_repo=lambda: cast(RunRuntimeRepository, runtime_repo),
        get_notification_service=lambda: None,
    )

    await publisher.safe_runtime_update_async(
        "run-1",
        status=RunRuntimeStatus.RUNNING,
    )
    runtime.status = RunRuntimeStatus.COMPLETED
    await asyncio.sleep(0.05)

    assert runtime_repo.retry_attempted.is_set() is False
    assert runtime_repo.attempts == 1


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
