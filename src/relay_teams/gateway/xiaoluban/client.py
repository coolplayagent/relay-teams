# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Optional
from uuid import uuid4

import httpx

from relay_teams.gateway.xiaoluban.models import (
    DEFAULT_XIAOLUBAN_BASE_URL,
    XiaolubanSendTextRequest,
    XiaolubanSendTextResponse,
)
from relay_teams.net import create_sync_http_client


class XiaolubanClient:
    def __init__(self) -> None:
        self._timeout_seconds = 30.0

    def send_text_message(
        self,
        *,
        text: str,
        receiver_uid: str,
        auth_token: str,
        base_url: str = DEFAULT_XIAOLUBAN_BASE_URL,
        sender: Optional[str] = None,
    ) -> XiaolubanSendTextResponse:
        request = XiaolubanSendTextRequest(
            content=text,
            receiver=receiver_uid,
            auth=auth_token,
            sender=sender,
        )
        try:
            with create_sync_http_client(
                timeout_seconds=self._timeout_seconds
            ) as client:
                response = client.post(
                    _normalize_base_url(base_url),
                    content=request.model_dump_json().encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
        except httpx.RequestError as exc:
            raise RuntimeError(f"Xiaoluban API request failed: {exc}") from exc
        return _parse_send_response(response)


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip()
    if not normalized:
        return DEFAULT_XIAOLUBAN_BASE_URL
    return normalized


def _parse_send_response(response: httpx.Response) -> XiaolubanSendTextResponse:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or str(exc)
        raise RuntimeError(f"Xiaoluban API failed to send message: {detail}") from exc
    raw_text = response.text.strip()
    if not raw_text:
        return XiaolubanSendTextResponse(message_id=f"xlbmsg_{uuid4().hex[:12]}")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return XiaolubanSendTextResponse(
            message_id=f"xlbmsg_{uuid4().hex[:12]}",
            raw_response=raw_text,
        )
    if isinstance(payload, dict):
        for key in ("message_id", "msg_id", "id", "request_id"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                return XiaolubanSendTextResponse(
                    message_id=candidate,
                    raw_response=raw_text,
                )
    return XiaolubanSendTextResponse(
        message_id=f"xlbmsg_{uuid4().hex[:12]}",
        raw_response=raw_text,
    )


__all__ = ["XiaolubanClient"]
