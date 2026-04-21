# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import random
import ssl
from contextlib import suppress
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, NoReturn, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse

import httpx
from websockets.exceptions import ConnectionClosedOK, InvalidStatus

from relay_teams.env.proxy_env import (
    load_proxy_env_config,
    proxy_applies_to_url,
    resolve_ssl_verify,
)
from relay_teams.gateway.feishu.lark_ws_compat import (
    import_lark_module,
    import_lark_ws_client_module,
)
from relay_teams.gateway.feishu.models import (
    FeishuTriggerRuntimeConfig,
    TriggerProcessingResult,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.net import create_sync_http_client

logger = get_logger(__name__)

if TYPE_CHECKING:
    from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
    from lark_oapi.ws.model import ClientConfig


class FeishuRuntimeConfigLookup(Protocol):
    def list_enabled_runtime_configs(
        self,
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


@runtime_checkable
class HeadersLike(Protocol):
    def get(self, key: str) -> str | None: ...


class WsClientLike(Protocol):
    _app_id: str
    _app_secret: str
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

    def _configure(self, conf: ClientConfig) -> None: ...


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
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        event_handler: FeishuTriggerHandlerLike,
        runner_factory: EventRunnerFactory | None = None,
    ) -> None:
        self._runtime_config_lookup = runtime_config_lookup
        self._event_handler = event_handler
        self._runner_factory = (
            _SharedFeishuRunnerFactory() if runner_factory is None else runner_factory
        )
        self._lock = Lock()
        self._runners: dict[
            str, tuple[FeishuTriggerRuntimeConfig, EventRunnerLike]
        ] = {}

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
            runtime_configs = self._runtime_config_lookup.list_enabled_runtime_configs()
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
                try:
                    runner = self._runner_factory(
                        runtime_config=runtime_config,
                        event_handler=self._event_handler,
                    )
                    runner.start()
                except Exception as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        event="feishu.subscription.start_failed",
                        message="Feishu SDK subscription failed to start",
                        payload={
                            "trigger_id": trigger_id,
                            "app_id": runtime_config.environment.app_id,
                        },
                        exc_info=exc,
                    )
                    continue
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
            _create_ws_controller if controller_factory is None else controller_factory
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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_client_module = import_lark_ws_client_module()
        setattr(ws_client_module, "loop", loop)
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
        lark = import_lark_module("lark_oapi")
        dispatcher_module = import_lark_module("lark_oapi.event.dispatcher_handler")
        EventDispatcherHandler = dispatcher_module.EventDispatcherHandler

        ws_client_module = import_lark_ws_client_module()
        WsClient = ws_client_module.Client

        environment = self._runtime_config.environment

        def _on_message(event: P2ImMessageReceiveV1) -> None:
            json_module = import_lark_module("lark_oapi.core.json")
            JSON = json_module.JSON

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
        from lark_oapi.ws.const import DEVICE_ID, SERVICE_ID

        ws_client_module = import_lark_ws_client_module()
        conn_url = await asyncio.to_thread(self._get_conn_url, client)
        conn_query = parse_qs(urlparse(conn_url).query)
        conn_ids = conn_query.get(DEVICE_ID)
        service_ids = conn_query.get(SERVICE_ID)
        if not conn_ids or not service_ids:
            raise RuntimeError("Feishu websocket connection metadata is incomplete")
        connection: WsConnectionLike
        try:
            connection = await ws_client_module.websockets.connect(
                conn_url,
                proxy=_resolve_websocket_proxy_url(conn_url),
                ssl=_build_websocket_ssl_context(conn_url),
            )
        except InvalidStatus as exc:
            _parse_ws_conn_exception(exc)
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
        try:
            while not self._stop_requested:
                connection = client._conn
                if connection is None:
                    raise RuntimeError("Feishu websocket connection is not available")
                message = await connection.recv()
                if isinstance(message, str):
                    message = message.encode("utf-8")
                await client._handle_message(message)
        except ConnectionClosedOK:
            if self._stop_requested:
                return
            raise

    async def _ping_loop(self, client: WsClientLike) -> None:
        ws_client_module = import_lark_ws_client_module()
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

    def _get_conn_url(self, client: WsClientLike) -> str:
        from lark_oapi.ws.const import GEN_ENDPOINT_URI
        from lark_oapi.ws.exception import ClientException, ServerException
        from lark_oapi.ws.model import EndpointResp

        response = self._create_feishu_http_client().post(
            f"https://open.feishu.cn{GEN_ENDPOINT_URI}",
            headers={"locale": "zh"},
            json={
                "AppID": client._app_id,
                "AppSecret": client._app_secret,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ServerException(
                response.status_code,
                response.text.strip() or "system busy",
            ) from exc
        response_json = _require_json_object(
            response.json(),
            error_context="load Feishu websocket endpoint",
        )
        endpoint = EndpointResp(response_json)
        endpoint_code = int(endpoint.code or 0)
        endpoint_message = str(endpoint.msg or "").strip()
        if endpoint_code == 0:
            pass
        elif endpoint_code in (1, 1000040343):
            raise ServerException(endpoint_code, endpoint_message or "system busy")
        else:
            raise ClientException(endpoint_code, endpoint_message or "unknown error")
        endpoint_data = endpoint.data
        if endpoint_data is None or not endpoint_data.URL:
            raise RuntimeError("Feishu websocket endpoint response missing URL")
        if endpoint_data.ClientConfig is not None:
            client._configure(endpoint_data.ClientConfig)
        return endpoint_data.URL

    def _create_feishu_http_client(self) -> httpx.Client:
        return create_sync_http_client(proxy_config=load_proxy_env_config())


def _create_ws_controller(
    *,
    runtime_config: FeishuTriggerRuntimeConfig,
    event_handler: FeishuTriggerHandlerLike,
) -> FeishuWsControllerLike:
    return _FeishuWsController(
        runtime_config=runtime_config,
        event_handler=event_handler,
    )


def _require_json_object(
    value: object,
    *,
    error_context: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{error_context}: invalid JSON response")
    return dict(value.items())


def _build_websocket_ssl_context(url: str) -> ssl.SSLContext | None:
    if not url.startswith("wss://"):
        return None
    ssl_context = ssl.create_default_context()
    if resolve_ssl_verify(proxy_config=load_proxy_env_config()):
        return ssl_context
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


def _resolve_websocket_proxy_url(url: str) -> str | None:
    proxy_config = load_proxy_env_config()
    if not proxy_applies_to_url(_httpish_url_for_websocket(url), proxy_config):
        return None
    if url.startswith("wss://"):
        return (
            proxy_config.https_proxy
            or proxy_config.http_proxy
            or proxy_config.all_proxy
        )
    if url.startswith("ws://"):
        return proxy_config.http_proxy or proxy_config.all_proxy
    return None


def _httpish_url_for_websocket(url: str) -> str:
    if url.startswith("wss://"):
        return f"https://{url.removeprefix('wss://')}"
    if url.startswith("ws://"):
        return f"http://{url.removeprefix('ws://')}"
    return url


def _resolve_ws_exception_headers(exc: Exception) -> HeadersLike | None:
    response = getattr(exc, "response", None)
    response_headers = getattr(response, "headers", None)
    if isinstance(response_headers, HeadersLike):
        return response_headers

    direct_headers = getattr(exc, "headers", None)
    if isinstance(direct_headers, HeadersLike):
        return direct_headers
    return None


def _parse_ws_conn_exception(exc: Exception) -> NoReturn:
    from lark_oapi.ws.const import (
        AUTH_FAILED,
        EXCEED_CONN_LIMIT,
        FORBIDDEN,
        HEADER_HANDSHAKE_AUTH_ERRCODE,
        HEADER_HANDSHAKE_MSG,
        HEADER_HANDSHAKE_STATUS,
    )
    from lark_oapi.ws.exception import ClientException, ServerException

    headers = _resolve_ws_exception_headers(exc)
    if headers is None:
        raise exc

    code = headers.get(HEADER_HANDSHAKE_STATUS)
    msg = headers.get(HEADER_HANDSHAKE_MSG)
    if code is None or msg is None:
        raise exc

    status_code = int(code)
    if status_code == AUTH_FAILED:
        auth_code = headers.get(HEADER_HANDSHAKE_AUTH_ERRCODE)
        if auth_code is not None and int(auth_code) == EXCEED_CONN_LIMIT:
            raise ClientException(status_code, msg)
        raise ServerException(status_code, msg)
    if status_code == FORBIDDEN:
        raise ClientException(status_code, msg)
    raise ServerException(status_code, msg)


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
