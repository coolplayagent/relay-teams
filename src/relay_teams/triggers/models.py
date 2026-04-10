# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from relay_teams.sessions.runs.enums import ExecutionMode
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class TriggerProvider(str, Enum):
    GITHUB = "github"


class GitHubTriggerAccountStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class GitHubWebhookStatus(str, Enum):
    UNREGISTERED = "unregistered"
    REGISTERED = "registered"
    ERROR = "error"


class TriggerTargetType(str, Enum):
    RUN_TEMPLATE = "run_template"
    AUTOMATION_PROJECT = "automation_project"


class TriggerDeliverySignatureStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"


class TriggerDeliveryIngestStatus(str, Enum):
    RECEIVED = "received"
    DUPLICATE = "duplicate"
    SIGNATURE_INVALID = "signature_invalid"
    INVALID_HEADERS = "invalid_headers"
    UNMATCHED = "unmatched"
    TRIGGERED = "triggered"
    FAILED = "failed"


class TriggerDispatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggerActionPhase(str, Enum):
    IMMEDIATE = "immediate"
    ON_RUN_COMPLETED = "on_run_completed"
    ON_RUN_FAILED = "on_run_failed"


class TriggerActionStatus(str, Enum):
    PENDING = "pending"
    WAITING_RUN = "waiting_run"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class GitHubActionType(str, Enum):
    COMMENT = "comment"
    ADD_LABELS = "add_labels"
    REMOVE_LABELS = "remove_labels"
    ASSIGN_USERS = "assign_users"
    UNASSIGN_USERS = "unassign_users"
    SET_COMMIT_STATUS = "set_commit_status"


class GitHubTriggerRunTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    prompt_template: str = Field(min_length=1)
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


class GitHubActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: GitHubActionType
    phase: TriggerActionPhase = TriggerActionPhase.IMMEDIATE
    body_template: str | None = None
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    commit_status_state: Literal["pending", "success", "failure", "error"] | None = None
    commit_status_context: str | None = None
    commit_status_description_template: str | None = None
    commit_status_target_url_template: str | None = None

    @model_validator(mode="after")
    def _validate_action_fields(self) -> GitHubActionSpec:
        if self.action_type == GitHubActionType.COMMENT:
            if not str(self.body_template or "").strip():
                raise ValueError("body_template is required for comment actions")
        if (
            self.action_type
            in {
                GitHubActionType.ADD_LABELS,
                GitHubActionType.REMOVE_LABELS,
            }
            and not self.labels
        ):
            raise ValueError("labels are required for label actions")
        if (
            self.action_type
            in {
                GitHubActionType.ASSIGN_USERS,
                GitHubActionType.UNASSIGN_USERS,
            }
            and not self.assignees
        ):
            raise ValueError("assignees are required for assignment actions")
        if self.action_type == GitHubActionType.SET_COMMIT_STATUS:
            if self.commit_status_state is None:
                raise ValueError(
                    "commit_status_state is required for set_commit_status actions"
                )
            if not str(self.commit_status_context or "").strip():
                raise ValueError(
                    "commit_status_context is required for set_commit_status actions"
                )
        return self


class TriggerRuleMatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(min_length=1)
    actions: tuple[str, ...] = ()
    base_branches: tuple[str, ...] = ()
    head_branches: tuple[str, ...] = ()
    labels_any: tuple[str, ...] = ()
    labels_all: tuple[str, ...] = ()
    draft_pr: bool | None = None
    sender_allow: tuple[str, ...] = ()
    sender_deny: tuple[str, ...] = ()
    paths_any: tuple[str, ...] = ()
    paths_ignore: tuple[str, ...] = ()
    check_conclusions: tuple[str, ...] = ()


class TriggerDispatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: TriggerTargetType
    run_template: GitHubTriggerRunTemplate | None = None
    automation_project_id: OptionalIdentifierStr = None
    action_hooks: tuple[GitHubActionSpec, ...] = ()

    @model_validator(mode="after")
    def _validate_target_fields(self) -> TriggerDispatchConfig:
        if self.target_type == TriggerTargetType.RUN_TEMPLATE:
            if self.run_template is None:
                raise ValueError("run_template is required for run_template targets")
            self.automation_project_id = None
        if self.target_type == TriggerTargetType.AUTOMATION_PROJECT:
            if not str(self.automation_project_id or "").strip():
                raise ValueError(
                    "automation_project_id is required for automation_project targets"
                )
            self.run_template = None
        return self


class GitHubTriggerAccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    display_name: str | None = None
    token: str | None = None
    webhook_secret: str | None = None
    enabled: bool = True


class GitHubTriggerAccountUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    display_name: str | None = None
    token: str | None = None
    webhook_secret: str | None = None
    enabled: bool | None = None


class GitHubTriggerAccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: GitHubTriggerAccountStatus
    token_configured: bool = False
    webhook_secret_configured: bool = False
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class GitHubRepoSubscriptionCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: RequiredIdentifierStr
    owner: str = Field(min_length=1)
    repo_name: str = Field(min_length=1)
    subscribed_events: tuple[str, ...] = ()
    register_webhook: bool = False
    callback_url: str | None = None


class GitHubRepoSubscriptionUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscribed_events: tuple[str, ...] | None = None
    enabled: bool | None = None


class GitHubRepoWebhookRegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback_url: str = Field(min_length=1)


class GitHubRepoSubscriptionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_subscription_id: RequiredIdentifierStr
    account_id: RequiredIdentifierStr
    owner: str = Field(min_length=1)
    repo_name: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    external_repo_id: OptionalIdentifierStr = None
    default_branch: str | None = None
    provider_webhook_id: str | None = None
    subscribed_events: tuple[str, ...] = ()
    webhook_status: GitHubWebhookStatus = GitHubWebhookStatus.UNREGISTERED
    enabled: bool = True
    last_webhook_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class TriggerRuleCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    provider: TriggerProvider = TriggerProvider.GITHUB
    account_id: RequiredIdentifierStr
    repo_subscription_id: RequiredIdentifierStr
    match_config: TriggerRuleMatchConfig
    dispatch_config: TriggerDispatchConfig
    enabled: bool = True


class TriggerRuleUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    match_config: TriggerRuleMatchConfig | None = None
    dispatch_config: TriggerDispatchConfig | None = None
    enabled: bool | None = None


class TriggerRuleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_rule_id: RequiredIdentifierStr
    provider: TriggerProvider = TriggerProvider.GITHUB
    account_id: RequiredIdentifierStr
    repo_subscription_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    enabled: bool = True
    match_config: TriggerRuleMatchConfig
    dispatch_config: TriggerDispatchConfig
    version: int = Field(default=1, ge=1)
    last_error: str | None = None
    last_fired_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class TriggerDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_delivery_id: RequiredIdentifierStr
    provider: TriggerProvider = TriggerProvider.GITHUB
    provider_delivery_id: str | None = None
    account_id: OptionalIdentifierStr = None
    repo_subscription_id: OptionalIdentifierStr = None
    event_name: str = Field(min_length=1)
    event_action: str | None = None
    signature_status: TriggerDeliverySignatureStatus
    ingest_status: TriggerDeliveryIngestStatus
    headers: dict[str, str] = Field(default_factory=dict)
    payload: JsonValue
    normalized_payload: dict[str, JsonValue] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    processed_at: datetime | None = None
    last_error: str | None = None


class TriggerEvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_evaluation_id: RequiredIdentifierStr
    trigger_delivery_id: RequiredIdentifierStr
    trigger_rule_id: RequiredIdentifierStr
    matched: bool
    reason_code: str = Field(min_length=1)
    reason_detail: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class TriggerDispatchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_dispatch_id: RequiredIdentifierStr
    trigger_delivery_id: RequiredIdentifierStr
    trigger_rule_id: RequiredIdentifierStr
    target_type: TriggerTargetType
    status: TriggerDispatchStatus
    session_id: OptionalIdentifierStr = None
    run_id: OptionalIdentifierStr = None
    automation_project_id: OptionalIdentifierStr = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class TriggerActionAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_action_attempt_id: RequiredIdentifierStr
    trigger_dispatch_id: RequiredIdentifierStr
    phase: TriggerActionPhase
    action_type: GitHubActionType
    status: TriggerActionStatus
    action_spec: GitHubActionSpec
    request_payload: dict[str, JsonValue] = Field(default_factory=dict)
    response_payload: dict[str, JsonValue] = Field(default_factory=dict)
    provider_resource_id: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


__all__ = [
    "GitHubActionSpec",
    "GitHubActionType",
    "GitHubRepoSubscriptionCreateInput",
    "GitHubRepoSubscriptionRecord",
    "GitHubRepoSubscriptionUpdateInput",
    "GitHubRepoWebhookRegistrationInput",
    "GitHubTriggerAccountCreateInput",
    "GitHubTriggerAccountRecord",
    "GitHubTriggerAccountStatus",
    "GitHubTriggerAccountUpdateInput",
    "GitHubTriggerRunTemplate",
    "GitHubWebhookStatus",
    "TriggerActionAttemptRecord",
    "TriggerActionPhase",
    "TriggerActionStatus",
    "TriggerDeliveryIngestStatus",
    "TriggerDeliveryRecord",
    "TriggerDeliverySignatureStatus",
    "TriggerDispatchConfig",
    "TriggerDispatchRecord",
    "TriggerDispatchStatus",
    "TriggerEvaluationRecord",
    "TriggerProvider",
    "TriggerRuleCreateInput",
    "TriggerRuleMatchConfig",
    "TriggerRuleRecord",
    "TriggerRuleUpdateInput",
    "TriggerTargetType",
]
