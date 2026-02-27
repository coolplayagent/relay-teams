from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from json import dumps
from pathlib import Path
from typing import TYPE_CHECKING

from agent_teams.core.enums import RunEventType
from agent_teams.core.models import ModelEndpointConfig, RunEvent
from agent_teams.runtime.console import close_model_stream, is_debug, log_debug, log_model_output, log_model_stream_chunk
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.tools.agent_builder import build_collaboration_agent
from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.runtime import ToolDeps

if TYPE_CHECKING:
    from agent_teams.coordination.task_execution_service import TaskExecutionService


@dataclass(frozen=True)
class LLMRequest:
    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    instance_id: str
    role_id: str
    system_prompt: str
    user_prompt: str


class LLMProvider:
    def generate(self, request: LLMRequest) -> str:
        raise NotImplementedError


class EchoProvider(LLMProvider):
    def generate(self, request: LLMRequest) -> str:
        return f'ECHO: {request.user_prompt}'


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo,
        instance_pool,
        shared_store,
        event_bus,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        workspace_root: Path,
        tool_registry: ToolRegistry,
        allowed_tools: tuple[str, ...],
        task_execution_service: TaskExecutionService,
    ) -> None:
        self._config = config
        self._task_repo = task_repo
        self._instance_pool = instance_pool
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo
        self._workspace_root = workspace_root
        self._tool_registry = tool_registry
        self._allowed_tools = allowed_tools
        self._task_execution_service = task_execution_service

    def generate(self, request: LLMRequest) -> str:
        tool_rules = f'Available tools: {", ".join(self._allowed_tools)}.'
        if is_debug():
            log_debug(
                f'[llm:start] role={request.role_id} run={request.run_id} '
                f'task={request.task_id} instance={request.instance_id}'
            )
        self._run_event_hub.publish(
            RunEvent(
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                event_type=RunEventType.MODEL_STEP_STARTED,
                payload_json='{}',
            )
        )
        agent = build_collaboration_agent(
            model_name=self._config.model,
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            system_prompt=f'{request.system_prompt}\n\n{tool_rules}',
            allowed_tools=self._allowed_tools,
            tool_registry=self._tool_registry,
        )
        deps = ToolDeps(
            task_repo=self._task_repo,
            instance_pool=self._instance_pool,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            workspace_root=self._workspace_root,
            run_id=request.run_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            task_execution_service=self._task_execution_service,
        )
        printed_any = False
        emitted_text_chunks: list[str] = []

        async def _event_stream_handler(_ctx, events: AsyncIterable[object]) -> None:
            nonlocal printed_any
            async for event in events:
                kind = getattr(event, 'event_kind', None)
                text: str | None = None
                if kind == 'part_start':
                    part = getattr(event, 'part', None)
                    maybe = getattr(part, 'content', None)
                    if isinstance(maybe, str) and maybe:
                        text = maybe
                elif kind == 'part_delta':
                    delta = getattr(event, 'delta', None)
                    maybe = getattr(delta, 'content_delta', None)
                    if isinstance(maybe, str) and maybe:
                        text = maybe

                if not text:
                    continue

                if is_debug():
                    print(text, end='', flush=True)
                else:
                    log_model_stream_chunk(request.role_id, text)
                printed_any = True
                emitted_text_chunks.append(text)
                self._run_event_hub.publish(
                    RunEvent(
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        event_type=RunEventType.TEXT_DELTA,
                        payload_json=dumps({'text': text}),
                    )
                )

        result = agent.run_sync(
            request.user_prompt,
            deps=deps,
            event_stream_handler=_event_stream_handler,
        )
        if printed_any and is_debug():
            print()
        if printed_any and not is_debug():
            close_model_stream()

        text = self._extract_text(result.response)
        if not text and emitted_text_chunks:
            text = ''.join(emitted_text_chunks)
        elif text and not emitted_text_chunks:
            self._run_event_hub.publish(
                RunEvent(
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    event_type=RunEventType.TEXT_DELTA,
                    payload_json=dumps({'text': text}),
                )
            )
        if text and not printed_any:
            log_model_output(request.role_id, text)
        self._run_event_hub.publish(
            RunEvent(
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                event_type=RunEventType.MODEL_STEP_FINISHED,
                payload_json='{}',
            )
        )
        if is_debug():
            log_debug(
                f'[llm:done] role={request.role_id} run={request.run_id} '
                f'task={request.task_id} chars={len(text)}'
            )
        return text

    def _extract_text(self, response: object) -> str:
        parts = getattr(response, 'parts', None)
        if isinstance(parts, list):
            texts: list[str] = []
            for part in parts:
                content = getattr(part, 'content', None)
                if isinstance(content, str) and content:
                    texts.append(content)
            if texts:
                return ''.join(texts)
        return str(response)
