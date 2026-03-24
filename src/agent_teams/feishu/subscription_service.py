# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from threading import Event, Lock, Thread
from typing import Protocol

from lark_oapi.core.json import JSON
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1

from agent_teams.feishu.models import FeishuTriggerRuntimeConfig, TriggerProcessingResult
from agent_teams.feishu.trigger_handler import FeishuTriggerHandler
from agent_teams.logger import get_logger, log_event
from agent_teams.triggers import TriggerDefinition

logger = get_logger(__name__)


class TriggerServiceLike(Protocol):
    def list_triggers(self) -> tuple[TriggerDefinition, ...] | list[TriggerDefinition]: ...


class FeishuConfigServiceLike(Protocol):
    def list_enabled_runtime_configs(
        self,
        triggers: tuple[TriggerDefinition, ...] | list[TriggerDefinition],
    ) -> tuple[FeishuTriggerRuntimeConfig, ...]: ...


class EventRunnerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_alive(self) -> bool: ...


class EventRunnerFactory(Protocol):
    def __call__(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandler,
    ) -> EventRunnerLike: ...


class WsClientLike(Protocol):
    _auto_reconnect: bool

    async def _disconnect(self) -> None: ...


class FeishuSubscriptionService:
    def __init__(
        self,
        *,
        trigger_service: TriggerServiceLike,
        feishu_config_service: FeishuConfigServiceLike,
        event_handler: FeishuTriggerHandler,
        runner_factory: EventRunnerFactory | None = None,
    ) -> None:
        self._trigger_service = trigger_service
        self._feishu_config_service = feishu_config_service
        self._event_handler = event_handler
        self._runner_factory = (
            _create_ws_runner if runner_factory is None else runner_factory
        )
        self._lock = Lock()
        self._runners: dict[str, tuple[FeishuTriggerRuntimeConfig, EventRunnerLike]] = {}

    def start(self) -> None:
        self.reload()

    def stop(self) -> None:
        with self._lock:
            trigger_ids = tuple(self._runners.keys())
            for trigger_id in trigger_ids:
                self._stop_runner_locked(trigger_id=trigger_id, reason="shutdown")

    def reload(self) -> None:
        with self._lock:
            trigger_records = tuple(self._trigger_service.list_triggers())
            runtime_configs = self._feishu_config_service.list_enabled_runtime_configs(
                trigger_records
            )
            desired = {config.trigger_id: config for config in runtime_configs}
            current_ids = set(self._runners.keys())
            desired_ids = set(desired.keys())

            for trigger_id in sorted(current_ids - desired_ids):
                self._stop_runner_locked(
                    trigger_id=trigger_id,
                    reason="disabled_or_missing_credentials",
                )

            for trigger_id, runtime_config in desired.items():
                existing = self._runners.get(trigger_id)
                if existing is not None:
                    current_config, runner = existing
                    if runner.is_alive() and current_config.signature == runtime_config.signature:
                        continue
                    self._stop_runner_locked(trigger_id=trigger_id, reason="reload")
                runner = self._runner_factory(
                    runtime_config=runtime_config,
                    event_handler=self._event_handler,
                )
                runner.start()
                self._runners[trigger_id] = (runtime_config, runner)
                log_event(
                    logger,
                    logging.INFO,
                    event="feishu.subscription.started",
                    message="Feishu SDK subscription started",
                    payload={
                        "trigger_id": trigger_id,
                        "app_id": runtime_config.environment.app_id,
                    },
                )

    def _stop_runner_locked(self, *, trigger_id: str, reason: str) -> None:
        existing = self._runners.pop(trigger_id, None)
        if existing is None:
            return
        _runtime_config, runner = existing
        runner.stop()
        log_event(
            logger,
            logging.INFO,
            event="feishu.subscription.stopped",
            message="Feishu SDK subscription stopped",
            payload={"trigger_id": trigger_id, "reason": reason},
        )


class _FeishuWsRunner:
    def __init__(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandler,
    ) -> None:
        self._runtime_config = runtime_config
        self._event_handler = event_handler
        self._thread = Thread(
            target=self._run,
            name=f"feishu-sdk-subscription-{runtime_config.trigger_id}",
            daemon=True,
        )
        self._stop_event = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: WsClientLike | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        client = self._client
        if loop is not None and client is not None:
            try:
                client._auto_reconnect = False
                future = asyncio.run_coroutine_threadsafe(client._disconnect(), loop)
                future.result(timeout=5)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        self._thread.join(timeout=10)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        import lark_oapi as lark
        import lark_oapi.ws.client as ws_client_module
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.ws.client import Client as WsClient

        environment = self._runtime_config.environment
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_client_module.loop = loop
        self._loop = loop

        def _on_message(event: P2ImMessageReceiveV1) -> None:
            raw_body = JSON.marshal(event) or "{}"
            result = self._event_handler.handle_sdk_event(
                trigger_id=self._runtime_config.trigger_id,
                event=event,
                raw_body=raw_body,
                headers={},
                remote_addr=None,
            )
            _log_processing_result(result)

        dispatcher = (
            EventDispatcherHandler.builder(
                environment.encrypt_key or "",
                environment.verification_token or "",
            )
            .register_p2_im_message_receive_v1(_on_message)
            .build()
        )
        client = WsClient(
            environment.app_id,
            environment.app_secret,
            log_level=lark.LogLevel.INFO,
            event_handler=dispatcher,
        )
        self._client = client
        try:
            client.start()
        except RuntimeError as exc:
            if self._stop_event.is_set():
                return
            log_event(
                logger,
                logging.ERROR,
                event="feishu.subscription.runtime_error",
                message="Feishu SDK subscription loop stopped unexpectedly",
                payload={
                    "trigger_id": self._runtime_config.trigger_id,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                event="feishu.subscription.failed",
                message="Feishu SDK subscription failed",
                payload={
                    "trigger_id": self._runtime_config.trigger_id,
                    "error": str(exc),
                },
            )
        finally:
            try:
                loop.close()
            except RuntimeError:
                pass


def _create_ws_runner(
    *,
    runtime_config: FeishuTriggerRuntimeConfig,
    event_handler: FeishuTriggerHandler,
) -> EventRunnerLike:
    return _FeishuWsRunner(
        runtime_config=runtime_config,
        event_handler=event_handler,
    )


def _log_processing_result(result: TriggerProcessingResult) -> None:
    if result.ignored:
        log_event(
            logger,
            logging.DEBUG,
            event="feishu.subscription.event_ignored",
            message="Ignored Feishu SDK event",
            payload={
                "trigger_id": result.trigger_id,
                "event_id": result.event_id,
                "reason": result.reason,
            },
        )
        return
    log_event(
        logger,
        logging.INFO,
        event="feishu.subscription.event_processed",
        message="Processed Feishu SDK event",
        payload={
            "trigger_id": result.trigger_id,
            "event_id": result.event_id,
            "run_id": result.run_id,
            "session_id": result.session_id,
            "duplicate": result.duplicate,
        },
    )
