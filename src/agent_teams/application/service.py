from __future__ import annotations

import asyncio
from json import dumps, loads
from pathlib import Path
from typing import Any, cast
import uuid

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.agents.core.meta_agent import MetaAgent
from agent_teams.coordination.coordinator import CoordinatorGraph
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.core.config import load_runtime_config
from agent_teams.core.enums import InjectionSource, RunEventType
from agent_teams.core.ids import new_trace_id
from agent_teams.core.models import (
    InjectionMessage,
    AgentRuntimeRecord,
    IntentInput,
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
from agent_teams.runtime.gate_manager import GateManager
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalAction, ToolApprovalManager
from agent_teams.runtime.console import set_debug
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.defaults import build_default_registry
from agent_teams.tools.policy import ToolApprovalPolicy
from agent_teams.workflow.spec import WorkflowSpec
from agent_teams.mcp.registry import McpRegistry, McpServerSpec
from agent_teams.skills.registry import SkillRegistry


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent


class AgentTeamsService:
    def __init__(
        self,
        roles_dir: Path | None = None,
        db_path: Path | None = None,
        config_dir: Path = _get_project_root() / ".agent_teams",
        debug: bool = False,
    ) -> None:
        set_debug(debug)
        runtime = load_runtime_config(
            config_dir=config_dir, roles_dir=roles_dir, db_path=db_path
        )

        role_registry = RoleLoader().load_all(runtime.paths.roles_dir)
        tool_registry = build_default_registry()

        # Load MCP configs from .agent_teams/mcp.json if it exists
        mcp_specs = []
        mcp_file = config_dir / "mcp.json"
        if mcp_file.exists():
            try:
                mcp_data = loads(mcp_file.read_text("utf-8"))
                servers = mcp_data.get("mcpServers", mcp_data)
                for name, cfg in servers.items():
                    # FastMCPToolset expects the {"mcpServers": {name: config}} structure
                    wrapped_cfg = {"mcpServers": {name: cfg}}
                    mcp_specs.append(McpServerSpec(name=name, config=wrapped_cfg))
            except Exception as e:
                print(f"Warning: Failed to load mcp.json: {e}")

        mcp_registry = McpRegistry(tuple(mcp_specs))

        from agent_teams.skills.discovery import SkillsDirectory

        skills_dir = config_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_directory = SkillsDirectory(base_dir=skills_dir)
        skill_registry = SkillRegistry(directory=skill_directory)

        for role in role_registry.list_roles():
            tool_registry.validate_known(role.tools)
            mcp_registry.validate_known(role.mcp_servers)
            skill_registry.validate_known(role.skills)

        task_repo = TaskRepository(runtime.paths.db_path)
        shared_store = SharedStore(runtime.paths.db_path)
        event_log = EventLog(runtime.paths.db_path)
        agent_repo = AgentInstanceRepository(runtime.paths.db_path)
        message_repo = MessageRepository(runtime.paths.db_path)
        session_repo = SessionRepository(runtime.paths.db_path)
        instance_pool = InstancePool.from_repo(agent_repo)
        injection_manager = RunInjectionManager()
        run_event_hub = RunEventHub(event_log=event_log)
        gate_manager = GateManager()
        tool_approval_manager = ToolApprovalManager()
        tool_approval_policy = ToolApprovalPolicy()

        prompt_builder = RuntimePromptBuilder()

        def provider_factory(role: RoleDefinition) -> LLMProvider:
            provider: LLMProvider
            profile_config = runtime.llm_profiles.get(role.model_profile)
            config_to_use = profile_config or runtime.llm_profiles.get("default")

            if config_to_use is None:
                provider = EchoProvider()
            else:
                provider = OpenAICompatibleProvider(
                    config_to_use,
                    task_repo=task_repo,
                    instance_pool=instance_pool,
                    shared_store=shared_store,
                    event_bus=event_log,
                    injection_manager=injection_manager,
                    run_event_hub=run_event_hub,
                    agent_repo=agent_repo,
                    workspace_root=Path.cwd(),
                    tool_registry=tool_registry,
                    mcp_registry=mcp_registry,
                    skill_registry=skill_registry,
                    allowed_tools=role.tools,
                    allowed_mcp_servers=role.mcp_servers,
                    allowed_skills=role.skills,
                    message_repo=message_repo,
                    role_registry=role_registry,
                    task_execution_service=task_execution_service,
                    tool_approval_manager=tool_approval_manager,
                    tool_approval_policy=tool_approval_policy,
                )
            return provider

        task_execution_service = TaskExecutionService(
            role_registry=role_registry,
            instance_pool=instance_pool,
            task_repo=task_repo,
            shared_store=shared_store,
            event_bus=event_log,
            agent_repo=agent_repo,
            message_repo=message_repo,
            prompt_builder=prompt_builder,
            provider_factory=provider_factory,
            injection_manager=injection_manager,
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
            gate_manager=gate_manager,
            run_event_hub=run_event_hub,
        )
        self._meta_agent = MetaAgent(coordinator=coordinator)
        self._task_repo = task_repo
        self._instance_pool = instance_pool
        self._role_registry = role_registry
        self._workflows: list[WorkflowSpec] = []
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._gate_manager = gate_manager
        self._tool_approval_manager = tool_approval_manager
        self._tool_approval_policy = tool_approval_policy
        self._agent_repo = agent_repo
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._event_log = event_log
        self._shared_store = shared_store
        self._config_dir = config_dir
        self._roles_dir = runtime.paths.roles_dir
        self._db_path = runtime.paths.db_path
        self._runtime = runtime
        self._mcp_registry = mcp_registry
        self._skill_registry = skill_registry
        self._tool_registry = tool_registry
        self._provider_factory = provider_factory
        self._task_execution_service = task_execution_service
        self._pending_runs: dict[str, IntentInput] = {}
        self._running_run_ids: set[str] = set()

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

    def get_config_status(self) -> dict:
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

    def get_model_config(self) -> dict:
        model_file = self._config_dir / "model.json"
        if model_file.exists():
            return loads(model_file.read_text("utf-8"))
        return {}

    def get_model_profiles(self) -> dict:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return {}
        config = loads(model_file.read_text("utf-8"))
        result = {}
        for name, profile in config.items():
            result[name] = {
                "model": profile.get("model", ""),
                "base_url": profile.get("base_url", ""),
                "has_api_key": bool(profile.get("api_key")),
                "temperature": profile.get("temperature", 0.7),
                "top_p": profile.get("top_p", 1.0),
                "max_tokens": profile.get("max_tokens", 4096),
            }
        return result

    def save_model_profile(self, name: str, profile: dict) -> None:
        model_file = self._config_dir / "model.json"
        config = {}
        if model_file.exists():
            config = loads(model_file.read_text("utf-8"))
        config[name] = profile
        model_file.write_text(dumps(config, indent=2), encoding="utf-8")
        self.reload_model_config()

    def delete_model_profile(self, name: str) -> None:
        model_file = self._config_dir / "model.json"
        if model_file.exists():
            config = loads(model_file.read_text("utf-8"))
            if name in config:
                del config[name]
                model_file.write_text(dumps(config, indent=2), encoding="utf-8")
                self.reload_model_config()

    def save_model_config(self, config: dict) -> None:
        model_file = self._config_dir / "model.json"
        model_file.write_text(dumps(config, indent=2), encoding="utf-8")
        self.reload_model_config()

    def reload_model_config(self) -> None:
        self._runtime = load_runtime_config(
            config_dir=self._config_dir,
            roles_dir=self._roles_dir,
            db_path=self._db_path,
        )
        self._recreate_task_execution_service()

    def _recreate_task_execution_service(self) -> None:
        from agent_teams.agents.core.subagent import SubAgentRunner

        def provider_factory(role: RoleDefinition) -> LLMProvider:
            profile_config = self._runtime.llm_profiles.get(role.model_profile)
            config_to_use = profile_config or self._runtime.llm_profiles.get("default")

            if config_to_use is None:
                return EchoProvider()
            return OpenAICompatibleProvider(
                config_to_use,
                task_repo=self._task_repo,
                instance_pool=self._instance_pool,
                shared_store=self._shared_store,
                event_bus=self._event_log,
                injection_manager=self._injection_manager,
                run_event_hub=self._run_event_hub,
                agent_repo=self._agent_repo,
                workspace_root=Path.cwd(),
                tool_registry=self._tool_registry,
                mcp_registry=self._mcp_registry,
                skill_registry=self._skill_registry,
                allowed_tools=role.tools,
                allowed_mcp_servers=role.mcp_servers,
                allowed_skills=role.skills,
                message_repo=self._message_repo,
                role_registry=self._role_registry,
                task_execution_service=self._task_execution_service,
                tool_approval_manager=self._tool_approval_manager,
                tool_approval_policy=self._tool_approval_policy,
            )

        self._provider_factory = provider_factory
        self._task_execution_service = TaskExecutionService(
            role_registry=self._role_registry,
            instance_pool=self._instance_pool,
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_bus=self._event_log,
            agent_repo=self._agent_repo,
            message_repo=self._message_repo,
            prompt_builder=RuntimePromptBuilder(),
            provider_factory=provider_factory,
            injection_manager=self._injection_manager,
        )

        self._meta_agent.coordinator.task_execution_service = (
            self._task_execution_service
        )

    def reload_mcp_config(self) -> None:
        self._mcp_registry = self._load_mcp_registry()
        for role in self._role_registry.list_roles():
            self._mcp_registry.validate_known(role.mcp_servers)

    def reload_skills_config(self) -> None:
        self._skill_registry = self._load_skill_registry()
        for role in self._role_registry.list_roles():
            self._skill_registry.validate_known(role.skills)

    def _load_mcp_registry(self) -> McpRegistry:
        mcp_specs = []
        mcp_file = self._config_dir / "mcp.json"
        if mcp_file.exists():
            try:
                mcp_data = loads(mcp_file.read_text("utf-8"))
                servers = mcp_data.get("mcpServers", mcp_data)
                for name, cfg in servers.items():
                    wrapped_cfg = {"mcpServers": {name: cfg}}
                    mcp_specs.append(McpServerSpec(name=name, config=wrapped_cfg))
            except Exception as e:
                print(f"Warning: Failed to load mcp.json: {e}")
        return McpRegistry(tuple(mcp_specs))

    def _load_skill_registry(self) -> SkillRegistry:
        from agent_teams.skills.discovery import SkillsDirectory

        skills_dir = self._config_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_directory = SkillsDirectory(base_dir=skills_dir)
        return SkillRegistry(directory=skill_directory)

    async def run_intent(self, intent: IntentInput) -> RunResult:
        intent.session_id = self._ensure_session(intent.session_id)
        run_id = new_trace_id().value
        self._injection_manager.activate(run_id)
        try:
            return await self._meta_agent.handle_intent(intent, trace_id=run_id)
        finally:
            self._injection_manager.deactivate(run_id)

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        """Register a run and return `(run_id, session_id)` for HTTP/SSE clients."""
        intent.session_id = self._ensure_session(intent.session_id)
        run_id = new_trace_id().value
        self._pending_runs[run_id] = intent
        return run_id, intent.session_id

    def _ensure_run_started(self, run_id: str) -> None:
        if run_id in self._running_run_ids:
            return
        intent = self._pending_runs.get(run_id)
        if intent is None:
            raise KeyError(f"Run {run_id} not found")

        self._running_run_ids.add(run_id)
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

        async def _worker() -> None:
            try:
                result = await self._meta_agent.handle_intent(intent, trace_id=run_id)
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
                self._running_run_ids.discard(run_id)
                self._pending_runs.pop(run_id, None)

        asyncio.create_task(_worker())

    async def stream_run_events(self, run_id: str):
        queue = self._run_event_hub.subscribe(run_id)
        self._ensure_run_started(run_id)

        while True:
            event = await queue.get()
            yield event
            if event.event_type in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED):
                self._run_event_hub.unsubscribe_all(run_id)
                break

    async def run_intent_stream(self, intent: IntentInput):
        run_id, _ = self.create_run(intent)
        async for event in self.stream_run_events(run_id):
            yield event

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
                    instance_id=record.instance_id,
                    role_id=record.role_id,
                    event_type=RunEventType.INJECTION_ENQUEUED,
                    payload_json=created.model_dump_json(),
                )
            )

        if created is None:
            raise KeyError(f"No RUNNING agent for run_id={run_id}")
        return created

    def resolve_gate(
        self, run_id: str, task_id: str, action: str, feedback: str = ""
    ) -> None:
        """HTTP handler calls this to let the human approve or request a revision."""
        from agent_teams.runtime.gate_manager import GateAction

        self._gate_manager.resolve_gate(
            run_id, task_id, action=action, feedback=feedback
        )  # type: ignore[arg-type]

    def list_open_gates(self, run_id: str) -> list[dict]:
        """Return currently open gate entries for a run."""
        return self._gate_manager.list_open_gates(run_id)

    def resolve_tool_approval(
        self, run_id: str, tool_call_id: str, action: str, feedback: str = ''
    ) -> None:
        if action not in {'approve', 'deny'}:
            raise ValueError(f'Unsupported action: {action}')
        self._tool_approval_manager.resolve_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            action=cast(ToolApprovalAction, action),
            feedback=feedback,
        )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        return self._tool_approval_manager.list_open_approvals(run_id=run_id)

    def dispatch_task_human(
        self, run_id: str, task_id: str, coordinator_instance_id: str
    ) -> None:
        """
        Human mode: inject a dispatch marker so the coordinator's polling loop
        picks up the selected task_id and dispatches it.
        """
        import json

        self._injection_manager.enqueue(
            run_id=run_id,
            recipient_instance_id=coordinator_instance_id,
            source=InjectionSource.USER,
            content=json.dumps({"__human_dispatch__": task_id}),
        )

    def get_coordinator_instance_id(self, session_id: str) -> str | None:
        return self._agent_repo.get_coordinator_instance_id(session_id)

    def dispatch_task_human_for_session(
        self, session_id: str, run_id: str, task_id: str
    ) -> None:
        coordinator_instance_id = self.get_coordinator_instance_id(session_id)
        if coordinator_instance_id is None:
            raise KeyError(f"No coordinator instance found for session={session_id}")
        self.dispatch_task_human(
            run_id=run_id,
            task_id=task_id,
            coordinator_instance_id=coordinator_instance_id,
        )

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

    def get_agent_messages(self, session_id: str, instance_id: str) -> list[dict]:
        return self._message_repo.get_messages_for_instance(session_id, instance_id)

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

    @staticmethod
    def _collect_pending_tool_approvals(
        parsed_events: list[tuple[dict, dict[str, Any]]],
    ) -> dict[str, list[dict[str, str]]]:
        by_run_call: dict[str, dict[str, dict[str, Any]]] = {}

        for ev, payload in parsed_events:
            run_id = ev["trace_id"]
            event_type = ev.get("event_type")
            tool_call_id = payload.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue

            run_map = by_run_call.setdefault(run_id, {})
            if event_type == RunEventType.TOOL_APPROVAL_REQUESTED.value:
                entry = run_map.setdefault(
                    tool_call_id,
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": str(payload.get("tool_name") or ""),
                        "args_preview": str(payload.get("args_preview") or ""),
                        "role_id": str(payload.get("role_id") or ""),
                        "instance_id": str(payload.get("instance_id") or ev.get("instance_id") or ""),
                        "requested_at": str(ev.get("occurred_at") or ""),
                        "status": "requested",
                        "feedback": "",
                        "has_tool_result": False,
                    },
                )
                entry["tool_name"] = str(payload.get("tool_name") or entry["tool_name"])
                entry["args_preview"] = str(payload.get("args_preview") or entry["args_preview"])
                entry["role_id"] = str(payload.get("role_id") or entry["role_id"])
                entry["instance_id"] = str(payload.get("instance_id") or entry["instance_id"])
                entry["requested_at"] = str(ev.get("occurred_at") or entry["requested_at"])
                entry["status"] = "requested"
            elif event_type == RunEventType.TOOL_APPROVAL_RESOLVED.value:
                entry = run_map.get(tool_call_id)
                if entry is None:
                    continue
                action = payload.get("action")
                if isinstance(action, str) and action:
                    entry["status"] = action
                feedback = payload.get("feedback")
                if isinstance(feedback, str) and feedback:
                    entry["feedback"] = feedback
            elif event_type == RunEventType.TOOL_RESULT.value:
                entry = run_map.get(tool_call_id)
                if entry is not None:
                    entry["has_tool_result"] = True

        result: dict[str, list[dict[str, str]]] = {}
        for run_id, run_calls in by_run_call.items():
            pending: list[dict[str, str]] = []
            for entry in run_calls.values():
                if entry.get("has_tool_result"):
                    continue
                if str(entry.get("status") or "requested") != "requested":
                    continue
                pending.append(
                    {
                        "tool_call_id": str(entry.get("tool_call_id") or ""),
                        "tool_name": str(entry.get("tool_name") or ""),
                        "args_preview": str(entry.get("args_preview") or ""),
                        "role_id": str(entry.get("role_id") or ""),
                        "instance_id": str(entry.get("instance_id") or ""),
                        "requested_at": str(entry.get("requested_at") or ""),
                        "status": str(entry.get("status") or "requested"),
                        "feedback": str(entry.get("feedback") or ""),
                    }
                )
            pending.sort(key=lambda item: item.get("requested_at") or "")
            if pending:
                result[run_id] = pending
        return result

    def _build_session_rounds(self, session_id: str) -> list[dict]:
        """Aggregate session events into run-scoped rounds for UI rendering."""
        import json

        events = self._event_log.list_by_session(session_id)
        parsed_events: list[tuple[dict, dict[str, Any]]] = []
        for ev in events:
            try:
                payload = json.loads(ev["payload_json"])
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
            parsed_events.append((ev, payload))

        rounds_map: dict[str, dict] = {}
        by_run_instance_role: dict[str, dict[str, str]] = {}
        by_run_role_instance: dict[str, dict[str, str]] = {}
        pending_by_run = self._collect_pending_tool_approvals(parsed_events)

        for ev, payload in parsed_events:
            run_id = ev["trace_id"]
            ev_instance = ev.get("instance_id") or payload.get("instance_id")
            ev_role = payload.get("role_id")
            if isinstance(ev_instance, str) and isinstance(ev_role, str):
                by_run_instance_role.setdefault(run_id, {})[ev_instance] = ev_role
                by_run_role_instance.setdefault(run_id, {}).setdefault(
                    ev_role, ev_instance
                )

        # Fallback mapping from repository records for runs with sparse events.
        for rec in self._agent_repo.list_by_session(session_id):
            run_map = by_run_instance_role.setdefault(rec.run_id, {})
            run_map.setdefault(rec.instance_id, rec.role_id)
            role_map = by_run_role_instance.setdefault(rec.run_id, {})
            role_map.setdefault(rec.role_id, rec.instance_id)

        for ev in events:
            run_id = ev["trace_id"]
            if run_id not in rounds_map:
                rounds_map[run_id] = {
                    "run_id": run_id,
                    "created_at": ev["occurred_at"],
                    "intent": None,
                    "coordinator_messages": [],
                    "pending_tool_approvals": pending_by_run.get(run_id, []),
                    "workflows": [],
                    "instance_role_map": by_run_instance_role.get(run_id, {}),
                    "role_instance_map": by_run_role_instance.get(run_id, {}),
                }
            # Keep the earliest timestamp as round creation time.
            if ev["occurred_at"] < rounds_map[run_id]["created_at"]:
                rounds_map[run_id]["created_at"] = ev["occurred_at"]

        # Fill run messages.
        messages = self.get_session_messages(session_id)
        for msg in messages:
            run_id = msg["trace_id"]
            round_data = rounds_map.get(run_id)
            if round_data is None:
                continue

            role_id = by_run_instance_role.get(run_id, {}).get(msg["instance_id"])
            msg["role_id"] = role_id

            # Infer run intent from coordinator user-prompt message.
            if msg["role"] == "user":
                content = msg.get("message", {})
                parts = content.get("parts", []) if isinstance(content, dict) else []
                for pt in parts:
                    if not isinstance(pt, dict):
                        continue
                    if (
                        pt.get("part_kind") == "user-prompt"
                        and not round_data["intent"]
                    ):
                        round_data["intent"] = pt.get("content", "")
                        break

            # Main chat should only show coordinator thread.
            if role_id == "coordinator_agent":
                round_data["coordinator_messages"].append(msg)

        # Fill run workflows via task-scoped shared state.
        tasks = self._task_repo.list_by_session(session_id)
        from agent_teams.core.enums import ScopeType
        from agent_teams.core.models import ScopeRef

        for task in tasks:
            run_id = task.envelope.trace_id
            round_data = rounds_map.get(run_id)
            if round_data is None:
                continue
            scope = ScopeRef(scope_type=ScopeType.TASK, scope_id=task.envelope.task_id)
            wf_str = self._shared_store.get_state(scope, "workflow_graph")
            if wf_str:
                round_data["workflows"].append(json.loads(wf_str))

        return sorted(
            list(rounds_map.values()), key=lambda x: x["created_at"], reverse=True
        )

    def get_session_rounds(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
    ) -> dict[str, object]:
        rounds = self._build_session_rounds(session_id)
        safe_limit = max(1, min(limit, 50))

        start = 0
        if cursor_run_id:
            for idx, item in enumerate(rounds):
                if item.get("run_id") == cursor_run_id:
                    start = idx + 1
                    break

        items = rounds[start : start + safe_limit]
        next_index = start + safe_limit
        has_more = next_index < len(rounds)
        next_cursor = items[-1]["run_id"] if has_more and items else None
        return {
            "items": items,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def get_round(self, session_id: str, run_id: str) -> dict:
        rounds = self._build_session_rounds(session_id)
        for r in rounds:
            if r["run_id"] == run_id:
                return r
        raise KeyError(f"Round {run_id} not found in session {session_id}")
