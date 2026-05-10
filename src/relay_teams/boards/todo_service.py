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
    BoardTodoArchiveRequest,
    BoardTodoBoardResponse,
    BoardTodoCreateInput,
    BoardTodoDeltaResponse,
    BoardTodoItem,
    BoardTodoLinkPullRequestRequest,
    BoardTodoSourceProvider,
    BoardTodoSourceType,
    BoardTodoStartRequest,
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
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import SessionRecord
from relay_teams.triggers.github_client import GitHubApiError, JsonObject
from relay_teams.triggers.models import (
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
)
from relay_teams.workspace.workspace_models import WorkspaceRecord

LOGGER = get_logger(__name__)
_GITHUB_REMOTE_PATTERN = re.compile(
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


class WorkspaceServiceLike(Protocol):
    async def get_workspace_async(self, workspace_id: str) -> WorkspaceRecord:
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
    ) -> SessionRecord:
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
    ) -> None:
        self._repository = repository
        self._workspace_service = workspace_service
        self._github_trigger_service = github_trigger_service
        self._github_client = github_client
        self._session_service = session_service
        self._run_service = run_service
        self._run_runtime_repo = run_runtime_repo
        self._get_shared_github_token = get_shared_github_token or (lambda: None)

    async def list_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        repository_full_name, diagnostics = await self._resolve_repository_full_name(
            workspace_id
        )
        await self.reconcile_workspace_async(workspace_id=workspace_id)
        items = await self._repository.list_by_workspace_async(
            workspace_id=workspace_id,
            include_archived=include_archived,
        )
        revision = await self._repository.get_workspace_revision_async(workspace_id)
        return _board_response(
            workspace_id=workspace_id,
            repository_full_name=repository_full_name,
            items=items,
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
        repository_full_name, diagnostics = await self._resolve_repository_full_name(
            workspace_id
        )
        await self.reconcile_workspace_async(workspace_id=workspace_id)
        return await self._delta_response(
            workspace_id=workspace_id,
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
        repository_full_name, sync_diagnostics, synced_at = await self._sync_github(
            workspace_id=workspace_id,
            force_full=True,
        )
        await self.reconcile_workspace_async(workspace_id=workspace_id)
        items = await self._repository.list_by_workspace_async(
            workspace_id=workspace_id,
            include_archived=include_archived,
        )
        revision = await self._repository.get_workspace_revision_async(workspace_id)
        return _board_response(
            workspace_id=workspace_id,
            repository_full_name=repository_full_name,
            items=items,
            diagnostics=sync_diagnostics,
            synced_at=synced_at,
            revision=revision,
        )

    async def sync_board_changes(
        self,
        request: BoardTodoSyncChangesRequest,
    ) -> BoardTodoDeltaResponse:
        repository_full_name, diagnostics, synced_at = await self._sync_github(
            workspace_id=request.workspace_id,
            force_full=request.force_full,
        )
        await self.reconcile_workspace_async(workspace_id=request.workspace_id)
        return await self._delta_response(
            workspace_id=request.workspace_id,
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
        repository_full_name, diagnostics = await self._resolve_repository_full_name(
            workspace_id
        )
        if repository_full_name is None:
            return None, diagnostics, None
        owner, repo = repository_full_name.split("/", maxsplit=1)
        synced_at = _utc_now()
        sync_diagnostics = list(diagnostics)
        synced = False
        sync_cursor = None
        if not force_full:
            sync_cursor = await self._repository.get_github_issue_sync_cursor_async(
                workspace_id=workspace_id,
                repository_full_name=repository_full_name,
            )
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
            await self._repository.update_github_issue_sync_cursor_async(
                workspace_id=workspace_id,
                repository_full_name=repository_full_name,
                cursor=_github_sync_cursor(synced_at),
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
        return (
            repository_full_name,
            tuple(sync_diagnostics),
            synced_at if synced else None,
        )

    async def create_todo(self, payload: BoardTodoCreateInput) -> BoardTodoItem:
        todo_id = _new_todo_id()
        now = _utc_now()
        item = BoardTodoItem(
            todo_id=todo_id,
            workspace_id=payload.workspace_id,
            status=BoardTodoStatus.TODO,
            title=payload.title.strip(),
            body=payload.body,
            source_provider=BoardTodoSourceProvider.LOCAL,
            source_type=BoardTodoSourceType.MANUAL,
            source_key=f"manual:{todo_id}",
            created_at=now,
            updated_at=now,
        )
        return await self._repository.create_async(item)

    async def start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStartRequest,
    ) -> BoardTodoItem:
        item = await self._repository.require_async(todo_id)
        if item.status != BoardTodoStatus.TODO:
            raise ValueError("only todo board items can be started")
        reserved = await self._repository.reserve_start_async(item)
        try:
            session = await self._session_service.create_session_async(
                workspace_id=reserved.workspace_id,
                metadata={
                    "board_todo_id": reserved.todo_id,
                    "board_todo_title": reserved.title,
                },
            )
            prompt = payload.prompt or _build_start_prompt(reserved)
            run_id, _session_id = await self._create_and_start_run(
                session_id=session.session_id,
                prompt=prompt,
                yolo=payload.yolo,
            )
            return await self._repository.update_async(
                reserved.model_copy(
                    update={
                        "status": BoardTodoStatus.IN_PROGRESS,
                        "session_id": session.session_id,
                        "run_id": run_id,
                        "last_status_reason": "Started from board todo item",
                    }
                )
            )
        except Exception:
            await self._restore_failed_start_reservation(reserved)
            raise

    async def _restore_failed_start_reservation(
        self,
        item: BoardTodoItem,
    ) -> None:
        try:
            current = await self._repository.require_async(item.todo_id)
            if (
                current.status == BoardTodoStatus.IN_PROGRESS
                and current.session_id is None
                and current.run_id is None
            ):
                await self._repository.update_async(
                    current.model_copy(
                        update={
                            "status": BoardTodoStatus.TODO,
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
        reserved = await self._repository.reserve_request_changes_async(item)
        try:
            prompt = _build_request_changes_prompt(
                item=reserved,
                feedback=payload.feedback,
            )
            run_id, _session_id = await self._create_and_start_run(
                session_id=_require_session_id(reserved),
                prompt=prompt,
                yolo=payload.yolo,
            )
            return await self._repository.update_async(
                reserved.model_copy(
                    update={
                        "status": BoardTodoStatus.IN_PROGRESS,
                        "run_id": run_id,
                        "last_status_reason": "Changes requested by user",
                    }
                )
            )
        except Exception:
            await self._restore_failed_request_changes_reservation(
                reserved,
                previous_run_id=item.run_id,
            )
            raise

    async def _restore_failed_request_changes_reservation(
        self,
        item: BoardTodoItem,
        *,
        previous_run_id: str | None,
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
            (
                repository_full_name,
                _diagnostics,
            ) = await self._resolve_repository_full_name(item.workspace_id)
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
        if (
            pull_request_repository is not None
            and pull_request_repository != repository_full_name
        ):
            raise ValueError(
                "pull request URL repository does not match the board item"
            )
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

    async def mark_run_completed_async(self, *, run_id: str) -> None:
        items = await self._repository.list_in_progress_async()
        for item in items:
            if item.run_id != run_id:
                continue
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.REVIEW,
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
        await self._repository.mark_pull_request_done_async(
            repository_full_name=repository_full_name,
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
                            "last_status_reason": "Bound session deleted",
                        }
                    )
                )
                continue
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.TODO,
                        "session_id": None,
                        "run_id": None,
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
                await self._repository.update_async(
                    item.model_copy(
                        update={
                            "status": BoardTodoStatus.TODO,
                            "session_id": None,
                            "run_id": None,
                            "last_status_reason": "Bound session run no longer exists",
                        }
                    )
                )
                continue
            if runtime.status != RunRuntimeStatus.COMPLETED:
                if runtime.status in (
                    RunRuntimeStatus.FAILED,
                    RunRuntimeStatus.STOPPED,
                ):
                    await self._repository.update_async(
                        item.model_copy(
                            update={
                                "status": BoardTodoStatus.TODO,
                                "session_id": None,
                                "run_id": None,
                                "last_status_reason": (
                                    "Bound session run ended without completion"
                                ),
                            }
                        )
                    )
                continue
            await self._repository.update_async(
                item.model_copy(
                    update={
                        "status": BoardTodoStatus.REVIEW,
                        "last_status_reason": "Bound session run completed",
                    }
                )
            )

    async def _delta_response(
        self,
        *,
        workspace_id: str,
        repository_full_name: str | None,
        include_archived: bool,
        after_revision: int,
        diagnostics: tuple[str, ...],
        synced_at: datetime | None,
    ) -> BoardTodoDeltaResponse:
        changed_items = await self._repository.list_delta_async(
            workspace_id=workspace_id,
            after_revision=after_revision,
            include_archived=include_archived,
        )
        removed_todo_ids = (
            ()
            if include_archived
            else await self._repository.list_removed_from_active_since_async(
                workspace_id=workspace_id,
                after_revision=after_revision,
            )
        )
        current_items = await self._repository.list_by_workspace_async(
            workspace_id=workspace_id,
            include_archived=include_archived,
        )
        revision = await self._repository.get_workspace_revision_async(workspace_id)
        return BoardTodoDeltaResponse(
            workspace_id=workspace_id,
            repository_full_name=repository_full_name,
            changed_items=changed_items,
            removed_todo_ids=removed_todo_ids,
            status_counts=_status_counts(current_items),
            diagnostics=diagnostics,
            synced_at=synced_at,
            revision=revision,
        )

    async def _upsert_github_issues(
        self,
        *,
        workspace_id: str,
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
    ) -> tuple[str, str]:
        content = content_parts_from_text(prompt)
        run_id, resolved_session_id = await self._run_service.create_run_async(
            IntentInput(
                session_id=session_id,
                input=content,
                display_input=content,
                yolo=yolo,
            ),
            source=InjectionSource.USER,
        )
        await self._run_service.ensure_run_started_async(run_id)
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


def _board_response(
    *,
    workspace_id: str,
    repository_full_name: str | None,
    items: tuple[BoardTodoItem, ...],
    diagnostics: tuple[str, ...],
    synced_at: datetime | None,
    revision: int,
) -> BoardTodoBoardResponse:
    return BoardTodoBoardResponse(
        workspace_id=workspace_id,
        repository_full_name=repository_full_name,
        items=items,
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
        return f"{match.group('owner')}/{_strip_git_suffix(match.group('repo'))}"
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
    return f"{owner}/{repo}"


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
    return f"{owner}/{repo}"


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


def _require_session_id(item: BoardTodoItem) -> str:
    if item.session_id is None:
        raise ValueError("board todo item is not bound to a session")
    return item.session_id


def _github_sync_cursor(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
