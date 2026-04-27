# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import BinaryIO
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.env import get_env_var
from relay_teams.gateway.acp_mcp_relay import AcpMcpRelay
from relay_teams.gateway.gateway_model_profile_override import (
    GatewayModelProfileOverride,
)
from relay_teams.gateway.gateway_models import GatewayChannelType, GatewayMcpServerSpec
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionBusyError,
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import (
    ContentPart,
    ContentPartAdapter,
    MediaAssetService,
    MediaModality,
    MediaRefContentPart,
    TextContentPart,
    infer_media_modality,
)
from relay_teams.metrics import MetricRecorder
from relay_teams.metrics.adapters import record_gateway_operation
from relay_teams.sessions.session_service import SessionService
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.run_models import IntentInput, RunEvent


type JsonRpcId = str | int

type AcpNotifier = Callable[[dict[str, JsonValue]], Awaitable[None]]


LOGGER = get_logger(__name__)


class AcpProtocolError(ValueError):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        status: str = "protocol_error",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class _AcpRunStopResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_reason: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    run_status: str = Field(min_length=1)
    recoverable: bool = False
    error_message: str | None = None
    clear_active_run: bool = True


class _AcpRequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cold_start: bool = False
    framed_input: bool | None = None
    runtime_uptime_ms: int | None = None


class _AcpRequestObserver:
    def __init__(
        self,
        *,
        metric_recorder: MetricRecorder | None,
        operation: str,
        method: str,
        message_id: JsonRpcId | None,
        params: Mapping[str, JsonValue],
        request_context: _AcpRequestContext | None,
    ) -> None:
        self._metric_recorder = metric_recorder
        self._operation = operation
        self._method = method
        self._message_id = message_id
        self._request_context = request_context or _AcpRequestContext()
        self._started_at = time.perf_counter()
        self._first_update_recorded = False
        self._workspace_id = ""
        self._internal_session_id = ""
        self._run_id = ""
        self._gateway_session_id = _optional_str(params, "sessionId") or ""
        self._connection_id = _optional_str(params, "connectionId") or ""
        self._server_id = (
            _optional_str(params, "acpId") or _optional_str(params, "serverId") or ""
        )
        self._gateway_transport = "stdio"
        prompt_blocks = params.get("prompt")
        self._prompt_block_count = (
            len(prompt_blocks) if isinstance(prompt_blocks, list) else 0
        )

    @property
    def operation(self) -> str:
        return self._operation

    @property
    def gateway_session_id(self) -> str:
        return self._gateway_session_id

    @property
    def internal_session_id(self) -> str:
        return self._internal_session_id

    @property
    def run_id(self) -> str:
        return self._run_id

    def bind_gateway_session_id(self, gateway_session_id: str) -> None:
        self._gateway_session_id = gateway_session_id

    def bind_internal_session_id(self, internal_session_id: str) -> None:
        self._internal_session_id = internal_session_id

    def bind_workspace_id(self, workspace_id: str) -> None:
        self._workspace_id = workspace_id

    def bind_run_id(self, run_id: str) -> None:
        self._run_id = run_id

    def bind_connection_id(self, connection_id: str) -> None:
        self._connection_id = connection_id

    def bind_server_id(self, server_id: str) -> None:
        self._server_id = server_id

    def bind_transport(self, gateway_transport: str) -> None:
        normalized = gateway_transport.strip()
        if normalized:
            self._gateway_transport = normalized

    def record_request_completed(self, *, status: str) -> None:
        self._record(
            phase="request",
            status=status,
            event="gateway.acp.request.completed",
            message="ACP gateway request completed",
        )

    def record_request_failed(
        self,
        *,
        status: str,
        message: str,
        exc: Exception | None = None,
    ) -> None:
        self._record(
            phase="request",
            status=status,
            event="gateway.acp.request.failed",
            message=message,
            exc=exc,
        )

    def record_prompt_run_start(self) -> None:
        if self._operation != "session_prompt":
            return
        self._record(
            phase="run_start",
            status="success",
            event="gateway.acp.request.completed",
            message="ACP prompt run started",
        )

    def record_prompt_first_update(self) -> None:
        if self._operation != "session_prompt" or self._first_update_recorded:
            return
        self._first_update_recorded = True
        self._record(
            phase="first_update",
            status="success",
            event="gateway.acp.prompt.first_update",
            message="ACP prompt emitted first agent update",
        )

    def _record(
        self,
        *,
        phase: str,
        status: str,
        event: str,
        message: str,
        exc: Exception | None = None,
    ) -> None:
        duration_ms = int((time.perf_counter() - self._started_at) * 1000)
        payload = self._payload(phase=phase, status=status)
        log_event(
            LOGGER,
            _gateway_log_level(status),
            event=event,
            message=message,
            payload=payload,
            duration_ms=duration_ms,
            exc_info=exc,
        )
        if self._metric_recorder is None:
            return
        record_gateway_operation(
            self._metric_recorder,
            workspace_id=self._workspace_id,
            session_id=self._internal_session_id,
            run_id=self._run_id,
            gateway_channel="acp_stdio",
            gateway_operation=self._operation,
            gateway_phase=phase,
            gateway_transport=self._gateway_transport,
            status=status,
            cold_start=self._request_context.cold_start,
            duration_ms=duration_ms,
        )

    def _payload(self, *, phase: str, status: str) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "method": self._method,
            "gateway_operation": self._operation,
            "gateway_phase": phase,
            "gateway_channel": "acp_stdio",
            "gateway_transport": self._gateway_transport,
            "status": status,
            "cold_start": self._request_context.cold_start,
        }
        if self._request_context.framed_input is not None:
            payload["framed_input"] = self._request_context.framed_input
        if self._request_context.runtime_uptime_ms is not None:
            payload["runtime_uptime_ms"] = self._request_context.runtime_uptime_ms
        if self._message_id is not None:
            payload["message_id"] = self._message_id
        if self._gateway_session_id:
            payload["gateway_session_id"] = self._gateway_session_id
        if self._internal_session_id:
            payload["session_id"] = self._internal_session_id
        if self._run_id:
            payload["run_id"] = self._run_id
        if self._connection_id:
            payload["connection_id"] = self._connection_id
        if self._server_id:
            payload["server_id"] = self._server_id
        if self._prompt_block_count > 0:
            payload["prompt_block_count"] = self._prompt_block_count
        return payload


class _ResumeTextSuppressor:
    def __init__(self, prefix: str) -> None:
        self._remaining = prefix

    def strip(self, text: str) -> str:
        if not text or not self._remaining:
            return text
        max_common = min(len(self._remaining), len(text))
        matched = 0
        while matched < max_common and self._remaining[matched] == text[matched]:
            matched += 1
        if matched == 0:
            self._remaining = ""
            return text
        self._remaining = self._remaining[matched:]
        remainder = text[matched:]
        if remainder and self._remaining:
            self._remaining = ""
        return remainder


class AcpGatewayServer:
    def __init__(
        self,
        *,
        gateway_session_service: GatewaySessionService,
        session_service: SessionService,
        run_service: SessionRunService,
        media_asset_service: MediaAssetService,
        notify: AcpNotifier,
        mcp_relay: AcpMcpRelay | None = None,
        session_ingress_service: GatewaySessionIngressService | None = None,
        metric_recorder: MetricRecorder | None = None,
    ) -> None:
        self._gateway_session_service = gateway_session_service
        self._session_service = session_service
        self._run_service = run_service
        self._media_asset_service = media_asset_service
        self._notify = notify
        self._metric_recorder = metric_recorder
        self._active_runs: dict[str, str] = {}
        self._zed_compat_mode = False
        self._mcp_relay = mcp_relay or AcpMcpRelay(metric_recorder=metric_recorder)
        self._session_ingress_service = session_ingress_service

    def set_notify(self, notify: AcpNotifier) -> None:
        self._notify = notify

    def set_zed_compat_mode(self, enabled: bool) -> None:
        self._zed_compat_mode = enabled

    def set_mcp_relay_outbound(
        self,
        *,
        send_request: Callable[
            [str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]
        ],
        send_notification: AcpNotifier,
    ) -> None:
        self._mcp_relay.set_outbound(
            send_request=send_request,
            send_notification=send_notification,
        )

    def _build_request_observer(
        self,
        *,
        method: str,
        message_id: JsonRpcId | None,
        params: dict[str, JsonValue],
        request_context: _AcpRequestContext | None,
    ) -> _AcpRequestObserver | None:
        operation = _tracked_gateway_operation(method)
        if operation is None:
            return None
        return _AcpRequestObserver(
            metric_recorder=self._metric_recorder,
            operation=operation,
            method=method,
            message_id=message_id,
            params=params,
            request_context=request_context,
        )

    def _bind_gateway_session_observer(
        self,
        observer: _AcpRequestObserver | None,
        *,
        gateway_session_id: str,
    ) -> None:
        if observer is None:
            return
        observer.bind_gateway_session_id(gateway_session_id)
        try:
            record = self._gateway_session_service.get_session(gateway_session_id)
        except KeyError:
            return
        observer.bind_internal_session_id(record.internal_session_id)
        try:
            session = self._session_service.get_session(record.internal_session_id)
        except KeyError:
            return
        observer.bind_workspace_id(session.workspace_id)

    async def handle_jsonrpc_message(
        self,
        message: dict[str, JsonValue],
        request_context: _AcpRequestContext | None = None,
    ) -> dict[str, JsonValue] | None:
        message_id = _optional_id(message)
        method = _required_method(message)
        params = _params_object(message)
        observer = self._build_request_observer(
            method=method,
            message_id=message_id,
            params=params,
            request_context=request_context,
        )

        try:
            if message_id is None:
                await self._handle_notification(method, params, observer=observer)
                if observer is not None:
                    observer.record_request_completed(status="success")
                return None
            result = await self._handle_request(
                method,
                params,
                message_id,
                observer=observer,
            )
            if observer is not None:
                observer.record_request_completed(
                    status=_request_status_from_result(method=method, result=result)
                )
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": result,
            }
        except AcpProtocolError as exc:
            if observer is not None:
                observer.record_request_failed(
                    status=exc.status,
                    message="ACP gateway request failed",
                    exc=exc,
                )
            if message_id is None:
                return None
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }
        except Exception as exc:
            if observer is not None:
                observer.record_request_failed(
                    status="internal_error",
                    message="ACP gateway request failed",
                    exc=exc,
                )
            if message_id is None:
                return None
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32000,
                    "message": str(exc) or exc.__class__.__name__,
                },
            }

    async def _handle_request(
        self,
        method: str,
        params: dict[str, JsonValue],
        message_id: JsonRpcId,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        if method == "initialize":
            return self._initialize_result(params)
        if method == "session/new":
            return self._create_session(params, observer=observer)
        if method == "session/load":
            return await self._load_session(params, observer=observer)
        if method == "session/prompt":
            return await self._prompt_session(params, message_id, observer=observer)
        if method == "session/resume":
            return await self._resume_session(params, observer=observer)
        if method == "session/cancel":
            return await self._cancel_session(params, observer=observer)
        if method == "mcp/connect":
            return await self._mcp_connect(params, observer=observer)
        if method == "mcp/message":
            return await self._mcp_message(params, message_id, observer=observer)
        if method == "mcp/disconnect":
            return await self._mcp_disconnect(params, observer=observer)
        raise AcpProtocolError(-32601, f"Method not found: {method}")

    async def _handle_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
        observer: _AcpRequestObserver | None = None,
    ) -> None:
        if method == "session/cancel":
            _ = await self._cancel_session(params, observer=observer)
            return
        if method == "initialized":
            return
        if method == "mcp/message":
            _ = await self._mcp_message(params, None, observer=observer)
            return
        raise AcpProtocolError(-32601, f"Method not found: {method}")

    @staticmethod
    def _initialize_result(params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        protocol_version = params.get("protocolVersion")
        resolved_protocol_version = 1
        if isinstance(protocol_version, int) and protocol_version > 0:
            resolved_protocol_version = protocol_version
        return {
            "protocolVersion": resolved_protocol_version,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "audio": True,
                    "embeddedContext": False,
                    "image": True,
                },
                "mcpCapabilities": {
                    "acp": True,
                    "http": False,
                    "sse": False,
                },
            },
            "agentInfo": {
                "name": "agent-teams",
                "version": "0.1.0",
            },
        }

    def _create_session(
        self,
        params: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        cwd = _optional_str(params, "cwd")
        capabilities = _optional_object(params, "capabilities")
        mcp_servers = _parse_mcp_servers(params.get("mcpServers"))
        model_profile_override = _parse_model_profile_override(
            params.get("modelProfileOverride")
        )
        try:
            record = self._gateway_session_service.create_session(
                channel_type=GatewayChannelType.ACP_STDIO,
                cwd=cwd,
                capabilities=capabilities,
                session_mcp_servers=mcp_servers,
                model_profile_override=model_profile_override,
            )
        except ValueError as exc:
            raise AcpProtocolError(-32602, str(exc)) from exc
        self._mcp_relay.bind_session_servers(record.gateway_session_id, mcp_servers)
        if observer is not None:
            observer.bind_gateway_session_id(record.gateway_session_id)
            observer.bind_internal_session_id(record.internal_session_id)
            observer.bind_workspace_id(
                self._session_service.get_session(
                    record.internal_session_id
                ).workspace_id
            )
        return {"sessionId": record.gateway_session_id}

    async def _load_session(
        self,
        params: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        record = self._gateway_session_service.get_session(gateway_session_id)
        self._bind_gateway_session_observer(
            observer,
            gateway_session_id=gateway_session_id,
        )
        if "cwd" in params:
            cwd = _optional_str(params, "cwd")
            if cwd is not None:
                try:
                    record = self._gateway_session_service.rebind_session_cwd(
                        gateway_session_id,
                        cwd=cwd,
                    )
                except ValueError as exc:
                    raise AcpProtocolError(-32602, str(exc)) from exc
                except RuntimeError as exc:
                    raise AcpProtocolError(-32000, str(exc), status="failed") from exc
        if "mcpServers" in params:
            mcp_servers = _parse_mcp_servers(params.get("mcpServers"))
            record = self._gateway_session_service.set_session_mcp_servers(
                gateway_session_id,
                mcp_servers,
            )
        if "modelProfileOverride" in params:
            model_profile_override = _parse_model_profile_override(
                params.get("modelProfileOverride")
            )
            record = self._gateway_session_service.set_session_model_profile_override(
                gateway_session_id,
                model_profile_override,
            )
        self._mcp_relay.bind_session_servers(
            gateway_session_id,
            record.session_mcp_servers,
        )
        messages = self._session_service.get_session_messages(
            record.internal_session_id
        )
        for item in messages:
            role = str(item.get("role") or "")
            message = item.get("message")
            for update in _message_payload_to_session_updates(role, message):
                await self._publish_session_update(
                    gateway_session_id,
                    update,
                )
        return {"sessionId": gateway_session_id}

    async def _prompt_session(
        self,
        params: dict[str, JsonValue],
        message_id: JsonRpcId,
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        _ = message_id
        gateway_session_id = _required_str(params, "sessionId")
        prompt_blocks = _required_list(params, "prompt")
        record = self._gateway_session_service.get_session(gateway_session_id)
        session = self._session_service.get_session(record.internal_session_id)
        self._bind_gateway_session_observer(
            observer,
            gateway_session_id=gateway_session_id,
        )
        recoverable_run_id = self._recoverable_run_id(record.internal_session_id)
        prompt_input = self._prompt_blocks_to_content_parts(
            prompt_blocks=prompt_blocks,
            session_id=record.internal_session_id,
            workspace_id=session.workspace_id,
        )
        if not prompt_input:
            raise AcpProtocolError(
                -32602,
                "prompt must contain at least one supported content block",
            )
        intent = IntentInput(
            session_id=record.internal_session_id,
            input=prompt_input,
            yolo=True,
        )
        with self._mcp_relay.session_scope(gateway_session_id):
            try:
                run_id = await self._start_prompt_run(
                    intent,
                    force_attach_recoverable=recoverable_run_id is not None,
                )
            except GatewaySessionBusyError as exc:
                raise AcpProtocolError(
                    -32000,
                    f"Session already has an active run: {exc.blocking_run_id}",
                    status="busy",
                )
            except RuntimeError as exc:
                raise AcpProtocolError(-32000, str(exc), status="failed") from exc
            if observer is not None:
                observer.bind_run_id(run_id)
                observer.record_prompt_run_start()
            self._active_runs[gateway_session_id] = run_id
            _ = self._gateway_session_service.bind_active_run(
                gateway_session_id, run_id
            )
            if not self._zed_compat_mode:
                for part in prompt_input:
                    content = _content_part_to_acp_content(part)
                    if content is None:
                        continue
                    await self._publish_session_update(
                        gateway_session_id,
                        {
                            "sessionUpdate": "user_message_chunk",
                            "content": content,
                        },
                    )
            after_event_id = 0
            text_suppressor: _ResumeTextSuppressor | None = None
            if recoverable_run_id is not None and run_id == recoverable_run_id:
                after_event_id, text_suppressor = self._resume_stream_state(
                    internal_session_id=record.internal_session_id,
                    run_id=run_id,
                )
            result = await self._await_run_stop(
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                after_event_id=after_event_id,
                text_suppressor=text_suppressor,
                observer=observer,
            )

        self._finalize_active_run_binding(
            gateway_session_id=gateway_session_id,
            run_id=run_id,
            clear_active_run=result.clear_active_run,
        )
        if result.error_message is not None:
            raise AcpProtocolError(
                -32000,
                result.error_message,
                status=result.run_status,
            )
        return {
            "stopReason": result.stop_reason,
            "runId": result.run_id,
            "runStatus": result.run_status,
            "recoverable": result.recoverable,
        }

    async def _start_prompt_run(
        self,
        intent: IntentInput,
        *,
        force_attach_recoverable: bool = False,
    ) -> str:
        # ACP follow-up prompts should be able to resume a recoverable run even
        # when the gateway ingress layer would otherwise treat the session as busy.
        if self._session_ingress_service is not None and not force_attach_recoverable:
            result = await self._session_ingress_service.require_started_async(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.REJECT_IF_BUSY,
                )
            )
            if result.run_id is None:
                raise RuntimeError("acp_run_not_started")
            return result.run_id
        run_id, _ = await self._run_service.create_run_async(intent)
        await self._run_service.ensure_run_started_async(run_id)
        return run_id

    async def _resume_session(
        self,
        params: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        record = self._gateway_session_service.get_session(gateway_session_id)
        self._bind_gateway_session_observer(
            observer,
            gateway_session_id=gateway_session_id,
        )
        run_id = str(record.active_run_id or "").strip()
        if not run_id:
            raise AcpProtocolError(-32602, "Session has no active run to resume")
        if observer is not None:
            observer.bind_run_id(run_id)
        after_event_id, text_suppressor = self._resume_stream_state(
            internal_session_id=record.internal_session_id,
            run_id=run_id,
        )
        with self._mcp_relay.session_scope(gateway_session_id):
            await self._run_service.resume_run_async(run_id)
            await self._run_service.ensure_run_started_async(run_id)
            self._active_runs[gateway_session_id] = run_id
            _ = self._gateway_session_service.bind_active_run(
                gateway_session_id, run_id
            )
            result = await self._await_run_stop(
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                after_event_id=after_event_id,
                text_suppressor=text_suppressor,
                observer=observer,
            )

        self._finalize_active_run_binding(
            gateway_session_id=gateway_session_id,
            run_id=run_id,
            clear_active_run=result.clear_active_run,
        )
        if result.error_message is not None:
            raise AcpProtocolError(
                -32000,
                result.error_message,
                status=result.run_status,
            )
        return {
            "stopReason": result.stop_reason,
            "runId": result.run_id,
            "runStatus": result.run_status,
            "recoverable": result.recoverable,
        }

    async def _await_run_stop(
        self,
        *,
        gateway_session_id: str,
        run_id: str,
        after_event_id: int = 0,
        text_suppressor: _ResumeTextSuppressor | None = None,
        observer: _AcpRequestObserver | None = None,
    ) -> _AcpRunStopResult:
        async for event in self._run_service.stream_run_events(
            run_id, after_event_id=after_event_id
        ):
            maybe_result = await self._map_run_event(
                gateway_session_id=gateway_session_id,
                event=event,
                text_suppressor=text_suppressor,
                observer=observer,
            )
            if maybe_result is None:
                continue
            return _AcpRunStopResult(
                stop_reason=maybe_result.stop_reason,
                run_id=run_id,
                run_status=maybe_result.run_status,
                recoverable=maybe_result.recoverable,
                error_message=maybe_result.error_message,
                clear_active_run=maybe_result.clear_active_run,
            )
        raise RuntimeError(f"ACP run watcher ended before a stop event for {run_id}.")

    def _recoverable_run_id(self, internal_session_id: str) -> str | None:
        recovery_snapshot = self._session_service.get_recovery_snapshot(
            internal_session_id
        )
        active_run = recovery_snapshot.get("active_run")
        if not isinstance(active_run, Mapping):
            return None
        run_id = str(active_run.get("run_id") or "").strip()
        if not run_id:
            return None
        is_recoverable = active_run.get("is_recoverable")
        if not isinstance(is_recoverable, bool) or not is_recoverable:
            return None
        phase = str(active_run.get("phase") or "").strip()
        status = str(active_run.get("status") or "").strip()
        should_show_recover = active_run.get("should_show_recover") is True
        if phase == "awaiting_recovery" or status == "paused" or should_show_recover:
            return run_id
        return None

    def _resume_stream_state(
        self, *, internal_session_id: str, run_id: str
    ) -> tuple[int, _ResumeTextSuppressor]:
        return (
            self._resume_after_event_id(
                internal_session_id=internal_session_id,
                run_id=run_id,
            ),
            _ResumeTextSuppressor(
                self._resume_text_prefix(
                    internal_session_id=internal_session_id,
                    run_id=run_id,
                )
            ),
        )

    def _resume_after_event_id(self, *, internal_session_id: str, run_id: str) -> int:
        recovery_snapshot = self._session_service.get_recovery_snapshot(
            internal_session_id
        )
        active_run = recovery_snapshot.get("active_run")
        if not isinstance(active_run, Mapping):
            return 0
        active_run_id = str(active_run.get("run_id") or "").strip()
        if active_run_id != run_id:
            return 0
        last_event_id = active_run.get("last_event_id")
        if not isinstance(last_event_id, int) or last_event_id < 0:
            return 0
        return last_event_id

    def _resume_text_prefix(self, *, internal_session_id: str, run_id: str) -> str:
        collected: list[str] = []
        for raw_event in self._session_service.get_global_events(internal_session_id):
            if not isinstance(raw_event, Mapping):
                continue
            if str(raw_event.get("trace_id") or "").strip() != run_id:
                continue
            try:
                event_type = RunEventType(str(raw_event.get("event_type") or ""))
            except ValueError:
                continue
            payload_json = raw_event.get("payload_json")
            if not isinstance(payload_json, str):
                continue
            payload = _load_payload(payload_json)
            if event_type == RunEventType.TEXT_DELTA:
                text = _optional_text(payload, "text")
                if text:
                    collected.append(text)
                continue
            if event_type != RunEventType.OUTPUT_DELTA:
                continue
            for content in _typed_output_payload_to_acp_content(payload):
                if content.get("type") != "text":
                    continue
                text = content.get("text")
                if isinstance(text, str) and text:
                    collected.append(text)
        return "".join(collected)

    async def _publish_agent_update(
        self,
        gateway_session_id: str,
        update: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> None:
        if observer is not None:
            observer.record_prompt_first_update()
        await self._publish_session_update(gateway_session_id, update)

    async def _map_run_event(
        self,
        *,
        gateway_session_id: str,
        event: RunEvent,
        text_suppressor: _ResumeTextSuppressor | None = None,
        observer: _AcpRequestObserver | None = None,
    ) -> _AcpRunStopResult | None:
        payload = _load_payload(event.payload_json)
        if event.event_type == RunEventType.TEXT_DELTA:
            text = _optional_text(payload, "text")
            if text_suppressor is not None and text:
                text = text_suppressor.strip(text)
            if text:
                await self._publish_agent_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": text,
                        },
                    },
                    observer=observer,
                )
            return None
        if event.event_type == RunEventType.OUTPUT_DELTA:
            for content in _typed_output_payload_to_acp_content(payload):
                if text_suppressor is not None and content.get("type") == "text":
                    block_text = content.get("text")
                    if isinstance(block_text, str):
                        filtered_text = text_suppressor.strip(block_text)
                        if not filtered_text:
                            continue
                        content = {**content, "text": filtered_text}
                await self._publish_agent_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": content,
                    },
                    observer=observer,
                )
            return None
        if event.event_type == RunEventType.GENERATION_PROGRESS:
            tool_call_id = f"generation_{event.run_id}"
            run_kind = _optional_str(payload, "run_kind") or "generation"
            phase = _optional_str(payload, "phase") or "running"
            status = (
                "failed"
                if phase == "failed"
                else "completed"
                if phase == "completed"
                else "in_progress"
            )
            if phase == "started":
                await self._publish_agent_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": tool_call_id,
                        "title": run_kind,
                        "status": "in_progress",
                    },
                    observer=observer,
                )
                return None
            update_payload: dict[str, JsonValue] = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "status": status,
            }
            preview_asset_id = _optional_str(payload, "preview_asset_id")
            if preview_asset_id is not None:
                update_payload["rawInput"] = preview_asset_id
            await self._publish_agent_update(
                gateway_session_id,
                update_payload,
                observer=observer,
            )
            return None
        if event.event_type == RunEventType.THINKING_DELTA:
            text = _optional_text(payload, "text")
            if text:
                await self._publish_agent_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {
                            "type": "text",
                            "text": text,
                        },
                    },
                    observer=observer,
                )
            return None
        if event.event_type == RunEventType.TOOL_CALL:
            tool_call_id = (
                _optional_str(payload, "tool_call_id") or f"tool_{uuid4().hex[:12]}"
            )
            raw_input = payload.get("args")
            update: dict[str, JsonValue] = {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_call_id,
                "title": _optional_str(payload, "tool_name") or "tool",
                "status": "in_progress",
            }
            if raw_input is not None:
                update["rawInput"] = raw_input
            await self._publish_agent_update(
                gateway_session_id,
                update,
                observer=observer,
            )
            return None
        if event.event_type == RunEventType.TOOL_RESULT:
            tool_call_id = (
                _optional_str(payload, "tool_call_id") or f"tool_{uuid4().hex[:12]}"
            )
            status = "failed" if payload.get("error") is True else "completed"
            content = _tool_result_payload_to_acp_content(payload)
            await self._publish_agent_update(
                gateway_session_id,
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool_call_id,
                    "status": status,
                    "content": content,
                },
                observer=observer,
            )
            return None
        if event.event_type == RunEventType.RUN_PAUSED:
            pause_message = self._paused_run_message(payload)
            await self._publish_agent_update(
                gateway_session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": pause_message,
                    },
                },
                observer=observer,
            )
            return _AcpRunStopResult(
                stop_reason="end_turn",
                run_id=event.run_id,
                run_status="paused",
                recoverable=True,
                error_message=None,
                clear_active_run=False,
            )
        if event.event_type == RunEventType.RUN_STOPPED:
            return _AcpRunStopResult(
                stop_reason="cancelled",
                run_id=event.run_id,
                run_status="stopped",
                recoverable=True,
                error_message=None,
                clear_active_run=False,
            )
        if event.event_type == RunEventType.RUN_FAILED:
            error_text = _optional_str(payload, "error") or "Run failed"
            return _AcpRunStopResult(
                stop_reason="end_turn",
                run_id=event.run_id,
                run_status="failed",
                recoverable=False,
                error_message=error_text,
                clear_active_run=True,
            )
        if event.event_type == RunEventType.RUN_COMPLETED:
            return _AcpRunStopResult(
                stop_reason="end_turn",
                run_id=event.run_id,
                run_status="completed",
                recoverable=False,
                error_message=None,
                clear_active_run=True,
            )
        return None

    async def _cancel_session(
        self,
        params: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        self._bind_gateway_session_observer(
            observer,
            gateway_session_id=gateway_session_id,
        )
        run_id = self._active_runs.get(gateway_session_id)
        if run_id is None:
            record = self._gateway_session_service.get_session(gateway_session_id)
            run_id = str(record.active_run_id or "").strip() or None
        if run_id is not None:
            if observer is not None:
                observer.bind_run_id(run_id)
            await self._run_service.stop_run_async(run_id)
        return {"status": "ok"}

    def _finalize_active_run_binding(
        self,
        *,
        gateway_session_id: str,
        run_id: str,
        clear_active_run: bool,
    ) -> None:
        if not clear_active_run:
            self._active_runs[gateway_session_id] = run_id
            _ = self._gateway_session_service.bind_active_run(
                gateway_session_id, run_id
            )
            return
        self._active_runs.pop(gateway_session_id, None)
        _ = self._gateway_session_service.bind_active_run(gateway_session_id, None)

    @staticmethod
    def _paused_run_message(payload: dict[str, JsonValue]) -> str:
        error_message = _optional_str(payload, "error_message")
        if error_message:
            return f"Run paused: {error_message}\nSend session/resume to continue."
        return "Run paused. Send session/resume to continue."

    async def _mcp_connect(
        self,
        params: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        server_id = _required_str(params, "acpId", fallback_key="serverId")
        self._bind_gateway_session_observer(
            observer,
            gateway_session_id=gateway_session_id,
        )
        try:
            server_spec = self._mcp_relay.session_server_spec(
                session_id=gateway_session_id,
                server_id=server_id,
            )
        except KeyError as exc:
            raise AcpProtocolError(-32602, str(exc)) from exc
        if observer is not None:
            observer.bind_server_id(server_id)
            observer.bind_transport(server_spec.transport)
        connection = self._gateway_session_service.open_mcp_connection(
            gateway_session_id=gateway_session_id,
            server_id=server_id,
        )
        if observer is not None:
            observer.bind_connection_id(connection.connection_id)
        await self._mcp_relay.open_connection(
            session_id=gateway_session_id,
            connection_id=connection.connection_id,
            server_spec=server_spec,
        )
        return {
            "connectionId": connection.connection_id,
            "serverId": connection.server_id,
            "status": connection.status.value,
        }

    async def _mcp_message(
        self,
        params: dict[str, JsonValue],
        message_id: JsonRpcId | None,
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        connection_id = _required_str(params, "connectionId")
        method = _required_str(params, "method")
        forwarded_params = _optional_object(params, "params")
        gateway_session_id = _optional_str(params, "sessionId")
        if gateway_session_id is not None:
            self._bind_gateway_session_observer(
                observer,
                gateway_session_id=gateway_session_id,
            )
        if observer is not None:
            observer.bind_connection_id(connection_id)
            observer.bind_transport("acp")
        try:
            return await self._mcp_relay.relay_inbound_message(
                connection_id=connection_id,
                method=method,
                params=forwarded_params,
                message_id=message_id,
            )
        except KeyError as exc:
            raise AcpProtocolError(-32602, str(exc)) from exc

    async def _mcp_disconnect(
        self,
        params: dict[str, JsonValue],
        *,
        observer: _AcpRequestObserver | None = None,
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        connection_id = _required_str(params, "connectionId")
        self._bind_gateway_session_observer(
            observer,
            gateway_session_id=gateway_session_id,
        )
        if observer is not None:
            observer.bind_connection_id(connection_id)
            observer.bind_transport("acp")
        _ = self._gateway_session_service.close_mcp_connection(
            gateway_session_id=gateway_session_id,
            connection_id=connection_id,
        )
        await self._mcp_relay.close_connection(connection_id=connection_id)
        return {"status": "closed", "connectionId": connection_id}

    def _usage_for_run(self, run_id: str) -> dict[str, JsonValue]:
        try:
            usage = self._session_service.get_token_usage_by_run(run_id)
        except sqlite3.Error:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "thought_tokens": 0,
                "cached_read_tokens": 0,
                "cached_write_tokens": 0,
                "total_tokens": 0,
            }
        return {
            "input_tokens": usage.total_input_tokens,
            "output_tokens": usage.total_output_tokens,
            "thought_tokens": usage.total_reasoning_output_tokens,
            "cached_read_tokens": usage.total_cached_input_tokens,
            "cached_write_tokens": 0,
            "total_tokens": usage.total_tokens,
        }

    async def _publish_session_update(
        self,
        gateway_session_id: str,
        update: dict[str, JsonValue],
    ) -> None:
        await self._notify(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": gateway_session_id,
                    "update": update,
                },
            }
        )

    def _prompt_blocks_to_content_parts(
        self,
        *,
        prompt_blocks: list[JsonValue],
        session_id: str,
        workspace_id: str,
    ) -> tuple[ContentPart, ...]:
        parts: list[ContentPart] = []
        for item in prompt_blocks:
            if not isinstance(item, dict):
                continue
            block_type = str(item.get("type") or "").strip()
            if block_type == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(TextContentPart(text=text))
                continue
            if block_type in {"image", "audio"}:
                media_part = _acp_media_block_to_content_part(
                    item=item,
                    media_asset_service=self._media_asset_service,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    forced_modality=(
                        MediaModality.IMAGE
                        if block_type == "image"
                        else MediaModality.AUDIO
                    ),
                )
                if media_part is not None:
                    parts.append(media_part)
                continue
            if block_type in {"resource", "resource_link"}:
                media_part = _acp_media_block_to_content_part(
                    item=item,
                    media_asset_service=self._media_asset_service,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    forced_modality=None,
                )
                if media_part is not None:
                    parts.append(media_part)
        return tuple(parts)


class AcpStdioRuntime:
    def __init__(
        self,
        *,
        server: AcpGatewayServer,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
    ) -> None:
        self._server = server
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._write_lock = asyncio.Lock()
        self._emit_framed_messages = True
        self._next_request_id = 0
        self._pending_requests: dict[
            JsonRpcId, asyncio.Future[dict[str, JsonValue]]
        ] = {}
        self._request_count = 0
        self._started_at = time.perf_counter()
        self._server.set_zed_compat_mode(False)
        self._server.set_mcp_relay_outbound(
            send_request=self.send_request,
            send_notification=self.send_message,
        )

    def set_transport_mode(self, *, framed_input: bool) -> None:
        self._emit_framed_messages = framed_input
        self._server.set_zed_compat_mode(not framed_input)

    async def serve_forever(self) -> None:
        tasks: set[asyncio.Task[None]] = set()
        while True:
            raw_message, framed_input = await asyncio.to_thread(
                _read_message_bytes, self._input_stream
            )
            if framed_input is not None:
                self.set_transport_mode(framed_input=framed_input)
            if raw_message is None:
                break
            if not raw_message:
                continue
            try:
                parsed = json.loads(raw_message.decode("utf-8"))
            except json.JSONDecodeError:
                await self.send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error",
                        },
                    }
                )
                continue
            if not isinstance(parsed, dict):
                await self.send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "Invalid Request",
                        },
                    }
                )
                continue
            task = asyncio.create_task(self._handle_message(parsed))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, parsed: dict[str, JsonValue]) -> None:
        _trace_acp_message("inbound", parsed)
        message_id = _optional_id(parsed)
        method = parsed.get("method")
        if message_id is not None and not isinstance(method, str):
            pending = self._pending_requests.get(message_id)
            if pending is not None and not pending.done():
                pending.set_result(parsed)
                return
        request_context: _AcpRequestContext | None = None
        if isinstance(method, str):
            cold_start = self._request_count == 0
            self._request_count += 1
            request_context = _AcpRequestContext(
                cold_start=cold_start,
                framed_input=self._emit_framed_messages,
                runtime_uptime_ms=int((time.perf_counter() - self._started_at) * 1000),
            )
        response = await self._server.handle_jsonrpc_message(
            parsed,
            request_context=request_context,
        )
        if response is None:
            return
        await self.send_message(response)

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self._next_request_id += 1
        request_id = self._next_request_id
        future: asyncio.Future[dict[str, JsonValue]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_requests[request_id] = future
        try:
            await self.send_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            return await future
        finally:
            if request_id in self._pending_requests:
                del self._pending_requests[request_id]

    async def send_message(self, message: dict[str, JsonValue]) -> None:
        _trace_acp_message("outbound", message)
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        async with self._write_lock:
            if self._emit_framed_messages:
                header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
                await asyncio.to_thread(self._output_stream.write, header)
            else:
                payload += b"\n"
            await asyncio.to_thread(self._output_stream.write, payload)
            await asyncio.to_thread(self._output_stream.flush)


def _read_message_bytes(stream: BinaryIO) -> tuple[bytes | None, bool | None]:
    while True:
        first_line = stream.readline()
        if not first_line:
            return None, None
        if first_line in {b"\r\n", b"\n"}:
            continue
        stripped = first_line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(b"content-length:"):
            _, _, raw_length = stripped.partition(b":")
            length = int(raw_length.strip())
            while True:
                header_line = stream.readline()
                if not header_line:
                    return None, True
                if header_line in {b"\r\n", b"\n"}:
                    break
            return stream.read(length), True
        return stripped, False


def _optional_id(message: dict[str, JsonValue]) -> JsonRpcId | None:
    raw = message.get("id")
    if isinstance(raw, (str, int)):
        return raw
    return None


def _required_method(message: dict[str, JsonValue]) -> str:
    raw_method = message.get("method")
    if isinstance(raw_method, str) and raw_method.strip():
        return raw_method.strip()
    raise AcpProtocolError(-32600, "Invalid Request")


def _params_object(message: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw_params = message.get("params")
    if raw_params is None:
        return {}
    if isinstance(raw_params, dict):
        return raw_params
    raise AcpProtocolError(-32602, "params must be an object")


def _required_str(
    payload: Mapping[str, object],
    key: str,
    *,
    fallback_key: str | None = None,
) -> str:
    value = payload.get(key)
    if value is None and fallback_key is not None:
        value = payload.get(fallback_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise AcpProtocolError(-32602, f"{key} must be a non-empty string")


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _optional_object(payload: Mapping[str, object], key: str) -> dict[str, JsonValue]:
    value = payload.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(inner_key): inner_value for inner_key, inner_value in value.items()}
    raise AcpProtocolError(-32602, f"{key} must be an object")


def _required_list(payload: Mapping[str, object], key: str) -> list[JsonValue]:
    value = payload.get(key)
    if isinstance(value, list):
        return value
    raise AcpProtocolError(-32602, f"{key} must be a list")


def _parse_model_profile_override(
    raw_value: JsonValue | None,
) -> GatewayModelProfileOverride | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, dict):
        raise AcpProtocolError(-32602, "modelProfileOverride must be an object")
    try:
        return GatewayModelProfileOverride.from_acp_payload(raw_value)
    except Exception as exc:
        raise AcpProtocolError(-32602, str(exc)) from exc


def _parse_mcp_servers(raw_value: JsonValue | None) -> tuple[GatewayMcpServerSpec, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise AcpProtocolError(-32602, "mcpServers must be a list")
    result: list[GatewayMcpServerSpec] = []
    for index, item in enumerate(raw_value):
        if not isinstance(item, dict):
            raise AcpProtocolError(-32602, "mcpServers items must be objects")
        raw_id = item.get("id")
        raw_transport = _detect_mcp_transport(item)
        if raw_transport is None:
            raise AcpProtocolError(
                -32602,
                f"mcpServers[{index}] must declare a transport, command, or url",
            )
        raw_name = item.get("name")
        normalized_name = (
            raw_name.strip()
            if isinstance(raw_name, str) and raw_name.strip()
            else (
                raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
            )
        )
        if normalized_name is None:
            raise AcpProtocolError(-32602, f"mcpServers[{index}].name must be a string")
        server_id = (
            raw_id.strip()
            if isinstance(raw_id, str) and raw_id.strip()
            else normalized_name
        )
        result.append(
            GatewayMcpServerSpec(
                server_id=server_id,
                name=normalized_name,
                transport=raw_transport,
                config={str(key): value for key, value in item.items()},
            )
        )
    return tuple(result)


def _detect_mcp_transport(item: dict[str, JsonValue]) -> str | None:
    raw_transport = item.get("transport")
    if isinstance(raw_transport, str) and raw_transport.strip():
        return raw_transport.strip()
    raw_type = item.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        return raw_type.strip()
    raw_command = item.get("command")
    if isinstance(raw_command, str) and raw_command.strip():
        return "stdio"
    raw_url = item.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "sse" if "/sse" in raw_url else "http"
    return None


def _prompt_blocks_to_text(prompt_blocks: list[JsonValue]) -> str:
    collected: list[str] = []
    for item in prompt_blocks:
        if not isinstance(item, dict):
            continue
        block_type = item.get("type")
        if block_type == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                collected.append(text)
            continue
        if block_type == "resource_link":
            uri = item.get("uri")
            if isinstance(uri, str) and uri.strip():
                collected.append(f"Resource: {uri.strip()}")
    return "\n\n".join(collected).strip()


def _typed_output_payload_to_acp_content(
    payload: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], ...]:
    raw_output = payload.get("output")
    if not isinstance(raw_output, list):
        return ()
    content: list[dict[str, JsonValue]] = []
    for item in raw_output:
        if not isinstance(item, dict):
            continue
        part = ContentPartAdapter.validate_python(item)
        block = _content_part_to_acp_content(part)
        if block is not None:
            content.append(block)
    return tuple(content)


def _tool_result_payload_to_acp_content(
    payload: dict[str, JsonValue],
) -> list[JsonValue]:
    raw_content = payload.get("content")
    if not isinstance(raw_content, list):
        result = payload.get("result")
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict):
                raw_content = data.get("content")
    if isinstance(raw_content, list):
        blocks: list[JsonValue] = []
        for item in raw_content:
            if not isinstance(item, dict):
                continue
            part = ContentPartAdapter.validate_python(item)
            block = _content_part_to_acp_content(part)
            if block is None:
                continue
            blocks.append({"type": "content", "content": block})
        if blocks:
            return blocks
    result_payload = payload.get("result")
    if isinstance(result_payload, dict):
        data = result_payload.get("data")
        if isinstance(data, dict):
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                return [{"type": "content", "content": {"type": "text", "text": text}}]
    result_text = json.dumps(result_payload, ensure_ascii=False, default=str)
    return [{"type": "content", "content": {"type": "text", "text": result_text}}]


def _message_payload_to_session_updates(
    role: str,
    message: object,
) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(message, dict):
        return ()
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ()
    updates: list[dict[str, JsonValue]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_kind = str(part.get("part_kind") or "")
        content = part.get("content")
        if role == "user" and part_kind == "user-prompt":
            updates.extend(_user_prompt_payload_to_updates(content))
            continue
        if role != "user" and part_kind == "thinking":
            if not isinstance(content, str) or not content.strip():
                continue
            updates.append(
                {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {
                        "type": "text",
                        "text": content,
                    },
                }
            )
            continue
        if role != "user" and part_kind == "text":
            if not isinstance(content, str) or not content.strip():
                continue
            updates.append(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": content,
                    },
                }
            )
            continue
        if role != "user" and part_kind == "file" and isinstance(content, dict):
            block = _binary_payload_to_acp_content(content)
            if block is None:
                continue
            updates.append(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": block,
                }
            )
    return tuple(updates)


def _user_prompt_payload_to_updates(
    content: object,
) -> tuple[dict[str, JsonValue], ...]:
    updates: list[dict[str, JsonValue]] = []
    if isinstance(content, str) and content.strip():
        updates.append(
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": content},
            }
        )
        return tuple(updates)
    if not isinstance(content, list):
        return ()
    for item in content:
        block = _user_content_item_to_acp_content(item)
        if block is None:
            continue
        updates.append(
            {
                "sessionUpdate": "user_message_chunk",
                "content": block,
            }
        )
    return tuple(updates)


def _user_content_item_to_acp_content(
    item: object,
) -> dict[str, JsonValue] | None:
    if isinstance(item, str) and item.strip():
        return {"type": "text", "text": item}
    if not isinstance(item, dict):
        return None
    item_kind = str(item.get("kind") or "").strip()
    if item_kind == "image-url":
        url = _optional_str(item, "url")
        if url is not None:
            return {
                "type": "image",
                "uri": url,
                "mimeType": _optional_str(item, "media_type") or "image/*",
            }
        return None
    if item_kind == "audio-url":
        url = _optional_str(item, "url")
        if url is not None:
            return {
                "type": "audio",
                "uri": url,
                "mimeType": _optional_str(item, "media_type") or "audio/*",
            }
        return None
    if item_kind == "video-url":
        url = _optional_str(item, "url")
        if url is not None:
            return {
                "type": "resource_link",
                "uri": url,
                "mimeType": _optional_str(item, "media_type") or "video/*",
            }
        return None
    if item_kind == "binary":
        return _binary_payload_to_acp_content(item)
    return None


def _binary_payload_to_acp_content(
    payload: dict[str, object],
) -> dict[str, JsonValue] | None:
    media_type = _optional_str(payload, "media_type")
    data = _optional_str(payload, "data")
    if media_type is None or data is None:
        return None
    data_uri = f"data:{media_type};base64,{data}"
    try:
        modality = infer_media_modality(media_type)
    except ValueError:
        return None
    if modality == MediaModality.IMAGE:
        return {"type": "image", "uri": data_uri, "mimeType": media_type}
    if modality == MediaModality.AUDIO:
        return {"type": "audio", "uri": data_uri, "mimeType": media_type}
    return {"type": "resource_link", "uri": data_uri, "mimeType": media_type}


def _content_part_to_acp_content(
    part: ContentPart,
) -> dict[str, JsonValue] | None:
    if isinstance(part, TextContentPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, MediaRefContentPart):
        if part.modality == MediaModality.IMAGE:
            return {
                "type": "image",
                "uri": part.url,
                "mimeType": part.mime_type,
            }
        if part.modality == MediaModality.AUDIO:
            return {
                "type": "audio",
                "uri": part.url,
                "mimeType": part.mime_type,
            }
        return {
            "type": "resource_link",
            "uri": part.url,
            "mimeType": part.mime_type,
            "title": part.name or "video",
        }
    return None


def _acp_media_block_to_content_part(
    *,
    item: dict[str, JsonValue],
    media_asset_service: MediaAssetService,
    session_id: str,
    workspace_id: str,
    forced_modality: MediaModality | None,
) -> MediaRefContentPart | None:
    uri = _optional_str(item, "uri") or _optional_str(item, "url")
    name = _optional_str(item, "name") or _name_from_uri(uri)
    mime_type = (
        _optional_str(item, "mimeType")
        or _optional_str(item, "mediaType")
        or _mime_type_from_data_uri(uri)
    )
    if uri is None:
        raw_data = _optional_str(item, "data")
        if raw_data is None or mime_type is None:
            return None
        uri = f"data:{mime_type};base64,{raw_data}"
    try:
        modality = forced_modality or infer_media_modality(
            mime_type or "",
            filename=name or "",
        )
    except ValueError:
        return None
    parsed = _parse_data_uri(uri)
    if parsed is not None:
        data_mime_type, raw = parsed
        record = media_asset_service.store_bytes(
            session_id=session_id,
            workspace_id=workspace_id,
            modality=modality,
            mime_type=mime_type or data_mime_type,
            data=raw,
            name=name or "",
            size_bytes=len(raw),
            source="acp_prompt",
        )
        return media_asset_service.to_content_part(record)
    record = media_asset_service.store_remote_reference(
        session_id=session_id,
        workspace_id=workspace_id,
        modality=modality,
        mime_type=mime_type or _default_mime_type_for_modality(modality),
        url=uri,
        name=name or "",
        source="acp_prompt",
    )
    return media_asset_service.to_content_part(record)


def _parse_data_uri(value: str | None) -> tuple[str, bytes] | None:
    if value is None or not value.startswith("data:") or "," not in value:
        return None
    header, encoded = value.split(",", 1)
    media_type = header[5:].split(";", 1)[0].strip() or "application/octet-stream"
    try:
        return media_type, base64.b64decode(encoded)
    except ValueError:
        return None


def _mime_type_from_data_uri(value: str | None) -> str | None:
    parsed = _parse_data_uri(value)
    if parsed is None:
        return None
    return parsed[0]


def _name_from_uri(uri: str | None) -> str | None:
    if uri is None:
        return None
    if "/" not in uri:
        return None
    return uri.rsplit("/", 1)[-1] or None


def _default_mime_type_for_modality(modality: MediaModality) -> str:
    if modality == MediaModality.IMAGE:
        return "image/png"
    if modality == MediaModality.AUDIO:
        return "audio/mpeg"
    return "video/mp4"


def _load_payload(raw_payload: str) -> dict[str, JsonValue]:
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _tracked_gateway_operation(method: str) -> str | None:
    return {
        "initialize": "initialize",
        "session/new": "session_new",
        "session/load": "session_load",
        "session/prompt": "session_prompt",
        "session/resume": "session_resume",
        "session/cancel": "session_cancel",
        "mcp/connect": "mcp_connect",
        "mcp/message": "mcp_message",
        "mcp/disconnect": "mcp_disconnect",
    }.get(method)


def _request_status_from_result(
    *,
    method: str,
    result: dict[str, JsonValue],
) -> str:
    if method in {"session/prompt", "session/resume"}:
        run_status = result.get("runStatus")
        if isinstance(run_status, str) and run_status.strip():
            return run_status.strip()
    return "success"


def _gateway_log_level(status: str) -> int:
    normalized = status.strip().lower()
    if normalized in {"failed", "internal_error"}:
        return logging.ERROR
    if normalized in {"busy", "protocol_error"}:
        return logging.WARNING
    return logging.INFO


def _trace_acp_message(direction: str, message: dict[str, JsonValue]) -> None:
    if not _env_flag_enabled("ACP_TRACE_STDIO"):
        return
    log_event(
        LOGGER,
        level=10,
        event=f"gateway.acp.{direction}",
        message=f"ACP {direction} message",
        payload={"message": message},
    )


def _env_flag_enabled(key: str) -> bool:
    raw = get_env_var(key, "") or ""
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def run_acp_stdio_server(server: AcpGatewayServer) -> None:
    runtime = AcpStdioRuntime(
        server=server,
        input_stream=sys.stdin.buffer,
        output_stream=sys.stdout.buffer,
    )
    server.set_notify(runtime.send_message)
    await runtime.serve_forever()
