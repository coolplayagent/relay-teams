# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from threading import Event, Lock, Thread
from typing import Callable, Protocol

from lark_oapi.core.json import JSON
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1

from agent_teams.feishu.client import load_feishu_environment
from agent_teams.feishu.models import FeishuEnvironment, TriggerProcessingResult
from agent_teams.feishu.trigger_handler import FeishuTriggerHandler
from agent_teams.logger import get_logger, log_event

logger = get_logger(__name__)


class EventRunnerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_alive(self) -> bool: ...


class EventRunnerFactory(Protocol):
    def __call__(
        self,
        *,
        environment: FeishuEnvironment,
        event_handler: FeishuTriggerHandler,
    ) -> EventRunnerLike: ...


class WsClientLike(Protocol):
    _auto_reconnect: bool

    async def _disconnect(self) -> None: ...


class FeishuSubscriptionService:
    def __init__(
        self,
        *,
        event_handler: FeishuTriggerHandler,
        environment_loader: Callable[
            [], FeishuEnvironment | None
        ] = load_feishu_environment,
        runner_factory: EventRunnerFactory | None = None,
    ) -> None:
        self._event_handler = event_handler
        self._environment_loader = environment_loader
        self._runner_factory = (
            _create_ws_runner if runner_factory is None else runner_factory
        )
        self._lock = Lock()
        self._runner: EventRunnerLike | None = None
        self._signature: tuple[str, str, str | None] | None = None

    def start(self) -> None:
        self.reload()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked(reason="shutdown")

    def reload(self) -> None:
        with self._lock:
            environment = self._environment_loader()
            if environment is None:
                self._stop_locked(reason="missing_credentials")
                return
            if not self._event_handler.has_enabled_feishu_trigger():
                self._stop_locked(reason="no_enabled_trigger")
                return
            signature = (
                environment.app_id,
                environment.app_secret,
                environment.encrypt_key,
            )
            if (
                self._runner is not None
                and self._runner.is_alive()
                and self._signature == signature
            ):
                return
            self._stop_locked(reason="reload")
            runner = self._runner_factory(
                environment=environment,
                event_handler=self._event_handler,
            )
            runner.start()
            self._runner = runner
            self._signature = signature
            log_event(
                logger,
                logging.INFO,
                event="feishu.subscription.started",
                message="Feishu SDK subscription started",
                payload={"app_id": environment.app_id},
            )

    def _stop_locked(self, *, reason: str) -> None:
        runner = self._runner
        self._runner = None
        self._signature = None
        if runner is None:
            return
        runner.stop()
        log_event(
            logger,
            logging.INFO,
            event="feishu.subscription.stopped",
            message="Feishu SDK subscription stopped",
            payload={"reason": reason},
        )


class _FeishuWsRunner:
    def __init__(
        self,
        *,
        environment: FeishuEnvironment,
        event_handler: FeishuTriggerHandler,
    ) -> None:
        self._environment = environment
        self._event_handler = event_handler
        self._thread = Thread(
            target=self._run,
            name="feishu-sdk-subscription",
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

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_client_module.loop = loop
        self._loop = loop

        def _on_message(event: P2ImMessageReceiveV1) -> None:
            raw_body = JSON.marshal(event) or "{}"
            result = self._event_handler.handle_sdk_event(
                event=event,
                raw_body=raw_body,
                headers={},
                remote_addr=None,
            )
            _log_processing_result(result)

        dispatcher = (
            EventDispatcherHandler.builder(
                self._environment.encrypt_key or "",
                self._environment.verification_token or "",
            )
            .register_p2_im_message_receive_v1(_on_message)
            .build()
        )
        client = WsClient(
            self._environment.app_id,
            self._environment.app_secret,
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
                payload={"error": str(exc)},
            )
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                event="feishu.subscription.failed",
                message="Feishu SDK subscription failed",
                payload={"error": str(exc)},
            )
        finally:
            try:
                loop.close()
            except RuntimeError:
                pass


def _create_ws_runner(
    *,
    environment: FeishuEnvironment,
    event_handler: FeishuTriggerHandler,
) -> EventRunnerLike:
    return _FeishuWsRunner(environment=environment, event_handler=event_handler)


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
