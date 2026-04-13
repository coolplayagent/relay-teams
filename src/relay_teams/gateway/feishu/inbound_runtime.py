# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Protocol

from relay_teams.gateway.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_MESSAGE_ID_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_SENDER_NAME_KEY,
    FEISHU_METADATA_SENDER_OPEN_ID_KEY,
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
)
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.providers.token_usage_repo import SessionTokenUsage
from relay_teams.sessions.runs.run_models import RuntimePromptConversationContext
from relay_teams.sessions import ExternalSessionBindingRepository
from relay_teams.sessions.runs.enums import ExecutionMode
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.session_models import SessionMode, SessionRecord

logger = get_logger(__name__)


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

    def sync_session_metadata(
        self, session_id: str, metadata: dict[str, str]
    ) -> None: ...

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]: ...

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage: ...

    def clear_session_messages(self, session_id: str) -> int: ...


class RunServiceLike(Protocol):
    def create_run(self, intent: IntentInput) -> tuple[str, str]: ...

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]: ...

    def ensure_run_started(self, run_id: str) -> None: ...

    def stop_run(self, run_id: str) -> None: ...


class FeishuClientLike(Protocol):
    def get_chat_name(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None: ...

    def resolve_user_name(
        self,
        *,
        open_id: str,
        chat_id: str | None = None,
        environment: FeishuEnvironment | None = None,
    ) -> str | None: ...


class FeishuInboundRuntime:
    def __init__(
        self,
        *,
        session_service: SessionServiceLike,
        run_service: RunServiceLike,
        external_session_binding_repo: ExternalSessionBindingRepository,
        feishu_client: FeishuClientLike | None = None,
        session_ingress_service: GatewaySessionIngressService | None = None,
    ) -> None:
        self._session_service = session_service
        self._run_service = run_service
        self._external_session_binding_repo = external_session_binding_repo
        self._feishu_client = feishu_client
        self._session_ingress_service = session_ingress_service

    def start_run(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> tuple[str, str]:
        session_id = self.resolve_session_id(
            runtime_config=runtime_config,
            message=message,
        )
        intent = IntentInput(
            session_id=session_id,
            input=content_parts_from_text(self._build_run_intent_text(message=message)),
            execution_mode=ExecutionMode.AI,
            yolo=runtime_config.target.yolo,
            thinking=runtime_config.target.thinking,
            conversation_context=self._build_conversation_context(message=message),
        )
        if self._session_ingress_service is not None:
            result = self._session_ingress_service.require_started(
                GatewaySessionIngressRequest(intent=intent)
            )
            if result.run_id is None:
                raise RuntimeError("session_busy")
            return session_id, result.run_id
        run_id, _session_id = self._run_service.create_detached_run(intent)
        self._run_service.ensure_run_started(run_id)
        return session_id, run_id

    def resolve_session_id(
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
            self._session_service.sync_session_metadata(
                session.session_id, merged_metadata
            )
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

    def stop_run(self, run_id: str) -> None:
        self._run_service.stop_run(run_id)

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
            FEISHU_METADATA_MESSAGE_ID_KEY: message.message_id,
            SESSION_METADATA_SOURCE_KIND_KEY: SESSION_SOURCE_KIND_IM,
            SESSION_METADATA_SOURCE_PROVIDER_KEY: FEISHU_PLATFORM,
            SESSION_METADATA_SOURCE_LABEL_KEY: source_label,
            SESSION_METADATA_SOURCE_ICON_KEY: SESSION_SOURCE_ICON_IM,
            "title": _build_session_title(runtime_config.trigger_name, source_label),
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
        }
        sender_name = str(message.sender_name or "").strip()
        sender_open_id = str(message.sender_open_id or "").strip()
        if sender_name:
            metadata[FEISHU_METADATA_SENDER_NAME_KEY] = sender_name
        else:
            metadata.pop(FEISHU_METADATA_SENDER_NAME_KEY, None)
        if sender_open_id:
            metadata[FEISHU_METADATA_SENDER_OPEN_ID_KEY] = sender_open_id
        else:
            metadata.pop(FEISHU_METADATA_SENDER_OPEN_ID_KEY, None)
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
            resolved = self._feishu_client.resolve_user_name(
                open_id=normalized_open_id,
                chat_id=chat_id,
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

    def _build_run_intent_text(self, *, message: FeishuNormalizedMessage) -> str:
        if message.chat_type.strip().lower() != "group":
            return message.trigger_text
        sender_label = (
            str(message.sender_name or "").strip()
            or str(message.sender_open_id or "").strip()
        )
        if not sender_label:
            sender_label = "unknown_sender"
        return f"收到来自 {sender_label} 的飞书消息：{message.trigger_text}"

    @staticmethod
    def _build_conversation_context(
        *,
        message: FeishuNormalizedMessage,
    ) -> RuntimePromptConversationContext:
        return RuntimePromptConversationContext(
            source_provider=FEISHU_PLATFORM,
            source_kind=SESSION_SOURCE_KIND_IM,
            feishu_chat_type=message.chat_type,
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


def _build_session_title(trigger_name: str, source_label: str) -> str:
    normalized_trigger_name = str(trigger_name).strip()
    normalized_source_label = str(source_label).strip()
    if not normalized_source_label:
        return normalized_trigger_name
    return f"{normalized_trigger_name} - {normalized_source_label}"


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
