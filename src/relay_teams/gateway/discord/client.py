# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx

from relay_teams.gateway.discord.models import DiscordBotIdentity
from relay_teams.net import create_async_http_client

_DISCORD_API_BASE_URL = "https://discord.com/api/v10"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class DiscordClient:
    def __init__(
        self,
        *,
        api_base_url: str = _DISCORD_API_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def fetch_current_bot_identity(self, *, token: str) -> DiscordBotIdentity:
        user_payload = await self._request_json(
            token=token,
            method="GET",
            path="/users/@me",
            payload=None,
        )
        application_id = await self._fetch_application_id(token=token)
        user_id = _require_payload_text(user_payload, "id")
        username = _require_payload_text(user_payload, "username")
        return DiscordBotIdentity(
            user_id=user_id,
            username=username,
            application_id=application_id,
        )

    async def send_text_message(
        self,
        *,
        token: str,
        channel_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> str:
        payload: dict[str, object] = {"content": text}
        normalized_reply_to_message_id = str(reply_to_message_id or "").strip()
        if normalized_reply_to_message_id:
            payload["message_reference"] = {
                "message_id": normalized_reply_to_message_id,
                "channel_id": channel_id,
                "fail_if_not_exists": False,
            }
            payload["allowed_mentions"] = {"replied_user": False}
        response_payload = await self._request_json(
            token=token,
            method="POST",
            path=f"/channels/{channel_id}/messages",
            payload=payload,
        )
        return _require_payload_text(response_payload, "id")

    async def send_file(
        self,
        *,
        token: str,
        channel_id: str,
        file_path: Path,
        reply_to_message_id: str | None = None,
    ) -> str:
        payload: dict[str, object] = {}
        normalized_reply_to_message_id = str(reply_to_message_id or "").strip()
        if normalized_reply_to_message_id:
            payload["message_reference"] = {
                "message_id": normalized_reply_to_message_id,
                "channel_id": channel_id,
                "fail_if_not_exists": False,
            }
            payload["allowed_mentions"] = {"replied_user": False}
        try:
            async with create_async_http_client(
                timeout_seconds=self._timeout_seconds,
            ) as client:
                with file_path.open("rb") as handle:
                    response = await client.post(
                        f"{self._api_base_url}/channels/{channel_id}/messages",
                        headers={"Authorization": _authorization_header(token)},
                        data={"payload_json": json.dumps(payload)},
                        files={
                            "files[0]": (
                                file_path.name,
                                handle,
                                "application/octet-stream",
                            )
                        },
                    )
        except httpx.RequestError as exc:
            raise RuntimeError(f"Discord API request failed: {exc}") from exc
        _raise_for_discord_error(response)
        parsed = _json_mapping(response)
        message_id = _require_payload_text(parsed, "id")
        return f"file sent ({file_path.name}, message={message_id})"

    async def _fetch_application_id(self, *, token: str) -> str | None:
        try:
            payload = await self._request_json(
                token=token,
                method="GET",
                path="/oauth2/applications/@me",
                payload=None,
            )
        except RuntimeError:
            return None
        application_id = payload.get("id")
        if isinstance(application_id, str) and application_id.strip():
            return application_id.strip()
        return None

    async def _request_json(
        self,
        *,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None,
    ) -> dict[str, object]:
        try:
            async with create_async_http_client(
                timeout_seconds=self._timeout_seconds,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._api_base_url}{path}",
                    headers={
                        "Authorization": _authorization_header(token),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.RequestError as exc:
            raise RuntimeError(f"Discord API request failed: {exc}") from exc
        _raise_for_discord_error(response)
        return _json_mapping(response)


def _authorization_header(token: str) -> str:
    normalized = token.strip()
    if normalized.casefold().startswith("bot "):
        return normalized
    return f"Bot {normalized}"


def _json_mapping(response: httpx.Response) -> dict[str, object]:
    return cast(dict[str, object], response.json())


def _require_payload_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Discord response missing {key}")
    return value.strip()


def _raise_for_discord_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text[:500]
        raise RuntimeError(
            f"Discord API request failed: {response.status_code} {detail}",
        ) from exc
