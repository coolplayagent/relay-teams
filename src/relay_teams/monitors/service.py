# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from relay_teams.logger import get_logger, log_event
from relay_teams.monitors.models import (
    MonitorAction,
    MonitorActionType,
    MonitorEventEnvelope,
    MonitorRule,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
    MonitorSubscriptionStatus,
    MonitorTriggerRecord,
)
from relay_teams.monitors.repository import MonitorRepository
from relay_teams.notifications import (
    NotificationContext,
    NotificationService,
    NotificationType,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent

LOGGER = get_logger(__name__)


class MonitorActionSink(Protocol):
    def handle_monitor_trigger(
        self,
        *,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
        message: str,
    ) -> None: ...


class MonitorService:
    def __init__(
        self,
        *,
        repository: MonitorRepository,
        run_event_hub: RunEventHub,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._repository = repository
        self._run_event_hub = run_event_hub
        self._notification_service = notification_service
        self._action_sink: MonitorActionSink | None = None

    def bind_action_sink(self, sink: MonitorActionSink | None) -> None:
        self._action_sink = sink

    def bind_notification_service(
        self,
        notification_service: NotificationService | None,
    ) -> None:
        self._notification_service = notification_service

    def create_monitor(
        self,
        *,
        run_id: str,
        session_id: str,
        source_kind: MonitorSourceKind,
        source_key: str,
        rule: MonitorRule,
        action: MonitorAction,
        created_by_instance_id: str | None,
        created_by_role_id: str | None,
        tool_call_id: str | None,
    ) -> MonitorSubscriptionRecord:
        now = _utc_now()
        record = MonitorSubscriptionRecord(
            monitor_id=f"mon_{uuid4().hex[:12]}",
            run_id=run_id,
            session_id=session_id,
            source_kind=source_kind,
            source_key=source_key.strip(),
            created_by_instance_id=_normalize_optional_text(created_by_instance_id),
            created_by_role_id=_normalize_optional_text(created_by_role_id),
            tool_call_id=_normalize_optional_text(tool_call_id),
            status=MonitorSubscriptionStatus.ACTIVE,
            rule=rule,
            action=action,
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create_subscription(record)
        self._publish_monitor_event(
            record=created,
            event_type=RunEventType.MONITOR_CREATED,
            payload={
                "monitor_id": created.monitor_id,
                "source_kind": created.source_kind.value,
                "source_key": created.source_key,
                "action_type": created.action.action_type.value,
            },
        )
        return created

    def list_for_run(self, run_id: str) -> tuple[MonitorSubscriptionRecord, ...]:
        return self._repository.list_for_run(run_id)

    def stop_for_run(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> MonitorSubscriptionRecord:
        record = self._repository.get_subscription(monitor_id)
        if record.run_id != run_id:
            raise KeyError(f"Monitor {monitor_id} does not belong to run {run_id}")
        if record.status == MonitorSubscriptionStatus.STOPPED:
            return record
        now = _utc_now()
        updated = self._repository.update_subscription(
            record.model_copy(
                update={
                    "status": MonitorSubscriptionStatus.STOPPED,
                    "updated_at": now,
                    "stopped_at": now,
                }
            )
        )
        self._publish_monitor_event(
            record=updated,
            event_type=RunEventType.MONITOR_STOPPED,
            payload={"monitor_id": updated.monitor_id},
        )
        return updated

    def emit(self, envelope: MonitorEventEnvelope) -> tuple[MonitorTriggerRecord, ...]:
        triggered: list[MonitorTriggerRecord] = []
        subscriptions = self._repository.list_active_for_source(
            source_kind=envelope.source_kind.value,
            source_key=envelope.source_key,
        )
        for subscription in subscriptions:
            if not _rule_matches(subscription.rule, envelope):
                continue
            recorded = self._repository.record_matching_trigger(
                monitor_id=subscription.monitor_id,
                envelope=envelope,
            )
            if recorded is None:
                continue
            updated, trigger = recorded
            self._publish_monitor_event(
                record=updated,
                event_type=RunEventType.MONITOR_TRIGGERED,
                payload={
                    "monitor_id": updated.monitor_id,
                    "monitor_trigger_id": trigger.monitor_trigger_id,
                    "event_name": envelope.event_name,
                    "source_kind": envelope.source_kind.value,
                    "source_key": envelope.source_key,
                    "action_type": updated.action.action_type.value,
                },
            )
            self._dispatch_action(updated, envelope)
            if updated.status == MonitorSubscriptionStatus.STOPPED:
                self._publish_monitor_event(
                    record=updated,
                    event_type=RunEventType.MONITOR_STOPPED,
                    payload={"monitor_id": updated.monitor_id},
                )
            triggered.append(trigger)
        return tuple(triggered)

    def build_trigger_message(
        self,
        *,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
    ) -> str:
        attributes_json = json.dumps(
            envelope.attributes, ensure_ascii=False, sort_keys=True
        )
        return (
            "A managed monitor detected a matching event. "
            "Investigate the event and take the next best action.\n\n"
            "<monitor-event>\n"
            f"<monitor-id>{_xml_escape(subscription.monitor_id)}</monitor-id>\n"
            f"<source-kind>{_xml_escape(subscription.source_kind.value)}</source-kind>\n"
            f"<source-key>{_xml_escape(subscription.source_key)}</source-key>\n"
            f"<event-name>{_xml_escape(envelope.event_name)}</event-name>\n"
            f"<body>{_xml_escape(envelope.body_text)}</body>\n"
            f"<attributes>{_xml_escape(attributes_json)}</attributes>\n"
            "</monitor-event>"
        )

    def _dispatch_action(
        self,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
    ) -> None:
        if subscription.action.action_type == MonitorActionType.EMIT_NOTIFICATION:
            self._emit_notification(subscription, envelope)
            return
        if self._action_sink is None:
            return
        try:
            self._action_sink.handle_monitor_trigger(
                subscription=subscription,
                envelope=envelope,
                message=self.build_trigger_message(
                    subscription=subscription,
                    envelope=envelope,
                ),
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="monitor.action_dispatch_failed",
                message="Failed to dispatch monitor action",
                payload={"monitor_id": subscription.monitor_id},
                exc_info=exc,
            )

    def _emit_notification(
        self,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
    ) -> None:
        if self._notification_service is None:
            return
        self._notification_service.emit(
            notification_type=NotificationType.MONITOR_TRIGGERED,
            title="Monitor Triggered",
            body=f"{envelope.event_name}: {envelope.body_text or subscription.source_key}",
            dedupe_key=envelope.dedupe_key
            or f"{subscription.monitor_id}:{envelope.event_name}",
            context=NotificationContext(
                session_id=subscription.session_id,
                run_id=subscription.run_id,
                trace_id=subscription.run_id,
                instance_id=subscription.created_by_instance_id,
                role_id=subscription.created_by_role_id,
                tool_call_id=subscription.tool_call_id,
            ),
        )

    def _publish_monitor_event(
        self,
        *,
        record: MonitorSubscriptionRecord,
        event_type: RunEventType,
        payload: dict[str, str],
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
                instance_id=record.created_by_instance_id,
                role_id=record.created_by_role_id,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )


def _rule_matches(rule: MonitorRule, envelope: MonitorEventEnvelope) -> bool:
    if rule.event_names and envelope.event_name not in rule.event_names:
        return False
    body_text = (
        envelope.body_text if rule.case_sensitive else envelope.body_text.lower()
    )
    if rule.text_patterns_any:
        patterns = (
            rule.text_patterns_any
            if rule.case_sensitive
            else tuple(pattern.lower() for pattern in rule.text_patterns_any)
        )
        if not any(pattern in body_text for pattern in patterns):
            return False
    for key, expected in rule.attribute_equals.items():
        actual = envelope.attributes.get(key)
        if actual != expected:
            return False
    for key, expected_values in rule.attribute_in.items():
        actual = envelope.attributes.get(key)
        if actual not in expected_values:
            return False
    return True


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
