from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
from pydantic import BaseModel, JsonValue
import pytest

from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.gateway.discord import (
    DiscordAccountCreateInput,
    DiscordAccountRecord,
    DiscordAccountRepository,
    DiscordAccountStatus,
    DiscordBotIdentity,
    DiscordClient,
    DiscordGatewayService,
    DiscordInboundMessage,
    DiscordInboundQueueRepository,
    DiscordInboundQueueStatus,
    DiscordSecretStore,
)
from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.im.command_service import ImSessionCommandResult
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressRequest,
    GatewaySessionIngressResult,
    GatewaySessionIngressService,
    GatewaySessionIngressStatus,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.workspace import WorkspaceService


@pytest.mark.asyncio
async def test_discord_account_create_persists_secret_without_starting_disabled(
    tmp_path: Path,
) -> None:
    fake_client = _FakeDiscordClient()
    fake_secret_store = _FakeSecretStore()
    service = _build_service(
        tmp_path,
        client=fake_client,
        secret_store=fake_secret_store,
    )

    created = await service.create_account(
        DiscordAccountCreateInput(
            display_name="Discord Main",
            bot_token="bot-token",
            enabled=False,
            allowed_channel_ids=("channel-1",),
            allow_channel_messages=True,
            workspace_id="workspace-1",
        )
    )
    listed = await service.list_accounts()

    assert fake_client.identity_tokens == ["bot-token"]
    assert fake_secret_store.tokens == {"bot-1": "bot-token"}
    assert created.account_id == "bot-1"
    assert created.status == DiscordAccountStatus.DISABLED
    assert created.secret_status.bot_token_configured is True
    assert listed[0].allowed_channel_ids == ("channel-1",)
    assert listed[0].allow_channel_messages is True


@pytest.mark.asyncio
async def test_handle_discord_dm_starts_run_and_replies_terminal_output(
    tmp_path: Path,
) -> None:
    repository = DiscordAccountRepository(tmp_path / "discord.db")
    inbound_queue_repo = DiscordInboundQueueRepository(tmp_path / "discord-queue.db")
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService(run_id="run-1")
    fake_run_service = _FakeRunService(
        (
            _FakeRunEvent(
                event_type=RunEventType.RUN_COMPLETED,
                payload_json=json.dumps({"output": "done from Discord"}),
            ),
        )
    )
    fake_im_tool = _FakeImToolService()
    service = _build_service(
        tmp_path,
        repository=repository,
        inbound_queue_repo=inbound_queue_repo,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
        run_service=fake_run_service,
        im_tool_service=fake_im_tool,
    )
    await repository.upsert_account(_discord_account())

    await service.handle_inbound_message(
        account_id="bot-1",
        message=DiscordInboundMessage(
            message_id="message-1",
            channel_id="dm-channel-1",
            author_id="user-1",
            author_name="Alex",
            content="inspect this repo",
            is_dm=True,
        ),
    )
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    assert fake_gateway_sessions.resolved_external_session_ids == [
        "discord:bot-1:dm:user-1"
    ]
    assert fake_ingress.requests[0].intent.intent == "inspect this repo"
    assert fake_ingress.requests[0].intent.conversation_context is not None
    assert (
        fake_ingress.requests[0].intent.conversation_context.source_provider
        == "discord"
    )
    assert (
        _SentDiscordText(
            account_id="bot-1",
            channel_id="dm-channel-1",
            text="Received. Processing now.",
            reply_to_message_id="message-1",
        )
        in fake_im_tool.sent_texts
    )
    assert (
        _SentDiscordText(
            account_id="bot-1",
            channel_id="dm-channel-1",
            text="done from Discord",
            reply_to_message_id="message-1",
        )
        in fake_im_tool.sent_texts
    )
    queue_record = await inbound_queue_repo.get_latest_by_run_id("run-1")
    assert queue_record is not None
    assert queue_record.status == DiscordInboundQueueStatus.COMPLETED


@pytest.mark.asyncio
async def test_handle_discord_inbound_ignores_unaccepted_guild_messages(
    tmp_path: Path,
) -> None:
    repository = DiscordAccountRepository(tmp_path / "discord.db")
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_ingress = _FakeIngressService(run_id="run-1")
    fake_im_tool = _FakeImToolService()
    service = _build_service(
        tmp_path,
        repository=repository,
        gateway_session_service=fake_gateway_sessions,
        session_ingress_service=fake_ingress,
        im_tool_service=fake_im_tool,
    )
    await repository.upsert_account(_discord_account())

    for message in (
        DiscordInboundMessage(
            message_id="message-ignored-1",
            channel_id="channel-9",
            guild_id="guild-1",
            author_id="user-1",
            content="plain channel message",
        ),
        DiscordInboundMessage(
            message_id="message-ignored-2",
            channel_id="channel-1",
            guild_id="guild-1",
            author_id="user-1",
            content="<@bot-1>",
            mentions_bot=True,
        ),
    ):
        await service.handle_inbound_message(account_id="bot-1", message=message)

    assert fake_gateway_sessions.resolved_external_session_ids == []
    assert fake_ingress.requests == []
    assert fake_im_tool.sent_texts == []


@pytest.mark.asyncio
async def test_discord_client_maps_status_errors_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v10/users/@me"
        return httpx.Response(
            401,
            text="invalid token",
            request=request,
        )

    monkeypatch.setattr(
        "relay_teams.gateway.discord.client.create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match="Discord API request failed: 401"):
        await DiscordClient().fetch_current_bot_identity(token="bad-token")


def _discord_account() -> DiscordAccountRecord:
    return DiscordAccountRecord(
        account_id="bot-1",
        display_name="Discord Main",
        status=DiscordAccountStatus.ENABLED,
        bot_user_id="bot-1",
        application_id="app-1",
        allowed_channel_ids=("channel-1",),
        allow_channel_messages=False,
        workspace_id="workspace-1",
    )


def _build_service(
    tmp_path: Path,
    *,
    repository: DiscordAccountRepository | None = None,
    inbound_queue_repo: DiscordInboundQueueRepository | None = None,
    client: _FakeDiscordClient | None = None,
    secret_store: _FakeSecretStore | None = None,
    gateway_session_service: _FakeGatewaySessionService | None = None,
    session_ingress_service: _FakeIngressService | None = None,
    run_service: _FakeRunService | None = None,
    im_tool_service: _FakeImToolService | None = None,
) -> DiscordGatewayService:
    return DiscordGatewayService(
        config_dir=tmp_path,
        repository=repository or DiscordAccountRepository(tmp_path / "discord.db"),
        secret_store=cast(DiscordSecretStore, secret_store or _FakeSecretStore()),
        client=cast(DiscordClient, client or _FakeDiscordClient()),
        gateway_session_service=cast(
            GatewaySessionService,
            gateway_session_service or _FakeGatewaySessionService(),
        ),
        run_service=run_service or _FakeRunService(()),
        workspace_service=cast(WorkspaceService, _FakeWorkspaceService()),
        orchestration_settings_service=cast(
            OrchestrationSettingsService,
            _FakeOrchestrationSettingsService(),
        ),
        im_tool_service=im_tool_service or _FakeImToolService(),
        im_session_command_service=_FakeImCommandService(),
        inbound_queue_repo=(
            inbound_queue_repo
            or DiscordInboundQueueRepository(tmp_path / "discord-queue.db")
        ),
        session_ingress_service=cast(
            GatewaySessionIngressService | None,
            session_ingress_service,
        ),
    )


class _FakeSecretStore:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        _ = config_dir
        return self.tokens.get(account_id)

    def set_bot_token(
        self,
        config_dir: Path,
        account_id: str,
        token: str | None,
    ) -> None:
        _ = config_dir
        if token is None:
            self.tokens.pop(account_id, None)
            return
        self.tokens[account_id] = token

    def delete_bot_token(self, config_dir: Path, account_id: str) -> None:
        _ = config_dir
        self.tokens.pop(account_id, None)


class _FakeDiscordClient:
    def __init__(self) -> None:
        self.identity_tokens: list[str] = []

    async def fetch_current_bot_identity(self, *, token: str) -> DiscordBotIdentity:
        self.identity_tokens.append(token)
        return DiscordBotIdentity(
            user_id="bot-1",
            username="Relay Discord",
            application_id="app-1",
        )


class _FakeWorkspaceService:
    def get_workspace(self, workspace_id: str) -> object:
        if workspace_id != "workspace-1":
            raise KeyError(workspace_id)
        return object()


class _FakeOrchestrationSettingsService:
    def default_orchestration_preset_id(self) -> str | None:
        return None


class _FakeGatewaySessionService:
    def __init__(self) -> None:
        self.resolved_external_session_ids: list[str] = []
        self.bound_runs: list[tuple[str, str | None]] = []
        self._records: dict[str, GatewaySessionRecord] = {}

    def resolve_or_create_session(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: object | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
        peer_user_id: str | None = None,
        peer_chat_id: str | None = None,
        capabilities: dict[str, JsonValue] | None = None,
        channel_state: dict[str, JsonValue] | None = None,
    ) -> GatewaySessionRecord:
        _ = (
            workspace_id,
            metadata,
            session_mode,
            normal_root_role_id,
            orchestration_preset_id,
            peer_user_id,
            capabilities,
        )
        self.resolved_external_session_ids.append(external_session_id)
        record = GatewaySessionRecord(
            gateway_session_id=f"gws-{len(self._records) + 1}",
            channel_type=channel_type,
            external_session_id=external_session_id,
            internal_session_id="session-1",
            peer_user_id=peer_user_id,
            peer_chat_id=peer_chat_id,
            channel_state=channel_state or {},
        )
        self._records[record.gateway_session_id] = record
        return record

    def bind_active_run(
        self,
        gateway_session_id: str,
        run_id: str | None,
    ) -> GatewaySessionRecord:
        self.bound_runs.append((gateway_session_id, run_id))
        record = self._records[gateway_session_id]
        updated = record.model_copy(update={"active_run_id": run_id})
        self._records[gateway_session_id] = updated
        return updated

    def update_channel_state(
        self,
        gateway_session_id: str,
        *,
        channel_state: dict[str, JsonValue],
        peer_chat_id: str | None = None,
    ) -> GatewaySessionRecord:
        record = self._records[gateway_session_id]
        updated_state = {**record.channel_state, **channel_state}
        updated = record.model_copy(
            update={"channel_state": updated_state, "peer_chat_id": peer_chat_id}
        )
        self._records[gateway_session_id] = updated
        return updated


class _FakeIngressService:
    def __init__(self, *, run_id: str | None) -> None:
        self.requests: list[GatewaySessionIngressRequest] = []
        self._run_id = run_id

    async def active_run_id_async(self, session_id: str) -> str | None:
        _ = session_id
        return None

    async def submit_async(
        self,
        request: GatewaySessionIngressRequest,
    ) -> GatewaySessionIngressResult:
        self.requests.append(request)
        return GatewaySessionIngressResult(
            status=GatewaySessionIngressStatus.STARTED,
            session_id=request.intent.session_id,
            run_id=self._run_id,
        )


class _FakeRunEvent:
    def __init__(self, *, event_type: RunEventType, payload_json: str) -> None:
        self.event_type = event_type
        self.payload_json = payload_json


class _FakeRunService:
    def __init__(self, events: tuple[_FakeRunEvent, ...]) -> None:
        self._events = events

    @property
    def bound_event_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def stream_run_events(self, run_id: str) -> AsyncIterator[_FakeRunEvent]:
        _ = run_id
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[_FakeRunEvent]:
        for event in self._events:
            yield event


class _SentDiscordText(BaseModel):
    account_id: str
    channel_id: str
    text: str
    reply_to_message_id: str | None


class _FakeImToolService:
    def __init__(self) -> None:
        self.sent_texts: list[_SentDiscordText] = []

    async def send_text_to_discord_channel(
        self,
        *,
        account_id: str,
        channel_id: str,
        text: str,
        reply_to_message_id: str | None,
    ) -> None:
        self.sent_texts.append(
            _SentDiscordText(
                account_id=account_id,
                channel_id=channel_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        )


class _FakeImCommandService:
    def handle_discord_command(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
        text: str,
    ) -> ImSessionCommandResult | None:
        _ = (session_id, gateway_session_id, text)
        return None
