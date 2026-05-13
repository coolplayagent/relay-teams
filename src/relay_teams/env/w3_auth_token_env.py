# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from relay_teams.env.w3_auth_source import get_w3_credentials
from relay_teams.logger import get_logger
from relay_teams.paths import get_app_config_dir
from relay_teams.providers.maas_auth import (
    MaaSAuthContext,
    MaaSLoginError,
    get_maas_token_service,
)
from relay_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    MaaSAuthConfig,
)
from relay_teams.secrets import AppSecretStore

__all__ = [
    "W3MaaSTokenService",
    "env_declares_w3_x_auth_token",
    "is_w3_x_auth_token_env_name",
    "overlay_w3_x_auth_token_env",
    "resolve_w3_x_auth_token",
]

LOGGER = get_logger(__name__)
_TARGET_ENV_NAME = "xauthtoken"
_NON_ENV_NAME_CHARS = re.compile(r"[^A-Za-z0-9]+")


class W3MaaSTokenService(Protocol):
    async def get_auth_context(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        raise NotImplementedError  # pragma: no cover


def is_w3_x_auth_token_env_name(name: str) -> bool:
    normalized = _NON_ENV_NAME_CHARS.sub("", name.strip()).casefold()
    return normalized == _TARGET_ENV_NAME


def env_declares_w3_x_auth_token(env: Mapping[str, object] | None) -> bool:
    if env is None:
        return False
    return any(is_w3_x_auth_token_env_name(key) for key in env)


async def overlay_w3_x_auth_token_env(
    env: Mapping[str, str],
    *,
    declared_env: Mapping[str, object] | None = None,
    config_dir: Path | None = None,
    token_service: W3MaaSTokenService | None = None,
    secret_store: AppSecretStore | None = None,
    inject_missing_declared: bool = False,
) -> dict[str, str]:
    result = dict(env)
    declarations = result if declared_env is None else declared_env
    matching_keys = tuple(
        key for key in declarations if is_w3_x_auth_token_env_name(key)
    )
    if not matching_keys:
        return result
    keys_to_write = tuple(
        key for key in matching_keys if key in result or inject_missing_declared
    )
    if not keys_to_write:
        return result
    token = await resolve_w3_x_auth_token(
        config_dir=config_dir,
        token_service=token_service,
        secret_store=secret_store,
    )
    if token is None:
        return result
    for key in tuple(result):
        if is_w3_x_auth_token_env_name(key):
            result.pop(key)
    for key in keys_to_write:
        result[key] = token
    return result


async def resolve_w3_x_auth_token(
    *,
    config_dir: Path | None = None,
    token_service: W3MaaSTokenService | None = None,
    secret_store: AppSecretStore | None = None,
) -> str | None:
    resolved_config_dir = (
        get_app_config_dir() if config_dir is None else config_dir.expanduser()
    ).resolve()
    credentials = await asyncio.to_thread(
        get_w3_credentials,
        resolved_config_dir,
        secret_store=secret_store,
    )
    if credentials is None:
        LOGGER.warning(
            "Skipping W3 X-Auth-Token runtime env overlay because W3 "
            "credentials are not configured",
            extra={
                "event": "w3.auth_token_env.credentials_missing",
                "payload": {"config_dir": str(resolved_config_dir)},
            },
        )
        return None
    try:
        auth_context = await (
            get_maas_token_service() if token_service is None else token_service
        ).get_auth_context(
            auth_config=MaaSAuthConfig(
                username=credentials.username,
                password=credentials.password,
            ),
            ssl_verify=None,
            connect_timeout_seconds=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
        )
    except MaaSLoginError as exc:
        LOGGER.warning(
            "Skipping W3 X-Auth-Token runtime env overlay because W3 login failed",
            extra={
                "event": "w3.auth_token_env.login_failed",
                "payload": {
                    "config_dir": str(resolved_config_dir),
                    "status_code": getattr(exc, "status_code", None),
                    "error": str(exc),
                },
            },
        )
        return None
    except Exception as exc:
        LOGGER.warning(
            "Skipping W3 X-Auth-Token runtime env overlay because token "
            "resolution failed",
            extra={
                "event": "w3.auth_token_env.resolve_failed",
                "payload": {
                    "config_dir": str(resolved_config_dir),
                    "error": str(exc) or exc.__class__.__name__,
                },
            },
            exc_info=exc,
        )
        return None
    return auth_context.token
