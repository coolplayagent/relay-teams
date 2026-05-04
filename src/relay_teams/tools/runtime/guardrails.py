# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
import asyncio
from enum import Enum

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.enums import TaskSpecStrictness

GUARDRAIL_STATE_KEY = "runtime_guardrails"
GUARDRAIL_REPORT_KEY = "runtime_guardrail_report"
MAX_RECORDED_GUARDRAIL_OBSERVATIONS = 200
DEFAULT_GUARDRAIL_PAYLOAD_LIMIT_BYTES = 1024 * 1024

_per_task_locks: dict[str, asyncio.Lock] = {}


def _get_task_lock(task_id: str) -> asyncio.Lock:
    lock = _per_task_locks.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _per_task_locks[task_id] = lock
    return lock


class RuntimeGuardrailLayer(str, Enum):
    PRE_EXECUTION = "pre_execution"
    IN_EXECUTION = "in_execution"
    POST_VALIDATION = "post_validation"


class RuntimeGuardrailAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    DENY = "deny"


class RuntimeGuardrailRuleType(str, Enum):
    TOOL_ALLOWLIST = "tool_allowlist"
    TOOL_DENYLIST = "tool_denylist"
    INPUT_SIZE = "input_size"
    OUTPUT_SIZE = "output_size"
    CALL_FREQUENCY = "call_frequency"
    SHELL_DESTRUCTIVE_PATTERN = "shell_destructive_pattern"


class RuntimeGuardrailStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


class RuntimeGuardrailRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1)
    layer: RuntimeGuardrailLayer
    rule_type: RuntimeGuardrailRuleType
    action: RuntimeGuardrailAction = RuntimeGuardrailAction.WARN
    description: str = ""
    enabled: bool = True
    tool_names: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()
    session_modes: tuple[str, ...] = ()
    run_kinds: tuple[str, ...] = ()
    max_bytes: int | None = Field(default=None, ge=1)
    max_calls_per_task: int | None = Field(default=None, ge=1)
    blocked_patterns: tuple[str, ...] = ()


def default_runtime_guardrail_rules() -> tuple[RuntimeGuardrailRule, ...]:
    return (
        RuntimeGuardrailRule(
            rule_id="role_tool_allowlist",
            layer=RuntimeGuardrailLayer.PRE_EXECUTION,
            rule_type=RuntimeGuardrailRuleType.TOOL_ALLOWLIST,
            action=RuntimeGuardrailAction.DENY,
            description="Deny tool calls outside the effective role tool boundary.",
        ),
        RuntimeGuardrailRule(
            rule_id="runtime_denied_tools",
            layer=RuntimeGuardrailLayer.PRE_EXECUTION,
            rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
            action=RuntimeGuardrailAction.DENY,
            description="Deny tools explicitly disabled by runtime policy.",
        ),
        RuntimeGuardrailRule(
            rule_id="destructive_shell_pattern",
            layer=RuntimeGuardrailLayer.PRE_EXECUTION,
            rule_type=RuntimeGuardrailRuleType.SHELL_DESTRUCTIVE_PATTERN,
            action=RuntimeGuardrailAction.DENY,
            tool_names=("shell",),
            blocked_patterns=(
                r"(^|[;&|]\s*)rm\s+(-[A-Za-z]*[rf][A-Za-z]*|--recursive|--force)\b",
                r"(^|[;&|]\s*)git\s+reset\s+--hard\b",
                r"(^|[;&|]\s*)git\s+clean\s+-[A-Za-z]*[xdf][A-Za-z]*\b",
                r"(^|[;&|]\s*)del\s+(/s|/q)\b",
                r"\bRemove-Item\b.*\s-Recurse\b",
            ),
            description="Block common destructive filesystem shell patterns.",
        ),
        RuntimeGuardrailRule(
            rule_id="large_tool_input",
            layer=RuntimeGuardrailLayer.PRE_EXECUTION,
            rule_type=RuntimeGuardrailRuleType.INPUT_SIZE,
            action=RuntimeGuardrailAction.WARN,
            max_bytes=DEFAULT_GUARDRAIL_PAYLOAD_LIMIT_BYTES,
            description="Flag unusually large tool inputs before execution.",
        ),
        RuntimeGuardrailRule(
            rule_id="write_surface_frequency",
            layer=RuntimeGuardrailLayer.PRE_EXECUTION,
            rule_type=RuntimeGuardrailRuleType.CALL_FREQUENCY,
            action=RuntimeGuardrailAction.WARN,
            tool_names=("edit", "shell", "write", "write_tmp"),
            max_calls_per_task=20,
            description="Flag unusually frequent write-capable tool calls.",
        ),
        RuntimeGuardrailRule(
            rule_id="large_tool_output",
            layer=RuntimeGuardrailLayer.IN_EXECUTION,
            rule_type=RuntimeGuardrailRuleType.OUTPUT_SIZE,
            action=RuntimeGuardrailAction.WARN,
            max_bytes=DEFAULT_GUARDRAIL_PAYLOAD_LIMIT_BYTES,
            description="Flag unusually large tool outputs after execution.",
        ),
    )


class RuntimeGuardrailPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    rules: tuple[RuntimeGuardrailRule, ...] = Field(
        default_factory=default_runtime_guardrail_rules
    )

    def matching_rules(
        self,
        *,
        layer: RuntimeGuardrailLayer,
        context: RuntimeGuardrailContext,
    ) -> tuple[RuntimeGuardrailRule, ...]:
        if not self.enabled:
            return ()
        return tuple(
            rule
            for rule in self.rules
            if rule.enabled
            and rule.layer == layer
            and _rule_matches_context(rule=rule, context=context)
        )


class RuntimeGuardrailContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    session_mode: str = ""
    run_kind: str = ""


class RuntimeGuardrailFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer: RuntimeGuardrailLayer
    rule_id: str = Field(min_length=1)
    rule_type: RuntimeGuardrailRuleType
    action: RuntimeGuardrailAction
    message: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class RuntimeGuardrailEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: tuple[RuntimeGuardrailFinding, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(
            finding.action == RuntimeGuardrailAction.DENY for finding in self.findings
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.action == RuntimeGuardrailAction.WARN
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.action == RuntimeGuardrailAction.DENY
        )


class RuntimeGuardrailObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    layer: RuntimeGuardrailLayer
    rule_id: str = Field(min_length=1)
    rule_type: RuntimeGuardrailRuleType
    action: RuntimeGuardrailAction
    message: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RuntimeGuardrailState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    run_id: str = ""
    session_id: str = ""
    role_id: str = ""
    tool_call_counts: dict[str, int] = Field(default_factory=dict)
    warning_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    observations: tuple[RuntimeGuardrailObservation, ...] = ()
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RuntimeGuardrailReportCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer: RuntimeGuardrailLayer
    name: str = Field(min_length=1)
    passed: bool
    details: str = ""


class RuntimeGuardrailReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    run_id: str = ""
    session_id: str = ""
    role_id: str = ""
    status: RuntimeGuardrailStatus = RuntimeGuardrailStatus.PASSED
    required_for_gate: bool = True
    total_tool_calls: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    tool_call_counts: dict[str, int] = Field(default_factory=dict)
    observations: tuple[RuntimeGuardrailObservation, ...] = ()
    checks: tuple[RuntimeGuardrailReportCheck, ...] = ()
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


def evaluate_pre_execution_guardrails(
    *,
    policy: RuntimeGuardrailPolicy,
    context: RuntimeGuardrailContext,
    tool_input: dict[str, JsonValue],
    allowed_tools: tuple[str, ...] | None,
    denied_tools: tuple[str, ...],
    call_count: int,
) -> RuntimeGuardrailEvaluation:
    findings: list[RuntimeGuardrailFinding] = []
    allowed_tool_names = set(allowed_tools) if allowed_tools is not None else None
    denied_tool_names = set(denied_tools)
    for rule in policy.matching_rules(
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        context=context,
    ):
        finding = _evaluate_pre_execution_rule(
            rule=rule,
            context=context,
            tool_input=tool_input,
            allowed_tools=allowed_tool_names,
            denied_tools=denied_tool_names,
            call_count=call_count,
        )
        if finding is not None:
            findings.append(finding)
    return RuntimeGuardrailEvaluation(findings=tuple(findings))


def evaluate_in_execution_guardrails(
    *,
    policy: RuntimeGuardrailPolicy,
    context: RuntimeGuardrailContext,
    tool_input: dict[str, JsonValue],
    result_envelope: dict[str, JsonValue],
) -> RuntimeGuardrailEvaluation:
    findings: list[RuntimeGuardrailFinding] = []
    _ = tool_input
    for rule in policy.matching_rules(
        layer=RuntimeGuardrailLayer.IN_EXECUTION,
        context=context,
    ):
        finding = _evaluate_in_execution_rule(
            rule=rule,
            context=context,
            result_envelope=result_envelope,
        )
        if finding is not None:
            findings.append(finding)
    return RuntimeGuardrailEvaluation(findings=tuple(findings))


def evaluate_post_validation_guardrails(
    *,
    policy: RuntimeGuardrailPolicy,
    context: RuntimeGuardrailContext,
    tool_input: dict[str, JsonValue],
    result_envelope: dict[str, JsonValue],
    strictness: TaskSpecStrictness = TaskSpecStrictness.MEDIUM,
) -> RuntimeGuardrailEvaluation:
    """Evaluate guardrails after tool execution with strictness escalation.

    Applies all rules across PRE_EXECUTION and IN_EXECUTION layers,
    then escalates WARN->DENY when strictness is HIGH and DENY->WARN
    when strictness is LOW.  This enables a stricter post-hoc
    validation pass for high-stakes tasks.
    """
    _ = tool_input
    findings: list[RuntimeGuardrailFinding] = []
    for layer in (
        RuntimeGuardrailLayer.PRE_EXECUTION,
        RuntimeGuardrailLayer.IN_EXECUTION,
    ):
        for rule in policy.matching_rules(layer=layer, context=context):
            if layer == RuntimeGuardrailLayer.PRE_EXECUTION:
                finding = _evaluate_in_execution_rule(
                    rule=rule,
                    context=context,
                    result_envelope=result_envelope,
                )
            else:
                finding = _evaluate_in_execution_rule(
                    rule=rule,
                    context=context,
                    result_envelope=result_envelope,
                )
            if finding is not None:
                findings.append(finding)
    for idx, finding in enumerate(findings):
        findings[idx] = adjust_finding_for_strictness(finding, strictness)
    return RuntimeGuardrailEvaluation(findings=tuple(findings))


def adjust_finding_for_strictness(
    finding: RuntimeGuardrailFinding,
    strictness: TaskSpecStrictness,
) -> RuntimeGuardrailFinding:
    """Adjust a guardrail finding's action based on task spec strictness.

    HIGH: WARN becomes DENY (stricter enforcement).
    LOW: DENY becomes WARN (more lenient).
    MEDIUM: unchanged.
    """
    if strictness == TaskSpecStrictness.HIGH:
        if finding.action == RuntimeGuardrailAction.WARN:
            return finding.model_copy(update={"action": RuntimeGuardrailAction.DENY})
    elif strictness == TaskSpecStrictness.LOW:
        if finding.action == RuntimeGuardrailAction.DENY:
            return finding.model_copy(update={"action": RuntimeGuardrailAction.WARN})
    return finding


def strictness_augmented_rules(
    base_rules: tuple[RuntimeGuardrailRule, ...],
    strictness: TaskSpecStrictness,
) -> tuple[RuntimeGuardrailRule, ...]:
    """Return rule set adjusted for task spec strictness.

    LOW: base rules unchanged.
    MEDIUM: base rules + reduced CALL_FREQUENCY limits.
    HIGH: base rules + tighter CALL_FREQUENCY, OUTPUT_SIZE, expanded shell patterns.
    """
    if strictness == TaskSpecStrictness.LOW:
        return base_rules

    augmented = list(base_rules)

    if strictness in (TaskSpecStrictness.MEDIUM, TaskSpecStrictness.HIGH):
        augmented.append(
            RuntimeGuardrailRule(
                rule_id="strictness_call_frequency_medium",
                layer=RuntimeGuardrailLayer.PRE_EXECUTION,
                rule_type=RuntimeGuardrailRuleType.CALL_FREQUENCY,
                action=RuntimeGuardrailAction.WARN,
                tool_names=("edit", "shell", "write"),
                max_calls_per_task=15,
                description="Medium-strictness call frequency guardrail.",
            )
        )

    if strictness == TaskSpecStrictness.HIGH:
        augmented.append(
            RuntimeGuardrailRule(
                rule_id="strictness_output_size_high",
                layer=RuntimeGuardrailLayer.IN_EXECUTION,
                rule_type=RuntimeGuardrailRuleType.OUTPUT_SIZE,
                action=RuntimeGuardrailAction.WARN,
                max_bytes=256 * 1024,
                description="High-strictness output size guardrail.",
            )
        )
        augmented.append(
            RuntimeGuardrailRule(
                rule_id="strictness_shell_expanded_high",
                layer=RuntimeGuardrailLayer.PRE_EXECUTION,
                rule_type=RuntimeGuardrailRuleType.SHELL_DESTRUCTIVE_PATTERN,
                action=RuntimeGuardrailAction.DENY,
                tool_names=("shell",),
                blocked_patterns=(
                    r"(^|[;&|]\s*)chmod\b",
                    r"(^|[;&|]\s*)chown\b",
                    r"(^|[;&|]\s*)mkfs\b",
                    r"(^|[;&|]\s*)dd\b.*of=",
                ),
                description="High-strictness expanded destructive shell patterns.",
            )
        )

    return tuple(augmented)


def guardrail_rules_from_contract(
    role_id: str,
    contract_invariants: tuple[Mapping[str, object], ...],
) -> tuple[RuntimeGuardrailRule, ...]:
    """Auto-generate guardrail rules from a role contract's invariants.

    Each invariant with a ``denied_tools`` or ``allowed_tools`` key
    produces a corresponding TOOL_DENYLIST or TOOL_ALLOWLIST rule scoped
    to the given role.
    """
    rules: list[RuntimeGuardrailRule] = []
    for idx, invariant in enumerate(contract_invariants):
        denied = invariant.get("denied_tools")
        if isinstance(denied, (tuple, list)) and denied:
            tool_names = tuple(str(t) for t in denied)
            rules.append(
                RuntimeGuardrailRule(
                    rule_id=f"contract_deny_{role_id}_{idx}",
                    layer=RuntimeGuardrailLayer.PRE_EXECUTION,
                    rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
                    action=RuntimeGuardrailAction.DENY,
                    tool_names=tool_names,
                    role_ids=(role_id,),
                    description=(
                        f"Contract-derived deny rule for role {role_id}: "
                        f"{', '.join(tool_names)}"
                    ),
                )
            )
        allowed = invariant.get("allowed_tools")
        if isinstance(allowed, (tuple, list)) and allowed:
            tool_names = tuple(str(t) for t in allowed)
            rules.append(
                RuntimeGuardrailRule(
                    rule_id=f"contract_allow_{role_id}_{idx}",
                    layer=RuntimeGuardrailLayer.PRE_EXECUTION,
                    rule_type=RuntimeGuardrailRuleType.TOOL_ALLOWLIST,
                    action=RuntimeGuardrailAction.DENY,
                    tool_names=tool_names,
                    role_ids=(role_id,),
                    description=(
                        f"Contract-derived allowlist rule for role {role_id}: "
                        f"{', '.join(tool_names)}"
                    ),
                )
            )
    return tuple(rules)


def merge_contract_rules(
    base_rules: tuple[RuntimeGuardrailRule, ...],
    contract_rules: tuple[RuntimeGuardrailRule, ...],
) -> tuple[RuntimeGuardrailRule, ...]:
    """Merge base rules with contract-derived rules, deduplicating by rule_id."""
    seen: dict[str, RuntimeGuardrailRule] = {}
    for rule in base_rules:
        seen[rule.rule_id] = rule
    for rule in contract_rules:
        seen[rule.rule_id] = rule
    return tuple(seen.values())


def adjust_evaluation_for_strictness(
    evaluation: RuntimeGuardrailEvaluation,
    strictness: TaskSpecStrictness,
) -> RuntimeGuardrailEvaluation:
    """Adjust all findings in an evaluation based on strictness level."""
    adjusted = tuple(
        adjust_finding_for_strictness(f, strictness) for f in evaluation.findings
    )
    return RuntimeGuardrailEvaluation(findings=adjusted)


async def record_runtime_guardrail_tool_call_async(
    *,
    shared_store: SharedStateRepository,
    context: RuntimeGuardrailContext,
) -> int:
    lock = _get_task_lock(context.task_id)
    async with lock:
        saved_json = await shared_store.update_state_async(
            scope=_task_scope(context.task_id),
            key=GUARDRAIL_STATE_KEY,
            update=lambda raw: _record_tool_call_state_json(
                raw=raw,
                context=context,
            ),
        )
    saved_state = RuntimeGuardrailState.model_validate_json(saved_json)
    return saved_state.tool_call_counts.get(context.tool_name, 0)


async def record_runtime_guardrail_findings_async(
    *,
    shared_store: SharedStateRepository,
    context: RuntimeGuardrailContext,
    findings: tuple[RuntimeGuardrailFinding, ...],
) -> RuntimeGuardrailState:
    lock = _get_task_lock(context.task_id)
    async with lock:
        saved_json = await shared_store.update_state_async(
            scope=_task_scope(context.task_id),
            key=GUARDRAIL_STATE_KEY,
            update=lambda raw: _record_findings_state_json(
                raw=raw,
                context=context,
                findings=findings,
            ),
        )
    return RuntimeGuardrailState.model_validate_json(saved_json)


async def load_runtime_guardrail_state_async(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
) -> RuntimeGuardrailState | None:
    raw = await shared_store.get_state_async(_task_scope(task_id), GUARDRAIL_STATE_KEY)
    if raw is None:
        return None
    try:
        return RuntimeGuardrailState.model_validate_json(raw)
    except ValueError:
        return None


def load_runtime_guardrail_report(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
) -> RuntimeGuardrailReport | None:
    raw = shared_store.get_state(_task_scope(task_id), GUARDRAIL_REPORT_KEY)
    if raw is None:
        return None
    try:
        return RuntimeGuardrailReport.model_validate_json(raw)
    except ValueError:
        return None


async def generate_runtime_guardrail_report_async(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    run_id: str,
    session_id: str,
    role_id: str,
) -> RuntimeGuardrailReport:
    state = await load_runtime_guardrail_state_async(
        shared_store=shared_store,
        task_id=task_id,
    )
    report = build_runtime_guardrail_report(
        state=state,
        task_id=task_id,
        run_id=run_id,
        session_id=session_id,
        role_id=role_id,
    )
    await shared_store.manage_state_async(
        StateMutation(
            scope=_task_scope(task_id),
            key=GUARDRAIL_REPORT_KEY,
            value_json=report.model_dump_json(),
        )
    )
    return report


def build_runtime_guardrail_report(
    *,
    state: RuntimeGuardrailState | None,
    task_id: str,
    run_id: str,
    session_id: str,
    role_id: str,
) -> RuntimeGuardrailReport:
    observations = () if state is None else state.observations
    counts = {} if state is None else dict(state.tool_call_counts)
    retained_blocked_count = _count_observations_by_action(
        observations=observations,
        action=RuntimeGuardrailAction.DENY,
    )
    retained_warning_count = _count_observations_by_action(
        observations=observations,
        action=RuntimeGuardrailAction.WARN,
    )
    blocked_count = (
        retained_blocked_count
        if state is None
        else max(state.blocked_count, retained_blocked_count)
    )
    warning_count = (
        retained_warning_count
        if state is None
        else max(state.warning_count, retained_warning_count)
    )
    status = _report_status(blocked_count=blocked_count, warning_count=warning_count)
    return RuntimeGuardrailReport(
        task_id=task_id,
        run_id=run_id or (state.run_id if state is not None else ""),
        session_id=session_id or (state.session_id if state is not None else ""),
        role_id=role_id or (state.role_id if state is not None else ""),
        status=status,
        total_tool_calls=sum(counts.values()),
        warning_count=warning_count,
        blocked_count=blocked_count,
        tool_call_counts=counts,
        observations=observations,
        checks=_report_checks(
            blocked_count=blocked_count,
            warning_count=warning_count,
            observations=observations,
        ),
    )


def runtime_guardrail_report_from_payload(
    payload: dict[str, object],
) -> RuntimeGuardrailReport | None:
    candidate = payload.get("report")
    if isinstance(candidate, dict):
        try:
            return RuntimeGuardrailReport.model_validate(candidate)
        except ValueError:
            return None
    try:
        return RuntimeGuardrailReport.model_validate(payload)
    except ValueError:
        return None


def runtime_guardrail_report_from_event_payload(
    value: object,
) -> RuntimeGuardrailReport | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return runtime_guardrail_report_from_payload(
        {str(key): item for key, item in parsed.items()}
    )


def guardrail_findings_payload(
    findings: tuple[RuntimeGuardrailFinding, ...],
) -> list[JsonValue]:
    return [
        finding.model_dump(mode="json")
        for finding in findings
        if finding.action != RuntimeGuardrailAction.ALLOW
    ]


def guardrail_meta_status(
    findings: tuple[RuntimeGuardrailFinding, ...],
) -> RuntimeGuardrailStatus:
    blocked_count = sum(
        1 for finding in findings if finding.action == RuntimeGuardrailAction.DENY
    )
    warning_count = sum(
        1 for finding in findings if finding.action == RuntimeGuardrailAction.WARN
    )
    return _report_status(blocked_count=blocked_count, warning_count=warning_count)


def _evaluate_pre_execution_rule(
    *,
    rule: RuntimeGuardrailRule,
    context: RuntimeGuardrailContext,
    tool_input: dict[str, JsonValue],
    allowed_tools: set[str] | None,
    denied_tools: set[str],
    call_count: int,
) -> RuntimeGuardrailFinding | None:
    if (
        rule.rule_type == RuntimeGuardrailRuleType.TOOL_ALLOWLIST
        and allowed_tools is not None
        and context.tool_name not in allowed_tools
    ):
        return _finding(
            rule=rule,
            context=context,
            message=f"Tool {context.tool_name} is not authorized for {context.role_id}.",
            details={"allowed_tools": _json_string_list(allowed_tools)},
        )
    if (
        rule.rule_type == RuntimeGuardrailRuleType.TOOL_DENYLIST
        and context.tool_name in denied_tools
    ):
        return _finding(
            rule=rule,
            context=context,
            message=f"Tool {context.tool_name} is denied by runtime policy.",
            details={"denied_tools": _json_string_list(denied_tools)},
        )
    if rule.rule_type == RuntimeGuardrailRuleType.INPUT_SIZE:
        limit = rule.max_bytes or DEFAULT_GUARDRAIL_PAYLOAD_LIMIT_BYTES
        size = _json_size_bytes(tool_input)
        if size > limit:
            return _finding(
                rule=rule,
                context=context,
                message=(
                    f"Tool {context.tool_name} input is {size} bytes, above "
                    f"{limit} byte guardrail limit."
                ),
                details={"input_bytes": size, "max_bytes": limit},
            )
    if rule.rule_type == RuntimeGuardrailRuleType.CALL_FREQUENCY:
        limit = rule.max_calls_per_task
        if limit is not None and call_count > limit:
            return _finding(
                rule=rule,
                context=context,
                message=(
                    f"Tool {context.tool_name} has been called {call_count} times "
                    f"for this task, above guardrail limit {limit}."
                ),
                details={"call_count": call_count, "max_calls_per_task": limit},
            )
    if rule.rule_type == RuntimeGuardrailRuleType.SHELL_DESTRUCTIVE_PATTERN:
        command = _tool_command(tool_input)
        if not command:
            return None
        pattern = _matching_pattern(command=command, patterns=rule.blocked_patterns)
        if pattern:
            return _finding(
                rule=rule,
                context=context,
                message="Shell command matched a destructive runtime guardrail pattern.",
                details={"pattern": pattern, "command": command[:500]},
            )
    return None


def _evaluate_in_execution_rule(
    *,
    rule: RuntimeGuardrailRule,
    context: RuntimeGuardrailContext,
    result_envelope: dict[str, JsonValue],
) -> RuntimeGuardrailFinding | None:
    if rule.rule_type != RuntimeGuardrailRuleType.OUTPUT_SIZE:
        return None
    limit = rule.max_bytes or DEFAULT_GUARDRAIL_PAYLOAD_LIMIT_BYTES
    size = _json_size_bytes(result_envelope)
    if size <= limit:
        return None
    return _finding(
        rule=rule,
        context=context,
        message=(
            f"Tool {context.tool_name} output is {size} bytes, above "
            f"{limit} byte guardrail limit."
        ),
        details={"output_bytes": size, "max_bytes": limit},
    )


def _record_tool_call_state_json(
    *,
    raw: str | None,
    context: RuntimeGuardrailContext,
) -> str:
    state = _runtime_guardrail_state_from_raw(raw=raw, context=context)
    counts = dict(state.tool_call_counts)
    counts[context.tool_name] = counts.get(context.tool_name, 0) + 1
    return state.model_copy(
        update={
            "run_id": context.run_id,
            "session_id": context.session_id,
            "role_id": context.role_id,
            "tool_call_counts": counts,
            "updated_at": datetime.now(tz=timezone.utc),
        }
    ).model_dump_json()


def _record_findings_state_json(
    *,
    raw: str | None,
    context: RuntimeGuardrailContext,
    findings: tuple[RuntimeGuardrailFinding, ...],
) -> str:
    state = _runtime_guardrail_state_from_raw(raw=raw, context=context)
    retained_warning_count = _count_observations_by_action(
        observations=state.observations,
        action=RuntimeGuardrailAction.WARN,
    )
    retained_blocked_count = _count_observations_by_action(
        observations=state.observations,
        action=RuntimeGuardrailAction.DENY,
    )
    warning_count = max(state.warning_count, retained_warning_count) + sum(
        1 for finding in findings if finding.action == RuntimeGuardrailAction.WARN
    )
    blocked_count = max(state.blocked_count, retained_blocked_count) + sum(
        1 for finding in findings if finding.action == RuntimeGuardrailAction.DENY
    )
    observations = list(state.observations)
    observations.extend(
        RuntimeGuardrailObservation(
            run_id=context.run_id,
            session_id=context.session_id,
            task_id=context.task_id,
            instance_id=context.instance_id,
            role_id=context.role_id,
            tool_name=context.tool_name,
            tool_call_id=context.tool_call_id,
            layer=finding.layer,
            rule_id=finding.rule_id,
            rule_type=finding.rule_type,
            action=finding.action,
            message=finding.message,
            details=finding.details,
        )
        for finding in findings
    )
    retained = _retain_observations(
        observations=observations,
        limit=MAX_RECORDED_GUARDRAIL_OBSERVATIONS,
    )
    return state.model_copy(
        update={
            "run_id": context.run_id,
            "session_id": context.session_id,
            "role_id": context.role_id,
            "warning_count": warning_count,
            "blocked_count": blocked_count,
            "observations": retained,
            "updated_at": datetime.now(tz=timezone.utc),
        }
    ).model_dump_json()


def _runtime_guardrail_state_from_raw(
    *,
    raw: str | None,
    context: RuntimeGuardrailContext,
) -> RuntimeGuardrailState:
    if raw is not None:
        try:
            return RuntimeGuardrailState.model_validate_json(raw)
        except ValueError:
            pass
    return RuntimeGuardrailState(
        task_id=context.task_id,
        run_id=context.run_id,
        session_id=context.session_id,
        role_id=context.role_id,
    )


def _report_checks(
    *,
    blocked_count: int,
    warning_count: int,
    observations: tuple[RuntimeGuardrailObservation, ...],
) -> tuple[RuntimeGuardrailReportCheck, ...]:
    pre_blocked = _count_observations(
        observations=observations,
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        action=RuntimeGuardrailAction.DENY,
    )
    in_execution_blocked = _count_observations(
        observations=observations,
        layer=RuntimeGuardrailLayer.IN_EXECUTION,
        action=RuntimeGuardrailAction.DENY,
    )
    return (
        RuntimeGuardrailReportCheck(
            layer=RuntimeGuardrailLayer.PRE_EXECUTION,
            name="pre_execution_boundary",
            passed=pre_blocked == 0,
            details=(
                "No pre-execution guardrail blocked tool calls."
                if pre_blocked == 0
                else f"{pre_blocked} pre-execution guardrail block(s) recorded."
            ),
        ),
        RuntimeGuardrailReportCheck(
            layer=RuntimeGuardrailLayer.IN_EXECUTION,
            name="execution_monitoring",
            passed=in_execution_blocked == 0,
            details=(
                "Execution monitoring did not block tool outputs."
                if in_execution_blocked == 0
                else f"{in_execution_blocked} execution monitoring block(s) recorded."
            ),
        ),
        RuntimeGuardrailReportCheck(
            layer=RuntimeGuardrailLayer.POST_VALIDATION,
            name="guardrail_report_available",
            passed=True,
            details=(
                f"Runtime guardrail report generated with {blocked_count} block(s) "
                f"and {warning_count} warning(s)."
            ),
        ),
    )


def _report_status(
    *,
    blocked_count: int,
    warning_count: int,
) -> RuntimeGuardrailStatus:
    if blocked_count > 0:
        return RuntimeGuardrailStatus.BLOCKED
    if warning_count > 0:
        return RuntimeGuardrailStatus.WARNING
    return RuntimeGuardrailStatus.PASSED


def _count_observations(
    *,
    observations: tuple[RuntimeGuardrailObservation, ...],
    layer: RuntimeGuardrailLayer,
    action: RuntimeGuardrailAction,
) -> int:
    return sum(
        1
        for observation in observations
        if observation.layer == layer and observation.action == action
    )


def _count_observations_by_action(
    *,
    observations: tuple[RuntimeGuardrailObservation, ...],
    action: RuntimeGuardrailAction,
) -> int:
    return sum(1 for observation in observations if observation.action == action)


def _retain_observations(
    *,
    observations: list[RuntimeGuardrailObservation],
    limit: int,
) -> tuple[RuntimeGuardrailObservation, ...]:
    if len(observations) <= limit:
        return tuple(observations)
    deny_obs = [o for o in observations if o.action == RuntimeGuardrailAction.DENY]
    other_obs = [o for o in observations if o.action != RuntimeGuardrailAction.DENY]
    if len(deny_obs) >= limit:
        return tuple(deny_obs[-limit:])
    remaining = limit - len(deny_obs)
    return tuple(deny_obs + other_obs[-remaining:])


def _finding(
    *,
    rule: RuntimeGuardrailRule,
    context: RuntimeGuardrailContext,
    message: str,
    details: dict[str, JsonValue],
) -> RuntimeGuardrailFinding:
    scoped_details = dict(details)
    scoped_details["role_id"] = context.role_id
    scoped_details["task_id"] = context.task_id
    return RuntimeGuardrailFinding(
        layer=rule.layer,
        rule_id=rule.rule_id,
        rule_type=rule.rule_type,
        action=rule.action,
        message=message,
        details=scoped_details,
    )


def _rule_matches_context(
    *,
    rule: RuntimeGuardrailRule,
    context: RuntimeGuardrailContext,
) -> bool:
    if rule.tool_names and context.tool_name not in rule.tool_names:
        return False
    if rule.role_ids and context.role_id not in rule.role_ids:
        return False
    if rule.session_modes and context.session_mode not in rule.session_modes:
        return False
    return not rule.run_kinds or context.run_kind in rule.run_kinds


def _tool_command(tool_input: dict[str, JsonValue]) -> str:
    value = tool_input.get("command")
    return value if isinstance(value, str) else ""


def _matching_pattern(*, command: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        if _pattern_matches(command=command, pattern=pattern):
            return pattern
    return ""


def _pattern_matches(*, command: str, pattern: str) -> bool:
    try:
        return re.search(pattern, command, flags=re.IGNORECASE) is not None
    except re.error:
        return pattern.casefold() in command.casefold()


def _json_size_bytes(value: JsonValue | dict[str, JsonValue]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _json_string_list(items: set[str]) -> list[JsonValue]:
    result: list[JsonValue] = []
    for item in sorted(items):
        result.append(item)
    return result


def _task_scope(task_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.TASK, scope_id=task_id)
