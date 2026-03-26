# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_teams.automation.automation_models import (
    AutomationFeishuBinding,
    AutomationFeishuBindingCandidate,
)
from agent_teams.gateway.feishu import (
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_PLATFORM,
    SESSION_METADATA_SOURCE_LABEL_KEY,
)
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.session_repository import SessionRepository


class FeishuAccountLike(Protocol):
    account_id: str
    display_name: str


class FeishuAccountLookup(Protocol):
    def get_account(self, account_id: str) -> FeishuAccountLike: ...


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(self, trigger_id: str) -> object | None: ...


class AutomationFeishuBindingService:
    def __init__(
        self,
        *,
        external_session_binding_repo: ExternalSessionBindingRepository,
        session_repo: SessionRepository,
        account_lookup: FeishuAccountLookup,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
    ) -> None:
        self._external_session_binding_repo = external_session_binding_repo
        self._session_repo = session_repo
        self._account_lookup = account_lookup
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
                account = self._account_lookup.get_account(binding.trigger_id)
                if (
                    self._runtime_config_lookup.get_runtime_config_by_trigger_id(
                        binding.trigger_id
                    )
                    is None
                ):
                    continue
                session = self._session_repo.get(binding.session_id)
            except KeyError:
                continue
            metadata = session.metadata
            chat_type = (
                str(metadata.get(FEISHU_METADATA_CHAT_TYPE_KEY, "")).strip()
                or "unknown"
            )
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
                    trigger_name=account.display_name,
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
        _ = self._account_lookup.get_account(binding.trigger_id)
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
    if title:
        return title
    source_label = str(metadata.get(SESSION_METADATA_SOURCE_LABEL_KEY, "")).strip()
    if source_label:
        return source_label
    return session_id


__all__ = ["AutomationFeishuBindingService"]
