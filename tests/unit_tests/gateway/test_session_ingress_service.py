# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
    GatewaySessionIngressStatus,
)
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository


class _FakeRunService:
    def __init__(self) -> None:
        self.created_intents: list[IntentInput] = []
        self.started_run_ids: list[str] = []

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created_intents.append(intent.model_copy(deep=True))
        return "run-1", intent.session_id

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)


def test_submit_preserves_default_root_instance_reuse(tmp_path) -> None:
    run_service = _FakeRunService()
    ingress_service = GatewaySessionIngressService(
        run_service=run_service,
        run_runtime_repo=RunRuntimeRepository(tmp_path / "gateway.db"),
    )

    result = ingress_service.submit(
        GatewaySessionIngressRequest(
            intent=IntentInput(
                session_id="session-1",
                input=content_parts_from_text("hello"),
            )
        )
    )

    assert result.status is GatewaySessionIngressStatus.STARTED
    assert run_service.created_intents[0].reuse_root_instance is True
    assert run_service.started_run_ids == ["run-1"]


def test_submit_preserves_explicit_root_instance_isolation(tmp_path) -> None:
    run_service = _FakeRunService()
    ingress_service = GatewaySessionIngressService(
        run_service=run_service,
        run_runtime_repo=RunRuntimeRepository(tmp_path / "gateway.db"),
    )

    result = ingress_service.submit(
        GatewaySessionIngressRequest(
            intent=IntentInput(
                session_id="session-1",
                input=content_parts_from_text("hello"),
                reuse_root_instance=False,
            )
        )
    )

    assert result.status is GatewaySessionIngressStatus.STARTED
    assert run_service.created_intents[0].reuse_root_instance is False
