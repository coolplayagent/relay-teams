from __future__ import annotations

import asyncio

from fastapi import FastAPI
import httpx
import pytest

from relay_teams.interfaces.server.deps import (
    get_run_service,
    get_session_service,
    get_skill_registry,
)
from relay_teams.interfaces.server.routers import runs, sessions
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.session_models import SessionRecord


class _FakeSkillRegistry:
    def resolve_known(
        self,
        values: tuple[str, ...],
        *,
        strict: bool,
        consumer: str,
    ) -> tuple[str, ...]:
        _ = (strict, consumer)
        return values


class _AsyncSessionService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, SessionRecord] = {}

    async def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        await asyncio.sleep(0.001)
        async with self._lock:
            resolved_session_id = session_id or f"session-{len(self._records) + 1}"
            record = SessionRecord(
                session_id=resolved_session_id,
                workspace_id=workspace_id,
                metadata={} if metadata is None else dict(metadata),
            )
            self._records[resolved_session_id] = record
            return record

    async def list_sessions(self) -> tuple[SessionRecord, ...]:
        async with self._lock:
            return tuple(self._records.values())


class _InjectedRecord:
    def __init__(self, *, run_id: str, source: InjectionSource, content: str) -> None:
        self._payload = {
            "run_id": run_id,
            "source": source.value,
            "content": content,
        }

    def model_dump(self) -> dict[str, object]:
        return dict(self._payload)


class _AsyncRunService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._created_runs: dict[str, IntentInput] = {}
        self.started_run_ids: list[str] = []
        self.injected_messages: list[tuple[str, str, str]] = []
        self.resolved_tool_approvals: list[tuple[str, str, str, str]] = []

    def create_run(self, intent_input: IntentInput) -> tuple[str, str]:
        _ = intent_input
        raise AssertionError("create_run sync API must not be used by the run router")

    def ensure_run_started(self, run_id: str) -> None:
        _ = run_id
        raise AssertionError(
            "ensure_run_started sync API must not be used by the run router"
        )

    async def create_run_async(self, intent_input: IntentInput) -> tuple[str, str]:
        await asyncio.sleep(0.001)
        async with self._lock:
            run_id = f"run-{len(self._created_runs) + 1}"
            self._created_runs[run_id] = intent_input
            if intent_input.session_id is None:
                raise RuntimeError("session id is required")
            return run_id, intent_input.session_id

    async def ensure_run_started_async(self, run_id: str) -> None:
        await asyncio.sleep(0.001)
        async with self._lock:
            self.started_run_ids.append(run_id)

    async def inject_message_async(
        self,
        *,
        run_id: str,
        source: InjectionSource,
        content: str,
    ) -> _InjectedRecord:
        await asyncio.sleep(0.001)
        async with self._lock:
            self.injected_messages.append((run_id, source.value, content))
        return _InjectedRecord(run_id=run_id, source=source, content=content)

    async def list_open_tool_approvals_async(
        self,
        run_id: str,
    ) -> list[dict[str, str]]:
        return [{"run_id": run_id, "tool_call_id": "call-open"}]

    async def resolve_tool_approval_async(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = "",
    ) -> None:
        await asyncio.sleep(0.001)
        async with self._lock:
            self.resolved_tool_approvals.append(
                (run_id, tool_call_id, action, feedback)
            )


def _create_app(
    *,
    session_service: _AsyncSessionService,
    run_service: _AsyncRunService,
) -> FastAPI:
    app = FastAPI()
    app.include_router(sessions.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_run_service] = lambda: run_service
    app.dependency_overrides[get_skill_registry] = _FakeSkillRegistry
    return app


@pytest.mark.asyncio
async def test_session_run_and_tool_call_routes_handle_concurrent_requests() -> None:
    session_service = _AsyncSessionService()
    run_service = _AsyncRunService()
    app = _create_app(session_service=session_service, run_service=run_service)
    transport = httpx.ASGITransport(app=app)
    request_count = 24

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=10.0,
    ) as client:
        session_responses = await asyncio.gather(
            *(
                client.post(
                    "/api/sessions",
                    json={
                        "session_id": f"session-load-{index}",
                        "workspace_id": "default",
                    },
                )
                for index in range(request_count)
            )
        )
        assert all(response.status_code == 200 for response in session_responses)

        run_responses = await asyncio.gather(
            *(
                client.post(
                    "/api/runs",
                    json={
                        "session_id": f"session-load-{index}",
                        "input": [
                            {
                                "kind": "text",
                                "text": f"load test run {index}",
                            }
                        ],
                        "execution_mode": "manual",
                    },
                )
                for index in range(request_count)
            )
        )
        assert all(response.status_code == 200 for response in run_responses)
        run_ids = [str(response.json()["run_id"]) for response in run_responses]

        inject_responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/runs/{run_id}/inject",
                    json={"source": "user", "content": f"follow-up {index}"},
                )
                for index, run_id in enumerate(run_ids)
            )
        )
        assert all(response.status_code == 200 for response in inject_responses)

        approval_responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/runs/{run_id}/tool-approvals/call-{index}/resolve",
                    json={"action": "approve", "feedback": f"ok {index}"},
                )
                for index, run_id in enumerate(run_ids)
            )
        )
        assert all(response.status_code == 200 for response in approval_responses)

        list_response = await client.get("/api/sessions")
        assert list_response.status_code == 200
        assert len(list_response.json()) == request_count

    assert sorted(run_service.started_run_ids) == sorted(run_ids)
    assert len(run_service.injected_messages) == request_count
    assert len(run_service.resolved_tool_approvals) == request_count
