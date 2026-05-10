# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from threading import Lock, Thread

import discord

from relay_teams.gateway.discord.models import DiscordInboundMessage
from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)


class DiscordGatewayWorker:
    def __init__(
        self,
        *,
        account_id: str,
        target_loop: asyncio.AbstractEventLoop,
        handle_message: Callable[
            [str, DiscordInboundMessage],
            Coroutine[object, object, None],
        ],
        set_running: Callable[[bool, str | None], None],
    ) -> None:
        self._account_id = account_id
        self._target_loop = target_loop
        self._handle_message = handle_message
        self._set_running = set_running
        self._client = _DiscordMessageClient(
            account_id=account_id,
            target_loop=target_loop,
            handle_message=handle_message,
            set_running=set_running,
        )
        self._thread: Thread | None = None
        self._lock = Lock()

    def start(self, *, token: str) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = Thread(
                target=self._run,
                kwargs={"token": token},
                name=f"discord-gateway-{self._account_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
        if thread is None:
            return
        self._client.request_close()
        thread.join(timeout=5.0)
        stopped = not thread.is_alive()
        with self._lock:
            if stopped and self._thread is thread:
                self._thread = None
        self._set_running(False, None if stopped else "stop_timeout")

    def is_alive(self) -> bool:
        with self._lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self, *, token: str) -> None:
        try:
            self._client.run(token)
        except Exception as exc:
            self._set_running(False, str(exc))
            log_event(
                LOGGER,
                logging.WARNING,
                event="gateway.discord.worker.failed",
                message="Discord gateway worker stopped with an error",
                payload={"account_id": self._account_id, "error": str(exc)},
                exc_info=exc,
            )


class _DiscordMessageClient(discord.Client):
    def __init__(
        self,
        *,
        account_id: str,
        target_loop: asyncio.AbstractEventLoop,
        handle_message: Callable[
            [str, DiscordInboundMessage],
            Coroutine[object, object, None],
        ],
        set_running: Callable[[bool, str | None], None],
    ) -> None:
        intents = discord.Intents.default()
        intents.dm_messages = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self._account_id = account_id
        self._target_loop = target_loop
        self._handle_message = handle_message
        self._set_running = set_running
        self._close_future: Future[object] | None = None

    async def on_ready(self) -> None:
        self._set_running(True, None)

    async def on_message(self, message: discord.Message) -> None:
        inbound = self._to_inbound_message(message)
        _ = asyncio.run_coroutine_threadsafe(
            self._handle_message(self._account_id, inbound),
            self._target_loop,
        )

    def request_close(self) -> None:
        try:
            loop = self.loop
            if loop.is_closed():
                return
            self._close_future = asyncio.run_coroutine_threadsafe(
                self.close(),
                loop,
            )
        except (AttributeError, RuntimeError):
            return

    def _to_inbound_message(self, message: discord.Message) -> DiscordInboundMessage:
        bot_user_id = self.user.id if self.user is not None else None
        mentions_bot = False
        if bot_user_id is not None:
            mentions_bot = any(user.id == bot_user_id for user in message.mentions)
        guild_id = str(message.guild.id) if message.guild is not None else None
        channel_id = str(message.channel.id)
        thread_id = channel_id if isinstance(message.channel, discord.Thread) else None
        return DiscordInboundMessage(
            message_id=str(message.id),
            channel_id=channel_id,
            author_id=str(message.author.id),
            author_name=str(message.author.name or ""),
            content=str(message.content or ""),
            guild_id=guild_id,
            thread_id=thread_id,
            mentions_bot=mentions_bot,
            is_dm=(message.guild is None),
            author_is_bot=bool(message.author.bot),
        )
