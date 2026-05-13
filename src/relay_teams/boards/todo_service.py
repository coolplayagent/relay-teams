# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import JsonValue

from relay_teams.boards.todo_models import (
    BoardTodoAttempt,
    BoardTodoAttemptStatus,
    BoardTodoAttemptType,
    BoardTodoArchiveRequest,
    BoardTodoBoardResponse,
    BoardTodoConcurrencySnapshot,
    BoardTodoDiagnostic,
    BoardTodoDeltaResponse,
    BoardTodoExecutionPolicy,
    BoardTodoExecutionQueueTicket,
    BoardTodoExecutionWorkspacePreview,
    BoardTodoHandoffPrompt,
    BoardTodoHandoffTemplate,
    BoardTodoHandoffTemplateDeleteResponse,
    BoardTodoHandoffTemplateInput,
    BoardTodoHandoffTemplateKind,
    BoardTodoHandoffTemplateSettingsResponse,
    BoardTodoQueueKind,
    BoardTodoQueuePreview,
    BoardTodoQueueStatus,
    BoardTodoRuntimeTargetKind,
    BoardTodoRuntimeTargetOption,
    BoardTodoItem,
    BoardTodoLinkPullRequestRequest,
    BoardTodoMarkDoneRequest,
    BoardTodoPreviewRequestChangesRequest,
    BoardTodoPreviewRequestChangesResponse,
    BoardTodoPreviewStartRequest,
    BoardTodoPreviewStartResponse,
    BoardTodoScope,
    BoardTodoSource,
    BoardTodoSourceCreateRequest,
    BoardTodoSourceDeleteResponse,
    BoardTodoSourceGroup,
    BoardTodoSourceKind,
    BoardTodoSourceProvider,
    BoardTodoSourceSettingsResponse,
    BoardTodoSourceType,
    BoardTodoTemplateScope,
    BoardTodoSourceUpdateRequest,
    BoardTodoSourceView,
    BoardTodoStartRequest,
    BoardTodoSyncStatus,
    BoardTodoStatus,
    BoardTodoStatusCounts,
    BoardTodoStatusUpdateRequest,
    BoardTodoSyncChangesRequest,
)
from relay_teams.boards.todo_repository import BoardTodoRepository
from relay_teams.logger import get_logger
from relay_teams.media import content_parts_from_text
from relay_teams.persistence.db import run_async_blocking
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import SessionMode, SessionRecord
from relay_teams.triggers.github_client import GitHubApiError, JsonObject
from relay_teams.triggers.models import (
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
)
from relay_teams.workspace.workspace_models import WorkspaceRecord
from relay_teams.workspace.workspace_models import FileScopeBackend

LOGGER = get_logger(__name__)
_GITHUB_REMOTE_PATTERN = re.compile(
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)
_SOURCE_WORKSPACE_ACTIVE_LIMIT = 2
_RUNTIME_TARGET_ACTIVE_LIMIT = 1
_QUEUE_WORKER_INTERVAL_SECONDS = 2.0
_QUEUE_CLAIM_SECONDS = 60
_ACTIVE_RUNTIME_STATUSES = {
    RunRuntimeStatus.QUEUED,
    RunRuntimeStatus.RUNNING,
    RunRuntimeStatus.PAUSED,
    RunRuntimeStatus.STOPPING,
}


class WorkspaceServiceLike(Protocol):
    async def get_workspace_async(self, workspace_id: str) -> WorkspaceRecord:
        raise NotImplementedError

    async def fork_workspace_async(
        self,
        source_workspace_id: str,
        *,
        name: str,
        start_ref: str | None = None,
    ) -> WorkspaceRecord:
        raise NotImplementedError

    async def delete_workspace_with_options_async(
        self,
        *,
        workspace_id: str,
        remove_directory: bool = False,
    ) -> WorkspaceRecord:
        raise NotImplementedError


class GitHubTriggerServiceLike(Protocol):
    async def list_accounts_async(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        raise NotImplementedError

    async def resolve_account_token_async(self, account_id: str) -> str | None:
        raise NotImplementedError


class GitHubApiClientLike(Protocol):
    async def list_repository_issues(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        state: str = "all",
        updated_since: datetime | None = None,
    ) -> tuple[JsonObject, ...]:
        raise NotImplementedError

    async def list_repository_pull_requests(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        state: str = "all",
        updated_since: datetime | None = None,
    ) -> tuple[JsonObject, ...]:
        raise NotImplementedError

    async def get_repository_pull_request(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        pull_request_number: int,
    ) -> JsonObject:
        raise NotImplementedError

    async def list_issue_timeline_events(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> tuple[JsonObject, ...]:
        raise NotImplementedError


class SessionServiceLike(Protocol):
    async def create_session_async(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        raise NotImplementedError

    async def get_session_async(self, session_id: str) -> SessionRecord:
        raise NotImplementedError

    async def delete_session_async(
        self,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        raise NotImplementedError


class SessionRunServiceLike(Protocol):
    async def create_run_async(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        raise NotImplementedError

    async def ensure_run_started_async(self, run_id: str) -> None:
        raise NotImplementedError

    async def stop_run_async(self, run_id: str) -> None:
        raise NotImplementedError


class RunRuntimeRepositoryLike(Protocol):
    async def get_async(self, run_id: str) -> RunRuntimeRecord | None:
        raise NotImplementedError


class BoardTodoService:
    def __init__(
        self,
        *,
        repository: BoardTodoRepository,
        workspace_service: WorkspaceServiceLike,
        github_trigger_service: GitHubTriggerServiceLike,
        github_client: GitHubApiClientLike,
        session_service: SessionServiceLike,
        run_service: SessionRunServiceLike,
        run_runtime_repo: RunRuntimeRepositoryLike,
        get_shared_github_token: Callable[[], str | None] | None = None,
        get_shell_safety_policy_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._workspace_service = workspace_service
        self._github_trigger_service = github_trigger_service
        self._github_client = github_client
        self._session_service = session_service
        self._run_service = run_service
        self._run_runtime_repo = run_runtime_repo
        self._get_shared_github_token = get_shared_github_token or (lambda: None)
        self._get_shell_safety_policy_enabled = get_shell_safety_policy_enabled or (
            lambda: True
        )
        self._queue_worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._queue_worker_task is not None:
            return
        self._queue_worker_task = asyncio.create_task(self._queue_worker_loop())

    async def stop(self) -> None:
        if self._queue_worker_task is None:
            return
        self._queue_worker_task.cancel()
        try:
            await self._queue_worker_task
        except asyncio.CancelledError:
            # Expected shutdown path after cancelling the background queue worker.
            pass
        self._queue_worker_task = None

    async def _queue_worker_loop(self) -> None:
        while True:
            try:
                await self.drain_queue_once()
            except Exception as exc:
                LOGGER.warning("Board TODO queue worker iteration failed: %s", exc)
            await asyncio.sleep(_QUEUE_WORKER_INTERVAL_SECONDS)

    async def list_sources(
        self,
        *,
        workspace_id: str,
    ) -> BoardTodoSourceSettingsResponse:
        scope = await self._resolve_board_scope(workspace_id)
        diagnostics = await self._ensure_default_sources(scope)
        sources = await self._repository.list_sources_async(
            workspace_id=scope.board_workspace_id
        )
        visible_sources = _configurable_sources(sources)
        states = {
            state.source_id: state
            for state in await self._repository.list_source_states_async(
                workspace_id=scope.board_workspace_id
            )
        }
        return BoardTodoSourceSettingsResponse(
            workspace_id=scope.view_workspace_id,
            board_workspace_id=scope.board_workspace_id,
            view_workspace_id=scope.view_workspace_id,
            is_fork_view=scope.is_fork_view,
            forked_from_workspace_id=scope.forked_from_workspace_id,
            sources=tuple(
                BoardTodoSourceView(
                    source=source,
                    state=states.get(source.source_id),
                )
                for source in visible_sources
            ),
            diagnostics=diagnostics,
        )

    async def create_source(
        self,
        payload: BoardTodoSourceCreateRequest,
    ) -> BoardTodoSource:
        scope = await self._resolve_board_scope(payload.workspace_id)
        if payload.kind != BoardTodoSourceKind.GITHUB_ISSUES:
            raise ValueError("only github_issues sources can be created manually")
        repository_full_name = _normalize_repository_full_name(
            payload.repository_full_name
        )
        if repository_full_name is None:
            raise ValueError("repository_full_name is required")
        existing = await self._repository.find_source_by_repository_async(
            workspace_id=scope.board_workspace_id,
            repository_full_name=repository_full_name,
        )
        if existing is not None:
            raise ValueError("GitHub TODO source already exists for this repository")
        display_name = payload.display_name.strip()
        if not display_name:
            raise ValueError("display_name cannot be blank")
        now = _utc_now()
        return await self._repository.create_source_async(
            BoardTodoSource(
                source_id=_new_source_id(),
                workspace_id=scope.board_workspace_id,
                kind=BoardTodoSourceKind.GITHUB_ISSUES,
                provider=BoardTodoSourceProvider.GITHUB,
                display_name=display_name,
                enabled=payload.enabled,
                repository_full_name=repository_full_name,
                created_at=now,
                updated_at=now,
            )
        )

    async def update_source(
        self,
        *,
        source_id: str,
        payload: BoardTodoSourceUpdateRequest,
    ) -> BoardTodoSource:
        source = await self._repository.require_source_async(source_id)
        if payload.workspace_id is not None:
            scope = await self._resolve_board_scope(payload.workspace_id)
            if scope.board_workspace_id != source.workspace_id:
                raise ValueError("source does not belong to the resolved board")
        if source.kind != BoardTodoSourceKind.GITHUB_ISSUES:
            raise ValueError("only github_issues sources can be edited")
        if source.system_managed and source.kind == BoardTodoSourceKind.MANUAL:
            raise ValueError("unsupported source settings cannot be edited")
        patch: dict[str, object] = {}
        repository_changed = False
        if payload.display_name is not None:
            display_name = payload.display_name.strip()
            if not display_name:
                raise ValueError("display_name cannot be blank")
            patch["display_name"] = display_name
        if payload.enabled is not None:
            patch["enabled"] = payload.enabled
        if payload.repository_full_name is not None:
            repository_full_name = _normalize_repository_full_name(
                payload.repository_full_name
            )
            if repository_full_name is None:
                raise ValueError("repository_full_name cannot be blank")
            existing = await self._repository.find_source_by_repository_async(
                workspace_id=source.workspace_id,
                repository_full_name=repository_full_name,
            )
            if existing is not None and existing.source_id != source.source_id:
                raise ValueError(
                    "GitHub TODO source already exists for this repository"
                )
            if repository_full_name != source.repository_full_name:
                item_count = (
                    await self._repository.count_items_for_source_identity_async(
                        source=source
                    )
                )
                if item_count > 0:
                    raise ValueError(
                        "source repository cannot be changed after importing TODOs"
                    )
                repository_changed = True
            patch["repository_full_name"] = repository_full_name
        updated_source = await self._repository.update_source_async(
            source.model_copy(update=patch)
        )
        if repository_changed:
            await self._repository.update_source_sync_state_async(
                source_id=source.source_id,
                workspace_id=source.workspace_id,
                sync_cursor=None,
                status=BoardTodoSyncStatus.IDLE,
                diagnostics=(),
                started_at=None,
                finished_at=None,
            )
        return updated_source

    async def delete_source(
        self,
        *,
        source_id: str,
    ) -> BoardTodoSourceDeleteResponse:
        source = await self._repository.require_source_async(source_id)
        if source.system_managed:
            raise ValueError("system-managed sources cannot be deleted")
        item_count = await self._repository.count_items_for_source_identity_async(
            source=source
        )
        if item_count > 0:
            raise ValueError("source has imported TODOs; disable it instead")
        await self._repository.delete_source_async(source_id)
        return BoardTodoSourceDeleteResponse(deleted=True, source_id=source_id)

    async def list_handoff_templates(
        self,
        *,
        workspace_id: str,
    ) -> BoardTodoHandoffTemplateSettingsResponse:
        scope = await self._resolve_board_scope(workspace_id)
        return BoardTodoHandoffTemplateSettingsResponse(
            workspace_id=scope.view_workspace_id,
            board_workspace_id=scope.board_workspace_id,
            view_workspace_id=scope.view_workspace_id,
            is_fork_view=scope.is_fork_view,
            forked_from_workspace_id=scope.forked_from_workspace_id,
            templates=await self._repository.list_handoff_templates_async(
                workspace_id=scope.board_workspace_id
            ),
        )

    async def upsert_workspace_handoff_template(
        self,
        payload: BoardTodoHandoffTemplateInput,
    ) -> BoardTodoHandoffTemplate:
        scope = await self._resolve_board_scope(payload.workspace_id)
        now = _utc_now()
        return await self._repository.upsert_handoff_template_async(
            BoardTodoHandoffTemplate(
                template_id=_new_template_id(),
                workspace_id=scope.board_workspace_id,
                scope=BoardTodoTemplateScope.WORKSPACE,
                template_kind=payload.template_kind,
                template=payload.template.strip(),
                created_at=now,
                updated_at=now,
            )
        )

    async def upsert_source_handoff_template(
        self,
        *,
        source_id: str,
        payload: BoardTodoHandoffTemplateInput,
    ) -> BoardTodoHandoffTemplate:
        source = await self._repository.require_source_async(source_id)
        scope = await self._resolve_board_scope(payload.workspace_id)
        if scope.board_workspace_id != source.workspace_id:
            raise ValueError("source does not belong to the resolved board")
        now = _utc_now()
        return await self._repository.upsert_handoff_template_async(
            BoardTodoHandoffTemplate(
                template_id=_new_template_id(),
                workspace_id=scope.board_workspace_id,
                scope=BoardTodoTemplateScope.SOURCE,
                template_kind=payload.template_kind,
                source_id=source.source_id,
                template=payload.template.strip(),
                created_at=now,
                updated_at=now,
            )
        )

    async def delete_source_handoff_template(
        self,
        *,
        template_id: str,
    ) -> BoardTodoHandoffTemplateDeleteResponse:
        template = await self._repository.require_handoff_template_async(template_id)
        if template.scope != BoardTodoTemplateScope.SOURCE:
            raise ValueError("only source handoff templates can be deleted")
        await self._repository.delete_handoff_template_async(template_id=template_id)
        return BoardTodoHandoffTemplateDeleteResponse(
            deleted=True,
            template_id=template_id,
        )

    async def list_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        scope = await self._resolve_board_scope(workspace_id)
        diagnostics = await self._ensure_default_sources(scope)
        repository_full_name = await self._primary_repository_full_name(
            scope.board_workspace_id
        )
        await self.reconcile_workspace_async(workspace_id=scope.board_workspace_id)
        items = await self._runtime_display_items(
            _supported_board_items(
                await self._repository.list_by_workspace_async(
                    workspace_id=scope.board_workspace_id,
                    include_archived=include_archived,
                )
            )
        )
        sources = await self._repository.list_sources_async(
            workspace_id=scope.board_workspace_id
        )
        revision = await self._repository.get_workspace_revision_async(
            scope.board_workspace_id
        )
        return _board_response(
            scope=scope,
            repository_full_name=repository_full_name,
            items=items,
            sources=sources,
            diagnostics=diagnostics,
            synced_at=None,
            revision=revision,
        )

    async def list_board_changes(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
        after_revision: int = 0,
    ) -> BoardTodoDeltaResponse:
        scope = await self._resolve_board_scope(workspace_id)
        diagnostics = await self._ensure_default_sources(scope)
        repository_full_name = await self._primary_repository_full_name(
            scope.board_workspace_id
        )
        await self.reconcile_workspace_async(workspace_id=scope.board_workspace_id)
        return await self._delta_response(
            scope=scope,
            repository_full_name=repository_full_name,
            include_archived=include_archived,
            after_revision=after_revision,
            diagnostics=diagnostics,
            synced_at=None,
        )

    async def sync_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        scope = await self._resolve_board_scope(workspace_id)
        repository_full_name, sync_diagnostics, synced_at = await self._sync_github(
            workspace_id=workspace_id,
            force_full=True,
        )
        await self.reconcile_workspace_async(workspace_id=scope.board_workspace_id)
        items = await self._runtime_display_items(
            _supported_board_items(
                await self._repository.list_by_workspace_async(
                    workspace_id=scope.board_workspace_id,
                    include_archived=include_archived,
                )
            )
        )
        sources = await self._repository.list_sources_async(
            workspace_id=scope.board_workspace_id
        )
        revision = await self._repository.get_workspace_revision_async(
            scope.board_workspace_id
        )
        return _board_response(
            scope=scope,
            repository_full_name=repository_full_name,
            items=items,
            sources=sources,
            diagnostics=sync_diagnostics,
            synced_at=synced_at,
            revision=revision,
        )

    async def sync_board_changes(
        self,
        request: BoardTodoSyncChangesRequest,
    ) -> BoardTodoDeltaResponse:
        scope = await self._resolve_board_scope(request.workspace_id)
        repository_full_name, diagnostics, synced_at = await self._sync_github(
            workspace_id=request.workspace_id,
            force_full=request.force_full,
        )
        await self.reconcile_workspace_async(workspace_id=scope.board_workspace_id)
        return await self._delta_response(
            scope=scope,
            repository_full_name=repository_full_name,
            include_archived=request.include_archived,
            after_revision=request.after_revision,
            diagnostics=diagnostics,
            synced_at=synced_at,
        )

    async def _sync_github(
        self,
        *,
        workspace_id: str,
        force_full: bool,
    ) -> tuple[str | None, tuple[str, ...], datetime | None]:
        scope = await self._resolve_board_scope(workspace_id)
        diagnostics = list(await self._ensure_default_sources(scope))
        sources = tuple(
            source
            for source in await self._repository.list_sources_async(
                workspace_id=scope.board_workspace_id
            )
            if source.kind == BoardTodoSourceKind.GITHUB_ISSUES and source.enabled
        )
        if not sources:
            diagnostics.append(
                "No enabled GitHub TODO source is configured for this board."
            )
            return None, tuple(diagnostics), None
        repository_full_name: str | None = None
        synced_at: datetime | None = None
        for source in sources:
            (
                source_repository,
                source_diagnostics,
                source_synced_at,
            ) = await self._sync_github_source(
                workspace_id=scope.board_workspace_id,
                source=source,
                force_full=force_full,
            )
            repository_full_name = repository_full_name or source_repository
            diagnostics.extend(source_diagnostics)
            synced_at = source_synced_at or synced_at
        return repository_full_name, tuple(diagnostics), synced_at

    async def _sync_github_source(
        self,
        *,
        workspace_id: str,
        source: BoardTodoSource,
        force_full: bool,
    ) -> tuple[str | None, tuple[str, ...], datetime | None]:
        repository_full_name = source.repository_full_name
        if repository_full_name is None:
            diagnostics = ("GitHub TODO source is missing repository_full_name.",)
            await self._repository.update_source_sync_state_async(
                source_id=source.source_id,
                workspace_id=workspace_id,
                sync_cursor=None,
                status=BoardTodoSyncStatus.FAILED,
                diagnostics=diagnostics,
                started_at=_utc_now(),
                finished_at=_utc_now(),
            )
            return None, diagnostics, None
        owner, repo = repository_full_name.split("/", maxsplit=1)
        synced_at = _utc_now()
        sync_diagnostics: list[str] = []
        synced = False
        sync_cursor = None
        if not force_full:
            state = await self._repository.get_source_state_async(
                source_id=source.source_id
            )
            sync_cursor = None if state is None else state.sync_cursor
        for token in await self._github_tokens():
            try:
                issues = await self._github_client.list_repository_issues(
                    token=token,
                    owner=owner,
                    repo=repo,
                    state="open" if force_full else "all",
                    updated_since=sync_cursor,
                )
                pull_requests = await self._github_client.list_repository_pull_requests(
                    token=token,
                    owner=owner,
                    repo=repo,
                    state="all",
                    updated_since=None if force_full else sync_cursor,
                )
            except GitHubApiError as exc:
                diagnostic = _format_github_sync_error(
                    error=exc,
                    repository_full_name=repository_full_name,
                    force_full=force_full,
                )
                LOGGER.warning(
                    "GitHub board TODO sync failed for %s workspace=%s "
                    "force_full=%s status=%s message=%s",
                    repository_full_name,
                    workspace_id,
                    force_full,
                    exc.status_code,
                    diagnostic,
                )
                sync_diagnostics.append(diagnostic)
                continue
            pull_request_map = _pull_request_map(pull_requests)
            await self._upsert_github_issues(
                workspace_id=workspace_id,
                source_id=source.source_id,
                repository_full_name=repository_full_name,
                issues=issues,
                synced_at=synced_at,
            )
            await self._link_review_issues_to_pull_requests(
                token=token,
                workspace_id=workspace_id,
                repository_full_name=repository_full_name,
                owner=owner,
                repo=repo,
                pull_requests=pull_requests,
            )
            closed_archived_count = await self._reconcile_closed_github_issues(
                token=token,
                workspace_id=workspace_id,
                repository_full_name=repository_full_name,
                owner=owner,
                repo=repo,
                issues=issues,
                pull_request_map=pull_request_map,
            )
            stale_archived_count = 0
            closed_done_count = 0
            if force_full:
                (
                    stale_archived_count,
                    closed_done_count,
                ) = await self._reconcile_non_open_github_issues(
                    token=token,
                    workspace_id=workspace_id,
                    repository_full_name=repository_full_name,
                    owner=owner,
                    repo=repo,
                    issues=issues,
                    pull_request_map=pull_request_map,
                )
            await self._archive_untracked_pull_request_items(workspace_id=workspace_id)
            await self._repository.update_source_sync_state_async(
                source_id=source.source_id,
                workspace_id=workspace_id,
                sync_cursor=_github_sync_cursor(synced_at),
                status=BoardTodoSyncStatus.SUCCEEDED,
                diagnostics=tuple(sync_diagnostics),
                started_at=synced_at,
                finished_at=_utc_now(),
            )
            revision = await self._repository.get_workspace_revision_async(workspace_id)
            LOGGER.info(
                "GitHub board TODO sync completed for %s workspace=%s "
                "force_full=%s cursor=%s open_issues=%s changed_issues=%s "
                "pull_requests=%s changed_pull_requests=%s "
                "closed_archived=%s stale_archived=%s "
                "closed_done=%s revision=%s",
                repository_full_name,
                workspace_id,
                force_full,
                sync_cursor.isoformat() if sync_cursor is not None else None,
                len(issues) if force_full else 0,
                0 if force_full else len(issues),
                len(pull_requests),
                0 if force_full else len(pull_requests),
                closed_archived_count,
                stale_archived_count,
                closed_done_count,
                revision,
            )
            synced = True
            break
        if not synced and not sync_diagnostics:
            sync_diagnostics.append(
                "No enabled GitHub trigger account token is available."
            )
        if not synced:
            await self._repository.update_source_sync_state_async(
                source_id=source.source_id,
                workspace_id=workspace_id,
                sync_cursor=sync_cursor,
                status=BoardTodoSyncStatus.FAILED,
                diagnostics=tuple(sync_diagnostics),
                started_at=synced_at,
                finished_at=_utc_now(),
            )
        return (
            repository_full_name,
            tuple(sync_diagnostics),
            synced_at if synced else None,
        )

    async def preview_start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoPreviewStartRequest,
    ) -> BoardTodoPreviewStartResponse:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.TODO:
            raise ValueError("only todo board items can be previewed for start")
        scope = await self._resolve_board_scope(
            payload.view_workspace_id or item.workspace_id
        )
        if scope.board_workspace_id != item.workspace_id:
            raise ValueError("todo does not belong to the resolved board")
        execution_policy = payload.execution_policy or _default_execution_policy(item)
        runtime_target = _resolve_runtime_target(
            runtime_target_id=payload.runtime_target_id,
            session_mode=None,
            normal_root_role_id=None,
            orchestration_preset_id=None,
        )
        concurrency = await self._concurrency_snapshot(
            source_workspace_id=item.workspace_id,
            runtime_target_id=runtime_target.target_id,
        )
        template_source, prompt = await self._render_handoff_prompt(
            item=item,
            template_kind=BoardTodoHandoffTemplateKind.START,
            built_in_prompt=_build_start_prompt(item),
            feedback=None,
        )
        return BoardTodoPreviewStartResponse(
            todo_id=item.todo_id,
            board_workspace_id=scope.board_workspace_id,
            view_workspace_id=scope.view_workspace_id,
            is_fork_view=scope.is_fork_view,
            forked_from_workspace_id=scope.forked_from_workspace_id,
            template_source=template_source,
            prompt=prompt,
            execution_policy=execution_policy,
            execution_workspace_preview=_execution_workspace_preview(
                item=item,
                scope=scope,
                execution_policy=execution_policy,
            ),
            runtime_target_id=runtime_target.target_id,
            runtime_target_options=_runtime_target_options(
                selected=runtime_target,
            ),
            concurrency=concurrency,
            queue_preview=_queue_preview(
                concurrency=concurrency,
                queue_if_full=payload.queue_if_full,
            ),
        )

    async def start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStartRequest,
    ) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.TODO:
            raise ValueError("only todo board items can be started")
        prompt = (payload.final_prompt or payload.prompt or "").strip()
        if not prompt:
            raise ValueError("final_prompt is required")
        scope = await self._resolve_board_scope(
            payload.view_workspace_id or item.workspace_id
        )
        if scope.board_workspace_id != item.workspace_id:
            raise ValueError("view workspace does not belong to this board")
        execution_policy = payload.execution_policy or _default_execution_policy(item)
        runtime_target = _resolve_runtime_target(
            runtime_target_id=payload.runtime_target_id,
            session_mode=payload.session_mode,
            normal_root_role_id=payload.normal_root_role_id,
            orchestration_preset_id=payload.orchestration_preset_id,
        )
        requested_session_mode, session_normal_role_id, session_preset_id = (
            _start_session_topology(
                runtime_target=runtime_target,
                session_mode=payload.session_mode,
                normal_root_role_id=payload.normal_root_role_id,
                orchestration_preset_id=payload.orchestration_preset_id,
            )
        )
        reserved = await self._repository.reserve_start_async(item)
        attempt: BoardTodoAttempt | None = None
        execution_workspace_id: str | None = None
        run_id: str | None = None
        session_id: str | None = None
        try:
            attempt = await self._create_handoff_attempt(
                item=reserved,
                attempt_type=BoardTodoAttemptType.START,
                final_prompt=prompt,
                template_kind="start",
                template_source=await self._template_source(
                    item=item,
                    template_kind=BoardTodoHandoffTemplateKind.START,
                ),
                scope=scope,
                execution_policy=execution_policy,
                runtime_target_kind=runtime_target.kind,
                runtime_target_id=runtime_target.target_id,
                yolo=payload.yolo,
                thinking=payload.thinking,
            )
            reserved = await self._repository.update_async(
                reserved.model_copy(
                    update={
                        "current_attempt_id": attempt.attempt_id,
                        "active_attempt_id": attempt.attempt_id,
                        "execution_policy": execution_policy,
                        "runtime_target_kind": runtime_target.kind,
                        "runtime_target_id": runtime_target.target_id,
                        "last_status_reason": "Preparing board todo handoff",
                    }
                )
            )
            concurrency = await self._concurrency_snapshot(
                source_workspace_id=item.workspace_id,
                runtime_target_id=runtime_target.target_id,
                excluded_todo_id=reserved.todo_id,
            )
            if _should_queue(concurrency) and payload.queue_if_full:
                return await self._queue_handoff(
                    reserved=reserved,
                    attempt=attempt,
                    queue_kind=BoardTodoQueueKind.START,
                    scope=scope,
                    execution_policy=execution_policy,
                    runtime_target=runtime_target,
                    session_mode=requested_session_mode,
                    normal_root_role_id=session_normal_role_id,
                    orchestration_preset_id=session_preset_id,
                    yolo=payload.yolo,
                    thinking=payload.thinking,
                )
            if _should_queue(concurrency):
                raise ValueError("handoff concurrency limit reached")
            execution_workspace_id = await self._prepare_execution_workspace(
                item=reserved,
                attempt_id=attempt.attempt_id,
                scope=scope,
                execution_policy=execution_policy,
            )
            attempt = await self._repository.update_attempt_async(
                attempt.model_copy(
                    update={"execution_workspace_id": execution_workspace_id}
                )
            )
            session = await self._session_service.create_session_async(
                workspace_id=execution_workspace_id,
                metadata={
                    "board_todo_id": reserved.todo_id,
                    "board_todo_title": reserved.title,
                },
                session_mode=requested_session_mode,
                normal_root_role_id=session_normal_role_id,
                orchestration_preset_id=session_preset_id,
            )
            session_id = session.session_id
            run_session_mode = session.session_mode
            target_role_id = None
            if run_session_mode == SessionMode.NORMAL:
                target_role_id = (
                    payload.normal_root_role_id
                    or _role_id_from_runtime_target(runtime_target.target_id)
                    or session.normal_root_role_id
                )
            run_id, _session_id = await self._create_and_start_run(
                session_id=session.session_id,
                prompt=prompt,
                yolo=payload.yolo,
                thinking=payload.thinking,
                target_role_id=target_role_id,
                session_mode=run_session_mode,
            )
            current_reserved = await self._current_handoff_reserved_item_or_raise(
                todo_id=reserved.todo_id,
                attempt_id=attempt.attempt_id,
            )
            await self._mark_attempt_active(
                attempt=attempt,
                session_id=session.session_id,
                run_id=run_id,
            )
            return await self._repository.update_async(
                current_reserved.model_copy(
                    update={
                        "status": BoardTodoStatus.IN_PROGRESS,
                        "session_id": session.session_id,
                        "run_id": run_id,
                        "current_attempt_id": attempt.attempt_id,
                        "active_attempt_id": attempt.attempt_id,
                        "execution_workspace_id": execution_workspace_id,
                        "execution_policy": execution_policy,
                        "runtime_target_kind": runtime_target.kind,
                        "runtime_target_id": runtime_target.target_id,
                        "queue_ticket_id": None,
                        "last_status_reason": "Started from board todo item",
                    }
                )
            )
        except Exception as exc:
            if run_id is not None:
                await self._stop_handoff_run(run_id)
            if session_id is not None:
                await self._cleanup_created_start_session(session_id)
            await self._cleanup_start_execution_workspace(
                execution_policy=execution_policy,
                execution_workspace_id=execution_workspace_id,
            )
            if attempt is not None:
                await self._mark_attempt_failed(attempt=attempt, error=str(exc))
                await self._record_diagnostic(
                    item=reserved,
                    kind="handoff_start_failed",
                    message=str(exc),
                    attempt_id=attempt.attempt_id,
                )
            await self._restore_failed_start_reservation(
                reserved,
                current_attempt_id=attempt.attempt_id if attempt is not None else None,
            )
            raise

    async def _restore_failed_start_reservation(
        self,
        item: BoardTodoItem,
        *,
        current_attempt_id: str | None = None,
    ) -> None:
        try:
            current = await self._repository.require_async(item.todo_id)
            if (
                current.status == BoardTodoStatus.IN_PROGRESS
                and current.session_id is None
                and current.run_id is None
                and current.active_attempt_id == current_attempt_id
                and current.queue_ticket_id is None
            ):
                await self._repository.update_async(
                    current.model_copy(
                        update={
                            "status": BoardTodoStatus.TODO,
                            "current_attempt_id": current_attempt_id,
                            "active_attempt_id": None,
                            "execution_workspace_id": None,
                            "execution_policy": None,
                            "runtime_target_kind": None,
                            "runtime_target_id": None,
                            "queue_ticket_id": None,
                            "last_status_reason": "Start failed",
                        }
                    )
                )
        except Exception as exc:
            LOGGER.warning(
                "Failed to restore board todo start reservation %s: %s",
                item.todo_id,
                exc,
            )

    async def preview_request_changes_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoPreviewRequestChangesRequest,
    ) -> BoardTodoPreviewRequestChangesResponse:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.REVIEW:
            raise ValueError("only review board items can preview request changes")
        if item.session_id is None:
            raise ValueError("board todo item is not bound to a session")
        scope = await self._resolve_board_scope(
            payload.view_workspace_id or item.workspace_id
        )
        if scope.board_workspace_id != item.workspace_id:
            raise ValueError("todo does not belong to the resolved board")
        execution_workspace_id = await self._request_changes_execution_workspace(item)
        session = await self._session_service.get_session_async(
            _require_session_id(item)
        )
        runtime_target = _request_changes_runtime_target(
            runtime_target_id=payload.runtime_target_id,
            item=item,
            session=session,
        )
        _validate_runtime_target_matches_session(
            runtime_target=runtime_target,
            session=session,
        )
        concurrency = await self._concurrency_snapshot(
            source_workspace_id=item.workspace_id,
            runtime_target_id=runtime_target.target_id,
        )
        template_source, prompt = await self._render_handoff_prompt(
            item=item,
            template_kind=BoardTodoHandoffTemplateKind.REQUEST_CHANGES,
            built_in_prompt=_build_request_changes_prompt(
                item=item,
                feedback=payload.feedback,
            ),
            feedback=payload.feedback,
        )
        return BoardTodoPreviewRequestChangesResponse(
            todo_id=item.todo_id,
            board_workspace_id=scope.board_workspace_id,
            view_workspace_id=scope.view_workspace_id,
            is_fork_view=scope.is_fork_view,
            forked_from_workspace_id=scope.forked_from_workspace_id,
            template_source=template_source,
            prompt=prompt,
            execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
            execution_workspace_preview=BoardTodoExecutionWorkspacePreview(
                policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                workspace_id=execution_workspace_id,
                source_workspace_id=item.workspace_id,
                display_name=execution_workspace_id,
            ),
            runtime_target_id=runtime_target.target_id,
            runtime_target_options=_runtime_target_options(
                selected=runtime_target,
            ),
            concurrency=concurrency,
            queue_preview=_queue_preview(
                concurrency=concurrency,
                queue_if_full=payload.queue_if_full,
            ),
            session_id=item.session_id,
            run_id=item.run_id,
        )

    async def request_changes(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStatusUpdateRequest,
    ) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.REVIEW:
            raise ValueError("only review board items can request changes")
        if item.session_id is None:
            raise ValueError("board todo item is not bound to a session")
        scope = await self._resolve_board_scope(
            payload.view_workspace_id or item.workspace_id
        )
        if scope.board_workspace_id != item.workspace_id:
            raise ValueError("view workspace does not belong to this board")
        prompt = (payload.final_prompt or payload.prompt or "").strip()
        if not prompt:
            raise ValueError("final_prompt is required")
        execution_workspace_id = await self._request_changes_execution_workspace(item)
        session = await self._session_service.get_session_async(
            _require_session_id(item)
        )
        runtime_target = _request_changes_runtime_target(
            runtime_target_id=payload.runtime_target_id,
            item=item,
            session=session,
        )
        _validate_runtime_target_matches_session(
            runtime_target=runtime_target,
            session=session,
        )
        reserved = await self._repository.reserve_request_changes_async(item)
        attempt: BoardTodoAttempt | None = None
        run_id: str | None = None
        try:
            attempt = await self._create_handoff_attempt(
                item=reserved,
                attempt_type=BoardTodoAttemptType.REQUEST_CHANGES,
                final_prompt=prompt,
                template_kind="request_changes",
                template_source=await self._template_source(
                    item=item,
                    template_kind=BoardTodoHandoffTemplateKind.REQUEST_CHANGES,
                ),
                scope=scope,
                execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                runtime_target_kind=runtime_target.kind,
                runtime_target_id=runtime_target.target_id,
                yolo=payload.yolo,
                thinking=payload.thinking,
                execution_workspace_id=execution_workspace_id,
            )
            reserved = await self._repository.update_async(
                reserved.model_copy(
                    update={
                        "current_attempt_id": attempt.attempt_id,
                        "active_attempt_id": attempt.attempt_id,
                        "execution_workspace_id": execution_workspace_id,
                        "execution_policy": BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                        "runtime_target_kind": runtime_target.kind,
                        "runtime_target_id": runtime_target.target_id,
                        "last_status_reason": "Preparing board todo request changes",
                    }
                )
            )
            concurrency = await self._concurrency_snapshot(
                source_workspace_id=item.workspace_id,
                runtime_target_id=runtime_target.target_id,
                excluded_todo_id=reserved.todo_id,
            )
            if _should_queue(concurrency) and payload.queue_if_full:
                return await self._queue_handoff(
                    reserved=reserved,
                    attempt=attempt,
                    queue_kind=BoardTodoQueueKind.REQUEST_CHANGES,
                    scope=scope,
                    execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                    runtime_target=runtime_target,
                    session_mode=None,
                    normal_root_role_id=None,
                    orchestration_preset_id=None,
                    yolo=payload.yolo,
                    thinking=payload.thinking,
                    execution_workspace_id=execution_workspace_id,
                    previous_run_id=item.run_id,
                )
            if _should_queue(concurrency):
                raise ValueError("handoff concurrency limit reached")
            target_role_id = (
                _role_id_from_runtime_target(runtime_target.target_id)
                or session.normal_root_role_id
                if session.session_mode == SessionMode.NORMAL
                else None
            )
            run_id, _session_id = await self._create_and_start_run(
                session_id=_require_session_id(reserved),
                prompt=prompt,
                yolo=payload.yolo,
                thinking=payload.thinking,
                target_role_id=target_role_id,
                session_mode=session.session_mode,
            )
            current_reserved = await self._current_handoff_reserved_item_or_raise(
                todo_id=reserved.todo_id,
                attempt_id=attempt.attempt_id,
            )
            await self._mark_attempt_active(
                attempt=attempt,
                session_id=_require_session_id(reserved),
                run_id=run_id,
            )
            return await self._repository.update_async(
                current_reserved.model_copy(
                    update={
                        "status": BoardTodoStatus.IN_PROGRESS,
                        "run_id": run_id,
                        "current_attempt_id": attempt.attempt_id,
                        "active_attempt_id": attempt.attempt_id,
                        "execution_workspace_id": execution_workspace_id,
                        "execution_policy": BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                        "runtime_target_kind": runtime_target.kind,
                        "runtime_target_id": runtime_target.target_id,
                        "queue_ticket_id": None,
                        "last_status_reason": "Changes requested by user",
                    }
                )
            )
        except Exception as exc:
            if run_id is not None:
                await self._stop_handoff_run(run_id)
            if attempt is not None:
                await self._mark_attempt_failed(attempt=attempt, error=str(exc))
                await self._record_diagnostic(
                    item=reserved,
                    kind="handoff_request_changes_failed",
                    message=str(exc),
                    attempt_id=attempt.attempt_id,
                )
            await self._restore_failed_request_changes_reservation(
                reserved,
                previous_run_id=item.run_id,
                current_attempt_id=(
                    attempt.attempt_id
                    if attempt is not None
                    else item.current_attempt_id
                ),
            )
            raise

    async def _restore_failed_request_changes_reservation(
        self,
        item: BoardTodoItem,
        *,
        previous_run_id: str | None,
        current_attempt_id: str | None,
    ) -> None:
        try:
            current = await self._repository.require_async(item.todo_id)
            if (
                current.status == BoardTodoStatus.IN_PROGRESS
                and current.session_id == item.session_id
                and current.run_id is None
            ):
                await self._repository.update_async(
                    current.model_copy(
                        update={
                            "status": BoardTodoStatus.REVIEW,
                            "run_id": previous_run_id,
                            "current_attempt_id": current_attempt_id,
                            "active_attempt_id": None,
                            "queue_ticket_id": None,
                            "last_status_reason": "Request changes failed",
                        }
                    )
                )
        except Exception as exc:
            LOGGER.warning(
                "Failed to restore board todo request-changes reservation %s: %s",
                item.todo_id,
                exc,
            )

    async def _create_handoff_attempt(
        self,
        *,
        item: BoardTodoItem,
        attempt_type: BoardTodoAttemptType,
        final_prompt: str,
        template_kind: str,
        template_source: str,
        scope: BoardTodoScope,
        execution_policy: BoardTodoExecutionPolicy,
        runtime_target_kind: BoardTodoRuntimeTargetKind,
        runtime_target_id: str,
        yolo: bool,
        thinking: RunThinkingConfig,
        execution_workspace_id: str | None = None,
    ) -> BoardTodoAttempt:
        now = _utc_now()
        attempt_id = _new_attempt_id()
        prompt_ref = _new_prompt_ref()
        attempt = await self._repository.create_attempt_async(
            BoardTodoAttempt(
                attempt_id=attempt_id,
                todo_id=item.todo_id,
                attempt_type=attempt_type,
                status=BoardTodoAttemptStatus.PENDING,
                board_workspace_id=scope.board_workspace_id,
                initiated_from_workspace_id=scope.view_workspace_id,
                source_workspace_id=item.workspace_id,
                execution_workspace_id=execution_workspace_id,
                execution_policy=execution_policy,
                runtime_target_kind=runtime_target_kind,
                runtime_target_id=runtime_target_id,
                yolo=yolo,
                thinking=thinking,
                prompt_ref=prompt_ref,
                created_at=now,
            )
        )
        await self._repository.create_handoff_prompt_async(
            BoardTodoHandoffPrompt(
                prompt_ref=prompt_ref,
                todo_id=item.todo_id,
                attempt_id=attempt_id,
                template_kind=template_kind,
                template_source=template_source,
                final_prompt_snapshot=final_prompt,
                created_at=now,
            )
        )
        return attempt

    async def _template_source(
        self,
        *,
        item: BoardTodoItem,
        template_kind: BoardTodoHandoffTemplateKind,
    ) -> str:
        template = await self._repository.get_handoff_template_async(
            workspace_id=item.workspace_id,
            template_kind=template_kind,
            source_id=item.source_id,
        )
        if template is None:
            return "built_in"
        if template.scope == BoardTodoTemplateScope.SOURCE:
            return f"source:{template.source_id}"
        return f"workspace:{template.workspace_id}"

    async def _render_handoff_prompt(
        self,
        *,
        item: BoardTodoItem,
        template_kind: BoardTodoHandoffTemplateKind,
        built_in_prompt: str,
        feedback: str | None,
    ) -> tuple[str, str]:
        template = await self._repository.get_handoff_template_async(
            workspace_id=item.workspace_id,
            template_kind=template_kind,
            source_id=item.source_id,
        )
        if template is None:
            return "built_in", built_in_prompt
        template_source = (
            f"source:{template.source_id}"
            if template.scope == BoardTodoTemplateScope.SOURCE
            else f"workspace:{template.workspace_id}"
        )
        return template_source, _render_handoff_template(
            template=template.template,
            item=item,
            feedback=feedback,
        )

    async def _concurrency_snapshot(
        self,
        *,
        source_workspace_id: str,
        runtime_target_id: str,
        excluded_queue_ticket_id: str | None = None,
        excluded_todo_id: str | None = None,
        include_claimable_queue_tickets: bool = True,
        capacity_before_ticket: BoardTodoExecutionQueueTicket | None = None,
    ) -> BoardTodoConcurrencySnapshot:
        source_active = 0
        runtime_target_active = 0
        items = await self._repository.list_by_workspace_async(
            workspace_id=source_workspace_id,
            include_archived=False,
        )
        for item in items:
            if item.todo_id == excluded_todo_id:
                continue
            if item.status != BoardTodoStatus.IN_PROGRESS:
                continue
            if item.queue_ticket_id is not None:
                if item.queue_ticket_id == excluded_queue_ticket_id:
                    continue
                ticket = await self._repository.get_queue_ticket_async(
                    item.queue_ticket_id
                )
                if ticket is None and item.run_id is None:
                    continue
                if ticket is not None and ticket.status in (
                    BoardTodoQueueStatus.CANCELLED,
                    BoardTodoQueueStatus.COMPLETED,
                    BoardTodoQueueStatus.FAILED,
                ):
                    if item.run_id is None:
                        continue
                if (
                    ticket is not None
                    and not include_claimable_queue_tickets
                    and _queue_ticket_can_be_claimed(ticket)
                    and item.run_id is None
                ):
                    continue
                if (
                    ticket is not None
                    and capacity_before_ticket is not None
                    and ticket.status == BoardTodoQueueStatus.CLAIMED
                    and item.run_id is None
                    and _queue_ticket_sorts_after(ticket, capacity_before_ticket)
                ):
                    continue
            if item.run_id is None:
                if item.queue_ticket_id is None and item.active_attempt_id is None:
                    continue
            else:
                runtime = await self._run_runtime_repo.get_async(item.run_id)
                if runtime is None or runtime.status not in _ACTIVE_RUNTIME_STATUSES:
                    if item.queue_ticket_id is None:
                        continue
            source_active += 1
            item_runtime_target_id = await self._runtime_target_id_for_capacity(item)
            if (
                item_runtime_target_id is None
                or item_runtime_target_id == runtime_target_id
            ):
                runtime_target_active += 1
        return BoardTodoConcurrencySnapshot(
            source_workspace_active=source_active,
            source_workspace_limit=_SOURCE_WORKSPACE_ACTIVE_LIMIT,
            runtime_target_active=runtime_target_active,
            runtime_target_limit=_RUNTIME_TARGET_ACTIVE_LIMIT,
        )

    async def _runtime_target_id_for_capacity(self, item: BoardTodoItem) -> str | None:
        if item.runtime_target_id is not None:
            return item.runtime_target_id
        if item.session_id is None:
            return None
        try:
            session = await self._session_service.get_session_async(item.session_id)
        except KeyError:
            return None
        return _runtime_target_from_session(session).target_id

    async def _queue_handoff(
        self,
        *,
        reserved: BoardTodoItem,
        attempt: BoardTodoAttempt,
        queue_kind: BoardTodoQueueKind,
        scope: BoardTodoScope,
        execution_policy: BoardTodoExecutionPolicy,
        runtime_target: BoardTodoRuntimeTargetOption,
        session_mode: SessionMode | None,
        normal_root_role_id: str | None,
        orchestration_preset_id: str | None,
        yolo: bool,
        thinking: RunThinkingConfig,
        execution_workspace_id: str | None = None,
        previous_run_id: str | None = None,
    ) -> BoardTodoItem:
        if attempt.prompt_ref is None:
            raise ValueError("handoff attempt is missing prompt snapshot")
        queue_ticket = await self._repository.create_queue_ticket_async(
            BoardTodoExecutionQueueTicket(
                queue_ticket_id=_new_queue_ticket_id(),
                todo_id=reserved.todo_id,
                attempt_id=attempt.attempt_id,
                prompt_ref=attempt.prompt_ref,
                queue_kind=queue_kind,
                status=BoardTodoQueueStatus.PENDING,
                board_workspace_id=scope.board_workspace_id,
                source_workspace_id=reserved.workspace_id,
                initiated_from_workspace_id=scope.view_workspace_id,
                execution_workspace_id=execution_workspace_id,
                previous_run_id=previous_run_id,
                execution_policy=execution_policy,
                runtime_target_kind=runtime_target.kind,
                runtime_target_id=runtime_target.target_id,
                session_mode=session_mode,
                normal_root_role_id=normal_root_role_id,
                orchestration_preset_id=orchestration_preset_id,
                yolo=yolo,
                thinking=thinking,
            )
        )
        await self._repository.update_attempt_async(
            attempt.model_copy(
                update={
                    "status": BoardTodoAttemptStatus.ACTIVE,
                    "queue_ticket_id": queue_ticket.queue_ticket_id,
                }
            )
        )
        return await self._repository.update_async(
            reserved.model_copy(
                update={
                    "status": BoardTodoStatus.IN_PROGRESS,
                    "run_id": None,
                    "current_attempt_id": attempt.attempt_id,
                    "active_attempt_id": attempt.attempt_id,
                    "execution_workspace_id": execution_workspace_id,
                    "execution_policy": execution_policy,
                    "runtime_target_kind": runtime_target.kind,
                    "runtime_target_id": runtime_target.target_id,
                    "queue_ticket_id": queue_ticket.queue_ticket_id,
                    "last_status_reason": "Queued for board todo handoff",
                }
            )
        )

    async def _prepare_execution_workspace(
        self,
        *,
        item: BoardTodoItem,
        attempt_id: str,
        scope: BoardTodoScope,
        execution_policy: BoardTodoExecutionPolicy,
    ) -> str:
        if execution_policy == BoardTodoExecutionPolicy.CURRENT_WORKSPACE:
            return scope.view_workspace_id
        workspace = await self._workspace_service.fork_workspace_async(
            item.workspace_id,
            name=_execution_workspace_name(item=item, attempt_id=attempt_id),
        )
        return workspace.workspace_id

    async def _cleanup_start_execution_workspace(
        self,
        *,
        execution_policy: BoardTodoExecutionPolicy,
        execution_workspace_id: str | None,
    ) -> None:
        if (
            execution_policy != BoardTodoExecutionPolicy.FORK_GIT_WORKTREE
            or execution_workspace_id is None
        ):
            return
        try:
            await self._workspace_service.delete_workspace_with_options_async(
                workspace_id=execution_workspace_id,
                remove_directory=True,
            )
        except Exception as exc:
            LOGGER.warning(
                "Failed to clean up board TODO execution workspace %s: %s",
                execution_workspace_id,
                exc,
            )

    async def _cleanup_created_start_session(self, session_id: str) -> None:
        try:
            await self._session_service.delete_session_async(
                session_id,
                force=True,
                cascade=True,
            )
        except Exception as exc:
            LOGGER.warning(
                "Failed to clean up board TODO start session %s: %s",
                session_id,
                exc,
            )

    async def _cleanup_queued_start_before_run(
        self,
        *,
        session_id: str,
        execution_policy: BoardTodoExecutionPolicy,
        execution_workspace_id: str | None,
    ) -> None:
        await self._cleanup_created_start_session(session_id)
        await self._cleanup_start_execution_workspace(
            execution_policy=execution_policy,
            execution_workspace_id=execution_workspace_id,
        )

    async def _request_changes_execution_workspace(self, item: BoardTodoItem) -> str:
        if item.execution_workspace_id is not None:
            return item.execution_workspace_id
        session = await self._session_service.get_session_async(
            _require_session_id(item)
        )
        if session.workspace_id:
            return session.workspace_id
        raise ValueError("review item is missing execution workspace context")

    async def drain_queue_once(self) -> int:
        drained = 0
        tickets = await self._repository.list_pending_queue_tickets_async(limit=None)
        for ticket in tickets:
            concurrency = await self._concurrency_snapshot(
                source_workspace_id=ticket.source_workspace_id,
                runtime_target_id=ticket.runtime_target_id or "",
                excluded_queue_ticket_id=ticket.queue_ticket_id,
                include_claimable_queue_tickets=False,
            )
            if _should_queue(concurrency):
                continue
            claimed = await self._claim_queue_ticket(ticket)
            if claimed is None:
                continue
            concurrency = await self._concurrency_snapshot(
                source_workspace_id=claimed.source_workspace_id,
                runtime_target_id=claimed.runtime_target_id or "",
                excluded_queue_ticket_id=claimed.queue_ticket_id,
                include_claimable_queue_tickets=False,
                capacity_before_ticket=claimed,
            )
            if _should_queue(concurrency):
                await self._release_queue_ticket_claim(claimed)
                continue
            await self._execute_queue_ticket(claimed)
            drained += 1
        return drained

    async def _claim_queue_ticket(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> BoardTodoExecutionQueueTicket | None:
        now = _utc_now()
        return await self._repository.claim_queue_ticket_async(
            ticket=ticket,
            claim_token=uuid4().hex,
            claim_expires_at=now + timedelta(seconds=_QUEUE_CLAIM_SECONDS),
            claimed_by="board-todo-queue-worker",
            now=now,
        )

    async def _release_queue_ticket_claim(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> None:
        released = await self._repository.release_queue_ticket_claim_async(ticket)
        if released is None:
            LOGGER.debug(
                "Skipped releasing board TODO queue ticket %s because the claim is "
                "no longer owned by this worker",
                ticket.queue_ticket_id,
            )

    async def _renew_queue_claim(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> BoardTodoExecutionQueueTicket:
        renewed = await self._repository.renew_queue_ticket_claim_async(
            ticket=ticket,
            claim_expires_at=_utc_now() + timedelta(seconds=_QUEUE_CLAIM_SECONDS),
        )
        if renewed is None:
            raise ValueError("queue ticket claim is no longer owned by this worker")
        return renewed

    async def _execute_queue_ticket(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> None:
        execution_workspace_id = ticket.execution_workspace_id
        run_id: str | None = None
        session_id: str | None = None
        attached = False
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._queue_claim_heartbeat(ticket=ticket, stop_event=stop_heartbeat)
        )
        try:
            item = await self._current_queue_owned_item_or_cancel(
                ticket=ticket,
                reason="Queued handoff ticket no longer owns TODO item",
            )
            if item is None:
                return
            attempt = await self._repository.require_attempt_async(ticket.attempt_id)
            prompt = await self._repository.require_handoff_prompt_async(
                ticket.prompt_ref
            )
            if ticket.queue_kind == BoardTodoQueueKind.START:
                execution_workspace_id = await self._prepare_execution_workspace(
                    item=item,
                    attempt_id=attempt.attempt_id,
                    scope=BoardTodoScope(
                        board_workspace_id=ticket.board_workspace_id,
                        view_workspace_id=(
                            ticket.initiated_from_workspace_id
                            or ticket.board_workspace_id
                        ),
                    ),
                    execution_policy=ticket.execution_policy,
                )
                ticket = await self._renew_queue_claim(ticket)
                session = await self._session_service.create_session_async(
                    workspace_id=execution_workspace_id,
                    metadata={
                        "board_todo_id": item.todo_id,
                        "board_todo_title": item.title,
                    },
                    session_mode=ticket.session_mode,
                    normal_root_role_id=(
                        ticket.normal_root_role_id
                        if ticket.session_mode == SessionMode.NORMAL
                        else None
                    ),
                    orchestration_preset_id=(
                        ticket.orchestration_preset_id
                        if ticket.session_mode == SessionMode.ORCHESTRATION
                        else None
                    ),
                )
                session_id = session.session_id
                ticket = await self._renew_queue_claim(ticket)
                target_role_id = _target_role_id_for_run(
                    session=session,
                    ticket=ticket,
                )
                current_item = await self._current_queue_owned_item_or_cancel(
                    ticket=ticket,
                    reason="Queued handoff ticket no longer owns TODO item before run start",
                )
                if current_item is None:
                    await self._cleanup_queued_start_before_run(
                        session_id=session.session_id,
                        execution_policy=ticket.execution_policy,
                        execution_workspace_id=execution_workspace_id,
                    )
                    return
                run_id, _session_id = await self._create_and_start_run(
                    session_id=session.session_id,
                    prompt=prompt.final_prompt_snapshot,
                    yolo=ticket.yolo,
                    thinking=ticket.thinking,
                    target_role_id=target_role_id,
                    session_mode=session.session_mode,
                )
                ticket = await self._renew_queue_claim(ticket)
                current_item = await self._current_queue_owned_item_or_cancel(
                    ticket=ticket,
                    reason="Queued handoff ticket no longer owns TODO item before run attach",
                )
                if current_item is None:
                    await self._stop_handoff_run(run_id)
                    await self._cleanup_queued_start_before_run(
                        session_id=session.session_id,
                        execution_policy=ticket.execution_policy,
                        execution_workspace_id=execution_workspace_id,
                    )
                    return
                await self._mark_attempt_active(
                    attempt=attempt,
                    session_id=session.session_id,
                    run_id=run_id,
                )
                await self._repository.update_async(
                    current_item.model_copy(
                        update={
                            "status": BoardTodoStatus.IN_PROGRESS,
                            "session_id": session.session_id,
                            "run_id": run_id,
                            "active_attempt_id": attempt.attempt_id,
                            "execution_workspace_id": execution_workspace_id,
                            "execution_policy": ticket.execution_policy,
                            "runtime_target_kind": ticket.runtime_target_kind,
                            "runtime_target_id": ticket.runtime_target_id,
                            "queue_ticket_id": None,
                            "last_status_reason": "Queued handoff started",
                        }
                    )
                )
                attached = True
            else:
                session = await self._session_service.get_session_async(
                    _require_session_id(item)
                )
                ticket = await self._renew_queue_claim(ticket)
                current_item = await self._current_queue_owned_item_or_cancel(
                    ticket=ticket,
                    reason="Queued request-changes ticket no longer owns TODO item before run start",
                )
                if current_item is None:
                    return
                run_id, _session_id = await self._create_and_start_run(
                    session_id=session.session_id,
                    prompt=prompt.final_prompt_snapshot,
                    yolo=ticket.yolo,
                    thinking=ticket.thinking,
                    target_role_id=(
                        _target_role_id_for_run(session=session, ticket=ticket)
                    ),
                    session_mode=session.session_mode,
                )
                ticket = await self._renew_queue_claim(ticket)
                current_item = await self._current_queue_owned_item_or_cancel(
                    ticket=ticket,
                    reason="Queued request-changes ticket no longer owns TODO item before run attach",
                )
                if current_item is None:
                    await self._stop_handoff_run(run_id)
                    return
                await self._mark_attempt_active(
                    attempt=attempt,
                    session_id=session.session_id,
                    run_id=run_id,
                )
                await self._repository.update_async(
                    current_item.model_copy(
                        update={
                            "status": BoardTodoStatus.IN_PROGRESS,
                            "run_id": run_id,
                            "active_attempt_id": attempt.attempt_id,
                            "execution_workspace_id": ticket.execution_workspace_id,
                            "execution_policy": ticket.execution_policy,
                            "runtime_target_kind": ticket.runtime_target_kind,
                            "runtime_target_id": ticket.runtime_target_id,
                            "queue_ticket_id": None,
                            "last_status_reason": "Queued request changes started",
                        }
                    )
                )
                attached = True
            await self._repository.update_queue_ticket_async(
                ticket.model_copy(update={"status": BoardTodoQueueStatus.COMPLETED})
            )
        except asyncio.CancelledError:
            if attached:
                raise
            if run_id is not None:
                await self._stop_handoff_run(run_id)
            if ticket.queue_kind == BoardTodoQueueKind.START and session_id is not None:
                await self._cleanup_created_start_session(session_id)
            if ticket.queue_kind == BoardTodoQueueKind.START:
                await self._cleanup_start_execution_workspace(
                    execution_policy=ticket.execution_policy,
                    execution_workspace_id=execution_workspace_id,
                )
            await self._fail_queue_ticket(ticket=ticket, error="queue worker stopped")
            raise
        except Exception as exc:
            if attached:
                LOGGER.warning(
                    "Queued board TODO handoff %s attached run %s but failed final "
                    "ticket bookkeeping: %s",
                    ticket.queue_ticket_id,
                    run_id,
                    exc,
                )
                return
            if run_id is not None:
                await self._stop_handoff_run(run_id)
            if ticket.queue_kind == BoardTodoQueueKind.START and session_id is not None:
                await self._cleanup_created_start_session(session_id)
            if ticket.queue_kind == BoardTodoQueueKind.START:
                await self._cleanup_start_execution_workspace(
                    execution_policy=ticket.execution_policy,
                    execution_workspace_id=execution_workspace_id,
                )
            await self._fail_queue_ticket(ticket=ticket, error=str(exc))
        finally:
            stop_heartbeat.set()
            try:
                await heartbeat
            except asyncio.CancelledError:
                LOGGER.debug(
                    "Board TODO queue claim heartbeat cancelled during cleanup for ticket %s",
                    ticket.queue_ticket_id,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Board TODO queue claim heartbeat failed during cleanup for ticket %s: %s",
                    ticket.queue_ticket_id,
                    exc,
                )

    async def _current_queue_owned_item_or_cancel(
        self,
        *,
        ticket: BoardTodoExecutionQueueTicket,
        reason: str,
    ) -> BoardTodoItem | None:
        try:
            item = await self._repository.require_async(ticket.todo_id)
        except KeyError:
            await self._cancel_stale_queue_ticket(ticket=ticket, reason=reason)
            return None
        if not _queue_ticket_still_owns_item(ticket=ticket, item=item):
            await self._cancel_stale_queue_ticket(ticket=ticket, reason=reason)
            return None
        return item

    async def _current_handoff_reserved_item_or_raise(
        self,
        *,
        todo_id: str,
        attempt_id: str,
    ) -> BoardTodoItem:
        current = await self._repository.require_async(todo_id)
        if (
            current.status != BoardTodoStatus.IN_PROGRESS
            or current.active_attempt_id != attempt_id
            or current.queue_ticket_id is not None
        ):
            raise ValueError("board TODO handoff reservation is no longer current")
        return current

    async def _stop_handoff_run(self, run_id: str) -> None:
        try:
            await self._run_service.stop_run_async(run_id)
        except Exception as exc:
            LOGGER.warning(
                "Failed to stop board TODO handoff run %s after handoff failure: %s",
                run_id,
                exc,
            )

    async def _queue_claim_heartbeat(
        self,
        *,
        ticket: BoardTodoExecutionQueueTicket,
        stop_event: asyncio.Event,
    ) -> None:
        interval = max(1.0, _QUEUE_CLAIM_SECONDS / 3)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                try:
                    renewed = await self._repository.renew_queue_ticket_claim_async(
                        ticket=ticket,
                        claim_expires_at=_utc_now()
                        + timedelta(seconds=_QUEUE_CLAIM_SECONDS),
                    )
                except Exception as exc:
                    LOGGER.warning(
                        "Failed to renew board TODO queue ticket %s claim: %s",
                        ticket.queue_ticket_id,
                        exc,
                    )
                    return
                if renewed is None:
                    LOGGER.warning(
                        "Stopped queue claim heartbeat because ticket %s is no "
                        "longer claimed by this worker",
                        ticket.queue_ticket_id,
                    )
                    return

    async def _cancel_stale_queue_ticket(
        self,
        *,
        ticket: BoardTodoExecutionQueueTicket,
        reason: str,
    ) -> None:
        diagnostics = (*ticket.diagnostics, reason)
        await self._repository.update_queue_ticket_async(
            ticket.model_copy(
                update={
                    "status": BoardTodoQueueStatus.CANCELLED,
                    "diagnostics": diagnostics,
                }
            )
        )
        try:
            attempt = await self._repository.require_attempt_async(ticket.attempt_id)
            await self._repository.update_attempt_async(
                attempt.model_copy(
                    update={
                        "status": BoardTodoAttemptStatus.CANCELLED,
                        "error": reason,
                        "finished_at": _utc_now(),
                    }
                )
            )
        except KeyError:
            LOGGER.warning(
                "Board TODO queue ticket %s references missing attempt %s",
                ticket.queue_ticket_id,
                ticket.attempt_id,
            )

    async def _fail_queue_ticket(
        self,
        *,
        ticket: BoardTodoExecutionQueueTicket,
        error: str,
    ) -> None:
        try:
            item = await self._repository.require_async(ticket.todo_id)
        except KeyError:
            item = None
        diagnostics = (*ticket.diagnostics, error)
        failed_ticket = await self._repository.update_claimed_queue_ticket_async(
            ticket.model_copy(
                update={
                    "status": BoardTodoQueueStatus.FAILED,
                    "failure_count": ticket.failure_count + 1,
                    "diagnostics": diagnostics,
                }
            )
        )
        if failed_ticket is None:
            LOGGER.info(
                "Skipping stale board TODO queue ticket failure for %s because the "
                "claim is no longer current",
                ticket.queue_ticket_id,
            )
            return
        if item is not None and _queue_ticket_still_owns_item(ticket=ticket, item=item):
            await self._record_diagnostic(
                item=item,
                kind="queue_run_creation_failed",
                message=error,
                attempt_id=failed_ticket.attempt_id,
                queue_ticket_id=failed_ticket.queue_ticket_id,
            )
            try:
                attempt = await self._repository.require_attempt_async(
                    failed_ticket.attempt_id
                )
                await self._mark_attempt_failed(attempt=attempt, error=error)
            except KeyError:
                LOGGER.debug(
                    "Skipping failure mark for missing board TODO attempt %s "
                    "while failing queue ticket %s",
                    failed_ticket.attempt_id,
                    failed_ticket.queue_ticket_id,
                )
            restore_status = (
                BoardTodoStatus.TODO
                if failed_ticket.queue_kind == BoardTodoQueueKind.START
                else BoardTodoStatus.REVIEW
            )
            restore_run_id = (
                failed_ticket.previous_run_id
                if failed_ticket.queue_kind == BoardTodoQueueKind.REQUEST_CHANGES
                else None
            )
            stale_handoff_fields: dict[str, object] = {}
            if failed_ticket.queue_kind == BoardTodoQueueKind.START:
                stale_handoff_fields = {
                    "current_attempt_id": None,
                    "execution_workspace_id": None,
                    "execution_policy": None,
                    "runtime_target_kind": None,
                    "runtime_target_id": None,
                }
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": restore_status,
                        "run_id": restore_run_id,
                        "active_attempt_id": None,
                        "queue_ticket_id": None,
                        "last_status_reason": "Queued handoff failed",
                        **stale_handoff_fields,
                    }
                )
            )
        elif item is not None:
            LOGGER.info(
                "Skipping stale board TODO queue restore for ticket %s because TODO "
                "%s no longer owns the ticket",
                ticket.queue_ticket_id,
                item.todo_id,
            )

    async def _record_diagnostic(
        self,
        *,
        item: BoardTodoItem,
        kind: str,
        message: str,
        attempt_id: str | None = None,
        queue_ticket_id: str | None = None,
    ) -> None:
        await self._repository.create_diagnostic_async(
            BoardTodoDiagnostic(
                diagnostic_id=_new_diagnostic_id(),
                todo_id=item.todo_id,
                workspace_id=item.workspace_id,
                kind=kind,
                message=message,
                attempt_id=attempt_id,
                queue_ticket_id=queue_ticket_id,
            )
        )

    async def _mark_attempt_active(
        self,
        *,
        attempt: BoardTodoAttempt,
        session_id: str,
        run_id: str,
    ) -> BoardTodoAttempt:
        return await self._repository.update_attempt_async(
            attempt.model_copy(
                update={
                    "status": BoardTodoAttemptStatus.ACTIVE,
                    "session_id": session_id,
                    "run_id": run_id,
                    "started_at": attempt.started_at or _utc_now(),
                }
            )
        )

    async def _mark_attempt_failed(
        self,
        *,
        attempt: BoardTodoAttempt,
        error: str,
    ) -> BoardTodoAttempt:
        current = await self._repository.require_attempt_async(attempt.attempt_id)
        return await self._repository.update_attempt_async(
            current.model_copy(
                update={
                    "status": BoardTodoAttemptStatus.FAILED,
                    "error": error,
                    "finished_at": _utc_now(),
                }
            )
        )

    async def _mark_attempt_succeeded(
        self,
        attempt_id: str | None,
    ) -> None:
        if attempt_id is None:
            return
        try:
            attempt = await self._repository.require_attempt_async(attempt_id)
        except KeyError:
            return
        await self._repository.update_attempt_async(
            attempt.model_copy(
                update={
                    "status": BoardTodoAttemptStatus.SUCCEEDED,
                    "finished_at": _utc_now(),
                }
            )
        )

    async def mark_done(
        self,
        *,
        todo_id: str,
        payload: BoardTodoMarkDoneRequest,
    ) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.REVIEW:
            raise ValueError("only review board items can be marked done")
        reason = payload.reason.strip() if payload.reason else ""
        return await self._repository.update_async(
            item.model_copy(
                update={
                    "status": BoardTodoStatus.DONE,
                    "last_status_reason": reason or "Completed by user",
                }
            )
        )

    async def archive_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoArchiveRequest,
    ) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        return await self._repository.update_async(
            item.model_copy(
                update={
                    "status": BoardTodoStatus.ARCHIVED,
                    "archived_at": _utc_now(),
                    "last_status_reason": payload.reason or "Archived by user",
                }
            )
        )

    async def restore_todo(self, *, todo_id: str) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.ARCHIVED:
            raise ValueError("only archived board todo items can be restored")
        return await self._repository.update_async(
            item.model_copy(
                update={
                    "status": BoardTodoStatus.TODO,
                    "session_id": None,
                    "run_id": None,
                    "current_attempt_id": None,
                    "active_attempt_id": None,
                    "execution_workspace_id": None,
                    "execution_policy": None,
                    "runtime_target_kind": None,
                    "runtime_target_id": None,
                    "queue_ticket_id": None,
                    "archived_at": None,
                    "last_status_reason": "Restored from archive",
                }
            )
        )

    async def link_pull_request(
        self,
        *,
        todo_id: str,
        payload: BoardTodoLinkPullRequestRequest,
    ) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        if item.status == BoardTodoStatus.ARCHIVED:
            raise ValueError("archived board todo items cannot link a pull request")
        repository_full_name = item.repository_full_name
        if repository_full_name is None:
            scope = await self._resolve_board_scope(item.workspace_id)
            await self._ensure_default_sources(scope)
            repository_full_name = await self._primary_repository_full_name(
                item.workspace_id
            )
        pull_request_repository = _parse_github_pull_request_url(
            payload.pull_request_url,
            payload.pull_request_number,
        )
        if _has_text(payload.pull_request_url) and pull_request_repository is None:
            raise ValueError(
                "pull request URL must be a GitHub pull request URL matching "
                "the pull request number"
            )
        if repository_full_name is None:
            repository_full_name = pull_request_repository
        if repository_full_name is None:
            raise ValueError("cannot link a pull request without a GitHub repository")
        if pull_request_repository is not None and not _same_repository_full_name(
            pull_request_repository,
            repository_full_name,
        ):
            raise ValueError(
                "pull request URL repository does not match the board item"
            )
        repository_full_name = _normalize_repository_full_name(repository_full_name)
        if repository_full_name is None:
            raise ValueError("cannot link a pull request without a GitHub repository")
        pull_request = await self._pull_request_for_link(
            repository_full_name=repository_full_name,
            pull_request_number=payload.pull_request_number,
        )
        linked_pr_url = payload.pull_request_url or _json_text(
            pull_request.get("html_url") if pull_request is not None else None
        )
        is_merged = _pull_request_is_merged(pull_request)
        return await self._repository.update_async(
            item.model_copy(
                update={
                    "repository_full_name": repository_full_name,
                    "linked_pr_number": payload.pull_request_number,
                    "linked_pr_url": linked_pr_url,
                    "status": (BoardTodoStatus.DONE if is_merged else item.status),
                    "last_status_reason": (
                        "Linked GitHub pull request merged"
                        if is_merged
                        else "Pull request linked"
                    ),
                }
            )
        )

    async def _pull_request_for_link(
        self,
        *,
        repository_full_name: str,
        pull_request_number: int,
    ) -> JsonObject | None:
        owner, repo = repository_full_name.split("/", maxsplit=1)
        for token in await self._github_tokens():
            try:
                return await self._github_client.get_repository_pull_request(
                    token=token,
                    owner=owner,
                    repo=repo,
                    pull_request_number=pull_request_number,
                )
            except GitHubApiError as exc:
                LOGGER.warning(
                    "failed to read linked GitHub pull request for %s#%s: %s",
                    repository_full_name,
                    pull_request_number,
                    exc,
                )
        return None

    async def _resolve_board_scope(self, view_workspace_id: str) -> BoardTodoScope:
        current_workspace_id = str(view_workspace_id).strip()
        seen: set[str] = set()
        forked_from_workspace_id: str | None = None
        while True:
            if current_workspace_id in seen:
                raise ValueError("workspace fork scope contains a cycle")
            seen.add(current_workspace_id)
            workspace = await self._workspace_service.get_workspace_async(
                current_workspace_id
            )
            file_scope = workspace.profile.file_scope
            if file_scope.backend != FileScopeBackend.GIT_WORKTREE:
                return BoardTodoScope(
                    board_workspace_id=current_workspace_id,
                    view_workspace_id=view_workspace_id,
                    is_fork_view=current_workspace_id != view_workspace_id,
                    forked_from_workspace_id=forked_from_workspace_id,
                )
            parent_workspace_id = file_scope.forked_from_workspace_id
            if parent_workspace_id is None:
                raise ValueError("git_worktree workspace is missing root workspace")
            forked_from_workspace_id = forked_from_workspace_id or parent_workspace_id
            current_workspace_id = parent_workspace_id

    async def _ensure_default_sources(self, scope: BoardTodoScope) -> tuple[str, ...]:
        if await self._repository.get_todo_sources_bootstrapped_async(
            scope.board_workspace_id
        ):
            return ()
        sources = await self._repository.list_sources_async(
            workspace_id=scope.board_workspace_id
        )
        has_github_source = any(
            source.kind == BoardTodoSourceKind.GITHUB_ISSUES for source in sources
        )
        if has_github_source:
            await self._repository.mark_todo_sources_bootstrapped_async(
                scope.board_workspace_id
            )
            return ()
        repository_full_name, diagnostics = await self._resolve_repository_full_name(
            scope.board_workspace_id
        )
        if repository_full_name is None:
            await self._repository.bootstrap_todo_sources_async(
                workspace_id=scope.board_workspace_id,
                source=None,
            )
            return diagnostics
        now = _utc_now()
        await self._repository.bootstrap_todo_sources_async(
            workspace_id=scope.board_workspace_id,
            source=BoardTodoSource(
                source_id=_new_source_id(),
                workspace_id=scope.board_workspace_id,
                kind=BoardTodoSourceKind.GITHUB_ISSUES,
                provider=BoardTodoSourceProvider.GITHUB,
                display_name=repository_full_name,
                enabled=True,
                repository_full_name=repository_full_name,
                created_at=now,
                updated_at=now,
            ),
        )
        return ()

    async def _primary_repository_full_name(self, workspace_id: str) -> str | None:
        sources = await self._repository.list_sources_async(workspace_id=workspace_id)
        for source in sources:
            if (
                source.kind == BoardTodoSourceKind.GITHUB_ISSUES
                and source.enabled
                and source.repository_full_name is not None
            ):
                return source.repository_full_name
        return None

    async def mark_run_completed_async(self, *, run_id: str) -> None:
        items = await self._repository.list_in_progress_async()
        for item in items:
            if item.run_id != run_id:
                continue
            await self._mark_attempt_succeeded(item.active_attempt_id)
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.REVIEW,
                        "active_attempt_id": None,
                        "queue_ticket_id": None,
                        "last_status_reason": "Bound session run completed",
                    }
                )
            )

    async def mark_github_pull_request_merged_async(
        self,
        *,
        repository_full_name: str,
        pull_request_number: int,
    ) -> None:
        normalized_repository_full_name = _normalize_repository_full_name(
            repository_full_name
        )
        if normalized_repository_full_name is None:
            return
        await self._repository.mark_pull_request_done_async(
            repository_full_name=normalized_repository_full_name,
            pull_request_number=pull_request_number,
            reason="Linked GitHub pull request merged",
        )

    def mark_github_pull_request_merged(
        self,
        *,
        repository_full_name: str,
        pull_request_number: int,
    ) -> None:
        run_async_blocking(
            self.mark_github_pull_request_merged_async(
                repository_full_name=repository_full_name,
                pull_request_number=pull_request_number,
            )
        )

    async def mark_session_deleted_async(self, *, session_id: str) -> None:
        items = await self._repository.list_by_session_async(session_id=session_id)
        for item in items:
            if item.status == BoardTodoStatus.ARCHIVED:
                continue
            if item.status == BoardTodoStatus.DONE:
                await self._repository.update_async(
                    item.model_copy(
                        update={
                            "session_id": None,
                            "run_id": None,
                            "active_attempt_id": None,
                            "queue_ticket_id": None,
                            "last_status_reason": "Bound session deleted",
                        }
                    )
                )
                continue
            if item.active_attempt_id is not None:
                try:
                    attempt = await self._repository.require_attempt_async(
                        item.active_attempt_id
                    )
                    await self._mark_attempt_failed(
                        attempt=attempt,
                        error="Bound session deleted",
                    )
                except KeyError:
                    LOGGER.debug(
                        "Skipping missing board TODO attempt %s while deleting "
                        "session %s for item %s",
                        item.active_attempt_id,
                        session_id,
                        item.todo_id,
                    )
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.TODO,
                        "session_id": None,
                        "run_id": None,
                        "active_attempt_id": None,
                        "execution_workspace_id": None,
                        "execution_policy": None,
                        "runtime_target_kind": None,
                        "runtime_target_id": None,
                        "queue_ticket_id": None,
                        "last_status_reason": "Bound session deleted",
                    }
                )
            )

    def mark_session_deleted(self, *, session_id: str) -> None:
        run_async_blocking(self.mark_session_deleted_async(session_id=session_id))

    async def reconcile_workspace_async(self, *, workspace_id: str) -> None:
        await self._archive_untracked_pull_request_items(workspace_id=workspace_id)
        items = await self._repository.list_by_workspace_async(
            workspace_id=workspace_id,
            include_archived=False,
        )
        for item in items:
            if item.status != BoardTodoStatus.IN_PROGRESS or item.run_id is None:
                continue
            runtime = await self._run_runtime_repo.get_async(item.run_id)
            if runtime is None:
                if item.active_attempt_id is not None:
                    try:
                        attempt = await self._repository.require_attempt_async(
                            item.active_attempt_id
                        )
                        await self._mark_attempt_failed(
                            attempt=attempt,
                            error="Bound session run no longer exists",
                        )
                    except KeyError:
                        LOGGER.debug(
                            "Skipping missing board TODO attempt %s while "
                            "reconciling workspace %s for item %s",
                            item.active_attempt_id,
                            workspace_id,
                            item.todo_id,
                        )
                await self._repository.update_async(
                    item.model_copy(
                        update={
                            "status": BoardTodoStatus.TODO,
                            "session_id": None,
                            "run_id": None,
                            "active_attempt_id": None,
                            "execution_workspace_id": None,
                            "execution_policy": None,
                            "runtime_target_kind": None,
                            "runtime_target_id": None,
                            "queue_ticket_id": None,
                            "last_status_reason": "Bound session run no longer exists",
                        }
                    )
                )
                continue
            if runtime.status != RunRuntimeStatus.COMPLETED:
                continue
            await self._mark_attempt_succeeded(item.active_attempt_id)
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.REVIEW,
                        "active_attempt_id": None,
                        "queue_ticket_id": None,
                        "last_status_reason": "Bound session run completed",
                    }
                )
            )

    async def _include_runtime_display_delta_items(
        self,
        *,
        workspace_id: str,
        include_archived: bool,
        changed_items: tuple[BoardTodoItem, ...],
    ) -> tuple[BoardTodoItem, ...]:
        changed_todo_ids = {item.todo_id for item in changed_items}
        runtime_items = await self._runtime_display_items(
            tuple(
                item
                for item in await self._repository.list_by_workspace_async(
                    workspace_id=workspace_id,
                    include_archived=include_archived,
                )
                if item.status == BoardTodoStatus.IN_PROGRESS
                and item.run_id is not None
                and item.todo_id not in changed_todo_ids
            )
        )
        if not runtime_items:
            return changed_items
        return *changed_items, *runtime_items

    async def _runtime_display_items(
        self,
        items: tuple[BoardTodoItem, ...],
    ) -> tuple[BoardTodoItem, ...]:
        display_items: list[BoardTodoItem] = []
        for item in items:
            display_items.append(await self._runtime_display_item(item))
        return tuple(display_items)

    async def _runtime_display_item(self, item: BoardTodoItem) -> BoardTodoItem:
        if item.run_id is None:
            return item
        runtime = await self._run_runtime_repo.get_async(item.run_id)
        if runtime is None:
            return item
        return item.model_copy(
            update={
                "run_status": runtime.status.value,
                "run_phase": runtime.phase.value,
                "run_recoverable": runtime.is_recoverable,
                "run_last_error": runtime.last_error,
            }
        )

    async def _delta_response(
        self,
        *,
        scope: BoardTodoScope,
        repository_full_name: str | None,
        include_archived: bool,
        after_revision: int,
        diagnostics: tuple[str, ...],
        synced_at: datetime | None,
    ) -> BoardTodoDeltaResponse:
        changed_items = await self._runtime_display_items(
            _supported_board_items(
                await self._repository.list_delta_async(
                    workspace_id=scope.board_workspace_id,
                    after_revision=after_revision,
                    include_archived=include_archived,
                )
            )
        )
        changed_items = _supported_board_items(
            await self._include_runtime_display_delta_items(
                workspace_id=scope.board_workspace_id,
                include_archived=include_archived,
                changed_items=changed_items,
            )
        )
        removed_todo_ids = (
            ()
            if include_archived
            else await self._supported_removed_todo_ids(
                workspace_id=scope.board_workspace_id,
                after_revision=after_revision,
            )
        )
        current_items = await self._runtime_display_items(
            _supported_board_items(
                await self._repository.list_by_workspace_async(
                    workspace_id=scope.board_workspace_id,
                    include_archived=include_archived,
                )
            )
        )
        sources = await self._repository.list_sources_async(
            workspace_id=scope.board_workspace_id
        )
        revision = await self._repository.get_workspace_revision_async(
            scope.board_workspace_id
        )
        return BoardTodoDeltaResponse(
            workspace_id=scope.view_workspace_id,
            board_workspace_id=scope.board_workspace_id,
            view_workspace_id=scope.view_workspace_id,
            is_fork_view=scope.is_fork_view,
            forked_from_workspace_id=scope.forked_from_workspace_id,
            repository_full_name=repository_full_name,
            changed_items=changed_items,
            removed_todo_ids=removed_todo_ids,
            source_groups=_source_groups(sources=sources, items=current_items),
            status_counts=_status_counts(current_items),
            diagnostics=diagnostics,
            synced_at=synced_at,
            revision=revision,
        )

    async def _supported_removed_todo_ids(
        self,
        *,
        workspace_id: str,
        after_revision: int,
    ) -> tuple[str, ...]:
        removed_todo_ids = await self._repository.list_removed_from_active_since_async(
            workspace_id=workspace_id,
            after_revision=after_revision,
        )
        supported_ids: list[str] = []
        for todo_id in removed_todo_ids:
            item = await self._repository.get_async(todo_id)
            if item is not None and _is_supported_board_item(item):
                supported_ids.append(todo_id)
        return tuple(supported_ids)

    async def _upsert_github_issues(
        self,
        *,
        workspace_id: str,
        source_id: str | None = None,
        repository_full_name: str,
        issues: Sequence[JsonObject],
        synced_at: datetime,
    ) -> None:
        for issue in issues:
            if isinstance(issue.get("pull_request"), dict):
                continue
            number = _json_int(issue.get("number"))
            title = _json_text(issue.get("title"))
            if number is None or not title:
                continue
            if _github_issue_is_closed(issue):
                continue
            await self._repository.upsert_source_async(
                BoardTodoItem(
                    todo_id=_new_todo_id(),
                    workspace_id=workspace_id,
                    source_id=source_id,
                    status=BoardTodoStatus.TODO,
                    title=title,
                    body=_json_text(issue.get("body")),
                    source_provider=BoardTodoSourceProvider.GITHUB,
                    source_type=BoardTodoSourceType.GITHUB_ISSUE,
                    source_key=f"github:{repository_full_name}:issue:{number}",
                    repository_full_name=repository_full_name,
                    issue_number=number,
                    html_url=_json_text(issue.get("html_url")) or None,
                    last_synced_at=synced_at,
                    source_updated_at=_json_datetime_or_none(issue.get("updated_at")),
                )
            )

    async def _reconcile_closed_github_issues(
        self,
        *,
        token: str,
        workspace_id: str,
        repository_full_name: str,
        owner: str,
        repo: str,
        issues: Sequence[JsonObject],
        pull_request_map: Mapping[int, JsonObject],
    ) -> int:
        archived_count = 0
        for issue in issues:
            if isinstance(issue.get("pull_request"), dict):
                continue
            if not _github_issue_is_closed(issue):
                continue
            number = _json_int(issue.get("number"))
            if number is None:
                continue
            source_key = f"github:{repository_full_name}:issue:{number}"
            try:
                item = await self._repository.require_by_source_async(
                    workspace_id=workspace_id,
                    source_provider=BoardTodoSourceProvider.GITHUB,
                    source_key=source_key,
                )
            except KeyError:
                continue
            if item.status in (BoardTodoStatus.ARCHIVED, BoardTodoStatus.DONE):
                continue
            pull_request = await self._pull_request_for_merge_check(
                token=token,
                owner=owner,
                repo=repo,
                repository_full_name=repository_full_name,
                pull_request_map=pull_request_map,
                pull_request_number=item.linked_pr_number,
            )
            if item.linked_pr_number is not None and _pull_request_is_merged(
                pull_request
            ):
                await self.mark_github_pull_request_merged_async(
                    repository_full_name=repository_full_name,
                    pull_request_number=item.linked_pr_number,
                )
                continue
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.ARCHIVED,
                        "archived_at": item.archived_at or _utc_now(),
                        "last_synced_at": _utc_now(),
                        "last_status_reason": "GitHub issue closed",
                    }
                )
            )
            archived_count += 1
        return archived_count

    async def _reconcile_non_open_github_issues(
        self,
        *,
        token: str,
        workspace_id: str,
        repository_full_name: str,
        owner: str,
        repo: str,
        issues: Sequence[JsonObject],
        pull_request_map: Mapping[int, JsonObject],
    ) -> tuple[int, int]:
        open_issue_numbers = {
            number
            for issue in issues
            if not isinstance(issue.get("pull_request"), dict)
            if not _github_issue_is_closed(issue)
            if (number := _json_int(issue.get("number"))) is not None
        }
        items = await self._repository.list_active_github_issue_items_async(
            workspace_id=workspace_id,
            repository_full_name=repository_full_name,
        )
        archived_count = 0
        done_count = 0
        for item in items:
            if item.issue_number is None or item.issue_number in open_issue_numbers:
                continue
            if item.status == BoardTodoStatus.DONE:
                continue
            pull_request = await self._pull_request_for_merge_check(
                token=token,
                owner=owner,
                repo=repo,
                repository_full_name=repository_full_name,
                pull_request_map=pull_request_map,
                pull_request_number=item.linked_pr_number,
            )
            if item.linked_pr_number is not None and _pull_request_is_merged(
                pull_request
            ):
                await self.mark_github_pull_request_merged_async(
                    repository_full_name=repository_full_name,
                    pull_request_number=item.linked_pr_number,
                )
                done_count += 1
                continue
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.ARCHIVED,
                        "archived_at": item.archived_at or _utc_now(),
                        "last_synced_at": _utc_now(),
                        "last_status_reason": "GitHub issue no longer open",
                    }
                )
            )
            archived_count += 1
        return archived_count, done_count

    async def _archive_untracked_pull_request_items(self, *, workspace_id: str) -> None:
        items = await self._repository.list_by_workspace_async(
            workspace_id=workspace_id,
            include_archived=False,
        )
        for item in items:
            if item.source_type != BoardTodoSourceType.GITHUB_PULL_REQUEST:
                continue
            if item.session_id is not None or item.run_id is not None:
                continue
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.ARCHIVED,
                        "archived_at": item.archived_at or _utc_now(),
                        "last_status_reason": (
                            "GitHub pull requests are linked to issue TODOs"
                        ),
                    }
                )
            )

    async def _link_review_issues_to_pull_requests(
        self,
        *,
        token: str,
        workspace_id: str,
        repository_full_name: str,
        owner: str,
        repo: str,
        pull_requests: Sequence[JsonObject],
    ) -> None:
        pull_request_map = _pull_request_map(pull_requests)
        items = await self._repository.list_by_workspace_async(
            workspace_id=workspace_id,
            include_archived=False,
        )
        for item in items:
            if (
                item.status != BoardTodoStatus.REVIEW
                or item.source_type != BoardTodoSourceType.GITHUB_ISSUE
                or item.issue_number is None
                or item.repository_full_name != repository_full_name
            ):
                continue
            linked_pr_number = item.linked_pr_number
            if linked_pr_number is None:
                try:
                    timeline_events = (
                        await self._github_client.list_issue_timeline_events(
                            token=token,
                            owner=owner,
                            repo=repo,
                            issue_number=item.issue_number,
                        )
                    )
                except GitHubApiError as exc:
                    LOGGER.warning(
                        "failed to read GitHub issue timeline for %s#%s: %s",
                        repository_full_name,
                        item.issue_number,
                        exc,
                    )
                    continue
                linked = _linked_pull_request_from_events(
                    timeline_events,
                    pull_request_map=pull_request_map,
                )
                if linked is None:
                    continue
                linked_pr_number, linked_pr_url = linked
                await self._repository.update_async(
                    item.model_copy(
                        update={
                            "linked_pr_number": linked_pr_number,
                            "linked_pr_url": linked_pr_url,
                            "last_status_reason": (
                                "Linked GitHub pull request found for issue"
                            ),
                        }
                    )
                )
            pull_request = await self._pull_request_for_merge_check(
                token=token,
                owner=owner,
                repo=repo,
                repository_full_name=repository_full_name,
                pull_request_map=pull_request_map,
                pull_request_number=linked_pr_number,
            )
            if linked_pr_number is not None and _pull_request_is_merged(pull_request):
                await self.mark_github_pull_request_merged_async(
                    repository_full_name=repository_full_name,
                    pull_request_number=linked_pr_number,
                )

    async def _pull_request_for_merge_check(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        repository_full_name: str,
        pull_request_map: Mapping[int, JsonObject],
        pull_request_number: int | None,
    ) -> JsonObject | None:
        if pull_request_number is None:
            return None
        pull_request = pull_request_map.get(pull_request_number)
        if pull_request is not None:
            return pull_request
        try:
            return await self._github_client.get_repository_pull_request(
                token=token,
                owner=owner,
                repo=repo,
                pull_request_number=pull_request_number,
            )
        except GitHubApiError as exc:
            LOGGER.warning(
                "failed to read GitHub pull request for %s#%s: %s",
                repository_full_name,
                pull_request_number,
                exc,
            )
            return None

    async def _create_and_start_run(
        self,
        *,
        session_id: str,
        prompt: str,
        yolo: bool,
        thinking: RunThinkingConfig | None = None,
        target_role_id: str | None = None,
        session_mode: SessionMode = SessionMode.NORMAL,
    ) -> tuple[str, str]:
        content = content_parts_from_text(prompt)
        shell_safety_policy_enabled = await asyncio.to_thread(
            self._get_shell_safety_policy_enabled
        )
        run_id, resolved_session_id = await self._run_service.create_run_async(
            IntentInput(
                session_id=session_id,
                input=content,
                display_input=content,
                yolo=yolo,
                shell_safety_policy_enabled=shell_safety_policy_enabled,
                thinking=thinking or RunThinkingConfig(),
                target_role_id=target_role_id,
                session_mode=session_mode,
            ),
            source=InjectionSource.USER,
        )
        try:
            await self._run_service.ensure_run_started_async(run_id)
        except Exception:
            await self._stop_handoff_run(run_id)
            raise
        return run_id, resolved_session_id

    async def _resolve_repository_full_name(
        self,
        workspace_id: str,
    ) -> tuple[str | None, tuple[str, ...]]:
        workspace = await self._workspace_service.get_workspace_async(workspace_id)
        root_path = workspace.root_path
        if root_path is None:
            return None, ("Workspace has no local root path.",)
        remote_url = await asyncio.to_thread(_read_git_remote_url, root_path)
        if remote_url is None:
            return None, ("Workspace has no origin Git remote.",)
        repository_full_name = _parse_github_remote(remote_url)
        if repository_full_name is None:
            return None, ("Workspace origin remote is not a GitHub repository.",)
        return repository_full_name, ()

    async def _github_tokens(self) -> tuple[str, ...]:
        accounts = await self._github_trigger_service.list_accounts_async()
        tokens: list[str] = []
        for account in accounts:
            if account.status != GitHubTriggerAccountStatus.ENABLED:
                continue
            token = await self._github_trigger_service.resolve_account_token_async(
                account.account_id
            )
            if token:
                tokens.append(token)
        shared_token = self._get_shared_github_token()
        if shared_token and shared_token not in tokens:
            tokens.append(shared_token)
        return tuple(tokens)


def _default_execution_policy(item: BoardTodoItem) -> BoardTodoExecutionPolicy:
    if item.execution_policy is not None:
        return item.execution_policy
    return BoardTodoExecutionPolicy.FORK_GIT_WORKTREE


def _runtime_target_from_session(
    session: SessionRecord,
) -> BoardTodoRuntimeTargetOption:
    return _resolve_runtime_target(
        runtime_target_id=None,
        session_mode=session.session_mode,
        normal_root_role_id=session.normal_root_role_id,
        orchestration_preset_id=session.orchestration_preset_id,
    )


def _request_changes_runtime_target(
    *,
    runtime_target_id: str | None,
    item: BoardTodoItem,
    session: SessionRecord,
) -> BoardTodoRuntimeTargetOption:
    if runtime_target_id is None and (
        item.runtime_target_id is None and item.runtime_target_kind is None
    ):
        return _runtime_target_from_session(session)
    return _resolve_runtime_target(
        runtime_target_id=runtime_target_id,
        session_mode=None,
        normal_root_role_id=None,
        orchestration_preset_id=None,
        fallback_runtime_target_id=item.runtime_target_id,
        fallback_runtime_target_kind=item.runtime_target_kind,
    )


def _resolve_runtime_target(
    *,
    runtime_target_id: str | None,
    session_mode: SessionMode | None,
    normal_root_role_id: str | None,
    orchestration_preset_id: str | None,
    fallback_runtime_target_id: str | None = None,
    fallback_runtime_target_kind: BoardTodoRuntimeTargetKind | None = None,
) -> BoardTodoRuntimeTargetOption:
    explicit_target_id = str(runtime_target_id or "").strip()
    if explicit_target_id and not (
        explicit_target_id.startswith("preset:")
        or explicit_target_id.startswith("role:")
    ):
        raise ValueError("runtime target id must start with role: or preset:")
    raw_target_id = str(runtime_target_id or fallback_runtime_target_id or "").strip()
    if raw_target_id.startswith("preset:"):
        preset_id = raw_target_id.removeprefix("preset:").strip()
        if not preset_id:
            raise ValueError("runtime target preset id is required")
        return BoardTodoRuntimeTargetOption(
            target_id=f"preset:{preset_id}",
            kind=BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET,
            label=f"Preset: {preset_id}",
        )
    if raw_target_id.startswith("role:"):
        role_id = raw_target_id.removeprefix("role:").strip()
        if not role_id:
            raise ValueError("runtime target role id is required")
        return BoardTodoRuntimeTargetOption(
            target_id=f"role:{role_id}",
            kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
            label=f"Role: {role_id}",
        )
    if fallback_runtime_target_kind == BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET:
        preset_id = (fallback_runtime_target_id or "default").removeprefix("preset:")
        return BoardTodoRuntimeTargetOption(
            target_id=f"preset:{preset_id}",
            kind=BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET,
            label=f"Preset: {preset_id}",
        )
    if fallback_runtime_target_kind == BoardTodoRuntimeTargetKind.LOCAL_ROLE:
        role_id = (fallback_runtime_target_id or "main_agent").removeprefix("role:")
        return BoardTodoRuntimeTargetOption(
            target_id=f"role:{role_id}",
            kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
            label=f"Role: {role_id}",
        )
    if session_mode == SessionMode.ORCHESTRATION:
        preset_id = orchestration_preset_id or "default"
        return BoardTodoRuntimeTargetOption(
            target_id=f"preset:{preset_id}",
            kind=BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET,
            label=f"Preset: {preset_id}",
        )
    role_id = normal_root_role_id or "main_agent"
    return BoardTodoRuntimeTargetOption(
        target_id=f"role:{role_id}",
        kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
        label=f"Role: {role_id}",
    )


def _runtime_target_options(
    *,
    selected: BoardTodoRuntimeTargetOption | None = None,
) -> tuple[BoardTodoRuntimeTargetOption, ...]:
    options = [
        BoardTodoRuntimeTargetOption(
            target_id="role:main_agent",
            kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
            label="Main Agent",
        ),
        BoardTodoRuntimeTargetOption(
            target_id="preset:default",
            kind=BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET,
            label="Default orchestration",
        ),
    ]
    if selected is not None and all(
        option.target_id != selected.target_id for option in options
    ):
        options.insert(0, selected)
    return tuple(options)


def _execution_workspace_preview(
    *,
    item: BoardTodoItem,
    scope: BoardTodoScope,
    execution_policy: BoardTodoExecutionPolicy,
) -> BoardTodoExecutionWorkspacePreview:
    if execution_policy == BoardTodoExecutionPolicy.CURRENT_WORKSPACE:
        return BoardTodoExecutionWorkspacePreview(
            policy=execution_policy,
            workspace_id=scope.view_workspace_id,
            source_workspace_id=item.workspace_id,
            display_name=scope.view_workspace_id,
        )
    return BoardTodoExecutionWorkspacePreview(
        policy=execution_policy,
        workspace_id=None,
        source_workspace_id=item.workspace_id,
        display_name="New git worktree fork",
    )


def _queue_preview(
    *,
    concurrency: BoardTodoConcurrencySnapshot,
    queue_if_full: bool,
) -> BoardTodoQueuePreview:
    slot_available = not _should_queue(concurrency)
    reason = None
    if not slot_available:
        reason = "handoff concurrency limit reached"
    return BoardTodoQueuePreview(
        queue_if_full=queue_if_full,
        slot_available=slot_available,
        will_queue=not slot_available and queue_if_full,
        reason=reason,
    )


def _should_queue(concurrency: BoardTodoConcurrencySnapshot) -> bool:
    return (
        concurrency.source_workspace_active >= concurrency.source_workspace_limit
        or concurrency.runtime_target_active >= concurrency.runtime_target_limit
    )


def _start_session_topology(
    *,
    runtime_target: BoardTodoRuntimeTargetOption,
    session_mode: SessionMode | None,
    normal_root_role_id: str | None,
    orchestration_preset_id: str | None,
) -> tuple[SessionMode | None, str | None, str | None]:
    if runtime_target.kind == BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET:
        if session_mode not in (None, SessionMode.ORCHESTRATION):
            raise ValueError("runtime target preset requires orchestration mode")
        preset_id = runtime_target.target_id.removeprefix("preset:")
        if orchestration_preset_id is not None and orchestration_preset_id != preset_id:
            raise ValueError("orchestration preset must match runtime target")
        return (
            SessionMode.ORCHESTRATION,
            None,
            preset_id,
        )
    if runtime_target.kind == BoardTodoRuntimeTargetKind.LOCAL_ROLE:
        if session_mode not in (None, SessionMode.NORMAL):
            raise ValueError("runtime target role requires normal mode")
        role_id = runtime_target.target_id.removeprefix("role:")
        if (
            normal_root_role_id is not None
            and normal_root_role_id.removeprefix("role:") != role_id
        ):
            raise ValueError("normal root role must match runtime target")
        return (
            SessionMode.NORMAL,
            role_id,
            None,
        )
    return (
        session_mode,
        normal_root_role_id if session_mode == SessionMode.NORMAL else None,
        (
            orchestration_preset_id
            if session_mode == SessionMode.ORCHESTRATION
            else None
        ),
    )


def _validate_runtime_target_matches_session(
    *,
    runtime_target: BoardTodoRuntimeTargetOption,
    session: SessionRecord,
) -> None:
    if (
        session.session_mode == SessionMode.NORMAL
        and runtime_target.kind != BoardTodoRuntimeTargetKind.LOCAL_ROLE
    ):
        raise ValueError("normal sessions require a role runtime target")
    if (
        session.session_mode == SessionMode.ORCHESTRATION
        and runtime_target.kind != BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET
    ):
        raise ValueError("orchestration sessions require a preset runtime target")
    if session.session_mode == SessionMode.ORCHESTRATION:
        requested_preset_id = runtime_target.target_id.removeprefix("preset:")
        session_preset_id = session.orchestration_preset_id or "default"
        if requested_preset_id != session_preset_id:
            raise ValueError("orchestration preset must match the existing session")


def _queue_ticket_can_be_claimed(ticket: BoardTodoExecutionQueueTicket) -> bool:
    if ticket.status == BoardTodoQueueStatus.PENDING:
        return True
    if ticket.status != BoardTodoQueueStatus.CLAIMED:
        return False
    if ticket.claim_expires_at is None:
        return False
    return ticket.claim_expires_at <= _utc_now()


def _queue_ticket_sorts_after(
    ticket: BoardTodoExecutionQueueTicket,
    reference: BoardTodoExecutionQueueTicket,
) -> bool:
    return (ticket.created_at, ticket.queue_ticket_id) > (
        reference.created_at,
        reference.queue_ticket_id,
    )


def _queue_ticket_still_owns_item(
    *,
    ticket: BoardTodoExecutionQueueTicket,
    item: BoardTodoItem,
) -> bool:
    return (
        item.status == BoardTodoStatus.IN_PROGRESS
        and item.queue_ticket_id == ticket.queue_ticket_id
        and item.active_attempt_id == ticket.attempt_id
    )


def _render_handoff_template(
    *,
    template: str,
    item: BoardTodoItem,
    feedback: str | None,
) -> str:
    values = {
        "todo.id": item.todo_id,
        "todo.title": item.title,
        "todo.body": item.body,
        "todo.url": item.html_url or "",
        "todo.repository": item.repository_full_name or "",
        "todo.issue_number": str(item.issue_number or ""),
        "workspace.board_id": item.workspace_id,
        "handoff.feedback": feedback or "",
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered.strip()


def _execution_workspace_name(*, item: BoardTodoItem, attempt_id: str) -> str:
    suffix = attempt_id.removeprefix("battempt_")[:10]
    source = item.repository_full_name or item.workspace_id
    safe_source = re.sub(r"[^a-zA-Z0-9_-]+", "-", source).strip("-").lower()
    return f"{safe_source}-todo-{item.todo_id.removeprefix('btodo_')[:8]}-{suffix}"


def _target_role_id_for_run(
    *,
    session: SessionRecord,
    ticket: BoardTodoExecutionQueueTicket,
) -> str | None:
    if session.session_mode != SessionMode.NORMAL:
        return None
    if ticket.normal_root_role_id is not None:
        return ticket.normal_root_role_id
    if ticket.runtime_target_id is not None and ticket.runtime_target_id.startswith(
        "role:"
    ):
        return ticket.runtime_target_id.removeprefix("role:")
    return session.normal_root_role_id


def _role_id_from_runtime_target(runtime_target_id: str | None) -> str | None:
    if runtime_target_id is None or not runtime_target_id.startswith("role:"):
        return None
    role_id = runtime_target_id.removeprefix("role:").strip()
    return role_id or None


def _board_response(
    *,
    scope: BoardTodoScope,
    repository_full_name: str | None,
    items: tuple[BoardTodoItem, ...],
    sources: tuple[BoardTodoSource, ...],
    diagnostics: tuple[str, ...],
    synced_at: datetime | None,
    revision: int,
) -> BoardTodoBoardResponse:
    return BoardTodoBoardResponse(
        workspace_id=scope.view_workspace_id,
        board_workspace_id=scope.board_workspace_id,
        view_workspace_id=scope.view_workspace_id,
        is_fork_view=scope.is_fork_view,
        forked_from_workspace_id=scope.forked_from_workspace_id,
        repository_full_name=repository_full_name,
        items=items,
        source_groups=_source_groups(sources=sources, items=items),
        status_counts=_status_counts(items),
        diagnostics=diagnostics,
        synced_at=synced_at,
        revision=revision,
    )


def _status_counts(items: tuple[BoardTodoItem, ...]) -> BoardTodoStatusCounts:
    counts = BoardTodoStatusCounts()
    for item in items:
        if item.status == BoardTodoStatus.TODO:
            counts.todo += 1
        elif item.status == BoardTodoStatus.IN_PROGRESS:
            counts.in_progress += 1
        elif item.status == BoardTodoStatus.REVIEW:
            counts.review += 1
        elif item.status == BoardTodoStatus.DONE:
            counts.done += 1
        elif item.status == BoardTodoStatus.ARCHIVED:
            counts.archived += 1
    return counts


def _is_supported_board_item(item: BoardTodoItem) -> bool:
    return (
        item.source_provider != BoardTodoSourceProvider.LOCAL
        and item.source_type != BoardTodoSourceType.MANUAL
    )


def _supported_board_items(
    items: tuple[BoardTodoItem, ...],
) -> tuple[BoardTodoItem, ...]:
    return tuple(item for item in items if _is_supported_board_item(item))


def _configurable_sources(
    sources: tuple[BoardTodoSource, ...],
) -> tuple[BoardTodoSource, ...]:
    return tuple(
        source for source in sources if source.kind == BoardTodoSourceKind.GITHUB_ISSUES
    )


def _source_groups(
    *,
    sources: tuple[BoardTodoSource, ...],
    items: tuple[BoardTodoItem, ...],
) -> tuple[BoardTodoSourceGroup, ...]:
    groups: list[BoardTodoSourceGroup] = []
    grouped_source_ids: set[str] = set()
    for source in _configurable_sources(sources):
        groups.append(
            BoardTodoSourceGroup(
                group_id=source.source_id,
                source_id=source.source_id,
                kind=source.kind.value,
                display_name=source.display_name,
                enabled=source.enabled,
                repository_full_name=source.repository_full_name,
            )
        )
        grouped_source_ids.add(source.source_id)
    for item in items:
        if not _is_supported_board_item(item):
            continue
        source_id = item.source_id
        if source_id is not None and source_id in grouped_source_ids:
            continue
        group_id = source_id or f"source:{item.source_provider.value}:{item.source_key}"
        if group_id in grouped_source_ids:
            continue
        groups.append(
            BoardTodoSourceGroup(
                group_id=group_id,
                source_id=source_id,
                kind=item.source_type.value,
                display_name=item.repository_full_name
                or f"{item.source_provider.value}/{item.source_type.value}",
                enabled=False,
                repository_full_name=item.repository_full_name,
            )
        )
        grouped_source_ids.add(group_id)
    return tuple(groups)


def _pull_request_map(
    pull_requests: Sequence[JsonObject],
) -> dict[int, JsonObject]:
    result: dict[int, JsonObject] = {}
    for pull_request in pull_requests:
        number = _json_int(pull_request.get("number"))
        if number is None:
            continue
        result[number] = pull_request
    return result


def _pull_request_is_merged(pull_request: JsonObject | None) -> bool:
    if pull_request is None:
        return False
    return pull_request.get("merged") is True or bool(
        _json_text(pull_request.get("merged_at"))
    )


def _github_issue_is_closed(issue: JsonObject) -> bool:
    return _json_text(issue.get("state")).lower() == "closed"


def _format_github_sync_error(
    *,
    error: GitHubApiError,
    repository_full_name: str,
    force_full: bool,
) -> str:
    raw_message = str(error).strip()
    if not raw_message and error.status_code is not None:
        raw_message = f"GitHub sync failed with status {error.status_code}"
    if not raw_message:
        raw_message = "GitHub sync failed before response"
    status = f" status={error.status_code}" if error.status_code is not None else ""
    mode = "full" if force_full else "incremental"
    return (
        f"GitHub sync failed for {repository_full_name} ({mode}{status}): {raw_message}"
    )


def _linked_pull_request_from_events(
    events: Sequence[JsonObject],
    *,
    pull_request_map: Mapping[int, JsonObject],
) -> tuple[int, str | None] | None:
    first_reference: tuple[int, str | None] | None = None
    first_merged_reference: tuple[int, str | None] | None = None
    for event in events:
        linked = _extract_pull_request_reference(event)
        if linked is None:
            continue
        number, url = linked
        pull_request = pull_request_map.get(number)
        resolved_url = url or _json_text(
            None if pull_request is None else pull_request.get("html_url")
        )
        reference = (number, resolved_url or None)
        if _timeline_event_suggests_closing_pr(event):
            return reference
        if first_merged_reference is None and _pull_request_is_merged(pull_request):
            first_merged_reference = reference
        if first_reference is None:
            first_reference = reference
    return first_merged_reference or first_reference


def _timeline_event_suggests_closing_pr(event: JsonObject) -> bool:
    return _json_text(event.get("event")).lower() in {
        "closed",
        "connected",
        "merged",
    }


def _extract_pull_request_reference(
    value: JsonValue | None,
) -> tuple[int, str | None] | None:
    if not isinstance(value, dict):
        return None
    pull_request = value.get("pull_request")
    if isinstance(pull_request, dict):
        number = _json_int(value.get("number")) or _json_int(pull_request.get("number"))
        if number is not None:
            return number, _json_text(value.get("html_url")) or _json_text(
                pull_request.get("html_url")
            ) or None
    for key in ("issue", "source", "subject"):
        child = value.get(key)
        linked = _extract_pull_request_reference(child)
        if linked is not None:
            return linked
        if isinstance(child, dict):
            nested_issue = child.get("issue")
            linked = _extract_pull_request_reference(nested_issue)
            if linked is not None:
                return linked
    return None


def _build_start_prompt(item: BoardTodoItem) -> str:
    parts = [
        "Please process this board TODO item.",
        "",
        f"Title: {item.title}",
        f"Workspace ID: {item.workspace_id}",
        f"Source: {item.source_provider.value}/{item.source_type.value}",
    ]
    if item.repository_full_name:
        parts.append(f"Repository: {item.repository_full_name}")
    if item.issue_number is not None:
        parts.append(f"Issue: #{item.issue_number}")
    if item.pull_request_number is not None:
        parts.append(f"Pull request: #{item.pull_request_number}")
    if item.html_url:
        parts.append(f"URL: {item.html_url}")
    if item.body:
        parts.extend(("", "Body:", item.body))
    parts.extend(
        (
            "",
            "When finished, summarize the changes, verification, and review notes.",
        )
    )
    return "\n".join(parts)


def _build_request_changes_prompt(*, item: BoardTodoItem, feedback: str) -> str:
    parts = [
        "Please revise the work for this board TODO item.",
        "",
        f"Title: {item.title}",
        f"Board TODO ID: {item.todo_id}",
    ]
    if item.html_url:
        parts.append(f"Original URL: {item.html_url}")
    parts.extend(("", "Requested changes:", feedback))
    return "\n".join(parts)


def _read_git_remote_url(root_path: Path) -> str | None:
    for args in (
        ("git", "-C", str(root_path), "remote", "get-url", "origin"),
        ("git", "-C", str(root_path), "config", "--get", "remote.origin.url"),
        ("git", "-C", str(root_path), "remote", "-v"),
    ):
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.debug("failed to read git remote from %s: %s", root_path, exc)
            continue
        if result.returncode == 0 and result.stdout.strip():
            if args[-1] == "-v":
                fallback_remote_url = _first_github_remote_url(result.stdout)
                if fallback_remote_url is not None:
                    return fallback_remote_url
                continue
            return result.stdout.strip()
    return None


def _first_github_remote_url(remote_output: str) -> str | None:
    for line in remote_output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        remote_url = parts[1].strip()
        if _parse_github_remote(remote_url) is not None:
            return remote_url
    return None


def _parse_github_remote(remote_url: str) -> str | None:
    match = _GITHUB_REMOTE_PATTERN.match(remote_url.strip())
    if match is not None:
        return _normalize_repository_full_name(
            f"{match.group('owner')}/{_strip_git_suffix(match.group('repo'))}"
        )
    parsed = urlparse(remote_url)
    if parsed.hostname != "github.com":
        return None
    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        return None
    owner = parts[0].strip()
    repo = _strip_git_suffix(parts[1].strip())
    if not owner or not repo:
        return None
    return f"{owner.lower()}/{repo.lower()}"


def _parse_github_pull_request_url(
    pull_request_url: str | None,
    pull_request_number: int,
) -> str | None:
    if pull_request_url is None:
        return None
    parsed = urlparse(pull_request_url.strip())
    if parsed.hostname != "github.com":
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 4 or parts[2] != "pull":
        return None
    try:
        url_pull_request_number = int(parts[3])
    except ValueError:
        return None
    if url_pull_request_number != pull_request_number:
        return None
    owner = parts[0].strip()
    repo = _strip_git_suffix(parts[1].strip())
    if not owner or not repo:
        return None
    return _normalize_repository_full_name(f"{owner}/{repo}")


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _strip_git_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def _json_text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _json_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _json_datetime_or_none(value: JsonValue | None) -> datetime | None:
    text = _json_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _new_todo_id() -> str:
    return f"btodo_{uuid4().hex[:12]}"


def _new_source_id() -> str:
    return f"bsrc_{uuid4().hex[:12]}"


def _new_attempt_id() -> str:
    return f"battempt_{uuid4().hex[:12]}"


def _new_prompt_ref() -> str:
    return f"bprompt_{uuid4().hex[:12]}"


def _new_template_id() -> str:
    return f"btemplate_{uuid4().hex[:12]}"


def _new_queue_ticket_id() -> str:
    return f"bqueue_{uuid4().hex[:12]}"


def _new_diagnostic_id() -> str:
    return f"bdiag_{uuid4().hex[:12]}"


def _normalize_repository_full_name(value: str | None) -> str | None:
    text = str(value or "").strip().strip("/")
    if not text:
        return None
    parts = text.split("/")
    if len(parts) != 2:
        return None
    owner = parts[0].strip()
    repo = _strip_git_suffix(parts[1].strip())
    if not owner or not repo:
        return None
    return f"{owner.lower()}/{repo.lower()}"


def _same_repository_full_name(left: str | None, right: str | None) -> bool:
    normalized_left = _normalize_repository_full_name(left)
    normalized_right = _normalize_repository_full_name(right)
    return (
        normalized_left is not None
        and normalized_right is not None
        and normalized_left == normalized_right
    )


def _require_session_id(item: BoardTodoItem) -> str:
    if item.session_id is None:
        raise ValueError("board todo item is not bound to a session")
    return item.session_id


def _github_sync_cursor(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
