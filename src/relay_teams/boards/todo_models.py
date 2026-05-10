# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class BoardTodoStatusCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo: int = 0
    in_progress: int = 0
    review: int = 0
    done: int = 0
    archived: int = 0


class BoardTodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
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
    repository_full_name: str | None = None
    items: tuple[BoardTodoItem, ...] = ()
    status_counts: BoardTodoStatusCounts = Field(default_factory=BoardTodoStatusCounts)
    diagnostics: tuple[str, ...] = ()
    synced_at: datetime | None = None
    revision: int = 0


class BoardTodoDeltaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    repository_full_name: str | None = None
    changed_items: tuple[BoardTodoItem, ...] = ()
    removed_todo_ids: tuple[RequiredIdentifierStr, ...] = ()
    status_counts: BoardTodoStatusCounts = Field(default_factory=BoardTodoStatusCounts)
    diagnostics: tuple[str, ...] = ()
    synced_at: datetime | None = None
    revision: int = 0


class BoardTodoCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    title: str = Field(min_length=1)
    body: str = ""


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

    prompt: str | None = None
    yolo: bool = True


class BoardTodoStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(min_length=1)
    yolo: bool = True


class BoardTodoArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class BoardTodoLinkPullRequestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pull_request_number: int = Field(ge=1)
    pull_request_url: str | None = None
