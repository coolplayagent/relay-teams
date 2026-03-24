from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import (
    get_feishu_subscription_service,
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


def _create_test_client(
    fake_service: object,
    *,
    fake_feishu_subscription_service: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(triggers.router, prefix="/api")
    app.dependency_overrides[get_trigger_service] = lambda: fake_service
    app.dependency_overrides[get_feishu_subscription_service] = (
        (lambda: fake_feishu_subscription_service)
        if fake_feishu_subscription_service is not None
        else (lambda: _FakeFeishuSubscriptionService())
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
        source_config={"provider": "feishu"},
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
    client = _create_test_client(
        _FakeFeishuTriggerService(),
        fake_feishu_subscription_service=subscription_service,
    )
    response = client.post(
        "/api/triggers",
        json={
            "name": "feishu_group",
            "source_type": "im",
            "source_config": {"provider": "feishu"},
        },
    )
    assert response.status_code == 200
    assert subscription_service.reload_calls == 1
