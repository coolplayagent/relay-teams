from __future__ import annotations

from collections.abc import Callable

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookHandlerConfig
from relay_teams.net.clients import create_async_http_client


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
                headers=handler.headers,
            )
        response.raise_for_status()
        return HookDecision.model_validate(response.json())
