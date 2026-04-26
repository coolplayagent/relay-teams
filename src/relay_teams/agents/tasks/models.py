from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shlex
import sys
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.agents.tasks.enums import (
    TaskSpecStrictness,
    TaskStatus,
    TaskTimeoutAction,
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


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checklist: tuple[str, ...] = Field(default=("non_empty_response",), min_length=1)
    required_files: tuple[Path, ...] = ()
    command_checks: tuple[VerificationCommand, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()

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
    strictness: TaskSpecStrictness = TaskSpecStrictness.LOW

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
        mode="before",
    )
    @classmethod
    def _normalize_spec_text(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="task spec text")


class TaskLifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float | None = Field(default=None, gt=0.0, le=86_400.0)
    heartbeat_interval_seconds: float | None = Field(default=None, gt=0.0, le=3600.0)
    on_timeout: TaskTimeoutAction = TaskTimeoutAction.FAIL


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


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    passed: bool
    checks: tuple[VerificationCheckResult, ...]
    unmet_items: tuple[str, ...] = ()
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    parent_task_id: OptionalIdentifierStr = None
    trace_id: RequiredIdentifierStr
    role_id: OptionalIdentifierStr = "Coordinator"
    title: str | None = None
    objective: str = Field(min_length=1)
    skills: Optional[tuple[str, ...]] = None
    verification: VerificationPlan
    spec: TaskSpec | None = None
    lifecycle: TaskLifecyclePolicy = Field(default_factory=TaskLifecyclePolicy)
    handoff: TaskHandoff | None = None

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> Optional[tuple[str, ...]]:
        return normalize_identifier_tuple(value, field_name="skills")


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
