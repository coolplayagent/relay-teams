from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import aiosqlite
import discord
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
    DiscordAccountUpdateInput,
    DiscordBotIdentity,
    DiscordClient,
    DiscordGatewayService,
    DiscordInboundMessage,
    DiscordInboundQueueRecord,
    DiscordInboundQueueRepository,
    DiscordInboundQueueStatus,
    DiscordSecretStore,
    get_discord_secret_store,
)
from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.discord.gateway_worker import (
    DiscordGatewayWorker,
    _DiscordMessageClient,
)
from relay_teams.gateway.im.command_service import (
    ImSessionCommandResult,
    ImSessionCommandService,
    _FeishuQueueLookup,
)
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressRequest,
    GatewaySessionIngressResult,
    GatewaySessionIngressService,
    GatewaySessionIngressStatus,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.secrets import AppSecretStore
from relay_teams.sessions.session_models import SessionMode
from relay_teams.sessions.session_service import SessionService
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


@pytest.mark.asyncio
async def test_start_account_worker_replaces_stale_cached_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_secret_store = _FakeSecretStore()
    fake_secret_store.tokens["bot-1"] = "bot-token"
    service = _build_service(tmp_path, secret_store=fake_secret_store)
    stale_worker = _FakeDiscordGatewayWorker(
        account_id="bot-1",
        target_loop=asyncio.get_running_loop(),
        handle_message=None,
        set_running=lambda running, error: None,
    )
    stale_worker.alive = False
    service._workers["bot-1"] = cast(DiscordGatewayWorker, stale_worker)
    _FAKE_DISCORD_WORKERS.clear()

    monkeypatch.setattr(
        "relay_teams.gateway.discord.service.DiscordGatewayWorker",
        _FakeDiscordGatewayWorker,
    )
    service._start_account_worker("bot-1")

    assert stale_worker.stop_calls == 1
    assert len(_FAKE_DISCORD_WORKERS) == 1
    replacement = _FAKE_DISCORD_WORKERS[0]
    assert replacement.start_tokens == ["bot-token"]
    assert service._workers["bot-1"] is replacement


@pytest.mark.asyncio
async def test_discord_account_update_enable_and_delete_manage_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeDiscordClient()
    fake_secret_store = _FakeSecretStore()
    service = _build_service(
        tmp_path,
        client=fake_client,
        secret_store=fake_secret_store,
    )
    _FAKE_DISCORD_WORKERS.clear()
    monkeypatch.setattr(
        "relay_teams.gateway.discord.service.DiscordGatewayWorker",
        _FakeDiscordGatewayWorker,
    )
    await service.create_account(
        DiscordAccountCreateInput(
            display_name="Discord Main",
            bot_token="initial-token",
            enabled=False,
            workspace_id="workspace-1",
        )
    )

    updated = await service.update_account(
        "bot-1",
        DiscordAccountUpdateInput(
            display_name="Discord Updated",
            bot_token="rotated-token",
            enabled=True,
            allow_channel_messages=True,
        ),
    )

    assert updated.display_name == "Discord Updated"
    assert updated.status == DiscordAccountStatus.ENABLED
    assert fake_secret_store.tokens["bot-1"] == "rotated-token"
    assert _FAKE_DISCORD_WORKERS[0].start_tokens == ["rotated-token"]
    with pytest.raises(RuntimeError, match="Cannot delete enabled Discord account"):
        await service.delete_account("bot-1")

    await service.delete_account("bot-1", force=True)

    assert fake_secret_store.tokens == {}
    assert _FAKE_DISCORD_WORKERS[0].stop_calls == 1
    with pytest.raises(KeyError, match="Unknown Discord account_id"):
        await service.get_account("bot-1")


@pytest.mark.asyncio
async def test_discord_account_repository_skips_dirty_list_rows_and_falls_back_on_get(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "discord-accounts.db"
    repository = DiscordAccountRepository(db_path)
    await repository.upsert_account(
        _discord_account().model_copy(
            update={"account_id": "bot-valid", "display_name": "Valid Discord"}
        )
    )
    fallback_time = datetime(2026, 5, 1, 8, 30, tzinfo=UTC)
    await _insert_discord_account_row(
        db_path,
        account_id="bot-dirty",
        display_name="Dirty Discord",
        created_at="not-a-date",
        updated_at=fallback_time.isoformat(),
    )

    listed = await repository.list_accounts()
    dirty = await repository.get_account("bot-dirty")
    await repository.delete_account("bot-valid")

    assert [account.account_id for account in listed] == ["bot-valid"]
    assert dirty.created_at == fallback_time
    assert dirty.updated_at == fallback_time
    assert await repository.list_accounts() == ()
    with pytest.raises(KeyError, match="Unknown Discord account_id"):
        await repository.get_account("missing")


@pytest.mark.asyncio
async def test_discord_inbound_queue_repository_lifecycle(
    tmp_path: Path,
) -> None:
    repository = DiscordInboundQueueRepository(tmp_path / "discord-queue.db")
    first, created_first = await repository.create_or_get(
        _queue_record(inbound_queue_id="queue-1", message_key="mid:1")
    )
    duplicate, created_duplicate = await repository.create_or_get(
        _queue_record(inbound_queue_id="queue-duplicate", message_key="mid:1")
    )
    second, created_second = await repository.create_or_get(
        _queue_record(inbound_queue_id="queue-2", message_key="mid:2")
    )

    assert created_first is True
    assert created_duplicate is False
    assert duplicate.inbound_queue_id == first.inbound_queue_id
    assert created_second is True
    assert await repository.get("missing") is None
    with pytest.raises(KeyError, match="Unknown Discord inbound queue record"):
        await repository.get_by_message_key(
            account_id="bot-1",
            channel_id="channel-1",
            message_key="missing",
        )
    assert await repository.get_latest_by_run_id(" ") is None
    assert await repository.has_non_terminal_item_for_run("") is False
    assert await repository.count_non_terminal_ahead(second.inbound_queue_id) == 1

    claimed = await repository.claim_starting(
        inbound_queue_id=first.inbound_queue_id,
        stale_before=datetime.now(tz=UTC),
    )
    assert claimed is not None
    assert claimed.status == DiscordInboundQueueStatus.STARTING
    assert (
        await repository.claim_starting(
            inbound_queue_id=first.inbound_queue_id,
            stale_before=datetime(2000, 1, 1, tzinfo=UTC),
        )
        is None
    )
    requeued = await repository.requeue_if_starting(
        inbound_queue_id=first.inbound_queue_id,
        last_error="busy",
    )
    assert requeued is not None
    assert requeued.status == DiscordInboundQueueStatus.QUEUED
    assert requeued.last_error == "busy"
    assert (
        await repository.requeue_if_starting(inbound_queue_id=second.inbound_queue_id)
        is None
    )

    waiting = await repository.update(
        second.model_copy(
            update={
                "status": DiscordInboundQueueStatus.WAITING_RESULT,
                "run_id": "run-1",
            }
        )
    )
    assert await repository.get_latest_by_run_id("run-1") == waiting
    assert await repository.has_non_terminal_item_for_run("run-1") is True
    completed = await repository.update(
        waiting.model_copy(
            update={
                "status": DiscordInboundQueueStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
            }
        )
    )
    assert completed.completed_at is not None
    assert await repository.has_non_terminal_item_for_run("run-1") is False

    stale_time = datetime.now(tz=UTC) - timedelta(minutes=5)
    stale, _ = await repository.create_or_get(
        _queue_record(
            inbound_queue_id="queue-stale",
            message_key="mid:stale",
            status=DiscordInboundQueueStatus.STARTING,
            updated_at=stale_time,
        )
    )
    minimum_ready = await repository.list_ready_to_start(
        limit=0,
        stale_before=stale_time + timedelta(seconds=1),
    )
    ready = await repository.list_ready_to_start(
        limit=100,
        stale_before=stale_time + timedelta(seconds=1),
    )

    assert len(minimum_ready) == 1
    assert stale.inbound_queue_id in {record.inbound_queue_id for record in ready}


@pytest.mark.asyncio
async def test_discord_client_fetches_identity_sends_text_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v10/users/@me":
            assert request.headers["Authorization"] == "Bot token-1"
            return httpx.Response(
                200,
                json={"id": " bot-1 ", "username": " Relay Discord "},
                request=request,
            )
        if request.url.path == "/api/v10/oauth2/applications/@me":
            return httpx.Response(200, json={"id": " app-1 "}, request=request)
        if request.url.path == "/api/v10/channels/channel-1/messages":
            if request.headers.get("Content-Type") == "application/json":
                payload = cast(
                    dict[str, object],
                    json.loads(request.content.decode("utf-8")),
                )
                seen_payloads.append(payload)
                assert request.headers["Authorization"] == "Bot existing-token"
                return httpx.Response(200, json={"id": " message-1 "}, request=request)
            body = request.content.decode("utf-8", errors="ignore")
            assert '"message_id": "message-1"' in body
            assert 'name="files[0]"' in body
            return httpx.Response(200, json={"id": " file-1 "}, request=request)
        return httpx.Response(404, text="not found", request=request)

    def client_factory(*, timeout_seconds: float) -> httpx.AsyncClient:
        _ = timeout_seconds
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "relay_teams.gateway.discord.client.create_async_http_client",
        client_factory,
    )
    file_path = tmp_path / "report.txt"
    file_path.write_text("hello", encoding="utf-8")
    client = DiscordClient()

    identity = await client.fetch_current_bot_identity(token="token-1")
    sent_message_id = await client.send_text_message(
        token="Bot existing-token",
        channel_id="channel-1",
        text="hello",
        reply_to_message_id="message-1",
    )
    file_result = await client.send_file(
        token="token-1",
        channel_id="channel-1",
        file_path=file_path,
        reply_to_message_id="message-1",
    )

    assert identity.user_id == "bot-1"
    assert identity.username == "Relay Discord"
    assert identity.application_id == "app-1"
    assert sent_message_id == "message-1"
    assert seen_payloads[0]["allowed_mentions"] == {"replied_user": False}
    assert file_result == "file sent (report.txt, message=file-1)"


@pytest.mark.asyncio
async def test_discord_client_tolerates_missing_application_and_rejects_bad_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v10/users/@me":
            return httpx.Response(
                200,
                json={"id": "bot-1", "username": "Relay Discord"},
                request=request,
            )
        if request.url.path == "/api/v10/oauth2/applications/@me":
            return httpx.Response(403, text="forbidden", request=request)
        if request.url.path == "/api/v10/channels/channel-1/messages":
            return httpx.Response(200, json={"id": "   "}, request=request)
        return httpx.Response(404, text="not found", request=request)

    def client_factory(*, timeout_seconds: float) -> httpx.AsyncClient:
        _ = timeout_seconds
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "relay_teams.gateway.discord.client.create_async_http_client",
        client_factory,
    )
    client = DiscordClient()

    identity = await client.fetch_current_bot_identity(token="token-1")

    assert identity.application_id is None
    with pytest.raises(RuntimeError, match="Discord response missing id"):
        await client.send_text_message(
            token="token-1",
            channel_id="channel-1",
            text="hello",
        )


@pytest.mark.asyncio
async def test_discord_client_maps_request_errors_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    def client_factory(*, timeout_seconds: float) -> httpx.AsyncClient:
        _ = timeout_seconds
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "relay_teams.gateway.discord.client.create_async_http_client",
        client_factory,
    )

    with pytest.raises(RuntimeError, match="Discord API request failed: network down"):
        await DiscordClient().send_text_message(
            token="token-1",
            channel_id="channel-1",
            text="hello",
        )


def test_discord_secret_store_normalizes_and_deletes_tokens(tmp_path: Path) -> None:
    fake_app_secret_store = _FakeAppSecretStore()
    store = DiscordSecretStore(secret_store=cast(AppSecretStore, fake_app_secret_store))

    store.set_bot_token(tmp_path, " bot-1 ", " token-1 ")
    stored = store.get_bot_token(tmp_path, "bot-1")
    store.set_bot_token(tmp_path, "bot-1", "   ")
    cleared = store.get_bot_token(tmp_path, "bot-1")
    store.set_bot_token(tmp_path, "bot-1", " token-2 ")
    store.delete_bot_token(tmp_path, " bot-1 ")

    assert stored == "token-1"
    assert cleared is None
    assert store.get_bot_token(tmp_path, "bot-1") is None
    assert store.can_persist_token() is True
    assert isinstance(get_discord_secret_store(), DiscordSecretStore)


def test_discord_gateway_worker_preserves_thread_parent_channel_id() -> None:
    class FakeDiscordUser:
        def __init__(self, *, user_id: int, name: str) -> None:
            self.id = user_id
            self.name = name
            self.bot = False

    class FakeDiscordGuild:
        def __init__(self, *, guild_id: int) -> None:
            self.id = guild_id

    class FakeDiscordMessage:
        def __init__(
            self,
            *,
            message_id: int,
            channel: discord.Thread,
            author: FakeDiscordUser,
            guild: FakeDiscordGuild,
            content: str,
        ) -> None:
            self.id = message_id
            self.channel = channel
            self.author = author
            self.guild = guild
            self.content = content
            self.mentions: list[FakeDiscordUser] = []

    async def handle_message(account_id: str, inbound: DiscordInboundMessage) -> None:
        _ = (account_id, inbound)

    def set_running(running: bool, error: str | None) -> None:
        _ = (running, error)

    loop = asyncio.new_event_loop()
    try:
        client = _DiscordMessageClient(
            account_id="bot-1",
            target_loop=loop,
            handle_message=handle_message,
            set_running=set_running,
        )
        thread = object.__new__(discord.Thread)
        thread.id = 123
        thread.parent_id = 456
        message = FakeDiscordMessage(
            message_id=789,
            channel=thread,
            author=FakeDiscordUser(user_id=111, name="alice"),
            guild=FakeDiscordGuild(guild_id=222),
            content="hello",
        )

        inbound = client._to_inbound_message(cast(discord.Message, message))

        assert inbound.channel_id == "456"
        assert inbound.thread_id == "123"
        assert inbound.guild_id == "222"
    finally:
        loop.close()


def test_discord_message_acceptance_and_terminal_text_helpers() -> None:
    account = _discord_account().model_copy(update={"allow_channel_messages": True})

    assert (
        DiscordGatewayService._accepted_text(
            account=account,
            message=DiscordInboundMessage(
                message_id="m1",
                channel_id="dm-1",
                author_id="user-1",
                content=" hello ",
                is_dm=True,
            ),
        )
        == "hello"
    )
    assert (
        DiscordGatewayService._accepted_text(
            account=account,
            message=DiscordInboundMessage(
                message_id="m2",
                channel_id="channel-2",
                guild_id="guild-1",
                author_id="user-1",
                content="<@bot-1> inspect",
                mentions_bot=True,
            ),
        )
        == "inspect"
    )
    assert (
        DiscordGatewayService._accepted_text(
            account=account,
            message=DiscordInboundMessage(
                message_id="m3",
                channel_id="channel-1",
                guild_id="guild-1",
                author_id="user-1",
                content="allowed channel",
            ),
        )
        == "allowed channel"
    )
    for message in (
        DiscordInboundMessage(
            message_id="m4",
            channel_id="dm-1",
            author_id="bot-2",
            content="bot",
            is_dm=True,
            author_is_bot=True,
        ),
        DiscordInboundMessage(
            message_id="m5",
            channel_id="dm-1",
            author_id="bot-1",
            content="self",
            is_dm=True,
        ),
        DiscordInboundMessage(
            message_id="m6",
            channel_id="dm-1",
            author_id="user-1",
            content=" ",
            is_dm=True,
        ),
    ):
        assert (
            DiscordGatewayService._accepted_text(account=account, message=message)
            is None
        )

    assert (
        DiscordGatewayService._terminal_text(
            _FakeRunEvent(event_type=RunEventType.RUN_COMPLETED, payload_json="{}")
        )
        == "Completed."
    )
    assert (
        DiscordGatewayService._terminal_text(
            _FakeRunEvent(event_type=RunEventType.RUN_STOPPED, payload_json="{}")
        )
        == "Run stopped."
    )
    assert (
        DiscordGatewayService._terminal_text(
            _FakeRunEvent(
                event_type=RunEventType.RUN_FAILED,
                payload_json=json.dumps({"error": "boom"}),
            )
        )
        == "Run failed: boom"
    )
    assert (
        DiscordGatewayService._paused_text(
            _FakeRunEvent(
                event_type=RunEventType.RUN_PAUSED,
                payload_json=json.dumps({"error_message": "need approval"}),
            )
        )
        == "Run paused: need approval\nSend resume to continue."
    )


@pytest.mark.asyncio
async def test_discord_service_lifecycle_starts_stops_and_reports_worker_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DiscordAccountRepository(tmp_path / "discord.db")
    fake_secret_store = _FakeSecretStore()
    fake_secret_store.tokens["bot-1"] = "bot-token"
    await repository.upsert_account(_discord_account())
    service = _build_service(
        tmp_path,
        repository=repository,
        secret_store=fake_secret_store,
    )
    _FAKE_DISCORD_WORKERS.clear()
    monkeypatch.setattr(
        "relay_teams.gateway.discord.service.DiscordGatewayWorker",
        _FakeDiscordGatewayWorker,
    )

    await service.start_async()
    service.stop()
    service._workers["bot-1"] = cast(
        DiscordGatewayWorker,
        _FakeDiscordGatewayWorker(
            account_id="bot-1",
            target_loop=asyncio.get_running_loop(),
            handle_message=None,
            set_running=lambda running, error: None,
        ),
    )
    await repository.upsert_account(
        _discord_account().model_copy(update={"status": DiscordAccountStatus.DISABLED})
    )
    await service.reload_async()
    fake_secret_store.tokens.clear()
    service._start_account_worker("bot-1")
    missing_token_status = service._status("bot-1")
    fake_secret_store.tokens["bot-1"] = "bot-token"
    service_without_loop = _build_service(
        tmp_path,
        secret_store=fake_secret_store,
        run_service=_FakeUnboundRunService(()),
    )
    service_without_loop._start_account_worker("bot-1")

    assert _FAKE_DISCORD_WORKERS[0].start_tokens == ["bot-token"]
    assert _FAKE_DISCORD_WORKERS[0].stop_calls == 1
    assert _FAKE_DISCORD_WORKERS[1].stop_calls == 1
    assert missing_token_status.last_error == "missing_token"
    assert service_without_loop._status("bot-1").last_error == "missing_loop"


@pytest.mark.asyncio
async def test_discord_service_command_response_starts_resumed_watcher(
    tmp_path: Path,
) -> None:
    repository = DiscordAccountRepository(tmp_path / "discord.db")
    fake_gateway_sessions = _FakeGatewaySessionService()
    fake_im_tool = _FakeImToolService()
    service = _build_service(
        tmp_path,
        repository=repository,
        gateway_session_service=fake_gateway_sessions,
        run_service=_FakeRunService(
            (
                _FakeRunEvent(
                    event_type=RunEventType.RUN_COMPLETED,
                    payload_json=json.dumps({"output": "resumed output"}),
                ),
            )
        ),
        im_tool_service=fake_im_tool,
        im_command_service=_FakeImCommandService(
            ImSessionCommandResult(
                text="[Resume] Resumed run-1.", resumed_run_id="run-1"
            )
        ),
    )
    await repository.upsert_account(_discord_account())

    await service.handle_inbound_message(
        account_id="bot-1",
        message=DiscordInboundMessage(
            message_id="message-command",
            channel_id="dm-channel-1",
            author_id="user-1",
            content="resume",
            is_dm=True,
        ),
    )
    for _ in range(5):
        await asyncio.sleep(0)

    assert (
        _SentDiscordText(
            account_id="bot-1",
            channel_id="dm-channel-1",
            text="[Resume] Resumed run-1.",
            reply_to_message_id="message-command",
        )
        in fake_im_tool.sent_texts
    )
    assert (
        _SentDiscordText(
            account_id="bot-1",
            channel_id="dm-channel-1",
            text="resumed output",
            reply_to_message_id="message-command",
        )
        in fake_im_tool.sent_texts
    )


@pytest.mark.asyncio
async def test_discord_service_ignores_missing_and_disabled_accounts(
    tmp_path: Path,
) -> None:
    repository = DiscordAccountRepository(tmp_path / "discord.db")
    fake_gateway_sessions = _FakeGatewaySessionService()
    service = _build_service(
        tmp_path,
        repository=repository,
        gateway_session_service=fake_gateway_sessions,
    )
    await repository.upsert_account(
        _discord_account().model_copy(update={"status": DiscordAccountStatus.DISABLED})
    )
    message = DiscordInboundMessage(
        message_id="message-1",
        channel_id="dm-channel-1",
        author_id="user-1",
        content="hello",
        is_dm=True,
    )

    await service.handle_inbound_message(account_id="missing", message=message)
    await service.handle_inbound_message(account_id="bot-1", message=message)

    assert fake_gateway_sessions.resolved_external_session_ids == []


@pytest.mark.asyncio
async def test_discord_service_start_queued_record_failure_paths(
    tmp_path: Path,
) -> None:
    repository = DiscordAccountRepository(tmp_path / "discord.db")
    inbound_queue_repo = DiscordInboundQueueRepository(tmp_path / "discord-queue.db")
    service = _build_service(
        tmp_path,
        repository=repository,
        inbound_queue_repo=inbound_queue_repo,
    )
    missing_account_record, _ = await inbound_queue_repo.create_or_get(
        _queue_record(
            inbound_queue_id="queue-missing-account",
            message_key="mid:missing-account",
            status=DiscordInboundQueueStatus.STARTING,
        ).model_copy(update={"account_id": "missing-account"})
    )
    missing_result = await service._start_queued_record(missing_account_record)
    failed_record = await inbound_queue_repo.get("queue-missing-account")
    await repository.upsert_account(_discord_account())
    no_ingress_record, _ = await inbound_queue_repo.create_or_get(
        _queue_record(
            inbound_queue_id="queue-no-ingress",
            message_key="mid:no-ingress",
            status=DiscordInboundQueueStatus.STARTING,
        )
    )
    no_ingress_result = await service._start_queued_record(no_ingress_record)
    requeued = await inbound_queue_repo.get("queue-no-ingress")

    assert missing_result is False
    assert failed_record is not None
    assert failed_record.status == DiscordInboundQueueStatus.FAILED
    assert failed_record.last_error == "Discord account not found: missing-account"
    assert no_ingress_result is False
    assert requeued is not None
    assert requeued.status == DiscordInboundQueueStatus.QUEUED


@pytest.mark.asyncio
async def test_discord_service_receipt_queue_depth_paths(tmp_path: Path) -> None:
    inbound_queue_repo = DiscordInboundQueueRepository(tmp_path / "discord-queue.db")
    active_run_record, _ = await inbound_queue_repo.create_or_get(
        _queue_record(
            inbound_queue_id="queue-active-run",
            message_key="mid:active-run",
            status=DiscordInboundQueueStatus.WAITING_RESULT,
        ).model_copy(update={"run_id": "run-active"})
    )
    queued_record, _ = await inbound_queue_repo.create_or_get(
        _queue_record(inbound_queue_id="queue-waiting", message_key="mid:waiting")
    )
    service = _build_service(
        tmp_path,
        inbound_queue_repo=inbound_queue_repo,
        session_ingress_service=_FakeIngressService(
            run_id=None,
            active_run_id="run-active",
        ),
    )
    external_blocker_service = _build_service(
        tmp_path,
        inbound_queue_repo=inbound_queue_repo,
        session_ingress_service=_FakeIngressService(
            run_id=None,
            active_run_id="run-external",
        ),
    )
    failed_with_error = active_run_record.model_copy(
        update={
            "status": DiscordInboundQueueStatus.FAILED,
            "last_error": "boom",
        }
    )
    failed_without_error = failed_with_error.model_copy(update={"last_error": " "})

    assert (
        await service._build_receipt_text(failed_with_error)
        == "Received, but processing failed: boom"
    )
    assert (
        await service._build_receipt_text(failed_without_error)
        == "Received, but processing failed."
    )
    assert (
        await service._build_receipt_text(
            active_run_record.model_copy(
                update={"status": DiscordInboundQueueStatus.WAITING_RESULT}
            )
        )
        == "Received. Processing now."
    )
    assert (
        await service._build_receipt_text(queued_record)
        == "Received. Queued behind 1 message(s) in this session."
    )
    assert (
        await external_blocker_service._build_receipt_text(queued_record)
        == "Received. Queued behind 2 message(s) in this session."
    )


@pytest.mark.asyncio
async def test_discord_service_watcher_and_future_edges(tmp_path: Path) -> None:
    fake_im_tool = _FakeImToolService()
    pause_service = _build_service(
        tmp_path,
        run_service=_FakeRunService(
            (
                _FakeRunEvent(event_type=RunEventType.RUN_STARTED, payload_json="{}"),
                _FakeRunEvent(
                    event_type=RunEventType.RUN_PAUSED,
                    payload_json=json.dumps({"error_message": "need input"}),
                ),
            )
        ),
        im_tool_service=fake_im_tool,
    )
    await pause_service._await_terminal_and_reply(
        account_id="bot-1",
        gateway_session_id="gws-1",
        run_id="run-paused",
        channel_id="channel-1",
        reply_to_message_id="message-1",
    )
    empty_service = _build_service(tmp_path, run_service=_FakeRunService(()))
    with pytest.raises(RuntimeError, match="ended before a stop event"):
        await empty_service._await_terminal_and_reply(
            account_id="bot-1",
            gateway_session_id="gws-1",
            run_id="run-empty",
            channel_id="channel-1",
            reply_to_message_id=None,
        )
    drain_service = _build_service(
        tmp_path,
        run_service=_FakeRunService(
            (
                _FakeRunEvent(
                    event_type=RunEventType.RUN_PAUSED,
                    payload_json="{}",
                ),
            )
        ),
        session_ingress_service=_FakeIngressService(run_id=None, active_run_id=None),
    )
    await drain_service._await_run_completion_for_queue_drain(
        session_id="session-1",
        run_id="run-drain",
    )
    cancelled_future: Future[None] = Future()
    cancelled_future.cancel()
    failed_future: Future[None] = Future()
    failed_future.set_exception(RuntimeError("reply failed"))
    DiscordGatewayService._handle_reply_future(
        account_id="bot-1",
        gateway_session_id="gws-1",
        run_id="run-cancelled",
        channel_id="channel-1",
        future=cancelled_future,
    )
    DiscordGatewayService._handle_reply_future(
        account_id="bot-1",
        gateway_session_id="gws-1",
        run_id="run-failed",
        channel_id="channel-1",
        future=failed_future,
    )
    queue_future: Future[None] = Future()
    queue_future.set_exception(RuntimeError("drain failed"))
    drain_service._drain_watched_runs.add("run-drain")
    drain_service._handle_queue_drain_future(
        session_id="session-1",
        run_id="run-drain",
        future=queue_future,
    )
    await asyncio.sleep(0)

    assert (
        _SentDiscordText(
            account_id="bot-1",
            channel_id="channel-1",
            text="Run paused: need input\nSend resume to continue.",
            reply_to_message_id="message-1",
        )
        in fake_im_tool.sent_texts
    )
    assert "run-drain" not in drain_service._drain_watched_runs


def test_discord_service_helper_edges(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    guild_message = DiscordInboundMessage(
        message_id="m1",
        channel_id="channel-1",
        guild_id="guild-1",
        thread_id="thread-1",
        author_id="user-1",
        content="hello",
    )
    unknown_guild_message = guild_message.model_copy(
        update={"guild_id": None, "thread_id": None}
    )

    assert (
        DiscordGatewayService._external_session_id(
            account_id="bot-1",
            message=guild_message,
        )
        == "discord:bot-1:guild:guild-1:channel:channel-1:thread:thread-1"
    )
    assert (
        DiscordGatewayService._external_session_id(
            account_id="bot-1",
            message=unknown_guild_message,
        )
        == "discord:bot-1:guild:unknown:channel:channel-1"
    )
    assert DiscordGatewayService._reply_channel_id(guild_message) == "thread-1"
    assert (
        DiscordGatewayService._terminal_text(
            _FakeRunEvent(
                event_type=RunEventType.RUN_FAILED,
                payload_json=json.dumps({"output": "partial output"}),
            )
        )
        == "partial output"
    )
    assert (
        DiscordGatewayService._terminal_text(
            _FakeRunEvent(event_type=RunEventType.RUN_FAILED, payload_json="{}")
        )
        == "Run failed."
    )
    assert (
        DiscordGatewayService._paused_text(
            _FakeRunEvent(event_type=RunEventType.RUN_PAUSED, payload_json="{")
        )
        == "Run paused.\nSend resume to continue."
    )
    assert (
        service._resolve_orchestration_preset_id(
            session_mode=SessionMode.ORCHESTRATION,
            requested_preset_id="preset-1",
            existing_preset_id=None,
        )
        == "preset-1"
    )
    with pytest.raises(ValueError, match="orchestration_preset_id is required"):
        service._resolve_orchestration_preset_id(
            session_mode=SessionMode.ORCHESTRATION,
            requested_preset_id=None,
            existing_preset_id=None,
        )
    with pytest.raises(RuntimeError, match="event loop is not bound"):
        service._require_loop()


def test_im_session_command_service_handles_discord_commands() -> None:
    service = _DiscordCommandServiceForTest()

    assert (
        service.handle_discord_command(
            session_id="session-1",
            gateway_session_id="gws-1",
            text="hello",
        )
        is None
    )
    assert (
        "[Session Commands]"
        in (
            service.handle_discord_command(
                session_id="session-1",
                gateway_session_id="gws-1",
                text="help",
            )
            or ImSessionCommandResult(text="")
        ).text
    )
    assert service.handle_discord_command(
        session_id="session-1",
        gateway_session_id="gws-1",
        text="status",
    ) == ImSessionCommandResult(text="status:session-1")
    assert service.handle_discord_command(
        session_id="session-1",
        gateway_session_id="gws-1",
        text="clear",
    ) == ImSessionCommandResult(text="clear:session-1:gws-1")
    assert service.handle_discord_command(
        session_id="session-1",
        gateway_session_id="gws-1",
        text="resume",
    ) == ImSessionCommandResult(text="resume:session-1:gws-1", resumed_run_id="run-1")


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


async def _insert_discord_account_row(
    db_path: Path,
    *,
    account_id: str,
    display_name: str,
    created_at: str,
    updated_at: str,
) -> None:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO discord_accounts(
                account_id,
                display_name,
                status,
                bot_user_id,
                application_id,
                allowed_channel_ids_json,
                allow_channel_messages,
                workspace_id,
                session_mode,
                normal_root_role_id,
                orchestration_preset_id,
                yolo,
                thinking_json,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                display_name,
                DiscordAccountStatus.ENABLED.value,
                account_id,
                "app-1",
                json.dumps(("channel-1",)),
                1,
                "workspace-1",
                "normal",
                None,
                None,
                1,
                "{}",
                created_at,
                updated_at,
            ),
        )
        await cursor.close()
        await conn.commit()


def _queue_record(
    *,
    inbound_queue_id: str,
    message_key: str,
    status: DiscordInboundQueueStatus = DiscordInboundQueueStatus.QUEUED,
    updated_at: datetime | None = None,
) -> DiscordInboundQueueRecord:
    now = datetime.now(tz=UTC)
    return DiscordInboundQueueRecord(
        inbound_queue_id=inbound_queue_id,
        account_id="bot-1",
        message_key=message_key,
        gateway_session_id="gws-1",
        session_id="session-1",
        peer_user_id="user-1",
        channel_id="channel-1",
        guild_id="guild-1",
        reply_to_message_id="message-1",
        text="inspect this repo",
        status=status,
        created_at=updated_at or now,
        updated_at=updated_at or now,
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
    im_command_service: _FakeImCommandService | None = None,
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
        im_session_command_service=im_command_service or _FakeImCommandService(),
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


class _FakeAppSecretStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], str] = {}

    def get_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> str | None:
        _ = config_dir
        return self._values.get((namespace, owner_id, field_name))

    def set_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> None:
        _ = config_dir
        key = (namespace, owner_id, field_name)
        if value is None:
            self._values.pop(key, None)
            return
        self._values[key] = value

    def delete_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> None:
        _ = config_dir
        self._values.pop((namespace, owner_id, field_name), None)


class _FakeDiscordClient:
    def __init__(self, *, user_id: str = "bot-1") -> None:
        self.identity_tokens: list[str] = []
        self._user_id = user_id

    async def fetch_current_bot_identity(self, *, token: str) -> DiscordBotIdentity:
        self.identity_tokens.append(token)
        return DiscordBotIdentity(
            user_id=self._user_id,
            username="Relay Discord",
            application_id="app-1",
        )


class _FakeWorkspaceService:
    def get_workspace(self, workspace_id: str) -> object:
        if workspace_id != "workspace-1":
            raise KeyError(workspace_id)
        return object()


class _FakeOrchestrationSettingsService:
    def __init__(self, preset_id: str | None = None) -> None:
        self._preset_id = preset_id

    def default_orchestration_preset_id(self) -> str | None:
        return self._preset_id


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
    def __init__(
        self,
        *,
        run_id: str | None,
        active_run_id: str | None = None,
    ) -> None:
        self.requests: list[GatewaySessionIngressRequest] = []
        self._run_id = run_id
        self._active_run_id = active_run_id

    async def active_run_id_async(self, session_id: str) -> str | None:
        _ = session_id
        return self._active_run_id

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


class _FakeUnboundRunService(_FakeRunService):
    @property
    def bound_event_loop(self) -> asyncio.AbstractEventLoop | None:
        return None


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
    def __init__(self, result: ImSessionCommandResult | None = None) -> None:
        self._result = result

    def handle_discord_command(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
        text: str,
    ) -> ImSessionCommandResult | None:
        _ = (session_id, gateway_session_id, text)
        return self._result


_FAKE_DISCORD_WORKERS: list["_FakeDiscordGatewayWorker"] = []


class _FakeDiscordGatewayWorker:
    def __init__(
        self,
        *,
        account_id: str,
        target_loop: asyncio.AbstractEventLoop,
        handle_message: object,
        set_running: Callable[[bool, str | None], None],
    ) -> None:
        _ = (target_loop, handle_message)
        self.account_id = account_id
        self.set_running = set_running
        self.alive = False
        self.stop_calls = 0
        self.start_tokens: list[str] = []
        _FAKE_DISCORD_WORKERS.append(self)

    def start(self, *, token: str) -> None:
        self.start_tokens.append(token)
        self.alive = True
        self.set_running(True, None)

    def stop(self) -> None:
        self.stop_calls += 1
        self.alive = False
        self.set_running(False, None)

    def is_alive(self) -> bool:
        return self.alive


class _DiscordCommandServiceForTest(ImSessionCommandService):
    def __init__(self) -> None:
        super().__init__(
            session_service=cast(SessionService, object()),
            run_service=cast(SessionRunService, object()),
            external_session_binding_repo=cast(
                ExternalSessionBindingRepository,
                object(),
            ),
            gateway_session_service=cast(GatewaySessionService, object()),
            feishu_message_pool_service=cast(_FeishuQueueLookup, object()),
        )

    def _cmd_wechat_status(self, *, session_id: str) -> str:
        return f"status:{session_id}"

    def _cmd_wechat_clear(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
    ) -> str:
        return f"clear:{session_id}:{gateway_session_id}"

    def _cmd_wechat_resume(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
    ) -> ImSessionCommandResult:
        return ImSessionCommandResult(
            text=f"resume:{session_id}:{gateway_session_id}",
            resumed_run_id="run-1",
        )
