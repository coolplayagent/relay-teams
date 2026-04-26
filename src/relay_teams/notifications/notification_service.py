# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
from json import dumps
import logging
from typing import Protocol

from relay_teams.logger import get_logger, log_event
from relay_teams.notifications.models import (
    NotificationConfig,
    NotificationContext,
    NotificationRequest,
    NotificationType,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent

logger = get_logger(__name__)


class NotificationDispatcher(Protocol):
    def dispatch(self, request: NotificationRequest) -> None: ...


class NotificationService:
    def __init__(
        self,
        *,
        run_event_hub: RunEventHub,
        get_config: Callable[[], NotificationConfig],
        dispatchers: tuple[NotificationDispatcher, ...] = (),
    ) -> None:
        self._run_event_hub = run_event_hub
        self._get_config = get_config
        self._dispatchers = dispatchers

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
            feishu_format=rule.feishu_format,
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
        for dispatcher in self._dispatchers:
            try:
                dispatcher.dispatch(request)
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    event="notification.dispatch.failed",
                    message="Notification dispatcher failed",
                    payload={
                        "dispatcher": type(dispatcher).__name__,
                        "notification_type": request.notification_type.value,
                        "run_id": request.context.run_id,
                        "session_id": request.context.session_id,
                    },
                    exc_info=exc,
                )
                continue
        return True

    async def emit_async(
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
            feishu_format=rule.feishu_format,
            dedupe_key=dedupe_key or self._build_dedupe_key(notification_type, context),
            context=context,
        )
        await publish_run_event_async(
            self._run_event_hub,
            RunEvent(
                session_id=context.session_id,
                run_id=context.run_id,
                trace_id=context.trace_id,
                task_id=context.task_id,
                instance_id=context.instance_id,
                role_id=context.role_id,
                event_type=RunEventType.NOTIFICATION_REQUESTED,
                payload_json=dumps(request.model_dump(mode="json"), ensure_ascii=False),
            ),
        )
        for dispatcher in self._dispatchers:
            try:
                await asyncio.to_thread(dispatcher.dispatch, request)
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    event="notification.dispatch.failed",
                    message="Notification dispatcher failed",
                    payload={
                        "dispatcher": type(dispatcher).__name__,
                        "notification_type": request.notification_type.value,
                        "run_id": request.context.run_id,
                        "session_id": request.context.session_id,
                    },
                    exc_info=exc,
                )
                continue
        return True

    @staticmethod
    def _build_dedupe_key(
        notification_type: NotificationType,
        context: NotificationContext,
    ) -> str:
        if context.tool_call_id:
            return f"{notification_type.value}:{context.run_id}:{context.tool_call_id}"
        return f"{notification_type.value}:{context.run_id}"
