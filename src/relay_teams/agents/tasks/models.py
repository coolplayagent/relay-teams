from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shlex
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.agents.tasks.enums import (
    FormalVerificationLanguage,
    FormalVerificationToolProfile,
    TaskArtifactPhase,
    TaskSpecStrictness,
    TaskSpecSyncStatus,
    TaskStatus,
    TaskTimeoutAction,
    VerificationEvidenceKind,
    VerificationEvidenceTarget,
    VerificationLayer,
)
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_identifier_tuple,
)


def _normalize_text_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, (list, tuple, set)):
        items = tuple(value)
    else:
        raise TypeError(f"{field_name} must be a string or sequence of strings")
    normalized: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return tuple(normalized)


def _split_command_string(
    value: str, *, platform: str = sys.platform
) -> tuple[str, ...]:
    if platform == "win32":
        return _split_windows_command_string(value)
    return tuple(shlex.split(value))


def _split_windows_command_string(value: str) -> tuple[str, ...]:
    args: list[str] = []
    current: list[str] = []
    in_quotes = False
    arg_started = False
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char in {" ", "\t"} and not in_quotes:
            if arg_started:
                args.append("".join(current))
                current = []
                arg_started = False
            index += 1
            continue
        if char == "\\":
            slash_count = 0
            while index < length and value[index] == "\\":
                slash_count += 1
                index += 1
            if index < length and value[index] == '"':
                current.extend("\\" * (slash_count // 2))
                if slash_count % 2 == 1:
                    current.append('"')
                else:
                    in_quotes = not in_quotes
                arg_started = True
                index += 1
                continue
            current.extend("\\" * slash_count)
            arg_started = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            arg_started = True
            index += 1
            continue
        current.append(char)
        arg_started = True
        index += 1
    if arg_started:
        args.append("".join(current))
    return tuple(args)


class VerificationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: tuple[str, ...] = Field(min_length=1)
    cwd: Path | None = None
    timeout_seconds: float = Field(default=120.0, gt=0.0, le=1200.0)

    @field_validator("command", mode="before")
    @classmethod
    def _normalize_command(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return _split_command_string(value)
        return _normalize_text_tuple(value, field_name="command")


class FormalVerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_language: FormalVerificationLanguage = FormalVerificationLanguage.CUSTOM
    tool_profile: FormalVerificationToolProfile = FormalVerificationToolProfile.CUSTOM
    properties: tuple[str, ...] = ()
    proof_artifacts: tuple[Path, ...] = ()
    counterexample_path: Path | None = None
    replay_command: VerificationCommand | None = None
    required: bool = True

    @field_validator("properties", mode="before")
    @classmethod
    def _normalize_properties(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="formal verification property")


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checklist: tuple[str, ...] = Field(default=("non_empty_response",), min_length=1)
    required_files: tuple[Path, ...] = ()
    command_checks: tuple[VerificationCommand, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()
    strictness: TaskSpecStrictness = TaskSpecStrictness.LOW
    formal_checks: tuple[FormalVerificationPlan, ...] = ()
    repeatability_runs: int = Field(default=1, ge=1, le=5)
    cross_evaluation_models: tuple[str, ...] = ()

    @field_validator("checklist", mode="before")
    @classmethod
    def _normalize_checklist(cls, value: object) -> tuple[str, ...]:
        normalized = _normalize_text_tuple(value, field_name="checklist")
        return normalized or ("non_empty_response",)

    @field_validator(
        "acceptance_criteria",
        "evidence_expectations",
        mode="before",
    )
    @classmethod
    def _normalize_verification_text(
        cls,
        value: object,
    ) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="verification text")


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()
    strictness: TaskSpecStrictness = TaskSpecStrictness.MEDIUM
    entities: tuple[str, ...] = ()
    approach: tuple[str, ...] = ()
    structure: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    norms: tuple[str, ...] = ()
    safeguards: tuple[str, ...] = ()
    prompt_artifact_version: int = Field(default=1, ge=1)
    prompt_code_sync_status: TaskSpecSyncStatus = TaskSpecSyncStatus.UNKNOWN
    formal_verification: FormalVerificationPlan | None = None

    @field_validator("summary", mode="before")
    @classmethod
    def _normalize_summary(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator(
        "requirements",
        "constraints",
        "acceptance_criteria",
        "out_of_scope",
        "verification_commands",
        "evidence_expectations",
        "entities",
        "approach",
        "structure",
        "operations",
        "norms",
        "safeguards",
        mode="before",
    )
    @classmethod
    def _normalize_spec_text(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="task spec text")


class SpecCheckpointPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    refresh_interval_tool_calls: int = Field(default=12, ge=1, le=1000)
    refresh_interval_messages: int = Field(default=48, ge=1, le=5000)
    refresh_interval_history_tokens: int = Field(default=8000, ge=1, le=1_000_000)
    max_summary_chars: int = Field(default=6000, ge=500, le=50_000)
    include_reasons: bool = True
    refresh_on_version_change: bool = False
    auto_evaluate_drift: bool = False
    drift_score_threshold: float = Field(default=3.0, ge=1.0, le=5.0)


class TaskLifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float | None = Field(default=None, gt=0.0, le=86_400.0)
    heartbeat_interval_seconds: float | None = Field(default=None, gt=0.0, le=3600.0)
    on_timeout: TaskTimeoutAction = TaskTimeoutAction.FAIL
    spec_checkpoint: SpecCheckpointPolicy = Field(default_factory=SpecCheckpointPolicy)
    max_retry_attempts: int = Field(default=3, ge=1, le=10)
    stale_silence_multiplier: float = Field(default=3.0, gt=1.0, le=10.0)

    @field_validator("spec_checkpoint", mode="before")
    @classmethod
    def _normalize_spec_checkpoint(
        cls,
        value: object,
    ) -> SpecCheckpointPolicy | object:
        if value is None:
            return SpecCheckpointPolicy()
        return value


class TaskHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: tuple[str, ...] = ()
    incomplete: tuple[str, ...] = ()
    key_files: tuple[Path, ...] = ()
    checks_run: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    reason: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @field_validator(
        "completed",
        "incomplete",
        "checks_run",
        "next_steps",
        mode="before",
    )
    @classmethod
    def _normalize_handoff_text(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="task handoff text")

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


class VerificationCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: VerificationLayer
    name: str = Field(min_length=1)
    passed: bool
    details: str = ""
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    evidence_path: Path | None = None
    output_excerpt: str = ""


class VerificationEvidenceMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: int


class VerificationEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: RequiredIdentifierStr
    kind: VerificationEvidenceKind
    summary: str = Field(min_length=1)
    source: str = ""
    passed: bool | None = None
    path: Path | None = None
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    tool_name: str = ""
    tool_call_id: str = ""
    output_excerpt: str = ""
    metrics: tuple[VerificationEvidenceMetric, ...] = ()
    supports: tuple[str, ...] = ()

    @field_validator("command", "supports", mode="before")
    @classmethod
    def _normalize_text_items(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="verification evidence text")


class VerificationEvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: VerificationEvidenceTarget
    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    satisfied: bool
    reason: str = ""

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _normalize_evidence_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="verification evidence id")


class VerificationEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    spec_artifact_id: OptionalIdentifierStr = None
    spec_source_task_id: OptionalIdentifierStr = None
    items: tuple[VerificationEvidenceItem, ...] = ()
    acceptance_links: tuple[VerificationEvidenceLink, ...] = ()
    expectation_links: tuple[VerificationEvidenceLink, ...] = ()
    formal_verification_required: bool = False
    formal_verification_passed: bool | None = None


class SemanticEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    criterion: str = Field(min_length=1)
    result_excerpt: str = ""
    evidence: tuple[VerificationEvidenceItem, ...] = ()


class SemanticEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1)
    passed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()
    evaluator: str = "rule"

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _normalize_semantic_evidence_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="semantic evidence id")


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    passed: bool
    checks: tuple[VerificationCheckResult, ...]
    unmet_items: tuple[str, ...] = ()
    evidence_bundle: VerificationEvidenceBundle | None = None
    semantic_results: tuple[SemanticEvaluationResult, ...] = ()
    repeatability_results: tuple[VerificationCheckResult, ...] = ()
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class TaskSpecArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: RequiredIdentifierStr
    task_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    trace_id: RequiredIdentifierStr
    source_task_id: OptionalIdentifierStr = None
    spec: TaskSpec
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    parent_task_id: OptionalIdentifierStr = None
    trace_id: RequiredIdentifierStr
    role_id: OptionalIdentifierStr = "Coordinator"
    title: str | None = None
    objective: str = Field(min_length=1)
    skills: (tuple[str, ...]) | None = None
    verification: VerificationPlan
    spec: TaskSpec | None = None
    spec_artifact_id: OptionalIdentifierStr = None
    spec_source_task_id: OptionalIdentifierStr = None
    evidence_bundle: VerificationEvidenceBundle | None = None
    lifecycle: TaskLifecyclePolicy = Field(default_factory=TaskLifecyclePolicy)
    handoff: TaskHandoff | None = None
    orchestration_node_id: OptionalIdentifierStr = None
    depends_on_task_ids: tuple[str, ...] = ()
    retry_attempt: int = Field(default=0, ge=0)
    lease_owner: str = ""
    lease_expires_at: datetime | None = None
    claim_token: str = ""

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> (tuple[str, ...]) | None:
        return normalize_identifier_tuple(value, field_name="skills")

    blocked_by_task_ids: tuple[str, ...] = ()

    @field_validator("depends_on_task_ids", mode="before")
    @classmethod
    def _normalize_depends_on_task_ids(cls, value: object) -> tuple[str, ...]:
        return (
            normalize_identifier_tuple(
                value,
                field_name="depends_on_task_ids",
            )
            or ()
        )

    @field_validator("blocked_by_task_ids", mode="before")
    @classmethod
    def _normalize_blocked_by_task_ids(cls, value: object) -> tuple[str, ...]:
        return (
            normalize_identifier_tuple(
                value,
                field_name="blocked_by_task_ids",
            )
            or ()
        )


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: TaskEnvelope
    status: TaskStatus = TaskStatus.CREATED
    assigned_instance_id: OptionalIdentifierStr = None
    result: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    passed: bool
    details: tuple[str, ...]
    report: VerificationReport | None = None


class SpecArtifactDiffFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1)
    field_label: str = Field(min_length=1)
    change_type: Literal["added", "removed", "modified", "unchanged"]
    old_value: str | None = None
    new_value: str | None = None
    old_items: tuple[str, ...] = ()
    new_items: tuple[str, ...] = ()
    added_items: tuple[str, ...] = ()
    removed_items: tuple[str, ...] = ()


class SpecArtifactDiffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    from_artifact_id: RequiredIdentifierStr
    to_artifact_id: RequiredIdentifierStr
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    field_changes: tuple[SpecArtifactDiffFieldChange, ...] = ()
    has_changes: bool
    summary: str = ""


class SpecArtifactVersionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: RequiredIdentifierStr
    task_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    trace_id: RequiredIdentifierStr
    source_task_id: OptionalIdentifierStr = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class TaskArtifactEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: RequiredIdentifierStr
    phase: TaskArtifactPhase = TaskArtifactPhase.EXECUTION
    timestamp: str = ""
    role_id: str = ""
    instance_id: str = ""
    event_type: str = Field(min_length=1)
    description: str = ""
    payload_json: str = "{}"
    linked_evidence_ids: tuple[str, ...] = ()

    @field_validator("linked_evidence_ids", mode="before")
    @classmethod
    def _normalize_linked_evidence(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="linked evidence ids")


class TaskArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    spec_artifact_id: str = ""
    entries: list[TaskArtifactEntry] = Field(default_factory=list)
    evidence_bundle: VerificationEvidenceBundle | None = None
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""


class TaskArtifactSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    spec_artifact_id: str = ""
    total_entries: int
    phase_counts: dict[str, int] = Field(default_factory=dict)
    evidence_item_count: int = 0
    has_verification_bundle: bool = False
    has_summary: bool = False
    created_at: str = ""
    updated_at: str = ""


class TaskArtifactSnapshot(BaseModel):
    """Normalized read-only artifact snapshot for Gater consumption."""

    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    spec_summary: str = ""
    execution_entries: tuple[TaskArtifactEntry, ...] = ()
    verification_entries: tuple[TaskArtifactEntry, ...] = ()
    delivery_entries: tuple[TaskArtifactEntry, ...] = ()
    evidence_bundle: VerificationEvidenceBundle | None = None
    verification_report_summary: str = ""
    total_entries: int = 0


class SpecCheckpointEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: RequiredIdentifierStr
    task_id: RequiredIdentifierStr
    artifact_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    trace_id: RequiredIdentifierStr
    checkpoint_seq: int = Field(ge=0)
    evaluator: str = Field(default="llm", min_length=1)
    fallback: bool = False
    overall_score: float = Field(ge=0.0, le=5.0)
    scores_json: str = "[]"
    summary: str = ""
    drift_detected: bool = False
    drift_detail: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
