# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
from collections.abc import Iterable
from pathlib import Path
import json
import hashlib
import mimetypes
from datetime import datetime, timedelta, timezone
import secrets
import time
from urllib.parse import quote
from uuid import uuid4

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx

from relay_teams.logger import get_logger
from relay_teams.net import create_sync_http_client
from relay_teams.gateway.wechat.models import (
    DEFAULT_WECHAT_BOT_TYPE,
    WeChatAccountRecord,
    WeChatBaseInfo,
    WeChatGetUpdatesResponse,
    WeChatLoginSession,
    WeChatOperationResponse,
    WeChatQrCodeResponse,
    WeChatQrStatusResponse,
    WeChatTypingConfigResponse,
    WeChatUploadMediaType,
    WeChatUploadedMedia,
    WeChatUploadUrlResponse,
)

_DEFAULT_LONG_POLL_TIMEOUT_MS = 35000
_DEFAULT_API_TIMEOUT_SECONDS = 15.0
_DEFAULT_CDN_TIMEOUT_SECONDS = 60.0
_CDN_UPLOAD_MAX_RETRIES = 3
_WECHAT_BOT_MESSAGE_TYPE = 2
_WECHAT_MESSAGE_STATE_FINISH = 2
_WECHAT_TEXT_ITEM_TYPE = 1
_WECHAT_IMAGE_ITEM_TYPE = 2
_WECHAT_FILE_ITEM_TYPE = 4
_WECHAT_VIDEO_ITEM_TYPE = 5

LOGGER = get_logger(__name__)


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
        response = self._request(
            base_url=account.base_url,
            path="ilink/bot/sendmessage",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "msg": self._build_text_message(
                    to_user_id=to_user_id,
                    text=text,
                    context_token=context_token,
                ),
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=_DEFAULT_API_TIMEOUT_SECONDS,
        )
        parsed = WeChatOperationResponse.model_validate(response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=parsed.errcode,
            errmsg=parsed.errmsg,
            operation="send_text_message",
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
        media_type = self._resolve_media_type(file_path)
        uploaded = self._upload_media(
            account=account,
            token=token,
            to_user_id=to_user_id,
            file_path=file_path,
            media_type=media_type,
        )
        self._send_media_message(
            account=account,
            token=token,
            to_user_id=to_user_id,
            file_path=file_path,
            media_type=media_type,
            uploaded=uploaded,
            context_token=context_token,
        )
        if media_type is WeChatUploadMediaType.IMAGE:
            return f"image sent ({file_path.name})"
        if media_type is WeChatUploadMediaType.VIDEO:
            return f"video sent ({file_path.name})"
        return f"file sent ({file_path.name})"

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
        response = self._request(
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
        parsed = WeChatOperationResponse.model_validate(response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=parsed.errcode,
            errmsg=parsed.errmsg,
            operation="send_typing",
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
    def _build_text_message(
        *,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> dict[str, object]:
        message: dict[str, object] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"agent-teams-wechat-{uuid4().hex}",
            "message_type": _WECHAT_BOT_MESSAGE_TYPE,
            "message_state": _WECHAT_MESSAGE_STATE_FINISH,
            "item_list": [
                {
                    "type": _WECHAT_TEXT_ITEM_TYPE,
                    "text_item": {"text": text},
                }
            ],
        }
        if context_token is not None and context_token.strip():
            message["context_token"] = context_token
        return message

    def _upload_media(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        file_path: Path,
        media_type: WeChatUploadMediaType,
    ) -> WeChatUploadedMedia:
        plaintext = file_path.read_bytes()
        raw_size = len(plaintext)
        raw_md5 = hashlib.md5(plaintext).hexdigest()
        aes_key_bytes = secrets.token_bytes(16)
        filekey = secrets.token_hex(16)
        encrypted_bytes = self._encrypt_aes_ecb(plaintext, aes_key_bytes)
        upload_response = self._request(
            base_url=account.base_url,
            path="ilink/bot/getuploadurl",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "filekey": filekey,
                "media_type": int(media_type.value),
                "to_user_id": to_user_id,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": len(encrypted_bytes),
                "no_need_thumb": True,
                "aeskey": aes_key_bytes.hex(),
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=_DEFAULT_API_TIMEOUT_SECONDS,
        )
        parsed = WeChatUploadUrlResponse.model_validate(upload_response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=parsed.errcode,
            errmsg=parsed.errmsg,
            operation="get_upload_url",
        )
        upload_url = self._resolve_upload_url(
            upload_response,
            cdn_base_url=account.cdn_base_url,
            filekey=filekey,
        )
        if not upload_url:
            response_size = len(json.dumps(upload_response, ensure_ascii=False))
            top_level_keys = sorted(upload_response.keys())
            upload_full_url_paths = self._collect_upload_full_url_candidate_paths(
                upload_response
            )
            upload_param_paths = self._collect_upload_param_candidate_paths(
                upload_response
            )
            LOGGER.warning(
                "WeChat get_upload_url response missing upload target",
                extra={
                    "top_level_keys": top_level_keys,
                    "upload_full_url_paths": upload_full_url_paths,
                    "upload_param_paths": upload_param_paths,
                    "response_size": response_size,
                },
            )
            raise RuntimeError(
                "WeChat get_upload_url failed: missing upload target "
                f"(top_level_keys={top_level_keys}, "
                f"upload_full_url_paths={upload_full_url_paths}, "
                f"upload_param_paths={upload_param_paths}, "
                f"response_size={response_size})"
            )
        download_query_param = self._upload_to_cdn(
            upload_url=upload_url,
            filekey=filekey,
            encrypted_bytes=encrypted_bytes,
        )
        return WeChatUploadedMedia(
            filekey=filekey,
            download_encrypted_query_param=download_query_param,
            aes_key_hex=aes_key_bytes.hex(),
            file_size=raw_size,
            file_size_ciphertext=len(encrypted_bytes),
        )

    def _send_media_message(
        self,
        *,
        account: WeChatAccountRecord,
        token: str,
        to_user_id: str,
        file_path: Path,
        media_type: WeChatUploadMediaType,
        uploaded: WeChatUploadedMedia,
        context_token: str | None,
    ) -> None:
        response = self._request(
            base_url=account.base_url,
            path="ilink/bot/sendmessage",
            route_tag=account.route_tag,
            method="POST",
            payload={
                "msg": self._build_media_message(
                    to_user_id=to_user_id,
                    file_path=file_path,
                    media_type=media_type,
                    uploaded=uploaded,
                    context_token=context_token,
                ),
                "base_info": WeChatBaseInfo().model_dump(mode="json"),
            },
            token=token,
            timeout_seconds=_DEFAULT_API_TIMEOUT_SECONDS,
        )
        parsed = WeChatOperationResponse.model_validate(response)
        self._raise_if_provider_error(
            ret=parsed.ret,
            errcode=parsed.errcode,
            errmsg=parsed.errmsg,
            operation="send_file_message",
        )

    @staticmethod
    def _build_media_message(
        *,
        to_user_id: str,
        file_path: Path,
        media_type: WeChatUploadMediaType,
        uploaded: WeChatUploadedMedia,
        context_token: str | None,
    ) -> dict[str, object]:
        media: dict[str, object] = {
            "encrypt_query_param": uploaded.download_encrypted_query_param,
            "aes_key": base64.b64encode(uploaded.aes_key_hex.encode("utf-8")).decode(
                "utf-8"
            ),
            "encrypt_type": 1,
        }
        item: dict[str, object]
        if media_type is WeChatUploadMediaType.IMAGE:
            item = {
                "type": _WECHAT_IMAGE_ITEM_TYPE,
                "image_item": {
                    "media": media,
                    "mid_size": uploaded.file_size_ciphertext,
                },
            }
        elif media_type is WeChatUploadMediaType.VIDEO:
            item = {
                "type": _WECHAT_VIDEO_ITEM_TYPE,
                "video_item": {
                    "media": media,
                    "video_size": uploaded.file_size_ciphertext,
                },
            }
        else:
            item = {
                "type": _WECHAT_FILE_ITEM_TYPE,
                "file_item": {
                    "media": media,
                    "file_name": file_path.name,
                    "len": str(uploaded.file_size),
                },
            }
        message: dict[str, object] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"agent-teams-wechat-{uuid4().hex}",
            "message_type": _WECHAT_BOT_MESSAGE_TYPE,
            "message_state": _WECHAT_MESSAGE_STATE_FINISH,
            "item_list": [item],
        }
        if context_token is not None and context_token.strip():
            message["context_token"] = context_token
        return message

    def _upload_to_cdn(
        self,
        *,
        upload_url: str,
        filekey: str,
        encrypted_bytes: bytes,
    ) -> str:
        last_exception: Exception | None = None
        for attempt in range(1, _CDN_UPLOAD_MAX_RETRIES + 1):
            try:
                with create_sync_http_client(
                    timeout_seconds=_DEFAULT_CDN_TIMEOUT_SECONDS
                ) as client:
                    response = client.request(
                        method="POST",
                        url=upload_url,
                        content=encrypted_bytes,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                if 400 <= response.status_code < 500:
                    message = self._read_cdn_error_message(response)
                    raise RuntimeError(
                        "WeChat CDN upload failed: "
                        f"status={response.status_code}, message={message}"
                    )
                if response.status_code != 200:
                    message = self._read_cdn_error_message(response)
                    raise httpx.HTTPStatusError(
                        message=(
                            "WeChat CDN upload failed: "
                            f"status={response.status_code}, message={message}"
                        ),
                        request=response.request,
                        response=response,
                    )
                download_query_param = str(
                    response.headers.get("x-encrypted-param", "")
                ).strip()
                if not download_query_param:
                    raise RuntimeError(
                        "WeChat CDN upload failed: missing x-encrypted-param header"
                    )
                return download_query_param
            except RuntimeError:
                raise
            except Exception as exc:
                last_exception = (
                    exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                )
                LOGGER.warning(
                    "WeChat CDN upload attempt failed",
                    extra={
                        "filekey": filekey,
                        "attempt": attempt,
                    },
                    exc_info=last_exception,
                )
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("WeChat CDN upload failed")

    @staticmethod
    def _resolve_media_type(file_path: Path) -> WeChatUploadMediaType:
        mime_type, _ = mimetypes.guess_type(file_path.name)
        if mime_type is not None:
            if mime_type.startswith("image/"):
                return WeChatUploadMediaType.IMAGE
            if mime_type.startswith("video/"):
                return WeChatUploadMediaType.VIDEO
        return WeChatUploadMediaType.FILE

    @staticmethod
    def _encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plaintext) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    @staticmethod
    def _build_cdn_upload_url(
        *,
        cdn_base_url: str,
        upload_param: str,
        filekey: str,
    ) -> str:
        normalized = cdn_base_url.rstrip("/")
        encoded_upload_param = quote(upload_param, safe="")
        encoded_filekey = quote(filekey, safe="")
        return (
            f"{normalized}/upload?encrypted_query_param={encoded_upload_param}"
            f"&filekey={encoded_filekey}"
        )

    @staticmethod
    def _read_cdn_error_message(response: httpx.Response) -> str:
        header_message = str(response.headers.get("x-error-message", "")).strip()
        if header_message:
            return header_message
        body_text = response.text.strip()
        if body_text:
            return body_text
        return "unknown_error"

    @classmethod
    def _resolve_upload_url(
        cls,
        payload: dict[str, object],
        *,
        cdn_base_url: str,
        filekey: str,
    ) -> str:
        upload_full_url = cls._extract_upload_full_url(payload)
        if upload_full_url:
            return cls._normalize_upload_url(
                cdn_base_url=cdn_base_url,
                upload_url=upload_full_url,
            )
        upload_param = cls._extract_upload_param(payload)
        if upload_param:
            return cls._build_cdn_upload_url(
                cdn_base_url=cdn_base_url,
                upload_param=upload_param,
                filekey=filekey,
            )
        return ""

    @classmethod
    def _extract_upload_full_url(cls, payload: dict[str, object]) -> str:
        direct_upload_url = cls._normalize_upload_param_value(
            payload.get("upload_full_url")
        )
        if direct_upload_url:
            return direct_upload_url
        direct_upload_url = cls._normalize_upload_param_value(
            payload.get("uploadFullUrl")
        )
        if direct_upload_url:
            return direct_upload_url
        for _path, value in cls._iter_candidate_values(
            payload,
            candidate_keys=frozenset({"upload_full_url", "uploadFullUrl"}),
        ):
            normalized = cls._normalize_upload_param_value(value)
            if normalized:
                return normalized
        return ""

    @classmethod
    def _extract_upload_param(cls, payload: dict[str, object]) -> str:
        direct_upload_param = cls._normalize_upload_param_value(
            payload.get("upload_param")
        )
        if direct_upload_param:
            return direct_upload_param
        direct_upload_param = cls._normalize_upload_param_value(
            payload.get("uploadParam")
        )
        if direct_upload_param:
            return direct_upload_param
        for _path, value in cls._iter_candidate_values(
            payload,
            candidate_keys=frozenset({"upload_param", "uploadParam"}),
        ):
            normalized = cls._normalize_upload_param_value(value)
            if normalized:
                return normalized
        return ""

    @classmethod
    def _collect_upload_full_url_candidate_paths(
        cls, payload: dict[str, object]
    ) -> tuple[str, ...]:
        return tuple(
            path
            for path, _value in cls._iter_candidate_values(
                payload,
                candidate_keys=frozenset({"upload_full_url", "uploadFullUrl"}),
            )
        )

    @classmethod
    def _collect_upload_param_candidate_paths(
        cls, payload: dict[str, object]
    ) -> tuple[str, ...]:
        return tuple(
            path
            for path, _value in cls._iter_candidate_values(
                payload,
                candidate_keys=frozenset({"upload_param", "uploadParam"}),
            )
        )

    @classmethod
    def _iter_candidate_values(
        cls,
        payload: object,
        *,
        candidate_keys: frozenset[str],
        path: str = "",
    ) -> Iterable[tuple[str, object]]:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if not isinstance(key, str):
                    continue
                next_path = f"{path}.{key}" if path else key
                if key in candidate_keys:
                    yield next_path, value
                yield from cls._iter_candidate_values(
                    value,
                    candidate_keys=candidate_keys,
                    path=next_path,
                )
            return
        if isinstance(payload, list):
            for index, item in enumerate(payload):
                next_path = f"{path}[{index}]" if path else f"[{index}]"
                yield from cls._iter_candidate_values(
                    item,
                    candidate_keys=candidate_keys,
                    path=next_path,
                )

    @classmethod
    def _normalize_upload_url(cls, *, cdn_base_url: str, upload_url: str) -> str:
        normalized_upload_url = upload_url.strip()
        if not normalized_upload_url:
            return ""
        if normalized_upload_url.startswith(("http://", "https://")):
            return normalized_upload_url
        return cls._build_url(base_url=cdn_base_url, path=normalized_upload_url)

    @staticmethod
    def _normalize_upload_param_value(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

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
