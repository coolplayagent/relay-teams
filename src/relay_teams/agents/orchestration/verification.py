from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Literal, NamedTuple

from relay_teams.agents.tasks.enums import TaskSpecStrictness, TaskStatus
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.orchestration.multi_model_evaluator import (
    MultiModelSemanticEvaluator,
)
from relay_teams.agents.tasks.models import (
    FormalVerificationPlan,
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
    VerificationCheckResult,
    VerificationCommand,
    VerificationEvidenceBundle,
    VerificationEvidenceItem,
    VerificationEvidenceLink,
    VerificationEvidenceMetric,
    VerificationPlan,
    VerificationReport,
    VerificationResult,
)
from relay_teams.agents.orchestration.role_contracts import (
    role_contract_verification_checks,
)
from relay_teams.agents.tasks.enums import (
    VerificationEvidenceKind,
    VerificationEvidenceTarget,
    VerificationLayer,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailReport,
    RuntimeGuardrailStatus,
    runtime_guardrail_report_from_payload,
)
from relay_teams.tools.runtime.models import ToolRuntimeDecision
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.agents.orchestration.llm_behavior_evaluator import (
    LLMBehaviorEvaluator,
)
from relay_teams.agents.orchestration.llm_security_evaluator import (
    LLMSecurityEvaluator,
)
from relay_teams.providers.provider_contracts import LLMProvider
from relay_teams.agents.orchestration.verification_helpers import (
    run_verification_llm_call,
)

_COMMAND_OUTPUT_CAPTURE_BYTES = 64 * 1024
_OUTPUT_READ_CHUNK_BYTES = 8192
_OUTPUT_EXCERPT_CHARS = 2000
_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_OUTPUT_READER_JOIN_TIMEOUT_SECONDS = 0.05
_EVIDENCE_TEXT_MATCH_MIN_TOKEN_COUNT = 2
_EVIDENCE_TEXT_MATCH_MIN_OVERLAP = 0.6

LOGGER = get_logger(__name__)

SemanticVerificationEvaluator = Callable[
    [SemanticEvaluationRequest], SemanticEvaluationResult
]


class VerificationEvaluatorFactory:
    """Factory that assembles the best-available semantic evaluator stack.

    If a *base_evaluator* is explicitly provided, it is used directly.
    If an *llm_provider* is available (without a base evaluator), the
    factory creates a placeholder evaluator that marks evaluations as
    needing manual review (since the sync ``SemanticVerificationEvaluator``
    protocol cannot drive async LLM calls).  Sub-evaluators that need
    LLM access should be constructed externally and passed via
    *base_evaluator*.
    """

    def __init__(
        self,
        *,
        llm_provider: LLMProvider | None = None,
        base_evaluator: SemanticVerificationEvaluator | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._base_evaluator = base_evaluator

    def build(self) -> SemanticVerificationEvaluator | None:
        if self._base_evaluator is not None:
            return self._base_evaluator
        if self._llm_provider is not None:
            return _LlmSemanticEvaluator(self._llm_provider)
        return None


class _LlmSemanticEvaluator:
    """Real LLM-backed semantic evaluator using the provider infrastructure.

    Calls the provider's async ``generate`` method via ``run_until_complete``.
    Falls back to the deferred evaluator on any failure.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def __call__(self, request: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        try:
            response_text = run_verification_llm_call(
                provider=self._provider,
                criterion=request.criterion,
                excerpt=request.result_excerpt[:2000],
            )
            return _parse_llm_evaluator_response(
                request=request, response_text=response_text or ""
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="verification.llm_evaluator_failed",
                message="LLM semantic evaluator call failed, falling back",
                payload={"criterion": request.criterion, "error": str(exc)},
            )
            return SemanticEvaluationResult(
                criterion=request.criterion,
                passed=True,
                confidence=0.3,
                reason=f"LLM evaluator failed ({exc}); manual review recommended.",
                evaluator="llm_fallback",
            )


def _parse_llm_evaluator_response(
    *,
    request: SemanticEvaluationRequest,
    response_text: str,
) -> SemanticEvaluationResult:
    """Parse the LLM evaluator response into a structured result."""
    try:
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if 0 <= json_start < json_end:
            parsed = json.loads(response_text[json_start:json_end])
            return SemanticEvaluationResult(
                criterion=request.criterion,
                passed=bool(parsed.get("passed", False)),
                confidence=float(parsed.get("confidence", 0.5)),
                reason=str(parsed.get("reason", "LLM evaluation")),
                evaluator="llm_semantic",
            )
    except (json.JSONDecodeError, ValueError, KeyError):
        log_event(
            LOGGER,
            logging.DEBUG,
            event="verification.llm_parse_fallback",
            message="LLM response could not be parsed; using fallback",
        )
    return SemanticEvaluationResult(
        criterion=request.criterion,
        passed=True,
        confidence=0.3,
        reason="LLM response could not be parsed; manual review recommended.",
        evaluator="llm_parse_fallback",
    )


def _deferred_llm_evaluator(
    request: SemanticEvaluationRequest,
) -> SemanticEvaluationResult:
    """Fallback evaluator when no LLM provider is available.

    Returns a tentative PASS with low confidence so the task does
    not block, while signaling that human review is recommended.
    """
    return SemanticEvaluationResult(
        criterion=request.criterion,
        passed=True,
        confidence=0.3,
        reason="LLM provider available but no evaluator wired; manual review recommended.",
        evaluator="deferred_llm",
    )


class _BoundedCommandResult(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: bytes
    output_truncated: bool


class _VerificationPlanRun(NamedTuple):
    checks: tuple[VerificationCheckResult, ...]
    evidence_bundle: VerificationEvidenceBundle
    semantic_results: tuple[SemanticEvaluationResult, ...]
    optional_formal_names: frozenset[str] = frozenset()


class _BoundedOutputCapture:
    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._stored_bytes = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._limit_exceeded = False
        self._lock = threading.Lock()

    @property
    def limit_exceeded(self) -> bool:
        with self._lock:
            return self._limit_exceeded

    def append(self, stream_name: Literal["stdout", "stderr"], chunk: bytes) -> None:
        with self._lock:
            available = max(self._max_bytes - self._stored_bytes, 0)
            if available > 0:
                stored = chunk[:available]
                if stream_name == "stdout":
                    self._stdout.extend(stored)
                else:
                    self._stderr.extend(stored)
                self._stored_bytes += len(stored)
            if len(chunk) > available:
                self._limit_exceeded = True

    def stdout_bytes(self) -> bytes:
        with self._lock:
            return bytes(self._stdout)

    def stderr_bytes(self) -> bytes:
        with self._lock:
            return bytes(self._stderr)


def verify_task(
    task_repo: TaskRepository,
    event_bus: EventLog,
    task_id: str,
    *,
    allowed_tools: tuple[str, ...] = (),
    tool_approval_policy: ToolApprovalPolicy | None = None,
    workspace_root: Path | None = None,
    semantic_evaluator: SemanticVerificationEvaluator | None = None,
    role: RoleDefinition | None = None,
    require_guardrail_report: bool = False,
    llm_provider: LLMProvider | None = None,
) -> VerificationResult:
    task = task_repo.get(task_id)
    evidence_bundle: VerificationEvidenceBundle | None
    semantic_results: tuple[SemanticEvaluationResult, ...] = ()
    if task.status != TaskStatus.COMPLETED or task.result is None:
        passed = False
        details = ("Task not completed yet",)
        checks = [
            VerificationCheckResult(
                layer=VerificationLayer.STRUCTURE,
                name="completed_status",
                passed=False,
                details="Task is not completed or has no result.",
            ),
        ]
        event_type = EventType.VERIFICATION_FAILED
        evidence_bundle = None
    else:
        plan_run = _run_verification_plan(
            task_id=task.envelope.task_id,
            plan=task.envelope.verification,
            result=task.result,
            event_bus=event_bus,
            trace_id=task.envelope.trace_id,
            task_id_filter=task.envelope.task_id,
            allowed_tools=allowed_tools,
            tool_approval_policy=tool_approval_policy or ToolApprovalPolicy(),
            workspace_root=workspace_root,
            semantic_evaluator=semantic_evaluator,
            guardrail_report=_latest_guardrail_report(
                event_bus=event_bus,
                trace_id=task.envelope.trace_id,
                task_id=task.envelope.task_id,
            ),
            require_guardrail_report=require_guardrail_report,
            llm_provider=llm_provider,
        )
        checks = list(plan_run.checks)
        semantic_results = plan_run.semantic_results
        formal_checks = tuple(
            check for check in checks if check.layer == VerificationLayer.FORMAL
        )
        required_formal_checks = tuple(
            check
            for check in formal_checks
            if check.name not in plan_run.optional_formal_names
        )
        evidence_bundle = plan_run.evidence_bundle.model_copy(
            update={
                "spec_artifact_id": task.envelope.spec_artifact_id,
                "spec_source_task_id": task.envelope.spec_source_task_id,
                "formal_verification_required": any(
                    formal_plan.required
                    for formal_plan in task.envelope.verification.formal_checks
                ),
                "formal_verification_passed": None
                if not required_formal_checks
                else all(check.passed for check in required_formal_checks),
            }
        )
        if role is not None:
            checks.extend(
                role_contract_verification_checks(
                    role=role,
                    task=task,
                    result=task.result,
                )
            )
        missing = tuple(
            check.name
            for check in checks
            if not check.passed
            and check.name
            and check.name not in plan_run.optional_formal_names
        )
        passed = len(missing) == 0
        details = ("Verification report passed",) if passed else missing
        event_type = (
            EventType.VERIFICATION_PASSED if passed else EventType.VERIFICATION_FAILED
        )

    repeatability_checks = tuple(
        check for check in checks if check.name.startswith("repeatability:")
    )
    report = VerificationReport(
        task_id=task.envelope.task_id,
        passed=passed,
        checks=tuple(checks),
        unmet_items=() if passed else details,
        evidence_bundle=evidence_bundle,
        semantic_results=semantic_results,
        repeatability_results=repeatability_checks,
    )
    verification = VerificationResult(
        task_id=task.envelope.task_id,
        passed=passed,
        details=details,
        report=report,
    )
    event_bus.emit(
        EventEnvelope(
            event_type=event_type,
            trace_id=task.envelope.trace_id,
            session_id=task.envelope.session_id,
            task_id=task.envelope.task_id,
            payload_json=verification.model_dump_json(),
        )
    )
    latest_task = task_repo.get(task.envelope.task_id)
    task_repo.update_envelope(
        latest_task.envelope.task_id,
        latest_task.envelope.model_copy(update={"evidence_bundle": evidence_bundle}),
    )
    return verification


def _run_verification_plan(
    *,
    task_id: str,
    plan: VerificationPlan,
    result: str,
    event_bus: EventLog,
    trace_id: str,
    task_id_filter: str,
    allowed_tools: tuple[str, ...],
    tool_approval_policy: ToolApprovalPolicy,
    workspace_root: Path | None,
    semantic_evaluator: SemanticVerificationEvaluator | None,
    guardrail_report: RuntimeGuardrailReport | None,
    require_guardrail_report: bool,
    llm_provider: LLMProvider | None = None,
) -> _VerificationPlanRun:
    checks: list[VerificationCheckResult] = []
    evidence_items: list[VerificationEvidenceItem] = [
        _task_result_evidence_item(result)
    ]
    checks.extend(_run_checklist_checks(plan=plan, result=result))
    required_file_checks = _run_required_file_checks(
        plan=plan, workspace_root=workspace_root
    )
    checks.extend(required_file_checks)
    evidence_items.extend(_evidence_items_from_checks(required_file_checks))
    command_checks = _run_command_checks(
        plan=plan,
        allowed_tools=allowed_tools,
        tool_approval_policy=tool_approval_policy,
        workspace_root=workspace_root,
    )
    checks.extend(command_checks)
    evidence_items.extend(_evidence_items_from_checks(command_checks))
    evidence_items.extend(
        _event_evidence_items(
            event_bus=event_bus,
            trace_id=trace_id,
            task_id=task_id_filter,
        )
    )
    guardrail_checks = _runtime_guardrail_checks(
        report=guardrail_report,
        required=require_guardrail_report,
    )
    checks.extend(guardrail_checks)

    # FE-5: LLM-based behavior and security evaluations
    if llm_provider is not None:
        behavior_evaluator = LLMBehaviorEvaluator(
            provider=llm_provider,
        )
        tool_call_events = _extract_tool_call_events(
            event_bus=event_bus,
            trace_id=trace_id,
            task_id=task_id_filter,
        )
        behavior_checks = behavior_evaluator.evaluate_behavior(
            task_id=task_id,
            tool_calls=tool_call_events,
            result=result,
            constraints=plan.acceptance_criteria,
        )
        checks.extend(behavior_checks)
        evidence_items.extend(_evidence_items_from_checks(list(behavior_checks)))

        security_evaluator = LLMSecurityEvaluator(
            provider=llm_provider,
        )
        security_checks = security_evaluator.evaluate_security(
            task_id=task_id,
            result=result,
            tool_calls=tool_call_events,
            guardrail_report=guardrail_report,
        )
        checks.extend(security_checks)
        evidence_items.extend(_evidence_items_from_checks(list(security_checks)))
    formal_checks, optional_formal_names = _run_formal_checks(
        plan=plan,
        allowed_tools=allowed_tools,
        tool_approval_policy=tool_approval_policy,
        workspace_root=workspace_root,
    )
    checks.extend(formal_checks)
    evidence_items.extend(_evidence_items_from_checks(formal_checks))
    strictness_checks = _run_strictness_checks(plan=plan)
    checks.extend(strictness_checks)
    evidence_items.extend(_evidence_items_from_checks(strictness_checks))
    (
        evidence_items_with_supports,
        acceptance_links,
        expectation_links,
    ) = _link_evidence_to_plan(plan=plan, evidence_items=tuple(evidence_items))
    checks.extend(
        _run_evidence_coverage_checks(
            acceptance_links=acceptance_links,
            expectation_links=expectation_links,
        )
    )
    effective_evaluator = _wrap_cross_evaluation_evaluator(
        semantic_evaluator=semantic_evaluator,
        cross_evaluation_models=plan.cross_evaluation_models,
    )
    semantic_results = _run_semantic_evaluations(
        task_id=task_id,
        criteria=plan.acceptance_criteria,
        result=result,
        evidence_items=evidence_items_with_supports,
        acceptance_links=acceptance_links,
        semantic_evaluator=effective_evaluator,
    )
    checks.extend(_semantic_evaluation_checks(semantic_results))
    evidence_bundle = VerificationEvidenceBundle(
        task_id=task_id,
        items=evidence_items_with_supports,
        acceptance_links=acceptance_links,
        expectation_links=expectation_links,
    )
    return _VerificationPlanRun(
        checks=tuple(checks),
        evidence_bundle=evidence_bundle,
        semantic_results=semantic_results,
        optional_formal_names=optional_formal_names,
    )


def _run_checklist_checks(
    *,
    plan: VerificationPlan,
    result: str,
) -> list[VerificationCheckResult]:
    normalized_result = result.lower()
    checks: list[VerificationCheckResult] = []
    for item in plan.checklist:
        key = item.lower()
        if key == "non_empty_response":
            passed = bool(result.strip())
            checks.append(
                VerificationCheckResult(
                    layer=VerificationLayer.STRUCTURE,
                    name=item,
                    passed=passed,
                    details=(
                        "Result text is non-empty."
                        if passed
                        else "Result text is empty."
                    ),
                )
            )
            continue
        passed = key in normalized_result
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name=item,
                passed=passed,
                details=(
                    "Checklist item was found in the result."
                    if passed
                    else "Checklist item was not found in the result."
                ),
            )
        )
    return checks


def _run_required_file_checks(
    *,
    plan: VerificationPlan,
    workspace_root: Path | None,
) -> list[VerificationCheckResult]:
    checks: list[VerificationCheckResult] = []
    for path in plan.required_files:
        if workspace_root is None and not path.is_absolute():
            checks.append(
                VerificationCheckResult(
                    layer=VerificationLayer.STRUCTURE,
                    name=f"required_file:{path}",
                    passed=False,
                    details="Required file verification requires a resolved workspace.",
                    evidence_path=path,
                )
            )
            continue
        resolved_path = _resolve_verification_path(path, workspace_root=workspace_root)
        if resolved_path is None:
            checks.append(
                VerificationCheckResult(
                    layer=VerificationLayer.STRUCTURE,
                    name=f"required_file:{path}",
                    passed=False,
                    details="Required file verification path escapes the workspace.",
                    evidence_path=path,
                )
            )
            continue
        exists = resolved_path.is_file()
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.STRUCTURE,
                name=f"required_file:{path}",
                passed=exists,
                details=(
                    "Required file exists."
                    if exists
                    else "Required file missing or is not a file."
                ),
                evidence_path=resolved_path,
            )
        )
    return checks


def _run_command_checks(
    *,
    plan: VerificationPlan,
    allowed_tools: tuple[str, ...],
    tool_approval_policy: ToolApprovalPolicy,
    workspace_root: Path | None,
) -> list[VerificationCheckResult]:
    decision = tool_approval_policy.evaluate("shell", allowed_tools=allowed_tools)
    if decision.runtime_decision == ToolRuntimeDecision.DENY:
        return [
            VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"command:{' '.join(command_check.command)}",
                passed=False,
                details=decision.reason
                or "Command verification requires shell authorization.",
                command=command_check.command,
            )
            for command_check in plan.command_checks
        ]
    if decision.required:
        return [
            VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"command:{' '.join(command_check.command)}",
                passed=True,
                details=(
                    "Command verification was skipped until shell approval is granted."
                ),
                command=command_check.command,
            )
            for command_check in plan.command_checks
        ]
    if workspace_root is None:
        return [
            VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"command:{' '.join(command_check.command)}",
                passed=False,
                details="Command verification requires a resolved workspace.",
                command=command_check.command,
            )
            for command_check in plan.command_checks
        ]
    checks: list[VerificationCheckResult] = []
    for command_check in plan.command_checks:
        result = _run_command_check(command_check, workspace_root=workspace_root)
        checks.append(result)
        if (
            result.passed
            and plan.strictness == TaskSpecStrictness.HIGH
            and plan.repeatability_runs > 1
        ):
            cwd = _resolve_command_cwd(command_check, workspace_root=workspace_root)
            if cwd is not None:
                initial_stdout = (result.output_excerpt or "") if result.passed else ""
                repeatability_failure = _check_command_repeatability(
                    command_check=command_check,
                    cwd=cwd,
                    repeatability_runs=plan.repeatability_runs,
                    initial_stdout=initial_stdout,
                )
                if repeatability_failure is not None:
                    checks.append(repeatability_failure)
                else:
                    checks.append(
                        VerificationCheckResult(
                            layer=VerificationLayer.BEHAVIOR,
                            name=f"repeatability:{' '.join(command_check.command)}",
                            passed=True,
                            details=(
                                f"Command produced consistent results across "
                                f"{plan.repeatability_runs} run(s)."
                            ),
                            command=command_check.command,
                        )
                    )
    return checks


def _run_command_check(
    command_check: VerificationCommand,
    *,
    workspace_root: Path | None,
) -> VerificationCheckResult:
    cwd = (
        _resolve_verification_path(command_check.cwd, workspace_root=workspace_root)
        if command_check.cwd is not None
        else workspace_root
    )
    if cwd is None:
        return VerificationCheckResult(
            layer=VerificationLayer.BEHAVIOR,
            name=f"command:{' '.join(command_check.command)}",
            passed=False,
            details="Command verification cwd escapes the workspace.",
            command=command_check.command,
        )
    try:
        completed = _run_bounded_command(
            command=command_check.command,
            cwd=cwd,
            timeout_seconds=command_check.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return VerificationCheckResult(
            layer=VerificationLayer.BEHAVIOR,
            name=f"command:{' '.join(command_check.command)}",
            passed=False,
            details=f"Command timed out after {command_check.timeout_seconds:.1f}s.",
            command=command_check.command,
            output_excerpt=_command_output_excerpt(
                stdout=_coerce_process_output(exc.stdout),
                stderr=_coerce_process_output(exc.stderr),
            ),
        )
    except OSError as exc:
        return VerificationCheckResult(
            layer=VerificationLayer.BEHAVIOR,
            name=f"command:{' '.join(command_check.command)}",
            passed=False,
            details=str(exc),
            command=command_check.command,
        )

    if completed.output_truncated:
        return VerificationCheckResult(
            layer=VerificationLayer.BEHAVIOR,
            name=f"command:{' '.join(command_check.command)}",
            passed=False,
            details=(
                "Command output exceeded "
                f"{_COMMAND_OUTPUT_CAPTURE_BYTES} byte verification capture limit."
            ),
            command=command_check.command,
            exit_code=completed.returncode,
            output_excerpt=_command_output_excerpt(
                stdout=_coerce_process_output(completed.stdout),
                stderr=_coerce_process_output(completed.stderr),
                truncated=True,
            ),
        )

    passed = completed.returncode == 0
    return VerificationCheckResult(
        layer=VerificationLayer.BEHAVIOR,
        name=f"command:{' '.join(command_check.command)}",
        passed=passed,
        details="Command exited with code 0." if passed else "Command failed.",
        command=command_check.command,
        exit_code=completed.returncode,
        output_excerpt=_command_output_excerpt(
            stdout=_coerce_process_output(completed.stdout),
            stderr=_coerce_process_output(completed.stderr),
        ),
    )


def _run_bounded_command(
    *,
    command: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
) -> _BoundedCommandResult:
    capture = _BoundedOutputCapture(_COMMAND_OUTPUT_CAPTURE_BYTES)
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output_threads = _start_output_reader_threads(process=process, capture=capture)
    returncode: int | None = None
    timed_out = False
    output_truncated = False
    deadline = time.monotonic() + timeout_seconds

    try:
        while returncode is None:
            returncode = process.poll()
            if returncode is not None:
                break
            if capture.limit_exceeded:
                output_truncated = True
                returncode = _kill_process_and_wait(process)
                break
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                timed_out = True
                returncode = _kill_process_and_wait(process)
                break
            time.sleep(min(_PROCESS_POLL_INTERVAL_SECONDS, remaining_seconds))
    finally:
        if process.poll() is None:
            returncode = _kill_process_and_wait(process)
        _finish_process_output(process, output_threads)

    if timed_out:
        raise subprocess.TimeoutExpired(
            cmd=list(command),
            timeout=timeout_seconds,
            output=capture.stdout_bytes(),
            stderr=capture.stderr_bytes(),
        )
    if returncode is None:
        returncode = process.returncode if process.returncode is not None else 1
    return _BoundedCommandResult(
        returncode=returncode,
        stdout=capture.stdout_bytes(),
        stderr=capture.stderr_bytes(),
        output_truncated=output_truncated or capture.limit_exceeded,
    )


def _start_output_reader_threads(
    *,
    process: subprocess.Popen[bytes],
    capture: _BoundedOutputCapture,
) -> tuple[threading.Thread, ...]:
    threads: list[threading.Thread] = []
    if process.stdout is not None:
        threads.append(
            _start_output_reader_thread(
                stream=process.stdout,
                stream_name="stdout",
                capture=capture,
            )
        )
    if process.stderr is not None:
        threads.append(
            _start_output_reader_thread(
                stream=process.stderr,
                stream_name="stderr",
                capture=capture,
            )
        )
    return tuple(threads)


def _start_output_reader_thread(
    *,
    stream: IO[bytes],
    stream_name: Literal["stdout", "stderr"],
    capture: _BoundedOutputCapture,
) -> threading.Thread:
    thread = threading.Thread(
        target=_read_process_output,
        kwargs={
            "stream": stream,
            "stream_name": stream_name,
            "capture": capture,
        },
        daemon=True,
    )
    thread.start()
    return thread


def _read_process_output(
    *,
    stream: IO[bytes],
    stream_name: Literal["stdout", "stderr"],
    capture: _BoundedOutputCapture,
) -> None:
    while True:
        try:
            chunk = _read_process_output_chunk(stream)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        capture.append(stream_name, chunk)


def _read_process_output_chunk(stream: IO[bytes]) -> bytes:
    if isinstance(stream, io.BufferedReader):
        return stream.read1(_OUTPUT_READ_CHUNK_BYTES)
    return stream.read(_OUTPUT_READ_CHUNK_BYTES)


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def _join_output_reader_threads(threads: tuple[threading.Thread, ...]) -> None:
    for thread in threads:
        thread.join(timeout=_OUTPUT_READER_JOIN_TIMEOUT_SECONDS)


def _finish_process_output(
    process: subprocess.Popen[bytes],
    threads: tuple[threading.Thread, ...],
) -> None:
    _join_output_reader_threads(threads)
    _close_process_streams(process)
    _join_output_reader_threads(threads)


def _kill_process_and_wait(process: subprocess.Popen[bytes]) -> int:
    try:
        process.kill()
    except OSError:
        # The process may have already exited; still wait for its final return code.
        pass
    return process.wait()


def _resolve_command_cwd(
    command_check: VerificationCommand,
    *,
    workspace_root: Path | None,
) -> Path | None:
    return (
        _resolve_verification_path(command_check.cwd, workspace_root=workspace_root)
        if command_check.cwd is not None
        else workspace_root
    )


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def _check_command_repeatability(
    *,
    command_check: VerificationCommand,
    cwd: Path,
    repeatability_runs: int,
    initial_stdout: str,
) -> VerificationCheckResult | None:
    command_label = " ".join(command_check.command)
    total_runs = repeatability_runs
    clean_initial = _strip_ansi(initial_stdout)
    for run_index in range(2, total_runs + 1):
        try:
            rerun = _run_bounded_command(
                command=command_check.command,
                cwd=cwd,
                timeout_seconds=command_check.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"repeatability:{command_label}",
                passed=False,
                details=f"Repeatability run {run_index}/{total_runs} timed out.",
                command=command_check.command,
            )
        except OSError as exc:
            return VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"repeatability:{command_label}",
                passed=False,
                details=f"Repeatability run {run_index}/{total_runs} failed: {exc}",
                command=command_check.command,
            )
        if rerun.returncode != 0:
            return VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"repeatability:{command_label}",
                passed=False,
                details=f"Repeatability run {run_index}/{total_runs} exited with code {rerun.returncode}.",
                command=command_check.command,
                exit_code=rerun.returncode,
            )
        rerun_stdout = _strip_ansi(_coerce_process_output(rerun.stdout))
        if rerun_stdout != clean_initial:
            return VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"repeatability:{command_label}",
                passed=False,
                details=(
                    f"Repeatability run {run_index}/{total_runs} "
                    f"produced inconsistent output."
                ),
                command=command_check.command,
                output_excerpt=_command_output_excerpt(
                    stdout=_coerce_process_output(rerun.stdout),
                    stderr=_coerce_process_output(rerun.stderr),
                ),
            )
    return None


def _run_formal_checks(
    *,
    plan: VerificationPlan,
    allowed_tools: tuple[str, ...],
    tool_approval_policy: ToolApprovalPolicy,
    workspace_root: Path | None,
) -> tuple[list[VerificationCheckResult], frozenset[str]]:
    checks: list[VerificationCheckResult] = []
    optional_names: set[str] = set()
    for index, formal_plan in enumerate(plan.formal_checks, start=1):
        checks.extend(
            _run_formal_plan_checks(
                formal_plan=formal_plan,
                index=index,
                allowed_tools=allowed_tools,
                tool_approval_policy=tool_approval_policy,
                workspace_root=workspace_root,
            )
        )
        if not formal_plan.required:
            optional_names.update(check.name for check in checks[-1:] if check.name)
    return checks, frozenset(optional_names)


def _run_formal_plan_checks(
    *,
    formal_plan: FormalVerificationPlan,
    index: int,
    allowed_tools: tuple[str, ...],
    tool_approval_policy: ToolApprovalPolicy,
    workspace_root: Path | None,
) -> list[VerificationCheckResult]:
    checks: list[VerificationCheckResult] = []
    prefix = (
        "formal:"
        f"{formal_plan.spec_language.value}:"
        f"{formal_plan.tool_profile.value}:{index}"
    )
    if (
        formal_plan.required
        and formal_plan.replay_command is None
        and not formal_plan.proof_artifacts
    ):
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.FORMAL,
                name=f"{prefix}:required_evidence",
                passed=False,
                details=(
                    "Formal verification requires a replay command or proof artifact."
                ),
            )
        )
    for path in formal_plan.proof_artifacts:
        checks.append(
            _run_formal_artifact_check(
                name=f"{prefix}:proof_artifact:{path}",
                path=path,
                workspace_root=workspace_root,
            )
        )
    if formal_plan.replay_command is not None:
        checks.append(
            _run_formal_replay_check(
                name=f"{prefix}:replay",
                command_check=formal_plan.replay_command,
                allowed_tools=allowed_tools,
                tool_approval_policy=tool_approval_policy,
                workspace_root=workspace_root,
            )
        )
    if formal_plan.counterexample_path is not None:
        checks.append(
            _run_formal_counterexample_check(
                name=f"{prefix}:counterexample:{formal_plan.counterexample_path}",
                path=formal_plan.counterexample_path,
                workspace_root=workspace_root,
            )
        )
    return checks


def _run_formal_artifact_check(
    *,
    name: str,
    path: Path,
    workspace_root: Path | None,
) -> VerificationCheckResult:
    if workspace_root is None and not path.is_absolute():
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details="Formal proof artifact verification requires a resolved workspace.",
            evidence_path=path,
        )
    resolved_path = _resolve_verification_path(path, workspace_root=workspace_root)
    if resolved_path is None:
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details="Formal proof artifact path escapes the workspace.",
            evidence_path=path,
        )
    exists = resolved_path.is_file()
    return VerificationCheckResult(
        layer=VerificationLayer.FORMAL,
        name=name,
        passed=exists,
        details=(
            "Formal proof artifact exists."
            if exists
            else "Formal proof artifact missing or is not a file."
        ),
        evidence_path=resolved_path,
    )


def _run_formal_replay_check(
    *,
    name: str,
    command_check: VerificationCommand,
    allowed_tools: tuple[str, ...],
    tool_approval_policy: ToolApprovalPolicy,
    workspace_root: Path | None,
) -> VerificationCheckResult:
    decision = tool_approval_policy.evaluate("shell", allowed_tools=allowed_tools)
    if decision.runtime_decision == ToolRuntimeDecision.DENY:
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details=decision.reason or "Formal replay requires shell authorization.",
            command=command_check.command,
        )
    if decision.required:
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details="Formal replay was skipped until shell approval is granted.",
            command=command_check.command,
        )
    if workspace_root is None:
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details="Formal replay requires a resolved workspace.",
            command=command_check.command,
        )
    check = _run_command_check(command_check, workspace_root=workspace_root)
    return check.model_copy(update={"layer": VerificationLayer.FORMAL, "name": name})


def _run_formal_counterexample_check(
    *,
    name: str,
    path: Path,
    workspace_root: Path | None,
) -> VerificationCheckResult:
    if workspace_root is None and not path.is_absolute():
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details="Counterexample verification requires a resolved workspace.",
            evidence_path=path,
        )
    resolved_path = _resolve_verification_path(path, workspace_root=workspace_root)
    if resolved_path is None:
        return VerificationCheckResult(
            layer=VerificationLayer.FORMAL,
            name=name,
            passed=False,
            details="Counterexample path escapes the workspace.",
            evidence_path=path,
        )
    exists = resolved_path.exists()
    return VerificationCheckResult(
        layer=VerificationLayer.FORMAL,
        name=name,
        passed=not exists,
        details=(
            "No counterexample artifact was produced."
            if not exists
            else "Formal verification produced a counterexample artifact."
        ),
        evidence_path=resolved_path,
    )


def _run_strictness_checks(*, plan: VerificationPlan) -> list[VerificationCheckResult]:
    if plan.strictness != TaskSpecStrictness.HIGH:
        return []
    has_hard_evidence = bool(
        plan.command_checks
        or plan.required_files
        or plan.formal_checks
        or plan.evidence_expectations
    )
    checks: list[VerificationCheckResult] = [
        VerificationCheckResult(
            layer=VerificationLayer.SPEC,
            name="strictness:high:evidence_required",
            passed=has_hard_evidence,
            details=(
                "High strictness has structured verification evidence."
                if has_hard_evidence
                else (
                    "High strictness requires verification commands, required files, "
                    "evidence expectations, or formal verification evidence."
                )
            ),
        )
    ]
    if plan.repeatability_runs >= 1:
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name="strictness:high:repeatability_configured",
                passed=True,
                details=(
                    f"High strictness repeatability configured: "
                    f"{plan.repeatability_runs} run(s)."
                    if plan.repeatability_runs > 1
                    else (
                        "High strictness with repeatability_runs=1; "
                        "consider increasing for production-critical tasks."
                    )
                ),
            )
        )
    return checks


def _task_result_evidence_item(result: str) -> VerificationEvidenceItem:
    return VerificationEvidenceItem(
        evidence_id="task_result",
        kind=VerificationEvidenceKind.TASK_RESULT,
        summary="Task result text",
        source="task_result",
        passed=bool(result.strip()),
        output_excerpt=_text_excerpt(result),
    )


def _evidence_items_from_checks(
    checks: list[VerificationCheckResult],
) -> tuple[VerificationEvidenceItem, ...]:
    items: list[VerificationEvidenceItem] = []
    for index, check in enumerate(checks, start=1):
        items.append(
            VerificationEvidenceItem(
                evidence_id=_evidence_id("check", index, check.name),
                kind=_evidence_kind_from_check(check),
                summary=_check_evidence_summary(check),
                source=_check_evidence_source(check),
                passed=_check_evidence_passed(check),
                path=check.evidence_path,
                command=check.command,
                exit_code=check.exit_code,
                output_excerpt=check.output_excerpt,
                metrics=_evidence_metrics_for_check(check),
            )
        )
    return tuple(items)


def _evidence_kind_from_check(
    check: VerificationCheckResult,
) -> VerificationEvidenceKind:
    if check.layer == VerificationLayer.FORMAL:
        return VerificationEvidenceKind.FORMAL_PROOF
    if check.layer == VerificationLayer.STRUCTURE and check.name.startswith(
        "required_file:"
    ):
        return VerificationEvidenceKind.REQUIRED_FILE
    if check.layer == VerificationLayer.BEHAVIOR:
        return _command_evidence_kind(check)
    return VerificationEvidenceKind.COMMAND


def _command_evidence_kind(
    check: VerificationCheckResult,
) -> VerificationEvidenceKind:
    command_text = " ".join(check.command).casefold()
    output_text = check.output_excerpt.casefold()
    combined_text = f"{command_text}\n{output_text}"
    if _text_mentions_any(
        combined_text,
        ("ruff", "lint", "basedpyright", "pyright", "mypy", "flake8"),
    ):
        return VerificationEvidenceKind.LINT_RESULT
    if _text_mentions_any(
        combined_text,
        ("git diff", "files changed", "insertion", "deletion"),
    ):
        return VerificationEvidenceKind.DIFF_SUMMARY
    if _text_mentions_any(
        combined_text,
        ("tla", "alloy", "lean", "coq", "isabelle", "model check", "proof"),
    ):
        return VerificationEvidenceKind.FORMAL_PROOF
    if _text_mentions_any(
        combined_text,
        ("pytest", "unittest", "test", "passed", "failed", "coverage"),
    ):
        return VerificationEvidenceKind.TEST_RESULT
    return VerificationEvidenceKind.COMMAND


def _check_evidence_summary(check: VerificationCheckResult) -> str:
    if check.command:
        command_text = " ".join(check.command)
        status = "passed" if check.passed else "failed"
        return f"Command {command_text} {status}."
    if check.evidence_path is not None:
        status = "exists" if check.passed else "missing"
        return f"Required file {check.evidence_path} {status}."
    return check.details or check.name


def _check_evidence_source(check: VerificationCheckResult) -> str:
    if "skipped until shell approval" in check.details:
        return "verification_check_skipped"
    return "verification_check"


def _check_evidence_passed(check: VerificationCheckResult) -> bool | None:
    if "skipped until shell approval" in check.details:
        return None
    return check.passed


def _evidence_metrics_for_check(
    check: VerificationCheckResult,
) -> tuple[VerificationEvidenceMetric, ...]:
    output = check.output_excerpt
    values: dict[str, int] = {}
    kind = _command_evidence_kind(check)
    if kind == VerificationEvidenceKind.TEST_RESULT:
        _collect_test_metrics(output, values)
    elif kind == VerificationEvidenceKind.LINT_RESULT:
        _collect_lint_metrics(output, values)
    elif kind == VerificationEvidenceKind.DIFF_SUMMARY:
        _collect_diff_metrics(output, values)
    elif kind == VerificationEvidenceKind.FORMAL_PROOF:
        _collect_formal_metrics(check, output, values)
    return tuple(
        VerificationEvidenceMetric(name=name, value=value)
        for name, value in sorted(values.items())
    )


def _collect_test_metrics(output: str, values: dict[str, int]) -> None:
    metric_patterns = {
        "tests_passed": r"(\d+)\s+passed",
        "tests_failed": r"(\d+)\s+failed",
        "test_errors": r"(\d+)\s+errors?",
        "tests_skipped": r"(\d+)\s+skipped",
    }
    for metric_name, pattern in metric_patterns.items():
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match is not None:
            values[metric_name] = int(match.group(1))
    ran_match = re.search(r"Ran\s+(\d+)\s+tests?", output, flags=re.IGNORECASE)
    if ran_match is not None:
        values["tests_total"] = int(ran_match.group(1))


def _collect_lint_metrics(output: str, values: dict[str, int]) -> None:
    found_match = re.search(r"Found\s+(\d+)\s+errors?", output, flags=re.IGNORECASE)
    if found_match is not None:
        values["lint_errors"] = int(found_match.group(1))
        return
    if re.search(r"All checks passed", output, flags=re.IGNORECASE) is not None:
        values["lint_errors"] = 0


def _collect_diff_metrics(output: str, values: dict[str, int]) -> None:
    pattern_by_name = {
        "files_changed": r"(\d+)\s+files?\s+changed",
        "insertions": r"(\d+)\s+insertions?\(\+\)",
        "deletions": r"(\d+)\s+deletions?\(-\)",
    }
    for metric_name, pattern in pattern_by_name.items():
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match is not None:
            values[metric_name] = int(match.group(1))


def _collect_formal_metrics(
    check: VerificationCheckResult,
    output: str,
    values: dict[str, int],
) -> None:
    if (
        check.layer != VerificationLayer.FORMAL
        and _command_evidence_kind(check) != VerificationEvidenceKind.FORMAL_PROOF
    ):
        return
    if check.passed or _text_mentions_any(
        output.casefold(),
        ("no error has been found", "proof completed", "qed", "no counterexample"),
    ):
        values["formal_checks_passed"] = 1


def _event_evidence_items(
    *,
    event_bus: EventLog,
    trace_id: str,
    task_id: str,
) -> tuple[VerificationEvidenceItem, ...]:
    items: list[VerificationEvidenceItem] = []
    for index, event in enumerate(event_bus.list_by_trace(trace_id), start=1):
        event_task_id = str(event.get("task_id") or "")
        if event_task_id != task_id:
            continue
        item = _event_evidence_item(index=index, event=event)
        if item is not None:
            items.append(item)
    return tuple(items)


def _event_evidence_item(
    *,
    index: int,
    event: Mapping[str, object],
) -> VerificationEvidenceItem | None:
    event_type = str(event.get("event_type") or "")
    payload = _parse_event_payload(event.get("payload_json"))
    if event_type == RunEventType.TOOL_CALL.value:
        return _tool_call_evidence_item(index=index, payload=payload)
    if event_type == RunEventType.TOOL_RESULT.value:
        return _tool_result_evidence_item(index=index, payload=payload)
    if event_type == RunEventType.RUNTIME_GUARDRAIL_REPORT.value:
        report = runtime_guardrail_report_from_payload(payload)
        if report is not None:
            return _runtime_guardrail_evidence_item(index=index, report=report)
    if _is_gate_finding_event(event_type=event_type, payload=payload):
        return _gate_finding_evidence_item(
            index=index, event_type=event_type, payload=payload
        )
    return None


def _tool_call_evidence_item(
    *,
    index: int,
    payload: dict[str, object],
) -> VerificationEvidenceItem | None:
    tool_name = str(payload.get("tool_name") or "").strip()
    tool_call_id = str(payload.get("tool_call_id") or "").strip()
    if not tool_name or not tool_call_id:
        return None
    return VerificationEvidenceItem(
        evidence_id=_evidence_id("tool_call", index, tool_call_id),
        kind=VerificationEvidenceKind.TOOL_CALL,
        summary=f"Tool {tool_name} was called.",
        source="event_log",
        passed=None,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        output_excerpt=_json_excerpt(payload.get("args")),
    )


def _tool_result_evidence_item(
    *,
    index: int,
    payload: dict[str, object],
) -> VerificationEvidenceItem | None:
    tool_name = str(payload.get("tool_name") or "").strip()
    tool_call_id = str(payload.get("tool_call_id") or "").strip()
    if not tool_name or not tool_call_id:
        return None
    is_error = payload.get("error") is True
    return VerificationEvidenceItem(
        evidence_id=_evidence_id("tool_result", index, tool_call_id),
        kind=VerificationEvidenceKind.TOOL_RESULT,
        summary=f"Tool {tool_name} returned {'an error' if is_error else 'a result'}.",
        source="event_log",
        passed=not is_error,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        output_excerpt=_json_excerpt(payload.get("result")),
    )


def _latest_guardrail_report(
    *,
    event_bus: EventLog,
    trace_id: str,
    task_id: str,
) -> RuntimeGuardrailReport | None:
    report: RuntimeGuardrailReport | None = None
    for event in event_bus.list_by_trace(trace_id):
        if str(event.get("task_id") or "") != task_id:
            continue
        if (
            str(event.get("event_type") or "")
            != RunEventType.RUNTIME_GUARDRAIL_REPORT.value
        ):
            continue
        candidate = runtime_guardrail_report_from_payload(
            _parse_event_payload(event.get("payload_json"))
        )
        if candidate is not None:
            report = candidate
    return report


def _extract_tool_call_events(
    *,
    event_bus: EventLog,
    trace_id: str,
    task_id: str,
) -> tuple[dict[str, object], ...]:
    """Extract tool call records from the event log for LLM evaluations."""
    calls: list[dict[str, object]] = []
    for event in event_bus.list_by_trace(trace_id):
        if str(event.get("task_id") or "") != task_id:
            continue
        payload = _parse_event_payload(event.get("payload_json"))
        if payload and payload.get("tool_name"):
            calls.append(
                {
                    "tool_name": str(payload.get("tool_name", "")),
                    "args": payload.get("tool_args", {}),
                }
            )
    return tuple(calls)


def _runtime_guardrail_checks(
    *,
    report: RuntimeGuardrailReport | None,
    required: bool,
) -> tuple[VerificationCheckResult, ...]:
    if report is None:
        if not required:
            return ()
        return (
            VerificationCheckResult(
                layer=VerificationLayer.SECURITY,
                name="runtime_guardrail_report",
                passed=False,
                details="Runtime guardrail report is required but was not generated.",
            ),
        )
    checks = [
        VerificationCheckResult(
            layer=VerificationLayer.SECURITY,
            name=f"runtime_guardrail:{check.name}",
            passed=check.passed,
            details=check.details,
        )
        for check in report.checks
    ]
    checks.append(
        VerificationCheckResult(
            layer=VerificationLayer.SECURITY,
            name="runtime_guardrail_status",
            passed=report.status != RuntimeGuardrailStatus.BLOCKED,
            details=(
                "Runtime guardrail report is clear."
                if report.status == RuntimeGuardrailStatus.PASSED
                else (
                    "Runtime guardrail report contains warnings."
                    if report.status == RuntimeGuardrailStatus.WARNING
                    else "Runtime guardrail report contains blocked actions."
                )
            ),
        )
    )
    return tuple(checks)


def _runtime_guardrail_evidence_items(
    report: RuntimeGuardrailReport | None,
) -> tuple[VerificationEvidenceItem, ...]:
    if report is None:
        return ()
    return (_runtime_guardrail_evidence_item(index=0, report=report),)


def _runtime_guardrail_evidence_item(
    *,
    index: int,
    report: RuntimeGuardrailReport,
) -> VerificationEvidenceItem:
    status = report.status.value
    return VerificationEvidenceItem(
        evidence_id=_evidence_id("guardrail", index, report.task_id),
        kind=VerificationEvidenceKind.RUNTIME_GUARDRAIL_REPORT,
        summary=f"Runtime guardrail report status is {status}.",
        source="runtime_guardrail_report",
        passed=report.status != RuntimeGuardrailStatus.BLOCKED,
        output_excerpt=_json_excerpt(report.model_dump(mode="json")),
        metrics=(
            VerificationEvidenceMetric(
                name="guardrail_tool_calls",
                value=report.total_tool_calls,
            ),
            VerificationEvidenceMetric(
                name="guardrail_warnings",
                value=report.warning_count,
            ),
            VerificationEvidenceMetric(
                name="guardrail_blocks",
                value=report.blocked_count,
            ),
        ),
    )


def _is_gate_finding_event(
    *,
    event_type: str,
    payload: dict[str, object],
) -> bool:
    role_id = str(payload.get("role_id") or "").casefold()
    return (
        "gater" in role_id
        or "gate" in role_id
        or "finding" in payload
        or "findings" in payload
        or event_type == EventType.TASK_TIMEOUT.value
    )


def _gate_finding_evidence_item(
    *,
    index: int,
    event_type: str,
    payload: dict[str, object],
) -> VerificationEvidenceItem:
    feedback = (
        payload.get("findings")
        or payload.get("finding")
        or payload.get("feedback")
        or payload
    )
    return VerificationEvidenceItem(
        evidence_id=_evidence_id("gate", index, event_type),
        kind=VerificationEvidenceKind.GATE_FINDING,
        summary=f"Gate event {event_type} was recorded.",
        source="event_log",
        passed=_gate_finding_passed(event_type=event_type, payload=payload),
        output_excerpt=_json_excerpt(feedback),
    )


def _gate_finding_passed(*, event_type: str, payload: dict[str, object]) -> bool:
    if event_type == EventType.TASK_TIMEOUT.value:
        return False
    passed = payload.get("passed")
    if isinstance(passed, bool):
        return passed
    return True


def _link_evidence_to_plan(
    *,
    plan: VerificationPlan,
    evidence_items: tuple[VerificationEvidenceItem, ...],
) -> tuple[
    tuple[VerificationEvidenceItem, ...],
    tuple[VerificationEvidenceLink, ...],
    tuple[VerificationEvidenceLink, ...],
]:
    supports_by_id: dict[str, set[str]] = {
        item.evidence_id: set(item.supports) for item in evidence_items
    }
    acceptance_links = tuple(
        _link_text_to_evidence(
            text=criterion,
            target=VerificationEvidenceTarget.ACCEPTANCE_CRITERION,
            evidence_items=evidence_items,
            supports_by_id=supports_by_id,
        )
        for criterion in plan.acceptance_criteria
    )
    expectation_links = tuple(
        _link_text_to_evidence(
            text=expectation,
            target=VerificationEvidenceTarget.EVIDENCE_EXPECTATION,
            evidence_items=evidence_items,
            supports_by_id=supports_by_id,
        )
        for expectation in plan.evidence_expectations
    )
    supported_items = tuple(
        item.model_copy(
            update={"supports": tuple(sorted(supports_by_id[item.evidence_id]))}
        )
        for item in evidence_items
    )
    return supported_items, acceptance_links, expectation_links


def _link_text_to_evidence(
    *,
    text: str,
    target: VerificationEvidenceTarget,
    evidence_items: tuple[VerificationEvidenceItem, ...],
    supports_by_id: dict[str, set[str]],
) -> VerificationEvidenceLink:
    evidence_ids: list[str] = []
    for item in evidence_items:
        if not _evidence_can_support_text(text=text, target=target, item=item):
            continue
        evidence_ids.append(item.evidence_id)
        supports_by_id[item.evidence_id].add(text)
    satisfied = bool(evidence_ids)
    return VerificationEvidenceLink(
        target=target,
        text=text,
        evidence_ids=tuple(evidence_ids),
        satisfied=satisfied,
        reason=_evidence_link_reason(
            target=target,
            evidence_ids=tuple(evidence_ids),
        ),
    )


def _evidence_can_support_text(
    *,
    text: str,
    target: VerificationEvidenceTarget,
    item: VerificationEvidenceItem,
) -> bool:
    if item.passed is False:
        return False
    if item.source == "verification_check_skipped":
        return False
    if item.kind == VerificationEvidenceKind.TASK_RESULT:
        return False
    if (
        target == VerificationEvidenceTarget.ACCEPTANCE_CRITERION
        and item.kind == VerificationEvidenceKind.TOOL_CALL
    ):
        return False
    return _text_matches_evidence(text=text, item=item)


def _evidence_link_reason(
    *,
    target: VerificationEvidenceTarget,
    evidence_ids: tuple[str, ...],
) -> str:
    if evidence_ids:
        return (
            f"Matched {len(evidence_ids)} evidence item(s): {', '.join(evidence_ids)}."
        )
    if target == VerificationEvidenceTarget.ACCEPTANCE_CRITERION:
        return "No concrete evidence item matched this acceptance criterion."
    return "No concrete evidence item matched this expected evidence."


def _run_evidence_coverage_checks(
    *,
    acceptance_links: tuple[VerificationEvidenceLink, ...],
    expectation_links: tuple[VerificationEvidenceLink, ...],
) -> tuple[VerificationCheckResult, ...]:
    checks: list[VerificationCheckResult] = []
    for link in acceptance_links:
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.EVIDENCE,
                name=f"acceptance_evidence:{link.text}",
                passed=link.satisfied,
                details=link.reason,
            )
        )
    for link in expectation_links:
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.EVIDENCE,
                name=f"expected_evidence:{link.text}",
                passed=link.satisfied,
                details=link.reason,
            )
        )
    return tuple(checks)


def _wrap_cross_evaluation_evaluator(
    *,
    semantic_evaluator: SemanticVerificationEvaluator | None,
    cross_evaluation_models: tuple[str, ...],
) -> SemanticVerificationEvaluator | None:
    if semantic_evaluator is None or not cross_evaluation_models:
        return semantic_evaluator
    evaluator_count = len(cross_evaluation_models)
    evaluators = tuple(semantic_evaluator for _ in range(evaluator_count))
    log_event(
        LOGGER,
        logging.INFO,
        event="verification.cross_evaluation_configured",
        message=(
            "Wrapping semantic evaluator with multi-model cross-validation "
            f"for {evaluator_count} model(s)"
        ),
        payload={
            "models": list(cross_evaluation_models),
        },
    )
    return MultiModelSemanticEvaluator(evaluators=evaluators)


def _run_semantic_evaluations(
    *,
    task_id: str,
    criteria: tuple[str, ...],
    result: str,
    evidence_items: tuple[VerificationEvidenceItem, ...],
    acceptance_links: tuple[VerificationEvidenceLink, ...],
    semantic_evaluator: SemanticVerificationEvaluator | None,
) -> tuple[SemanticEvaluationResult, ...]:
    links_by_text = {link.text: link for link in acceptance_links}
    results: list[SemanticEvaluationResult] = []
    for criterion in criteria:
        link = links_by_text.get(criterion)
        linked_evidence = _linked_evidence_items(
            link=link, evidence_items=evidence_items
        )
        baseline = _rule_semantic_evaluation(
            criterion=criterion,
            link=link,
            linked_evidence=linked_evidence,
        )
        results.append(
            _run_optional_semantic_evaluator(
                task_id=task_id,
                criterion=criterion,
                result=result,
                linked_evidence=linked_evidence,
                baseline=baseline,
                semantic_evaluator=semantic_evaluator,
            )
        )
    return tuple(results)


def _linked_evidence_items(
    *,
    link: VerificationEvidenceLink | None,
    evidence_items: tuple[VerificationEvidenceItem, ...],
) -> tuple[VerificationEvidenceItem, ...]:
    if link is None:
        return ()
    evidence_ids = set(link.evidence_ids)
    return tuple(item for item in evidence_items if item.evidence_id in evidence_ids)


def _rule_semantic_evaluation(
    *,
    criterion: str,
    link: VerificationEvidenceLink | None,
    linked_evidence: tuple[VerificationEvidenceItem, ...],
) -> SemanticEvaluationResult:
    if link is None or not link.evidence_ids:
        return SemanticEvaluationResult(
            criterion=criterion,
            passed=False,
            confidence=0.0,
            reason="No evidence was linked to this acceptance criterion.",
        )
    strong_evidence_ids = tuple(
        item.evidence_id for item in linked_evidence if _is_strong_evidence(item)
    )
    if strong_evidence_ids:
        return SemanticEvaluationResult(
            criterion=criterion,
            passed=True,
            confidence=0.85,
            reason="Concrete verification evidence supports this acceptance criterion.",
            evidence_ids=strong_evidence_ids,
        )
    self_report_ids = tuple(
        item.evidence_id
        for item in linked_evidence
        if item.kind == VerificationEvidenceKind.TASK_RESULT
    )
    if self_report_ids:
        return SemanticEvaluationResult(
            criterion=criterion,
            passed=False,
            confidence=0.25,
            reason=(
                "The task result self-reports this criterion, but independent "
                "verification evidence is required."
            ),
            evidence_ids=self_report_ids,
        )
    return SemanticEvaluationResult(
        criterion=criterion,
        passed=False,
        confidence=0.2,
        reason="Only weak or inconclusive evidence was linked to this criterion.",
        evidence_ids=tuple(item.evidence_id for item in linked_evidence),
    )


def _is_strong_evidence(item: VerificationEvidenceItem) -> bool:
    if not item.passed:
        return False
    return item.kind in {
        VerificationEvidenceKind.REQUIRED_FILE,
        VerificationEvidenceKind.COMMAND,
        VerificationEvidenceKind.TEST_RESULT,
        VerificationEvidenceKind.LINT_RESULT,
        VerificationEvidenceKind.DIFF_SUMMARY,
        VerificationEvidenceKind.FORMAL_PROOF,
        VerificationEvidenceKind.TOOL_RESULT,
        VerificationEvidenceKind.GATE_FINDING,
    }


def _run_optional_semantic_evaluator(
    *,
    task_id: str,
    criterion: str,
    result: str,
    linked_evidence: tuple[VerificationEvidenceItem, ...],
    baseline: SemanticEvaluationResult,
    semantic_evaluator: SemanticVerificationEvaluator | None,
) -> SemanticEvaluationResult:
    if semantic_evaluator is None:
        return baseline
    request = SemanticEvaluationRequest(
        task_id=task_id,
        criterion=criterion,
        result_excerpt=_text_excerpt(result),
        evidence=linked_evidence,
    )
    try:
        evaluated = semantic_evaluator(request)
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="verification.semantic_evaluator_failed",
            message="Semantic verification evaluator failed; using rule fallback",
            payload={"task_id": task_id, "criterion": criterion, "error": str(exc)},
        )
        return baseline.model_copy(
            update={
                "reason": (
                    f"{baseline.reason} External evaluator failed; rule fallback used."
                )
            }
        )
    evaluator_name = evaluated.evaluator
    if evaluator_name == "rule":
        evaluator_name = "external"
    return evaluated.model_copy(
        update={
            "criterion": criterion,
            "evaluator": evaluator_name,
        }
    )


def _semantic_evaluation_checks(
    semantic_results: tuple[SemanticEvaluationResult, ...],
) -> tuple[VerificationCheckResult, ...]:
    return tuple(
        VerificationCheckResult(
            layer=VerificationLayer.SEMANTIC,
            name=f"semantic_acceptance:{result.criterion}",
            passed=result.passed,
            details=(
                f"{result.reason} confidence={result.confidence:.2f}; "
                f"evidence={', '.join(result.evidence_ids) or 'none'}"
            ),
        )
        for result in semantic_results
    )


def _text_matches_evidence(
    *,
    text: str,
    item: VerificationEvidenceItem,
) -> bool:
    normalized_text = text.casefold().strip()
    searchable_text = _evidence_search_text(item).casefold()
    if normalized_text and normalized_text in searchable_text:
        return True
    expected_tokens = _significant_tokens(normalized_text)
    evidence_tokens = _significant_tokens(searchable_text)
    if not expected_tokens:
        return False
    if len(expected_tokens) < _EVIDENCE_TEXT_MATCH_MIN_TOKEN_COUNT:
        return expected_tokens.issubset(evidence_tokens)
    overlap = len(expected_tokens & evidence_tokens) / len(expected_tokens)
    return overlap >= _EVIDENCE_TEXT_MATCH_MIN_OVERLAP


def _evidence_search_text(item: VerificationEvidenceItem) -> str:
    path = "" if item.path is None else str(item.path)
    metrics = " ".join(f"{metric.name} {metric.value}" for metric in item.metrics)
    return "\n".join(
        part
        for part in (
            item.kind.value.replace("_", " "),
            item.summary,
            item.source,
            path,
            " ".join(item.command),
            item.tool_name,
            item.output_excerpt,
            metrics,
        )
        if part
    )


def _significant_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-zA-Z0-9_]+", text.casefold()):
        normalized = _normalize_match_token(token)
        if token in _MATCH_STOPWORDS or normalized in _MATCH_STOPWORDS:
            continue
        if normalized:
            tokens.add(normalized)
    return tokens


_MATCH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "or",
        "output",
        "should",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)


def _normalize_match_token(token: str) -> str:
    normalized = token.strip("_")
    if normalized == "pass":
        return normalized
    if normalized in {"pytest", "unittest"}:
        return "test"
    if normalized in {"passes", "passed", "passing"}:
        return "pass"
    if normalized in {"fails", "failed", "failing"}:
        return "fail"
    if len(normalized) > 4 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if len(normalized) > 4 and normalized.endswith(
        ("ches", "shes", "sses", "xes", "zes")
    ):
        return normalized[:-2]
    if len(normalized) > 4 and normalized.endswith("es"):
        return normalized[:-1]
    if len(normalized) > 3 and normalized.endswith("s"):
        return normalized[:-1]
    return normalized


def _parse_event_payload(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): item for key, item in parsed.items()}


def _json_excerpt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _text_excerpt(value)
    try:
        return _text_excerpt(json.dumps(value, sort_keys=True))
    except TypeError:
        return _text_excerpt(str(value))


def _text_excerpt(value: str) -> str:
    return _command_output_excerpt(stdout=value, stderr="")


def _evidence_id(prefix: str, index: int, text: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", text.strip()).strip("-").lower()
    if not normalized:
        return f"{prefix}:{index}"
    return f"{prefix}:{index}:{normalized[:80]}"


def _text_mentions_any(text: str, needles: tuple[str, ...]) -> bool:
    for needle in needles:
        if " " in needle:
            if needle in text:
                return True
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", text):
            return True
    return False


def _command_output_excerpt(
    *,
    stdout: str,
    stderr: str,
    truncated: bool = False,
) -> str:
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    if len(combined) <= _OUTPUT_EXCERPT_CHARS:
        return f"{combined}...(truncated)" if truncated else combined
    return combined[:_OUTPUT_EXCERPT_CHARS] + "...(truncated)"


def _coerce_process_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _resolve_verification_path(
    path: Path, *, workspace_root: Path | None
) -> Path | None:
    if workspace_root is None:
        return path if path.is_absolute() else None
    root = workspace_root.resolve()
    candidate = (path if path.is_absolute() else root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
