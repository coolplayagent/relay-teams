# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import shutil
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from threading import Lock
from typing import TYPE_CHECKING, cast

from relay_teams.agent_runtimes.instances.models import AgentRuntimeRecord
from relay_teams.logger import get_logger
from relay_teams.media import content_parts_to_text
from relay_teams.media import user_prompt_content_to_text
from relay_teams.metrics import SqliteMetricAggregateStore
from relay_teams.monitors.repository import MonitorRepository
from relay_teams.persistence.scope_models import ScopeRef, ScopeType
from relay_teams.validation import (
    require_cascade_delete,
    require_force_delete,
)
from relay_teams.sessions.session_metadata import (
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_AUTO,
    SESSION_TITLE_SOURCE_MANUAL,
)
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.runtime_config import RuntimeConfig
from relay_teams.sessions.session_rounds_projection import (
    ROUND_PROJECTION_EVENT_TYPES,
)
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.runs.run_state_models import RunStateRecord
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import IntentInput, RunEvent
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskKind
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.user_question_models import UserQuestionRequestRecord
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from relay_teams.tools.workspace_tools.edit_state import READ_STATE_PREFIX
from relay_teams.sessions.session_models import (
    ProjectKind,
    SessionMetadataPatch,
    SessionMode,
    SessionRecord,
)
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerRecord,
    SessionHistoryMarkerType,
)
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_list_cache import (
    DEFAULT_LIST_SESSIONS_CACHE_MS,
    LIST_SESSIONS_CACHE_MS_ENV,
    SessionListCacheMixin,
    resolve_positive_int_env as _resolve_positive_int_env,
)
from relay_teams.sessions.session_read_models import (
    DEFAULT_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS,
    SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS_ENV,
    SessionReadModelMixin,
    _RecoverySnapshotCacheEntry,
)
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import (
    TokenUsageRepository,
)
from relay_teams.workspace import (
    WorkspaceManager,
    WorkspaceService,
    build_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)

import asyncio

if TYPE_CHECKING:
    from relay_teams.agents.execution.subagent_reflection import (
        SubagentReflectionService,
    )
    from relay_teams.agents.orchestration.settings_service import (
        OrchestrationSettingsService,
    )
    from relay_teams.media import MediaAssetService
    from relay_teams.mcp.mcp_registry import McpRegistry
    from relay_teams.roles.memory_service import RoleMemoryService
    from relay_teams.roles.role_registry import RoleRegistry
    from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
    from relay_teams.skills.skill_registry import SkillRegistry


from relay_teams.roles.role_registry import SystemRolesUnavailableError

LOGGER = get_logger(__name__)
AUTOMATION_INTERNAL_WORKSPACE_ID = "automation-system"
ACTIVE_RUN_REBIND_ERROR = (
    "Cannot rebind workspace while session has active or recoverable run"
)
TERMINAL_RUN_STATUSES = frozenset(
    {
        RunRuntimeStatus.COMPLETED,
        RunRuntimeStatus.FAILED,
        RunRuntimeStatus.STOPPED,
    }
)
_LEGACY_COORDINATOR_IDENTIFIERS = (
    "coordinator",
    "coordinator agent",
    "coordinator_agent",
)
_MAIN_AGENT_IDENTIFIERS = ("mainagent", "main agent", "main_agent")
_AUTO_SESSION_TITLE_MAX_CHARS = 120


def _legacy_coordinator_identifiers() -> tuple[str, ...]:
    return _LEGACY_COORDINATOR_IDENTIFIERS


def _main_agent_identifiers() -> tuple[str, ...]:
    return _MAIN_AGENT_IDENTIFIERS


def _normalize_auto_session_title(value: str) -> str | None:
    for raw_line in str(value or "").splitlines():
        normalized = " ".join(raw_line.strip().split())
        if not normalized:
            continue
        if len(normalized) <= _AUTO_SESSION_TITLE_MAX_CHARS:
            return normalized
        return f"{normalized[: _AUTO_SESSION_TITLE_MAX_CHARS - 3].rstrip()}..."
    return None


def _system_roles_unavailable_error_type() -> type[Exception]:
    return SystemRolesUnavailableError


class SessionService(SessionListCacheMixin, SessionReadModelMixin):
    def __init__(
        self,
        *,
        session_repo: SessionRepository,
        task_repo: TaskRepository,
        agent_repo: AgentInstanceRepository,
        message_repo: MessageRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        user_question_repo: UserQuestionRepository | None = None,
        run_runtime_repo: RunRuntimeRepository,
        token_usage_repo: TokenUsageRepository,
        monitor_repository: MonitorRepository | None = None,
        session_history_marker_repo: SessionHistoryMarkerRepository | None = None,
        run_state_repo: RunStateRepository | None = None,
        background_task_repository: BackgroundTaskRepository | None = None,
        todo_service: TodoService | None = None,
        run_event_hub: RunEventHub | None = None,
        active_run_registry: ActiveSessionRunRegistry | None = None,
        event_log: EventLog | None = None,
        shared_store: SharedStateRepository | None = None,
        metrics_store: SqliteMetricAggregateStore | None = None,
        workspace_manager: WorkspaceManager | None = None,
        workspace_service: WorkspaceService | None = None,
        external_session_binding_repo: ExternalSessionBindingRepository | None = None,
        role_memory_service: RoleMemoryService | None = None,
        subagent_reflection_service: SubagentReflectionService | None = None,
        role_registry: RoleRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        mcp_registry: McpRegistry | None = None,
        orchestration_settings_service: OrchestrationSettingsService | None = None,
        media_asset_service: MediaAssetService | None = None,
        run_intent_repo: RunIntentRepository | None = None,
        get_runtime: Callable[[], RuntimeConfig] | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._task_repo = task_repo
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._user_question_repo = user_question_repo
        self._run_runtime_repo = run_runtime_repo
        self._token_usage_repo = token_usage_repo
        self._monitor_repository = monitor_repository
        self._session_history_marker_repo = session_history_marker_repo
        self._run_state_repo = run_state_repo
        self._background_task_repository = background_task_repository
        self._todo_service = todo_service
        self._run_event_hub = run_event_hub
        self._active_run_registry = active_run_registry
        self._event_log = event_log
        self._shared_store = shared_store
        self._metrics_store = metrics_store
        self._workspace_manager = workspace_manager
        self._workspace_service = workspace_service
        self._external_session_binding_repo = external_session_binding_repo
        self._role_memory_service = role_memory_service
        self._subagent_reflection_service = subagent_reflection_service
        self._role_registry = role_registry
        self._skill_registry = skill_registry
        self._mcp_registry = mcp_registry
        self._orchestration_settings_service = orchestration_settings_service
        self._media_asset_service = media_asset_service
        self._run_intent_repo = run_intent_repo
        self._get_runtime = get_runtime
        self._list_sessions_cache: tuple[float, tuple[SessionRecord, ...]] | None = None
        self._list_sessions_cache_lock = Lock()
        self._list_sessions_cache_dirty = False
        self._list_sessions_refresh_task: asyncio.Task[None] | None = None
        self._list_sessions_refresh_started_monotonic = 0.0
        self._list_sessions_cache_version = 0
        self._list_sessions_cache_ttl_seconds = (
            _resolve_positive_int_env(
                LIST_SESSIONS_CACHE_MS_ENV,
                DEFAULT_LIST_SESSIONS_CACHE_MS,
            )
            / 1000
        )
        self._recovery_cache_ms = self._resolve_session_snapshot_cache_ms()
        self._snapshot_refresh_min_interval_ms = _resolve_positive_int_env(
            SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS_ENV,
            DEFAULT_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS,
        )
        self._recovery_snapshot_cache: dict[str, _RecoverySnapshotCacheEntry] = {}
        self._recovery_snapshot_cache_lock = Lock()
        self._recovery_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._rounds_snapshot_cache: dict[str, _RecoverySnapshotCacheEntry] = {}
        self._rounds_snapshot_cache_lock = Lock()
        self._rounds_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._rounds_snapshot_args_by_key: dict[
            str, tuple[str, int, str | None, bool, bool]
        ] = {}
        self._subagents_snapshot_cache: dict[str, _RecoverySnapshotCacheEntry] = {}
        self._subagents_snapshot_cache_lock = Lock()
        self._subagents_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._agents_snapshot_cache: dict[str, _RecoverySnapshotCacheEntry] = {}
        self._agents_snapshot_cache_lock = Lock()
        self._agents_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._tasks_snapshot_cache: dict[str, _RecoverySnapshotCacheEntry] = {}
        self._tasks_snapshot_cache_lock = Lock()
        self._tasks_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._token_usage_snapshot_cache: dict[str, _RecoverySnapshotCacheEntry] = {}
        self._token_usage_snapshot_cache_lock = Lock()
        self._token_usage_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        if self._run_event_hub is not None:
            self._run_event_hub.add_publish_observer(
                self._observe_run_event_for_snapshot_dirty
            )

    def replace_role_registry(self, role_registry: RoleRegistry | None) -> None:
        self._role_registry = role_registry

    def replace_subagent_reflection_service(
        self,
        subagent_reflection_service: SubagentReflectionService | None,
    ) -> None:
        self._subagent_reflection_service = subagent_reflection_service

    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        project_kind: ProjectKind = ProjectKind.WORKSPACE,
        project_id: str | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        resolved_session_id = self._resolve_session_create_id(session_id)
        self._require_workspace_for_session_create(
            project_kind=project_kind,
            workspace_id=workspace_id,
        )
        (
            resolved_session_mode,
            resolved_normal_root_role_id,
            resolved_orchestration_preset_id,
        ) = self._resolve_session_create_topology(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            metadata=metadata,
            project_kind=project_kind,
            project_id=project_id,
            session_mode=session_mode,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
        )
        record = self._session_repo.create(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            metadata=metadata,
            project_kind=project_kind,
            project_id=project_id,
            session_mode=resolved_session_mode,
            normal_root_role_id=resolved_normal_root_role_id,
            orchestration_preset_id=resolved_orchestration_preset_id,
        )
        self._invalidate_list_sessions_cache()
        self._merge_record_into_list_sessions_cache(record)
        self._seed_empty_session_snapshot_caches(record.session_id)
        return record

    async def create_session_async(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        project_kind: ProjectKind = ProjectKind.WORKSPACE,
        project_id: str | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        resolved_session_id = self._resolve_session_create_id(session_id)
        await self._require_workspace_for_session_create_async(
            project_kind=project_kind,
            workspace_id=workspace_id,
        )
        (
            resolved_session_mode,
            resolved_normal_root_role_id,
            resolved_orchestration_preset_id,
        ) = self._resolve_session_create_topology(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            metadata=metadata,
            project_kind=project_kind,
            project_id=project_id,
            session_mode=session_mode,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
        )
        record = await self._session_repo.create_async(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            metadata=metadata,
            project_kind=project_kind,
            project_id=project_id,
            session_mode=resolved_session_mode,
            normal_root_role_id=resolved_normal_root_role_id,
            orchestration_preset_id=resolved_orchestration_preset_id,
        )
        self._invalidate_list_sessions_cache()
        self._merge_record_into_list_sessions_cache(record)
        self._seed_empty_session_snapshot_caches(record.session_id)
        return record

    @staticmethod
    def _resolve_session_create_id(session_id: str | None) -> str:
        if session_id:
            return session_id
        return f"session-{uuid.uuid4().hex[:8]}"

    def _require_workspace_for_session_create(
        self,
        *,
        project_kind: ProjectKind,
        workspace_id: str,
    ) -> None:
        if self._workspace_service is None:
            return
        if (
            project_kind == ProjectKind.AUTOMATION
            and workspace_id == AUTOMATION_INTERNAL_WORKSPACE_ID
        ):
            return
        self._workspace_service.require_workspace(workspace_id)

    async def _require_workspace_for_session_create_async(
        self,
        *,
        project_kind: ProjectKind,
        workspace_id: str,
    ) -> None:
        if self._workspace_service is None:
            return
        if (
            project_kind == ProjectKind.AUTOMATION
            and workspace_id == AUTOMATION_INTERNAL_WORKSPACE_ID
        ):
            return
        await self._workspace_service.require_workspace_async(workspace_id)

    def _resolve_session_create_topology(
        self,
        *,
        session_id: str,
        workspace_id: str,
        metadata: dict[str, str] | None,
        project_kind: ProjectKind,
        project_id: str | None,
        session_mode: SessionMode | None,
        normal_root_role_id: str | None,
        orchestration_preset_id: str | None,
    ) -> tuple[SessionMode, str | None, str | None]:
        resolved_session_mode = session_mode or SessionMode.NORMAL
        resolved_normal_root_role_id = normal_root_role_id
        resolved_orchestration_preset_id = orchestration_preset_id
        if (
            session_mode is None
            and orchestration_preset_id is None
            and self._orchestration_settings_service is not None
        ):
            resolved_session_mode = (
                self._orchestration_settings_service.default_session_mode()
            )
            resolved_orchestration_preset_id = (
                self._orchestration_settings_service.default_orchestration_preset_id()
            )
        if (
            resolved_normal_root_role_id is None
            and self._orchestration_settings_service is not None
        ):
            resolved_normal_root_role_id = (
                self._orchestration_settings_service.default_normal_root_role_id()
            )
        if resolved_normal_root_role_id is None and self._role_registry is not None:
            resolved_normal_root_role_id = self._require_main_agent_role_id()
        resolved_normal_root_role_id = self._resolve_normal_root_role_id(
            resolved_normal_root_role_id
        )
        if (
            resolved_session_mode == SessionMode.ORCHESTRATION
            and self._orchestration_settings_service is not None
        ):
            probe = SessionRecord(
                session_id=session_id,
                workspace_id=workspace_id,
                project_kind=project_kind,
                project_id=project_id,
                metadata={} if metadata is None else dict(metadata),
                session_mode=SessionMode.ORCHESTRATION,
                normal_root_role_id=resolved_normal_root_role_id,
                orchestration_preset_id=resolved_orchestration_preset_id,
            )
            _ = self._orchestration_settings_service.resolve_run_topology(probe)
        return (
            resolved_session_mode,
            resolved_normal_root_role_id,
            resolved_orchestration_preset_id,
        )

    def update_session(self, session_id: str, patch: SessionMetadataPatch) -> None:
        current = self._session_repo.get(session_id)
        next_metadata = dict(current.metadata)

        if "custom_metadata" in patch.model_fields_set:
            next_metadata = self._replace_custom_metadata(
                next_metadata,
                patch.custom_metadata,
            )

        if "source_label" in patch.model_fields_set:
            self._apply_optional_metadata_value(
                next_metadata,
                key="source_label",
                value=patch.source_label,
            )

        if "source_icon" in patch.model_fields_set:
            self._apply_optional_metadata_value(
                next_metadata,
                key="source_icon",
                value=patch.source_icon,
            )

        if "title" in patch.model_fields_set:
            title_value = str(patch.title or "").strip()
            if title_value:
                next_metadata["title"] = title_value
                if "title_source" not in patch.model_fields_set:
                    next_metadata[SESSION_METADATA_TITLE_SOURCE_KEY] = (
                        SESSION_TITLE_SOURCE_MANUAL
                    )
            else:
                next_metadata.pop("title", None)
                next_metadata.pop(SESSION_METADATA_TITLE_SOURCE_KEY, None)

        if "title_source" in patch.model_fields_set:
            title_value = str(next_metadata.get("title") or "").strip()
            if not title_value:
                raise ValueError("title_source requires title to be set")
            title_source = str(patch.title_source or "").strip()
            if not title_source:
                next_metadata.pop(SESSION_METADATA_TITLE_SOURCE_KEY, None)
            else:
                next_metadata[SESSION_METADATA_TITLE_SOURCE_KEY] = title_source

        self._session_repo.update_metadata(session_id, next_metadata)
        self._invalidate_list_sessions_cache()
        self._merge_record_into_list_sessions_cache(
            self._with_terminal_run_projection(self._session_repo.get(session_id))
        )

    async def update_session_async(
        self, session_id: str, patch: SessionMetadataPatch
    ) -> None:
        await asyncio.to_thread(self.update_session, session_id, patch)

    def sync_session_metadata(
        self,
        session_id: str,
        metadata: dict[str, str],
    ) -> None:
        _ = self._session_repo.get(session_id)
        self._session_repo.update_metadata(session_id, dict(metadata))
        self._invalidate_list_sessions_cache()
        self._merge_record_into_list_sessions_cache(
            self._with_terminal_run_projection(self._session_repo.get(session_id))
        )

    def _replace_custom_metadata(
        self,
        metadata: dict[str, str],
        custom_metadata: dict[str, str] | None,
    ) -> dict[str, str]:
        next_metadata = {
            key: value
            for key, value in metadata.items()
            if self._is_reserved_session_metadata_key(key)
        }
        if custom_metadata is None:
            return next_metadata
        next_metadata.update(custom_metadata)
        return next_metadata

    def _apply_optional_metadata_value(
        self,
        metadata: dict[str, str],
        *,
        key: str,
        value: str | None,
    ) -> None:
        normalized_value = str(value or "").strip()
        if normalized_value:
            metadata[key] = normalized_value
            return
        metadata.pop(key, None)

    @staticmethod
    def _is_reserved_session_metadata_key(key: str) -> bool:
        return key in {
            "title",
            SESSION_METADATA_TITLE_SOURCE_KEY,
            "source_label",
            "source_icon",
            "source_kind",
            "source_provider",
        } or key.startswith("feishu_")

    def _with_auto_session_title(self, record: SessionRecord) -> SessionRecord:
        metadata = dict(record.metadata)
        title = str(metadata.get("title") or "").strip()
        title_source = str(
            metadata.get(SESSION_METADATA_TITLE_SOURCE_KEY) or ""
        ).strip()
        if title and title_source != SESSION_TITLE_SOURCE_AUTO:
            return record
        auto_title = self._resolve_auto_session_title(record.session_id)
        if auto_title is None:
            return record
        metadata["title"] = auto_title
        metadata[SESSION_METADATA_TITLE_SOURCE_KEY] = SESSION_TITLE_SOURCE_AUTO
        return record.model_copy(update={"metadata": metadata})

    def _with_auto_session_title_from_preloaded(
        self,
        record: SessionRecord,
        *,
        first_intent_titles: Mapping[str, str],
        first_user_messages: Mapping[str, Mapping[str, object]],
    ) -> SessionRecord:
        metadata = dict(record.metadata)
        title = str(metadata.get("title") or "").strip()
        title_source = str(
            metadata.get(SESSION_METADATA_TITLE_SOURCE_KEY) or ""
        ).strip()
        if title and title_source != SESSION_TITLE_SOURCE_AUTO:
            return record
        auto_title = self._resolve_auto_session_title_from_preloaded(
            record.session_id,
            first_intent_titles=first_intent_titles,
            first_user_messages=first_user_messages,
        )
        if auto_title is None:
            return record
        metadata["title"] = auto_title
        metadata[SESSION_METADATA_TITLE_SOURCE_KEY] = SESSION_TITLE_SOURCE_AUTO
        return record.model_copy(update={"metadata": metadata})

    def _resolve_auto_session_title(self, session_id: str) -> str | None:
        run_intent_title = self._first_run_intent_title(session_id)
        if run_intent_title is not None:
            return run_intent_title
        return self._first_user_message_title(session_id)

    def _resolve_auto_session_title_from_preloaded(
        self,
        session_id: str,
        *,
        first_intent_titles: Mapping[str, str],
        first_user_messages: Mapping[str, Mapping[str, object]],
    ) -> str | None:
        title = first_intent_titles.get(session_id)
        if title is not None:
            return title
        message = first_user_messages.get(session_id)
        if message is None:
            return None
        return self._user_message_title(message.get("message"))

    @staticmethod
    def _run_intent_title(intent: IntentInput) -> str | None:
        return _normalize_auto_session_title(
            content_parts_to_text(intent.display_input or intent.input)
        )

    def _first_run_intent_title(self, session_id: str) -> str | None:
        if self._run_intent_repo is None:
            return None
        for intent in self._run_intent_repo.list_by_session(session_id).values():
            title = self._run_intent_title(intent)
            if title is not None:
                return title
        return None

    def _first_user_message_title(self, session_id: str) -> str | None:
        messages = self._message_repo.get_user_messages_by_session(
            session_id,
            include_cleared=True,
            include_hidden_from_context=True,
        )
        for message in messages:
            title = self._user_message_title(message.get("message"))
            if title is not None:
                return title
        return None

    @staticmethod
    def _user_message_title(message: object) -> str | None:
        if not isinstance(message, dict):
            return None
        raw_parts = message.get("parts")
        if not isinstance(raw_parts, list):
            return None
        for raw_part in raw_parts:
            if not isinstance(raw_part, dict):
                continue
            part_kind = str(raw_part.get("part_kind") or "").strip()
            if part_kind != "user-prompt":
                continue
            title = _normalize_auto_session_title(
                user_prompt_content_to_text(raw_part.get("content"))
            )
            if title is not None:
                return title
        return None

    def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: SessionMode,
        normal_root_role_id: str | None,
        orchestration_preset_id: str | None,
    ) -> SessionRecord:
        session = self._session_repo.get(session_id)
        if session.started_at is not None:
            raise RuntimeError("Session mode can no longer be changed")
        resolved_normal_root_role_id = self._resolve_normal_root_role_id(
            normal_root_role_id
            if normal_root_role_id is not None
            else session.normal_root_role_id
        )
        if (
            session_mode == SessionMode.ORCHESTRATION
            and self._orchestration_settings_service is not None
        ):
            probe = session.model_copy(
                update={
                    "session_mode": SessionMode.ORCHESTRATION,
                    "normal_root_role_id": resolved_normal_root_role_id,
                    "orchestration_preset_id": orchestration_preset_id,
                }
            )
            _ = self._orchestration_settings_service.resolve_run_topology(probe)
        self._session_repo.update_topology(
            session_id,
            session_mode=session_mode,
            normal_root_role_id=resolved_normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
        )
        self._invalidate_list_sessions_cache()
        return self.get_session(session_id)

    async def update_session_topology_async(
        self,
        session_id: str,
        *,
        session_mode: SessionMode,
        normal_root_role_id: str | None,
        orchestration_preset_id: str | None,
    ) -> SessionRecord:
        return await asyncio.to_thread(
            self.update_session_topology,
            session_id,
            session_mode=session_mode,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
        )

    def rebind_session_workspace(
        self,
        session_id: str,
        *,
        workspace_id: str,
    ) -> SessionRecord:
        session = self._session_repo.get(session_id)
        if session.workspace_id == workspace_id:
            return session
        if self._workspace_service is not None:
            self._workspace_service.require_workspace(workspace_id)
        if self._select_active_run(session_id) is not None:
            raise RuntimeError(ACTIVE_RUN_REBIND_ERROR)
        project_id = session.project_id
        if session.project_kind == ProjectKind.WORKSPACE:
            project_id = workspace_id
        self._session_repo.update_workspace(
            session_id,
            workspace_id=workspace_id,
            project_id=project_id or workspace_id,
        )
        self._invalidate_list_sessions_cache()
        self._agent_repo.update_session_workspace(
            session_id,
            workspace_id=workspace_id,
        )
        return self.get_session(session_id)

    def _resolve_normal_root_role_id(self, role_id: str | None) -> str | None:
        if self._role_registry is None:
            normalized = str(role_id or "").strip()
            return normalized or None
        _ = self._require_main_agent_role_id()
        return self._role_registry.resolve_normal_mode_role_id(role_id)

    def _require_main_agent_role_id(self) -> str:
        error_type = _system_roles_unavailable_error_type()
        if self._role_registry is None:
            raise error_type(
                "Required system roles are unavailable: main_agent: role registry is not configured"
            )
        try:
            return self._role_registry.get_main_agent_role_id()
        except (KeyError, ValueError) as exc:
            raise error_type(
                f"Required system roles are unavailable: main_agent: {exc}"
            ) from exc

    def delete_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        session = self._session_repo.get(session_id)
        if self._select_active_run(session_id) is not None:
            require_force_delete(
                force,
                message="Cannot delete session while it has active or recoverable run",
            )
        task_records = self._task_repo.list_by_session(session_id)
        agent_records = self._agent_repo.list_by_session(session_id)
        background_task_records: tuple[BackgroundTaskRecord, ...] = ()
        if self._background_task_repository is not None:
            background_task_records = self._background_task_repository.list_by_session(
                session_id
            )
        if self._has_dependent_session_data(
            session_id,
            task_records=task_records,
            agent_records=agent_records,
            background_task_records=background_task_records,
        ):
            require_cascade_delete(
                cascade,
                message="Cannot delete session without cascade while related session data exists",
            )
        task_ids = [record.envelope.task_id for record in task_records]
        instance_ids = [record.instance_id for record in agent_records]
        role_scope_ids = sorted(
            {f"{record.session_id}:{record.role_id}" for record in agent_records}
            | {
                build_instance_role_scope_id(
                    record.session_id,
                    record.role_id,
                    record.instance_id,
                )
                for record in agent_records
            }
        )
        session_scope_ids = sorted(
            {
                build_instance_session_scope_id(
                    record.session_id,
                    record.instance_id,
                )
                for record in agent_records
            }
        )
        conversation_ids = sorted(
            {
                record.conversation_id
                for record in agent_records
                if record.conversation_id
            }
            | {
                build_conversation_id(
                    record.session_id,
                    record.role_id,
                )
                for record in agent_records
            }
        )
        self._message_repo.delete_by_session(session_id)
        if self._event_log is not None:
            self._event_log.delete_by_session(session_id)
        if self._shared_store is not None:
            self._shared_store.delete_by_session(
                session_id,
                task_ids=task_ids,
                instance_ids=instance_ids,
                role_scope_ids=role_scope_ids,
                session_scope_ids=session_scope_ids,
                conversation_ids=conversation_ids,
                workspace_ids=[],
            )
        self._approval_ticket_repo.delete_by_session(session_id)
        if self._background_task_repository is not None:
            self._background_task_repository.delete_by_session(session_id)
        self._delete_background_task_logs(
            session=session,
            background_task_records=background_task_records,
        )
        self._run_runtime_repo.delete_by_session(session_id)
        if self._todo_service is not None:
            self._todo_service.delete_for_session(session_id)
        if self._monitor_repository is not None:
            self._monitor_repository.delete_by_session(session_id)
        self._task_repo.delete_by_session(session_id)
        self._agent_repo.delete_by_session(session_id)
        if self._session_history_marker_repo is not None:
            self._session_history_marker_repo.delete_by_session(session_id)
        if self._external_session_binding_repo is not None:
            self._external_session_binding_repo.delete_by_session(session_id)
        if self._media_asset_service is not None:
            self._media_asset_service.delete_session_assets(session_id)
        self._session_repo.delete(session_id)
        self._token_usage_repo.delete_by_session(session_id)
        if self._metrics_store is not None:
            self._metrics_store.delete_by_session(session_id)
        if self._workspace_manager is not None:
            session_dir = self._workspace_manager.session_artifact_dir(
                workspace_id=session.workspace_id,
                session_id=session_id,
            )
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
        self._invalidate_list_sessions_cache()
        self._remove_record_from_list_sessions_cache(session_id)
        self._clear_session_snapshot_caches(session_id)
        self._refresh_list_sessions_cache()

    async def delete_session_async(
        self,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self.delete_session, session_id, force=force, cascade=cascade
        )

    def _has_dependent_session_data(
        self,
        session_id: str,
        *,
        task_records: tuple[object, ...],
        agent_records: tuple[object, ...],
        background_task_records: tuple[BackgroundTaskRecord, ...],
    ) -> bool:
        if task_records or agent_records or background_task_records:
            return True
        if self._message_repo.get_messages_by_session(session_id):
            return True
        if self._run_runtime_repo.list_by_session(session_id):
            return True
        if self._event_log is not None and self._event_log.list_by_session(session_id):
            return True
        if (
            self._session_history_marker_repo is not None
            and self._session_history_marker_repo.list_by_session(session_id)
        ):
            return True
        if self._external_session_binding_repo is not None and any(
            binding.session_id == session_id
            for binding in self._external_session_binding_repo.list_by_platform(
                "feishu"
            )
        ):
            return True
        return False

    def delete_normal_mode_subagent(self, session_id: str, instance_id: str) -> None:
        session = self._session_repo.get(session_id)
        agent = self._require_session_agent(session_id, instance_id)
        if not self._is_normal_mode_subagent_record(agent, session=session):
            raise KeyError(instance_id)
        runtime = self._run_runtime_repo.get(agent.run_id)
        if runtime is not None and runtime.status in {
            RunRuntimeStatus.QUEUED,
            RunRuntimeStatus.RUNNING,
            RunRuntimeStatus.STOPPING,
            RunRuntimeStatus.PAUSED,
        }:
            raise RuntimeError("Cannot delete a running subagent")

        background_task_records = self._list_subagent_background_tasks(
            session_id=session_id,
            instance_id=agent.instance_id,
            run_id=agent.run_id,
        )
        if any(record.is_active for record in background_task_records):
            raise RuntimeError("Cannot delete a running subagent")

        task_ids = [
            record.envelope.task_id
            for record in self._task_repo.list_by_session(session_id)
            if record.envelope.trace_id == agent.run_id
        ]
        self._message_repo.delete_by_instance(agent.instance_id)
        if self._event_log is not None:
            self._event_log.delete_by_trace(agent.run_id)
        if self._run_state_repo is not None:
            self._run_state_repo.delete(agent.run_id)
        if self._shared_store is not None:
            self._shared_store.delete_for_subagent(
                instance_id=agent.instance_id,
                session_scope_id=build_instance_session_scope_id(
                    session_id,
                    agent.instance_id,
                ),
                role_scope_id=build_instance_role_scope_id(
                    session_id,
                    agent.role_id,
                    agent.instance_id,
                ),
                conversation_id=agent.conversation_id,
                task_ids=task_ids,
            )
            self._shared_store.delete_by_scope_key_prefix(
                ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id),
                READ_STATE_PREFIX + agent.conversation_id + ":",
            )
        self._approval_ticket_repo.delete_by_run(agent.run_id)
        for background_task_record in background_task_records:
            if self._background_task_repository is None:
                break
            self._background_task_repository.delete(
                background_task_record.background_task_id
            )
        self._delete_background_task_logs(
            session=session,
            background_task_records=background_task_records,
        )
        self._run_runtime_repo.delete(agent.run_id)
        if self._todo_service is not None:
            self._todo_service.delete_for_run(agent.run_id)
        for task_id in task_ids:
            self._task_repo.delete(task_id)
        self._agent_repo.delete_instance(agent.instance_id)
        if self._session_history_marker_repo is not None:
            self._session_history_marker_repo.delete_by_conversation(
                session_id,
                agent.conversation_id,
            )
        self._token_usage_repo.delete_by_run(agent.run_id)
        self._invalidate_list_sessions_cache()

    async def delete_normal_mode_subagent_async(
        self, session_id: str, instance_id: str
    ) -> None:
        await asyncio.to_thread(
            self.delete_normal_mode_subagent, session_id, instance_id
        )

    def _delete_background_task_logs(
        self,
        *,
        session: SessionRecord,
        background_task_records: tuple[BackgroundTaskRecord, ...],
    ) -> None:
        if self._workspace_manager is None or not background_task_records:
            return
        workspace = self._workspace_manager.resolve(
            session_id=session.session_id,
            role_id="background-task-cleanup",
            instance_id=None,
            workspace_id=session.workspace_id,
        )
        for record in background_task_records:
            log_path = str(record.log_path).strip()
            if not log_path:
                continue
            try:
                resolved_log_path = workspace.resolve_read_path(log_path)
            except Exception:
                continue
            if not resolved_log_path.is_file():
                continue
            with contextlib.suppress(OSError):
                resolved_log_path.unlink()

    def get_session(self, session_id: str) -> SessionRecord:
        cached = self._get_session_from_list_cache(session_id, allow_stale=False)
        if cached is not None:
            return cached
        return self._with_terminal_run_projection(
            self._with_subagent_count_projection(
                self._with_auto_session_title(self._session_repo.get(session_id))
            )
        )

    async def get_session_async(self, session_id: str) -> SessionRecord:
        cached = self._get_session_from_list_cache(session_id, allow_stale=True)
        if cached is not None:
            self._ensure_list_sessions_refresh_task_if_stale()
            return cached
        return self._with_terminal_run_projection(
            await self._with_subagent_count_projection_async(
                self._with_auto_session_title(
                    await self._session_repo.get_async(session_id)
                )
            )
        )

    def assert_session_exists(self, session_id: str) -> None:
        _ = self._session_repo.get(session_id)

    async def assert_session_exists_async(self, session_id: str) -> None:
        _ = await self._session_repo.get_async(session_id)

    def _list_subagent_background_tasks(
        self,
        *,
        session_id: str,
        instance_id: str,
        run_id: str,
    ) -> tuple[BackgroundTaskRecord, ...]:
        if self._background_task_repository is None:
            return ()
        return tuple(
            record
            for record in self._background_task_repository.list_by_session(session_id)
            if record.kind == BackgroundTaskKind.SUBAGENT
            and (
                record.subagent_instance_id == instance_id
                or record.subagent_run_id == run_id
            )
        )

    def mark_latest_terminal_run_viewed(self, session_id: str) -> None:
        record = self._session_repo.get(session_id)
        cached = self._get_session_from_list_cache(session_id, allow_stale=True)
        if cached is not None and cached.latest_terminal_run_id:
            self._mark_cached_terminal_run_viewed(record, cached.latest_terminal_run_id)
            return
        runtimes = self._run_runtime_repo.list_by_session(session_id)
        background_tasks = (
            self._background_task_repository.list_by_session(session_id)
            if self._background_task_repository is not None
            else ()
        )
        latest_terminal = self._latest_terminal_run_from_preloaded(
            runtimes,
            self._subagent_run_ids_from_records(
                runtimes=runtimes,
                background_tasks=background_tasks,
            ),
        )
        if latest_terminal is None:
            return
        self._mark_cached_terminal_run_viewed(record, latest_terminal.run_id)

    async def mark_latest_terminal_run_viewed_async(self, session_id: str) -> None:
        record = await self._session_repo.get_async(session_id)
        cached = self._get_session_from_list_cache(session_id, allow_stale=True)
        if cached is not None and cached.latest_terminal_run_id:
            await self._mark_cached_terminal_run_viewed_async(
                record,
                cached.latest_terminal_run_id,
            )
            return
        runtimes = await self._run_runtime_repo.list_by_session_async(session_id)
        background_tasks = (
            await self._background_task_repository.list_by_session_async(session_id)
            if self._background_task_repository is not None
            else ()
        )
        latest_terminal = self._latest_terminal_run_from_preloaded(
            runtimes,
            self._subagent_run_ids_from_records(
                runtimes=runtimes,
                background_tasks=background_tasks,
            ),
        )
        if latest_terminal is None:
            return
        await self._mark_cached_terminal_run_viewed_async(
            record,
            latest_terminal.run_id,
        )

    def _mark_cached_terminal_run_viewed(
        self,
        record: SessionRecord,
        run_id: str,
    ) -> None:
        self._session_repo.mark_terminal_run_viewed(record.session_id, run_id)
        self._merge_terminal_view_record_into_list_cache(record, run_id)

    async def _mark_cached_terminal_run_viewed_async(
        self,
        record: SessionRecord,
        run_id: str,
    ) -> None:
        await self._session_repo.mark_terminal_run_viewed_async(
            record.session_id,
            run_id,
        )
        self._merge_terminal_view_record_into_list_cache(record, run_id)

    def _merge_terminal_view_record_into_list_cache(
        self,
        record: SessionRecord,
        run_id: str,
    ) -> None:
        cached = self._get_session_from_list_cache(record.session_id, allow_stale=True)
        if cached is not None and cached.latest_terminal_run_id == run_id:
            self._merge_record_into_list_sessions_cache(
                cached.model_copy(
                    update={
                        "last_viewed_terminal_run_id": run_id,
                        "has_unread_terminal_run": False,
                    }
                )
            )
            return
        self._merge_record_into_list_sessions_cache(
            record.model_copy(update={"last_viewed_terminal_run_id": run_id})
        )

    def list_sessions_by_workspace(
        self, workspace_id: str
    ) -> tuple[SessionRecord, ...]:
        return self._session_repo.list_by_workspace(workspace_id)

    def list_sessions_by_project(
        self,
        *,
        project_kind: ProjectKind,
        project_id: str,
    ) -> tuple[dict[str, object], ...]:
        return tuple(
            record.model_dump(mode="json")
            for record in self.list_sessions()
            if record.project_kind == project_kind and record.project_id == project_id
        )

    def list_normal_mode_subagents(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        session = self._session_repo.get(session_id)
        if session.session_mode != SessionMode.NORMAL:
            return ()
        root_tasks_by_run: dict[str, object] = {}
        for task in self._task_repo.list_by_session(session_id):
            if task.envelope.parent_task_id is None:
                root_tasks_by_run[task.envelope.trace_id] = task
        records = [
            record
            for record in self._agent_repo.list_by_session(session_id)
            if self._is_normal_mode_subagent_record(record, session=session)
        ]
        records.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        run_ids = tuple(dict.fromkeys(record.run_id for record in records))
        runtime_by_run = {
            runtime.run_id: runtime
            for runtime in self._run_runtime_repo.list_by_session(session_id)
            if runtime.run_id in run_ids
        }
        run_state_by_run = (
            {
                run_state.run_id: run_state
                for run_state in self._run_state_repo.list_by_session(session_id)
                if run_state.run_id in run_ids
            }
            if self._run_state_repo is not None
            else {}
        )
        approval_counts = (
            self._approval_ticket_repo.count_open_by_run_ids(run_ids)
            if self._approval_ticket_repo is not None
            else {}
        )
        question_counts = (
            self._user_question_repo.count_open_by_run_ids(run_ids)
            if self._user_question_repo is not None
            else {}
        )
        return tuple(
            {
                **self._normal_mode_subagent_projection(
                    record,
                    runtime_by_run=runtime_by_run,
                    run_state_by_run=run_state_by_run,
                    approval_counts=approval_counts,
                    question_counts=question_counts,
                ),
                "title": self._subagent_title_for_run(
                    run_id=record.run_id,
                    root_tasks_by_run=root_tasks_by_run,
                ),
            }
            for record in records
        )

    async def list_normal_mode_subagents_async(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self.list_normal_mode_subagents, session_id)

    async def stream_normal_mode_subagent_events(
        self,
        session_id: str,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        session = self._session_repo.get(session_id)
        if session.session_mode != SessionMode.NORMAL:
            return

        queue = (
            self._run_event_hub.subscribe_session(session_id)
            if self._run_event_hub is not None
            else None
        )
        replay_high_watermark = max(0, int(after_event_id))
        subagent_run_ids = self._subagent_run_ids(session_id)
        try:
            if self._event_log is not None:
                known_rows = (
                    await self._event_log.list_by_session_run_ids_after_id_async(
                        session_id,
                        tuple(sorted(subagent_run_ids)),
                        replay_high_watermark,
                    )
                    if subagent_run_ids
                    else ()
                )
                legacy_rows = await self._event_log.list_subagent_run_events_by_session_after_id_async(
                    session_id,
                    replay_high_watermark,
                )
                rows_by_id: dict[int, Mapping[str, object]] = {}
                for row in (*known_rows, *legacy_rows):
                    row_id = row.get("id")
                    if isinstance(row_id, int):
                        rows_by_id[row_id] = row
                rows = tuple(rows_by_id[key] for key in sorted(rows_by_id))
                for row in rows:
                    event = self._run_event_from_log_row(row)
                    if event is None:
                        continue
                    if event.event_id is not None:
                        replay_high_watermark = max(
                            replay_high_watermark,
                            event.event_id,
                        )
                    yield event

            if queue is None:
                return

            while True:
                event = await queue.get()
                if event.session_id != session_id:
                    continue
                if (
                    event.run_id not in subagent_run_ids
                    and not self._is_legacy_subagent_run_id(event.run_id)
                ):
                    subagent_run_ids = self._subagent_run_ids(session_id)
                if (
                    event.run_id not in subagent_run_ids
                    and not self._is_legacy_subagent_run_id(event.run_id)
                ):
                    continue
                event_id = event.event_id
                if event_id is not None and event_id <= replay_high_watermark:
                    continue
                if event_id is not None:
                    replay_high_watermark = max(
                        replay_high_watermark,
                        event_id,
                    )
                yield event
        finally:
            if queue is not None and self._run_event_hub is not None:
                self._run_event_hub.unsubscribe_session(session_id, queue)

    def list_agents_in_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        session = self._session_repo.get(session_id)
        latest_by_role: dict[str, AgentRuntimeRecord] = {}
        for record in self._agent_repo.list_by_session(session_id):
            if self._is_normal_mode_subagent_record(record, session=session):
                continue
            existing = latest_by_role.get(record.role_id)
            if existing is None or (
                record.updated_at,
                record.created_at,
            ) >= (
                existing.updated_at,
                existing.created_at,
            ):
                latest_by_role[record.role_id] = record
        return tuple(
            self._agent_projection(latest_by_role[role_id])
            for role_id in sorted(latest_by_role.keys())
        )

    async def list_agents_in_session_async(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        session = await self._session_repo.get_async(session_id)
        latest_by_role: dict[str, AgentRuntimeRecord] = {}
        for record in await self._agent_repo.list_by_session_async(session_id):
            if self._is_normal_mode_subagent_record(record, session=session):
                continue
            existing = latest_by_role.get(record.role_id)
            if existing is None or (
                record.updated_at,
                record.created_at,
            ) >= (
                existing.updated_at,
                existing.created_at,
            ):
                latest_by_role[record.role_id] = record

        projections: list[dict[str, object]] = []
        for role_id in sorted(latest_by_role.keys()):
            projections.append(
                await self._agent_projection_async(latest_by_role[role_id])
            )
        return tuple(projections)

    async def get_agent_reflection_async(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        agent = await self._require_session_agent_async(session_id, instance_id)
        return await self._reflection_projection_async(agent)

    async def refresh_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        if self._subagent_reflection_service is None or self._role_registry is None:
            raise RuntimeError("Subagent reflection is not available")
        agent = await self._require_session_agent_async(session_id, instance_id)
        if self._role_registry.is_coordinator_role(agent.role_id):
            raise RuntimeError("Coordinator reflection refresh is not supported")
        role = self._role_registry.get(agent.role_id)
        record = await self._subagent_reflection_service.refresh_reflection(
            role=role,
            workspace_id=agent.workspace_id,
            conversation_id=agent.conversation_id,
        )
        return await self._reflection_projection_async(
            agent,
            role_record=record,
            source="manual",
        )

    async def update_agent_reflection_async(
        self,
        session_id: str,
        instance_id: str,
        *,
        summary: str,
    ) -> dict[str, object]:
        if self._role_memory_service is None:
            raise RuntimeError("Subagent reflection is not available")
        agent = await self._require_session_agent_async(session_id, instance_id)
        record = await self._role_memory_service.update_reflection_memory_async(
            role_id=agent.role_id,
            workspace_id=agent.workspace_id,
            content_markdown=summary,
        )
        return await self._reflection_projection_async(
            agent,
            role_record=record,
            source="manual_edit",
        )

    async def delete_agent_reflection_async(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        if self._role_memory_service is None:
            raise RuntimeError("Subagent reflection is not available")
        agent = await self._require_session_agent_async(session_id, instance_id)
        await self._role_memory_service.delete_reflection_memory_async(
            role_id=agent.role_id,
            workspace_id=agent.workspace_id,
        )
        return await self._reflection_projection_async(agent, source="manual_delete")

    def get_agent_messages(
        self, session_id: str, instance_id: str
    ) -> list[dict[str, object]]:
        messages = cast(
            list[dict[str, object]],
            self._message_repo.get_messages_for_instance(
                session_id,
                instance_id,
                include_cleared=True,
                include_hidden_from_context=True,
            ),
        )
        try:
            agent = self._agent_repo.get_instance(instance_id)
        except KeyError:
            return [
                self._project_message_timeline_entry(message) for message in messages
            ]
        for message in messages:
            if "role_id" not in message or not message.get("role_id"):
                message["role_id"] = agent.role_id
        markers = self._list_agent_history_markers(
            session_id=session_id,
            conversation_id=agent.conversation_id,
        )
        return self._build_agent_timeline_entries(
            messages=messages,
            markers=markers,
        )

    async def get_agent_messages_async(
        self, session_id: str, instance_id: str
    ) -> list[dict[str, object]]:
        return await asyncio.to_thread(self.get_agent_messages, session_id, instance_id)

    def get_global_events(self, session_id: str) -> list[dict[str, object]]:
        if self._event_log is None:
            return []
        events = self._event_log.list_by_session(session_id)
        return cast(list[dict[str, object]], list(events))

    async def get_global_events_async(self, session_id: str) -> list[dict[str, object]]:
        return await asyncio.to_thread(self.get_global_events, session_id)

    def _get_round_projection_events(self, session_id: str) -> list[dict[str, object]]:
        if self._event_log is None:
            return []
        events = self._event_log.list_by_session_event_types(
            session_id,
            ROUND_PROJECTION_EVENT_TYPES,
        )
        return cast(list[dict[str, object]], list(events))

    def _get_round_projection_events_for_runs(
        self,
        session_id: str,
        run_ids: tuple[str, ...],
    ) -> list[dict[str, object]]:
        if self._event_log is None:
            return []
        events = self._event_log.list_by_session_run_ids_event_types(
            session_id,
            run_ids,
            ROUND_PROJECTION_EVENT_TYPES,
        )
        return cast(list[dict[str, object]], list(events))

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return cast(
            list[dict[str, object]],
            self._message_repo.get_messages_by_session(session_id),
        )

    async def get_session_messages_async(
        self, session_id: str
    ) -> list[dict[str, object]]:
        return await asyncio.to_thread(self.get_session_messages, session_id)

    def clear_session_messages(self, session_id: str) -> int:
        _ = self._session_repo.get(session_id)
        messages = self._message_repo.get_messages_by_session(session_id)
        count = len(messages)
        if self._session_history_marker_repo is not None:
            self._session_history_marker_repo.create_clear_marker(session_id)
        else:
            self._message_repo.delete_by_session(session_id)
            self._token_usage_repo.delete_by_session(session_id)
        return count

    def _get_session_history_markers(
        self,
        session_id: str,
    ) -> list[dict[str, object]]:
        if self._session_history_marker_repo is None:
            return []
        markers = self._session_history_marker_repo.list_by_session(session_id)
        return [marker.model_dump(mode="json") for marker in markers]

    def _list_agent_history_markers(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> tuple[SessionHistoryMarkerRecord, ...]:
        if self._session_history_marker_repo is None:
            return ()
        markers = self._session_history_marker_repo.list_by_session(session_id)
        return tuple(
            marker
            for marker in markers
            if marker.marker_type == SessionHistoryMarkerType.CLEAR
            or (
                marker.marker_type == SessionHistoryMarkerType.COMPACTION
                and marker.metadata.get("conversation_id") == conversation_id
            )
        )

    @staticmethod
    def _project_message_timeline_entry(
        message: dict[str, object],
    ) -> dict[str, object]:
        return {
            "entry_type": "message",
            **message,
        }

    @staticmethod
    def _project_history_marker_entry(
        marker: SessionHistoryMarkerRecord,
    ) -> dict[str, object]:
        label = _history_marker_label(marker)
        return {
            "entry_type": "marker",
            "marker_id": marker.marker_id,
            "marker_type": marker.marker_type.value,
            "created_at": marker.created_at.isoformat(),
            "label": label,
        }

    def _build_agent_timeline_entries(
        self,
        *,
        messages: list[dict[str, object]],
        markers: tuple[SessionHistoryMarkerRecord, ...],
    ) -> list[dict[str, object]]:
        clear_markers = [
            marker
            for marker in markers
            if marker.marker_type == SessionHistoryMarkerType.CLEAR
        ]
        compaction_markers = {
            marker.marker_id: marker
            for marker in markers
            if marker.marker_type == SessionHistoryMarkerType.COMPACTION
        }
        clear_index = 0
        entries: list[dict[str, object]] = []

        for index, message in enumerate(messages):
            created_at = str(message.get("created_at") or "")
            while clear_index < len(clear_markers):
                marker = clear_markers[clear_index]
                if marker.created_at.isoformat() > created_at:
                    break
                entries.append(self._project_history_marker_entry(marker))
                clear_index += 1

            entries.append(self._project_message_timeline_entry(message))
            hidden_marker_id = str(message.get("hidden_marker_id") or "")
            if not hidden_marker_id:
                continue
            next_hidden_marker_id = ""
            if index + 1 < len(messages):
                next_hidden_marker_id = str(
                    messages[index + 1].get("hidden_marker_id") or ""
                )
            if next_hidden_marker_id == hidden_marker_id:
                continue
            marker = compaction_markers.get(hidden_marker_id)
            if marker is None:
                continue
            entries.append(self._project_history_marker_entry(marker))

        while clear_index < len(clear_markers):
            entries.append(
                self._project_history_marker_entry(clear_markers[clear_index])
            )
            clear_index += 1
        return entries

    def _with_terminal_run_projection(self, record: SessionRecord) -> SessionRecord:
        latest_terminal = self._latest_terminal_run(record.session_id)
        if latest_terminal is None:
            return record
        last_viewed_run_id = str(record.last_viewed_terminal_run_id or "").strip()
        return record.model_copy(
            update={
                "latest_terminal_run_id": latest_terminal.run_id,
                "latest_terminal_run_status": latest_terminal.status.value,
                "latest_terminal_run_updated_at": latest_terminal.updated_at,
                "has_unread_terminal_run": last_viewed_run_id != latest_terminal.run_id,
            }
        )

    def _with_subagent_count_projection(self, record: SessionRecord) -> SessionRecord:
        if record.session_mode != SessionMode.NORMAL:
            return record.model_copy(update={"subagent_session_count": 0})
        counts = self._agent_repo.count_normal_mode_subagents_by_session_ids(
            (record.session_id,)
        )
        return record.model_copy(
            update={"subagent_session_count": counts.get(record.session_id, 0)}
        )

    async def _with_subagent_count_projection_async(
        self, record: SessionRecord
    ) -> SessionRecord:
        if record.session_mode != SessionMode.NORMAL:
            return record.model_copy(update={"subagent_session_count": 0})
        counts = (
            await self._agent_repo.count_normal_mode_subagents_by_session_ids_async(
                (record.session_id,)
            )
        )
        return record.model_copy(
            update={"subagent_session_count": counts.get(record.session_id, 0)}
        )

    def _with_terminal_run_projection_from_preloaded(
        self,
        record: SessionRecord,
        *,
        runtimes: tuple[RunRuntimeRecord, ...],
        excluded_run_ids: set[str],
    ) -> SessionRecord:
        latest_terminal = self._latest_terminal_run_from_preloaded(
            runtimes,
            excluded_run_ids,
        )
        if latest_terminal is None:
            return record
        last_viewed_run_id = str(record.last_viewed_terminal_run_id or "").strip()
        return record.model_copy(
            update={
                "latest_terminal_run_id": latest_terminal.run_id,
                "latest_terminal_run_status": latest_terminal.status.value,
                "latest_terminal_run_updated_at": latest_terminal.updated_at,
                "has_unread_terminal_run": last_viewed_run_id != latest_terminal.run_id,
            }
        )

    def _latest_terminal_run(self, session_id: str) -> RunRuntimeRecord | None:
        excluded_run_ids = self._subagent_run_ids(session_id)
        for runtime in self._run_runtime_repo.list_by_session(session_id):
            if runtime.run_id in excluded_run_ids:
                continue
            if runtime.status in TERMINAL_RUN_STATUSES:
                return runtime
        return None

    @staticmethod
    def _latest_terminal_run_from_preloaded(
        runtimes: tuple[RunRuntimeRecord, ...],
        excluded_run_ids: set[str],
    ) -> RunRuntimeRecord | None:
        for runtime in sorted(runtimes, key=lambda item: item.updated_at, reverse=True):
            if runtime.run_id in excluded_run_ids:
                continue
            if runtime.status in TERMINAL_RUN_STATUSES:
                return runtime
        return None

    def _select_active_run(
        self, session_id: str
    ) -> tuple[str, RunRuntimeRecord] | None:
        excluded_run_ids = self._subagent_run_ids(session_id)
        hinted_run_id = (
            self._active_run_registry.get_active_run_id(session_id)
            if self._active_run_registry is not None
            else None
        )
        if hinted_run_id and hinted_run_id not in excluded_run_ids:
            hinted_runtime = self._run_runtime_repo.get(hinted_run_id)
            if hinted_runtime is not None:
                return hinted_run_id, hinted_runtime

        runtimes = list(self._run_runtime_repo.list_by_session(session_id))
        if not runtimes:
            return None
        runtimes.sort(key=lambda item: item.updated_at, reverse=True)
        for runtime in runtimes:
            if runtime.run_id in excluded_run_ids:
                continue
            if runtime.status in {
                RunRuntimeStatus.RUNNING,
                RunRuntimeStatus.STOPPING,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
                RunRuntimeStatus.QUEUED,
            }:
                return runtime.run_id, runtime
        for runtime in runtimes:
            if runtime.run_id in excluded_run_ids:
                continue
            if runtime.status not in {
                RunRuntimeStatus.COMPLETED,
                RunRuntimeStatus.FAILED,
            }:
                continue
            if self._has_background_tasks(runtime.run_id):
                return runtime.run_id, runtime
        return None

    def _select_active_run_from_preloaded(
        self,
        *,
        session_id: str,
        runtimes: tuple[RunRuntimeRecord, ...],
        excluded_run_ids: set[str],
        active_background_run_ids: set[str],
    ) -> tuple[str, RunRuntimeRecord] | None:
        hinted_run_id = (
            self._active_run_registry.get_active_run_id(session_id)
            if self._active_run_registry is not None
            else None
        )
        sorted_runtimes = sorted(
            runtimes, key=lambda item: item.updated_at, reverse=True
        )
        if hinted_run_id and hinted_run_id not in excluded_run_ids:
            hinted_runtime = next(
                (
                    runtime
                    for runtime in sorted_runtimes
                    if runtime.run_id == hinted_run_id
                ),
                None,
            )
            if hinted_runtime is not None:
                return hinted_run_id, hinted_runtime

        for runtime in sorted_runtimes:
            if runtime.run_id in excluded_run_ids:
                continue
            if runtime.status in {
                RunRuntimeStatus.RUNNING,
                RunRuntimeStatus.STOPPING,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
                RunRuntimeStatus.QUEUED,
            }:
                return runtime.run_id, runtime
        for runtime in sorted_runtimes:
            if runtime.run_id in excluded_run_ids:
                continue
            if runtime.status not in {
                RunRuntimeStatus.COMPLETED,
                RunRuntimeStatus.FAILED,
            }:
                continue
            if runtime.run_id in active_background_run_ids:
                return runtime.run_id, runtime
        return None

    def _subagent_run_ids(self, session_id: str) -> set[str]:
        try:
            session = self._session_repo.get(session_id)
        except KeyError:
            session = None
        run_ids = {
            runtime.run_id
            for runtime in self._run_runtime_repo.list_by_session(session_id)
            if self._is_legacy_subagent_run_id(runtime.run_id)
        }
        if session is not None:
            run_ids.update(
                record.run_id
                for record in self._agent_repo.list_by_session(session_id)
                if self._is_normal_mode_subagent_record(record, session=session)
            )
        if self._background_task_repository is None:
            return run_ids
        return run_ids | {
            record.subagent_run_id
            for record in self._background_task_repository.list_by_session(session_id)
            if (
                record.kind == BackgroundTaskKind.SUBAGENT
                and record.subagent_run_id is not None
            )
        }

    @staticmethod
    def _subagent_run_ids_from_records(
        *,
        runtimes: tuple[RunRuntimeRecord, ...],
        background_tasks: tuple[BackgroundTaskRecord, ...],
    ) -> set[str]:
        run_ids = {
            runtime.run_id
            for runtime in runtimes
            if str(runtime.run_id).strip().startswith("subagent_run_")
        }
        run_ids.update(
            record.subagent_run_id
            for record in background_tasks
            if (
                record.kind == BackgroundTaskKind.SUBAGENT
                and record.subagent_run_id is not None
            )
        )
        return run_ids

    def _subagent_run_ids_by_session_ids(
        self,
        *,
        session_ids: tuple[str, ...],
        runtimes_by_session: Mapping[str, tuple[RunRuntimeRecord, ...]],
        background_tasks_by_session: Mapping[str, tuple[BackgroundTaskRecord, ...]],
    ) -> dict[str, set[str]]:
        return {
            session_id: self._subagent_run_ids_from_records(
                runtimes=runtimes_by_session.get(session_id, ()),
                background_tasks=background_tasks_by_session.get(session_id, ()),
            )
            for session_id in session_ids
        }

    @staticmethod
    def _active_background_run_ids(
        background_tasks_by_session: Mapping[str, tuple[BackgroundTaskRecord, ...]],
    ) -> set[str]:
        return {
            record.run_id
            for records in background_tasks_by_session.values()
            for record in records
            if record.execution_mode == "background" and record.is_active
        }

    def _has_background_tasks(self, run_id: str) -> bool:
        if self._background_task_repository is None:
            return False
        return any(
            record.execution_mode == "background" and record.is_active
            for record in self._background_task_repository.list_by_run(run_id)
        )

    def _paused_subagent_snapshot(
        self,
        runtime: RunRuntimeRecord,
    ) -> dict[str, object] | None:
        if runtime.phase not in {
            RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
            RunRuntimePhase.SUBAGENT_RUNNING,
        }:
            return None
        instance_id = runtime.active_subagent_instance_id or runtime.active_instance_id
        if not instance_id:
            return None
        role_id = runtime.active_role_id or ""
        if self._is_reserved_system_role(role_id):
            return None
        try:
            agent = self._agent_repo.get_instance(instance_id)
        except KeyError:
            return {
                "instance_id": instance_id,
                "role_id": role_id,
                "task_id": runtime.active_task_id,
            }
        if self._is_reserved_system_role(agent.role_id):
            return None
        return {
            "instance_id": agent.instance_id,
            "role_id": agent.role_id,
            "task_id": runtime.active_task_id,
        }

    def _is_reserved_system_role(self, role_id: str) -> bool:
        safe_role_id = str(role_id or "").strip()
        if not safe_role_id:
            return False
        if self._role_registry is not None and (
            self._role_registry.is_coordinator_role(safe_role_id)
            or self._role_registry.is_main_agent_role(safe_role_id)
        ):
            return True
        normalized = safe_role_id.casefold()
        return (
            normalized in _legacy_coordinator_identifiers()
            or normalized in _main_agent_identifiers()
        )

    def _require_session_agent(
        self,
        session_id: str,
        instance_id: str,
    ) -> AgentRuntimeRecord:
        agent = self._agent_repo.get_instance(instance_id)
        if agent.session_id != session_id:
            raise KeyError(instance_id)
        return agent

    async def _require_session_agent_async(
        self,
        session_id: str,
        instance_id: str,
    ) -> AgentRuntimeRecord:
        agent = await self._agent_repo.get_instance_async(instance_id)
        if agent.session_id != session_id:
            raise KeyError(instance_id)
        return agent

    def _agent_projection(self, record: AgentRuntimeRecord) -> dict[str, object]:
        reflection = self._reflection_projection(record)
        return {
            **record.model_dump(mode="json"),
            "reflection_summary_preview": reflection["preview"],
            "reflection_updated_at": reflection["updated_at"],
        }

    async def _agent_projection_async(
        self,
        record: AgentRuntimeRecord,
    ) -> dict[str, object]:
        reflection = await self._reflection_projection_async(record)
        return {
            **record.model_dump(mode="json"),
            "reflection_summary_preview": reflection["preview"],
            "reflection_updated_at": reflection["updated_at"],
        }

    def _normal_mode_subagent_projection(
        self,
        record: AgentRuntimeRecord,
        *,
        runtime_by_run: Mapping[str, RunRuntimeRecord] | None = None,
        run_state_by_run: Mapping[str, RunStateRecord] | None = None,
        approval_counts: Mapping[str, int] | None = None,
        question_counts: Mapping[str, int] | None = None,
    ) -> dict[str, object]:
        projected = self._agent_projection(record)
        runtime = (
            runtime_by_run.get(record.run_id)
            if runtime_by_run is not None
            else self._run_runtime_repo.get(record.run_id)
        )
        run_state = (
            run_state_by_run.get(record.run_id)
            if run_state_by_run is not None
            else (
                self._run_state_repo.get_run_state(record.run_id)
                if self._run_state_repo is not None
                else None
            )
        )
        approval_count = (
            approval_counts.get(record.run_id, 0)
            if approval_counts is not None
            else None
        )
        if approval_count is None:
            approval_count = 0
            if runtime is not None and self._approval_ticket_repo is not None:
                approval_count = len(
                    self._approval_ticket_repo.list_open_by_run(runtime.run_id)
                )
        question_count = (
            question_counts.get(record.run_id, 0)
            if question_counts is not None
            else self._pending_user_question_count(record.run_id)
        )
        stream_connected = False
        if self._run_event_hub is not None:
            stream_connected = self._run_event_hub.has_subscribers(
                record.run_id
            ) or self._run_event_hub.has_session_subscribers(record.session_id)
        projected["run_status"] = (
            runtime.status.value if runtime is not None else projected["status"]
        )
        projected["run_phase"] = (
            self._public_phase(runtime, approval_count, question_count)
            if runtime is not None
            else ""
        )
        projected["last_event_id"] = (
            int(run_state.last_event_id) if run_state is not None else 0
        )
        projected["checkpoint_event_id"] = (
            int(run_state.checkpoint_event_id) if run_state is not None else 0
        )
        projected["stream_connected"] = stream_connected
        return projected

    @staticmethod
    def _reflection_projection(
        record: AgentRuntimeRecord,
        *,
        source: str = "stored",
        role_record: object | None = None,
    ) -> dict[str, object]:
        return _build_reflection_projection(
            record=record,
            source=source,
            memory=role_record,
        )

    async def _reflection_projection_async(
        self,
        record: AgentRuntimeRecord,
        *,
        source: str = "stored",
        role_record: object | None = None,
    ) -> dict[str, object]:
        memory = role_record
        if memory is None and self._role_memory_service is not None:
            memory = await self._role_memory_service.get_reflection_record_async(
                role_id=record.role_id,
                workspace_id=record.workspace_id,
            )
        return _build_reflection_projection(
            record=record,
            source=source,
            memory=memory,
        )

    def _public_phase(
        self,
        runtime: RunRuntimeRecord,
        approval_count: int,
        question_count: int = 0,
    ) -> str:
        if runtime.status == RunRuntimeStatus.STOPPING:
            return "stopping"
        if approval_count > 0:
            return "awaiting_tool_approval"
        if (
            question_count > 0
            or runtime.phase == RunRuntimePhase.AWAITING_MANUAL_ACTION
        ):
            return "awaiting_manual_action"
        if runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP:
            return "awaiting_subagent_followup"
        if runtime.phase == RunRuntimePhase.AWAITING_RECOVERY:
            return "awaiting_recovery"
        if runtime.status == RunRuntimeStatus.RUNNING:
            return "running"
        if runtime.status == RunRuntimeStatus.PAUSED:
            return (
                "awaiting_manual_action"
                if runtime.phase == RunRuntimePhase.AWAITING_MANUAL_ACTION
                else (
                    "awaiting_subagent_followup"
                    if runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
                    else "awaiting_recovery"
                )
            )
        if runtime.status == RunRuntimeStatus.STOPPED:
            return "stopped"
        if runtime.status == RunRuntimeStatus.QUEUED:
            return "queued"
        if runtime.status == RunRuntimeStatus.COMPLETED:
            return "completed"
        if runtime.status == RunRuntimeStatus.FAILED:
            return "failed"
        return runtime.phase.value

    def _pending_user_question_count(self, run_id: str) -> int:
        if self._user_question_repo is None:
            return 0
        return len(self._user_question_repo.list_by_run(run_id))

    def _pending_user_question_counts_by_run(self, session_id: str) -> dict[str, int]:
        if self._user_question_repo is None:
            return {}
        counts: dict[str, int] = {}
        for record in self._user_question_repo.list_by_session(session_id):
            counts[record.run_id] = counts.get(record.run_id, 0) + 1
        return counts

    def _list_resolvable_user_questions_for_session(
        self, session_id: str
    ) -> tuple[UserQuestionRequestRecord, ...]:
        if self._user_question_repo is None:
            return ()
        records = self._user_question_repo.list_by_session(session_id)
        if self._run_runtime_repo is None:
            return records
        return tuple(
            record
            for record in records
            if self._run_runtime_repo.get(record.run_id) is not None
        )

    @staticmethod
    def _is_runtime_publicly_recoverable(runtime: RunRuntimeRecord) -> bool:
        return runtime.is_recoverable and runtime.status != RunRuntimeStatus.STOPPING

    @staticmethod
    def _subagent_title_for_run(
        *,
        run_id: str,
        root_tasks_by_run: dict[str, object],
    ) -> str:
        root_task = root_tasks_by_run.get(run_id)
        if root_task is None:
            return ""
        envelope = getattr(root_task, "envelope", None)
        title = str(getattr(envelope, "title", "") or "").strip()
        if title:
            return title
        objective = str(getattr(envelope, "objective", "") or "").strip()
        if not objective:
            return ""
        return objective[:80]

    @staticmethod
    def _run_event_from_log_row(row: Mapping[str, object]) -> RunEvent | None:
        row_id = row.get("id")
        if not isinstance(row_id, int):
            return None
        try:
            event_type = RunEventType(str(row["event_type"]))
        except (KeyError, ValueError):
            return None
        trace_id = str(row.get("trace_id") or "").strip()
        session_id = str(row.get("session_id") or "").strip()
        if not trace_id or not session_id:
            return None
        return RunEvent(
            session_id=session_id,
            run_id=trace_id,
            trace_id=trace_id,
            task_id=(str(row["task_id"]) if row.get("task_id") is not None else None),
            instance_id=(
                str(row["instance_id"]) if row.get("instance_id") is not None else None
            ),
            event_type=event_type,
            payload_json=str(row.get("payload_json") or "{}"),
            event_id=row_id,
        )

    @staticmethod
    def _is_legacy_subagent_run_id(run_id: str) -> bool:
        return str(run_id or "").strip().startswith("subagent_run_")

    def _is_subagent_run_id(self, run_id: str) -> bool:  # pragma: no cover
        safe_run_id = str(run_id or "").strip()
        if not safe_run_id:
            return False
        if self._is_legacy_subagent_run_id(safe_run_id):
            return True
        records = self._agent_repo.list_by_run(safe_run_id)
        if not records:
            return False
        try:
            session = self._session_repo.get(records[0].session_id)
        except KeyError:
            return False
        return any(
            self._is_normal_mode_subagent_record(record, session=session)
            for record in records
        )

    def _is_normal_mode_subagent_record(
        self,
        record: AgentRuntimeRecord,
        *,
        session: SessionRecord,
    ) -> bool:
        return session.session_mode == SessionMode.NORMAL and (
            bool(str(record.parent_instance_id or "").strip())
            or self._is_legacy_subagent_run_id(record.run_id)
        )


def _build_reflection_projection(
    *,
    record: AgentRuntimeRecord,
    source: str,
    memory: object | None,
) -> dict[str, object]:
    if memory is None:
        return {
            "instance_id": record.instance_id,
            "role_id": record.role_id,
            "summary": "",
            "preview": "",
            "updated_at": None,
            "source": source,
        }
    updated_at = getattr(memory, "updated_at", None)
    summary = str(getattr(memory, "content_markdown", "") or "").strip()
    return {
        "instance_id": record.instance_id,
        "role_id": record.role_id,
        "summary": summary,
        "preview": _reflection_preview_from_text(summary),
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
        "source": source,
    }


def _reflection_preview_from_text(text: str, *, max_chars: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _history_marker_label(marker: SessionHistoryMarkerRecord) -> str:
    if marker.marker_type == SessionHistoryMarkerType.CLEAR:
        return "History cleared"
    if marker.metadata.get("compaction_strategy") == "rolling_summary":
        return "History compacted (rolling summary)"
    return "History compacted"
