# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.tools.feishu_tools import FeishuToolContextResolver, FeishuToolService
from agent_teams.tools.registry import ToolResolutionContext


_ENV = FeishuEnvironment(
    app_id="app-1",
    app_secret="secret-1",
    verification_token="vt",
    encrypt_key="ek",
)

_SOURCE = FeishuTriggerSourceConfig(app_id="app-1", app_name="test-app")
_TARGET = FeishuTriggerTargetConfig()

_CHAT_ID = "oc_test_chat"
_TRIGGER_ID = "trigger-1"
_SESSION_ID = "session-1"


def _make_session(
    *,
    session_id: str = _SESSION_ID,
    platform: str = "feishu",
    chat_id: str = _CHAT_ID,
    trigger_id: str = _TRIGGER_ID,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        workspace_id="ws-1",
        metadata={
            FEISHU_METADATA_PLATFORM_KEY: platform,
            FEISHU_METADATA_CHAT_ID_KEY: chat_id,
            FEISHU_METADATA_TRIGGER_ID_KEY: trigger_id,
        },
    )


class _FakeSessionRepo:
    def __init__(self, sessions: dict[str, SessionRecord] | None = None) -> None:
        self._sessions = sessions or {}

    def get(self, session_id: str) -> SessionRecord:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return self._sessions[session_id]


class _FakeRuntimeConfigLookup:
    def __init__(
        self,
        configs: dict[str, FeishuTriggerRuntimeConfig] | None = None,
    ) -> None:
        self._configs = configs or {}

    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> FeishuTriggerRuntimeConfig | None:
        return self._configs.get(trigger_id)


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_files: list[tuple[str, Path]] = []

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        self.sent_texts.append((chat_id, text))

    def send_file(
        self,
        *,
        chat_id: str,
        file_path: Path,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        self.sent_files.append((chat_id, file_path))
        return f"file sent ({file_path.name})"


def _build_service(
    *,
    sessions: dict[str, SessionRecord] | None = None,
    configs: dict[str, FeishuTriggerRuntimeConfig] | None = None,
    feishu_client: _FakeFeishuClient | None = None,
) -> tuple[FeishuToolService, _FakeFeishuClient]:
    client = feishu_client or _FakeFeishuClient()
    service = FeishuToolService(
        session_repo=_FakeSessionRepo(sessions),
        runtime_config_lookup=_FakeRuntimeConfigLookup(configs),
        feishu_client=client,
    )
    return service, client


def _build_context_resolver(
    *,
    sessions: dict[str, SessionRecord] | None = None,
    configs: dict[str, FeishuTriggerRuntimeConfig] | None = None,
) -> FeishuToolContextResolver:
    return FeishuToolContextResolver(
        session_repo=_FakeSessionRepo(sessions),
        runtime_config_lookup=_FakeRuntimeConfigLookup(configs),
    )


def _default_configs() -> dict[str, FeishuTriggerRuntimeConfig]:
    return {
        _TRIGGER_ID: FeishuTriggerRuntimeConfig(
            trigger_id=_TRIGGER_ID,
            trigger_name="test-trigger",
            source=_SOURCE,
            target=_TARGET,
            environment=_ENV,
        ),
    }


def _default_sessions() -> dict[str, SessionRecord]:
    return {_SESSION_ID: _make_session()}


def test_resolver_returns_feishu_tool_for_feishu_session() -> None:
    resolver = _build_context_resolver(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ("feishu_send",)


def test_resolver_returns_no_tool_without_feishu_context() -> None:
    resolver = _build_context_resolver(
        sessions={_SESSION_ID: _make_session(platform="other")},
        configs=_default_configs(),
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ()


def test_send_text_success() -> None:
    service, client = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )
    result = service.send_text(session_id=_SESSION_ID, text="hello")
    assert result == "Message sent."
    assert len(client.sent_texts) == 1
    assert client.sent_texts[0] == (_CHAT_ID, "hello")


def test_send_text_no_feishu_session() -> None:
    session = _make_session(platform="other")
    service, client = _build_service(
        sessions={_SESSION_ID: session},
        configs=_default_configs(),
    )
    result = service.send_text(session_id=_SESSION_ID, text="hello")
    assert "not linked" in result
    assert len(client.sent_texts) == 0


def test_send_text_unknown_session() -> None:
    service, client = _build_service(configs=_default_configs())
    result = service.send_text(session_id="nonexistent", text="hello")
    assert "not linked" in result
    assert len(client.sent_texts) == 0


def test_send_text_no_runtime_config() -> None:
    service, client = _build_service(sessions=_default_sessions(), configs={})
    result = service.send_text(session_id=_SESSION_ID, text="hello")
    assert "not linked" in result
    assert len(client.sent_texts) == 0


def test_send_file_success(tmp_path: Path) -> None:
    test_file = tmp_path / "report.pdf"
    test_file.write_text("content")
    service, client = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )
    result = service.send_file(session_id=_SESSION_ID, file_path=test_file)
    assert "file sent" in result
    assert len(client.sent_files) == 1
    assert client.sent_files[0] == (_CHAT_ID, test_file)


def test_send_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    service, _ = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )
    with pytest.raises(FileNotFoundError):
        service.send_file(session_id=_SESSION_ID, file_path=missing)


def test_send_file_no_feishu_session(tmp_path: Path) -> None:
    test_file = tmp_path / "report.pdf"
    test_file.write_text("content")
    session = _make_session(platform="other")
    service, client = _build_service(
        sessions={_SESSION_ID: session},
        configs=_default_configs(),
    )
    result = service.send_file(session_id=_SESSION_ID, file_path=test_file)
    assert "not linked" in result
    assert len(client.sent_files) == 0


def test_resolve_context_missing_chat_id() -> None:
    session = _make_session(chat_id="")
    service, client = _build_service(
        sessions={_SESSION_ID: session},
        configs=_default_configs(),
    )
    result = service.send_text(session_id=_SESSION_ID, text="hello")
    assert "not linked" in result
    assert len(client.sent_texts) == 0


def test_resolve_context_missing_trigger_id() -> None:
    session = _make_session(trigger_id="")
    service, client = _build_service(
        sessions={_SESSION_ID: session},
        configs=_default_configs(),
    )
    result = service.send_text(session_id=_SESSION_ID, text="hello")
    assert "not linked" in result
    assert len(client.sent_texts) == 0
