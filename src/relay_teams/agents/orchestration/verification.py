from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Literal, NamedTuple

from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import (
    VerificationCheckResult,
    VerificationCommand,
    VerificationPlan,
    VerificationReport,
    VerificationResult,
)
from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.tools.runtime.models import ToolRuntimeDecision
from relay_teams.tools.runtime.policy import ToolApprovalPolicy

_COMMAND_OUTPUT_CAPTURE_BYTES = 64 * 1024
_OUTPUT_READ_CHUNK_BYTES = 8192
_OUTPUT_EXCERPT_CHARS = 2000
_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_OUTPUT_READER_JOIN_TIMEOUT_SECONDS = 1.0


class _BoundedCommandResult(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: bytes
    output_truncated: bool


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
) -> VerificationResult:
    task = task_repo.get(task_id)
    if task.status != TaskStatus.COMPLETED or task.result is None:
        passed = False
        details = ("Task not completed yet",)
        checks = (
            VerificationCheckResult(
                layer=VerificationLayer.STRUCTURE,
                name="completed_status",
                passed=False,
                details="Task is not completed or has no result.",
            ),
        )
        event_type = EventType.VERIFICATION_FAILED
    else:
        checks = _run_verification_plan(
            plan=task.envelope.verification,
            result=task.result,
            allowed_tools=allowed_tools,
            tool_approval_policy=tool_approval_policy or ToolApprovalPolicy(),
            workspace_root=workspace_root,
        )
        missing = tuple(
            check.name for check in checks if not check.passed and check.name
        )
        passed = len(missing) == 0
        details = ("Verification report passed",) if passed else missing
        event_type = (
            EventType.VERIFICATION_PASSED if passed else EventType.VERIFICATION_FAILED
        )

    report = VerificationReport(
        task_id=task.envelope.task_id,
        passed=passed,
        checks=checks,
        unmet_items=() if passed else details,
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
    return verification


def _run_verification_plan(
    *,
    plan: VerificationPlan,
    result: str,
    allowed_tools: tuple[str, ...],
    tool_approval_policy: ToolApprovalPolicy,
    workspace_root: Path | None,
) -> tuple[VerificationCheckResult, ...]:
    checks: list[VerificationCheckResult] = []
    checks.extend(_run_checklist_checks(plan=plan, result=result))
    checks.extend(_run_required_file_checks(plan=plan, workspace_root=workspace_root))
    checks.extend(
        _run_command_checks(
            plan=plan,
            allowed_tools=allowed_tools,
            tool_approval_policy=tool_approval_policy,
            workspace_root=workspace_root,
        )
    )
    checks.extend(_run_spec_evidence_checks(plan=plan, result=result))
    return tuple(checks)


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
                passed=False,
                details="Command verification requires shell approval before execution.",
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
    return [
        _run_command_check(command_check, workspace_root=workspace_root)
        for command_check in plan.command_checks
    ]


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
                process.kill()
                returncode = process.wait()
                break
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                timed_out = True
                process.kill()
                returncode = process.wait()
                break
            time.sleep(min(_PROCESS_POLL_INTERVAL_SECONDS, remaining_seconds))
    finally:
        if process.poll() is None:
            process.kill()
            returncode = process.wait()
        _close_process_streams(process)
        _join_output_reader_threads(output_threads)

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
            chunk = stream.read(_OUTPUT_READ_CHUNK_BYTES)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        capture.append(stream_name, chunk)


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def _join_output_reader_threads(threads: tuple[threading.Thread, ...]) -> None:
    for thread in threads:
        thread.join(timeout=_OUTPUT_READER_JOIN_TIMEOUT_SECONDS)


def _run_spec_evidence_checks(
    *,
    plan: VerificationPlan,
    result: str,
) -> list[VerificationCheckResult]:
    normalized_result = result.lower()
    checks: list[VerificationCheckResult] = []
    for criterion in plan.acceptance_criteria:
        passed = criterion.lower() in normalized_result
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name=f"acceptance:{criterion}",
                passed=passed,
                details=(
                    "Acceptance criterion was cited in the result."
                    if passed
                    else "Acceptance criterion was not cited in the result."
                ),
            )
        )
    for expectation in plan.evidence_expectations:
        passed = expectation.lower() in normalized_result
        checks.append(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name=f"evidence:{expectation}",
                passed=passed,
                details=(
                    "Expected evidence was cited in the result."
                    if passed
                    else "Expected evidence was not cited in the result."
                ),
            )
        )
    return checks


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
