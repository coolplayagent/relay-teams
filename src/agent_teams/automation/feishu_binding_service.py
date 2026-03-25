# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_teams.automation.automation_models import (
    AutomationFeishuBinding,
    AutomationFeishuBindingCandidate,
)
from agent_teams.feishu import (
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_PLATFORM,
    SESSION_METADATA_SOURCE_LABEL_KEY,
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_MANUAL,
)
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.triggers import TriggerDefinition


class TriggerLookup(Protocol):
    def get_trigger(self, trigger_id: str) -> TriggerDefinition: ...


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(self, trigger_id: str) -> object | None: ...

    def is_feishu_trigger(self, trigger: TriggerDefinition) -> bool: ...


class AutomationFeishuBindingService:
    def __init__(
        self,
        *,
        external_session_binding_repo: ExternalSessionBindingRepository,
        session_repo: SessionRepository,
        trigger_lookup: TriggerLookup,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
    ) -> None:
        self._external_session_binding_repo = external_session_binding_repo
        self._session_repo = session_repo
        self._trigger_lookup = trigger_lookup
        self._runtime_config_lookup = runtime_config_lookup

    def list_candidates(self) -> tuple[AutomationFeishuBindingCandidate, ...]:
        candidates: list[AutomationFeishuBindingCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for binding in self._external_session_binding_repo.list_by_platform(
            FEISHU_PLATFORM
        ):
            dedupe_key = (
                binding.trigger_id,
                binding.tenant_key,
                binding.external_chat_id,
            )
            if dedupe_key in seen:
                continue
            try:
                trigger = self._trigger_lookup.get_trigger(binding.trigger_id)
                if not self._runtime_config_lookup.is_feishu_trigger(trigger):
                    continue
                session = self._session_repo.get(binding.session_id)
            except KeyError:
                continue
            metadata = session.metadata
            chat_type = str(
                metadata.get(FEISHU_METADATA_CHAT_TYPE_KEY, "")
            ).strip() or "unknown"
            source_label = str(
                metadata.get(SESSION_METADATA_SOURCE_LABEL_KEY, "")
            ).strip() or _fallback_source_label(binding.external_chat_id)
            session_title = _resolve_session_title(
                metadata=metadata,
                session_id=session.session_id,
            )
            candidates.append(
                AutomationFeishuBindingCandidate(
                    trigger_id=binding.trigger_id,
                    trigger_name=trigger.display_name,
                    tenant_key=binding.tenant_key,
                    chat_id=binding.external_chat_id,
                    chat_type=chat_type,
                    source_label=source_label,
                    session_id=binding.session_id,
                    session_title=session_title,
                    updated_at=binding.updated_at,
                )
            )
            seen.add(dedupe_key)
        return tuple(candidates)

    def validate_binding(
        self,
        binding: AutomationFeishuBinding,
    ) -> AutomationFeishuBinding:
        trigger = self._trigger_lookup.get_trigger(binding.trigger_id)
        if not self._runtime_config_lookup.is_feishu_trigger(trigger):
            raise ValueError("delivery_binding.trigger_id must reference a Feishu IM trigger")
        if (
            self._runtime_config_lookup.get_runtime_config_by_trigger_id(
                binding.trigger_id
            )
            is None
        ):
            raise ValueError(
                "delivery_binding.trigger_id does not have usable Feishu credentials"
            )
        exists = self._external_session_binding_repo.exists(
            platform=FEISHU_PLATFORM,
            trigger_id=binding.trigger_id,
            tenant_key=binding.tenant_key,
            external_chat_id=binding.chat_id,
        )
        if not exists:
            raise ValueError(
                "delivery_binding must reference an existing Feishu chat binding"
            )
        for candidate in self.list_candidates():
            if (
                candidate.trigger_id == binding.trigger_id
                and candidate.tenant_key == binding.tenant_key
                and candidate.chat_id == binding.chat_id
            ):
                return AutomationFeishuBinding(
                    trigger_id=candidate.trigger_id,
                    tenant_key=candidate.tenant_key,
                    chat_id=candidate.chat_id,
                    chat_type=candidate.chat_type,
                    source_label=candidate.source_label,
                )
        return binding


def _fallback_source_label(chat_id: str) -> str:
    normalized_chat_id = str(chat_id).strip()
    if len(normalized_chat_id) <= 8:
        return normalized_chat_id
    return normalized_chat_id[-8:]


def _resolve_session_title(*, metadata: Mapping[str, str], session_id: str) -> str:
    title = str(metadata.get("title", "")).strip()
    title_source = str(metadata.get(SESSION_METADATA_TITLE_SOURCE_KEY, "")).strip()
    source_label = str(metadata.get(SESSION_METADATA_SOURCE_LABEL_KEY, "")).strip()
    if title_source == SESSION_TITLE_SOURCE_MANUAL and title:
        return title
    if source_label:
        return source_label
    if title:
        return title
    return session_id


__all__ = ["AutomationFeishuBindingService"]
