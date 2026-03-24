# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime

from agent_teams.feishu.models import FeishuEnvironment
from agent_teams.feishu.subscription_service import FeishuSubscriptionService
from agent_teams.feishu.trigger_handler import FeishuTriggerHandler
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.triggers import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerDefinition,
    TriggerSourceType,
    TriggerStatus,
)


class _FakeTriggerService:
    def __init__(self, *, enabled: bool = True) -> None:
        now = datetime.now(tz=UTC)
        self.trigger = TriggerDefinition(
            trigger_id="trg_feishu",
            name="feishu_group",
            display_name="Feishu Group",
            source_type=TriggerSourceType.IM,
            status=TriggerStatus.ENABLED if enabled else TriggerStatus.DISABLED,
            public_token="token",
            source_config={"provider": "feishu", "trigger_rule": "mention_only"},
            auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
            target_config={"workspace_id": "default"},
            created_at=now,
            updated_at=now,
        )

    def list_triggers(self) -> tuple[TriggerDefinition, ...]:
        return (self.trigger,)

    def ingest_event(self, event, **_kwargs):  # pragma: no cover - not used here
        raise AssertionError("ingest_event should not be called in subscription tests")


class _FakeSessionService:
    def create_session(self, **_kwargs):  # pragma: no cover - not used here
        raise AssertionError(
            "create_session should not be called in subscription tests"
        )

    def get_session(self, session_id: str):  # pragma: no cover - not used here
        raise KeyError(session_id)

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        return None


class _FakeRunService:
    def create_run(self, intent):  # pragma: no cover - not used here
        raise AssertionError("create_run should not be called in subscription tests")

    def ensure_run_started(self, run_id: str) -> None:
        return None


class _FakeRunner:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_alive(self) -> bool:
        return self.started and not self.stopped


def _build_handler(tmp_path, *, enabled: bool = True) -> FeishuTriggerHandler:
    return FeishuTriggerHandler(
        trigger_service=_FakeTriggerService(enabled=enabled),
        session_service=_FakeSessionService(),
        run_service=_FakeRunService(),
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
    )


def test_subscription_service_starts_runner_when_configured(tmp_path) -> None:
    runner = _FakeRunner()
    service = FeishuSubscriptionService(
        event_handler=_build_handler(tmp_path),
        environment_loader=lambda: FeishuEnvironment(
            app_id="cli_app",
            app_secret="secret",
            verification_token="verify",
            encrypt_key=None,
        ),
        runner_factory=lambda **_kwargs: runner,
    )

    service.start()

    assert runner.started is True
    assert runner.stopped is False


def test_subscription_service_does_not_start_without_env(tmp_path) -> None:
    runner = _FakeRunner()
    service = FeishuSubscriptionService(
        event_handler=_build_handler(tmp_path),
        environment_loader=lambda: None,
        runner_factory=lambda **_kwargs: runner,
    )

    service.start()

    assert runner.started is False


def test_subscription_service_reloads_runner_on_signature_change(tmp_path) -> None:
    first_runner = _FakeRunner()
    second_runner = _FakeRunner()
    runners = [first_runner, second_runner]
    envs = [
        FeishuEnvironment(
            app_id="cli_app",
            app_secret="secret-1",
            verification_token="verify",
            encrypt_key=None,
        ),
        FeishuEnvironment(
            app_id="cli_app",
            app_secret="secret-2",
            verification_token="verify",
            encrypt_key=None,
        ),
    ]
    index = {"value": 0}

    def _load_env() -> FeishuEnvironment:
        return envs[index["value"]]

    def _runner_factory(**_kwargs) -> _FakeRunner:
        return runners.pop(0)

    service = FeishuSubscriptionService(
        event_handler=_build_handler(tmp_path),
        environment_loader=_load_env,
        runner_factory=_runner_factory,
    )

    service.start()
    index["value"] = 1
    service.reload()

    assert first_runner.started is True
    assert first_runner.stopped is True
    assert second_runner.started is True
    assert second_runner.stopped is False
