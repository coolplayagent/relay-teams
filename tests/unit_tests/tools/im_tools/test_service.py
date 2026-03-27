# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.automation import (
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from agent_teams.gateway.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_MESSAGE_ID_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from agent_teams.gateway import (
    GatewayChannelType,
    GatewaySessionRecord,
)
from agent_teams.media import content_parts_from_text
from agent_teams.sessions.runs.run_models import (
    IntentInput,
    RuntimePromptConversationContext,
)
from agent_teams.sessions.session_models import ProjectKind, SessionRecord
from agent_teams.gateway.im import ImToolContextResolver, ImToolService
from agent_teams.tools.registry import ToolResolutionContext
from agent_teams.gateway.wechat.models import WeChatAccountRecord


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
_AUTOMATION_PROJECT_ID = "aut-1"
_WECHAT_ACCOUNT_ID = "wx-account-1"
_WECHAT_PEER_ID = "wx-peer-1"


def _make_session(
    *,
    session_id: str = _SESSION_ID,
    platform: str = "feishu",
    chat_id: str = _CHAT_ID,
    trigger_id: str = _TRIGGER_ID,
    chat_type: str = "group",
    message_id: str = "om_1",
    project_kind: ProjectKind = ProjectKind.WORKSPACE,
    project_id: str | None = None,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        workspace_id="ws-1",
        project_kind=project_kind,
        project_id=project_id,
        metadata={
            FEISHU_METADATA_PLATFORM_KEY: platform,
            FEISHU_METADATA_CHAT_ID_KEY: chat_id,
            FEISHU_METADATA_CHAT_TYPE_KEY: chat_type,
            FEISHU_METADATA_TRIGGER_ID_KEY: trigger_id,
            FEISHU_METADATA_MESSAGE_ID_KEY: message_id,
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
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None:
        return self._configs.get(trigger_id)


class _FakeAutomationProjectRepo:
    def __init__(
        self,
        projects: dict[str, AutomationProjectRecord] | None = None,
    ) -> None:
        self._projects = projects or {}

    def get(self, automation_project_id: str) -> AutomationProjectRecord:
        if automation_project_id not in self._projects:
            raise KeyError(automation_project_id)
        return self._projects[automation_project_id]


class _FakeGatewaySessionLookup:
    def __init__(
        self,
        sessions: dict[str, GatewaySessionRecord] | None = None,
    ) -> None:
        self._sessions = sessions or {}

    def get_by_internal_session_id(
        self,
        internal_session_id: str,
    ) -> GatewaySessionRecord | None:
        return self._sessions.get(internal_session_id)


class _FakeRunIntentLookup:
    def __init__(self, intents: dict[str, IntentInput] | None = None) -> None:
        self._intents = intents or {}

    def get(self, run_id: str) -> IntentInput:
        if run_id not in self._intents:
            raise KeyError(run_id)
        return self._intents[run_id]


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_texts: list[tuple[str, str]] = []
        self.reply_texts: list[tuple[str, str]] = []
        self.sent_files: list[tuple[str, Path]] = []

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        _ = environment
        self.sent_texts.append((chat_id, text))

    def reply_text_message(
        self,
        *,
        message_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        _ = environment
        self.reply_texts.append((message_id, text))

    def send_file(
        self,
        *,
        chat_id: str,
        file_path: Path,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        _ = environment
        self.sent_files.append((chat_id, file_path))
        return f"file sent ({file_path.name})"


class _FakeWeChatAccountRepo:
    def __init__(self) -> None:
        self._accounts = {
            _WECHAT_ACCOUNT_ID: WeChatAccountRecord(
                account_id=_WECHAT_ACCOUNT_ID,
                display_name="WeChat Account",
                base_url="https://wechat.example.test",
                cdn_base_url="https://cdn.example.test",
            )
        }

    def get_account(self, account_id: str) -> WeChatAccountRecord:
        if account_id not in self._accounts:
            raise KeyError(account_id)
        return self._accounts[account_id]


class _FakeWeChatSecretStore:
    def __init__(self, token: str | None = "wechat-token") -> None:
        self._token = token

    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        _ = (config_dir, account_id)
        return self._token


class _FakeWeChatClient:
    def __init__(self) -> None:
        self.sent_texts: list[tuple[str, str, str, str | None]] = []
        self.sent_files: list[tuple[str, str, Path, str | None]] = []

    def send_text_message(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        self.sent_texts.append(
            (account.account_id, token, to_user_id, context_token or "")
        )

    def send_file(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        file_path: Path,
        context_token: str | None,
    ) -> str:
        self.sent_files.append(
            (account.account_id, to_user_id, file_path, context_token)
        )
        return f"file sent ({file_path.name})"


def _build_service(
    *,
    sessions: dict[str, SessionRecord] | None = None,
    configs: dict[str, FeishuTriggerRuntimeConfig] | None = None,
    projects: dict[str, AutomationProjectRecord] | None = None,
    gateway_sessions: dict[str, GatewaySessionRecord] | None = None,
    run_intents: dict[str, IntentInput] | None = None,
    wechat_token: str | None = "wechat-token",
    feishu_client: _FakeFeishuClient | None = None,
    wechat_client: _FakeWeChatClient | None = None,
) -> tuple[ImToolService, _FakeFeishuClient, _FakeWeChatClient]:
    resolved_feishu_client = feishu_client or _FakeFeishuClient()
    resolved_wechat_client = wechat_client or _FakeWeChatClient()
    service = ImToolService(
        config_dir=Path("C:/config"),
        session_repo=_FakeSessionRepo(sessions),
        runtime_config_lookup=_FakeRuntimeConfigLookup(configs),
        run_intent_lookup=_FakeRunIntentLookup(run_intents),
        automation_project_repo=_FakeAutomationProjectRepo(projects),
        gateway_session_lookup=_FakeGatewaySessionLookup(gateway_sessions),
        feishu_client=resolved_feishu_client,
        wechat_account_repo=_FakeWeChatAccountRepo(),
        wechat_secret_store=_FakeWeChatSecretStore(wechat_token),
        wechat_client=resolved_wechat_client,
    )
    return service, resolved_feishu_client, resolved_wechat_client


def _build_context_resolver(
    *,
    sessions: dict[str, SessionRecord] | None = None,
    configs: dict[str, FeishuTriggerRuntimeConfig] | None = None,
    projects: dict[str, AutomationProjectRecord] | None = None,
    gateway_sessions: dict[str, GatewaySessionRecord] | None = None,
) -> ImToolContextResolver:
    return ImToolContextResolver(
        session_repo=_FakeSessionRepo(sessions),
        runtime_config_lookup=_FakeRuntimeConfigLookup(configs),
        automation_project_repo=_FakeAutomationProjectRepo(projects),
        gateway_session_lookup=_FakeGatewaySessionLookup(gateway_sessions),
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


def _automation_session(
    *,
    session_id: str = _SESSION_ID,
    project_id: str = _AUTOMATION_PROJECT_ID,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        workspace_id="ws-1",
        project_kind=ProjectKind.AUTOMATION,
        project_id=project_id,
        metadata={},
    )


def _automation_project(
    *,
    automation_project_id: str = _AUTOMATION_PROJECT_ID,
    chat_id: str = _CHAT_ID,
    trigger_id: str = _TRIGGER_ID,
) -> AutomationProjectRecord:
    return AutomationProjectRecord(
        automation_project_id=automation_project_id,
        name="daily-briefing",
        display_name="Daily Briefing",
        status=AutomationProjectStatus.ENABLED,
        workspace_id="ws-1",
        prompt="Summarize the day.",
        schedule_mode=AutomationScheduleMode.CRON,
        cron_expression="0 9 * * *",
        timezone="UTC",
        run_config=AutomationRunConfig(),
        delivery_binding=AutomationFeishuBinding(
            trigger_id=trigger_id,
            tenant_key="tenant-1",
            chat_id=chat_id,
            chat_type="group",
            source_label="Release Updates",
        ),
        trigger_id="schedule-trigger",
    )


def _wechat_gateway_session(
    *,
    session_id: str = _SESSION_ID,
    account_id: str = _WECHAT_ACCOUNT_ID,
    peer_user_id: str = _WECHAT_PEER_ID,
    context_token: str | None = "ctx-1",
) -> GatewaySessionRecord:
    return GatewaySessionRecord(
        gateway_session_id="gws-1",
        channel_type=GatewayChannelType.WECHAT,
        external_session_id=f"wechat:{account_id}:{peer_user_id}",
        internal_session_id=session_id,
        peer_user_id=peer_user_id,
        peer_chat_id=peer_user_id,
        channel_state={
            "account_id": account_id,
            "peer_user_id": peer_user_id,
            "context_token": context_token,
        },
    )


def test_resolver_returns_im_tool_for_feishu_session() -> None:
    resolver = _build_context_resolver(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ("im_send",)


def test_resolver_returns_im_tool_for_wechat_session() -> None:
    resolver = _build_context_resolver(
        configs=_default_configs(),
        gateway_sessions={_SESSION_ID: _wechat_gateway_session()},
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ("im_send",)


def test_resolver_returns_no_tool_without_im_context() -> None:
    resolver = _build_context_resolver(
        sessions={_SESSION_ID: _make_session(platform="other")},
        configs=_default_configs(),
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ()


def test_resolver_returns_im_tool_for_automation_session_binding() -> None:
    resolver = _build_context_resolver(
        sessions={_SESSION_ID: _automation_session()},
        configs=_default_configs(),
        projects={_AUTOMATION_PROJECT_ID: _automation_project()},
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ("im_send",)


def test_resolver_returns_no_tool_for_automation_session_without_binding() -> None:
    project = _automation_project().model_copy(update={"delivery_binding": None})
    resolver = _build_context_resolver(
        sessions={_SESSION_ID: _automation_session()},
        configs=_default_configs(),
        projects={_AUTOMATION_PROJECT_ID: project},
    )

    resolved = resolver.resolve_implicit_tools(
        ToolResolutionContext(session_id=_SESSION_ID)
    )

    assert resolved == ()


def test_send_text_success_for_feishu() -> None:
    service, feishu_client, wechat_client = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert result == "Message sent."
    assert feishu_client.sent_texts == []
    assert feishu_client.reply_texts == [("om_1", "hello")]
    assert wechat_client.sent_texts == []


def test_send_text_success_for_feishu_p2p_uses_reply() -> None:
    service, feishu_client, wechat_client = _build_service(
        sessions={
            _SESSION_ID: _make_session(
                chat_type="p2p",
                message_id="om_p2p_1",
            )
        },
        configs=_default_configs(),
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert result == "Message sent."
    assert feishu_client.sent_texts == []
    assert feishu_client.reply_texts == [("om_p2p_1", "hello")]
    assert wechat_client.sent_texts == []


def test_send_text_for_feishu_group_run_can_force_direct_send() -> None:
    service, feishu_client, wechat_client = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
        run_intents={
            "run-1": IntentInput(
                session_id=_SESSION_ID,
                input=content_parts_from_text("hello"),
                conversation_context=RuntimePromptConversationContext(
                    source_provider="feishu",
                    source_kind="im",
                    feishu_chat_type="group",
                    im_force_direct_send=True,
                ),
            )
        },
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello", run_id="run-1")

    assert result == "Message sent."
    assert feishu_client.sent_texts == [(_CHAT_ID, "hello")]
    assert feishu_client.reply_texts == []
    assert wechat_client.sent_texts == []


def test_send_text_success_for_wechat() -> None:
    service, feishu_client, wechat_client = _build_service(
        configs=_default_configs(),
        gateway_sessions={_SESSION_ID: _wechat_gateway_session()},
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert result == "Message sent."
    assert feishu_client.sent_texts == []
    assert wechat_client.sent_texts == [
        (_WECHAT_ACCOUNT_ID, "wechat-token", _WECHAT_PEER_ID, "ctx-1")
    ]


def test_send_text_no_im_session() -> None:
    service, feishu_client, wechat_client = _build_service(
        sessions={_SESSION_ID: _make_session(platform="other")},
        configs=_default_configs(),
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert "not linked" in result
    assert feishu_client.sent_texts == []
    assert wechat_client.sent_texts == []


def test_send_text_unknown_session() -> None:
    service, feishu_client, wechat_client = _build_service(configs=_default_configs())

    result = service.send_text(session_id="nonexistent", text="hello")

    assert "not linked" in result
    assert feishu_client.sent_texts == []
    assert wechat_client.sent_texts == []


def test_send_text_no_runtime_config() -> None:
    service, feishu_client, wechat_client = _build_service(
        sessions=_default_sessions(),
        configs={},
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert "not linked" in result
    assert feishu_client.sent_texts == []
    assert wechat_client.sent_texts == []


def test_send_text_success_for_automation_session_binding() -> None:
    service, feishu_client, wechat_client = _build_service(
        sessions={_SESSION_ID: _automation_session()},
        configs=_default_configs(),
        projects={_AUTOMATION_PROJECT_ID: _automation_project()},
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert result == "Message sent."
    assert feishu_client.sent_texts == [(_CHAT_ID, "hello")]
    assert feishu_client.reply_texts == []
    assert wechat_client.sent_texts == []


def test_send_text_wechat_requires_token() -> None:
    service, _, _ = _build_service(
        configs=_default_configs(),
        gateway_sessions={_SESSION_ID: _wechat_gateway_session()},
        wechat_token=None,
    )

    with pytest.raises(RuntimeError, match="bot token is missing"):
        _ = service.send_text(session_id=_SESSION_ID, text="hello")


def test_send_file_success_for_feishu(tmp_path: Path) -> None:
    test_file = tmp_path / "report.pdf"
    test_file.write_text("content")
    service, feishu_client, wechat_client = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )

    result = service.send_file(session_id=_SESSION_ID, file_path=test_file)

    assert "file sent" in result
    assert feishu_client.sent_files == [(_CHAT_ID, test_file)]
    assert wechat_client.sent_texts == []
    assert wechat_client.sent_files == []


def test_send_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    service, _, _ = _build_service(
        sessions=_default_sessions(),
        configs=_default_configs(),
    )

    with pytest.raises(FileNotFoundError):
        _ = service.send_file(session_id=_SESSION_ID, file_path=missing)


def test_send_file_no_im_session(tmp_path: Path) -> None:
    test_file = tmp_path / "report.pdf"
    test_file.write_text("content")
    service, feishu_client, wechat_client = _build_service(
        sessions={_SESSION_ID: _make_session(platform="other")},
        configs=_default_configs(),
    )

    result = service.send_file(session_id=_SESSION_ID, file_path=test_file)

    assert "not linked" in result
    assert feishu_client.sent_files == []
    assert wechat_client.sent_texts == []
    assert wechat_client.sent_files == []


def test_send_file_success_for_wechat(tmp_path: Path) -> None:
    test_file = tmp_path / "report.pdf"
    test_file.write_text("content")
    service, feishu_client, wechat_client = _build_service(
        configs=_default_configs(),
        gateway_sessions={_SESSION_ID: _wechat_gateway_session()},
    )

    result = service.send_file(session_id=_SESSION_ID, file_path=test_file)

    assert result == "file sent (report.pdf)"
    assert feishu_client.sent_files == []
    assert wechat_client.sent_texts == []
    assert wechat_client.sent_files == [
        (_WECHAT_ACCOUNT_ID, _WECHAT_PEER_ID, test_file, "ctx-1")
    ]


def test_send_file_wechat_requires_token(tmp_path: Path) -> None:
    test_file = tmp_path / "report.pdf"
    test_file.write_text("content")
    service, _, _ = _build_service(
        configs=_default_configs(),
        gateway_sessions={_SESSION_ID: _wechat_gateway_session()},
        wechat_token=None,
    )

    with pytest.raises(RuntimeError, match="bot token is missing"):
        _ = service.send_file(session_id=_SESSION_ID, file_path=test_file)


def test_resolve_context_missing_chat_id() -> None:
    session = _make_session(chat_id="")
    service, feishu_client, wechat_client = _build_service(
        sessions={_SESSION_ID: session},
        configs=_default_configs(),
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert "not linked" in result
    assert feishu_client.sent_texts == []
    assert wechat_client.sent_texts == []


def test_resolve_context_missing_trigger_id() -> None:
    session = _make_session(trigger_id="")
    service, feishu_client, wechat_client = _build_service(
        sessions={_SESSION_ID: session},
        configs=_default_configs(),
    )

    result = service.send_text(session_id=_SESSION_ID, text="hello")

    assert "not linked" in result
    assert feishu_client.sent_texts == []
    assert wechat_client.sent_texts == []
