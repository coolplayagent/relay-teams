from __future__ import annotations

from collections.abc import Awaitable
from enum import Enum
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)


class GatewaySessionBusyError(RuntimeError):
    def __init__(self, *, session_id: str, blocking_run_id: str) -> None:
        self.session_id = session_id
        self.blocking_run_id = blocking_run_id
        super().__init__(
            f"Session {session_id} is busy with active run {blocking_run_id}"
        )


class GatewaySessionIngressBusyPolicy(str, Enum):
    START_IF_IDLE = "start_if_idle"
    QUEUE_IF_BUSY = "queue_if_busy"
    REJECT_IF_BUSY = "reject_if_busy"


class GatewaySessionIngressStatus(str, Enum):
    STARTED = "started"
    QUEUED = "queued"
    REJECTED = "rejected"


class GatewaySessionIngressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentInput
    busy_policy: GatewaySessionIngressBusyPolicy = (
        GatewaySessionIngressBusyPolicy.REJECT_IF_BUSY
    )


class GatewaySessionIngressResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: GatewaySessionIngressStatus
    session_id: str = Field(min_length=1)
    run_id: str | None = None
    blocking_run_id: str | None = None


class _CreateDetachedRun(Protocol):
    def __call__(self, intent: IntentInput) -> tuple[str, str]: ...


class _EnsureRunStarted(Protocol):
    def __call__(self, run_id: str) -> None: ...


class _CreateDetachedRunAsync(Protocol):
    def __call__(self, intent: IntentInput) -> Awaitable[tuple[str, str]]:
        raise NotImplementedError


class _EnsureRunStartedAsync(Protocol):
    def __call__(self, run_id: str) -> Awaitable[None]:
        raise NotImplementedError


class GatewaySessionIngressService:
    def __init__(
        self,
        *,
        run_service: object,
        run_runtime_repo: RunRuntimeRepository,
    ) -> None:
        self._run_service = run_service
        self._run_runtime_repo = run_runtime_repo

    def submit(
        self,
        request: GatewaySessionIngressRequest,
    ) -> GatewaySessionIngressResult:
        blocking_run_id = self.active_run_id(request.intent.session_id)
        if blocking_run_id is not None:
            return self._busy_result(
                session_id=request.intent.session_id,
                blocking_run_id=blocking_run_id,
                busy_policy=request.busy_policy,
            )
        safe_intent = request.intent.model_copy(deep=True)
        try:
            run_id, _ = self._create_detached_run(safe_intent)
            self._ensure_run_started(run_id)
        except RuntimeError as exc:
            blocking_run_id = self.active_run_id(request.intent.session_id)
            if blocking_run_id is None or not _looks_like_session_busy_error(exc):
                raise
            return self._busy_result(
                session_id=request.intent.session_id,
                blocking_run_id=blocking_run_id,
                busy_policy=request.busy_policy,
            )
        return GatewaySessionIngressResult(
            status=GatewaySessionIngressStatus.STARTED,
            session_id=request.intent.session_id,
            run_id=run_id,
        )

    def require_started(
        self,
        request: GatewaySessionIngressRequest,
    ) -> GatewaySessionIngressResult:
        result = self.submit(request)
        if result.status is GatewaySessionIngressStatus.STARTED:
            return result
        blocking_run_id = str(result.blocking_run_id or "").strip() or "unknown"
        raise GatewaySessionBusyError(
            session_id=request.intent.session_id,
            blocking_run_id=blocking_run_id,
        )

    async def submit_async(
        self,
        request: GatewaySessionIngressRequest,
    ) -> GatewaySessionIngressResult:
        blocking_run_id = await self.active_run_id_async(request.intent.session_id)
        if blocking_run_id is not None:
            return self._busy_result(
                session_id=request.intent.session_id,
                blocking_run_id=blocking_run_id,
                busy_policy=request.busy_policy,
            )
        safe_intent = request.intent.model_copy(deep=True)
        try:
            run_id, _ = await self._create_detached_run_async(safe_intent)
            await self._ensure_run_started_async(run_id)
        except RuntimeError as exc:
            blocking_run_id = await self.active_run_id_async(request.intent.session_id)
            if blocking_run_id is None or not _looks_like_session_busy_error(exc):
                raise
            return self._busy_result(
                session_id=request.intent.session_id,
                blocking_run_id=blocking_run_id,
                busy_policy=request.busy_policy,
            )
        return GatewaySessionIngressResult(
            status=GatewaySessionIngressStatus.STARTED,
            session_id=request.intent.session_id,
            run_id=run_id,
        )

    async def require_started_async(
        self,
        request: GatewaySessionIngressRequest,
    ) -> GatewaySessionIngressResult:
        result = await self.submit_async(request)
        if result.status is GatewaySessionIngressStatus.STARTED:
            return result
        blocking_run_id = str(result.blocking_run_id or "").strip() or "unknown"
        raise GatewaySessionBusyError(
            session_id=request.intent.session_id,
            blocking_run_id=blocking_run_id,
        )

    def active_run_id(self, session_id: str) -> str | None:
        for runtime in self._list_session_runtimes(session_id):
            if self._is_busy_runtime(runtime):
                return runtime.run_id
        return None

    async def active_run_id_async(self, session_id: str) -> str | None:
        for runtime in await self._list_session_runtimes_async(session_id):
            if self._is_busy_runtime(runtime):
                return runtime.run_id
        return None

    def is_session_busy(self, session_id: str) -> bool:
        return self.active_run_id(session_id) is not None

    async def is_session_busy_async(self, session_id: str) -> bool:
        return await self.active_run_id_async(session_id) is not None

    def _busy_result(
        self,
        *,
        session_id: str,
        blocking_run_id: str,
        busy_policy: GatewaySessionIngressBusyPolicy,
    ) -> GatewaySessionIngressResult:
        if busy_policy is GatewaySessionIngressBusyPolicy.QUEUE_IF_BUSY:
            return GatewaySessionIngressResult(
                status=GatewaySessionIngressStatus.QUEUED,
                session_id=session_id,
                blocking_run_id=blocking_run_id,
            )
        return GatewaySessionIngressResult(
            status=GatewaySessionIngressStatus.REJECTED,
            session_id=session_id,
            blocking_run_id=blocking_run_id,
        )

    def _list_session_runtimes(self, session_id: str) -> tuple[RunRuntimeRecord, ...]:
        return tuple(
            sorted(
                self._run_runtime_repo.list_by_session(session_id),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        )

    async def _list_session_runtimes_async(
        self, session_id: str
    ) -> tuple[RunRuntimeRecord, ...]:
        return tuple(
            sorted(
                await self._run_runtime_repo.list_by_session_async(session_id),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        )

    @staticmethod
    def _is_busy_runtime(runtime: RunRuntimeRecord) -> bool:
        return runtime.status in {
            RunRuntimeStatus.QUEUED,
            RunRuntimeStatus.RUNNING,
            RunRuntimeStatus.STOPPING,
            RunRuntimeStatus.PAUSED,
            RunRuntimeStatus.STOPPED,
        }

    def _create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        run_service = self._run_service
        create_detached_run = getattr(run_service, "create_detached_run", None)
        if callable(create_detached_run):
            return cast(_CreateDetachedRun, create_detached_run)(intent)
        create_run = getattr(run_service, "create_run", None)
        if not callable(create_run):
            raise RuntimeError("Gateway session ingress run service is unavailable")
        return cast(_CreateDetachedRun, create_run)(intent)

    async def _create_detached_run_async(self, intent: IntentInput) -> tuple[str, str]:
        run_service = self._run_service
        create_detached_run = getattr(run_service, "create_detached_run_async", None)
        if callable(create_detached_run):
            return await cast(_CreateDetachedRunAsync, create_detached_run)(intent)
        create_run = getattr(run_service, "create_run_async", None)
        if not callable(create_run):
            raise RuntimeError("Gateway session ingress run service is unavailable")
        return await cast(_CreateDetachedRunAsync, create_run)(intent)

    def _ensure_run_started(self, run_id: str) -> None:
        ensure_run_started = getattr(self._run_service, "ensure_run_started", None)
        if not callable(ensure_run_started):
            raise RuntimeError("Gateway session ingress run service is unavailable")
        cast(_EnsureRunStarted, ensure_run_started)(run_id)

    async def _ensure_run_started_async(self, run_id: str) -> None:
        ensure_run_started = getattr(
            self._run_service, "ensure_run_started_async", None
        )
        if not callable(ensure_run_started):
            raise RuntimeError("Gateway session ingress run service is unavailable")
        await cast(_EnsureRunStartedAsync, ensure_run_started)(run_id)


def _looks_like_session_busy_error(error: RuntimeError) -> bool:
    message = str(error).strip().lower()
    return (
        "already has active run" in message
        or "does not accept follow-up input" in message
        or "waiting for tool approval" in message
        or "stopping. wait for it to stop" in message
    )


__all__ = [
    "GatewaySessionBusyError",
    "GatewaySessionIngressBusyPolicy",
    "GatewaySessionIngressRequest",
    "GatewaySessionIngressResult",
    "GatewaySessionIngressService",
    "GatewaySessionIngressStatus",
]
