from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from threading import Thread
import uuid

from pydantic_ai.messages import ModelMessagesTypeAdapter

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.agents.core.meta_agent import MetaAgent
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
    SessionRecord,
    SubAgentInstance,
    TaskEnvelope,
    TaskRecord,
)
from agent_teams.state.event_log import EventLog
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import (
    EchoProvider,
    LLMProvider,
    OpenAICompatibleProvider,
)
from agent_teams.roles.registry import RoleLoader
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.console import set_debug
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.defaults import build_default_registry
from agent_teams.workflow.spec import WorkflowSpec


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent


class AgentTeamsApp:
    def __init__(
        self,
        roles_dir: Path | None = None,
        db_path: Path | None = None,
        model_config: ModelEndpointConfig | None = None,
        config_dir: Path = _get_project_root() / ".agent_teams",
        debug: bool = False,
    ) -> None:
        set_debug(debug)
        runtime = load_runtime_config(
            config_dir=config_dir, roles_dir=roles_dir, db_path=db_path
        )
        effective_model_config = model_config or runtime.model_endpoint

        role_registry = RoleLoader().load_all(runtime.paths.roles_dir)
        tool_registry = build_default_registry()
        for role in role_registry.list_roles():
            tool_registry.validate_known(role.tools)

        task_repo = TaskRepository(runtime.paths.db_path)
        shared_store = SharedStore(runtime.paths.db_path)
        event_log = EventLog(runtime.paths.db_path)
        agent_repo = AgentInstanceRepository(runtime.paths.db_path)
        message_repo = MessageRepository(runtime.paths.db_path)
        session_repo = SessionRepository(runtime.paths.db_path)
        instance_pool = InstancePool.from_repo(agent_repo)
        injection_manager = RunInjectionManager()
        run_event_hub = RunEventHub(event_log=event_log)

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
                    event_bus=event_log,
                    injection_manager=injection_manager,
                    run_event_hub=run_event_hub,
                    agent_repo=agent_repo,
                    workspace_root=Path.cwd(),
                    tool_registry=tool_registry,
                    allowed_tools=role.tools,
                    message_repo=message_repo,
                    role_registry=role_registry,
                    task_execution_service=task_execution_service,
                )
            return provider

        task_execution_service = TaskExecutionService(
            role_registry=role_registry,
            instance_pool=instance_pool,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_log,
            agent_repo=agent_repo,
            prompt_builder=prompt_builder,
            provider_factory=provider_factory,
        )

        coordinator = CoordinatorGraph(
            role_registry=role_registry,
            instance_pool=instance_pool,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_log,
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
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._event_log = event_log
        self._shared_store = shared_store

    def _ensure_session(self, session_id: str | None) -> str:
        if not session_id:
            new_id = f"session-{uuid.uuid4().hex[:8]}"
            self._session_repo.create(session_id=new_id)
            return new_id
        try:
            # check if exists
            self._session_repo.get(session_id)
            return session_id
        except KeyError:
            # create if not found
            self._session_repo.create(session_id=session_id)
            return session_id

    def run_intent(self, intent: IntentInput) -> RunResult:
        intent.session_id = self._ensure_session(intent.session_id)
        run_id = new_trace_id().value
        self._injection_manager.activate(run_id)
        try:
            return self._meta_agent.handle_intent(intent, trace_id=run_id)
        finally:
            self._injection_manager.deactivate(run_id)

    def run_intent_stream(self, intent: IntentInput):
        intent.session_id = self._ensure_session(intent.session_id)
        run_id = new_trace_id().value
        queue = self._run_event_hub.subscribe(run_id)
        self._injection_manager.activate(run_id)
        self._run_event_hub.publish(
            RunEvent(
                session_id=intent.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_STARTED,
                payload_json=dumps({"session_id": intent.session_id}),
            )
        )

        def _worker() -> None:
            try:
                result = self._meta_agent.handle_intent(intent, trace_id=run_id)
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=intent.session_id,
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
                        session_id=intent.session_id,
                        run_id=run_id,
                        trace_id=run_id,
                        task_id=None,
                        event_type=RunEventType.RUN_FAILED,
                        payload_json=dumps({"error": str(exc)}),
                    )
                )
            finally:
                self._injection_manager.deactivate(run_id)

        Thread(target=_worker, daemon=True).start()

        while True:
            event = queue.get()
            yield event
            if event.event_type in (
                RunEventType.RUN_COMPLETED,
                RunEventType.RUN_FAILED,
            ):
                self._run_event_hub.unsubscribe_all(run_id)
                break

    def inject_message(
        self, run_id: str, source: InjectionSource, content: str
    ) -> InjectionMessage:
        running = self._agent_repo.list_running(run_id)
        if not running:
            raise KeyError(f"No RUNNING agent for run_id={run_id}")

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
                    session_id=record.session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=None,
                    event_type=RunEventType.INJECTION_ENQUEUED,
                    payload_json=created.model_dump_json(),
                )
            )

        if created is None:
            raise KeyError(f"No RUNNING agent for run_id={run_id}")
        return created

    def create_workflow(self, spec: WorkflowSpec) -> str:
        self._workflows.append(spec)
        return spec.workflow_id

    def create_session(
        self, session_id: str | None = None, metadata: dict[str, str] | None = None
    ) -> SessionRecord:
        if not session_id:
            session_id = f"session-{uuid.uuid4().hex[:8]}"
        return self._session_repo.create(session_id=session_id, metadata=metadata)

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        self._session_repo.update_metadata(session_id, metadata)

    def delete_session(self, session_id: str) -> None:
        # Verify it exists first
        self._session_repo.get(session_id)
        
        # Gather scoped entities to delete from shared_store
        tasks = self._task_repo.list_by_session(session_id)
        agents = self._agent_repo.list_by_session(session_id)
        
        task_ids = [t.envelope.task_id for t in tasks]
        instance_ids = [a.instance_id for a in agents]

        # Delete dependent data
        self._shared_store.delete_by_session(session_id, task_ids, instance_ids)
        self._message_repo.delete_by_session(session_id)
        self._event_log.delete_by_session(session_id)
        self._task_repo.delete_by_session(session_id)
        self._agent_repo.delete_by_session(session_id)
        
        # Finally delete the session itself
        self._session_repo.delete(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        return self._session_repo.get(session_id)

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        return self._session_repo.list_all()

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

    def list_agents_in_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        return self._agent_repo.list_by_session(session_id)

    def get_agent_messages(self, instance_id: str):
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        import json
        history = self._message_repo.get_history(instance_id)
        # model_dump() is not on the base ModelMessage directly for the Union.
        # Use TypeAdapter to properly dump it.
        json_bytes = ModelMessagesTypeAdapter.dump_json(history)
        return json.loads(json_bytes)
        
    def get_global_events(self, session_id: str) -> list[dict]:
        events = self._event_log.list_by_session(session_id)
        return list(events)

    def get_session_messages(self, session_id: str) -> list[dict]:
        import json
        return self._message_repo.get_messages_by_session(session_id)

    def get_session_workflows(self, session_id: str) -> list[dict]:
        import json
        from agent_teams.core.enums import ScopeType
        from agent_teams.core.models import ScopeRef

        tasks = self._task_repo.list_by_session(session_id)
        workflows = []
        for t in tasks:
            scope = ScopeRef(scope_type=ScopeType.TASK, scope_id=t.envelope.task_id)
            obj = self._shared_store.get_state(scope, "workflow_graph")
            if obj:
                workflows.append(json.loads(obj))
        return workflows
