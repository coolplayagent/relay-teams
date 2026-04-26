# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from json import dumps

from pydantic_ai.messages import (
    BinaryContent,
    FilePart,
    ModelResponse,
    TextPart,
)

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import create_subagent_instance
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.media import (
    ContentPart,
    MediaAssetService,
    MediaRefContentPart,
    TextContentPart,
    content_parts_to_text,
)
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_event_publisher import RunEventPublisher
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RunEvent,
    RunKind,
    RunResult,
)
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimePhase, RunRuntimeStatus
from relay_teams.sessions.runs.run_terminal_results import RunTerminalResultService
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.workspace import build_conversation_id


class MediaRunExecutor:
    def __init__(
        self,
        *,
        session_repo: SessionRepository,
        get_role_registry: Callable[[], RoleRegistry | None],
        provider_factory: Callable[[RoleDefinition, str | None], LLMProvider],
        require_agent_repo: Callable[[], AgentInstanceRepository],
        require_task_repo: Callable[[], TaskRepository],
        require_message_repo: Callable[[], MessageRepository],
        require_media_asset_service: Callable[[], MediaAssetService],
        event_publisher: RunEventPublisher,
        terminal_results: RunTerminalResultService,
    ) -> None:
        self._session_repo = session_repo
        self._get_role_registry = get_role_registry
        self._provider_factory = provider_factory
        self._require_agent_repo = require_agent_repo
        self._require_task_repo = require_task_repo
        self._require_message_repo = require_message_repo
        self._require_media_asset_service = require_media_asset_service
        self._event_publisher = event_publisher
        self._terminal_results = terminal_results

    async def run_media_generation(
        self,
        *,
        run_id: str,
        intent: IntentInput,
    ) -> RunResult:
        session = self._session_repo.get(intent.session_id)
        role_id = self.resolve_generation_role_id(intent)
        role_registry = self._require_role_registry()
        role = role_registry.get(role_id)
        provider = self._provider_factory(role, intent.session_id)
        conversation_id = build_conversation_id(intent.session_id, role_id)
        instance = create_subagent_instance(
            role_id,
            workspace_id=session.workspace_id,
            session_id=intent.session_id,
            conversation_id=conversation_id,
        )
        root_task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=intent.session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id=role_id,
            objective=intent.intent or intent.run_kind.value,
            verification=VerificationPlan(checklist=("generated_media",)),
        )
        agent_repo = self._require_agent_repo()
        task_repo = self._require_task_repo()
        agent_repo.upsert_instance(
            run_id=run_id,
            trace_id=run_id,
            session_id=intent.session_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            workspace_id=session.workspace_id,
            conversation_id=conversation_id,
            status=InstanceStatus.RUNNING,
        )
        _ = task_repo.create(root_task)
        task_repo.update_status(
            root_task.task_id,
            TaskStatus.RUNNING,
            assigned_instance_id=instance.instance_id,
        )
        self._event_publisher.safe_runtime_update(
            run_id,
            root_task_id=root_task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
            active_instance_id=instance.instance_id,
            active_task_id=root_task.task_id,
            active_role_id=role_id,
            active_subagent_instance_id=None,
            last_error=None,
        )
        await self._event_publisher.safe_publish_run_event_async(
            RunEvent(
                session_id=intent.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                event_type=RunEventType.MODEL_STEP_STARTED,
                payload_json=dumps(
                    {"role_id": role_id, "instance_id": instance.instance_id}
                ),
            ),
            failure_event="run.event.publish_failed",
        )
        await self.publish_generation_progress_async(
            run_id=run_id,
            session_id=intent.session_id,
            task_id=root_task.task_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            run_kind=intent.run_kind.value,
            phase="started",
            progress=0.0,
            preview_asset_id=None,
        )
        request = LLMRequest(
            run_id=run_id,
            trace_id=run_id,
            task_id=root_task.task_id,
            session_id=intent.session_id,
            workspace_id=session.workspace_id,
            conversation_id=conversation_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            system_prompt="",
            user_prompt=intent.intent or None,
            input=intent.input,
            run_kind=intent.run_kind,
            generation_config=intent.generation_config,
            thinking=intent.thinking,
        )
        try:
            output = await self.execute_native_generation(
                provider=provider,
                request=request,
            )
            if not output:
                raise RuntimeError("Provider returned no media output")
            self.append_media_output_message(request=request, output=output)
            await self.publish_output_delta_async(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                output=output,
            )
            preview_asset_id = next(
                (
                    part.asset_id
                    for part in output
                    if isinstance(part, MediaRefContentPart)
                ),
                None,
            )
            await self.publish_generation_progress_async(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                run_kind=intent.run_kind.value,
                phase="completed",
                progress=1.0,
                preview_asset_id=preview_asset_id,
            )
            await self._event_publisher.safe_publish_run_event_async(
                RunEvent(
                    session_id=intent.session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=root_task.task_id,
                    instance_id=instance.instance_id,
                    role_id=role_id,
                    event_type=RunEventType.MODEL_STEP_FINISHED,
                    payload_json=dumps(
                        {"role_id": role_id, "instance_id": instance.instance_id}
                    ),
                ),
                failure_event="run.event.publish_failed",
            )
            task_repo.update_status(
                root_task.task_id,
                TaskStatus.COMPLETED,
                assigned_instance_id=instance.instance_id,
                result=content_parts_to_text(output),
            )
            agent_repo.mark_status(instance.instance_id, InstanceStatus.COMPLETED)
            return RunResult(
                trace_id=run_id,
                root_task_id=root_task.task_id,
                status="completed",
                completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
                output=output,
            )
        except Exception as exc:
            result = self._terminal_results.build_completed_error_run_result(
                run_id=run_id,
                session_id=intent.session_id,
                root_task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                conversation_id=conversation_id,
                workspace_id=session.workspace_id,
                error_code="native_generation_failed",
                error_message=str(exc),
            )
            task_repo.update_status(
                root_task.task_id,
                TaskStatus.COMPLETED,
                assigned_instance_id=instance.instance_id,
                result=result.output_text,
                error_message=result.error_message,
            )
            agent_repo.mark_status(instance.instance_id, InstanceStatus.COMPLETED)
            await self.publish_generation_progress_async(
                run_id=run_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                role_id=role_id,
                run_kind=intent.run_kind.value,
                phase="completed",
                progress=1.0,
                preview_asset_id=None,
            )
            return result

    @staticmethod
    async def execute_native_generation(
        *,
        provider: LLMProvider,
        request: LLMRequest,
    ) -> tuple[TextContentPart | MediaRefContentPart, ...]:
        if request.run_kind == RunKind.GENERATE_IMAGE:
            return MediaRunExecutor._native_generation_output(
                await provider.generate_image(request)
            )
        if request.run_kind == RunKind.GENERATE_AUDIO:
            return MediaRunExecutor._native_generation_output(
                await provider.generate_audio(request)
            )
        if request.run_kind == RunKind.GENERATE_VIDEO:
            return MediaRunExecutor._native_generation_output(
                await provider.generate_video(request)
            )
        raise RuntimeError(
            f"Unsupported native generation run kind: {request.run_kind.value}"
        )

    @staticmethod
    def _native_generation_output(
        output: tuple[ContentPart, ...],
    ) -> tuple[TextContentPart | MediaRefContentPart, ...]:
        native_output: list[TextContentPart | MediaRefContentPart] = []
        for part in output:
            if isinstance(part, TextContentPart | MediaRefContentPart):
                native_output.append(part)
                continue
            raise RuntimeError(
                f"Unsupported native generation output part kind: {part.kind}"
            )
        return tuple(native_output)

    def resolve_generation_role_id(self, intent: IntentInput) -> str:
        if intent.target_role_id is not None and intent.target_role_id.strip():
            return intent.target_role_id
        role_registry = self._require_role_registry()
        if intent.topology is not None and intent.topology.normal_root_role_id.strip():
            return intent.topology.normal_root_role_id
        return role_registry.get_main_agent_role_id()

    async def publish_generation_progress_async(
        self,
        *,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        run_kind: str,
        phase: str,
        progress: float,
        preview_asset_id: str | None,
    ) -> None:
        await self._event_publisher.safe_publish_run_event_async(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.GENERATION_PROGRESS,
                payload_json=dumps(
                    {
                        "run_kind": run_kind,
                        "phase": phase,
                        "progress": progress,
                        "preview_asset_id": preview_asset_id,
                    }
                ),
            ),
            failure_event="run.event.publish_failed",
        )

    async def publish_output_delta_async(
        self,
        *,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        output: tuple[TextContentPart | MediaRefContentPart, ...],
    ) -> None:
        payload = {
            "output": [part.model_dump(mode="json") for part in output],
            "role_id": role_id,
            "instance_id": instance_id,
        }
        await self._event_publisher.safe_publish_run_event_async(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.OUTPUT_DELTA,
                payload_json=dumps(payload),
            ),
            failure_event="run.event.publish_failed",
        )

    def append_media_output_message(
        self,
        *,
        request: LLMRequest,
        output: tuple[TextContentPart | MediaRefContentPart, ...],
    ) -> None:
        message_repo = self._require_message_repo()
        media_asset_service = self._require_media_asset_service()
        response_parts: list[TextPart | FilePart] = []
        for part in output:
            if isinstance(part, TextContentPart):
                response_parts.append(TextPart(content=part.text))
                continue
            record = media_asset_service.get_asset(part.asset_id)
            try:
                file_path, _media_type = media_asset_service.get_asset_file(
                    session_id=record.session_id,
                    asset_id=record.asset_id,
                )
            except FileNotFoundError:
                response_parts.append(TextPart(content=part.url))
                continue
            response_parts.append(
                FilePart(
                    content=BinaryContent(
                        data=file_path.read_bytes(),
                        media_type=record.mime_type,
                    )
                )
            )
        if not response_parts:
            return
        message_repo.append(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[
                ModelResponse(parts=response_parts, model_name="media_generation")
            ],
        )

    def _require_role_registry(self) -> RoleRegistry:
        role_registry = self._get_role_registry()
        if role_registry is None:
            raise RuntimeError(
                "SessionRunService requires role_registry for media generation"
            )
        return role_registry
