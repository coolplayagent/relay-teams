# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_teams.tools.feishu_tools.context import _AutomationProjectLookup
from agent_teams.feishu.models import FeishuEnvironment
from agent_teams.tools.feishu_tools.context import (
    FeishuChatContext,
    _RuntimeConfigLookup,
    _SessionLookup,
    resolve_feishu_chat_context,
)


class _FeishuSender(Protocol):
    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...

    def send_file(
        self,
        *,
        chat_id: str,
        file_path: Path,
        environment: FeishuEnvironment | None = None,
    ) -> str: ...


class FeishuToolService:
    def __init__(
        self,
        *,
        session_repo: _SessionLookup,
        runtime_config_lookup: _RuntimeConfigLookup,
        automation_project_repo: _AutomationProjectLookup | None = None,
        feishu_client: _FeishuSender,
    ) -> None:
        self._session_repo = session_repo
        self._runtime_config_lookup = runtime_config_lookup
        self._automation_project_repo = automation_project_repo
        self._feishu_client = feishu_client

    def send_text(self, *, session_id: str, text: str) -> str:
        ctx = self._resolve_context(session_id)
        if ctx is None:
            return "Session is not linked to a Feishu chat."
        self._feishu_client.send_text_message(
            chat_id=ctx.chat_id,
            text=text,
            environment=ctx.environment,
        )
        return "Message sent."

    def send_file(self, *, session_id: str, file_path: Path) -> str:
        ctx = self._resolve_context(session_id)
        if ctx is None:
            return "Session is not linked to a Feishu chat."
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        return self._feishu_client.send_file(
            chat_id=ctx.chat_id,
            file_path=file_path,
            environment=ctx.environment,
        )

    def _resolve_context(self, session_id: str) -> FeishuChatContext | None:
        return resolve_feishu_chat_context(
            session_repo=self._session_repo,
            runtime_config_lookup=self._runtime_config_lookup,
            automation_project_repo=self._automation_project_repo,
            session_id=session_id,
        )
