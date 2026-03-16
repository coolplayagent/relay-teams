# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from json import dumps

from agent_teams.notifications.models import (
    NotificationConfig,
    NotificationContext,
    NotificationRequest,
    NotificationType,
)
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent


class NotificationService:
    def __init__(
        self,
        *,
        run_event_hub: RunEventHub,
        get_config: Callable[[], NotificationConfig],
    ) -> None:
        self._run_event_hub = run_event_hub
        self._get_config = get_config

    def emit(
        self,
        *,
        notification_type: NotificationType,
        title: str,
        body: str,
        context: NotificationContext,
        dedupe_key: str | None = None,
    ) -> bool:
        config = self._get_config()
        rule = config.rule_for(notification_type)
        if not rule.enabled or not rule.channels:
            return False

        request = NotificationRequest(
            notification_type=notification_type,
            title=title,
            body=body,
            channels=rule.channels,
            dedupe_key=dedupe_key or self._build_dedupe_key(notification_type, context),
            context=context,
        )
        self._run_event_hub.publish(
            RunEvent(
                session_id=context.session_id,
                run_id=context.run_id,
                trace_id=context.trace_id,
                task_id=context.task_id,
                instance_id=context.instance_id,
                role_id=context.role_id,
                event_type=RunEventType.NOTIFICATION_REQUESTED,
                payload_json=dumps(request.model_dump(mode="json"), ensure_ascii=False),
            )
        )
        return True

    @staticmethod
    def _build_dedupe_key(
        notification_type: NotificationType,
        context: NotificationContext,
    ) -> str:
        if context.tool_call_id:
            return f"{notification_type.value}:{context.run_id}:{context.tool_call_id}"
        return f"{notification_type.value}:{context.run_id}"
