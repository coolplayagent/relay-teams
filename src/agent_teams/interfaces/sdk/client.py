from __future__ import annotations

from json import dumps
from pathlib import Path
from threading import Thread

from agent_teams.agents.instance_pool import InstancePool
from agent_teams.agents.meta_agent import MetaAgent
from agent_teams.coordination.coordinator import CoordinatorGraph
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.core.config import load_runtime_config
from agent_teams.core.enums import InjectionSource, RunEventType
from agent_teams.core.ids import new_trace_id
from agent_teams.core.models import (
    InjectionMessage,
    IntentInput,
    ModelEndpointConfig,
    RoleDefinition,
    RunEvent,
    RunResult,
    SubAgentInstance,
    TaskEnvelope,
    TaskRecord,
)
from agent_teams.events.event_bus import EventBus
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import EchoProvider, LLMProvider, OpenAICompatibleProvider
from agent_teams.roles.registry import RoleLoader
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.console import set_debug
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.registry.defaults import build_default_registry
from agent_teams.workflow.spec import WorkflowSpec


class AgentTeamsApp:
    def __init__(
        self,
        roles_dir: Path | None = None,
        db_path: Path | None = None,
        model_config: ModelEndpointConfig | None = None,
        config_dir: Path = Path('.agent_teams'),
        debug: bool = False,
    ) -> None:
        set_debug(debug)
        runtime = load_runtime_config(config_dir=config_dir, roles_dir=roles_dir, db_path=db_path)
        effective_model_config = model_config or runtime.model_endpoint

        role_registry = RoleLoader().load_all(runtime.paths.roles_dir)
        tool_registry = build_default_registry()
        for role in role_registry.list_roles():
            tool_registry.validate_known(role.tools)

        task_repo = TaskRepository(runtime.paths.db_path)
        shared_store = SharedStore(runtime.paths.db_path)
        event_bus = EventBus(runtime.paths.db_path)
        agent_repo = AgentInstanceRepository(runtime.paths.db_path)
        instance_pool = InstancePool()
        injection_manager = RunInjectionManager()
        run_event_hub = RunEventHub()

        prompt_builder = RuntimePromptBuilder()

        def provider_factory(role: RoleDefinition) -> LLMProvider:
            provider: LLMProvider
            if effective_model_config is None:
                provider = EchoProvider()
            else:
                provider = OpenAICompatibleProvider(
                    effective_model_config,
                    task_repo=task_repo,
                    instance_pool=instance_pool,
                    shared_store=shared_store,
                    event_bus=event_bus,
                    injection_manager=injection_manager,
                    run_event_hub=run_event_hub,
                    agent_repo=agent_repo,
                    workspace_root=Path.cwd(),
                    tool_registry=tool_registry,
                    allowed_tools=role.tools,
                    task_execution_service=task_execution_service,
                )
            return provider

        task_execution_service = TaskExecutionService(
            role_registry=role_registry,
            instance_pool=instance_pool,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_bus,
            agent_repo=agent_repo,
            prompt_builder=prompt_builder,
            provider_factory=provider_factory,
        )

        coordinator = CoordinatorGraph(
            role_registry=role_registry,
            instance_pool=instance_pool,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_bus,
            agent_repo=agent_repo,
            prompt_builder=prompt_builder,
            provider_factory=provider_factory,
            task_execution_service=task_execution_service,
        )
        self._meta_agent = MetaAgent(coordinator=coordinator)
        self._task_repo = task_repo
        self._instance_pool = instance_pool
        self._role_registry = role_registry
        self._workflows: list[WorkflowSpec] = []
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo

    def run_intent(self, intent: IntentInput) -> RunResult:
        run_id = new_trace_id().value
        self._injection_manager.activate(run_id)
        try:
            return self._meta_agent.handle_intent(intent, trace_id=run_id)
        finally:
            self._injection_manager.deactivate(run_id)

    def run_intent_stream(self, intent: IntentInput):
        run_id = new_trace_id().value
        queue = self._run_event_hub.subscribe(run_id)
        self._injection_manager.activate(run_id)
        self._run_event_hub.publish(
            RunEvent(
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_STARTED,
                payload_json=dumps({'session_id': intent.session_id}),
            )
        )

        def _worker() -> None:
            try:
                result = self._meta_agent.handle_intent(intent, trace_id=run_id)
                self._run_event_hub.publish(
                    RunEvent(
                        run_id=run_id,
                        trace_id=result.trace_id,
                        task_id=result.root_task_id,
                        event_type=RunEventType.RUN_COMPLETED,
                        payload_json=dumps(result.model_dump()),
                    )
                )
            except Exception as exc:
                self._run_event_hub.publish(
                    RunEvent(
                        run_id=run_id,
                        trace_id=run_id,
                        task_id=None,
                        event_type=RunEventType.RUN_FAILED,
                        payload_json=dumps({'error': str(exc)}),
                    )
                )
            finally:
                self._injection_manager.deactivate(run_id)

        Thread(target=_worker, daemon=True).start()

        while True:
            event = queue.get()
            yield event
            if event.event_type in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED):
                self._run_event_hub.unsubscribe_all(run_id)
                break

    def inject_message(self, run_id: str, source: InjectionSource, content: str) -> InjectionMessage:
        running = self._agent_repo.list_running(run_id)
        if not running:
            raise KeyError(f'No RUNNING agent for run_id={run_id}')

        created: InjectionMessage | None = None
        for record in running:
            created = self._injection_manager.enqueue(
                run_id=run_id,
                recipient_instance_id=record.instance_id,
                source=source,
                content=content,
            )
            self._run_event_hub.publish(
                RunEvent(
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=None,
                    event_type=RunEventType.INJECTION_ENQUEUED,
                    payload_json=created.model_dump_json(),
                )
            )

        if created is None:
            raise KeyError(f'No RUNNING agent for run_id={run_id}')
        return created

    def create_workflow(self, spec: WorkflowSpec) -> str:
        self._workflows.append(spec)
        return spec.workflow_id

    def submit_task(self, task: TaskEnvelope) -> str:
        self._task_repo.create(task)
        return task.task_id

    def query_task(self, task_id: str) -> TaskRecord:
        return self._task_repo.get(task_id)

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        return self._task_repo.list_all()

    def create_subagent(self, role_id: str) -> SubAgentInstance:
        return self._instance_pool.create_subagent(role_id)

    def list_roles(self) -> tuple[RoleDefinition, ...]:
        return self._role_registry.list_roles()
