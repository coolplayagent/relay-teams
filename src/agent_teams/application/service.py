from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import uuid
from typing import Callable

from agent_teams.agents.core.meta_agent import MetaAgent
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.application.bootstrap import build_service_components
from agent_teams.application.config_manager import ConfigManager
from agent_teams.application.provider_runtime import (
    create_provider_factory,
    create_task_execution_service,
)
from agent_teams.application.run_manager import RunManager
from agent_teams.application.session_service import SessionService
from agent_teams.application.task_service import TaskService
from agent_teams.application.workflow_orchestration_service import (
    DispatchAction,
    WorkflowOrchestrationService,
    WorkflowTaskSpecInput,
    WorkflowType,
)
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.core.config import load_runtime_config, RuntimeConfig
from agent_teams.core.types import JsonObject
from agent_teams.core.enums import InjectionSource
from agent_teams.core.models import (
    InjectionMessage,
    AgentRuntimeRecord,
    IntentInput,
    RoleDefinition,
    RunResult,
    SessionRecord,
    SubAgentInstance,
    TaskEnvelope,
    TaskRecord,
)
from agent_teams.application.rounds_projection import (
    collect_pending_stream_snapshots,
    collect_pending_tool_approvals,
    find_round_by_run_id,
    paginate_rounds,
)
from agent_teams.mcp.registry import McpRegistry
from agent_teams.providers.llm import LLMProvider
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.console import set_debug
from agent_teams.runtime.gate_manager import GateManager
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalManager
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository, RunTokenUsage, SessionTokenUsage
from agent_teams.tools.policy import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry
from agent_teams.workflow.spec import WorkflowSpec


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent


class AgentTeamsService:
    def __init__(
        self,
        roles_dir: Path | None = None,
        db_path: Path | None = None,
        config_dir: Path | None = None,
        debug: bool = False,
    ) -> None:
        if config_dir is None:
            config_dir = _get_project_root() / ".agent_teams"
        set_debug(debug)
        components = build_service_components(
            config_dir=config_dir,
            roles_dir=roles_dir,
            db_path=db_path,
        )

        self._meta_agent: MetaAgent = components.meta_agent
        self._task_repo: TaskRepository = components.task_repo
        self._instance_pool: InstancePool = components.instance_pool
        self._role_registry: RoleRegistry = components.role_registry
        self._workflows: list[WorkflowSpec] = []
        self._injection_manager: RunInjectionManager = components.injection_manager
        self._run_control_manager: RunControlManager = components.run_control_manager
        self._run_event_hub: RunEventHub = components.run_event_hub
        self._gate_manager: GateManager = components.gate_manager
        self._tool_approval_manager: ToolApprovalManager = (
            components.tool_approval_manager
        )
        self._tool_approval_policy: ToolApprovalPolicy = components.tool_approval_policy
        self._agent_repo: AgentInstanceRepository = components.agent_repo
        self._session_repo: SessionRepository = components.session_repo
        self._message_repo: MessageRepository = components.message_repo
        self._event_log: EventLog = components.event_log
        self._shared_store: SharedStore = components.shared_store
        self._token_usage_repo: TokenUsageRepository = components.token_usage_repo
        self._config_dir: Path = config_dir
        self._config_manager: ConfigManager = components.config_manager
        self._roles_dir: Path = components.runtime.paths.roles_dir
        self._db_path: Path = components.runtime.paths.db_path
        self._runtime: RuntimeConfig = components.runtime
        self._mcp_registry: McpRegistry = components.mcp_registry
        self._skill_registry: SkillRegistry = components.skill_registry
        self._tool_registry: ToolRegistry = components.tool_registry
        self._provider_factory: Callable[[RoleDefinition], LLMProvider] = (
            components.provider_factory
        )
        self._task_execution_service: TaskExecutionService = (
            components.task_execution_service
        )
        self._run_manager: RunManager = RunManager(
            meta_agent=self._meta_agent,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
        )
        self._session_service: SessionService = SessionService(
            session_repo=self._session_repo,
            task_repo=self._task_repo,
            agent_repo=self._agent_repo,
            shared_store=self._shared_store,
            message_repo=self._message_repo,
            event_log=self._event_log,
        )
        self._task_service: TaskService = TaskService(
            task_repo=self._task_repo,
            instance_pool=self._instance_pool,
            role_registry=self._role_registry,
        )
        self._workflow_orchestration_service: WorkflowOrchestrationService = (
            WorkflowOrchestrationService(
                task_repo=self._task_repo,
                shared_store=self._shared_store,
                role_registry=self._role_registry,
                instance_pool=self._instance_pool,
                agent_repo=self._agent_repo,
                task_execution_service=self._task_execution_service,
                injection_manager=self._injection_manager,
            )
        )

    def _ensure_session(self, session_id: str | None) -> str:
        if not session_id:
            new_id = f"session-{uuid.uuid4().hex[:8]}"
            _ = self._session_repo.create(session_id=new_id)
            return new_id
        try:
            # check if exists
            _ = self._session_repo.get(session_id)
            return session_id
        except KeyError:
            # create if not found
            _ = self._session_repo.create(session_id=session_id)
            return session_id

    def get_config_status(self) -> JsonObject:
        return {
            "model": {
                "loaded": True,
                "profiles": list(self._runtime.llm_profiles.keys()),
            },
            "mcp": {
                "loaded": True,
                "servers": list(self._mcp_registry.list_names()),
            },
            "skills": {
                "loaded": True,
                "skills": list(self._skill_registry.list_names()),
            },
        }

    def get_model_config(self) -> JsonObject:
        return self._config_manager.get_model_config()

    def get_model_profiles(self) -> dict[str, JsonObject]:
        return self._config_manager.get_model_profiles()

    def save_model_profile(self, name: str, profile: JsonObject) -> None:
        self._config_manager.save_model_profile(name, profile)
        self.reload_model_config()

    def delete_model_profile(self, name: str) -> None:
        self._config_manager.delete_model_profile(name)
        self.reload_model_config()

    def save_model_config(self, config: JsonObject) -> None:
        self._config_manager.save_model_config(config)
        self.reload_model_config()

    def reload_model_config(self) -> None:
        self._runtime = load_runtime_config(
            config_dir=self._config_dir,
            roles_dir=self._roles_dir,
            db_path=self._db_path,
        )
        self._recreate_task_execution_service()

    def _recreate_task_execution_service(self) -> None:
        def get_task_execution_service():
            return self._task_execution_service

        self._provider_factory = create_provider_factory(
            runtime=self._runtime,
            task_repo=self._task_repo,
            instance_pool=self._instance_pool,
            shared_store=self._shared_store,
            event_log=self._event_log,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            tool_registry=self._tool_registry,
            mcp_registry=self._mcp_registry,
            skill_registry=self._skill_registry,
            message_repo=self._message_repo,
            role_registry=self._role_registry,
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            tool_approval_policy=self._tool_approval_policy,
            get_task_execution_service=get_task_execution_service,
            token_usage_repo=self._token_usage_repo,
        )
        self._task_execution_service = create_task_execution_service(
            role_registry=self._role_registry,
            instance_pool=self._instance_pool,
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_log=self._event_log,
            agent_repo=self._agent_repo,
            message_repo=self._message_repo,
            provider_factory=self._provider_factory,
            injection_manager=self._injection_manager,
            run_control_manager=self._run_control_manager,
        )

        self._meta_agent.coordinator.task_execution_service = (
            self._task_execution_service
        )
        self._workflow_orchestration_service = WorkflowOrchestrationService(
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            role_registry=self._role_registry,
            instance_pool=self._instance_pool,
            agent_repo=self._agent_repo,
            task_execution_service=self._task_execution_service,
            injection_manager=self._injection_manager,
        )

    def reload_mcp_config(self) -> None:
        self._mcp_registry = self._config_manager.load_mcp_registry()
        for role in self._role_registry.list_roles():
            self._mcp_registry.validate_known(role.mcp_servers)

    def reload_skills_config(self) -> None:
        self._skill_registry = self._config_manager.load_skill_registry()
        for role in self._role_registry.list_roles():
            self._skill_registry.validate_known(role.skills)

    async def run_intent(self, intent: IntentInput) -> RunResult:
        return await self._run_manager.run_intent(
            intent,
            ensure_session=self._ensure_session,
        )

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        return self._run_manager.create_run(intent, ensure_session=self._ensure_session)

    def _ensure_run_started(self, run_id: str) -> None:
        self._run_manager.ensure_run_started(run_id)

    async def stream_run_events(self, run_id: str):
        async for event in self._run_manager.stream_run_events(run_id):
            yield event

    async def run_intent_stream(self, intent: IntentInput):
        async for event in self._run_manager.run_intent_stream(
            intent,
            ensure_session=self._ensure_session,
        ):
            yield event

    def inject_message(
        self, run_id: str, source: InjectionSource, content: str
    ) -> InjectionMessage:
        return self._run_manager.inject_message(run_id, source, content)

    def resolve_tool_approval(
        self, run_id: str, tool_call_id: str, action: str, feedback: str = ""
    ) -> None:
        self._run_manager.resolve_tool_approval(run_id, tool_call_id, action, feedback)

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        return self._run_manager.list_open_tool_approvals(run_id)

    def stop_run(self, run_id: str) -> None:
        self._run_manager.stop_run(run_id)

    def stop_subagent(self, run_id: str, instance_id: str) -> dict[str, str]:
        return self._run_manager.stop_subagent(run_id, instance_id)

    def inject_subagent_message(
        self,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        self._run_manager.inject_subagent_message(
            run_id=run_id,
            instance_id=instance_id,
            content=content,
        )

    def create_workflow(self, spec: WorkflowSpec) -> str:
        self._workflows.append(spec)
        return spec.workflow_id

    def create_session(
        self, session_id: str | None = None, metadata: dict[str, str] | None = None
    ) -> SessionRecord:
        return self._session_service.create_session(
            session_id=session_id, metadata=metadata
        )

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        self._session_service.update_session(session_id, metadata)

    def delete_session(self, session_id: str) -> None:
        self._session_service.delete_session(session_id)
        self._token_usage_repo.delete_by_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        return self._session_service.get_session(session_id)

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        return self._session_service.list_sessions()

    def submit_task(self, task: TaskEnvelope) -> str:
        return self._task_service.submit_task(task)

    def query_task(self, task_id: str) -> TaskRecord:
        return self._task_service.query_task(task_id)

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        return self._task_service.list_tasks()

    def create_subagent(self, role_id: str) -> SubAgentInstance:
        return self._task_service.create_subagent(role_id)

    def list_roles(self) -> tuple[RoleDefinition, ...]:
        return self._task_service.list_roles()

    def list_agents_in_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        return self._session_service.list_agents_in_session(session_id)

    def get_agent_messages(
        self, session_id: str, instance_id: str
    ) -> list[dict[str, object]]:
        return self._session_service.get_agent_messages(session_id, instance_id)

    def get_global_events(self, session_id: str) -> list[dict[str, object]]:
        return self._session_service.get_global_events(session_id)

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return self._session_service.get_session_messages(session_id)

    def get_session_workflows(self, session_id: str) -> list[dict[str, object]]:
        return self._session_service.get_session_workflows(session_id)

    def create_workflow_graph_for_run(
        self,
        *,
        run_id: str,
        objective: str,
        workflow_type: WorkflowType = "custom",
        tasks: list[WorkflowTaskSpecInput] | None = None,
    ) -> dict[str, object]:
        return self._workflow_orchestration_service.create_workflow_graph(
            run_id=run_id,
            objective=objective,
            workflow_type=workflow_type,
            tasks=tasks,
        )

    async def dispatch_tasks_for_run(
        self,
        *,
        run_id: str,
        workflow_id: str,
        action: DispatchAction,
        feedback: str = "",
        max_dispatch: int = 1,
    ) -> dict[str, object]:
        return await self._workflow_orchestration_service.dispatch_tasks(
            run_id=run_id,
            workflow_id=workflow_id,
            action=action,
            feedback=feedback,
            max_dispatch=max_dispatch,
        )

    def get_workflow_status_for_run(
        self, *, run_id: str, workflow_id: str
    ) -> dict[str, object]:
        return self._workflow_orchestration_service.get_workflow_status(
            run_id=run_id,
            workflow_id=workflow_id,
        )

    @staticmethod
    def _collect_pending_tool_approvals(
        parsed_events: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    ) -> dict[str, list[dict[str, str]]]:
        return collect_pending_tool_approvals(parsed_events)

    @staticmethod
    def _collect_pending_stream_snapshots(
        parsed_events: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
        session_messages: Sequence[Mapping[str, object]],
        by_run_instance_role: dict[str, dict[str, str]],
    ) -> dict[str, JsonObject]:
        return collect_pending_stream_snapshots(
            parsed_events,
            session_messages,
            by_run_instance_role,
        )

    def _build_session_rounds(self, session_id: str) -> list[dict[str, object]]:
        return self._session_service.build_session_rounds(session_id)

    def get_session_rounds(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
    ) -> dict[str, object]:
        rounds = self._build_session_rounds(session_id)
        return paginate_rounds(
            rounds,
            limit=limit,
            cursor_run_id=cursor_run_id,
        )

    def get_round(self, session_id: str, run_id: str) -> dict[str, object]:
        rounds = self._build_session_rounds(session_id)
        return find_round_by_run_id(rounds, session_id=session_id, run_id=run_id)

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return self._token_usage_repo.get_by_run(run_id)

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        return self._token_usage_repo.get_by_session(session_id)
