from __future__ import annotations

from dataclasses import dataclass
from json import dumps
from pathlib import Path

from agent_teams.core.enums import RunEventType
from agent_teams.core.models import ModelEndpointConfig, RunEvent
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.tools.agent_builder import build_collaboration_agent
from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.runtime import ToolDeps


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

    def generate(self, request: LLMRequest) -> str:
        tool_rules = f'Available tools: {", ".join(self._allowed_tools)}.'
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
        )
        result = agent.run_sync(request.user_prompt, deps=deps)
        text = self._extract_text(result.response)
        self._run_event_hub.publish(
            RunEvent(
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                event_type=RunEventType.TEXT_DELTA,
                payload_json=dumps({'text': text}),
            )
        )
        self._run_event_hub.publish(
            RunEvent(
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                event_type=RunEventType.MODEL_STEP_FINISHED,
                payload_json='{}',
            )
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
