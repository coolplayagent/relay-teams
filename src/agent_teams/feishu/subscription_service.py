# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import random
from contextlib import suppress
from threading import Event, Lock, Thread
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse

from lark_oapi.core.json import JSON
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
from websockets.legacy.exceptions import InvalidStatusCode

from agent_teams.feishu.models import FeishuTriggerRuntimeConfig, TriggerProcessingResult
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


class FeishuTriggerHandlerLike(Protocol):
    def handle_sdk_event(
        self,
        *,
        trigger_id: str,
        event: P2ImMessageReceiveV1,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult: ...


class EventRunnerLike(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_alive(self) -> bool: ...


class EventRunnerFactory(Protocol):
    def __call__(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> EventRunnerLike: ...


@runtime_checkable
class ShutdownableRunnerFactory(Protocol):
    def shutdown(self) -> None: ...


class WsConnectionLike(Protocol):
    async def close(self) -> None: ...

    async def recv(self) -> bytes | str: ...


class WsClientLike(Protocol):
    _auto_reconnect: bool
    _ping_interval: int
    _reconnect_count: int
    _reconnect_interval: int
    _reconnect_nonce: int
    _conn: WsConnectionLike | None
    _conn_url: str
    _service_id: str
    _conn_id: str

    async def _disconnect(self) -> None: ...

    async def _handle_message(self, msg: bytes) -> None: ...

    async def _write_message(self, data: bytes) -> None: ...

    def _fmt_log(self, fmt: str, *args: object) -> str: ...

    def _get_conn_url(self) -> str: ...


class FeishuWsControllerLike(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def is_running(self) -> bool: ...


class FeishuWsControllerFactory(Protocol):
    def __call__(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> FeishuWsControllerLike: ...


class FeishuSubscriptionService:
    def __init__(
        self,
        *,
        trigger_service: TriggerServiceLike,
        feishu_config_service: FeishuConfigServiceLike,
        event_handler: FeishuTriggerHandlerLike,
        runner_factory: EventRunnerFactory | None = None,
    ) -> None:
        self._trigger_service = trigger_service
        self._feishu_config_service = feishu_config_service
        self._event_handler = event_handler
        self._runner_factory = (
            _SharedFeishuRunnerFactory()
            if runner_factory is None
            else runner_factory
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
        if isinstance(self._runner_factory, ShutdownableRunnerFactory):
            self._runner_factory.shutdown()

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
                    if (
                        runner.is_alive()
                        and current_config.signature == runtime_config.signature
                    ):
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


class _SharedFeishuRunnerFactory:
    def __init__(
        self,
        *,
        hub: _FeishuWsHub | None = None,
    ) -> None:
        self._hub = _FeishuWsHub() if hub is None else hub

    def __call__(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> EventRunnerLike:
        return _HubBackedRunner(
            hub=self._hub,
            runtime_config=runtime_config,
            event_handler=event_handler,
        )

    def shutdown(self) -> None:
        self._hub.shutdown()


class _HubBackedRunner:
    def __init__(
        self,
        *,
        hub: _FeishuWsHub,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> None:
        self._hub = hub
        self._runtime_config = runtime_config
        self._event_handler = event_handler

    def start(self) -> None:
        self._hub.start_client(
            runtime_config=self._runtime_config,
            event_handler=self._event_handler,
        )

    def stop(self) -> None:
        self._hub.stop_client(self._runtime_config.trigger_id)

    def is_alive(self) -> bool:
        return self._hub.is_client_active(self._runtime_config.trigger_id)


class _FeishuWsHub:
    def __init__(
        self,
        *,
        controller_factory: FeishuWsControllerFactory | None = None,
    ) -> None:
        self._controller_factory = (
            _create_ws_controller
            if controller_factory is None
            else controller_factory
        )
        self._controllers: dict[str, FeishuWsControllerLike] = {}
        self._lock = Lock()
        self._ready = Event()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start_client(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> None:
        with self._lock:
            self._ensure_thread_locked()
            loop = self._loop
        if loop is None:
            raise RuntimeError("Feishu subscription hub loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self._start_client_async(
                runtime_config=runtime_config,
                event_handler=event_handler,
            ),
            loop,
        )
        future.result(timeout=10)

    def stop_client(self, trigger_id: str) -> None:
        with self._lock:
            loop = self._loop
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self._stop_client_async(trigger_id),
            loop,
        )
        future.result(timeout=10)

    def is_client_active(self, trigger_id: str) -> bool:
        with self._lock:
            loop = self._loop
        if loop is None:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._is_client_active_async(trigger_id),
            loop,
        )
        return bool(future.result(timeout=5))

    def shutdown(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
        future.result(timeout=15)
        thread.join(timeout=15)
        with self._lock:
            self._thread = None
            self._loop = None
            self._ready.clear()

    async def _start_client_async(
        self,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> None:
        controller = self._controllers.get(runtime_config.trigger_id)
        if controller is None:
            controller = self._controller_factory(
                runtime_config=runtime_config,
                event_handler=event_handler,
            )
            self._controllers[runtime_config.trigger_id] = controller
        await controller.start()

    async def _stop_client_async(self, trigger_id: str) -> None:
        controller = self._controllers.pop(trigger_id, None)
        if controller is None:
            return
        await controller.stop()

    async def _is_client_active_async(self, trigger_id: str) -> bool:
        controller = self._controllers.get(trigger_id)
        return controller is not None and controller.is_running()

    async def _shutdown_async(self) -> None:
        trigger_ids = tuple(self._controllers.keys())
        for trigger_id in trigger_ids:
            controller = self._controllers.pop(trigger_id, None)
            if controller is not None:
                await controller.stop()
        asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        thread = Thread(
            target=self._run_loop,
            name="feishu-sdk-subscription-hub",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Timed out waiting for Feishu subscription hub loop")

    def _run_loop(self) -> None:
        import lark_oapi.ws.client as ws_client_module

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_client_module.loop = loop
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = tuple(task for task in asyncio.all_tasks(loop) if not task.done())
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()


class _FeishuWsController:
    def __init__(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: FeishuTriggerHandlerLike,
    ) -> None:
        self._runtime_config = runtime_config
        self._event_handler = event_handler
        self._client: WsClientLike | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_requested = False
        self._task = asyncio.create_task(
            self._run(),
            name=f"feishu-sdk-subscription-{self._runtime_config.trigger_id}",
        )

    async def stop(self) -> None:
        self._stop_requested = True
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        from lark_oapi.ws.exception import ClientException

        reconnect_attempt = 0
        while not self._stop_requested:
            try:
                client = self._build_client()
                self._client = client
                await self._connect_client(client)
                reconnect_attempt = 0
                await self._run_connected_client(client)
            except asyncio.CancelledError:
                raise
            except ClientException as exc:
                if self._stop_requested:
                    return
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
                return
            except Exception as exc:
                if self._stop_requested:
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
            finally:
                await self._disconnect_client()
                self._client = None
            if self._stop_requested:
                return
            reconnect_attempt += 1
            delay = self._resolve_reconnect_delay(reconnect_attempt)
            if delay is None:
                return
            await asyncio.sleep(delay)

    def _build_client(self) -> WsClientLike:
        import lark_oapi as lark
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.ws.client import Client as WsClient

        environment = self._runtime_config.environment

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
        return WsClient(
            environment.app_id,
            environment.app_secret,
            log_level=lark.LogLevel.INFO,
            event_handler=dispatcher,
        )

    async def _connect_client(self, client: WsClientLike) -> None:
        import lark_oapi.ws.client as ws_client_module
        from lark_oapi.ws.const import DEVICE_ID, SERVICE_ID

        conn_url = client._get_conn_url()
        conn_query = parse_qs(urlparse(conn_url).query)
        conn_ids = conn_query.get(DEVICE_ID)
        service_ids = conn_query.get(SERVICE_ID)
        if not conn_ids or not service_ids:
            raise RuntimeError("Feishu websocket connection metadata is incomplete")
        try:
            connection = await ws_client_module.websockets.connect(conn_url)
        except InvalidStatusCode as exc:
            ws_client_module._parse_ws_conn_exception(exc)
            raise
        client._conn = connection
        client._conn_url = conn_url
        client._conn_id = conn_ids[0]
        client._service_id = service_ids[0]
        ws_client_module.logger.info(client._fmt_log("connected to {}", conn_url))

    async def _run_connected_client(self, client: WsClientLike) -> None:
        receive_task = asyncio.create_task(
            self._receive_loop(client),
            name=f"feishu-sdk-receive-{self._runtime_config.trigger_id}",
        )
        ping_task = asyncio.create_task(
            self._ping_loop(client),
            name=f"feishu-sdk-ping-{self._runtime_config.trigger_id}",
        )
        done, pending = await asyncio.wait(
            {receive_task, ping_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
        raise RuntimeError("Feishu websocket loop exited unexpectedly")

    async def _receive_loop(self, client: WsClientLike) -> None:
        while not self._stop_requested:
            connection = client._conn
            if connection is None:
                raise RuntimeError("Feishu websocket connection is not available")
            message = await connection.recv()
            if isinstance(message, str):
                message = message.encode("utf-8")
            await client._handle_message(message)

    async def _ping_loop(self, client: WsClientLike) -> None:
        import lark_oapi.ws.client as ws_client_module

        while not self._stop_requested:
            try:
                if client._conn is not None and client._service_id:
                    frame = ws_client_module._new_ping_frame(int(client._service_id))
                    await client._write_message(frame.SerializeToString())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    event="feishu.subscription.ping_failed",
                    message="Feishu SDK ping failed",
                    payload={
                        "trigger_id": self._runtime_config.trigger_id,
                        "error": str(exc),
                    },
                )
            await asyncio.sleep(max(client._ping_interval, 1))

    async def _disconnect_client(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client._auto_reconnect = False
            await client._disconnect()
        except Exception:
            return

    def _resolve_reconnect_delay(self, attempt: int) -> float | None:
        client = self._client
        if client is None:
            return None
        if client._reconnect_count >= 0 and attempt > client._reconnect_count:
            return None
        if attempt == 1 and client._reconnect_nonce > 0:
            return random.random() * client._reconnect_nonce
        return float(max(client._reconnect_interval, 1))


def _create_ws_controller(
    *,
    runtime_config: FeishuTriggerRuntimeConfig,
    event_handler: FeishuTriggerHandlerLike,
) -> FeishuWsControllerLike:
    return _FeishuWsController(
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
