# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from json import dumps
import logging
import threading
from typing import Protocol

from pydantic import JsonValue

from relay_teams.hooks import HookDecisionBundle, HookEventName, HookEventInput
from relay_teams.hooks.hook_event_models import NotificationInput
from relay_teams.logger import get_logger, log_event
from relay_teams.notifications.models import (
    NotificationConfig,
    NotificationContext,
    NotificationRequest,
    NotificationType,
)
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent

logger = get_logger(__name__)
_NOTIFICATION_HOOK_ACTIVE: ContextVar[bool] = ContextVar(
    "relay_teams_notification_hook_active",
    default=False,
)


class NotificationDispatcher(Protocol):
    def dispatch(self, request: NotificationRequest) -> None:
        raise NotImplementedError


class NotificationHookService(Protocol):
    async def execute(
        self,
        *,
        event_input: HookEventInput,
        run_event_hub: RunEventHub | None,
    ) -> HookDecisionBundle:
        raise NotImplementedError


class NotificationInjectionManager(Protocol):
    def is_active(self, run_id: str) -> bool:
        raise NotImplementedError

    def enqueue(
        self,
        run_id: str,
        recipient_instance_id: str,
        *,
        source: InjectionSource,
        content: str,
    ) -> object:
        raise NotImplementedError


class _NotificationHookLoop:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="relay-teams-notification-hooks",
            daemon=True,
        )
        self._thread.start()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        self._ready.wait()
        loop = self._loop
        if loop is None:
            raise RuntimeError("Notification hook loop was not initialized.")
        return loop

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()


class NotificationService:
    def __init__(
        self,
        *,
        run_event_hub: RunEventHub,
        get_config: Callable[[], NotificationConfig],
        dispatchers: tuple[NotificationDispatcher, ...] = (),
        hook_service: NotificationHookService | None = None,
        injection_manager: NotificationInjectionManager | None = None,
    ) -> None:
        self._run_event_hub = run_event_hub
        self._get_config = get_config
        self._dispatchers = dispatchers
        self._hook_service = hook_service
        self._injection_manager = injection_manager
        self._hook_loop: _NotificationHookLoop | None = None
        self._hook_loop_lock = threading.Lock()
        self._active_loop_hook_tasks: set[asyncio.Task[None]] = set()

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
        self._emit_notification_hook(request)
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

    def _emit_notification_hook(self, request: NotificationRequest) -> None:
        if self._hook_service is None:
            return
        if _NOTIFICATION_HOOK_ACTIVE.get():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._run_event_hub.loop_for_run(request.context.run_id)
            if loop is None or not loop.is_running():
                loop = self._get_hook_loop().loop
            future = asyncio.run_coroutine_threadsafe(
                self._run_notification_hook(request),
                loop,
            )
            _ = future.result()
            return
        task = loop.create_task(self._run_notification_hook(request))
        self._active_loop_hook_tasks.add(task)
        task.add_done_callback(self._active_loop_hook_tasks.discard)

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
        await self._emit_notification_hook_async(request)
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

    async def _emit_notification_hook_async(self, request: NotificationRequest) -> None:
        if self._hook_service is None:
            return
        if _NOTIFICATION_HOOK_ACTIVE.get():
            return
        await self._run_notification_hook(request)

    def _get_hook_loop(self) -> _NotificationHookLoop:
        hook_loop = self._hook_loop
        if hook_loop is not None:
            return hook_loop
        with self._hook_loop_lock:
            if self._hook_loop is None:
                self._hook_loop = _NotificationHookLoop()
            return self._hook_loop

    async def _run_notification_hook(self, request: NotificationRequest) -> None:
        hook_service = self._hook_service
        if hook_service is None:
            return
        token = _NOTIFICATION_HOOK_ACTIVE.set(True)
        try:
            bundle = await hook_service.execute(
                event_input=NotificationInput(
                    event_name=HookEventName.NOTIFICATION,
                    session_id=request.context.session_id,
                    run_id=request.context.run_id,
                    trace_id=request.context.trace_id,
                    task_id=request.context.task_id,
                    instance_id=request.context.instance_id,
                    role_id=request.context.role_id,
                    session_mode=request.context.session_mode,
                    run_kind=request.context.run_kind,
                    notification_type=request.notification_type.value,
                    title=request.title,
                    body=request.body,
                    channels=tuple(channel.value for channel in request.channels),
                    dedupe_key=request.dedupe_key,
                    tool_call_id=request.context.tool_call_id,
                    tool_name=request.context.tool_name,
                ),
                run_event_hub=self._run_event_hub,
            )
            self._enqueue_additional_context(
                request=request,
                contexts=bundle.additional_context,
            )
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                event="notification.hook.failed",
                message="Notification hook failed",
                payload=_notification_hook_failure_payload(request),
                exc_info=exc,
            )
        finally:
            _NOTIFICATION_HOOK_ACTIVE.reset(token)

    def _enqueue_additional_context(
        self,
        *,
        request: NotificationRequest,
        contexts: tuple[str, ...],
    ) -> None:
        if self._injection_manager is None or not request.context.instance_id:
            return
        if not self._injection_manager.is_active(request.context.run_id):
            return
        content = "\n\n".join(item.strip() for item in contexts if item.strip())
        if not content:
            return
        _ = self._injection_manager.enqueue(
            request.context.run_id,
            request.context.instance_id,
            source=InjectionSource.SYSTEM,
            content=content,
        )

    @staticmethod
    def _build_dedupe_key(
        notification_type: NotificationType,
        context: NotificationContext,
    ) -> str:
        if context.tool_call_id:
            return f"{notification_type.value}:{context.run_id}:{context.tool_call_id}"
        return f"{notification_type.value}:{context.run_id}"


def _notification_hook_failure_payload(
    request: NotificationRequest,
) -> dict[str, JsonValue]:
    return {
        "notification_type": request.notification_type.value,
        "run_id": request.context.run_id,
        "session_id": request.context.session_id,
        "dedupe_key": request.dedupe_key,
    }
