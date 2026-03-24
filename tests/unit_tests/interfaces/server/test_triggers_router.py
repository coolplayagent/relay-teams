from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import (
    get_feishu_subscription_service,
    get_feishu_trigger_config_service,
    get_trigger_service,
)
from agent_teams.interfaces.server.routers import triggers
from agent_teams.triggers import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerAuthRejectedError,
    TriggerDefinition,
    TriggerEventRecord,
    TriggerEventStatus,
    TriggerIngestResult,
    TriggerSourceType,
    TriggerStatus,
)


def _build_trigger() -> TriggerDefinition:
    now = datetime.now(tz=UTC)
    return TriggerDefinition(
        trigger_id="trg_test",
        name="router_test_trigger",
        display_name="Router Test Trigger",
        source_type=TriggerSourceType.WEBHOOK,
        status=TriggerStatus.ENABLED,
        public_token="token-test",
        source_config={},
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config=None,
        created_at=now,
        updated_at=now,
    )


def _build_event(
    status: TriggerEventStatus = TriggerEventStatus.RECEIVED,
) -> TriggerEventRecord:
    return TriggerEventRecord(
        sequence_id=1,
        event_id="tev_test",
        trigger_id="trg_test",
        trigger_name="router_test_trigger",
        source_type=TriggerSourceType.WEBHOOK,
        event_key="evt-1",
        status=status,
        received_at=datetime.now(tz=UTC),
        occurred_at=None,
        payload={"action": "push"},
        metadata={},
        headers={},
        remote_addr=None,
        auth_mode=TriggerAuthMode.NONE,
        auth_result="accepted",
        auth_reason="ok",
    )


class _FakeTriggerService:
    def __init__(self) -> None:
        self.trigger = _build_trigger()
        self.event = _build_event()
        self.deleted_trigger_ids: list[str] = []

    def create_trigger(self, _req: object) -> TriggerDefinition:
        return self.trigger

    def list_triggers(self) -> tuple[TriggerDefinition, ...]:
        return (self.trigger,)

    def get_trigger(self, trigger_id: str) -> TriggerDefinition:
        if trigger_id != self.trigger.trigger_id:
            raise KeyError(trigger_id)
        return self.trigger

    def update_trigger(self, trigger_id: str, _req: object) -> TriggerDefinition:
        return self.get_trigger(trigger_id)

    def delete_trigger(self, trigger_id: str) -> None:
        _ = self.get_trigger(trigger_id)
        self.deleted_trigger_ids.append(trigger_id)

    def set_trigger_status(
        self, trigger_id: str, status: TriggerStatus
    ) -> TriggerDefinition:
        _ = self.get_trigger(trigger_id)
        self.trigger = self.trigger.model_copy(update={"status": status})
        return self.trigger

    def rotate_public_token(self, trigger_id: str) -> TriggerDefinition:
        _ = self.get_trigger(trigger_id)
        self.trigger = self.trigger.model_copy(update={"public_token": "token-rotated"})
        return self.trigger

    def ingest_event(self, _req: object, **_kwargs: object) -> TriggerIngestResult:
        return TriggerIngestResult(
            accepted=True,
            event_id=self.event.event_id,
            duplicate=False,
            status=TriggerEventStatus.RECEIVED,
            trigger_id=self.trigger.trigger_id,
            trigger_name=self.trigger.name,
        )

    def ingest_webhook(self, **_kwargs: object) -> TriggerIngestResult:
        return TriggerIngestResult(
            accepted=True,
            event_id=self.event.event_id,
            duplicate=False,
            status=TriggerEventStatus.RECEIVED,
            trigger_id=self.trigger.trigger_id,
            trigger_name=self.trigger.name,
        )

    def get_event(self, event_id: str) -> TriggerEventRecord:
        if event_id != self.event.event_id:
            raise KeyError(event_id)
        return self.event

    def list_events(
        self, _trigger_id: str, *, limit: int, cursor_event_id: str | None
    ) -> tuple[tuple[TriggerEventRecord, ...], str | None]:
        _ = (limit, cursor_event_id)
        return (self.event,), None


class _FakeRejectingTriggerService(_FakeTriggerService):
    def ingest_webhook(self, **_kwargs: object) -> TriggerIngestResult:
        raise TriggerAuthRejectedError(
            "forbidden",
            _build_event(status=TriggerEventStatus.REJECTED_AUTH),
        )


class _FakeFeishuSubscriptionService:
    def __init__(self) -> None:
        self.reload_calls = 0

    def reload(self) -> None:
        self.reload_calls += 1


class _FakeFeishuTriggerConfigService:
    def __init__(self) -> None:
        self.saved_secret_calls: list[tuple[str, dict[str, str] | None, bool]] = []
        self.cleared_trigger_ids: list[str] = []
        self.deleted_secret_trigger_ids: list[str] = []

    def validate_create_request(self, _req: object) -> None:
        return None

    def validate_update_request(self, *, existing: object, request: object) -> None:
        _ = (existing, request)
        return None

    def save_secret_config(
        self,
        *,
        trigger_id: str,
        secret_config_payload: dict[str, str] | None,
        require_app_secret: bool,
    ) -> None:
        self.saved_secret_calls.append(
            (trigger_id, secret_config_payload, require_app_secret)
        )

    def attach_secret_status(self, trigger: TriggerDefinition) -> TriggerDefinition:
        return trigger.model_copy(
            update={"secret_status": {"app_secret_configured": True}}
        )

    def attach_secret_statuses(
        self,
        triggers: tuple[TriggerDefinition, ...],
    ) -> tuple[TriggerDefinition, ...]:
        return tuple(self.attach_secret_status(trigger) for trigger in triggers)

    def runtime_settings_changed(
        self,
        before: TriggerDefinition,
        after: TriggerDefinition,
    ) -> bool:
        return before.target_config != after.target_config

    def subscription_runtime_changed_for_update(
        self,
        *,
        existing: TriggerDefinition,
        request: object,
    ) -> bool:
        _ = existing
        source_config = getattr(request, "source_config", None)
        secret_config = getattr(request, "secret_config", None)
        if source_config is not None and source_config.get("app_id") != "cli_demo":
            return True
        return secret_config is not None

    def clear_bindings(self, trigger_id: str) -> None:
        self.cleared_trigger_ids.append(trigger_id)

    def delete_secret_config(self, trigger_id: str) -> None:
        self.deleted_secret_trigger_ids.append(trigger_id)


def _create_test_client(
    fake_service: object,
    *,
    fake_feishu_subscription_service: object | None = None,
    fake_feishu_trigger_config_service: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(triggers.router, prefix="/api")
    app.dependency_overrides[get_trigger_service] = lambda: fake_service
    app.dependency_overrides[get_feishu_subscription_service] = (
        (lambda: fake_feishu_subscription_service)
        if fake_feishu_subscription_service is not None
        else (lambda: _FakeFeishuSubscriptionService())
    )
    app.dependency_overrides[get_feishu_trigger_config_service] = (
        (lambda: fake_feishu_trigger_config_service)
        if fake_feishu_trigger_config_service is not None
        else (lambda: _FakeFeishuTriggerConfigService())
    )
    return TestClient(app)


def test_trigger_router_create_and_webhook_ingest() -> None:
    client = _create_test_client(_FakeTriggerService())

    create_resp = client.post(
        "/api/triggers",
        json={"name": "router_test_trigger", "source_type": "webhook"},
    )
    assert create_resp.status_code == 200
    assert create_resp.json()["trigger_id"] == "trg_test"

    webhook_resp = client.post(
        "/api/triggers/webhooks/token-test",
        json={"payload": {"action": "push"}},
    )
    assert webhook_resp.status_code == 200
    assert webhook_resp.json()["accepted"] is True


def test_trigger_router_maps_auth_rejection_to_403() -> None:
    client = _create_test_client(_FakeRejectingTriggerService())
    response = client.post(
        "/api/triggers/webhooks/token-test",
        json={"payload": {"action": "push"}},
    )
    assert response.status_code == 403


def test_trigger_router_reloads_feishu_subscription_on_feishu_trigger_create() -> None:
    now = datetime.now(tz=UTC)
    trigger = TriggerDefinition(
        trigger_id="trg_feishu",
        name="feishu_group",
        display_name="Feishu Group",
        source_type=TriggerSourceType.IM,
        status=TriggerStatus.ENABLED,
        public_token="token-test",
        source_config={
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": "cli_demo",
            "app_name": "bot",
        },
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config={"workspace_id": "default"},
        created_at=now,
        updated_at=now,
    )

    class _FakeFeishuTriggerService(_FakeTriggerService):
        def create_trigger(self, _req: object) -> TriggerDefinition:
            self.trigger = trigger
            return trigger

    subscription_service = _FakeFeishuSubscriptionService()
    feishu_config_service = _FakeFeishuTriggerConfigService()
    client = _create_test_client(
        _FakeFeishuTriggerService(),
        fake_feishu_subscription_service=subscription_service,
        fake_feishu_trigger_config_service=feishu_config_service,
    )
    response = client.post(
        "/api/triggers",
        json={
            "name": "feishu_group",
            "source_type": "im",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "bot",
            },
            "target_config": {"workspace_id": "default"},
            "secret_config": {"app_secret": "secret-demo"},
        },
    )
    assert response.status_code == 200
    assert subscription_service.reload_calls == 1
    assert feishu_config_service.saved_secret_calls == [
        ("trg_feishu", {"app_secret": "secret-demo"}, True)
    ]
    assert response.json()["secret_status"]["app_secret_configured"] is True


def test_trigger_router_clears_bindings_when_feishu_runtime_settings_change() -> None:
    now = datetime.now(tz=UTC)
    trigger = TriggerDefinition(
        trigger_id="trg_feishu",
        name="feishu_group",
        display_name="Feishu Group",
        source_type=TriggerSourceType.IM,
        status=TriggerStatus.ENABLED,
        public_token="token-test",
        source_config={
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": "cli_demo",
            "app_name": "bot",
        },
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config={
            "workspace_id": "default",
            "session_mode": "normal",
            "yolo": True,
        },
        created_at=now,
        updated_at=now,
    )

    class _FakeFeishuTriggerService(_FakeTriggerService):
        def __init__(self) -> None:
            super().__init__()
            self.trigger = trigger

        def update_trigger(self, trigger_id: str, req: object) -> TriggerDefinition:
            _ = self.get_trigger(trigger_id)
            target_config = getattr(req, "target_config", None)
            if target_config is None:
                return self.trigger
            self.trigger = self.trigger.model_copy(update={"target_config": target_config})
            return self.trigger

    subscription_service = _FakeFeishuSubscriptionService()
    feishu_config_service = _FakeFeishuTriggerConfigService()
    client = _create_test_client(
        _FakeFeishuTriggerService(),
        fake_feishu_subscription_service=subscription_service,
        fake_feishu_trigger_config_service=feishu_config_service,
    )

    response = client.patch(
        "/api/triggers/trg_feishu",
        json={
            "target_config": {
                "workspace_id": "workspace-ops",
                "session_mode": "orchestration",
                "orchestration_preset_id": "default",
                "yolo": False,
                "thinking": {"enabled": True, "effort": "high"},
            }
        },
    )

    assert response.status_code == 200
    assert subscription_service.reload_calls == 0
    assert feishu_config_service.cleared_trigger_ids == ["trg_feishu"]


def test_trigger_router_reloads_feishu_subscription_when_runtime_credentials_change() -> None:
    now = datetime.now(tz=UTC)
    trigger = TriggerDefinition(
        trigger_id="trg_feishu",
        name="feishu_group",
        display_name="Feishu Group",
        source_type=TriggerSourceType.IM,
        status=TriggerStatus.ENABLED,
        public_token="token-test",
        source_config={
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": "cli_demo",
            "app_name": "bot",
        },
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config={"workspace_id": "default"},
        created_at=now,
        updated_at=now,
    )

    class _FakeFeishuTriggerService(_FakeTriggerService):
        def __init__(self) -> None:
            super().__init__()
            self.trigger = trigger

        def update_trigger(self, trigger_id: str, req: object) -> TriggerDefinition:
            _ = self.get_trigger(trigger_id)
            source_config = getattr(req, "source_config", None)
            updated_source_config = (
                self.trigger.source_config if source_config is None else source_config
            )
            self.trigger = self.trigger.model_copy(update={"source_config": updated_source_config})
            return self.trigger

    subscription_service = _FakeFeishuSubscriptionService()
    feishu_config_service = _FakeFeishuTriggerConfigService()
    client = _create_test_client(
        _FakeFeishuTriggerService(),
        fake_feishu_subscription_service=subscription_service,
        fake_feishu_trigger_config_service=feishu_config_service,
    )

    response = client.patch(
        "/api/triggers/trg_feishu",
        json={
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_changed",
                "app_name": "bot",
            }
        },
    )

    assert response.status_code == 200
    assert subscription_service.reload_calls == 1


def test_trigger_router_deletes_feishu_trigger_and_reloads_subscription() -> None:
    now = datetime.now(tz=UTC)
    trigger = TriggerDefinition(
        trigger_id="trg_feishu",
        name="feishu_group",
        display_name="Feishu Group",
        source_type=TriggerSourceType.IM,
        status=TriggerStatus.ENABLED,
        public_token="token-test",
        source_config={
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": "cli_demo",
            "app_name": "bot",
        },
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config={"workspace_id": "default"},
        created_at=now,
        updated_at=now,
    )

    class _FakeFeishuTriggerService(_FakeTriggerService):
        def __init__(self) -> None:
            super().__init__()
            self.trigger = trigger

    service = _FakeFeishuTriggerService()
    subscription_service = _FakeFeishuSubscriptionService()
    feishu_config_service = _FakeFeishuTriggerConfigService()
    client = _create_test_client(
        service,
        fake_feishu_subscription_service=subscription_service,
        fake_feishu_trigger_config_service=feishu_config_service,
    )

    response = client.delete("/api/triggers/trg_feishu")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.deleted_trigger_ids == ["trg_feishu"]
    assert feishu_config_service.cleared_trigger_ids == ["trg_feishu"]
    assert feishu_config_service.deleted_secret_trigger_ids == ["trg_feishu"]
    assert subscription_service.reload_calls == 1
