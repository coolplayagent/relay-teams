# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from agent_teams.automation import AutomationProjectRecord
from agent_teams.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FEISHU_PLATFORM,
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
)
from agent_teams.sessions.session_models import ProjectKind, SessionRecord
from agent_teams.tools.registry.registry import ToolResolutionContext

FEISHU_IMPLICIT_TOOLS: tuple[str, ...] = ("feishu_send",)


class _SessionLookup(Protocol):
    def get(self, session_id: str) -> SessionRecord: ...


class _RuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> FeishuTriggerRuntimeConfig | None: ...


class _AutomationProjectLookup(Protocol):
    def get(self, automation_project_id: str) -> AutomationProjectRecord: ...


class FeishuChatContext:
    def __init__(self, *, chat_id: str, environment: FeishuEnvironment) -> None:
        self.chat_id = chat_id
        self.environment = environment


def resolve_feishu_chat_context(
    *,
    session_repo: _SessionLookup,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None = None,
    session_id: str,
) -> FeishuChatContext | None:
    try:
        session = session_repo.get(session_id)
    except KeyError:
        return None
    return _resolve_from_session(
        session=session,
        runtime_config_lookup=runtime_config_lookup,
        automation_project_repo=automation_project_repo,
    )


class FeishuToolContextResolver:
    def __init__(
        self,
        *,
        session_repo: _SessionLookup,
        runtime_config_lookup: _RuntimeConfigLookup,
        automation_project_repo: _AutomationProjectLookup | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._runtime_config_lookup = runtime_config_lookup
        self._automation_project_repo = automation_project_repo

    def resolve_implicit_tools(
        self,
        context: ToolResolutionContext,
    ) -> tuple[str, ...]:
        session_id = context.session_id.strip()
        if not session_id:
            return ()
        chat_context = resolve_feishu_chat_context(
            session_repo=self._session_repo,
            runtime_config_lookup=self._runtime_config_lookup,
            automation_project_repo=self._automation_project_repo,
            session_id=session_id,
        )
        if chat_context is None:
            return ()
        return FEISHU_IMPLICIT_TOOLS


def _resolve_from_session(
    *,
    session: SessionRecord,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None,
) -> FeishuChatContext | None:
    metadata = session.metadata
    if str(metadata.get(FEISHU_METADATA_PLATFORM_KEY, "")).strip() != FEISHU_PLATFORM:
        return _resolve_automation_binding_context(
            session=session,
            runtime_config_lookup=runtime_config_lookup,
            automation_project_repo=automation_project_repo,
        )
    trigger_id = str(metadata.get(FEISHU_METADATA_TRIGGER_ID_KEY, "")).strip()
    if not trigger_id:
        return None
    chat_id = str(metadata.get(FEISHU_METADATA_CHAT_ID_KEY, "")).strip()
    if not chat_id:
        return None
    runtime_config = runtime_config_lookup.get_runtime_config_by_trigger_id(trigger_id)
    if runtime_config is None:
        return None
    return FeishuChatContext(
        chat_id=chat_id,
        environment=runtime_config.environment,
    )


def _resolve_automation_binding_context(
    *,
    session: SessionRecord,
    runtime_config_lookup: _RuntimeConfigLookup,
    automation_project_repo: _AutomationProjectLookup | None,
) -> FeishuChatContext | None:
    if (
        session.project_kind != ProjectKind.AUTOMATION
        or automation_project_repo is None
    ):
        return None
    automation_project_id = str(session.project_id or "").strip()
    if not automation_project_id:
        return None
    try:
        project = automation_project_repo.get(automation_project_id)
    except KeyError:
        return None
    binding = project.delivery_binding
    if binding is None:
        return None
    runtime_config = runtime_config_lookup.get_runtime_config_by_trigger_id(
        binding.trigger_id
    )
    if runtime_config is None:
        return None
    return FeishuChatContext(
        chat_id=binding.chat_id,
        environment=runtime_config.environment,
    )
