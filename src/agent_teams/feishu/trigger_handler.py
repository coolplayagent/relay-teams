# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from json import JSONDecodeError, loads
from typing import Protocol, cast

from lark_oapi.api.im.v1.model.event_message import EventMessage
from lark_oapi.api.im.v1.model.event_sender import EventSender
from lark_oapi.api.im.v1.model.user_id import UserId
from lark_oapi.core.json import JSON
from lark_oapi.event.context import EventHeader
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
from pydantic import JsonValue

from agent_teams.feishu.models import (
    FEISHU_PLATFORM,
    FeishuChatQueueClearResult,
    FeishuChatQueueItemPreview,
    FeishuChatQueueSummary,
    FeishuEnvironment,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    TriggerProcessingResult,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.providers.token_usage_repo import SessionTokenUsage
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.runs.run_models import IntentInput
from agent_teams.sessions.session_models import SessionMode, SessionRecord
from agent_teams.triggers import (
    TriggerDefinition,
    TriggerIngestInput,
    TriggerIngestResult,
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
    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...


class FeishuMessagePoolServiceLike(Protocol):
    def enqueue_message(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        normalized: FeishuNormalizedMessage,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult: ...

    def get_chat_summary(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
        preview_limit: int = 3,
    ) -> FeishuChatQueueSummary: ...

    def clear_chat(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
    ) -> FeishuChatQueueClearResult: ...


class FeishuTriggerHandler:
    def __init__(
        self,
        *,
        trigger_service: TriggerServiceLike,
        feishu_config_service: FeishuConfigResolverLike,
        session_service: SessionServiceLike,
        run_service: RunServiceLike,
        external_session_binding_repo: ExternalSessionBindingRepository,
        message_pool_service: FeishuMessagePoolServiceLike,
        feishu_client: FeishuClientLike | None = None,
    ) -> None:
        self._trigger_service = trigger_service
        self._feishu_config_service = feishu_config_service
        self._session_service = session_service
        self._run_service = run_service
        self._external_session_binding_repo = external_session_binding_repo
        self._message_pool_service = message_pool_service
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
        return self._message_pool_service.enqueue_message(
            runtime_config=runtime_config,
            normalized=normalized,
            raw_body=raw_body,
            headers=headers,
            remote_addr=remote_addr,
        )

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
        else:
            response_text = self._cmd_clear(
                runtime_config=runtime_config,
                message=normalized,
            )
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
            "status - Show current session and queue state",
            "clear  - Clear session context and queued messages",
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
        queue_summary = self._message_pool_service.get_chat_summary(
            trigger_id=runtime_config.trigger_id,
            tenant_key=message.tenant_key,
            chat_id=message.chat_id,
        )
        lines = ["[Session Status]", ""]
        if session_id is None:
            lines.append("Session: (none)")
        else:
            messages = self._session_service.get_session_messages(session_id)
            usage = self._session_service.get_token_usage_by_session(session_id)
            lines.extend(
                [
                    f"Session: {session_id}",
                    f"Messages: {len(messages)}",
                    f"Tokens: input={usage.total_input_tokens}"
                    f"  output={usage.total_output_tokens}"
                    f"  total={usage.total_tokens}",
                    f"Requests: {usage.total_requests}",
                ]
            )
            recent = messages[-3:]
            if recent:
                lines.append("")
                lines.append("Recent messages:")
                for msg in recent:
                    role = str(msg.get("role", "unknown"))
                    preview = _extract_content_preview(msg)
                    lines.append(f"  [{role}] {preview}")

        lines.append("")
        lines.append(
            "Queue: "
            f"active={queue_summary.active_total} "
            f"queued={queue_summary.queued_count} "
            f"claimed={queue_summary.claimed_count} "
            f"waiting={queue_summary.waiting_result_count} "
            f"retryable_failed={queue_summary.retryable_failed_count} "
            f"dead_letter={queue_summary.dead_letter_count} "
            f"cancelled={queue_summary.cancelled_count}"
        )
        if queue_summary.processing_item is not None:
            lines.append("")
            lines.append(
                "Processing: " + self._format_queue_item(queue_summary.processing_item)
            )
        if queue_summary.queued_items:
            lines.append("")
            lines.append("Queued messages:")
            for item in queue_summary.queued_items:
                lines.append("  " + self._format_queue_item(item))
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
        cleared_session_messages = 0
        if session_id is not None:
            try:
                cleared_session_messages = self._session_service.clear_session_messages(
                    session_id
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    event="feishu.command.clear_failed",
                    message="Failed to clear session messages",
                    payload={"session_id": session_id, "error": str(exc)},
                )
                return "[Clear] Failed to clear session messages."
        try:
            queue_result = self._message_pool_service.clear_chat(
                trigger_id=runtime_config.trigger_id,
                tenant_key=message.tenant_key,
                chat_id=message.chat_id,
            )
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.command.clear_queue_failed",
                message="Failed to clear queued Feishu messages",
                payload={
                    "trigger_id": runtime_config.trigger_id,
                    "tenant_key": message.tenant_key,
                    "chat_id": message.chat_id,
                    "error": str(exc),
                },
            )
            return "[Clear] Failed to clear queued messages."
        if session_id is None and queue_result.cleared_queue_count == 0:
            return "[Clear] No active session or queued messages. Nothing to clear."
        return (
            "[Clear] "
            f"Cleared {cleared_session_messages} active session messages and "
            f"{queue_result.cleared_queue_count} queued messages. "
            f"Stopped {queue_result.stopped_run_count} active runs."
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

    def _format_queue_item(self, item: FeishuChatQueueItemPreview) -> str:
        segments = [item.processing_status.value]
        if item.intent_preview:
            segments.append(item.intent_preview)
        if item.run_id:
            segments.append(f"run={item.run_id}")
        if item.run_status:
            segments.append(f"status={item.run_status}")
        if item.run_phase:
            segments.append(f"phase={item.run_phase}")
        if item.blocking_reason:
            segments.append(f"blocked={item.blocking_reason}")
        if item.last_error:
            segments.append(f"error={item.last_error}")
        return " | ".join(segments)


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
