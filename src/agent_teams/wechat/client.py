# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
import secrets
import time

import httpx

from agent_teams.net import create_sync_http_client
from agent_teams.wechat.models import (
    DEFAULT_WECHAT_BOT_TYPE,
    WeChatAccountRecord,
    WeChatBaseInfo,
    WeChatGetUpdatesResponse,
    WeChatLoginSession,
    WeChatQrCodeResponse,
    WeChatQrStatusResponse,
    WeChatTypingConfigResponse,
)

_DEFAULT_LONG_POLL_TIMEOUT_MS = 35000
_DEFAULT_API_TIMEOUT_SECONDS = 15.0


class WeChatClient:
    def start_qr_login(
        self,
        *,
        base_url: str,
        route_tag: str | None = None,
        bot_type: str = DEFAULT_WECHAT_BOT_TYPE,
    ) -> WeChatQrCodeResponse:
        response = self._request(
            base_url=base_url,
            path=f"ilink/bot/get_bot_qrcode?bot_type={bot_type}",
            route_tag=route_tag,
            method="GET",
            payload=None,
            token=None,
            timeout_seconds=_DEFAULT_API_TIMEOUT_SECONDS,
        )
        parsed = WeChatQrCodeResponse.model_validate(response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=parsed.errcode,
            errmsg=parsed.errmsg,
            operation="start_qr_login",
        )
        return parsed

    def wait_qr_login(
        self,
        *,
        login_session: WeChatLoginSession,
        timeout_ms: int,
    ) -> WeChatQrStatusResponse:
        deadline = datetime.now(tz=timezone.utc) + timedelta(milliseconds=timeout_ms)
        while datetime.now(tz=timezone.utc) < deadline:
            try:
                response = self._request(
                    base_url=login_session.base_url,
                    path=f"ilink/bot/get_qrcode_status?qrcode={login_session.qrcode}",
                    route_tag=login_session.route_tag,
                    method="GET",
                    payload=None,
                    token=None,
                    timeout_seconds=_DEFAULT_API_TIMEOUT_SECONDS,
                    extra_headers={"iLink-App-ClientVersion": "1"},
                )
            except httpx.ReadTimeout:
                time.sleep(1.0)
                continue
            parsed = WeChatQrStatusResponse.model_validate(response)
            self._raise_if_provider_error(
                ret=parsed.ret,
                errcode=parsed.errcode,
                errmsg=parsed.errmsg,
                operation="wait_qr_login",
            )
            if parsed.status in {"confirmed", "expired"}:
                return parsed
            time.sleep(1.0)
        return WeChatQrStatusResponse(status="expired")

    def get_updates(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        timeout_ms: int = _DEFAULT_LONG_POLL_TIMEOUT_MS,
    ) -> WeChatGetUpdatesResponse:
        timeout_seconds = max(timeout_ms / 1000.0 + 5.0, _DEFAULT_API_TIMEOUT_SECONDS)
        response = self._request(
            base_url=account.base_url,
            path="ilink/bot/getupdates",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "get_updates_buf": account.sync_cursor,
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=timeout_seconds,
        )
        parsed = WeChatGetUpdatesResponse.model_validate(response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=parsed.errcode,
            errmsg=parsed.errmsg,
            operation="get_updates",
        )
        return parsed

    def send_text_message(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        self._request(
            base_url=account.base_url,
            path="ilink/bot/sendmessage",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "msg": {
                    "to_user_id": to_user_id,
                    "context_token": context_token,
                    "item_list": [
                        {
                            "type": 1,
                            "text_item": {"text": text},
                        }
                    ],
                },
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=_DEFAULT_API_TIMEOUT_SECONDS,
        )

    def get_typing_ticket(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        peer_user_id: str,
        context_token: str | None,
    ) -> str | None:
        response = self._request(
            base_url=account.base_url,
            path="ilink/bot/getconfig",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "ilink_user_id": peer_user_id,
                "context_token": context_token,
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=10.0,
        )
        parsed = WeChatTypingConfigResponse.model_validate(response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=None,
            errmsg=parsed.errmsg,
            operation="get_typing_ticket",
        )
        return parsed.typing_ticket

    def send_typing(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        peer_user_id: str,
        typing_ticket: str,
        status: int,
    ) -> None:
        self._request(
            base_url=account.base_url,
            path="ilink/bot/sendtyping",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "ilink_user_id": peer_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=10.0,
        )

    def _request(
        self,
        *,
        base_url: str,
        path: str,
        route_tag: str | None,
        method: str,
        payload: dict[str, object] | None,
        token: str | None,
        timeout_seconds: float,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        url = self._build_url(base_url=base_url, path=path)
        body = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        headers = self._build_headers(
            body=body,
            token=token,
            route_tag=route_tag,
            extra_headers=extra_headers,
        )
        with create_sync_http_client(timeout_seconds=timeout_seconds) as client:
            response = client.request(
                method=method,
                url=url,
                content=body.encode("utf-8") if body is not None else None,
                headers=headers,
            )
        response.raise_for_status()
        if not response.text:
            return {}
        parsed = json.loads(response.text)
        if isinstance(parsed, dict):
            return parsed
        raise RuntimeError(f"Unexpected WeChat response payload type: {type(parsed)!r}")

    @staticmethod
    def _build_url(*, base_url: str, path: str) -> str:
        normalized = base_url.rstrip("/")
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{normalized}/{path.lstrip('/')}"

    def _build_headers(
        self,
        *,
        body: str | None,
        token: str | None,
        route_tag: str | None,
        extra_headers: dict[str, str] | None,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._build_wechat_uin(),
        }
        if body is not None:
            headers["Content-Length"] = str(len(body.encode("utf-8")))
        if token is not None and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        if route_tag is not None and route_tag.strip():
            headers["SKRouteTag"] = route_tag.strip()
        if extra_headers is not None:
            headers.update(extra_headers)
        return headers

    @staticmethod
    def _build_wechat_uin() -> str:
        random_value = secrets.randbelow(2**32 - 1)
        return base64.b64encode(str(random_value).encode("utf-8")).decode("utf-8")

    @staticmethod
    def _raise_if_provider_error(
        *,
        ret: int | None,
        errcode: int | None,
        errmsg: str | None,
        operation: str,
    ) -> None:
        if ret in (None, 0) and errcode in (None, 0):
            return
        details: list[str] = []
        if ret not in (None, 0):
            details.append(f"ret={ret}")
        if errcode not in (None, 0):
            details.append(f"errcode={errcode}")
        message = (errmsg or "").strip()
        if message:
            details.append(message)
        suffix = ", ".join(details) if details else "provider returned an error"
        raise RuntimeError(f"WeChat {operation} failed: {suffix}")
