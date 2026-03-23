# -*- coding: utf-8 -*-
from __future__ import annotations

from json import JSONDecodeError, loads
import logging
import re
from typing import Protocol, cast

from lark_oapi.core.json import JSON
from lark_oapi.event.context import EventHeader
from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
from lark_oapi.api.im.v1.model.event_message import EventMessage
from lark_oapi.api.im.v1.model.event_sender import EventSender
from lark_oapi.api.im.v1.model.user_id import UserId
from pydantic import JsonValue

from agent_teams.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TENANT_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FEISHU_PLATFORM,
    FeishuNormalizedMessage,
    TriggerProcessingResult,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.sessions import (
    ExternalSessionBindingRepository,
)
from agent_teams.sessions.runs.enums import ExecutionMode
from agent_teams.sessions.runs.run_models import IntentInput
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.triggers import (
    TriggerDefinition,
    TriggerIngestInput,
    TriggerIngestResult,
    TriggerSourceType,
    TriggerStatus,
)

_AT_TAG_PATTERN = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE)
_LEADING_MENTION_TOKEN_PATTERN = re.compile(r"^(?:@\S+\s*)+")
logger = get_logger(__name__)


class TriggerServiceLike(Protocol):
    def list_triggers(self) -> tuple[TriggerDefinition, ...] | list[TriggerDefinition]: ...

    def ingest_event(
        self,
        event: TriggerIngestInput,
        *,
        headers: dict[str, str],
        remote_addr: str | None,
        raw_body: str,
    ) -> TriggerIngestResult: ...


class SessionServiceLike(Protocol):
    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord: ...

    def get_session(self, session_id: str) -> SessionRecord: ...

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None: ...


class RunServiceLike(Protocol):
    def create_run(self, intent: IntentInput) -> tuple[str, str]: ...

    def ensure_run_started(self, run_id: str) -> None: ...


class FeishuTriggerHandler:
    def __init__(
        self,
        *,
        trigger_service: TriggerServiceLike,
        session_service: SessionServiceLike,
        run_service: RunServiceLike,
        external_session_binding_repo: ExternalSessionBindingRepository,
    ) -> None:
        self._trigger_service = trigger_service
        self._session_service = session_service
        self._run_service = run_service
        self._external_session_binding_repo = external_session_binding_repo

    def handle_sdk_event(
        self,
        *,
        event: P2ImMessageReceiveV1,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        trigger = self._resolve_active_trigger()
        if trigger is None:
            return TriggerProcessingResult(
                status="ignored",
                ignored=True,
                reason="no_enabled_trigger",
            )
        normalized = _normalize_sdk_message(event)
        return self._handle_normalized_message(
            trigger=trigger,
            normalized=normalized,
            raw_body=raw_body,
            headers=headers,
            remote_addr=remote_addr,
        )

    def has_enabled_feishu_trigger(self) -> bool:
        return self._resolve_active_trigger(log_conflict=False) is not None

    def _handle_normalized_message(
        self,
        *,
        trigger: TriggerDefinition,
        normalized: FeishuNormalizedMessage | None,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        if normalized is None:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                ignored=True,
                reason="unsupported_event_type",
            )
        if normalized.chat_type.lower() != "group":
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                event_id=normalized.event_id,
                ignored=True,
                reason="unsupported_chat_type",
            )
        if _is_sender_bot(normalized.sender_type):
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                event_id=normalized.event_id,
                ignored=True,
                reason="sender_is_bot",
            )

        trigger_rule = _trigger_rule(trigger)
        if trigger_rule == "mention_only" and not normalized.mentioned:
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                event_id=normalized.event_id,
                ignored=True,
                reason="mention_required",
            )
        if not normalized.trigger_text.strip():
            return TriggerProcessingResult(
                status="ignored",
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                event_id=normalized.event_id,
                ignored=True,
                reason="empty_trigger_text",
            )

        ingest_result = self._trigger_service.ingest_event(
            TriggerIngestInput(
                trigger_id=trigger.trigger_id,
                source_type=TriggerSourceType.IM,
                event_key=normalized.event_id,
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

        session_id = self._resolve_session_id(trigger=trigger, message=normalized)
        run_id, _session_id = self._run_service.create_run(
            IntentInput(
                session_id=session_id,
                intent=normalized.trigger_text,
                execution_mode=ExecutionMode.AI,
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

    def _resolve_active_trigger(
        self,
        *,
        log_conflict: bool = True,
    ) -> TriggerDefinition | None:
        candidates = [
            trigger
            for trigger in self._trigger_service.list_triggers()
            if _is_enabled_feishu_trigger(trigger)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda trigger: (trigger.name.lower(), trigger.trigger_id))
        active = candidates[0]
        if log_conflict and len(candidates) > 1:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.subscription.multiple_triggers",
                message="Multiple enabled Feishu triggers found; using the first trigger",
                payload={
                    "active_trigger_id": active.trigger_id,
                    "active_trigger_name": active.name,
                    "enabled_trigger_ids": [trigger.trigger_id for trigger in candidates],
                },
            )
        return active

    def _resolve_session_id(
        self,
        *,
        trigger: TriggerDefinition,
        message: FeishuNormalizedMessage,
    ) -> str:
        binding = self._external_session_binding_repo.get_binding(
            platform=FEISHU_PLATFORM,
            tenant_key=message.tenant_key,
            external_chat_id=message.chat_id,
        )
        metadata = {
            FEISHU_METADATA_PLATFORM_KEY: FEISHU_PLATFORM,
            FEISHU_METADATA_TENANT_KEY: message.tenant_key,
            FEISHU_METADATA_CHAT_ID_KEY: message.chat_id,
            FEISHU_METADATA_CHAT_TYPE_KEY: message.chat_type,
            FEISHU_METADATA_TRIGGER_ID_KEY: trigger.trigger_id,
        }
        if binding is not None:
            try:
                session = self._session_service.get_session(binding.session_id)
            except KeyError:
                session = self._session_service.create_session(
                    workspace_id=_workspace_id(trigger),
                    metadata=metadata,
                )
                self._external_session_binding_repo.upsert_binding(
                    platform=FEISHU_PLATFORM,
                    tenant_key=message.tenant_key,
                    external_chat_id=message.chat_id,
                    session_id=session.session_id,
                )
                return session.session_id
            merged_metadata = dict(session.metadata)
            merged_metadata.update(metadata)
            self._session_service.update_session(session.session_id, merged_metadata)
            return session.session_id

        session = self._session_service.create_session(
            workspace_id=_workspace_id(trigger),
            metadata=metadata,
        )
        self._external_session_binding_repo.upsert_binding(
            platform=FEISHU_PLATFORM,
            tenant_key=message.tenant_key,
            external_chat_id=message.chat_id,
            session_id=session.session_id,
        )
        return session.session_id


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
            payload=payload,
            metadata=_sdk_message_metadata(header, message),
        )

    raw_text = _extract_message_text_from_content(message.content)
    mentioned = "<at " in raw_text.lower() or bool(message.mentions)
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


def _sanitize_trigger_text(raw_text: str, *, mentioned: bool) -> str:
    cleaned = raw_text.strip()
    if mentioned:
        cleaned = _LEADING_MENTION_TOKEN_PATTERN.sub("", cleaned).strip()
    return cleaned


def _is_enabled_feishu_trigger(trigger: TriggerDefinition) -> bool:
    if trigger.source_type != TriggerSourceType.IM:
        return False
    if trigger.status != TriggerStatus.ENABLED:
        return False
    provider = str(trigger.source_config.get("provider", "")).strip().lower()
    return provider == FEISHU_PLATFORM


def _is_sender_bot(sender_type: str | None) -> bool:
    if sender_type is None:
        return False
    lowered = sender_type.strip().lower()
    return lowered in {"app", "bot"}


def _workspace_id(trigger: TriggerDefinition) -> str:
    if trigger.target_config is None:
        return "default"
    value = trigger.target_config.get("workspace_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "default"


def _trigger_rule(trigger: TriggerDefinition) -> str:
    value = trigger.source_config.get("trigger_rule")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "mention_only"
