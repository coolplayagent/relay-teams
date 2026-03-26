# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from json import JSONDecodeError, loads
from typing import TYPE_CHECKING, Protocol, cast

from lark_oapi.api.im.v1.model.event_message import EventMessage
from lark_oapi.api.im.v1.model.event_sender import EventSender
from lark_oapi.api.im.v1.model.user_id import UserId
from lark_oapi.core.json import JSON
from lark_oapi.event.context import EventHeader
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
from pydantic import JsonValue

from agent_teams.gateway.feishu.models import (
    FEISHU_PLATFORM,
    FeishuChatQueueClearResult,
    FeishuChatQueueSummary,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    TriggerProcessingResult,
)
from agent_teams.logger import get_logger, log_event

if TYPE_CHECKING:
    from agent_teams.gateway.im import ImSessionCommandService, ImToolService

_AT_TAG_PATTERN = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE)
_AT_TAG_LABEL_PATTERN = re.compile(r"<at\b[^>]*>(.*?)</at>", re.IGNORECASE)
_LEADING_MENTION_TOKEN_PATTERN = re.compile(r"^(?:@\S+\s*)+")

logger = get_logger(__name__)


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None: ...


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
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        message_pool_service: FeishuMessagePoolServiceLike,
        im_tool_service: ImToolService,
        im_session_command_service: ImSessionCommandService,
    ) -> None:
        self._runtime_config_lookup = runtime_config_lookup
        self._message_pool_service = message_pool_service
        self._im_tool_service = im_tool_service
        self._im_session_command_service = im_session_command_service

    def handle_sdk_event(
        self,
        *,
        trigger_id: str,
        event: P2ImMessageReceiveV1,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            trigger_id
        )
        if runtime_config is None:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger_id,
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
        response_text = self._im_session_command_service.handle_feishu_command(
            runtime_config=runtime_config,
            message=normalized,
        )
        if response_text is not None:
            self._send_command_response(
                chat_id=normalized.chat_id,
                chat_type=normalized.chat_type,
                message_id=normalized.message_id,
                text=response_text,
                runtime_config=runtime_config,
            )
            return TriggerProcessingResult(
                status="command",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                event_id=normalized.event_id,
                reason="session_command",
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

        return self._message_pool_service.enqueue_message(
            runtime_config=runtime_config,
            normalized=normalized,
            raw_body=raw_body,
            headers=headers,
            remote_addr=remote_addr,
        )

    def _send_command_response(
        self,
        *,
        chat_id: str,
        chat_type: str,
        message_id: str,
        text: str,
        runtime_config: FeishuTriggerRuntimeConfig,
    ) -> None:
        try:
            self._im_tool_service.send_text_to_feishu_chat(
                chat_id=chat_id,
                text=text,
                environment=runtime_config.environment,
                reply_to_message_id=(
                    message_id if chat_type.strip().lower() == "group" else None
                ),
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.command_response.send_failed",
                message="Failed to send command response to Feishu chat",
                payload={"chat_id": chat_id, "error": str(exc)},
            )


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
