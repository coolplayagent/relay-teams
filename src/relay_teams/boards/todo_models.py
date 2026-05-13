# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class BoardTodoStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    ARCHIVED = "archived"


class BoardTodoSourceProvider(str, Enum):
    LOCAL = "local"
    GITHUB = "github"


class BoardTodoSourceType(str, Enum):
    MANUAL = "manual"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PULL_REQUEST = "github_pull_request"


class BoardTodoSourceKind(str, Enum):
    MANUAL = "manual"
    GITHUB_ISSUES = "github_issues"


class BoardTodoSyncStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BoardTodoAttemptType(str, Enum):
    START = "start"
    REQUEST_CHANGES = "request_changes"


class BoardTodoAttemptStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BoardTodoExecutionPolicy(str, Enum):
    FORK_GIT_WORKTREE = "fork_git_worktree"
    CURRENT_WORKSPACE = "current_workspace"


class BoardTodoRuntimeTargetKind(str, Enum):
    LOCAL_ROLE = "local_role"
    ORCHESTRATION_PRESET = "orchestration_preset"


class BoardTodoQueueKind(str, Enum):
    START = "start"
    REQUEST_CHANGES = "request_changes"


class BoardTodoQueueStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BoardTodoTemplateScope(str, Enum):
    WORKSPACE = "workspace"
    SOURCE = "source"


class BoardTodoHandoffTemplateKind(str, Enum):
    START = "start"
    REQUEST_CHANGES = "request_changes"


class BoardTodoStatusCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo: int = 0
    in_progress: int = 0
    review: int = 0
    done: int = 0
    archived: int = 0


class BoardTodoSourceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: RequiredIdentifierStr
    source_id: OptionalIdentifierStr = None
    kind: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    enabled: bool = True
    repository_full_name: str | None = None


class BoardTodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    source_id: OptionalIdentifierStr = None
    status: BoardTodoStatus = BoardTodoStatus.TODO
    title: str = Field(min_length=1)
    body: str = ""
    source_provider: BoardTodoSourceProvider = BoardTodoSourceProvider.LOCAL
    source_type: BoardTodoSourceType = BoardTodoSourceType.MANUAL
    source_key: str = Field(min_length=1)
    repository_full_name: str | None = None
    issue_number: int | None = Field(default=None, ge=1)
    pull_request_number: int | None = Field(default=None, ge=1)
    html_url: str | None = None
    session_id: OptionalIdentifierStr = None
    run_id: OptionalIdentifierStr = None
    current_attempt_id: OptionalIdentifierStr = None
    active_attempt_id: OptionalIdentifierStr = None
    execution_workspace_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy | None = None
    runtime_target_kind: BoardTodoRuntimeTargetKind | None = None
    runtime_target_id: str | None = None
    queue_ticket_id: OptionalIdentifierStr = None
    run_status: str | None = None
    run_phase: str | None = None
    run_recoverable: bool = False
    run_last_error: str | None = None
    linked_pr_number: int | None = Field(default=None, ge=1)
    linked_pr_url: str | None = None
    archived_at: datetime | None = None
    last_synced_at: datetime | None = None
    source_updated_at: datetime | None = None
    last_status_reason: str | None = None
    item_revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _validate_source_shape(self) -> BoardTodoItem:
        if self.source_provider == BoardTodoSourceProvider.LOCAL:
            if self.source_type != BoardTodoSourceType.MANUAL:
                raise ValueError("local board todo items must use manual source type")
            return self
        if self.source_provider == BoardTodoSourceProvider.GITHUB:
            if not str(self.repository_full_name or "").strip():
                raise ValueError("GitHub board todo items require repository_full_name")
            if self.source_type == BoardTodoSourceType.GITHUB_ISSUE:
                if self.issue_number is None:
                    raise ValueError(
                        "GitHub issue board todo items require issue_number"
                    )
                return self
            if self.source_type == BoardTodoSourceType.GITHUB_PULL_REQUEST:
                if self.pull_request_number is None:
                    raise ValueError(
                        "GitHub pull request board todo items require pull_request_number"
                    )
                return self
        raise ValueError("invalid board todo source")


class BoardTodoBoardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    board_workspace_id: RequiredIdentifierStr | None = None
    view_workspace_id: RequiredIdentifierStr | None = None
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None
    repository_full_name: str | None = None
    items: tuple[BoardTodoItem, ...] = ()
    source_groups: tuple[BoardTodoSourceGroup, ...] = ()
    status_counts: BoardTodoStatusCounts = Field(default_factory=BoardTodoStatusCounts)
    diagnostics: tuple[str, ...] = ()
    synced_at: datetime | None = None
    revision: int = 0


class BoardTodoDeltaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    board_workspace_id: RequiredIdentifierStr | None = None
    view_workspace_id: RequiredIdentifierStr | None = None
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None
    repository_full_name: str | None = None
    changed_items: tuple[BoardTodoItem, ...] = ()
    removed_todo_ids: tuple[RequiredIdentifierStr, ...] = ()
    source_groups: tuple[BoardTodoSourceGroup, ...] = ()
    status_counts: BoardTodoStatusCounts = Field(default_factory=BoardTodoStatusCounts)
    diagnostics: tuple[str, ...] = ()
    synced_at: datetime | None = None
    revision: int = 0


class BoardTodoSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    include_archived: bool = False


class BoardTodoSyncChangesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    include_archived: bool = False
    after_revision: int = Field(default=0, ge=0)
    force_full: bool = False


class BoardTodoStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_workspace_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy | None = None
    runtime_target_id: str | None = None
    queue_if_full: bool = True
    final_prompt: str | None = None
    prompt: str | None = None
    session_mode: SessionMode | None = None
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


class BoardTodoPreviewStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_workspace_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy | None = None
    runtime_target_id: str | None = None
    queue_if_full: bool = True


class BoardTodoStartRoleOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = ""


class BoardTodoStartPresetOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = ""


class BoardTodoRuntimeTargetOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    kind: BoardTodoRuntimeTargetKind
    label: str = Field(min_length=1)
    description: str = ""


class BoardTodoExecutionWorkspacePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: BoardTodoExecutionPolicy
    workspace_id: OptionalIdentifierStr = None
    source_workspace_id: RequiredIdentifierStr
    display_name: str = Field(min_length=1)


class BoardTodoConcurrencySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_workspace_active: int = Field(default=0, ge=0)
    source_workspace_limit: int = Field(default=2, ge=1)
    runtime_target_active: int = Field(default=0, ge=0)
    runtime_target_limit: int = Field(default=1, ge=1)


class BoardTodoQueuePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_if_full: bool = True
    slot_available: bool = True
    will_queue: bool = False
    reason: str | None = None


class BoardTodoPreviewStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: RequiredIdentifierStr
    board_workspace_id: RequiredIdentifierStr
    view_workspace_id: RequiredIdentifierStr
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None
    template_kind: str = "start"
    template_source: str = "built_in"
    prompt: str
    execution_policy: BoardTodoExecutionPolicy = (
        BoardTodoExecutionPolicy.FORK_GIT_WORKTREE
    )
    execution_workspace_preview: BoardTodoExecutionWorkspacePreview | None = None
    runtime_target_id: str | None = None
    runtime_target_options: tuple[BoardTodoRuntimeTargetOption, ...] = ()
    concurrency: BoardTodoConcurrencySnapshot = Field(
        default_factory=BoardTodoConcurrencySnapshot
    )
    queue_preview: BoardTodoQueuePreview = Field(default_factory=BoardTodoQueuePreview)
    session_mode: SessionMode | None = None
    normal_root_role_id: OptionalIdentifierStr = None
    normal_mode_roles: tuple[BoardTodoStartRoleOption, ...] = ()
    orchestration_preset_id: OptionalIdentifierStr = None
    orchestration_presets: tuple[BoardTodoStartPresetOption, ...] = ()
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    diagnostics: tuple[str, ...] = ()


class BoardTodoPreviewRequestChangesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_workspace_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy | None = None
    runtime_target_id: str | None = None
    queue_if_full: bool = True
    feedback: str = Field(min_length=1)


class BoardTodoPreviewRequestChangesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: RequiredIdentifierStr
    board_workspace_id: RequiredIdentifierStr
    view_workspace_id: RequiredIdentifierStr
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None
    template_kind: str = "request_changes"
    template_source: str = "built_in"
    prompt: str
    execution_policy: BoardTodoExecutionPolicy | None = None
    execution_workspace_preview: BoardTodoExecutionWorkspacePreview | None = None
    runtime_target_id: str | None = None
    runtime_target_options: tuple[BoardTodoRuntimeTargetOption, ...] = ()
    concurrency: BoardTodoConcurrencySnapshot = Field(
        default_factory=BoardTodoConcurrencySnapshot
    )
    queue_preview: BoardTodoQueuePreview = Field(default_factory=BoardTodoQueuePreview)
    session_id: OptionalIdentifierStr = None
    run_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    diagnostics: tuple[str, ...] = ()


class BoardTodoStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_workspace_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy | None = None
    runtime_target_id: str | None = None
    queue_if_full: bool = True
    feedback: str = Field(min_length=1)
    final_prompt: str | None = None
    prompt: str | None = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


class BoardTodoHandoffPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_ref: RequiredIdentifierStr
    todo_id: RequiredIdentifierStr
    attempt_id: RequiredIdentifierStr
    template_kind: str = Field(min_length=1)
    template_source: str = Field(min_length=1)
    final_prompt_snapshot: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class BoardTodoAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: RequiredIdentifierStr
    todo_id: RequiredIdentifierStr
    attempt_type: BoardTodoAttemptType
    status: BoardTodoAttemptStatus = BoardTodoAttemptStatus.PENDING
    board_workspace_id: OptionalIdentifierStr = None
    initiated_from_workspace_id: OptionalIdentifierStr = None
    source_workspace_id: OptionalIdentifierStr = None
    execution_workspace_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy | None = None
    runtime_target_kind: BoardTodoRuntimeTargetKind | None = None
    runtime_target_id: str | None = None
    queue_ticket_id: OptionalIdentifierStr = None
    handoff_initiator: str = "human"
    start_policy: str = "human_required"
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    session_id: OptionalIdentifierStr = None
    run_id: OptionalIdentifierStr = None
    prompt_ref: OptionalIdentifierStr = None
    summary: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BoardTodoExecutionQueueTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_ticket_id: RequiredIdentifierStr
    todo_id: RequiredIdentifierStr
    attempt_id: RequiredIdentifierStr
    prompt_ref: RequiredIdentifierStr
    queue_kind: BoardTodoQueueKind
    status: BoardTodoQueueStatus = BoardTodoQueueStatus.PENDING
    board_workspace_id: RequiredIdentifierStr
    source_workspace_id: RequiredIdentifierStr
    initiated_from_workspace_id: OptionalIdentifierStr = None
    execution_workspace_id: OptionalIdentifierStr = None
    previous_run_id: OptionalIdentifierStr = None
    execution_policy: BoardTodoExecutionPolicy = (
        BoardTodoExecutionPolicy.FORK_GIT_WORKTREE
    )
    runtime_target_kind: BoardTodoRuntimeTargetKind | None = None
    runtime_target_id: str | None = None
    session_mode: SessionMode | None = None
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    claim_token: str | None = None
    claim_expires_at: datetime | None = None
    claimed_by: str | None = None
    failure_count: int = Field(default=0, ge=0)
    diagnostics: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class BoardTodoHandoffTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    scope: BoardTodoTemplateScope
    template_kind: BoardTodoHandoffTemplateKind
    template: str = Field(min_length=1)
    source_id: OptionalIdentifierStr = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _validate_template_scope(self) -> BoardTodoHandoffTemplate:
        if self.scope == BoardTodoTemplateScope.SOURCE and self.source_id is None:
            raise ValueError("source handoff templates require source_id")
        if (
            self.scope == BoardTodoTemplateScope.WORKSPACE
            and self.source_id is not None
        ):
            raise ValueError("workspace handoff templates cannot include source_id")
        return self


class BoardTodoHandoffTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    template_kind: BoardTodoHandoffTemplateKind
    template: str = Field(min_length=1)


class BoardTodoHandoffTemplateSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    board_workspace_id: RequiredIdentifierStr
    view_workspace_id: RequiredIdentifierStr
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None
    templates: tuple[BoardTodoHandoffTemplate, ...] = ()


class BoardTodoHandoffTemplateDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool = True
    template_id: RequiredIdentifierStr


class BoardTodoDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnostic_id: RequiredIdentifierStr
    todo_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    kind: str = Field(min_length=1)
    message: str = Field(min_length=1)
    attempt_id: OptionalIdentifierStr = None
    queue_ticket_id: OptionalIdentifierStr = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class BoardTodoMarkDoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class BoardTodoArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class BoardTodoLinkPullRequestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pull_request_number: int = Field(ge=1)
    pull_request_url: str | None = None


class BoardTodoScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_workspace_id: RequiredIdentifierStr
    view_workspace_id: RequiredIdentifierStr
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None


class BoardTodoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    kind: BoardTodoSourceKind
    provider: BoardTodoSourceProvider
    display_name: str = Field(min_length=1)
    enabled: bool = True
    repository_full_name: str | None = None
    system_managed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _validate_source_config(self) -> BoardTodoSource:
        if self.kind == BoardTodoSourceKind.MANUAL:
            if self.provider != BoardTodoSourceProvider.LOCAL:
                raise ValueError("manual board todo sources must use local provider")
            return self
        if self.kind == BoardTodoSourceKind.GITHUB_ISSUES:
            if self.provider != BoardTodoSourceProvider.GITHUB:
                raise ValueError("github_issues sources must use github provider")
            if not str(self.repository_full_name or "").strip():
                raise ValueError("github_issues sources require repository_full_name")
            return self
        raise ValueError("invalid board todo source kind")


class BoardTodoSourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    sync_cursor: datetime | None = None
    last_sync_started_at: datetime | None = None
    last_sync_finished_at: datetime | None = None
    last_sync_status: BoardTodoSyncStatus = BoardTodoSyncStatus.IDLE
    last_diagnostics: tuple[str, ...] = ()


class BoardTodoSourceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: BoardTodoSource
    state: BoardTodoSourceState | None = None


class BoardTodoSourceSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    board_workspace_id: RequiredIdentifierStr
    view_workspace_id: RequiredIdentifierStr
    is_fork_view: bool = False
    forked_from_workspace_id: OptionalIdentifierStr = None
    sources: tuple[BoardTodoSourceView, ...] = ()
    diagnostics: tuple[str, ...] = ()


class BoardTodoSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    kind: BoardTodoSourceKind = BoardTodoSourceKind.GITHUB_ISSUES
    display_name: str = Field(min_length=1)
    enabled: bool = True
    repository_full_name: str | None = None


class BoardTodoSourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr | None = None
    display_name: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    repository_full_name: str | None = None


class BoardTodoSourceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool
    source_id: RequiredIdentifierStr
