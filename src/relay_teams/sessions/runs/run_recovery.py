# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from json import dumps, loads
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.assistant_errors import build_auto_recovery_prompt
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from relay_teams.sessions.runs.run_event_publisher import RunEventPublisher
from relay_teams.sessions.runs.run_models import RunEvent, RunResult
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeStatus,
)
from relay_teams.trace import bind_trace_context

logger = get_logger(__name__)


class AutoRecoveryReason(StrEnum):
    INVALID_TOOL_ARGS_JSON = "auto_recovery_invalid_tool_args_json"
    NETWORK_STREAM_INTERRUPTED = "auto_recovery_network_stream_interrupted"
    NETWORK_TIMEOUT = "auto_recovery_network_timeout"
    NETWORK_ERROR = "auto_recovery_network_error"


class AutoRecoveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: str
    reason: AutoRecoveryReason
    max_attempts: int
    prompt: str


def auto_recovery_policy(
    *,
    error_code: str,
    reason: AutoRecoveryReason,
    max_attempts: int,
) -> AutoRecoveryPolicy:
    prompt = build_auto_recovery_prompt(error_code)
    if prompt is None:
        raise ValueError(f"Missing auto recovery prompt for error_code={error_code}")
    return AutoRecoveryPolicy(
        error_code=error_code,
        reason=reason,
        max_attempts=max_attempts,
        prompt=prompt,
    )


AUTO_RECOVERY_POLICIES = (
    auto_recovery_policy(
        error_code="model_tool_args_invalid_json",
        reason=AutoRecoveryReason.INVALID_TOOL_ARGS_JSON,
        max_attempts=1,
    ),
    auto_recovery_policy(
        error_code="network_stream_interrupted",
        reason=AutoRecoveryReason.NETWORK_STREAM_INTERRUPTED,
        max_attempts=5,
    ),
    auto_recovery_policy(
        error_code="network_timeout",
        reason=AutoRecoveryReason.NETWORK_TIMEOUT,
        max_attempts=5,
    ),
    auto_recovery_policy(
        error_code="network_error",
        reason=AutoRecoveryReason.NETWORK_ERROR,
        max_attempts=1,
    ),
)


def is_transient_network_error_message(error_message: str) -> bool:
    normalized = error_message.strip().lower()
    if not normalized:
        return False
    blocking_markers = (
        "no_proxy",
        "proxy authentication",
        "proxy auth",
        "ssl",
        "tls",
        "certificate",
        "cert",
        "connection refused",
        "actively refused",
        "name or service not known",
        "nodename nor servname",
        "temporary failure in name resolution",
        "getaddrinfo",
        "dns",
        "host not found",
        "407",
    )
    if any(marker in normalized for marker in blocking_markers):
        return False
    transient_markers = (
        "connection reset",
        "connection aborted",
        "connection closed",
        "server disconnected",
        "temporarily unavailable",
        "temporary network",
        "temporary failure",
        "eof",
        "broken pipe",
    )
    return any(marker in normalized for marker in transient_markers)


def auto_recovery_policy_matches_payload(
    *,
    policy: AutoRecoveryPolicy,
    payload: RecoverableRunPausePayload,
) -> bool:
    if policy.error_code != "network_error":
        return True
    return is_transient_network_error_message(payload.error_message)


def auto_recovery_policy_for(
    payload: RecoverableRunPausePayload,
) -> AutoRecoveryPolicy | None:
    for policy in AUTO_RECOVERY_POLICIES:
        if policy.error_code != payload.error_code:
            continue
        if not auto_recovery_policy_matches_payload(
            policy=policy,
            payload=payload,
        ):
            return None
        return policy
    return None


class AppendFollowupToInstance(Protocol):
    def __call__(
        self,
        *,
        run_id: str,
        instance_id: str,
        task_id: str | None,
        content: str,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool: ...


class AppendFollowupToCoordinator(Protocol):
    def __call__(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool: ...


class RunRecoveryService:
    def __init__(
        self,
        *,
        get_event_log: Callable[[], EventLog | None],
        get_runtime: Callable[[str], RunRuntimeRecord | None],
        event_publisher: RunEventPublisher,
        append_followup_to_instance: AppendFollowupToInstance,
        append_followup_to_coordinator: AppendFollowupToCoordinator,
        resume_existing_run: Callable[[str], Awaitable[RunResult]],
    ) -> None:
        self._get_event_log = get_event_log
        self._get_runtime = get_runtime
        self._event_publisher = event_publisher
        self._append_followup_to_instance = append_followup_to_instance
        self._append_followup_to_coordinator = append_followup_to_coordinator
        self._resume_existing_run = resume_existing_run
        self._attempts: dict[tuple[str, AutoRecoveryReason], int] = {}

    async def run_with_auto_recovery(
        self,
        *,
        run_id: str,
        session_id: str,
        runner: Callable[[], Awaitable[RunResult]],
    ) -> RunResult:
        async def _resume_runner() -> RunResult:
            return await self._resume_existing_run(run_id)

        current_runner = runner
        while True:
            try:
                return await current_runner()
            except RecoverableRunPauseError as exc:
                policy = auto_recovery_policy_for(exc.payload)
                if policy is None:
                    raise
                attempt = self._next_attempt(exc.payload, policy=policy)
                if attempt is None:
                    raise
                self._record_attempt(
                    run_id=run_id,
                    reason=policy.reason,
                    attempt=attempt,
                )
                self._queue_prompt(payload=exc.payload, policy=policy)
                resume_payload = self.transition_run_to_resumed(
                    run_id=run_id,
                    session_id=session_id,
                    reason=policy.reason,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                )
                with bind_trace_context(
                    trace_id=run_id,
                    run_id=run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.WARNING,
                        event="run.auto_recovery.resumed",
                        message="Run resumed automatically after recoverable LLM failure",
                        payload={
                            **resume_payload,
                            "error_code": exc.payload.error_code,
                        },
                    )
                current_runner = _resume_runner

    def build_run_paused_payload(
        self, payload: RecoverableRunPausePayload
    ) -> dict[str, JsonValue]:
        paused_payload: dict[str, JsonValue] = payload.model_dump(mode="json")
        policy = auto_recovery_policy_for(payload)
        if policy is None:
            return paused_payload
        attempts = self._count_attempts(
            payload.run_id,
            reason=policy.reason,
        )
        paused_payload["auto_recovery_exhausted"] = attempts >= policy.max_attempts
        paused_payload["attempt"] = attempts
        paused_payload["max_attempts"] = policy.max_attempts
        paused_payload["auto_recovery_reason"] = policy.reason.value
        return paused_payload

    def transition_run_to_resumed(
        self,
        *,
        run_id: str,
        session_id: str,
        reason: str,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, JsonValue]:
        runtime = self._get_runtime(run_id)
        phase = RunRuntimePhase.COORDINATOR_RUNNING
        if runtime is not None and runtime.phase != RunRuntimePhase.TERMINAL:
            phase = runtime.phase
        self._event_publisher.safe_runtime_update(
            run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=phase,
            last_error=None,
        )
        payload = self._build_run_resumed_payload(
            session_id=session_id,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        self._event_publisher.safe_publish_run_event(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_RESUMED,
                payload_json=dumps(payload),
            ),
            failure_event="run.event.publish_failed",
        )
        return payload

    def clear_attempts(self, run_id: str) -> None:
        self._attempts = {
            key: value for key, value in self._attempts.items() if key[0] != run_id
        }

    def count_attempts(
        self,
        run_id: str,
        *,
        reason: AutoRecoveryReason,
    ) -> int:
        return self._count_attempts(run_id, reason=reason)

    def _next_attempt(
        self,
        payload: RecoverableRunPausePayload,
        *,
        policy: AutoRecoveryPolicy,
    ) -> int | None:
        attempts = self._count_attempts(
            payload.run_id,
            reason=policy.reason,
        )
        if attempts >= policy.max_attempts:
            return None
        return attempts + 1

    def _count_attempts(
        self,
        run_id: str,
        *,
        reason: AutoRecoveryReason,
    ) -> int:
        persisted_attempts = self._count_persisted_attempts(run_id, reason=reason)
        in_memory_attempts = self._attempts.get((run_id, reason), 0)
        return max(persisted_attempts, in_memory_attempts)

    def _count_persisted_attempts(
        self,
        run_id: str,
        *,
        reason: AutoRecoveryReason,
    ) -> int:
        event_log = self._get_event_log()
        if event_log is None:
            return 0
        count = 0
        for event in event_log.list_by_trace(run_id):
            if str(event.get("event_type") or "") != RunEventType.RUN_RESUMED.value:
                continue
            raw_payload = event.get("payload_json")
            if not isinstance(raw_payload, str) or not raw_payload.strip():
                continue
            try:
                parsed = loads(raw_payload)
            except ValueError:
                continue
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("reason") or "") == reason.value:
                count += 1
        return count

    def _record_attempt(
        self,
        *,
        run_id: str,
        reason: AutoRecoveryReason,
        attempt: int,
    ) -> None:
        key = (run_id, reason)
        current = self._attempts.get(key, 0)
        self._attempts[key] = max(current, attempt)

    def _queue_prompt(
        self,
        *,
        payload: RecoverableRunPausePayload,
        policy: AutoRecoveryPolicy,
    ) -> None:
        if self._append_followup_to_instance(
            run_id=payload.run_id,
            instance_id=payload.instance_id,
            task_id=payload.task_id,
            content=policy.prompt,
            enqueue=False,
            source=InjectionSource.SYSTEM,
        ):
            return
        self._append_followup_to_coordinator(
            payload.run_id,
            policy.prompt,
            enqueue=False,
            source=InjectionSource.SYSTEM,
        )

    @staticmethod
    def _build_run_resumed_payload(
        *,
        session_id: str,
        reason: str,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "session_id": session_id,
            "reason": reason,
        }
        if attempt is not None:
            payload["attempt"] = attempt
        if max_attempts is not None:
            payload["max_attempts"] = max_attempts
        return payload
