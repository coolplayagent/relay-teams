# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime

from agent_teams.feishu.models import (
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from agent_teams.feishu.subscription_service import FeishuSubscriptionService
from agent_teams.triggers import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerDefinition,
    TriggerSourceType,
    TriggerStatus,
)


def _build_trigger(
    *,
    trigger_id: str,
    name: str,
    app_id: str,
    app_name: str,
    enabled: bool = True,
) -> TriggerDefinition:
    now = datetime.now(tz=UTC)
    return TriggerDefinition(
        trigger_id=trigger_id,
        name=name,
        display_name=name,
        source_type=TriggerSourceType.IM,
        status=TriggerStatus.ENABLED if enabled else TriggerStatus.DISABLED,
        public_token=f"token-{trigger_id}",
        source_config={
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": app_id,
            "app_name": app_name,
        },
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config={"workspace_id": "default"},
        created_at=now,
        updated_at=now,
    )


def _build_runtime(trigger: TriggerDefinition, *, app_secret: str) -> FeishuTriggerRuntimeConfig:
    return FeishuTriggerRuntimeConfig(
        trigger_id=trigger.trigger_id,
        trigger_name=trigger.name,
        source=FeishuTriggerSourceConfig.model_validate(trigger.source_config),
        target=FeishuTriggerTargetConfig.model_validate(trigger.target_config or {}),
        environment=FeishuEnvironment(
            app_id=str(trigger.source_config["app_id"]),
            app_secret=app_secret,
            app_name=str(trigger.source_config["app_name"]),
        ),
    )


class _FakeTriggerService:
    def __init__(self, *triggers: TriggerDefinition) -> None:
        self.triggers = triggers

    def list_triggers(self) -> tuple[TriggerDefinition, ...]:
        return self.triggers


class _FakeFeishuConfigService:
    def __init__(self, runtime_configs: tuple[FeishuTriggerRuntimeConfig, ...]) -> None:
        self.runtime_configs = runtime_configs

    def list_enabled_runtime_configs(
        self,
        _triggers: tuple[TriggerDefinition, ...] | list[TriggerDefinition],
    ) -> tuple[FeishuTriggerRuntimeConfig, ...]:
        return self.runtime_configs


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


class _FakeHandler:
    pass


def test_subscription_service_starts_one_runner_per_enabled_bot() -> None:
    trigger_a = _build_trigger(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
    )
    trigger_b = _build_trigger(
        trigger_id="trg_b",
        name="bot_b",
        app_id="cli_b",
        app_name="bot-b",
    )
    runner_a = _FakeRunner()
    runner_b = _FakeRunner()
    runners = [runner_a, runner_b]
    service = FeishuSubscriptionService(
        trigger_service=_FakeTriggerService(trigger_a, trigger_b),
        feishu_config_service=_FakeFeishuConfigService(
            (_build_runtime(trigger_a, app_secret="secret-a"), _build_runtime(trigger_b, app_secret="secret-b"))
        ),
        event_handler=_FakeHandler(),
        runner_factory=lambda **_kwargs: runners.pop(0),
    )

    service.start()

    assert runner_a.started is True
    assert runner_b.started is True
    assert runner_a.stopped is False
    assert runner_b.stopped is False


def test_subscription_service_reloads_only_changed_bot_runner() -> None:
    trigger = _build_trigger(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
    )
    first_runner = _FakeRunner()
    second_runner = _FakeRunner()
    runtime_configs = [
        (_build_runtime(trigger, app_secret="secret-a"),),
        (_build_runtime(trigger, app_secret="secret-b"),),
    ]
    runtime_index = {"value": 0}

    service = FeishuSubscriptionService(
        trigger_service=_FakeTriggerService(trigger),
        feishu_config_service=_FakeFeishuConfigService(runtime_configs[0]),
        event_handler=_FakeHandler(),
        runner_factory=lambda **_kwargs: first_runner,
    )

    service.start()
    service._feishu_config_service = _FakeFeishuConfigService(runtime_configs[1])
    service._runner_factory = lambda **_kwargs: second_runner
    service.reload()

    assert first_runner.started is True
    assert first_runner.stopped is True
    assert second_runner.started is True
    assert second_runner.stopped is False


def test_subscription_service_stops_runner_when_bot_no_longer_enabled() -> None:
    trigger = _build_trigger(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
    )
    runner = _FakeRunner()
    service = FeishuSubscriptionService(
        trigger_service=_FakeTriggerService(trigger),
        feishu_config_service=_FakeFeishuConfigService(
            (_build_runtime(trigger, app_secret="secret-a"),)
        ),
        event_handler=_FakeHandler(),
        runner_factory=lambda **_kwargs: runner,
    )

    service.start()
    service._feishu_config_service = _FakeFeishuConfigService(())
    service.reload()

    assert runner.started is True
    assert runner.stopped is True
