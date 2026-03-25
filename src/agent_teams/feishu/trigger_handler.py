# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from json import JSONDecodeError, loads
import re
from typing import Protocol, cast

from lark_oapi.api.im.v1.model.event_message import EventMessage
from lark_oapi.api.im.v1.model.event_sender import EventSender
from lark_oapi.api.im.v1.model.user_id import UserId
from lark_oapi.core.json import JSON
from lark_oapi.event.context import EventHeader
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
from pydantic import JsonValue

from agent_teams.providers.token_usage_repo import SessionTokenUsage

from agent_teams.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TENANT_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FEISHU_PLATFORM,
    FeishuEnvironment,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    SESSION_METADATA_SOURCE_ICON_KEY,
    SESSION_METADATA_SOURCE_KIND_KEY,
    SESSION_METADATA_SOURCE_LABEL_KEY,
    SESSION_METADATA_SOURCE_PROVIDER_KEY,
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_SOURCE_ICON_IM,
    SESSION_SOURCE_KIND_IM,
    SESSION_TITLE_SOURCE_AUTO,
    SESSION_TITLE_SOURCE_MANUAL,
    TriggerProcessingResult,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.runs.enums import ExecutionMode
from agent_teams.sessions.runs.run_models import IntentInput
from agent_teams.sessions.session_models import SessionMode, SessionRecord
from agent_teams.triggers import (
    TriggerDefinition,
    TriggerIngestInput,
    TriggerIngestResult,
    TriggerSourceType,
)

_AT_TAG_PATTERN = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE)
_AT_TAG_LABEL_PATTERN = re.compile(r"<at\b[^>]*>(.*?)</at>", re.IGNORECASE)
_LEADING_MENTION_TOKEN_PATTERN = re.compile(r"^(?:@\S+\s*)+")
_SESSION_COMMANDS: frozenset[str] = frozenset({"help", "status", "clear"})
logger = get_logger(__name__)


class TriggerServiceLike(Protocol):
    def get_trigger(self, trigger_id: str) -> TriggerDefinition: ...

    def ingest_event(
        self,
        event: TriggerIngestInput,
        *,
        headers: dict[str, str],
        remote_addr: str | None,
        raw_body: str,
    ) -> TriggerIngestResult: ...


class FeishuConfigResolverLike(Protocol):
    def resolve_runtime_config(
        self,
        trigger: TriggerDefinition,
    ) -> FeishuTriggerRuntimeConfig | None: ...


class SessionServiceLike(Protocol):
    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord: ...

    def get_session(self, session_id: str) -> SessionRecord: ...

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None: ...

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]: ...

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage: ...

    def clear_session_messages(self, session_id: str) -> int: ...


class RunServiceLike(Protocol):
    def create_run(self, intent: IntentInput) -> tuple[str, str]: ...

    def ensure_run_started(self, run_id: str) -> None: ...


class FeishuClientLike(Protocol):
    def get_chat_name(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None: ...

    def get_user_name(
        self,
        *,
        open_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None: ...

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...


class FeishuTriggerHandler:
    def __init__(
        self,
        *,
        trigger_service: TriggerServiceLike,
        feishu_config_service: FeishuConfigResolverLike,
        session_service: SessionServiceLike,
        run_service: RunServiceLike,
        external_session_binding_repo: ExternalSessionBindingRepository,
        feishu_client: FeishuClientLike | None = None,
    ) -> None:
        self._trigger_service = trigger_service
        self._feishu_config_service = feishu_config_service
        self._session_service = session_service
        self._run_service = run_service
        self._external_session_binding_repo = external_session_binding_repo
        self._feishu_client = feishu_client

    def handle_sdk_event(
        self,
        *,
        trigger_id: str,
        event: P2ImMessageReceiveV1,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        try:
            trigger = self._trigger_service.get_trigger(trigger_id)
        except KeyError:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger_id,
                ignored=True,
                reason="trigger_not_found",
            )
        try:
            runtime_config = self._feishu_config_service.resolve_runtime_config(trigger)
        except ValueError:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                ignored=True,
                reason="invalid_trigger_config",
            )
        if runtime_config is None:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                ignored=True,
                reason="missing_credentials",
            )
        normalized = _normalize_sdk_message(event)
        return self._handle_normalized_message(
            runtime_config=runtime_config,
            normalized=normalized,
            raw_body=raw_body,
            headers=headers,
            remote_addr=remote_addr,
        )

    def _handle_normalized_message(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        normalized: FeishuNormalizedMessage | None,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        if normalized is None:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                ignored=True,
                reason="unsupported_event_type",
            )
        chat_type = normalized.chat_type.lower()
        if chat_type not in {"group", "p2p"}:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                event_id=normalized.event_id,
                ignored=True,
                reason="unsupported_chat_type",
            )
        if _is_sender_bot(normalized.sender_type):
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                event_id=normalized.event_id,
                ignored=True,
                reason="sender_is_bot",
            )

        trigger_rule = runtime_config.source.trigger_rule
        if (
            trigger_rule == "mention_only"
            and chat_type == "group"
            and not normalized.mentioned
        ):
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                event_id=normalized.event_id,
                ignored=True,
                reason="mention_required",
            )
        if trigger_rule == "mention_only" and chat_type == "group":
            if not _mention_targets_app(
                mention_names=normalized.mention_names,
                app_name=runtime_config.source.app_name,
            ):
                return TriggerProcessingResult(
                    status="ignored",
                    trigger_id=runtime_config.trigger_id,
                    trigger_name=runtime_config.trigger_name,
                    event_id=normalized.event_id,
                    ignored=True,
                    reason="mention_not_for_app",
                )
        if not normalized.trigger_text.strip():
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                event_id=normalized.event_id,
                ignored=True,
                reason="empty_trigger_text",
            )

        command = normalized.trigger_text.strip().casefold()
        if command in _SESSION_COMMANDS:
            return self._handle_session_command(
                command=command,
                runtime_config=runtime_config,
                normalized=normalized,
            )

        self._send_acknowledgement(
            chat_id=normalized.chat_id,
            environment=runtime_config.environment,
        )

        ingest_result = self._trigger_service.ingest_event(
            TriggerIngestInput(
                trigger_id=runtime_config.trigger_id,
                source_type=TriggerSourceType.IM,
                event_key=_feishu_event_key(normalized),
                payload=normalized.payload,
                metadata=normalized.metadata,
            ),
            headers=headers,
            remote_addr=remote_addr,
            raw_body=raw_body,
        )
        if ingest_result.duplicate:
            return TriggerProcessingResult(
                status="accepted",
                trigger_id=ingest_result.trigger_id,
                trigger_name=ingest_result.trigger_name,
                event_id=ingest_result.event_id,
                duplicate=True,
            )

        session_id = self._resolve_session_id(
            runtime_config=runtime_config,
            message=normalized,
        )
        run_id, _session_id = self._run_service.create_run(
            IntentInput(
                session_id=session_id,
                intent=normalized.trigger_text,
                execution_mode=ExecutionMode.AI,
                yolo=runtime_config.target.yolo,
                thinking=runtime_config.target.thinking,
            )
        )
        self._run_service.ensure_run_started(run_id)
        return TriggerProcessingResult(
            status="accepted",
            trigger_id=ingest_result.trigger_id,
            trigger_name=ingest_result.trigger_name,
            event_id=ingest_result.event_id,
            duplicate=False,
            session_id=session_id,
            run_id=run_id,
        )

    def _resolve_session_id(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str:
        binding = self._external_session_binding_repo.get_binding(
            platform=FEISHU_PLATFORM,
            trigger_id=runtime_config.trigger_id,
            tenant_key=message.tenant_key,
            external_chat_id=message.chat_id,
        )
        metadata = self._build_session_metadata(
            runtime_config=runtime_config,
            message=message,
        )
        if binding is not None:
            try:
                session = self._session_service.get_session(binding.session_id)
            except KeyError:
                session = self._create_session(
                    runtime_config=runtime_config,
                    metadata=metadata,
                )
                self._external_session_binding_repo.upsert_binding(
                    platform=FEISHU_PLATFORM,
                    trigger_id=runtime_config.trigger_id,
                    tenant_key=message.tenant_key,
                    external_chat_id=message.chat_id,
                    session_id=session.session_id,
                )
                return session.session_id
            merged_metadata = self._merge_session_metadata(
                current_metadata=session.metadata,
                next_metadata=metadata,
            )
            self._session_service.update_session(session.session_id, merged_metadata)
            return session.session_id

        session = self._create_session(runtime_config=runtime_config, metadata=metadata)
        self._external_session_binding_repo.upsert_binding(
            platform=FEISHU_PLATFORM,
            trigger_id=runtime_config.trigger_id,
            tenant_key=message.tenant_key,
            external_chat_id=message.chat_id,
            session_id=session.session_id,
        )
        return session.session_id

    def _create_session(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        metadata: dict[str, str],
    ) -> SessionRecord:
        return self._session_service.create_session(
            workspace_id=runtime_config.target.workspace_id,
            metadata=metadata,
            session_mode=runtime_config.target.session_mode,
            normal_root_role_id=runtime_config.target.normal_root_role_id,
            orchestration_preset_id=runtime_config.target.orchestration_preset_id,
        )

    def _build_session_metadata(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> dict[str, str]:
        source_label = self._resolve_source_label(
            runtime_config=runtime_config,
            message=message,
        )
        metadata = {
            FEISHU_METADATA_PLATFORM_KEY: FEISHU_PLATFORM,
            FEISHU_METADATA_TENANT_KEY: message.tenant_key,
            FEISHU_METADATA_CHAT_ID_KEY: message.chat_id,
            FEISHU_METADATA_CHAT_TYPE_KEY: message.chat_type,
            FEISHU_METADATA_TRIGGER_ID_KEY: runtime_config.trigger_id,
            SESSION_METADATA_SOURCE_KIND_KEY: SESSION_SOURCE_KIND_IM,
            SESSION_METADATA_SOURCE_PROVIDER_KEY: FEISHU_PLATFORM,
            SESSION_METADATA_SOURCE_LABEL_KEY: source_label,
            SESSION_METADATA_SOURCE_ICON_KEY: SESSION_SOURCE_ICON_IM,
            "title": _build_session_title(runtime_config.trigger_name, source_label),
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
        }
        return metadata

    def _resolve_source_label(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str:
        chat_type = message.chat_type.strip().lower()
        if chat_type == "group":
            chat_name = self._lookup_chat_name(
                runtime_config=runtime_config,
                chat_id=message.chat_id,
            )
            if chat_name is not None:
                return chat_name
            return _build_fallback_source_label("group", message.chat_id)
        if chat_type == "p2p":
            user_name = self._lookup_user_name(
                runtime_config=runtime_config,
                open_id=message.sender_open_id,
                chat_id=message.chat_id,
            )
            if user_name is not None:
                return user_name
            return _build_fallback_source_label("p2p", message.chat_id)
        return _build_fallback_source_label(chat_type, message.chat_id)

    def _lookup_chat_name(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        chat_id: str,
    ) -> str | None:
        if self._feishu_client is None:
            return None
        try:
            resolved = self._feishu_client.get_chat_name(
                chat_id=chat_id,
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.session_name.chat_lookup_failed",
                message="Failed to resolve Feishu chat name for IM session",
                payload={
                    "trigger_id": runtime_config.trigger_id,
                    "chat_id": chat_id,
                    "error": str(exc),
                },
            )
            return None
        normalized = str(resolved or "").strip()
        return normalized or None

    def _lookup_user_name(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        open_id: str | None,
        chat_id: str,
    ) -> str | None:
        normalized_open_id = str(open_id or "").strip()
        if self._feishu_client is None or not normalized_open_id:
            return None
        try:
            resolved = self._feishu_client.get_user_name(
                open_id=normalized_open_id,
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.session_name.user_lookup_failed",
                message="Failed to resolve Feishu user name for IM session",
                payload={
                    "trigger_id": runtime_config.trigger_id,
                    "chat_id": chat_id,
                    "sender_open_id": normalized_open_id,
                    "error": str(exc),
                },
            )
            return None
        normalized = str(resolved or "").strip()
        return normalized or None

    def _send_acknowledgement(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment,
    ) -> None:
        if self._feishu_client is None:
            return
        try:
            self._feishu_client.send_text_message(
                chat_id=chat_id,
                text="收到，正在处理",
                environment=environment,
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.acknowledgement.send_failed",
                message="Failed to send acknowledgement to Feishu chat",
                payload={
                    "chat_id": chat_id,
                    "error": str(exc),
                },
            )

    # ------------------------------------------------------------------
    # Session commands
    # ------------------------------------------------------------------

    def _handle_session_command(
        self,
        *,
        command: str,
        runtime_config: FeishuTriggerRuntimeConfig,
        normalized: FeishuNormalizedMessage,
    ) -> TriggerProcessingResult:
        if command == "help":
            response_text = self._cmd_help()
        elif command == "status":
            response_text = self._cmd_status(
                runtime_config=runtime_config,
                message=normalized,
            )
        elif command == "clear":
            response_text = self._cmd_clear(
                runtime_config=runtime_config,
                message=normalized,
            )
        else:
            response_text = self._cmd_help()

        self._send_command_response(
            chat_id=normalized.chat_id,
            text=response_text,
            environment=runtime_config.environment,
        )
        return TriggerProcessingResult(
            status="command",
            trigger_id=runtime_config.trigger_id,
            trigger_name=runtime_config.trigger_name,
            event_id=normalized.event_id,
            reason="session_command",
        )

    def _cmd_help(self) -> str:
        lines = [
            "[Session Commands]",
            "",
            "help   - Show this help message",
            "status - Show current session info",
            "clear  - Reset the current conversation context",
        ]
        return "\n".join(lines)

    def _cmd_status(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str:
        session_id = self._resolve_existing_session_id(
            runtime_config=runtime_config,
            message=message,
        )
        if session_id is None:
            return "[Status] No active session for this chat."

        messages = self._session_service.get_session_messages(session_id)
        usage = self._session_service.get_token_usage_by_session(session_id)

        lines: list[str] = [
            "[Session Status]",
            "",
            f"Session: {session_id}",
            f"Messages: {len(messages)}",
            f"Tokens: input={usage.total_input_tokens}"
            f"  output={usage.total_output_tokens}"
            f"  total={usage.total_tokens}",
            f"Requests: {usage.total_requests}",
        ]

        recent = messages[-6:]
        if recent:
            lines.append("")
            lines.append("Recent messages:")
            for msg in recent:
                role = str(msg.get("role", "unknown"))
                preview = _extract_content_preview(msg)
                lines.append(f"  [{role}] {preview}")

        return "\n".join(lines)

    def _cmd_clear(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str:
        session_id = self._resolve_existing_session_id(
            runtime_config=runtime_config,
            message=message,
        )
        if session_id is None:
            return "[Clear] No active session. Nothing to clear."

        try:
            count = self._session_service.clear_session_messages(session_id)
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.command.clear_failed",
                message="Failed to clear session messages",
                payload={"session_id": session_id, "error": str(exc)},
            )
            return "[Clear] Failed to clear messages."

        return (
            f"[Clear] Cleared {count} active messages. "
            "Earlier history remains available."
        )

    def _resolve_existing_session_id(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str | None:
        binding = self._external_session_binding_repo.get_binding(
            platform=FEISHU_PLATFORM,
            trigger_id=runtime_config.trigger_id,
            tenant_key=message.tenant_key,
            external_chat_id=message.chat_id,
        )
        if binding is None:
            return None
        try:
            self._session_service.get_session(binding.session_id)
        except KeyError:
            return None
        return binding.session_id

    def _send_command_response(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment,
    ) -> None:
        if self._feishu_client is None:
            return
        try:
            self._feishu_client.send_text_message(
                chat_id=chat_id,
                text=text,
                environment=environment,
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.command_response.send_failed",
                message="Failed to send command response to Feishu chat",
                payload={"chat_id": chat_id, "error": str(exc)},
            )

    def _merge_session_metadata(
        self,
        *,
        current_metadata: dict[str, str],
        next_metadata: dict[str, str],
    ) -> dict[str, str]:
        merged_metadata = dict(current_metadata)
        merged_metadata.update(next_metadata)
        if (
            current_metadata.get(SESSION_METADATA_TITLE_SOURCE_KEY)
            == SESSION_TITLE_SOURCE_MANUAL
        ):
            merged_metadata[SESSION_METADATA_TITLE_SOURCE_KEY] = (
                SESSION_TITLE_SOURCE_MANUAL
            )
            current_title = str(current_metadata.get("title") or "").strip()
            if current_title:
                merged_metadata["title"] = current_title
            else:
                merged_metadata.pop("title", None)
        return merged_metadata


def _parse_json_object(raw_body: str) -> dict[str, JsonValue]:
    try:
        parsed = cast(object, loads(raw_body))
    except JSONDecodeError as exc:
        raise ValueError("Feishu event body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Feishu event body must be a JSON object")
    return cast(dict[str, JsonValue], parsed)


def _normalize_sdk_message(event: P2ImMessageReceiveV1) -> FeishuNormalizedMessage:
    header = event.header
    event_data = event.event
    if header is None:
        raise ValueError("Feishu event is missing header")
    if event_data is None:
        raise ValueError("Feishu event is missing event body")
    message = event_data.message
    if message is None:
        raise ValueError("Feishu event is missing message")
    sender = event_data.sender
    if sender is None:
        raise ValueError("Feishu event is missing sender")

    message_type = str(message.message_type or "").strip()
    payload = _sdk_event_payload(event)
    mention_names = _extract_mention_names(payload)
    if message_type != "text":
        return FeishuNormalizedMessage(
            event_id=_sdk_event_id(header, message),
            tenant_key=_sdk_tenant_key(header, sender),
            chat_id=str(message.chat_id or "").strip(),
            chat_type=str(message.chat_type or "").strip(),
            message_id=str(message.message_id or "").strip(),
            message_type=message_type or "unknown",
            sender_type=_sdk_sender_type(sender),
            sender_open_id=_sdk_sender_open_id(sender.sender_id),
            mention_names=mention_names,
            payload=payload,
            metadata=_sdk_message_metadata(header, message),
        )

    raw_text = _extract_message_text_from_content(message.content)
    mentioned = "<at " in raw_text.lower() or bool(mention_names)
    trigger_text = _sanitize_trigger_text(
        _AT_TAG_PATTERN.sub("", raw_text),
        mentioned=mentioned,
    )
    payload["message_text"] = trigger_text
    payload["raw_text"] = raw_text
    return FeishuNormalizedMessage(
        event_id=_sdk_event_id(header, message),
        tenant_key=_sdk_tenant_key(header, sender),
        chat_id=str(message.chat_id or "").strip(),
        chat_type=str(message.chat_type or "").strip(),
        message_id=str(message.message_id or "").strip(),
        message_type=message_type,
        sender_type=_sdk_sender_type(sender),
        sender_open_id=_sdk_sender_open_id(sender.sender_id),
        raw_text=raw_text,
        trigger_text=trigger_text,
        mentioned=mentioned,
        mention_names=mention_names,
        payload=payload,
        metadata=_sdk_message_metadata(header, message),
    )


def _sdk_event_payload(event: P2ImMessageReceiveV1) -> dict[str, JsonValue]:
    marshaled = JSON.marshal(event)
    if marshaled is None:
        raise ValueError("Feishu SDK event payload is empty")
    return _parse_json_object(marshaled)


def _sdk_event_id(header: EventHeader, message: EventMessage) -> str:
    event_id = str(header.event_id or "").strip()
    if event_id:
        return event_id
    message_id = str(message.message_id or "").strip()
    if message_id:
        return message_id
    raise ValueError("Feishu callback is missing event_id")


def _sdk_tenant_key(header: EventHeader, sender: EventSender) -> str:
    tenant_key = str(header.tenant_key or "").strip()
    if tenant_key:
        return tenant_key
    fallback = str(sender.tenant_key or "").strip()
    if fallback:
        return fallback
    raise ValueError("Feishu callback is missing tenant_key")


def _sdk_message_metadata(
    header: EventHeader,
    message: EventMessage,
) -> dict[str, str]:
    metadata = {
        "provider": FEISHU_PLATFORM,
        "tenant_key": str(header.tenant_key or "").strip(),
        "event_id": _sdk_event_id(header, message),
        "message_id": str(message.message_id or "").strip(),
        "chat_id": str(message.chat_id or "").strip(),
        "chat_type": str(message.chat_type or "").strip(),
    }
    return {key: value for key, value in metadata.items() if value}


def _sdk_sender_type(sender: EventSender) -> str | None:
    sender_type = str(sender.sender_type or "").strip()
    return sender_type or None


def _sdk_sender_open_id(sender_id: UserId | None) -> str | None:
    if sender_id is None:
        return None
    open_id = str(sender_id.open_id or "").strip()
    return open_id or None


def _extract_message_text_from_content(content_value: object) -> str:
    if not isinstance(content_value, str) or not content_value.strip():
        return ""
    try:
        parsed = cast(object, loads(content_value))
    except JSONDecodeError:
        return content_value.strip()
    if isinstance(parsed, dict):
        text = parsed.get("text")
        if isinstance(text, str):
            return text
    return content_value.strip()


def _extract_mention_names(payload: dict[str, JsonValue]) -> tuple[str, ...]:
    collected: list[str] = []
    seen: set[str] = set()
    event_value = payload.get("event")
    message_value = (
        event_value.get("message") if isinstance(event_value, dict) else None
    )
    if isinstance(message_value, dict):
        mentions_value = message_value.get("mentions")
        if isinstance(mentions_value, list):
            for mention_value in mentions_value:
                if not isinstance(mention_value, dict):
                    continue
                _add_mention_name(collected, seen, mention_value.get("name"))
        content_value = message_value.get("content")
        if isinstance(content_value, str):
            for match in _AT_TAG_LABEL_PATTERN.finditer(content_value):
                _add_mention_name(collected, seen, match.group(1))
    return tuple(collected)


def _add_mention_name(
    collected: list[str],
    seen: set[str],
    raw_value: object,
) -> None:
    if not isinstance(raw_value, str):
        return
    normalized = _normalize_name(raw_value)
    if normalized is None or normalized in seen:
        return
    seen.add(normalized)
    collected.append(raw_value.strip())


def _mention_targets_app(*, mention_names: tuple[str, ...], app_name: str) -> bool:
    normalized_app_name = _normalize_name(app_name)
    if normalized_app_name is None:
        return False
    return any(
        _normalize_name(mention_name) == normalized_app_name
        for mention_name in mention_names
    )


def _normalize_name(value: str) -> str | None:
    normalized = value.strip().casefold()
    return normalized or None


def _sanitize_trigger_text(raw_text: str, *, mentioned: bool) -> str:
    cleaned = raw_text.strip()
    if mentioned:
        cleaned = _LEADING_MENTION_TOKEN_PATTERN.sub("", cleaned).strip()
    return cleaned


def _is_sender_bot(sender_type: str | None) -> bool:
    if sender_type is None:
        return False
    lowered = sender_type.strip().lower()
    return lowered in {"app", "bot"}


def _build_session_title(trigger_name: str, source_label: str) -> str:
    normalized_trigger_name = str(trigger_name).strip()
    normalized_source_label = str(source_label).strip()
    if not normalized_source_label:
        return normalized_trigger_name
    return f"{normalized_trigger_name} · {normalized_source_label}"


def _build_fallback_source_label(chat_type: str, chat_id: str) -> str:
    normalized_chat_type = str(chat_type).strip().lower()
    if normalized_chat_type == "group":
        prefix = "Group"
    elif normalized_chat_type == "p2p":
        prefix = "DM"
    else:
        prefix = "Chat"
    return f"{prefix} {_short_chat_id(chat_id)}"


def _short_chat_id(chat_id: str) -> str:
    normalized_chat_id = str(chat_id).strip()
    if len(normalized_chat_id) <= 8:
        return normalized_chat_id
    return normalized_chat_id[-8:]


def _feishu_event_key(message: FeishuNormalizedMessage) -> str:
    normalized_message_id = str(message.message_id).strip()
    if normalized_message_id:
        return normalized_message_id
    return message.event_id


def _extract_content_preview(
    msg: dict[str, object],
    *,
    max_length: int = 60,
) -> str:
    message_payload = msg.get("message")
    if not isinstance(message_payload, dict):
        return "(no content)"
    parts = message_payload.get("parts")
    if not isinstance(parts, list) or not parts:
        return "(no content)"
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("part_kind", ""))
        if kind not in {"user-prompt", "text"}:
            continue
        content = part.get("content")
        if isinstance(content, str) and content.strip():
            text = content.strip().replace("\n", " ")
            if len(text) > max_length:
                return text[:max_length] + "..."
            return text
    return "(no content)"
