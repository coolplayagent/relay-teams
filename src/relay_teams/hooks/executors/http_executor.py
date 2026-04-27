from __future__ import annotations

import json
import os
import re
from collections.abc import Callable

import httpx

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.hooks.executors.output_parser import (
    parse_empty_hook_output,
    parse_hook_decision_payload,
)
from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookEventName, HookHandlerConfig
from relay_teams.net.clients import create_async_http_client


class NonBlockingHttpHookError(RuntimeError):
    pass


class HttpHookExecutor:
    def __init__(
        self,
        *,
        get_proxy_config: Callable[[], ProxyEnvConfig] | None = None,
    ) -> None:
        self._get_proxy_config = get_proxy_config

    async def execute(
        self,
        *,
        handler: HookHandlerConfig,
        event_input: HookEventInput,
    ) -> HookDecision:
        url = str(handler.url or "").strip()
        if not url:
            raise ValueError("HTTP hook requires a url")
        proxy_config = None
        if self._get_proxy_config is not None:
            proxy_config = self._get_proxy_config()
        async with create_async_http_client(
            proxy_config=proxy_config,
            timeout_seconds=handler.timeout_seconds,
            connect_timeout_seconds=handler.timeout_seconds,
        ) as client:
            response = await client.post(
                url,
                json=event_input.model_dump(mode="json"),
                headers=_interpolate_headers(
                    headers=handler.headers,
                    allowed_env_vars=handler.allowed_env_vars,
                ),
            )
        return _parse_http_response(response, event_name=event_input.event_name)


def _parse_http_response(
    response: httpx.Response,
    *,
    event_name: HookEventName,
) -> HookDecision:
    if not 200 <= response.status_code < 300:
        raise NonBlockingHttpHookError(
            f"HTTP hook returned status {response.status_code}"
        )
    if not response.text.strip():
        return parse_empty_hook_output(event_name=event_name)
    raw_text = response.text.strip()
    if _looks_like_json(raw_text):
        try:
            return parse_hook_decision_payload(
                json.loads(raw_text),
                event_name=event_name,
            )
        except json.JSONDecodeError:
            pass
    return HookDecision(
        decision=parse_empty_hook_output(event_name=event_name).decision,
        additional_context=(raw_text,),
    )


def _looks_like_json(value: str) -> bool:
    return value.startswith("{") and value.endswith("}")


_ENV_REFERENCE_PATTERN = re.compile(
    r"\$(?P<plain>[A-Za-z_][A-Za-z0-9_]*)|\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)}"
)


def _interpolate_headers(
    *,
    headers: dict[str, str],
    allowed_env_vars: tuple[str, ...],
) -> dict[str, str]:
    if not headers:
        return {}
    allowed = set(allowed_env_vars)
    return {
        key: _interpolate_header_value(value=value, allowed_env_vars=allowed)
        for key, value in headers.items()
    }


def _interpolate_header_value(
    *,
    value: str,
    allowed_env_vars: set[str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("plain") or match.group("braced") or ""
        if name not in allowed_env_vars:
            return ""
        return os.environ.get(name, "")

    return _ENV_REFERENCE_PATTERN.sub(replace, value)
